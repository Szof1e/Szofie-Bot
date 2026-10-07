"""Temporary coalitions with shared intelligence and logistics."""

from __future__ import annotations
import copy
import datetime as dt
import re
from typing import Any, Dict, List, Optional, Tuple
import discord
from discord import app_commands
from discord.ext import commands
from szofie.catalog import ARMORED_MODELS, CONSUMABLES, ITEM_HOLD_CAPS, LOADABLE_VEHICLES, VEHICLE_CATALOG
from cogs.fishing import FISH_BY_ID, ROD_BY_ID
from szofie import coalitions, continuity as continuity_state, thor as thor_state, nyx, ui
from szofie.betting import AmountParseError, parse_amount
from szofie.plushies import PLUSHIE_BY_ID, plushie_perk
from szofie import assets as asset_service
from szofie.currency import transfer_wallet

_NAME_RE = re.compile("^[A-Za-z0-9][A-Za-z0-9 _'\\-]{1,31}$")
COALITION_MEMBER_HARD_CAP = 10
_INTEL_FIELD_CHARS = 850
_INTEL_PAGE_CHARS = 5000


def _intel_chunks(value: str, limit: int = _INTEL_FIELD_CHARS) -> List[str]:
    """Split a long intel section without dropping any of its content."""
    remaining = str(value or "none")
    chunks: List[str] = []
    while remaining:
        cut = min(limit, len(remaining))
        if cut < len(remaining):
            newline = remaining.rfind("\n", cut // 2, cut + 1)
            if newline >= 0:
                cut = newline + 1
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    return chunks


def coalition_member_limit(cfg: Any) -> int:
    """Resolve the configured capacity without ever exceeding the hard cap."""
    return max(
        1,
        min(
            COALITION_MEMBER_HARD_CAP,
            int(cfg.get("economy.coalition_max_members", COALITION_MEMBER_HARD_CAP)),
        ),
    )


class CoalitionCog(commands.Cog, name="Coalitions"):
    """Short-lived diplomatic groups backed by the shared economy document."""

    coalition = app_commands.Group(
        name="coalition", description="Form and manage a temporary coalition of up to ten players."
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.econ = bot.economy

    def cfg(self, guild_id: int):
        return self.bot.config.for_guild(guild_id)

    def _economy_cog(self):
        return self.bot.get_cog("Economy")

    def _doc(self, guild_id: int) -> Dict[str, Any]:
        doc = self.econ.store.load(guild_id)
        if coalitions.cleanup(doc):
            self.econ.mark_dirty(guild_id)
        return doc

    def _user(self, guild_id: int, user_id: int) -> Dict[str, Any]:
        starting = int(self.cfg(guild_id).get("economy.starting_balance", 100))
        return self.econ.user(guild_id, user_id, starting)

    async def _save(self, guild_id: int) -> None:
        coalitions.remember_membership(self.econ.store.load(guild_id))
        await self.econ.save(guild_id)

    async def _guard(self, interaction: discord.Interaction, *, defer: bool = True):
        if defer:
            await ui.defer_response(interaction, ephemeral=True)
        if interaction.guild_id is None:
            await ui.respond(
                interaction, embed=ui.error_embed("Coalitions only operate inside a server."), ephemeral=True
            )
            return None
        cfg = self.cfg(interaction.guild_id)
        if not cfg.get("economy.enabled", True):
            await ui.respond(
                interaction, embed=ui.error_embed("The economy is disabled here."), ephemeral=True
            )
            return None
        return cfg

    @staticmethod
    def _member_ids(group: Dict[str, Any]) -> List[int]:
        out: List[int] = []
        for raw in group.get("members", []):
            try:
                out.append(int(raw))
            except (TypeError, ValueError):
                continue
        return out

    @staticmethod
    def _name(group: Dict[str, Any]) -> str:
        return str(group.get("name", "Unnamed Coalition"))

    @staticmethod
    def _find_group_by_name(doc: Dict[str, Any], name: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        needle = str(name).strip().casefold()
        for cid, group in coalitions.state(doc)["groups"].items():
            if str(group.get("name", "")).casefold() == needle or str(cid) == str(name).strip():
                return (str(cid), group)
        return None

    def _member_label(self, guild: Optional[discord.Guild], user_id: int) -> str:
        member = guild.get_member(int(user_id)) if guild else None
        return member.display_name if member else f"User {int(user_id)}"

    @coalition.command(name="create", description="Create a seven-day coalition for 50 million donuts.")
    @app_commands.describe(name="Coalition name (2-32 letters, numbers, spaces, apostrophes, - or _)")
    async def create(self, interaction: discord.Interaction, name: str) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        clean_name = " ".join(name.strip().split())
        if not _NAME_RE.fullmatch(clean_name):
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    "Use a 2–32 character name containing letters, numbers, spaces, apostrophes, `-` or `_`."
                ),
                ephemeral=True,
            )
            return
        doc = self._doc(gid)
        root = coalitions.state(doc)
        if coalitions.coalition_for(doc, uid):
            await ui.respond(
                interaction, embed=ui.error_embed("You already belong to a coalition."), ephemeral=True
            )
            return
        cooldown = coalitions.cooldown_until(doc, uid)
        if cooldown:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"Your diplomatic cooldown ends <t:{int(cooldown.timestamp())}:R>."),
                ephemeral=True,
            )
            return
        if self._find_group_by_name(doc, clean_name):
            await ui.respond(
                interaction, embed=ui.error_embed("That coalition name is already in use."), ephemeral=True
            )
            return
        cost = int(cfg.get("economy.coalition_creation_cost", 50000000))
        founder = self._user(gid, uid)
        econ_cog = self._economy_cog()
        net = int(founder.get("donuts", 0)) + int(founder.get("bank", 0))
        if net < cost:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"Creating a coalition costs 🍩 **{ui.format_donuts(cost)}** donuts."),
                ephemeral=True,
            )
            return
        if econ_cog is not None:
            econ_cog._take(founder, cost)
        else:
            wallet = min(cost, int(founder.get("donuts", 0)))
            founder["donuts"] = int(founder.get("donuts", 0)) - wallet
            founder["bank"] = int(founder.get("bank", 0)) - (cost - wallet)
        cid = str(root["next_id"])
        root["next_id"] += 1
        now = coalitions.utcnow()
        days = max(1.0, float(cfg.get("economy.coalition_duration_days", 7)))
        expiry = now + dt.timedelta(days=days)
        group = {
            "name": clean_name,
            "founder": uid,
            "members": [uid],
            "created_at": now.isoformat(),
            "expires_at": expiry.isoformat(),
            "log": [],
        }
        coalitions.append_log(group, uid, "created", f"Coalition formed for {ui.format_donuts(cost)} donuts")
        root["groups"][cid] = group
        await self._save(gid)
        await self.bot.ledger.record(
            gid,
            uid,
            -cost,
            "coalition-create",
            after=int(founder.get("donuts", 0)) + int(founder.get("bank", 0)),
        )
        await ui.respond(
            interaction,
            embed=ui.base_embed(
                title=f"🤝 {clean_name} formed",
                description=f"{interaction.user.mention} established a temporary coalition for 🍩 **{ui.format_donuts(cost)}** donuts.\n\n**Capacity:** 1/{coalition_member_limit(cfg)}\n**Expires:** <t:{int(expiry.timestamp())}:F> · <t:{int(expiry.timestamp())}:R>\nInvite signatories with `/coalition invite`.",
                color=cfg.color,
            ),
        )

    @coalition.command(name="invite", description="Invite a player to your coalition.")
    @app_commands.describe(member="Player to invite")
    async def invite(self, interaction: discord.Interaction, member: discord.Member) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        doc = self._doc(gid)
        found = coalitions.coalition_for(doc, uid)
        if found is None:
            await ui.respond(
                interaction, embed=ui.error_embed("Create or join a coalition first."), ephemeral=True
            )
            return
        cid, group = found
        if int(group.get("founder", 0)) != uid:
            await ui.respond(
                interaction,
                embed=ui.error_embed("Only the Coalition Commander can issue invitations."),
                ephemeral=True,
            )
            return
        if member.bot or member.id == uid:
            await ui.respond(
                interaction, embed=ui.error_embed("Invite another human player."), ephemeral=True
            )
            return
        limit = coalition_member_limit(cfg)
        if len(self._member_ids(group)) >= limit:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"This coalition already has the maximum of {limit} signatories."),
                ephemeral=True,
            )
            return
        if coalitions.coalition_for(doc, member.id):
            await ui.respond(
                interaction,
                embed=ui.error_embed("That player already belongs to a coalition."),
                ephemeral=True,
            )
            return
        cooldown = coalitions.cooldown_until(doc, member.id)
        if cooldown:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"That player's diplomatic cooldown ends <t:{int(cooldown.timestamp())}:R>."
                ),
                ephemeral=True,
            )
            return
        pending = coalitions.pending_invites(doc, member.id)
        if any((str(item.get("coalition_id")) == cid for item in pending)):
            await ui.respond(
                interaction, embed=ui.warn_embed("That invitation is already pending."), ephemeral=True
            )
            return
        hours = max(1.0, float(cfg.get("economy.coalition_invite_hours", 24)))
        expires = coalitions.utcnow() + dt.timedelta(hours=hours)
        pending.append({"coalition_id": cid, "invited_by": uid, "expires_at": expires.isoformat()})
        coalitions.state(doc)["invites"][str(member.id)] = pending
        coalitions.append_log(group, uid, "invited", f"Invited {member.id}")
        await self._save(gid)
        await ui.respond(
            interaction,
            content=member.mention,
            embed=ui.base_embed(
                title="🤝 Coalition invitation",
                description=f"{member.mention}, you were invited to join **{self._name(group)}** by {interaction.user.mention}.\nAccept with `/coalition join` before <t:{int(expires.timestamp())}:R>.",
                color=cfg.color,
            ),
            allowed_mentions=discord.AllowedMentions(users=[member]),
        )

    async def _invite_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        doc = self._doc(interaction.guild_id)
        needle = (current or "").casefold()
        out: List[app_commands.Choice[str]] = []
        for pending in coalitions.pending_invites(doc, interaction.user.id):
            group = coalitions.state(doc)["groups"].get(str(pending.get("coalition_id")))
            if not group:
                continue
            name = self._name(group)
            if not needle or needle in name.casefold():
                out.append(app_commands.Choice(name=name[:100], value=name))
        return out[:25]

    @coalition.command(name="join", description="Accept a pending coalition invitation.")
    @app_commands.describe(coalition="Coalition to join; leave blank when you have only one invitation")
    @app_commands.autocomplete(coalition=_invite_autocomplete)
    async def join(self, interaction: discord.Interaction, coalition: Optional[str] = None) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        doc = self._doc(gid)
        if coalitions.coalition_for(doc, uid):
            await ui.respond(
                interaction, embed=ui.error_embed("You already belong to a coalition."), ephemeral=True
            )
            return
        cooldown = coalitions.cooldown_until(doc, uid)
        if cooldown:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"Your diplomatic cooldown ends <t:{int(cooldown.timestamp())}:R>."),
                ephemeral=True,
            )
            return
        pending = coalitions.pending_invites(doc, uid)
        if not pending:
            await ui.respond(
                interaction, embed=ui.error_embed("You have no active coalition invitations."), ephemeral=True
            )
            return
        selected = None
        if coalition:
            found = self._find_group_by_name(doc, coalition)
            if found:
                selected = next(
                    (invite for invite in pending if str(invite.get("coalition_id")) == found[0]), None
                )
        elif len(pending) == 1:
            selected = pending[0]
        if selected is None:
            await ui.respond(
                interaction,
                embed=ui.error_embed("Choose which pending invitation to accept."),
                ephemeral=True,
            )
            return
        cid = str(selected.get("coalition_id"))
        group = coalitions.state(doc)["groups"].get(cid)
        if group is None:
            await ui.respond(
                interaction, embed=ui.error_embed("That invitation has expired."), ephemeral=True
            )
            return
        limit = coalition_member_limit(cfg)
        members = self._member_ids(group)
        if len(members) >= limit:
            await ui.respond(
                interaction,
                embed=ui.error_embed("That coalition filled up before you joined."),
                ephemeral=True,
            )
            return
        members.append(uid)
        group["members"] = members
        coalitions.state(doc)["invites"].pop(str(uid), None)
        coalitions.append_log(group, uid, "joined", f"{uid} accepted an invitation")
        await self._save(gid)
        expires = coalitions.parse_time(group.get("expires_at"))
        expiry_text = f"<t:{int(expires.timestamp())}:R>" if expires else "soon"
        await ui.respond(
            interaction,
            embed=ui.base_embed(
                title=f"🤝 {self._name(group)} gains a signatory",
                description=f"{interaction.user.mention} joined the coalition. Mutual intelligence, logistics and non-aggression are now active.\n\n**Capacity:** {len(members)}/{limit} · **Expires:** {expiry_text}",
                color=cfg.color,
            ),
        )

    @coalition.command(name="overview", description="Privately inspect your coalition and its treaty.")
    async def overview(self, interaction: discord.Interaction) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        doc = self._doc(interaction.guild_id)
        found = coalitions.coalition_for(doc, interaction.user.id)
        if found is None:
            await ui.respond(
                interaction, embed=ui.error_embed("You do not belong to a coalition."), ephemeral=True
            )
            return
        _, group = found
        founder = int(group.get("founder", 0))
        members = self._member_ids(group)
        lines = [
            f"{('⭐' if member == founder else '•')} <@{member}> — {('Coalition Commander' if member == founder else 'Signatory')}"
            for member in members
        ]
        expires = coalitions.parse_time(group.get("expires_at"))
        embed = ui.base_embed(
            title=f"🤝 {self._name(group)}",
            description="\n".join(lines)
            + (
                f"\n\n**Treaty expires:** <t:{int(expires.timestamp())}:F> · <t:{int(expires.timestamp())}:R>"
                if expires
                else ""
            ),
            color=cfg.color,
        )
        embed.add_field(
            name="Collective Security Pact",
            value="Members cannot steal, rob, cast hexes, use Cop Calls, launch ICBMs, reconnoitre, deploy strategic vehicles, fire Project THOR, or intercept one another's THOR launches.",
            inline=False,
        )
        embed.set_footer(
            text="Private intelligence: /coalition intel · Mutual logistics: /coalition transfer"
        )
        await ui.respond(
            interaction, embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    def _asset_lines(
        self, cfg: Any, u: Dict[str, Any], subject_id: Optional[int] = None
    ) -> Tuple[str, str, str, str, str]:
        econ_cog = self._economy_cog()
        if econ_cog is not None:
            ready = econ_cog._icbm_settle(u)
            econ_cog._aa_settle(cfg, u)
            econ_cog._s400_settle(u)
            states = {model: econ_cog._vehicle_settle(u, model) for model in VEHICLE_CATALOG}
        else:
            ready = int(u.get("icbm_ready", 0))
            states = {model: (u.get("vehicles", {}) or {}).get(model, {}) for model in VEHICLE_CATALOG}
        thor_state.settle_aegis_bmd(states["aegis"], cfg)
        continuity_state.settle(u, cfg)
        if subject_id is not None:
            u = u
        money = f"🍩 Wallet **{ui.format_donuts(int(u.get('donuts', 0)))}** · Bank **{ui.format_donuts(int(u.get('bank', 0)))}**\n🏦 Bedrock Vault **{ui.format_donuts(int(u.get('deep_vault_balance', 0)))}** ({('operational' if u.get('deep_vault_owned') else 'not built')})"
        inv = u.get("inventory", {}) or {}
        item_lines = [
            f"{CONSUMABLES[key]['emoji']} {CONSUMABLES[key]['name']} ×{int(inv.get(key, 0))}"
            for key in CONSUMABLES
            if int(inv.get(key, 0)) > 0
        ]
        owned_plushies = [
            f"{PLUSHIE_BY_ID[key].emoji} {PLUSHIE_BY_ID[key].name}"
            for key, count in (u.get("plushies", {}) or {}).items()
            if int(count or 0) > 0 and key in PLUSHIE_BY_ID
        ]
        resources = "\n".join(item_lines) if item_lines else "No consumables"
        resources += "\n**Permanent perks:** " + (", ".join(owned_plushies) if owned_plushies else "none")
        arsenal = [f"🚀 ICBM ×{ready}"]
        for model, spec in VEHICLE_CATALOG.items():
            state = states[model]
            building = coalitions.parse_time(state.get("building_until"))
            if building:
                status = f"building until <t:{int(building.timestamp())}:R>"
            elif state.get("owned"):
                status = "owned"
                if state.get("jet_damaged"):
                    status += " · damaged / depot recovery required"
                patrol_until = coalitions.parse_time(state.get("patrol_until"))
                if patrol_until and patrol_until > coalitions.utcnow():
                    status += f" · combat air patrol until <t:{int(patrol_until.timestamp())}:R>"
                if model == "b2":
                    status += " · B61-12 armed" if state.get("armed") else " · payload empty"
                elif model in LOADABLE_VEHICLES:
                    status += f" · ammo ×{int(state.get('ammo', 0))}"
                    if model == "apache":
                        upgrading = coalitions.parse_time(state.get("comanche_upgrade_until"))
                        if upgrading:
                            status += f" · RAH-66 conversion <t:{int(upgrading.timestamp())}:R>"
                        elif state.get("comanche_upgraded"):
                            if state.get("comanche_damaged"):
                                status += " · RAH-66 grounded for repairs"
                            else:
                                status += " · RAH-66 package"
                last = coalitions.parse_time(state.get("last_deploy_at"))
                if last and econ_cog is not None:
                    seconds = econ_cog._vehicle_cooldown_seconds(cfg, model)
                    if coalitions.utcnow() < last + dt.timedelta(seconds=seconds):
                        status += f" · ready <t:{int(last.timestamp() + seconds)}:R>"
            else:
                continue
            arsenal.append(f"{spec['emoji']} {spec['short']} — {status}")
        arsenal.extend(
            [
                f"🛡️ Regular AA: {int(u.get('aa_rockets_loaded', 0))} loaded / {int(u.get('aa_rockets_stock', 0))} reserve",
                f"📡 S-400: {('owned' if u.get('s400_owned') else 'not built')} · 40N6E ×{int(u.get('s400_interceptors', 0))}",
            ]
        )
        thor = thor_state.normalize(u)
        thor_state.settle(u, cfg)
        orbiting = sum(
            (
                1
                for component in thor["components"].values()
                if component.get("status") in {"orbit", "assembled"}
            )
        )
        if (
            thor.get("operational")
            or orbiting
            or any((component.get("status") != "none" for component in thor["components"].values()))
        ):
            thor_status = "operational" if thor.get("operational") else f"{orbiting}/3 modules in orbit"
            thor_status += (
                f" · rods {int(thor.get('rods', 0))}/{int(cfg.get('economy.thor_rod_capacity', 6))}"
            )
            thor_status += " · chambered" if thor.get("chambered") else " · cradle empty"
            arsenal.append(f"⚡ Project THOR — {thor_status}")
        gbi_status = (
            f"{int(thor.get('gbi_stock', 0))}/{int(cfg.get('economy.thor_gbi_stock_capacity', 2))} ready"
        )
        gbi_building = thor_state.parse_time(thor.get("gbi_building_until"))
        if gbi_building:
            gbi_status += (
                f" · {int(thor.get('gbi_building_qty', 0))} building <t:{int(gbi_building.timestamp())}:R>"
            )
        arsenal.append(f"🎯 GBI/EKV ascent defence — {gbi_status}")
        aegis = states.get("aegis", {})
        if aegis.get("owned") and (aegis.get("bmd_owned") or aegis.get("bmd_building_until")):
            bmd_status = (
                f"SM-3 Block IIA ×{int(aegis.get('sm3_ammo', 0))}"
                if aegis.get("bmd_owned")
                else "refit in progress"
            )
            arsenal.append(f"🛰️ Aegis BMD — {bmd_status}")
        from szofie import space_fleet

        arsenal.extend(space_fleet.summary(u))
        if (u.get("nyx") or {}).get("status", "none") != "none":
            arsenal.append("🛰️ NYX counter-intelligence\n" + nyx.summary(u, cfg))
        if isinstance(u.get("death_star"), dict):
            from szofie import deathstar

            station = deathstar.normalize(u)
            arsenal.append(
                f"DS-1 Death Star — {('operational' if station['operational'] else 'project')} · {('disabled' if station['damaged'] else 'undamaged')} · charge {('ready' if station['charged'] else 'empty')} · Alliance squadrons {station['squadron_stock']}/3 · GDP index {ui.format_donuts(deathstar.gdp(station) if subject_id is not None else deathstar.gdp(station))}"
            )
        rods = [
            ROD_BY_ID[key].name
            for key, count in (u.get("rods", {}) or {}).items()
            if int(count or 0) > 0 and key in ROD_BY_ID
        ]
        fish_total = sum((max(0, int(value or 0)) for value in (u.get("fish", {}) or {}).values()))
        equipped = ROD_BY_ID.get(str(u.get("equipped_rod") or ""))
        fishing = f"🐟 Stored catches **{fish_total:,}**\n🎣 Equipped: **{(equipped.name if equipped else 'none')}**\nOwned rods: {(', '.join(rods) if rods else 'none')}"
        continuity_state.settle(u, cfg)
        bunker = continuity_state.normalize(u)
        continuity = (
            continuity_state.exact_summary(
                bunker,
                names={
                    "items": {key: str(spec["name"]) for key, spec in CONSUMABLES.items()},
                    "plushies": {p.id: p.name for p in PLUSHIE_BY_ID.values()},
                    "rods": {r.id: r.name for r in ROD_BY_ID.values()},
                    "vehicles": {key: str(spec["short"]) for key, spec in VEHICLE_CATALOG.items()},
                },
            )
            if bunker.get("owned")
            else "No operational Raven Rock facility"
        )
        return (money, resources, "\n".join(arsenal), fishing, continuity)

    @staticmethod
    def _intel_member_pages(
        title: str, money: str, sections: List[Tuple[str, str]], color: int
    ) -> List[discord.Embed]:
        pages: List[discord.Embed] = []
        page = ui.base_embed(title=title, description=money, color=color)
        for heading, content in sections:
            for index, chunk in enumerate(_intel_chunks(content), 1):
                field_name = heading if index == 1 else f"{heading} (continued {index})"
                if page.fields and (
                    len(page) + len(field_name) + len(chunk) > _INTEL_PAGE_CHARS or len(page.fields) >= 20
                ):
                    pages.append(page)
                    page = ui.base_embed(title=f"{title} — continued", color=color)
                page.add_field(name=field_name, value=chunk, inline=False)
        pages.append(page)
        return pages

    @coalition.command(
        name="intel", description="Privately inspect every signatory's hidden resources and arsenal."
    )
    async def intel(self, interaction: discord.Interaction) -> None:
        cfg = await self._guard(interaction, defer=False)
        if cfg is None:
            return
        gid = interaction.guild_id
        doc = self._doc(gid)
        found = coalitions.coalition_for(doc, interaction.user.id)
        if found is None:
            await ui.respond(
                interaction,
                embed=ui.error_embed("Only coalition signatories receive joint intelligence."),
                ephemeral=True,
            )
            return
        _, group = found
        await interaction.response.defer(ephemeral=True, thinking=True)
        pages: List[discord.Embed] = []
        page_subjects = []
        for uid in self._member_ids(group):
            u = self._user(gid, uid)
            if nyx.hidden(u, interaction.user.id, uid):
                pages.append(nyx.censored_embed())
                page_subjects.append(uid)
                continue
            money, resources, arsenal, fishing, continuity = self._asset_lines(cfg, u, uid)
            member_pages = self._intel_member_pages(
                f"🛰️ {self._member_label(interaction.guild, uid)} — Joint Intelligence",
                money,
                [
                    ("📦 Resources", resources),
                    ("⚔️ Full strategic posture", arsenal),
                    ("🎣 Fisheries", fishing),
                    ("🏔️ Raven Rock continuity loadout", continuity),
                ],
                cfg.color,
            )
            pages.extend(member_pages)
            page_subjects.extend([uid] * len(member_pages))
        await self._save(gid)
        if not pages:
            await interaction.edit_original_response(
                embed=ui.warn_embed("This coalition has no signatories to inspect.")
            )
            return

        def page_guard(index):
            subject = page_subjects[index]
            user = self._doc(gid).get("users", {}).get(str(subject), {})
            return nyx.censored_embed() if nyx.hidden(user, interaction.user.id, subject) else None

        pages = [page_guard(index) or page for index, page in enumerate(pages)]
        view = ui.Paginator(pages, interaction.user.id, page_guard=page_guard) if len(pages) > 1 else None
        await interaction.edit_original_response(
            embed=pages[0], view=view, allowed_mentions=discord.AllowedMentions.none()
        )

    async def _asset_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        doc = self._doc(interaction.guild_id)
        if coalitions.coalition_for(doc, interaction.user.id) is None:
            return []
        u = self._user(interaction.guild_id, interaction.user.id)
        cfg = self.cfg(interaction.guild_id)
        econ_cog = self._economy_cog()
        choices: List[Tuple[str, str]] = [("🍩 Donuts", "donuts")]
        for key, spec in CONSUMABLES.items():
            count = int((u.get("inventory", {}) or {}).get(key, 0))
            if count > 0:
                choices.append((f"{spec['emoji']} {spec['name']} ×{count}", f"item:{key}"))
        for key, count in (u.get("fish", {}) or {}).items():
            if int(count or 0) > 0 and key in FISH_BY_ID:
                fish = FISH_BY_ID[key]
                choices.append((f"{fish.emoji} {fish.name} ×{int(count)}", f"fish:{key}"))
        if econ_cog is not None:
            ready = econ_cog._icbm_settle(u)
            if econ_cog._aa_settle(cfg, u):
                self.econ.mark_dirty(interaction.guild_id)
            econ_cog._s400_settle(u)
        else:
            ready = int(u.get("icbm_ready", 0))
        if ready > 0:
            choices.append((f"🚀 Completed ICBM ×{ready}", "ammo:icbm"))
        if int(u.get("aa_rockets_stock", 0)) > 0:
            choices.append((f"🛡️ Regular AA reserve ×{int(u['aa_rockets_stock'])}", "ammo:aa-stock"))
        if int(u.get("aa_rockets_loaded", 0)) > 0:
            choices.append((f"🛡️ Regular AA loaded ×{int(u['aa_rockets_loaded'])}", "ammo:aa-loaded"))
        if int(u.get("s400_interceptors", 0)) > 0:
            choices.append((f"📡 40N6E interceptor ×{int(u['s400_interceptors'])}", "ammo:s400"))
        thor = thor_state.normalize(u)
        thor_state.settle(u, cfg)
        if int(thor.get("gbi_stock", 0)) > 0:
            choices.append((f"🎯 GBI/EKV ×{int(thor['gbi_stock'])}", "ammo:gbi"))
        for component_id, component in thor["components"].items():
            if component.get("status") == "ready":
                name = thor_state.COMPONENTS[component_id]["short"]
                choices.append((f"📦 THOR module — {name}", f"thor-component:{component_id}"))
        reserved = 1 if thor.get("chambered") or thor_state.parse_time(thor.get("chambering_until")) else 0
        transferable_rods = max(0, int(thor.get("rods", 0)) - reserved)
        if thor.get("operational") and transferable_rods:
            choices.append((f"🔩 THOR kinetic penetrator ×{transferable_rods}", "ammo:thor"))
        vehicles = u.get("vehicles", {}) or {}
        for model, spec in VEHICLE_CATALOG.items():
            state = econ_cog._vehicle_settle(u, model) if econ_cog is not None else vehicles.get(model, {})
            if state.get("owned"):
                choices.append((f"{spec['emoji']} Vehicle — {spec['short']}", f"vehicle:{model}"))
                if model == "b2" and state.get("armed"):
                    choices.append(("☢️ B61-12 B-2 payload", "ammo:b61"))
                if model in LOADABLE_VEHICLES and int(state.get("ammo", 0)) > 0:
                    choices.append((f"🎯 {spec['ammo']} ×{int(state['ammo'])}", f"ammo:{model}"))
                if model == "aegis" and state.get("bmd_owned") and (int(state.get("sm3_ammo", 0)) > 0):
                    choices.append((f"🛰️ SM-3 Block IIA ×{int(state['sm3_ammo'])}", "ammo:sm3"))
        if u.get("s400_owned"):
            choices.append(("📡 Vehicle — S-400 Triumf battery", "vehicle:s400"))
        needle = (current or "").casefold()
        return [
            app_commands.Choice(name=label[:100], value=value)
            for label, value in choices
            if not needle or needle in label.casefold() or needle in value.casefold()
        ][:25]

    def _vehicle_transfer_error(
        self, cfg: Any, donor: Dict[str, Any], receiver: Dict[str, Any], model: str, donor_id: int
    ) -> Optional[str]:
        econ_cog = self._economy_cog()
        if econ_cog is None:
            return "The strategic logistics service is temporarily unavailable."
        source = econ_cog._vehicle_settle(donor, model)
        dest = econ_cog._vehicle_settle(receiver, model)
        if not source.get("owned"):
            return f"You do not have an operational {VEHICLE_CATALOG[model]['short']} to transfer."
        if source.get("jet_damaged") or source.get("jet_repair_until"):
            return "Finish the jet's depot recovery before transferring it."
        patrol_until = coalitions.parse_time(source.get("patrol_until"))
        if patrol_until and patrol_until > coalitions.utcnow():
            return "Finish the combat air patrol before transferring this jet."
        if (
            dest.get("owned")
            or coalitions.parse_time(dest.get("building_until"))
            or asset_service.reserved(receiver, "vehicle", model)
        ):
            return f"The recipient already owns or is building a {VEHICLE_CATALOG[model]['short']}."
        if model in ARMORED_MODELS:
            if source.get(f"{model}_damaged") or coalitions.parse_time(
                source.get(f"{model}_repairing_until")
            ):
                return "Repair the battle-damaged tank before transferring it."
            if source.get("garrison_country") or coalitions.parse_time(source.get("garrison_transfer_until")):
                return "Withdraw the tank from country duty before transferring it."
        if coalitions.parse_time(source.get("building_until")):
            return "Vehicles under construction cannot be transferred."
        if coalitions.parse_time(source.get("arming_until")) or coalitions.parse_time(
            source.get("loading_until")
        ):
            return "Finish all payload or ammunition work before transferring that vehicle."
        if coalitions.parse_time(source.get("comanche_upgrade_until")):
            return "Finish the RAH-66 Comanche conversion before transferring that aircraft."
        if model == "apache" and source.get("comanche_damaged"):
            return "Repair the battle-damaged RAH-66 Comanche before transferring that aircraft."
        if coalitions.parse_time(source.get("bmd_building_until")) or coalitions.parse_time(
            source.get("sm3_loading_until")
        ):
            return "Finish the Aegis BMD refit and SM-3 loading before transferring that vehicle."
        if model in LOADABLE_VEHICLES:
            spec = VEHICLE_CATALOG[model]
            cap = int(cfg.get(f"economy.{spec['cap']}", int(spec["fallback_cap"])))
            if int(source.get("ammo", 0)) > cap:
                return (
                    f"The recipient can carry at most {cap} {spec['ammo']} round(s). Unload ammunition first."
                )
        last = coalitions.parse_time(source.get("last_deploy_at"))
        if last:
            ready = last + dt.timedelta(seconds=econ_cog._vehicle_cooldown_seconds(cfg, model))
            if coalitions.utcnow() < ready:
                return f"That vehicle remains in mission turnaround until <t:{int(ready.timestamp())}:R>."
        return None

    @coalition.command(name="transfer", description="Privately transfer supplies to another signatory.")
    @app_commands.describe(
        member="Coalition member receiving the asset",
        asset="Asset to transfer",
        amount="Donuts: 25k/2.5m/1b/half/all; other assets: whole-number quantity",
    )
    @app_commands.autocomplete(asset=_asset_autocomplete)
    async def transfer(
        self, interaction: discord.Interaction, member: discord.Member, asset: str, amount: str = "1"
    ) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        doc = self._doc(gid)
        found = coalitions.coalition_for(doc, uid)
        if found is None or member.id not in self._member_ids(found[1]) or member.id == uid:
            await ui.respond(
                interaction,
                embed=ui.error_embed("Choose another signatory in your coalition."),
                ephemeral=True,
            )
            return
        if member.bot:
            await ui.respond(
                interaction, embed=ui.error_embed("Bots cannot receive coalition supplies."), ephemeral=True
            )
            return
        group = found[1]
        donor, receiver = (self._user(gid, uid), self._user(gid, member.id))
        econ_cog = self._economy_cog()
        key = asset.strip().lower()
        if key == "donuts":
            try:
                qty = parse_amount(amount, available=int(donor.get("donuts", 0)))
            except AmountParseError as exc:
                await ui.respond(interaction, embed=ui.error_embed(str(exc)), ephemeral=True)
                return
        else:
            raw_qty = str(amount).strip().replace(",", "").replace("_", "")
            if not raw_qty.isdigit() or int(raw_qty) < 1 or int(raw_qty) > 1000000000:
                await ui.respond(
                    interaction,
                    embed=ui.error_embed(
                        "Items, fish, vehicles and ammunition require a whole-number quantity."
                    ),
                    ephemeral=True,
                )
                return
            qty = int(raw_qty)
        label = key

        def fail(message: str):
            return ui.error_embed(message)

        error: Optional[str] = None
        if key == "donuts":
            have = int(donor.get("donuts", 0))
            if qty > have:
                error = f"You only have {ui.format_donuts(have)} donuts in your wallet."
            else:
                transfer_wallet(donor, receiver, qty)
                label = f"🍩 {ui.format_donuts(qty)} donuts"
        elif key.startswith("item:"):
            item = key.split(":", 1)[1]
            if item not in CONSUMABLES:
                error = "That consumable does not exist."
            else:
                have = int(donor.setdefault("inventory", {}).get(item, 0))
                cap = asset_service.hold_cap(cfg, item)
                dest_have = int(receiver.setdefault("inventory", {}).get(item, 0))
                if qty > have:
                    error = f"You only have {have} {CONSUMABLES[item]['name']}."
                elif (
                    cap is not None and dest_have + qty + asset_service.reserved(receiver, "item", item) > cap
                ):
                    error = f"The recipient can hold at most {cap} {CONSUMABLES[item]['name']}."
                else:
                    donor["inventory"][item] = have - qty
                    receiver["inventory"][item] = dest_have + qty
                    label = f"{CONSUMABLES[item]['emoji']} {CONSUMABLES[item]['name']} ×{qty}"
        elif key.startswith("fish:"):
            fish_id = key.split(":", 1)[1]
            fish = FISH_BY_ID.get(fish_id)
            have = int(donor.setdefault("fish", {}).get(fish_id, 0))
            if fish is None:
                error = "That fishing supply does not exist."
            elif qty > have:
                error = f"You only have {have} {fish.name}."
            else:
                donor["fish"][fish_id] = have - qty
                receiver.setdefault("fish", {})[fish_id] = int(receiver["fish"].get(fish_id, 0)) + qty
                source_meta = donor.setdefault("fish_meta", {}).get(fish_id)
                dest_meta = receiver.setdefault("fish_meta", {}).get(fish_id)
                ages = [
                    float(meta.get("age", 0.0)) for meta in (source_meta, dest_meta) if isinstance(meta, dict)
                ]
                receiver["fish_meta"][fish_id] = {"age": max(ages, default=0.0), "w1": False, "w2": False}
                if donor["fish"][fish_id] <= 0:
                    donor["fish"].pop(fish_id, None)
                    donor["fish_meta"].pop(fish_id, None)
                label = f"{fish.emoji} {fish.name} ×{qty}"
        elif key.startswith("thor-component:"):
            component_id = key.split(":", 1)[1]
            source_thor = thor_state.normalize(donor)
            dest_thor = thor_state.normalize(receiver)
            thor_state.settle(donor, cfg)
            thor_state.settle(receiver, cfg)
            if qty != 1:
                error = "THOR modules must be transferred one at a time."
            elif component_id not in thor_state.COMPONENTS:
                error = "That THOR module does not exist."
            elif source_thor["components"][component_id].get("status") != "ready":
                error = "Only a completed, unlaunched THOR module can be transferred."
            elif dest_thor.get("operational") or thor_state.parse_time(dest_thor.get("assembling_until")):
                error = "The recipient already has an assembled or assembling Project THOR."
            elif dest_thor["components"][component_id].get("status") != "none":
                error = "The recipient already owns or is building that THOR module."
            else:
                dest_thor["components"][component_id] = copy.deepcopy(source_thor["components"][component_id])
                source_thor["components"][component_id] = thor_state.default_component()
                label = f"📦 {thor_state.COMPONENTS[component_id]['short']}"
        elif key.startswith("vehicle:"):
            model = key.split(":", 1)[1]
            if qty != 1:
                error = "Vehicles must be transferred one at a time."
            elif model == "s400":
                if not donor.get("s400_owned"):
                    error = "You do not own an operational S-400 battery."
                elif receiver.get("s400_owned") or coalitions.parse_time(receiver.get("s400_building_at")):
                    error = "The recipient already owns or is building an S-400 battery."
                elif coalitions.parse_time(donor.get("s400_building_at")):
                    error = "An S-400 under construction cannot be transferred."
                else:
                    receiver["s400_owned"] = True
                    receiver["s400_building_at"] = None
                    receiver["s400_interceptors"] = int(donor.get("s400_interceptors", 0))
                    donor["s400_owned"] = False
                    donor["s400_building_at"] = None
                    donor["s400_interceptors"] = 0
                    label = "📡 S-400 Triumf battery"
            elif model not in VEHICLE_CATALOG:
                error = "That vehicle does not exist."
            else:
                error = self._vehicle_transfer_error(cfg, donor, receiver, model, uid)
                if error is None and econ_cog is not None:
                    source = econ_cog._vehicle_state(donor, model)
                    receiver.setdefault("vehicles", {})[model] = copy.deepcopy(source)
                    donor.setdefault("vehicles", {})[model] = {
                        "owned": False,
                        "building_until": None,
                        "last_deploy_at": None,
                    }
                    if model == "b2":
                        donor["vehicles"][model].update({"armed": False, "arming_until": None})
                    if model in LOADABLE_VEHICLES:
                        donor["vehicles"][model].update({"ammo": 0, "loading_until": None, "loading_qty": 0})
                    label = f"{VEHICLE_CATALOG[model]['emoji']} {VEHICLE_CATALOG[model]['short']}"
        elif key.startswith("ammo:"):
            ammo = key.split(":", 1)[1]
            if ammo == "gbi":
                source_thor = thor_state.normalize(donor)
                dest_thor = thor_state.normalize(receiver)
                thor_state.settle(donor, cfg)
                thor_state.settle(receiver, cfg)
                have = int(source_thor.get("gbi_stock", 0))
                dest = int(dest_thor.get("gbi_stock", 0))
                pending = int(dest_thor.get("gbi_building_qty", 0))
                cap = int(cfg.get("economy.thor_gbi_stock_capacity", 2))
                if qty > have:
                    error = f"You have only {have} ready GBI/EKV interceptor(s)."
                elif dest + pending + qty > cap:
                    room = max(0, cap - dest - pending)
                    error = f"The recipient's GBI/EKV stockpile has room for only {room} more."
                else:
                    source_thor["gbi_stock"] = have - qty
                    dest_thor["gbi_stock"] = dest + qty
                    label = f"🎯 GBI/EKV ×{qty}"
            elif ammo == "thor":
                source_thor = thor_state.normalize(donor)
                dest_thor = thor_state.normalize(receiver)
                thor_state.settle(donor, cfg)
                thor_state.settle(receiver, cfg)
                cap = int(cfg.get("economy.thor_rod_capacity", 6))
                reserved = (
                    1
                    if source_thor.get("chambered")
                    or thor_state.parse_time(source_thor.get("chambering_until"))
                    else 0
                )
                have = max(0, int(source_thor.get("rods", 0)) - reserved)
                dest = int(dest_thor.get("rods", 0))
                pending = (
                    int(dest_thor.get("resupply_qty", 0))
                    if thor_state.parse_time(dest_thor.get("resupply_until"))
                    else 0
                )
                if not source_thor.get("operational"):
                    error = "You need an operational Project THOR to release kinetic penetrators."
                elif not dest_thor.get("operational"):
                    error = "The recipient needs an operational Project THOR."
                elif qty > have:
                    error = f"You have only {have} unchambered penetrator(s) available."
                elif dest + pending + qty > cap:
                    error = f"The recipient's orbital magazine has room for only {max(0, cap - dest - pending)} more."
                else:
                    source_thor["rods"] = int(source_thor.get("rods", 0)) - qty
                    dest_thor["rods"] = dest + qty
                    label = f"🔩 THOR kinetic penetrator ×{qty}"
            elif ammo == "sm3":
                if econ_cog is None:
                    error = "Strategic logistics are temporarily unavailable."
                else:
                    source = econ_cog._vehicle_settle(donor, "aegis")
                    dest_state = econ_cog._vehicle_settle(receiver, "aegis")
                    thor_state.settle_aegis_bmd(source, cfg)
                    thor_state.settle_aegis_bmd(dest_state, cfg)
                    have, dest = (int(source.get("sm3_ammo", 0)), int(dest_state.get("sm3_ammo", 0)))
                    cap = int(cfg.get("economy.thor_sm3_capacity", 3))
                    if not source.get("owned") or not source.get("bmd_owned"):
                        error = "You need an operational Aegis BMD refit to release SM-3 missiles."
                    elif not dest_state.get("owned") or not dest_state.get("bmd_owned"):
                        error = "The recipient needs an operational Aegis BMD refit."
                    elif coalitions.parse_time(source.get("sm3_loading_until")) or coalitions.parse_time(
                        dest_state.get("sm3_loading_until")
                    ):
                        error = "Finish active SM-3 loading cycles before transferring interceptors."
                    elif qty > have:
                        error = f"You only have {have} SM-3 Block IIA interceptor(s)."
                    elif dest + qty > cap:
                        error = f"The recipient's BMD battery has room for only {max(0, cap - dest)} more."
                    else:
                        source["sm3_ammo"], dest_state["sm3_ammo"] = (have - qty, dest + qty)
                        label = f"🛰️ RIM-161 SM-3 Block IIA ×{qty}"
            elif ammo == "icbm":
                if econ_cog is None:
                    error = "Strategic logistics are temporarily unavailable."
                else:
                    have = econ_cog._icbm_settle(donor)
                    dest = econ_cog._icbm_settle(receiver)
                    cap = econ_cog._icbm_cap(cfg, receiver)
                    pending = int(coalitions.parse_time(receiver.get("icbm_building_at")) is not None)
                    if qty > have:
                        error = f"You only have {have} completed ICBM(s)."
                    elif dest + pending + qty > cap:
                        error = f"The recipient's silo has room for only {max(0, cap - dest - pending)} more ICBM(s)."
                    else:
                        donor["icbm_ready"] = have - qty
                        receiver["icbm_ready"] = dest + qty
                        label = f"🚀 Completed ICBM ×{qty}"
            elif ammo in {"aa-stock", "aa-loaded"}:
                if econ_cog is not None:
                    for user in (donor, receiver):
                        if econ_cog._aa_settle(cfg, user):
                            self.econ.mark_dirty(gid)
                field = "aa_rockets_stock" if ammo == "aa-stock" else "aa_rockets_loaded"
                have, dest = (int(donor.get(field, 0)), int(receiver.get(field, 0)))
                cap = (
                    int(cfg.get("economy.aa_rocket_stock_cap", 10))
                    if ammo == "aa-stock"
                    else int(cfg.get("economy.aa_rocket_load_cap", 5))
                    + (2 if plushie_perk(receiver, "aa_build") else 0)
                )
                pending = (
                    int(receiver.get("aa_rocket_build_qty", 0))
                    if ammo == "aa-stock" and coalitions.parse_time(receiver.get("aa_rocket_build_at"))
                    else 0
                )
                if qty > have:
                    error = f"You only have {have} of that AA ammunition."
                elif dest + pending + qty > cap:
                    error = f"The recipient has room for only {max(0, cap - dest - pending)} more."
                else:
                    donor[field], receiver[field] = (have - qty, dest + qty)
                    label = f"🛡️ Regular AA {('reserve' if ammo == 'aa-stock' else 'loaded')} ×{qty}"
            elif ammo == "s400":
                have, dest = (
                    int(donor.get("s400_interceptors", 0)),
                    int(receiver.get("s400_interceptors", 0)),
                )
                cap = int(cfg.get("economy.s400_interceptor_cap", 2))
                if not receiver.get("s400_owned"):
                    error = "The recipient needs an operational S-400 battery."
                elif qty > have:
                    error = f"You only have {have} 40N6E interceptor(s)."
                elif dest + qty > cap:
                    error = f"The recipient's S-400 can hold only {cap} interceptor(s)."
                else:
                    donor["s400_interceptors"], receiver["s400_interceptors"] = (have - qty, dest + qty)
                    label = f"📡 40N6E interceptor ×{qty}"
            elif ammo == "b61":
                if qty != 1:
                    error = "B61-12 payloads must be transferred one at a time."
                elif econ_cog is None:
                    error = "Strategic logistics are temporarily unavailable."
                else:
                    source = econ_cog._vehicle_settle(donor, "b2")
                    dest = econ_cog._vehicle_settle(receiver, "b2")
                    if not source.get("owned") or not source.get("armed"):
                        error = "Your operational B-2 is not carrying a B61-12 payload."
                    elif not dest.get("owned"):
                        error = "The recipient needs an operational B-2 Spirit."
                    elif dest.get("armed") or coalitions.parse_time(dest.get("arming_until")):
                        error = "The recipient's B-2 already has a payload assigned."
                    else:
                        source["armed"], dest["armed"] = (False, True)
                        label = "☢️ B61-12 B-2 payload"
            elif ammo in LOADABLE_VEHICLES:
                if econ_cog is None:
                    error = "Strategic logistics are temporarily unavailable."
                else:
                    source = econ_cog._vehicle_settle(donor, ammo)
                    dest_state = econ_cog._vehicle_settle(receiver, ammo)
                    spec = VEHICLE_CATALOG[ammo]
                    cap = int(cfg.get(f"economy.{spec['cap']}", int(spec["fallback_cap"])))
                    have, dest = (int(source.get("ammo", 0)), int(dest_state.get("ammo", 0)))
                    if not source.get("owned"):
                        error = f"You need an operational {spec['short']} to release that ammunition."
                    elif not dest_state.get("owned"):
                        error = f"The recipient needs an operational {spec['short']}."
                    elif coalitions.parse_time(source.get("loading_until")) or coalitions.parse_time(
                        dest_state.get("loading_until")
                    ):
                        error = "Finish active loading cycles before transferring ammunition."
                    elif qty > have:
                        error = f"You only have {have} {spec['ammo']} round(s)."
                    elif dest + qty > cap:
                        error = f"The recipient has room for only {max(0, cap - dest)} more round(s)."
                    else:
                        source["ammo"], dest_state["ammo"] = (have - qty, dest + qty)
                        label = f"🎯 {spec['ammo']} ×{qty}"
            else:
                error = "That ammunition type does not exist."
        else:
            error = "Choose an asset from the transfer list."
        if error:
            await ui.respond(interaction, embed=fail(error), ephemeral=True)
            return
        coalitions.append_log(group, uid, "transfer", f"Sent {label} to {member.id}")
        await self._save(gid)
        if key == "donuts":
            await self.bot.ledger.record(
                gid,
                uid,
                -qty,
                "coalition-transfer-sent",
                after=int(donor.get("donuts", 0)) + int(donor.get("bank", 0)),
                other=member.id,
            )
            await self.bot.ledger.record(
                gid,
                member.id,
                qty,
                "coalition-transfer-received",
                after=int(receiver.get("donuts", 0)) + int(receiver.get("bank", 0)),
                other=uid,
            )
        else:
            reason = f"coalition-transfer:{key}:{qty}"
            await self.bot.ledger.record(
                gid,
                uid,
                0,
                reason,
                after=int(donor.get("donuts", 0)) + int(donor.get("bank", 0)),
                other=member.id,
            )
            await self.bot.ledger.record(
                gid,
                member.id,
                0,
                reason,
                after=int(receiver.get("donuts", 0)) + int(receiver.get("bank", 0)),
                other=uid,
                actor=uid,
            )
        await ui.respond(
            interaction,
            embed=ui.ok_embed(
                f"Transferred **{label}** to **{member.display_name}**. This transfer is permanent.",
                title="📦 Mutual Logistics",
            ),
            ephemeral=True,
        )

    @coalition.command(name="log", description="Privately inspect your coalition's recent activity.")
    async def log(self, interaction: discord.Interaction) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        doc = self._doc(interaction.guild_id)
        found = coalitions.coalition_for(doc, interaction.user.id)
        if found is None:
            await ui.respond(
                interaction, embed=ui.error_embed("You do not belong to a coalition."), ephemeral=True
            )
            return
        entries = list(found[1].get("log", []))[-20:]
        if not entries:
            description = "No activity has been recorded."
        else:
            lines = []
            for entry in reversed(entries):
                stamp = coalitions.parse_time(entry.get("at"))
                when = f"<t:{int(stamp.timestamp())}:R>" if stamp else "Earlier"
                lines.append(
                    f"{when} · <@{int(entry.get('actor', 0))}> · **{entry.get('action', 'event')}**\n{str(entry.get('detail', ''))[:300]}"
                )
            description = "\n\n".join(lines)[:4096]
        await ui.respond(
            interaction,
            embed=ui.base_embed(
                title=f"📜 {self._name(found[1])} — activity log", description=description, color=cfg.color
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @coalition.command(
        name="leave", description="Leave your coalition and begin a 24-hour diplomatic cooldown."
    )
    async def leave(self, interaction: discord.Interaction) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        doc = self._doc(gid)
        found = coalitions.coalition_for(doc, uid)
        if found is None:
            await ui.respond(
                interaction, embed=ui.error_embed("You do not belong to a coalition."), ephemeral=True
            )
            return
        cid, group = found
        was_founder = int(group.get("founder", 0)) == uid
        members = [member for member in self._member_ids(group) if member != uid]
        hours = max(1.0, float(cfg.get("economy.coalition_rejoin_cooldown_hours", 24)))
        cooldown = coalitions.set_cooldown(doc, uid, hours)
        if not members:
            coalitions.state(doc)["groups"].pop(cid, None)
            action = "The final signatory left; the coalition dissolved."
        else:
            group["members"] = members
            if was_founder:
                group["founder"] = members[0]
                coalitions.append_log(group, uid, "leadership", f"Command transferred to {members[0]}")
            coalitions.append_log(group, uid, "left", f"{uid} left the coalition")
            action = "Leadership passed automatically." if was_founder else ""
        await self._save(gid)
        await ui.respond(
            interaction,
            embed=ui.base_embed(
                title="🤝 Coalition departed",
                description=f"{interaction.user.mention} left **{self._name(group)}**. {action}\nTheir diplomatic cooldown ends <t:{int(cooldown.timestamp())}:R>.",
                color=ui.COLOR_WARN,
            ),
        )

    @coalition.command(name="kick", description="Commander: remove a signatory from the coalition.")
    @app_commands.describe(member="Signatory to remove")
    async def kick(self, interaction: discord.Interaction, member: discord.Member) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        doc = self._doc(gid)
        found = coalitions.coalition_for(doc, uid)
        if found is None or int(found[1].get("founder", 0)) != uid:
            await ui.respond(
                interaction,
                embed=ui.error_embed("Only the Coalition Commander can remove a signatory."),
                ephemeral=True,
            )
            return
        group = found[1]
        members = self._member_ids(group)
        if member.id == uid or member.id not in members:
            await ui.respond(
                interaction, embed=ui.error_embed("Choose another current signatory."), ephemeral=True
            )
            return
        view = ui.ConfirmView(uid, danger_label="Remove signatory")
        await ui.respond(
            interaction,
            embed=ui.warn_embed(
                f"Remove **{member.display_name}** from **{self._name(group)}**? Their protection and intelligence access end immediately, and they begin a 24-hour cooldown."
            ),
            view=view,
            ephemeral=True,
        )
        if await view.wait():
            try:
                await interaction.edit_original_response(
                    embed=ui.warn_embed("Confirmation timed out — nothing was changed."), view=None
                )
            except discord.HTTPException:
                pass
            return
        if not view.value:
            return
        doc = self._doc(gid)
        found = coalitions.coalition_for(doc, uid)
        if (
            found is None
            or int(found[1].get("founder", 0)) != uid
            or member.id not in self._member_ids(found[1])
        ):
            await interaction.followup.send(
                embed=ui.error_embed("The coalition changed before confirmation; no one was removed."),
                ephemeral=True,
            )
            return
        group = found[1]
        members = self._member_ids(group)
        group["members"] = [member_id for member_id in members if member_id != member.id]
        hours = max(1.0, float(cfg.get("economy.coalition_rejoin_cooldown_hours", 24)))
        cooldown = coalitions.set_cooldown(doc, member.id, hours)
        coalitions.append_log(group, uid, "kicked", f"Removed {member.id}")
        await self._save(gid)
        await interaction.followup.send(
            embed=ui.base_embed(
                title="📜 Signatory removed",
                description=f"{member.mention} was removed from **{self._name(group)}** by {interaction.user.mention}. Their cooldown ends <t:{int(cooldown.timestamp())}:R>.",
                color=ui.COLOR_WARN,
            ),
            allowed_mentions=discord.AllowedMentions(users=[member]),
            ephemeral=False,
        )

    @coalition.command(name="disband", description="Commander: permanently dissolve the coalition.")
    async def disband(self, interaction: discord.Interaction) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        doc = self._doc(gid)
        found = coalitions.coalition_for(doc, uid)
        if found is None or int(found[1].get("founder", 0)) != uid:
            await ui.respond(
                interaction,
                embed=ui.error_embed("Only the Coalition Commander can dissolve the coalition."),
                ephemeral=True,
            )
            return
        cid, group = found
        name = self._name(group)
        hours = max(1.0, float(cfg.get("economy.coalition_rejoin_cooldown_hours", 24)))
        for member in self._member_ids(group):
            coalitions.set_cooldown(doc, member, hours)
        coalitions.state(doc)["groups"].pop(cid, None)
        for invited, pending in list(coalitions.state(doc)["invites"].items()):
            live = [item for item in pending if str(item.get("coalition_id")) != cid]
            if live:
                coalitions.state(doc)["invites"][invited] = live
            else:
                coalitions.state(doc)["invites"].pop(invited, None)
        await self._save(gid)
        await ui.respond(
            interaction,
            embed=ui.base_embed(
                title="📜 Coalition dissolved",
                description=f"{interaction.user.mention} dissolved **{name}**. All mutual intelligence, logistics access and non-aggression protection ended immediately. No fees or assets were refunded.",
                color=ui.COLOR_WARN,
            ),
        )

    @coalition.command(name="list", description="Publicly list active coalitions and their signatories.")
    async def list_coalitions(self, interaction: discord.Interaction) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        doc = self._doc(interaction.guild_id)
        groups = list(coalitions.state(doc)["groups"].values())
        if not groups:
            description = "No active coalitions. Establish one with `/coalition create`."
        else:
            lines = []
            groups.sort(key=lambda group: str(group.get("name", "")).casefold())
            for group in groups[:25]:
                expires = coalitions.parse_time(group.get("expires_at"))
                expiry = f" · expires <t:{int(expires.timestamp())}:R>" if expires else ""
                members = " ".join((f"<@{uid}>" for uid in self._member_ids(group)))
                lines.append(f"**{self._name(group)}** — {members}{expiry}")
            description = "\n".join(lines)[:4096]
        await self._save(interaction.guild_id)
        await ui.respond(
            interaction,
            embed=ui.base_embed(title="🌐 Active coalitions", description=description, color=cfg.color),
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CoalitionCog(bot))
