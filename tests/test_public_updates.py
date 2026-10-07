"""Public-note approval, privacy, durable delivery and restart reconciliation."""

import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import discord
from cogs.patchnotes import ApprovalView, PatchNotes
from szofie import checks, public_updates
from tests.test_system_integration import _FakeInteraction


class Channel:
    def __init__(self):
        self.id = public_updates.CHANNEL_ID
        self.guild = SimpleNamespace(id=1, me=SimpleNamespace(id=800))
        self.messages = []
        self.permissions = discord.Permissions(
            view_channel=True, send_messages=True, read_message_history=True
        )
        self.send = AsyncMock(side_effect=self.deliver)
        self.history_calls = []

    def permissions_for(self, member):
        return self.permissions

    async def deliver(self, **kwargs):
        message = SimpleNamespace(
            id=1000 + len(self.messages), author=SimpleNamespace(id=800), content=kwargs["content"]
        )
        self.messages.append(message)
        return message

    async def history(self, *, limit=100, after=None, oldest_first=False):
        self.history_calls.append((limit, getattr(after, "id", None)))
        messages = [m for m in self.messages if after is None or m.id > after.id]
        messages.sort(key=lambda message: message.id, reverse=not oldest_first)
        for message in messages[:limit]:
            yield message


class PublicUpdateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "updates.json"
        self.outbox = public_updates.PublicUpdates(self.path)
        self.channel = Channel()
        self.bot = SimpleNamespace(
            user=SimpleNamespace(id=800),
            owner_ids_set={123},
            get_channel=lambda _uid: self.channel,
            fetch_channel=AsyncMock(return_value=self.channel),
        )

    async def draft(self):
        return await self.outbox.draft(
            "Nyx", "- Cooldown reduced to 10 hours.\n- Cloak duration remains 3 hours.", 1, 123
        )

    async def approved(self):
        row = await self.draft()
        await self.outbox.approve(row["id"], public_updates.digest(row), 1, 123)
        return row

    async def test_empty_and_draft_queue_never_publish_or_read_private_systems(self):
        await self.outbox.publish(self.bot)
        row = await self.draft()
        await self.outbox.publish(self.bot)
        self.channel.send.assert_not_awaited()
        self.bot.fetch_channel.assert_not_awaited()
        self.assertEqual(self.outbox.entry(row["id"])["status"], "draft")
        self.assertFalse(hasattr(self.bot, "economy"))
        self.assertFalse(hasattr(self.bot, "ledger"))

    async def test_approval_posts_exact_preview_without_pings_and_only_once_across_restart(self):
        row = await self.approved()
        expected = public_updates.public_text(row)
        await asyncio.gather(self.outbox.publish(self.bot), self.outbox.publish(self.bot))
        self.channel.send.assert_awaited_once()
        sent = self.channel.send.call_args.kwargs
        self.assertEqual(sent["content"], expected)
        self.assertFalse(sent["allowed_mentions"].everyone)
        self.assertFalse(sent["allowed_mentions"].users)
        self.assertFalse(sent["allowed_mentions"].roles)
        self.assertTrue(sent["silent"])
        self.assertTrue(sent["suppress_embeds"])
        self.assertEqual(sent["nonce"], row["id"])
        self.assertNotIn("approved_by", sent["content"])
        restored = public_updates.PublicUpdates(self.path)
        await restored.publish(self.bot)
        self.channel.send.assert_awaited_once()
        self.assertEqual(restored.entry(row["id"])["message_id"], self.channel.messages[0].id)

    async def test_stale_preview_and_cross_server_approval_are_rejected(self):
        row = await self.draft()
        self.outbox.document["entries"][row["id"]]["notes"] = "Different public change"
        with self.assertRaises(ValueError):
            await self.outbox.approve(row["id"], public_updates.digest(row), 1, 123)
        current = self.outbox.entry(row["id"])
        with self.assertRaises(ValueError):
            await self.outbox.approve(row["id"], public_updates.digest(current), 2, 123)
        await self.outbox.publish(self.bot)
        self.channel.send.assert_not_awaited()

    async def test_changed_content_missing_approval_or_removed_owner_never_publish(self):
        row = await self.approved()
        original = copy.deepcopy(self.outbox.document)
        for change in (
            {"notes": "Different approved-looking text"},
            {"deployed_confirmed": False},
            {"approved_digest": None},
            {"approved_by": 456},
            {"approved_at": None},
        ):
            self.outbox.document = copy.deepcopy(original)
            self.outbox.document["entries"][row["id"]].update(change)
            await self.outbox.publish(self.bot)
        self.channel.send.assert_not_awaited()

    async def test_snowflake_ids_and_approval_revocation_do_not_leak_or_publish(self):
        with self.assertRaises(ValueError):
            await self.outbox.draft("Update", "Account 123456789012345678 changed", 1, 123)
        row = await self.approved()
        self.bot.owner_ids_set.clear()
        await self.outbox.publish(self.bot)
        self.channel.send.assert_not_awaited()
        self.assertEqual(self.outbox.entry(row["id"])["status"], "approved")

    async def test_exact_discord_limit_is_accepted(self):
        row = {"id": "PN-123456789ABC", "title": "Update", "notes": "x"}
        overhead = len(public_updates.public_text(row)) - 1
        row["notes"] = "x" * (2000 - overhead)
        self.assertEqual(len(public_updates.validate(row)), 2000)
        row["notes"] += "x"
        with self.assertRaises(ValueError):
            public_updates.validate(row)

    async def test_approval_or_intent_save_failure_prevents_send_and_rolls_back(self):
        row = await self.draft()
        before = copy.deepcopy(self.outbox.document)
        with patch.object(self.outbox, "save", new=AsyncMock(side_effect=OSError("disk full"))):
            with self.assertRaises(OSError):
                await self.outbox.approve(row["id"], public_updates.digest(row), 1, 123)
        self.assertEqual(self.outbox.document, before)
        await self.outbox.approve(row["id"], public_updates.digest(row), 1, 123)
        with patch.object(self.outbox, "save", new=AsyncMock(side_effect=OSError("disk full"))):
            with self.assertRaises(OSError):
                await self.outbox.publish(self.bot)
        self.channel.send.assert_not_awaited()
        self.assertEqual(self.outbox.entry(row["id"])["status"], "approved")

    async def test_crash_after_discord_send_is_reconciled_from_history_not_resent(self):
        row = await self.approved()
        real_save = self.outbox.save
        saves = 0

        async def fail_ack_save():
            nonlocal saves
            saves += 1
            if saves == 2:
                raise OSError("disk full after post")
            await real_save()

        with patch.object(self.outbox, "save", side_effect=fail_ack_save):
            with self.assertRaises(OSError):
                await self.outbox.publish(self.bot)
        self.assertEqual(self.outbox.entry(row["id"])["status"], "posted")
        self.assertEqual(json.loads(self.path.read_text())["entries"][row["id"]]["status"], "sending")
        restored = public_updates.PublicUpdates(self.path)
        await restored.publish(self.bot)
        self.channel.send.assert_awaited_once()
        self.assertEqual(restored.entry(row["id"])["status"], "posted")

    async def test_uncertain_send_without_matching_post_is_held_not_repeated(self):
        row = await self.approved()
        self.channel.send.side_effect = asyncio.TimeoutError()
        await self.outbox.publish(self.bot)
        restored = public_updates.PublicUpdates(self.path)
        await restored.publish(self.bot)
        await restored.publish(self.bot)
        self.channel.send.assert_awaited_once()
        self.assertEqual(restored.entry(row["id"])["status"], "sending")
        self.assertIn("Held", restored.entry(row["id"])["last_error"])

    async def test_timeout_after_actual_delivery_is_reconciled_even_after_restart(self):
        row = await self.approved()

        async def deliver_then_timeout(**kwargs):
            await self.channel.deliver(**kwargs)
            raise asyncio.TimeoutError()

        self.channel.send.side_effect = deliver_then_timeout
        await self.outbox.publish(self.bot)
        restored = public_updates.PublicUpdates(self.path)
        await restored.publish(self.bot)
        self.channel.send.assert_awaited_once()
        self.assertEqual(restored.entry(row["id"])["status"], "posted")

    async def test_missing_permissions_offline_fetch_and_wrong_destination_leave_queue_pending(self):
        row = await self.approved()
        self.channel.permissions.read_message_history = False
        with self.assertRaises(ValueError):
            await self.outbox.publish(self.bot)
        self.channel.permissions.read_message_history = True
        self.bot.get_channel = lambda _uid: None
        self.bot.fetch_channel.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "missing access"
        )
        with self.assertRaises(discord.Forbidden):
            await self.outbox.publish(self.bot)
        self.assertEqual(self.outbox.entry(row["id"])["status"], "approved")
        self.channel.send.assert_not_awaited()
        self.bot.get_channel = lambda _uid: self.channel
        self.channel.id += 1
        with self.assertRaises(ValueError):
            await self.outbox.publish(self.bot)

    async def test_cancelled_notes_stay_cancelled_after_restart(self):
        row = await self.approved()
        await self.outbox.cancel(row["id"], 1)
        await public_updates.PublicUpdates(self.path).publish(self.bot)
        self.channel.send.assert_not_awaited()

    async def test_definite_discord_rejection_can_retry_without_marking_posted(self):
        row = await self.approved()
        self.channel.send.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "missing access"
        )
        await self.outbox.publish(self.bot)
        self.assertEqual(self.outbox.entry(row["id"])["status"], "approved")
        self.channel.send.side_effect = self.channel.deliver
        await self.outbox.publish(self.bot)
        self.assertEqual(self.outbox.entry(row["id"])["status"], "posted")

    async def test_corrupt_queue_fails_closed_without_overwriting_it(self):
        self.path.write_text("broken json")
        with self.assertRaises(ValueError):
            public_updates.PublicUpdates(self.path)
        self.assertEqual(self.path.read_text(), "broken json")

    def interaction(self, uid=123, guild_id=1):
        interaction = _FakeInteraction(user_id=uid, guild_id=guild_id)
        interaction.client = self.bot
        return interaction

    def cog(self):
        cog = object.__new__(PatchNotes)
        cog.bot, cog.outbox = (self.bot, self.outbox)
        return cog

    async def test_controls_are_private_owner_only_and_bound_to_destination_guild(self):
        cog = self.cog()
        for command in PatchNotes.group.commands:
            self.assertIn(checks.owner_only_check, command.checks)
        with self.assertRaises(checks.Denied):
            await cog.authorize(self.interaction(456))
        with self.assertRaises(checks.Denied):
            await cog.authorize(self.interaction(guild_id=2))
        dm = self.interaction()
        dm.guild = dm.guild_id = None
        with self.assertRaises(checks.Denied):
            await cog.authorize(dm)
        for callback, args in ((PatchNotes.draft, ("Update", "Public fix")), (PatchNotes.queue, ())):
            interaction = self.interaction()
            await callback.callback(cog, interaction, *args)
            self.assertTrue(interaction.response.deferred["ephemeral"])
            self.assertTrue(interaction.response.calls[-1]["ephemeral"])
        self.channel.send.assert_not_awaited()

    async def test_review_is_exact_and_stale_or_non_owner_buttons_cannot_approve(self):
        cog = self.cog()
        row = await self.draft()
        interaction = self.interaction()
        await PatchNotes.review.callback(cog, interaction, row["id"])
        reply = interaction.response.calls[-1]
        self.assertTrue(reply["ephemeral"])
        self.assertEqual(reply["embed"].description, public_updates.public_text(row))
        view = reply["view"]
        self.addCleanup(view.stop)
        self.assertIsInstance(view, ApprovalView)
        self.assertFalse(await view.interaction_check(self.interaction(456)))
        self.bot.owner_ids_set.add(789)
        self.assertFalse(await view.interaction_check(self.interaction(789)))
        self.assertTrue(await view.interaction_check(self.interaction()))
        self.outbox.document["entries"][row["id"]]["notes"] = "Changed public draft"
        click = self.interaction()
        await view.approve.callback(click)
        self.assertTrue(click.response.deferred["ephemeral"])
        self.channel.send.assert_not_awaited()

    async def test_authorized_approval_button_publishes_and_receipt_stays_private(self):
        row = await self.draft()
        view = ApprovalView(self.cog(), row, 123)
        self.addCleanup(view.stop)
        click = self.interaction()
        click.message = SimpleNamespace(edit=AsyncMock())
        await view.approve.callback(click)
        self.assertTrue(click.response.deferred["ephemeral"])
        self.assertTrue(click.response.calls[-1]["ephemeral"])
        self.channel.send.assert_awaited_once()
        self.assertEqual(self.outbox.entry(row["id"])["status"], "posted")
        self.assertTrue(all((child.disabled for child in view.children)))

    async def test_history_recovery_uses_anchor_and_never_accepts_another_authors_copy(self):
        row = await self.approved()
        self.channel.messages.append(
            SimpleNamespace(id=99, author=SimpleNamespace(id=456), content="Previous message")
        )
        self.channel.send.side_effect = asyncio.TimeoutError()
        await self.outbox.publish(self.bot)
        self.assertEqual(self.outbox.entry(row["id"])["history_anchor"], 99)
        self.channel.messages.append(
            SimpleNamespace(id=100, author=SimpleNamespace(id=456), content=public_updates.public_text(row))
        )
        for index in range(101, 302):
            self.channel.messages.append(
                SimpleNamespace(id=index, author=SimpleNamespace(id=456), content="Other message")
            )
        restored = public_updates.PublicUpdates(self.path)
        await restored.publish(self.bot)
        self.assertEqual(restored.entry(row["id"])["status"], "sending")
        self.assertIn("scan limit", restored.entry(row["id"])["last_error"])
        self.assertIn((public_updates.HISTORY_LIMIT + 1, 99), self.channel.history_calls)
        self.channel.send.assert_awaited_once()

    async def test_missing_send_permission_does_not_block_private_review_or_cancel(self):
        cog = self.cog()
        row = await self.draft()
        self.channel.permissions.send_messages = False
        review = self.interaction()
        await PatchNotes.review.callback(cog, review, row["id"])
        self.addCleanup(review.response.calls[-1]["view"].stop)
        cancelled = self.interaction()
        await PatchNotes.cancel.callback(cog, cancelled, row["id"])
        self.assertEqual(self.outbox.entry(row["id"])["status"], "cancelled")
        self.assertTrue(cancelled.response.calls[-1]["ephemeral"])
