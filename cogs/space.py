"""ISD expeditions, transport interception and Solar System colonies."""

from __future__ import annotations
import asyncio
import copy
import datetime as dt
import random
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from szofie import (
    coalitions,
    deathstar as ds,
    imperial_star_destroyer as isd,
    nyx,
    space,
    space_fleet as fleet,
    space_guides,
    ui,
)
from szofie.economy import _default_user
from szofie.betting import AmountParseError, parse_amount
from szofie import assets as asset_service
from szofie import status_ui
from szofie import activity_contracts as activities, activity_ui
from szofie.catalog import VEHICLE_CATALOG

ART = Path(__file__).resolve().parent.parent / "assets" / "space"
NYX_CHOICE = app_commands.Choice(name="NYX counter-intelligence satellite", value="nyx")
CRAFT_CHOICES = [app_commands.Choice(name=s.name, value=k) for k, s in fleet.CRAFT.items()] + [NYX_CHOICE]


def fmt(value: int) -> str:
    return ui.format_donuts(value)


def net(user: Dict[str, Any]) -> int:
    return max(0, int(user.get("donuts", 0))) + max(0, int(user.get("bank", 0)))


def take(user: Dict[str, Any], amount: int) -> None:
    wallet = min(max(0, int(user.get("donuts", 0))), amount)
    user["donuts"] = int(user.get("donuts", 0)) - wallet
    user["bank"] = int(user.get("bank", 0)) - (amount - wallet)


