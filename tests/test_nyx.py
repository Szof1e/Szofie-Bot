"""NYX lifecycle, intel confidentiality and isolated category-damage regressions."""

import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from cogs.economy import EconomyCog, _strategic_objective_autocomplete
from cogs.satellite import SatelliteCog, ART_DIR
from cogs.thor import ThorCog
from cogs.imperial_star_destroyer import ImperialStarDestroyerCog
from szofie import nyx, thor, space, space_fleet as fleet, imperial_star_destroyer as isd
from szofie.config import DEFAULTS
from szofie.economy import _default_user
from tests.test_system_integration import _FakeBot, _FakeInteraction, _FakeUser


class NyxStateTests(unittest.TestCase):
    def setUp(self):
        self.now = thor.utcnow()
        self.user = _default_user(0)
        self.cfg = {"economy." + key: value for key, value in DEFAULTS["economy"].items()}

    def orbital(self):
        nyx.normalize(self.user)["status"] = "orbit"
        return {"users": {"123": self.user}}

    def test_defaults_match_approved_price_and_deadlines(self):
        self.assertEqual(self.cfg["economy.nyx_build_cost"], 15 * 10**15)
        self.assertEqual(self.cfg["economy.nyx_launch_cost"], 15 * 10**14)
        self.assertEqual(self.cfg["economy.nyx_build_hours"], 12)
        self.assertEqual(self.cfg["economy.nyx_launch_minutes"], 30)
        self.assertEqual(self.cfg["economy.nyx_cooldown_hours"], 10)
        self.assertEqual(self.cfg["economy.nyx_active_hours"], 3)

    def test_normalization_is_backward_compatible_and_json_persistent(self):
        self.assertFalse(nyx.active(self.user, self.now))
        self.assertNotIn("nyx", self.user)
        self.assertEqual(nyx.normalize(self.user), nyx.default_state())
        self.assertEqual(nyx.normalize(json.loads(json.dumps(self.user))), nyx.default_state())

    def test_fabrication_uses_absolute_deadline_and_offline_catchup(self):
        state = nyx.normalize(self.user)
        state.update(status="fabricating", ready_at=(self.now + dt.timedelta(hours=12)).isoformat())
        self.assertFalse(nyx.settle(self.user, self.now + dt.timedelta(hours=11)))
        self.assertTrue(nyx.settle(self.user, self.now + dt.timedelta(hours=12)))
        self.assertEqual(state["status"], "ready")

    def test_launch_finishes_once_after_restart_only_after_public_warning(self):
        for announced, success in ((False, False), (True, False), (True, True)):
            user = _default_user(0)
            nyx.normalize(user).update(
                status="launching",
                launch=dict(
                    announced=announced, resolves_at=self.now.isoformat(), interceptors=[{"success": success}]
                ),
            )
            restored = json.loads(json.dumps(user))
            result = nyx.finish_launch(restored, self.now + dt.timedelta(hours=2))
            self.assertEqual(result, success if announced else None)
            if announced:
                self.assertIsNone(nyx.finish_launch(restored, self.now + dt.timedelta(hours=3)))
                self.assertEqual(restored["nyx"]["status"], "none" if success else "orbit")

    def test_activation_self_only_invalidation_and_expiry_boundaries(self):
        doc = self.orbital()
        other = doc["users"]["456"] = _default_user(0)
        other["recon_targets"] = {"123": self.now.isoformat(), "789": "untouched"}
        other["sr71_construction_intel"] = {"123": {"builds": {"b2": "old"}}, "789": {}}
        until = nyx.activate(doc, 123, self.cfg, self.now)
        self.assertEqual(until, self.now + dt.timedelta(hours=3))
        self.assertNotIn("123", other["recon_targets"])
        self.assertNotIn("123", other["sr71_construction_intel"])
        self.assertIn("789", other["recon_targets"])
        self.assertFalse(nyx.active(other, self.now))
        self.assertTrue(nyx.active(self.user, until - dt.timedelta(microseconds=1)))
        self.assertFalse(nyx.active(self.user, until))
        with self.assertRaises(ValueError):
            nyx.activate(doc, 123, self.cfg, self.now + dt.timedelta(hours=9, minutes=59))
        nyx.activate(doc, 123, self.cfg, self.now + dt.timedelta(hours=10))

    def test_existing_activation_uses_shorter_cooldown_without_rewriting_expiry(self):
        doc = self.orbital()
        nyx.activate(doc, 123, dict(self.cfg, **{"economy.nyx_cooldown_hours": 20}), self.now)
        restored = json.loads(json.dumps(doc))
        state = restored["users"]["123"]["nyx"]
        original_until = state["active_until"]
        deadline = self.now + dt.timedelta(hours=10)
        for cfg in (self.cfg, {}):
            with self.subTest(cfg=bool(cfg)):
                text = nyx.summary(restored["users"]["123"], cfg, self.now)
                self.assertIn(f"<t:{int(deadline.timestamp())}:R>", text)
                with self.assertRaises(ValueError):
                    nyx.activate(restored, 123, cfg, deadline - dt.timedelta(microseconds=1))
                self.assertEqual(state["active_until"], original_until)
        until = nyx.activate(restored, 123, self.cfg, deadline)
        self.assertEqual(until, deadline + dt.timedelta(hours=3))
        self.assertIsNone(nyx.leaderboard_entry(restored["users"]["123"], deadline))

    def test_cannot_activate_without_orbit(self):
        for status in ("none", "fabricating", "ready", "launching"):
            nyx.normalize(self.user)["status"] = status
            with self.assertRaises(ValueError):
                nyx.activate({"users": {"123": self.user}}, 123, self.cfg, self.now)

    def test_ground_satellite_destroyed_but_ascent_and_orbit_survive_thor(self):
        for status in ("fabricating", "ready", "launching", "orbit"):
            user = _default_user(0)
            nyx.normalize(user)["status"] = status
            result = ThorCog._wipe_target(user, self.cfg)
            exposed = status in {"fabricating", "ready"}
            self.assertEqual(result["vehicles"], int(exposed))
            self.assertEqual(user["nyx"]["status"], "none" if exposed else status)


