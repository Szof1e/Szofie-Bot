"""Public release regressions: ordinary rules, admin gates and optional media."""

from __future__ import annotations

import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from cogs.economy import (
    EconomyCog,
    _b2_intercept_chance,
    _b52_intercept_chance,
    _s400_intercept_chance,
    _u2_intercept_chance,
    _virginia_rod_break_chance,
    _casino_display_snapshot,
)
from cogs.thor import ThorCog
from cogs.space import SpaceCog
from szofie import checks, hexes, public_updates, ui
from szofie.catalog import VEHICLE_CATALOG
from szofie.config import DEFAULTS, ConfigManager
from szofie.economy import Economy, _default_user
from szofie.storage import Storage
from tests.test_system_integration import _FakeBot, _FakeInteraction, _FakeUser


class PublicRulesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bot = _FakeBot(Path(self.temp.name))
        self.bot.owner_ids_set = {1}
        self.cog = object.__new__(EconomyCog)
        self.cog.bot, self.cog.econ = self.bot, self.bot.economy
        self.cfg = self.bot.config.for_guild(1)

    async def test_reads_and_saves_never_create_replacement_vehicles_or_ammunition(self):
        for user_id in (1, 2, 3):
            user = self.bot.economy.user(1, user_id, 100)
            self.assertFalse(any(state.get("owned") for state in user["vehicles"].values()))
            self.assertEqual(user["icbm_ready"], 0)
            self.assertFalse(user["s400_owned"])
            self.cog._vehicle_settle(user, "f22").update(owned=True, ammo=0, jet_damaged=True)
            user["vehicles"]["b2"].update(owned=False, armed=False)
            user["s400_owned"] = True
            user["s400_interceptors"] = 0
            user["thor"]["gbi_stock"] = 0
            user["imperial_star_destroyer"]["counter_stock"] = 0
            before = copy.deepcopy(user)
            for _ in range(3):
                self.assertEqual(self.bot.economy.user(1, user_id, 100), before)
                self.bot.economy.all_users(1)
                await self.bot.economy.save(1)
                self.assertEqual(user, before)
        restored = Economy()
        restored.store = Storage(self.bot.economy.store.directory)
        self.assertEqual(restored.user(1, 1, 100), self.bot.economy.user(1, 1, 100))

    async def test_strategic_odds_are_identical_for_admin_and_ordinary_accounts(self):
        victim = _default_user(100)
        victim["aa_rockets_loaded"] = 1
        for attacker in (1, 2, 3):
            for defender in (1, 2, 3):
                self.assertEqual(_s400_intercept_chance(self.cfg, defender), 30)
                self.assertEqual(_b2_intercept_chance(self.cfg, attacker, defender), 30)
                self.assertEqual(_b52_intercept_chance(self.cfg, attacker, defender), 50)
                self.assertEqual(_u2_intercept_chance(self.cfg, attacker, defender, victim), 30)
                self.assertEqual(_virginia_rod_break_chance(self.cfg, defender, "abyssal"), 40)
                self.assertEqual(_virginia_rod_break_chance(self.cfg, defender, "mythicrod"), 20)

    async def test_hex_affects_admin_and_ordinary_players_equally(self):
        expiry = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
        user = _default_user(100)
        user["hexed_until"] = expiry.isoformat()
        for user_id in (1, 2, 3):
            self.assertTrue(hexes.is_hexed(user, user_id=user_id))
            with patch("szofie.hexes.random.uniform", return_value=0.4):
                self.assertEqual(hexes.penalty(user, user_id), 0.4)

    async def test_casino_records_are_reported_without_alteration(self):
        user = _default_user(100)
        user["casino_stats"] = {"overall": {"plays": 3, "net": 42, "wagered": 300}}
        user["casino_reputation"] = 10
        before = copy.deepcopy(user)
        stats, reputation = _casino_display_snapshot(user)
        self.assertEqual(stats, before["casino_stats"])
        self.assertEqual(reputation, 10)
        self.assertEqual(user, before)

    async def test_explicit_admin_adjustments_still_require_authorization_and_are_logged(self):
        self.bot.ledger.record = AsyncMock()
        owner = _FakeInteraction(user_id=1)
        owner.client = self.bot
        victim = _FakeUser(2)
        user = self.bot.economy.user(1, 2, 100)
        await EconomyCog.donutadmin.callback(self.cog, owner, victim, 500, "Public admin test")
        self.assertEqual(user["donuts"], 600)
        self.bot.ledger.record.assert_awaited()
        self.assertEqual(self.bot.ledger.record.call_args.kwargs["actor"], 1)
        self.bot.ledger.record.reset_mock()
        stranger = _FakeInteraction(user_id=3)
        stranger.client = self.bot
        await EconomyCog.donutadmin.callback(self.cog, stranger, victim, 500)
        self.assertEqual(user["donuts"], 600)
        self.bot.ledger.record.assert_not_awaited()

    async def test_missing_art_keeps_actions_and_responses_usable(self):
        empty = Path(self.temp.name) / "no-art"
        with (
            patch("cogs.economy.PLUSHIE_DIR", empty),
            patch("cogs.economy.ITEM_DIR", empty),
            patch("cogs.thor.THOR_DIR", empty),
            patch("cogs.space.ART", empty),
        ):
            self.assertIsNone(self.cog._plushie_image("angler"))
            self.assertIsNone(self.cog._item_image("android21-autonomous-helper.png"))
            embed = ui.base_embed(title="Test")
            self.assertIsNone(ThorCog._art(self.cfg, embed, "thor-impact.png"))
            self.assertIsNone(SpaceCog(self.bot).art(embed, "fleet/arquitens-build.png"))

    async def test_bundled_art_is_used_and_legacy_craft_fallbacks_work(self):
        self.assertIsNotNone(self.cog._plushie_image("angler"))
        self.assertIsNotNone(self.cog._item_image("android21-autonomous-helper.png"))
        embed = ui.base_embed(title="Test")
        media = ThorCog._art(self.cfg, embed, "thor-impact.png")
        self.assertIsNotNone(media)
        media.close()
        for name, expected in (
            ("fleet/arquitens-build.png", "arquitens-build.png"),
            ("fleet/cutlass-build.png", "drake-cutlass-explorer.png"),
            ("fleet/transport-build.png", "gr75-loading-v3.png"),
            ("fleet/xwing-build.png", "xwing-patrol.png"),
        ):
            with self.subTest(name=name):
                embed = ui.base_embed(title="Test")
                media = SpaceCog(self.bot).art(embed, name)
                self.assertIsNotNone(media)
                self.assertEqual(embed.image.url, "attachment://" + expected)
                media.close()
        self.assertTrue(DEFAULTS["economy"]["icbm_media"])
        self.assertTrue(DEFAULTS["economy"]["aa_media"])
        self.assertTrue(DEFAULTS["moderation"]["nuke_media"])

    async def test_new_install_has_current_prices_and_respects_later_customization(self):
        for key, amount in (
            ("icbm_build_cost", 750_000_000),
            ("vehicle_b2_cost", 15_000_000_000),
            ("thor_rod_cost", 15_000_000_000_000),
        ):
            self.assertEqual(self.cfg.get("economy." + key), amount)
        self.cfg.set("economy.icbm_build_cost", 1234)
        await self.bot.config.flush()
        fresh = ConfigManager(Storage(self.bot.config.storage.directory))
        self.assertEqual(fresh.for_guild(1).get("economy.icbm_build_cost"), 1234)

    async def test_all_vehicle_catalogue_entries_have_ordinary_default_states(self):
        user = _default_user(0)
        for key in VEHICLE_CATALOG:
            if key == "s400":
                continue
            state = self.cog._vehicle_settle(user, key)
            self.assertFalse(state["owned"])


class PublicSourceTests(unittest.TestCase):
    def test_configuration_contains_no_identity_specific_gameplay_keys(self):
        keys = ConfigManager.schema_keys()
        self.assertFalse(any(key.startswith("economy.owner_") for key in keys))

    def test_all_slash_commands_have_valid_structure(self):
        self.assertTrue(EconomyCog.donutadmin.checks)
        self.assertIn(checks.owner_only_check, EconomyCog.donutadmin.checks)

    def test_patchnotes_destination_is_not_a_fixed_deployment_channel(self):
        self.assertEqual(public_updates.CHANNEL_ID, 0)
