"""Curated public notes only: isolated approval queue and fail-closed delivery.

This module must never import economy, configuration, ledger or diff readers.
The only publishable input is an explicit, reviewed title and body.
"""

from __future__ import annotations
import os
import asyncio
import copy
import datetime as dt
import hashlib
import json
import re
import unicodedata
import uuid
from pathlib import Path
import discord
from .storage import Storage

CHANNEL_ID = int(os.getenv("PATCHNOTES_CHANNEL_ID", "0") or "0")
OUTBOX_FILE = Path(__file__).resolve().parent.parent / "data" / "public_updates.json"
HISTORY_LIMIT = 200
_CREDENTIAL = re.compile(
    "(?:\\bmfa\\.[\\w-]{60,}|[\\w-]{24,}\\.[\\w-]{6,}\\.[\\w-]{27,}|\\b(?:token|password|api[_ -]?key)\\s*[:=])",
    re.I,
)


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def public_text(entry):
    return f"Patch Notes — {entry['title']}\n\n{entry['notes']}\n\nUpdate {entry['id']}"


def digest(entry):
    return hashlib.sha256(public_text(entry).encode("utf-8")).hexdigest()


def validate(entry):
    """Defence in depth, not a substitute for human approval of public content."""
    if not isinstance(entry.get("id"), str) or not re.fullmatch("PN-[A-F0-9]{12}", entry["id"]):
        raise ValueError("Invalid patch-note ID.")
    title, notes = (entry.get("title"), entry.get("notes"))
    if (
        not isinstance(title, str)
        or not title.strip()
        or len(title) > 120
        or ("\n" in title)
        or ("\r" in title)
    ):
        raise ValueError("Use a single-line title of 1–120 characters.")
    if not isinstance(notes, str) or not notes.strip():
        raise ValueError("Write an explicit public summary first.")
    text = public_text(entry)
    if len(text.encode("utf-16-le")) // 2 > 2000:
        raise ValueError("The complete post must fit 2,000 characters; shorten the notes.")
    cleaned = "".join((c for c in unicodedata.normalize("NFKC", text) if unicodedata.category(c) != "Cf"))
    if _CREDENTIAL.search(cleaned) or re.search("(?<!\\d)\\d{17,20}(?!\\d)", cleaned):
        raise ValueError(
            "This summary may contain private mechanics or credentials. Rewrite it as public-only text."
        )
    if any((c in cleaned for c in ("\x00", "\r"))) or re.search("@everyone|@here|<[@#]", cleaned, re.I):
        raise ValueError("Patch notes cannot contain pings or embedded Discord mentions.")
    return text


