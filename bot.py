"""Szofie — a multirole Discord economy and community bot."""

from __future__ import annotations
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Optional, Set
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from szofie import __version__, ui
from szofie.checks import Denied
from szofie.config import ConfigManager
from szofie.economy import Economy
from szofie.ledger import Ledger
from szofie.nyx_reports import PublicReports
from szofie.storage import Storage
from szofie.ui import error_embed

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s  %(levelname)-8s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("szofie")


class _AutocompleteRaceFilter(logging.Filter):
    """Silence the unactionable 'Unknown interaction' (10062) spam discord.py logs
    when a user types fast and Discord expires an autocomplete before we answer.
    Genuine autocomplete errors (anything but a 10062 NotFound) still get through."""

    def filter(self, record: logging.LogRecord) -> bool:
        if "autocomplete" not in record.getMessage():
            return True
        exc = record.exc_info[1] if record.exc_info else None
        if isinstance(exc, discord.NotFound) and exc.code == 10062:
            return False
        return True


logging.getLogger("discord.app_commands.tree").addFilter(_AutocompleteRaceFilter())
PLACEHOLDER_ID = 123456789012345678
COGS = (
    "cogs.general",
    "cogs.moderation",
    "cogs.starboard",
    "cogs.fun",
    "cogs.economy",
    "cogs.thor",
    "cogs.satellite",
    "cogs.imperial_star_destroyer",
    "cogs.death_star",
    "cogs.space",
    "cogs.coalitions",
    "cogs.countries",
    "cogs.blackjack",
    "cogs.loans",
    "cogs.fishing",
    "cogs.continuity",
    "cogs.trivia",
    "cogs.events",
    "cogs.seasons",
    "cogs.settings",
    "cogs.patchnotes",
)


def _parse_ids(raw: Optional[str]) -> Set[int]:
    if not raw:
        return set()
    out: Set[int] = set()
    for chunk in raw.replace(",", " ").split():
        digits = "".join((ch for ch in chunk if ch.isdigit()))
        if digits:
            out.add(int(digits))
    return out


