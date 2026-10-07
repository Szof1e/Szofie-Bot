"""Starboard — react ⭐ to a message and, once it clears the threshold, Szofie
reposts it to a highlights channel and keeps the star count live.

Reaction-driven (no slash command to run): configure it with
`/config set starboard.channel #highlights` and, optionally,
`/config set starboard.threshold 3`. The source→board message mapping is
persisted per guild so counts keep updating across restarts.
"""

from __future__ import annotations
import asyncio
import logging
from pathlib import Path
from typing import Any, Dict
import discord
from discord.ext import commands
from szofie import ui
from szofie.storage import Storage

log = logging.getLogger("szofie.starboard")
STARBOARD_DIR = Path(__file__).resolve().parent.parent / "data" / "starboard"
STAR_COLOR = 16755763
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")


class Starboard(commands.Cog):
    """Feature the messages a server loves."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = Storage(directory=STARBOARD_DIR)
        self._locks: Dict[int, asyncio.Lock] = {}

    async def cog_unload(self) -> None:
        await self.store.flush()

    def _posts(self, guild_id: int) -> Dict[str, Any]:
        doc = self.store.load(guild_id)
        if "posts" not in doc or not isinstance(doc["posts"], dict):
            doc["posts"] = {}
        return doc["posts"]

    async def _persist(self, guild_id: int) -> None:
        self.store.mark_dirty(guild_id)
        await self.store.save(guild_id)

    def _lock(self, guild_id: int) -> asyncio.Lock:
        if guild_id not in self._locks:
            self._locks[guild_id] = asyncio.Lock()
        return self._locks[guild_id]

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self._refresh(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self._refresh(payload)

    async def _refresh(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is None:
            return
        cfg = self.bot.config.for_guild(payload.guild_id)
        if not cfg.get("starboard.enabled", True):
            return
        board_id = cfg.get("starboard.channel")
        if not board_id:
            return
        star = str(cfg.get("starboard.emoji", "⭐"))
        if str(payload.emoji) != star:
            return
        if payload.channel_id == int(board_id):
            return
        source = self.bot.get_channel(payload.channel_id)
        board = self.bot.get_channel(int(board_id))
        if not isinstance(source, discord.abc.Messageable) or not isinstance(board, discord.abc.Messageable):
            return
        async with self._lock(payload.guild_id):
            try:
                message = await source.fetch_message(payload.message_id)
            except discord.HTTPException:
                return
            if cfg.get("starboard.ignore_bots", True) and message.author.bot:
                return
            count = await self._count_stars(message, star, bool(cfg.get("starboard.self_star", False)))
            threshold = int(cfg.get("starboard.threshold", 2))
            posts = self._posts(payload.guild_id)
            entry = posts.get(str(payload.message_id))
            if count >= threshold:
                content = f"{star} **{count}**  •  <#{payload.channel_id}>"
                embed = self._build_embed(message, star)
                if entry:
                    try:
                        existing = await board.fetch_message(int(entry["starboard_id"]))
                        await existing.edit(content=content, embed=embed)
                    except discord.HTTPException:
                        posted = await board.send(content=content, embed=embed)
                        entry = {"starboard_id": posted.id}
                else:
                    posted = await board.send(content=content, embed=embed)
                    entry = {"starboard_id": posted.id}
                entry["count"] = count
                posts[str(payload.message_id)] = entry
                await self._persist(payload.guild_id)
            elif entry:
                try:
                    existing = await board.fetch_message(int(entry["starboard_id"]))
                    await existing.delete()
                except discord.HTTPException:
                    pass
                posts.pop(str(payload.message_id), None)
                await self._persist(payload.guild_id)

    @staticmethod
    async def _count_stars(message: discord.Message, star: str, self_star: bool) -> int:
        """Count non-bot stars, optionally excluding the author's own."""
        reaction = next((r for r in message.reactions if str(r.emoji) == star), None)
        if reaction is None:
            return 0
        count = 0
        try:
            async for user in reaction.users():
                if user.bot:
                    continue
                if not self_star and user.id == message.author.id:
                    continue
                count += 1
        except discord.HTTPException:
            return reaction.count
        return count

    @staticmethod
    def _build_embed(message: discord.Message, star: str) -> discord.Embed:
        embed = ui.base_embed(description=message.content or "", color=STAR_COLOR)
        embed.set_author(
            name=message.author.display_name,
            icon_url=message.author.display_avatar.url if message.author.display_avatar else None,
        )
        embed.add_field(name="Source", value=f"[Jump to message]({message.jump_url})", inline=False)
        for att in message.attachments:
            name = (att.filename or "").lower()
            if any((name.endswith(ext) for ext in IMAGE_EXTS)):
                embed.set_image(url=att.url)
                break
        embed.timestamp = message.created_at
        embed.set_footer(text=f"{star} Starboard")
        return embed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Starboard(bot))
