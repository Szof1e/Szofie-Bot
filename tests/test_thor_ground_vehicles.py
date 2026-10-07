"""Location-based THOR loss tests using isolated state only."""

import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from cogs.thor import ThorCog
from szofie import deathstar, guides, imperial_star_destroyer as isd, nyx, space, space_fleet as fleet, thor
from szofie.economy import _default_user
from tests.test_system_integration import _FakeBot, _FakeInteraction, _FakeUser


class ThorGroundVehicleTests(unittest.TestCase):
    def setUp(self):
        self.now = space.now()
        self.future = (self.now + dt.timedelta(hours=1)).isoformat()

    def hull(self, user, key, *, building=False, **extra):
        hull = fleet.ship(user, key)
        hull.update(
            owned=not building,
            build_until=self.future if building else None,
            deployed=False,
            location="earth",
            payload=2,
            payload_qty=1,
            payload_until=self.future,
            damaged=True,
            repair_until=self.future,
        )
        hull.update(extra)
        if key in fleet.LEGACY:
            state = space.normalize(user)
            state[f"{key}_owned"] = not building
            state[f"{key}_build_until"] = self.future if building else None
        fleet.set_payload(user, key, 2)
        return hull

    def test_all_ground_hulls_builds_payloads_and_repairs_are_destroyed(self):
        for building in (False, True):
            for key in fleet.CRAFT:
                with self.subTest(craft=key, building=building):
                    user = _default_user(0)
                    self.hull(user, key, building=building)
                    losses = ThorCog._wipe_target(user, {})
                    self.assertEqual(losses["vehicles"], 1)
                    self.assertEqual(losses["ammo"], 3)
                    self.assertFalse(fleet.owned(user, key))
                    self.assertIsNone(fleet.build_until(user, key))
                    self.assertEqual(fleet.payload(user, key), 0)
                    fleet.settle_hulls(user, self.now + dt.timedelta(days=3))
                    self.assertFalse(fleet.owned(user, key))
                    self.assertEqual(fleet.payload(user, key), 0)

    def test_every_launched_or_offworld_hull_keeps_all_state(self):
        for mode in ("ascent", "earth_orbit", "mars"):
            for key in fleet.CRAFT:
                with self.subTest(craft=key, mode=mode):
                    user = _default_user(0)
                    hull = self.hull(user, key)
                    if mode == "ascent":
                        hull["launch"] = dict(
                            id="TEST",
                            craft=key,
                            kind="launch",
                            body="earth",
                            ready_at=self.future,
                            attempts=[],
                        )
                    elif mode == "earth_orbit":
                        hull["deployed"] = True
                    else:
                        hull["location"] = "mars"
                    before = copy.deepcopy(hull)
                    losses = ThorCog._wipe_target(user, {})
                    self.assertEqual(losses["vehicles"], 0)
                    self.assertEqual(losses["ammo"], 0)
                    self.assertTrue(fleet.owned(user, key))
                    self.assertEqual(fleet.payload(user, key), 2)
                    self.assertEqual(fleet.ship(user, key), before)
                    restored = json.loads(json.dumps(user))
                    self.assertEqual(fleet.ship(restored, key), before)

    def test_active_ascent_finishes_normally_after_impact(self):
        user = _default_user(0)
        hull = self.hull(user, "arquitens")
        hull.update(damaged=False, repair_until=None, payload_until=None, payload_qty=0)
        record = fleet.launch(user, "arquitens", self.now, "ASCENT")
        ThorCog._wipe_target(user, {})
        fleet.settle_hulls(user, space.at(record["ready_at"]))
        self.assertTrue(fleet.deployed(user, "arquitens"))
        self.assertEqual(fleet.payload(user, "arquitens"), 2)

    def test_aborted_launch_is_grounded_and_can_be_destroyed(self):
        user = _default_user(0)
        hull = self.hull(user, "arquitens")
        hull.update(launch=None, deployed=False, damaged=True)
        ThorCog._wipe_target(user, {})
        self.assertFalse(fleet.owned(user, "arquitens"))

    def test_launched_cutlass_travel_and_expedition_are_not_cancelled(self):
        user = _default_user(0)
        self.hull(user, "cutlass", deployed=True)
        state = space.normalize(user)
        state.update(
            travel_craft="cutlass",
            travel_until=self.future,
            destination="mars",
            expedition={"body": "mars", "ready_at": self.future},
        )
        before = copy.deepcopy(
            {k: state[k] for k in ("travel_craft", "travel_until", "destination", "expedition")}
        )
        ThorCog._wipe_target(user, {})
        self.assertEqual({k: state[k] for k in before}, before)
        fleet.settle_hulls(user, self.now + dt.timedelta(hours=2))
        self.assertEqual(fleet.ship(user, "cutlass")["location"], "mars")

    def test_launched_gr75_keeps_hull_but_earth_loading_escrow_is_destroyed(self):
        user = _default_user(0)
        self.hull(user, "transport", deployed=True)
        state = space.normalize(user)
        state.update(
            load_until=self.future, load_manifest={"donuts": 500, "vehicles": {"b2": {"owned": True}}}
        )
        loss = ThorCog._wipe_target(user, {})
        self.assertTrue(fleet.owned(user, "transport"))
        self.assertIsNone(state["load_until"])
        self.assertIsNone(state["load_manifest"])
        self.assertEqual(loss["vehicles"], 1)
        fleet.settle_hulls(user, self.now + dt.timedelta(days=2))
        self.assertFalse(state["cargo"]["donuts"])
        self.assertFalse(state["cargo"]["vehicles"])

    def test_carrier_vehicles_survive_but_other_cargo_still_wipes(self):
        user = _default_user(0)
        isd.normalize(user).update(operational=False, damaged=True)
        station = deathstar.normalize(user)
        station.update(operational=False, damaged=True)
        for hold in (space.normalize(user)["cargo"], station["cargo"]):
            hold.update(donuts=500, vehicles={"b2": {"owned": True, "armed": True}}, inventory={"hex": 1})
        loss = ThorCog._wipe_target(user, {})
        for hold in (space.normalize(user)["cargo"], station["cargo"]):
            self.assertEqual(hold["vehicles"], {"b2": {"owned": True, "armed": True}})
            self.assertEqual(hold["donuts"], 0)
            self.assertEqual(hold["inventory"], {})
        self.assertEqual(loss["vehicles"], 0)

    def test_heavy_transport_ground_build_and_stockpiles_are_destroyed(self):
        for building in (False, True):
            user = _default_user(0)
            station = deathstar.normalize(user)
            station.update(
                transport_owned=not building,
                transport_until=self.future if building else None,
                squadron_stock=2,
                squadron_until=self.future,
            )
            loss = ThorCog._wipe_target(user, {})
            self.assertEqual(loss["vehicles"], 1)
            self.assertEqual(loss["ammo"], 3)
            deathstar.settle(user, self.now + dt.timedelta(days=3))
            self.assertFalse(station["transport_owned"])
            self.assertEqual(station["squadron_stock"], 0)

    def test_legacy_launched_heavy_transport_is_migrated_and_preserved(self):
        user = _default_user(0)
        deathstar.normalize(user)
        user["death_star"].update(transport_owned=True)
        user["death_star"].pop("transport_deployed", None)
        user["death_star"]["components"]["hull"].update(status="orbit")
        station = deathstar.normalize(user)
        self.assertTrue(station["transport_deployed"])
        ThorCog._wipe_target(user, {})
        self.assertTrue(station["transport_owned"])

    def test_isd_destroy_roll_is_exactly_ten_percent_not_variable(self):
        for roll in range(1, 101):
            user = _default_user(0)
            part = isd.normalize(user)["components"]["shipyard"]
            part["status"] = "ready"
            with patch("cogs.thor.random.randint", return_value=roll) as rng:
                loss = ThorCog._wipe_target(user, {})
            rng.assert_called_once_with(1, 100)
            self.assertEqual(loss["ground_isd_destroyed"], int(roll <= 10))

    def test_full_wipe_still_removes_launched_ships(self):
        user = _default_user(0)
        self.hull(user, "carrack", deployed=True)
        fleet.wipe(user)
        self.assertFalse(fleet.owned(user, "carrack"))


if __name__ == "__main__":
    unittest.main()