class Szofie(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(
            command_prefix=self._dynamic_prefix,
            intents=intents,
            help_command=None,
            activity=discord.Activity(type=discord.ActivityType.listening, name="/help"),
        )
        self.storage = Storage()
        self.config = ConfigManager(self.storage)
        self.economy = Economy()
        self.ledger = Ledger()
        self.nyx_reports = PublicReports(self, ROOT / "data" / "nyx_reports.json")
        self.owner_ids_set: Set[int] = _parse_ids(os.getenv("OWNER_IDS"))
        self.dev_guild_ids: Set[int] = _parse_ids(os.getenv("GUILD_ID"))
        self._autosave_task: Optional[asyncio.Task] = None

    async def _dynamic_prefix(self, bot: "Szofie", message: discord.Message):
        default = commands.when_mentioned(bot, message)
        if message.guild is None:
            return default + ["!"]
        prefix = self.config.for_guild(message.guild.id).get("general.prefix", "!")
        return default + [prefix]

    async def setup_hook(self) -> None:
        for cog in COGS:
            try:
                await self.load_extension(cog)
                log.info("Loaded %s", cog)
            except Exception:
                log.exception("Failed to load %s", cog)
        self.tree.on_error = self.on_tree_error
        await self._sync_commands()
        self._autosave_task = asyncio.create_task(self._autosave_loop())

    async def _sync_commands(self) -> None:
        """Register slash commands, preferring the dev guild(s) for instant updates.

        GUILD_ID may name several servers; each is synced independently so the bot
        can run on multiple servers at once with instant command updates. A wrong or
        stale ID must never take the whole bot down, so if EVERY guild sync fails the
        bot degrades to a global one instead of propagating out of setup_hook (which
        would abort login).
        """
        if self.dev_guild_ids:
            any_ok = False
            for gid in sorted(self.dev_guild_ids):
                guild = discord.Object(id=gid)
                try:
                    self.tree.copy_global_to(guild=guild)
                    synced = await self.tree.sync(guild=guild)
                except discord.HTTPException as exc:
                    log.warning(
                        "Could not sync to guild %s (%s). Check that this is a real server ID and that Szofie has been invited there.",
                        gid,
                        exc,
                    )
                else:
                    any_ok = True
                    log.info("Synced %d commands to guild %s", len(synced), gid)
            if any_ok:
                await self._clear_global_commands()
                return
            log.warning("No guild sync succeeded — falling back to a global sync.")
        synced = await self.tree.sync()
        log.info(
            "Synced %d commands globally — these can take up to an hour to appear. Set GUILD_ID in .env for instant updates.",
            len(synced),
        )

    async def _clear_global_commands(self) -> None:
        """Drop any globally-registered commands.

        A global registration and a guild registration of the same command both
        show up in the picker, so Discord lists everything twice. That happens
        the moment one run falls back to a global sync (a bad GUILD_ID, say) and
        a later run succeeds against the guild — the stale globals linger until
        something removes them.
        """
        try:
            existing = await self.tree.fetch_commands()
        except discord.HTTPException as exc:
            log.debug("Could not list global commands (%s); skipping cleanup.", exc)
            return
        if not existing:
            return
        self.tree.clear_commands(guild=None)
        try:
            await self.tree.sync()
        except discord.HTTPException as exc:
            log.warning("Could not clear %d global command(s): %s", len(existing), exc)
            return
        log.info("Cleared %d stale global command(s) that were duplicating the guild set.", len(existing))

    async def _autosave_loop(self) -> None:
        """Flush dirty settings and gameplay state every 30 seconds."""
        try:
            while True:
                await asyncio.sleep(30)
                try:
                    await self.config.flush()
                except Exception:
                    log.exception("Config autosave failed")
                try:
                    await self.economy.flush()
                except Exception:
                    log.exception("Economy autosave failed")
        except asyncio.CancelledError:
            pass

    async def on_ready(self) -> None:
        log.info("Szofie v%s online as %s (%s)", __version__, self.user, self.user.id)
        for guild in self.guilds:
            log.info("  in %r  ->  GUILD_ID=%s  (owner %s)", guild.name, guild.id, guild.owner_id)
        if not self.guilds:
            log.warning("Szofie isn't in any server yet — use the invite link to add it.")
        if not self.owner_ids_set:
            log.warning("OWNER_IDS is empty — bot-owner administration is disabled.")
        elif PLACEHOLDER_ID in self.owner_ids_set:
            log.warning(
                "OWNER_IDS contains a placeholder. Set your actual user ID to enable bot-owner administration."
            )
        if PLACEHOLDER_ID in self.dev_guild_ids:
            log.warning(
                "GUILD_ID still contains the example value from .env.example — replace it with real server ID(s) from the list above."
            )

    async def close(self) -> None:
        if self._autosave_task:
            self._autosave_task.cancel()
        try:
            await self.config.flush()
        except Exception:
            log.exception("Final config flush failed")
        try:
            await self.economy.flush()
        except Exception:
            log.exception("Final economy flush failed")
        await super().close()

    async def log_action(self, guild: discord.Guild, embed: discord.Embed) -> None:
        """Send an audit entry to the configured log channel, if any."""
        cfg = self.config.for_guild(guild.id)
        channel_id = cfg.get("general.log_channel")
        if not channel_id:
            return
        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.abc.Messageable):
            return
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            log.warning("Could not write to log channel %s", channel_id)

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        """Prefix commands are owner-only utilities, so ordinary chatter that
        happens to start with the prefix is not an error worth logging."""
        if isinstance(error, (commands.CommandNotFound, commands.CheckFailure)):
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(f"Missing argument: `{error.param.name}`")
            return
        log.exception("Prefix command error", exc_info=error)

    async def on_tree_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, Denied):
            message = error.reason
        elif isinstance(error, app_commands.CommandOnCooldown):
            message = f"Slow down — try again in {error.retry_after:.1f}s."
        elif isinstance(error, app_commands.TransformerError):
            message = (
                "I couldn't resolve one of those options. Pick a valid user or value from Discord's list."
            )
        elif isinstance(error, app_commands.CheckFailure):
            message = "You aren't allowed to run that here."
        elif (
            isinstance(error, app_commands.CommandInvokeError)
            and isinstance(error.original, discord.NotFound)
            and (error.original.code == 10062)
        ):
            command_name = getattr(interaction.command, "qualified_name", "unknown")
            log.warning(
                "Interaction expired before command response (%s); check connection or host stalls.",
                command_name,
            )
            return
        elif isinstance(error, app_commands.CommandInvokeError) and isinstance(
            error.original, discord.Forbidden
        ):
            message = "Discord refused that — Szofie is missing a permission. Check the bot's role position and channel overrides."
        else:
            log.exception("Command error", exc_info=error)
            message = "Something went wrong. Check the console for details."
        embed = error_embed(message)
        try:
            await ui.respond(interaction, embed=embed, ephemeral=True)
        except discord.HTTPException:
            pass


def main() -> None:
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token or token.startswith("paste-"):
        print(
            "\n  DISCORD_TOKEN is not set.\n  Copy .env.example to .env and paste your bot token into it.\n",
            file=sys.stderr,
        )
        raise SystemExit(1)
    bot = Szofie()
    try:
        bot.run(token, log_handler=None)
    except discord.LoginFailure:
        print("\n  Discord rejected that token. Reset it in the Developer Portal.\n", file=sys.stderr)
        raise SystemExit(1)
    except discord.PrivilegedIntentsRequired:
        print(
            "\n  Enable the MESSAGE CONTENT and SERVER MEMBERS intents:\n  Developer Portal -> your app -> Bot -> Privileged Gateway Intents.\n",
            file=sys.stderr,
        )
        raise SystemExit(1)


if __name__ == "__main__":
    main()
