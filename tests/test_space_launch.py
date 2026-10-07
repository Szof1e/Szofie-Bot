"""Earth deployment, launch counterplay and legacy-route regression coverage."""

import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from discord import app_commands
from cogs.space import SpaceCog
from szofie import coalitions, imperial_star_destroyer as isd, space, space_fleet as fleet
from szofie.economy import _default_user
from szofie.ledger import incoming_security_actor, Ledger
from tests.test_system_integration import _FakeBot, _FakeInteraction, _FakeUser


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.now = space.now()
        self.doc = {"users": {str(uid): _default_user(10000 * fleet.Q) for uid in (1, 2, 3, 4, 5)}}

    def user(self, uid=1):
        return self.doc["users"][str(uid)]

    def ready(self, key, uid=1, deployed=False):
        user = self.user(uid)
        hull = fleet.ship(user, key)
        if key in fleet.LEGACY:
            space.normalize(user)[f"{key}_owned"] = True
        else:
            hull["owned"] = True
        hull["deployed"] = deployed
        fleet.set_payload(user, key, fleet.CRAFT[key].capacity)
        return hull

    def test_every_constructed_hull_requires_launch(self):
        for key, spec in fleet.CRAFT.items():
            with self.subTest(craft=key):
                fleet.commission(self.user(), key, self.now)
                fleet.settle_hulls(self.user(), self.now + dt.timedelta(hours=spec.build_hours))
                self.assertTrue(fleet.owned(self.user(), key))
                self.assertFalse(fleet.deployed(self.user(), key))
                self.assertEqual(fleet.ship(self.user(), key)["location"], "earth")
                with self.assertRaisesRegex(ValueError, "Launch"):
                    fleet.start(self.doc, 1, key, "survey", "mars", self.now, "blocked")

    def test_launch_completion_is_absolute_restart_safe_and_once_only(self):
        for key in fleet.CRAFT:
            self.ready(key)
            record = fleet.launch(self.user(), key, self.now, "launch-" + key)
            self.assertEqual(space.at(record["ready_at"]), self.now + dt.timedelta(minutes=30))
        self.doc = json.loads(json.dumps(self.doc))
        self.assertFalse(fleet.settle_world(self.doc, self.now + dt.timedelta(minutes=29)))
        self.assertTrue(fleet.settle_world(self.doc, self.now + dt.timedelta(days=2)))
        self.assertTrue(all((fleet.deployed(self.user(), k) for k in fleet.CRAFT)))
        before = copy.deepcopy(self.doc)
        self.assertFalse(fleet.settle_world(self.doc, self.now + dt.timedelta(days=3)))
        self.assertEqual(before, self.doc)

    def test_launch_rejects_not_owned_busy_damaged_duplicate_and_non_earth(self):
        with self.assertRaises(ValueError):
            fleet.launch(self.user(), "prospector", self.now, "no-hull")
        hull = self.ready("prospector")
        for field, value in (
            ("deployed", True),
            ("location", "mars"),
            ("damaged", True),
            ("repair_until", self.now.isoformat()),
            ("payload_until", self.now.isoformat()),
            ("mission", {"kind": "mine"}),
        ):
            before = copy.deepcopy(hull)
            hull[field] = value
            with self.assertRaises(ValueError):
                fleet.launch(self.user(), "prospector", self.now, field)
            hull.clear()
            hull.update(before)
        fleet.launch(self.user(), "prospector", self.now, "first")
        with self.assertRaises(ValueError):
            fleet.launch(self.user(), "prospector", self.now, "again")
        with self.assertRaises(ValueError):
            fleet.prepare(self.user(), "prospector", 1, self.now)

    def test_grounded_fighters_cannot_escort_raid_or_counter_launch(self):
        self.ready("xwing")
        self.ready("prospector", 2, True)
        state = space.normalize(self.user())
        state["surveys"]["mars"] = self.now.isoformat()
        state2 = space.normalize(self.user(2))
        state2["surveys"]["psyche"] = self.now.isoformat()
        fleet.start(self.doc, 2, "prospector", "mine", "psyche", self.now, "mine")
        with self.assertRaisesRegex(ValueError, "Launch"):
            fleet.escort(self.doc, 1, "xwing", 1, "mars", self.now, "escort")
        with self.assertRaisesRegex(ValueError, "Launch"):
            fleet.raid(self.doc, 1, "xwing", 2, "prospector", self.now, "raid")
        self.ready("vulture", 2)
        fleet.launch(self.user(2), "vulture", self.now, "launch")
        with self.assertRaisesRegex(ValueError, "Launch"):
            fleet.launch_intercept(self.doc, 1, 2, "vulture", "xwing", self.now, "attack")

    def test_interception_chances_and_single_payload_consumption(self):
        for key in ("prospector", "arquitens"):
            for counter in ("xwing", "awing"):
                with self.subTest(target=key, counter=counter):
                    self.ready(key, 2)
                    self.ready(counter, 1, True)
                    fleet.launch(self.user(2), key, self.now, key + counter)
                    before = fleet.payload(self.user(), counter)
                    result = fleet.launch_intercept(self.doc, 1, 2, key, counter, self.now, "miss", roll=100)
                    expected = fleet.LAUNCH_COUNTERS[counter] - (15 if key in fleet.HEAVY_CRAFT else 0)
                    self.assertEqual(result["chance"], expected)
                    self.assertFalse(result["hit"])
                    self.assertEqual(fleet.payload(self.user(), counter), before - 1)
                    with self.assertRaises(ValueError):
                        fleet.launch_intercept(self.doc, 1, 2, key, counter, self.now, "repeat")
                    fleet.ship(self.user(2), key)["launch"] = None
                    fleet.ship(self.user(), counter)["mission"] = None

    def test_hit_preserves_hull_wealth_projects_and_requires_repair_relaunch(self):
        self.ready("xwing", 1, True)
        hull = self.ready("arquitens", 2)
        isd.normalize(self.user(2))["counter_stock"] = 2
        wealth = copy.deepcopy(self.user(2))
        fleet.launch(self.user(2), "arquitens", self.now, "launch")
        result = fleet.launch_intercept(self.doc, 1, 2, "arquitens", "xwing", self.now, "hit", roll=1)
        self.assertTrue(result["hit"])
        self.assertTrue(fleet.owned(self.user(2), "arquitens"))
        self.assertTrue(hull["damaged"])
        self.assertFalse(fleet.deployed(self.user(2), "arquitens"))
        self.assertEqual(fleet.payload(self.user(2), "arquitens"), 0)
        for field in (
            "donuts",
            "bank",
            "inventory",
            "plushies",
            "vehicles",
            "thor",
            "imperial_star_destroyer",
        ):
            self.assertEqual(self.user(2).get(field), wealth.get(field))
        with self.assertRaises(ValueError):
            fleet.launch(self.user(2), "arquitens", self.now, "blocked")
        self.assertEqual(fleet.repair(self.user(2), "arquitens", self.now), 150 * fleet.Q)
        fleet.settle_world(self.doc, self.now + dt.timedelta(hours=6))
        self.assertFalse(fleet.deployed(self.user(2), "arquitens"))
        fleet.launch(self.user(2), "arquitens", self.now + dt.timedelta(hours=6), "relaunch")
        fleet.settle_world(self.doc, self.now + dt.timedelta(hours=7))
        self.assertTrue(fleet.deployed(self.user(2), "arquitens"))

    def test_bwing_suits_only_heavy_launches_and_uses_existing_stock(self):
        self.ready("cutlass", 2)
        fleet.launch(self.user(2), "cutlass", self.now, "light")
        package = isd.normalize(self.user())
        package["counter_stock"] = 2
        with self.assertRaisesRegex(ValueError, "heavy"):
            fleet.launch_intercept(self.doc, 1, 2, "cutlass", "bwing", self.now, "bad")
        self.assertEqual(package["counter_stock"], 2)
        self.ready("hammerhead", 2)
        fleet.launch(self.user(2), "hammerhead", self.now, "heavy")
        result = fleet.launch_intercept(self.doc, 1, 2, "hammerhead", "bwing", self.now, "hit", roll=1)
        self.assertEqual(result["chance"], 40)
        self.assertEqual(package["counter_stock"], 1)

    def test_three_unique_attempts_and_no_self_or_allies(self):
        self.ready("vulture", 2)
        fleet.launch(self.user(2), "vulture", self.now, "window")
        for uid in (1, 3, 4, 5):
            self.ready("awing", uid, True)
        for uid in (1, 3, 4):
            fleet.launch_intercept(self.doc, uid, 2, "vulture", "awing", self.now, str(uid), roll=100)
        before = fleet.payload(self.user(5), "awing")
        with self.assertRaises(ValueError):
            fleet.launch_intercept(self.doc, 5, 2, "vulture", "awing", self.now, "fourth")
        self.assertEqual(fleet.payload(self.user(5), "awing"), before)
        with self.assertRaises(ValueError):
            fleet.launch_intercept(self.doc, 2, 2, "vulture", "awing", self.now, "self")
        coalitions.state(self.doc)["groups"]["g"] = {
            "members": [2, 5],
            "leader": 2,
            "name": "test",
            "expires_at": (self.now + dt.timedelta(days=1)).isoformat(),
        }
        with self.assertRaisesRegex(ValueError, "coalition"):
            fleet.launch_intercept(self.doc, 5, 2, "vulture", "awing", self.now, "ally")

    def test_closed_window_does_not_spend_stock(self):
        self.ready("xwing", 1, True)
        self.ready("prospector", 2)
        fleet.launch(self.user(2), "prospector", self.now, "window")
        coalitions.remember_membership(self.doc)
        before = copy.deepcopy(self.doc)
        with self.assertRaises(ValueError):
            fleet.launch_intercept(
                self.doc, 1, 2, "prospector", "xwing", self.now + dt.timedelta(minutes=30), "late"
            )
        self.assertEqual(self.doc, before)

    def test_old_deployed_missions_preserved_but_unused_hulls_stay_grounded(self):
        user = self.user()
        state = space.normalize(user)
        state["fleet"] = {
            "ships": {
                "prospector": {
                    "owned": True,
                    "mission": {
                        "id": "old",
                        "craft": "prospector",
                        "kind": "mine",
                        "body": "psyche",
                        "ready_at": (self.now + dt.timedelta(hours=3)).isoformat(),
                        "materials": {},
                    },
                    "location": "earth",
                },
                "vulture": {"owned": True, "location": "mars"},
                "arquitens": {"owned": True, "location": "earth"},
            }
        }
        self.assertTrue(fleet.deployed(user, "prospector"))
        self.assertTrue(fleet.deployed(user, "vulture"))
        self.assertFalse(fleet.deployed(user, "arquitens"))

    def test_cutlass_ground_gate_and_return_to_earth(self):
        self.ready("cutlass")
        self.assertIsNone(space.exploration_craft(self.user()))
        fleet.launch(self.user(), "cutlass", self.now, "launch")
        self.assertIsNone(space.exploration_craft(self.user()))
        fleet.settle_hulls(self.user(), self.now + dt.timedelta(minutes=30))
        self.assertEqual(space.exploration_craft(self.user()), "cutlass")
        space.normalize(self.user()).update(
            destination="earth", travel_until=self.now.isoformat(), travel_craft="cutlass"
        )
        space.settle(self.user(), self.now)
        self.assertFalse(fleet.deployed(self.user(), "cutlass"))

    def test_remote_isd_does_not_deploy_an_unused_ground_cutlass(self):
        user = self.user()
        state = space.normalize(user)
        state.update(cutlass_owned=True, location="mars")
        isd.normalize(user)["operational"] = True
        self.assertFalse(fleet.deployed(user, "cutlass"))

    def test_rebuilding_old_hull_does_not_inherit_previous_deployment(self):
        user = self.user()
        state = space.normalize(user)
        state["fleet"] = {
            "ships": {
                "prospector": {
                    "owned": False,
                    "location": "mars",
                    "build_until": (self.now + dt.timedelta(hours=1)).isoformat(),
                }
            },
            "history": [{"craft": "prospector"}],
        }
        self.assertFalse(fleet.deployed(user, "prospector"))
        fleet.settle_hulls(user, self.now + dt.timedelta(hours=1))
        self.assertFalse(fleet.deployed(user, "prospector"))
        self.assertEqual(fleet.ship(user, "prospector")["location"], "earth")
        fleet.launch(user, "prospector", self.now + dt.timedelta(hours=1), "new-launch")

    def test_successful_interception_only_enters_victim_attack_log(self):
        for reason in ("space-launch-intercept-hit", "space-launch-intercept-miss"):
            entry = dict(reason=reason, user=1, other=2)
            self.assertEqual(incoming_security_actor(entry, 2), 1 if reason.endswith("hit") else None)
            self.assertIsNone(incoming_security_actor(entry, 1))


