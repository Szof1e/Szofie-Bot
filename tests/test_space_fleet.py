"""Ordinary-spacecraft regressions, isolated from live storage and Discord."""

from __future__ import annotations
import copy
import datetime as dt
import random
import tempfile
import unittest
from pathlib import Path
from cogs.space import SpaceCog
from cogs.thor import ThorCog
from szofie import coalitions, deathstar, space, space_fleet as fleet
from szofie.economy import _default_user
from discord import app_commands
from tests.test_system_integration import _FakeBot, _FakeInteraction


class FleetCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_payload_mission_and_private_status_acknowledge_and_persist(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = SpaceCog(bot)
            user = bot.economy.user(1, 123, 0)
            user["donuts"] = 1000 * fleet.Q
            choice = app_commands.Choice(name=fleet.CRAFT["prospector"].name, value="prospector")
            interaction = _FakeInteraction()
            await SpaceCog.build.callback(cog, interaction, choice)
            self.assertTrue(interaction.response.is_done())
            self.assertEqual(user["donuts"], 925 * fleet.Q)
            fleet.settle_hulls(user, space.now() + dt.timedelta(hours=9))
            interaction = _FakeInteraction()
            await SpaceCog.fleet_payload.callback(cog, interaction, choice, 1)
            self.assertEqual(user["donuts"], 924 * fleet.Q)
            fleet.settle_hulls(user, space.now() + dt.timedelta(hours=1))
            launched = _FakeInteraction()
            await SpaceCog.fleet_launch.callback(cog, launched, choice)
            fleet.settle_hulls(user, space.now() + dt.timedelta(minutes=31))
            space.normalize(user)["surveys"]["psyche"] = space.now().isoformat()
            interaction = _FakeInteraction()
            await SpaceCog.fleet_mission.callback(
                cog, interaction, choice, app_commands.Choice(name="Mine", value="mine"), "psyche"
            )
            self.assertTrue(fleet.ship(user, "prospector")["mission"])
            interaction = _FakeInteraction()
            await SpaceCog.fleet_status.callback(cog, interaction, choice)
            reply = interaction.response.calls[-1]
            self.assertTrue(reply["ephemeral"])
            self.assertEqual(reply["view"].author_id, 123)
            bot.economy.store._cache.clear()
            restored = bot.economy.user(1, 123, 0)
            self.assertEqual(fleet.ship(restored, "prospector")["mission"]["body"], "psyche")


class FleetTests(unittest.TestCase):
    def setUp(self):
        self.now = space.now()
        self.doc = {"users": {str(uid): _default_user(10000 * fleet.Q) for uid in (1, 2, 3)}}
        for user in self.doc["users"].values():
            state = space.normalize(user)
            state["surveys"] = {key: self.now.isoformat() for key in space.BODIES}

    def user(self, uid=1):
        return self.doc["users"][str(uid)]

    def ready(self, key, uid=1):
        user = self.user(uid)
        hull = fleet.ship(user, key)
        hull["deployed"] = True
        if key in fleet.LEGACY:
            space.normalize(user)[f"{key}_owned"] = True
        else:
            hull["owned"] = True
        fleet.set_payload(user, key, fleet.CRAFT[key].capacity)
        return hull

    def allies(self):
        coalitions.state(self.doc)["groups"]["1"] = {
            "members": [1, 2],
            "leader": 1,
            "name": "Test",
            "expires_at": (self.now + dt.timedelta(days=3)).isoformat(),
        }

    def intel(self, body="mars"):
        fleet.normalize(self.user())["recon"]["2"] = {
            "body": body,
            "expires_at": (self.now + dt.timedelta(hours=4)).isoformat(),
            "rows": [],
        }

    def hit_seed(self):
        return next((str(i) for i in range(100) if random.Random(str(i)).randint(1, 100) <= 40))

    def test_construction_and_payload_resume_once(self):
        user = self.user()
        before = user["donuts"]
        self.assertEqual(fleet.commission(user, "prospector", self.now), 75 * fleet.Q)
        self.assertEqual(user["donuts"], before - 75 * fleet.Q)
        with self.assertRaises(ValueError):
            fleet.commission(user, "prospector", self.now)
        due = self.now + dt.timedelta(hours=8)
        self.assertTrue(fleet.settle_hulls(user, due))
        self.assertTrue(fleet.owned(user, "prospector"))
        self.assertFalse(fleet.settle_hulls(user, due))
        fleet.prepare(user, "prospector", 3, due)
        self.assertEqual(fleet.payload(user, "prospector"), 0)
        fleet.settle_hulls(user, due + dt.timedelta(minutes=30))
        self.assertEqual(fleet.payload(user, "prospector"), 3)
        self.assertFalse(fleet.settle_hulls(user, due + dt.timedelta(days=2)))

    def test_legacy_xwing_ammo_is_shared_not_duplicated(self):
        self.ready("xwing")
        fleet.set_payload(self.user(), "xwing", 0)
        fleet.prepare(self.user(), "xwing", 2, self.now)
        self.assertEqual(space.normalize(self.user())["xwing_ammo"], 2)
        self.assertEqual(fleet.payload(self.user(), "xwing"), 2)
        fleet.escort(self.doc, 1, "xwing", 1, "mars", self.now, "escort")
        self.assertEqual(space.normalize(self.user())["xwing_ammo"], 1)
        with self.assertRaises(ValueError):
            fleet.prepare(self.user(), "xwing", 1, self.now)

    def test_prospector_needs_no_isd_and_cannot_mine_planets(self):
        self.ready("prospector")
        with self.assertRaises(ValueError):
            fleet.start(self.doc, 1, "prospector", "mine", "mars", self.now, "bad")
        mission = fleet.start(self.doc, 1, "prospector", "mine", "psyche", self.now, "mine")
        self.assertEqual(mission["materials"], {"metals": 28})
        self.assertFalse(fleet.settle_world(self.doc, self.now))
        self.assertTrue(fleet.settle_world(self.doc, space.at(mission["ready_at"])))
        self.assertEqual(space.normalize(self.user())["field_materials"], {"metals": 28})
        before = copy.deepcopy(self.user())
        self.assertFalse(fleet.settle_world(self.doc, self.now + dt.timedelta(days=7)))
        self.assertEqual(self.user(), before)

    def test_new_surveys_share_existing_exploration_milestones(self):
        self.ready("carrack")
        for key in ("mars", "venus", "psyche"):
            record = fleet.start(self.doc, 1, "carrack", "survey", key, self.now, "survey-" + key)
            fleet.settle_world(self.doc, space.at(record["ready_at"]))
        self.assertIn("pathfinder", self.user()["titles"])
        self.assertEqual(space.normalize(self.user())["milestones"], [3])
        self.assertTrue(space.normalize(self.user())["journal"]["psyche"]["last_completed_at"])

    def test_legacy_cutlass_arrival_updates_local_fleet_location(self):
        self.ready("cutlass")
        state = space.normalize(self.user())
        state.update(destination="mars", travel_until=self.now.isoformat(), travel_craft="cutlass")
        space.settle(self.user(), self.now)
        self.assertEqual(fleet.ship(self.user(), "cutlass")["location"], "mars")
        self.assertIsNone(state["travel_craft"])

    def test_successful_precision_hits_emit_one_security_event_at_deadline(self):
        self.ready("ywing")
        self.intel()
        space.normalize(self.user(2))["colonies"]["mars"] = {"base": True, "mine": True, "materials": {}}
        record = fleet.start(
            self.doc, 1, "ywing", "bomb", "mars", self.now, self.hit_seed(), target_uid=2, objective="mine"
        )
        events = []
        fleet.settle_world(self.doc, space.at(record["ready_at"]) + dt.timedelta(hours=3), events=events)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["victim"], 2)
        self.assertEqual(events[0]["at"], space.at(record["ready_at"]))
        fleet.settle_world(self.doc, self.now + dt.timedelta(days=1), events=events)
        self.assertEqual(len(events), 1)

    def test_busy_legacy_craft_is_not_a_docked_objective_or_depot_loader(self):
        self.ready("transport")
        fleet.ship(self.user(), "transport")["location"] = "mars"
        state = space.normalize(self.user())
        state["load_until"] = (self.now + dt.timedelta(hours=2)).isoformat()
        state["colonies"]["mars"] = {"base": True, "ammo_depot": True, "ammo_stock": {"transport": 1}}
        self.assertNotIn("ship:transport", fleet.objectives(self.user(), "mars", self.now))
        with self.assertRaises(ValueError):
            fleet.depot(self.user(), self.doc, "mars", "load", "transport", 1, self.now)

    def test_no_duplicate_assignment_or_payload_charge_on_rejected_mission(self):
        self.ready("prospector")
        fleet.start(self.doc, 1, "prospector", "mine", "vesta", self.now, "first")
        before = fleet.payload(self.user(), "prospector")
        with self.assertRaises(ValueError):
            fleet.start(self.doc, 1, "prospector", "mine", "psyche", self.now, "second")
        self.assertEqual(fleet.payload(self.user(), "prospector"), before)

    def test_natural_salvage_claim_is_finite_and_wallet_only(self):
        self.ready("vulture")
        self.ready("vulture", 2)
        before = self.user()["donuts"]
        bank = self.user()["bank"]
        record = fleet.start(self.doc, 1, "vulture", "salvage", "vesta", self.now, "salvage")
        with self.assertRaises(ValueError):
            fleet.start(self.doc, 2, "vulture", "salvage", "vesta", self.now, "double")
        fleet.settle_world(self.doc, space.at(record["ready_at"]))
        self.assertEqual(self.user()["donuts"], before + 2 * fleet.Q)
        self.assertEqual(self.user()["bank"], bank)
        self.assertFalse(fleet.settle_world(self.doc, self.now + dt.timedelta(days=3)))

    def test_wrecks_cannot_farm_self_attacker_or_coalition_losses(self):
        self.ready("prospector", 2)
        self.ready("vulture")
        fleet._damage(self.doc, self.user(2), 2, "prospector", 1, "vesta", "battle", self.now)
        wreck = next(iter(space.galaxy(self.doc)["wrecks"]))
        with self.assertRaises(ValueError):
            fleet.start(self.doc, 1, "vulture", "salvage", "vesta", self.now, "bad", wreck_id=wreck)
        self.ready("vulture", 3)
        record = fleet.start(self.doc, 3, "vulture", "salvage", "vesta", self.now, "good", wreck_id=wreck)
        self.assertLess(record["payout"], fleet.CRAFT["prospector"].cost // 4)

    def test_supply_escrow_is_conserved_and_alliance_expiry_returns_it(self):
        self.ready("transport")
        self.allies()
        source = space.colony(space.normalize(self.user()), "mars")
        source.update(base=True, materials={"metals": 100})
        dest = space.colony(space.normalize(self.user(2)), "luna")
        dest.update(base=True)
        record = fleet.start(
            self.doc,
            1,
            "transport",
            "supply",
            "luna",
            self.now,
            "supply",
            target_uid=2,
            source="mars",
            amount=80,
        )
        self.assertEqual(source["materials"]["metals"], 20)
        coalitions.state(self.doc)["groups"] = {}
        fleet.settle_world(self.doc, space.at(record["ready_at"]))
        self.assertEqual(source["materials"]["metals"], 100)
        self.assertFalse(dest["materials"])

    def test_gr75_legacy_loading_and_fleet_supply_cannot_overlap(self):
        self.ready("transport")
        space.normalize(self.user())["load_until"] = (self.now + dt.timedelta(hours=24)).isoformat()
        with self.assertRaises(ValueError):
            fleet.prepare(self.user(), "transport", 1, self.now)
        with self.assertRaises(ValueError):
            fleet.start(self.doc, 1, "transport", "supply", "mars", self.now, "busy")

    def test_raid_only_reduces_mission_cargo_and_has_finite_slots(self):
        self.ready("prospector", 2)
        self.ready("awing")
        self.ready("xwing", 3)
        record = fleet.start(self.doc, 2, "prospector", "mine", "psyche", self.now, "miner")
        unchanged = copy.deepcopy(self.user(2))
        before = self.user(2)["donuts"]
        result = fleet.raid(self.doc, 1, "awing", 2, "prospector", self.now, "hit", roll=1)
        self.assertTrue(result["hit"])
        self.assertEqual(record["materials"]["metals"], 21)
        self.assertEqual(self.user(2)["donuts"], before)
        self.assertEqual(self.user(2)["thor"], unchanged["thor"])
        with self.assertRaises(ValueError):
            fleet.raid(self.doc, 1, "awing", 2, "prospector", self.now, "repeat", roll=1)
        fleet.raid(self.doc, 3, "xwing", 2, "prospector", self.now, "hit2", roll=1)
        self.assertEqual(record["materials"]["metals"], 16)
        self.assertEqual(len(record["raids"]), 2)

    def test_strongest_escort_responds_once_without_stacking(self):
        self.ready("prospector", 2)
        self.ready("awing")
        self.ready("hammerhead", 2)
        fleet.start(self.doc, 2, "prospector", "mine", "psyche", self.now, "miner")
        fleet.escort(self.doc, 2, "hammerhead", 2, "psyche", self.now, "escort")
        result = fleet.raid(self.doc, 1, "awing", 2, "prospector", self.now, "raid", roll=40)
        self.assertFalse(result["hit"])
        self.assertEqual(result["chance"], 30)
        self.assertIsNone(fleet.ship(self.user(2), "hammerhead")["mission"])

    def test_interdictor_delays_only_once_and_never_erases_cargo(self):
        self.ready("prospector", 2)
        self.ready("interdictor")
        self.ready("interdictor", 3)
        record = fleet.start(self.doc, 2, "prospector", "mine", "psyche", self.now, "miner")
        old = space.at(record["ready_at"])
        before = copy.deepcopy(record["materials"])
        fleet.raid(self.doc, 1, "interdictor", 2, "prospector", self.now, "gravity", roll=1)
        self.assertEqual(space.at(record["ready_at"]), old + dt.timedelta(hours=1))
        self.assertEqual(record["materials"], before)
        with self.assertRaises(ValueError):
            fleet.raid(self.doc, 3, "interdictor", 2, "prospector", self.now, "chain", roll=1)

    def test_ywing_needs_fresh_local_recon_and_cannot_select_thor(self):
        self.ready("ywing")
        site = space.colony(space.normalize(self.user(2)), "mars")
        site.update(base=True, mine=True)
        with self.assertRaises(ValueError):
            fleet.start(
                self.doc, 1, "ywing", "bomb", "mars", self.now, "blind", target_uid=2, objective="mine"
            )
        self.intel()
        for objective in ("thor", "isd", "deep_vault_balance", "ship:bwing"):
            with self.assertRaises(ValueError):
                fleet.start(
                    self.doc,
                    1,
                    "ywing",
                    "bomb",
                    "mars",
                    self.now,
                    "forbidden",
                    target_uid=2,
                    objective=objective,
                )
        record = fleet.start(
            self.doc, 1, "ywing", "bomb", "mars", self.now, self.hit_seed(), target_uid=2, objective="mine"
        )
        before = self.user(2)["donuts"]
        fleet.settle_world(self.doc, space.at(record["ready_at"]))
        self.assertTrue(site["mine_disabled_until"])
        self.assertEqual(self.user(2)["donuts"], before)
        self.assertEqual(
            space.settle_materials(
                site, space.BODIES["mars"], space.at(record["ready_at"]) + dt.timedelta(hours=3)
            ),
            (0, False),
        )

    def test_local_bomb_damage_is_repairable_not_permanent_loss(self):
        self.ready("ywing")
        self.ready("prospector", 2)["location"] = "mars"
        self.intel()
        record = fleet.start(
            self.doc,
            1,
            "ywing",
            "bomb",
            "mars",
            self.now,
            self.hit_seed(),
            target_uid=2,
            objective="ship:prospector",
        )
        fleet.settle_world(self.doc, space.at(record["ready_at"]))
        self.assertTrue(fleet.ship(self.user(2), "prospector")["damaged"])
        self.assertTrue(fleet.owned(self.user(2), "prospector"))
        cost = fleet.repair(self.user(2), "prospector", self.now + dt.timedelta(hours=1))
        self.assertEqual(cost, fleet.CRAFT["prospector"].cost // 4)
        fleet.settle_hulls(self.user(2), self.now + dt.timedelta(hours=4))
        self.assertFalse(fleet.ship(self.user(2), "prospector")["damaged"])
        self.assertEqual(fleet.payload(self.user(2), "prospector"), 0)

    def test_thor_preserves_launched_industrial_missions_and_rewards(self):
        self.ready("prospector")
        fleet.start(self.doc, 1, "prospector", "mine", "psyche", self.now, "exposed")
        ThorCog._wipe_target(self.user(), {})
        fleet.settle_world(self.doc, self.now + dt.timedelta(days=3))
        self.assertTrue(fleet.owned(self.user(), "prospector"))
        self.assertTrue(space.normalize(self.user())["field_materials"])

    def test_destroyed_destination_cancels_pending_rewards(self):
        self.ready("prospector")
        record = fleet.start(self.doc, 1, "prospector", "mine", "psyche", self.now, "mine")
        deathstar.world(self.doc)["destroyed"]["psyche"] = {"at": self.now.isoformat()}
        fleet.settle_world(self.doc, space.at(record["ready_at"]))
        self.assertFalse(space.normalize(self.user())["field_materials"])
        self.assertIsNone(fleet.ship(self.user(), "prospector")["mission"])

    def test_coalition_attack_and_nonally_escort_are_blocked(self):
        self.ready("awing")
        self.ready("prospector", 2)
        self.allies()
        fleet.start(self.doc, 2, "prospector", "mine", "psyche", self.now, "miner")
        with self.assertRaises(ValueError):
            fleet.raid(self.doc, 1, "awing", 2, "prospector", self.now, "friendly")
        with self.assertRaises(ValueError):
            fleet.escort(self.doc, 1, "awing", 3, "mars", self.now, "nonally")

    def test_ammo_depot_never_stores_superweapon_ammunition(self):
        self.ready("ywing")["location"] = "mars"
        site = space.colony(space.normalize(self.user()), "mars")
        site.update(base=True)
        fleet.depot(self.user(), self.doc, "mars", "build", None, 1, self.now)
        fleet.settle_world(self.doc, self.now + dt.timedelta(hours=6))
        with self.assertRaises(ValueError):
            fleet.depot(self.user(), self.doc, "mars", "stock", "thor", 1, self.now)
        fleet.depot(self.user(), self.doc, "mars", "stock", "ywing", 3, self.now)
        fleet.set_payload(self.user(), "ywing", 0)
        fleet.depot(self.user(), self.doc, "mars", "load", "ywing", 2, self.now)
        self.assertEqual(site["ammo_stock"]["ywing"], 1)
        self.assertEqual(fleet.payload(self.user(), "ywing"), 2)

    def test_command_caps_and_public_guides(self):
        self.assertEqual(len(SpaceCog.space_group.commands), 25)
        self.assertIn("fleet", [c.name for c in SpaceCog.space_group.commands])
        pages = SpaceCog.fleet_pages()
        self.assertTrue(all((len(p) < 6000 and len(p.description) < 4096 for p in pages)))
        text = "\n".join((p.description for p in pages))
        for key, spec in fleet.CRAFT.items():
            self.assertIn(spec.name, text)
        self.assertNotIn("owner_", text)

    def test_late_settlement_does_not_refresh_recon(self):
        self.ready("carrack")
        record = fleet.start(self.doc, 1, "carrack", "recon", "mars", self.now, "scan", target_uid=2)
        fleet.settle_world(self.doc, space.at(record["ready_at"]))
        intel = fleet.normalize(self.user())["recon"]["2"]
        self.assertEqual(space.at(intel["expires_at"]), space.at(record["ready_at"]) + dt.timedelta(hours=4))
        fleet.settle_world(self.doc, self.now + dt.timedelta(days=2))
        self.assertNotIn("2", fleet.normalize(self.user())["recon"])
        self.ready("carrack")
        fleet.start(self.doc, 1, "carrack", "recon", "mars", self.now, "scan-late", target_uid=2)
        fleet.settle_world(self.doc, self.now + dt.timedelta(days=2))
        self.assertNotIn("2", fleet.normalize(self.user())["recon"])

    def test_escort_still_defends_before_its_expiry_during_downtime(self):
        self.ready("ywing")
        self.ready("hammerhead", 2)
        self.intel()
        site = space.colony(space.normalize(self.user(2)), "mars")
        site.update(base=True, mine=True)
        token = next((str(i) for i in range(200) if 45 < random.Random(str(i)).randint(1, 100) <= 80))
        fleet.escort(self.doc, 2, "hammerhead", 2, "mars", self.now, "escort")
        fleet.start(self.doc, 1, "ywing", "bomb", "mars", self.now, token, target_uid=2, objective="mine")
        fleet.settle_world(self.doc, self.now + dt.timedelta(hours=10))
        self.assertNotIn("mine_disabled_until", site)
        self.assertTrue(
            any(("Escort responded" in row["result"] for row in fleet.normalize(self.user(2))["history"]))
        )


if __name__ == "__main__":
    unittest.main()
