"""NYX completes activation replies without awaiting historical Discord edits."""

import asyncio
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
import discord
from cogs.satellite import SatelliteCog
from szofie import nyx, ui
from szofie.nyx_reports import PublicReports, record_response
from tests import test_nyx_public_report_cleanup as cleanup_tests
from tests.test_system_integration import _FakeBot, _FakeInteraction


class NyxResponseReliabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bot = _FakeBot(Path(self.temp.name))
        self.bot.user = NS(id=999)
        self.messages = {}
        self.channel = NS(
            id=2, guild=NS(id=1), fetch_message=AsyncMock(side_effect=lambda mid: self.messages[mid])
        )
        self.bot.get_channel = lambda _id: self.channel
        self.registry = PublicReports(self.bot, Path(self.temp.name) / "reports.json")
        self.bot.nyx_reports = self.registry
        self.bot.ledger.record = AsyncMock()
        self.user = self.bot.economy.user(1, 123, 100)
        nyx.normalize(self.user).update(status="orbit", last_activation_at=None)
        self.cog = SatelliteCog(self.bot)

    async def message(self, mid=10, view=None):
        message = cleanup_tests.Message(mid=mid, description="<@123> private build deadline")
        self.messages[mid] = message
        await self.registry.register(message, [123], view)
        return message

    async def test_activation_queues_revokes_and_finishes_reply_without_history_http(self):
        view = ui.Paginator([ui.base_embed(description="SECRET")], 123)
        self.addCleanup(view.stop)
        message = await self.message(view=view)
        interaction = _FakeInteraction()
        with patch.object(self.cog, "_art", return_value=None):
            await asyncio.wait_for(self.cog.activate(interaction), timeout=1)
        self.assertTrue(nyx.active(self.user))
        self.assertTrue(view._nyx_revoked)
        self.assertTrue(view.is_finished())
        message.edit.assert_not_awaited()
        self.assertTrue(self.registry.data["reports"]["10"]["pending"])
        self.assertEqual(interaction.response.calls[-1]["embed"].title, "NYX — GHOST PROTOCOL ACTIVE")
        self.assertTrue(interaction.response.calls[-1]["ephemeral"])
        self.assertIn("background", interaction.response.calls[-1]["embed"].description)
        self.bot.ledger.record.assert_awaited_once()
        await self.registry.redact_active()
        self.assertEqual(message.embeds[0].title, "INTELLIGENCE CENSORED")

    async def test_duplicate_activation_does_not_restart_field_or_apply_twice(self):
        with patch.object(self.cog, "_art", return_value=None):
            await self.cog.activate(_FakeInteraction())
            until = self.user["nyx"]["active_until"]
            await self.cog.activate(_FakeInteraction())
        self.assertEqual(until, self.user["nyx"]["active_until"])
        self.bot.ledger.record.assert_awaited_once()

    async def test_text_is_completed_before_optional_art_and_upload_failure_is_nonfatal(self):
        interaction = _FakeInteraction()
        art = Mock()

        async def failed_art(**kwargs):
            self.assertEqual(interaction.response.calls[-1]["embed"].title, "NYX — GHOST PROTOCOL ACTIVE")
            self.assertNotIn("szofie_deferred_private", interaction.extras)
            raise asyncio.TimeoutError()

        message = NS(edit=AsyncMock(side_effect=failed_art))
        with (
            patch.object(self.cog, "_art", return_value=art),
            patch("szofie.ui.response_message", AsyncMock(return_value=message)),
            self.assertLogs("cogs.satellite", level="WARNING"),
        ):
            await self.cog.activate(interaction)
        art.close.assert_called_once()
        self.assertTrue(nyx.active(self.user))
        self.bot.ledger.record.assert_awaited_once()

    async def test_transient_confirmation_retry_does_not_reactivate_or_double_log(self):
        interaction = _FakeInteraction()
        original = interaction.edit_original_response
        calls = 0

        async def edit(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise discord.HTTPException(NS(status=503, reason="Unavailable"), "temporary")
            return await original(**kwargs)

        interaction.edit_original_response = AsyncMock(side_effect=edit)
        with (
            patch.object(self.cog, "_art", return_value=None),
            self.assertLogs("cogs.satellite", level="WARNING"),
        ):
            await self.cog.activate(interaction)
        self.assertEqual(calls, 2)
        self.bot.ledger.record.assert_awaited_once()

    async def test_queue_failure_does_not_hide_successful_activation_confirmation(self):
        self.registry.queue_active = AsyncMock(side_effect=OSError("disk busy"))
        interaction = _FakeInteraction()
        with (
            patch.object(self.cog, "_art", return_value=None),
            self.assertLogs("cogs.satellite", level="ERROR"),
        ):
            await self.cog.activate(interaction)
        self.assertTrue(nyx.active(self.user))
        self.assertEqual(interaction.response.calls[-1]["embed"].title, "NYX — GHOST PROTOCOL ACTIVE")

    async def test_webhook_report_without_cached_guild_uses_interaction_context(self):
        message = cleanup_tests.Message(description="<@123> stockpile")
        message.guild = None
        message.channel = None
        interaction = _FakeInteraction()
        interaction.client = self.bot
        interaction.channel_id = 2
        interaction.command = NS(qualified_name="arsenal")
        with patch("szofie.nyx_reports.discord.Message", cleanup_tests.Message):
            await record_response(interaction, message, False)
        record = self.registry.data["reports"]["10"]
        self.assertEqual((record["guild"], record["channel"]), (1, 2))

    async def test_followup_missing_context_never_turns_delivered_action_into_error(self):
        message = cleanup_tests.Message()
        message.guild = message.channel = None
        interaction = _FakeInteraction(guild_id=None)
        interaction.client = self.bot
        interaction.guild_id = 1
        interaction.channel_id = None
        interaction.command = NS(qualified_name="arsenal")
        with (
            patch("szofie.nyx_reports.discord.Message", cleanup_tests.Message),
            self.assertLogs("szofie.nyx_reports", level="ERROR"),
        ):
            await record_response(interaction, message, False)
        self.assertEqual(self.registry.data["reports"], {})

    async def test_same_message_concurrent_cleanup_edits_only_once(self):
        message = await self.message()
        nyx.normalize(self.user)["active_until"] = (
            dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=3)
        ).isoformat()
        await self.registry.queue_active()
        record = self.registry.data["reports"]["10"]
        await asyncio.gather(self.registry.redact_one("10", record), self.registry.redact_one("10", record))
        message.edit.assert_awaited_once()

    async def test_slow_index_redaction_is_bounded_after_delivery_and_keeps_retry_record(self):
        message = await self.message()
        nyx.normalize(self.user)["active_until"] = (
            dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=3)
        ).isoformat()

        async def slow_edit(**kwargs):
            await asyncio.sleep(100)

        message.edit = AsyncMock(side_effect=slow_edit)
        interaction = _FakeInteraction()
        interaction.client = self.bot
        interaction.command = NS(qualified_name="arsenal")
        wait_for = asyncio.wait_for

        async def short_wait(awaitable, timeout):
            return await wait_for(awaitable, 0.05)

        with (
            patch("szofie.nyx_reports.discord.Message", cleanup_tests.Message),
            patch("szofie.nyx_reports.asyncio.wait_for", side_effect=short_wait),
            self.assertLogs("szofie.nyx_reports", level="ERROR"),
        ):
            await record_response(interaction, message, False)
        self.assertTrue(self.registry.data["reports"]["10"]["pending"])
        self.assertFalse(self.registry.data["reports"]["10"]["redacted"])
        message.edit = AsyncMock(side_effect=message.changed)
        await self.registry.redact_active()
        self.assertEqual(message.embeds[0].title, "INTELLIGENCE CENSORED")

    async def test_slow_old_report_does_not_block_new_report_in_another_channel(self):
        first = await self.message()
        second = await self.message(mid=11)
        entered, release = (asyncio.Event(), asyncio.Event())

        async def slow(**kwargs):
            entered.set()
            await release.wait()
            return await first.changed(**kwargs)

        first.edit = AsyncMock(side_effect=slow)
        one = asyncio.create_task(self.registry.redact_one("10", self.registry.data["reports"]["10"]))
        await entered.wait()
        try:
            await asyncio.wait_for(
                self.registry.redact_one("11", self.registry.data["reports"]["11"]), timeout=1
            )
            second.edit.assert_awaited_once()
        finally:
            release.set()
            await one


if __name__ == "__main__":
    unittest.main()
