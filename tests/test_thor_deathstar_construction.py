"""Flat THOR construction risk and narrowly scoped owner transport completion."""

import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from cogs.death_star import DeathStarCog
from cogs.thor import ThorCog
from szofie import deathstar as ds, guides, nyx, thor
from szofie.economy import _default_user
from szofie.storage import Storage
from tests.test_system_integration import _FakeBot, _FakeInteraction, _FakeUser


class ThorDeathStarConstructionTests(unittest.TestCase):
    def setUp(self):
        self.now = thor.utcnow()
        self.future = (self.now + dt.timedelta(hours=12)).isoformat()

    def building(self):
        user = _default_user(100)
        state = ds.normalize(user)
        for key in ("framework", "hull", "reactor"):
            state["components"][key].update(status="fabricating", ready_at=self.future)
        return user

    def test_exactly_one_of_one_hundred_rolls_destroys_at_most_one_section(self):
        for roll in range(1, 101):
            user = self.building()
            rng = Mock()
            rng.randint.return_value = roll
            rng.choice.side_effect = lambda parts: parts[0]
            loss = ds.thor_construction_impact(user, self.now, rng)
            rng.randint.assert_called_once_with(1, 100)
            self.assertEqual(loss, {"at_risk": 1, "destroyed": int(roll == 1)})
            remaining = sum(
                (part["status"] == "fabricating" for part in user["death_star"]["components"].values())
            )
            self.assertEqual(remaining, 2 if roll == 1 else 3)
            if roll == 1:
                self.assertEqual(user["death_star"]["components"]["framework"], ds.component())
            else:
                rng.choice.assert_not_called()

    def test_ready_launching_orbit_assembled_and_absent_sections_never_roll(self):
        for status in ("none", "ready", "launching", "orbit", "assembled"):
            user = _default_user(100)
            state = ds.normalize(user)
            for part in state["components"].values():
                part.update(status=status)
            before = copy.deepcopy(state)
            rng = Mock()
            self.assertEqual(ds.thor_construction_impact(user, self.now, rng), {"at_risk": 0, "destroyed": 0})
            rng.randint.assert_not_called()
            self.assertEqual(state, before)
        user = _default_user(0)
        ds.thor_construction_impact(user, self.now, Mock())
        self.assertNotIn("death_star", user)

    def test_overdue_fabrication_becomes_ready_before_the_roll(self):
        user = self.building()
        for part in user["death_star"]["components"].values():
            if part["status"] == "fabricating":
                part["ready_at"] = self.now.isoformat()
        rng = Mock()
        self.assertEqual(ds.thor_construction_impact(user, self.now, rng)["at_risk"], 0)
        rng.randint.assert_not_called()
        self.assertEqual(user["death_star"]["components"]["hull"]["status"], "ready")

    def test_destroyed_section_cannot_finish_after_restart_or_refund_itself(self):
        user = self.building()
        user["death_star"].update(owner_paid=987654321, project_funds=321)
        rng = Mock()
        rng.randint.return_value = 1
        rng.choice.side_effect = lambda parts: parts[0]
        ds.thor_construction_impact(user, self.now, rng)
        restored = json.loads(json.dumps(user))
        ds.settle(restored, self.now + dt.timedelta(days=7))
        self.assertEqual(restored["death_star"]["components"]["framework"], ds.component())
        self.assertEqual(restored["death_star"]["components"]["hull"]["status"], "ready")
        self.assertEqual(restored["death_star"]["owner_paid"], 987654321)
        self.assertEqual(restored["death_star"]["project_funds"], 321)
        self.assertEqual(restored["donuts"], 100)

    def test_nyx_degraded_categories_preserve_deathstar_construction(self):
        for category in nyx.CATEGORIES:
            user = self.building()
            nyx.normalize(user).update(
                status="orbit", active_until=(self.now + dt.timedelta(hours=3)).isoformat()
            )
            before = copy.deepcopy(user["death_star"]["components"])
            with (
                patch("cogs.thor.random.choice", return_value=category),
                patch("cogs.thor.random.randint") as roll,
            ):
                loss = ThorCog._wipe_target(user, {}, current=self.now)
            roll.assert_not_called()
            self.assertEqual(loss["ground_deathstar_destroyed"], 0)
            self.assertEqual(user["death_star"]["components"], before)


class ThorDeathStarCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_intercepted_rod_does_not_roll_construction_damage(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = ThorCog(bot)
            bot.config.for_guild(1).set("economy.thor_media", False)
            attacker = bot.economy.user(1, 123, 100)
            victim = bot.economy.user(1, 456, 100)
            thor.normalize(attacker).update(operational=True, rods=1, chambered=True)
            victim["vehicles"]["aegis"].update(owned=True, bmd_owned=True, sm3_ammo=2)
            ds.normalize(victim)["components"]["hull"].update(
                status="fabricating", ready_at=(thor.utcnow() + dt.timedelta(hours=12)).isoformat()
            )
            with (
                patch("cogs.thor.random.randint", return_value=1),
                patch.object(cog, "_wipe_target") as impact,
            ):
                await ThorCog.strike.callback(cog, _FakeInteraction(), _FakeUser(456))
            impact.assert_not_called()
            self.assertEqual(victim["death_star"]["components"]["hull"]["status"], "fabricating")
