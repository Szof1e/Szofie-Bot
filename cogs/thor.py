"""Project THOR: staged orbital construction and apex kinetic strikes."""

from __future__ import annotations
import asyncio
import datetime as dt
import random
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional
import discord
from discord import app_commands
from discord.ext import commands
from szofie.catalog import LOADABLE_VEHICLES, VEHICLE_CATALOG
from szofie import combat as combat_service, guides
from szofie import status_ui
from szofie.currency import visible_total as _net, take_visible as _take
from cogs.economy import _public_strategic_chance
from szofie import (
    coalitions,
    continuity as continuity_state,
    imperial_star_destroyer as isd_state,
    space as space_state,
    thor as thor_state,
    nyx,
    ui,
)

THOR_DIR = Path(__file__).resolve().parent.parent / "assets" / "thor"
RESUPPLY_KEYS = thor_state.RESUPPLY_KEYS
ISD_GROUND_DESTRUCTION_PCT = combat_service.ISD_GROUND_DESTRUCTION_PCT


def _fmt(value: int) -> str:
    return ui.format_donuts(value)


def _hours_label(hours: float) -> str:
    minutes = round(float(hours) * 60)
    if minutes < 60:
        return f"{minutes} minute{('s' if minutes != 1 else '')}"
    return f"{float(hours):g} hour{('s' if float(hours) != 1 else '')}"


