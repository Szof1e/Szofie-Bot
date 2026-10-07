"""Reset, reservation and response regressions. Synthetic accounts only."""

import asyncio
import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from discord import app_commands
from cogs.blackjack import Blackjack, BlackjackView, Card
from cogs.continuity import ContinuityCog
from cogs.economy import EconomyCog
from cogs.imperial_star_destroyer import ImperialStarDestroyerCog
from cogs.settings import Settings
from cogs.thor import ThorCog
from szofie import assets, continuity, currency, guides, space, space_fleet as fleet, ui
from szofie.config import ConfigManager, DEFAULTS
from szofie.economy import _default_user
from szofie.ledger import incoming_security_actor
from tests.test_system_integration import _FakeBot, _FakeInteraction, _FakeUser


class GeneratedGuideTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {f"economy.{k}": v for k, v in DEFAULTS["economy"].items()}

    def test_thor_recipe_changes_propagate_to_guide_and_total(self):
        self.cfg.update(
            {
                "economy.thor_odin_cost": 11 * 10**12,
                "economy.thor_odin_build_hours": 9,
                "economy.thor_chamber_hours": 0.25,
                "economy.thor_sm3_intercept_chance": 40,
            }
        )
        pages = guides.thor_pages(self.cfg, color=0)
        self.assertIn("11 trillion donuts / 9h", pages[0].description)
        self.assertIn("31 trillion donuts", pages[0].description)
        self.assertIn("0.25h", pages[1].description)
        self.assertIn("40%", pages[2].description)
        self.assertIn("one 40% interception attempt", pages[2].description)
        self.assertNotIn("combined", pages[2].description)

    def test_isd_recipe_changes_propagate_to_guide_and_budget(self):
        self.cfg.update(
            {
                "economy.isd_launch_cost": 500 * 10**15,
                "economy.isd_counter_cost": 70 * 10**15,
                "economy.isd_counter_build_hours": 2,
            }
        )
        pages = guides.isd_pages(self.cfg, color=0)
        self.assertIn("16.5 quintillion donuts", pages[0].description)
        self.assertIn("70 quadrillion donuts / 2h", pages[1].description)

    def test_vehicle_catalog_is_lossless_and_live_config_aware(self):
        self.cfg["economy.vehicle_b52_cost"] = 99 * 10**12
        pages = guides.vehicle_pages(self.cfg, color=0)
        text = "\n".join((field.name + field.value for page in pages for field in page.fields))
        from szofie.catalog import VEHICLE_CATALOG

        for spec in VEHICLE_CATALOG.values():
            self.assertIn(spec["label"], text)
        self.assertIn("99 trillion", text)

    def test_generated_reference_matches_authoritative_defaults(self):
        path = Path(__file__).resolve().parents[1] / "docs/PUBLIC_CATALOG.md"
        self.assertEqual(path.read_text(), render())


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.current = space.now()
        self.user = _default_user(0)
        self.state = continuity.normalize(self.user)
        self.state.update(owned=True, transfer_action="retrieve", transfer_until=self.current.isoformat())
        self.cfg = {f"economy.{k}": v for k, v in DEFAULTS["economy"].items()}

    def test_caps_are_numeric_and_excess_returns_to_bunker(self):
        self.cfg["economy.hold_cap_lock"] = 3
        self.user["inventory"]["lock"] = 2
        self.state["transfer_payload"] = {"kind": "item", "id": "lock", "amount": 3}
        continuity.settle(self.user, self.cfg, self.current)
        self.assertEqual(self.user["inventory"]["lock"], 3)
        self.assertEqual(self.state["items"]["lock"], 2)
        self.assertIsNone(self.state["transfer_payload"])

    def test_reservation_applies_to_available_item_space(self):
        self.cfg["economy.hold_cap_lock"] = 3
        self.state["transfer_payload"] = {"kind": "item", "id": "lock", "amount": 2}
        self.assertEqual(assets.inventory_space(self.cfg, self.user, "lock"), 1)

    def test_recovery_waits_until_impact_seal_and_pays_exactly_once(self):
        self.state["transfer_payload"] = {"kind": "funds", "amount": 10**30 + 1}
        continuity.thor_impact(self.user, self.cfg, self.current)
        seal = continuity.parse_time(self.state["sealed_until"])
        continuity.settle(self.user, self.cfg, seal - dt.timedelta(seconds=1))
        self.assertEqual(self.user["donuts"], 0)
        self.assertEqual(continuity.parse_time(self.state["transfer_until"]), seal)
        continuity.settle(self.user, self.cfg, seal)
        continuity.settle(self.user, self.cfg, seal + dt.timedelta(days=1))
        self.assertEqual(self.user["donuts"], 10**30 + 1)

    def test_legacy_sealed_recovery_is_migrated_before_payment(self):
        self.state["sealed_until"] = (self.current + dt.timedelta(hours=12)).isoformat()
        self.state["transfer_payload"] = {"kind": "funds", "amount": 500}
        continuity.settle(self.user, self.cfg, self.current)
        self.assertEqual(self.user["donuts"], 0)
        self.assertEqual(self.state["transfer_until"], self.state["sealed_until"])

    def test_vehicle_recovery_does_not_overwrite_a_paid_build(self):
        paid = {
            "owned": False,
            "building_until": (self.current + dt.timedelta(hours=5)).isoformat(),
            "ammo": 0,
        }
        self.user["vehicles"]["b52"] = copy.deepcopy(paid)
        restored = {"model": "b52", "state": {"owned": True, "ammo": 2}}
        self.state["transfer_payload"] = {"kind": "vehicle", "data": restored}
        continuity.settle(self.user, self.cfg, self.current)
        self.assertEqual(self.user["vehicles"]["b52"], paid)
        self.assertEqual(self.state["vehicle"], restored)

    def test_rod_recovery_does_not_overwrite_new_enchants(self):
        self.user["rods"]["abyssal"] = 1
        self.user["rod_enchants"]["abyssal"] = ["new"]
        self.state["transfer_payload"] = {"kind": "rod", "data": {"id": "abyssal", "enchants": ["old"]}}
        continuity.settle(self.user, self.cfg, self.current)
        self.assertEqual(self.user["rod_enchants"]["abyssal"], ["new"])
        self.assertEqual(self.state["rod"]["enchants"], ["old"])

    def test_plushie_conflict_does_not_create_duplicate_perks(self):
        self.user["plushies"]["angler"] = 1
        self.state["transfer_payload"] = {"kind": "plushie", "id": "angler", "amount": 1}
        continuity.settle(self.user, self.cfg, self.current)
        self.assertEqual(self.user["plushies"]["angler"], 1)
        self.assertEqual(self.state["plushies"]["angler"], 1)