class PublicUpdates:
    """One fixed-channel outbox, independent of player state and server aliases."""

    def __init__(self, path=OUTBOX_FILE):
        self.path = Path(path)
        self.lock = asyncio.Lock()
        self.document = {"version": 1, "entries": {}}
        if self.path.exists():
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                not isinstance(document, dict)
                or document.get("version") != 1
                or (not isinstance(document.get("entries"), dict))
            ):
                raise ValueError("Invalid public-update queue; publication is stopped.")
            self.document = document

    async def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(
            Storage._write, self.path, json.dumps(self.document, indent=2, ensure_ascii=False)
        )

    def entry(self, update_id):
        entry = self.document["entries"].get(update_id.upper())
        if not isinstance(entry, dict):
            raise ValueError("Unknown patch-note ID. Use `/patchnotes queue`.")
        return copy.deepcopy(entry)

    def entries(self, guild_id):
        return [
            copy.deepcopy(row)
            for row in self.document["entries"].values()
            if isinstance(row, dict) and row.get("guild_id") == guild_id
        ]

    async def draft(self, title, notes, guild_id, author_id):
        entry = {
            "id": "PN-" + uuid.uuid4().hex[:12].upper(),
            "title": title.strip(),
            "notes": notes.strip(),
            "guild_id": guild_id,
            "status": "draft",
            "created_at": now(),
            "created_by": author_id,
            "message_id": None,
        }
        validate(entry)
        async with self.lock:
            self.document["entries"][entry["id"]] = entry
            try:
                await self.save()
            except BaseException:
                self.document["entries"].pop(entry["id"], None)
                raise
        return copy.deepcopy(entry)

    async def approve(self, update_id, expected_digest, guild_id, approver_id):
        async with self.lock:
            row = self.document["entries"].get(update_id)
            if not isinstance(row, dict) or row.get("guild_id") != guild_id:
                raise ValueError("That note does not belong to this server.")
            if row.get("status") != "draft":
                raise ValueError("This note is no longer awaiting approval. Refresh its review.")
            validate(row)
            if digest(row) != expected_digest:
                raise ValueError("The draft changed. Review its exact text again before approving.")
            before = copy.deepcopy(row)
            row.update(
                status="approved",
                approved_at=now(),
                approved_by=approver_id,
                approved_digest=expected_digest,
                deployed_confirmed=True,
            )
            try:
                await self.save()
            except BaseException:
                row.clear()
                row.update(before)
                raise

    async def cancel(self, update_id, guild_id):
        async with self.lock:
            row = self.document["entries"].get(update_id)
            if not isinstance(row, dict) or row.get("guild_id") != guild_id:
                raise ValueError("That note does not belong to this server.")
            if row.get("status") not in {"draft", "approved", "sending"}:
                raise ValueError("Only a pending note can be cancelled. Posted messages are not deleted.")
            before = copy.deepcopy(row)
            row.update(status="cancelled", cancelled_at=now())
            try:
                await self.save()
            except BaseException:
                row.clear()
                row.update(before)
                raise

    @staticmethod
    async def channel(bot, *, require_permissions=True):
        channel = bot.get_channel(CHANNEL_ID)
        if channel is None:
            channel = await bot.fetch_channel(CHANNEL_ID)
        if getattr(channel, "id", None) != CHANNEL_ID or getattr(channel, "guild", None) is None:
            raise ValueError("The updates destination must be the configured server channel.")
        if require_permissions:
            permissions = channel.permissions_for(channel.guild.me)
            if not (
                permissions.view_channel and permissions.send_messages and permissions.read_message_history
            ):
                raise ValueError(
                    "The bot needs View Channel, Send Messages and Read Message History in the updates channel."
                )
        return channel

    async def _recover(self, channel, bot, row, text):
        anchor = int(row.get("history_anchor", 0))
        count = 0
        async for message in channel.history(
            limit=HISTORY_LIMIT + 1, after=discord.Object(anchor) if anchor else None, oldest_first=True
        ):
            count += 1
            if message.author.id == bot.user.id and message.content == text:
                row.update(status="posted", message_id=message.id, posted_at=now(), last_error=None)
                await self.save()
                return True
        row["last_error"] = (
            "History scan limit reached; check the channel before cancelling."
            if count > HISTORY_LIMIT
            else "Delivery uncertain; no matching post found. Held to avoid duplicate posts."
        )
        return False

    async def publish(self, bot):
        """Flush eligible entries, reconciling crash-after-send without duplicates."""
        async with self.lock:
            pending = [
                row
                for row in self.document["entries"].values()
                if isinstance(row, dict) and row.get("status") in {"approved", "sending"}
            ]
            if not pending:
                return
            channel = await self.channel(bot)
            for row in pending:
                if row.get("guild_id") != channel.guild.id:
                    row["last_error"] = "This note belongs to a different server."
                    continue
                try:
                    text = validate(row)
                    if (
                        row.get("approved_digest") != digest(row)
                        or row.get("deployed_confirmed") is not True
                        or (not isinstance(row.get("approved_by"), int))
                        or (row.get("approved_by") not in getattr(bot, "owner_ids_set", set()))
                        or (not row.get("approved_at"))
                    ):
                        row["last_error"] = "Approval missing or text changed; publication blocked."
                        continue
                    if row["status"] == "sending":
                        await self._recover(channel, bot, row, text)
                        continue
                    anchor = 0
                    async for message in channel.history(limit=1):
                        anchor = message.id
                    before = copy.deepcopy(row)
                    row.update(status="sending", history_anchor=anchor, attempted_at=now())
                    try:
                        await self.save()
                    except BaseException:
                        row.clear()
                        row.update(before)
                        raise
                    try:
                        message = await channel.send(
                            content=text,
                            allowed_mentions=discord.AllowedMentions.none(),
                            suppress_embeds=True,
                            silent=True,
                            nonce=row["id"],
                        )
                    except discord.HTTPException as exc:
                        if exc.status in {400, 403, 404, 429}:
                            row["status"] = "approved"
                        row["last_error"] = "Discord delivery failed; waiting for retry or reconciliation."
                        await self.save()
                        continue
                    except (OSError, asyncio.TimeoutError):
                        row["last_error"] = (
                            "Delivery uncertain; waiting for reconciliation without resending."
                        )
                        await self.save()
                        continue
                    row.update(status="posted", message_id=message.id, posted_at=now(), last_error=None)
                    await self.save()
                except ValueError:
                    row["last_error"] = "Public-content validation failed; publication blocked."
