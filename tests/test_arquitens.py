"""Arquitens combat, web assets, persistence and cross-fleet timing regressions."""

import asyncio
import copy
import datetime as dt
import hashlib
import json
import random
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from discord import app_commands
from cogs.space import SpaceCog
from cogs.imperial_star_destroyer import ImperialStarDestroyerCog
from szofie import coalitions, imperial_star_destroyer as isd, space, space_fleet as fleet
from szofie.economy import _default_user
from tests import test_space_fleet as existing_fleet
from tests.test_system_integration import _FakeBot, _FakeInteraction, _FakeUser


class ArquitensTests(unittest.TestCase):
    setUp = existing_fleet.FleetTests.setUp
    user = existing_fleet.FleetTests.user
    ready = existing_fleet.FleetTests.ready
    intel = existing_fleet.FleetTests.intel
    allies = existing_fleet.FleetTests.allies

    def setup_attack(self):
        self.ready("arquitens")
        self.ready("prospector", 2)["location"] = "mars"
        self.ready("vulture", 2)["location"] = "mars"
        self.intel()

    def launch(self, token="test", **kwargs):
        return fleet.start(
            self.doc,
            1,
            "arquitens",
            "broadside",
            "mars",
            self.now,
            token,
            target_uid=2,
            objective="ship:prospector",
            **kwargs,
        )

    def seed(self, hit=True, threshold=70, retaliation=None):
        for i in range(10000):
            token = str(i)
            rolls = [random.Random(token + "-ship:" + k).randint(1, 100) for k in ("prospector", "vulture")]
            if not all(((r <= threshold) == hit for r in rolls)):
                continue
            if (
                retaliation is not None
                and (random.Random(token + "-damage").randint(1, 100) <= 30) != retaliation
            ):
                continue
            return token
        self.fail("No deterministic test seed found")

    def test_cost_build_payload_and_repair(self):
        user = self.user()
        user.update(donuts=100 * fleet.Q, bank=600 * fleet.Q)
        self.assertEqual(fleet.commission(user, "arquitens", self.now), 600 * fleet.Q)
        self.assertEqual((user["donuts"], user["bank"]), (0, 100 * fleet.Q))
        due = self.now + dt.timedelta(hours=20)
        self.assertFalse(fleet.owned(user, "arquitens"))
        fleet.settle_world(self.doc, due)
        self.assertTrue(fleet.owned(user, "arquitens"))
        self.assertEqual(fleet.prepare(user, "arquitens", 4, due), 40 * fleet.Q)
        fleet.settle_world(self.doc, due + dt.timedelta(minutes=29))
        self.assertEqual(fleet.payload(user, "arquitens"), 0)
        fleet.settle_world(self.doc, due + dt.timedelta(minutes=30))
        self.assertEqual(fleet.payload(user, "arquitens"), 4)
        with self.assertRaises(ValueError):
            fleet.prepare(user, "arquitens", 1, due)
        hull = fleet.ship(user, "arquitens")
        hull["damaged"] = True
        user["bank"] = 200 * fleet.Q
        self.assertEqual(fleet.repair(user, "arquitens", due), 150 * fleet.Q)
        fleet.settle_world(self.doc, due + dt.timedelta(hours=5))
        self.assertTrue(hull["damaged"])
        fleet.settle_world(self.doc, due + dt.timedelta(hours=6))
        self.assertFalse(hull["damaged"])

    def test_shortfall_is_visible_and_payment_is_atomic(self):
        user = self.user()
        user.update(donuts=1 * fleet.Q, bank=0)
        with self.assertRaisesRegex(ValueError, "600 quadrillion.*599 quadrillion"):
            fleet.commission(user, "arquitens", self.now)
        self.assertEqual(user["donuts"], fleet.Q)
        self.assertIsNone(fleet.build_until(user, "arquitens"))

    def test_two_independent_hits_damage_hulls_not_wealth(self):
        self.setup_attack()
        before = copy.deepcopy(self.user(2))
        record = self.launch(self.seed(), second_objective="ship:vulture")
        self.assertEqual(fleet.payload(self.user(), "arquitens"), 3)
        self.assertEqual(space.at(record["ready_at"]), self.now + dt.timedelta(hours=1))
        events = []
        fleet.settle_world(self.doc, space.at(record["ready_at"]), events=events)
        for key in ("prospector", "vulture"):
            self.assertTrue(fleet.ship(self.user(2), key)["damaged"])
            self.assertTrue(fleet.owned(self.user(2), key))
            self.assertEqual(fleet.payload(self.user(2), key), 0)
        self.assertEqual(self.user(2)["donuts"], before["donuts"])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["victim"], 2)
        after = copy.deepcopy(self.doc)
        self.assertFalse(fleet.settle_world(self.doc, space.at(record["ready_at"])))
        self.assertEqual(self.doc, after)

    def test_partial_hit_rolls_are_independent(self):
        self.setup_attack()
        token = next(
            (
                str(i)
                for i in range(500)
                if random.Random(str(i) + "-ship:prospector").randint(1, 100)
                <= 70
                < random.Random(str(i) + "-ship:vulture").randint(1, 100)
            )
        )
        self.launch(token, second_objective="ship:vulture")
        fleet.settle_world(self.doc, self.now + dt.timedelta(hours=1))
        self.assertTrue(fleet.ship(self.user(2), "prospector")["damaged"])
        self.assertFalse(fleet.ship(self.user(2), "vulture")["damaged"])

    def test_recon_and_valid_distinct_targets_are_required(self):
        self.setup_attack()
        for kwargs in (
            {"second_objective": "ship:prospector"},
            {"second_objective": "mine"},
            {"second_objective": "ship:isd"},
            {"second_objective": "ship:bwing"},
            {"second_objective": "ship:thor"},
        ):
            with self.assertRaises(ValueError):
                self.launch(**kwargs)
        self.assertEqual(fleet.payload(self.user(), "arquitens"), 4)
        fleet.normalize(self.user())["recon"]["2"]["expires_at"] = self.now.isoformat()
        with self.assertRaisesRegex(ValueError, "fresh Carrack"):
            self.launch()

    def test_wrong_body_and_travelling_industry_cannot_be_hit(self):
        self.setup_attack()
        fleet.ship(self.user(2), "prospector")["location"] = "psyche"
        with self.assertRaises(ValueError):
            self.launch()
        fleet.start(self.doc, 2, "prospector", "mine", "psyche", self.now, "industrial")
        self.assertNotIn("ship:prospector", fleet.broadside_objectives(self.user(2), "psyche", self.now))
        self.assertNotIn("ship:prospector", fleet.broadside_objectives(self.user(2), "mars", self.now))

    def test_coalition_at_launch_and_arrival_is_respected(self):
        self.setup_attack()
        self.allies()
        with self.assertRaises(ValueError):
            self.launch()
        coalitions.state(self.doc)["groups"].clear()
        self.launch(self.seed())
        self.allies()
        coalitions.remember_membership(self.doc, self.now + dt.timedelta(minutes=30))
        fleet.settle_world(self.doc, self.now + dt.timedelta(hours=1))
        self.assertFalse(fleet.ship(self.user(2), "prospector")["damaged"])

    def test_departed_target_is_not_followed(self):
        self.setup_attack()
        self.launch(self.seed())
        fleet.start(self.doc, 2, "prospector", "mine", "psyche", self.now, "escape")
        fleet.settle_world(self.doc, self.now + dt.timedelta(hours=1))
        self.assertFalse(fleet.ship(self.user(2), "prospector")["damaged"])
        self.assertTrue(fleet.ship(self.user(2), "prospector")["mission"])

    def test_cooldown_is_from_arrival_and_survives_recall(self):
        self.setup_attack()
        self.launch(self.seed(hit=False))
        impact = self.now + dt.timedelta(hours=1)
        fleet.settle_world(self.doc, impact)
        hull = fleet.ship(self.user(), "arquitens")
        self.assertEqual(space.at(hull["combat_until"]), self.now + dt.timedelta(hours=4))
        with self.assertRaisesRegex(ValueError, "cooldown"):
            fleet.start(
                self.doc,
                1,
                "arquitens",
                "broadside",
                "mars",
                impact,
                "again",
                target_uid=2,
                objective="ship:prospector",
            )
        patrol = fleet.escort(self.doc, 1, "arquitens", 1, "mars", impact, "patrol")
        hull.update(mission=None, location=patrol["body"])
        self.assertEqual(space.at(hull["combat_until"]), self.now + dt.timedelta(hours=4))
        fleet.settle_world(self.doc, self.now + dt.timedelta(hours=4))
        self.assertIsNone(hull["combat_until"])

    def test_patrol_needs_two_packs_and_has_two_responses(self):
        self.ready("arquitens", 2)
        self.ready("ywing")
        self.intel()
        site = space.colony(space.normalize(self.user(2)), "mars")
        site.update(base=True, mine=True)
        fleet.set_payload(self.user(2), "arquitens", 1)
        with self.assertRaisesRegex(ValueError, "two capacitor"):
            fleet.escort(self.doc, 2, "arquitens", 2, "mars", self.now, "patrol")
        fleet.set_payload(self.user(2), "arquitens", 4)
        patrol = fleet.escort(self.doc, 2, "arquitens", 2, "mars", self.now, "patrol")
        self.assertEqual(fleet.payload(self.user(2), "arquitens"), 2)
        token = next(
            (
                str(i)
                for i in range(500)
                if 50 < random.Random(str(i)).randint(1, 100) <= 80
                and random.Random(str(i) + "-damage").randint(1, 100) > 25
            )
        )
        for offset in (0, 1):
            fleet.start(
                self.doc,
                1,
                "ywing",
                "bomb",
                "mars",
                self.now + dt.timedelta(hours=offset),
                token,
                target_uid=2,
                objective="mine",
            )
            fleet.settle_world(self.doc, self.now + dt.timedelta(hours=offset + 1))
            self.assertNotIn("mine_disabled_until", site)
            self.assertEqual(patrol["responses_remaining"], 1 - offset)
        self.assertIsNone(fleet.ship(self.user(2), "arquitens")["mission"])

    def test_two_targets_only_consume_one_patrol_response(self):
        self.setup_attack()
        self.ready("arquitens", 2)
        patrol = fleet.escort(self.doc, 2, "arquitens", 2, "mars", self.now, "guard")
        self.launch(self.seed(hit=False, threshold=40, retaliation=False), second_objective="ship:vulture")
        fleet.settle_world(self.doc, self.now + dt.timedelta(hours=1))
        self.assertEqual(patrol["responses_remaining"], 1)
        self.assertFalse(fleet.ship(self.user(2), "prospector")["damaged"])
        self.assertFalse(fleet.ship(self.user(2), "vulture")["damaged"])

    def test_damaged_escort_is_not_resurrected(self):
        self.setup_attack()
        self.ready("arquitens", 2)
        fleet.escort(self.doc, 2, "arquitens", 2, "mars", self.now, "guard")
        self.assertIn("ship:arquitens", fleet.broadside_objectives(self.user(2), "mars", self.now))
        token = next(
            (str(i) for i in range(500) if random.Random(str(i) + "-ship:arquitens").randint(1, 100) <= 40)
        )
        fleet.start(
            self.doc,
            1,
            "arquitens",
            "broadside",
            "mars",
            self.now,
            token,
            target_uid=2,
            objective="ship:arquitens",
        )
        fleet.settle_world(self.doc, self.now + dt.timedelta(hours=1))
        hull = fleet.ship(self.user(2), "arquitens")
        self.assertTrue(hull["damaged"])
        self.assertIsNone(hull["mission"])

    def test_strongest_escort_never_stacks(self):
        for key in ("arquitens", "hammerhead", "awing", "xwing"):
            self.ready(key, 2)
            fleet.escort(self.doc, 2, key, 2, "mars", self.now, key)
        defenders = fleet._defenders(self.doc, 2, "mars", self.now)
        self.assertEqual([key for _, key, _ in defenders], ["hammerhead"])
        fleet._respond_escorts(defenders, "mars", self.now, "test")
        self.assertIsNone(fleet.ship(self.user(2), "hammerhead")["mission"])
        self.assertEqual(fleet.ship(self.user(2), "arquitens")["mission"]["responses_remaining"], 2)

    def test_all_ordinary_hulls_are_valid_when_docked(self):
        for key in fleet.CRAFT:
            self.ready(key, 2)["location"] = "mars"
        values = fleet.broadside_objectives(self.user(2), "mars", self.now)
        self.assertEqual(set(values), {"ship:" + key for key in fleet.CRAFT})
        self.assertNotIn("mine", values)

    def test_patrol_answers_fighter_raids_and_interdictor_once_per_encounter(self):
        for raider in ("xwing", "awing", "interdictor"):
            with self.subTest(raider=raider):
                self.setUp()
                self.ready("arquitens", 2)
                self.ready("prospector", 2)
                self.ready(raider)
                fleet.start(self.doc, 2, "prospector", "mine", "psyche", self.now, "convoy")
                patrol = fleet.escort(self.doc, 2, "arquitens", 2, "psyche", self.now, "patrol")
                result = fleet.raid(self.doc, 1, raider, 2, "prospector", self.now, "raid", roll=1)
                self.assertEqual(result["chance"], 40 if raider == "interdictor" else 35)
                self.assertEqual(patrol["responses_remaining"], 1)
                self.assertIs(fleet.ship(self.user(2), "arquitens")["mission"], patrol)

    def test_capacitors_use_local_depot_stock_without_duplication(self):
        self.ready("arquitens")["location"] = "mars"
        fleet.set_payload(self.user(), "arquitens", 0)
        site = space.colony(space.normalize(self.user()), "mars")
        site.update(base=True, ammo_depot=True)
        self.assertEqual(
            fleet.depot(self.user(), self.doc, "mars", "stock", "arquitens", 6, self.now), 60 * fleet.Q
        )
        self.assertEqual(fleet.depot(self.user(), self.doc, "mars", "load", "arquitens", 4, self.now), 0)
        self.assertEqual(fleet.payload(self.user(), "arquitens"), 4)
        self.assertEqual(site["ammo_stock"]["arquitens"], 2)
        with self.assertRaises(ValueError):
            fleet.depot(self.user(), self.doc, "mars", "load", "arquitens", 1, self.now)
        self.assertEqual(site["ammo_stock"]["arquitens"], 2)

    def test_second_objective_cannot_be_smuggled_into_other_missions(self):
        self.ready("ywing")
        self.intel()
        site = space.colony(space.normalize(self.user(2)), "mars")
        site.update(base=True, mine=True)
        with self.assertRaisesRegex(ValueError, "second objective"):
            fleet.start(
                self.doc,
                1,
                "ywing",
                "bomb",
                "mars",
                self.now,
                "invalid",
                target_uid=2,
                objective="mine",
                second_objective="ship:arquitens",
            )
        self.assertEqual(fleet.payload(self.user(), "ywing"), 3)

    def test_retaliation_requires_escort_and_zero_hits(self):
        for escort, damaged in ((True, True), (False, False)):
            with self.subTest(escort=escort):
                self.setUp()
                self.setup_attack()
                if escort:
                    self.ready("arquitens", 2)
                    fleet.escort(self.doc, 2, "arquitens", 2, "mars", self.now, "guard")
                self.launch(
                    self.seed(hit=False, threshold=70, retaliation=True), second_objective="ship:vulture"
                )
                fleet.settle_world(self.doc, self.now + dt.timedelta(hours=1))
                self.assertEqual(fleet.ship(self.user(), "arquitens")["damaged"], damaged)

    def test_thor_wipes_new_hull_and_builds_without_touching_orbital_hardware(self):
        self.setup_attack()
        self.launch()
        self.user()["thor"] = {"test_orbital": True}
        fleet.wipe(self.user())
        self.assertFalse(fleet.owned(self.user(), "arquitens"))
        self.assertEqual(fleet.payload(self.user(), "arquitens"), 0)
        self.assertEqual(self.user()["thor"], {"test_orbital": True})

    def test_mine_bomb_preserves_earned_cycles_and_pauses_outage(self):
        self.ready("ywing")
        self.intel()
        site = space.colony(space.normalize(self.user(2)), "mars")
        site.update(
            base=True, mine=True, material_at=(self.now - dt.timedelta(hours=12)).isoformat(), materials={}
        )
        token = next((str(i) for i in range(500) if random.Random(str(i)).randint(1, 100) <= 80))
        fleet.start(self.doc, 1, "ywing", "bomb", "mars", self.now, token, target_uid=2, objective="mine")
        fleet.settle_world(self.doc, self.now + dt.timedelta(hours=1))
        self.assertEqual(space.material_total(site), 22)
        self.assertEqual(
            space.settle_materials(site, space.BODIES["mars"], self.now + dt.timedelta(hours=3)), (0, False)
        )
        space.settle_materials(site, space.BODIES["mars"], self.now + dt.timedelta(hours=10))
        self.assertEqual(space.material_total(site), 33)

    def test_later_repair_does_not_retroactively_create_a_target(self):
        self.ready("ywing")
        self.ready("prospector", 2)["location"] = "mars"
        self.intel()
        fleet.start(
            self.doc,
            1,
            "ywing",
            "bomb",
            "mars",
            self.now,
            "before-repair",
            target_uid=2,
            objective="ship:prospector",
        )
        hull = fleet.ship(self.user(2), "prospector")
        hull.update(damaged=True, repair_until=(self.now + dt.timedelta(hours=2)).isoformat())
        online = copy.deepcopy(self.doc)
        offline = copy.deepcopy(self.doc)
        fleet.settle_world(online, self.now + dt.timedelta(hours=1))
        fleet.settle_world(online, self.now + dt.timedelta(hours=8))
        fleet.settle_world(offline, self.now + dt.timedelta(hours=8))
        self.assertEqual(online, offline)
        self.assertFalse(fleet.ship(offline["users"]["2"], "prospector")["damaged"])

    def test_expired_alliance_preserves_historical_escort(self):
        self.ready("ywing")
        self.ready("hammerhead", 3)
        self.intel()
        coalitions.state(self.doc)["groups"]["1"] = {
            "members": [2, 3],
            "created_at": self.now.isoformat(),
            "expires_at": (self.now + dt.timedelta(hours=2)).isoformat(),
        }
        coalitions.remember_membership(self.doc, self.now)
        site = space.colony(space.normalize(self.user(2)), "mars")
        site.update(base=True, mine=True)
        fleet.escort(self.doc, 3, "hammerhead", 2, "mars", self.now, "ally-guard")
        token = next((str(i) for i in range(500) if 45 < random.Random(str(i)).randint(1, 100) <= 80))
        fleet.start(self.doc, 1, "ywing", "bomb", "mars", self.now, token, target_uid=2, objective="mine")
        online = copy.deepcopy(self.doc)
        offline = copy.deepcopy(self.doc)
        fleet.settle_world(online, self.now + dt.timedelta(hours=1))
        for doc in (online, offline):
            coalitions.cleanup(doc, self.now + dt.timedelta(hours=8))
        for doc in (online, offline):
            fleet.settle_world(doc, self.now + dt.timedelta(hours=8))
        self.assertEqual(online, offline)
        self.assertNotIn("mine_disabled_until", space.normalize(offline["users"]["2"])["colonies"]["mars"])

    def test_treaty_history_does_not_grant_current_access(self):
        self.allies()
        coalitions.remember_membership(self.doc, self.now)
        coalitions.state(self.doc)["groups"]["1"]["members"] = [1]
        coalitions.remember_membership(self.doc, self.now + dt.timedelta(minutes=30))
        self.assertTrue(coalitions.are_allied_at(self.doc, 1, 2, self.now + dt.timedelta(minutes=15)))
        self.assertFalse(coalitions.are_allied_at(self.doc, 1, 2, self.now + dt.timedelta(hours=1)))
        self.assertFalse(coalitions.are_allied(self.doc, 1, 2))


class ArquitensCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_prices_roles_and_choices_are_complete(self):
        for param in SpaceCog.build.parameters:
            if param.name == "craft":
                self.assertEqual({choice.value for choice in param.choices}, set(fleet.CRAFT) | {"nyx"})
                for choice in param.choices:
                    self.assertIn("quadrillion", choice.name)
                    self.assertLessEqual(len(choice.name), 100)
        choices = {p.name: p.choices for p in SpaceCog.fleet_mission.parameters}
        self.assertIn("broadside", [c.value for c in choices["mission"]])
        self.assertIn("second_objective", choices)
        for command in (SpaceCog.fleet_escort, SpaceCog.fleet_recall):
            self.assertIn(
                "arquitens", [c.value for p in command.parameters if p.name == "craft" for c in p.choices]
            )
        pages = SpaceCog.fleet_pages()
        self.assertTrue(all((len(page) <= 6000 for page in pages)))
        text = "\n".join((p.description for p in pages))
        for phrase in ("70%", "30 points", "TWO encounters", "150 quadrillion", "second_objective"):
            self.assertIn(phrase, text)

    async def test_build_acknowledges_before_saving_and_uses_authentic_art(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = SpaceCog(bot)
            interaction = _FakeInteraction()
            bot.economy.user(1, 123, 0)["donuts"] = 1000 * fleet.Q
            original = bot.economy.save

            async def save(gid):
                self.assertTrue(interaction.response.is_done())
                await original(gid)

            bot.economy.save = save
            await SpaceCog.build.callback(
                cog, interaction, app_commands.Choice(name="Cruiser", value="arquitens")
            )
            reply = interaction.response.calls[-1]
            bot.economy.store._cache.clear()
            restored = bot.economy.user(1, 123, 0)
            self.assertTrue(fleet.build_until(restored, "arquitens"))

    async def test_broadside_autocomplete_and_command_persist_two_targets(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = SpaceCog(bot)
            user = bot.economy.user(1, 123, 0)
            victim = bot.economy.user(1, 456, 0)
            space.normalize(user)["surveys"]["mars"] = space.now().isoformat()
            fleet.ship(user, "arquitens")["owned"] = True
            fleet.set_payload(user, "arquitens", 4)
            fleet.ship(user, "arquitens")["deployed"] = True
            for key in ("prospector", "vulture"):
                fleet.ship(victim, key).update(owned=True, location="mars")
            fleet.normalize(user)["recon"]["456"] = {
                "body": "mars",
                "expires_at": (space.now() + dt.timedelta(hours=4)).isoformat(),
                "rows": [],
            }
            interaction = _FakeInteraction()
            target = _FakeUser(456)
            interaction.namespace = SimpleNamespace(
                target=target, body="mars", mission="broadside", objective="ship:prospector"
            )
            self.assertEqual(
                [c.value for c in await cog.fleet_second_objectives(interaction, "")], ["ship:vulture"]
            )
            await SpaceCog.fleet_mission.callback(
                cog,
                interaction,
                app_commands.Choice(name="Cruiser", value="arquitens"),
                app_commands.Choice(name="Broadside", value="broadside"),
                "mars",
                target=target,
                objective="ship:prospector",
                second_objective="ship:vulture",
            )
            bot.economy.store._cache.clear()
            record = fleet.ship(bot.economy.user(1, 123, 0), "arquitens")["mission"]
            self.assertEqual(record["objectives"], ["ship:prospector", "ship:vulture"])

    async def test_wipe_waits_for_existing_writer_then_persists_wiped_hulls(self):
        await self.check_wipe_serialization("cinder")

    async def test_planetary_wipe_waits_for_writer_and_retains_personal_hulls(self):
        await self.check_wipe_serialization("planetary")

    async def check_wipe_serialization(self, kind):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            bot.get_channel = lambda _id: None
            cog = ImperialStarDestroyerCog(bot)
            cog._write_backup = lambda *_args: None
            victim = bot.economy.user(1, 456, 0)
            victim["donuts"] = 999
            fleet.ship(victim, "arquitens")["owned"] = True
            space.colony(space.normalize(victim), "mars").update(base=True)
            bot.economy.user(1, 123, 0)
            doc = bot.economy.store.load(1)
            isd.windows(doc)["audit"] = {
                "kind": kind,
                "body": "mars",
                "builder": 123,
                "guild_id": 1,
                "channel_id": 0,
                "remaining_seconds": 0,
                "announced": True,
                "interceptors": [],
                "raven_snapshots": {},
            }
            entered, release = (threading.Event(), threading.Event())
            original = bot.economy.store._write

            def gated(path, payload):
                if threading.current_thread() is not threading.main_thread():
                    entered.set()
                    if not release.wait(3):
                        raise TimeoutError("test writer not released")
                original(path, payload)

            bot.economy.store._write = gated
            save = asyncio.create_task(bot.economy.save(1))
            resolve = None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                resolve = asyncio.create_task(cog._resolve_window(1, "audit"))
                await asyncio.sleep(0.01)
                self.assertFalse(resolve.done())
                release.set()
                await save
                await resolve
            finally:
                release.set()
                await asyncio.gather(save, *([resolve] if resolve else []), return_exceptions=True)
            disk = json.loads((Path(raw) / "economy" / "1.json").read_text())
            self.assertEqual(disk["users"]["456"]["donuts"], 0 if kind == "cinder" else 999)
            self.assertEqual(fleet.owned(disk["users"]["456"], "arquitens"), kind != "cinder")
            self.assertNotIn("mars", space.normalize(disk["users"]["456"])["colonies"])
            self.assertNotIn("audit", disk.get("isd_windows", {}))
