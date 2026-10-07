"""Offline regressions for the Solar System extension."""

from __future__ import annotations
import datetime as dt
import copy
import unittest
from cogs.imperial_star_destroyer import ImperialStarDestroyerCog
from cogs.space import SpaceCog
from cogs.thor import ThorCog
from szofie import space
from szofie.economy import Economy, _default_user


class SpaceStateTests(unittest.TestCase):
    def test_cutlass_completion_and_craft_gate_are_restart_safe(self):
        user = _default_user(0)
        state = space.normalize(user)
        state["cutlass_build_until"] = (space.now() - dt.timedelta(seconds=1)).isoformat()
        self.assertTrue(space.settle(user))
        self.assertIsNone(space.exploration_craft(user))
        from szofie import space_fleet as fleet

        current = space.now()
        fleet.launch(user, "cutlass", current, "launch")
        fleet.settle_hulls(user, current + dt.timedelta(minutes=30))
        self.assertEqual(space.exploration_craft(user), "cutlass")
        self.assertFalse(space.settle(user))
        from szofie import imperial_star_destroyer as isd

        ship = isd.normalize(user)
        ship["operational"] = True
        self.assertIsNone(space.exploration_craft(user))
        self.assertIn("refit", SpaceCog.next_step(state, user))
        state["mode"] = "expedition"
        self.assertEqual(space.exploration_craft(user), "isd")
        ship["damaged"] = True
        self.assertIsNone(space.exploration_craft(user))
        self.assertIn("repair", SpaceCog.next_step(state, user))

    def test_auto_materials_cap_and_fraction_do_not_replay(self):
        current = space.now()
        site = {
            "mine": True,
            "material_at": (current - dt.timedelta(days=20)).isoformat(),
            "specialisation": "industrial",
            "materials": {},
        }
        target = space.BODIES["mars"]
        count, changed = space.settle_materials(site, target, current)
        self.assertTrue(changed)
        self.assertEqual(count, 11 * 125 * 28 // 100)
        before = copy.deepcopy(site)
        self.assertEqual(space.settle_materials(site, target, current), (0, False))
        self.assertEqual(site, before)
        count, _ = space.settle_materials(site, target, current + dt.timedelta(hours=6))
        self.assertEqual(count, 13)
        self.assertEqual(site["material_remainder"], 75)

    def test_old_mines_start_now_without_retroactive_free_materials(self):
        current = space.now()
        site = {"mine": True, "mine_at": (current - dt.timedelta(days=50)).isoformat(), "materials": {}}
        self.assertEqual(space.settle_materials(site, space.BODIES["mars"], current), (0, True))
        self.assertEqual(site["materials"], {})

    def test_civilian_boost_only_affects_wallet_production_not_gdp(self):
        current = space.now()
        user = _default_user(0)
        site = {
            "gdp": 8000,
            "specialisation": "civilian",
            "income_at": (current - dt.timedelta(days=1)).isoformat(),
        }
        self.assertEqual(space.settle_colony_income(user, site, current), (120, True))
        self.assertEqual(user["donuts"], 120)
        self.assertEqual(site["gdp"], 8000)
        self.assertEqual(space.settle_colony_income(user, site, current), (0, False))

    def test_missions_are_once_only_and_grant_unique_world_milestones(self):
        user = _default_user(0)
        state = space.normalize(user)
        state["cutlass_owned"] = True
        current = space.now()
        doc = {"users": {"1": user}}
        for index, target in enumerate(list(space.BODIES.values())[1:]):
            if target.key == "earth":
                continue
            state["location"] = target.key
            record = space.start_expedition(user, target, current, f"seed-{index}")
            before = copy.deepcopy(record)
            self.assertFalse(space.settle_expedition(user, doc, current))
            self.assertEqual(record, before)
            self.assertTrue(space.settle_expedition(user, doc, current + dt.timedelta(hours=6)))
            bag = copy.deepcopy(state["field_materials"])
            self.assertFalse(space.settle_expedition(user, doc, current + dt.timedelta(days=10)))
            self.assertEqual(state["field_materials"], bag)
            if len(state["journal"]) == 25:
                break
        self.assertIn("pathfinder", user["titles"])
        self.assertIn("space_pathfinder", user["badges"])
        self.assertEqual(user["plushies"]["explorer21"], 1)
        self.assertIn("solarsurveyor", user["titles"])
        self.assertEqual(state["milestones"], [3, 10, 25])

    def test_destroyed_body_cancels_mission_and_thor_spares_offworld_starter_hull(self):
        from szofie import deathstar

        user = _default_user(0)
        state = space.normalize(user)
        state.update(cutlass_owned=True, location="mars")
        doc = {"users": {"1": user}}
        record = space.start_expedition(user, space.BODIES["mars"], space.now(), "destroyed")
        deathstar.world(doc)["destroyed"]["mars"] = {"at": space.now().isoformat()}
        self.assertTrue(space.settle_expedition(user, doc, space.at(record["ready_at"])))
        self.assertFalse(state["journal"])
        self.assertFalse(state["field_materials"])
        state["field_materials"]["metals"] = 8
        ThorCog._wipe_target(user, {})
        self.assertTrue(state["cutlass_owned"])
        self.assertFalse(state["field_materials"])

    def test_assembly_cannot_finish_while_defence_window_is_active(self):
        from szofie import imperial_star_destroyer as isd

        user = _default_user(0)
        ship = isd.normalize(user)
        ship.update(
            assembling_until=(space.now() - dt.timedelta(days=1)).isoformat(), active_window_id="TEST"
        )
        self.assertFalse(isd.settle(user))
        self.assertFalse(ship["operational"])
        ship["active_window_id"] = None
        self.assertTrue(isd.settle(user))
        self.assertTrue(ship["operational"])

    def test_major_catalog_is_unique_and_classifies_orbit_only_bodies(self) -> None:
        self.assertEqual(len(space.BODIES), len(space._ROWS))
        for key in (
            "mercury",
            "venus",
            "earth",
            "mars",
            "jupiter",
            "saturn",
            "uranus",
            "neptune",
            "ceres",
            "pluto",
            "haumea",
            "makemake",
            "eris",
            "luna",
            "titan",
            "psyche",
        ):
            self.assertIn(key, space.BODIES)
        self.assertFalse(space.BODIES["jupiter"].landable)
        self.assertFalse(space.BODIES["sun"].landable)
        self.assertTrue(space.BODIES["ceres"].landable)
        self.assertGreater(space.daily_income(space.gdp(8, 4)), 0)

    def test_refit_loading_and_travel_settle_once(self) -> None:
        user = _default_user(0)
        state = space.normalize(user)
        past = (space.now() - dt.timedelta(hours=1)).isoformat()
        state.update(
            refit_until=past,
            refit_target="expedition",
            transport_build_until=past,
            load_until=past,
            load_manifest={"donuts": 50, "inventory": {"lock": 2}, "plushies": {"dealer": 1}, "vehicles": {}},
            travel_until=past,
            destination="mars",
        )
        self.assertTrue(space.settle(user))
        self.assertEqual(state["mode"], "expedition")
        self.assertTrue(state["transport_owned"])
        self.assertEqual(state["cargo"]["donuts"], 50)
        self.assertEqual(state["cargo"]["inventory"]["lock"], 2)
        self.assertEqual(state["location"], "mars")
        self.assertFalse(space.settle(user))
        self.assertEqual(state["cargo"]["donuts"], 50)
        state.update(refit_until=past, refit_target="attack")
        space.settle(user)
        self.assertEqual(state["mode"], "attack")

    def test_planetary_wipe_is_local_and_cinder_catalog_is_separate(self) -> None:
        doc = {"users": {"1": _default_user(0), "2": _default_user(0)}}
        galaxy = space.galaxy(doc)
        galaxy["claims"].update(mars=1, ceres=2)
        for user in doc["users"].values():
            state = space.normalize(user)
            state["colonies"]["mars"] = {"gdp": 100}
            state["colonies"]["ceres"] = {"gdp": 200}
        ImperialStarDestroyerCog._apply_planetary_wipe(doc, "mars")
        self.assertNotIn("mars", galaxy["claims"])
        self.assertEqual(galaxy["claims"]["ceres"], 2)
        for user in doc["users"].values():
            self.assertNotIn("mars", space.normalize(user)["colonies"])
            self.assertIn("ceres", space.normalize(user)["colonies"])

    def test_space_materials_are_consumed_without_creating_any(self) -> None:
        colony = {"materials": {"metals": 8, "water-ice": 6}}
        self.assertFalse(space.consume_materials(colony, 15))
        self.assertEqual(space.material_total(colony), 14)
        self.assertTrue(space.consume_materials(colony, 10))
        self.assertEqual(space.material_total(colony), 4)

    def test_thor_does_not_turn_cargo_into_a_second_vault(self) -> None:
        victim = _default_user(0)
        state = space.normalize(victim)
        state["cargo"]["donuts"] = 500
        state["cargo"]["plushies"]["dealer"] = 1
        state["load_manifest"] = {
            "donuts": 100,
            "inventory": {"lock": 2},
            "plushies": {},
            "fish": {},
            "rods": {},
            "vehicles": {},
        }
        state["colonies"]["mars"] = {"gdp": 100}
        summary = ThorCog._wipe_target(victim, {})
        self.assertEqual(summary["visible"], 600)
        self.assertEqual(summary["plushies"], 1)
        self.assertEqual(state["cargo"]["donuts"], 0)
        self.assertIsNone(state["load_manifest"])
        self.assertIn("mars", state["colonies"])

    def test_commands_are_registered(self) -> None:
        names = {command.name for command in SpaceCog.space_group.commands}
        self.assertTrue(
            {
                "refit",
                "load",
                "intercept",
                "travel",
                "survey",
                "land",
                "build-site",
                "mine",
                "terraform",
                "claim",
                "develop",
                "bombard",
                "discover",
                "colony",
                "stow",
                "offload",
            }.issubset(names)
        )


if __name__ == "__main__":
    unittest.main()
