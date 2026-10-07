"""Durable revocation of bot-owned public intelligence, including old reports.

The index stores message/subject IDs, not balances or rendered intel. Discord
warnings and attack outcomes are deliberately not reports. Original messages
are archived locally before edits; redacted reports are never restored at expiry.
"""

from __future__ import annotations
import asyncio
import json
import logging
import os
import re
import warnings
from pathlib import Path
import discord
from . import nyx
from .storage import Storage

log = logging.getLogger(__name__)
MENTION = re.compile("<@!?(\\d+)>")
REPORT_COMMANDS = frozenset(
    {
        "balance",
        "bank",
        "stash",
        "inventory",
        "plushies",
        "badges",
        "titles",
        "vehicle hangar",
        "hangar",
        "bucket",
        "rod",
        "bestiary",
        "arsenal",
        "casinostats",
        "leaderboard",
        "season",
        "work status",
        "job status",
        "country atlas",
        "country inspect",
        "country list",
        "country leaderboard",
        "space atlas",
        "space inspect",
        "space status",
        "space fleet status",
        "space fleet launches",
        "isd status",
        "isd windows",
        "isd inspect",
        "deathstar status",
        "deathstar windows",
        "deathstar inspect",
        "thor status",
        "thor launches",
        "aa status",
        "aa s400-status",
        "loans",
        "loan list",
        "loan status",
        "coalition intel",
        "vehicle build",
        "vehicle load",
        "vehicle arm",
        "vehicle upgrade",
        "vehicle repair",
        "icbm build",
        "aa build",
        "aa load",
        "aa shield",
        "aa s400-build",
        "aa s400-load",
        "thor fabricate",
        "thor assemble",
        "thor resupply",
        "thor chamber",
        "thor service",
        "thor gbi-build",
        "thor gbi-upgrade",
        "thor bmd-refit",
        "thor bmd-load",
        "isd fabricate",
        "isd assemble",
        "isd arm",
        "isd repair",
        "isd counter-build",
        "deathstar build",
        "deathstar fabricate",
        "deathstar assemble",
        "deathstar transport",
        "deathstar charge",
        "deathstar repair",
        "deathstar develop",
        "deathstar squadron",
        "space build",
        "space fleet payload",
        "space fleet repair",
    }
)
AMBIGUOUS_TARGET_COMMANDS = frozenset(
    {
        "balance",
        "bank",
        "stash",
        "inventory",
        "plushies",
        "badges",
        "titles",
        "bucket",
        "rod",
        "bestiary",
        "loans",
        "loan list",
        "isd status",
    }
)
AGGREGATE_COMMANDS = frozenset(
    {
        "leaderboard",
        "season",
        "country atlas",
        "country leaderboard",
        "space atlas",
        "space fleet launches",
        "isd windows",
        "deathstar windows",
    }
)
LEGACY_TITLES = (
    "IMPERIAL STAR DESTROYER STATUS",
    "DEATH STAR STATUS",
    "PROJECT THOR STATUS",
    "SPACE FLEET STATUS",
    "DEATHMARK TRACK — BOTH TARGETS EXPOSED",
    "NYX — GHOST PROTOCOL ACTIVE",
    "HALL OF FAME",
)


def payload_subjects(embeds, content=""):
    text = content or ""
    text += json.dumps([embed.to_dict() for embed in embeds], ensure_ascii=False)
    return {int(uid) for uid in MENTION.findall(text)}


def legacy_report(message, bot_id, known_users):
    """Classify only our historical reports. Never infer from an attack mention."""
    if message.author.id != bot_id or getattr(message.flags, "ephemeral", False):
        return set()
    embeds = message.embeds
    if not embeds or all((embed.title == "INTELLIGENCE CENSORED" for embed in embeds)):
        return set()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        interaction = getattr(message, "interaction", None)
    name = getattr(interaction, "name", "") or ""
    titles = " ".join((embed.title or "" for embed in embeds)).upper()
    if name not in REPORT_COMMANDS and (not any((title in titles for title in LEGACY_TITLES))):
        return set()
    subjects = payload_subjects(embeds, message.content)
    metadata = getattr(message, "interaction_metadata", None)
    actor = getattr(metadata, "user", None) or getattr(interaction, "user", None)
    ambiguous_isd_page = "IMPERIAL STAR DESTROYER STATUS" in titles and (not subjects)
    if (name in AMBIGUOUS_TARGET_COMMANDS | AGGREGATE_COMMANDS or ambiguous_isd_page) and (not subjects):
        subjects.update((int(uid) for uid in known_users))
    if actor is not None:
        subjects.add(actor.id)
    return subjects