class NyxDamageTests(NyxStateTests):
    def victim(self):
        user = _default_user(0)
        user.update(
            donuts=101,
            bank=202,
            deep_vault_balance=303,
            deep_vault_withdraw_amount=10,
            deep_vault_withdraw_at=(self.now + dt.timedelta(hours=1)).isoformat(),
            inventory={"hex": 2},
            plushies={"general21": 2},
            fish={"cod": 3},
            rods={"abyssal": 1},
            rod_enchants={"abyssal": {"keen": 5}},
            equipped_rod="abyssal",
            icbm_ready=2,
            aa_rockets_stock=5,
            aa_rockets_loaded=2,
        )
        nyx.normalize(user).update(
            status="orbit", active_until=(self.now + dt.timedelta(hours=3)).isoformat()
        )
        user["vehicles"] = {
            "b52": {"owned": True, "ammo": 2, "loading_qty": 1},
            "u2": {"owned": False, "building_until": (self.now + dt.timedelta(hours=1)).isoformat()},
        }
        isd.normalize(user)["components"]["shipyard"]["status"] = "ready"
        thor.normalize(user)["components"]["odin"]["status"] = "ready"
        fleet.ship(user, "arquitens").update(owned=True, deployed=True)
        return user

    def hit(self, user, category):
        return ThorCog._nyx_impact(user, self.cfg, category, attacker_id=123, defender_id=456)

    def test_donut_category_preserves_other_assets_and_wipes_escrow(self):
        user = self.victim()
        state = space.normalize(user)
        state["cargo"]["donuts"] = 404
        state["load_manifest"] = {"donuts": 505, "inventory": {"hex": 1}}
        state["load_until"] = (self.now + dt.timedelta(hours=1)).isoformat()
        before = copy.deepcopy(user)
        result = self.hit(user, "donuts")
        self.assertEqual(result["visible"], 101 + 202 + 404 + 505)
        self.assertEqual(result["deep"], 303)
        for key in (
            "vehicles",
            "inventory",
            "plushies",
            "fish",
            "rods",
            "rod_enchants",
            "thor",
            "imperial_star_destroyer",
        ):
            if key in before:
                self.assertEqual(user[key], before[key])
        self.assertEqual(state["load_manifest"]["inventory"], {"hex": 1})
        self.assertEqual(user["deep_vault_withdraw_amount"], 0)
        self.assertIsNone(user["deep_vault_withdraw_at"])
        self.assertEqual(result["vehicles"], 0)

    def test_vehicle_category_preserves_money_spares_and_all_project_components(self):
        user = self.victim()
        before = copy.deepcopy(user)
        result = self.hit(user, "vehicles")
        self.assertEqual(result["vehicles"], 2)
        self.assertEqual(result["ammo"], 3)
        self.assertFalse(user["vehicles"]["b52"]["owned"])
        self.assertIsNone(user["vehicles"]["u2"]["building_until"])
        self.assertTrue(fleet.owned(user, "arquitens"))
        for key in (
            "donuts",
            "bank",
            "deep_vault_balance",
            "inventory",
            "plushies",
            "fish",
            "rods",
            "thor",
            "icbm_ready",
            "aa_rockets_stock",
            "aa_rockets_loaded",
        ):
            self.assertEqual(user[key], before[key])
        self.assertEqual(isd.normalize(user)["components"]["shipyard"]["status"], "ready")

    def test_isd_category_uses_existing_roll_and_threatens_one_part_only(self):
        for roll, destroyed in ((10, 1), (11, 0)):
            user = self.victim()
            parts = isd.normalize(user)["components"]
            parts["hull"]["status"] = "ready"
            before = copy.deepcopy(user)
            with patch("cogs.thor.random.randint", return_value=roll):
                result = self.hit(user, "isd")
            self.assertEqual(result["ground_isd_destroyed"], destroyed)
            self.assertEqual(sum((p["status"] == "ready" for p in parts.values())), 2 - destroyed)
            for key in ("donuts", "bank", "deep_vault_balance", "vehicles", "fish", "plushies", "thor"):
                self.assertEqual(user[key], before[key])

    def test_empty_category_never_rerolls_and_orbital_satellite_survives(self):
        user = _default_user(0)
        nyx.normalize(user).update(
            status="orbit", active_until=(self.now + dt.timedelta(hours=3)).isoformat()
        )
        user["donuts"] = 100
        with patch("cogs.thor.random.choice", return_value="isd") as choice:
            result = ThorCog._wipe_target(user, self.cfg, current=self.now)
        choice.assert_called_once_with(nyx.CATEGORIES)
        self.assertEqual(result["nyx_category"], "isd")
        self.assertEqual(user["donuts"], 100)
        self.assertEqual(user["nyx"]["status"], "orbit")

    def test_field_is_checked_at_impact_and_expired_field_gets_full_wipe(self):
        user = self.victim()
        with patch(
            "cogs.thor.random.choice", side_effect=lambda seq: "donuts" if seq == nyx.CATEGORIES else seq[0]
        ):
            result = ThorCog._wipe_target(user, self.cfg, current=self.now)
        self.assertEqual(result["nyx_category"], "donuts")
        user = self.victim()
        result = ThorCog._wipe_target(user, self.cfg, current=self.now + dt.timedelta(hours=3))
        self.assertNotIn("nyx_category", result)
        self.assertEqual(user["plushies"], {})


class NyxInteractionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bot = _FakeBot(Path(self.temp.name))
        self.bot.get_channel = lambda _id: None
        self.cog = SatelliteCog(self.bot)
        self.econ = object.__new__(EconomyCog)
        self.econ.bot, self.econ.econ = (self.bot, self.bot.economy)
        self.bot.cogs["Economy"] = self.econ
        self.cfg = self.bot.config.for_guild(1)

    def user(self, uid=123):
        return self.cog._user(1, uid)

    def cloak(self, user):
        nyx.normalize(user).update(
            status="orbit", active_until=(thor.utcnow() + dt.timedelta(hours=3)).isoformat()
        )

    async def test_build_charges_once_defers_and_rejects_duplicate(self):
        user = self.user()
        user["donuts"] = 2 * nyx.BUILD_COST
        for _ in range(2):
            interaction = _FakeInteraction()
            await self.cog.build(interaction)
            self.assertIsNotNone(interaction.response.deferred)
        self.assertEqual(user["donuts"], nyx.BUILD_COST)
        self.assertEqual(user["nyx"]["status"], "fabricating")

    async def test_insufficient_funds_shows_price_and_does_not_start(self):
        interaction = _FakeInteraction()
        await self.cog.build(interaction)
        self.assertEqual(self.user()["nyx"]["status"], "none")
        self.assertIn("15 quadrillion", interaction.response.calls[-1]["embed"].description)

    async def test_launch_public_warning_then_deadline_and_restart_settlement(self):
        user = self.user()
        nyx.normalize(user)["status"] = "ready"
        user["donuts"] = nyx.LAUNCH_COST
        interaction = _FakeInteraction()
        await self.cog.launch(interaction)
        record = user["nyx"]["launch"]
        self.assertTrue(record["announced"])
        self.assertEqual(user["donuts"], 0)
        self.assertFalse(interaction.response.calls[-1]["ephemeral"])
        self.assertAlmostEqual(
            (thor.parse_time(record["resolves_at"]) - thor.utcnow()).total_seconds(), 1800, delta=2
        )
        record["resolves_at"] = (thor.utcnow() - dt.timedelta(seconds=1)).isoformat()
        await SatelliteCog(self.bot)._settle_launches(1)
        self.assertEqual(user["nyx"]["status"], "orbit")

    async def test_failed_warning_restores_exact_paid_pockets(self):
        user = self.user()
        nyx.normalize(user)["status"] = "ready"
        user.update(donuts=100, bank=nyx.LAUNCH_COST - 100)
        with patch.object(self.cog, "_respond", new=AsyncMock(side_effect=RuntimeError("offline"))):
            with self.assertRaises(RuntimeError):
                await self.cog.launch(_FakeInteraction())
        self.assertEqual(user["donuts"], 100)
        self.assertEqual(user["bank"], nyx.LAUNCH_COST - 100)
        self.assertEqual(user["nyx"]["status"], "ready")

    async def test_crash_before_public_warning_refunds_once(self):
        user = self.user()
        user.update(donuts=0, bank=0)
        nyx.normalize(user).update(
            status="launching", launch=dict(announced=False, refund_donuts=100, refund_bank=200)
        )
        await self.cog._settle_launches(1)
        await self.cog._settle_launches(1)
        self.assertEqual(user["donuts"], 100)
        self.assertEqual(user["bank"], 200)
        self.assertEqual(user["nyx"]["status"], "ready")

    async def test_interception_consumes_one_gbi_duplicate_and_closed_denied(self):
        target = self.user(456)
        nyx.normalize(target).update(
            status="launching",
            launch=dict(
                announced=True,
                resolves_at=(thor.utcnow() + dt.timedelta(minutes=30)).isoformat(),
                interceptors=[],
            ),
        )
        battery = thor.normalize(self.user())
        battery["gbi_stock"] = 2
        with patch("cogs.satellite.random.randint", side_effect=[30, 1]):
            await self.cog.intercept(_FakeInteraction(), _FakeUser(456))
        await self.cog.intercept(_FakeInteraction(), _FakeUser(456))
        self.assertEqual(battery["gbi_stock"], 1)
        self.assertEqual(len(target["nyx"]["launch"]["interceptors"]), 1)
        self.assertTrue(target["nyx"]["launch"]["interceptors"][0]["success"])
        await self.cog.intercept(_FakeInteraction(), _FakeUser(789))
        self.assertEqual(battery["gbi_stock"], 1)

    async def test_fresh_and_legacy_intel_cannot_bypass_field(self):
        pilot, target = (self.user(), self.user(456))
        self.cloak(target)
        future = (thor.utcnow() + dt.timedelta(hours=6)).isoformat()
        pilot["recon_targets"]["456"] = future
        pilot["sr71_construction_intel"] = {"456": {"expires_at": future, "builds": {"b2": future}}}
        self.assertFalse(self.econ._active_recon(pilot, 456, target))
        self.assertIsNone(self.econ._active_construction_intel(pilot, 456, target))
        self.assertEqual(
            self.econ._grant_recon_package(pilot, 456, target, thor.utcnow() + dt.timedelta(hours=6)), []
        )
        target["nyx"]["active_until"] = thor.utcnow().isoformat()
        self.assertFalse(self.econ._active_recon(pilot, 456, target))

    async def test_all_three_recon_sorties_jam_without_reporting_balances(self):
        for model in ("u2", "sr71", "deimos"):
            pilot, target = (self.user(), self.user(456))
            self.cloak(target)
            self.econ._vehicle_state(pilot, model).update(owned=True, last_deploy_at=None)
            target["deep_vault_owned"] = True
            target["deep_vault_balance"] = 987654321234567
            interaction = _FakeInteraction()
            await getattr(self.econ, "_" + model + "_execute")(interaction, _FakeUser(456))
            report = "\n".join(
                (json.dumps(row["embed"].to_dict()) for row in interaction.followup.calls if row.get("embed"))
            )
            self.assertIn("RECONNAISSANCE JAMMED", report)
            self.assertNotIn("987", report)
            self.assertNotIn("456", pilot["recon_targets"])
            self.assertTrue(pilot["vehicles"][model]["owned"])
            self.assertIsNotNone(pilot["vehicles"][model]["last_deploy_at"])

    async def test_cloaked_deimos_operator_never_exposes_uncloaked_victim(self):
        pilot = self.user()
        self.cloak(pilot)
        self.econ._vehicle_state(pilot, "deimos").update(owned=True, last_deploy_at=None)
        target = self.user(456)
        await self.econ._deimos_execute(_FakeInteraction(), _FakeUser(456))
        self.assertFalse(pilot["recon_targets"])
        self.assertFalse(target["recon_targets"])

    async def test_targeting_autocomplete_reverts_to_blind_catalog(self):
        pilot, target = (self.user(), self.user(456))
        self.cloak(target)
        self.econ._vehicle_state(target, "b2").update(
            building_until=(thor.utcnow() + dt.timedelta(hours=2)).isoformat()
        )
        pilot["sr71_construction_intel"] = {
            "456": {
                "expires_at": (thor.utcnow() + dt.timedelta(hours=5)).isoformat(),
                "builds": {"b2": "secret"},
            }
        }
        interaction = _FakeInteraction()
        interaction.client = self.bot
        interaction.namespace = SimpleNamespace(vehicle="lrhw", target=_FakeUser(456))
        choices = await _strategic_objective_autocomplete(interaction, "")
        self.assertTrue(choices)
        self.assertTrue(all(("Recon track" not in row.name for row in choices)))

    async def test_public_guide_private_status_and_cinder_reset(self):
        interaction = _FakeInteraction()
        await self.cog.guide(interaction)
        self.assertFalse(interaction.response.calls[-1]["ephemeral"])
        interaction.response.calls[-1]["view"].stop()
        interaction = _FakeInteraction()
        await self.cog.status(interaction)
        self.assertTrue(interaction.response.calls[-1]["ephemeral"])
        self.cloak(self.user())
        ImperialStarDestroyerCog(self.bot)._apply_cinder_wipe(self.cog._doc(1), {"guild_id": 1})
        self.assertFalse(nyx.active(self.user()))
        self.assertEqual(nyx.normalize(self.user())["status"], "none")

    async def test_interception_cap_and_coalition_protection(self):
        target = self.user(456)
        nyx.normalize(target).update(
            status="launching",
            launch=dict(
                announced=True,
                resolves_at=(thor.utcnow() + dt.timedelta(minutes=30)).isoformat(),
                interceptors=[],
            ),
        )
        for uid in (123, 124, 125, 126):
            thor.normalize(self.user(uid))["gbi_stock"] = 1
            with patch("cogs.satellite.random.randint", side_effect=[10, 100]):
                await self.cog.intercept(_FakeInteraction(user_id=uid), _FakeUser(456))
        self.assertEqual(len(target["nyx"]["launch"]["interceptors"]), 3)
        self.assertEqual(thor.normalize(self.user(126))["gbi_stock"], 1)
        target["nyx"]["launch"]["interceptors"] = []
        from szofie import coalitions

        coalitions.state(self.cog._doc(1))["groups"]["1"] = dict(
            members=[123, 456], expires_at=(thor.utcnow() + dt.timedelta(days=1)).isoformat()
        )
        thor.normalize(self.user())["gbi_stock"] = 1
        await self.cog.intercept(_FakeInteraction(), _FakeUser(456))
        self.assertEqual(thor.normalize(self.user())["gbi_stock"], 1)
        self.assertEqual(target["nyx"]["launch"]["interceptors"], [])

    async def test_successful_launch_intercept_is_retained_in_victims_attacklog(self):
        from szofie.ledger import incoming_security_actor

        target = self.user(456)
        nyx.normalize(target).update(
            status="launching",
            launch=dict(
                announced=True,
                resolves_at=(thor.utcnow() - dt.timedelta(seconds=1)).isoformat(),
                interceptors=[dict(user=123, success=True)],
            ),
        )
        self.bot.ledger.record = AsyncMock()
        await self.cog._settle_launches(1)
        self.assertEqual(target["nyx"]["status"], "none")
        hit = self.bot.ledger.record.await_args_list[-1]
        self.assertEqual(hit.args[3], "nyx-intercept-hit")
        self.assertEqual(
            incoming_security_actor(dict(user=hit.args[1], reason=hit.args[3], **hit.kwargs), 456), 123
        )

    async def test_aegis_intercepts_before_field_selects_any_damage(self):
        victim = self.user(456)
        self.cloak(victim)
        attacker = self.user()
        thor.normalize(attacker).update(operational=True, rods=1, chambered=True)
        self.econ._vehicle_state(victim, "aegis").update(owned=True, bmd_owned=True, sm3_ammo=2)
        victim.update(donuts=999, deep_vault_balance=777)
        interaction = _FakeInteraction()
        with patch("cogs.thor.random.randint", return_value=1), patch("cogs.thor.random.choice") as choice:
            await ThorCog.strike.callback(ThorCog(self.bot), interaction, _FakeUser(456))
        choice.assert_not_called()
        self.assertEqual(victim["donuts"], 999)
        self.assertEqual(victim["deep_vault_balance"], 777)
        self.assertEqual(victim["vehicles"]["aegis"]["sm3_ammo"], 1)
        self.assertEqual(thor.normalize(attacker)["rods"], 0)

    async def test_landed_strike_reports_one_category_and_is_durable(self):
        victim = self.user(456)
        self.cloak(victim)
        victim.update(donuts=987, bank=654, deep_vault_balance=321)
        victim["plushies"]["labcoat21"] = 1
        attacker = self.user()
        thor.normalize(attacker).update(operational=True, rods=1, chambered=True)
        interaction = _FakeInteraction()
        with patch("cogs.thor.random.choice", return_value="donuts"):
            await ThorCog.strike.callback(ThorCog(self.bot), interaction, _FakeUser(456))
        self.assertEqual(victim["donuts"], 0)
        self.assertEqual(victim["deep_vault_balance"], 0)
        self.assertEqual(victim["plushies"]["labcoat21"], 1)
        self.assertEqual(victim["nyx"]["status"], "orbit")
        report = interaction.followup.calls[-1]["embed"]
        self.assertIn("DEGRADED TARGETING", report.title)
        self.assertNotIn("Total destruction", report.description)
        persisted = json.loads(self.bot.economy.store._path(1).read_text())
        self.assertEqual(persisted["users"]["456"]["donuts"], 0)
        self.assertEqual(persisted["users"]["456"]["plushies"]["labcoat21"], 1)