class ThorCog(commands.Cog, name="Project THOR"):
    """Multi-stage endgame weapon backed by the shared economy document."""

    thor = app_commands.Group(name="thor", description="Build, defend against and operate Project THOR.")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.econ = bot.economy
        self._launch_tasks: Dict[str, asyncio.Task] = {}
        self._guild_locks: Dict[int, asyncio.Lock] = {}
        self._restore_task: Optional[asyncio.Task] = None

    async def cog_load(self) -> None:
        self._restore_task = asyncio.create_task(self._restore_launch_tasks())

    def cog_unload(self) -> None:
        if self._restore_task:
            self._restore_task.cancel()
        for task in self._launch_tasks.values():
            task.cancel()
        self._launch_tasks.clear()

    def cfg(self, guild_id: int):
        return self.bot.config.for_guild(guild_id)

    def _user(self, guild_id: int, user_id: int) -> Dict[str, Any]:
        starting = int(self.cfg(guild_id).get("economy.starting_balance", 100))
        return self.econ.user(guild_id, user_id, starting)

    def _doc(self, guild_id: int) -> Dict[str, Any]:
        return self.econ.store.load(guild_id)

    @staticmethod
    def _launches(doc: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        launches = doc.get("thor_launches")
        if not isinstance(launches, dict):
            launches = doc["thor_launches"] = {}
        return launches

    def _lock(self, guild_id: int) -> asyncio.Lock:
        canonical = self.econ.store.canonical_id(guild_id)
        return self._guild_locks.setdefault(canonical, asyncio.Lock())

    async def _save(self, guild_id: int) -> None:
        await self.econ.save(guild_id)

    async def _guard(
        self, interaction: discord.Interaction, *, channel: bool = True, ephemeral: bool = False
    ):
        if interaction.guild_id is None:
            await ui.respond(
                interaction,
                embed=ui.error_embed("Project THOR only operates inside a server."),
                ephemeral=True,
            )
            return None
        cfg = self.cfg(interaction.guild_id)
        if not cfg.get("economy.enabled", True):
            await ui.respond(
                interaction, embed=ui.error_embed("The economy is disabled here."), ephemeral=True
            )
            return None
        restricted = cfg.get("economy.channel")
        if channel and restricted and (interaction.channel_id != int(restricted)):
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"Use Project THOR in <#{int(restricted)}>."),
                ephemeral=True,
            )
            return None
        await ui.defer_response(interaction, ephemeral=ephemeral)
        return cfg

    @staticmethod
    def _allied(doc: Dict[str, Any], first: int, second: int) -> bool:
        coalitions.cleanup(doc)
        return coalitions.are_allied(doc, first, second)

    @staticmethod
    def _state(user: Dict[str, Any], cfg: Any) -> Dict[str, Any]:
        thor_state.settle(user, cfg)
        return thor_state.normalize(user)

    @staticmethod
    def _art(cfg: Any, embed: discord.Embed, filename: str) -> Optional[discord.File]:
        if not cfg.get("economy.thor_media", True):
            return None
        path = THOR_DIR / filename
        if not path.is_file():
            return None
        embed.set_image(url=f"attachment://{filename}")
        return discord.File(str(path), filename=filename)

    async def _respond_with_art(
        self,
        interaction: discord.Interaction,
        cfg: Any,
        embed: discord.Embed,
        filename: str,
        *,
        followup: bool = False,
        content: Optional[str] = None,
        ephemeral: bool = False,
    ) -> None:
        kwargs: Dict[str, Any] = {"embed": embed, "ephemeral": ephemeral}
        if content is not None:
            kwargs["content"] = content
            kwargs["allowed_mentions"] = discord.AllowedMentions(users=True)
        art = self._art(cfg, embed, filename)
        if art is not None:
            kwargs["file"] = art
        if followup:
            await interaction.followup.send(**kwargs)
        else:
            await ui.respond(interaction, **kwargs)

    async def _restore_launch_tasks(self) -> None:
        try:
            await self.bot.wait_until_ready()
            seen: set[str] = set()
            for guild in self.bot.guilds:
                launches = self._launches(self._doc(guild.id))
                for launch_id, record in list(launches.items()):
                    if launch_id not in seen:
                        seen.add(launch_id)
                        launch_guild = (
                            int(record.get("guild_id", guild.id)) if isinstance(record, dict) else guild.id
                        )
                        self._schedule_launch(launch_guild, launch_id)
        except asyncio.CancelledError:
            return

    def _schedule_launch(self, guild_id: int, launch_id: str) -> None:
        existing = self._launch_tasks.get(launch_id)
        if existing is not None and (not existing.done()):
            return
        self._launch_tasks[launch_id] = asyncio.create_task(self._launch_waiter(guild_id, launch_id))

    async def _launch_waiter(self, guild_id: int, launch_id: str) -> None:
        try:
            record = self._launches(self._doc(guild_id)).get(launch_id)
            due = thor_state.parse_time(record.get("resolves_at")) if record else None
            if due is not None:
                await asyncio.sleep(max(0.0, (due - thor_state.utcnow()).total_seconds()))
            await self._resolve_launch(guild_id, launch_id, announce=True)
        except asyncio.CancelledError:
            return
        finally:
            current = self._launch_tasks.get(launch_id)
            if current is asyncio.current_task():
                self._launch_tasks.pop(launch_id, None)

    async def _settle_due_launches(self, guild_id: int) -> None:
        now = thor_state.utcnow()
        for launch_id, record in list(self._launches(self._doc(guild_id)).items()):
            due = thor_state.parse_time(record.get("resolves_at"))
            if due is not None and due <= now:
                await self._resolve_launch(guild_id, launch_id, announce=True)

    async def _resolve_launch(self, guild_id: int, launch_id: str, *, announce: bool) -> Optional[bool]:
        """Resolve one ascent. Returns True when intercepted, False on orbit."""
        async with self._lock(guild_id):
            doc = self._doc(guild_id)
            launches = self._launches(doc)
            record = launches.get(launch_id)
            if not isinstance(record, dict):
                return None
            due = thor_state.parse_time(record.get("resolves_at"))
            if due is not None and due > thor_state.utcnow():
                return None
            builder_id = int(record.get("builder", 0))
            component_id = str(record.get("component", ""))
            if component_id not in thor_state.COMPONENTS or builder_id <= 0:
                launches.pop(launch_id, None)
                await self._save(guild_id)
                return None
            builder = self._user(guild_id, builder_id)
            state = thor_state.normalize(builder)
            component = state["components"][component_id]
            intercepted = any(
                (
                    bool(item.get("success"))
                    for item in record.get("interceptors", [])
                    if isinstance(item, dict)
                )
            )
            if str(component.get("launch_id")) == launch_id:
                component.update(status="none" if intercepted else "orbit", ready_at=None, launch_id=None)
            launches.pop(launch_id, None)
            await self._save(guild_id)
            reason = "thor-module-intercepted" if intercepted else "thor-module-orbit"
            await self.bot.ledger.record(
                guild_id,
                builder_id,
                0,
                reason,
                after=_net(builder),
                detail=thor_state.COMPONENTS[component_id]["short"],
            )
        if announce:
            channel = self.bot.get_channel(int(record.get("channel_id", 0)))
            if channel is not None:
                cfg = self.cfg(guild_id)
                module = thor_state.COMPONENTS[component_id]["short"]
                shooters = [
                    int(item.get("user", 0))
                    for item in record.get("interceptors", [])
                    if isinstance(item, dict) and int(item.get("user", 0) or 0) > 0
                ]
                if intercepted:
                    winners = [
                        int(item.get("user", 0))
                        for item in record.get("interceptors", [])
                        if isinstance(item, dict) and item.get("success")
                    ]
                    embed = ui.base_embed(
                        title="💥 GBI/EKV — ASCENT INTERCEPT",
                        description=f"The **{module}** and its heavy-lift launch vehicle were destroyed before reaching orbit. Both must be purchased and built again.\n\n**Successful interceptor:** {' '.join((f'<@{uid}>' for uid in winners))}",
                        color=ui.COLOR_OK,
                    )
                    art_name = "thor-gbi-intercept.png"
                else:
                    embed = ui.base_embed(
                        title="🛰️ THOR MODULE ESTABLISHED IN ORBIT",
                        description=f"<@{builder_id}>'s **{module}** survived the ascent window and is now beyond GBI interception. It is ready for orbital assembly once the other modules arrive.\n\n**GBI/EKVs defeated:** {len(shooters)}",
                        color=ui.COLOR_WARN,
                    )
                    art_name = "thor-module-launch.png"
                art = self._art(cfg, embed, art_name)
                kwargs: Dict[str, Any] = {
                    "content": " ".join((f"<@{uid}>" for uid in [builder_id, *shooters])),
                    "embed": embed,
                    "allowed_mentions": discord.AllowedMentions(users=True),
                }
                if art is not None:
                    kwargs["file"] = art
                try:
                    await channel.send(**kwargs)
                except discord.HTTPException:
                    if art is not None:
                        art.close()
        return intercepted

    @thor.command(name="fabricate", description="Fabricate one of Project THOR's three orbital modules.")
    @app_commands.describe(component="Unique THOR module to fabricate")
    @app_commands.choices(
        component=[
            app_commands.Choice(name=str(spec["name"]), value=key)
            for key, spec in thor_state.COMPONENTS.items()
        ]
    )
    async def fabricate(self, interaction: discord.Interaction, component: app_commands.Choice[str]) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        await self._settle_due_launches(gid)
        user = self._user(gid, uid)
        state = self._state(user, cfg)
        key = component.value
        spec = thor_state.COMPONENTS[key]
        if state.get("operational") or thor_state.parse_time(state.get("assembling_until")):
            await ui.respond(
                interaction,
                embed=ui.error_embed("Your Project THOR constellation is already assembled or assembling."),
                ephemeral=True,
            )
            return
        if any((part.get("status") == "fabricating" for part in state["components"].values())):
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    "Your classified fabrication line can build only one THOR module at a time."
                ),
                ephemeral=True,
            )
            return
        part = state["components"][key]
        if part.get("status") != "none":
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"Your {spec['short']} is already fabricated, launching, or in orbit."),
                ephemeral=True,
            )
            return
        cost = int(cfg.get(f"economy.{spec['cost_key']}", int(spec["cost"])))
        if _net(user) < cost:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"Fabricating the {spec['short']} costs 🍩 **{_fmt(cost)}**."),
                ephemeral=True,
            )
            return
        before = _net(user)
        _take(user, cost)
        hours = max(0.01, float(cfg.get(f"economy.{spec['hours_key']}", float(spec["hours"]))))
        ready = thor_state.utcnow() + dt.timedelta(hours=hours)
        part.update(status="fabricating", ready_at=ready.isoformat(), launch_id=None)
        await self._save(gid)
        await self.bot.ledger.record(gid, uid, _net(user) - before, f"thor-fabricate-{key}", after=_net(user))
        embed = ui.base_embed(
            title=f"🏭 {spec['name']} — FABRICATION STARTED",
            description=f"{interaction.user.mention} committed 🍩 **{_fmt(cost)}** to the **{spec['short']}**.\n**Role:** {spec['role']}\n**Fabrication completes:** <t:{int(ready.timestamp())}:F> · <t:{int(ready.timestamp())}:R>\n\nIt must still survive a separately funded launch to orbit.",
            color=cfg.color,
        )
        await self._respond_with_art(interaction, cfg, embed, str(spec["art"]))

    @thor.command(
        name="launch", description="Launch a completed THOR module into an interceptible ascent window."
    )
    @app_commands.describe(component="Fabricated module to launch")
    @app_commands.choices(
        component=[
            app_commands.Choice(name=str(spec["name"]), value=key)
            for key, spec in thor_state.COMPONENTS.items()
        ]
    )
    async def launch(self, interaction: discord.Interaction, component: app_commands.Choice[str]) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        await self._settle_due_launches(gid)
        user = self._user(gid, uid)
        state = self._state(user, cfg)
        key = component.value
        spec = thor_state.COMPONENTS[key]
        part = state["components"][key]
        if part.get("status") != "ready":
            ready = thor_state.parse_time(part.get("ready_at"))
            msg = (
                f"That module is still fabricating until <t:{int(ready.timestamp())}:R>."
                if ready
                else "That module has not completed ground fabrication."
            )
            await ui.respond(interaction, embed=ui.error_embed(msg), ephemeral=True)
            return
        if any((item.get("status") == "launching" for item in state["components"].values())):
            await ui.respond(
                interaction,
                embed=ui.error_embed("Only one of your THOR module launches may be in ascent at a time."),
                ephemeral=True,
            )
            return
        cost = int(cfg.get("economy.thor_launch_cost", 2000000000000))
        if _net(user) < cost:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"The heavy-lift launch vehicle costs 🍩 **{_fmt(cost)}**."),
                ephemeral=True,
            )
            return
        before = _net(user)
        _take(user, cost)
        launch_id = f"THR-{secrets.token_hex(3).upper()}"
        minutes = max(0.1, float(cfg.get("economy.thor_ascent_minutes", 30)))
        resolves = thor_state.utcnow() + dt.timedelta(minutes=minutes)
        part.update(status="launching", ready_at=None, launch_id=launch_id)
        self._launches(self._doc(gid))[launch_id] = {
            "guild_id": gid,
            "builder": uid,
            "component": key,
            "created_at": thor_state.utcnow().isoformat(),
            "resolves_at": resolves.isoformat(),
            "channel_id": interaction.channel_id,
            "interceptors": [],
        }
        await self._save(gid)
        await self.bot.ledger.record(
            gid,
            uid,
            _net(user) - before,
            "thor-module-launch",
            after=_net(user),
            detail=f"{launch_id} · {spec['short']}",
        )
        self._schedule_launch(gid, launch_id)
        max_shots = int(cfg.get("economy.thor_gbi_max_interceptors", 3))
        gbi_cost = int(cfg.get("economy.thor_gbi_cost", 250000000000))
        gbi_hours = float(cfg.get("economy.thor_gbi_build_hours", 0.5))
        embed = ui.base_embed(
            title="🚀 CLASSIFIED ORBITAL LAUNCH DETECTED",
            description=f"{interaction.user.mention} launched the **{spec['short']}** aboard a Super Heavy-Lift Orbital Launch Vehicle.\n\n**Launch ID:** `{launch_id}`\n**Ascent resolves:** <t:{int(resolves.timestamp())}:F> · <t:{int(resolves.timestamp())}:R>\n**GBI/EKV response slots:** 0/{max_shots}\n**Ready GBI/EKV required:** build for 🍩 **{_fmt(gbi_cost)}** each in {_hours_label(gbi_hours)}\n\nOther non-allied players with a ready missile may fire once with `/thor intercept launch_id:{launch_id}`.",
            color=ui.COLOR_WARN,
        )
        await self._respond_with_art(
            interaction, cfg, embed, "thor-module-launch.png", content=interaction.user.mention
        )

    @thor.command(name="gbi-build", description="Build and stockpile GBI/EKVs for THOR-module interception.")
    @app_commands.describe(quantity="Interceptors to build together (stockpile capacity: two)")
    async def gbi_build(
        self, interaction: discord.Interaction, quantity: app_commands.Range[int, 1, 2] = 1
    ) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        user = self._user(gid, uid)
        state = self._state(user, cfg)
        building = thor_state.parse_time(state.get("gbi_building_until"))
        if building is not None:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(f"Your GBI/EKV batch completes <t:{int(building.timestamp())}:R>."),
                ephemeral=True,
            )
            return
        capacity = max(1, int(cfg.get("economy.thor_gbi_stock_capacity", 2)))
        ready = int(state.get("gbi_stock", 0))
        qty = int(quantity)
        room = max(0, capacity - ready)
        if qty > room:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"Your GBI/EKV stockpile has room for only {room} more."),
                ephemeral=True,
            )
            return
        unit = int(
            cfg.get("economy.thor_gbi_block2_unit_cost", 500000000000)
            if state.get("gbi_block2_owned")
            else cfg.get("economy.thor_gbi_cost", 250000000000)
        )
        cost = unit * qty
        if _net(user) < cost:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"Building {qty} GBI/EKV interceptor(s) costs 🍩 **{_fmt(cost)}**."),
                ephemeral=True,
            )
            return
        before = _net(user)
        _take(user, cost)
        hours = max(0.01, float(cfg.get("economy.thor_gbi_build_hours", 0.5)))
        completes = thor_state.utcnow() + dt.timedelta(hours=hours)
        state["gbi_building_until"] = completes.isoformat()
        state["gbi_building_qty"] = qty
        await self._save(gid)
        await self.bot.ledger.record(
            gid, uid, _net(user) - before, "thor-gbi-build", after=_net(user), detail=f"{qty} interceptor(s)"
        )
        await ui.respond(
            interaction,
            embed=ui.base_embed(
                title="🛡️ GBI/EKV CONSTRUCTION STARTED",
                description=f"{interaction.user.mention} committed 🍩 **{_fmt(cost)}** to build **{qty} Ground-Based Interceptor{('s' if qty != 1 else '')}**.\n\n**Ready:** <t:{int(completes.timestamp())}:F> · <t:{int(completes.timestamp())}:R>\n**Stockpile after completion:** {ready + qty}/{capacity}\nA ready GBI/EKV can be fired with `/thor intercept` during a module's ascent window.",
                color=cfg.color,
            ),
        )

    @thor.command(name="gbi-upgrade", description="Upgrade your ascent interceptors to GBI Block II.")
    async def gbi_upgrade(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        user = self._user(gid, uid)
        state = self._state(user, cfg)
        if state.get("gbi_block2_owned"):
            await ui.respond(
                interaction,
                embed=ui.warn_embed("Your GBI battery already fields Block II interceptors."),
                ephemeral=True,
            )
            return
        pending = thor_state.parse_time(state.get("gbi_block2_building_until"))
        if pending:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(f"The Block II upgrade completes <t:{int(pending.timestamp())}:R>."),
                ephemeral=True,
            )
            return
        cost = int(cfg.get("economy.thor_gbi_block2_cost", 2000000000000))
        if _net(user) < cost:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"The GBI Block II upgrade costs 🍩 **{_fmt(cost)}**."),
                ephemeral=True,
            )
            return
        before = _net(user)
        _take(user, cost)
        hours = max(0.01, float(cfg.get("economy.thor_gbi_block2_hours", 12)))
        ready = thor_state.utcnow() + dt.timedelta(hours=hours)
        state["gbi_block2_building_until"] = ready.isoformat()
        await self._save(gid)
        await self.bot.ledger.record(gid, uid, _net(user) - before, "thor-gbi-block2", after=_net(user))
        await ui.respond(
            interaction,
            embed=ui.base_embed(
                title="🎯 GBI BLOCK II MODERNIZATION",
                description=f"{interaction.user.mention} committed 🍩 **{_fmt(cost)}** to the Block II tracking package.\n\n**Operational:** <t:{int(ready.timestamp())}:F> · <t:{int(ready.timestamp())}:R>\nFuture GBI/EKVs cost 500B each and receive a public 20–35% tracking solution.",
                color=cfg.color,
            ),
        )

    async def _launch_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        await self._settle_due_launches(interaction.guild_id)
        now = thor_state.utcnow()
        needle = (current or "").casefold()
        choices: List[app_commands.Choice[str]] = []
        for launch_id, record in self._launches(self._doc(interaction.guild_id)).items():
            due = thor_state.parse_time(record.get("resolves_at"))
            if due is None or due <= now:
                continue
            key = str(record.get("component", ""))
            label = f"{launch_id} · {thor_state.COMPONENTS.get(key, {}).get('short', key)}"
            builder = int(record.get("user_id", record.get("builder", 0)))
            if builder and nyx.hidden(
                self._user(interaction.guild_id, builder), interaction.user.id, builder
            ):
                label = f"{launch_id} · NYX-classified ascent"
            if not needle or needle in label.casefold():
                choices.append(app_commands.Choice(name=label[:100], value=launch_id))
        return choices[:25]

    @thor.command(name="intercept", description="Fire one stockpiled GBI/EKV at an ascending THOR module.")
    @app_commands.describe(launch_id="Active THOR launch ID")
    @app_commands.autocomplete(launch_id=_launch_autocomplete)
    async def intercept(self, interaction: discord.Interaction, launch_id: str) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        await self._settle_due_launches(gid)
        launch_id = launch_id.strip().upper()
        record = self._launches(self._doc(gid)).get(launch_id)
        due = thor_state.parse_time(record.get("resolves_at")) if record else None
        if not isinstance(record, dict) or due is None or due <= thor_state.utcnow():
            await ui.respond(
                interaction,
                embed=ui.error_embed("That THOR ascent window is no longer active."),
                ephemeral=True,
            )
            return
        builder_id = int(record.get("builder", 0))
        if builder_id == uid:
            await ui.respond(
                interaction,
                embed=ui.error_embed("You cannot intercept your own orbital launch."),
                ephemeral=True,
            )
            return
        if self._allied(self._doc(gid), uid, builder_id):
            await ui.respond(
                interaction,
                embed=ui.error_embed("Your coalition treaty forbids sabotaging an ally's orbital launch."),
                ephemeral=True,
            )
            return
        interceptors = record.setdefault("interceptors", [])
        if any((int(item.get("user", 0)) == uid for item in interceptors if isinstance(item, dict))):
            await ui.respond(
                interaction,
                embed=ui.error_embed("You already committed one GBI/EKV to this launch."),
                ephemeral=True,
            )
            return
        maximum = max(1, int(cfg.get("economy.thor_gbi_max_interceptors", 3)))
        if len(interceptors) >= maximum:
            await ui.respond(
                interaction,
                embed=ui.error_embed("All available GBI/EKV response slots are already committed."),
                ephemeral=True,
            )
            return
        user = self._user(gid, uid)
        state = self._state(user, cfg)
        ready = int(state.get("gbi_stock", 0))
        if ready <= 0:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    "You have no ready GBI/EKV. Build up to two in advance with `/thor gbi-build`."
                ),
                ephemeral=True,
            )
            return
        state["gbi_stock"] = ready - 1
        upgraded = bool(state.get("gbi_block2_owned"))
        low_key = "economy.thor_gbi_block2_chance_min" if upgraded else "economy.thor_gbi_chance_min"
        high_key = "economy.thor_gbi_block2_chance_max" if upgraded else "economy.thor_gbi_chance_max"
        low = max(0, min(100, int(cfg.get(low_key, 20 if upgraded else 10))))
        high = max(0, min(100, int(cfg.get(high_key, 35 if upgraded else 30))))
        if high < low:
            low, high = (high, low)
        chance = random.randint(low, high)
        roll = random.randint(1, 100)
        interceptors.append(
            {
                "user": uid,
                "chance": chance,
                "roll": roll,
                "success": roll <= chance,
                "committed_at": thor_state.utcnow().isoformat(),
            }
        )
        await self._save(gid)
        await self.bot.ledger.record(
            gid, uid, 0, "thor-gbi-fired", after=_net(user), other=builder_id, detail=launch_id
        )
        component_id = str(record.get("component", ""))
        module = thor_state.COMPONENTS.get(component_id, {}).get("short", component_id)
        embed = ui.base_embed(
            title="🎯 GBI/EKV COMMITTED",
            description=f"{interaction.user.mention} fired a stockpiled Ground-Based Interceptor at <@{builder_id}>'s **{module}**.\n\n**Launch ID:** `{launch_id}`\n**Tracking solution:** {chance}%\n**Ready stock remaining:** {int(state.get('gbi_stock', 0))}/{int(cfg.get('economy.thor_gbi_stock_capacity', 2))}\n**Response slots:** {len(interceptors)}/{maximum}\n**Resolution:** <t:{int(due.timestamp())}:R>\n\nThe hit result remains classified until the ascent window closes.",
            color=cfg.color,
        )
        await ui.respond(
            interaction,
            content=f"{interaction.user.mention} <@{builder_id}>",
            embed=embed,
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    @thor.command(
        name="orbital-assemble", description="Assemble all three orbiting modules into Project THOR."
    )
    async def orbital_assemble(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        await self._settle_due_launches(gid)
        user = self._user(gid, uid)
        state = self._state(user, cfg)
        if state.get("operational"):
            await ui.respond(
                interaction,
                embed=ui.warn_embed("Your Project THOR constellation is already operational."),
                ephemeral=True,
            )
            return
        assembling = thor_state.parse_time(state.get("assembling_until"))
        if assembling:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(f"Orbital assembly completes <t:{int(assembling.timestamp())}:R>."),
                ephemeral=True,
            )
            return
        missing = [
            spec["short"]
            for key, spec in thor_state.COMPONENTS.items()
            if state["components"][key].get("status") != "orbit"
        ]
        if missing:
            await ui.respond(
                interaction,
                embed=ui.error_embed("Still required in orbit: " + ", ".join(missing)),
                ephemeral=True,
            )
            return
        cost = int(cfg.get("economy.thor_assembly_cost", 2000000000000))
        if _net(user) < cost:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"Orbital assembly costs 🍩 **{_fmt(cost)}**."),
                ephemeral=True,
            )
            return
        before = _net(user)
        _take(user, cost)
        hours = max(0.01, float(cfg.get("economy.thor_assembly_hours", 24)))
        ready = thor_state.utcnow() + dt.timedelta(hours=hours)
        state["assembling_until"] = ready.isoformat()
        await self._save(gid)
        await self.bot.ledger.record(gid, uid, _net(user) - before, "thor-assembly", after=_net(user))
        embed = ui.base_embed(
            title="🛰️ PROJECT THOR — ORBITAL ASSEMBLY",
            description=f"{interaction.user.mention} committed 🍩 **{_fmt(cost)}** and initiated autonomous docking of ODIN, MJÖLNIR and BIFRÖST.\n\n**Constellation operational:** <t:{int(ready.timestamp())}:F> · <t:{int(ready.timestamp())}:R>\nOrbital assembly can no longer be targeted by ascent interceptors.",
            color=ui.COLOR_WARN,
        )
        await self._respond_with_art(interaction, cfg, embed, "thor-orbital-assembly.png")

    @thor.command(name="resupply", description="Send a batch of one to three kinetic penetrators to THOR.")
    @app_commands.describe(quantity="Penetrators in this orbital resupply batch")
    async def resupply(
        self, interaction: discord.Interaction, quantity: app_commands.Range[int, 1, 3]
    ) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        user = self._user(gid, uid)
        state = self._state(user, cfg)
        if not state.get("operational"):
            await ui.respond(
                interaction,
                embed=ui.error_embed("Complete Project THOR's orbital assembly before ordering penetrators."),
                ephemeral=True,
            )
            return
        pending = thor_state.parse_time(state.get("resupply_until"))
        if pending:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(f"An orbital resupply reaches THOR <t:{int(pending.timestamp())}:R>."),
                ephemeral=True,
            )
            return
        capacity = max(1, int(cfg.get("economy.thor_rod_capacity", 6)))
        qty = int(quantity)
        if int(state.get("rods", 0)) + qty > capacity:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"The orbital magazine has room for only {max(0, capacity - int(state.get('rods', 0)))} more rod(s)."
                ),
                ephemeral=True,
            )
            return
        unit = int(cfg.get("economy.thor_rod_cost", 15000000000000))
        cost = unit * qty
        if _net(user) < cost:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"That {qty}-rod batch costs 🍩 **{_fmt(cost)}**."),
                ephemeral=True,
            )
            return
        before = _net(user)
        _take(user, cost)
        hours_key, fallback = RESUPPLY_KEYS[qty]
        hours = max(0.01, float(cfg.get(f"economy.{hours_key}", fallback)))
        ready = thor_state.utcnow() + dt.timedelta(hours=hours)
        state["resupply_until"] = ready.isoformat()
        state["resupply_qty"] = qty
        await self._save(gid)
        await self.bot.ledger.record(
            gid,
            uid,
            _net(user) - before,
            "thor-rod-resupply",
            after=_net(user),
            detail=f"{qty} penetrator(s)",
        )
        embed = ui.base_embed(
            title="🔩 THOR KINETIC-PENETRATOR RESUPPLY",
            description=f"{interaction.user.mention} committed **{qty}** Tungsten Hypervelocity Kinetic Penetrator(s) for 🍩 **{_fmt(cost)}**.\n\n**Orbital delivery:** <t:{int(ready.timestamp())}:F> · <t:{int(ready.timestamp())}:R>\n**Magazine after delivery:** {int(state.get('rods', 0)) + qty}/{capacity}",
            color=cfg.color,
        )
        await self._respond_with_art(interaction, cfg, embed, "thor-rod-loading.png")

    @thor.command(name="chamber", description="Move one stored THOR rod into the orbital release cradle.")
    async def chamber(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        user = self._user(gid, uid)
        state = self._state(user, cfg)
        service = thor_state.parse_time(state.get("service_until"))
        limit = max(1, int(cfg.get("economy.thor_service_after_shots", 3)))
        if service:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(
                    f"Mandatory orbital servicing completes <t:{int(service.timestamp())}:R>."
                ),
                ephemeral=True,
            )
            return
        if int(state.get("shots_since_service", 0)) >= limit:
            await ui.respond(
                interaction,
                embed=ui.error_embed("The release system is service-locked. Run `/thor service`."),
                ephemeral=True,
            )
            return
        if not state.get("operational"):
            await ui.respond(
                interaction, embed=ui.error_embed("Project THOR is not operational."), ephemeral=True
            )
            return
        if state.get("chambered"):
            await ui.respond(
                interaction,
                embed=ui.warn_embed("A kinetic penetrator is already locked in the release cradle."),
                ephemeral=True,
            )
            return
        chambering = thor_state.parse_time(state.get("chambering_until"))
        if chambering:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(f"Internal loading completes <t:{int(chambering.timestamp())}:R>."),
                ephemeral=True,
            )
            return
        if int(state.get("rods", 0)) <= 0:
            await ui.respond(
                interaction,
                embed=ui.error_embed("The orbital magazine is empty. Use `/thor resupply`."),
                ephemeral=True,
            )
            return
        hours = max(0.01, float(cfg.get("economy.thor_chamber_hours", 1.5)))
        ready = thor_state.utcnow() + dt.timedelta(hours=hours)
        state["chambering_until"] = ready.isoformat()
        await self._save(gid)
        embed = ui.base_embed(
            title="⚙️ MJÖLNIR INTERNAL TRANSFER",
            description=f"A Tungsten Hypervelocity Kinetic Penetrator is moving from the orbital magazine into {interaction.user.mention}'s release cradle.\n\n**Strike-ready:** <t:{int(ready.timestamp())}:F> · <t:{int(ready.timestamp())}:R>",
            color=cfg.color,
        )
        await self._respond_with_art(interaction, cfg, embed, "thor-rod-loading.png")

    @thor.command(name="service", description="Perform mandatory servicing after every three THOR shots.")
    async def service(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        user = self._user(gid, uid)
        state = self._state(user, cfg)
        if not state.get("operational"):
            await ui.respond(
                interaction, embed=ui.error_embed("Project THOR is not operational."), ephemeral=True
            )
            return
        pending = thor_state.parse_time(state.get("service_until"))
        if pending:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(f"Orbital servicing completes <t:{int(pending.timestamp())}:R>."),
                ephemeral=True,
            )
            return
        limit = max(1, int(cfg.get("economy.thor_service_after_shots", 3)))
        fired = int(state.get("shots_since_service", 0))
        if fired < limit:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(f"Servicing is not due yet ({fired}/{limit} shots)."),
                ephemeral=True,
            )
            return
        cost = int(cfg.get("economy.thor_service_cost", 2000000000000))
        if _net(user) < cost:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"Mandatory servicing costs 🍩 **{_fmt(cost)}**."),
                ephemeral=True,
            )
            return
        before = _net(user)
        _take(user, cost)
        hours = max(0.01, float(cfg.get("economy.thor_service_hours", 12)))
        ready = thor_state.utcnow() + dt.timedelta(hours=hours)
        state["service_until"] = ready.isoformat()
        await self._save(gid)
        await self.bot.ledger.record(gid, uid, _net(user) - before, "thor-service", after=_net(user))
        await ui.respond(
            interaction,
            embed=ui.base_embed(
                title="🔧 PROJECT THOR ORBITAL SERVICING",
                description=f"{interaction.user.mention} committed 🍩 **{_fmt(cost)}** to inspect the release cradle, guidance package and magazine interfaces.\n\n**Firing authority restored:** <t:{int(ready.timestamp())}:F> · <t:{int(ready.timestamp())}:R>",
                color=cfg.color,
            ),
        )

    def _aegis(self, user: Dict[str, Any], cfg: Any) -> Dict[str, Any]:
        economy = self.bot.get_cog("Economy")
        if economy is not None:
            state = economy._vehicle_settle(user, "aegis")
        else:
            state = user.setdefault("vehicles", {}).setdefault("aegis", {})
        return thor_state.settle_aegis_bmd(state, cfg)

    def _settle_aegis(self, user: Dict[str, Any], cfg: Any) -> Dict[str, Any]:
        return self._aegis(user, cfg)

    @thor.command(name="bmd-refit", description="Refit an operational Aegis destroyer for THOR interception.")
    async def bmd_refit(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        user = self._user(gid, uid)
        aegis = self._settle_aegis(user, cfg)
        if not aegis.get("owned"):
            await ui.respond(
                interaction,
                embed=ui.error_embed("You need an operational Flight III Aegis destroyer first."),
                ephemeral=True,
            )
            return
        if aegis.get("bmd_owned"):
            await ui.respond(
                interaction,
                embed=ui.warn_embed("This Aegis destroyer already carries the BMD refit."),
                ephemeral=True,
            )
            return
        building = thor_state.parse_time(aegis.get("bmd_building_until"))
        if building:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(f"The BMD refit completes <t:{int(building.timestamp())}:R>."),
                ephemeral=True,
            )
            return
        cost = int(cfg.get("economy.thor_bmd_refit_cost", 2000000000000))
        if _net(user) < cost:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"The Aegis BMD refit costs 🍩 **{_fmt(cost)}**."),
                ephemeral=True,
            )
            return
        before = _net(user)
        _take(user, cost)
        hours = max(0.01, float(cfg.get("economy.thor_bmd_refit_hours", 18)))
        ready = thor_state.utcnow() + dt.timedelta(hours=hours)
        aegis["bmd_building_until"] = ready.isoformat()
        await self._save(gid)
        await self.bot.ledger.record(gid, uid, _net(user) - before, "thor-bmd-refit", after=_net(user))
        await ui.respond(
            interaction,
            embed=ui.base_embed(
                title="🛡️ AEGIS BALLISTIC MISSILE DEFENSE REFIT",
                description=f"{interaction.user.mention}'s Flight III Aegis destroyer entered a 🍩 **{_fmt(cost)}** refit.\n\n**BMD operational:** <t:{int(ready.timestamp())}:F> · <t:{int(ready.timestamp())}:R>\nThe refit unlocks RIM-161 SM-3 Block IIA interception of descending THOR rods.",
                color=cfg.color,
            ),
        )

    @thor.command(name="bmd-load", description="Purchase and load SM-3 Block IIA THOR interceptors.")
    @app_commands.describe(quantity="Interceptors to load")
    async def bmd_load(
        self, interaction: discord.Interaction, quantity: app_commands.Range[int, 1, 3] = 1
    ) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        user = self._user(gid, uid)
        aegis = self._settle_aegis(user, cfg)
        if not aegis.get("owned") or not aegis.get("bmd_owned"):
            await ui.respond(
                interaction,
                embed=ui.error_embed("You need an operational Aegis BMD refit first."),
                ephemeral=True,
            )
            return
        loading = thor_state.parse_time(aegis.get("sm3_loading_until"))
        if loading:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(f"SM-3 loading completes <t:{int(loading.timestamp())}:R>."),
                ephemeral=True,
            )
            return
        cap = max(1, int(cfg.get("economy.thor_sm3_capacity", 3)))
        qty = int(quantity)
        if int(aegis.get("sm3_ammo", 0)) + qty > cap:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"The BMD battery has room for only {max(0, cap - int(aegis.get('sm3_ammo', 0)))} more."
                ),
                ephemeral=True,
            )
            return
        unit = int(cfg.get("economy.thor_sm3_cost", 250000000000))
        cost = unit * qty
        if _net(user) < cost:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"Loading {qty} SM-3 interceptor(s) costs 🍩 **{_fmt(cost)}**."),
                ephemeral=True,
            )
            return
        before = _net(user)
        _take(user, cost)
        hours = max(0.01, float(cfg.get("economy.thor_sm3_load_hours", 3)))
        ready = thor_state.utcnow() + dt.timedelta(hours=hours)
        aegis["sm3_loading_until"] = ready.isoformat()
        aegis["sm3_loading_qty"] = qty
        await self._save(gid)
        await self.bot.ledger.record(
            gid, uid, _net(user) - before, "thor-sm3-load", after=_net(user), detail=f"{qty} interceptor(s)"
        )
        await ui.respond(
            interaction,
            embed=ui.base_embed(
                title="🚀 RIM-161 SM-3 BLOCK IIA LOADING",
                description=f"{interaction.user.mention} purchased **{qty}** interceptor(s) for 🍩 **{_fmt(cost)}**.\n**Ready:** <t:{int(ready.timestamp())}:F> · <t:{int(ready.timestamp())}:R>\n**Battery after loading:** {int(aegis.get('sm3_ammo', 0)) + qty}/{cap}",
                color=cfg.color,
            ),
        )

    @staticmethod
    def _loaded_rounds(state: Dict[str, Any]) -> int:
        return sum(
            (
                max(0, int(state.get(key, 0) or 0))
                for key in ("ammo", "loading_qty", "sm3_ammo", "sm3_loading_qty")
            )
        ) + int(bool(state.get("armed")))

    @staticmethod
    def _nyx_impact(
        victim: Dict[str, Any], cfg: Any, category: str, *, attacker_id: int, defender_id: int
    ) -> Dict[str, Any]:
        """Only the chosen category is mutated; empty categories never reroll."""
        from szofie import deathstar, space_fleet

        summary = dict.fromkeys(
            (
                "visible",
                "deep",
                "items",
                "fish",
                "rods",
                "plushies",
                "vehicles",
                "ammo",
                "ground_thor",
                "ground_isd_at_risk",
                "ground_isd_destroyed",
                "ground_deathstar_at_risk",
                "ground_deathstar_destroyed",
            ),
            0,
        )
        summary["nyx_category"] = category
        expedition = space_state.normalize(victim)
        station = deathstar.normalize(victim)
        if category == "donuts":
            summary["visible"] = max(0, int(victim.get("donuts", 0))) + max(0, int(victim.get("bank", 0)))
            summary["deep"] = max(0, int(victim.get("deep_vault_balance", 0)))
            victim.update(
                donuts=0,
                bank=0,
                deep_vault_balance=0,
                deep_vault_withdraw_amount=0,
                deep_vault_withdraw_at=None,
            )
            for hold in (expedition["cargo"], expedition.get("load_manifest") or {}, station["cargo"]):
                summary["visible"] += max(0, int(hold.get("donuts", 0)))
                hold["donuts"] = 0
        elif category == "vehicles":
            from szofie import air_dominance

            air_dominance.clear_operations(victim)
            fleet_loss = space_fleet.thor_impact(victim)
            summary["vehicles"] += fleet_loss["vehicles"]
            summary["ammo"] += fleet_loss["ammo"]
            summary["vehicles"] += nyx.ground_impact(victim)
            carrier = isd_state.normalize(victim)
            holds = [
                (expedition.get("load_manifest") or {}, False),
                (expedition["cargo"], bool(carrier["operational"] or carrier["damaged"])),
                (station["cargo"], bool(station["operational"] or station["damaged"])),
            ]
            for hold, orbital in holds:
                if not orbital:
                    packed = hold.get("vehicles") or {}
                    summary["vehicles"] += len(packed)
                    summary["ammo"] += sum(
                        (
                            ThorCog._loaded_rounds(state)
                            for state in packed.values()
                            if isinstance(state, dict)
                        )
                    )
                    hold["vehicles"] = {}
            if not station["transport_deployed"]:
                summary["vehicles"] += int(bool(station["transport_owned"] or station["transport_until"]))
                station.update(transport_owned=False, transport_until=None)
            if victim.get("s400_owned") or victim.get("s400_building_at"):
                summary["vehicles"] += 1
                summary["ammo"] += max(0, int(victim.get("s400_interceptors", 0)))
                victim.update(s400_owned=False, s400_building_at=None, s400_interceptors=0)
            for state in (victim.get("vehicles") or {}).values():
                if not isinstance(state, dict):
                    continue
                if state.get("owned") or state.get("building_until"):
                    summary["vehicles"] += 1
                summary["ammo"] += ThorCog._loaded_rounds(state)
                last = state.get("last_deploy_at")
                state.clear()
                state.update(owned=False, building_until=None, last_deploy_at=last)
        elif category == "isd":
            parts = [
                part
                for part in isd_state.normalize(victim)["components"].values()
                if part.get("status") in {"fabricating", "ready"}
            ]
            if parts:
                summary["ground_isd_at_risk"] = 1
                part = random.choice(parts)
                resolution_chance = ISD_GROUND_DESTRUCTION_PCT
                if random.randint(1, 100) <= resolution_chance:
                    part.clear()
                    part.update(isd_state.default_component())
                    summary["ground_isd_destroyed"] = 1
        else:
            raise ValueError("Unknown NYX damage category")
        return summary

    @staticmethod
    def _wipe_target(
        victim: Dict[str, Any],
        cfg: Any,
        *,
        attacker_id: int = 0,
        defender_id: int = 0,
        doc: Optional[Dict[str, Any]] = None,
        current: Optional[dt.datetime] = None,
    ) -> Dict[str, Any]:
        """Erase terrestrial holdings; launched/off-world hulls remain intact."""
        combat_service.settle_target_before_impact(victim, cfg, current, doc, defender_id)
        continuity_state.thor_impact(victim, cfg, current)
        if nyx.active(victim, current):
            return ThorCog._nyx_impact(
                victim, cfg, random.choice(nyx.CATEGORIES), attacker_id=attacker_id, defender_id=defender_id
            )
        from szofie import air_dominance

        air_dominance.clear_operations(victim)
        summary = {
            "visible": max(0, int(victim.get("donuts", 0))) + max(0, int(victim.get("bank", 0))),
            "deep": max(0, int(victim.get("deep_vault_balance", 0))),
            "items": sum((max(0, int(v or 0)) for v in (victim.get("inventory", {}) or {}).values()))
            + int(bool(victim.get("android21_helper"))),
            "fish": sum((max(0, int(v or 0)) for v in (victim.get("fish", {}) or {}).values())),
            "rods": sum((1 for v in (victim.get("rods", {}) or {}).values() if int(v or 0) > 0)),
            "plushies": sum((max(0, int(v or 0)) for v in (victim.get("plushies", {}) or {}).values())),
            "vehicles": 0,
            "ammo": 0,
            "ground_thor": 0,
            "ground_isd_at_risk": 0,
            "ground_isd_destroyed": 0,
            "ground_deathstar_at_risk": 0,
            "ground_deathstar_destroyed": 0,
        }
        victim["donuts"] = 0
        victim["bank"] = 0
        victim["deep_vault_balance"] = 0
        victim["deep_vault_withdraw_amount"] = 0
        victim["deep_vault_withdraw_at"] = None
        victim.setdefault("inventory", {}).clear()
        victim["android21_helper"] = False
        victim["helper_certification_tier"] = 0
        victim["android21_helper_at"] = None
        if isinstance(victim.get("employment"), dict):
            victim["employment"]["career_licence_tier"] = 0
        victim.setdefault("plushies", {}).clear()
        victim.setdefault("fish", {}).clear()
        victim.setdefault("fish_meta", {}).clear()
        victim["autofish"] = False
        victim.setdefault("rods", {}).clear()
        victim.setdefault("rod_enchants", {}).clear()
        victim["equipped_rod"] = None
        expedition = space_state.normalize(victim)
        cargo = expedition.get("cargo", {})
        manifest = expedition.get("load_manifest") or {}
        station = victim.get("death_star")
        station_cargo = station.get("cargo", {}) if isinstance(station, dict) else {}
        carrier = isd_state.normalize(victim)
        orbital_cargo_vehicles = (
            cargo.get("vehicles", {}) if carrier["operational"] or carrier["damaged"] else {}
        )
        orbital_station_vehicles = (
            station_cargo.get("vehicles", {})
            if isinstance(station, dict) and (station.get("operational") or station.get("damaged"))
            else {}
        )
        for stored, safe_vehicles in (
            (cargo, orbital_cargo_vehicles),
            (manifest, {}),
            (station_cargo, orbital_station_vehicles),
        ):
            summary["visible"] += max(0, int(stored.get("donuts", 0)))
            summary["items"] += sum((max(0, int(v)) for v in stored.get("inventory", {}).values()))
            summary["plushies"] += sum((max(0, int(v)) for v in stored.get("plushies", {}).values()))
            summary["fish"] += sum((max(0, int(v)) for v in stored.get("fish", {}).values()))
            summary["rods"] += len(stored.get("rods", {}))
            if not safe_vehicles:
                summary["vehicles"] += len(stored.get("vehicles", {}))
        expedition["cargo"] = space_state.default_user()["cargo"]
        expedition["cargo"]["vehicles"] = orbital_cargo_vehicles
        if isinstance(station, dict) and station_cargo:
            from szofie import deathstar

            station["cargo"] = deathstar.cargo()
            station["cargo"]["vehicles"] = orbital_station_vehicles
        expedition.update(
            load_manifest=None, load_until=None, load_hits=0, interceptors=[], field_materials={}
        )
        from szofie import space_fleet

        fleet_losses = space_fleet.thor_impact(victim)
        summary["vehicles"] += fleet_losses["vehicles"]
        summary["vehicles"] += nyx.ground_impact(victim)
        summary["ammo"] += fleet_losses["ammo"]
        from szofie import deathstar

        station = deathstar.normalize(victim)
        if not station["transport_deployed"]:
            summary["vehicles"] += int(bool(station["transport_owned"] or station["transport_until"]))
            station.update(transport_owned=False, transport_until=None)
        summary["ammo"] += max(0, int(station["squadron_stock"])) + int(bool(station["squadron_until"]))
        station.update(squadron_stock=0, squadron_until=None)
        summary["ammo"] += max(0, int(victim.get("icbm_ready", 0)))
        summary["ammo"] += max(0, int(victim.get("aa_rockets_stock", 0)))
        summary["ammo"] += max(0, int(victim.get("aa_rockets_loaded", 0)))
        summary["ammo"] += max(0, int(victim.get("aa_rocket_build_qty", 0)))
        summary["ammo"] += max(0, int(victim.get("s400_interceptors", 0)))
        if thor_state.parse_time(victim.get("icbm_building_at")):
            summary["ammo"] += 1
        victim["icbm_ready"] = 0
        victim["icbm_building_at"] = None
        victim["aa_shield_until"] = None
        victim["aa_rockets_stock"] = 0
        victim["aa_rockets_loaded"] = 0
        victim["aa_rocket_build_at"] = None
        victim["aa_rocket_build_qty"] = 0
        if victim.get("s400_owned") or thor_state.parse_time(victim.get("s400_building_at")):
            summary["vehicles"] += 1
        victim["s400_owned"] = False
        victim["s400_building_at"] = None
        victim["s400_interceptors"] = 0
        for model, state in (victim.get("vehicles", {}) or {}).items():
            if not isinstance(state, dict):
                continue
            if state.get("owned") or thor_state.parse_time(state.get("building_until")):
                summary["vehicles"] += 1
            summary["ammo"] += max(0, int(state.get("ammo", 0) or 0))
            summary["ammo"] += max(0, int(state.get("loading_qty", 0) or 0))
            if state.get("armed"):
                summary["ammo"] += 1
            summary["ammo"] += max(0, int(state.get("sm3_ammo", 0) or 0))
            summary["ammo"] += max(0, int(state.get("sm3_loading_qty", 0) or 0))
            state["owned"] = False
            state["building_until"] = None
            state["last_deploy_at"] = None
            if model == "b2" or "armed" in state:
                state["armed"] = False
                state["arming_until"] = None
            if model == "apache" or "comanche_upgraded" in state:
                state["comanche_upgraded"] = False
                state["comanche_upgrade_until"] = None
            if model in LOADABLE_VEHICLES or "ammo" in state:
                state["ammo"] = 0
                state["loading_until"] = None
                state["loading_qty"] = 0
            if model == "aegis" or "bmd_owned" in state:
                state["bmd_owned"] = False
                state["bmd_building_until"] = None
                state["sm3_ammo"] = 0
                state["sm3_loading_until"] = None
                state["sm3_loading_qty"] = 0
        own_thor = thor_state.normalize(victim)
        summary["ammo"] += max(0, int(own_thor.get("gbi_stock", 0) or 0))
        summary["ammo"] += max(0, int(own_thor.get("gbi_building_qty", 0) or 0))
        own_thor["gbi_stock"] = 0
        own_thor["gbi_building_until"] = None
        own_thor["gbi_building_qty"] = 0
        if own_thor.get("gbi_block2_owned") or thor_state.parse_time(
            own_thor.get("gbi_block2_building_until")
        ):
            summary["ground_thor"] += 1
        own_thor["gbi_block2_owned"] = False
        own_thor["gbi_block2_building_until"] = None
        for component in own_thor["components"].values():
            if thor_state.reset_ground_component(component):
                summary["ground_thor"] += 1
        own_isd = isd_state.normalize(victim)
        summary["ammo"] += max(0, int(own_isd.get("counter_stock", 0)))
        if isd_state.parse_time(own_isd.get("counter_building_until")):
            summary["ammo"] += 1
        own_isd["counter_stock"] = 0
        own_isd["counter_building_until"] = None
        exposed_parts = [
            part for part in own_isd["components"].values() if part.get("status") in {"fabricating", "ready"}
        ]
        if exposed_parts:
            summary["ground_isd_at_risk"] = 1
            part = random.choice(exposed_parts)
            resolution_chance = ISD_GROUND_DESTRUCTION_PCT
            if random.randint(1, 100) <= resolution_chance:
                part.clear()
                part.update(isd_state.default_component())
                summary["ground_isd_destroyed"] = 1
        station_loss = deathstar.thor_construction_impact(
            victim, current, rng=random, resolution_pct=deathstar.THOR_CONSTRUCTION_DESTRUCTION_PCT
        )
        summary["ground_deathstar_at_risk"] = station_loss["at_risk"]
        summary["ground_deathstar_destroyed"] = station_loss["destroyed"]
        return summary

    @thor.command(name="strike", description="Release a chambered THOR penetrator at any non-allied player.")
    @app_commands.describe(target="Player to erase; no minimum balance is required")
    async def strike(self, interaction: discord.Interaction, target: discord.Member) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        if target.id == uid:
            await ui.respond(
                interaction,
                embed=ui.error_embed("Project THOR will not accept its own operator as a target."),
                ephemeral=True,
            )
            return
        if target.bot:
            await ui.respond(
                interaction,
                embed=ui.error_embed("Bots have no terrestrial economy to erase."),
                ephemeral=True,
            )
            return
        if self._allied(self._doc(gid), uid, target.id):
            await ui.respond(
                interaction,
                embed=ui.error_embed("Your coalition treaty forbids a THOR strike against this player."),
                ephemeral=True,
            )
            return
        attacker = self._user(gid, uid)
        state = self._state(attacker, cfg)
        if not state.get("operational"):
            await ui.respond(
                interaction,
                embed=ui.error_embed("Your Project THOR constellation is not operational."),
                ephemeral=True,
            )
            return
        lockdown = thor_state.parse_time(attacker.get("strategic_lockdown_until"))
        if lockdown and lockdown > thor_state.utcnow():
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"Your strategic command network is disabled by a B-52 strike until <t:{int(lockdown.timestamp())}:R>."
                ),
                ephemeral=True,
            )
            return
        service = thor_state.parse_time(state.get("service_until"))
        service_limit = max(1, int(cfg.get("economy.thor_service_after_shots", 3)))
        if service:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"Mandatory servicing completes <t:{int(service.timestamp())}:R>."),
                ephemeral=True,
            )
            return
        if int(state.get("shots_since_service", 0)) >= service_limit:
            await ui.respond(
                interaction,
                embed=ui.error_embed("THOR is service-locked after three shots. Run `/thor service`."),
                ephemeral=True,
            )
            return
        chambering = thor_state.parse_time(state.get("chambering_until"))
        if chambering:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"The release cradle finishes loading <t:{int(chambering.timestamp())}:R>."
                ),
                ephemeral=True,
            )
            return
        if not state.get("chambered") or int(state.get("rods", 0)) <= 0:
            await ui.respond(
                interaction,
                embed=ui.error_embed("No penetrator is chambered. Use `/thor chamber` first."),
                ephemeral=True,
            )
            return
        last = thor_state.parse_time(state.get("last_strike_at"))
        cooldown = max(0.0, float(cfg.get("economy.thor_strike_cooldown_hours", 3))) * 3600
        now = thor_state.utcnow()
        if last and (now - last).total_seconds() < cooldown:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"Orbital firing authority returns <t:{int(last.timestamp() + cooldown)}:R>."
                ),
                ephemeral=True,
            )
            return
        combat_service.settle_before_impact(self._doc(gid), now)
        state["rods"] = max(0, int(state.get("rods", 0)) - 1)
        state["chambered"] = False
        state["chambering_until"] = None
        state["last_strike_at"] = now.isoformat()
        state["shots_since_service"] = int(state.get("shots_since_service", 0)) + 1
        victim = self._user(gid, target.id)
        aegis = self._settle_aegis(victim, cfg)
        engaged = bool(aegis.get("owned") and aegis.get("bmd_owned") and (int(aegis.get("sm3_ammo", 0)) > 0))
        intercepted = False
        chance = 0
        displayed_chance = 0
        missiles_fired = 0
        if engaged:
            public_single = _public_strategic_chance(cfg, "economy.thor_sm3_intercept_chance", 30)
            missiles_fired = 1
            aegis["sm3_ammo"] = max(0, int(aegis.get("sm3_ammo", 0)) - missiles_fired)
            displayed_chance = public_single
            chance = _public_strategic_chance(cfg, "economy.thor_sm3_intercept_chance", 30)
            chance = chance
            intercepted = random.randint(1, 100) <= chance
        summary: Optional[Dict[str, Any]] = None
        if not intercepted:
            summary = self._wipe_target(
                victim, cfg, attacker_id=uid, defender_id=target.id, doc=self._doc(gid), current=now
            )
        await self._save(gid)
        await self.bot.ledger.record(gid, uid, 0, "thor-launch", after=_net(attacker), other=target.id)
        if summary is not None:
            await self.bot.ledger.record(
                gid,
                target.id,
                -summary["visible"],
                "thor-hit",
                after=_net(victim),
                other=uid,
                actor=uid,
                detail=f"{('NYX ' + summary['nyx_category'] + ' only' if summary.get('nyx_category') else 'total wipe')} · deep {_fmt(summary['deep'])} · plushies {summary['plushies']} · vehicles {summary['vehicles']} · Death Star sections {summary['ground_deathstar_destroyed']}",
            )
            if summary["deep"]:
                await self.bot.ledger.record(
                    gid,
                    target.id,
                    -summary["deep"],
                    "deep-vault-hit",
                    after=0,
                    other=uid,
                    actor=uid,
                    detail="Project THOR kinetic impact",
                )
        release = ui.base_embed(
            title="🔩 KINETIC PENETRATOR RELEASED",
            description=f"{interaction.user.mention}'s **MJÖLNIR orbital magazine** released a Tungsten Hypervelocity Kinetic Penetrator toward {target.mention}.\n\nThe vehicle is in autonomous deorbit and terminal-guidance flight. Any operational Aegis BMD response is resolving now.",
            color=ui.COLOR_WARN,
        )
        await self._respond_with_art(
            interaction,
            cfg,
            release,
            "thor-rod-release.png",
            followup=True,
            content=f"{interaction.user.mention} {target.mention}",
        )
        if intercepted:
            cooldown_hours = float(cfg.get("economy.thor_strike_cooldown_hours", 3))
            embed = ui.base_embed(
                title="🛡️ EXO-ATMOSPHERIC INTERCEPT",
                description=f"{target.mention}'s **Aegis BMD** launched a **RIM-161 SM-3 Block IIA** and shattered {interaction.user.mention}'s descending kinetic penetrator.\n\n**Interception chance:** {displayed_chance}%\n**SM-3s fired:** {missiles_fired}\n**Target damage:** None\nThe rod was consumed and THOR entered its full {cooldown_hours:g}-hour firing cycle.",
                color=ui.COLOR_OK,
            )
            await self._respond_with_art(
                interaction,
                cfg,
                embed,
                "thor-sm3-intercept.png",
                followup=True,
                content=f"{interaction.user.mention} {target.mention}",
            )
            return
        assert summary is not None
        summary = dict(summary, visible=summary["visible"], deep=summary["deep"])
        if summary.get("nyx_category"):
            category = summary["nyx_category"]
            details = {
                "donuts": f"{_fmt(summary['visible'])} wallet/bank/cargo donuts and {_fmt(summary['deep'])} deepvault donuts erased.",
                "vehicles": f"{summary['vehicles']} Earth vehicle(s)/build(s) and {summary['ammo']} onboard/loading round(s) destroyed.",
                "isd": "One exposed ISD component was destroyed."
                if summary["ground_isd_destroyed"]
                else "The exposed ISD component survived its ordinary roll."
                if summary["ground_isd_at_risk"]
                else "No exposed ISD component existed; the strike caused no category damage.",
            }
            embed = ui.base_embed(
                title="PROJECT THOR — NYX DEGRADED TARGETING",
                description=f"Ghost Protocol scrambled the strike against {target.mention}. **Random category:** {('ISD part' if category == 'isd' else category.title())} only.\n\n{details[category]}\n\nAll other categories survived. Empty categories never reroll. Raven Rock and launched/off-world vehicles remain protected. The rod was consumed and the normal firing cooldown applies.",
            )
            path = Path(__file__).resolve().parents[1] / "assets" / "nyx" / nyx.ART["active"]
            kwargs = {"embed": embed, "allowed_mentions": discord.AllowedMentions.none()}
            if cfg.get("economy.thor_media", True) and path.is_file():
                embed.set_image(url="attachment://" + path.name)
                kwargs["file"] = discord.File(str(path), filename=path.name)
            await interaction.followup.send(**kwargs)
            return
        embed = ui.base_embed(
            title="☄️ PROJECT THOR — KINETIC IMPACT CONFIRMED",
            description=f"A Tungsten Hypervelocity Kinetic Penetrator struck {target.mention}'s terrestrial economic and military complex. **Total destruction confirmed.**\n\n🍩 **{_fmt(summary['visible'])}** wallet and bank donuts erased\n🏦 **{_fmt(summary['deep'])}** Bedrock Vault donuts erased\n📦 **{_fmt(summary['items'])}** functional items destroyed\n🧸 **{_fmt(summary['plushies'])}** plushies destroyed\n🐟 **{_fmt(summary['fish'])}** fish destroyed\n🎣 **{summary['rods']}** rods and all enchantments destroyed\n✈️ **{summary['vehicles']}** Earth-ground vehicles/builds destroyed\n🚀 **{summary['ammo']}** completed, loaded or building munitions destroyed\n🏭 **{summary['ground_thor']}** ground THOR module/upgrade(s) destroyed\n"
            + (
                "🚩 **1** exposed ISD component destroyed\n\n"
                if summary["ground_isd_destroyed"]
                else "🚩 Exposed ISD component survived\n\n"
                if summary["ground_isd_at_risk"]
                else "🚩 No exposed ISD component was at risk\n\n"
            )
            + (
                "🌑 **1** fabricating Death Star section destroyed\n\n"
                if summary["ground_deathstar_destroyed"]
                else "🌑 Death Star construction survived its 1% destruction roll\n\n"
                if summary["ground_deathstar_at_risk"]
                else "🌑 No fabricating Death Star section was at risk\n\n"
            )
            + "Launched/off-world vehicles, vanity collectibles, countries, diplomacy, debts and orbiting Imperial hardware survived.",
            color=ui.COLOR_BAD,
        )
        await self._respond_with_art(
            interaction,
            cfg,
            embed,
            "thor-impact.png",
            followup=True,
            content=f"{interaction.user.mention} {target.mention}",
        )

    @thor.command(name="status", description="Inspect your THOR program, magazine and Aegis BMD readiness.")
    async def status(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction, channel=False, ephemeral=True)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        await self._settle_due_launches(gid)
        user = self._user(gid, uid)
        state = self._state(user, cfg)
        aegis = self._settle_aegis(user, cfg)
        await self._save(gid)
        construction = status_ui.component_fields(
            thor_state.COMPONENTS, state["components"], windows=self._launches(self._doc(gid))
        )
        assembly = thor_state.parse_time(state.get("assembling_until"))
        constellation = (
            "🟢 Operational"
            if state.get("operational")
            else f"🏗️ Assembling\n{status_ui.line('Completes', status_ui.deadline(assembly))}"
            if assembly
            else "⚪ Not assembled"
        )
        cap = int(cfg.get("economy.thor_rod_capacity", 6))
        resupply = thor_state.parse_time(state.get("resupply_until"))
        chambering = thor_state.parse_time(state.get("chambering_until"))
        magazine = f"**Stored total:** {int(state.get('rods', 0))}/{cap}\n"
        magazine += (
            "**Release cradle:** ARMED"
            if state.get("chambered")
            else f"**Release cradle:** ⏳ Loading · {status_ui.deadline(chambering)}"
            if chambering
            else "**Release cradle:** empty"
        )
        if resupply:
            magazine += f"\n**Resupply:** {int(state.get('resupply_qty', 0))} arriving · {status_ui.deadline(resupply)}"
        last = thor_state.parse_time(state.get("last_strike_at"))
        cooldown = float(cfg.get("economy.thor_strike_cooldown_hours", 3)) * 3600
        if last and thor_state.utcnow() < last + dt.timedelta(seconds=cooldown):
            magazine += f"\n**Launch cooldown:** {status_ui.deadline(last + dt.timedelta(seconds=cooldown))}"
        service = thor_state.parse_time(state.get("service_until"))
        service_limit = int(cfg.get("economy.thor_service_after_shots", 3))
        service_text = f"**Service cycle:** {int(state.get('shots_since_service', 0))}/{service_limit} shots"
        if service:
            service_text += f"\n**Servicing completes:** {status_ui.deadline(service)}"
        elif int(state.get("shots_since_service", 0)) >= service_limit:
            service_text += "\n🔴 Service required · `/thor service`"
        overview = [("📋 Orbital platform", status_ui.line("State", constellation))]
        blocked = []
        if not state.get("operational"):
            blocked.append("Platform not assembled.")
        if not state.get("chambered"):
            blocked.append("Release cradle is empty or loading.")
        if service or int(state.get("shots_since_service", 0)) >= service_limit:
            blocked.append("Servicing required or in progress.")
        if last and thor_state.utcnow() < last + dt.timedelta(seconds=cooldown):
            blocked.append("Launch cooldown is active.")
        overview.append(("⚠️ Launch readiness", "\n".join(blocked) or "🟢 Ready to fire."))
        overview.append(
            (
                "➡️ Next action",
                "Check Construction, then `/thor assemble`."
                if not state.get("operational")
                else "Use `/thor service` when required, `/thor resupply` for rods, and `/thor chamber` to arm the release cradle.",
            )
        )
        gbi_capacity = int(cfg.get("economy.thor_gbi_stock_capacity", 2))
        gbi_building = thor_state.parse_time(state.get("gbi_building_until"))
        gbi_status = f"**Ready:** {int(state.get('gbi_stock', 0))}/{gbi_capacity}"
        if gbi_building:
            gbi_status += f"\n**Building:** {int(state.get('gbi_building_qty', 0))} · ready {status_ui.deadline(gbi_building)}"
        else:
            gbi_status += "\n**Assembly line:** idle · `/thor gbi-build`"
        block2 = thor_state.parse_time(state.get("gbi_block2_building_until"))
        if state.get("gbi_block2_owned"):
            gbi_status += "\n**Tracking package:** GBI Block II"
        elif block2:
            gbi_status += f"\n**Block II upgrade:** ready {status_ui.deadline(block2)}"
        else:
            gbi_status += "\n**Tracking package:** baseline · `/thor gbi-upgrade`"
        bmd_ready = thor_state.parse_time(aegis.get("bmd_building_until"))
        sm3_loading = thor_state.parse_time(aegis.get("sm3_loading_until"))
        if not aegis.get("owned"):
            defense = "No operational Aegis destroyer"
        elif bmd_ready:
            defense = f"🏗️ BMD refit completes {status_ui.deadline(bmd_ready)}"
        elif not aegis.get("bmd_owned"):
            defense = "Aegis operational · BMD refit absent"
        else:
            defense = f"BMD operational · SM-3 **{int(aegis.get('sm3_ammo', 0))}/{int(cfg.get('economy.thor_sm3_capacity', 3))}**"
            defense += " · **one missile per incoming rod**"
            if sm3_loading:
                defense += f"\n**Loading completes:** {status_ui.deadline(sm3_loading)}"
        pages = status_ui.report(
            f"⚡ {interaction.user.display_name}'s Project THOR",
            [
                ("Overview", overview),
                ("Construction", construction),
                (
                    "Ammunition and servicing",
                    [("🚀 Kinetic magazine", magazine), ("🔧 Platform servicing", service_text)],
                ),
                ("Defences", [("🎯 GBI/EKV ascent defence", gbi_status), ("🛡️ Aegis BMD", defense)]),
            ],
            color=cfg.color,
            checked_at=thor_state.utcnow(),
            footer="Private program report · deadlines advance in real time, including offline · /thor guide",
        )
        await ui.respond(
            interaction, embed=pages[0], view=status_ui.pager(pages, interaction.user.id), ephemeral=True
        )

    @thor.command(name="guide", description="Learn every Project THOR construction, attack and defense step.")
    async def guide(self, interaction: discord.Interaction) -> None:
        cfg = await self._guard(interaction, channel=False)
        if cfg is None:
            return
        pages = guides.thor_pages(cfg, color=cfg.color)
        view = ui.Paginator(pages, interaction.user.id)
        await ui.respond(
            interaction, embed=pages[0], view=view, allowed_mentions=discord.AllowedMentions.none()
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ThorCog(bot))