class PublicReports:
    def __init__(self, bot, path: Path):
        self.bot = bot
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = None
        self.cleanup_lock = None
        self.views = {}
        if path.exists():
            self.data = json.loads(path.read_text())
            if not isinstance(self.data, dict):
                raise ValueError("Invalid NYX report index; refusing to discard its history")
        else:
            self.data = {}
        self.data.setdefault("reports", {})
        self.data.setdefault("channels", {})

    async def save(self):
        if self.lock is None:
            self.lock = asyncio.Lock()
        async with self.lock:
            payload = json.dumps(self.data, ensure_ascii=False)
            await asyncio.to_thread(Storage._write, self.path, payload)

    def users(self, gid):
        return self.bot.economy.store.load(gid).get("users", {})

    def cloaked(self, record):
        users = self.users(record["guild"])
        return any((nyx.active(users.get(str(uid), {})) for uid in record["subjects"]))

    def revoke_view(self, mid):
        view = self.views.pop(str(mid), None)
        if view is None:
            return
        view._nyx_revoked = True
        if hasattr(view, "pages"):
            view.pages[:] = [nyx.censored_embed() for _ in view.pages]
            view.page_guard = lambda _index: nyx.censored_embed()
        view.stop()

    async def register(self, message, subjects, view=None, *, guild_id=None, channel_id=None):
        if not subjects or getattr(message.flags, "ephemeral", False):
            return
        if message.author.id != self.bot.user.id:
            return
        guild_id = getattr(getattr(message, "guild", None), "id", None) or guild_id
        channel_id = getattr(getattr(message, "channel", None), "id", None) or channel_id
        if guild_id is None or channel_id is None:
            raise ValueError("Public report is missing its originating channel context")
        key = str(message.id)
        record = self.data["reports"].setdefault(
            key, dict(guild=guild_id, channel=channel_id, subjects=[], redacted=False, pending=False)
        )
        if any((embed.title != "INTELLIGENCE CENSORED" for embed in message.embeds)):
            record["redacted"] = False
        record["subjects"] = sorted(set(record["subjects"]) | {int(uid) for uid in subjects})
        if view is not None:
            self.views[key] = view
        record["pending"] = record["pending"] or self.cloaked(record)
        if record["pending"]:
            self.revoke_view(key)
        await self.save()
        if record["pending"] or record["redacted"]:
            await self.redact_one(key, record, message)

    async def redact_one(self, key, record, message=None):
        if self.cleanup_lock is None:
            self.cleanup_lock = {}
        lock = self.cleanup_lock.setdefault(key, asyncio.Lock())
        async with lock:
            if record["redacted"] and (not record["pending"]):
                return True
            self.revoke_view(key)
            channel = self.bot.get_channel(record["channel"])
            try:
                if channel is None:
                    channel = await self.bot.fetch_channel(record["channel"])
                message = message or await channel.fetch_message(int(key))
                if message.author.id != self.bot.user.id:
                    return False
                if not record.get("archived"):
                    original = dict(
                        message=int(key),
                        channel=record["channel"],
                        guild=record["guild"],
                        content=message.content,
                        embeds=[e.to_dict() for e in message.embeds],
                        attachments=[a.to_dict() for a in message.attachments],
                    )
                    await asyncio.to_thread(self._archive, original)
                    record["archived"] = True
                    await self.save()
                await message.edit(
                    content=None,
                    embeds=[nyx.censored_embed()],
                    attachments=[],
                    view=None,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.NotFound:
                record.update(redacted=True, pending=False)
                await self.save()
                return True
            except (discord.HTTPException, OSError, asyncio.TimeoutError):
                record["pending"] = True
                await self.save()
                log.warning("NYX public report cleanup incomplete; retry queued.")
                return False
            record.update(redacted=True, pending=False)
            await self.save()
            return True

    def _archive(self, original):
        archive = self.path.with_name("nyx_report_archive.jsonl")
        with archive.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(original, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    async def redact_active(self):
        self.views = {key: view for key, view in self.views.items() if not view.is_finished()}
        edited = failed = 0
        for key, record in list(self.data["reports"].items()):
            if record["redacted"] and (not record["pending"]):
                continue
            if not record["pending"] and (not self.cloaked(record)):
                continue
            record["pending"] = True
            await self.save()
            if await self.redact_one(key, record):
                edited += 1
            else:
                failed += 1
        return (edited, failed)

    async def queue_active(self):
        """Revoke pagers and durably queue old reports without any Discord I/O."""
        queued = 0
        for key, record in self.data["reports"].items():
            if record["redacted"] and (not record["pending"]):
                continue
            if record["pending"] or self.cloaked(record):
                record["pending"] = True
                self.revoke_view(key)
                queued += 1
        if queued:
            await self.save()
        return queued

    async def scan_channel(self, channel):
        """Backfill complete accessible history, with durable resume checkpoints."""
        key = str(channel.id)
        cursor = self.data["channels"].setdefault(key, {"before": None, "complete": False})
        incremental = cursor["complete"] and bool(cursor.get("latest"))
        latest = cursor.get("latest")
        if incremental and getattr(channel, "last_message_id", None) and (channel.last_message_id <= latest):
            return
        before = discord.Object(cursor["before"]) if cursor["before"] and (not incremental) else None
        options = {"limit": None, "before": before}
        if incremental:
            options = {"limit": None, "after": discord.Object(latest), "oldest_first": False}
        count = 0
        newest = latest or 0
        try:
            async for message in channel.history(**options):
                subjects = legacy_report(message, self.bot.user.id, self.users(channel.guild.id))
                if subjects:
                    await self.register(message, subjects)
                newest = max(newest, message.id)
                if not incremental:
                    cursor["before"] = message.id
                    cursor["latest"] = newest
                count += 1
                if count % 100 == 0:
                    await self.save()
            cursor["complete"] = True
            cursor["latest"] = newest
            cursor.pop("error", None)
        except discord.HTTPException:
            cursor["error"] = "history_unavailable"
            log.warning("NYX legacy history scan could not read a channel; retry queued.")
        await self.save()

    async def backfill(self):
        for guild in self.bot.guilds:
            channels = {
                channel.id: channel
                for channel in [*guild.channels, *guild.threads]
                if hasattr(channel, "history")
            }
            for channel in guild.channels:
                if not hasattr(channel, "archived_threads"):
                    continue
                try:
                    async for thread in channel.archived_threads(limit=None):
                        channels[thread.id] = thread
                    if isinstance(channel, discord.TextChannel):
                        async for thread in channel.archived_threads(private=True, joined=True, limit=None):
                            channels[thread.id] = thread
                except discord.HTTPException:
                    pass
            for channel in channels.values():
                try:
                    async for message in channel.history(limit=100):
                        subjects = legacy_report(message, self.bot.user.id, self.users(guild.id))
                        if subjects:
                            await self.register(message, subjects)
                except discord.HTTPException:
                    pass
            for channel in channels.values():
                await self.scan_channel(channel)


async def record_response(interaction, message, private, view=None):
    """Called after every shared response, including initial sends and followups."""
    registry = getattr(getattr(interaction, "client", None), "nyx_reports", None)
    if not isinstance(registry, PublicReports) or private or interaction.guild_id is None:
        return
    subjects = set(interaction.extras.get("szofie_nyx_public_subjects", ()))
    resolver = interaction.extras.get("szofie_nyx_report")
    if resolver:
        subjects.update(resolver())
    command = getattr(getattr(interaction, "command", None), "qualified_name", "")
    if command in REPORT_COMMANDS:
        if (
            not resolver
            and command not in AGGREGATE_COMMANDS
            and ("szofie_nyx_public_subjects" not in interaction.extras)
        ):
            subjects.add(interaction.user.id)
        if view is not None and hasattr(view, "pages"):
            subjects.update(payload_subjects(view.pages))
    if not subjects:
        return
    try:
        if not isinstance(message, discord.Message):
            message = await interaction.original_response()
        subjects.update(payload_subjects(message.embeds, message.content))
        await asyncio.wait_for(
            registry.register(
                message, subjects, view, guild_id=interaction.guild_id, channel_id=interaction.channel_id
            ),
            timeout=2,
        )
    except Exception:
        log.exception("NYX report indexing failed; historical cleanup will retry.")
