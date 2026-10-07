"""Public report revocation survives restarts, expired tokens and late sends."""

import asyncio
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import discord
from cogs.economy import EconomyCog
from szofie import nyx, ui
from szofie.nyx_reports import PublicReports, legacy_report
from szofie.status_ui import StatusPager
from tests.test_system_integration import _FakeBot, _FakeInteraction


class Message:
    def __init__(
        self,
        mid=10,
        *,
        command="isd status",
        author=999,
        actor=123,
        content="",
        title="IMPERIAL STAR DESTROYER STATUS",
        description="<@456> SECRET reactor deadline",
    ):
        self.id = mid
        self.author = NS(id=author)
        self.guild = NS(id=1)
        self.channel = NS(id=2)
        self.flags = NS(ephemeral=False)
        self.content = content
        self.embeds = [ui.base_embed(title=title, description=description)]
        self.attachments = [NS(to_dict=lambda: {"filename": "secret.png", "url": "secret-url"})]
        self.interaction = NS(name=command, user=NS(id=actor))
        self.interaction_metadata = NS(user=NS(id=actor))
        self.edit = AsyncMock(side_effect=self.changed)

    async def changed(self, **kwargs):
        self.content = kwargs.get("content") or ""
        self.embeds = kwargs.get("embeds", self.embeds)
        self.attachments = kwargs.get("attachments", self.attachments)
        return self


class PublicReportCleanupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "nyx_reports.json"
        self.bot = _FakeBot(Path(self.temp.name))
        self.bot.user = NS(id=999)
        self.messages = {}
        self.channel = NS(
            id=2, guild=NS(id=1), fetch_message=AsyncMock(side_effect=lambda mid: self.messages[mid])
        )
        self.bot.get_channel = lambda cid: self.channel if cid == 2 else None
        self.bot.fetch_channel = AsyncMock(return_value=self.channel)
        self.reports = PublicReports(self.bot, self.path)
        self.now = dt.datetime.now(dt.timezone.utc)
        for uid in (123, 456, 789):
            self.bot.economy.user(1, uid, 100)

    def cloak(self, uid=456):
        user = self.bot.economy.user(1, uid, 100)
        nyx.normalize(user).update(
            status="orbit",
            last_activation_at=self.now.isoformat(),
            active_until=(self.now + dt.timedelta(hours=3)).isoformat(),
        )

    async def register(self, message=None, subjects=(456,), view=None):
        message = message or Message()
        self.messages[message.id] = message
        await self.reports.register(message, subjects, view)
        return message

    async def test_old_public_message_edit_removes_every_payload_and_button(self):
        view = ui.Paginator([ui.base_embed(description="SECRET"), ui.base_embed(description="OTHER")], 123)
        message = await self.register(view=view)
        self.cloak()
        self.assertEqual(await self.reports.redact_active(), (1, 0))
        kwargs = message.edit.call_args.kwargs
        self.assertIsNone(kwargs["content"])
        self.assertIsNone(kwargs["view"])
        self.assertEqual(len(kwargs["embeds"]), 1)
        self.assertNotIn("SECRET", json.dumps(kwargs["embeds"][0].to_dict()))
        self.assertTrue(view.is_finished())
        self.assertTrue(view._nyx_revoked)
        self.assertNotIn("SECRET", json.dumps([page.to_dict() for page in view.pages]))

    async def test_restart_uses_bot_message_edit_not_expired_interaction_token(self):
        message = await self.register()
        self.cloak()
        restarted = PublicReports(self.bot, self.path)
        self.assertEqual(await restarted.redact_active(), (1, 0))
        self.channel.fetch_message.assert_awaited_once_with(message.id)
        self.assertTrue(restarted.data["reports"]["10"]["redacted"])

    async def test_redacted_messages_never_restore_after_expiry(self):
        message = await self.register()
        self.cloak()
        await self.reports.redact_active()
        nyx.normalize(self.bot.economy.user(1, 456, 0))["active_until"] = self.now.isoformat()
        self.assertEqual(await self.reports.redact_active(), (0, 0))
        self.assertEqual(message.embeds[0].title, "INTELLIGENCE CENSORED")
        message.edit.assert_awaited_once()

    async def test_failed_edits_retry_even_after_field_expires(self):
        message = await self.register()
        response = NS(status=403, reason="Forbidden")
        message.edit.side_effect = discord.Forbidden(response, "Cannot edit")
        self.cloak()
        self.assertEqual(await self.reports.redact_active(), (0, 1))
        self.assertTrue(self.reports.data["reports"]["10"]["pending"])
        nyx.normalize(self.bot.economy.user(1, 456, 0))["active_until"] = self.now.isoformat()
        message.edit.side_effect = message.changed
        self.assertEqual(await self.reports.redact_active(), (1, 0))

    async def test_deleted_report_is_completed_without_retry_or_mutating_users(self):
        message = await self.register()
        message.edit.side_effect = discord.NotFound(NS(status=404, reason="Not Found"), "Deleted")
        self.cloak()
        before = json.dumps(self.reports.users(1), sort_keys=True)
        self.assertEqual(await self.reports.redact_active(), (1, 0))
        self.assertEqual(json.dumps(self.reports.users(1), sort_keys=True), before)

    async def test_private_and_foreign_messages_are_not_indexed_or_edited(self):
        for message in (Message(author=111), Message(mid=11)):
            if message.id == 11:
                message.flags.ephemeral = True
            await self.reports.register(message, {456})
            message.edit.assert_not_awaited()
        self.assertEqual(self.reports.data["reports"], {})

    async def test_late_public_delivery_during_cloak_is_censored_immediately(self):
        self.cloak()
        message = await self.register()
        message.edit.assert_awaited_once()
        self.assertEqual(message.embeds[0].title, "INTELLIGENCE CENSORED")

    async def test_unrelated_reports_stay_unchanged(self):
        message = await self.register(subjects={789})
        self.cloak()
        self.assertEqual(await self.reports.redact_active(), (0, 0))
        message.edit.assert_not_awaited()

    async def test_index_contains_no_balances_or_rendered_intelligence(self):
        await self.register()
        text = self.path.read_text()
        for secret in ("SECRET", "reactor", "deadline", "donuts", "bank", "secret-url"):
            self.assertNotIn(secret, text)
        self.cloak()
        await self.reports.redact_active()
        archive = self.path.with_name("nyx_report_archive.jsonl")
        self.assertIn("SECRET", archive.read_text())
        self.assertNotIn("SECRET", self.path.read_text())

    async def test_legacy_target_subject_includes_all_mentions_and_actor(self):
        subjects = legacy_report(
            Message(description="<@456> <@!789> secret"), 999, {"123": {}, "456": {}, "789": {}}
        )
        self.assertEqual(subjects, {123, 456, 789})

    async def test_ambiguous_old_balance_is_conservatively_bound(self):
        message = Message(command="balance", title="Username's stash", description="123 trillion")
        self.assertEqual(legacy_report(message, 999, {"123": {}, "456": {}}), {123, 456})

    async def test_old_isd_construction_page_without_owner_id_is_still_protected(self):
        message = Message(
            command="isd status",
            title="IMPERIAL STAR DESTROYER STATUS — Construction",
            description="Hull fabrication completes in 11 hours",
        )
        self.assertEqual(legacy_report(message, 999, {"123": {}, "456": {}}), {123, 456})

    async def test_old_isd_followup_without_command_name_cannot_escape_censorship(self):
        message = Message(
            command="",
            title="IMPERIAL STAR DESTROYER STATUS — Construction",
            description="Hull fabrication completes in 11 hours",
        )
        self.assertEqual(legacy_report(message, 999, {"123": {}, "456": {}}), {123, 456})

    async def test_required_warning_and_attack_outcome_are_never_classified(self):
        cases = (
            ("isd launch", "IMPERIAL COMPONENT LAUNCH DETECTED"),
            ("space fleet launch", "EARTH SPACECRAFT LAUNCH"),
            ("deathstar deploy", "DEATH STAR SECTION — PUBLIC DEFENSE WINDOW"),
            ("thor strike", "KINETIC PENETRATOR RELEASED"),
            ("", "B-2 SPIRIT — TOTAL STRIKE"),
        )
        for command, title in cases:
            self.assertEqual(legacy_report(Message(command=command, title=title), 999, {"456": {}}), set())

    async def test_deimos_followup_with_no_command_metadata_is_classified(self):
        message = Message(command="", title="☠️ DEATHMARK TRACK — BOTH TARGETS EXPOSED")
        self.assertEqual(legacy_report(message, 999, {"456": {}}), {123, 456})

    async def test_old_automatic_season_podium_is_a_wealth_report(self):
        message = Message(command="", title="🏆 Hall of Fame", description="<@456> won with 1 trillion")
        self.assertEqual(legacy_report(message, 999, {"456": {}}), {123, 456})

    async def test_own_earned_title_progress_stays_readable_but_private_under_nyx(self):
        self.cloak(123)
        cog = object.__new__(EconomyCog)
        cog.bot, cog.econ = (self.bot, self.bot.economy)
        interaction = _FakeInteraction()
        await EconomyCog.titles_cmd.callback(
            cog, interaction, view=discord.app_commands.Choice(name="Earned", value="earned")
        )
        replies = interaction.response.calls + interaction.followup.calls
        self.assertTrue(replies[-1]["ephemeral"])
        self.assertNotEqual(replies[-1]["embed"].title, "INTELLIGENCE CENSORED")
        if replies[-1].get("view"):
            replies[-1]["view"].stop()

    async def test_history_scan_backfills_all_and_reuses_checkpoints(self):
        rows = [
            Message(mid=101),
            Message(mid=100, command="isd launch", title="IMPERIAL COMPONENT LAUNCH DETECTED"),
        ]
        for message in rows:
            self.messages[message.id] = message
        calls = []

        async def history(**kwargs):
            calls.append(kwargs)
            for message in rows:
                yield message

        self.channel.history = history
        self.channel.last_message_id = rows[0].id
        self.cloak()
        await self.reports.scan_channel(self.channel)
        await self.reports.scan_channel(self.channel)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["limit"], None)
        self.assertTrue(self.reports.data["channels"]["2"]["complete"])
        rows[0].edit.assert_awaited_once()
        rows[1].edit.assert_not_awaited()

    async def test_history_cursor_resumes_after_read_failure(self):
        self.reports.data["channels"]["2"] = dict(before=500, complete=False)

        async def history(**kwargs):
            self.assertEqual(kwargs["before"].id, 500)
            raise discord.Forbidden(NS(status=403, reason="Forbidden"), "history")
            yield

        self.channel.history = history
        await self.reports.scan_channel(self.channel)
        cursor = json.loads(self.path.read_text())["channels"]["2"]
        self.assertFalse(cursor["complete"])
        self.assertEqual(cursor["before"], 500)
        self.assertEqual(cursor["error"], "history_unavailable")

    async def test_arrow_callback_rechecks_after_interaction_check(self):
        visible = True
        view = ui.Paginator(
            [ui.base_embed(description="SECRET")],
            123,
            page_guard=lambda _i: None if visible else nyx.censored_embed(),
        )
        self.addCleanup(view.stop)
        interaction = _FakeInteraction()
        interaction.response.edit_message = AsyncMock()
        await view.interaction_check(interaction)
        visible = False
        await view.next.callback(interaction)
        self.assertEqual(
            interaction.response.edit_message.call_args.kwargs["embed"].title, "INTELLIGENCE CENSORED"
        )

    async def test_topic_jump_cannot_restore_a_revoked_status_page(self):
        view = StatusPager([ui.base_embed(title="Overview", description="SECRET")], 123)
        self.addCleanup(view.stop)
        view._nyx_revoked = True
        view.topic_select._values = ["0"]
        interaction = _FakeInteraction()
        interaction.response.edit_message = AsyncMock()
        await view.select_topic(interaction)
        self.assertEqual(
            interaction.response.edit_message.call_args.kwargs["embed"].title, "INTELLIGENCE CENSORED"
        )

    async def test_shared_delivery_indexes_initial_deferred_and_followup_reports(self):
        self.bot.nyx_reports = self.reports
        for mode in ("initial", "deferred", "followup"):
            with self.subTest(mode=mode):
                message = Message(mid={"initial": 20, "deferred": 21, "followup": 22}[mode])
                self.messages[message.id] = message
                interaction = _FakeInteraction()
                interaction.client = self.bot
                interaction.original_response = AsyncMock(return_value=message)
                nyx.protect_report(interaction, self.bot.economy, 456)
                if mode == "deferred":
                    await ui.defer_response(interaction)
                elif mode == "followup":
                    await interaction.response.send_message(content="Previous response")
                await ui.respond(interaction, embed=message.embeds[0])
                self.assertIn(str(message.id), self.reports.data["reports"])
        self.cloak()
        self.assertEqual(await self.reports.redact_active(), (3, 0))

    async def test_aggregate_report_indexes_every_cached_page_subject(self):
        self.bot.nyx_reports = self.reports
        message = Message(mid=30, command="leaderboard", title="Leaderboard", description="<@123> 1 trillion")
        self.messages[30] = message
        view = ui.Paginator([message.embeds[0], ui.base_embed(description="<@789> 5 trillion")], 123)
        self.addCleanup(view.stop)
        interaction = _FakeInteraction()
        interaction.client = self.bot
        interaction.command = NS(qualified_name="leaderboard")
        interaction.original_response = AsyncMock(return_value=message)
        await ui.respond(interaction, embed=message.embeds[0], view=view)
        self.assertEqual(self.reports.data["reports"]["30"]["subjects"], [123, 789])
        self.cloak(789)
        self.assertEqual(await self.reports.redact_active(), (1, 0))

    async def test_private_delivery_does_not_fetch_or_index_ephemeral_message(self):
        self.bot.nyx_reports = self.reports
        interaction = _FakeInteraction()
        interaction.client = self.bot
        interaction.original_response = AsyncMock()
        nyx.protect_report(interaction, self.bot.economy, 456)
        await ui.respond(interaction, embed=ui.base_embed(description="Private intel"), ephemeral=True)
        interaction.original_response.assert_not_awaited()
        self.assertEqual(self.reports.data["reports"], {})

    async def test_shared_economy_field_redacts_reports_in_other_server(self):
        self.bot.economy.store.aliases[2] = 1
        message = Message(mid=31)
        message.guild.id = 2
        await self.register(message)
        self.cloak()
        self.assertEqual(await self.reports.redact_active(), (1, 0))
        self.assertEqual(message.embeds[0].title, "INTELLIGENCE CENSORED")

    async def test_completed_backfill_scans_newer_messages_after_high_watermark(self):
        message = Message(mid=600)
        self.reports.data["channels"]["2"] = dict(before=100, latest=500, complete=True)
        self.channel.last_message_id = 600

        async def history(**kwargs):
            self.assertEqual(kwargs["after"].id, 500)
            self.assertFalse(kwargs["oldest_first"])
            yield message

        self.channel.history = history
        self.messages[600] = message
        self.cloak()
        await self.reports.scan_channel(self.channel)
        self.assertEqual(self.reports.data["channels"]["2"]["latest"], 600)
        self.assertEqual(message.embeds[0].title, "INTELLIGENCE CENSORED")

    async def test_concurrent_registration_and_cleanup_use_the_runtime_loop(self):
        self.cloak()
        rows = [Message(mid=mid) for mid in (70, 71, 72)]
        self.messages.update({message.id: message for message in rows})
        await asyncio.gather(
            *(self.reports.register(message, {456}) for message in rows), self.reports.redact_active()
        )
        self.assertTrue(all((record["redacted"] for record in self.reports.data["reports"].values())))
        self.assertTrue(all((message.embeds[0].title == "INTELLIGENCE CENSORED" for message in rows)))


class RuntimeConstructionTests(unittest.TestCase):
    def test_construction_before_asyncio_run_does_not_bind_locks_to_an_old_loop(self):
        with tempfile.TemporaryDirectory() as folder:
            registry = PublicReports(NS(), Path(folder) / "index.json")
            self.assertIsNone(registry.lock)
            self.assertIsNone(registry.cleanup_lock)

            async def exercise():
                await asyncio.gather(registry.save(), registry.save(), registry.save())

            asyncio.run(exercise())
            self.assertTrue(registry.path.exists())


if __name__ == "__main__":
    unittest.main()
