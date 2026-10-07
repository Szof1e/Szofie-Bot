"""Interaction handoff, reset and privacy regressions using temporary data only."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import discord
from discord import app_commands
from bot import Szofie
from cogs.economy import EconomyCog, WorkShiftView
from cogs.imperial_star_destroyer import ImperialStarDestroyerCog
from cogs.loans import Loans, LoanOfferView
from cogs.seasons import Seasons
from cogs.settings import Settings, key_autocomplete
from cogs.trivia import Trivia, TriviaView
from szofie import ui
from szofie.storage import Storage
from tests.test_system_integration import _FakeBot, _FakeInteraction, _FakeMessage, _FakeUser


class ResponseHandoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixture_rejects_double_acknowledgement(self):
        for first in ("defer", "send_message"):
            for second in ("defer", "send_message"):
                with self.subTest(first=first, second=second):
                    interaction = _FakeInteraction()
                    await getattr(interaction.response, first)()
                    with self.assertRaises(RuntimeError):
                        await getattr(interaction.response, second)()

    async def test_visibility_transition_returns_actual_followup_not_deleted_original(self):
        interaction = _FakeInteraction()
        actual = _FakeMessage()
        interaction.followup.send = AsyncMock(return_value=actual)
        interaction.original_response = AsyncMock(side_effect=AssertionError("original was deleted"))
        await ui.defer_response(interaction)
        await ui.respond(interaction, embed=ui.ok_embed("Private confirmation"), ephemeral=True)
        self.assertTrue(interaction.original_deleted)
        self.assertIs(await ui.response_message(interaction), actual)
        interaction.followup.send.assert_awaited_once()
        self.assertTrue(interaction.followup.send.call_args.kwargs["ephemeral"])
        self.assertTrue(interaction.followup.send.call_args.kwargs["wait"])

    async def test_failed_deferred_delivery_can_retry_original(self):
        interaction = _FakeInteraction()
        actual = _FakeMessage()
        failure = discord.HTTPException(SimpleNamespace(status=500, reason="Server error"), "temporary")
        interaction.edit_original_response = AsyncMock(side_effect=[failure, actual])
        await ui.defer_response(interaction, ephemeral=True)
        with self.assertRaises(discord.HTTPException):
            await ui.respond(interaction, content="Result", ephemeral=True)
        self.assertIn("szofie_deferred_private", interaction.extras)
        await ui.respond(interaction, content="Result", ephemeral=True)
        self.assertIs(await ui.response_message(interaction), actual)
        self.assertFalse(interaction.followup.calls)

    async def test_vehicle_confirmation_defers_privately_and_retains_message(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            interaction = _FakeInteraction()
            message = _FakeMessage()
            interaction.edit_original_response = AsyncMock(return_value=message)
            interaction.original_response = AsyncMock(side_effect=AssertionError("unnecessary fetch"))
            with patch.object(cog, "_mission_preflight", return_value=None):
                await EconomyCog.vehicle_deploy.callback(
                    cog, interaction, app_commands.Choice(name="U-2", value="u2"), _FakeUser(456)
                )
            self.assertTrue(interaction.response.deferred["ephemeral"])
            view = interaction.edit_original_response.call_args.kwargs["view"]
            self.assertIs(view.message, message)
            self.assertFalse(interaction.followup.calls)
            view.stop()


class AutosaveTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_stores_flush_and_failures_do_not_block_other_store(self):
        for failing in (None, "config", "economy"):
            with self.subTest(failing=failing):
                bot = SimpleNamespace(
                    config=SimpleNamespace(flush=AsyncMock()), economy=SimpleNamespace(flush=AsyncMock())
                )
                if failing:
                    getattr(bot, failing).flush.side_effect = OSError("test write failure")
                with (
                    patch("bot.asyncio.sleep", new=AsyncMock(side_effect=[None, asyncio.CancelledError()])),
                    patch("bot.log"),
                ):
                    await Szofie._autosave_loop(bot)
                bot.config.flush.assert_awaited_once()
                bot.economy.flush.assert_awaited_once()


class ResetViewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bot = _FakeBot(Path(self.temp.name))
        self.cog = object.__new__(EconomyCog)
        self.cog.bot, self.cog.econ = (self.bot, self.bot.economy)

    def wipe(self):
        ImperialStarDestroyerCog(self.bot)._apply_cinder_wipe(self.bot.economy.store.load(1), {"guild_id": 1})

    async def test_current_work_shift_still_completes(self):
        view = WorkShiftView(self.cog, 1, 123, "retail", {"correct": 0, "choices": ["yes"]})
        self.cog._complete_work_shift = AsyncMock(return_value=ui.ok_embed("Paid"))
        await view._answer(_FakeInteraction(), 0)
        self.cog._complete_work_shift.assert_awaited_once()

    async def test_current_trivia_question_still_pays(self):
        cog = Trivia(self.bot)
        user = cog.user(1, 123)
        before = user["donuts"]
        view = TriviaView(cog, 123, ["A", "B"], 0, "A", 100, 25, "Test", 1)
        await view.on_answer(_FakeInteraction(), 0)
        self.assertGreater(user["donuts"], before)

    async def test_current_loan_offer_still_transfers_and_records_debt(self):
        cog = Loans(self.bot)
        lender, borrower = (_FakeUser(123), _FakeUser(456))
        lender.guild = borrower.guild = SimpleNamespace(id=1)
        cog.user(1, 123)["donuts"] = 1000
        cog.user(1, 456)["donuts"] = 0
        view = LoanOfferView(cog, lender, borrower, 100, 10, None)
        await cog.finalize_loan(_FakeInteraction(user_id=456), view)
        self.assertEqual(cog.user(1, 123)["donuts"], 900)
        self.assertEqual(cog.user(1, 456)["donuts"], 100)
        self.assertEqual(cog._loans(1)[0]["owed"], 110)
        view.stop()

    async def test_work_shift_cannot_pay_after_full_reset(self):
        view = WorkShiftView(self.cog, 1, 123, "retail", {"correct": 0, "choices": ["yes", "no"]})
        self.cog._complete_work_shift = AsyncMock()
        self.wipe()
        await view._answer(_FakeInteraction(), 0)
        self.cog._complete_work_shift.assert_not_awaited()
        self.assertEqual(self.cog.user(1, 123)["donuts"], 0)

    async def test_old_shift_timeout_cannot_clear_new_shift(self):
        view = WorkShiftView(self.cog, 1, 123, "retail", {"correct": 0, "choices": ["yes"]})
        view.message = _FakeMessage()
        self.cog._expire_work_shift = AsyncMock()
        self.wipe()
        await view.on_timeout()
        self.cog._expire_work_shift.assert_not_awaited()
        view.stop()

    async def test_trivia_answer_cannot_pay_after_full_reset(self):
        cog = Trivia(self.bot)
        view = TriviaView(cog, 123, ["A", "B"], 0, "A", 100, 25, "Test", 1)
        cog.settle_answer = AsyncMock()
        self.wipe()
        await view.on_answer(_FakeInteraction(), 0)
        cog.settle_answer.assert_not_awaited()

    async def test_old_trivia_timeout_does_not_reset_new_streak(self):
        cog = Trivia(self.bot)
        view = TriviaView(cog, 123, ["A", "B"], 0, "A", 100, 25, "Test", 1)
        view.message = _FakeMessage()
        cog.settle_answer = AsyncMock()
        self.wipe()
        await view.on_timeout()
        cog.settle_answer.assert_not_awaited()
        self.assertTrue(view.answered)
        view.stop()

    async def test_loan_offer_invalidated_even_if_new_account_can_afford_it(self):
        cog = Loans(self.bot)
        lender, borrower = (_FakeUser(123), _FakeUser(456))
        lender.guild = borrower.guild = SimpleNamespace(id=1)
        self.cog.user(1, 123)["donuts"] = 1000
        view = LoanOfferView(cog, lender, borrower, 100, 0, None)
        self.wipe()
        self.cog.user(1, 123)["donuts"] = 1000
        await cog.finalize_loan(_FakeInteraction(user_id=456), view)
        self.assertEqual(cog.user(1, 123)["donuts"], 1000)
        self.assertEqual(cog.user(1, 456)["donuts"], 0)
        self.assertFalse(cog._loans(1))
        view.stop()

    async def test_loan_acceptance_rechecks_active_loan_limit(self):
        cog = Loans(self.bot)
        lender, borrower = (_FakeUser(123), _FakeUser(456))
        lender.guild = borrower.guild = SimpleNamespace(id=1)
        cog.user(1, 123)["donuts"] = 1000
        view = LoanOfferView(cog, lender, borrower, 100, 0, None)
        cog._loans(1).extend([{"borrower": 456, "owed": 100}] * 3)
        await cog.finalize_loan(_FakeInteraction(user_id=456), view)
        self.assertEqual(cog.user(1, 123)["donuts"], 1000)
        self.assertEqual(len(cog._loans(1)), 3)
        view.stop()

    async def test_wheel_animation_cannot_restore_pot_after_reset(self):
        cog = self.cog
        cog._wheel_cd = {}
        self.bot.config.for_guild(1).set("economy.wheel_min_bet", 1)
        cog.user(1, 123)["donuts"] = 1000
        interaction = _FakeInteraction()

        async def reset_during_animation(_seconds):
            self.wipe()

        with (
            patch("cogs.economy.asyncio.sleep", side_effect=reset_during_animation),
            patch("cogs.economy.time.monotonic", return_value=0.25),
            patch("cogs.economy.gamelocks.is_locked", return_value=False),
            patch("cogs.economy.spin_wheel") as spin,
        ):
            await EconomyCog.wheel.callback(cog, interaction, 100)
        spin.assert_not_called()
        self.assertEqual(cog.user(1, 123)["donuts"], 0)
        self.assertEqual(cog._wheel_pot(1), 0)
        self.assertNotIn(123, cog._wheel_cd)


class ConfigPrivacyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bot = _FakeBot(Path(self.temp.name))
        self.bot.log_action = AsyncMock()
        self.cog = Settings(self.bot)

    async def test_private_keys_not_autocompleted_or_shown_in_view(self):
        interaction = _FakeInteraction()
        interaction.client = self.bot
        self.bot.owner_ids_set = {interaction.user.id}
        self.assertFalse(await key_autocomplete(interaction, "economy.owner_"))
        await Settings.view.callback(
            self.cog, interaction, app_commands.Choice(name="economy", value="economy")
        )
        pages = interaction.response.calls[-1]["view"].pages
        self.assertFalse(any(("owner_" in field.name for page in pages for field in page.fields)))
        interaction.response.calls[-1]["view"].stop()

    async def test_export_omits_private_policy(self):
        interaction = _FakeInteraction()
        await Settings.export.callback(self.cog, interaction)
        file = interaction.response.calls[-1]["file"]
        try:
            file.fp.seek(0)
            payload = json.loads(file.fp.read())
            self.assertFalse(any((k.startswith("owner_") for k in payload["economy"])))
        finally:
            file.close()


class SmallerSubsystemTests(unittest.IsolatedAsyncioTestCase):
    async def test_season_dm_denied_without_creating_guild_document(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            with patch("discord.ext.tasks.Loop.start"):
                cog = Seasons(bot)
            interaction = _FakeInteraction(guild_id=None)
            await Seasons.season.callback(cog, interaction)
            self.assertFalse(bot.economy.store._cache)
            self.assertTrue(interaction.response.calls[-1]["ephemeral"])

    async def test_season_standings_never_overflow_field_limit(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            bot.config.for_guild(1).set("economy.currency_name", "donuts" * 20)
            with patch("discord.ext.tasks.Loop.start"):
                cog = Seasons(bot)
            with patch.object(
                cog, "_public_ranked", return_value=[(str(123 + i), 10**50) for i in range(10)]
            ):
                interaction = _FakeInteraction()
                await Seasons.season.callback(cog, interaction)
            call = interaction.response.calls[-1]
            pages = call["view"].pages if call.get("view") else [call["embed"]]
            text = "".join((field.value for page in pages for field in page.fields))
            for i in range(10):
                self.assertIn(f"<@{123 + i}>", text)
            for page in pages:
                self.assertLessEqual(len(page), 6000)
                self.assertTrue(all((len(field.value) <= 1024 for field in page.fields)))
            if call.get("view"):
                call["view"].stop()

    async def test_non_object_json_is_preserved_and_recovers_to_defaults(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "1.json"
            path.write_text("[1, 2]")
            storage = Storage(Path(raw))
            with self.assertLogs("szofie.storage", level="ERROR"):
                self.assertEqual(storage.load(1), {})
            self.assertEqual(json.loads(path.with_suffix(".json.corrupt").read_text()), [1, 2])