class LaunchCommands(unittest.IsolatedAsyncioTestCase):
    async def test_launch_hit_is_retained_in_security_ledger(self):
        with tempfile.TemporaryDirectory() as raw:
            ledger = Ledger(Path(raw))
            await ledger.record(1, 1, 0, "space-launch-intercept-hit", other=2)
            await ledger.record(1, 3, 0, "space-launch-intercept-miss", other=2)
            incoming = await ledger.read_security(1, victim=2)
            self.assertEqual(len(incoming), 1)
            self.assertEqual(incoming[0]["security_actor"], 1)

    async def test_commands_acknowledge_persist_and_publish_only_launch_data(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = SpaceCog(bot)
            target = bot.economy.user(1, 456, 0)
            fighter = bot.economy.user(1, 123, 0)
            hull = fleet.ship(target, "arquitens")
            hull["owned"] = True
            space.normalize(fighter).update(xwing_owned=True, xwing_ammo=1)
            fleet.ship(fighter, "xwing")["deployed"] = True
            choice = app_commands.Choice(name=fleet.CRAFT["arquitens"].name, value="arquitens")
            launched = _FakeInteraction(user_id=456)
            with patch.object(cog, "send"):
                await SpaceCog.fleet_launch.callback(cog, launched, choice)
            self.assertTrue(launched.response.is_done())
            self.assertIsNotNone(hull["launch"])
            listing = _FakeInteraction()
            await SpaceCog.fleet_launches.callback(cog, listing)
            reply = listing.response.calls[-1]
            self.assertIn("Arquitens", reply["embed"].description)
            self.assertNotIn("donuts", reply["embed"].description)
            reply["view"].stop()
            interceptor = _FakeInteraction()
            with patch.object(cog, "send"), patch("szofie.space_fleet.random.Random") as rng:
                rng.return_value.randint.return_value = 1
                await SpaceCog.fleet_intercept.callback(
                    cog,
                    interceptor,
                    _FakeUser(456),
                    choice,
                    app_commands.Choice(name="X-wing", value="xwing"),
                )
            self.assertTrue(interceptor.response.is_done())
            self.assertTrue(hull["damaged"])
            bot.economy.store._cache.clear()
            restored = bot.economy.user(1, 456, 0)
            self.assertTrue(fleet.ship(restored, "arquitens")["damaged"])
            self.assertIsNone(fleet.ship(restored, "arquitens")["launch"])

    async def test_grounded_transport_and_industrial_survey_do_not_bypass_launch(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = SpaceCog(bot)
            user = bot.economy.user(1, 123, 0)
            user["donuts"] = 1000 * fleet.Q
            space.normalize(user).update(transport_owned=True, mode="expedition")
            isd.normalize(user)["operational"] = True
            blocked = _FakeInteraction()
            await SpaceCog.load.callback(
                cog, blocked, app_commands.Choice(name="Donuts", value="donuts"), "1qa"
            )
            self.assertIsNone(space.normalize(user)["load_until"])
            isd.normalize(user)["operational"] = False
            fleet.ship(user, "prospector")["owned"] = True
            blocked = _FakeInteraction()
            await SpaceCog.survey.callback(cog, blocked, "mars")
            self.assertNotIn("mars", space.normalize(user)["surveys"])


if __name__ == "__main__":
    unittest.main()
