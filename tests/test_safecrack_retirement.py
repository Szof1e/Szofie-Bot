"""Safecrack retirement without changing other attacks or existing collectibles."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from cogs import economy as economy_commands
from cogs.economy import ATTACKLOG_METHODS, EconomyCog
from szofie.config import DEFAULTS, ConfigManager
from szofie.economy import Economy, _default_user
from szofie.ledger import SECURITY_EVENT_REASONS, incoming_security_actor
from szofie.plushies import PLUSHIE_BY_ID, plushie_perk
from szofie.retired_items import SAFECRACK_CONFIG_KEYS, remove_safecrack
from szofie.storage import Storage
from szofie.titles import TITLES
from tests.test_system_integration import _FakeBot, _FakeInteraction


class SafecrackRetirementTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_settings_are_removed_and_cannot_be_configured(self):
        with tempfile.TemporaryDirectory() as raw:
            storage = Storage(Path(raw))
            storage.load(1)["economy"] = {key: 123 for key in SAFECRACK_CONFIG_KEYS}
            storage.load(1)["economy"]["bank_rob_max_percent"] = 17
            manager = ConfigManager(storage)
            cfg = manager.for_guild(1)
            for key in SAFECRACK_CONFIG_KEYS:
                self.assertNotIn(key, cfg.raw()["economy"])
                self.assertNotIn(key, DEFAULTS["economy"])
                self.assertNotIn("economy." + key, manager.schema_keys())
                self.assertFalse(manager.coerce("economy." + key, "10")[0])
            self.assertEqual(cfg.get("economy.bank_rob_max_percent"), 17)
            await manager.flush()
            self.assertFalse(set(SAFECRACK_CONFIG_KEYS) & set(Storage(Path(raw)).load(1)["economy"]))

    async def test_old_cooldowns_removed_without_asset_or_jail_changes(self):
        with tempfile.TemporaryDirectory() as raw:
            econ = Economy()
            econ.store = Storage(Path(raw))
            user = econ.user(1, 123, 100)
            user.update(
                safecrack_at="2026-10-04T00:00:00+00:00",
                safecracked_at="2026-10-04T01:00:00+00:00",
                jailed_until="2026-10-05T00:00:00+00:00",
            )
            user["plushies"]["catburglar"] = 1
            expected = copy.deepcopy(user)
            expected.pop("safecrack_at")
            expected.pop("safecracked_at")
            self.assertEqual(econ.all_users(1)["123"], expected)
            self.assertFalse(remove_safecrack(user))
            await econ.flush()
            self.assertEqual(Storage(Path(raw)).load(1)["users"]["123"], expected)
            self.assertNotIn("safecrack_at", _default_user(100))
            self.assertNotIn("safecracked_at", _default_user(100))

    async def test_retired_plushie_cannot_be_bought_even_by_typed_id(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            user = bot.economy.user(1, 123, 10**12)
            before = copy.deepcopy(user)
            interaction = _FakeInteraction()
            with patch.object(cog, "_guard", AsyncMock(return_value=bot.config.for_guild(1))):
                await EconomyCog.buy.callback(cog, interaction, "catburglar", 1)
            self.assertEqual(user, before)
            self.assertIn("retired legacy collectible", interaction.response.calls[-1]["embed"].description)
            for needle in ("cat", "Cat Burglar", "catburglar"):
                self.assertNotIn(
                    "catburglar", {c.value for c in await cog._buy_autocomplete(interaction, needle)}
                )

    async def test_retired_plushie_not_in_shop_but_owned_collectible_still_exists(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            user = bot.economy.user(1, 123, 100)
            user["plushies"]["catburglar"] = 1
            interaction = _FakeInteraction()
            with patch.object(cog, "_guard", AsyncMock(return_value=bot.config.for_guild(1))):
                await EconomyCog.shop.callback(cog, interaction)
            fields = "\n".join((f.value for f in interaction.response.calls[-1]["embed"].fields))
            self.assertNotIn("Cat Burglar", fields)
            self.assertEqual(user["plushies"]["catburglar"], 1)
            self.assertEqual(PLUSHIE_BY_ID["catburglar"].perk, "cosmetic")
            self.assertEqual(plushie_perk(user, "safecrack"), 0)

    async def test_historic_attack_log_remains_readable(self):
        self.assertIn("safecrack-success", SECURITY_EVENT_REASONS)
        self.assertEqual(ATTACKLOG_METHODS["safecrack-success"], "Safecrack vault robbery")
        old_entry = {"reason": "safecrack-success", "user": 456, "other": 123, "delta": -500}
        self.assertEqual(incoming_security_actor(old_entry, 456), 123)

    async def test_public_command_guides_no_longer_advertise_safecrack(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("README.md", "cogs/general.py", "cogs/coalitions.py"):
            source = (root / name).read_text()
            self.assertNotIn("/safecrack", source)
            self.assertNotIn("!idsafe", source)

    async def test_title_records_retained_for_existing_owners(self):
        self.assertEqual(sum((title.price > 0 for title in TITLES)), 34)
        self.assertEqual(sum((title.price == 0 for title in TITLES)), 14)


if __name__ == "__main__":
    unittest.main()
