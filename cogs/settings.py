"""Live configuration from inside Discord.

`/config set <key> <value>` writes straight into the schema in szofie/config.py,
so anything with a default there is tunable without touching the code.
"""

from __future__ import annotations
import copy
import io
import json
import logging
import math
from typing import Any, List, Optional
import discord
from discord import app_commands
from discord.ext import commands
from szofie import checks, config_audit, ui
from szofie.config import DEFAULTS, OWNER_ONLY_KEYS, OWNER_ONLY_SECTIONS, ConfigManager, is_owner_only

log = logging.getLogger("szofie.settings")
SECTIONS = list(DEFAULTS.keys())


async def key_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    try:
        checks.config_access_check(interaction)
    except checks.Denied:
        return []
    needle = current.lower()
    keys = ConfigManager.schema_keys()
    hits = [k for k in keys if needle in k.lower()]
    return [app_commands.Choice(name=k, value=k) for k in hits[:25]]


def format_value(value: Any) -> str:
    if value is None:
        return "*(not set)*"
    if isinstance(value, bool):
        return "`true`" if value else "`false`"
    if isinstance(value, list):
        return "*(empty)*" if not value else " ".join((f"`{v}`" for v in value))
    return f"`{value}`"


class Settings(commands.Cog):
    """Configure Szofie."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _audit_config(
        self,
        interaction: discord.Interaction,
        action: str,
        detail: str,
        *,
        key: Optional[str] = None,
        before: Any = None,
        after: Any = None,
    ) -> None:
        """Log a config change to the audit channel so tampering leaves a trail
        even if the value is changed back afterwards. The same event is written
        to a durable on-disk log (data/config_audit.jsonl) that Discord users
        can't reach — so deleting the audit channel no longer erases the trail."""
        config_audit.record(
            action=action,
            actor_id=interaction.user.id,
            actor_name=str(interaction.user),
            guild_id=interaction.guild_id,
            key=key,
            before=before,
            after=after,
            allowed=True,
            note=detail,
        )
        embed = ui.base_embed(title=f"⚙ {action}", color=ui.COLOR_WARN)
        embed.add_field(
            name="By", value=f"{interaction.user.mention} (`{interaction.user.id}`)", inline=False
        )
        embed.add_field(name="Change", value=detail[:1024], inline=False)
        await self.bot.log_action(interaction.guild, embed)

    def _audit_blocked(
        self,
        interaction: discord.Interaction,
        action: str,
        key: str,
        *,
        before: Any = None,
        after: Any = None,
    ) -> None:
        """Record a blocked attempt to touch an owner-only setting. These are the
        red flags: a non-owner probing the donut supply leaves a permanent mark."""
        config_audit.record(
            action=action,
            actor_id=interaction.user.id,
            actor_name=str(interaction.user),
            guild_id=interaction.guild_id,
            key=key,
            before=before,
            after=after,
            allowed=False,
            note="Blocked: owner-only setting.",
        )
        log.warning(
            "BLOCKED non-owner %s (%s) attempted %s on %s in guild %s.",
            interaction.user,
            interaction.user.id,
            action,
            key,
            interaction.guild_id,
        )

    def _owner_locked(self, interaction: discord.Interaction, key: str) -> bool:
        """True (and the interaction is answered) if a non-owner tried to touch
        an owner-only setting."""
        return is_owner_only(key) and (not checks.is_privileged_owner(interaction))

    @staticmethod
    def _restore_owner_settings(cfg, destination: dict) -> None:
        """Bulk staff operations cannot reset money policy or blind its audit."""
        current = cfg.raw()
        for section in OWNER_ONLY_SECTIONS:
            destination[section] = copy.deepcopy(current.get(section, DEFAULTS[section]))
        for path in OWNER_ONLY_KEYS:
            section, key = path.split(".", 1)
            destination.setdefault(section, {})[key] = copy.deepcopy(cfg.get(path))

    @staticmethod
    async def _settled(interaction: discord.Interaction, view: ui.ConfirmView) -> bool:
        """Wait on a confirmation and report whether it was approved."""
        timed_out = await view.wait()
        if timed_out:
            try:
                await interaction.edit_original_response(
                    embed=ui.warn_embed("Confirmation timed out — nothing was changed."), view=None
                )
            except discord.HTTPException:
                pass
            return False
        if view.value:
            checks.config_access_check(interaction)
        return bool(view.value)

    group = app_commands.Group(
        name="config", description="View and change Szofie's settings.", guild_only=True
    )

    @group.command(name="view", description="Show the current settings.")
    @app_commands.describe(section="Limit the output to one section")
    @app_commands.choices(section=[app_commands.Choice(name=s, value=s) for s in SECTIONS])
    @app_commands.check(checks.config_access_check)
    async def view(
        self, interaction: discord.Interaction, section: Optional[app_commands.Choice[str]] = None
    ) -> None:
        cfg = self.bot.config.for_guild(interaction.guild_id)
        wanted = [section.value] if section else SECTIONS
        pages: List[discord.Embed] = []
        for name in wanted:
            block = DEFAULTS.get(name, {})
            keys = [key for key in block if True]
            per_page = 10
            for start in range(0, len(keys), per_page):
                chunk = keys[start : start + per_page]
                part = start // per_page + 1
                parts = max(1, (len(keys) + per_page - 1) // per_page)
                suffix = f" · {part}/{parts}" if parts > 1 else ""
                embed = ui.base_embed(
                    title=f"⚙ Settings · {name}{suffix}",
                    description=f"Change any of these with `/config set {name}.<key> <value>`.",
                    color=cfg.color,
                )
                for key in chunk:
                    path = f"{name}.{key}"
                    value = cfg.get(path)
                    rendered = format_value(value)
                    if key == "log_channel" and value:
                        rendered = f"<#{value}>"
                    elif name == "roles" and key in {"dj", "mod"} and value:
                        rendered = f"<@&{value}>"
                    elif name == "moderation" and key == "protected_channels" and value:
                        rendered = " ".join((f"<#{v}>" for v in value))
                    if len(rendered) > 500:
                        rendered = rendered[:497] + "..."
                    embed.add_field(name=key, value=rendered, inline=True)
                pages.append(embed)
        if len(pages) == 1:
            await ui.respond(interaction, embed=pages[0], ephemeral=True)
        else:
            view = ui.Paginator(pages, interaction.user.id)
            await ui.respond(interaction, embed=pages[0], view=view, ephemeral=True)

    @group.command(name="set", description="Change one setting.")
    @app_commands.describe(
        key="The setting to change, e.g. starboard.threshold",
        value="The new value — use `none` to clear an optional setting",
    )
    @app_commands.autocomplete(key=key_autocomplete)
    @app_commands.check(checks.config_access_check)
    async def set_cmd(self, interaction: discord.Interaction, key: str, value: str) -> None:
        await ui.defer_response(interaction, ephemeral=True)
        key = key.strip()
        if self._owner_locked(interaction, key):
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    "**That setting is owner-only.** Economy policy and the audit-log destination can only be changed by the server or bot owner."
                ),
                ephemeral=True,
            )
            self._audit_blocked(
                interaction,
                "Setting changed",
                key,
                before=self.bot.config.for_guild(interaction.guild_id).get(key),
                after=value,
            )
            return
        ok, parsed, error = ConfigManager.coerce(key, value)
        if not ok:
            await ui.respond(
                interaction, embed=ui.error_embed(error or "Couldn't parse that value."), ephemeral=True
            )
            return
        problem = self._validate(key, parsed)
        if problem:
            await ui.respond(interaction, embed=ui.error_embed(problem), ephemeral=True)
            return
        cfg = self.bot.config.for_guild(interaction.guild_id)
        previous = cfg.get(key)
        cfg.set(key, parsed)
        await self.bot.config.save(interaction.guild_id)
        embed = ui.ok_embed(f"`{key}` updated.", title="Settings")
        embed.color = cfg.color
        embed.add_field(name="Before", value=format_value(previous)[:1024], inline=True)
        embed.add_field(name="After", value=format_value(parsed)[:1024], inline=True)
        if key == "general.embed_color":
            embed.set_footer(text="Embed colour applied.")
        await ui.respond(interaction, embed=embed, ephemeral=True)
        await self._audit_config(
            interaction,
            "Setting changed",
            f"`{key}`: {format_value(previous)} → {format_value(parsed)}",
            key=key,
            before=previous,
            after=parsed,
        )

    @staticmethod
    def _validate(key: str, value: Any) -> Optional[str]:
        if key == "starboard.threshold" and int(value) < 1:
            return "The starboard threshold must be at least 1."
        if key == "economy.casino_tour_games" and (not 1 <= int(value) <= 5):
            return "Casino Tour must require between 1 and 5 different games."
        if key == "economy.casino_tour_reward" and int(value) < 0:
            return "Casino Tour reward can't be negative."
        if key == "economy.casino_tour_streak_bonus_pct" and (not 0 <= int(value) <= 100):
            return "Casino Tour streak bonus must be between 0% and 100%."
        if key == "economy.casino_tour_streak_cap" and (not 1 <= int(value) <= 365):
            return "Casino Tour streak cap must be between 1 and 365 days."
        if key == "economy.roulette_jackpot_contribution_pct" and (not 0 <= int(value) <= 10):
            return "Roulette progressive contribution must be between 0% and 10%."
        if key == "general.embed_color":
            raw = str(value).lstrip("#")
            try:
                int(raw, 16)
            except ValueError:
                return "Give a hex colour like `#B57EDC`."
            if len(raw) != 6:
                return "Hex colours need exactly 6 digits, like `#B57EDC`."
        if key == "general.prefix" and (not str(value) or len(str(value)) > 5):
            return "The prefix must be 1-5 characters."
        return None

    @group.command(name="reset", description="Restore a setting (or everything) to its default.")
    @app_commands.describe(key="The setting to reset — leave empty to reset everything")
    @app_commands.autocomplete(key=key_autocomplete)
    @app_commands.check(checks.config_access_check)
    async def reset(self, interaction: discord.Interaction, key: Optional[str] = None) -> None:
        await ui.defer_response(interaction, ephemeral=True)
        cfg = self.bot.config.for_guild(interaction.guild_id)
        if key:
            key = key.strip()
            if self._owner_locked(interaction, key):
                await ui.respond(
                    interaction, embed=ui.error_embed("**That setting is owner-only.**"), ephemeral=True
                )
                self._audit_blocked(interaction, "Setting reset", key)
                return
            try:
                default = ConfigManager.default_for(key)
            except KeyError:
                await ui.respond(
                    interaction, embed=ui.error_embed(f"`{key}` isn't a valid setting."), ephemeral=True
                )
                return
            previous = cfg.get(key)
            cfg.set(key, copy.deepcopy(default))
            await self.bot.config.save(interaction.guild_id)
            await ui.respond(
                interaction, embed=ui.ok_embed(f"`{key}` reset to {format_value(default)}."), ephemeral=True
            )
            await self._audit_config(
                interaction,
                "Setting reset",
                f"`{key}` → {format_value(default)}",
                key=key,
                before=previous,
                after=default,
            )
            return
        preview = ui.warn_embed(
            "This resets **every** Szofie setting on this server to its default.", title="Reset all settings"
        )
        view = ui.ConfirmView(interaction.user.id, danger_label="Reset everything")
        await ui.respond(interaction, embed=preview, view=view, ephemeral=True)
        if not await self._settled(interaction, view):
            return
        raw = cfg.raw()
        protected = not checks.is_privileged_owner(interaction)
        merged = copy.deepcopy(DEFAULTS)
        if protected:
            self._restore_owner_settings(cfg, merged)
        if "_migrations" in raw:
            merged["_migrations"] = copy.deepcopy(raw["_migrations"])
        raw.clear()
        raw.update(merged)
        await self.bot.config.save(interaction.guild_id)
        await interaction.edit_original_response(
            embed=ui.ok_embed(
                "Every setting is back to its default."
                + (" (owner-only settings left untouched.)" if protected else "")
            ),
            view=None,
        )
        await self._audit_config(interaction, "All settings reset", "Restored defaults.")

    @group.command(name="export", description="Download this server's settings as JSON.")
    @app_commands.check(checks.config_access_check)
    async def export(self, interaction: discord.Interaction) -> None:
        cfg = self.bot.config.for_guild(interaction.guild_id)
        public = copy.deepcopy(cfg.raw())
        payload = json.dumps(public, indent=2, ensure_ascii=False)
        buffer = io.BytesIO(payload.encode("utf-8"))
        file = discord.File(buffer, filename=f"szofie-{interaction.guild_id}.json")
        await ui.respond(
            interaction,
            embed=ui.ok_embed("Here's your full server configuration."),
            file=file,
            ephemeral=True,
        )

    @group.command(name="import", description="Restore settings from an exported JSON file.")
    @app_commands.describe(file="A file produced by /config export")
    @app_commands.check(checks.config_access_check)
    async def import_cmd(self, interaction: discord.Interaction, file: discord.Attachment) -> None:
        await ui.defer_response(interaction, ephemeral=True)
        if file.size > 1000000:
            await ui.respond(
                interaction,
                embed=ui.error_embed("That file is too large to be a Szofie config."),
                ephemeral=True,
            )
            return
        try:
            raw_bytes = await file.read()
            incoming = json.loads(raw_bytes.decode("utf-8"))
        except (discord.HTTPException, UnicodeDecodeError, json.JSONDecodeError) as exc:
            await ui.respond(
                interaction, embed=ui.error_embed(f"Couldn't read that file: `{exc}`"), ephemeral=True
            )
            return
        if not isinstance(incoming, dict) or not set(incoming) & set(DEFAULTS):
            await ui.respond(
                interaction,
                embed=ui.error_embed("That doesn't look like a Szofie config export."),
                ephemeral=True,
            )
            return
        for section, block in incoming.items():
            if section not in DEFAULTS:
                continue
            if not isinstance(block, dict):
                await ui.respond(
                    interaction, embed=ui.error_embed("Config sections must be objects."), ephemeral=True
                )
                return
            for key, value in block.items():
                if key not in DEFAULTS[section]:
                    continue
                path = f"{section}.{key}"
                expected = DEFAULTS[section][key]
                valid = (
                    value is None or type(value) is int
                    if expected is None
                    else type(value) in (int, float)
                    if type(expected) is float
                    else type(value) is type(expected)
                )
                if not valid or (type(value) is float and (not math.isfinite(value))):
                    await ui.respond(
                        interaction, embed=ui.error_embed(f"Invalid value type for `{path}`."), ephemeral=True
                    )
                    return
                error = self._validate(path, value)
                if error:
                    await ui.respond(interaction, embed=ui.error_embed(error), ephemeral=True)
                    return
        preview = ui.warn_embed(
            f"This replaces every current setting with the contents of `{file.filename}`, including every configurable server setting.",
            title="Import settings",
        )
        view = ui.ConfirmView(interaction.user.id, danger_label="Import and overwrite")
        await ui.respond(interaction, embed=preview, view=view, ephemeral=True)
        if not await self._settled(interaction, view):
            return
        cfg = self.bot.config.for_guild(interaction.guild_id)
        raw = cfg.raw()
        protected = not checks.is_privileged_owner(interaction)
        merged = copy.deepcopy(DEFAULTS)
        for section, block in incoming.items():
            if section not in DEFAULTS:
                continue
            for key, value in block.items():
                if key in DEFAULTS[section]:
                    merged[section][key] = copy.deepcopy(value)
        if protected:
            for section, block in incoming.items():
                if section not in DEFAULTS or not isinstance(block, dict):
                    continue
                for key, value in block.items():
                    path = f"{section}.{key}"
                    if key in DEFAULTS[section] and is_owner_only(path) and (value != cfg.get(path)):
                        self._audit_blocked(
                            interaction, "Setting import blocked", path, before=cfg.get(path), after=value
                        )
            self._restore_owner_settings(cfg, merged)
        if "_migrations" in raw:
            merged["_migrations"] = copy.deepcopy(raw["_migrations"])
        raw.clear()
        raw.update(merged)
        await self.bot.config.save(interaction.guild_id)
        await interaction.edit_original_response(
            embed=ui.ok_embed(
                "Configuration imported." + (" (owner-only settings kept.)" if protected else "")
            ),
            view=None,
        )
        await self._audit_config(interaction, "Settings imported", f"From `{file.filename}`")

    @group.command(name="audit", description="Review recent config changes and blocked attempts.")
    @app_commands.describe(count="How many recent entries to show (1-25, default 15)")
    @app_commands.check(checks.owner_only_check)
    async def audit(
        self, interaction: discord.Interaction, count: app_commands.Range[int, 1, 25] = 15
    ) -> None:
        entries = config_audit.tail(count, guild_id=interaction.guild_id)
        if not entries:
            await ui.respond(
                interaction,
                embed=ui.ok_embed(
                    "No config changes recorded yet on this server. Every future `/config` change — and every blocked attempt — will be logged here."
                ),
                ephemeral=True,
            )
            return
        lines: List[str] = []
        for e in reversed(entries):
            ts = str(e.get("ts", ""))[:19].replace("T", " ")
            actor = e.get("actor_name") or "?"
            aid = e.get("actor_id")
            mark = "✅" if e.get("allowed", True) else "⛔ BLOCKED"
            key = e.get("key")
            if e.get("allowed", True) and e.get("key") is not None:
                change = f"`{key}`: `{e.get('before')}` → `{e.get('after')}`"
            elif e.get("key") is not None:
                change = f"tried `{key}`"
            else:
                change = e.get("note") or e.get("action", "")
            lines.append(f"{mark} `{ts}` **{actor}** (`{aid}`)\n• {e.get('action', '')} — {change}")
        body = "\n\n".join(lines)
        if len(body) > 4000:
            body = body[:3990] + "\n…"
        embed = ui.base_embed(title="⚙ Config audit trail", color=ui.COLOR_WARN, description=body)
        embed.set_footer(text="Durable log — survives channel deletion (data/config_audit.jsonl).")
        await ui.respond(interaction, embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Settings(bot))
