"""NYX construction, public ascent interception and self-only Ghost Protocol."""

from __future__ import annotations
import asyncio
import copy
import datetime as dt
import logging
import random
import secrets
from pathlib import Path
from typing import Any, Dict
import discord
from discord.ext import commands, tasks
from szofie import coalitions, nyx, thor, ui
from szofie import status_ui
from szofie.currency import take_visible, visible_total

log = logging.getLogger(__name__)
ART_DIR = Path(__file__).resolve().parents[1] / "assets" / "nyx"


class SatelliteCog(commands.Cog):
    """Internal lifecycle service, exposed only through the existing space menus."""

    def __init__(self, bot):
        self.bot = bot
        self.econ = bot.economy
        self._locks: Dict[int, asyncio.Lock] = {}
        self._report_backfill = None
        self._last_backfill = None

    async def cog_load(self):
        self.launch_sweep.start()
        self.report_sweep.start()

    def cog_unload(self):
        self.launch_sweep.cancel()
        self.report_sweep.cancel()
        if self._report_backfill is not None:
            self._report_backfill.cancel()

    async def _backfill_reports(self):
        try:
            await self.bot.nyx_reports.backfill()
        except Exception:
            log.exception("NYX historical report cleanup interrupted; checkpoints retained.")

    @tasks.loop(seconds=15)
    async def report_sweep(self):
        try:
            await self.bot.nyx_reports.redact_active()
        except Exception:
            log.exception("NYX public report cleanup failed; next sweep will retry.")
        current = thor.utcnow()
        if (self._report_backfill is None or self._report_backfill.done()) and (
            self._last_backfill is None or current - self._last_backfill >= dt.timedelta(minutes=10)
        ):
            self._last_backfill = current
            self._report_backfill = asyncio.create_task(self._backfill_reports())

    @report_sweep.before_loop
    async def before_report_sweep(self):
        await self.bot.wait_until_ready()

    def _lock(self, gid):
        return self._locks.setdefault(self.econ.store.canonical_id(gid), asyncio.Lock())

    def _doc(self, gid):
        return self.econ.store.load(gid)

    def _user(self, gid, uid):
        cfg = self.bot.config.for_guild(gid)
        return self.econ.user(gid, uid, int(cfg.get("economy.starting_balance", 100)))

    async def _guard(self, interaction, *, private=False, channel=True):
        if interaction.guild_id is None:
            await ui.respond(
                interaction, embed=ui.error_embed("Satellites operate inside a server."), ephemeral=True
            )
            return None
        cfg = self.bot.config.for_guild(interaction.guild_id)
        restricted = cfg.get("economy.channel")
        if not cfg.get("economy.enabled", True):
            await ui.respond(
                interaction, embed=ui.error_embed("The economy is disabled here."), ephemeral=True
            )
            return None
        if channel and restricted and (interaction.channel_id != int(restricted)):
            await ui.respond(
                interaction, embed=ui.error_embed(f"Use satellites in <#{int(restricted)}>."), ephemeral=True
            )
            return None
        await ui.defer_response(interaction, ephemeral=private)
        return cfg

    @staticmethod
    def _art(embed, stage):
        path = ART_DIR / nyx.ART[stage]
        if not path.is_file():
            return None
        embed.set_image(url="attachment://" + path.name)
        return discord.File(str(path), filename=path.name)

    async def _respond(self, interaction, embed, stage, *, private=False):
        art = self._art(embed, stage)
        kwargs = {"embed": embed, "ephemeral": private, "allowed_mentions": discord.AllowedMentions.none()}
        if art:
            kwargs["file"] = art
        try:
            await ui.respond(interaction, **kwargs)
        except discord.HTTPException:
            if art is None:
                raise
            art.close()
            embed.set_image(url=None)
            kwargs.pop("file", None)
            await ui.respond(interaction, **kwargs)
        finally:
            if art:
                art.close()

    async def _settle_launches(self, gid):
        announcements = []
        recovered = False
        async with self._lock(gid):
            doc = self._doc(gid)
            for uid, user in doc.get("users", {}).items():
                record = (user.get("nyx") or {}).get("launch")
                if not isinstance(record, dict):
                    continue
                if not record.get("announced"):
                    for pocket in ("donuts", "bank"):
                        user[pocket] = int(user.get(pocket, 0)) + int(record.get("refund_" + pocket, 0))
                    nyx.normalize(user).update(status="ready", launch=None)
                    recovered = True
                    continue
                result = nyx.finish_launch(user)
                if result is not None:
                    announcements.append((int(uid), copy.deepcopy(record), result))
            if announcements or recovered:
                await self.econ.save(gid)
        for uid, record, intercepted in announcements:
            await self.bot.ledger.record(
                gid,
                uid,
                0,
                "nyx-intercepted" if intercepted else "nyx-orbit",
                after=visible_total(self._user(gid, uid)),
            )
            if intercepted:
                for row in record.get("interceptors", []):
                    if row.get("success"):
                        await self.bot.ledger.record(
                            gid,
                            uid,
                            0,
                            "nyx-intercept-hit",
                            after=visible_total(self._user(gid, uid)),
                            other=int(row["user"]),
                            actor=int(row["user"]),
                            detail="NYX satellite and launch vehicle destroyed during ascent",
                        )
            channel = self.bot.get_channel(int(record.get("channel_id", 0)))
            if channel is None:
                continue
            stage = "intercepted" if intercepted else "orbit"
            embed = ui.base_embed(
                title="NYX — LAUNCH INTERCEPTED" if intercepted else "NYX — ORBIT ESTABLISHED",
                description=f"<@{uid}>'s NYX satellite and rocket were destroyed. Rebuild before trying again."
                if intercepted
                else f"<@{uid}>'s NYX reached orbit. Ordinary targeted attacks can no longer destroy it. Activate your self-only field with `/space fleet mission craft:NYX mission:Jam`.",
            )
            art = self._art(embed, stage)
            kwargs = {"embed": embed, "allowed_mentions": discord.AllowedMentions.none()}
            if art:
                kwargs["file"] = art
            try:
                await channel.send(**kwargs)
            except discord.HTTPException:
                if art:
                    art.close()
                log.warning("NYX launch result delivery failed; outcome remains saved.")

    @tasks.loop(seconds=15)
    async def launch_sweep(self):
        seen = set()
        for guild in self.bot.guilds:
            canonical = self.econ.store.canonical_id(guild.id)
            if canonical in seen:
                continue
            seen.add(canonical)
            try:
                await self._settle_launches(guild.id)
            except Exception:
                log.exception("NYX launch settlement failed")

    @launch_sweep.before_loop
    async def before_launch_sweep(self):
        await self.bot.wait_until_ready()

    async def build(self, interaction: discord.Interaction):
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        await self._settle_launches(gid)
        async with self._lock(gid):
            user = self._user(gid, uid)
            nyx.settle(user)
            state = nyx.normalize(user)
            cost = max(0, int(cfg.get("economy.nyx_build_cost", nyx.BUILD_COST)))
            if state["status"] != "none":
                await ui.respond(
                    interaction,
                    embed=ui.error_embed("You already have a NYX project or satellite."),
                    ephemeral=True,
                )
                return
            if visible_total(user) < cost:
                await ui.respond(
                    interaction,
                    embed=ui.error_embed(f"NYX requires {ui.format_donuts(cost)} donuts from wallet/bank."),
                    ephemeral=True,
                )
                return
            ready = thor.utcnow() + dt.timedelta(
                hours=max(0.01, float(cfg.get("economy.nyx_build_hours", nyx.BUILD_HOURS)))
            )
            take_visible(user, cost)
            state.update(status="fabricating", ready_at=ready.isoformat())
            await self.econ.save(gid)
        await self.bot.ledger.record(gid, uid, -cost, "nyx-build", after=visible_total(user))
        launch_cost = int(cfg.get("economy.nyx_launch_cost", nyx.LAUNCH_COST))
        await self._respond(
            interaction,
            ui.base_embed(
                title="NYX — FABRICATION STARTED",
                description=f"**Paid:** {ui.format_donuts(cost)} donuts\n**Completes:** <t:{int(ready.timestamp())}:F> (<t:{int(ready.timestamp())}:R>)\nThen `/space fleet launch craft:NYX`: {ui.format_donuts(launch_cost)} donuts. The grounded satellite remains vulnerable to THOR.",
            ),
            "fabrication",
        )

    async def launch(self, interaction: discord.Interaction):
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        await self._settle_launches(gid)
        async with self._lock(gid):
            user = self._user(gid, uid)
            nyx.settle(user)
            state = nyx.normalize(user)
            if state["status"] != "ready":
                await ui.respond(
                    interaction,
                    embed=ui.error_embed("NYX fabrication must finish before launch."),
                    ephemeral=True,
                )
                return
            lockdown = thor.parse_time(user.get("strategic_lockdown_until"))
            if lockdown and lockdown > thor.utcnow():
                await ui.respond(
                    interaction,
                    embed=ui.error_embed("Your runway/operations lockdown blocks this launch."),
                    ephemeral=True,
                )
                return
            cost = max(0, int(cfg.get("economy.nyx_launch_cost", nyx.LAUNCH_COST)))
            if visible_total(user) < cost:
                await ui.respond(
                    interaction,
                    embed=ui.error_embed(
                        f"Launch requires {ui.format_donuts(cost)} donuts from wallet/bank."
                    ),
                    ephemeral=True,
                )
                return
            wallet_paid = min(cost, max(0, int(user.get("donuts", 0))))
            take_visible(user, cost)
            record = dict(
                id="NYX-" + secrets.token_hex(3).upper(),
                guild_id=gid,
                channel_id=interaction.channel_id,
                resolves_at=None,
                announced=False,
                interceptors=[],
                refund_donuts=wallet_paid,
                refund_bank=cost - wallet_paid,
            )
            state.update(status="launching", launch=record)
            await self.econ.save(gid)
            minutes = max(1, float(cfg.get("economy.nyx_launch_minutes", nyx.LAUNCH_MINUTES)))
            embed = ui.base_embed(
                title="NYX — PUBLIC LAUNCH WARNING",
                description=f"<@{uid}> is launching NYX. **Paid:** {ui.format_donuts(cost)} donuts.\nA **{minutes:g}-minute** interception window starts when this warning is delivered.\nReady GBI/EKV owners: `/space fleet intercept target:<@{uid}> craft:NYX counter:GBI/EKV`. One missile per defender, up to three defenders. Coalition allies cannot intercept. After the window closes, a surviving satellite becomes untouchable by ordinary targeted attacks.",
            )
            try:
                await self._respond(interaction, embed, "launch")
            except Exception:
                user["donuts"] = int(user.get("donuts", 0)) + wallet_paid
                user["bank"] = int(user.get("bank", 0)) + cost - wallet_paid
                state.update(status="ready", launch=None)
                await self.econ.save(gid)
                raise
            record.update(
                announced=True, resolves_at=(thor.utcnow() + dt.timedelta(minutes=minutes)).isoformat()
            )
            await self.econ.save(gid)
        await self.bot.ledger.record(gid, uid, -cost, "nyx-launch", after=visible_total(user))

    async def intercept(self, interaction: discord.Interaction, target: discord.Member):
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        await self._settle_launches(gid)
        async with self._lock(gid):
            doc = self._doc(gid)
            coalitions.cleanup(doc)
            if target.id == uid or target.bot or coalitions.are_allied(doc, uid, target.id):
                await ui.respond(
                    interaction,
                    embed=ui.error_embed("You cannot intercept yourself, a bot or a coalition ally."),
                    ephemeral=True,
                )
                return
            victim = self._user(gid, target.id)
            state = nyx.normalize(victim)
            record = state.get("launch")
            due = thor.parse_time(record.get("resolves_at")) if isinstance(record, dict) else None
            if (
                state["status"] != "launching"
                or not isinstance(record, dict)
                or (not record.get("announced"))
                or (not due)
                or (due <= thor.utcnow())
            ):
                await ui.respond(
                    interaction,
                    embed=ui.error_embed("That player has no active NYX ascent window."),
                    ephemeral=True,
                )
                return
            rows = record["interceptors"]
            if len(rows) >= 3 or any((int(row["user"]) == uid for row in rows)):
                await ui.respond(
                    interaction,
                    embed=ui.error_embed("You already fired, or all three interception slots are taken."),
                    ephemeral=True,
                )
                return
            user = self._user(gid, uid)
            thor.settle(user, cfg)
            battery = thor.normalize(user)
            if battery["gbi_stock"] <= 0:
                await ui.respond(
                    interaction,
                    embed=ui.error_embed("Build ready interceptors first with `/thor gbi-build`."),
                    ephemeral=True,
                )
                return
            upgraded = battery["gbi_block2_owned"]
            prefix = "thor_gbi_block2_chance_" if upgraded else "thor_gbi_chance_"
            low = max(0, min(100, int(cfg.get("economy." + prefix + "min", 20 if upgraded else 10))))
            high = max(0, min(100, int(cfg.get("economy." + prefix + "max", 35 if upgraded else 30))))
            public_chance = random.randint(min(low, high), max(low, high))
            success = random.randint(1, 100) <= public_chance
            battery["gbi_stock"] -= 1
            rows.append(dict(user=uid, success=success))
            await self.econ.save(gid)
        await self.bot.ledger.record(gid, uid, 0, "nyx-gbi-fired", after=visible_total(user), other=target.id)
        await ui.respond(
            interaction,
            embed=ui.base_embed(
                title="NYX — GBI/EKV COMMITTED",
                description=f"One ready GBI/EKV consumed. **Ordinary tracking solution:** {public_chance}%.\n**Slots used:** {len(rows)}/3. **Result:** <t:{int(due.timestamp())}:R>. The result is revealed after the ascent window closes.",
            ),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def activate(self, interaction: discord.Interaction):
        cfg = await self._guard(interaction, private=True)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        await self._settle_launches(gid)
        async with self._lock(gid):
            user = self._user(gid, uid)
            try:
                until = nyx.activate(self._doc(gid), uid, cfg)
            except ValueError as exc:
                await ui.respond(interaction, embed=ui.error_embed(str(exc)), ephemeral=True)
                return
            await self.econ.save(gid)
        await self.bot.ledger.record(gid, uid, 0, "nyx-activate", after=visible_total(user))
        registry = getattr(self.bot, "nyx_reports", None)
        if registry is not None:
            try:
                await registry.queue_active()
            except Exception:
                log.exception("NYX cleanup queue failed; background sweep will retry.")
        embed = ui.base_embed(
            title="NYX — GHOST PROTOCOL ACTIVE",
            description=f"Your counter-intelligence field lasts until <t:{int(until.timestamp())}:R>. U-2, Deimos and SR-71 cannot reveal your intel; old targeting packages have been invalidated. You disappear entirely from public leaderboard/season rankings until the field expires; actual funds keep changing normally. A THOR rod that gets through can affect only one random category: donuts, Earth vehicles or one exposed ISD part. Fresh intel is blocked immediately. Old public report buttons are disabled now; previously posted public bot status/asset reports are queued for permanent censorship in the background under Discord edit limits. Fresh checks are required after expiry. Conventional attacks and civilisation-wiping superweapons remain dangerous.",
        )
        for attempt in range(2):
            try:
                await ui.respond(
                    interaction, embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
                )
                break
            except (discord.HTTPException, OSError, asyncio.TimeoutError) as exc:
                if attempt or (isinstance(exc, discord.HTTPException) and exc.status < 500):
                    raise
                log.warning("NYX confirmation delivery interrupted; retrying the saved result only.")
        artwork = embed.copy()
        art = None
        try:
            art = self._art(artwork, "active")
            if art is None:
                return

            async def upload():
                message = await ui.response_message(interaction)
                await message.edit(
                    embed=artwork, attachments=[art], allowed_mentions=discord.AllowedMentions.none()
                )

            await asyncio.wait_for(upload(), timeout=8)
        except (discord.HTTPException, OSError, asyncio.TimeoutError):
            log.warning("NYX artwork delivery interrupted; the text confirmation remains available.")
        finally:
            if art is not None:
                art.close()

    async def status(self, interaction: discord.Interaction):
        cfg = await self._guard(interaction, private=True, channel=False)
        if cfg is None:
            return
        await self._settle_launches(interaction.guild_id)
        user = self._user(interaction.guild_id, interaction.user.id)
        text = nyx.summary(user, cfg)
        state = nyx.normalize(user)
        stage = (
            "active"
            if nyx.active(user)
            else {
                "none": "ready",
                "ready": "ready",
                "fabricating": "fabrication",
                "launching": "launch",
                "orbit": "orbit",
            }[state["status"]]
        )
        await self.econ.save(interaction.guild_id)
        next_action = (
            "Build with `/space build craft:NYX`."
            if state["status"] == "none"
            else "Launch with `/space fleet launch craft:NYX`."
            if state["status"] == "ready"
            else "Activate with `/space fleet mission craft:NYX mission:Jam`."
            if state["status"] == "orbit"
            else "Wait for construction or the launch window to complete, then check this status again."
        )
        pages = status_ui.report(
            "🛰️ " + nyx.NAME,
            [("Overview", [("📋 Satellite readiness", text), ("➡️ Next action", next_action)])],
        )
        await self._respond(interaction, pages[0], stage, private=True)

    async def guide(self, interaction: discord.Interaction):
        cfg = await self._guard(interaction, channel=False)
        if cfg is None:
            return
        pages = nyx.guide_pages(cfg)
        await ui.respond(
            interaction, embed=pages[0], view=ui.Paginator(pages, interaction.user.id, timeout=180)
        )


async def setup(bot):
    await bot.add_cog(SatelliteCog(bot))
