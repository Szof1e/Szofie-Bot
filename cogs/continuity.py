"""Raven Rock Continuity Complex: deliberately limited THOR-proof storage."""

from __future__ import annotations
import copy
import datetime as dt
from typing import Any, Dict, Optional
import discord
from discord import app_commands
from discord.ext import commands
from szofie.catalog import CONSUMABLES, VEHICLE_CATALOG
from szofie import assets as asset_service
from cogs.fishing import ROD_BY_ID
from szofie import continuity as continuity_state, imperial_star_destroyer as isd_state, ui
from szofie import status_ui
from szofie.betting import AmountParseError, parse_amount
from szofie.plushies import PLUSHIE_BY_ID
from szofie.currency import visible_total as _net, take_visible as _take

CONVENTIONAL_VEHICLES = frozenset(VEHICLE_CATALOG) - {"b2", "aegis", "u2", "deimos", "sr71"}
CONTINUITY_FUNDS_CAP_DEFAULT = 100000000000000


class ContinuityCog(commands.Cog, name="Raven Rock Continuity"):
    continuity = app_commands.Group(
        name="continuity", description="Build and operate a limited THOR-proof recovery bunker."
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.econ = bot.economy

    def cfg(self, guild_id: int):
        return self.bot.config.for_guild(guild_id)

    def user(self, guild_id: int, user_id: int) -> Dict[str, Any]:
        starting = int(self.cfg(guild_id).get("economy.starting_balance", 100))
        return self.econ.user(guild_id, user_id, starting)

    async def _guard(self, interaction: discord.Interaction):
        if interaction.guild_id is None:
            await ui.respond(
                interaction,
                embed=ui.error_embed("Continuity facilities only operate inside a server."),
                ephemeral=True,
            )
            return None
        cfg = self.cfg(interaction.guild_id)
        if not cfg.get("economy.enabled", True):
            await ui.respond(
                interaction, embed=ui.error_embed("The economy is disabled here."), ephemeral=True
            )
            return None
        await ui.defer_response(interaction, ephemeral=True)
        return cfg

    async def _save(self, guild_id: int) -> None:
        await self.econ.save(guild_id)

    async def _reject_cinder_transfer(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id is None:
            return False
        if isd_state.active_cinder(self.econ.store.load(interaction.guild_id)):
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    "Raven Rock sealed every transfer hatch when Operation Cinder began charging. Only the loadout secured before the warning can survive."
                ),
                ephemeral=True,
            )
            return True
        return False

    @staticmethod
    def _busy(state: Dict[str, Any]) -> Optional[str]:
        transfer = continuity_state.parse_time(state.get("transfer_until"))
        if transfer:
            return f"A secure transfer is already in progress until <t:{int(transfer.timestamp())}:R>."
        sealed = continuity_state.parse_time(state.get("sealed_until"))
        if sealed:
            return f"Raven Rock is sealed after a THOR impact until <t:{int(sealed.timestamp())}:R>."
        return None

    @continuity.command(name="build", description="Construct the Raven Rock Continuity Complex.")
    async def build(self, interaction: discord.Interaction) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        if await self._reject_cinder_transfer(interaction):
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        user = self.user(gid, uid)
        continuity_state.settle(user, cfg)
        state = continuity_state.normalize(user)
        if state.get("owned"):
            await ui.respond(
                interaction,
                embed=ui.warn_embed("Your Raven Rock Continuity Complex is already operational."),
                ephemeral=True,
            )
            return
        building = continuity_state.parse_time(state.get("building_until"))
        if building:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(f"Raven Rock completes <t:{int(building.timestamp())}:R>."),
                ephemeral=True,
            )
            return
        cost = int(cfg.get("economy.continuity_cost", 5000000000000))
        if _net(user) < cost:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"Raven Rock costs 🍩 **{ui.format_donuts(cost)}**."),
                ephemeral=True,
            )
            return
        before = _net(user)
        _take(user, cost)
        hours = max(0.01, float(cfg.get("economy.continuity_build_hours", 24)))
        ready = continuity_state.utcnow() + dt.timedelta(hours=hours)
        state["building_until"] = ready.isoformat()
        await self._save(gid)
        await self.bot.ledger.record(gid, uid, _net(user) - before, "continuity-build", after=_net(user))
        await ui.respond(
            interaction,
            embed=ui.base_embed(
                title="🏔️ RAVEN ROCK CONSTRUCTION STARTED",
                description=f"{interaction.user.mention} committed 🍩 **{ui.format_donuts(cost)}** to a hardened continuity complex.\n\n**Operational:** <t:{int(ready.timestamp())}:F> · <t:{int(ready.timestamp())}:R>\nOnly assets deliberately transferred inside survive a THOR impact.",
                color=cfg.color,
            ),
        )

    @continuity.command(name="status", description="Privately inspect your Raven Rock recovery loadout.")
    async def status(self, interaction: discord.Interaction) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        user = self.user(interaction.guild_id, interaction.user.id)
        changed = continuity_state.settle(user, cfg)
        state = continuity_state.normalize(user)
        if changed:
            await self._save(interaction.guild_id)
        state = dict(state, funds=state.get("funds", 0))
        building = continuity_state.parse_time(state.get("building_until"))
        if not state.get("owned"):
            text = (
                f"**State:** 🏗️ Building\n**Completes:** {status_ui.deadline(building)}"
                if building
                else "**State:** ⚪ Not built\n\n➡️ Build your recovery bunker with `/continuity build`."
            )
            await ui.respond(
                interaction,
                embed=ui.base_embed(title="🏔️ Raven Rock", description=text, color=cfg.color),
                ephemeral=True,
            )
            return
        transfer = continuity_state.parse_time(state.get("transfer_until"))
        sealed = continuity_state.parse_time(state.get("sealed_until"))
        items = sum((int(v) for v in state["items"].values()))
        plushies = sum((int(v) for v in state["plushies"].values()))
        rod = state.get("rod") or {}
        vehicle = state.get("vehicle") or {}
        label = "🔐 Transfer in progress" if transfer else "☢️ Sealed after impact" if sealed else "🟢 Ready"
        fields = [
            ("📋 Bunker readiness", status_ui.line("State", label)),
            (
                "💰 Protected funds",
                f"**Stored:** {ui.format_donuts(int(state.get('funds', 0)))}\n**Capacity:** {ui.format_donuts(int(cfg.get('economy.continuity_funds_cap', CONTINUITY_FUNDS_CAP_DEFAULT)))}",
            ),
            (
                "📦 Recovery loadout",
                "\n".join(
                    [
                        status_ui.line(
                            "Consumables", f"{items}/{int(cfg.get('economy.continuity_item_cap', 20))}"
                        ),
                        status_ui.line(
                            "Plushies", f"{plushies}/{int(cfg.get('economy.continuity_plushie_cap', 5))}"
                        ),
                        status_ui.line(
                            "Rod", ROD_BY_ID[str(rod["id"])].name if rod.get("id") in ROD_BY_ID else "Empty"
                        ),
                        status_ui.line(
                            "Vehicle",
                            VEHICLE_CATALOG.get(str(vehicle.get("model")), {}).get("short", "Empty"),
                        ),
                    ]
                ),
            ),
        ]
        timers = []
        if transfer:
            timers.append(
                status_ui.line("Transfer", str(state.get("transfer_action")).title())
                + "\n"
                + status_ui.line("Completes", status_ui.deadline(transfer))
            )
        if sealed:
            timers.append(status_ui.line("Post-impact seal lifts", status_ui.deadline(sealed)))
        fields.append(("⏳ Transfers and seals", "\n\n".join(timers) or "No transfers or seals active."))
        fields.append(
            (
                "➡️ Next action",
                "Use `/continuity store` to protect assets or `/continuity retrieve` to recover them.",
            )
        )
        minutes = float(cfg.get("economy.continuity_transfer_hours", 0.25)) * 60
        pages = status_ui.report(
            "🏔️ Raven Rock — Private Loadout",
            [("Overview", fields)],
            color=cfg.color,
            footer=f"{minutes:g}-minute store/retrieve transfers · finish dates use your local timezone",
        )
        await ui.respond(interaction, embed=pages[0], ephemeral=True)

    @continuity.command(name="store", description="Begin a 15-minute deposit transfer into Raven Rock.")
    @app_commands.describe(
        category="Asset class",
        identifier="Item, plushie, rod or vehicle ID",
        amount="Amount; funds accept all/half/25k/2m/1b",
    )
    @app_commands.choices(
        category=[
            app_commands.Choice(name="Funds", value="funds"),
            app_commands.Choice(name="Consumable item", value="item"),
            app_commands.Choice(name="Plushie", value="plushie"),
            app_commands.Choice(name="Fishing rod", value="rod"),
            app_commands.Choice(name="Conventional vehicle", value="vehicle"),
        ]
    )
    async def store(
        self,
        interaction: discord.Interaction,
        category: app_commands.Choice[str],
        identifier: Optional[str] = None,
        amount: Optional[str] = None,
    ) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        if await self._reject_cinder_transfer(interaction):
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        user = self.user(gid, uid)
        continuity_state.settle(user, cfg)
        state = continuity_state.normalize(user)
        if not state.get("owned"):
            await ui.respond(interaction, embed=ui.error_embed("Build Raven Rock first."), ephemeral=True)
            return
        busy = self._busy(state)
        if busy:
            await ui.respond(interaction, embed=ui.warn_embed(busy), ephemeral=True)
            return
        kind = category.value
        key = (identifier or "").casefold().strip()
        payload: Dict[str, Any]
        try:
            if kind == "funds":
                value = parse_amount(amount, available=_net(user), default_all=True)
                cap = int(cfg.get("economy.continuity_funds_cap", CONTINUITY_FUNDS_CAP_DEFAULT))
                if value > _net(user):
                    raise ValueError("You do not have that many visible wallet/bank donuts.")
                if value <= 0 or int(state.get("funds", 0)) + value > cap:
                    raise ValueError(f"Protected funds cannot exceed {ui.format_donuts(cap)}.")
                _take(user, value)
                payload = {"kind": kind, "amount": value}
            elif kind == "item":
                if key not in CONSUMABLES:
                    raise ValueError("Choose a consumable item ID from `/shop`.")
                have = int((user.get("inventory", {}) or {}).get(key, 0))
                value = parse_amount(amount, available=have, default_all=True)
                cap = int(cfg.get("economy.continuity_item_cap", 20))
                if value > have:
                    raise ValueError("You do not own that many of the item.")
                if value <= 0 or sum(state["items"].values()) + value > cap:
                    raise ValueError(f"Raven Rock holds at most {cap} consumable items total.")
                user["inventory"][key] = have - value
                if user["inventory"][key] <= 0:
                    del user["inventory"][key]
                payload = {"kind": kind, "id": key, "amount": value}
            elif kind == "plushie":
                if key not in PLUSHIE_BY_ID or int((user.get("plushies", {}) or {}).get(key, 0)) <= 0:
                    raise ValueError("You do not own that plushie ID.")
                cap = int(cfg.get("economy.continuity_plushie_cap", 5))
                if sum(state["plushies"].values()) >= cap:
                    raise ValueError(f"Raven Rock holds at most {cap} plushies.")
                user["plushies"].pop(key, None)
                payload = {"kind": kind, "id": key, "amount": 1}
            elif kind == "rod":
                if key not in ROD_BY_ID or not int((user.get("rods", {}) or {}).get(key, 0)):
                    raise ValueError("You do not own that rod ID.")
                if state.get("rod"):
                    raise ValueError("Raven Rock already contains a rod.")
                enchants = list((user.get("rod_enchants", {}) or {}).pop(key, []) or [])
                user["rods"].pop(key, None)
                if user.get("equipped_rod") == key:
                    user["equipped_rod"] = None
                payload = {"kind": kind, "data": {"id": key, "enchants": enchants}}
            else:
                if key not in CONVENTIONAL_VEHICLES:
                    raise ValueError("That is not an eligible conventional vehicle ID.")
                vehicle = (user.get("vehicles", {}) or {}).get(key, {})
                economy_cog = self.bot.get_cog("Economy")
                if economy_cog is not None:
                    vehicle = economy_cog._vehicle_settle(user, key)
                if not vehicle.get("owned"):
                    raise ValueError("That vehicle is not operational.")
                patrol_until = continuity_state.parse_time(vehicle.get("patrol_until"))
                if vehicle.get("jet_damaged") or (patrol_until and patrol_until > continuity_state.utcnow()):
                    raise ValueError("Repair this jet or finish its patrol before storing it.")
                if any(
                    (
                        continuity_state.parse_time(vehicle.get(field))
                        for field in (
                            "building_until",
                            "loading_until",
                            "comanche_upgrade_until",
                            "comanche_repairing_until",
                            "b52_repairing_until",
                            "jet_repair_until",
                        )
                    )
                ):
                    raise ValueError("Finish all building, loading or repairs before storage.")
                if state.get("vehicle"):
                    raise ValueError("Raven Rock already contains a vehicle.")
                stored = copy.deepcopy(vehicle)
                vehicle["owned"] = False
                vehicle["ammo"] = 0
                vehicle["last_deploy_at"] = None
                payload = {"kind": kind, "data": {"model": key, "state": stored}}
        except (AmountParseError, ValueError) as exc:
            await ui.respond(interaction, embed=ui.error_embed(str(exc)), ephemeral=True)
            return
        hours = max(0.01, float(cfg.get("economy.continuity_transfer_hours", 0.25)))
        ready = continuity_state.utcnow() + dt.timedelta(hours=hours)
        state.update(transfer_until=ready.isoformat(), transfer_action="store", transfer_payload=payload)
        await self._save(gid)
        await self.bot.ledger.record(gid, uid, 0, "continuity-store", after=_net(user), detail=kind)
        await ui.respond(
            interaction,
            embed=ui.ok_embed(
                f"Secure **{kind}** transfer started. Raven Rock receives it <t:{int(ready.timestamp())}:R>."
            ),
        )

    @continuity.command(
        name="retrieve", description="Begin a 15-minute withdrawal transfer from Raven Rock, unless sealed."
    )
    @app_commands.describe(
        category="Asset class",
        identifier="Stored item or plushie ID",
        amount="Amount; funds accept all/half/25k/2m/1b",
    )
    @app_commands.choices(
        category=[
            app_commands.Choice(name="Funds", value="funds"),
            app_commands.Choice(name="Consumable item", value="item"),
            app_commands.Choice(name="Plushie", value="plushie"),
            app_commands.Choice(name="Fishing rod", value="rod"),
            app_commands.Choice(name="Conventional vehicle", value="vehicle"),
        ]
    )
    async def retrieve(
        self,
        interaction: discord.Interaction,
        category: app_commands.Choice[str],
        identifier: Optional[str] = None,
        amount: Optional[str] = None,
    ) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        if await self._reject_cinder_transfer(interaction):
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        user = self.user(gid, uid)
        continuity_state.settle(user, cfg)
        state = continuity_state.normalize(user)
        if not state.get("owned"):
            await ui.respond(interaction, embed=ui.error_embed("Build Raven Rock first."), ephemeral=True)
            return
        busy = self._busy(state)
        if busy:
            await ui.respond(interaction, embed=ui.warn_embed(busy), ephemeral=True)
            return
        kind = category.value
        key = (identifier or "").casefold().strip()
        payload: Dict[str, Any]
        try:
            if kind == "funds":
                have = int(state.get("funds", 0))
                value = parse_amount(amount, available=have, default_all=True)
                if value <= 0 or value > have:
                    raise ValueError("No protected funds are available.")
                state["funds"] = have - value
                payload = {"kind": kind, "amount": value}
            elif kind == "item":
                have = int(state["items"].get(key, 0))
                value = parse_amount(amount, available=have, default_all=True)
                if key not in CONSUMABLES or value <= 0 or value > have:
                    raise ValueError("That item is not stored.")
                cap = asset_service.hold_cap(cfg, key)
                if cap is not None and int((user.get("inventory", {}) or {}).get(key, 0)) + value > cap:
                    raise ValueError(f"Retrieval would exceed the normal holding cap of {cap}.")
                state["items"][key] = have - value
                if state["items"][key] <= 0:
                    del state["items"][key]
                payload = {"kind": kind, "id": key, "amount": value}
            elif kind == "plushie":
                if int(state["plushies"].get(key, 0)) <= 0:
                    raise ValueError("That plushie is not stored.")
                if int((user.get("plushies", {}) or {}).get(key, 0)) > 0:
                    raise ValueError("You already own that plushie outside Raven Rock.")
                state["plushies"].pop(key, None)
                payload = {"kind": kind, "id": key, "amount": 1}
            elif kind == "rod":
                data = state.get("rod")
                if not isinstance(data, dict):
                    raise ValueError("No rod is stored.")
                rid = str(data.get("id", ""))
                if int((user.get("rods", {}) or {}).get(rid, 0)) > 0:
                    raise ValueError("You already own that rod outside Raven Rock.")
                state["rod"] = None
                payload = {"kind": kind, "data": copy.deepcopy(data)}
            else:
                data = state.get("vehicle")
                if not isinstance(data, dict):
                    raise ValueError("No vehicle is stored.")
                model = str(data.get("model", ""))
                active = (user.get("vehicles", {}) or {}).get(model, {})
                if active.get("owned") or continuity_state.parse_time(active.get("building_until")):
                    raise ValueError("You already own or are building that vehicle outside Raven Rock.")
                state["vehicle"] = None
                payload = {"kind": kind, "data": copy.deepcopy(data)}
        except (AmountParseError, ValueError) as exc:
            await ui.respond(interaction, embed=ui.error_embed(str(exc)), ephemeral=True)
            return
        hours = max(0.01, float(cfg.get("economy.continuity_transfer_hours", 0.25)))
        ready = continuity_state.utcnow() + dt.timedelta(hours=hours)
        state.update(transfer_until=ready.isoformat(), transfer_action="retrieve", transfer_payload=payload)
        await self._save(gid)
        await self.bot.ledger.record(gid, uid, 0, "continuity-retrieve", after=_net(user), detail=kind)
        await ui.respond(
            interaction,
            embed=ui.ok_embed(
                f"Recovery **{kind}** transfer started. It reaches you <t:{int(ready.timestamp())}:R>."
            ),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ContinuityCog(bot))
