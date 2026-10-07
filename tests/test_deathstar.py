"""Death Star accounting, restart safety, destructive scope and command tests."""

from __future__ import annotations
import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import discord
from discord import app_commands
from cogs.death_star import DeathStarCog, DeathStarAlert
from cogs.space import SpaceCog
from cogs.thor import ThorCog
from cogs.imperial_star_destroyer import ImperialStarDestroyerCog
from szofie import deathstar as ds, space
from szofie.economy import _default_user, Economy
from szofie.betting import parse_amount
from szofie.ledger import incoming_security_actor
from tests.test_system_integration import _FakeBot, _FakeInteraction, _FakeUser, _assert_embed_valid

NOW = dt.datetime(2026, 10, 2, tzinfo=dt.timezone.utc)


class DeathStarStateTests(unittest.TestCase):
    def test_exact_project_budget_and_sextillion_parser(self):
        self.assertEqual(sum((s["cost"] for s in ds.COMPONENTS.values())), 800 * ds.SX)
        self.assertEqual(ds.project_cost(), 10**24)
        self.assertEqual(ds.DEPLOY_COST, 125 * ds.SX // 10)
        self.assertEqual(ds.ASSEMBLY_COST, 100 * ds.SX)
        self.assertEqual(parse_amount("1.5sx"), 1500 * ds.QI)
        self.assertEqual(parse_amount("0.000000000000000000001sx"), 1)

    def test_offline_fabrication_and_squadron_settle_once(self):
        user = _default_user(0)
        state = ds.normalize(user)
        state["components"]["reactor"].update(
            status="fabricating", ready_at=(NOW - dt.timedelta(days=1)).isoformat()
        )
        state.update(
            squadron_stock=2, squadron_until=NOW.isoformat(), charged=False, charge_until=NOW.isoformat()
        )
        ds.settle(user, NOW)
        ds.settle(user, NOW)
        self.assertEqual(state["components"]["reactor"]["status"], "ready")
        self.assertEqual(state["squadron_stock"], 3)
        self.assertTrue(state["charged"])
        self.assertEqual(user["donuts"], 0)

    def test_assembly_must_wait_for_online_defence_window(self):
        user = _default_user(0)
        state = ds.normalize(user)
        state.update(assembling_until=(NOW - dt.timedelta(days=2)).isoformat(), active_window_id="assembly")
        ds.settle(user, NOW)
        self.assertFalse(state["operational"])
        self.assertEqual(user["donuts"], 0)
        state.update(active_window_id=None, assembling_until=NOW.isoformat())
        ds.settle(user, NOW)
        self.assertTrue(state["operational"])
        self.assertTrue(all((p["status"] == "assembled" for p in state["components"].values())))

    def test_production_is_exact_capped_and_idempotent(self):
        user = _default_user(10)
        state = ds.normalize(user)
        state.update(operational=True, income_at=(NOW - dt.timedelta(days=20)).isoformat())
        self.assertEqual(ds.settle(user, NOW), 35 * ds.QI)
        self.assertEqual(ds.settle(user, NOW), 0)
        self.assertEqual(user["donuts"], 35 * ds.QI + 10)
        self.assertEqual(ds.settle(user, NOW + dt.timedelta(seconds=1)), 5 * ds.QI // 86400)
        self.assertEqual(user["deep_vault_balance"], 0)

    def test_development_splits_old_and_new_income_at_deadline(self):
        user = _default_user(0)
        state = ds.normalize(user)
        state.update(
            operational=True,
            income_at=(NOW - dt.timedelta(days=2)).isoformat(),
            development_until=(NOW - dt.timedelta(days=1)).isoformat(),
        )
        self.assertEqual(ds.settle(user, NOW), 145 * ds.QI // 10)
        self.assertEqual(state["development"], 1)
        self.assertEqual(ds.settle(user, NOW), 0)
        state["development"] = 10
        self.assertEqual(ds.gdp(state) // 80, 50 * ds.QI)

    def test_disabled_and_charging_intervals_never_accrue_later(self):
        for field, value in (("damaged", True), ("active_window_id", "fire")):
            user = _default_user(0)
            state = ds.normalize(user)
            state.update(operational=True, income_at=(NOW - dt.timedelta(days=2)).isoformat())
            state[field] = value
            self.assertEqual(ds.settle(user, NOW), 0)
            state[field] = False if field == "damaged" else None
            self.assertEqual(ds.settle(user, NOW + dt.timedelta(days=1)), 5 * ds.QI)

    def test_upgrade_catchup_cap_applies_once_across_both_rates(self):
        user = _default_user(0)
        state = ds.normalize(user)
        state.update(
            operational=True,
            income_at=(NOW - dt.timedelta(days=20)).isoformat(),
            development_until=(NOW - dt.timedelta(days=10)).isoformat(),
        )
        self.assertEqual(ds.settle(user, NOW), 665 * ds.QI // 10)
        self.assertEqual(ds.settle(user, NOW), 0)

    def test_repair_income_starts_at_completion_not_before(self):
        user = _default_user(0)
        state = ds.normalize(user)
        state.update(
            operational=True,
            damaged=True,
            damage_kind="station",
            repairing_until=(NOW - dt.timedelta(days=1)).isoformat(),
            income_at=(NOW - dt.timedelta(days=10)).isoformat(),
        )
        self.assertEqual(ds.settle(user, NOW), 5 * ds.QI)
        self.assertFalse(state["damaged"])
        self.assertEqual(ds.settle(user, NOW), 0)

    def test_cargo_moves_rods_enchants_and_vehicle_state_without_cloning(self):
        user = _default_user(100)
        user["rods"] = {"abyssal": 1}
        user["rod_enchants"] = {"abyssal": ["sharp", "swift"]}
        user["equipped_rod"] = "abyssal"
        user["vehicles"]["apache"].update(owned=True, ammo=6, comanche_upgraded=True)
        ds.transfer_cargo(user, "load", "rods", "abyssal", 1)
        ds.transfer_cargo(user, "load", "vehicles", "apache", 1)
        self.assertIsNone(user["equipped_rod"])
        self.assertNotIn("apache", user["vehicles"])
        ds.transfer_cargo(user, "unload", "rods", "abyssal", 1)
        ds.transfer_cargo(user, "unload", "vehicles", "apache", 1)
        self.assertEqual(user["rod_enchants"]["abyssal"], ["sharp", "swift"])
        self.assertEqual(user["vehicles"]["apache"]["ammo"], 6)
        self.assertTrue(user["vehicles"]["apache"]["comanche_upgraded"])
        with self.assertRaises(ValueError):
            ds.transfer_cargo(user, "load", "rods", "abyssal", 2)

    def test_cargo_funds_and_fish_age_preserve_exact_assets(self):
        user = _default_user(10**23 + 17)
        ds.transfer_cargo(user, "load", "donuts", None, 10**23)
        self.assertEqual(user["donuts"], 17)
        ds.transfer_cargo(user, "unload", "donuts", None, 10**23)
        self.assertEqual(user["donuts"], 10**23 + 17)
        user["fish"] = {"cod": 5}
        user["fish_meta"] = {"cod": {"age": 1.5}}
        ds.transfer_cargo(user, "load", "fish", "cod", 3)
        ds.transfer_cargo(user, "unload", "fish", "cod", 3)
        self.assertEqual(user["fish"]["cod"], 5)
        self.assertEqual(user["fish_meta"]["cod"]["age"], 1.5)

    def test_reconstruction_never_restores_prior_holdings(self):
        doc = {"users": {}, "space_galaxy": {"claims": {"mars": 2}, "discovered": {}}}
        root = ds.world(doc)
        root["destroyed"]["mars"] = {"at": NOW.isoformat()}
        root["reconstructions"]["mars"] = {"ready_at": NOW.isoformat()}
        self.assertEqual(ds.settle_world(doc, NOW), ["mars"])
        self.assertFalse(ds.destroyed(doc, "mars"))
        self.assertEqual(space.galaxy(doc)["claims"], {})
        self.assertEqual(ds.settle_world(doc, NOW), [])

    def test_earth_and_sun_never_wiped(self):
        for key in ("earth", "sun", "deathstar-123"):
            doc = {"users": {"2": _default_user(100)}}
            with self.assertRaises(ValueError):
                ds.wipe_planet(doc, key, 1, 2, {}, NOW)
            self.assertEqual(doc["users"]["2"]["donuts"], 100)

    def test_project_escrow_uses_integer_ceiling_and_failed_payment_is_unchanged(self):
        user = _default_user(0)
        state = ds.normalize(user)
        state["project_funds"] = 10**26
        before = copy.deepcopy(user)
        with self.assertRaises(ValueError):
            ds.pay_project(user, state, ds.COMPONENTS["reactor"]["cost"])
        self.assertEqual(user, before)
        user["donuts"] = 10**25
        cost = 10**24 + 1
        self.assertEqual(ds.pay_project(user, state, cost), (cost + 3) // 4)

    def test_thor_erases_exposed_station_cargo_but_not_the_orbital_station(self):
        user = _default_user(0)
        state = ds.normalize(user)
        state.update(operational=True)
        state["cargo"].update(donuts=123, plushies={"labcoat": 1}, rods={"abyssal": 1})
        summary = ThorCog._wipe_target(user, {})
        self.assertEqual(summary["visible"], 123)
        self.assertEqual(summary["plushies"], 1)
        self.assertTrue(state["operational"])
        self.assertEqual(state["cargo"], ds.cargo())

    def test_cinder_cancels_deathstar_windows_but_does_not_recreate_destroyed_planet(self):
        cog = object.__new__(ImperialStarDestroyerCog)
        cog.cfg = lambda gid: {}
        doc = {
            "users": {"2": _default_user(100)},
            "deathstar_windows": {"DS-1": {}},
            "deathstar_notices": {"pending": {}},
            "deathstar_world": {"destroyed": {"mars": {}}, "reconstructions": {"mars": {}}},
        }
        cog._apply_cinder_wipe(doc, {"guild_id": 1})
        self.assertEqual(doc["deathstar_windows"], {})
        self.assertEqual(doc["deathstar_world"]["reconstructions"], {})
        self.assertIn("mars", doc["deathstar_world"]["destroyed"])


class CapturingLedger:
    def __init__(self):
        self.rows = []

    async def record(self, *args, **kwargs):
        self.rows.append((args, kwargs))


class Channel:
    def __init__(self):
        self.messages = []

    async def send(self, **kwargs):
        self.messages.append(kwargs)
        return SimpleNamespace(id=len(self.messages))


class DeathStarCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bot = _FakeBot(self.root)
        self.channel = Channel()
        self.bot.get_channel = lambda cid: self.channel
        self.bot.ledger = CapturingLedger()
        self.cog = DeathStarCog(self.bot)
        self.art = mock.patch("cogs.death_star.ART", self.root / "art")
        self.backup = mock.patch("cogs.death_star.BACKUPS", self.root / "backups")
        self.art.start()
        self.backup.start()

    def tearDown(self):
        self.art.stop()
        self.backup.stop()
        self.tmp.cleanup()

    def operational(self, uid=123):
        user = self.cog.user(1, uid)
        user["donuts"] = 10**25
        state = ds.normalize(user)
        state.update(operational=True, charged=True, location="mars", income_at=ds.now().isoformat())
        return (user, state)

    def window(self, kind="fire", *, success=False):
        user, state = self.operational()
        interaction = _FakeInteraction()
        window = self.cog.open_window(
            self.cog.doc(1),
            state,
            interaction,
            kind,
            120,
            component="hull" if kind == "component" else None,
            body="mars" if kind == "fire" else None,
            sovereign=2 if kind == "fire" else None,
        )
        record = ds.windows(self.cog.doc(1))[window]
        record.update(
            announced=True,
            remaining_seconds=0,
            interceptors=[{"user": 3, "success": True}] if success else [],
        )
        if kind == "fire":
            space.galaxy(self.cog.doc(1))["claims"]["mars"] = 2
        if kind == "assembly":
            state["operational"] = False
            state["assembling_until"] = (ds.now() + dt.timedelta(hours=60)).isoformat()
            for part in state["components"].values():
                part["status"] = "orbit"
        if kind == "component":
            state["components"]["hull"].update(status="launching", window_id=window)
        return (user, state, window, record)

    async def test_three_parallel_builds_duplicate_prevention_and_no_fourth_charge(self):
        user = self.cog.user(1, 123)
        user["donuts"] = 10**25
        user["imperial_star_destroyer"]["operational"] = True
        ds.normalize(user)["transport_owned"] = True
        for key in ("framework", "hull", "reactor"):
            await DeathStarCog.build.callback(
                self.cog, _FakeInteraction(), app_commands.Choice(name=key, value=key)
            )
        balance = user["donuts"]
        for key in ("framework", "command"):
            interaction = _FakeInteraction()
            await DeathStarCog.build.callback(self.cog, interaction, app_commands.Choice(name=key, value=key))
            self.assertEqual(user["donuts"], balance)
            self.assertEqual(interaction.original_edits[-1]["embed"].color.value, ui_error_color())

    async def test_deployment_publishes_warning_and_starts_online_window(self):
        user, state = self.operational()
        state["components"]["hull"]["status"] = "ready"
        state["transport_owned"] = True
        await DeathStarCog.deploy.callback(
            self.cog, _FakeInteraction(), app_commands.Choice(name="hull", value="hull")
        )
        record = ds.windows(self.cog.doc(1))[state["active_window_id"]]
        self.assertTrue(record["announced"])
        self.assertEqual(record["remaining_seconds"], 7200)
        self.assertIsInstance(self.channel.messages[-1]["view"], DeathStarAlert)
        balance = user["donuts"]
        await DeathStarCog.deploy.callback(
            self.cog, _FakeInteraction(), app_commands.Choice(name="hull", value="hull")
        )
        self.assertEqual(user["donuts"], balance)

    async def test_unposted_warning_cannot_consume_defence_time(self):
        user, state = self.operational()
        window = self.cog.open_window(
            self.cog.doc(1), state, _FakeInteraction(), "fire", 120, body="mars", sovereign=2
        )
        self.bot.get_channel = lambda cid: None
        await self.cog.tick(1, 3600)
        self.assertEqual(ds.windows(self.cog.doc(1))[window]["remaining_seconds"], 7200)
        self.assertFalse(ds.windows(self.cog.doc(1))[window]["announced"])

    async def test_interceptors_one_per_defender_and_five_maximum(self):
        _, _, window, record = self.window()
        record["remaining_seconds"] = 100
        for uid in (3, 4, 5, 6, 7):
            ds.normalize(self.cog.user(1, uid))["squadron_stock"] = 2
            with mock.patch("cogs.death_star.random.randint", return_value=100):
                await self.cog.commit_interceptor(_FakeInteraction(user_id=uid), window)
        self.assertEqual(len(record["interceptors"]), 5)
        for uid in (3, 8):
            state = ds.normalize(self.cog.user(1, uid))
            state["squadron_stock"] = 2
            await self.cog.commit_interceptor(_FakeInteraction(user_id=uid), window)
            self.assertEqual(state["squadron_stock"], 2)

    async def test_station_disabled_on_interception_and_repair_keeps_project(self):
        user, state, window, _ = self.window(success=True)
        await self.cog.resolve_window(1, window)
        self.assertTrue(state["damaged"])
        self.assertTrue(state["operational"])
        before = user["donuts"]
        await DeathStarCog.repair.callback(self.cog, _FakeInteraction())
        self.assertEqual(before - user["donuts"], ds.REPAIR_COST)
        self.assertAlmostEqual(
            (ds.at(state["repairing_until"]) - ds.now()).total_seconds(), 48 * 3600, delta=3
        )
        self.assertFalse(ds.destroyed(self.cog.doc(1), "mars"))

    async def test_assembly_sabotage_preserves_all_sections(self):
        user, state, window, _ = self.window("assembly", success=True)
        await self.cog.resolve_window(1, window)
        self.assertTrue(state["damaged"])
        self.assertEqual(state["damage_kind"], "assembly")
        self.assertTrue(all((p["status"] == "orbit" for p in state["components"].values())))
        await DeathStarCog.repair.callback(self.cog, _FakeInteraction())
        self.assertAlmostEqual(
            (ds.at(state["repairing_until"]) - ds.now()).total_seconds(), 24 * 3600, delta=3
        )

    async def test_planet_hit_has_exact_backup_and_successful_victim_attack_logs(self):
        user, state, window, _ = self.window()
        victim = self.cog.user(1, 2)
        victim["donuts"] = 555
        space.normalize(victim)["colonies"]["mars"] = {"gdp": 99}
        space.galaxy(self.cog.doc(1))["claims"]["mars"] = 2
        await self.cog.resolve_window(1, window)
        saved = json.loads(next((self.root / "backups").glob("*.json")).read_text())
        self.assertEqual(saved["users"]["2"]["donuts"], 555)
        self.assertEqual(self.cog.doc(1)["users"]["2"]["donuts"], 0)
        self.assertTrue(ds.destroyed(self.cog.doc(1), "mars"))
        rows = [(args, kwargs) for args, kwargs in self.bot.ledger.rows if args[3] == "deathstar-planet-hit"]
        self.assertEqual(len(rows), 1)
        args, kwargs = rows[0]
        self.assertEqual(
            incoming_security_actor({"reason": args[3], "user": args[1], "actor": kwargs["actor"]}, 2), 123
        )
        await self.cog.resolve_window(1, window)
        self.assertEqual(len([r for r in self.bot.ledger.rows if r[0][3] == "deathstar-planet-hit"]), 1)

    async def test_backup_failure_never_wipes_or_consumes_due_window(self):
        _, state, window, _ = self.window()
        self.cog.user(1, 2)["donuts"] = 555
        with mock.patch("cogs.death_star.Storage._write", side_effect=OSError("backup failed")):
            with self.assertRaises(OSError):
                await self.cog.resolve_window(1, window)
        self.assertEqual(self.cog.doc(1)["users"]["2"]["donuts"], 555)
        self.assertIn(window, ds.windows(self.cog.doc(1)))
        self.assertEqual(state["active_window_id"], window)

    async def test_failed_impact_persistence_rolls_back_in_memory_for_retry(self):
        _, _, window, _ = self.window()
        self.cog.user(1, 2)["donuts"] = 555
        with mock.patch.object(self.cog.econ, "save", side_effect=OSError("disk failed")):
            with self.assertRaises(OSError):
                await self.cog.resolve_window(1, window)
        self.assertEqual(self.cog.doc(1)["users"]["2"]["donuts"], 555)
        self.assertIn(window, ds.windows(self.cog.doc(1)))
        self.assertFalse(ds.destroyed(self.cog.doc(1), "mars"))

    async def test_guide_is_public_paged_and_within_discord_limits(self):
        interaction = _FakeInteraction()
        await DeathStarCog.guide.callback(self.cog, interaction)
        call = interaction.response.calls[0]
        self.assertFalse(call.get("ephemeral", False))
        self.assertEqual(len(call["view"].pages), 10)
        for page in call["view"].pages:
            _assert_embed_valid(self, page)
        self.assertEqual(call["view"].author_id, 123)
        self.assertNotIn("owner_", " ".join((p.description for p in call["view"].pages)))

    async def test_atlas_shows_artificial_moons_and_debris_fields(self):
        self.operational()
        ds.world(self.cog.doc(1))["destroyed"]["mars"] = {"at": ds.now().isoformat()}
        interaction = _FakeInteraction()
        await SpaceCog.atlas.callback(SpaceCog(self.bot), interaction)
        pages = interaction.response.calls[0]["view"].pages
        content = "\n".join((p.description for p in pages))
        self.assertIn("DESTROYED: debris field", content)
        self.assertIn("deathstar-123", content)
        self.assertIn("artificial moon", content)
        for page in pages:
            _assert_embed_valid(self, page)

    async def test_fire_preflight_blocks_earth_self_allies_and_debris(self):
        _, state = self.operational()
        doc = self.cog.doc(1)
        state["location"] = "earth"
        with self.assertRaises(ValueError):
            self.cog.fire_preflight(doc, state, 123, ds.now())
        state["location"] = "mars"
        space.galaxy(doc)["claims"]["mars"] = 123
        with self.assertRaises(ValueError):
            self.cog.fire_preflight(doc, state, 123, ds.now())
        space.galaxy(doc)["claims"]["mars"] = 2
        with mock.patch("cogs.death_star.coalitions.are_allied", return_value=True):
            with self.assertRaises(ValueError):
                self.cog.fire_preflight(doc, state, 123, ds.now())
        ds.world(doc)["destroyed"]["mars"] = {}
        with self.assertRaises(ValueError):
            self.cog.fire_preflight(doc, state, 123, ds.now())

    async def test_shared_alias_reuses_same_station_lock_and_window_store(self):
        self.econ_alias()
        self.assertIs(self.cog.lock(1), self.cog.lock(9))
        self.assertIs(ds.windows(self.cog.doc(1)), ds.windows(self.cog.doc(9)))

    async def test_confirmed_fire_consumes_one_charge_and_posts_server_warning(self):
        _, state = self.operational()
        space.galaxy(self.cog.doc(1))["claims"]["mars"] = 2
        click = _FakeInteraction()
        view = SimpleNamespace(value=True, interaction=click, wait=mock.AsyncMock())
        with mock.patch("cogs.death_star.ui.ConfirmView", return_value=view):
            await DeathStarCog.fire.callback(self.cog, _FakeInteraction())
        self.assertFalse(state["charged"])
        self.assertIsNotNone(state["last_fire_at"])
        record = ds.windows(self.cog.doc(1))[state["active_window_id"]]
        self.assertEqual(record["sovereign"], 2)
        self.assertTrue(record["announced"])
        self.assertEqual(self.channel.messages[-1]["content"], "@everyone")
        self.assertTrue(self.channel.messages[-1]["allowed_mentions"].everyone)
        self.assertFalse(ds.destroyed(self.cog.doc(1), "mars"))

    async def test_cancelled_confirmation_spends_nothing(self):
        user, state = self.operational()
        before = user["donuts"]
        view = SimpleNamespace(value=False, interaction=None, wait=mock.AsyncMock())
        with mock.patch("cogs.death_star.ui.ConfirmView", return_value=view):
            await DeathStarCog.fire.callback(self.cog, _FakeInteraction())
        self.assertTrue(state["charged"])
        self.assertIsNone(state["last_fire_at"])
        self.assertEqual(user["donuts"], before)
        self.assertEqual(ds.windows(self.cog.doc(1)), {})

    async def test_failed_fire_commit_save_preserves_charge_for_retry(self):
        self.operational()
        view = SimpleNamespace(value=True, interaction=_FakeInteraction(), wait=mock.AsyncMock())
        save = self.cog.econ.save
        calls = 0

        async def fail_second_save(gid):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("disk failure")
            await save(gid)

        with (
            mock.patch("cogs.death_star.ui.ConfirmView", return_value=view),
            mock.patch.object(self.cog.econ, "save", side_effect=fail_second_save),
        ):
            with self.assertRaises(OSError):
                await DeathStarCog.fire.callback(self.cog, _FakeInteraction())
        state = ds.normalize(self.cog.doc(1)["users"]["123"])
        self.assertTrue(state["charged"])
        self.assertIsNone(state["last_fire_at"])
        self.assertEqual(ds.windows(self.cog.doc(1)), {})

    async def test_confirmation_revalidates_new_sovereign_without_spending_charge(self):
        _, state = self.operational()
        doc = self.cog.doc(1)
        space.galaxy(doc)["claims"]["mars"] = 2

        async def change_owner():
            space.galaxy(doc)["claims"]["mars"] = 3

        click = _FakeInteraction()
        view = SimpleNamespace(value=True, interaction=click, wait=change_owner)
        with mock.patch("cogs.death_star.ui.ConfirmView", return_value=view):
            await DeathStarCog.fire.callback(self.cog, _FakeInteraction())
        self.assertTrue(state["charged"])
        self.assertIsNone(state["last_fire_at"])
        self.assertIn("sovereignty changed", click.followup.calls[-1]["content"])

    async def test_changed_sovereignty_during_public_window_cancels_impact(self):
        _, _, window, _ = self.window()
        self.cog.user(1, 2)["donuts"] = 555
        space.galaxy(self.cog.doc(1))["claims"]["mars"] = 3
        await self.cog.resolve_window(1, window)
        self.assertEqual(self.cog.doc(1)["users"]["2"]["donuts"], 555)
        self.assertFalse(ds.destroyed(self.cog.doc(1), "mars"))
        self.assertNotIn(window, ds.windows(self.cog.doc(1)))

    async def test_http_warning_failure_retries_without_advancing_window(self):
        _, state = self.operational()
        window = self.cog.open_window(
            self.cog.doc(1), state, _FakeInteraction(), "fire", 120, body="mars", sovereign=None
        )
        failure = discord.HTTPException(SimpleNamespace(status=503, reason="Unavailable"), "unavailable")
        with mock.patch.object(self.channel, "send", side_effect=failure):
            await self.cog.tick(1, 30)
        self.assertFalse(ds.windows(self.cog.doc(1))[window]["announced"])
        self.assertEqual(ds.windows(self.cog.doc(1))[window]["remaining_seconds"], 7200)
        await self.cog.deliver_notices(1)
        self.assertTrue(ds.windows(self.cog.doc(1))[window]["announced"])

    async def test_squadron_preparation_cap_and_duplicate_cost(self):
        user = self.cog.user(1, 123)
        user["donuts"] = 100 * ds.QI
        state = ds.normalize(user)
        await DeathStarCog.squadron.callback(self.cog, _FakeInteraction())
        self.assertEqual(user["donuts"], 90 * ds.QI)
        await DeathStarCog.squadron.callback(self.cog, _FakeInteraction())
        self.assertEqual(user["donuts"], 90 * ds.QI)
        state.update(squadron_stock=3, squadron_until=None)
        await DeathStarCog.squadron.callback(self.cog, _FakeInteraction())
        self.assertEqual(user["donuts"], 90 * ds.QI)

    async def test_development_spends_exact_funds_and_materials_once(self):
        user, state = self.operational()
        state["cargo"]["materials"] = {"iron": 30}
        before = user["donuts"]
        await DeathStarCog.develop.callback(self.cog, _FakeInteraction())
        self.assertEqual(before - user["donuts"], 50 * ds.QI)
        self.assertEqual(state["cargo"]["materials"]["iron"], 5)
        await DeathStarCog.develop.callback(self.cog, _FakeInteraction())
        self.assertEqual(before - user["donuts"], 50 * ds.QI)

    async def test_reconstruction_consumes_materials_and_returns_unclaimed_world(self):
        user, state = self.operational()
        state["cargo"]["materials"] = {"iron": 100}
        doc = self.cog.doc(1)
        ds.world(doc)["destroyed"]["mars"] = {}
        before = user["donuts"]
        await DeathStarCog.reconstruct.callback(self.cog, _FakeInteraction(), "mars")
        self.assertEqual(before - user["donuts"], 250 * ds.QI)
        self.assertEqual(sum(state["cargo"]["materials"].values()), 0)
        ready = ds.at(ds.world(doc)["reconstructions"]["mars"]["ready_at"])
        ds.settle_world(doc, ready)
        self.assertFalse(ds.destroyed(doc, "mars"))
        self.assertNotIn("mars", space.galaxy(doc)["claims"])

    async def test_failed_purchase_save_rolls_back_for_safe_retry(self):
        user = self.cog.user(1, 123)
        user["donuts"] = 100 * ds.QI
        with mock.patch.object(self.cog.econ, "save", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                await DeathStarCog.squadron.callback(self.cog, _FakeInteraction())
        restored = self.cog.doc(1)["users"]["123"]
        self.assertEqual(restored["donuts"], 100 * ds.QI)
        self.assertIsNone(ds.normalize(restored)["squadron_until"])

    async def test_destroyed_world_blocks_ordinary_landing(self):
        user = self.cog.user(1, 123)
        space.normalize(user)["location"] = "mars"
        ds.world(self.cog.doc(1))["destroyed"]["mars"] = {}
        interaction = _FakeInteraction()
        interaction.command = SimpleNamespace(name="land")
        self.assertFalse(await SpaceCog(self.bot).guard(interaction))
        self.assertIn("debris field", interaction.response.calls[-1]["content"])

    async def test_sleep_gap_and_shared_alias_do_not_double_advance_windows(self):
        self.econ_alias()
        self.bot.guilds = [SimpleNamespace(id=1), SimpleNamespace(id=9)]
        self.cog._last_tick = 1
        with (
            mock.patch("cogs.death_star.time.monotonic", return_value=9000),
            mock.patch.object(self.cog, "tick", new_callable=mock.AsyncMock) as tick,
        ):
            await DeathStarCog.sweep.coro(self.cog)
        tick.assert_awaited_once_with(1, 30.0)

    def econ_alias(self):
        self.bot.economy.store.aliases[9] = 1


def ui_error_color():
    from szofie import ui

    return ui.COLOR_BAD


if __name__ == "__main__":
    unittest.main()
