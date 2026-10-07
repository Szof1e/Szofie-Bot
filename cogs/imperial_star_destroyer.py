"""Imperial Star Destroyer: server-wide, publicly counterable endgame reset."""

from __future__ import annotations
import asyncio
import copy
import datetime as dt
import json
import logging
import math
import random
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import discord
from discord import app_commands
from discord.ext import commands
from szofie import coalitions, continuity as continuity_state
from szofie import launch_escorts
from szofie.currency import visible_total as _net, take_visible as _take
from szofie import imperial_star_destroyer as isd_state, guides
from szofie import space as space_state
from szofie import ui, space_guides, nyx
from szofie import status_ui
from szofie.betting import AmountParseError, parse_amount
from szofie.storage import Storage
from szofie.economy import _default_user

log = logging.getLogger("szofie.isd")
ASSET_DIR = Path(__file__).resolve().parent.parent / "assets" / "imperial_star_destroyer"
SPACE_ASSET_DIR = Path(__file__).resolve().parent.parent / "assets" / "space"
BACKUP_DIR = Path(__file__).resolve().parent.parent / "backups" / "operation-cinder"
MAX_CONCURRENT_COMPONENT_FABRICATIONS = 2


def _fmt(value: int) -> str:
    return ui.format_donuts(value)


def _status_deadline(ready: dt.datetime, checked_at: dt.datetime) -> str:
    """A static countdown snapshot plus an unambiguous, localised finish date."""
    minutes = max(0, math.ceil((ready - checked_at).total_seconds() / 60))
    hours, minutes = divmod(minutes, 60)
    return f"remaining when checked: **{hours}h {minutes:02d}m** · finishes <t:{int(ready.timestamp())}:f>"


