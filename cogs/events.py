"""Random events — server-wide happenings that fire on a timer.

A background loop checks each guild and, once ``events.interval_hours`` of online
time has passed since the last one, fires a weighted-random event: a timed
buff/debuff (read by other cogs through :mod:`szofie.events`) or an instant
opportunity (a donut drop button, or a bounty on a random player). The cadence
is stored on the economy doc, so restarts and offline gaps never double-fire.
"""

from __future__ import annotations
import json
import logging
import random
from pathlib import Path
from typing import Optional
import discord
from discord import app_commands
from discord.ext import commands, tasks
from szofie import events as ev
from szofie import ui

log = logging.getLogger("szofie.events")
FORCE_FILE = Path(__file__).resolve().parent.parent / "data" / "force_event.json"


class DonutDropView(discord.ui.View):
    """A crate the first few clickers split. Grants a fixed share per winner,
    disables once the winners are in or the timeout passes."""

    def __init__(self, cog: "Events", guild_id: int, share: int, winners: int):
        super().__init__(timeout=1800)
        self.cog = cog
        self.guild_id = guild_id
        self.share = share
        self.winners = winners
        self.claimed: list[int] = []

    @discord.ui.button(label="Grab it!", style=discord.ButtonStyle.success, emoji="🍩")
    async def grab(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        uid = interaction.user.id
        if uid in self.claimed:
            await ui.respond(interaction, embed=ui.warn_embed("You already grabbed a share!"), ephemeral=True)
            return
        if len(self.claimed) >= self.winners:
            await ui.respond(
                interaction, embed=ui.warn_embed("Too slow — the crate's empty."), ephemeral=True
            )
            return
        self.claimed.append(uid)
        await interaction.response.defer()
        cfg = self.cog.cfg(self.guild_id)
        starting = int(cfg.get("economy.starting_balance", 100))
        u = self.cog.econ.user(self.guild_id, uid, starting)
        u["donuts"] = int(u.get("donuts", 0)) + self.share
        await self.cog.econ.save(self.guild_id)
        try:
            await self.cog.bot.ledger.record(
                self.guild_id,
                uid,
                self.share,
                "event-drop",
                after=int(u.get("donuts", 0)) + int(u.get("bank", 0)),
            )
        except Exception:
            log.exception("ledger record failed for donut drop")
        if len(self.claimed) >= self.winners:
            button.disabled = True
            self.stop()
        names = ", ".join((f"<@{c}>" for c in self.claimed))
        embed = ui.base_embed(
            title="🍩 Donut Drop!",
            description=f"A crate of donuts! First **{self.winners}** to grab it get {self.cog.money(cfg, self.share)} each.\n\n**Grabbed:** {names}"
            + ("\n\n*All gone!*" if button.disabled else ""),
            color=cfg.color,
        )
        await interaction.edit_original_response(embed=embed, view=self)
        await interaction.followup.send(
            embed=ui.ok_embed(f"You grabbed {self.cog.money(cfg, self.share)}!"), ephemeral=True
        )


class Events(commands.Cog):
    """Fires and announces server-wide random events."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.econ = bot.economy
        self.event_loop.start()

    def cog_unload(self) -> None:
        self.event_loop.cancel()

    def cfg(self, guild_id: int):
        return self.bot.config.for_guild(guild_id)

    def money(self, cfg, amount: int) -> str:
        name = cfg.get("economy.currency_name", "donuts")
        emoji = cfg.get("economy.currency_emoji", "🍩")
        return f"{emoji} **{ui.format_donuts(int(amount))}** {name}"

    def _pick_interval(self, cfg) -> float:
        """A fresh random gap (seconds) until the next event, in the configured hour range."""
        lo = float(cfg.get("events.interval_min_hours", 1))
        hi = float(cfg.get("events.interval_max_hours", 2))
        if hi < lo:
            lo, hi = (hi, lo)
        return random.uniform(lo, hi) * 3600

    def _channel(self, guild: discord.Guild, cfg) -> Optional[discord.abc.Messageable]:
        for key in ("events.channel", "economy.channel"):
            cid = cfg.get(key)
            ch = guild.get_channel(int(cid)) if cid else None
            if ch is not None:
                return ch
        return guild.system_channel

    @staticmethod
    def _forced_event() -> Optional[str]:
        """A one-shot forced event id from the control file (cleared on read)."""
        try:
            d = json.loads(FORCE_FILE.read_text())
        except (FileNotFoundError, ValueError, OSError):
            return None
        eid = d.get("event") if isinstance(d, dict) else None
        if eid:
            try:
                FORCE_FILE.unlink()
            except OSError:
                pass
        return eid if eid in ev.CATALOG_BY_ID else None

    @tasks.loop(minutes=10)
    async def event_loop(self) -> None:
        now = ev._now()
        forced = self._forced_event()
        seen: set[int] = set()
        for guild in list(self.bot.guilds):
            gid = guild.id
            canonical = self.econ.store.canonical_id(gid)
            if canonical in seen:
                continue
            seen.add(canonical)
            cfg = self.cfg(gid)
            if not cfg.get("economy.enabled", True) or not cfg.get("events.enabled", True):
                continue
            doc = self.econ.store.load(gid)
            st = ev.state(doc)
            if forced:
                if self._channel(guild, cfg) is not None:
                    try:
                        await self._fire(guild, cfg, doc, event=ev.CATALOG_BY_ID[forced])
                    except Exception:
                        log.exception("forced event %s failed for guild %s", forced, gid)
                    await self.econ.save(gid)
                    forced = None
                    continue
            last = ev._parse(st.get("last_at"))
            if last is None:
                st["last_at"] = now.isoformat()
                st["interval"] = self._pick_interval(cfg)
                self.econ.store.mark_dirty(gid)
                continue
            interval = float(st.get("interval") or self._pick_interval(cfg))
            if (now - last).total_seconds() < interval:
                continue
            st["last_at"] = now.isoformat()
            st["interval"] = self._pick_interval(cfg)
            try:
                await self._fire(guild, cfg, doc)
            except Exception:
                log.exception("event firing failed for guild %s", gid)
            await self.econ.save(gid)

    @event_loop.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()

    async def _fire(self, guild: discord.Guild, cfg, doc, event: "ev.Event | None" = None) -> None:
        channel = self._channel(guild, cfg)
        if channel is None:
            return
        if event is None:
            previous = ev.state(doc).get("last_event_id")
            pool = [candidate for candidate in ev.CATALOG if candidate.id != previous] or ev.CATALOG
            event = random.choices(pool, weights=[e.weight for e in pool])[0]
        ev.state(doc)["last_event_id"] = event.id
        if event.kind == "timed":
            ev.start_timed(doc, event.id, event.duration_min)
            until = ev.active_until(doc)
            embed = ui.base_embed(
                title=f"{event.emoji} {event.name}",
                description=f"{event.blurb}\n\n*Ends <t:{int(until.timestamp())}:R>.*",
                color=cfg.color,
            )
            await self._post(channel, embed)
            return
        if event.id == "drop":
            await self._donut_drop(channel, cfg)
        elif event.id == "bounty":
            await self._bounty(guild, channel, cfg, doc)

    async def _post(self, channel, embed, *, ping_here: bool = False, content: str = "", **kwargs) -> None:
        if ping_here:
            content = f"@here {content}".strip()
        try:
            await channel.send(
                content=content or None,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(everyone=False, users=True),
                **kwargs,
            )
        except discord.HTTPException:
            log.warning("could not post event to channel")

    async def _donut_drop(self, channel, cfg) -> None:
        lo = int(cfg.get("events.donut_drop_min", 100))
        hi = int(cfg.get("events.donut_drop_max", 300))
        winners = max(1, int(cfg.get("events.donut_drop_winners", 4)))
        total = random.randint(min(lo, hi), max(lo, hi))
        share = max(1, total // winners)
        view = DonutDropView(self, channel.guild.id, share, winners)
        embed = ui.base_embed(
            title="🍩 Donut Drop!",
            description=f"A crate of donuts hit the server! First **{winners}** to grab it get {self.money(cfg, share)} each.\n\n*First come, first served.*",
            color=cfg.color,
        )
        await self._post(channel, embed, view=view)

    async def _bounty(self, guild, channel, cfg, doc) -> None:
        floor = int(cfg.get("events.bounty_min_worth", 500))
        eligible = [
            uid
            for uid, u in self.econ.all_users(guild.id).items()
            if int(u.get("donuts", 0)) + int(u.get("bank", 0)) >= floor
        ]
        if not eligible:
            return
        target = random.choice(eligible)
        bonus = int(cfg.get("events.bounty_bonus", 750))
        ev.set_bounty(doc, int(target), bonus)
        embed = ui.base_embed(
            title="🎯 Bounty!",
            description=f"A bounty's been placed on <@{target}>! **Rob them** with `/steal` or `/robbank` and claim a {self.money(cfg, bonus)} bonus on top of the take.\n\n*The bounty stands until someone collects it.*",
            color=ui.COLOR_WARN,
        )
        await self._post(channel, embed, content=f"<@{target}>")

    @app_commands.command(name="event", description="See the active server event and the next event window.")
    async def event_status(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await ui.respond(
                interaction, embed=ui.error_embed("Events only run inside a server."), ephemeral=True
            )
            return
        cfg = self.cfg(interaction.guild_id)
        doc = self.econ.store.load(interaction.guild_id)
        active_id = ev.active(doc)
        current = ev.CATALOG_BY_ID.get(active_id or "")
        state = ev.state(doc)
        last = ev._parse(state.get("last_at"))
        next_at = int(last.timestamp() + float(state.get("interval", 0))) if last else None
        if current:
            until = ev.active_until(doc)
            desc = f"{current.emoji} **{current.name}**\n{current.blurb}"
            if until:
                desc += f"\n\n**Ends:** <t:{int(until.timestamp())}:f> · <t:{int(until.timestamp())}:R>"
        else:
            desc = "No timed event is active right now."
        if next_at:
            timer = (
                f"**Next event window:** <t:{next_at}:f> · <t:{next_at}:R>\nEvents never repeat back-to-back."
            )
        else:
            timer = "Next event window has not been scheduled yet."
        embed = ui.base_embed(title="📡 Server Event", color=cfg.color)
        embed.add_field(name="📋 Current event", value=desc, inline=False)
        embed.add_field(name="⏳ Schedule", value=timer, inline=False)
        await ui.respond(interaction, embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Events(bot))
