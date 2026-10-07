"""Exact endgame payments and public manual consistency; no live data touched."""

import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
from cogs.economy import EconomyCog, ATTACKLOG_METHODS, _ledger_label
from cogs.general import CATEGORIES, General
from cogs.imperial_star_destroyer import ImperialStarDestroyerCog
from szofie import guides, ui
from szofie.economy import _default_user
from szofie.ledger import Ledger, SECURITY_EVENT_REASONS
from szofie.imperial_star_destroyer import normalize
from tests.test_air_dominance import Config
from tests.test_system_integration import _FakeBot, _FakeInteraction


class ExactProjectPaymentTests(unittest.TestCase):
    def test_personal_share_and_escrow_are_exact_through_trigintillion(self):
        for cost in (1, 3, 4, 5, 3 * 10**18, 3 * 10**18 + 1, 10**93 + 1):
            with self.subTest(cost=cost):
                personal = (cost + 3) // 4
                owner = _default_user(1)
                owner.update(bank=personal + 9, deep_vault_balance=999)
                state = normalize(owner)
                state.update(project_funds=cost * 2, owner_paid=7)
                cog = object.__new__(ImperialStarDestroyerCog)
                result = cog._pay_project(owner, state, cost)
                self.assertEqual(result, (personal, cost - personal))
                self.assertEqual(owner["donuts"] + owner["bank"], 10)
                self.assertEqual(owner["deep_vault_balance"], 999)
                self.assertEqual(state["owner_paid"], 7 + personal)
                self.assertEqual(state["project_funds"], cost * 2 - (cost - personal))

    def test_one_donut_below_required_share_fails_without_mutation(self):
        for cost in (3 * 10**18 + 1, 10**93 + 1):
            with self.subTest(cost=cost):
                personal = (cost + 3) // 4
                owner = _default_user(personal - 1)
                state = normalize(owner)
                state.update(project_funds=cost * 2)
                before = copy.deepcopy(owner)
                with self.assertRaises(ValueError):
                    object.__new__(ImperialStarDestroyerCog)._pay_project(owner, state, cost)
                self.assertEqual(owner, before)


class ManualConsistencyTests(unittest.TestCase):
    def test_main_help_describes_single_shot_bmd_and_fifth_gen_packages(self):
        text = CATEGORIES["Economy"]["blurb"]
        self.assertNotIn("salvo", text)
        for phrase in (
            "one SM-3 per incoming rod",
            "F-22",
            "F-35",
            "J-20",
            "Su-57",
            "/vehicle load",
            "mission packages",
        ):
            self.assertIn(phrase, text)
        self.assertLessEqual(len(text), 4096)

    def test_loading_tutorial_uses_real_parameter_and_separates_loading_from_launch(self):
        self.assertIn("qty", {p.name for p in EconomyCog.vehicle_load.parameters})
        pages = guides.air_dominance_pages(Config(), color=0)
        text = "\n".join((field.value for page in pages for field in page.fields))
        for phrase in (
            "qty:<units>",
            "starts one loading batch",
            "cannot launch while loading",
            "spends one loaded package",
            "Loading is not an attack",
        ):
            self.assertIn(phrase, text)
        for page in pages:
            self.assertLessEqual(len(page), 6000)
            self.assertTrue(all((len(field.value) <= 1024 for field in page.fields)))

    def test_guides_use_current_ordinary_conversion_time_and_contribution_limit(self):
        cfg = Config(**{"economy.vehicle_b2_arm_hours": 2.5, "economy.isd_contribution_cap": 2 * 10**15})
        recipes = guides.vehicle_pages(cfg, color=0)
        b2 = next((field.value for page in recipes for field in page.fields if "B-2 Spirit" in field.name))
        self.assertIn("2.5h conversion", b2)
        self.assertIn("up to 2 quadrillion donuts each", guides.isd_pages(cfg, color=0)[0].description)

    def test_generated_public_reference_includes_fifth_gen_missions_and_preparation(self):
        text = guides.public_catalog_markdown(Config())
        for phrase in (
            "Loading mission packages",
            "F-22A — Combat Air Patrol",
            "F-35A — Sensor-Fusion Link",
            "J-20 — High-Value Airframe Hunt",
            "Su-57 — Two-Pass Raid",
            "qty:<units>",
            "1h",
            "/vehicle arm",
        ):
            self.assertIn(phrase, text)


class PublicCommandTextTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_jet_hits_reach_victim_log_and_survive_retention_cleanup(self):
        with tempfile.TemporaryDirectory() as raw:
            ledger = Ledger(Path(raw) / "ledger")
            old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
            for model in ("j20", "su57"):
                await ledger.record(
                    1,
                    123,
                    0,
                    f"vehicle-{model}-hit",
                    other=456,
                    actor=456,
                    occurred_at=old,
                    detail="vehicle:apache",
                )
                await ledger.record(1, 456, 0, f"vehicle-{model}-launch", other=123, occurred_at=old)
            await ledger.prune(1, 7)
            entries = await ledger.read_security(1, victim=123)
            self.assertEqual({row["reason"] for row in entries}, {"vehicle-j20-hit", "vehicle-su57-hit"})
            self.assertTrue(all((row["security_actor"] == 456 for row in entries)))
            self.assertEqual(await ledger.read_security(1, victim=456), [])
            self.assertEqual(set(ATTACKLOG_METHODS), set(SECURITY_EVENT_REASONS))
            bot = _FakeBot(Path(raw) / "bot")
            bot.ledger = ledger
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            interaction = _FakeInteraction()
            await EconomyCog.attacklog.callback(cog, interaction, 20)
            reply = interaction.original_edits[-1]
            text = reply["embed"].description
            for phrase in ("J-20 Mighty Dragon", "Su-57 Felon", "ID `456`", "vehicle:apache"):
                self.assertIn(phrase, text)
            self.assertEqual(reply["allowed_mentions"].to_dict()["parse"], [])

    async def test_jet_ledger_event_names_have_readable_labels(self):
        for model in ("f22", "f35", "j20", "su57"):
            for suffix in ("launch", "repair"):
                reason = f"vehicle-{model}-{suffix}"
                self.assertNotEqual(_ledger_label(reason), reason)

    async def test_isd_charge_report_matches_configured_payment(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cfg = bot.config.for_guild(1)
            cost = 7 * 10**18
            cfg.set("economy.isd_cinder_cost", cost)
            user = bot.economy.user(1, 123, 0)
            user["donuts"] = 10**20
            normalize(user).update(operational=True)
            cog = ImperialStarDestroyerCog(bot)
            cog._respond_art = AsyncMock()
            await ImperialStarDestroyerCog.arm.callback(cog, _FakeInteraction())
            embed = cog._respond_art.call_args.args[2]
            self.assertIn(ui.format_donuts(cost), embed.description)
            self.assertNotIn("3 quintillion", embed.description)
            self.assertEqual(user["donuts"], 10**20 - cost)

    async def test_complete_main_help_retains_new_roles_in_public_pager(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = General(bot)
            interaction = _FakeInteraction()
            await General.help_cmd.callback(cog, interaction)
            reply = interaction.response.calls[-1]
            view = reply.get("view")
            pages = view.pages if view else [reply["embed"]]
            try:
                text = "\n".join((field.value for page in pages for field in page.fields))
                self.assertIn("Fifth-generation jets", text)
                self.assertIn("one SM-3 per incoming rod", text)
                self.assertFalse(reply["ephemeral"])
                for page in pages:
                    self.assertLessEqual(len(page), 6000)
                    self.assertTrue(all((len(f.value) <= 1024 for f in page.fields)))
            finally:
                if view:
                    view.stop()