class DestructiveChronologyTests(unittest.TestCase):
    def test_overdue_cutlass_return_is_grounded_before_thor(self):
        current = space.now()
        user = _default_user(0)
        space.normalize(user).update(
            cutlass_owned=True,
            location="mars",
            destination="earth",
            travel_craft="cutlass",
            travel_until=(current - dt.timedelta(hours=1)).isoformat(),
        )
        fleet.ship(user, "cutlass").update(owned=True, deployed=True, location="mars")
        ThorCog._wipe_target(user, {}, current=current)
        self.assertFalse(fleet.owned(user, "cutlass"))

    def test_overdue_salvage_is_paid_before_wipe_not_after_it(self):
        current = space.now()
        user = _default_user(0)
        doc = {"users": {"123": user}}
        started = current - dt.timedelta(days=1)
        space.normalize(user)["surveys"]["mars"] = started.isoformat()
        fleet.ship(user, "vulture").update(owned=True, deployed=True, payload=1)
        fleet.start(doc, 123, "vulture", "salvage", "mars", started, "TEST")
        summary = ThorCog._wipe_target(user, {}, defender_id=123, doc=doc, current=current)
        self.assertGreater(summary["visible"], 0)
        fleet.settle_world(doc, current + dt.timedelta(hours=1))
        self.assertEqual(user["donuts"], 0)
        self.assertTrue(fleet.owned(user, "vulture"))

    def test_future_offworld_salvage_still_earns_its_reward(self):
        current = space.now()
        user = _default_user(0)
        doc = {"users": {"123": user}}
        space.normalize(user)["surveys"]["mars"] = current.isoformat()
        fleet.ship(user, "vulture").update(owned=True, deployed=True, payload=1)
        fleet.start(doc, 123, "vulture", "salvage", "mars", current, "TEST-FUTURE")
        ThorCog._wipe_target(user, {}, defender_id=123, doc=doc, current=current)
        self.assertIsNotNone(fleet.ship(user, "vulture")["mission"])
        fleet.settle_world(doc, current + dt.timedelta(days=1))
        self.assertGreater(user["donuts"], 0)

    def test_legacy_transport_loading_keeps_airborne_evidence(self):
        user = _default_user(0)
        space.normalize(user).update(
            transport_owned=True,
            load_until=(space.now() + dt.timedelta(hours=1)).isoformat(),
            load_manifest={},
        )
        ThorCog._wipe_target(user, {})
        self.assertTrue(fleet.owned(user, "transport"))


