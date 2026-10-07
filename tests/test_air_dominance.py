"""Offline fifth-gen missions, privacy, settlement and cross-system regressions."""

import copy
import datetime as dt
import json
import types
import tempfile
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch
from cogs.economy import EconomyCog, _vehicle_build_autocomplete, _vehicle_load_autocomplete
from cogs import air_dominance as commands
from cogs.thor import ThorCog
from szofie import air_dominance as air, guides, nyx
from szofie.catalog import VEHICLE_CATALOG, LOADABLE_VEHICLES, CONVENTIONAL_AMMO_TARGETS
from szofie.config import DEFAULTS
from szofie.economy import _default_user
from szofie.thor import utcnow


class Config:
    color = 123

    def __init__(self, **values):
        self.values = values

    def get(self, key, fallback=None):
        if key in self.values:
            return self.values[key]
        section, _, field = key.partition(".")
        return DEFAULTS.get(section, {}).get(field, fallback)


class AirRulesTests(unittest.TestCase):
    def test_recipes_and_caps_match_defaults(self):
        cfg = Config()
        for model, values in air.JETS.items():
            spec = VEHICLE_CATALOG[model]
            self.assertEqual(cfg.get("economy." + spec["cost"]), values[2])
            self.assertIn(model, LOADABLE_VEHICLES)
            self.assertIn(model, CONVENTIONAL_AMMO_TARGETS)
            self.assertEqual(cfg.get("economy." + spec["cap"]), 4)
            self.assertEqual(EconomyCog._vehicle_cooldown_seconds(cfg, model), values[5] * 60)

    def test_patrol_is_single_use_and_non_air_targets_excluded(self):
        for model in ("icbm", "virginia", "lrhw", "thor", "isd", "nyx", "himars"):
            self.assertEqual(air.patrol_chance(model), 0)
        self.assertEqual(air.patrol_chance("b2"), 20)
        self.assertEqual(air.patrol_chance("su57"), 35)
        self.assertEqual(air.patrol_chance("b52"), 65)
        user = {"vehicles": {"f22": {"owned": True, "patrol_target": "2", "patrol_until": air.deadline(2)}}}
        state = air.patrol(user, 2)
        with patch("cogs.air_dominance.random.randint", return_value=100):
            self.assertFalse(commands.engage((state, "patrol_until", "Patrol", 65, 2), Config(), 1))
        self.assertIsNone(air.patrol(user, 2))

    def test_link_one_use_cap_expiry_and_nyx(self):
        user, victim = ({}, {})
        user["fusion_links"] = {"2": {"until": air.deadline(0.5), "created": utcnow().isoformat()}}
        self.assertEqual(air.vehicle_chance(user, 2, victim, 90), 95)
        self.assertEqual(air.vehicle_chance(user, 2, victim, 90), 90)
        user["fusion_links"]["2"] = {"until": air.deadline(-1), "created": utcnow().isoformat()}
        self.assertIsNone(air.link(user, 2, victim))
        user["fusion_links"]["2"] = {"until": air.deadline(0.5), "created": utcnow().isoformat()}
        victim["nyx"] = {"status": "orbit", "active_until": air.deadline(3)}
        self.assertIsNone(air.link(user, 2, victim))

    def test_nyx_activation_removes_links_and_source_recon(self):
        user = {"nyx": dict(status="orbit", last_activation_at=None, active_until=None)}
        observer = {"fusion_links": {"2": dict(until=air.deadline(0.5))}}
        nyx.activate({"users": {"2": user, "1": observer}}, 2, Config())
        self.assertFalse(observer["fusion_links"])

    def test_source_loss_preserves_independent_recon(self):
        expiry = air.deadline(3)
        observer = {
            "recon_targets": {"2": expiry},
            "sr71_construction_intel": {
                "2": {
                    "source": "u2",
                    "expires_at": expiry,
                    "sources": {
                        "u2": {"expires_at": expiry, "builds": {}},
                        "sr71": {"expires_at": expiry, "builds": {"su57": expiry}},
                    },
                }
            },
        }
        air.invalidate_source(observer, "u2")
        self.assertEqual(observer["sr71_construction_intel"]["2"]["source"], "sr71")
        self.assertIn("2", observer["recon_targets"])
        air.invalidate_source(observer, "sr71")
        self.assertFalse(observer["recon_targets"])

    def test_thor_wipes_ground_jets_repairs_and_sorties(self):
        user = _default_user(0)
        for model in air.JETS:
            state = EconomyCog._vehicle_state(user, model)
            state.update(
                owned=True,
                ammo=4,
                jet_damaged=True,
                jet_repair_until=air.deadline(2),
                patrol_until=air.deadline(2),
                patrol_target="2",
                second_pass={"token": "stale"},
            )
        user["fusion_links"] = {"2": {"until": air.deadline(0.5)}}
        ThorCog._wipe_target(user, Config())
        restored = json.loads(json.dumps(user))
        for model in air.JETS:
            state = restored["vehicles"][model]
            self.assertFalse(state["owned"])
            self.assertEqual(state["ammo"], 0)
            self.assertIsNone(state["patrol_until"])
            self.assertIsNone(state["second_pass"])
            self.assertIsNone(state["jet_repair_until"])
        self.assertNotIn("fusion_links", restored)

    def test_guide_limits(self):
        for page in guides.air_dominance_pages(Config(), color=1):
            self.assertLessEqual(len(page), 6000)
            self.assertLessEqual(len(page.description or ""), 4096)
            for field in page.fields:
                self.assertLessEqual(len(field.value), 1024)


class MissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.users = {"1": _default_user(10**15), "2": _default_user(1000000)}
        self.doc = {"users": self.users}
        self.cfg = Config()
        self.cog = object.__new__(EconomyCog)
        self.cog.cfg = lambda gid: self.cfg
        self.cog.user = lambda gid, uid: self.users.setdefault(str(uid), _default_user(0))
        self.cog.econ = types.SimpleNamespace(store=types.SimpleNamespace(load=lambda gid: self.doc))
        self.cog.persist = AsyncMock()
        self.cog._log = AsyncMock()
        self.cog._guard = AsyncMock(return_value=self.cfg)
        self.cog._coalition_attack_error = lambda *args: None
        self.cog.bot = types.SimpleNamespace(ledger=types.SimpleNamespace(record=AsyncMock()))
        self.pilot = types.SimpleNamespace(id=1, bot=False, display_name="Pilot", mention="<@1>")
        self.target = types.SimpleNamespace(id=2, bot=False, display_name="Target", mention="<@2>")
        self.i = types.SimpleNamespace(
            guild_id=10,
            user=self.pilot,
            extras={},
            client=types.SimpleNamespace(get_cog=lambda name: self.cog),
        )
        self.respond = patch("cogs.air_dominance.ui.respond", new=AsyncMock()).start()
        self.result = patch("cogs.air_dominance.result", new=AsyncMock()).start()
        self.addCleanup(patch.stopall)

    def ready(self, model):
        state = self.cog._vehicle_settle(self.users["1"], model)
        state.update(owned=True, ammo=4)
        return state

    def recon(self):
        self.cog._grant_recon_package(
            self.users["1"], 2, self.users["2"], utcnow() + dt.timedelta(hours=1), source="sr71"
        )

    async def test_no_defence_means_no_spontaneous_shootdown(self):
        state = self.ready("su57")
        await commands.execute(self.cog, self.i, self.target, "su57", "wallet")
        self.assertFalse(state["jet_damaged"])
        self.assertEqual(state["ammo"], 3)
        self.assertEqual(self.users["2"]["donuts"], 800000)

    async def test_stock_damage_rounds_up_exactly_without_float_conversion(self):
        for count in (1, 2, 5, 2**53 + 1, 10**93 + 1):
            with self.subTest(count=count):
                state = self.ready("su57")
                state.update(last_deploy_at=None, second_pass=None)
                self.users["2"]["aa_rockets_loaded"] = count
                await commands.execute(self.cog, self.i, self.target, "su57", "aa:regular-aa")
                lost = (count * 60 + 99) // 100
                self.assertEqual(self.users["2"]["aa_rockets_loaded"], count - lost)
                self.assertEqual(state["ammo"], 3)
                view = self.result.call_args.kwargs.get("view")
                if view is not None:
                    view.stop()

    async def test_second_pass_different_target_same_cooldown_once_only(self):
        state = self.ready("su57")
        target = self.cog._vehicle_settle(self.users["2"], "apache")
        target.update(owned=True, ammo=5)
        await commands.execute(self.cog, self.i, self.target, "su57", "wallet")
        token = state["second_pass"]["token"]
        last = state["last_deploy_at"]
        await commands.execute(self.cog, self.i, self.target, "su57", "wallet", token=token)
        self.assertEqual(state["ammo"], 3)
        await commands.execute(self.cog, self.i, self.target, "su57", "ammo:apache", token=token)
        self.assertEqual(state["ammo"], 2)
        self.assertEqual(target["ammo"], 2)
        self.assertEqual(state["last_deploy_at"], last)
        await commands.execute(self.cog, self.i, self.target, "su57", "aa:s400", token=token)
        self.assertEqual(state["ammo"], 2)

    async def test_intercept_repair_keeps_unspent_packages(self):
        state = self.ready("su57")
        self.users["2"].update(s400_owned=True, s400_interceptors=2)
        with patch("cogs.air_dominance.random.randint", return_value=1):
            await commands.execute(self.cog, self.i, self.target, "su57", "wallet")
        self.assertTrue(state["owned"])
        self.assertTrue(state["jet_damaged"])
        self.assertEqual(state["ammo"], 3)
        self.assertEqual(self.users["2"]["s400_interceptors"], 1)
        before = self.users["1"]["donuts"]
        await commands.repair(self.cog, self.i, "su57", self.cfg)
        self.assertEqual(before - self.users["1"]["donuts"], 112500000000)
        with (
            patch("cogs.economy._now", return_value=utcnow() + dt.timedelta(hours=3)),
            patch("szofie.air_dominance.utcnow", return_value=utcnow() + dt.timedelta(hours=3)),
        ):
            self.cog._vehicle_settle(self.users["1"], "su57")
        self.assertFalse(state["jet_damaged"])
        self.assertEqual(state["ammo"], 3)

    async def test_f35_requires_recon_and_one_package_creates_link(self):
        state = self.ready("f35")
        await commands.execute(self.cog, self.i, self.target, "f35")
        self.assertEqual(state["ammo"], 4)
        self.recon()
        await commands.execute(self.cog, self.i, self.target, "f35")
        self.assertEqual(state["ammo"], 3)
        self.assertIsNotNone(air.link(self.users["1"], 2, self.users["2"]))

    async def test_j20_source_loss_and_no_collateral(self):
        self.ready("j20")
        target = self.cog._vehicle_settle(self.users["2"], "u2")
        target.update(owned=True)
        self.recon()
        before = self.users["2"]["donuts"]
        with patch("cogs.air_dominance.random.randint", return_value=1):
            await commands.execute(self.cog, self.i, self.target, "j20", "u2")
        self.assertFalse(target["owned"])
        self.assertEqual(before, self.users["2"]["donuts"])

    async def test_superweapon_pools_and_hulls_are_rejected_without_cost(self):
        state = self.ready("su57")
        self.recon()
        for objective in (
            "ammo:thor",
            "ammo:sm3",
            "vehicle:nyx",
            "vehicle:isd",
            "vehicle:aegis",
            "vehicle:deathstar",
        ):
            await commands.execute(self.cog, self.i, self.target, "su57", objective)
            self.assertEqual(state["ammo"], 4)
            self.assertIsNone(state["last_deploy_at"])

    async def test_patrol_one_per_player_strongest_defence_spends_only_one(self):
        state = self.ready("f22")
        await commands.patrol(self.cog, self.i)
        self.assertEqual(state["ammo"], 3)
        self.users["1"].update(s400_owned=True, s400_interceptors=2)
        choice = commands.jet_defence(self.cog, 10, 1, self.users["1"], self.cfg, "j20")
        self.assertEqual(choice[2], "F-22A combat air patrol")
        with patch("cogs.air_dominance.random.randint", return_value=100):
            commands.engage(choice, self.cfg, 2)
        self.assertEqual(self.users["1"]["s400_interceptors"], 2)
        self.assertIsNone(state["patrol_until"])

    async def test_expired_and_thor_cancelled_second_pass_cannot_spend(self):
        state = self.ready("su57")
        await commands.execute(self.cog, self.i, self.target, "su57", "wallet")
        token = state["second_pass"]["token"]
        state["second_pass"]["until"] = air.deadline(-1)
        await commands.execute(self.cog, self.i, self.target, "su57", "ammo:apache", token=token)
        self.assertEqual(state["ammo"], 3)
        air.clear_operations(self.users["1"])
        await commands.execute(self.cog, self.i, self.target, "su57", "wallet", token=token)
        self.assertEqual(state["ammo"], 3)

    async def test_nyx_target_objectives_never_leak(self):
        self.users["2"]["nyx"] = {"status": "orbit", "active_until": air.deadline(3)}
        self.cog._vehicle_settle(self.users["2"], "u2")["owned"] = True
        self.i.namespace = types.SimpleNamespace(vehicle="j20", target=self.target)
        self.assertEqual(await commands.objectives(self.cog, self.i, ""), [])

    async def test_autocomplete_finds_every_jet_within_discord_limits(self):
        for model in air.JETS:
            for renderer in (_vehicle_build_autocomplete, _vehicle_load_autocomplete):
                rows = await renderer(self.i, model)
                self.assertIn(model, [row.value for row in rows])
                self.assertLessEqual(len(rows), 25)

    async def test_country_cooldown_does_not_mutate_on_sortie(self):
        self.ready("su57")
        self.users["1"]["country_vehicle_cooldowns"] = {"su57": air.deadline(-1)}
        before = copy.deepcopy(self.users["1"]["country_vehicle_cooldowns"])
        await commands.execute(self.cog, self.i, self.target, "su57", "wallet")
        self.assertEqual(before, self.users["1"]["country_vehicle_cooldowns"])

    async def test_stronger_ground_defence_does_not_spend_patrol(self):
        self.cfg = Config(**{"economy.vehicle_b52_intercept_chance": 90})
        defender = self.users["2"]
        defender.update(s400_owned=True, s400_interceptors=2)
        state = self.cog._vehicle_settle(defender, "f22")
        state.update(owned=True, ammo=2, patrol_until=air.deadline(2), patrol_target="2")
        choice = commands.conventional_choice(self.cog, 10, 2, defender, self.cfg, "b52", self.users["1"])
        self.assertEqual(choice[2], "S-400 40N6E")
        with patch("cogs.air_dominance.random.randint", return_value=100):
            commands.engage(choice, self.cfg, 1, "b52")
        self.assertEqual(defender["s400_interceptors"], 1)
        self.assertIsNotNone(state["patrol_until"])

    async def test_second_pass_other_user_cannot_open(self):
        view = commands.SecondPass(self.cog, 1, self.target, "authorization")
        other = types.SimpleNamespace(user=types.SimpleNamespace(id=3))
        await view.again.callback(other)
        self.respond.assert_awaited()
        self.assertIn("not your sortie", self.respond.await_args.kwargs["embed"].description)


class CommandIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_jet_country_campaign_ignores_mission_turnaround(self):
        from discord import app_commands
        from cogs.countries import Countries
        from tests.test_system_integration import _FakeBot, _FakeInteraction

        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            bot.cogs["Economy"] = cog
            user = bot.economy.user(1, 123, 100)
            user.update(donuts=100000000, bank=0)
            state = cog._vehicle_settle(user, "su57")
            state.update(owned=True, ammo=4, last_deploy_at=utcnow().isoformat())
            last = state["last_deploy_at"]
            interaction = _FakeInteraction(user_id=123)
            with patch("cogs.countries.random.randint", return_value=100):
                await Countries.invade.callback(
                    Countries(bot),
                    interaction,
                    "fji",
                    app_commands.Choice(name="Su-57", value="su57"),
                    20000000,
                )
            self.assertEqual(state["ammo"], 3)
            self.assertEqual(state["last_deploy_at"], last)
            self.assertIsNotNone(user["country_invasion_until"])

    async def test_build_load_callbacks_and_real_time_settlement(self):
        from tests.test_system_integration import _FakeBot, _FakeInteraction

        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            bot.cogs["Economy"] = cog
            user = bot.economy.user(1, 123, 100)
            user.update(donuts=10**15, bank=0)
            with patch.object(cog, "_strategic_art", return_value=None):
                await EconomyCog.vehicle_build.callback(cog, _FakeInteraction(), "f22")
                state = user["vehicles"]["f22"]
                self.assertFalse(state["owned"])
                due = dt.datetime.fromisoformat(state["building_until"])
                with patch("cogs.economy._now", return_value=due):
                    cog._vehicle_settle(user, "f22")
                    await EconomyCog.vehicle_load.callback(cog, _FakeInteraction(), "f22", 4)
                self.assertEqual(state["loading_qty"], 4)
                self.assertEqual(state["ammo"], 0)
                loaded = dt.datetime.fromisoformat(state["loading_until"])
                with patch("cogs.economy._now", return_value=loaded):
                    cog._vehicle_settle(user, "f22")
                self.assertEqual(state["ammo"], 4)
