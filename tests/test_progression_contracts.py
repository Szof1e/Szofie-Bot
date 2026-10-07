"""Offline regressions for the approved progression, civilian and launch package."""

import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from discord import app_commands
from cogs.economy import EconomyCog, _strategic_objective_autocomplete
from cogs.fishing import Fishing, FISH_BY_ID
from cogs.space import SpaceCog
from cogs.imperial_star_destroyer import ImperialStarDestroyerCog
from cogs.death_star import DeathStarCog
from szofie import activity_contracts as activities, activity_ui, coalitions, deathstar, guides
from szofie import imperial_star_destroyer as isd, launch_escorts, progression, space, space_fleet as fleet
from szofie.config import ConfigManager, DEFAULTS
from szofie.economy import _default_user, Economy
from szofie.storage import Storage
from tests.test_system_integration import _FakeBot, _FakeInteraction, _FakeUser, _assert_embed_valid


def ready(user, key, packs=None):
    hull = fleet.ship(user, key)
    hull.update(owned=True, deployed=True, location="earth")
    if key in fleet.LEGACY:
        space.normalize(user)[key + "_owned"] = True
    fleet.set_payload(user, key, fleet.CRAFT[key].capacity if packs is None else packs)
    return hull


class ProgressionMathTests(unittest.TestCase):
    def test_helper_allowances_and_price_credit(self):
        user = _default_user(0)
        user["android21_helper"] = True
        for tier, (_, _, bonus, _) in enumerate(progression.LICENCES, 1):
            user["employment"]["career_licence_tier"] = tier
            user["helper_certification_tier"] = tier - 1
            target, price = progression.helper_quote(user)
            previous = progression.HELPER_TOTAL_COSTS[tier - 2] if tier > 1 else 0
            self.assertEqual((target, price), (tier, progression.HELPER_TOTAL_COSTS[tier - 1] - previous))
            user["helper_certification_tier"] = tier
            self.assertEqual(progression.helper_allowance(user), bonus // 4)
            with self.assertRaises(ValueError):
                progression.helper_quote(user)
        user["android21_helper"] = False
        self.assertEqual(progression.helper_allowance(user), 0)

    def test_helper_cannot_exceed_owned_licence(self):
        user = {
            "android21_helper": True,
            "helper_certification_tier": 5,
            "employment": {"career_licence_tier": 1},
        }
        self.assertEqual(progression.helper_allowance(user), 18750000)
        user["employment"]["career_licence_tier"] = 0
        self.assertEqual(progression.helper_allowance(user), 0)
        with self.assertRaises(ValueError):
            progression.helper_quote(user)

    def test_fines_scale_and_cannot_take_hidden_funds(self):
        user = {"donuts": 10**12, "bank": 10**12, "deep_vault": 10**25, "continuity": {"funds": 10**25}}
        self.assertEqual(progression.theft_fine(user, 500000, 50), 10**10)
        self.assertEqual(progression.theft_fine(user, 2000000, 100), 2 * 10**10)
        self.assertEqual(progression.theft_fine({"donuts": 100, "bank": 50}, 500000, 50), 150)
        self.assertEqual(progression.theft_fine({"donuts": 0, "bank": 0}, 500000, 50), 0)
        self.assertEqual(progression.reversal(user, 10**30), 2 * 10**11)
        self.assertEqual(progression.reversal(user, 1000), 750)

    def test_uno_is_a_conservative_wallet_then_bank_transfer(self):
        cfg = SimpleNamespace(
            get=lambda key, fallback=None: DEFAULTS["economy"].get(key.split(".")[-1], fallback)
        )
        thief = {"donuts": 100, "bank": 900, "deep_vault": 10**20}
        victim = {"donuts": 50, "bank": 0}
        amount = object.__new__(EconomyCog)._uno_reverse(cfg, thief, victim, 10000)
        self.assertEqual(amount, 100)
        self.assertEqual(thief["donuts"] + thief["bank"] + victim["donuts"], 1050)
        self.assertEqual(thief["deep_vault"], 10**20)


class FishActivityTests(unittest.TestCase):
    def setUp(self):
        self.user = _default_user(0)
        self.now = dt.datetime.now(dt.timezone.utc)

    def test_two_stable_deliveries_known_species_and_daily_refresh(self):
        for abyss in (False, True):
            user = _default_user(0)
            if abyss:
                user["rods"]["abyssal"] = 1
            board = activities.fish_board(user, self.now)
            token = board["id"]
            self.assertEqual(len(board["offers"]), 2)
            self.assertEqual(activities.fish_board(user, self.now)["id"], token)
            for index, offer in enumerate(board["offers"]):
                self.assertTrue(set(offer["fish"]) <= set(FISH_BY_ID))
                self.assertFalse(set(offer["fish"]) & set(activities.ESOTERIC))
                user["fish"].update(offer["fish"])
                paid = activities.deliver_fish(user, token, index, self.now)
                self.assertEqual(paid, offer["reward"])
                with self.assertRaises(ValueError):
                    activities.deliver_fish(user, token, index, self.now)
            balance = user["donuts"]
            new = activities.fish_board(user, self.now + dt.timedelta(days=1))
            self.assertNotEqual(new["id"], token)
            self.assertEqual(user["donuts"], balance)
            with self.assertRaises(ValueError):
                activities.deliver_fish(user, token, 0, self.now + dt.timedelta(days=1))

    def test_missing_specimens_never_partially_consume(self):
        self.user["fish"] = {"voidjelly": 10}
        before = copy.deepcopy(self.user["fish"])
        with self.assertRaises(ValueError):
            activities.donate(self.user, "biosensor")
        self.assertEqual(self.user["fish"], before)
        self.assertEqual(activities.rare_bonus(self.user), 0)

    def test_biosensor_and_archive_consume_once_and_persist(self):
        self.user["fish"].update(activities.PROJECTS["biosensor"][1])
        activities.donate(self.user, "biosensor")
        self.assertEqual(activities.rare_bonus(self.user), 5)
        with self.assertRaises(ValueError):
            activities.donate(self.user, "biosensor")
        key = activities.ESOTERIC[0]
        self.user["fish"][key] = 2
        activities.donate(self.user, "archive:" + key)
        with self.assertRaises(ValueError):
            activities.donate(self.user, "archive:" + key)
        recovered = json.loads(json.dumps(self.user))
        self.assertEqual(activities.research(recovered)["archive"], [key])
        self.assertEqual(recovered["fish"][key], 1)

    def test_habitat_requires_base_and_caps_daily_conversion(self):
        activities.research(self.user)["habitat"] = True
        self.user["fish"].update(voidjelly=50, abyssalserpent=20)
        state = space.normalize(self.user)
        state["location"] = "mars"
        doc = {"users": {"123": self.user}}
        before = copy.deepcopy(self.user["fish"])
        with self.assertRaises(ValueError):
            activities.habitat(self.user, doc, self.now)
        self.assertEqual(self.user["fish"], before)
        site = space.colony(state, "mars")
        site["base"] = True
        for _ in range(3):
            activities.habitat(self.user, doc, self.now)
        self.assertEqual(sum(site["materials"].values()), 6)
        with self.assertRaises(ValueError):
            activities.habitat(self.user, doc, self.now)
        activities.habitat(self.user, doc, self.now + dt.timedelta(days=1))
        self.assertEqual(sum(site["materials"].values()), 8)
        self.assertEqual(self.user["donuts"], 0)

    def test_biosensor_changes_only_civilian_rare_roll(self):
        doc = {"users": {"123": self.user}}
        ready(self.user, "cutlass")
        space.normalize(self.user)["surveys"]["mars"] = self.now.isoformat()
        with patch("szofie.space_fleet.random.Random") as rng:
            rng.return_value.randint.return_value = 12
            first = fleet.start(doc, 123, "cutlass", "survey", "mars", self.now, "before")
            self.assertFalse(first["rare"])
            fleet.ship(self.user, "cutlass")["mission"] = None
            activities.research(self.user)["biosensor"] = True
            second = fleet.start(doc, 123, "cutlass", "survey", "mars", self.now, "after")
            self.assertTrue(second["rare"])
            self.assertEqual(first["materials"], second["materials"])


class SpaceContractTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime.now(dt.timezone.utc)
        self.user = _default_user(0)
        self.doc = {"users": {"123": self.user, "456": _default_user(0)}}
        state = space.normalize(self.user)
        state["surveys"] = {key: self.now.isoformat() for key in ("mars", "ceres", "psyche")}
        for key in ("cutlass", "prospector", "vulture", "transport"):
            ready(self.user, key)

    def accept(self, kind):
        board = activities.space_board(self.user, self.doc, self.now)
        index = next((i for i, o in enumerate(board["offers"]) if o["kind"] == kind))
        return activities.accept_space(self.user, self.doc, board["id"], index, self.now)

    def test_board_stable_eligible_and_one_active(self):
        board = activities.space_board(self.user, self.doc, self.now)
        self.assertEqual(len(board["offers"]), 3)
        self.assertEqual({o["kind"] for o in board["offers"]}, {"survey", "mine", "salvage"})
        self.assertEqual(activities.space_board(self.user, self.doc, self.now)["id"], board["id"])
        self.accept("survey")
        with self.assertRaises(ValueError):
            self.accept("mine")
        self.assertEqual(self.user["donuts"], 0)

    def test_supply_requires_owned_materials_and_surveyed_valid_route(self):
        state = space.normalize(self.user)
        for body in ("mars", "ceres"):
            space.colony(state, body)["base"] = True
        space.colony(state, "mars")["materials"] = {"metals": 12}
        contract = self.accept("supply")
        record = fleet.start(
            self.doc,
            123,
            "transport",
            "supply",
            contract["body"],
            self.now + dt.timedelta(seconds=1),
            "supply",
            source="mars",
            amount=10,
        )
        fleet.settle_world(self.doc, space.at(record["ready_at"]) + dt.timedelta(seconds=1))
        self.assertTrue(contract["completed"])
        self.assertEqual(self.user["donuts"], 6 * activities.Q)
        self.assertEqual(space.material_total(state["colonies"][contract["body"]]), 13)

    def test_old_missions_and_duplicate_arrivals_cannot_pay(self):
        contract = self.accept("survey")
        record = {
            "id": "old",
            "kind": "survey",
            "body": contract["body"],
            "started_at": (self.now - dt.timedelta(minutes=1)).isoformat(),
            "materials": {"metals": 2},
        }
        resolved = self.now + dt.timedelta(hours=2)
        self.assertEqual(activities.complete_space(self.user, record, resolved), 0)
        record["started_at"] = (self.now + dt.timedelta(seconds=1)).isoformat()
        paid = activities.complete_space(self.user, record, resolved)
        self.assertEqual(paid, 3 * activities.Q)
        self.assertEqual(activities.complete_space(self.user, record, resolved), 0)
        self.assertEqual(self.user["donuts"], paid)

    def test_real_mining_arrival_pays_once_across_serialized_restart(self):
        contract = self.accept("mine")
        record = fleet.start(
            self.doc, 123, "prospector", "mine", contract["body"], self.now + dt.timedelta(seconds=1), "mine"
        )
        recovered = json.loads(json.dumps(self.doc))
        current = space.at(record["ready_at"]) + dt.timedelta(seconds=1)
        fleet.settle_world(recovered, current)
        user = recovered["users"]["123"]
        self.assertEqual(user["donuts"], 5 * activities.Q)
        self.assertEqual(
            sum(space.normalize(user)["field_materials"].values()), sum(record["materials"].values()) + 3
        )
        fleet.settle_world(recovered, current + dt.timedelta(days=1))
        self.assertEqual(user["donuts"], 5 * activities.Q)

    def test_salvage_keeps_existing_output_and_cannot_farm_own_wreck(self):
        contract = self.accept("salvage")
        space.galaxy(self.doc).setdefault("wrecks", {})["own"] = {"body": contract["body"], "excluded": [123]}
        with self.assertRaises(ValueError):
            fleet.start(
                self.doc,
                123,
                "vulture",
                "salvage",
                contract["body"],
                self.now + dt.timedelta(seconds=1),
                "bad",
                wreck_id="own",
            )
        self.assertFalse(contract["completed"])
        record = fleet.start(
            self.doc, 123, "vulture", "salvage", contract["body"], self.now + dt.timedelta(seconds=1), "good"
        )
        fleet.settle_world(self.doc, space.at(record["ready_at"]) + dt.timedelta(seconds=1))
        self.assertEqual(self.user["donuts"], 4 * activities.Q + record["payout"])

    def test_expired_wrong_body_and_destroyed_destination_do_not_pay(self):
        contract = self.accept("survey")
        record = fleet.start(
            self.doc, 123, "cutlass", "survey", contract["body"], self.now + dt.timedelta(seconds=1), "survey"
        )
        wrong = dict(record, body="wrong")
        self.assertEqual(activities.complete_space(self.user, wrong, self.now + dt.timedelta(hours=2)), 0)
        self.assertEqual(activities.complete_space(self.user, record, self.now + dt.timedelta(hours=49)), 0)
        with patch.object(deathstar, "destroyed", return_value=True):
            fleet.settle_world(self.doc, space.at(record["ready_at"]) + dt.timedelta(seconds=1))
        self.assertFalse(contract["completed"])
        self.assertEqual(self.user["donuts"], 0)


class LaunchEscortTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime.now(dt.timezone.utc)
        self.doc = {"users": {"123": _default_user(0), "456": _default_user(0)}}
        self.user = self.doc["users"]["123"]

    def test_each_craft_reserves_limited_packs_and_returns_unused_once(self):
        for key, (chance, cap) in launch_escorts.SCREENING.items():
            with self.subTest(key=key):
                hull = ready(self.user, key)
                original = fleet.payload(self.user, key)
                record = {"builder": 123, "kind": "component"}
                prepared = launch_escorts.prepare(self.doc, 123, 123, key, self.now)
                launch_escorts.reserve(self.doc, record, "W", prepared)
                self.assertEqual(fleet.payload(self.user, key), original - cap)
                rng = SimpleNamespace(randint=lambda *_: chance)
                self.assertTrue(launch_escorts.screen(self.doc, record, self.now, rng))
                launch_escorts.release(self.doc, record)
                launch_escorts.release(self.doc, record)
                self.assertEqual(fleet.payload(self.user, key), original - 1)
                self.assertIsNone(hull["mission"])

    def test_no_reloading_swapping_or_normal_mission_while_reserved(self):
        ready(self.user, "awing")
        record = {"builder": 123, "kind": "component"}
        launch_escorts.reserve(
            self.doc, record, "W", launch_escorts.prepare(self.doc, 123, 123, "awing", self.now)
        )
        with self.assertRaises(ValueError):
            fleet.prepare(self.user, "awing", 1, self.now)
        with self.assertRaises(ValueError):
            launch_escorts.prepare(self.doc, 123, 123, "awing", self.now)
        self.assertIn("deployment window resolves", " ".join(fleet.snapshot(self.user, "earth", self.now)))

    def test_finite_responses_screen_only_one_committed_package(self):
        ready(self.user, "hammerhead", 2)
        record = {"builder": 123, "kind": "component"}
        launch_escorts.reserve(
            self.doc, record, "W", launch_escorts.prepare(self.doc, 123, 123, "hammerhead", self.now)
        )
        rng = SimpleNamespace(randint=lambda *_: 100)
        self.assertFalse(launch_escorts.screen(self.doc, record, self.now, rng))
        self.assertEqual(record["escort"]["responses"], 1)
        self.assertFalse(launch_escorts.screen(self.doc, record, self.now, rng))
        self.assertEqual(record["escort"]["responses"], 0)
        self.assertFalse(launch_escorts.screen(self.doc, record, self.now, rng))
        launch_escorts.release(self.doc, record)
        self.assertEqual(fleet.payload(self.user, "hammerhead"), 0)

    def test_stale_window_binding_and_civilisation_reset_never_refund(self):
        hull = ready(self.user, "awing")
        record = {"builder": 123, "kind": "component"}
        launch_escorts.reserve(
            self.doc, record, "W", launch_escorts.prepare(self.doc, 123, 123, "awing", self.now)
        )
        hull["mission"]["window_id"] = "different"
        self.assertFalse(launch_escorts.screen(self.doc, record, self.now))
        launch_escorts.release(self.doc, record)
        self.assertEqual(hull["mission"]["window_id"], "different")
        self.assertEqual(fleet.payload(self.user, "awing"), 2)

    def test_allies_only_and_ground_damaged_loading_hulls_rejected(self):
        other = self.doc["users"]["456"]
        hull = ready(other, "arquitens")
        with self.assertRaises(ValueError):
            launch_escorts.prepare(self.doc, 123, 456, "arquitens", self.now)
        coalitions.state(self.doc)["groups"]["1"] = {
            "members": [123, 456],
            "leader": 123,
            "name": "Test",
            "expires_at": (self.now + dt.timedelta(days=2)).isoformat(),
        }
        self.assertEqual(launch_escorts.prepare(self.doc, 123, 456, "arquitens", self.now)["responses"], 2)
        for key, value in [
            ("deployed", False),
            ("damaged", True),
            ("payload_until", (self.now + dt.timedelta(minutes=30)).isoformat()),
        ]:
            old = hull[key]
            hull[key] = value
            with self.assertRaises(ValueError):
                launch_escorts.prepare(self.doc, 123, 456, "arquitens", self.now)
            hull[key] = old


class ActivityCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bot = _FakeBot(Path(self.temp.name))
        self.cog = object.__new__(EconomyCog)
        self.cog.bot, self.cog.econ = (self.bot, self.bot.economy)
        self.user = self.cog.user(1, 123)
        self.now = dt.datetime.now(dt.timezone.utc)

    async def test_helper_upgrade_settles_old_backlog_then_credits_new_rate(self):
        user = self.user
        e = self.cog._employment(user)
        e.update(active="engineering", career_licence_tier=2, manual_shifts=20)
        self.cog._career_record(e, "engineering")["xp"] = 200
        user.update(
            android21_helper=True,
            helper_certification_tier=1,
            donuts=10**12,
            android21_helper_at=(self.now - dt.timedelta(minutes=20)).isoformat(),
        )
        interaction = _FakeInteraction()
        old = 33000000 * 70 // 100 + 18750000
        with patch("cogs.economy._now", return_value=self.now):
            await self.cog.certification_preview(interaction, helper=True)
            self.assertEqual(user["donuts"], 10**12 + 2 * old)
            view = interaction.response.calls[-1]["view"]
            await view.commit(_FakeInteraction(user_id=456))
            self.assertFalse(view.used)
            click = _FakeInteraction()
            await view.commit(click)
            self.assertEqual(user["helper_certification_tier"], 2)
            self.assertEqual(user["donuts"], 10**12 + 2 * old - 210000000000)
            self.assertTrue(click.response.deferred["ephemeral"])
            balance = user["donuts"]
            await view.commit(_FakeInteraction())
            self.assertEqual(user["donuts"], balance)
        cfg = self.bot.config.for_guild(1)
        gross, _, shifts, _ = self.cog._settle_android21_helper(
            cfg, 1, 123, user, now=self.now + dt.timedelta(minutes=10)
        )
        self.assertEqual((gross, shifts), (33000000 * 70 // 100 + 125000000, 1))
        self.assertEqual(e["manual_shifts"], 20)
        await self.cog.persist(1)
        disk = Storage(Path(self.temp.name) / "economy").load(1)["users"]["123"]
        self.assertEqual(disk["helper_certification_tier"], 2)

    async def test_fish_board_fresh_confirmation_and_two_quotes_pay_once(self):
        cog = object.__new__(Fishing)
        cog.bot, cog.econ = (self.bot, self.bot.economy)
        interaction = _FakeInteraction()
        await cog.activity_board(interaction)
        reply = interaction.response.calls[-1]
        _assert_embed_valid(self, reply["embed"])
        self.assertTrue(reply["ephemeral"])
        token = activities.fish_board(self.user)["id"]
        offer = activities.fish_board(self.user)["offers"][0]
        self.user["fish"].update(offer["fish"])
        quotes = [_FakeInteraction(), _FakeInteraction()]
        for quote in quotes:
            await cog.activity_preview(quote, token, "deliver:0")
        balance = self.user["donuts"]
        for quote in quotes:
            await quote.response.calls[-1]["view"].commit(_FakeInteraction())
        self.assertEqual(self.user["donuts"], balance + offer["reward"])
        stored = Storage(Path(self.temp.name) / "economy").load(1)["users"]["123"]
        self.assertTrue(stored["activity_contracts"]["fish"]["offers"][0]["paid"])

    async def test_space_board_confirmation_accepts_no_money_and_is_private(self):
        cog = SpaceCog(self.bot)
        ready(self.user, "cutlass")
        space.normalize(self.user)["surveys"]["mars"] = self.now.isoformat()
        interaction = _FakeInteraction()
        await cog.activity_board(interaction)
        board = activities.space_board(self.user, cog.doc(1))
        quote = _FakeInteraction()
        await cog.activity_preview(quote, board["id"], "0")
        view = quote.response.calls[-1]["view"]
        balance = self.user["donuts"]
        self.assertIsNone(board.get("active"))
        await view.commit(_FakeInteraction())
        self.assertEqual(self.user["donuts"], balance)
        self.assertFalse(board["active"]["completed"])
        self.assertTrue(quote.response.calls[-1]["ephemeral"])
        self.assertFalse(await view.interaction_check(_FakeInteraction(user_id=456)))
        _assert_embed_valid(self, interaction.response.calls[-1]["embed"])

    async def test_confirm_defers_before_persistence_and_timeout_disables(self):
        click = _FakeInteraction()

        async def effect(interaction):
            self.assertTrue(interaction.response.is_done())
            self.assertTrue(interaction.response.deferred["ephemeral"])

        callback = AsyncMock(side_effect=effect)
        view = activity_ui.Confirm(123, "Buy", callback)
        await view.commit(click)
        await view.commit(_FakeInteraction())
        self.assertEqual(callback.await_count, 1)
        await view.on_timeout()
        self.assertTrue(all((c.disabled for c in view.children)))

    async def test_public_install_preserves_administrator_price_customisation(self):
        storage = Storage(Path(self.temp.name) / "migration")
        legacy = storage.load(1)
        legacy["economy"] = {
            "icbm_build_cost": 100000000,
            "vehicle_b2_cost": 2100000000,
            "thor_rod_cost": 1500000000000,
            "slots_win_pct": 54,
            "vehicle_b2_build_hours": 5,
            "uno_reverse_cap": 5000000,
        }
        cfg = ConfigManager(storage).for_guild(1)
        self.assertEqual(cfg.get("economy.icbm_build_cost"), 100000000)
        self.assertEqual(cfg.get("economy.vehicle_b2_cost"), 2100000000)
        self.assertEqual(cfg.get("economy.thor_rod_cost"), 1500000000000)
        self.assertEqual(cfg.get("economy.slots_win_pct"), 54)
        self.assertEqual(cfg.get("economy.vehicle_b2_build_hours"), 5)
        self.assertEqual(cfg.get("economy.uno_reverse_percent"), 75)
        await storage.flush()
        other = Storage(Path(self.temp.name) / "migration")
        other.load(1)["economy"]["icbm_build_cost"] = 800000000
        self.assertEqual(ConfigManager(other).for_guild(1).get("economy.icbm_build_cost"), 800000000)

    async def test_launch_escort_wires_into_isd_command_and_interceptor(self):
        cog = ImperialStarDestroyerCog(self.bot)
        self.user["donuts"] = 10**20
        part = isd.normalize(self.user)["components"]["shipyard"]
        part["status"] = "ready"
        ready(self.user, "awing")
        with patch.object(cog, "_respond_art", new=AsyncMock()):
            await ImperialStarDestroyerCog.launch.callback(
                cog,
                _FakeInteraction(),
                app_commands.Choice(name="Shipyard", value="shipyard"),
                app_commands.Choice(name="A-wing", value="awing"),
            )
            key = isd.normalize(self.user)["active_window_id"]
            record = isd.windows(cog._doc(1))[key]
            attacker = self.bot.economy.user(1, 456, 0)
            isd.normalize(attacker)["counter_stock"] = 1
            with patch("szofie.launch_escorts.random.randint", return_value=1):
                await ImperialStarDestroyerCog.intercept.callback(cog, _FakeInteraction(user_id=456), key)
        self.assertTrue(record["interceptors"][0]["screened"])
        self.assertFalse(record["interceptors"][0]["success"])
        self.assertEqual(isd.normalize(attacker)["counter_stock"], 0)
        self.assertEqual(record["escort"]["responses"], 1)
        self.assertEqual(fleet.payload(self.user, "awing"), 2)

    async def test_launch_escort_wires_into_deathstar_deployment(self):
        cog = DeathStarCog(self.bot)
        self.user["donuts"] = 10**30
        state = deathstar.normalize(self.user)
        state["transport_owned"] = True
        key = next(iter(deathstar.COMPONENTS))
        state["components"][key]["status"] = "ready"
        ready(self.user, "hammerhead")
        with (
            patch.object(cog, "image_reply", new=AsyncMock()),
            patch.object(cog, "deliver_notices", new=AsyncMock()),
        ):
            await DeathStarCog.deploy.callback(
                cog,
                _FakeInteraction(),
                app_commands.Choice(name=key, value=key),
                app_commands.Choice(name="Hammerhead", value="hammerhead"),
            )
        record = deathstar.windows(cog.doc(1))[state["active_window_id"]]
        self.assertEqual(record["escort"]["responses"], 3)
        self.assertEqual(fleet.payload(self.user, "hammerhead"), 0)
        self.assertEqual(state["components"][key]["status"], "launching")

    async def test_rapid_dragon_focused_requires_recon_and_destroys_selected_depot(self):
        attacker = self.user
        victim = self.cog.user(1, 456)
        attacker["vehicles"]["c130j"] = {"owned": True, "ammo": 1}
        victim["vehicles"]["f15e"] = {"owned": True, "ammo": 10}
        victim["vehicles"]["himars"] = {"owned": True, "ammo": 5}
        target = _FakeUser(456)
        error = self.cog._mission_preflight(1, 123, "c130j", target, "focused:f15e")
        self.assertIn("reconnaissance", error)
        self.assertEqual(attacker["vehicles"]["c130j"]["ammo"], 1)
        attacker["recon_targets"]["456"] = (self.now + dt.timedelta(hours=1)).isoformat()
        self.assertIsNone(self.cog._mission_preflight(1, 123, "c130j", target, "focused:f15e"))
        with (
            patch("cogs.economy.random.randint", return_value=100),
            patch.object(self.cog, "_strategic_art", return_value=None),
        ):
            await self.cog._focused_vehicle_execute(_FakeInteraction(), target, "c130j", "focused:f15e")
        self.assertEqual(victim["vehicles"]["f15e"]["ammo"], 2)
        self.assertEqual(victim["vehicles"]["himars"]["ammo"], 5)
        self.assertEqual(attacker["vehicles"]["c130j"]["ammo"], 0)
        self.assertIsNotNone(attacker["vehicles"]["c130j"]["last_deploy_at"])

    async def test_rapid_dragon_autocomplete_does_not_inspect_target_or_leak_ammo(self):
        interaction = _FakeInteraction()
        interaction.namespace = SimpleNamespace(vehicle="c130j", target=_FakeUser(456))
        choices = await _strategic_objective_autocomplete(interaction, "f15e")
        self.assertEqual([c.value for c in choices], ["focused:f15e"])
        self.assertNotIn("456", str([(c.name, c.value) for c in choices]))
        self.assertNotIn("456", self.bot.economy.store.load(1)["users"])

    async def test_ordinary_guides_and_new_panels_fit_discord(self):
        cfg = self.bot.config.for_guild(1)
        for pages in (
            guides.air_dominance_pages(cfg, color=0),
            guides.isd_pages(cfg, color=0),
            guides.thor_pages(cfg, color=0),
        ):
            for page in pages:
                _assert_embed_valid(self, page)
        call = _FakeInteraction()
        await EconomyCog.job_licences.callback(self.cog, call)
        _assert_embed_valid(self, call.response.calls[-1]["embed"])
        self.assertIn("25%", call.response.calls[-1]["embed"].description)
        self.assertEqual(
            {b.label for b in call.response.calls[-1]["view"].children},
            {"View licences", "Certify", "Certify helper"},
        )