class ExplorationDashboard(discord.ui.View):
    def __init__(self, cog: "SpaceCog", author_id: int):
        super().__init__(timeout=180)
        self.cog, self.author_id = (cog, author_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True
        await ui.respond(interaction, content="Open `/space status` for your own dashboard.", ephemeral=True)
        return False

    @discord.ui.button(label="Atlas", style=discord.ButtonStyle.secondary)
    async def atlas(self, interaction: discord.Interaction, button: discord.ui.Button):
        await SpaceCog.atlas.callback(self.cog, interaction)

    @discord.ui.button(label="Colonies", style=discord.ButtonStyle.secondary)
    async def colonies(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.show_colonies(interaction)

    @discord.ui.button(label="Expedition (6h)", style=discord.ButtonStyle.primary)
    async def expedition(self, interaction: discord.Interaction, button: discord.ui.Button):
        await SpaceCog.expedition.callback(self.cog, interaction)

    @discord.ui.button(label="Next step", style=discord.ButtonStyle.success)
    async def next_step(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.cog.guard(interaction, ephemeral=True):
            return
        user = self.cog.user(interaction.guild_id, interaction.user.id)
        state = self.cog.settle_user(user)
        await ui.respond(interaction, content=self.cog.next_step(state, user), ephemeral=True)

    @discord.ui.button(label="Civilian contracts", style=discord.ButtonStyle.primary)
    async def contracts(self, interaction, button):
        await self.cog.activity_board(interaction)


class SpaceCog(commands.Cog, name="Solar System"):
    space_group = app_commands.Group(
        name="space", description="Explore with a Drake Cutlass and colonise with an ISD."
    )
    fleet_group = app_commands.Group(
        name="fleet", description="Ordinary spacecraft, missions, escorts and repairs.", parent=space_group
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.econ = bot.economy
        self._locks: Dict[int, asyncio.Lock] = {}

    async def cog_load(self) -> None:
        self.income_loop.start()

    def cog_unload(self) -> None:
        self.income_loop.cancel()

    def lock(self, gid: int) -> asyncio.Lock:
        return self._locks.setdefault(self.econ.store.canonical_id(gid), asyncio.Lock())

    def user(self, gid: int, uid: int) -> Dict[str, Any]:
        start = int(self.bot.config.for_guild(gid).get("economy.starting_balance", 100))
        return self.econ.user(gid, uid, start)

    def doc(self, gid: int) -> Dict[str, Any]:
        return self.econ.store.load(gid)

    async def nyx_action(self, interaction, action, *, target=None):
        service = self.bot.get_cog("SatelliteCog")
        if service is None:
            await ui.respond(
                interaction, "NYX is temporarily unavailable. Try again shortly.", ephemeral=True
            )
            return
        callback = getattr(service, action)
        if target is None:
            await callback(interaction)
        else:
            await callback(interaction, target)

    def settle_progress(self, gid: int, uid: int, user: Dict[str, Any]) -> bool:
        current = space.now()
        changed = fleet.settle_hulls(user, current)
        state, doc = (space.normalize(user), self.doc(gid))
        changed = space.settle_expedition(user, doc, current) or changed
        for key, site in state["colonies"].items():
            target = space.body(doc, key)
            if target and isinstance(site, dict) and (not ds.destroyed(doc, key)):
                _, settled = space.settle_materials(site, target, current)
                changed = settled or changed
        return changed

    async def settle_fleet(self, gid: int) -> None:
        """Resolve combat deadlines before any display advances hull clocks."""
        async with self.lock(gid):
            doc = self.doc(gid)
            balances = {
                uid: int(u.get("donuts", 0)) for uid, u in doc.get("users", {}).items() if isinstance(u, dict)
            }
            contract_totals = {
                uid: int(activities.root(u).get("space", {}).get("earned", 0))
                for uid, u in doc.get("users", {}).items()
                if isinstance(u, dict)
            }
            events = []
            if fleet.settle_world(doc, space.now(), events=events):
                await self.econ.save(gid)
            await self.record_fleet_events(gid, events)
            for uid, u in doc.get("users", {}).items():
                if isinstance(u, dict) and int(u.get("donuts", 0)) > balances.get(uid, 0):
                    delta = int(u["donuts"]) - balances.get(uid, 0)
                    bonus = int(activities.root(u).get("space", {}).get("earned", 0)) - contract_totals.get(
                        uid, 0
                    )
                    if bonus:
                        await self.bot.ledger.record(gid, int(uid), bonus, "space-contract", after=net(u))
                    if delta > bonus:
                        await self.bot.ledger.record(
                            gid, int(uid), delta - bonus, "space-fleet-salvage", after=net(u)
                        )

    async def guard(self, interaction: discord.Interaction, *, ephemeral: bool = False) -> bool:
        if not interaction.guild_id:
            await ui.respond(interaction, "Space commands work only in a server.", ephemeral=True)
            return False
        cfg = self.bot.config.for_guild(interaction.guild_id)
        if not cfg.get("economy.enabled", True):
            await ui.respond(interaction, "The economy is disabled here.", ephemeral=True)
            return False
        restricted = cfg.get("economy.channel")
        if restricted and interaction.channel_id != int(restricted):
            await ui.respond(interaction, f"Use <#{int(restricted)}> for space commands.", ephemeral=True)
            return False
        await ui.defer_response(interaction, ephemeral=ephemeral)
        doc = self.doc(interaction.guild_id)
        ds.settle_world(doc)
        await self.settle_fleet(interaction.guild_id)
        user = self.user(interaction.guild_id, interaction.user.id)
        if self.settle_progress(interaction.guild_id, interaction.user.id, user):
            await self.econ.save(interaction.guild_id)
        command = getattr(getattr(interaction, "command", None), "name", "")
        if command in {
            "land",
            "build-site",
            "mine",
            "stow",
            "offload",
            "terraform",
            "claim",
            "bombard",
            "expedition",
        }:
            location = space.normalize(self.user(interaction.guild_id, interaction.user.id))["location"]
            if ds.destroyed(doc, location):
                await ui.respond(
                    interaction,
                    content="This body is a debris field. It has no settlement or GDP until `/deathstar reconstruct` completes.",
                    ephemeral=True,
                )
                return False
        return True

    async def record_fleet_events(self, gid: int, events: List[Dict[str, Any]]) -> None:
        for event in events:
            await self.bot.ledger.record(
                gid,
                event["attacker"],
                0,
                "space-fleet-precision-hit",
                other=event["victim"],
                detail=event["detail"],
                occurred_at=event["at"],
            )

    def art(self, embed: discord.Embed, filename: str) -> Optional[discord.File]:
        path = ART / filename
        if not path.is_file() and filename.startswith("fleet/"):
            craft = Path(filename).stem.rsplit("-", 1)[0]
            legacy = {
                "cutlass": "drake-cutlass-explorer.png",
                "transport": "gr75-loading-v3.png",
                "xwing": "xwing-patrol.png",
            }
            if craft in legacy:
                path = ART / legacy[craft]
        if path.is_file():
            embed.set_image(url=f"attachment://{path.name}")
            if path.name.startswith("arquitens-"):
                embed.set_footer(
                    text="Reference-guided Arquitens game illustration · Ship references: Lucasfilm / Fantasy Flight Games"
                )
            return discord.File(str(path), filename=path.name)
        return None

    async def send(
        self,
        interaction: discord.Interaction,
        embed: discord.Embed,
        image: Optional[str] = None,
        *,
        ephemeral: bool = False,
    ) -> None:
        picture = self.art(embed, image) if image else None
        await ui.respond(
            interaction,
            embed=embed,
            file=picture,
            ephemeral=ephemeral,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @staticmethod
    def ready(user: Dict[str, Any]) -> bool:
        state = isd.normalize(user)
        return bool(
            state.get("operational") and (not state.get("damaged")) and (not state.get("repairing_until"))
        )

    @staticmethod
    def settle_user(user: Dict[str, Any]) -> Dict[str, Any]:
        isd.settle(user)
        space.settle(user)
        return space.normalize(user)

    async def autocomplete_body(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        found = list(space.BODIES.values())
        if interaction.guild_id:
            for key in space.galaxy(self.doc(interaction.guild_id))["discovered"]:
                item = space.body(self.doc(interaction.guild_id), key)
                if item:
                    found.append(item)
        needle = current.casefold()
        return [
            app_commands.Choice(name=f"{b.name} · {b.kind}"[:100], value=b.key)
            for b in found
            if needle in b.name.casefold() or needle in b.key.casefold()
        ][:25]

    def selected(self, interaction: discord.Interaction, key: str) -> Optional[space.Body]:
        return space.body(self.doc(interaction.guild_id), key.lower().strip())

    def _exploration_pages(self, interaction: discord.Interaction) -> List[discord.Embed]:
        """Player-facing expedition manual, shared by both discovery commands."""
        cfg = self.bot.config.for_guild(interaction.guild_id) if interaction.guild_id else None

        def setting(key: str, fallback: int) -> int:
            return int(cfg.get(f"economy.{key}", fallback)) if cfg else fallback

        launch_cost = setting("isd_launch_cost", 250 * space.QA)
        assembly_cost = setting("isd_assembly_cost", 2 * space.QI)
        assembly_hours = setting("isd_assembly_hours", 96)
        charge_cost = setting("isd_cinder_cost", 3 * space.QI)
        charge_hours = setting("isd_cinder_build_hours", 48)
        bwing_cost = setting("isd_counter_cost", 45 * space.QA)
        bwing_hours = setting("isd_counter_build_hours", 4)
        component_cost = sum((int(part["cost"]) for part in isd.COMPONENTS.values()))
        sections = space_guides.orientation_sections() + [
            (
                "Start here: your first expedition",
                f"**Start exploring now:** `/space build craft:Drake Cutlass` costs **50 quadrillion / 6h**. After construction, `/space fleet launch` from Earth and wait through the 30-minute public window. It surveys, travels, runs field expeditions and prospects asteroids. No ISD is needed for this starter route.\n\n**The colony route:** build an ISD → refit to expedition → survey a body → travel → land → build a base and mine → build a lab and Terraforming Unit → complete five viability phases → claim → develop. Use `/space status` whenever you lose your place.\n\n**Get the ship first:** `/isd fabricate` makes six different components, up to two at once ({fmt(component_cost)} total). `/isd launch` sends each ready part to orbit ({fmt(launch_cost)} each), with a public interception window. After all six survive, `/isd assemble` costs {fmt(assembly_cost)} and takes {assembly_hours}h. New operational ships start in **attack mode**. `/isd status` shows the project; `/isd guide` explains the full construction and defense sequence.",
            ),
            (
                "Modes and refitting",
                f"**Attack mode:** `/isd arm`, `/isd fire` and planetary `/space bombard`. **Expedition mode:** cargo loading, travel, surveys, settlements and mining.\n\nUse `/space refit expedition` or `/space refit attack`. Each refit costs {fmt(space.REFIT_COST)} and takes {space.REFIT_HOURS}h; it does **not** consume the ship. The ISD must be operational and undamaged, with no active defense window, loading operation or voyage. **Returning to attack mode requires an empty cargo hold.** Jump to **Unload cargo and go back to war** for unloading. Check your mode and timers with `/space status`.",
            ),
            (
                "GR-75 transport and cargo",
                f"`/space build transport` constructs a GR-75 for {fmt(space.TRANSPORT_COST)} in 12h. Then `/space fleet launch craft:GR-75` must survive its 30-minute public window. At **Earth**, with an operational expedition ISD and a deployed transport, use `/space load` to select donuts, items, plushies, fish, a rod or a completed ungarrisoned vehicle with its ammunition. Name the item/vehicle ID when needed.\n\nThe first pack costs {fmt(space.LOAD_BASE_COST)} and opens a {space.LOAD_HOURS}h loading window. You can add more before it closes. Donuts must come from your **wallet** and add a 1% handling fee; other fees may use wallet plus bank. Assets leave your normal holdings immediately and become usable cargo after the timer. `half`/`all` and amount shorthand work where an amount is requested. Non-vehicle ISD cargo is **not** Raven Rock protection against THOR; vehicles already aboard its orbital hold survive THOR. All cargo remains exposed to Cinder.",
            ),
            (
                "X-wing transport interception",
                f"`/space build xwing` costs {fmt(space.XWING_COST)} and takes {space.XWING_BUILD_HOURS}h. `/space fleet payload craft:X-wing` buys ready proton torpedoes for {fmt(space.XWING_AMMO_COST)}; capacity is {space.XWING_AMMO_CAP}. Launch the X-wing with `/space fleet launch` first. Use `/space intercept` on an **active** GR-75 loading window, or `/space fleet intercept` against an ordinary Earth-launch window. You cannot target yourself or a coalition ally.\n\nEach attacker gets one attempt per convoy; at most {space.MAX_INTERCEPTIONS} different attackers may try. Each torpedo has a public {space.INTERCEPTION_CHANCE}% hit chance and is spent either way. A hit removes 5% of the packed donuts and roughly 5% of eligible item/fish stacks, disables the GR-75 for a 12h rebuild, and delays loading. Plushies, rods and unique vehicles are not lost to this interception effect. Loading still completes after the extended timer.",
            ),
            (
                "Find, survey and reach a world",
                "`/space atlas` lists known planets, moons, dwarf planets and asteroids. `/space discover` can add an official JPL asteroid or moon. Build a Cutlass, then `/space fleet launch` and wait through its 30-minute public window. A deployed Cutlass or operational expedition ISD uses `/space survey body` (2 quadrillion once per body) to reveal its resource class and hazard; survey before travelling anywhere except Earth. The resource rating is a **game index**, not a measured real-world reserve.\n\n`/space travel body` moves the ISD after loading and any prior voyage finish. Inner worlds take **2h / 10 quadrillion**, the asteroid belt **4h / 20 quadrillion**, outer worlds **8h / 40 quadrillion**, and far worlds **12h / 60 quadrillion** with an ISD. Cutlass voyages take the same time but cost **2 / 4 / 8 / 12 quadrillion** respectively. Once your ISD is assembled, refit it for exploration; the starter does not move a separate warship. The Sun and Earth are survey-only for settlement purposes. Giant planets use orbital platforms; eligible moons, planets and asteroids use landers.",
            ),
            (
                "Land, build and mine",
                "At your destination, `/space land` establishes a site for **10 quadrillion**. Build in order with `/space build-site`: **base 100 quadrillion → mining rig 150 quadrillion → research lab 250 quadrillion + 10 local materials → Terraforming Unit 500 quadrillion + 20 local materials**. Giant planets use an orbital platform, but follow the same progression.\n\nOnce the rig exists, materials produce **automatically every 6h**, even while offline (at most seven days catch-up). They remain at that colony. `/space mine` can still run every **6h** for its manual wallet payout, but does not duplicate automatic materials. Build costs draw from wallet plus bank, but required materials must be **stored locally**. `/space colony body` shows the site's structures, materials and viability.",
            ),
            (
                "Move materials and terraform",
                "To supply a different colony, use `/space stow material amount` at a completed base, travel there, then `/space offload material amount` at its completed base. Those commands move mined materials between the local store and the ISD; ordinary `/space unload` is for other cargo. `all` works for material amounts.\n\nAfter building the Terraforming Unit, run `/space terraform` **five times**. Each phase consumes **10 locally stored materials** and adds **20% viability**. The five phase prices are **100 quadrillion, 150 quadrillion, 200 quadrillion, 250 quadrillion and 300 quadrillion** (1 quintillion total), in order. Mine or deliver enough materials between phases. At **100% viability**, `/space claim` becomes available.",
            ),
            (
                "Claim, GDP and passive income",
                "`/space claim` makes you sovereign of a fully viable body. Several players may have sites there, but **only one** can hold its claim; another ruler's existing claim blocks yours. The starting GDP is a **fictional index** based on the body's resource score and hazard, not its literal real-world GDP.\n\nThe sovereign receives production in their **wallet automatically each hour**. Downtime catch-up is capped at **7 days**. `/space develop body` buys up to **10 levels**; each adds 10% of base GDP to the index and raises production. **Price for level N = base GDP × N ÷ 4, rounded down to whole donuts.** Base GDP is the world's original, undeveloped index—not your upgraded GDP. For example, base GDP 100 quadrillion means level 1 costs 25 quadrillion, level 2 costs 50 quadrillion, and level 10 costs 250 quadrillion. Payment uses wallet then bank; Deep Vault is not spent automatically. Use `/space colony body` to inspect your next upgrade price, your level, GDP, materials and estimated daily income.",
            ),
            (
                "Unload cargo and go back to war",
                f"Once loading and travel have finished, `/space unload kind` returns donuts, items, plushies, fish, rods or vehicles to usable holdings. At Earth you can unload immediately; elsewhere you need a **completed local base**. Rod enchants travel with the rod, and a vehicle keeps its loaded state. An already-active identical vehicle must be resolved before unloading its cargo copy.\n\nMined materials instead use `/space offload` at a base. To refit to attack mode, finish any loading/voyage and **empty every cargo category**, including materials. Then `/space refit attack` costs {fmt(space.REFIT_COST)} / {space.REFIT_HOURS}h. Your colonies and claims remain in place when the ship changes mode.",
            ),
            (
                "Planetary strikes and command map",
                f"In attack mode, `/isd arm` prepares a charge for {fmt(charge_cost)} / {charge_hours}h. `/isd fire` is the **server-wide** Cinder attack and has a two-hour public warning; only assets sealed in Raven Rock survive a hit. `/space bombard` instead attacks your ISD's **current colonisable body**, not Earth. It has a **24h** planetary cooldown and a one-hour public B-wing window. A hit erases all colonies and sovereignty **on that body only**, including your own; the charge is spent even if defenders stop it.\n\nDefenders build B-wing packages with `/isd counter-build` ({fmt(bwing_cost)} / {bwing_hours}h, capacity two), then `/isd intercept` during the warning. Planetary defense allows up to ten one-package defenders at 10% each. For full megaproject, Cinder and repair rules, use `/isd guide`. For quick checks: `/space status` = ship/cargo/timers; `/space atlas` = worlds; `/space colony` = settlement; `/isd status` = construction/weapons.",
            ),
            (
                "Drake Cutlass: the starter route",
                "Build with `/space build craft:Drake Cutlass` (**50 quadrillion / 6h**), then use `/space fleet launch` from Earth and survive its 30-minute public counter window. No expedition refit is needed before an ISD is assembled. Survey a destination (2 quadrillion once), travel, then use `/space expedition` or the dashboard button. It is a civilian exploration role in this bot, not a new attack weapon.\n\nOn intact **asteroids**, `/space mine` prospects once per 6h: **two materials** plus a smaller wallet payout. Materials enter your field store. Only an expedition ISD can found colonies, terraform, claim worlds or carry bulk assets. After obtaining an ISD, `/space stow` transfers field samples to its hold at Earth or a completed base; `/space offload` delivers them to a colony. Field samples are exposed to THOR, not protected storage.",
            ),
            (
                "Expeditions and the discovery journal",
                "`/space expedition` starts **one free six-hour mission** at your current intact world, with a ready Cutlass or expedition ISD. No missions at Earth/the Sun, during travel or while refitting. Travel and refits wait until the mission finishes. Rewards settle automatically after downtime; no claim button or ship-loss roll.\n\nAsteroids/dwarf worlds use geological sampling; icy moons use subsurface surveys; giant planets use orbital atmospheric research; rocky worlds use surface expeditions. Every result gives materials, even an inconclusive result. Samples enter the local base store if one exists, otherwise the field store. `/space journal` records distinct worlds and ordinary/rare specimens. Base rare chance is **10%**, plus **10 points** for a local lab and **10 points** for Research focus. Discoveries are fictional game collectibles.\n\n**3 worlds:** Pathfinder title + badge. **10:** Explorer 21 cosmetic plushie. **25:** Solar Surveyor title. Repeating one world does not farm milestone rewards.",
            ),
            (
                "Colony focus and the dashboard",
                "After claiming, choose `/space specialise body focus`. One focus per colony: **Industrial +25% automatic materials**, **Research +10 percentage points rare findings**, or **Civilian +20% colonial wallet production**. The first choice is free; a change costs **25 quadrillion**. Old production is settled before a change, so upgrades do not retroactively boost the backlog.\n\n`/space status` is your private button dashboard: **Atlas**, **Colonies**, **Expedition (6h)** and **Next step**. The expedition button starts the free mission if none is running, or shows its finish date. Colony summaries show local stores and daily production; `/space colony body` gives the full detail. Timers display absolute finish dates as well as relative countdowns. Guides remain public; only the person who opened a pager can operate its buttons.",
            ),
        ]
        pages: List[discord.Embed] = []
        for index, (heading, description) in enumerate(sections, start=1):
            page = ui.base_embed(
                title=f"Solar System Guide {index}/{len(sections)} — {heading}",
                description=description,
                color=cfg.color if cfg else 11894492,
            )
            page.set_footer(text="Topic dropdown or page buttons · only the requester can navigate")
            pages.append(page)
        return pages

    async def _send_exploration_guide(self, interaction: discord.Interaction) -> None:
        await ui.defer_response(interaction)
        pages = self._exploration_pages(interaction)
        pages.extend(self.fleet_pages(include_orientation=False))
        space_guides.number_pages(pages, "Space Guide")
        await ui.respond(
            interaction,
            embed=pages[0],
            view=space_guides.SpaceGuidePager(pages, interaction.user.id),
            ephemeral=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(
        name="exploration",
        description="Public space walkthrough: goals, ship examples, interactions, cargo and colonies.",
    )
    async def exploration(self, interaction: discord.Interaction) -> None:
        await self._send_exploration_guide(interaction)

    @space_group.command(
        name="guide", description="Paged guide to ISD expeditions and Solar System colonies."
    )
    async def guide(self, interaction: discord.Interaction) -> None:
        await self._send_exploration_guide(interaction)

    @space_group.command(name="atlas", description="Browse major worlds and known surveyed small bodies.")
    async def atlas(self, interaction: discord.Interaction) -> None:
        await ui.defer_response(interaction)
        doc = self.doc(interaction.guild_id) if interaction.guild_id else {}
        claims = space.galaxy(doc)["claims"]
        ds.settle_world(doc)
        rows = list(space.BODIES.values())
        rows.extend(
            (space.body(doc, key) for key in space.galaxy(doc)["discovered"] if key not in space.BODIES)
        )
        rows = [b for b in rows if b]
        pages = []
        page_subjects = []
        for i in range(0, len(rows), 10):
            lines = []
            subjects = []
            for b in rows[i : i + 10]:
                owner = claims.get(b.key)
                cloaked = owner and nyx.active(doc.get("users", {}).get(str(owner), {}))
                if owner and (not cloaked):
                    subjects.append(str(owner))
                suffix = (
                    " · claimed · NYX intelligence censored"
                    if cloaked
                    else f" · claimed by <@{owner}>"
                    if owner
                    else ""
                )
                if ds.destroyed(doc, b.key):
                    suffix = " · DESTROYED: debris field, no GDP"
                    reconstruction = ds.world(doc)["reconstructions"].get(b.key)
                    if reconstruction:
                        suffix += f" · rebuilding <t:{int(ds.at(reconstruction['ready_at']).timestamp())}:F>"
                access = (
                    "landable"
                    if b.landable
                    else "orbital station"
                    if b.key in space.GIANT_PLANETS
                    else "survey only"
                )
                lines.append(f"`{b.key}` {b.name} · {b.kind} · {b.resource} · {access}{suffix}")
            pages.append(
                ui.base_embed(
                    title=f"Solar System Atlas {i // 10 + 1}/{(len(rows) + 9) // 10}",
                    description="\n".join(lines),
                )
            )
            page_subjects.append(subjects)
        moons = []
        moon_subjects = []
        for uid, candidate in doc.get("users", {}).items():
            if isinstance(candidate, dict) and nyx.active(candidate):
                continue
            state = candidate.get("death_star") if isinstance(candidate, dict) else None
            if isinstance(state, dict) and state.get("operational"):
                moons.append(
                    f"`deathstar-{uid}` DS-1 · artificial moon · owner <@{uid}> · orbit `{state['location']}` · GDP {fmt(ds.gdp(state))} · {('disabled' if state['damaged'] else 'operational')}"
                )
                moon_subjects.append(str(uid))
        for index in range(0, len(moons), 10):
            pages.append(
                ui.base_embed(title="Artificial Moons", description="\n".join(moons[index : index + 10]))
            )
            page_subjects.append(moon_subjects[index : index + 10])

        def page_guard(index):
            if any((nyx.active(doc.get("users", {}).get(uid, {})) for uid in page_subjects[index])):
                return nyx.censored_embed()
            return None

        await ui.respond(
            interaction,
            embed=pages[0],
            view=ui.Paginator(pages, interaction.user.id, page_guard=page_guard),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @space_group.command(name="discover", description="Add a known asteroid or moon from NASA/JPL records.")
    @app_commands.describe(designation="Small-body or natural-satellite name, number or official designation")
    async def discover(self, interaction: discord.Interaction, designation: str) -> None:
        if not await self.guard(interaction, ephemeral=True):
            return
        if len(designation) > 60:
            await ui.respond(interaction, "Use a shorter official designation.", ephemeral=True)
            return
        try:
            timeout = aiohttp.ClientTimeout(total=12)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    "https://ssd-api.jpl.nasa.gov/sbdb.api", params={"sstr": designation, "phys-par": "true"}
                ) as response:
                    response.raise_for_status()
                    payload = await response.json()
                if not isinstance(payload.get("object"), dict):
                    async with session.get(
                        "https://ssd.jpl.nasa.gov/api/horizons_lookup.api",
                        params={"sstr": designation, "group": "sat"},
                    ) as response:
                        response.raise_for_status()
                        moon_payload = await response.json()
                    results = moon_payload.get("result", [])
                    if len(results) != 1 or results[0].get("type") != "natural satellite":
                        raise ValueError(
                            "JPL could not uniquely resolve that body. Try its official designation."
                        )
                    moon = results[0]
                    spkid = str(moon.get("spkid", ""))
                    name = str(moon.get("name", designation)).strip()[:80]
                    if not spkid.isdigit():
                        raise ValueError("JPL did not provide a stable body identifier.")
                    for existing in space.BODIES.values():
                        if (
                            existing.kind == "moon"
                            and existing.name.lower().replace(" (moon)", "") == name.lower()
                        ):
                            await interaction.followup.send(
                                f"That moon is already in the atlas as `{existing.key}`.", ephemeral=True
                            )
                            return
                    system = int(spkid) // 100
                    zone = {3: "inner", 4: "inner", 5: "outer", 6: "outer", 7: "far", 8: "far", 9: "far"}.get(
                        system, "far"
                    )
                    key = f"moon-{spkid}"
                    item = {
                        "name": name,
                        "kind": "moon",
                        "zone": zone,
                        "resource": "unclassified-regolith",
                        "score": 2,
                        "hazard": 7,
                        "landable": True,
                        "source": "NASA/JPL Horizons Lookup",
                        "spkid": spkid,
                    }
                    async with self.lock(interaction.guild_id):
                        space.galaxy(self.doc(interaction.guild_id))["discovered"][key] = item
                        await self.econ.save(interaction.guild_id)
                    await interaction.followup.send(
                        f"Added JPL natural satellite `{key}` **{name}** to the atlas. Survey it to begin.",
                        ephemeral=True,
                    )
                    return
            obj = payload.get("object", {})
            spkid = str(obj.get("spkid", ""))
            if not spkid.isdigit() or obj.get("kind") not in {"an", "au"}:
                raise ValueError(
                    "JPL could not resolve a single small body. Try its number or formal designation."
                )
            if spkid in space.JPL_ALIASES:
                await interaction.followup.send(
                    f"That body is already in the atlas as `{space.JPL_ALIASES[spkid]}`.", ephemeral=True
                )
                return
            phys = payload.get("phys_par", {}) or {}
            diameter = next((float(p.get("value")) for p in phys if p.get("name") == "diameter"), 0.0)
            spectral = next(
                (str(p.get("value", "")).upper() for p in phys if p.get("name") in {"spec_T", "spec_B"}), ""
            )
            resource = (
                "carbon"
                if spectral.startswith("C")
                else "metals"
                if spectral.startswith("M")
                else "silicates"
                if spectral.startswith("S")
                else "unknown"
            )
            orbit = payload.get("orbit", {}).get("elements", [])
            semi_major = next((float(p.get("value")) for p in orbit if p.get("name") == "a"), 2.5)
            key = f"sb-{spkid}"
            name = str(obj.get("fullname") or obj.get("des") or designation).strip()[:80]
            zone = "inner" if semi_major < 2 else "belt" if semi_major < 4 else "outer"
            item = {
                "name": name,
                "kind": "asteroid",
                "zone": zone,
                "resource": resource if resource != "unknown" else "unclassified-regolith",
                "score": 3 if resource != "unknown" else 2,
                "hazard": 7,
                "landable": diameter >= 1.0,
                "diameter_km": diameter,
                "source": "NASA/JPL SBDB",
                "spkid": spkid,
            }
            async with self.lock(interaction.guild_id):
                space.galaxy(self.doc(interaction.guild_id))["discovered"][key] = item
                await self.econ.save(interaction.guild_id)
            await interaction.followup.send(
                f"Added `{key}` **{name}** to the atlas. "
                + (
                    "Landable after survey."
                    if item["landable"]
                    else "Orbit-only until a diameter of at least 1 km is documented."
                ),
                ephemeral=True,
            )
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError) as exc:
            await interaction.followup.send(f"JPL lookup did not complete: {exc}", ephemeral=True)

    @space_group.command(name="status", description="See your ISD mode, cargo, journey and colonies.")
    async def status(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        if not await self.guard(interaction, ephemeral=True):
            return
        user = self.user(interaction.guild_id, interaction.user.id)
        state = self.settle_user(user)
        await self.econ.save(interaction.guild_id)
        sections = [
            (
                "📋 Explorer and journey",
                "\n".join(
                    [
                        status_ui.line("Explorer", space.exploration_craft(user) or "⚪ Not ready"),
                        status_ui.line("ISD mode", str(state["mode"]).title()),
                        status_ui.line("Location", status_ui.body_name(state["location"], space.BODIES)),
                    ]
                ),
            )
        ]
        mission = state.get("expedition")
        activities = []
        if mission:
            activities.append(
                status_ui.line("Field expedition", f"{mission['kind']} · {mission['name']}")
                + "\n"
                + status_ui.line("Completes", status_ui.deadline(mission["ready_at"]))
            )
        for key, label in (
            ("refit_until", "ISD role refit"),
            ("load_until", "ISD cargo loading"),
            ("travel_until", "Arrival"),
            ("cutlass_build_until", "Drake Cutlass construction"),
        ):
            due = space.at(state.get(key))
            if due:
                activities.append(status_ui.line(label, status_ui.deadline(due)))
        sections.append(
            (
                "⏳ Current activity",
                "\n\n".join(activities) or "No expedition, voyage or loading in progress.",
            )
        )
        cargo = state["cargo"]
        sections.append(
            (
                "📦 Onboard cargo",
                "\n".join(
                    [
                        status_ui.line("Donuts", fmt(cargo["donuts"])),
                        status_ui.line(
                            "Items / plushies / fish",
                            f"{sum(cargo['inventory'].values())} / {sum(cargo['plushies'].values())} / {sum(cargo['fish'].values())}",
                        ),
                        status_ui.line(
                            "Rods / vehicles / materials",
                            f"{len(cargo['rods'])} / {len(cargo['vehicles'])} / {sum(cargo['materials'].values())}",
                        ),
                    ]
                ),
            )
        )
        sections.append(
            (
                "🪐 Colonies and discoveries",
                "\n".join(
                    [
                        status_ui.line("Colonies", len(state["colonies"])),
                        status_ui.line("Journal worlds", len(state["journal"])),
                        status_ui.line(
                            "Field samples", f"{sum(state['field_materials'].values())} materials"
                        ),
                        status_ui.line("X-wing proton torpedoes", state["xwing_ammo"]),
                    ]
                ),
            )
        )
        sections.append(
            (
                "➡️ Next action",
                self.next_step(state, user)
                + "\n\nCraft launches, ammunition, repairs and recon: `/space fleet status`. Instructions: `/space fleet guide`.",
            )
        )
        pages = status_ui.report("🌌 EXPLORATION DASHBOARD", [("Overview", sections)], checked_at=space.now())
        await ui.respond(
            interaction,
            embed=pages[0],
            view=ExplorationDashboard(self, interaction.user.id),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @staticmethod
    def next_step(state: Dict[str, Any], user: Optional[Dict[str, Any]] = None) -> str:
        if state["travel_until"]:
            return "Next: wait for arrival, then use the Expedition button."
        if state["expedition"]:
            return "Next: your expedition completes automatically; check `/space journal` afterwards."
        if state["refit_until"]:
            return "Next: wait for your refit to finish, then resume exploration."
        ship = isd.normalize(user) if user is not None else {}
        if ship.get("operational"):
            if ship.get("damaged") or ship.get("repairing_until"):
                return "Next: repair your ISD with `/isd repair`, or wait for its existing repair to finish."
            if state["mode"] != "expedition":
                return "Next: `/space refit mode:Expedition` before surveys, travel or field missions."
        if state["cutlass_build_until"]:
            return "Next: wait for your Cutlass, then `/space fleet launch craft:Drake Cutlass`."
        if (
            user is not None
            and state["cutlass_owned"]
            and (not ship.get("operational"))
            and (not fleet.deployed(user, "cutlass"))
        ):
            return "Next: `/space fleet launch craft:Drake Cutlass`, then wait through its 30-minute public interception window."
        if not state["cutlass_owned"] and (not state["surveys"]) and (not ship.get("operational")):
            return "Start: `/space build craft:Drake Cutlass` (50 quadrillion / 6h), or build an ISD with `/isd guide`."
        if state["location"] == "earth":
            return "Next: `/space survey body:mars`, then `/space travel body:mars`."
        site = state["colonies"].get(state["location"]) or {}
        if not site.get("landed"):
            return "Explore with the Expedition button. To colonise, bring an operational expedition ISD and use `/space land`."
        for structure in ("base", "mine", "lab", "terraform"):
            if not site.get(structure):
                return f"Next: `/space build-site structure:{structure}`. Mines produce local materials automatically every 6h."
        if int(site.get("viability", 0)) < 100:
            return "Next: `/space terraform` (five phases in total), then `/space claim`."
        return "Next: claim the world, choose `/space specialise`, then `/space develop`. Try expeditions for discoveries."

    async def show_colonies(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        if not await self.guard(interaction, ephemeral=True):
            return
        state = self.settle_user(self.user(interaction.guild_id, interaction.user.id))
        fields = [
            (
                f"🪐 {status_ui.body_name(key, space.BODIES)}",
                "\n".join(
                    [
                        status_ui.line("Body ID", f"`{key}`"),
                        status_ui.line("Viability", f"{site.get('viability', 0)}%"),
                        status_ui.line("Focus", site.get("specialisation") or "Unspecialised"),
                        status_ui.line("Local materials", space.material_total(site)),
                        status_ui.line("Wallet production", f"{fmt(space.colony_daily_income(site))}/day"),
                    ]
                ),
            )
            for key, site in state["colonies"].items()
            if isinstance(site, dict)
        ]
        pages = status_ui.report(
            "🪐 YOUR COLONIES",
            [
                (
                    "Settlements",
                    fields
                    or [
                        (
                            "➡️ Next action",
                            "No settlements yet. Explore first, then use an expedition ISD to land.",
                        )
                    ],
                )
            ],
        )
        await ui.respond(
            interaction, embed=pages[0], view=status_ui.pager(pages, interaction.user.id), ephemeral=True
        )

    @space_group.command(
        name="expedition", description="Start one free six-hour field mission at your current world."
    )
    async def expedition(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        if not await self.guard(interaction, ephemeral=True):
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        async with self.lock(gid):
            user = self.user(gid, uid)
            state = self.settle_user(user)
            target = space.body(self.doc(gid), state["location"])
            record = state.get("expedition")
            if record:
                text = f"**{record['kind']}** at **{record['name']}** finishes <t:{int(space.at(record['ready_at']).timestamp())}:f>. Rewards settle automatically."
            else:
                try:
                    if not target or ds.destroyed(self.doc(gid), target.key):
                        raise ValueError(
                            "Choose an intact planet, moon or asteroid; debris fields cannot support field missions."
                        )
                    record = space.start_expedition(user, target, space.now(), secrets.token_hex(16))
                except ValueError as exc:
                    await ui.respond(interaction, content=str(exc), ephemeral=True)
                    return
                await self.econ.save(gid)
                text = f"Started **{record['kind']}** on **{target.name}**. Finishes <t:{int(space.at(record['ready_at']).timestamp())}:f>. No fee or ship-loss roll. Findings and materials enter your journal/stores automatically; inconclusive missions still earn one material."
        await self.send(
            interaction,
            ui.base_embed(title="FIELD EXPEDITION", description=text),
            "drake-cutlass-explorer.png",
            ephemeral=True,
        )

    @space_group.command(
        name="journal", description="Read your discoveries, samples and exploration milestones."
    )
    async def journal(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        if not await self.guard(interaction, ephemeral=True):
            return
        state = space.normalize(self.user(interaction.guild_id, interaction.user.id))
        rows = [
            f"**{entry['name']}** · {entry['missions']} missions · {', '.join(entry['discoveries'])} · last: {entry['last_result']}"
            for entry in state["journal"].values()
        ]
        overview = ui.base_embed(
            title="EXPLORATION JOURNAL",
            description=f"Worlds explored: **{len(rows)}**. Milestones: 3 worlds = Pathfinder title + badge; 10 = Explorer 21 cosmetic plushie; 25 = Solar Surveyor title.\nSamples are game collectibles, not assertions of real alien life or measured reserves.\nField materials: "
            + (", ".join((f"{v} {k}" for k, v in state["field_materials"].items())) or "none"),
        )
        pages = [overview] + [
            ui.base_embed(title="DISCOVERY RECORDS", description="\n\n".join(rows[i : i + 6]))
            for i in range(0, len(rows), 6)
        ]
        view = ui.Paginator(pages, interaction.user.id)
        button = discord.ui.Button(label="Civilian contracts", style=discord.ButtonStyle.primary)
        button.callback = self.activity_board
        view.add_item(button)
        await ui.respond(interaction, embed=pages[0], view=view, ephemeral=True)

    async def activity_board(self, interaction):
        if not await self.guard(interaction, ephemeral=True):
            return
        user = self.user(interaction.guild_id, interaction.user.id)
        board = activities.space_board(user, self.doc(interaction.guild_id))
        lines = [
            "Three daily offers where your ships/routes permit. One accepted contract at a time; 48 hours to finish. Only successful fleet missions STARTED after acceptance count. Existing mission output is retained; contracts add wallet pay and materials."
        ]
        active = board.get("active")
        if active:
            lines.append(
                f"\n**Last/current contract:** {active['name']} at {active['body']} · "
                + (
                    "paid"
                    if active.get("completed")
                    else f"expires {status_ui.deadline(space.at(active['expires_at']))}"
                )
            )
        options = []
        for index, offer in enumerate(board["offers"]):
            cost, craft = self._contract_terms(user, offer)
            hours = max(
                2, space.travel_hours(space.body(self.doc(interaction.guild_id), offer["body"]).zone)
            ) + (2 if offer["kind"] == "mine" else 0)
            lines.append(
                f"\n**{offer['name']} — {offer['body']}** · {fmt(offer['reward'])} extra donuts + {offer['materials']} material units\nUse {craft}; service pack {fmt(cost)}; about {hours}h (interdiction can delay arrival). "
                + (
                    "Deliver at least 10 materials between different own/allied completed bases."
                    if offer["kind"] == "supply"
                    else "Complete a new qualifying mission."
                )
            )
            if index not in board["paid"] and (not active or active.get("completed")):
                options.append((offer["name"] + " — " + offer["body"], str(index)))
        if not board["offers"]:
            lines.append(
                "\nLaunch a civilian spacecraft and survey a destination first. Supply offers also require two valid completed bases and available source materials."
            )
        await self.econ.save(interaction.guild_id)
        await activity_ui.show(
            interaction,
            ui.base_embed(title="Civilian space contracts", description="\n".join(lines)),
            activity_ui.Board(self, interaction.user.id, options, board["id"]),
        )

    @staticmethod
    def _contract_terms(user, offer):
        keys = {
            "survey": ("cutlass", "carrack"),
            "supply": ("transport",),
            "mine": ("prospector",),
            "salvage": ("vulture",),
        }[offer["kind"]]
        key = next((key for key in keys if fleet.owned(user, key)), keys[0])
        return (fleet.CRAFT[key].payload_cost, fleet.CRAFT[key].name)

    async def activity_preview(self, interaction, token, action):
        if not await self.guard(interaction, ephemeral=True):
            return
        user = self.user(interaction.guild_id, interaction.user.id)
        board = activities.space_board(user, self.doc(interaction.guild_id))
        index = int(action)
        if board["id"] != token or not 0 <= index < len(board["offers"]):
            await ui.respond(interaction, content="This board has refreshed.", ephemeral=True)
            return
        offer = board["offers"][index]
        cost, craft = self._contract_terms(user, offer)

        async def commit(click):
            if not await self.guard(click, ephemeral=True):
                return
            try:
                current = self.user(click.guild_id, click.user.id)
                accepted = activities.accept_space(current, self.doc(click.guild_id), token, index)
            except ValueError as error:
                await ui.respond(click, content=str(error), ephemeral=True)
                return
            await self.econ.save(click.guild_id)
            await ui.respond(
                click,
                embed=ui.ok_embed(
                    f"Accepted {accepted['name']} at {accepted['body']}. Now launch a NEW matching /space fleet mission. Acceptance does not spend a service pack or start a mission. Deadline: {status_ui.deadline(space.at(accepted['expires_at']))}. Payment settles automatically on successful arrival."
                ),
                ephemeral=True,
            )

        await activity_ui.show(
            interaction,
            ui.base_embed(
                title="Accept civilian contract",
                description=f"{offer['name']} at {offer['body']}\nExtra reward: {fmt(offer['reward'])} donuts + {offer['materials']} materials.\nRequired craft: {craft}; existing payload cost {fmt(cost)}. 48h after acceptance. Failed missions, old records and returned deliveries do not qualify.",
            ),
            activity_ui.Confirm(interaction.user.id, "Accept contract", commit),
        )

    @space_group.command(
        name="specialise",
        description="Choose your claimed colony's focus; first choice free, changes 25 quadrillion.",
    )
    @app_commands.autocomplete(body=autocomplete_body)
    @app_commands.choices(focus=[app_commands.Choice(name=k.title(), value=k) for k in space.SPECIALISATIONS])
    async def specialise(
        self, interaction: discord.Interaction, body: str, focus: app_commands.Choice[str]
    ) -> None:
        nyx.protect_report(interaction, self.econ)
        if not await self.guard(interaction, ephemeral=True):
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        async with self.lock(gid):
            user = self.user(gid, uid)
            state = self.settle_user(user)
            site = state["colonies"].get(body)
            target = space.body(self.doc(gid), body)
            if (
                not target
                or not site
                or ds.destroyed(self.doc(gid), body)
                or (str(space.galaxy(self.doc(gid))["claims"].get(body)) != str(uid))
            ):
                await ui.respond(
                    interaction, content="You must hold this intact world's completed claim.", ephemeral=True
                )
                return
            if site.get("specialisation") == focus.value:
                await ui.respond(interaction, content="That focus is already active.", ephemeral=True)
                return
            cost = space.SPECIALISATION_CHANGE_COST if site.get("specialisation") else 0
            if net(user) < cost:
                await ui.respond(interaction, content=f"Changing focus costs {fmt(cost)}.", ephemeral=True)
                return
            current = space.now()
            payout, _ = space.settle_colony_income(user, site, current)
            space.settle_materials(site, target, current)
            take(user, cost)
            site["specialisation"] = focus.value
            await self.econ.save(gid)
        if payout:
            await self.bot.ledger.record(
                gid, uid, payout, "space-production", after=net(user) + cost, detail=body
            )
        if cost:
            await self.bot.ledger.record(
                gid, uid, -cost, "space-specialisation", after=net(user), detail=body
            )
        await ui.respond(
            interaction,
            content=f"{target.name}: {focus.name}. {space.SPECIALISATIONS[focus.value]}. Paid {fmt(cost)}.",
            ephemeral=True,
        )

    @space_group.command(
        name="refit", description="Switch an operational ISD between attack and expedition mode."
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="Expedition", value="expedition"),
            app_commands.Choice(name="Attack", value="attack"),
        ]
    )
    async def refit(self, interaction: discord.Interaction, mode: app_commands.Choice[str]) -> None:
        nyx.protect_report(interaction, self.econ)
        if not await self.guard(interaction):
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        async with self.lock(gid):
            user = self.user(gid, uid)
            state = self.settle_user(user)
            if not self.ready(user):
                error = "Your ISD must be operational and undamaged."
            elif state["mode"] == mode.value or state["refit_until"]:
                error = "That mode is already active or a refit is underway."
            elif isd.normalize(user).get("active_window_id"):
                error = "A defensive window is active."
            elif state["load_until"] or state["travel_until"] or state["expedition"]:
                error = "Finish loading, travel and field expeditions before changing mode."
            elif mode.value == "attack" and (
                state["cargo"]["donuts"]
                or any(
                    (
                        state["cargo"][k]
                        for k in ("inventory", "plushies", "fish", "rods", "vehicles", "materials")
                    )
                )
            ):
                error = "Unload all cargo before restoring attack mode."
            elif net(user) < space.REFIT_COST:
                error = f"A refit costs {fmt(space.REFIT_COST)}."
            else:
                error = None
            if error:
                await ui.respond(interaction, error, ephemeral=True)
                return
            take(user, space.REFIT_COST)
            due = space.now() + dt.timedelta(hours=space.REFIT_HOURS)
            state.update(refit_target=mode.value, refit_until=due.isoformat())
            await self.econ.save(gid)
        await self.bot.ledger.record(
            gid, uid, -space.REFIT_COST, "isd-space-refit", after=net(user), detail=mode.value
        )
        await self.send(
            interaction,
            ui.ok_embed(f"ISD refit to **{mode.value}** finishes <t:{int(due.timestamp())}:R>."),
            "isd-expedition.png",
        )

    @space_group.command(
        name="build", description="Build an ordinary spacecraft or NYX counter-intelligence satellite."
    )
    @app_commands.choices(
        craft=[
            app_commands.Choice(name=f"{spec.name} · {fmt(spec.cost)} · {spec.build_hours}h", value=key)
            for key, spec in fleet.CRAFT.items()
        ]
        + [app_commands.Choice(name="NYX satellite · 15 quadrillion · 12h", value="nyx")]
    )
    async def build(self, interaction: discord.Interaction, craft: app_commands.Choice[str]) -> None:
        nyx.protect_report(interaction, self.econ)
        if craft.value == "nyx":
            await self.nyx_action(interaction, "build")
            return
        if not await self.guard(interaction):
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        key = craft.value
        async with self.lock(gid):
            user = self.user(gid, uid)
            try:
                cost = fleet.commission(user, key, space.now())
            except ValueError as error:
                await ui.respond(interaction, str(error), ephemeral=True)
                return
            due = space.at(fleet.build_until(user, key))
            await self.econ.save(gid)
        await self.bot.ledger.record(gid, uid, -cost, "space-craft-build", after=net(user), detail=key)
        await self.send(
            interaction,
            ui.ok_embed(
                f"**{fleet.CRAFT[key].name}** · paid {fmt(cost)} · construction finishes <t:{int(due.timestamp())}:f> (<t:{int(due.timestamp())}:R>).\nPrepare payloads, then `/space fleet launch`: a 30-minute public interception window must finish before use."
            ),
            f"fleet/{key}-build.png",
        )

    async def torpedo(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        if not await self.guard(interaction, ephemeral=True):
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        async with self.lock(gid):
            user = self.user(gid, uid)
            state = self.settle_user(user)
            try:
                fleet.prepare(user, "xwing", 1, space.now())
            except ValueError as error:
                await ui.respond(interaction, str(error), ephemeral=True)
                return
            await self.econ.save(gid)
        await self.bot.ledger.record(gid, uid, -space.XWING_AMMO_COST, "space-torpedo", after=net(user))
        await ui.respond(interaction, "Proton torpedo loaded.", ephemeral=True)

    @fleet_group.command(
        name="guide",
        description="Public ship tutorials and interaction map; choose a craft to open its worked example.",
    )
    @app_commands.choices(craft=CRAFT_CHOICES)
    async def fleet_guide(
        self, interaction: discord.Interaction, craft: Optional[app_commands.Choice[str]] = None
    ) -> None:
        if craft and craft.value == "nyx":
            await self.nyx_action(interaction, "guide")
            return
        await ui.defer_response(interaction)
        pages = self.fleet_pages()
        index = 0
        if craft and craft.value in fleet.CRAFT:
            heading = fleet.CRAFT[craft.value].name
            index = next(
                (i for i, page in enumerate(pages) if (page.title or "").split(" — ", 1)[-1] == heading)
            )
        await ui.respond(
            interaction,
            embed=pages[index],
            view=space_guides.SpaceGuidePager(pages, interaction.user.id, initial_index=index),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @staticmethod
    def fleet_pages(*, include_orientation: bool = True) -> List[discord.Embed]:
        sections = [
            (
                "Start here",
                "Ordinary spacecraft do useful work without an ISD. Build with `/space build`, prepare service packs/ammunition with `/space fleet payload`, then `/space fleet launch` from Earth. All ordinary hulls must survive a 30-minute public launch window before use. After launch, survey a destination, then select `/space fleet mission`. Ships have separate assignments, not the ISD's shared cargo voyage. One mission per hull; no fuel/crew spreadsheets. Payload preparation takes 30 minutes (established X-wing torpedoes remain immediate). Mission deadlines are absolute UTC timestamps and continue through laptop downtime; check `/space fleet status` for results. `/arsenal` remains your complete private force report. NYX is also in `/space build`: launch it with `/space fleet launch`, then activate via `/space fleet mission craft:NYX mission:Jam`. It needs no body, target or payload. Use `/space fleet guide craft:NYX` for its counter-intelligence manual.",
            ),
            (
                "Earth launches and interception",
                "Construction produces a grounded hull, not a ready orbital ship. `/space fleet launches` lists public windows; `/space fleet intercept target craft counter` commits one attempt. X-wing proton torpedoes: 35%; A-wing concussion missiles: 45%. Against heavy Hammerhead, Interdictor or Arquitens these are 20%/30%; a ready B-wing Ion Assault Package from `/isd counter-build` is also suitable at 40%. Fighters must already be launched, idle and armed; one payload/package is spent even on a miss. One attempt per player, at most three players per launch; no self/allied interception. A hit aborts launch, loses onboard payload and leaves a repairable ground hull (25% hull cost, normal repair time). Repair then relaunch; no construction refund. Grounded/launching hulls cannot run missions, raids, escort, Cutlass exploration or GR-75 loading. Existing deployed ships stay deployed. Cutlass returning to Earth must relaunch. ISD components retain their B-wing windows, Death Star components their dedicated squadron windows, and THOR modules their GBI/EKV windows. Ordinary launch interception never targets those projects.",
            ),
            (
                "Industry and discovery",
                "Prospector: asteroid mining yields 12 + twice the body's resource score in materials, taking zone travel time + 2h (4–14h). No colony/ISD needed. Vulture: salvage one natural debris claim per body per UTC day (8 metals + 2 quadrillion before raids), or a finite battle wreck listed in `/space fleet wrecks`. Self, attacker and allied losses cannot be salvaged by them; no repeated claims. Cutlass survey: 2 materials/10% rare specimen. Carrack survey: 6 + score materials/30% rare. Survey/salvage/recon take 2/4/8/12h by zone. Rewards go to the local base, otherwise field materials. The old Cutlass expedition and mine routes still work.",
            ),
            (
                "Carrack intelligence and Y-wing bombing",
                "Carrack recon scans one player at one surveyed body and privately records local ordinary ships, payloads, assignments and active/disabled colony structures. Intel lasts 4h from the original completion deadline, not from whenever you next log in. Select Y-wing + bomb with that target/body and choose a live objective from autocomplete. A strike takes 1h, base landing chance 80%. Hit one mine or lab for 4h downtime, empty one local ammo depot and disable it 4h, or damage one docked ordinary spacecraft. Never Earth wallets/vaults, THOR assets, ISD components, B-wing packages or Death Star assets. Coalition members cannot attack each other; objective/alliance checks run again at impact.",
            ),
            (
                "Arquitens command cruiser",
                "Build for 600 quadrillion / 20h. Turbolaser capacitor packs cost 10 quadrillion each; capacity four, 30-minute preparation per batch. Carrack recon unlocks `/space fleet mission` with Arquitens + broadside: choose a target, surveyed body, objective and optional second_objective. One pack, 1h flight, 70% base hit chance per selected hull. Target one or two different docked ordinary spacecraft or active escorts, not travelling industrial missions, structures, Earth assets or superweapon equipment. Hits damage ships and lose their payloads, not their hull purchase. Cooldown: 3h after arrival; recall/patrol do not reset it. If no hull is hit against an escort, 30% risk of attacker damage. Security patrol costs TWO packs upfront, protects you/an active ally at one body for 6h, and reduces success by 30 points for up to TWO encounters. The strongest single escort answers each encounter; bonuses never stack. Repair: 150 quadrillion / 6h.",
            ),
            (
                "Escorts and raids",
                "Use `/space fleet escort` with X-wing, A-wing, Hammerhead or Arquitens to protect yourself or a current ally at a surveyed body for up to 6h. Each hull can only have one assignment. The strongest matching escort responds, reducing incoming success by 15/25/35/30 percentage points, respectively; Arquitens has two responses, the others one. Escort walls do not stack. Escorts can be recalled, but their spent payload is not refunded. X-wing and A-wing raid one active mining/salvage/material convoy with `/space fleet raid`. Base success 65%; each successful raid removes 25% of its remaining material/salvage reward. Max two raids per mission, one per attacker. Misses against an escort have a 25% depot-damage risk. Raiders have a 2h turnaround. The original `/space intercept` still targets the 24h Earth→ISD load with its established 25%/5%-cargo rules. Fleet escorts do not replace superweapon interception packages.",
            ),
            (
                "Supplies and the Interdictor",
                "GR-75 supply missions deliver 1–100 already-owned materials from one completed base to a different completed base belonging to you/an active ally. Set source, body, optional target and amount (all/half supported). Materials leave the source at departure and remain exposed, not duplicated. A lost endpoint or expired alliance returns surviving cargo to the sender where possible. The GR-75 cannot also load an ISD during a convoy. Interdictor uses `/space fleet raid` against a live ordinary mission: 70% base success, one fixed 1h delay, never chainable; it does not erase wealth or stop ISD/Death Star/THOR launches. It creates a visible raid opportunity and takes a 2h turnaround.",
            ),
            (
                "Repairs and local ammunition",
                "Ordinary-space combat damages hulls instead of deleting expensive purchases. `/space fleet repair` costs 25% of the original hull price and takes 3h (Carrack 4h, Hammerhead/Arquitens 6h, Interdictor 8h). A damaged ship loses onboard payloads and cannot operate until repaired. THOR destroys every ordinary hull/build still on Earth's ground, but a launched, orbiting or off-world hull keeps its payload, timers and mission. Earth-orbit ships are NOT ground ships. Aborted/intercepted launches return to ground risk. Server/planet wipes retain their broader rules; this is not Raven Rock protection for other cargo. At a completed colony base, `/space fleet depot` builds a 50 quadrillion/6h local ammo depot, stocks up to six packs per ordinary craft at normal payload prices, and loads an idle hull docked there. Y-wings can empty that depot; no THOR ammunition enters it.",
            ),
        ]
        sections = (
            (space_guides.orientation_sections() if include_orientation else [])
            + sections
            + space_guides.ship_sections()
            + space_guides.troubleshooting_sections()
        )
        pages = [
            ui.base_embed(title=f"Space Fleet {i}/{len(sections)} — {name}", description=text)
            for i, (name, text) in enumerate(sections, 1)
        ]
        for page in pages:
            page.set_footer(text="Topic dropdown or page buttons · only the requester can navigate")
        return pages

    @fleet_group.command(
        name="status", description="Privately inspect every ordinary craft, timers, results and recon."
    )
    @app_commands.choices(craft=CRAFT_CHOICES)
    async def fleet_status(
        self, interaction: discord.Interaction, craft: Optional[app_commands.Choice[str]] = None
    ) -> None:
        nyx.protect_report(interaction, self.econ)
        if craft and craft.value == "nyx":
            await self.nyx_action(interaction, "status")
            return
        if not await self.guard(interaction, ephemeral=True):
            return
        user = self.user(interaction.guild_id, interaction.user.id)
        topics = []
        overview = []
        ship_topics = []
        attention = []
        unbuilt = []
        for key, spec in fleet.CRAFT.items():
            hull = fleet.ship(user, key)
            building = fleet.build_until(user, key)
            if (
                not fleet.owned(user, key)
                and (not building)
                and (not hull["repair_until"])
                and (not hull["launch"])
            ):
                unbuilt.append(spec.name)
                continue
            label = (
                "building"
                if building
                else "repairing"
                if hull["repair_until"]
                else "damaged"
                if hull["damaged"]
                else "launching"
                if hull["launch"]
                else "operational"
                if hull["deployed"]
                else "grounded"
            )
            location = (
                "Earth orbit"
                if hull["location"] == "earth" and hull["deployed"]
                else status_ui.body_name(hull["location"], space.BODIES)
            )
            rows = [
                status_ui.line("State", status_ui.state(label)),
                status_ui.line("Location", location),
                status_ui.line(
                    "Payload", f"{fleet.payload(user, key)}/{spec.capacity} · {spec.payload_name}"
                ),
            ]
            mission = hull["mission"]
            if mission:
                rows.append(
                    status_ui.line(
                        "Mission", f"{mission['kind']} · {status_ui.body_name(mission['body'], space.BODIES)}"
                    )
                )
                if mission["kind"] == "escort":
                    rows.append(status_ui.line("Patrol responses", mission.get("responses_remaining", 1)))
                if mission["kind"] == "launch-escort":
                    rows.append(
                        status_ui.line(
                            "Launch window",
                            f"`{mission['window_id']}` · {mission.get('responses_remaining', 0)} reserved responses left; unused packs return on resolution",
                        )
                    )
                if mission.get("objectives"):
                    rows.append(status_ui.line("Objectives", ", ".join(mission["objectives"])))
            if label in {"damaged", "grounded"}:
                attention.append(f"**{spec.name}:** {status_ui.state(label)}")
            overview.append(
                f"**{spec.name}**\n{status_ui.line('State', status_ui.state(label))}\n{status_ui.line('Payload', f'{fleet.payload(user, key)}/{spec.capacity}')} · {location}"
            )
            timers = []
            for label, raw in (
                ("Construction", fleet.build_until(user, key)),
                ("Earth launch", (hull["launch"] or {}).get("ready_at")),
                ("Payload prep", hull["payload_until"]),
                ("Repair", hull["repair_until"]),
                ("Mission", (hull["mission"] or {}).get("ready_at")),
                ("Broadside cooldown", hull["combat_until"]),
            ):
                due = space.at(raw)
                if due:
                    timers.append(status_ui.line(label, status_ui.deadline(due)))
            ship_topics.append(
                (
                    spec.name,
                    [
                        ("🚀 Craft readiness", "\n".join(rows)),
                        (
                            "⏳ Construction, loading and turnaround",
                            "\n".join(timers) or "No timers running.",
                        ),
                        (
                            "➡️ Next action",
                            "Use `/space fleet launch` for grounded craft, `/space fleet repair` for damage, and `/space fleet mission` for deployments. Full instructions: `/space fleet guide`.",
                        ),
                    ],
                )
            )
        overview_fields = [
            ("⚠️ Attention needed", "\n\n".join(attention) or "No grounded or damaged owned craft."),
            (
                "🚀 Your fleet",
                "\n\n".join(overview) or "No spacecraft yet. Use `/space build` and `/space fleet guide`.",
            ),
            (
                "🛰️ NYX counter-intelligence satellite",
                nyx.summary(user, self.bot.config.for_guild(interaction.guild_id)),
            ),
        ]
        if unbuilt:
            overview_fields.append(("⚪ Not built", " · ".join(unbuilt)))
        topics.append(("Fleet overview", overview_fields))
        topics.extend(ship_topics)
        state = fleet.normalize(user)
        censored_tracks = False
        for target in list(state["recon"]):
            expires = space.at(state["recon"][target].get("expires_at"))
            captured = expires - dt.timedelta(hours=fleet.RECON_HOURS) if expires else None
            if nyx.blocks_snapshot(
                self.doc(interaction.guild_id).get("users", {}).get(str(target), {}), captured
            ):
                state["recon"].pop(target, None)
                censored_tracks = True
        if censored_tracks:
            await self.econ.save(interaction.guild_id)
        intel = []
        for target, entry in state["recon"].items():
            if space.at(entry["expires_at"]) <= space.now():
                continue
            intel.append(
                (
                    f"🔎 Intel on <@{target}>",
                    status_ui.line("Location", status_ui.body_name(entry["body"], space.BODIES))
                    + "\n"
                    + status_ui.line("Expires", status_ui.deadline(entry["expires_at"]))
                    + "\n\n"
                    + "\n".join(entry["rows"]),
                )
            )
        intel_subjects = list(state["recon"])
        topics.append(("Intelligence", intel or [("🔎 Reconnaissance", "No active fleet intelligence.")]))
        history = [
            f"**{(fleet.CRAFT[r['craft']].name if r['craft'] in fleet.CRAFT else r['craft'])} — {r['kind']}**\n"
            + status_ui.line("Location", status_ui.body_name(r["body"], space.BODIES))
            + "\n"
            + status_ui.line("Result", r["result"])
            for r in state["history"][-8:]
        ]
        topics.append(
            ("Mission history", [("📜 Recent results", "\n\n".join(history) or "No completed missions yet.")])
        )
        pages = status_ui.report(
            "🚀 SPACE FLEET STATUS",
            topics,
            checked_at=space.now(),
            footer="Private fleet report · choose a ship or topic · /space fleet guide",
        )
        selected = craft.value if craft else None
        if not selected:
            selected = next((key for key in fleet.CRAFT if fleet.ship(user, key)["damaged"]), None)
        if not selected and state["history"]:
            selected = state["history"][-1]["craft"]
        picture = None
        if selected:
            hull = fleet.ship(user, selected)
            stage = (
                "repair"
                if hull["repair_until"]
                else "damaged"
                if hull["damaged"]
                else "build"
                if fleet.build_until(user, selected)
                else "payload"
                if hull["payload_until"]
                else "mission"
            )
            if selected == "arquitens" and stage == "mission" and hull["mission"]:
                stage = "patrol" if hull["mission"]["kind"] == "escort" else "broadside"
            picture = self.art(pages[0], f"fleet/{selected}-{stage}.png")

        def page_guard(_index):
            users = self.doc(interaction.guild_id).get("users", {})
            if any((nyx.active(users.get(str(uid), {})) for uid in intel_subjects)):
                return nyx.censored_embed()
            return None

        nyx.protect_report(interaction, self.econ, interaction.user.id, *intel_subjects)
        await ui.respond(
            interaction,
            embed=pages[0],
            file=picture,
            view=status_ui.pager(pages, interaction.user.id, page_guard=page_guard),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @fleet_group.command(
        name="launch",
        description="Launch a completed ground hull from Earth through a 30-minute counter window.",
    )
    @app_commands.choices(craft=CRAFT_CHOICES)
    async def fleet_launch(self, interaction: discord.Interaction, craft: app_commands.Choice[str]) -> None:
        if craft.value == "nyx":
            await self.nyx_action(interaction, "launch")
            return
        if not await self.guard(interaction):
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        async with self.lock(gid):
            user = self.user(gid, uid)
            try:
                record = fleet.launch(user, craft.value, space.now(), secrets.token_hex(6))
            except ValueError as error:
                await ui.respond(interaction, str(error), ephemeral=True)
                return
            await self.econ.save(gid)
        await self.bot.ledger.record(gid, uid, 0, "space-earth-launch", after=net(user), detail=craft.value)
        due = space.at(record["ready_at"])
        heavy = craft.value in fleet.HEAVY_CRAFT
        await self.send(
            interaction,
            ui.base_embed(
                title="EARTH SPACECRAFT LAUNCH",
                description=f"{interaction.user.display_name}'s **{craft.name}** is launching from Earth.\nInterception closes <t:{int(due.timestamp())}:f> · <t:{int(due.timestamp())}:R>.\nCounters: {('X-wing 20%, A-wing 30%, B-wing package 40%' if heavy else 'X-wing 35%, A-wing 45%')}. Use `/space fleet intercept`; one attempt per player, three players maximum. A hit aborts deployment and requires hull repair; otherwise the ship becomes usable in orbit.",
            ),
            f"fleet/{craft.value}-mission.png",
        )

    @fleet_group.command(
        name="launches", description="List public Earth-launch windows without exposing private holdings."
    )
    async def fleet_launches(self, interaction: discord.Interaction) -> None:
        if not await self.guard(interaction):
            return
        rows = []
        subjects = []
        for uid, user in self.doc(interaction.guild_id).get("users", {}).items():
            if not isinstance(user, dict):
                continue
            if nyx.active(user):
                rows.append(nyx.CENSORED)
                subjects.append(None)
                continue
            nyx_record = (user.get("nyx") or {}).get("launch")
            if isinstance(nyx_record, dict) and nyx_record.get("announced"):
                due = space.at(nyx_record.get("resolves_at"))
                if due and due > space.now():
                    rows.append(
                        f"<@{uid}> · NYX satellite (`nyx`) · closes <t:{int(due.timestamp())}:R> · {len(nyx_record.get('interceptors', []))}/3 attempts · counter: GBI/EKV"
                    )
                    subjects.append(uid)
            for key, hull in fleet.normalize(user)["ships"].items():
                record = hull.get("launch") if isinstance(hull, dict) else None
                if key not in fleet.CRAFT or not isinstance(record, dict):
                    continue
                due = space.at(record.get("ready_at"))
                if due and due > space.now():
                    rows.append(
                        f"<@{uid}> · {fleet.CRAFT[key].name} (`{key}`) · closes <t:{int(due.timestamp())}:R> · {len(record['attempts'])}/{fleet.LAUNCH_ATTEMPTS} attempts"
                    )
                    subjects.append(uid)
        rows = rows or ["No open Earth spacecraft launches. Build a hull, then `/space fleet launch`."]
        pages = [
            ui.base_embed(title="PUBLIC EARTH LAUNCH WINDOWS", description="\n".join(rows[i : i + 10]))
            for i in range(0, len(rows), 10)
        ]

        def page_guard(index):
            users = self.doc(interaction.guild_id).get("users", {})
            if any(
                (
                    uid is not None and nyx.active(users.get(str(uid), {}))
                    for uid in subjects[index * 10 : (index + 1) * 10]
                )
            ):
                return nyx.censored_embed()
            return None

        await ui.respond(
            interaction,
            embed=pages[0],
            view=ui.Paginator(pages, interaction.user.id, page_guard=page_guard),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @fleet_group.command(
        name="intercept", description="Intercept an Earth launch: fighters/B-wing for hulls, GBI/EKV for NYX."
    )
    @app_commands.choices(
        craft=CRAFT_CHOICES,
        counter=[
            app_commands.Choice(name=n, value=k)
            for k, n in (
                ("xwing", "T-65 X-wing"),
                ("awing", "RZ-1 A-wing"),
                ("bwing", "B-wing Ion Assault Package"),
                ("gbi", "GBI/EKV (NYX only)"),
            )
        ],
    )
    async def fleet_intercept(
        self,
        interaction: discord.Interaction,
        target: discord.Member,
        craft: app_commands.Choice[str],
        counter: app_commands.Choice[str],
    ) -> None:
        if craft.value == "nyx":
            if counter.value != "gbi":
                await ui.respond(
                    interaction,
                    "NYX ascent requires a ready GBI/EKV, not a fighter or B-wing package.",
                    ephemeral=True,
                )
                return
            await self.nyx_action(interaction, "intercept", target=target)
            return
        if counter.value == "gbi":
            await ui.respond(
                interaction,
                "GBI/EKV here is for NYX only. Choose a suitable fighter or B-wing for ordinary hulls.",
                ephemeral=True,
            )
            return
        if not await self.guard(interaction):
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        async with self.lock(gid):
            user = self.user(gid, uid)
            self.user(gid, target.id)
            isd.settle(user)
            try:
                result = fleet.launch_intercept(
                    self.doc(gid),
                    uid,
                    target.id,
                    craft.value,
                    counter.value,
                    space.now(),
                    secrets.token_hex(6),
                )
            except ValueError as error:
                await ui.respond(interaction, str(error), ephemeral=True)
                return
            await self.econ.save(gid)
        await self.bot.ledger.record(
            gid,
            uid,
            0,
            "space-launch-intercept-hit" if result["hit"] else "space-launch-intercept-miss",
            after=net(user),
            other=target.id,
            detail=f"{counter.name} against {craft.name}",
        )
        await self.send(
            interaction,
            ui.base_embed(
                title="EARTH LAUNCH INTERCEPTION",
                description=f"{interaction.user.display_name}'s {counter.name} challenged {target.display_name}'s {craft.name}.\n"
                + (
                    "Hit: launch aborted, onboard payload lost. Repair the ground hull, then launch again."
                    if result["hit"]
                    else "Miss: the launch continues. The interceptor's payload/package was spent."
                ),
            ),
            f"fleet/{craft.value}-{('damaged' if result['hit'] else 'mission')}.png",
        )

    @fleet_group.command(
        name="payload", description="Buy service packs/ammunition for an ordinary spacecraft."
    )
    @app_commands.choices(craft=[app_commands.Choice(name=s.name, value=k) for k, s in fleet.CRAFT.items()])
    @app_commands.describe(qty="Units to prepare within the craft's capacity")
    async def fleet_payload(
        self,
        interaction: discord.Interaction,
        craft: app_commands.Choice[str],
        qty: app_commands.Range[int, 1, 6] = 1,
    ) -> None:
        nyx.protect_report(interaction, self.econ)
        if not await self.guard(interaction):
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        key = craft.value
        async with self.lock(gid):
            user = self.user(gid, uid)
            try:
                cost = fleet.prepare(user, key, int(qty), space.now())
            except ValueError as error:
                await ui.respond(interaction, str(error), ephemeral=True)
                return
            await self.econ.save(gid)
            due = space.at(fleet.ship(user, key)["payload_until"])
        await self.bot.ledger.record(
            gid, uid, -cost, "space-fleet-payload", after=net(user), detail=f"{key} ×{qty}"
        )
        await self.send(
            interaction,
            ui.ok_embed(
                f"{craft.name}: {qty} payload(s), paid {fmt(cost)}. "
                + (f"Ready <t:{int(due.timestamp())}:R>." if due else "Ready now.")
            ),
            f"fleet/{key}-payload.png",
        )

    async def fleet_objectives(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        ns = interaction.namespace
        raw = getattr(ns, "target", None)
        try:
            target = int(getattr(raw, "id", raw) or 0)
        except (TypeError, ValueError):
            return []
        body_key = str(getattr(ns, "body", "") or "").lower()
        user = self.econ.all_users(interaction.guild_id).get(str(target)) if interaction.guild_id else None
        if user and nyx.active(user):
            return []
        own = self.user(interaction.guild_id, interaction.user.id) if interaction.guild_id else {}
        intel = fleet.normalize(own)["recon"].get(str(target), {})
        expires = space.at(intel.get("expires_at"))
        if user and nyx.blocks_snapshot(
            user, expires - dt.timedelta(hours=fleet.RECON_HOURS) if expires else None
        ):
            return []
        if (
            not user
            or intel.get("body") != body_key
            or (not space.at(intel.get("expires_at")))
            or (space.at(intel["expires_at"]) <= space.now())
        ):
            return []
        mission = getattr(ns, "mission", "")
        resolver = (
            fleet.broadside_objectives
            if getattr(mission, "value", mission) == "broadside"
            else fleet.objectives
        )
        values = resolver(user, body_key, space.now())
        return [
            app_commands.Choice(
                name=fleet.CRAFT[v[5:]].name if v.startswith("ship:") else v.replace("_", " ").title(),
                value=v,
            )
            for v in values
            if current.lower()
            in (v + " " + (fleet.CRAFT[v[5:]].name if v.startswith("ship:") else "")).lower()
        ][:25]

    async def fleet_second_objectives(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        return [
            choice
            for choice in await self.fleet_objectives(interaction, current)
            if choice.value != getattr(interaction.namespace, "objective", None)
        ]

    @fleet_group.command(
        name="mission", description="Industry, recon, bombing, broadside or NYX's self-only Jam field."
    )
    @app_commands.choices(craft=CRAFT_CHOICES)
    @app_commands.choices(
        mission=[
            app_commands.Choice(name=k.title(), value=k)
            for k in ("mine", "salvage", "survey", "recon", "supply", "bomb", "broadside", "jam")
        ]
    )
    @app_commands.autocomplete(
        body=autocomplete_body,
        source=autocomplete_body,
        objective=fleet_objectives,
        second_objective=fleet_second_objectives,
    )
    @app_commands.describe(
        body="Surveyed destination; leave blank for NYX Jam",
        target="Recon/bomb/broadside target or allied supply recipient",
        source="Supply source colony",
        amount="Supply materials: 1–100, half or all",
        wreck="Battle wreck ID; blank = natural debris",
        objective="First local target; fresh Carrack recon required",
        second_objective="Optional different spacecraft; Arquitens broadside only",
    )
    async def fleet_mission(
        self,
        interaction: discord.Interaction,
        craft: app_commands.Choice[str],
        mission: app_commands.Choice[str],
        body: Optional[str] = None,
        target: Optional[discord.Member] = None,
        objective: Optional[str] = None,
        source: Optional[str] = None,
        amount: str = "all",
        wreck: Optional[str] = None,
        second_objective: Optional[str] = None,
    ) -> None:
        if craft.value == "nyx":
            if mission.value != "jam" or any((body, target, objective, source, wreck, second_objective)):
                await ui.respond(
                    interaction,
                    "NYX uses mission:Jam on yourself. Leave body, target and objectives blank.",
                    ephemeral=True,
                )
                return
            await self.nyx_action(interaction, "activate")
            return
        if mission.value == "jam" or not body:
            await ui.respond(
                interaction,
                "Choose a surveyed body for this craft; Jam is only available to NYX.",
                ephemeral=True,
            )
            return
        if not await self.guard(interaction):
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        key = craft.value
        async with self.lock(gid):
            user = self.user(gid, uid)
            doc = self.doc(gid)
            if target:
                self.user(gid, target.id)
            source_key = (source or "").strip().lower()
            body_key = body.strip().lower()
            try:
                qty = 0
                if mission.value == "supply":
                    bag = space.normalize(user)["colonies"].get(source_key, {})
                    available = min(100, space.material_total(bag))
                    qty = parse_amount(amount, available=available)
                record = fleet.start(
                    doc,
                    uid,
                    key,
                    mission.value,
                    body_key,
                    space.now(),
                    secrets.token_hex(6),
                    target_uid=target.id if target else None,
                    objective=objective,
                    source=source_key,
                    amount=qty,
                    wreck_id=wreck,
                    second_objective=second_objective,
                )
            except (ValueError, AmountParseError) as error:
                await ui.respond(interaction, str(error), ephemeral=True)
                return
            await self.econ.save(gid)
        await self.bot.ledger.record(
            gid,
            uid,
            0,
            "space-fleet-mission",
            after=net(user),
            other=target.id if target else None,
            detail=f"{key}/{mission.value}/{body_key}",
        )
        due = space.at(record["ready_at"])
        await self.send(
            interaction,
            ui.base_embed(
                title=f"{craft.name.upper()} — {mission.name.upper()}",
                description=f"Mission `{record['id']}` at {body_key}; one payload committed.\nCompletes <t:{int(due.timestamp())}:f> · <t:{int(due.timestamp())}:R>.\nCheck `/space fleet status` for settled results; deploy an escort to protect industrial missions.",
            ),
            f"fleet/{key}-{('broadside' if key == 'arquitens' else 'mission')}.png",
        )

    @fleet_group.command(
        name="escort",
        description="Assign one fighter/corvette/cruiser to protect you or an ally for six hours.",
    )
    @app_commands.choices(
        craft=[
            app_commands.Choice(name=fleet.CRAFT[k].name, value=k)
            for k in ("xwing", "awing", "hammerhead", "arquitens")
        ]
    )
    @app_commands.autocomplete(body=autocomplete_body)
    async def fleet_escort(
        self,
        interaction: discord.Interaction,
        craft: app_commands.Choice[str],
        body: str,
        target: Optional[discord.Member] = None,
    ) -> None:
        if not await self.guard(interaction):
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        async with self.lock(gid):
            user = self.user(gid, uid)
            doc = self.doc(gid)
            try:
                record = fleet.escort(
                    doc,
                    uid,
                    craft.value,
                    target.id if target else uid,
                    body.strip().lower(),
                    space.now(),
                    secrets.token_hex(6),
                )
            except ValueError as error:
                await ui.respond(interaction, str(error), ephemeral=True)
                return
            await self.econ.save(gid)
        await self.send(
            interaction,
            ui.ok_embed(
                f"{craft.name} escort assigned at {record['body']} until <t:{int(space.at(record['ready_at']).timestamp())}:R>. Protects one player; strongest escort only. {record['responses_remaining']} response(s); {fleet.CRAFT[craft.value].escort_bonus}-point reduction. No stacking."
            ),
            f"fleet/{craft.value}-{('patrol' if craft.value == 'arquitens' else 'mission')}.png",
        )

    @fleet_group.command(
        name="raid", description="Raid or delay one player's active ordinary industrial spacecraft mission."
    )
    @app_commands.choices(
        craft=[
            app_commands.Choice(name=fleet.CRAFT[k].name, value=k) for k in ("xwing", "awing", "interdictor")
        ]
    )
    @app_commands.choices(
        victim_craft=[
            app_commands.Choice(name=fleet.CRAFT[k].name, value=k)
            for k in ("prospector", "vulture", "transport")
        ]
    )
    async def fleet_raid(
        self,
        interaction: discord.Interaction,
        craft: app_commands.Choice[str],
        target: discord.Member,
        victim_craft: app_commands.Choice[str],
    ) -> None:
        if not await self.guard(interaction):
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        async with self.lock(gid):
            user = self.user(gid, uid)
            self.user(gid, target.id)
            try:
                result = fleet.raid(
                    self.doc(gid),
                    uid,
                    craft.value,
                    target.id,
                    victim_craft.value,
                    space.now(),
                    secrets.token_hex(6),
                )
            except ValueError as error:
                await ui.respond(interaction, str(error), ephemeral=True)
                return
            await self.econ.save(gid)
        await self.bot.ledger.record(
            gid,
            uid,
            0,
            "space-fleet-hit" if result["hit"] else "space-fleet-miss",
            after=net(user),
            other=target.id,
            detail=f"{craft.name} against {victim_craft.name} at {result['body']}",
        )
        text = f"{interaction.user.display_name}'s {craft.name} targeted {target.display_name}'s {victim_craft.name} at {result['body']}.\n"
        text += (
            "Gravity lock: arrival delayed one hour; no wealth destroyed."
            if result["delayed_until"]
            else "Hit: 25% of the remaining mission cargo/reward lost."
            if result["hit"]
            else "The mission escaped."
        )
        if result["damaged"]:
            text += "\nThe raider was intercepted and needs depot repair."
        await self.send(
            interaction,
            ui.base_embed(title="ORDINARY SPACE ENCOUNTER", description=text),
            f"fleet/{craft.value}-{('damaged' if result['damaged'] else 'mission')}.png",
        )

    @fleet_group.command(
        name="repair", description="Repair a damaged ordinary spacecraft for 25% of its hull cost."
    )
    @app_commands.choices(craft=[app_commands.Choice(name=s.name, value=k) for k, s in fleet.CRAFT.items()])
    async def fleet_repair(self, interaction: discord.Interaction, craft: app_commands.Choice[str]) -> None:
        nyx.protect_report(interaction, self.econ)
        if not await self.guard(interaction):
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        async with self.lock(gid):
            user = self.user(gid, uid)
            try:
                cost = fleet.repair(user, craft.value, space.now())
            except ValueError as error:
                await ui.respond(interaction, str(error), ephemeral=True)
                return
            await self.econ.save(gid)
        due = space.at(fleet.ship(user, craft.value)["repair_until"])
        await self.bot.ledger.record(
            gid, uid, -cost, "space-fleet-repair", after=net(user), detail=craft.value
        )
        await self.send(
            interaction,
            ui.ok_embed(
                f"{craft.name} repair paid {fmt(cost)}; ready <t:{int(due.timestamp())}:R>. Payloads must be prepared again."
            ),
            f"fleet/{craft.value}-repair.png",
        )

    @fleet_group.command(
        name="recall", description="Recall an escort assignment without refunding its service pack."
    )
    @app_commands.choices(
        craft=[
            app_commands.Choice(name=fleet.CRAFT[k].name, value=k)
            for k in ("xwing", "awing", "hammerhead", "arquitens")
        ]
    )
    async def fleet_recall(self, interaction: discord.Interaction, craft: app_commands.Choice[str]) -> None:
        nyx.protect_report(interaction, self.econ)
        if not await self.guard(interaction, ephemeral=True):
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        async with self.lock(gid):
            hull = fleet.ship(self.user(gid, uid), craft.value)
            mission = hull["mission"]
            if not isinstance(mission, dict) or mission["kind"] != "escort":
                await ui.respond(
                    interaction, "Only an active escort assignment can be recalled.", ephemeral=True
                )
                return
            hull.update(mission=None, location=mission["body"])
            await self.econ.save(gid)
        await ui.respond(
            interaction, "Escort recalled; its committed payload is not refunded.", ephemeral=True
        )

    @fleet_group.command(
        name="wrecks", description="List finite unclaimed salvage at a body; no private player holdings."
    )
    @app_commands.autocomplete(body=autocomplete_body)
    async def fleet_wrecks(self, interaction: discord.Interaction, body: str) -> None:
        if not await self.guard(interaction):
            return
        rows = [
            f"`{key}` — {sum(record.get('materials', {}).values())} materials + {fmt(record.get('donuts', 0))}"
            for key, record in space.galaxy(self.doc(interaction.guild_id)).get("wrecks", {}).items()
            if record.get("body") == body.lower() and (not record.get("claimed_by"))
        ]
        rows.append("Natural debris: one shared claim per body per UTC day; omit the wreck option to try it.")
        pages = [
            ui.base_embed(title=f"SALVAGE AT {body.upper()}", description="\n".join(rows[i : i + 12]))
            for i in range(0, len(rows), 12)
        ]
        await ui.respond(interaction, embed=pages[0], view=ui.Paginator(pages, interaction.user.id))

    @fleet_group.command(
        name="depot", description="Build, stock or load a colony's ordinary-spacecraft ammunition depot."
    )
    @app_commands.choices(
        action=[app_commands.Choice(name=k.title(), value=k) for k in ("build", "stock", "load")],
        craft=[app_commands.Choice(name=s.name, value=k) for k, s in fleet.CRAFT.items()],
    )
    @app_commands.autocomplete(body=autocomplete_body)
    async def fleet_depot(
        self,
        interaction: discord.Interaction,
        body: str,
        action: app_commands.Choice[str],
        craft: Optional[app_commands.Choice[str]] = None,
        qty: app_commands.Range[int, 1, 6] = 1,
    ) -> None:
        nyx.protect_report(interaction, self.econ)
        if not await self.guard(interaction):
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        async with self.lock(gid):
            user = self.user(gid, uid)
            try:
                cost = fleet.depot(
                    user,
                    self.doc(gid),
                    body.strip().lower(),
                    action.value,
                    craft.value if craft else None,
                    int(qty),
                    space.now(),
                )
            except ValueError as error:
                await ui.respond(interaction, str(error), ephemeral=True)
                return
            await self.econ.save(gid)
        await self.bot.ledger.record(
            gid, uid, -cost, "space-fleet-depot", after=net(user), detail=f"{body}/{action.value}"
        )
        await ui.respond(
            interaction,
            f"Depot {action.name.lower()} committed at {body}; paid {fmt(cost)}. Construction 6h; normal stock transfers immediate.",
        )

    @space_group.command(
        name="load", description="Escrow selected assets into a 24-hour GR-75 loading operation."
    )
    @app_commands.choices(
        kind=[
            app_commands.Choice(name="Donuts", value="donuts"),
            app_commands.Choice(name="Item", value="inventory"),
            app_commands.Choice(name="Plushie", value="plushies"),
            app_commands.Choice(name="Fish", value="fish"),
            app_commands.Choice(name="Rod", value="rods"),
            app_commands.Choice(name="Vehicle", value="vehicles"),
        ]
    )
    @app_commands.describe(
        name="Item, plushie or vehicle ID; omit for donuts",
        amount="Quantity or donut shorthand such as 1qi, half, all",
    )
    async def load(
        self,
        interaction: discord.Interaction,
        kind: app_commands.Choice[str],
        amount: str = "1",
        name: Optional[str] = None,
    ) -> None:
        if not await self.guard(interaction):
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        async with self.lock(gid):
            user = self.user(gid, uid)
            state = self.settle_user(user)
            if not self.ready(user) or state["mode"] != "expedition" or state["refit_until"]:
                error = "You need an operational ISD in expedition mode."
            elif state["travel_until"] or state["location"] != "earth":
                error = "Start a new cargo operation at Earth, while not travelling."
            elif not state["transport_owned"]:
                error = "A ready GR-75 transport is required."
            elif not fleet.deployed(user, "transport"):
                error = "Launch your GR-75 from Earth with `/space fleet launch` and finish its counter window first."
            elif (
                fleet.ship(user, "transport")["mission"]
                or fleet.ship(user, "transport")["damaged"]
                or fleet.ship(user, "transport")["repair_until"]
            ):
                error = "Your GR-75 is assigned to a convoy or needs depot repair."
            else:
                error = None
            if error:
                await ui.respond(interaction, error, ephemeral=True)
                return
            field = kind.value
            item = (name or "").lower().strip()
            if field == "donuts":
                try:
                    qty = parse_amount(amount, available=max(0, int(user.get("donuts", 0))))
                except AmountParseError as exc:
                    await ui.respond(interaction, str(exc), ephemeral=True)
                    return
                available = max(0, int(user.get("donuts", 0)))
            elif field in {"inventory", "plushies", "fish"}:
                available = max(0, int(user.get(field, {}).get(item, 0)))
                if not item or available <= 0:
                    await ui.respond(interaction, "You do not own that item or plushie ID.", ephemeral=True)
                    return
                try:
                    qty = parse_amount(amount, available=available)
                except AmountParseError as exc:
                    await ui.respond(interaction, str(exc), ephemeral=True)
                    return
            elif field == "rods":
                available = 1 if user.get("rods", {}).get(item) else 0
                if not item or not available:
                    await ui.respond(interaction, "You do not own that rod ID.", ephemeral=True)
                    return
                if (
                    item in (state.get("load_manifest") or {}).get("rods", {})
                    or item in state["cargo"]["rods"]
                ):
                    await ui.respond(
                        interaction, "That rod is already aboard or being loaded.", ephemeral=True
                    )
                    return
                qty = 1
            else:
                vehicle = user.get("vehicles", {}).get(item, {})
                economy_cog = self.bot.get_cog("Economy")
                if economy_cog is not None and item in VEHICLE_CATALOG:
                    vehicle = economy_cog._vehicle_settle(user, item)
                if not isinstance(vehicle, dict):
                    await ui.respond(
                        interaction, "You do not own a ready vehicle with that ID.", ephemeral=True
                    )
                    return
                patrol_until = space.at(vehicle.get("patrol_until"))
                if vehicle.get("jet_damaged") or (patrol_until and patrol_until > space.now()):
                    await ui.respond(
                        interaction,
                        "Repair this jet or finish its patrol before packing it aboard.",
                        ephemeral=True,
                    )
                    return
                if not isinstance(vehicle, dict) or not vehicle.get("owned"):
                    await ui.respond(
                        interaction, "You do not own a ready vehicle with that ID.", ephemeral=True
                    )
                    return
                if (
                    vehicle.get("building_until")
                    or vehicle.get("loading_until")
                    or vehicle.get("garrison_country")
                ):
                    await ui.respond(
                        interaction,
                        "Finish building, loading or withdrawing that vehicle first.",
                        ephemeral=True,
                    )
                    return
                if (
                    item in (state.get("load_manifest") or {}).get("vehicles", {})
                    or item in state["cargo"]["vehicles"]
                ):
                    await ui.respond(
                        interaction, "That vehicle is already aboard or being loaded.", ephemeral=True
                    )
                    return
                qty = available = 1
            if qty <= 0 or qty > available:
                await ui.respond(interaction, "That exceeds your available amount.", ephemeral=True)
                return
            starting = state["load_until"] is None
            fee = (space.LOAD_BASE_COST if starting else 0) + (qty // 100 if field == "donuts" else 0)
            if net(user) < fee + (qty if field == "donuts" else 0):
                await ui.respond(
                    interaction, f"Need {fmt(fee)} in fees plus the selected donuts.", ephemeral=True
                )
                return
            if field == "donuts" and int(user.get("donuts", 0)) < qty:
                await ui.respond(interaction, "Loaded donuts must be in your wallet.", ephemeral=True)
                return
            if field == "donuts":
                user["donuts"] = int(user.get("donuts", 0)) - qty
            take(user, fee)
            manifest = state.get("load_manifest")
            if not isinstance(manifest, dict):
                manifest = state["load_manifest"] = {
                    "donuts": 0,
                    "inventory": {},
                    "plushies": {},
                    "fish": {},
                    "rods": {},
                    "vehicles": {},
                }
            if field == "donuts":
                manifest["donuts"] = int(manifest.get("donuts", 0)) + qty
            elif field == "vehicles":
                manifest["vehicles"][item] = copy.deepcopy(user["vehicles"][item])
                user["vehicles"][item] = copy.deepcopy(_default_user(0)["vehicles"][item])
            elif field == "rods":
                manifest["rods"][item] = {
                    "enchants": copy.deepcopy(user.get("rod_enchants", {}).get(item, [])),
                    "equipped": user.get("equipped_rod") == item,
                }
                user["rods"].pop(item, None)
                user.setdefault("rod_enchants", {}).pop(item, None)
                if user.get("equipped_rod") == item:
                    user["equipped_rod"] = None
                    user["autofish"] = False
            else:
                user[field][item] -= qty
                if user[field][item] <= 0:
                    user[field].pop(item, None)
                manifest[field][item] = int(manifest[field].get(item, 0)) + qty
            if starting:
                state["load_until"] = (space.now() + dt.timedelta(hours=space.LOAD_HOURS)).isoformat()
                state["load_hits"] = 0
                state["interceptors"] = []
            due = space.at(state["load_until"])
            await self.econ.save(gid)
        await self.bot.ledger.record(
            gid,
            uid,
            -(fee + (qty if field == "donuts" else 0)),
            "space-cargo-load",
            after=net(user),
            detail=field,
        )
        await self.send(
            interaction,
            ui.ok_embed(
                f"{(ui.format_donuts(qty) if field == 'donuts' else format(qty, ','))} {field} escrowed for your ISD. Loading completes <t:{int(due.timestamp())}:R>. It can be intercepted until then."
            ),
            "gr75-loading-v3.png",
            ephemeral=True,
        )

    @space_group.command(
        name="intercept", description="Use a ready X-wing and torpedo against an active GR-75 load."
    )
    async def intercept(self, interaction: discord.Interaction, target: discord.Member) -> None:
        if not await self.guard(interaction):
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        async with self.lock(gid):
            attacker = self.user(gid, uid)
            own = self.settle_user(attacker)
            victim = self.user(gid, target.id)
            state = self.settle_user(victim)
            if target.id == uid or coalitions.are_allied(self.doc(gid), uid, target.id):
                error = "You cannot intercept yourself or a coalition ally."
            elif not own["xwing_owned"] or own["xwing_ammo"] <= 0:
                error = "Build an X-wing and load a proton torpedo first."
            elif not fleet.deployed(attacker, "xwing"):
                error = "Launch your X-wing from Earth with `/space fleet launch` first."
            elif any(
                (
                    fleet.ship(attacker, "xwing").get(k)
                    for k in ("mission", "damaged", "repair_until", "payload_until")
                )
            ):
                error = "Your X-wing is assigned elsewhere or needs depot repair."
            elif not state["load_until"] or space.at(state["load_until"]) <= space.now():
                error = "That player has no active transport-loading window."
            elif len(state["interceptors"]) >= space.MAX_INTERCEPTIONS:
                error = "Both interception slots are filled."
            elif uid in state["interceptors"]:
                error = "You already attempted this convoy."
            else:
                error = None
            if error:
                await ui.respond(interaction, error, ephemeral=True)
                return
            own["xwing_ammo"] -= 1
            state["interceptors"].append(uid)
            hit = random.randint(1, 100) <= space.INTERCEPTION_CHANCE
            if hit:
                manifest = state["load_manifest"]
                manifest["donuts"] = int(manifest.get("donuts", 0)) * 95 // 100
                for item, qty in list(manifest.get("inventory", {}).items()):
                    manifest["inventory"][item] = max(0, int(qty) - int(qty) // 20)
                for item, qty in list(manifest.get("fish", {}).items()):
                    manifest["fish"][item] = max(0, int(qty) - int(qty) // 20)
                state["load_hits"] += 1
                state["transport_owned"] = False
                fleet.ship(victim, "transport")["deployed"] = False
                repair = space.now() + dt.timedelta(hours=12)
                state["transport_build_until"] = repair.isoformat()
                state["load_until"] = (
                    max(space.at(state["load_until"]), repair) + dt.timedelta(hours=12)
                ).isoformat()
            await self.econ.save(gid)
        await self.bot.ledger.record(
            gid,
            uid,
            0,
            "space-xwing-hit" if hit else "space-xwing-miss",
            after=net(attacker),
            other=target.id,
            detail="hit" if hit else "miss",
        )
        embed = ui.base_embed(
            title="X-WING TRANSPORT INTERCEPTION",
            description=f"<@{uid}> attacked <@{target.id}>'s GR-75. "
            + (
                "The transport was disabled; loading was delayed and some ordinary cargo was lost."
                if hit
                else "The transport escaped. Loading continues."
            ),
        )
        picture = self.art(embed, "xwing-intercept.png" if hit else "xwing-patrol.png")
        await ui.respond(
            interaction, embed=embed, file=picture, allowed_mentions=discord.AllowedMentions(users=True)
        )

    @space_group.command(name="travel", description="Send your Cutlass or expedition ISD to a surveyed body.")
    @app_commands.autocomplete(body=autocomplete_body)
    async def travel(self, interaction: discord.Interaction, body: str) -> None:
        nyx.protect_report(interaction, self.econ)
        if not await self.guard(interaction):
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        destination = self.selected(interaction, body)
        if not destination:
            await ui.respond(
                interaction, "Unknown body. Browse `/space atlas` or use `/space discover`.", ephemeral=True
            )
            return
        async with self.lock(gid):
            user = self.user(gid, uid)
            state = self.settle_user(user)
            craft = space.exploration_craft(user)
            if not craft:
                error = "Build and launch a Drake Cutlass (`/space fleet launch`), or refit an operational ISD to expedition mode."
            elif state["load_until"] or state["travel_until"] or state["expedition"]:
                error = "Finish loading, your current journey or expedition first."
            elif state["location"] == destination.key:
                error = "Your explorer is already there."
            elif destination.key != "earth" and destination.key not in state["surveys"]:
                error = "Survey this destination before travelling."
            else:
                error = None
            cost = space.travel_hours(destination.zone) * (space.QA if craft == "cutlass" else 5 * space.QA)
            if not error and net(user) < cost:
                error = f"This journey costs {fmt(cost)}."
            if error:
                await ui.respond(interaction, error, ephemeral=True)
                return
            take(user, cost)
            due = space.now() + dt.timedelta(hours=space.travel_hours(destination.zone))
            state.update(destination=destination.key, travel_until=due.isoformat(), travel_craft=craft)
            await self.econ.save(gid)
        await self.bot.ledger.record(gid, uid, -cost, "space-travel", after=net(user), detail=destination.key)
        await self.send(
            interaction,
            ui.base_embed(
                title="EXPLORATION VOYAGE UNDERWAY",
                description=f"Craft: **{('Drake Cutlass' if craft == 'cutlass' else 'ISD')}** · destination: **{destination.name}** · paid {fmt(cost)} · arrival <t:{int(due.timestamp())}:f> · <t:{int(due.timestamp())}:R>.",
            ),
            "drake-cutlass-explorer.png" if craft == "cutlass" else "isd-expedition.png",
        )

    @space_group.command(
        name="survey", description="Survey a body and reveal its conservative resource class."
    )
    @app_commands.autocomplete(body=autocomplete_body)
    async def survey(self, interaction: discord.Interaction, body: str) -> None:
        nyx.protect_report(interaction, self.econ)
        if not await self.guard(interaction, ephemeral=True):
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        target = self.selected(interaction, body)
        if not target:
            await ui.respond(interaction, "Unknown body.", ephemeral=True)
            return
        async with self.lock(gid):
            user = self.user(gid, uid)
            state = self.settle_user(user)
            independent_surveyor = any(
                (
                    fleet.deployed(user, key)
                    and (not fleet.ship(user, key)["damaged"])
                    and (not fleet.ship(user, key)["repair_until"])
                    for key in ("carrack", "prospector", "vulture")
                )
            )
            if not space.exploration_craft(user) and (not independent_surveyor):
                await ui.respond(
                    interaction,
                    content="A launched Cutlass, Carrack or industrial ship, or an expedition ISD, is required. Use `/space fleet launch` after construction.",
                    ephemeral=True,
                )
                return
            if target.key not in state["surveys"]:
                cost = 2 * space.QA
                if net(user) < cost:
                    await ui.respond(interaction, "Survey costs 2 quadrillion.", ephemeral=True)
                    return
                take(user, cost)
                state["surveys"][target.key] = space.now().isoformat()
                await self.econ.save(gid)
            else:
                cost = 0
        if cost:
            await self.bot.ledger.record(gid, uid, -cost, "space-survey", after=net(user), detail=target.key)
        await ui.respond(
            interaction,
            embed=ui.base_embed(
                title=f"SURVEY — {target.name}",
                description=f"Class: **{target.kind}** · region: **{target.zone}** · resource profile: **{target.resource}** · hazard **{target.hazard}/10** · {('landable' if target.landable else 'orbital station' if target.key in space.GIANT_PLANETS else 'survey only')}\n\nThis is a game index based on available observations, not a measured reserve estimate.",
            ),
            ephemeral=True,
        )

    @space_group.command(name="land", description="Establish a landing site at your ISD's current body.")
    async def land(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        if not await self.guard(interaction):
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        async with self.lock(gid):
            user = self.user(gid, uid)
            state = self.settle_user(user)
            target = space.body(self.doc(gid), state["location"])
            if (
                not self.ready(user)
                or state["travel_until"]
                or state["refit_until"]
                or state["expedition"]
                or (state["mode"] != "expedition")
            ):
                error = "Reach the destination with an operational expedition ISD and finish field missions first. The Cutlass cannot found colonies."
            elif not target or not space.colonisable(target):
                error = "This body cannot support a landing site or orbital platform."
            elif space.colony(state, target.key)["landed"]:
                error = "You already landed here."
            elif net(user) < 10 * space.QA:
                error = "A lander costs 10 quadrillion."
            else:
                error = None
            if error:
                await ui.respond(interaction, content=error, ephemeral=True)
                return
            take(user, 10 * space.QA)
            space.colony(state, target.key)["landed"] = True
            await self.econ.save(gid)
        await self.bot.ledger.record(
            gid, uid, -10 * space.QA, "space-lander", after=net(user), detail=target.key
        )
        site = (
            "Orbital platform established around"
            if target.key in space.GIANT_PLANETS
            else "Lander established on"
        )
        await self.send(
            interaction,
            ui.ok_embed(f"{site} **{target.name}**. Build a base next."),
            "gas-giant-station.png" if target.key in space.GIANT_PLANETS else "colony-landing.png",
        )

    @space_group.command(
        name="build-site", description="Build the next colony structure at your current world."
    )
    @app_commands.choices(
        structure=[
            app_commands.Choice(name=f"Base · {fmt(space.BASE_COST)}", value="base"),
            app_commands.Choice(name=f"Mining rig · {fmt(space.MINE_COST)}", value="mine"),
            app_commands.Choice(name=f"Research lab · {fmt(space.LAB_COST)} + 10 materials", value="lab"),
            app_commands.Choice(
                name=f"Terraforming Unit · {fmt(space.TERRAFORM_COST)} + 20 materials", value="terraform"
            ),
        ]
    )
    async def build_site(self, interaction: discord.Interaction, structure: app_commands.Choice[str]) -> None:
        nyx.protect_report(interaction, self.econ)
        if not await self.guard(interaction):
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        key = structure.value
        prices = {
            "base": space.BASE_COST,
            "mine": space.MINE_COST,
            "lab": space.LAB_COST,
            "terraform": space.TERRAFORM_COST,
        }
        previous = {"base": "landed", "mine": "base", "lab": "mine", "terraform": "lab"}
        materials_needed = {"base": 0, "mine": 0, "lab": 10, "terraform": 20}
        async with self.lock(gid):
            user = self.user(gid, uid)
            state = self.settle_user(user)
            body = space.body(self.doc(gid), state["location"])
            if (
                not self.ready(user)
                or state["mode"] != "expedition"
                or state["travel_until"]
                or (not body)
                or (not space.colonisable(body))
            ):
                error = "Bring an operational expedition ISD to a landable world or giant-planet orbit first."
            else:
                colony = space.colony(state, body.key)
                material = body.resource
                if colony[key]:
                    error = "That structure is already built."
                elif not colony[previous[key]]:
                    error = f"Build or complete {previous[key]} first."
                elif space.material_total(colony) < materials_needed[key]:
                    error = f"Need {materials_needed[key]} locally stored space materials first."
                elif net(user) < prices[key]:
                    error = f"This structure costs {fmt(prices[key])}."
                else:
                    error = None
            if error:
                await ui.respond(interaction, error, ephemeral=True)
                return
            take(user, prices[key])
            space.consume_materials(colony, materials_needed[key])
            colony[key] = True
            if key == "mine":
                colony["material_at"] = space.now().isoformat()
            await self.econ.save(gid)
        await self.bot.ledger.record(
            gid, uid, -prices[key], "space-site-build", after=net(user), detail=f"{body.key}:{key}"
        )
        await self.send(
            interaction,
            ui.ok_embed(
                f"**{key.replace('_', ' ').title()}** completed on **{body.name}**. Paid {fmt(prices[key])} donuts and {materials_needed[key]} materials."
            ),
            "colony-landing.png",
        )

    @space_group.command(
        name="mine",
        description="Earn a mining payout; colony materials accumulate automatically every six hours.",
    )
    async def mine(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        if not await self.guard(interaction, ephemeral=True):
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        async with self.lock(gid):
            user = self.user(gid, uid)
            state = self.settle_user(user)
            body = space.body(self.doc(gid), state["location"])
            colony = state["colonies"].get(body.key) if body else None
            if space.exploration_craft(user) == "cutlass":
                if (
                    not body
                    or body.kind != "asteroid"
                    or state["travel_until"]
                    or state["expedition"]
                    or ds.destroyed(self.doc(gid), body.key)
                ):
                    await ui.respond(
                        interaction,
                        content="Cutlass prospecting requires an intact asteroid, with no voyage or expedition underway.",
                        ephemeral=True,
                    )
                    return
                prior = space.at(state.get("field_mine_at"))
                if prior and prior + dt.timedelta(hours=6) > space.now():
                    await ui.respond(
                        interaction,
                        content="Cutlass prospecting is available once every six hours.",
                        ephemeral=True,
                    )
                    return
                count = 2
                payout = body.score * space.QA // 4
                state["field_materials"][body.resource] = (
                    int(state["field_materials"].get(body.resource, 0)) + count
                )
                state["field_mine_at"] = space.now().isoformat()
                user["donuts"] = int(user.get("donuts", 0)) + payout
                await self.econ.save(gid)
                await self.bot.ledger.record(
                    gid, uid, payout, "space-mining", after=net(user), detail=body.key
                )
                await ui.respond(
                    interaction,
                    content=f"Cutlass prospecting recovered {count} {body.resource} and {fmt(payout)} to wallet. Samples enter your field store, not the ISD cargo hold.",
                    ephemeral=True,
                )
                return
            if state["mode"] != "expedition" or state["travel_until"] or (not colony) or (not colony["mine"]):
                error = "You need a mining rig at your present destination."
            elif (
                space.at(colony.get("mine_disabled_until"))
                and space.at(colony["mine_disabled_until"]) > space.now()
            ):
                error = f"This mining rig is disabled until <t:{int(space.at(colony['mine_disabled_until']).timestamp())}:R>."
            elif (
                space.at(colony.get("mine_at"))
                and space.at(colony["mine_at"]) + dt.timedelta(hours=6) > space.now()
            ):
                error = f"Mining ready <t:{int((space.at(colony['mine_at']) + dt.timedelta(hours=6)).timestamp())}:R>."
            else:
                error = None
            if error:
                await ui.respond(interaction, error, ephemeral=True)
                return
            payout = body.score * space.QA // 2
            colony["mine_at"] = space.now().isoformat()
            user["donuts"] = int(user.get("donuts", 0)) + payout
            await self.econ.save(gid)
        await self.bot.ledger.record(gid, uid, payout, "space-mining", after=net(user), detail=body.key)
        await ui.respond(
            interaction,
            embed=ui.ok_embed(
                f"Earned **{fmt(payout)}** to wallet on {body.name}. Materials are produced automatically every six hours and remain in the local store; this command does not claim them twice."
            ),
        )

    @space_group.command(
        name="stow", description="Move mined material from your current base into the ISD cargo hold."
    )
    async def stow(self, interaction: discord.Interaction, material: str, amount: str = "all") -> None:
        nyx.protect_report(interaction, self.econ)
        if not await self.guard(interaction, ephemeral=True):
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        material = material.lower().strip()
        async with self.lock(gid):
            user = self.user(gid, uid)
            state = self.settle_user(user)
            colony = state["colonies"].get(state["location"])
            if (
                not self.ready(user)
                or state["refit_until"]
                or state["mode"] != "expedition"
                or state["travel_until"]
                or (state["location"] != "earth" and (not colony or not colony.get("base")))
            ):
                error = "Use this at a completed local base in expedition mode."
            else:
                error = None
            local = (colony or {}).get("materials", {})
            field = state["field_materials"]
            available = max(0, int(local.get(material, 0))) + max(0, int(field.get(material, 0)))
            try:
                qty = parse_amount(amount, available=available)
            except AmountParseError as exc:
                error = str(exc)
                qty = 0
            if not error and (qty <= 0 or qty > available):
                error = "That material quantity is not in your local store."
            if error:
                await ui.respond(interaction, error, ephemeral=True)
                return
            from_local = min(qty, max(0, int(local.get(material, 0))))
            if from_local:
                local[material] -= from_local
            if qty > from_local:
                field[material] -= qty - from_local
            cargo = state["cargo"]["materials"]
            cargo[material] = int(cargo.get(material, 0)) + qty
            await self.econ.save(gid)
        await ui.respond(interaction, f"Stowed {qty} {material} aboard the ISD.", ephemeral=True)

    @space_group.command(
        name="offload", description="Deliver ISD material cargo into your current colony store."
    )
    async def offload(self, interaction: discord.Interaction, material: str, amount: str = "all") -> None:
        nyx.protect_report(interaction, self.econ)
        if not await self.guard(interaction, ephemeral=True):
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        material = material.lower().strip()
        async with self.lock(gid):
            user = self.user(gid, uid)
            state = self.settle_user(user)
            colony = state["colonies"].get(state["location"])
            if (
                not self.ready(user)
                or state["refit_until"]
                or state["mode"] != "expedition"
                or state["travel_until"]
                or (not colony)
                or (not colony.get("base"))
            ):
                error = "Use this at a completed local base in expedition mode."
            else:
                error = None
            available = max(0, int(state["cargo"]["materials"].get(material, 0)))
            try:
                qty = parse_amount(amount, available=available)
            except AmountParseError as exc:
                error = str(exc)
                qty = 0
            if not error and (qty <= 0 or qty > available):
                error = "That material is not aboard your ISD."
            if error:
                await ui.respond(interaction, error, ephemeral=True)
                return
            state["cargo"]["materials"][material] -= qty
            if state["cargo"]["materials"][material] == 0:
                state["cargo"]["materials"].pop(material)
            colony["materials"][material] = int(colony["materials"].get(material, 0)) + qty
            await self.econ.save(gid)
        await ui.respond(interaction, f"Offloaded {qty} {material} into this colony.", ephemeral=True)

    @space_group.command(name="terraform", description="Advance your settlement's five viability phases.")
    async def terraform(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        if not await self.guard(interaction):
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        async with self.lock(gid):
            user = self.user(gid, uid)
            state = self.settle_user(user)
            body = space.body(self.doc(gid), state["location"])
            colony = space.colony(state, body.key) if body else None
            level = int(colony["viability"]) // 20 if colony else 0
            cost = (100 + level * 50) * space.QA
            material = body.resource if body else "unknown"
            if (
                not self.ready(user)
                or state["refit_until"]
                or state["mode"] != "expedition"
                or state["travel_until"]
                or (not colony)
                or (not colony["terraform"])
            ):
                error = "Build a Terraforming Unit at your current world first."
            elif int(colony["viability"]) >= 100:
                error = "This settlement is already fully viable."
            elif space.material_total(colony) < 10:
                error = "Need 10 locally stored space materials."
            elif net(user) < cost:
                error = f"Phase {level + 1} costs {fmt(cost)}."
            else:
                error = None
            if error:
                await ui.respond(interaction, error, ephemeral=True)
                return
            take(user, cost)
            space.consume_materials(colony, 10)
            colony["viability"] = min(100, int(colony["viability"]) + 20)
            await self.econ.save(gid)
        await self.bot.ledger.record(gid, uid, -cost, "space-terraform", after=net(user), detail=body.key)
        await self.send(
            interaction,
            ui.ok_embed(
                f"**{body.name}** settlement viability: **{colony['viability']}%**. "
                + (
                    "You may now claim it."
                    if colony["viability"] == 100
                    else "Mine more material for the next phase."
                )
            ),
            "terraforming.png",
        )

    @space_group.command(name="claim", description="Claim a fully viable celestial settlement.")
    async def claim(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        if not await self.guard(interaction):
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        async with self.lock(gid):
            user = self.user(gid, uid)
            state = self.settle_user(user)
            body = space.body(self.doc(gid), state["location"])
            colony = space.colony(state, body.key) if body else None
            claims = space.galaxy(self.doc(gid))["claims"]
            if (
                not self.ready(user)
                or state["refit_until"]
                or state["mode"] != "expedition"
                or state["travel_until"]
                or (not body)
                or (not colony)
                or (int(colony["viability"]) < 100)
            ):
                error = "Complete all five viability phases at your current body first."
            elif claims.get(body.key) and str(claims[body.key]) != str(uid):
                error = "This body already has a sovereign claimant. Your established site remains yours."
            elif claims.get(body.key):
                error = "You already hold this claim."
            else:
                error = None
            if error:
                await ui.respond(interaction, error, ephemeral=True)
                return
            claims[body.key] = uid
            colony["gdp"] = space.gdp(body.score, body.hazard)
            colony["income_at"] = space.now().isoformat()
            await self.econ.save(gid)
        await self.send(
            interaction,
            ui.base_embed(
                title="CELESTIAL CLAIM ESTABLISHED",
                description=f"<@{uid}> claimed **{body.name}**. Colonial GDP index: **{fmt(colony['gdp'])}**. Estimated daily wallet production: **{fmt(space.daily_income(colony['gdp']))}**.",
            ),
            "terraforming.png",
        )

    @space_group.command(name="colony", description="Inspect your settlement, resources and GDP on a body.")
    @app_commands.autocomplete(body=autocomplete_body)
    async def colony_status(self, interaction: discord.Interaction, body: str) -> None:
        nyx.protect_report(interaction, self.econ)
        if not await self.guard(interaction, ephemeral=True):
            return
        target = self.selected(interaction, body)
        if not target:
            await ui.respond(interaction, "Unknown body.", ephemeral=True)
            return
        state = self.settle_user(self.user(interaction.guild_id, interaction.user.id))
        colony = state["colonies"].get(target.key)
        if not isinstance(colony, dict):
            await ui.respond(interaction, "You have no settlement there.", ephemeral=True)
            return
        owner = space.galaxy(self.doc(interaction.guild_id))["claims"].get(target.key)
        structures = ", ".join((k for k in ("base", "mine", "lab", "terraform") if colony.get(k))) or "none"
        materials = ", ".join((f"{v} {k}" for k, v in colony.get("materials", {}).items() if v)) or "none"
        sections = [
            (
                "📋 Settlement overview",
                "\n".join(
                    [
                        status_ui.line("Landing", "✅ Landed" if colony.get("landed") else "⚪ Not landed"),
                        status_ui.line(
                            "Sovereign",
                            "You"
                            if str(owner) == str(interaction.user.id)
                            else "Another player"
                            if owner
                            else "Unclaimed",
                        ),
                        status_ui.line("Viability", f"{colony.get('viability', 0)}%"),
                        status_ui.line("Focus", colony.get("specialisation") or "None"),
                    ]
                ),
            ),
            (
                "🏗️ Infrastructure and materials",
                "\n".join(
                    [
                        status_ui.line("Structures", structures),
                        status_ui.line("Stored materials", materials),
                        status_ui.line("Automatic materials", "Every 6h · seven-day catch-up cap"),
                    ]
                ),
            ),
        ]
        economy = [
            status_ui.line("Development", f"{colony.get('development', 0)}/10"),
            status_ui.line("GDP index", fmt(colony.get("gdp", 0))),
            status_ui.line("Wallet production", f"{fmt(space.colony_daily_income(colony))}/day"),
        ]
        level = max(0, int(colony.get("development", 0)))
        if level < 10:
            cost = space.gdp(target.score, target.hazard) * (level + 1) // 4
            economy.append(
                f"**Next development:** level {level + 1} · {fmt(cost)} donuts (requires your claim)"
            )
        sections.append(("💰 GDP and development", "\n".join(economy)))
        disabled = []
        for structure in ("mine", "lab", "ammo_depot"):
            due = space.at(colony.get(f"{structure}_disabled_until"))
            if due and due > space.now():
                disabled.append(
                    status_ui.line(
                        structure.replace("_", " ").title(), f"🔴 Disabled until {status_ui.deadline(due)}"
                    )
                )
        if disabled:
            sections.append(("⚠️ Disabled infrastructure", "\n".join(disabled)))
        if colony.get("ammo_depot") or colony.get("ammo_depot_until"):
            depot = [
                status_ui.line("State", "🟢 Ready" if colony.get("ammo_depot") else "🏗️ Building"),
                status_ui.line(
                    "Stock",
                    ", ".join(
                        (
                            f"{fleet.CRAFT[k].name} ×{v}"
                            for k, v in colony.get("ammo_stock", {}).items()
                            if k in fleet.CRAFT
                        )
                    )
                    or "Empty",
                ),
            ]
            if colony.get("ammo_depot_until"):
                depot.append(status_ui.line("Completes", status_ui.deadline(colony["ammo_depot_until"])))
            sections.append(("🚀 Local ammunition depot", "\n".join(depot)))
        sections.append(
            (
                "➡️ Next action",
                "Use `/space build-site` for infrastructure, `/space terraform` for viability, `/space claim` for sovereignty and `/space develop` for GDP. Instructions: `/space guide`.",
            )
        )
        pages = status_ui.report(
            f"🪐 COLONY — {target.name}", [("Overview", sections)], checked_at=space.now()
        )
        await ui.respond(
            interaction, embed=pages[0], view=status_ui.pager(pages, interaction.user.id), ephemeral=True
        )

    @space_group.command(
        name="develop", description="Grow a claimed world's GDP and automatic wallet production."
    )
    @app_commands.autocomplete(body=autocomplete_body)
    async def develop(self, interaction: discord.Interaction, body: str) -> None:
        nyx.protect_report(interaction, self.econ)
        if not await self.guard(interaction, ephemeral=True):
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        target = self.selected(interaction, body)
        if target and ds.destroyed(self.doc(interaction.guild_id), target.key):
            await ui.respond(
                interaction, "A debris field cannot be developed. Reconstruct it first.", ephemeral=True
            )
            return
        if not target:
            await ui.respond(interaction, "Unknown body.", ephemeral=True)
            return
        async with self.lock(gid):
            user = self.user(gid, uid)
            state = self.settle_user(user)
            colony = state["colonies"].get(target.key)
            claims = space.galaxy(self.doc(gid))["claims"]
            if str(claims.get(target.key)) != str(uid) or not isinstance(colony, dict):
                error = "You must hold this fully established claim."
            else:
                level = max(0, int(colony.get("development", 0)))
                base = space.gdp(target.score, target.hazard)
                cost = base * (level + 1) // 4
                if level >= 10:
                    error = "This colony is fully developed."
                elif net(user) < cost:
                    error = (
                        f"Development level {level + 1}\n"
                        + ui.format_purchase_shortfall(cost, net(user))
                        + "\nDeep Vault is not spent automatically."
                    )
                else:
                    error = None
            if error:
                await ui.respond(interaction, error, ephemeral=True)
                return
            current = space.now()
            income, _ = space.settle_colony_income(user, colony, current)
            take(user, cost)
            colony["development"] = level + 1
            colony["gdp"] = base * (100 + 10 * colony["development"]) // 100
            await self.econ.save(gid)
        if income:
            await self.bot.ledger.record(
                gid, uid, income, "space-production", after=net(user) + cost, detail=target.key
            )
        await self.bot.ledger.record(gid, uid, -cost, "space-development", after=net(user), detail=target.key)
        await ui.respond(
            interaction,
            embed=ui.ok_embed(
                f"**{target.name}** development **{colony['development']}/10**. GDP index **{fmt(colony['gdp'])}**; about **{fmt(space.colony_daily_income(colony))}/day** to wallet."
            ),
        )

    @space_group.command(
        name="unload", description="Transfer an arrived ISD cargo category back into usable holdings."
    )
    @app_commands.choices(
        kind=[
            app_commands.Choice(name="Donuts", value="donuts"),
            app_commands.Choice(name="Items", value="inventory"),
            app_commands.Choice(name="Plushies", value="plushies"),
            app_commands.Choice(name="Fish", value="fish"),
            app_commands.Choice(name="Rods", value="rods"),
            app_commands.Choice(name="Vehicles", value="vehicles"),
        ]
    )
    async def unload(self, interaction: discord.Interaction, kind: app_commands.Choice[str]) -> None:
        nyx.protect_report(interaction, self.econ)
        if not await self.guard(interaction, ephemeral=True):
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        async with self.lock(gid):
            user = self.user(gid, uid)
            state = self.settle_user(user)
            if state["load_until"] or state["travel_until"]:
                await ui.respond(interaction, "Finish loading and travel before unloading.", ephemeral=True)
                return
            if state["location"] != "earth" and (not space.colony(state, state["location"])["base"]):
                await ui.respond(
                    interaction, "Build a local base before unloading at this destination.", ephemeral=True
                )
                return
            field = kind.value
            cargo = state["cargo"]
            if not cargo[field]:
                await ui.respond(interaction, "No cargo in that category.", ephemeral=True)
                return
            if field == "donuts":
                user["donuts"] = int(user.get("donuts", 0)) + int(cargo[field])
                cargo[field] = 0
            elif field == "vehicles":
                for model, snapshot in cargo[field].items():
                    if asset_service.vehicle_unavailable(user, model):
                        await ui.respond(
                            interaction,
                            "An identical vehicle is already active; resolve it before unloading.",
                            ephemeral=True,
                        )
                        return
                for model, snapshot in list(cargo[field].items()):
                    user.setdefault("vehicles", {})[model] = snapshot
                    cargo[field].pop(model)
            elif field == "rods":
                if any(
                    (
                        int(user.get("rods", {}).get(rod_id, 0)) > 0
                        or asset_service.reserved(user, "rod", rod_id)
                        for rod_id in cargo[field]
                    )
                ):
                    await ui.respond(
                        interaction,
                        content="An identical rod is already active. Resolve it before unloading; existing enchants will not be overwritten.",
                        ephemeral=True,
                    )
                    return
                for rod_id, snapshot in list(cargo[field].items()):
                    user.setdefault("rods", {})[rod_id] = 1
                    user.setdefault("rod_enchants", {})[rod_id] = snapshot.get("enchants", [])
                    if snapshot.get("equipped") and (not user.get("equipped_rod")):
                        user["equipped_rod"] = rod_id
                    cargo[field].pop(rod_id)
            else:
                if field == "inventory":
                    cfg = self.bot.config.for_guild(gid)
                    for item, qty in cargo[field].items():
                        room = asset_service.inventory_space(cfg, user, item)
                        if room is not None and int(qty) > room:
                            await ui.respond(
                                interaction,
                                content="Unloading would exceed an item's normal holding cap. Use some held items first; your cargo is unchanged.",
                                ephemeral=True,
                            )
                            return
                if field == "plushies" and any(
                    (
                        int(user.get(field, {}).get(item, 0)) or asset_service.reserved(user, "plushie", item)
                        for item in cargo[field]
                    )
                ):
                    await ui.respond(
                        interaction,
                        content="An identical plushie is owned or reserved by recovery. Resolve it before unloading; your cargo is unchanged.",
                        ephemeral=True,
                    )
                    return
                for item, qty in list(cargo[field].items()):
                    user.setdefault(field, {})[item] = int(user.get(field, {}).get(item, 0)) + int(qty)
                    cargo[field].pop(item)
            await self.econ.save(gid)
        await ui.respond(
            interaction, content=f"{kind.name} unloaded into your usable holdings.", ephemeral=True
        )

    @space_group.command(name="bombard", description="In attack mode, strike the world your ISD is orbiting.")
    async def bombard(self, interaction: discord.Interaction) -> None:
        if not await self.guard(interaction, ephemeral=True):
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        user = self.user(gid, uid)
        state = self.settle_user(user)
        ship = isd.normalize(user)
        target = space.body(self.doc(gid), state["location"])
        if not self.ready(user) or state["mode"] != "attack" or state["refit_until"] or state["travel_until"]:
            await ui.respond(
                interaction, "Your ISD must be at a world in operational attack mode.", ephemeral=True
            )
            return
        if not target or not space.colonisable(target) or target.key == "earth":
            await ui.respond(
                interaction,
                "Select a colonisable world or giant-planet orbit other than Earth.",
                ephemeral=True,
            )
            return
        if not ship.get("armed") or ship.get("active_window_id"):
            await ui.respond(
                interaction,
                "Prepare a Cinder charge with `/isd arm` and finish other windows first.",
                ephemeral=True,
            )
            return
        prior = space.at(state.get("last_bombard_at"))
        if prior and prior + dt.timedelta(hours=24) > space.now():
            await ui.respond(
                interaction,
                f"Planetary weapons ready <t:{int((prior + dt.timedelta(hours=24)).timestamp())}:R>.",
                ephemeral=True,
            )
            return
        confirmation = ui.ConfirmView(uid, timeout=45, danger_label="Bombard this world")
        await ui.respond(
            interaction,
            embed=ui.warn_embed(
                f"A hit will destroy all colonies and the claim on **{target.name}**, including your own site. The charge is consumed whether defenders stop it or not. A one-hour public B-wing window opens first. Confirm?",
                title="PLANETARY STRIKE AUTHORIZATION",
            ),
            view=confirmation,
            ephemeral=True,
        )
        await confirmation.wait()
        if confirmation.value is not True:
            return
        async with self.lock(gid):
            user = self.user(gid, uid)
            state = self.settle_user(user)
            ship = isd.normalize(user)
            if (
                state["mode"] != "attack"
                or state["location"] != target.key
                or state["refit_until"]
                or (not ship.get("armed"))
                or ship.get("active_window_id")
                or isd.active_cinder(self.doc(gid))
            ):
                if confirmation.interaction:
                    await confirmation.interaction.followup.send(
                        "The ISD state changed; no strike was committed.", ephemeral=True
                    )
                return
            window_id = f"PLANET-{secrets.token_hex(3).upper()}"
            record = {
                "kind": "planetary",
                "guild_id": gid,
                "builder": uid,
                "body": target.key,
                "channel_id": interaction.channel_id,
                "remaining_seconds": 3600.0,
                "interceptors": [],
                "created_at": space.now().isoformat(),
                "window_id": window_id,
                "announced": False,
            }
            isd.windows(self.doc(gid))[window_id] = record
            ship["armed"] = False
            ship["active_window_id"] = window_id
            state["last_bombard_at"] = space.now().isoformat()
            await self.econ.save(gid)
        cog = self.bot.get_cog("Imperial Star Destroyer")
        if cog:
            await cog._announce_window(gid, window_id)
        if confirmation.interaction:
            await confirmation.interaction.followup.send(
                f"Planetary attack `{window_id}` committed.", ephemeral=True
            )

    @tasks.loop(seconds=60)
    async def income_loop(self) -> None:
        seen = set()
        for guild in self.bot.guilds:
            gid = guild.id
            canonical = self.econ.store.canonical_id(gid)
            if canonical in seen:
                continue
            seen.add(canonical)
            if not self.bot.config.for_guild(gid).get("economy.enabled", True):
                continue
            payouts: Dict[int, int] = {}
            contract_payouts: Dict[int, int] = {}
            async with self.lock(gid):
                doc = self.doc(gid)
                claims = space.galaxy(doc)["claims"]
                current = space.now()
                before = {
                    uid: int(user.get("donuts", 0))
                    for uid, user in doc.get("users", {}).items()
                    if isinstance(user, dict)
                }
                before_contracts = {
                    uid: int(activities.root(user)["space"].get("earned", 0))
                    for uid, user in doc.get("users", {}).items()
                    if isinstance(user, dict)
                }
                events = []
                changed = fleet.settle_world(doc, current, events=events)
                for raw_uid, user in doc.get("users", {}).items():
                    if isinstance(user, dict) and int(user.get("donuts", 0)) > before.get(raw_uid, 0):
                        bonus = int(activities.root(user)["space"].get("earned", 0)) - before_contracts.get(
                            raw_uid, 0
                        )
                        contract_payouts[int(raw_uid)] = bonus
                        payouts[int(raw_uid)] = int(user["donuts"]) - before.get(raw_uid, 0) - bonus
                for raw_uid, user in self.econ.all_users(gid).items():
                    if not isinstance(user, dict):
                        continue
                    changed = self.settle_progress(gid, int(raw_uid), user) or changed
                    state = self.settle_user(user)
                    for key, colony in state["colonies"].items():
                        if not isinstance(colony, dict):
                            continue
                        if ds.destroyed(doc, key):
                            continue
                        if str(claims.get(key)) != str(raw_uid) or not int(colony.get("gdp", 0)):
                            continue
                        payout, settled = space.settle_colony_income(user, colony, current)
                        if settled:
                            payouts[int(raw_uid)] = payouts.get(int(raw_uid), 0) + payout
                            changed = True
                if changed:
                    await self.econ.save(gid)
            await self.record_fleet_events(gid, events)
            for uid, payout in contract_payouts.items():
                if payout:
                    await self.bot.ledger.record(
                        gid, uid, payout, "space-contract", after=net(self.user(gid, uid))
                    )
            for uid, payout in payouts.items():
                if payout:
                    await self.bot.ledger.record(
                        gid, uid, payout, "space-production", after=net(self.user(gid, uid))
                    )

    @income_loop.before_loop
    async def before_income(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SpaceCog(bot))