class ImperialStarDestroyerCog(commands.Cog, name="Imperial Star Destroyer"):
    isd = app_commands.Group(
        name="isd", description="Build, oppose and operate the Imperial Star Destroyer megaproject."
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.econ = bot.economy
        self._locks: Dict[int, asyncio.Lock] = {}
        self._window_task: Optional[asyncio.Task] = None

    async def cog_load(self) -> None:
        self._window_task = asyncio.create_task(self._window_loop())

    def cog_unload(self) -> None:
        if self._window_task:
            self._window_task.cancel()

    def cfg(self, guild_id: int):
        return self.bot.config.for_guild(guild_id)

    def _doc(self, guild_id: int) -> Dict[str, Any]:
        return self.econ.store.load(guild_id)

    def _user(self, guild_id: int, user_id: int) -> Dict[str, Any]:
        starting = int(self.cfg(guild_id).get("economy.starting_balance", 100))
        return self.econ.user(guild_id, user_id, starting)

    def _lock(self, guild_id: int) -> asyncio.Lock:
        canonical = self.econ.store.canonical_id(guild_id)
        return self._locks.setdefault(canonical, asyncio.Lock())

    async def _save(self, guild_id: int) -> None:
        await self.econ.save(guild_id)

    async def _guard(self, interaction: discord.Interaction, *, channel: bool = True):
        if interaction.guild_id is None:
            await ui.respond(
                interaction,
                embed=ui.error_embed("The Imperial Star Destroyer can only operate inside a server."),
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
                embed=ui.error_embed(f"Use the megaproject commands in <#{int(restricted)}>."),
                ephemeral=True,
            )
            return None
        await ui.defer_response(interaction)
        return cfg

    @staticmethod
    def _state(user: Dict[str, Any]) -> Dict[str, Any]:
        isd_state.settle(user)
        return isd_state.normalize(user)

    def _projects(self, guild_id: int) -> List[Tuple[int, Dict[str, Any], Dict[str, Any]]]:
        projects: List[Tuple[int, Dict[str, Any], Dict[str, Any]]] = []
        for raw_id, user in self.econ.all_users(guild_id).items():
            if not isinstance(user, dict):
                continue
            state = self._state(user)
            if isd_state.project_started(state):
                projects.append((int(raw_id), user, state))
        projects.sort(key=lambda item: item[0])
        return projects

    @staticmethod
    def _window_id(prefix: str) -> str:
        return f"{prefix}-{secrets.token_hex(3).upper()}"

    @staticmethod
    def _window_deadline(record: Dict[str, Any]) -> dt.datetime:
        return isd_state.utcnow() + dt.timedelta(seconds=max(0, float(record.get("remaining_seconds", 0))))

    def _art(self, cfg: Any, embed: discord.Embed, filename: str) -> Optional[discord.File]:
        if not cfg.get("economy.isd_media", True):
            return None
        path = ASSET_DIR / filename
        if not path.is_file():
            path = SPACE_ASSET_DIR / filename
            if not path.is_file():
                return None
        embed.set_image(url=f"attachment://{filename}")
        return discord.File(str(path), filename=filename)

    async def _respond_art(
        self,
        interaction: discord.Interaction,
        cfg: Any,
        embed: discord.Embed,
        filename: str,
        *,
        content: Optional[str] = None,
        ephemeral: bool = False,
    ) -> None:
        kwargs: Dict[str, Any] = {"embed": embed, "ephemeral": ephemeral}
        art = self._art(cfg, embed, filename)
        if art:
            kwargs["file"] = art
        if content is not None:
            kwargs["content"] = content
            kwargs["allowed_mentions"] = discord.AllowedMentions(users=True, everyone=True)
        await ui.respond(interaction, **kwargs)

    async def _send_art(
        self,
        channel: discord.abc.Messageable,
        cfg: Any,
        embed: discord.Embed,
        filename: str,
        *,
        content: Optional[str] = None,
        everyone: bool = False,
    ) -> None:
        kwargs: Dict[str, Any] = {"embed": embed}
        art = self._art(cfg, embed, filename)
        if art:
            kwargs["file"] = art
        if content is not None:
            kwargs["content"] = content
            kwargs["allowed_mentions"] = discord.AllowedMentions(users=True, everyone=everyone)
        try:
            await channel.send(**kwargs)
        except discord.HTTPException:
            pass

    async def _window_loop(self) -> None:
        try:
            await self.bot.wait_until_ready()
            previous = time.monotonic()
            while not self.bot.is_closed():
                await asyncio.sleep(15)
                current = time.monotonic()
                elapsed = min(15.0, max(0.0, current - previous))
                previous = current
                if not getattr(self.bot, "is_ready", lambda: True)():
                    continue
                seen: set[int] = set()
                for guild in self.bot.guilds:
                    canonical = self.econ.store.canonical_id(guild.id)
                    if canonical in seen:
                        continue
                    seen.add(canonical)
                    try:
                        await self._advance_windows(guild.id, elapsed)
                    except Exception:
                        log.exception("ISD window advancement failed for guild %s", guild.id)
        except asyncio.CancelledError:
            return

    async def _advance_windows(self, guild_id: int, elapsed: float) -> None:
        due: List[str] = []
        pending: List[str] = []
        elapsed = min(15.0, max(0.0, elapsed))
        reminders: List[Tuple[Dict[str, Any], int]] = []
        async with self._lock(guild_id):
            records = isd_state.windows(self._doc(guild_id))
            changed = False
            for window_id, record in list(records.items()):
                if not isinstance(record, dict):
                    records.pop(window_id, None)
                    changed = True
                    continue
                if record.get("announced") is False:
                    pending.append(window_id)
                    continue
                previous = max(0.0, float(record.get("remaining_seconds", 0)))
                remaining = max(0.0, previous - elapsed)
                record["remaining_seconds"] = remaining
                if record.get("kind") == "cinder":
                    sent = record.setdefault("milestones_sent", [])
                    for threshold in (3600, 1800, 900):
                        if previous > threshold >= remaining and threshold not in sent:
                            sent.append(threshold)
                            reminders.append((copy.deepcopy(record), threshold))
                changed = True
                if remaining <= 0:
                    due.append(window_id)
            if changed:
                await self._save(guild_id)
        for window_id in due:
            await self._resolve_window(guild_id, window_id)
        for window_id in pending:
            await self._announce_window(guild_id, window_id)
        for record, threshold in reminders:
            if float(record.get("remaining_seconds", 0)) <= 0:
                continue
            channel = self.bot.get_channel(int(record.get("channel_id", 0)))
            if channel is None:
                continue
            embed = ui.base_embed(
                title="OPERATION CINDER — CHARGE UPDATE",
                description=f"Approximately **{ui.human_duration(threshold)}** of bot-online time remains. Use `/isd intercept {record.get('window_id', '')}` with a prebuilt B-wing package.\n\nCommitted defenders: **{len(record.get('interceptors', []))}/10**",
                color=ui.COLOR_BAD,
            )
            try:
                await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                pass

    async def _announce_window(self, guild_id: int, window_id: str) -> bool:
        """Durable retry of public warnings; no unseen defense window counts down."""
        record = isd_state.windows(self._doc(guild_id)).get(window_id)
        if not record or record.get("announced") is not False:
            return True
        channel = self.bot.get_channel(int(record.get("channel_id", 0)))
        if channel is None:
            return False
        kind = record.get("kind")
        limits = {"component": (5, 15), "assembly": (8, 12), "cinder": (10, 10), "planetary": (10, 10)}
        count, chance = limits.get(kind, (0, 0))
        cfg = self.cfg(guild_id)
        stage = "launch" if kind == "component" else kind
        count = int(cfg.get(f"economy.isd_{stage}_max_interceptors", count))
        chance = int(cfg.get(f"economy.isd_{stage}_intercept_chance", chance))
        name = str(
            record.get("body") or isd_state.COMPONENTS.get(record.get("component"), {}).get("name") or kind
        )
        embed = ui.base_embed(
            title="IMPERIAL STAR DESTROYER — PUBLIC DEFENSE WINDOW",
            description=f"<@{record['builder']}> committed **{kind}**: **{name}**.\nWindow `{window_id}` · {ui.human_duration(record['remaining_seconds'])} of bot-online time.\nUse `/isd intercept` with a prebuilt B-wing package. Up to {count} unique defenders; ordinary chance {chance}% each. "
            + (
                "Five defenders unlock a final 15% coordination roll. Only pre-sealed Raven Rock survives a server-wide Cinder hit."
                if kind == "cinder"
                else "All settlements and the claim on this body are at risk."
                if kind == "planetary"
                else "Construction may be disrupted by a successful interception."
            ),
        )
        filename = {
            "component": "isd-component-launch.png",
            "assembly": "isd-orbital-assembly.png",
            "cinder": "isd-cinder-charge.png",
            "planetary": "isd-planetary-bombard.png",
        }.get(kind)
        picture = self._art(self.cfg(guild_id), embed, filename) if filename else None
        kwargs = {
            "embed": embed,
            "content": "@everyone" if kind in {"cinder", "planetary"} else f"<@{record['builder']}>",
            "allowed_mentions": discord.AllowedMentions(everyone=kind in {"cinder", "planetary"}, users=True),
        }
        if picture:
            kwargs["file"] = picture
        try:
            await channel.send(**kwargs)
        except discord.HTTPException:
            return False
        finally:
            if picture:
                picture.close()
        async with self._lock(guild_id):
            current = isd_state.windows(self._doc(guild_id)).get(window_id)
            if current:
                current["announced"] = True
                await self._save(guild_id)
        return True

    def _pay_project(self, owner: Dict[str, Any], state: Dict[str, Any], cost: int) -> Tuple[int, int]:
        """Require the owner to pay at least 25%; escrow may cover the rest."""
        minimum_personal = (cost + 3) // 4
        escrow = min(int(state.get("project_funds", 0)), cost - minimum_personal)
        personal = cost - escrow
        if _net(owner) < personal:
            raise ValueError(
                f"This stage needs {_fmt(cost)} total. Coalition escrow covers {_fmt(escrow)}, so you must personally supply {_fmt(personal)}."
            )
        _take(owner, personal)
        state["project_funds"] = int(state.get("project_funds", 0)) - escrow
        state["owner_paid"] = int(state.get("owner_paid", 0)) + personal
        return (personal, escrow)

    async def _resolve_window(self, guild_id: int, window_id: str) -> None:
        cfg = self.cfg(guild_id)
        record: Optional[Dict[str, Any]] = None
        result = "invalid"
        component_key = ""
        winners: List[int] = []
        planetary_victims: List[int] = []
        cinder_victims: List[int] = []
        builder_id = 0
        canonical = self.econ.store.canonical_id(guild_id)
        async with self._lock(guild_id), self.econ.store._lock(canonical):
            doc = self._doc(guild_id)
            records = isd_state.windows(doc)
            raw = records.get(window_id)
            if not isinstance(raw, dict) or float(raw.get("remaining_seconds", 0)) > 0:
                return
            if raw.get("announced") is False:
                return
            successful = any((a.get("success") for a in raw.get("interceptors", []) if isinstance(a, dict)))
            if raw.get("kind") in {"cinder", "planetary"} and (not successful):
                self._write_backup(guild_id, doc, window_id)
            record = raw
            launch_escorts.release(doc, record)
            pre_resolution = copy.deepcopy(doc) if raw.get("kind") in {"cinder", "planetary"} else None
            builder_id = int(record.get("builder", 0))
            builder = self._user(guild_id, builder_id)
            state = isd_state.normalize(builder)
            attempts = [a for a in record.get("interceptors", []) if isinstance(a, dict)]
            winners = [int(a.get("user", 0)) for a in attempts if a.get("success")]
            stopped = bool(winners)
            if record.get("kind") == "cinder" and (not stopped):
                minimum = int(cfg.get("economy.isd_coordination_min_defenders", 5))
                if len(attempts) >= minimum:
                    stopped = random.randint(1, 100) <= int(cfg.get("economy.isd_coordination_chance", 15))
                    if stopped:
                        winners = [int(a.get("user", 0)) for a in attempts]
            kind = str(record.get("kind", ""))
            if kind == "component":
                component_key = str(record.get("component", ""))
                part = state.get("components", {}).get(component_key, {})
                if part.get("window_id") == window_id:
                    part.update(status="none" if stopped else "orbit", ready_at=None, window_id=None)
                state["active_window_id"] = None
                result = "component-stopped" if stopped else "component-orbit"
            elif kind == "assembly":
                state["active_window_id"] = None
                if stopped:
                    orbiting = [k for k, p in state["components"].items() if p.get("status") == "orbit"]
                    component_key = (
                        random.choice(orbiting) if orbiting else random.choice(list(isd_state.COMPONENTS))
                    )
                    state["components"][component_key] = isd_state.default_component()
                    state["assembling_until"] = None
                    state["operational"] = False
                    state["armed"] = False
                    result = "assembly-stopped"
                else:
                    result = "assembly-secure"
            elif kind == "cinder":
                state["active_window_id"] = None
                if stopped:
                    state["damaged"] = True
                    state["armed"] = False
                    result = "cinder-stopped"
                else:
                    result = "cinder-impact"
            elif kind == "planetary":
                state["active_window_id"] = None
                if stopped:
                    state["damaged"] = True
                    state["armed"] = False
                    result = "planetary-stopped"
                else:
                    result = "planetary-impact"
            records.pop(window_id, None)
            if result == "cinder-impact":
                cinder_victims = [
                    int(uid)
                    for uid, candidate in doc.get("users", {}).items()
                    if isinstance(candidate, dict) and int(uid) != builder_id
                ]
                self._apply_cinder_wipe(doc, record)
                self.econ.store.mark_dirty(guild_id)
                try:
                    self.econ.store._write(
                        self.econ.store._path(self.econ.store.canonical_id(guild_id)),
                        json.dumps(doc, indent=2, ensure_ascii=False),
                    )
                except Exception:
                    doc.clear()
                    doc.update(pre_resolution)
                    raise
            elif result == "planetary-impact":
                body_key = str(record.get("body", ""))
                planetary_victims = [
                    int(raw_id)
                    for raw_id, candidate in doc.get("users", {}).items()
                    if isinstance(candidate, dict)
                    and body_key in space_state.normalize(candidate).get("colonies", {})
                ]
                self._apply_planetary_wipe(doc, str(record.get("body", "")))
                self.econ.store.mark_dirty(guild_id)
                try:
                    self.econ.store._write(
                        self.econ.store._path(self.econ.store.canonical_id(guild_id)),
                        json.dumps(doc, indent=2, ensure_ascii=False),
                    )
                except Exception:
                    doc.clear()
                    doc.update(pre_resolution)
                    raise
            else:
                self.econ.store.mark_dirty(guild_id)
        if record is None:
            return
        if result not in {"cinder-impact", "planetary-impact"}:
            await self._save(guild_id)
        if result == "cinder-impact":
            await self.bot.ledger.record(
                guild_id, builder_id, 0, "isd-cinder-impact", after=0, detail=window_id
            )
            for victim_id in cinder_victims:
                await self.bot.ledger.record(
                    guild_id, builder_id, 0, "isd-cinder-impact", after=0, other=victim_id, detail=window_id
                )
        elif result == "planetary-impact":
            for victim_id in planetary_victims:
                if victim_id != builder_id:
                    await self.bot.ledger.record(
                        guild_id,
                        builder_id,
                        0,
                        "isd-planetary-impact",
                        after=0,
                        other=victim_id,
                        detail=str(record.get("body", "")),
                    )
        channel = self.bot.get_channel(int(record.get("channel_id", 0)))
        if channel is None:
            return
        defenders = " ".join((f"<@{uid}>" for uid in winners if uid)) or "None"
        if result == "component-stopped":
            name = isd_state.COMPONENTS.get(component_key, {}).get("name", component_key)
            embed = ui.base_embed(
                title="B-WING ASCENT INTERCEPT CONFIRMED",
                description=f"**{name}** was destroyed before reaching orbit.\n\nSuccessful defenders: {defenders}\nThe component and its launch must be purchased again.",
                color=ui.COLOR_OK,
            )
            await self._send_art(
                channel, cfg, embed, "isd-bwing-intercept.png", content=f"<@{builder_id}> {defenders}"
            )
        elif result == "component-orbit":
            name = isd_state.COMPONENTS.get(component_key, {}).get("name", component_key)
            embed = ui.base_embed(
                title="STAR DESTROYER COMPONENT REACHED ORBIT",
                description=f"<@{builder_id}>'s **{name}** survived the defensive window and is now secured in orbit.",
                color=ui.COLOR_WARN,
            )
            await self._send_art(channel, cfg, embed, "isd-component-launch.png", content=f"<@{builder_id}>")
        elif result == "assembly-stopped":
            name = isd_state.COMPONENTS.get(component_key, {}).get("name", component_key)
            embed = ui.base_embed(
                title="ORBITAL ASSEMBLY DISRUPTED",
                description=f"The defending fleet broke the docking operation and deorbited **{name}**.\n\nSuccessful defenders: {defenders}",
                color=ui.COLOR_OK,
            )
            await self._send_art(
                channel, cfg, embed, "isd-bwing-intercept.png", content=f"<@{builder_id}> {defenders}"
            )
        elif result == "assembly-secure":
            embed = ui.base_embed(
                title="ASSEMBLY PERIMETER SECURED",
                description="The public counter window closed without a successful hit. Construction continues toward full operational status.",
                color=ui.COLOR_WARN,
            )
            await self._send_art(channel, cfg, embed, "isd-orbital-assembly.png", content=f"<@{builder_id}>")
        elif result == "cinder-stopped":
            embed = ui.base_embed(
                title="OPERATION CINDER ABORTED",
                description=f"B-wing ion strikes disabled the Base Delta Zero array before firing. The 3 quintillion charge was lost and the Star Destroyer requires a 1 quintillion, 72-hour repair.\n\nDefending force: {defenders}",
                color=ui.COLOR_OK,
            )
            await self._send_art(
                channel,
                cfg,
                embed,
                "isd-cinder-intercepted.png",
                content=f"@everyone <@{builder_id}>",
                everyone=True,
            )
        elif result == "cinder-impact":
            embed = ui.base_embed(
                title="OPERATION CINDER — TOTAL EXTINCTION",
                description="The Base Delta Zero array completed its firing cycle. Every exposed donut, vault, item, plushie, fish, rod, upgrade, vehicle, munition, badge, title, country and megaproject has been erased.\n\nThe firing Star Destroyer overloaded and was destroyed. Only assets already sealed inside an operational Raven Rock survived.",
                color=ui.COLOR_BAD,
            )
            await self._send_art(
                channel, cfg, embed, "isd-cinder-impact.png", content="@everyone", everyone=True
            )
        elif result == "planetary-stopped":
            embed = ui.base_embed(
                title="PLANETARY BOMBARDMENT INTERCEPTED",
                description=f"B-wing ion packages disabled the attacking ISD before it struck **{record.get('body')}**. Its charge was lost and the ship needs repairs. Defenders: {defenders}.",
                color=ui.COLOR_OK,
            )
            await self._send_art(
                channel, cfg, embed, "isd-cinder-intercepted.png", content=f"<@{builder_id}> {defenders}"
            )
        elif result == "planetary-impact":
            target = space_state.body(self._doc(guild_id), str(record.get("body", "")))
            name = target.name if target else str(record.get("body", "unknown world"))
            embed = ui.base_embed(
                title="PLANETARY BOMBARDMENT — IMPACT",
                description=f"The ISD struck **{name}**. Every settlement and sovereignty claim on that body was destroyed. Other worlds, personal inventories and Raven Rock were not affected.",
                color=ui.COLOR_BAD,
            )
            await self._send_art(
                channel, cfg, embed, "isd-planetary-bombard.png", content="@everyone", everyone=True
            )

    def _write_backup(self, guild_id: int, doc: Dict[str, Any], strike_id: str) -> None:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        canonical = self.econ.store.canonical_id(guild_id)
        stamp = isd_state.utcnow().strftime("%Y%m%dT%H%M%SZ")
        path = BACKUP_DIR / f"{canonical}-{stamp}-{strike_id}.json"
        Storage._write(path, json.dumps(doc, indent=2, ensure_ascii=False))

    def _apply_cinder_wipe(self, doc: Dict[str, Any], record: Dict[str, Any]) -> None:
        snapshots = record.get("raven_snapshots", {})
        users = doc.get("users", {})
        if not isinstance(users, dict):
            users = doc["users"] = {}
        impact_at = isd_state.utcnow().isoformat()
        for uid, old in list(users.items()):
            if not isinstance(old, dict):
                continue
            fresh = _default_user(0)
            snapshot = snapshots.get(str(uid)) if isinstance(snapshots, dict) else None
            if isinstance(snapshot, dict) and snapshot.get("owned"):
                fresh["continuity"] = copy.deepcopy(snapshot)
                continuity_state.thor_impact(fresh, self.cfg(int(record.get("guild_id", 0))))
            fresh["cinder_wiped_at"] = impact_at
            users[str(uid)] = fresh
        doc["countries"] = {
            "territories": {},
            "history": [{"at": impact_at, "text": "Operation Cinder reset"}],
        }
        doc["coalitions"] = {"groups": {}, "invites": {}, "cooldowns": {}, "next_id": 1}
        doc["loans"] = []
        doc["next_loan"] = 1
        doc["wheel_pot"] = 0
        doc["roulette_jackpot"] = 0
        doc["events"] = {}
        doc["season"] = {}
        doc["thor_launches"] = {}
        doc["isd_windows"] = {}
        doc["deathstar_windows"] = {}
        doc["deathstar_notices"] = {}
        if isinstance(doc.get("deathstar_world"), dict):
            doc["deathstar_world"]["reconstructions"] = {}
        doc["space_galaxy"] = {"claims": {}, "discovered": {}}

    @staticmethod
    def _apply_planetary_wipe(doc: Dict[str, Any], body_key: str) -> None:
        galaxy = space_state.galaxy(doc)
        galaxy["claims"].pop(body_key, None)
        for user in doc.get("users", {}).values():
            if isinstance(user, dict):
                state = space_state.normalize(user)
                state["colonies"].pop(body_key, None)
                if (state.get("expedition") or {}).get("body") == body_key:
                    state["expedition"] = None

    @isd.command(name="fabricate", description="Fabricate one Star Destroyer component.")
    @app_commands.describe(component="Component to fabricate")
    @app_commands.choices(
        component=[
            app_commands.Choice(name=spec["name"], value=key) for key, spec in isd_state.COMPONENTS.items()
        ]
    )
    async def fabricate(self, interaction: discord.Interaction, component: app_commands.Choice[str]) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        user = self._user(gid, uid)
        state = self._state(user)
        if state.get("operational"):
            await ui.respond(
                interaction,
                embed=ui.warn_embed("Your Star Destroyer is already operational."),
                ephemeral=True,
            )
            return
        key = component.value
        part = state["components"][key]
        spec = isd_state.COMPONENTS[key]
        if part.get("status") != "none":
            await ui.respond(
                interaction,
                embed=ui.warn_embed(f"{spec['name']} is already {part.get('status')}."),
                ephemeral=True,
            )
            return
        if (
            sum((p.get("status") == "fabricating" for p in state["components"].values()))
            >= MAX_CONCURRENT_COMPONENT_FABRICATIONS
        ):
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"You may fabricate up to {MAX_CONCURRENT_COMPONENT_FABRICATIONS} different components at once."
                ),
                ephemeral=True,
            )
            return
        try:
            personal, escrow = self._pay_project(user, state, int(spec["cost"]))
        except ValueError as exc:
            await ui.respond(interaction, embed=ui.error_embed(str(exc)), ephemeral=True)
            return
        ready = isd_state.utcnow() + dt.timedelta(hours=float(spec["hours"]))
        part.update(status="fabricating", ready_at=ready.isoformat(), window_id=None)
        await self._save(gid)
        await self.bot.ledger.record(
            gid, uid, -personal, f"isd-fabricate-{key}", after=_net(user), detail=f"escrow {_fmt(escrow)}"
        )
        embed = ui.base_embed(
            title="IMPERIAL MEGAPROJECT FABRICATION",
            description=f"{interaction.user.mention} began **{spec['name']}**.\n\nCost: **{_fmt(int(spec['cost']))}**\nPersonal: **{_fmt(personal)}** · coalition escrow: **{_fmt(escrow)}**\nCompleted: <t:{int(ready.timestamp())}:F> · <t:{int(ready.timestamp())}:R>",
            color=cfg.color,
        )
        await self._respond_art(interaction, cfg, embed, "isd-shipyard.png", content=interaction.user.mention)

    @isd.command(
        name="contribute", description="Irreversibly fund your coalition ally's active Star Destroyer."
    )
    @app_commands.describe(
        amount="Amount such as 50q, 0.5qi, half or all",
        target="Coalition ally whose active Star Destroyer project receives the funds",
    )
    async def contribute(
        self, interaction: discord.Interaction, amount: str, target: Optional[discord.Member] = None
    ) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        projects = [
            project
            for project in self._projects(gid)
            if project[0] != uid and coalitions.are_allied(self._doc(gid), uid, project[0])
        ]
        found: Optional[Tuple[int, Dict[str, Any], Dict[str, Any]]] = None
        if target is not None:
            found = next((project for project in projects if project[0] == target.id), None)
            if found is None:
                await ui.respond(
                    interaction,
                    embed=ui.error_embed(
                        "That player is not a current coalition ally with an active Star Destroyer project."
                    ),
                    ephemeral=True,
                )
                return
        elif len(projects) == 1:
            found = projects[0]
        elif len(projects) > 1:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    "Several coalition projects are active. Select the ally you want to fund with `target`."
                ),
                ephemeral=True,
            )
            return
        if found is None:
            await ui.respond(
                interaction,
                embed=ui.error_embed("There is no allied project for you to fund."),
                ephemeral=True,
            )
            return
        owner_id, _, state = found
        user = self._user(gid, uid)
        try:
            value = parse_amount(amount, available=_net(user))
        except AmountParseError as exc:
            await ui.respond(interaction, embed=ui.error_embed(str(exc)), ephemeral=True)
            return
        cap = int(cfg.get("economy.isd_contribution_cap", 10**18))
        paid = int(state["contributions"].get(str(uid), 0))
        if value > cap - paid:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"Your lifetime contribution cap is {_fmt(cap)}; {_fmt(cap - paid)} remains."
                ),
                ephemeral=True,
            )
            return
        if value > _net(user):
            await ui.respond(
                interaction, embed=ui.error_embed("You do not have that many visible donuts."), ephemeral=True
            )
            return
        _take(user, value)
        state["project_funds"] += value
        state["contributions"][str(uid)] = paid + value
        await self._save(gid)
        await self.bot.ledger.record(gid, uid, -value, "isd-contribution", after=_net(user), other=owner_id)
        await ui.respond(
            interaction,
            embed=ui.ok_embed(
                f"Committed **{_fmt(value)}** to <@{owner_id}>'s megaproject. Contributions cannot be refunded."
            ),
        )

    @isd.command(
        name="launch", description="Launch one completed component into an online-time interception window."
    )
    @app_commands.describe(component="Completed component to launch")
    @app_commands.choices(
        component=[app_commands.Choice(name=s["name"], value=k) for k, s in isd_state.COMPONENTS.items()]
    )
    @app_commands.choices(
        escort=[app_commands.Choice(name=k.title(), value=k) for k in launch_escorts.SCREENING]
    )
    @app_commands.describe(
        escort="Optional idle launched escort at Earth; reserves finite packs",
        escort_owner="Yourself or a current coalition ally",
    )
    async def launch(
        self,
        interaction: discord.Interaction,
        component: app_commands.Choice[str],
        escort: Optional[app_commands.Choice[str]] = None,
        escort_owner: Optional[discord.Member] = None,
    ) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        user = self._user(gid, uid)
        state = self._state(user)
        key = component.value
        if state["components"][key].get("status") != "ready":
            await ui.respond(
                interaction, embed=ui.error_embed("That component is not ready for launch."), ephemeral=True
            )
            return
        if state.get("active_window_id"):
            await ui.respond(
                interaction,
                embed=ui.error_embed("Your megaproject already has an active defensive window."),
                ephemeral=True,
            )
            return
        cost = int(cfg.get("economy.isd_launch_cost", 250000000000000000))
        try:
            if escort_owner and (not escort):
                raise ValueError("Choose an escort craft as well as its owner.")
            prepared = (
                launch_escorts.prepare(
                    self._doc(gid),
                    uid,
                    (escort_owner or interaction.user).id,
                    escort.value,
                    isd_state.utcnow(),
                )
                if escort
                else None
            )
            personal, escrow = self._pay_project(user, state, cost)
        except ValueError as exc:
            await ui.respond(interaction, embed=ui.error_embed(str(exc)), ephemeral=True)
            return
        window_id = self._window_id("ISD-L")
        remaining = float(cfg.get("economy.isd_launch_window_minutes", 60)) * 60
        record = {
            "kind": "component",
            "guild_id": gid,
            "builder": uid,
            "component": key,
            "channel_id": interaction.channel_id,
            "remaining_seconds": remaining,
            "interceptors": [],
            "created_at": isd_state.utcnow().isoformat(),
            "announced": False,
        }
        isd_state.windows(self._doc(gid))[window_id] = record
        launch_escorts.reserve(self._doc(gid), record, window_id, prepared)
        state["components"][key].update(status="launching", ready_at=None, window_id=window_id)
        state["active_window_id"] = window_id
        await self._save(gid)
        await self.bot.ledger.record(
            gid,
            uid,
            -personal,
            "isd-component-launch",
            after=_net(user),
            detail=f"{key} · escrow {_fmt(escrow)}",
        )
        deadline = self._window_deadline(record)
        escort_note = (
            f"\nEscort: **{prepared['craft']}** · {prepared['screening']}% screening · {prepared['responses']} reserved responses."
            if prepared
            else ""
        )
        embed = ui.base_embed(
            title="IMPERIAL COMPONENT LAUNCH DETECTED",
            description=f"{interaction.user.mention} launched **{isd_state.COMPONENTS[key]['name']}** toward orbit.\n\nLaunch ID: `{window_id}`\nDefense closes: <t:{int(deadline.timestamp())}:R> while the bot is online\nUp to **5** unique defenders may commit a prebuilt B-wing package at **15%** each after any escort screening.\nLaunch cost: **{_fmt(cost)}**."
            + escort_note,
            color=ui.COLOR_WARN,
        )
        await self._respond_art(
            interaction, cfg, embed, "isd-component-launch.png", content=interaction.user.mention
        )
        record["announced"] = True
        await self._save(gid)

    @isd.command(
        name="assemble", description="Begin final orbital assembly after all six components reach orbit."
    )
    async def assemble(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        user = self._user(gid, uid)
        state = self._state(user)
        if state.get("operational") or state.get("assembling_until"):
            await ui.respond(
                interaction, embed=ui.warn_embed("Assembly is already active or complete."), ephemeral=True
            )
            return
        missing = [
            s["name"]
            for k, s in isd_state.COMPONENTS.items()
            if state["components"][k].get("status") != "orbit"
        ]
        if missing:
            await ui.respond(
                interaction,
                embed=ui.error_embed("Still required in orbit: " + ", ".join(missing)),
                ephemeral=True,
            )
            return
        cost = int(cfg.get("economy.isd_assembly_cost", 2 * 10**18))
        try:
            personal, escrow = self._pay_project(user, state, cost)
        except ValueError as exc:
            await ui.respond(interaction, embed=ui.error_embed(str(exc)), ephemeral=True)
            return
        ready = isd_state.utcnow() + dt.timedelta(hours=float(cfg.get("economy.isd_assembly_hours", 96)))
        window_id = self._window_id("ISD-A")
        remaining = float(cfg.get("economy.isd_assembly_window_minutes", 120)) * 60
        record = {
            "kind": "assembly",
            "guild_id": gid,
            "builder": uid,
            "channel_id": interaction.channel_id,
            "remaining_seconds": remaining,
            "interceptors": [],
            "created_at": isd_state.utcnow().isoformat(),
            "announced": False,
        }
        isd_state.windows(self._doc(gid))[window_id] = record
        state["assembling_until"] = ready.isoformat()
        state["active_window_id"] = window_id
        await self._save(gid)
        await self.bot.ledger.record(
            gid, uid, -personal, "isd-assembly", after=_net(user), detail=f"escrow {_fmt(escrow)}"
        )
        deadline = self._window_deadline(record)
        embed = ui.base_embed(
            title="IMPERIAL STAR DESTROYER — ORBITAL ASSEMBLY",
            description=f"All six sections have entered autonomous docking.\n\nAssembly completes: <t:{int(ready.timestamp())}:R>\nDefense window: <t:{int(deadline.timestamp())}:R> of bot-online time\nUp to **8** unique defenders may commit a B-wing package at **12%** each. A successful strike deorbits one random component.",
            color=ui.COLOR_WARN,
        )
        await self._respond_art(
            interaction, cfg, embed, "isd-orbital-assembly.png", content=interaction.user.mention
        )
        record["announced"] = True
        await self._save(gid)

    @isd.command(name="arm", description="Build the one-shot Operation Cinder charge.")
    async def arm(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        user = self._user(gid, uid)
        state = self._state(user)
        space_state.settle(user)
        if space_state.normalize(user).get("mode") != "attack" or space_state.normalize(user).get(
            "refit_until"
        ):
            await ui.respond(
                interaction,
                embed=ui.error_embed("Finish refitting to attack mode before arming."),
                ephemeral=True,
            )
            return
        if not state.get("operational") or state.get("damaged") or state.get("repairing_until"):
            await ui.respond(
                interaction,
                embed=ui.error_embed("The Star Destroyer must be operational and undamaged."),
                ephemeral=True,
            )
            return
        if state.get("armed") or state.get("arming_until"):
            await ui.respond(
                interaction,
                embed=ui.warn_embed("An Operation Cinder charge is already ready or being prepared."),
                ephemeral=True,
            )
            return
        cost = int(cfg.get("economy.isd_cinder_cost", 3 * 10**18))
        try:
            personal, escrow = self._pay_project(user, state, cost)
        except ValueError as exc:
            await ui.respond(interaction, embed=ui.error_embed(str(exc)), ephemeral=True)
            return
        ready = isd_state.utcnow() + dt.timedelta(hours=float(cfg.get("economy.isd_cinder_build_hours", 48)))
        state["arming_until"] = ready.isoformat()
        await self._save(gid)
        await self.bot.ledger.record(
            gid, uid, -personal, "isd-cinder-charge", after=_net(user), detail=f"escrow {_fmt(escrow)}"
        )
        embed = ui.base_embed(
            title="OPERATION CINDER CHARGE PREPARATION",
            description=f"A **{_fmt(cost)} donut** Base Delta Zero charge is entering the weapons grid.\n\nFiring-ready: <t:{int(ready.timestamp())}:F> · <t:{int(ready.timestamp())}:R>",
            color=ui.COLOR_WARN,
        )
        await self._respond_art(
            interaction, cfg, embed, "isd-cinder-charge.png", content=interaction.user.mention
        )

    @isd.command(name="fire", description="Commit Operation Cinder and begin the two-hour server warning.")
    async def fire(self, interaction: discord.Interaction) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        user = self._user(gid, uid)
        state = self._state(user)
        space_state.settle(user)
        if space_state.normalize(user).get("mode") != "attack" or space_state.normalize(user).get(
            "refit_until"
        ):
            await ui.respond(
                interaction,
                embed=ui.error_embed("Finish refitting to attack mode before firing."),
                ephemeral=True,
            )
            return
        if not state.get("operational") or not state.get("armed") or state.get("damaged"):
            await ui.respond(
                interaction,
                embed=ui.error_embed("No operational, charged Operation Cinder system is available."),
                ephemeral=True,
            )
            return
        if state.get("active_window_id") or isd_state.active_cinder(self._doc(gid)):
            await ui.respond(
                interaction,
                embed=ui.error_embed("A strategic defensive window is already active."),
                ephemeral=True,
            )
            return
        view = ui.ConfirmView(uid, timeout=45, danger_label="Authorize Operation Cinder")
        embed = ui.warn_embed(
            "This is irreversible. If the community fails to stop it, every exposed asset in the shared economy—including your Star Destroyer and your own holdings—will be erased. Only assets already sealed in Raven Rock survive. Confirm within 45 seconds.",
            title="FINAL FIRING AUTHORIZATION",
        )
        await ui.respond(interaction, embed=embed, view=view, ephemeral=True)
        await view.wait()
        if view.value is not True:
            return
        async with self._lock(gid):
            user = self._user(gid, uid)
            state = self._state(user)
            space_state.settle(user)
            if space_state.normalize(user).get("mode") != "attack" or space_state.normalize(user).get(
                "refit_until"
            ):
                if view.interaction:
                    await view.interaction.followup.send(
                        embed=ui.error_embed("The ISD mode changed before confirmation."), ephemeral=True
                    )
                return
            if not state.get("armed") or state.get("active_window_id"):
                if view.interaction:
                    await view.interaction.followup.send(
                        embed=ui.error_embed("Firing authority changed before confirmation."), ephemeral=True
                    )
                return
            snapshots: Dict[str, Any] = {}
            for raw_id, target in self.econ.all_users(gid).items():
                if not isinstance(target, dict):
                    continue
                continuity_state.settle(target, cfg)
                bunker = continuity_state.normalize(target)
                if bunker.get("owned"):
                    saved = copy.deepcopy(bunker)
                    saved.update(transfer_until=None, transfer_action=None, transfer_payload=None)
                    snapshots[str(raw_id)] = saved
            window_id = self._window_id("CINDER")
            remaining = float(cfg.get("economy.isd_cinder_window_minutes", 120)) * 60
            record = {
                "kind": "cinder",
                "guild_id": gid,
                "builder": uid,
                "channel_id": interaction.channel_id,
                "remaining_seconds": remaining,
                "interceptors": [],
                "raven_snapshots": snapshots,
                "created_at": isd_state.utcnow().isoformat(),
                "milestones_sent": [],
                "window_id": window_id,
                "announced": False,
            }
            isd_state.windows(self._doc(gid))[window_id] = record
            state["armed"] = False
            state["active_window_id"] = window_id
            await self._save(gid)
        deadline = self._window_deadline(record)
        warning = ui.base_embed(
            title="OPERATION CINDER DETECTED",
            description=f"{interaction.user.mention}'s Imperial Star Destroyer has begun charging its Base Delta Zero array.\n\n**Impact:** <t:{int(deadline.timestamp())}:F> · <t:{int(deadline.timestamp())}:R> of bot-online time\n**Window:** `{window_id}`\nUp to **10** unique non-allied players may commit one prebuilt B-wing package at **10%** each. Five defenders also unlock a final **15% Fleet Coordination** roll.\n\nEvery exposed player asset and country will be erased. Only pre-sealed Raven Rock contents survive.",
            color=ui.COLOR_BAD,
        )
        channel = interaction.channel
        if isinstance(channel, discord.abc.Messageable):
            await self._announce_window(gid, window_id)
        if view.interaction:
            await view.interaction.followup.send(
                embed=ui.ok_embed(f"Operation Cinder committed. Window `{window_id}` is live."),
                ephemeral=True,
            )

    @isd.command(name="counter-build", description="Build one B-wing Ion Assault Package for future windows.")
    async def counter_build(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        user = self._user(gid, uid)
        state = self._state(user)
        if state.get("counter_building_until"):
            await ui.respond(
                interaction,
                embed=ui.warn_embed("A B-wing package is already under construction."),
                ephemeral=True,
            )
            return
        cap = int(cfg.get("economy.isd_counter_capacity", 2))
        if int(state.get("counter_stock", 0)) >= cap:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(f"Your B-wing stockpile is full ({cap}/{cap})."),
                ephemeral=True,
            )
            return
        cost = int(cfg.get("economy.isd_counter_cost", 45000000000000000))
        if _net(user) < cost:
            await ui.respond(
                interaction, embed=ui.error_embed(f"A B-wing package costs **{_fmt(cost)}**."), ephemeral=True
            )
            return
        _take(user, cost)
        ready = isd_state.utcnow() + dt.timedelta(hours=float(cfg.get("economy.isd_counter_build_hours", 4)))
        state["counter_building_until"] = ready.isoformat()
        await self._save(gid)
        await self.bot.ledger.record(gid, uid, -cost, "isd-counter-build", after=_net(user))
        await ui.respond(
            interaction,
            embed=ui.base_embed(
                title="B-WING ION ASSAULT PACKAGE",
                description=f"Committed **{_fmt(cost)}**. Package ready <t:{int(ready.timestamp())}:F> · <t:{int(ready.timestamp())}:R>.\nCapacity: {int(state.get('counter_stock', 0))}/{cap} ready.",
                color=cfg.color,
            ),
        )

    async def _window_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        needle = (current or "").casefold()
        out = []
        for window_id, record in isd_state.windows(self._doc(interaction.guild_id)).items():
            if not isinstance(record, dict) or float(record.get("remaining_seconds", 0)) <= 0:
                continue
            label = f"{window_id} · {record.get('kind', 'window')}"
            if nyx.hidden(
                self._user(interaction.guild_id, record["builder"]), interaction.user.id, record["builder"]
            ):
                label = f"{window_id} · NYX-classified window"
            if not needle or needle in label.casefold():
                out.append(app_commands.Choice(name=label[:100], value=window_id))
        return out[:25]

    @isd.command(name="intercept", description="Commit one ready B-wing package to an active ISD window.")
    @app_commands.describe(window_id="Active launch, assembly or Operation Cinder window")
    @app_commands.autocomplete(window_id=_window_autocomplete)
    async def intercept(self, interaction: discord.Interaction, window_id: str) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        window_id = window_id.strip().upper()
        record = isd_state.windows(self._doc(gid)).get(window_id)
        if not isinstance(record, dict) or float(record.get("remaining_seconds", 0)) <= 0:
            await ui.respond(
                interaction,
                embed=ui.error_embed("That defensive window is no longer active."),
                ephemeral=True,
            )
            return
        builder = int(record.get("builder", 0))
        if uid == builder or coalitions.are_allied(self._doc(gid), uid, builder):
            await ui.respond(
                interaction,
                embed=ui.error_embed("You cannot intercept your own or a coalition ally's megaproject."),
                ephemeral=True,
            )
            return
        attempts = record.setdefault("interceptors", [])
        if any((int(a.get("user", 0)) == uid for a in attempts if isinstance(a, dict))):
            await ui.respond(
                interaction,
                embed=ui.error_embed("You already committed a package to this window."),
                ephemeral=True,
            )
            return
        kind = str(record.get("kind"))
        maximum = int(
            cfg.get(
                f"economy.isd_{('launch' if kind == 'component' else kind)}_max_interceptors",
                {"component": 5, "assembly": 8, "cinder": 10, "planetary": 10}.get(kind, 5),
            )
        )
        if len(attempts) >= maximum:
            await ui.respond(
                interaction, embed=ui.error_embed("All response slots are already committed."), ephemeral=True
            )
            return
        user = self._user(gid, uid)
        state = self._state(user)
        if int(state.get("counter_stock", 0)) <= 0:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    "No ready B-wing package. Build one in advance with `/isd counter-build`."
                ),
                ephemeral=True,
            )
            return
        chance_key = "launch" if kind == "component" else kind
        chance = int(
            cfg.get(
                f"economy.isd_{chance_key}_intercept_chance",
                {"component": 15, "assembly": 12, "cinder": 10, "planetary": 10}.get(kind, 10),
            )
        )
        screened = (
            launch_escorts.screen(self._doc(gid), record, isd_state.utcnow())
            if kind == "component"
            else False
        )
        roll = 100 if screened else random.randint(1, 100)
        attempts.append(
            {
                "user": uid,
                "success": roll <= chance and (not screened),
                "screened": screened,
                "committed_at": isd_state.utcnow().isoformat(),
            }
        )
        state["counter_stock"] = int(state.get("counter_stock", 0)) - 1
        await self._save(gid)
        await self.bot.ledger.record(
            gid, uid, 0, "isd-counter-fired", after=_net(user), other=builder, detail=window_id
        )
        deadline = self._window_deadline(record)
        embed = ui.base_embed(
            title="B-WING PACKAGE COMMITTED",
            description=f"{interaction.user.mention} committed a prebuilt ion-assault package against <@{builder}>.\n\nWindow: `{window_id}`\nPublic hit chance: **{chance}%** after any finite launch-escort screening\nResponses: **{len(attempts)}/{maximum}**\nOnline-time remaining: approximately **{ui.human_duration(record.get('remaining_seconds'))}**\nResult remains classified until resolution.",
            color=cfg.color,
        )
        await self._respond_art(
            interaction,
            cfg,
            embed,
            "isd-bwing-intercept.png",
            content=f"{interaction.user.mention} <@{builder}>",
        )

    @isd.command(name="repair", description="Repair an ion-disabled Star Destroyer.")
    async def repair(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        user = self._user(gid, uid)
        state = self._state(user)
        if not state.get("damaged") or state.get("repairing_until"):
            await ui.respond(
                interaction,
                embed=ui.error_embed("Your Star Destroyer is not awaiting a repair payment."),
                ephemeral=True,
            )
            return
        cost = int(cfg.get("economy.isd_repair_cost", 10**18))
        if _net(user) < cost:
            await ui.respond(
                interaction, embed=ui.error_embed(f"Repairs cost **{_fmt(cost)}**."), ephemeral=True
            )
            return
        _take(user, cost)
        ready = isd_state.utcnow() + dt.timedelta(hours=float(cfg.get("economy.isd_repair_hours", 72)))
        state["repairing_until"] = ready.isoformat()
        await self._save(gid)
        await self.bot.ledger.record(gid, uid, -cost, "isd-repair", after=_net(user))
        await ui.respond(
            interaction,
            embed=ui.base_embed(
                title="STAR DESTROYER REPAIR AUTHORIZED",
                description=f"Committed **{_fmt(cost)}**. Weapons and propulsion return <t:{int(ready.timestamp())}:F> · <t:{int(ready.timestamp())}:R>.",
                color=cfg.color,
            ),
        )

    @isd.command(
        name="status",
        description="Show your or another player's Star Destroyer project and your counter stock.",
    )
    @app_commands.describe(target="Player whose public Star Destroyer project you want to inspect")
    async def status(self, interaction: discord.Interaction, target: Optional[discord.Member] = None) -> None:
        nyx.protect_report(interaction, self.econ, (target or interaction.user).id, interaction.user.id)
        cfg = await self._guard(interaction, channel=False)
        if cfg is None:
            return
        checked_at = isd_state.utcnow()
        selected = target or interaction.user
        selected_user = self._user(interaction.guild_id, selected.id)
        if nyx.hidden(selected_user, interaction.user.id, selected.id, checked_at):
            await ui.respond(interaction, embed=nyx.censored_embed(), ephemeral=True)
            return
        state = self._state(selected_user)
        projects = [
            project
            for project in self._projects(interaction.guild_id)
            if project[0] == interaction.user.id or not nyx.active(project[1], checked_at)
        ]
        own_user = self._user(interaction.guild_id, interaction.user.id)
        own = self._state(own_user)
        overview = []
        construction = []
        support = []
        if not isd_state.project_started(state):
            text = f"<@{selected.id}> has no active Imperial Star Destroyer megaproject."
            overview.append(("📋 Project overview", text))
        else:
            space_state.settle(selected_user)
            expedition = space_state.normalize(selected_user)
            readiness = (
                "repairing"
                if state.get("repairing_until")
                else "damaged"
                if state.get("damaged")
                else "operational"
                if state.get("operational")
                else "building"
                if state.get("assembling_until")
                else "🧩 Components in progress · ship not assembled"
            )
            overview.append(
                (
                    "📋 Project overview",
                    "\n".join(
                        [
                            status_ui.line("Project owner", f"<@{selected.id}>"),
                            status_ui.line("State", status_ui.state(readiness)),
                            status_ui.line("Coalition escrow", _fmt(state.get("project_funds", 0))),
                            status_ui.line("Role", str(expedition["mode"]).title()),
                            status_ui.line(
                                "Location", status_ui.body_name(expedition["location"], space_state.BODIES)
                            ),
                        ]
                    ),
                )
            )
            construction = status_ui.component_fields(
                isd_state.COMPONENTS,
                state["components"],
                windows=isd_state.windows(self._doc(interaction.guild_id)),
                format_deadline=lambda raw: _status_deadline(isd_state.parse_time(raw), checked_at),
            )
            payload = (
                "🏗️ Building"
                if state.get("arming_until")
                else "🟢 Ready"
                if state.get("armed")
                else "⚪ Empty"
            )
            support.append(("🚀 Cinder charge", status_ui.line("Payload", payload)))
            timers = []
            for raw, label in (
                (state.get("assembling_until"), "Orbital assembly"),
                (expedition.get("refit_until"), "Role refit"),
                (state.get("arming_until"), "Cinder charge integration"),
                (state.get("repairing_until"), "Ion-damage repair"),
            ):
                if raw:
                    timers.append(status_ui.line(label, status_ui.deadline(raw)))
            support.append(("⏳ Timers and repairs", "\n".join(timers) or "No timers running."))
            active = state.get("active_window_id")
            if active:
                rec = isd_state.windows(self._doc(interaction.guild_id)).get(active, {})
                overview.append(
                    (
                        "⚠️ Active interception window",
                        status_ui.line("Window ID", f"`{active}`")
                        + "\n"
                        + status_ui.line(
                            "Remaining",
                            f"{ui.human_duration(rec.get('remaining_seconds'))} bot-online time remaining when checked · pauses offline",
                        ),
                    )
                )
            if selected.id == interaction.user.id:
                next_action = (
                    "Use `/isd repair` to recover from ion damage."
                    if state.get("damaged")
                    else "Use `/isd guide` for attack and exploration options."
                    if state.get("operational")
                    else f"Deploy ready components with `/isd launch`, then `/isd assemble` once all {len(isd_state.COMPONENTS)} are in orbit."
                )
                overview.append(("➡️ Next action", next_action))
        overview.append(("🌌 Server projects", status_ui.line("Active player projects", len(projects))))
        counter = [status_ui.line("Ready B-wing packages", f"{int(own.get('counter_stock', 0))}/2")]
        building = isd_state.parse_time(own.get("counter_building_until"))
        if building:
            counter.append(status_ui.line("Next package", status_ui.deadline(building)))
        support.append(("🛡️ YOUR interception packages", "\n".join(counter)))
        pages = status_ui.report(
            "🌌 IMPERIAL STAR DESTROYER STATUS",
            [
                ("Overview", overview),
                ("Construction", construction),
                ("Payload, timers and your counters", support),
            ],
            color=cfg.color,
            checked_at=checked_at,
            footer="Countdowns are snapshots when checked · builds advance offline; interception windows pause offline · /isd guide",
        )
        private_report = nyx.active(selected_user, checked_at) or nyx.active(own_user, checked_at)

        def page_guard(_index):
            if nyx.hidden(selected_user, interaction.user.id, selected.id):
                return nyx.censored_embed()
            if not private_report and (nyx.active(selected_user) or nyx.active(own_user)):
                return nyx.censored_embed()
            return None

        await ui.respond(
            interaction,
            embed=pages[0],
            view=status_ui.pager(pages, interaction.user.id, page_guard=page_guard),
            ephemeral=private_report,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @isd.command(
        name="guide", description="Learn the complete Imperial Star Destroyer and Operation Cinder system."
    )
    async def guide(self, interaction: discord.Interaction) -> None:
        cfg = await self._guard(interaction, channel=False)
        if cfg is None:
            return
        pages = guides.isd_pages(cfg, color=cfg.color)
        await ui.respond(
            interaction,
            embed=pages[0],
            view=space_guides.SpaceGuidePager(pages, interaction.user.id),
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ImperialStarDestroyerCog(bot))
