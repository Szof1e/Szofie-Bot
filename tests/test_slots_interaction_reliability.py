"""Slots acknowledge early and delivery retries never settle another wager."""

import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import discord
from bot import Szofie
from cogs.economy import CasinoReplayView, EconomyCog
from szofie import betting, ui
from tests.test_system_integration import _FakeBot, _FakeInteraction, _FakeMessage, _FakeUser


class SlotsReliabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bot = _FakeBot(Path(self.temp.name))
        self.cog = object.__new__(EconomyCog)
        self.cog.bot, self.cog.econ = (self.bot, self.bot.economy)
        self.cog._slot_cd, self.cog._wheel_cd = ({}, {})
        self.cog._award = AsyncMock()
        self.bot.ledger.record = AsyncMock()
        self.user = self.cog.user(1, 123)
        self.user["donuts"] = 1000

    async def test_slash_amount_is_acknowledged_before_balance_loading(self):
        interaction = _FakeInteraction()
        interaction.client = self.bot

        def balances(request):
            self.assertTrue(request.response.is_done(), "Lookup must follow acknowledgement")
            return (1000, 0)

        with patch.object(betting, "_balances", side_effect=balances):
            self.assertEqual(await betting.AcknowledgedBetTransformer().transform(interaction, "half"), 500)
        self.assertEqual(interaction.response.deferred, {"thinking": True, "ephemeral": False})
        await ui.defer_response(interaction)

    async def test_failed_transformer_ack_never_reads_balances(self):
        interaction = _FakeInteraction()
        interaction.response.defer = AsyncMock(side_effect=RuntimeError("ack failed"))
        with patch.object(betting, "_balances") as balances:
            with self.assertRaisesRegex(RuntimeError, "ack failed"):
                await betting.AcknowledgedBetTransformer().transform(interaction, "all")
        balances.assert_not_called()

    async def test_failed_direct_ack_never_changes_wallet_cooldown_or_stats(self):
        before = copy.deepcopy(self.user)
        interaction = _FakeInteraction()
        interaction.response.defer = AsyncMock(side_effect=RuntimeError("ack failed"))
        with self.assertRaisesRegex(RuntimeError, "ack failed"):
            await EconomyCog.slots.callback(self.cog, interaction, 100)
        self.assertEqual(self.user, before)
        self.assertEqual(self.cog._slot_cd, {})
        self.bot.ledger.record.assert_not_awaited()

    async def test_each_replay_acknowledges_before_lookup_and_dispatch(self):
        for label in ("same", "quarter", "half", "bet_all"):
            with self.subTest(button=label):
                interaction = _FakeInteraction()

                def user(*args):
                    self.assertTrue(interaction.response.is_done())
                    return {"donuts": 1000}

                async def run(*args):
                    self.assertTrue(interaction.response.is_done())

                callback = AsyncMock(side_effect=run)
                cog = SimpleNamespace(user=user, slots=SimpleNamespace(callback=callback))
                view = CasinoReplayView(cog, 123, "slots", 100)
                await getattr(view, label).callback(interaction)
                callback.assert_awaited_once()
                view.stop()

    async def test_slow_storage_and_ledger_only_run_after_acknowledgement(self):
        interaction = _FakeInteraction()
        original = self.cog.persist

        async def persist(gid):
            self.assertTrue(interaction.response.is_done())
            await original(gid)

        self.cog.persist = persist
        with patch("cogs.economy.biased_spin", return_value=(["🍬", "⭐", "🍫"], 0)):
            await EconomyCog.slots.callback(self.cog, interaction, 100)
        view = interaction.response.calls[-1]["view"]
        self.assertIsNotNone(view.message)
        self.assertEqual(self.user["donuts"], 900)
        self.assertIn("Buttons last 10 min", interaction.response.calls[-1]["embed"].footer.text)
        view.stop()

    async def test_transient_delivery_failure_retries_same_saved_spin_once(self):
        interaction = _FakeInteraction()
        delivered = _FakeMessage()
        error = discord.HTTPException(SimpleNamespace(status=503, reason="Unavailable"), "temporary")
        interaction.edit_original_response = AsyncMock(side_effect=[error, delivered])
        with patch("cogs.economy.biased_spin", return_value=(["🍬", "⭐", "🍫"], 0)) as spin:
            with self.assertLogs("cogs.economy", level="WARNING"):
                await EconomyCog.slots.callback(self.cog, interaction, 100)
        spin.assert_called_once()
        self.bot.ledger.record.assert_awaited_once()
        self.assertEqual(self.user["donuts"], 900)
        self.assertEqual(interaction.edit_original_response.await_count, 2)
        first, second = interaction.edit_original_response.await_args_list
        self.assertIs(first.kwargs["embed"], second.kwargs["embed"])
        self.assertIs(first.kwargs["view"], second.kwargs["view"])
        view = second.kwargs["view"]
        self.assertIs(view.message, delivered)
        view.stop()

    async def test_permanent_delivery_error_does_not_retry_or_recharge(self):
        interaction = _FakeInteraction()
        error = discord.HTTPException(SimpleNamespace(status=400, reason="Bad request"), "invalid")
        interaction.edit_original_response = AsyncMock(side_effect=error)
        with patch("cogs.economy.biased_spin", return_value=(["🍬", "⭐", "🍫"], 0)) as spin:
            with self.assertRaises(discord.HTTPException):
                await EconomyCog.slots.callback(self.cog, interaction, 100)
        spin.assert_called_once()
        self.assertEqual(self.user["donuts"], 900)
        interaction.edit_original_response.assert_awaited_once()

    async def test_expired_replay_buttons_are_visibly_disabled_without_a_bet(self):
        view = CasinoReplayView(self.cog, 123, "slots", 100)
        view.message = _FakeMessage()
        await view.on_timeout()
        self.assertTrue(all((child.disabled for child in view.children)))
        self.assertIs(view.message.edits[-1]["view"], view)
        self.assertEqual(self.user["donuts"], 1000)
        self.bot.ledger.record.assert_not_awaited()
        view.stop()

    async def test_missing_message_on_expiry_does_not_raise(self):
        view = CasinoReplayView(self.cog, 123, "slots", 100)
        await view.on_timeout()
        self.assertTrue(all((child.disabled for child in view.children)))
        view.stop()

    async def test_unknown_interaction_is_logged_without_attempting_another_reply(self):
        interaction = _FakeInteraction()
        interaction.command = SimpleNamespace(name="slots", qualified_name="slots")
        original = discord.NotFound(
            SimpleNamespace(status=404, reason="Not found"), {"code": 10062, "message": "Unknown interaction"}
        )
        error = discord.app_commands.CommandInvokeError(interaction.command, original)
        with self.assertLogs("szofie", level="WARNING") as records:
            await Szofie.on_tree_error(SimpleNamespace(), interaction, error)
        self.assertIn("expired", records.output[0])
        self.assertFalse(interaction.response.calls)

    def test_slash_parameter_remains_string_bet(self):
        option = EconomyCog.slots.parameters[0]
        self.assertEqual(option.name, "bet")
        self.assertEqual(option.type, discord.AppCommandOptionType.string)
