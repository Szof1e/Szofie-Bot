"""Channel security: protected, auditable /nuke.

Destructive commands are gated three ways: a permission check, an optional
button confirmation, and a protected-channel list that no one can bypass
without editing the config.
"""

from __future__ import annotations
import datetime as dt
import logging
import random
from pathlib import Path
from typing import Optional
import discord
from discord import app_commands
from discord.ext import commands
from szofie import checks, ui

log = logging.getLogger("szofie.moderation")
NUKE_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "nuke"
NUKE_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
NUKE_MESSAGES = [
    "Delicious. This channel has been devoured whole by {user}.",
    "Android 21 was hungry. {user} left nothing of this channel behind.",
    "Absorbed. Every message here is gone — courtesy of {user}.",
    "This channel looked far too sweet. {user} couldn't resist.",
    "The Majin hunger takes another channel. {user} served it up.",
    "One bite from {user} and the channel is wiped clean.",
    "Turned to candy and swallowed. {user} is still peckish.",
    "The experiment is complete. {user} erased this channel from existence.",
    "You all looked so tasty. The channel didn't survive {user}.",
    "Consumed. {user} suggests you start over — if you dare.",
]


class Moderation(commands.Cog):
    """Keep the server clean and safe."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _protected(self, guild_id: int, channel_id: int) -> bool:
        cfg = self.bot.config.for_guild(guild_id)
        protected = cfg.get("moderation.protected_channels", []) or []
        return channel_id in {int(cid) for cid in protected}

    async def _confirm(
        self, interaction: discord.Interaction, embed: discord.Embed, *, setting: str, label: str = "Confirm"
    ) -> bool:
        """Show a confirmation gate, unless `setting` has switched it off.

        Whichever branch is taken, the original interaction has been responded
        to by the time this returns, so callers can always use `followup`.
        """
        cfg = self.bot.config.for_guild(interaction.guild_id)
        if not cfg.get(setting, True):
            await interaction.response.defer()
            return True
        view = ui.ConfirmView(interaction.user.id, danger_label=label)
        await ui.respond(interaction, embed=embed, view=view)
        timed_out = await view.wait()
        if timed_out:
            try:
                await interaction.edit_original_response(
                    embed=ui.warn_embed("Confirmation timed out — nothing was changed."), view=None
                )
            except discord.HTTPException:
                pass
            return False
        return bool(view.value)

    async def _audit(self, guild: discord.Guild, actor: discord.abc.User, action: str, detail: str) -> None:
        cfg = self.bot.config.for_guild(guild.id)
        if not cfg.get("moderation.log_deletions", True):
            return
        embed = ui.base_embed(title=f"🛡 {action}", color=ui.COLOR_WARN)
        embed.add_field(name="Moderator", value=f"{actor.mention} (`{actor.id}`)", inline=False)
        embed.add_field(name="Details", value=detail, inline=False)
        embed.timestamp = dt.datetime.now(dt.timezone.utc)
        await self.bot.log_action(guild, embed)

    @app_commands.command(
        name="nuke",
        description="Wipe a channel completely by recreating it (keeps name, permissions, position).",
    )
    @app_commands.describe(
        channel="Channel to nuke (defaults to this one)", reason="Recorded in the audit log"
    )
    @app_commands.check(lambda i: checks.feature_enabled(i, "moderation"))
    @app_commands.check(checks.nuke_check)
    async def nuke(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        reason: Optional[str] = None,
    ) -> None:
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await ui.respond(
                interaction, embed=ui.error_embed("Only regular text channels can be nuked."), ephemeral=True
            )
            return
        if self._protected(interaction.guild_id, target.id):
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"{target.mention} is on the protected-channels list and cannot be nuked."
                ),
                ephemeral=True,
            )
            return
        guild = interaction.guild
        blocked = {guild.rules_channel, guild.public_updates_channel}
        if target in blocked:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    "Discord doesn't allow deleting the rules or community-updates channel."
                ),
                ephemeral=True,
            )
            return
        me = guild.me
        if not target.permissions_for(me).manage_channels or not me.guild_permissions.manage_channels:
            await ui.respond(
                interaction,
                embed=ui.error_embed("I need **Manage Channels** to nuke a channel."),
                ephemeral=True,
            )
            return
        preview = ui.warn_embed(
            f"This will **delete {target.mention} and every message in it**, then recreate an identical empty channel with the same name, permissions, topic and position.\n\n**Message history cannot be recovered.**",
            title="Confirm nuke",
        )
        preview.add_field(name="Channel", value=f"#{target.name} (`{target.id}`)", inline=True)
        preview.add_field(name="Reason", value=reason or "—", inline=True)
        if not await self._confirm(interaction, preview, setting="moderation.confirm_nuke", label="Nuke it"):
            return
        position = target.position
        old_id = target.id
        audit_reason = f"/nuke by {interaction.user} ({interaction.user.id})"
        if reason:
            audit_reason += f" — {reason}"
        try:
            fresh = await target.clone(reason=audit_reason)
            await target.delete(reason=audit_reason)
            await fresh.edit(position=position, reason=audit_reason)
        except discord.Forbidden:
            await self._safe_followup(
                interaction, ui.error_embed("Discord denied that — my role may sit below the channel's.")
            )
            return
        except discord.HTTPException as exc:
            await self._safe_followup(interaction, ui.error_embed(f"Discord rejected the request: `{exc}`"))
            return
        remapped = await self._remap_channel(guild.id, old_id, fresh.id)
        cfg = self.bot.config.for_guild(guild.id)
        done = ui.base_embed(
            title="💥 Channel nuked",
            description=self._pick_nuke_message(interaction.user.mention)
            + (f"\n**Reason:** {reason}" if reason else ""),
            color=cfg.color,
        )
        done.timestamp = dt.datetime.now(dt.timezone.utc)
        image_file = None
        if cfg.get("moderation.nuke_media", True):
            path = self._random_nuke_image()
            if path is not None:
                safe_name = f"nuke{path.suffix.lower()}"
                image_file = discord.File(str(path), filename=safe_name)
                done.set_image(url=f"attachment://{safe_name}")
        try:
            if image_file is not None:
                await fresh.send(embed=done, file=image_file)
            else:
                await fresh.send(embed=done)
        except discord.HTTPException as exc:
            log.warning("Nuke banner failed to post in #%s: %s", fresh.name, exc)
            if image_file is not None:
                try:
                    await fresh.send(embed=done)
                except discord.HTTPException:
                    pass
        note = f"Nuked and recreated {fresh.mention}."
        if remapped:
            note += f"\nRepointed **{remapped}** setting(s) at the new channel."
        await self._safe_followup(interaction, ui.ok_embed(note))
        await self._audit(
            guild,
            interaction.user,
            "Channel nuked",
            f"#{target.name} (`{target.id}`) → {fresh.mention} (`{fresh.id}`)"
            + (f"\nReason: {reason}" if reason else ""),
        )

    async def _remap_channel(self, guild_id: int, old_id: int, new_id: int) -> int:
        """Repoint every stored reference to a channel after it was recreated.

        Returns how many references were updated. Covers audit, economy,
        starboard, event and season destinations and the protected-channel list.
        """
        cfg = self.bot.config.for_guild(guild_id)
        changed = 0
        for key in (
            "general.log_channel",
            "economy.channel",
            "starboard.channel",
            "events.channel",
            "seasons.channel",
        ):
            if int(cfg.get(key) or 0) == old_id:
                cfg.set(key, new_id)
                changed += 1
        protected = list(cfg.get("moderation.protected_channels", []) or [])
        if old_id in protected:
            cfg.set("moderation.protected_channels", [new_id if c == old_id else c for c in protected])
            changed += 1
        if changed:
            self.bot.config.storage.mark_dirty(guild_id)
            await self.bot.config.save(guild_id)
        return changed

    @staticmethod
    def _random_nuke_image() -> Optional[Path]:
        """A random image file from assets/nuke/, or None if the folder's empty.

        Read fresh every call so images can be added or swapped without a restart.
        """
        try:
            candidates = [
                p for p in NUKE_ASSETS_DIR.iterdir() if p.is_file() and p.suffix.lower() in NUKE_IMAGE_EXTS
            ]
        except OSError:
            return None
        return random.choice(candidates) if candidates else None

    @staticmethod
    def _pick_nuke_message(mention: str) -> str:
        """A random Android 21-themed line with the moderator mention folded in."""
        return random.choice(NUKE_MESSAGES).replace("{user}", mention)

    @staticmethod
    async def _safe_followup(interaction: discord.Interaction, embed: discord.Embed) -> None:
        """Followups fail if the channel they were sent from is gone — that's fine."""
        try:
            await interaction.followup.send(embed=embed, ephemeral=True)
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation(bot))
