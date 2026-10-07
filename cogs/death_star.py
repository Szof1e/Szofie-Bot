"""DS-1 megaproject, artificial-moon economy and public orbital counterplay."""

from __future__ import annotations
import asyncio
import copy
import datetime as dt
import json
import logging
import random
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Optional
import discord
from discord import app_commands
from discord.ext import commands, tasks
from szofie import launch_escorts
from szofie import coalitions, deathstar as ds, imperial_star_destroyer as isd, nyx, space, space_guides, ui
from szofie import status_ui as status
from szofie.betting import AmountParseError, parse_amount
from szofie.storage import Storage

log = logging.getLogger("szofie.deathstar")
ART = Path(__file__).resolve().parent.parent / "assets/death_star"
BACKUPS = Path(__file__).resolve().parent.parent / "backups/deathstar-impacts"
fmt = ui.format_donuts


class DeathStarAlert(discord.ui.View):
    """Public inspect/intercept buttons; every click checks the actual requester."""

    def __init__(self, cog, gid: int, window_id: str):
        super().__init__(timeout=None)
        self.cog, self.gid, self.window_id = (cog, gid, window_id)
        for button in self.children:
            button.custom_id = f"deathstar:{window_id}:{button.label.lower()}"

    @discord.ui.button(label="Inspect", style=discord.ButtonStyle.secondary)
    async def inspect(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.inspect_window(interaction, self.window_id)

    @discord.ui.button(label="Intercept", style=discord.ButtonStyle.danger)
    async def intercept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.commit_interceptor(interaction, self.window_id)


class DeathStarCog(commands.Cog, name="Death Star"):
    deathstar = app_commands.Group(
        name="deathstar", description="Build an artificial moon or destroy a rival's civilisation."
    )

    def __init__(self, bot: commands.Bot):
        self.bot, self.econ = (bot, bot.economy)
        self._locks: Dict[int, asyncio.Lock] = {}
        self._last_tick = time.monotonic()

    async def cog_load(self):
        self.sweep.start()

    def cog_unload(self):
        self.sweep.cancel()

    def lock(self, gid):
        return self._locks.setdefault(self.econ.store.canonical_id(gid), asyncio.Lock())

    def doc(self, gid):
        return self.econ.store.load(gid)

    def user(self, gid, uid):
        return self.econ.user(
            gid, uid, int(self.bot.config.for_guild(gid).get("economy.starting_balance", 100))
        )

    async def guard(self, interaction, *, channel=True):
        if not interaction.guild_id:
            await interaction.response.send_message(
                content="Death Star commands require a server.", ephemeral=True
            )
            return False
        cfg = self.bot.config.for_guild(interaction.guild_id)
        if not cfg.get("economy.enabled", True):
            await interaction.response.send_message(content="The economy is disabled here.", ephemeral=True)
            return False
        restricted = cfg.get("economy.channel")
        if channel and restricted and (interaction.channel_id != int(restricted)):
            await interaction.response.send_message(
                content=f"Use <#{int(restricted)}> for megaproject commands.", ephemeral=True
            )
            return False
        return True

    @staticmethod
    def ready(state):
        if not state["operational"] or state["damaged"] or state["repairing_until"]:
            raise ValueError("An operational, undamaged Death Star is required.")
        if state["active_window_id"] or state["travel_until"]:
            raise ValueError("Finish the active interception window or voyage first.")

    @staticmethod
    def deadline(raw):
        due = ds.at(raw)
        return f"<t:{int(due.timestamp())}:F>" if due else "none"

    async def image_reply(self, interaction, embed, image=None, *, pages=None):
        kwargs = {"embed": embed, "allowed_mentions": discord.AllowedMentions.none()}
        if pages:
            kwargs["view"] = status.pager(pages, interaction.user.id)
        if image and (ART / image).is_file():
            embed.set_image(url=f"attachment://{image}")
            kwargs["file"] = discord.File(str(ART / image), filename=image)
        await ui.respond(
            interaction, ephemeral=bool(interaction.extras.get("szofie_deferred_private")), **kwargs
        )

    async def run(self, interaction, action, *, public=True):
        """All same-project mutations revalidate under the canonical economy lock."""
        nyx.protect_report(interaction, self.econ)
        if not await self.guard(interaction):
            return
        await ui.defer_response(interaction, ephemeral=not public)
        gid, uid = (interaction.guild_id, interaction.user.id)
        pages = None
        async with self.lock(gid):
            doc = self.doc(gid)
            previous = copy.deepcopy(doc)
            user, current = (self.user(gid, uid), ds.now())
            ds.settle_world(doc, current)
            payout = ds.settle(user, current)
            state = ds.normalize(user)
            try:
                message, image, reason, delta = action(doc, user, state, current)
                if isinstance(message, list):
                    pages, embed = (message, message[0])
                else:
                    embed = ui.base_embed(title="DEATH STAR — DS-1", description=message)
            except (ValueError, KeyError) as exc:
                message, image, reason, delta = (str(exc), None, None, 0)
                embed = ui.error_embed(message)
            try:
                await self.econ.save(gid)
            except Exception:
                doc.clear()
                doc.update(previous)
                self.econ.store.mark_dirty(gid)
                raise
        if payout:
            await self.bot.ledger.record(gid, uid, payout, "deathstar-production", after=ds.available(user))
        if reason:
            await self.bot.ledger.record(gid, uid, delta, reason, after=ds.available(user), detail=message)
        try:
            if pages:
                await self.image_reply(interaction, embed, image, pages=pages)
            else:
                await self.image_reply(interaction, embed, image)
        except discord.HTTPException:
            log.warning("Death Star acknowledgement delivery failed; committed state remains durable")
        await self.deliver_notices(gid)

    def open_window(
        self, doc, state, interaction, kind, minutes, *, component=None, body=None, sovereign=None
    ):
        window_id = f"DS-{secrets.token_hex(5).upper()}"
        record = {
            "id": window_id,
            "guild_id": interaction.guild_id,
            "channel_id": interaction.channel_id,
            "builder": interaction.user.id,
            "kind": kind,
            "component": component,
            "body": body,
            "sovereign": sovereign,
            "remaining_seconds": minutes * 60,
            "announced": False,
            "interceptors": [],
            "created_at": ds.now().isoformat(),
        }
        ds.windows(doc)[window_id] = record
        state["active_window_id"] = window_id
        return window_id

    def warning(self, record):
        kind = record["kind"]
        rate = 10 if kind == "fire" else 15
        remaining = ui.human_duration(record["remaining_seconds"])
        scope = (
            f"Target body: `{record['body']}`. Locked-in sovereign: {('<@' + str(record['sovereign']) + '>' if record.get('sovereign') else 'none')}\nA successful shot erases every local settlement and resets the sovereign's entire civilisation. Other residents lose only their local sites; Raven Rock contents survive.\n\n"
            if kind == "fire"
            else ""
        )
        consequence = {
            "component": "A successful intercept destroys only this deploying section; the builder must fabricate and deploy it again.",
            "assembly": "A successful intercept sabotages assembly. All eight sections survive; repair is required before assembly resumes.",
            "fire": "A successful intercept cancels the shot, spends the charge and disables the station until repaired.",
        }.get(kind, "")
        return ui.base_embed(
            title="DEATH STAR — PUBLIC DEFENSIVE WINDOW",
            description=f"<@{record['builder']}> opened **{kind}** window `{record['id']}`.\n\n"
            + scope
            + f"Remaining: **{remaining} of bot-online time**. Offline time pauses this window.\nUp to five different defenders may commit one ready Alliance Assault Squadron each at **{rate}%**.\n{consequence}\n\n**Required counter:** `/deathstar squadron` (10 quintillion, 12h). Prepare it in advance; ordinary X-wings, B-wings, AA and GBI/EKV do not work here. Each attempt spends one package, hit or miss.\nComponent deployments may reserve one finite Earth launch escort; screening is rolled before the normal intercept.\nUse **Intercept** or `/deathstar intercept window:{record['id']}`. You cannot intercept your own or a coalition ally's project. See `/deathstar guide` → **Intercept sections step by step**.",
            color=ui.COLOR_BAD,
        )

    def intel_warning(self, record, requester_id):
        builder = self.user(record["guild_id"], record["builder"])
        if not nyx.hidden(builder, requester_id, record["builder"]):
            return self.warning(record)
        return ui.base_embed(
            title="CLASSIFIED DEFENSIVE WINDOW",
            description=f"{nyx.CENSORED}\n\nWindow `{record['id']}` · {ui.human_duration(record['remaining_seconds'])} bot-online time remains. A ready Alliance Assault Squadron may still intercept with `/deathstar intercept window:{record['id']}`. Public counterplay is not disabled by NYX.",
        )

    async def deliver_notices(self, gid):
        for window_id in list(ds.windows(self.doc(gid))):
            async with self.lock(gid):
                record = ds.windows(self.doc(gid)).get(window_id)
                if not record or record["announced"]:
                    continue
                channel = self.bot.get_channel(int(record["channel_id"]))
                if channel is None:
                    continue
                embed = self.warning(record)
                image = "deathstar-charge.png" if record["kind"] == "fire" else "deathstar-construction.png"
                kwargs = {
                    "embed": embed,
                    "view": DeathStarAlert(self, gid, window_id),
                    "allowed_mentions": discord.AllowedMentions(
                        everyone=record["kind"] == "fire", users=True
                    ),
                }
                if record["kind"] == "fire":
                    kwargs["content"] = "@everyone"
                if (ART / image).is_file():
                    embed.set_image(url=f"attachment://{image}")
                    kwargs["file"] = discord.File(str(ART / image), filename=image)
                try:
                    message = await channel.send(**kwargs)
                except discord.HTTPException:
                    continue
                record.update(announced=True, message_id=message.id)
                await self.econ.save(gid)
        for notice_id in list(self.doc(gid).get("deathstar_notices", {})):
            async with self.lock(gid):
                notice = self.doc(gid).get("deathstar_notices", {}).get(notice_id)
                if not notice:
                    continue
                channel = self.bot.get_channel(int(notice["channel_id"]))
                if channel is None:
                    continue
                embed = ui.base_embed(
                    title=notice["title"], description=notice["description"], color=ui.COLOR_WARN
                )
                kwargs = {
                    "embed": embed,
                    "allowed_mentions": discord.AllowedMentions(everyone=notice["everyone"]),
                }
                if notice["everyone"]:
                    kwargs["content"] = "@everyone"
                if (ART / notice["image"]).is_file():
                    embed.set_image(url=f"attachment://{notice['image']}")
                    kwargs["file"] = discord.File(str(ART / notice["image"]), filename=notice["image"])
                try:
                    await channel.send(**kwargs)
                except discord.HTTPException:
                    continue
                self.doc(gid)["deathstar_notices"].pop(notice_id, None)
                await self.econ.save(gid)

    async def inspect_window(self, interaction, window_id):
        if not await self.guard(interaction, channel=False):
            return
        record = ds.windows(self.doc(interaction.guild_id)).get(window_id.upper())
        if not record:
            await interaction.response.send_message(
                content="That defensive window has ended.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=self.intel_warning(record, interaction.user.id),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def commit_interceptor(self, interaction, window_id):
        def action(doc, user, state, current):
            record = ds.windows(doc).get(window_id.strip().upper())
            if not record or not record["announced"] or record["remaining_seconds"] <= 0:
                raise ValueError("That window is not open. Check `/deathstar windows`.")
            builder, uid = (int(record["builder"]), interaction.user.id)
            if uid == builder or coalitions.are_allied(doc, uid, builder):
                raise ValueError("You cannot intercept your own or an allied project.")
            attempts = record["interceptors"]
            if len(attempts) >= ds.MAX_DEFENDERS or any((a["user"] == uid for a in attempts)):
                raise ValueError("Five defenders maximum; each defender gets one attempt per window.")
            if state["squadron_stock"] < 1:
                raise ValueError("Build an Alliance Assault Squadron first with `/deathstar squadron`.")
            state["squadron_stock"] -= 1
            chance = 10 if record["kind"] == "fire" else 15
            screened = launch_escorts.screen(doc, record, current) if record["kind"] == "component" else False
            success = not screened and random.randint(1, 100) <= chance
            attempts.append({"user": uid, "success": success, "screened": screened})
            return (
                f"Squadron committed to `{record['id']}` at {chance}% after any launch-escort screening. {('Confirmed hit; the operation will be stopped when the window resolves.' if success else 'Attack missed.')}",
                "deathstar-interception.png",
                "deathstar-interception",
                0,
            )

        await self.run(interaction, action)

    async def resolve_window(self, gid, window_id):
        victims = []
        reason = None
        async with self.lock(gid):
            doc = self.doc(gid)
            record = ds.windows(doc).get(window_id)
            if not record or not record["announced"] or record["remaining_seconds"] > 0:
                return
            builder = int(record["builder"])
            user = doc.get("users", {}).get(str(builder))
            if not isinstance(user, dict) or ds.normalize(user)["active_window_id"] != window_id:
                launch_escorts.release(doc, record)
                ds.windows(doc).pop(window_id, None)
                await self.econ.save(gid)
                return
            state, current = (ds.normalize(user), ds.now())
            stopped = any((a["success"] for a in record["interceptors"]))
            kind = record["kind"]
            title = "DEATH STAR — OPERATION RESOLVED"
            image = "deathstar-interception.png" if stopped else "deathstar-construction.png"
            if kind == "fire" and (not stopped):
                affected_ids = [
                    int(uid)
                    for uid, u in doc.get("users", {}).items()
                    if isinstance(u, dict) and record["body"] in (u.get("space") or {}).get("colonies", {})
                ]
                if record.get("sovereign"):
                    affected_ids.append(int(record["sovereign"]))
                current_sovereign = space.galaxy(doc)["claims"].get(record["body"])
                sovereignty_changed = str(current_sovereign) != str(record.get("sovereign"))
                if (
                    sovereignty_changed
                    or ds.destroyed(doc, record["body"])
                    or any(
                        (uid == builder or coalitions.are_allied(doc, builder, uid) for uid in affected_ids)
                    )
                ):
                    kind = "cancelled"
            if kind == "fire" and (not stopped):
                canonical = self.econ.store.canonical_id(gid)
                BACKUPS.mkdir(parents=True, exist_ok=True)
                backup_path = BACKUPS / f"{canonical}-{window_id}.json"
                Storage._write(backup_path, json.dumps(doc, indent=2, ensure_ascii=False))
            previous = copy.deepcopy(doc)
            try:
                launch_escorts.release(doc, record)
                payout = ds.settle(user, current)
                state["active_window_id"] = None
                state["income_at"] = current.isoformat()
                if kind == "component":
                    part = state["components"][record["component"]]
                    part.update(
                        ds.component()
                        if stopped
                        else {"status": "orbit", "ready_at": None, "window_id": None}
                    )
                    description = f"Section **{ds.COMPONENTS[record['component']]['name']}** {('was destroyed during deployment.' if stopped else 'reached orbit.')}"
                elif kind == "assembly":
                    if stopped:
                        state.update(
                            damaged=True,
                            damage_kind="assembly",
                            assembly_remaining=max(
                                0, (ds.at(state["assembling_until"]) - current).total_seconds()
                            ),
                            assembling_until=None,
                        )
                        description = "Assembly sabotaged. All eight sections survive. Repair costs 100 quintillion and takes 24h; remaining assembly time then resumes."
                    else:
                        description = "Assembly perimeter secured. The original 72-hour assembly deadline remains unchanged."
                elif kind == "fire":
                    if stopped:
                        state.update(damaged=True, damage_kind="station", charged=False)
                        description = "Thermal-exhaust-port assault disabled the station. Charge lost. Repair: 200 quintillion, 48h. GDP is suspended until repair completes."
                    else:
                        victims = ds.wipe_planet(
                            doc,
                            record["body"],
                            builder,
                            record["sovereign"],
                            self.bot.config.for_guild(gid),
                            current,
                        )
                        description = f"**{record['body']} has been destroyed and is now a debris field.**\nCivilisation reset: {('<@' + str(record['sovereign']) + '>' if record.get('sovereign') else 'no sovereign')}. Every local settlement is gone. Other residents lost only local assets. Only the sovereign's sealed Raven Rock contents survived their account reset. Reconstruction creates an unclaimed, undeveloped body; it does not restore lost holdings."
                        title, image, reason = (
                            "DEATH STAR — PLANETARY ANNIHILATION",
                            "deathstar-impact.png",
                            "deathstar-planet-hit",
                        )
                else:
                    description = "Strike cancelled: the target became a debris field, sovereignty or diplomacy changed. The committed charge remains spent and the firing cooldown remains active."
                ds.windows(doc).pop(window_id, None)
                doc.setdefault("deathstar_notices", {})[window_id] = {
                    "channel_id": record["channel_id"],
                    "title": title,
                    "description": description,
                    "image": image,
                    "everyone": record["kind"] == "fire",
                }
                if kind == "assembly" and (not stopped) and (ds.at(state["assembling_until"]) <= current):
                    state["assembling_until"] = current.isoformat()
                ds.settle(user, current)
                await self.econ.save(gid)
            except Exception:
                doc.clear()
                doc.update(previous)
                self.econ.store.mark_dirty(gid)
                raise
        if payout:
            await self.bot.ledger.record(
                gid, builder, payout, "deathstar-production", after=ds.available(user)
            )
        if reason:
            for victim in victims:
                await self.bot.ledger.record(
                    gid,
                    victim,
                    0,
                    reason,
                    actor=builder,
                    other=builder,
                    after=ds.available(self.doc(gid)["users"][str(victim)]),
                    detail=f"{record['body']} · {window_id}",
                )
        await self.deliver_notices(gid)

    async def tick(self, gid, elapsed):
        if not self.bot.config.for_guild(gid).get("economy.enabled", True):
            return
        due, payouts = ([], [])
        async with self.lock(gid):
            doc, current = (self.doc(gid), ds.now())
            ds.settle_world(doc, current)
            for uid, user in doc.get("users", {}).items():
                if isinstance(user, dict) and isinstance(user.get("death_star"), dict):
                    payout = ds.settle(user, current)
                    if payout:
                        payouts.append((int(uid), payout, ds.available(user)))
            for window_id, record in ds.windows(doc).items():
                if record["announced"]:
                    record["remaining_seconds"] = max(0, record["remaining_seconds"] - elapsed)
                    if record["remaining_seconds"] == 0:
                        due.append(window_id)
            await self.econ.save(gid)
        for uid, payout, balance in payouts:
            await self.bot.ledger.record(gid, uid, payout, "deathstar-production", after=balance)
        for window_id in due:
            await self.resolve_window(gid, window_id)
        await self.deliver_notices(gid)

    @tasks.loop(seconds=30)
    async def sweep(self):
        current = time.monotonic()
        elapsed = min(30.0, max(0, current - self._last_tick))
        self._last_tick = current
        seen = set()
        for guild in self.bot.guilds:
            canonical = self.econ.store.canonical_id(guild.id)
            if canonical in seen:
                continue
            seen.add(canonical)
            try:
                await self.tick(guild.id, elapsed)
            except Exception:
                log.exception("Death Star sweep failed for one economy; will retry")

    @sweep.before_loop
    async def before_sweep(self):
        await self.bot.wait_until_ready()
        self._last_tick = time.monotonic()
        for guild in self.bot.guilds:
            for record in ds.windows(self.doc(guild.id)).values():
                if record.get("message_id") and record.get("announced"):
                    self.bot.add_view(
                        DeathStarAlert(self, guild.id, record["id"]), message_id=record["message_id"]
                    )

    @deathstar.command(
        name="build", description="Fabricate one of eight Death Star sections; up to three at once."
    )
    @app_commands.choices(
        component=[app_commands.Choice(name=v["name"], value=k) for k, v in ds.COMPONENTS.items()]
    )
    async def build(self, interaction: discord.Interaction, component: app_commands.Choice[str]):
        def action(doc, user, state, current):
            ship = isd.normalize(user)
            isd.settle(user, current)
            if not ship["operational"] or ship["damaged"] or (not state["transport_owned"]):
                raise ValueError(
                    "Keep an operational ISD and build `/deathstar transport` first. Neither prerequisite is consumed."
                )
            if state["operational"] or state["assembling_until"] or state["damaged"]:
                raise ValueError("The station is already assembled, assembling or awaiting repair.")
            key = component.value
            if state["components"][key]["status"] != "none":
                raise ValueError("That section is already underway or complete.")
            if sum((p["status"] == "fabricating" for p in state["components"].values())) >= ds.MAX_BUILDING:
                raise ValueError("Three sections can fabricate at once. Wait for one to finish.")
            spec = ds.COMPONENTS[key]
            paid = ds.pay_project(user, state, spec["cost"])
            due = current + dt.timedelta(hours=spec["hours"])
            state["components"][key].update(status="fabricating", ready_at=due.isoformat())
            return (
                f"Fabricating **{spec['name']}**. Cost: {fmt(spec['cost'])}. Ready {self.deadline(due.isoformat())}.",
                "deathstar-construction.png",
                "deathstar-fabricate",
                -paid,
            )

        await self.run(interaction, action)

    @deathstar.command(
        name="transport",
        description=f"Build Imperial Heavy Transport: {fmt(ds.TRANSPORT_COST)} donuts / 24h.",
    )
    async def transport(self, interaction: discord.Interaction):
        def action(doc, user, state, current):
            if state["transport_owned"] or state["transport_until"]:
                raise ValueError("Your dedicated transport is already ready or building.")
            ds.pay(user, ds.TRANSPORT_COST)
            state["transport_deployed"] = False
            state["transport_until"] = (current + dt.timedelta(hours=24)).isoformat()
            return (
                f"Imperial Heavy Transport: {fmt(ds.TRANSPORT_COST)}, ready {self.deadline(state['transport_until'])}.",
                "deathstar-construction.png",
                "deathstar-transport",
                -ds.TRANSPORT_COST,
            )

        await self.run(interaction, action)

    @deathstar.command(
        name="contribute", description="Irreversibly fund your coalition ally's Death Star project."
    )
    async def contribute(
        self,
        interaction: discord.Interaction,
        target: discord.Member,
        amount: str,
        material: Optional[str] = None,
    ):
        def action(doc, user, state, current):
            if target.bot or not coalitions.are_allied(doc, interaction.user.id, target.id):
                raise ValueError("Choose a human coalition ally, not yourself.")
            ally = doc.get("users", {}).get(str(target.id))
            if not ally or not isinstance(ally.get("death_star"), dict):
                raise ValueError("Your ally must start their project/transport first.")
            project = ds.normalize(ally)
            if material:
                source = state["cargo"]["materials"]
                value = parse_amount(amount, available=int(source.get(material, 0)))
                if value <= 0 or value > int(source.get(material, 0)):
                    raise ValueError("Not enough of that material in your station cargo.")
                source[material] -= value
                project["cargo"]["materials"][material] = (
                    int(project["cargo"]["materials"].get(material, 0)) + value
                )
                return (
                    f"Contributed {value} {material} to <@{target.id}>'s station cargo.",
                    None,
                    "deathstar-contribution",
                    0,
                )
            value = parse_amount(amount, available=ds.available(user))
            if value <= 0:
                raise ValueError("Contribution must be positive.")
            ds.pay(user, value)
            project["project_funds"] += value
            project["contributions"][str(interaction.user.id)] = (
                int(project["contributions"].get(str(interaction.user.id), 0)) + value
            )
            return (
                f"Committed {fmt(value)} to <@{target.id}>. No refunds; the builder funds at least 25% of each stage.",
                None,
                "deathstar-contribution",
                -value,
            )

        await self.run(interaction, action)

    @deathstar.command(
        name="deploy", description="Deploy a completed section through a two-hour defensive window."
    )
    @app_commands.choices(
        component=[app_commands.Choice(name=v["name"], value=k) for k, v in ds.COMPONENTS.items()]
    )
    @app_commands.choices(
        escort=[app_commands.Choice(name=k.title(), value=k) for k in launch_escorts.SCREENING]
    )
    @app_commands.describe(
        escort="Optional idle launched escort at Earth", escort_owner="Yourself or a current coalition ally"
    )
    async def deploy(
        self,
        interaction: discord.Interaction,
        component: app_commands.Choice[str],
        escort: Optional[app_commands.Choice[str]] = None,
        escort_owner: Optional[discord.Member] = None,
    ):
        def action(doc, user, state, current):
            if not state["transport_owned"] or state["active_window_id"]:
                raise ValueError("A ready heavy transport and no active project window are required.")
            key = component.value
            if state["components"][key]["status"] != "ready":
                raise ValueError("That section is not ready.")
            if escort_owner and (not escort):
                raise ValueError("Choose an escort craft as well as its owner.")
            prepared = (
                launch_escorts.prepare(
                    doc, interaction.user.id, (escort_owner or interaction.user).id, escort.value, current
                )
                if escort
                else None
            )
            paid = ds.pay_project(user, state, ds.DEPLOY_COST)
            window = self.open_window(doc, state, interaction, "component", 120, component=key)
            launch_escorts.reserve(doc, ds.windows(doc)[window], window, prepared)
            state["transport_deployed"] = True
            state["components"][key].update(status="launching", window_id=window)
            escort_note = (
                f" Escort: {prepared['craft']}, {prepared['screening']}% screening, {prepared['responses']} reserved responses."
                if prepared
                else ""
            )
            return (
                f"Deployment `{window}` committed for {fmt(ds.DEPLOY_COST)}. Two bot-online hours; five defenders at 15% each after any escort screening."
                + escort_note,
                "deathstar-construction.png",
                "deathstar-deploy",
                -paid,
            )

        await self.run(interaction, action)

    @deathstar.command(
        name="assemble", description="Assemble all eight orbiting sections; 72h with a 4h sabotage window."
    )
    async def assemble(self, interaction: discord.Interaction):
        def action(doc, user, state, current):
            if (
                state["active_window_id"]
                or state["operational"]
                or state["assembling_until"]
                or state["damaged"]
            ):
                raise ValueError("Finish the current operation or repair first.")
            if any((p["status"] != "orbit" for p in state["components"].values())):
                raise ValueError("All eight sections must be in orbit.")
            paid = ds.pay_project(user, state, ds.ASSEMBLY_COST)
            state["assembling_until"] = (current + dt.timedelta(hours=72)).isoformat()
            window = self.open_window(doc, state, interaction, "assembly", 240)
            return (
                f"Assembly `{window}`: {fmt(ds.ASSEMBLY_COST)}. Completion {self.deadline(state['assembling_until'])}; four bot-online hours of counterplay at 15% per squadron.",
                "deathstar-construction.png",
                "deathstar-assembly",
                -paid,
            )

        await self.run(interaction, action)

    @deathstar.command(
        name="squadron", description="Prepare one Alliance Assault Squadron; 10 quintillion, 12h, cap three."
    )
    async def squadron(self, interaction: discord.Interaction):
        def action(doc, user, state, current):
            if state["squadron_until"] or state["squadron_stock"] >= ds.MAX_SQUADRONS:
                raise ValueError("Finish the current squadron or use one of your three ready packages.")
            ds.pay(user, ds.SQUADRON_COST)
            state["squadron_until"] = (current + dt.timedelta(hours=ds.SQUADRON_HOURS)).isoformat()
            return (
                f"Alliance X-wing/Y-wing package preparing. Cost {fmt(ds.SQUADRON_COST)}; ready {self.deadline(state['squadron_until'])}.",
                "deathstar-interception.png",
                "deathstar-squadron",
                -ds.SQUADRON_COST,
            )

        await self.run(interaction, action)

    @deathstar.command(
        name="intercept",
        description="Spend one ready Alliance Assault Squadron against a Death Star deployment, assembly or shot.",
    )
    @app_commands.describe(
        window="Copy the DS-... ID from /deathstar windows or the warning; not a player or part name"
    )
    async def intercept(self, interaction: discord.Interaction, window: str):
        await self.commit_interceptor(interaction, window)

    @deathstar.command(name="windows", description="List active public Death Star interception windows.")
    async def window_list(self, interaction: discord.Interaction):
        if not await self.guard(interaction, channel=False):
            return
        records = list(ds.windows(self.doc(interaction.guild_id)).values())
        pages = (
            [ui.base_embed(title="Death Star windows", description="No active defensive windows.")]
            if not records
            else [self.intel_warning(r, interaction.user.id) for r in records]
        )
        page_guard = (
            (lambda index: self.intel_warning(records[index], interaction.user.id)) if records else None
        )
        await interaction.response.send_message(
            embed=pages[0],
            view=ui.Paginator(pages, interaction.user.id, page_guard=page_guard),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @deathstar.command(
        name="charge", description="Prepare one 100-quintillion superlaser charge over 24 hours."
    )
    async def charge(self, interaction: discord.Interaction):
        def action(doc, user, state, current):
            self.ready(state)
            if state["charged"] or state["charge_until"]:
                raise ValueError("One charge maximum, including preparation.")
            ds.pay(user, ds.CHARGE_COST)
            state["charge_until"] = (current + dt.timedelta(hours=ds.CHARGE_HOURS)).isoformat()
            return (
                f"Superlaser charge purchased for {fmt(ds.CHARGE_COST)}. Ready {self.deadline(state['charge_until'])}.",
                "deathstar-charge.png",
                "deathstar-charge",
                -ds.CHARGE_COST,
            )

        await self.run(interaction, action)

    async def body_autocomplete(self, interaction: discord.Interaction, current: str):
        doc = self.doc(interaction.guild_id) if interaction.guild_id else {}
        bodies = list(space.BODIES.values()) + [space.body(doc, k) for k in space.galaxy(doc)["discovered"]]
        return [
            app_commands.Choice(name=b.name[:100], value=b.key)
            for b in bodies
            if b and (current.lower() in b.name.lower() or current.lower() in b.key)
        ][:25]

    @deathstar.command(
        name="travel", description="Move the artificial moon; travel costs 1 quintillion per hour."
    )
    @app_commands.autocomplete(body=body_autocomplete)
    async def travel(self, interaction: discord.Interaction, body: str):
        def action(doc, user, state, current):
            self.ready(state)
            target = space.body(doc, body.lower().strip())
            if not target or target.key == "sun":
                raise ValueError("Choose a known body other than the Sun.")
            if target.key == state["location"]:
                raise ValueError("The station is already there.")
            hours = space.travel_hours(target.zone)
            cost = hours * ds.QI
            ds.pay(user, cost)
            state.update(
                destination=target.key, travel_until=(current + dt.timedelta(hours=hours)).isoformat()
            )
            return (
                f"Travelling to **{target.name}** for {fmt(cost)}. Arrival {self.deadline(state['travel_until'])}.",
                "deathstar-operational.png",
                "deathstar-travel",
                -cost,
            )

        await self.run(interaction, action)

    def fire_preflight(self, doc, state, uid, current):
        self.ready(state)
        if not state["charged"]:
            raise ValueError("Prepare a superlaser charge first.")
        prior = ds.at(state["last_fire_at"])
        if prior and prior + dt.timedelta(hours=72) > current:
            raise ValueError("The 72-hour firing cooldown is still active.")
        target = space.body(doc, state["location"])
        if not target or target.key in {"earth", "sun"} or (not space.colonisable(target)):
            raise ValueError("Travel to a colonisable planet, moon or asteroid other than Earth first.")
        if ds.destroyed(doc, target.key):
            raise ValueError("That body is already a debris field.")
        sovereign_raw = space.galaxy(doc)["claims"].get(target.key)
        sovereign = int(sovereign_raw) if sovereign_raw is not None else None
        ids = [
            int(k)
            for k, u in doc.get("users", {}).items()
            if isinstance(u, dict) and target.key in (u.get("space") or {}).get("colonies", {})
        ]
        if sovereign is not None:
            ids.append(sovereign)
        if any((v == uid or coalitions.are_allied(doc, uid, v) for v in ids)):
            raise ValueError("You cannot destroy your own or an allied civilisation/settlement.")
        return (target, sovereign)

    @deathstar.command(
        name="fire", description="Confirm a planet kill and sovereign civilisation reset; public 2h warning."
    )
    async def fire(self, interaction: discord.Interaction):
        if not await self.guard(interaction):
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        async with self.lock(interaction.guild_id):
            doc, user = (self.doc(interaction.guild_id), self.user(interaction.guild_id, interaction.user.id))
            payout = ds.settle(user)
            try:
                target, sovereign = self.fire_preflight(
                    doc, ds.normalize(user), interaction.user.id, ds.now()
                )
                error = None
            except ValueError as exc:
                error = str(exc)
            await self.econ.save(interaction.guild_id)
        if payout:
            await self.bot.ledger.record(
                interaction.guild_id,
                interaction.user.id,
                payout,
                "deathstar-production",
                after=ds.available(user),
            )
        if error:
            await interaction.edit_original_response(content=error)
            return
        view = ui.ConfirmView(interaction.user.id, timeout=45, danger_label="Destroy planet")
        await interaction.edit_original_response(
            embed=ui.warn_embed(
                f"Destroy **{target.name}** and all local settlements. Sovereign: {('<@' + str(sovereign) + '>' if sovereign else 'none')}.\n\nThe sovereign also loses wallet/bank/Deep Vault, items, plushies, rods, fish, vehicles, ammunition, THOR/ISD/Death Star projects, countries, other celestial claims, career progress, upgrades, titles and badges. Only sealed Raven Rock contents survive, locked for 24h. Other residents lose only local sites.\n\nCharge consumed and 72h cooldown starts on commitment, even if intercepted. A two-hour bot-online counter window and server-wide warning open first. A pre-impact backup is retained."
            ),
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await view.wait()
        if view.value is not True or not view.interaction:
            return
        async with self.lock(interaction.guild_id):
            doc = self.doc(interaction.guild_id)
            previous = copy.deepcopy(doc)
            user = self.user(interaction.guild_id, interaction.user.id)
            payout = ds.settle(user)
            state = ds.normalize(user)
            try:
                if not self.bot.config.for_guild(interaction.guild_id).get("economy.enabled", True):
                    raise ValueError("The economy was disabled while you were confirming.")
                actual, actual_sovereign = self.fire_preflight(doc, state, interaction.user.id, ds.now())
                if actual.key != target.key or actual_sovereign != sovereign:
                    raise ValueError(
                        "Target or sovereignty changed. Run the command again to confirm the new scope."
                    )
                state.update(charged=False, last_fire_at=ds.now().isoformat())
                window = self.open_window(
                    doc, state, interaction, "fire", 120, body=target.key, sovereign=sovereign
                )
                state["income_at"] = ds.now().isoformat()
                error = None
            except ValueError as exc:
                error = str(exc)
            try:
                await self.econ.save(interaction.guild_id)
            except Exception:
                doc.clear()
                doc.update(previous)
                self.econ.store.mark_dirty(interaction.guild_id)
                raise
        if payout:
            await self.bot.ledger.record(
                interaction.guild_id,
                interaction.user.id,
                payout,
                "deathstar-production",
                after=ds.available(user),
            )
        if error:
            await view.interaction.followup.send(content=error, ephemeral=True)
            return
        await self.bot.ledger.record(
            interaction.guild_id,
            interaction.user.id,
            0,
            "deathstar-fire",
            after=ds.available(user),
            detail=f"{target.key} · {window}",
        )
        await view.interaction.followup.send(
            content=f"Planet kill `{window}` committed. GDP pauses during charging. The public timer starts only after the warning is posted.",
            ephemeral=True,
        )
        await self.deliver_notices(interaction.guild_id)

    @deathstar.command(
        name="repair",
        description="Repair a disabled station or sabotaged assembly without losing its sections.",
    )
    async def repair(self, interaction: discord.Interaction):
        def action(doc, user, state, current):
            if not state["damaged"] or state["repairing_until"] or state["active_window_id"]:
                raise ValueError("Nothing is awaiting repair, or repair/window is already active.")
            assembly = state["damage_kind"] == "assembly"
            cost, hours = (ds.ASSEMBLY_REPAIR_COST, 24) if assembly else (ds.REPAIR_COST, 48)
            ds.pay(user, cost)
            state["repairing_until"] = (current + dt.timedelta(hours=hours)).isoformat()
            return (
                f"Repair costs {fmt(cost)}. Completion {self.deadline(state['repairing_until'])}. Existing sections survive.",
                "deathstar-construction.png",
                "deathstar-repair",
                -cost,
            )

        await self.run(interaction, action)

    @deathstar.command(
        name="develop",
        description="Upgrade the artificial moon's GDP; ten levels, materials and a 12h timer.",
    )
    async def develop(self, interaction: discord.Interaction):
        def action(doc, user, state, current):
            self.ready(state)
            if state["development_until"] or state["development"] >= 10:
                raise ValueError("Development is already underway or all ten levels are complete.")
            if sum(state["cargo"]["materials"].values()) < ds.DEVELOP_MATERIALS:
                raise ValueError("Load 25 space materials into station cargo first.")
            cost = 50 * ds.QI * (state["development"] + 1)
            ds.pay(user, cost)
            space.consume_materials(state["cargo"], ds.DEVELOP_MATERIALS)
            state["development_until"] = (current + dt.timedelta(hours=12)).isoformat()
            return (
                f"Industrial level {state['development'] + 1}: {fmt(cost)} + 25 materials, completion {self.deadline(state['development_until'])}. Income rises by 4.5 quintillion/day after completion.",
                "deathstar-industry.png",
                "deathstar-development",
                -cost,
            )

        await self.run(interaction, action)

    @deathstar.command(
        name="cargo", description="Load/unload station cargo; it is exposed storage, not a Raven Rock bunker."
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Load", value="load"),
            app_commands.Choice(name="Unload", value="unload"),
        ],
        kind=[
            app_commands.Choice(name=k.title(), value=k)
            for k in ("donuts", "inventory", "plushies", "fish", "rods", "vehicles", "materials")
        ],
    )
    async def cargo(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        kind: app_commands.Choice[str],
        amount: str = "all",
        asset: Optional[str] = None,
    ):
        def operation(doc, user, state, current):
            self.ready(state)
            loading = action.value == "load"
            source = user if loading else state["cargo"]
            if kind.value == "materials":
                expedition = space.normalize(user)
                if expedition["location"] != state["location"] or expedition["travel_until"]:
                    raise ValueError("Your material-carrying ISD must be at the same body as the Death Star.")
                if loading:
                    source = expedition["cargo"]
            if kind.value == "donuts":
                have = int(source.get("donuts", 0))
            elif kind.value == "vehicles":
                have = int(bool(source.get("vehicles", {}).get(asset, {}).get("owned")))
            else:
                have = int(source.get(kind.value, {}).get(asset, 0))
            value = parse_amount(amount, available=have)
            if kind.value == "inventory" and (not loading):
                from cogs.economy import ITEM_HOLD_CAPS

                cap_key = ITEM_HOLD_CAPS.get(asset)
                if cap_key and value + int(user.get("inventory", {}).get(asset, 0)) > int(
                    self.bot.config.for_guild(interaction.guild_id).get(f"economy.{cap_key}", 0)
                ):
                    raise ValueError("Unloading would exceed that item's normal holding cap.")
            ds.transfer_cargo(user, action.value, kind.value, asset, value)
            delta = (-value if loading else value) if kind.value == "donuts" else 0
            return (
                f"{action.name}: {(fmt(value) if kind.value == 'donuts' else str(value))} {asset or kind.value}. Station cargo is not protected from civilisation resets.",
                "deathstar-industry.png",
                "deathstar-cargo",
                delta,
            )

        await self.run(interaction, operation, public=False)

    @deathstar.command(
        name="reconstruct",
        description="Recreate a destroyed body: 250 quintillion, 100 materials, seven days.",
    )
    @app_commands.autocomplete(body=body_autocomplete)
    async def reconstruct(self, interaction: discord.Interaction, body: str):
        def action(doc, user, state, current):
            self.ready(state)
            key = body.strip().lower()
            root = ds.world(doc)
            if state["location"] != key or key not in root["destroyed"]:
                raise ValueError("Bring your operational station to the debris field first.")
            if key in root["reconstructions"]:
                raise ValueError("A reconstruction project is already active for that body.")
            if sum(state["cargo"]["materials"].values()) < ds.RECONSTRUCTION_MATERIALS:
                raise ValueError("Load 100 space materials into station cargo first.")
            ds.pay(user, ds.RECONSTRUCTION_COST)
            space.consume_materials(state["cargo"], ds.RECONSTRUCTION_MATERIALS)
            ready = current + dt.timedelta(days=7)
            root["reconstructions"][key] = {"builder": interaction.user.id, "ready_at": ready.isoformat()}
            return (
                f"Reconstructing `{key}` for 250 quintillion + 100 materials. Completion {self.deadline(ready.isoformat())}. Returns unclaimed and undeveloped, with no asset refunds.",
                "deathstar-reconstruction.png",
                "deathstar-reconstruction",
                -ds.RECONSTRUCTION_COST,
            )

        await self.run(interaction, action)

    @deathstar.command(
        name="salvage", description="Salvage a debris field once every six hours for space-only materials."
    )
    async def salvage(self, interaction: discord.Interaction):
        def action(doc, user, state, current):
            self.ready(state)
            if not ds.destroyed(doc, state["location"]):
                raise ValueError("Travel to a destroyed body's debris field first.")
            anchors = ds.world(doc)["salvage"]
            token = f"{interaction.user.id}:{state['location']}"
            prior = ds.at(anchors.get(token))
            if prior and prior + dt.timedelta(hours=6) > current:
                raise ValueError("This debris field can be salvaged once per six hours per player.")
            anchors[token] = current.isoformat()
            quantity = random.randint(5, 10)
            state["cargo"]["materials"]["planetary-salvage"] = (
                int(state["cargo"]["materials"].get("planetary-salvage", 0)) + quantity
            )
            return (
                f"Recovered {quantity} units of planetary salvage. No GDP or donut production exists in a debris field.",
                "deathstar-reconstruction.png",
                "deathstar-salvage",
                0,
            )

        await self.run(interaction, action)

    @deathstar.command(
        name="status", description="Privately inspect your station, moon GDP, deadlines and cargo."
    )
    async def status(self, interaction: discord.Interaction):
        def action(doc, user, state, current):
            label = (
                "repairing"
                if state["repairing_until"]
                else "damaged"
                if state["damaged"]
                else "operational"
                if state["operational"]
                else "building"
                if state["assembling_until"]
                else "none"
            )
            readiness = status.state(label)
            if label == "none" and any((p["status"] != "none" for p in state["components"].values())):
                readiness = "🏗️ Components in progress · station not assembled"
            overview = [
                (
                    "📋 Station overview",
                    "\n".join(
                        [
                            status.line("State", readiness),
                            status.line("Artificial moon ID", f"`deathstar-{interaction.user.id}`"),
                            status.line("Location", status.body_name(state["location"], space.BODIES)),
                            status.line("Project escrow", fmt(state["project_funds"])),
                        ]
                    ),
                )
            ]
            record = ds.windows(doc).get(state["active_window_id"])
            if record:
                overview.append(
                    (
                        "⚠️ Active interception window",
                        status.line("Window ID", f"`{record['id']}`")
                        + "\n"
                        + status.line(
                            "Remaining",
                            f"{ui.human_duration(record['remaining_seconds'])} bot-online time remaining when checked · pauses offline",
                        ),
                    )
                )
            if state["damaged"]:
                next_action = "Repair your station with `/deathstar repair`."
            elif not state["operational"]:
                next_action = "Check Construction; deploy ready sections with `/deathstar deploy`, then `/deathstar assemble` once all eight are in orbit."
            else:
                next_action = "Use `/deathstar guide` for development, travel, charging and firing."
            overview.append(("➡️ Next action", next_action))
            construction = status.component_fields(
                ds.COMPONENTS, state["components"], windows=ds.windows(doc)
            )
            timers = []
            for key, name in (
                ("assembling_until", "Orbital assembly"),
                ("repairing_until", "Station repair"),
                ("charge_until", "Superlaser charging"),
                ("squadron_until", "Squadron preparation"),
                ("transport_until", "Heavy Transport construction"),
                ("travel_until", "Voyage"),
                ("development_until", "Moon development"),
            ):
                if state[key]:
                    timers.append(status.line(name, status.deadline(state[key])))
            prior = ds.at(state["last_fire_at"])
            if prior:
                timers.append(status.line("Next firing", status.deadline(prior + dt.timedelta(hours=72))))
            support = [
                (
                    "🚀 Weapons and interception packages",
                    "\n".join(
                        [
                            status.line("Superlaser charge", "🟢 Ready" if state["charged"] else "⚪ Empty"),
                            status.line("Alliance Assault Squadrons", f"{state['squadron_stock']}/3 ready"),
                        ]
                    ),
                ),
                (
                    "🚛 Heavy Transport",
                    "\n".join(
                        [
                            status.line(
                                "State",
                                "🏗️ Building"
                                if state["transport_until"]
                                else "🟢 Ready"
                                if state["transport_owned"]
                                else "⚪ Not built",
                            ),
                            status.line(
                                "Deployment",
                                "🛰️ Deployed · safe from THOR"
                                if state["transport_deployed"]
                                else "Grounded · exposed to THOR",
                            ),
                        ]
                    ),
                ),
                ("⏳ Timers and repairs", "\n".join(timers) or "No timers running."),
            ]
            cargo = state["cargo"]
            production = (
                "🟢 Active"
                if state["operational"] and (not state["damaged"]) and (not state["active_window_id"])
                else "⏸️ Paused / not operational"
            )
            economy = [
                (
                    "💰 Artificial-moon economy",
                    "\n".join(
                        [
                            status.line("GDP index", fmt(ds.gdp(state))),
                            status.line("Wallet production", f"{fmt(ds.gdp(state) // 80)} / day"),
                            status.line("Production state", production),
                            status.line("Development", f"{state['development']}/10"),
                        ]
                    ),
                ),
                (
                    "📦 Onboard cargo",
                    "\n".join(
                        [
                            status.line("Donuts", fmt(cargo["donuts"])),
                            status.line("Items", sum(cargo["inventory"].values())),
                            status.line("Plushies", sum(cargo["plushies"].values())),
                            status.line("Rods", len(cargo["rods"])),
                            status.line("Vehicles", len(cargo["vehicles"])),
                            status.line("Materials", sum(cargo["materials"].values())),
                        ]
                    ),
                ),
            ]
            pages = status.report(
                "🌑 DEATH STAR STATUS",
                [
                    ("Overview", overview),
                    ("Construction", construction),
                    ("Weapons, transport and timers", support),
                    ("Economy and cargo", economy),
                ],
                checked_at=current,
                footer="Private station report · builds advance offline; interception windows pause offline · /deathstar guide",
            )
            return (pages, "deathstar-operational.png", None, 0)

        await self.run(interaction, action, public=False)

    def guide_pages(self):
        sections = [
            (
                "Start here",
                f"DS-1 is an artificial moon and a planet-killer. No server-wide project limit. First build an operational ISD, then `/deathstar transport` ({fmt(ds.TRANSPORT_COST)} donuts, 24h). They support construction and are not consumed. Station stages total **{fmt(ds.project_cost())}**, excluding transport, travel, development and charges.\n\n**Route:** operational ISD → dedicated Heavy Transport → build sections → deploy each through counterplay → assemble → develop GDP or charge/fire. The Heavy Transport is NOT a GR-75, and ordinary `/space fleet launch` does not deploy Death Star sections. It stays grounded (THOR can destroy it) until its first `/deathstar deploy`, which launches it with the section. Ready/building Alliance squadron stock is also exposed to THOR; committed interception flights are not recalled. Use `/space guide` for the interaction map and ordinary exploration/colonies.",
            ),
            (
                "Fabricate and deploy",
                "Use `/deathstar build component` for eight different sections; three fabricate simultaneously.\n"
                + "\n".join(
                    (
                        f"• {v['name']}: {fmt(v['cost'])}, {v['hours'] // 24} days"
                        for v in ds.COMPONENTS.values()
                    )
                )
                + f"\nThen `/deathstar deploy component`: {fmt(ds.DEPLOY_COST)} each and a two-hour public interception window. Each section must deploy separately; only one project window may be active at a time. Optional `escort` / `escort_owner` reserves your own or a coalition ally's idle launched Earth ship: X-wing 15%/1 response, A-wing 25%/2, Hammerhead 35%/3 or Arquitens 30%/2. One ready pack per counter attempt: screen first, then roll 15% if unscreened. No stacking, swapping or refilling during the window; unused packs return afterward. Assembly/shots are not escorted. Before completion, a landed ordinary THOR strike has a {ds.THOR_CONSTRUCTION_DESTRUCTION_PCT}% chance to destroy at most ONE section still fabricating; a lost section must be built again. Ready, deploying, orbiting and assembled sections are excluded from that THOR roll. An Alliance Assault Squadron is the counter, NOT an ordinary fighter or B-wing. A successful interception destroys only that deploying section; rebuild and redeploy it. Build timers advance offline; defensive windows only advance online after the warning is posted. Next topic: **Intercept sections step by step**.",
            ),
            (
                "Intercept sections step by step",
                f"**What do I build?** `/deathstar squadron` creates one **Alliance Assault Squadron**: {fmt(ds.SQUADRON_COST)} donuts, {ds.SQUADRON_HOURS}h, up to {ds.MAX_SQUADRONS} ready packages. Only one package can be preparing at a time. You need NO ISD, Death Star, Heavy Transport, ordinary X-wing/Y-wing hull or separate payload purchase. Payment uses wallet then bank, not Deep Vault.\n\n**1. Prepare early:** run `/deathstar squadron`, then wait until it is ready. A 12h build cannot finish inside a fresh 2h deployment window. `/arsenal` shows your ready stock.\n**2. Find a launch:** when a builder runs `/deathstar deploy component:<section>`, the public warning opens a **2 bot-online-hour** window. Run `/deathstar windows` and copy its `DS-...` ID.\n**3. Attack that window:** `/deathstar intercept window:<DS-ID>` or the warning's **Intercept** button. Select a window ID, NOT the builder or component name. No ship travel or local escort is required.\n**4. Know the limits:** {ds.MAX_DEFENDERS} different defenders maximum; one attempt per defender per window. Each deployment attempt has **15%** success and consumes one ready package, hit or miss. Stockpiling three does NOT give three attempts at the same window. Self/coalition-allied projects cannot be intercepted.\n\n**On a hit:** that section is destroyed when the window resolves; all other sections remain. The builder must pay to fabricate and deploy it again. With no successful intercept it reaches orbit. You cannot use this command against a section still fabricating/ready on the ground, or one already in orbit after its window closes. Windows pause while the bot is offline. NYX may censor the builder/section, but the window ID and interception command remain usable.",
            ),
            (
                "Assemble and fund",
                f"All eight sections must reach orbit. `/deathstar assemble`: {fmt(ds.ASSEMBLY_COST)}, 72h, with a four-hour sabotage window. A hit damages assembly, not all sections; repair is 100 quintillion and 24h before remaining assembly resumes. `/deathstar contribute target amount` irreversibly funds a coalition ally; the builder personally pays at least 25% of each construction/deployment/assembly stage. Specify `material` to contribute units from your station material hold instead of donuts.",
            ),
            (
                "Moon GDP and development",
                "Production starts only after operational assembly: GDP 400 quintillion → 5 quintillion/day. Ten `/deathstar develop` levels add 4.5 quintillion/day each, up to 50 quintillion/day. Next-level cost: 50 quintillion × level, plus 25 materials and 12h. Earnings go automatically to wallet. Seven-day offline catch-up maximum; disabled stations and public charging windows produce nothing. Income is settled at the old rate before an upgrade changes GDP.",
            ),
            (
                "Travel, materials and cargo",
                "`/deathstar travel body`: 2/4/8/12h by zone, 1 quintillion/hour. Use `/deathstar cargo load/unload kind amount asset` for donuts, items, plushies, fish, rods, conventional vehicles or materials. Amounts support all/half and suffixes. Materials move from your ISD's material hold at the same body. Whole vehicles carry ammo/upgrades; no duplicate hulls or rods. Station cargo is exposed, not a bunker.",
            ),
            (
                "Charge and fire",
                "`/deathstar charge`: 100 quintillion, 24h; capacity one. Travel to the target, then `/deathstar fire`. It names the planet and sovereign and requires confirmation. Commitment spends the charge, starts a 72h cooldown and pings the server. Two bot-online hours of counterplay follow. Without a successful intercept, the shot lands. No minimum victim balance. Earth and Sun are excluded; self/allied settlements cannot be targeted.",
            ),
            (
                "Destruction scope",
                "Every settlement on the target disappears; it becomes a debris field with no GDP or claim. The named sovereign ALSO loses wallet, bank, Deep Vault, items, plushies, rods/enchants, fish, vehicles, ammo, THOR/ISD/Death Star projects, countries, other colonies, economic upgrades, career progress, titles and badges. Other residents lose only their local site. Only contents already stored in operational Raven Rock survive; retrieval locks for 24h. Audit history and loan liabilities remain. Each impact has an exact pre-wipe backup.",
            ),
            (
                "Defend and repair",
                "The SAME dedicated Alliance Assault Squadron counters three DIFFERENT windows. Each consumes one ready package; five unique defenders maximum, one attempt per defender per window.\n\n**Section deployment:** 2 bot-online hours, **15%** per attempt. A hit destroys ONLY the deployed section; fabricate and deploy it again.\n**Final assembly:** 4 bot-online hours, **15%** per attempt. A hit damages assembly; all eight sections survive. `/deathstar repair`: 100 quintillion, 24h; remaining assembly time then resumes.\n**Superlaser firing:** 2 bot-online hours, **10%** per attempt. A hit stops the shot, spends the charge and disables the station/GDP. `/deathstar repair`: 200 quintillion, 48h.\n\nExisting AA/THOR/ICBMs and ordinary X-wing/B-wing packages cannot intercept DS-1 windows.\n\nTHOR is NOT a deployment interceptor: it separately has a 1% construction-destruction roll against one still-fabricating section on a landed ordinary strike. NYX-degraded strikes do not apply that roll.\n\n**Worked defense:** `/deathstar squadron` → wait 12h → `/deathstar windows` → copy an active ID → `/deathstar intercept window:<ID>`. This spends the dedicated squadron, not your ordinary X-wing/Y-wing hulls. You can prepare this counter without owning a Death Star. Local fleet escorts cannot stop a superlaser.",
            ),
            (
                "Debris and reconstruction",
                "Bring your station to a debris field. `/deathstar salvage` recovers 5–10 materials per six hours, per player/body. `/deathstar reconstruct body`: 250 quintillion + 100 materials, seven days. The new body is unclaimed and undeveloped; old assets never return automatically. Reconstruction advances offline. Use `/space atlas` for body destruction/reconstruction status and artificial moons.",
            ),
        ]
        pages = [
            ui.base_embed(title=f"Death Star Guide {i + 1}/{len(sections)} — {title}", description=text)
            for i, (title, text) in enumerate(sections)
        ]
        for page in pages:
            page.set_footer(text="Public guide · topic dropdown or buttons · /deathstar status is private")
        return pages

    @deathstar.command(
        name="guide",
        description="Public button-paged guide to construction, moon GDP, destruction and defence.",
    )
    async def guide(self, interaction: discord.Interaction):
        pages = self.guide_pages()
        await interaction.response.send_message(
            embed=pages[0],
            view=space_guides.SpaceGuidePager(pages, interaction.user.id),
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(DeathStarCog(bot))
