"""Seasons & Hall of Fame — a recurring, no-reset competition.

Every `seasons.length_days` (default 30) the bot snapshots the standings ranked
by **net worth** (wallet + bank), crowns the #1, enshrines the top finishers in
a permanent Hall of Fame, then rolls into the next season. Nothing resets — only
the recognition is periodic, so end-of-season robbing wars decide the crown.

Season state lives on the economy guild doc under "season"; the cadence is stored
there too, so restarts and offline gaps never double-roll.
"""

from __future__ import annotations
import datetime as dt
import logging
from typing import Any, Dict, List, Optional
import discord
from discord import app_commands
from discord.ext import commands, tasks
from szofie import badges, nyx, nyx_reports, ui

log = logging.getLogger("szofie.seasons")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse(value: Optional[str]) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


class Seasons(commands.Cog):
    """Runs the seasonal competition and its Hall of Fame."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.econ = bot.economy
        self.season_loop.start()

    def cog_unload(self) -> None:
        self.season_loop.cancel()

    def cfg(self, guild_id: int):
        return self.bot.config.for_guild(guild_id)

    def money(self, cfg, amount: int) -> str:
        name = cfg.get("economy.currency_name", "donuts")
        emoji = cfg.get("economy.currency_emoji", "🍩")
        return f"{emoji} **{ui.format_donuts(int(amount))}** {name}"

    @staticmethod
    def _net(u: Dict[str, Any]) -> int:
        return int(u.get("donuts", 0)) + int(u.get("bank", 0))

    def _state(self, guild_id: int) -> Dict[str, Any]:
        doc = self.econ.store.load(guild_id)
        s = doc.get("season")
        if not isinstance(s, dict):
            s = {"no": 1, "started_at": None, "hall_of_fame": []}
            doc["season"] = s
        s.setdefault("no", 1)
        s.setdefault("hall_of_fame", [])
        return s

    def _channel(self, guild: discord.Guild, cfg) -> Optional[discord.abc.Messageable]:
        for key in ("seasons.channel", "events.channel", "economy.channel"):
            cid = cfg.get(key)
            ch = guild.get_channel(int(cid)) if cid else None
            if ch is not None:
                return ch
        return guild.system_channel

    def _ranked(self, guild_id: int):
        """(uid, net) for every player with a positive balance, richest first.
        Users on the hidden list never appear (so they can't rank or be crowned)."""
        hidden = set()
        rows = [
            (uid, self._net(u)) for uid, u in self.econ.all_users(guild_id).items() if int(uid) not in hidden
        ]
        rows = [(uid, net) for uid, net in rows if net > 0]
        rows.sort(key=lambda r: -r[1])
        return rows

    def _public_ranked(self, guild_id: int):
        """Visible standings omit NYX, without changing eligibility or prizes."""
        hidden = set()
        rows = [
            (uid, entry["wealth"])
            for uid, user in self.econ.all_users(guild_id).items()
            if int(uid) not in hidden
            for entry in [nyx.leaderboard_entry(user)]
            if entry is not None and entry["wealth"] > 0
        ]
        rows.sort(key=lambda row: -row[1])
        return rows

    def _public_hidden(self, guild_id: int):
        """Hide active fields from current and historical public podiums only."""
        hidden = {str(uid) for uid in set()}
        hidden.update((str(uid) for uid, user in self.econ.all_users(guild_id).items() if nyx.active(user)))
        return hidden

    @tasks.loop(minutes=30)
    async def season_loop(self) -> None:
        now = _now()
        seen: set[int] = set()
        for guild in list(self.bot.guilds):
            gid = guild.id
            canonical = self.econ.store.canonical_id(gid)
            if canonical in seen:
                continue
            seen.add(canonical)
            cfg = self.cfg(gid)
            if not cfg.get("economy.enabled", True) or not cfg.get("seasons.enabled", True):
                continue
            s = self._state(gid)
            length = float(cfg.get("seasons.length_days", 30)) * 86400
            started = _parse(s.get("started_at"))
            if started is None:
                s["started_at"] = now.isoformat()
                self.econ.store.mark_dirty(gid)
                continue
            if (now - started).total_seconds() < length:
                continue
            s["started_at"] = now.isoformat()
            try:
                await self._rollover(guild, cfg, s)
            except Exception:
                log.exception("season rollover failed for guild %s", gid)
            await self.econ.save(gid)

    @season_loop.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()

    async def _rollover(self, guild: discord.Guild, cfg, s: Dict[str, Any]) -> None:
        ranked = self._ranked(guild.id)
        season_no = int(s.get("no", 1))
        if not ranked:
            s["no"] = season_no + 1
            return
        champ_id, champ_net = ranked[0]
        top3 = [{"user": str(uid), "net": net} for uid, net in ranked[:3]]
        cu = self.econ.user(guild.id, int(champ_id), int(cfg.get("economy.starting_balance", 100)))
        cu.setdefault("titles", {})["champion"] = 1
        cu["season_wins"] = int(cu.get("season_wins", 0)) + 1
        badges.grant(cu, "champion")
        prize = int(cfg.get("seasons.prize", 0))
        if prize > 0:
            cu["donuts"] = int(cu.get("donuts", 0)) + prize
        s.setdefault("hall_of_fame", []).append(
            {
                "season": season_no,
                "champion": str(champ_id),
                "net": champ_net,
                "top3": top3,
                "ended": _now().isoformat(),
                "wins": cu["season_wins"],
            }
        )
        s["no"] = season_no + 1
        channel = self._channel(guild, cfg)
        if channel is not None:
            wins = cu["season_wins"]
            medals = ["🥇", "🥈", "🥉"]
            hidden = self._public_hidden(guild.id)
            public_top3 = [row for row in top3 if row["user"] not in hidden]
            board = "\n".join(
                (
                    f"{medals[i]} <@{r['user']}> — {self.money(cfg, r['net'])}"
                    for i, r in enumerate(public_top3)
                )
            )
            desc = (
                f"🏆 **Season {season_no} is over!**\n\nYour champion: <@{champ_id}>"
                + (f" (**×{wins}**)" if wins > 1 else "")
                + f" with {self.money(cfg, champ_net)}!\n\n{board}\n\n"
                + (f"They pocket a {self.money(cfg, prize)} prize and " if prize > 0 else "They earn ")
                + f"the 🏆 **Season Champion** title, a badge, and a place in the Hall of Fame. **Season {season_no + 1}** starts now — get grinding (and guarding your vault)."
            )
            content = f"<@{champ_id}>"
            if str(champ_id) in hidden:
                content = None
                desc = (
                    f"🏆 **Season {season_no} is over!**\n\n"
                    + (f"{board}\n\n" if board else "")
                    + f"Season recognition and prizes have been awarded. **Season {season_no + 1}** starts now — get grinding (and guarding your vault)."
                )
            try:
                embed = ui.base_embed(title="🏆 Hall of Fame", description=desc, color=ui.COLOR_OK)
                message = await channel.send(content=content, embed=embed)
                registry = getattr(self.bot, "nyx_reports", None)
                if isinstance(registry, nyx_reports.PublicReports):
                    await registry.register(message, nyx_reports.payload_subjects([embed], content or ""))
            except discord.HTTPException:
                log.warning("could not post season result")

    @app_commands.command(name="season", description="Current standings or past season champions.")
    @app_commands.describe(view="Show current standings or Hall of Fame history")
    @app_commands.choices(
        view=[
            app_commands.Choice(name="Current standings", value="current"),
            app_commands.Choice(name="Hall of Fame", value="history"),
        ]
    )
    async def season(
        self, interaction: discord.Interaction, view: Optional[app_commands.Choice[str]] = None
    ) -> None:
        if interaction.guild_id is None:
            await ui.respond(
                interaction, embed=ui.error_embed("Seasons only exist inside a server."), ephemeral=True
            )
            return
        await ui.defer_response(interaction)
        cfg = self.cfg(interaction.guild_id)
        if view is not None and view.value == "history":
            history = self._history_embed(interaction.guild_id, cfg)
            interaction.extras["szofie_nyx_public_subjects"] = nyx_reports.payload_subjects([history])
            await ui.respond(interaction, embed=history)
            return
        if not cfg.get("seasons.enabled", True):
            await ui.respond(
                interaction, embed=ui.warn_embed("Seasons are switched off on this server."), ephemeral=True
            )
            return
        s = self._state(interaction.guild_id)
        season_no = int(s.get("no", 1))
        started = _parse(s.get("started_at"))
        length = float(cfg.get("seasons.length_days", 30)) * 86400
        ranked = self._public_ranked(interaction.guild_id)
        interaction.extras["szofie_nyx_public_subjects"] = [int(uid) for uid, _ in ranked[:10]]
        embed = ui.base_embed(
            title=f"🏆 Season {season_no}",
            description="Ranked by **net worth** (wallet + bank). Rob, grind, and guard your lead — whoever's on top when the season ends takes the crown.",
            color=cfg.color,
        )
        fields = []
        if ranked:
            medals = ["🥇", "🥈", "🥉"]
            lines = []
            for i, (uid, net) in enumerate(ranked[:10]):
                rank = medals[i] if i < 3 else f"**{i + 1}.**"
                lines.append(f"{rank} <@{uid}> — {self.money(cfg, net)}")
            fields.append(("Standings", "\n".join(lines), False))
        else:
            fields.append(("Standings", "*Nobody's on the board yet.*", False))
        if started is not None:
            ends = int(started.timestamp() + length)
            fields.append(("Season ends", f"<t:{ends}:R> (<t:{ends}:f>)", False))
        pages = ui.field_pages(embed.title, embed.description, fields, color=cfg.color)
        for page in pages:
            page.set_footer(text="See past champions with /season view:history")

        def page_guard(_index):
            users = self.econ.store.load(interaction.guild_id).get("users", {})
            if any((nyx.active(users.get(str(uid), {})) for uid, _ in ranked[:10])):
                return nyx.censored_embed()
            return None

        await ui.respond(
            interaction,
            embed=page_guard(0) or pages[0],
            view=ui.Paginator(pages, interaction.user.id, page_guard=page_guard) if len(pages) > 1 else None,
        )

    def _history_embed(self, guild_id: int, cfg) -> discord.Embed:
        s = self._state(guild_id)
        hof: List[Dict[str, Any]] = s.get("hall_of_fame", []) or []
        hidden = self._public_hidden(guild_id)
        hof = [rec for rec in hof if str(rec.get("champion")) not in hidden]
        embed = ui.base_embed(title="🏆 Hall of Fame", color=cfg.color)
        if not hof:
            embed.description = "No champions yet — the first season is still being fought over. Check `/season` for the current standings."
        else:
            lines = []
            for rec in reversed(hof[-15:]):
                wins = rec.get("wins")
                tag = f"  (×{wins})" if wins and wins > 1 else ""
                lines.append(
                    f"🏆 **Season {rec.get('season')}** — <@{rec.get('champion')}> · {self.money(cfg, rec.get('net', 0))}{tag}"
                )
            embed.description = "\n".join(lines)
        return embed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Seasons(bot))
