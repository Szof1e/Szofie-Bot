"""Permission helpers shared by every cog.

Szofie layers three gates on top of Discord's own permissions:
  1. bot owners (OWNER_IDS) always pass;
  2. an optional configured role (roles.mod) passes;
  3. otherwise the member needs the underlying Discord permission.
"""

from __future__ import annotations
from typing import Optional
import discord
from discord import app_commands


class Denied(app_commands.CheckFailure):
    """Raised with a human-readable reason so the error handler can show it."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def config_access_check(interaction: discord.Interaction) -> bool:
    guild_only_check(interaction)
    if is_owner(interaction):
        return True
    member = interaction.user
    if isinstance(member, discord.Member):
        if member.guild_permissions.manage_guild:
            return True
        cfg = _config(interaction)
        if has_role_id(member, cfg.get("roles.admin")) or has_role_id(member, cfg.get("roles.mod")):
            return True
    raise Denied("You need Manage Server or the configured admin/mod role to use `/config`.")


def is_owner(interaction: discord.Interaction) -> bool:
    return interaction.user.id in getattr(interaction.client, "owner_ids_set", set())


def is_privileged_owner(interaction: discord.Interaction) -> bool:
    """Bot owner (OWNER_IDS) or the server owner.

    Used to lock the economy config. Falls back to the guild owner so the real
    owner keeps control even when OWNER_IDS isn't configured in .env.
    """
    if is_owner(interaction):
        return True
    guild = interaction.guild
    return guild is not None and interaction.user.id == guild.owner_id


def has_role_id(member: discord.Member, role_id: Optional[int]) -> bool:
    if not role_id:
        return False
    return any((role.id == role_id for role in member.roles))


def _config(interaction: discord.Interaction):
    return interaction.client.config.for_guild(interaction.guild_id)


def guild_only_check(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        raise Denied("Szofie only works inside a server.")
    return True


def nuke_check(interaction: discord.Interaction) -> bool:
    """Gate for protected channel nukes."""
    guild_only_check(interaction)
    if is_owner(interaction):
        return True
    cfg = _config(interaction)
    if cfg.get("moderation.nuke_owner_only", False):
        raise Denied("`/nuke` is restricted to the bot owner on this server.")
    member = interaction.user
    if isinstance(member, discord.Member):
        if member.guild_permissions.manage_channels:
            return True
        if has_role_id(member, cfg.get("roles.mod")):
            return True
    raise Denied("You need **Manage Channels** or the configured mod role.")


def admin_check(interaction: discord.Interaction) -> bool:
    """Gate for changing bot configuration."""
    guild_only_check(interaction)
    if is_owner(interaction):
        return True
    member = interaction.user
    if isinstance(member, discord.Member) and member.guild_permissions.manage_guild:
        return True
    raise Denied("You need **Manage Server** to change Szofie's settings.")


def owner_only_check(interaction: discord.Interaction) -> bool:
    """Strict gate: ONLY a bot owner (OWNER_IDS) passes — not admins, not the
    server owner. Used for the private config audit and owner utilities."""
    if is_owner(interaction):
        return True
    raise Denied("🔒 **Owner-only.** Only the bot owner can touch Szofie's configuration.")


def feature_enabled(interaction: discord.Interaction, feature: str) -> bool:
    cfg = _config(interaction)
    if not cfg.get(f"{feature}.enabled", True):
        raise Denied(
            f"The **{feature}** module is disabled here (`/config set {feature}.enabled true` to turn it back on)."
        )
    return True