class SharedServicesTests(unittest.TestCase):
    def test_wallet_transfer_preserves_arbitrary_precision_and_rejects_overdraft(self):
        donor, recipient = ({"donuts": 10**400 + 1}, {"donuts": 2})
        currency.transfer_wallet(donor, recipient, 10**400)
        self.assertEqual((donor["donuts"], recipient["donuts"]), (1, 10**400 + 2))
        before = copy.deepcopy((donor, recipient))
        with self.assertRaises(ValueError):
            currency.transfer_wallet(donor, recipient, 2)
        self.assertEqual((donor, recipient), before)

    def test_theft_credit_is_not_an_incoming_attack(self):
        for reason in ("steal-success", "robbank-success", "safecrack-success"):
            self.assertIsNone(
                incoming_security_actor({"reason": reason, "user": 123, "other": 456, "delta": 500}, 123)
            )
            self.assertEqual(
                incoming_security_actor({"reason": reason, "user": 456, "other": 123, "delta": -500}, 456),
                123,
            )

    def test_decimal_settings_reject_nonfinite_or_invalid_numbers(self):
        key = "economy.vehicle_u2_cooldown_hours"
        for value in ("bad", "nan", "inf", "-inf"):
            self.assertFalse(ConfigManager.coerce(key, value)[0])
        self.assertEqual(ConfigManager.coerce(key, "1.5"), (True, 1.5, None))


class CommandStabilizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_arsenal_defers_only_once_and_stays_private(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            interaction = _FakeInteraction()
            defer = interaction.response.defer

            async def strict_defer(**kwargs):
                self.assertFalse(interaction.response.is_done(), "Never acknowledge twice")
                await defer(**kwargs)

            interaction.response.defer = strict_defer
            await EconomyCog.arsenal.callback(cog, interaction)
            self.assertEqual(interaction.response.deferred, {"thinking": True, "ephemeral": True})
            self.assertEqual(len(interaction.original_edits), 1)
            self.assertFalse(interaction.followup.calls)
            interaction.original_edits[0]["view"].stop()

    async def test_incoming_recovery_reserves_vehicle_without_charging_new_build(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            user = bot.economy.user(1, 123, 0)
            user["donuts"] = 10**15
            continuity.normalize(user).update(
                owned=True, vehicle={"model": "b52", "state": {"owned": True, "ammo": 0}}
            )
            await ContinuityCog.retrieve.callback(
                ContinuityCog(bot), _FakeInteraction(), app_commands.Choice(name="Vehicle", value="vehicle")
            )
            before = user["donuts"]
            with patch("discord.ext.tasks.Loop.start"):
                cog = EconomyCog(bot)
            await EconomyCog.vehicle_build.callback(
                cog, _FakeInteraction(), app_commands.Choice(name="B-52", value="b52")
            )
            self.assertEqual(user["donuts"], before)
            self.assertFalse(user["vehicles"]["b52"]["building_until"])

    async def test_cinder_invalidates_open_blackjack_and_both_casino_pots(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = Blackjack(bot)
            user = bot.economy.user(1, 123, 0)
            user["donuts"] = 1000
            view = BlackjackView(
                cog,
                _FakeInteraction(),
                1000,
                [Card("K", "S"), Card("Q", "H"), Card("10", "D"), Card("7", "C")],
                {},
            )
            doc = bot.economy.store.load(1)
            doc.update(roulette_jackpot=777, wheel_pot=888)
            ImperialStarDestroyerCog(bot)._apply_cinder_wipe(doc, {"guild_id": 1})
            await view._resolve()
            self.assertEqual(doc["users"]["123"]["donuts"], 0)
            self.assertTrue(view.finished)
            self.assertEqual((doc["roulette_jackpot"], doc["wheel_pot"]), (0, 0))

    async def test_isd_acknowledges_before_delayed_save(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            user = bot.economy.user(1, 123, 0)
            user["donuts"] = 10**30
            interaction = _FakeInteraction()
            original = bot.economy.save

            async def slow_save(gid):
                self.assertTrue(interaction.response.is_done())
                await asyncio.sleep(0.01)
                await original(gid)

            bot.economy.save = slow_save
            await ImperialStarDestroyerCog.fabricate.callback(
                ImperialStarDestroyerCog(bot),
                interaction,
                app_commands.Choice(name="Shipyard", value="shipyard"),
            )
            self.assertEqual(len(interaction.original_edits), 1)
            for attachment in interaction.original_edits[0].get("attachments", []):
                attachment.close()

    async def test_config_import_rejects_bad_schema_before_confirmation_or_mutation(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            before = copy.deepcopy(bot.config.for_guild(1).raw())
            file = SimpleNamespace(
                size=100,
                filename="bad.json",
                read=AsyncMock(return_value=json.dumps({"economy": {"thor_chamber_hours": "bad"}}).encode()),
            )
            interaction = _FakeInteraction()
            await Settings.import_cmd.callback(Settings(bot), interaction, file)
            self.assertTrue(interaction.response.is_done())
            self.assertEqual(bot.config.for_guild(1).raw(), before)
            self.assertNotIn("view", interaction.original_edits[-1])


from scripts.render_public_catalog import render
