"""Claimable countries, passive GDP production, and bounded territorial warfare."""

from __future__ import annotations
import datetime as dt
import logging
import math
import random
import secrets
from typing import Any, Dict, List, Optional, Tuple
import discord
from discord import app_commands
from discord.ext import commands, tasks
from szofie import badges, coalitions, countries, nyx, status_ui, ui
from szofie.betting import SpendTransformer
from szofie.currency import net_total, take_wallet_first
from szofie.plushies import plushie_perk

OFFENSE = {
    "b2": 100,
    "xb70": 92,
    "lrhw": 78,
    "zumwalt": 72,
    "m1a2": 70,
    "leopard2a7": 70,
    "virginia": 68,
    "c130j": 60,
    "f15e": 58,
    "himars": 46,
    "champ": 44,
    "mq9": 42,
    "maldx": 38,
    "f22": 68,
    "f35": 66,
    "j20": 74,
    "su57": 76,
}
DEFENSE = {"patriot": 18, "aegis": 16, "p8": 12, "f15e": 10, "s400": 24}
LABELS = {
    "b2": "B-2 Spirit",
    "xb70": "XB-70 Valkyrie",
    "lrhw": "LRHW Dark Eagle",
    "m1a2": "M1A2 SEPv3 Abrams",
    "leopard2a7": "Leopard 2A7A1",
    "zumwalt": "Zumwalt railgun",
    "virginia": "Virginia-class submarine",
    "c130j": "C-130J Rapid Dragon",
    "f15e": "F-15E Strike Eagle",
    "himars": "M142 HIMARS",
    "champ": "CHAMP microwave system",
    "mq9": "MQ-9 Reaper",
    "maldx": "ADM-160 MALD-X",
    "f22": "F-22A Raptor",
    "f35": "F-35A Lightning II",
    "j20": "J-20 Mighty Dragon",
    "su57": "Su-57 Felon",
}
ATLAS_PAGE_SIZE = 15
ATLAS_PAGES = math.ceil(len(countries.COUNTRIES) / ATLAS_PAGE_SIZE)
FORTIFICATION_MIN_SPEND = 1000000
FORTIFICATION_MAX_USEFUL = 1265625000
ARMORED_MODELS = ("m1a2", "leopard2a7")
log = logging.getLogger("szofie.countries")


def invasion_cooldown_minutes(cfg: Any) -> int:
    """Roll the independent country-campaign cooldown within configured bounds."""
    low = max(1, int(cfg.get("economy.country_invasion_cooldown_min_minutes", 10)))
    high = max(1, int(cfg.get("economy.country_invasion_cooldown_max_minutes", 15)))
    if high < low:
        low, high = (high, low)
    return low + secrets.randbelow(high - low + 1)


def useful_war_chest_cap(territory_value: int) -> int:
    """Largest war chest that can affect combat, rounded up to whole donuts."""
    value = max(1, int(territory_value))
    return max(1000000, (value * 9 + 15) // 16)


def public_invasion_chance(attack: int, defense: int) -> int:
    """Return the ordinary chance that is safe to show to players."""
    return max(20, min(80, int(50 + (int(attack) - int(defense)) / 2)))


class Countries(commands.Cog):
    country = app_commands.Group(name="country", description="Claim, govern and conquer countries.")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.econ = bot.economy
        if hasattr(bot, "wait_until_ready"):
            self.income_sweep.start()

    def cog_unload(self) -> None:
        if self.income_sweep.is_running():
            self.income_sweep.cancel()

    def cfg(self, gid: int):
        return self.bot.config.for_guild(gid)

    def doc(self, gid: int) -> Dict[str, Any]:
        return self.econ.store.load(gid)

    def user(self, gid: int, uid: int) -> Dict[str, Any]:
        return self.econ.user(gid, uid, int(self.cfg(gid).get("economy.starting_balance", 100)))

    def economy_cog(self):
        return self.bot.get_cog("Economy")

    net = staticmethod(net_total)
    take = staticmethod(take_wallet_first)

    async def guard(self, interaction: discord.Interaction):
        await ui.defer_response(interaction)
        if interaction.guild_id is None:
            await ui.respond(
                interaction, embed=ui.error_embed("Countries only exist inside a server."), ephemeral=True
            )
            return None
        cfg = self.cfg(interaction.guild_id)
        if not cfg.get("economy.enabled", True):
            await ui.respond(
                interaction, embed=ui.error_embed("The economy is disabled here."), ephemeral=True
            )
            return None
        return cfg

    def _settle_owner_income(self, gid: int, uid: int, current: dt.datetime) -> tuple[int, bool]:
        """Move one ruler's matured country income into their wallet.

        The old ``last_collected_at`` key remains as a migration-compatible
        settlement cursor. Fractional donuts are carried per territory so an
        hourly sweep never reduces low-rate countries through repeated rounding.
        """
        holdings = countries.owned(self.doc(gid), uid)
        if not holdings:
            return (0, False)
        user = self.user(gid, uid)
        growth_pct = int(self.cfg(gid).get("economy.country_development_gdp_pct", 3))
        cap_hours = 48 * (1 + plushie_perk(user, "country_capacity") / 100)
        payout = 0
        touched = False
        for index, (country, territory) in enumerate(holdings):
            last = countries.parse_time(territory.get("last_collected_at")) or current
            elapsed_seconds = max(0.0, (current - last).total_seconds())
            capped_seconds = min(elapsed_seconds, cap_hours * 3600)
            if capped_seconds <= 0:
                continue
            window_start = current - dt.timedelta(seconds=capped_seconds)
            reconstruction = countries.parse_time(territory.get("reconstruction_until"))
            reduced_seconds = 0.0
            if reconstruction is not None and reconstruction > window_start:
                reduced_seconds = max(0.0, (min(current, reconstruction) - window_start).total_seconds())
            effective_seconds = capped_seconds - reduced_seconds * 0.6
            daily_net = max(
                0,
                countries.gross_per_day(country, index, territory, growth_pct)
                - countries.upkeep_per_day(country, plushie_perk(user, "country_upkeep"), territory),
            )
            try:
                carry = max(0.0, float(territory.get("income_remainder", 0.0)))
            except (TypeError, ValueError):
                carry = 0.0
            exact = daily_net * effective_seconds / 86400 + carry
            whole = max(0, int(exact))
            territory["income_remainder"] = round(exact - whole, 9)
            territory["last_collected_at"] = current.isoformat()
            payout += whole
            touched = True
        if payout:
            user["donuts"] = int(user.get("donuts", 0)) + payout
        return (payout, touched)

    async def settle_income(
        self, gid: int, current: Optional[dt.datetime] = None, owner_ids: Optional[set[int]] = None
    ) -> Dict[int, int]:
        """Settle automatic country income and return credited amounts by owner."""
        current = current or countries.now()
        territories = countries.state(self.doc(gid))["territories"]
        if owner_ids is None:
            owner_ids = set()
            for territory in territories.values():
                if not isinstance(territory, dict):
                    continue
                try:
                    owner_ids.add(int(territory.get("owner", 0)))
                except (TypeError, ValueError):
                    continue
            owner_ids.discard(0)
        payouts: Dict[int, int] = {}
        touched = False
        for uid in sorted(owner_ids):
            payout, owner_touched = self._settle_owner_income(gid, uid, current)
            touched = touched or owner_touched
            if payout:
                payouts[uid] = payout
        if touched:
            await self.econ.save(gid)
        for uid, payout in payouts.items():
            await self.bot.ledger.record(
                gid, uid, payout, "country-production", after=self.net(self.user(gid, uid))
            )
        return payouts

    @tasks.loop(hours=1)
    async def income_sweep(self) -> None:
        """Deliver country income hourly, including capped offline catch-up."""
        seen: set[int] = set()
        for guild in list(self.bot.guilds):
            canonical = self.econ.store.canonical_id(guild.id)
            if canonical in seen:
                continue
            seen.add(canonical)
            if not self.cfg(guild.id).get("economy.enabled", True):
                continue
            try:
                await self.settle_income(guild.id)
            except Exception:
                log.exception("Automatic country income failed for guild %s", guild.id)

    @income_sweep.before_loop
    async def _before_income_sweep(self) -> None:
        await self.bot.wait_until_ready()

    async def country_autocomplete(self, interaction: discord.Interaction, current: str):
        needle = (current or "").casefold()
        matches = [
            c for c in countries.COUNTRIES if not needle or needle in c.name.casefold() or needle in c.id
        ]
        territories = (
            countries.state(self.doc(interaction.guild_id))["territories"] if interaction.guild_id else {}
        )
        pct = (
            int(self.cfg(interaction.guild_id).get("economy.country_development_gdp_pct", 3))
            if interaction.guild_id
            else 3
        )
        return [
            app_commands.Choice(
                name=f"{c.flag} {c.name} · ${countries.effective_gdp_b(c, territories.get(c.id), pct):,}B GDP",
                value=c.id,
            )
            for c in matches[:25]
        ]

    @staticmethod
    def resolve(raw: str) -> Optional[countries.Country]:
        needle = str(raw).strip().casefold()
        if needle in countries.BY_ID:
            return countries.BY_ID[needle]
        return next((c for c in countries.COUNTRIES if c.name.casefold() == needle), None)

    def owner_name(self, guild: Optional[discord.Guild], raw: Any) -> str:
        try:
            uid = int(raw)
        except (TypeError, ValueError):
            return "Unclaimed"
        member = guild.get_member(uid) if guild else None
        return member.display_name if member else f"User {uid}"

    def _active_armored_garrison(
        self, gid: int, owner_id: int, country_id: Optional[str], *, require_ammo: bool = True
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        if not country_id:
            return None
        user = self.user(gid, owner_id)
        economy_cog = self.economy_cog()
        fleet = user.get("vehicles", {}) if isinstance(user.get("vehicles"), dict) else {}
        for model in ARMORED_MODELS:
            tank = fleet.get(model, {}) if isinstance(fleet.get(model), dict) else {}
            if economy_cog is not None:
                tank = economy_cog._vehicle_settle(user, model)
            damaged_key = f"{model}_damaged"
            repairing_key = f"{model}_repairing_until"
            if (
                tank.get("owned")
                and (not tank.get(damaged_key))
                and (countries.parse_time(tank.get(repairing_key)) is None)
                and (countries.parse_time(tank.get("garrison_transfer_until")) is None)
                and (str(tank.get("garrison_country") or "") == str(country_id))
                and (not require_ammo or int(tank.get("ammo", 0)) > 0)
            ):
                return (model, tank)
        return None

    def defense_power(
        self, gid: int, owner_id: int, territory: Dict[str, Any], country_id: Optional[str] = None
    ) -> int:
        user = self.user(gid, owner_id)
        fleet = user.get("vehicles", {}) if isinstance(user.get("vehicles"), dict) else {}
        power = 0
        for model, points in DEFENSE.items():
            if model == "s400":
                if user.get("s400_owned") and int(user.get("s400_interceptors", 0)) > 0:
                    power += points
            else:
                st = fleet.get(model, {}) if isinstance(fleet.get(model), dict) else {}
                if st.get("owned") and int(st.get("ammo", 0)) > 0:
                    power += points
        power += plushie_perk(user, "country_defense")
        invested = max(0, int(territory.get("fortification", 0)))
        power += min(45, math.isqrt(invested * 16 // 10000000))
        armored = self._active_armored_garrison(gid, owner_id, country_id)
        if armored is not None:
            model, _ = armored
            power += int(self.cfg(gid).get(f"economy.vehicle_{model}_garrison_defense", 25))
        return power

    @country.command(name="guide", description="Learn how countries, GDP, development and invasions work.")
    async def guide(self, interaction: discord.Interaction) -> None:
        cfg = await self.guard(interaction)
        if cfg is None:
            return
        currency = str(cfg.get("economy.currency_name", "donuts"))
        growth_pct = int(cfg.get("economy.country_development_gdp_pct", 3))
        max_level = int(cfg.get("economy.country_development_max_level", 20))
        first_cost_pct = int(cfg.get("economy.country_development_base_cost_pct", 5))
        cost_growth_pct = int(cfg.get("economy.country_development_cost_growth_pct", 10))
        start = ui.base_embed(
            title="🌍 Country Guide 1/4 — Getting Started",
            description="Countries are permanent economic and military territories. They produce donuts, add to your sovereign GDP ranking, can be developed, defended and conquered, and are shared across both servers.",
            color=cfg.color,
        )
        start.add_field(
            name="1. Find a country",
            value="Use `/country atlas` to browse all **195 countries**. The atlas shows current GDP, economic value, tier, development level and ruler. Use `/country inspect` for one country's exact status, defense estimate and growth path.",
            inline=False,
        )
        start.add_field(
            name="2. Get your first country",
            value="Use `/country claim` on an **unclaimed** country and pay its listed value from wallet and bank. You get exactly **one peaceful claim**. If you already received a nationality starter or rule any country, every additional territory must come from conquest.",
            inline=False,
        )
        start.add_field(
            name="3. Run your realm",
            value="`/country portfolio` privately shows all your countries and their net daily production. Net production is sent to your wallet automatically every hour. `/country develop` grows GDP and output. `/country reinforce` strengthens defenses. `/country leaderboard` ranks rulers by combined GDP.",
            inline=False,
        )
        start.add_field(
            name="Important ownership rule",
            value="A successful conquest keeps the country's development path for its new ruler. `/country abandon` gives no refund and deletes its development, fortifications and special starter path permanently.",
            inline=False,
        )
        economy = ui.base_embed(
            title="📈 Country Guide 2/4 — GDP, Income & Development",
            description="GDP is prestige and progression; economic value is the number used for production and military costs.",
            color=ui.COLOR_OK,
        )
        economy.add_field(
            name="How passive production works",
            value="Every territory earns at the full **100% production rate**, regardless of how many countries you rule or when you acquired them. Upkeep is deducted automatically and net income reaches your wallet **once per hour** while the bot is online. After downtime, the next sweep catches up as much as **48 hours**; Cartographer 21 raises this to 60 hours. Time beyond the cap is lost.",
            inline=False,
        )
        economy.add_field(
            name="Normal development path",
            value=f"Use `/country develop country levels` to buy **1–5 levels** at once, or choose **half/all** to buy the most levels that allocation can afford. Normal countries have {max_level} levels; each adds **{growth_pct}% GDP and production**. Level 1 costs {first_cost_pct}% of economic value and each later level is {cost_growth_pct}% more expensive. The investment is burned from the economy in {currency}.",
            inline=False,
        )
        economy.add_field(
            name="Nationality starter path",
            value="The six nationality grants begin equally at **$1B GDP and exactly 1M net donuts/day**. They use a separate **50-level curved path**: early levels grow slowly, later levels accelerate, and level 50 reaches that country's catalogued real-world GDP. Their starting upkeep is normalized so personal upkeep perks cannot break level-0 fairness.",
            inline=False,
        )
        economy.add_field(
            name="No retroactive upgrade trick",
            value="When you develop, that country's stored income is first settled at its **old rate**, then the upgrade takes effect. You cannot save 48 hours and multiply it retroactively. Development survives conquest.",
            inline=False,
        )
        war = ui.base_embed(
            title="⚔️ Country Guide 3/4 — Invading",
            description="You may invade a **claimed enemy country** or take an **unclaimed country by force**. You cannot invade yourself or a coalition ally. The attacking vehicle follows the same build and ammunition rules as `/vehicle deploy`, but country campaigns use a separate randomized **10–15 minute cooldown**. Normal vehicle mission cooldowns do not block invasions, and invasions do not place vehicles into strategic turnaround.",
            color=ui.COLOR_BAD,
        )
        war.add_field(
            name="Exact invasion checklist",
            value="**1.** Build an offensive vehicle with `/vehicle build`.\n**2.** Load its ammunition with `/vehicle load`; a B-2 needs an armed B61-12 instead.\n**3.** Wait for construction/loading and your separate country-campaign cooldown to finish.\n**4.** Keep enough wallet+bank funds for the war chest. Minimum is **5% of the territory's economic value**, but never below 1M.\n**5.** Run `/country invade`, choose the target, vehicle and war chest.",
            inline=False,
        )
        war.add_field(
            name="Offensive vehicle power",
            value=" · ".join((f"{LABELS[model]} **{power}**" for model, power in OFFENSE.items()))
            + ". A larger war chest adds up to 75 attack power. Spending above that useful ceiling is automatically limited before payment. Final victory chance is never below **20%** or above **80%**.",
            inline=False,
        )
        war.add_field(
            name="What the attack consumes",
            value="The effective war chest is burned on victory or defeat; any requested excess is never charged. One ammunition unit is normally spent either way, and only the separate country-campaign cooldown begins. Mechanic 21 may preserve non-B-2 ammunition. A B-2 spends its armed bomb.",
            inline=False,
        )
        war.add_field(
            name="Victory and defeat",
            value="Against a ruler, victory transfers ownership, keeps permanent development and cuts fortification to one-third. Against an unclaimed country, victory creates a fresh level-0 territory with no fortification. Either victory causes **24 hours at 40% production**. On defeat, the ruler keeps the country—or an unclaimed country stays free—and the war chest and ammunition remain spent.",
            inline=False,
        )
        war.add_field(
            name="Armored breach support",
            value="An Abrams or Leopard can invade normally, or `/vehicle deploy` can first breach one claimed enemy country. The Abrams removes 25% of current fortification and may defeat a Javelin track with Trophy; the Leopard removes 30% but has no Trophy reroll. Either gives only that attacker +20 invasion power there for 30 minutes and never damages GDP or development. Use `/warfare` for the full sequence.",
            inline=False,
        )
        defense = ui.base_embed(
            title="🛡️ Country Guide 4/4 — Defense, Coalitions & Examples",
            description="Country defense is automatic. The ruler does not need to press a button when an invasion arrives.",
            color=ui.COLOR_WARN,
        )
        defense.add_field(
            name="Where defense power comes from",
            value="Base tier: Micro **18**, Developing **32**, Regional **50**, Major **70**, Global **90**. An unclaimed country uses only this base tier defense. A claimed country also receives its ruler's systems, fortification and coalition support. Loaded systems add: S-400 **24**, Patriot **18**, Aegis **16**, P-8A **12**, F-15E **10**. A loaded Abrams or Leopard assigned with `/vehicle garrison` adds **25** to that one country and spends one tank round whenever it defends; an empty tank stays visible but adds nothing. Sovereign 21 adds 10. `/country reinforce` burns at least 1M for permanent fortification, capped at +45 defense power with diminishing returns. Requests beyond the useful maximum are automatically limited before payment.",
            inline=False,
        )
        defense.add_field(
            name="Coalition interaction",
            value="Coalition members cannot invade one another. You may reinforce an ally's country. During an invasion, each coalition ally can automatically contribute up to **15 defense power** from their ready fleet. Country ownership itself is individual and countries cannot be transferred through coalition logistics.",
            inline=False,
        )
        defense.add_field(
            name="Simple example",
            value="If you own one country, it earns at 100%. Conquer a second—or a tenth—and each also earns at 100%. Develop any country to raise its GDP and production. If an enemy conquers it, they inherit those development levels but suffer 24 hours of reconstruction.",
            inline=False,
        )
        defense.add_field(
            name="Which command do I use?",
            value="Browse: `/country atlas` · Details: `/country inspect` · Your realm: `/country portfolio` · Income: automatic to wallet · Grow: `/country develop` · Defend: `/country reinforce` · Expand: `/country invade` · Rankings: `/country leaderboard`.",
            inline=False,
        )
        pages = [start, economy, war, defense]
        view = ui.Paginator(pages, interaction.user.id)
        await ui.respond(
            interaction, embed=pages[0], view=view, allowed_mentions=discord.AllowedMentions.none()
        )

    @country.command(name="atlas", description="Browse the world market and current rulers.")
    @app_commands.describe(page=f"Atlas page (1-{ATLAS_PAGES})")
    async def atlas(
        self, interaction: discord.Interaction, page: app_commands.Range[int, 1, ATLAS_PAGES] = 1
    ) -> None:
        cfg = await self.guard(interaction)
        if cfg is None:
            return
        doc = self.doc(interaction.guild_id)
        territories = countries.state(doc)["territories"]
        growth_pct = int(cfg.get("economy.country_development_gdp_pct", 3))
        ordered = sorted(
            countries.COUNTRIES,
            key=lambda c: c.gdp_b
            if nyx.active(doc.get("users", {}).get(str(territories.get(c.id, {}).get("owner")), {}))
            else countries.effective_gdp_b(c, territories.get(c.id), growth_pct),
            reverse=True,
        )
        pages = []
        page_subjects = []
        for page_index in range(ATLAS_PAGES):
            chunk = ordered[page_index * ATLAS_PAGE_SIZE : (page_index + 1) * ATLAS_PAGE_SIZE]
            lines = []
            subjects = []
            for c in chunk:
                t = territories.get(c.id, {})
                if t and nyx.active(doc.get("users", {}).get(str(t.get("owner")), {})):
                    lines.append(f"{c.flag} **{c.name}** · claimed · NYX intelligence censored")
                    continue
                if t.get("owner"):
                    subjects.append(str(t["owner"]))
                ruler = self.owner_name(interaction.guild, t.get("owner")) if t else "Unclaimed"
                gdp = countries.effective_gdp_b(c, t, growth_pct)
                level = countries.development_level(t)
                developed = f" · Dev {level}" if level else ""
                value = countries.economic_value(c, t)
                tier = countries.territory_tier(c, t)
                lines.append(
                    f"{c.flag} **{c.name}** · ${gdp:,}B · 🍩 {ui.format_donuts(value)}\n└ {tier.title()}{developed} · {ruler}"
                )
            embed = ui.base_embed(title="🌍 World Atlas", description="\n".join(lines), color=cfg.color)
            embed.set_footer(
                text=f"Page {page_index + 1}/{ATLAS_PAGES} · 195 countries · base GDP plus permanent development · /country inspect"
            )
            pages.append(embed)
            page_subjects.append(subjects)

        def page_guard(index):
            if any((nyx.active(doc.get("users", {}).get(uid, {})) for uid in page_subjects[index])):
                return nyx.censored_embed()
            return None

        view = ui.Paginator(pages, interaction.user.id, initial_index=page - 1, page_guard=page_guard)
        interaction.extras["szofie_nyx_public_subjects"] = {
            int(uid) for subjects in page_subjects for uid in subjects
        }
        await ui.respond(interaction, embed=pages[page - 1], view=view)

    @country.command(name="inspect", description="Inspect a country's economy, ruler and defenses.")
    @app_commands.autocomplete(country=country_autocomplete)
    async def inspect(self, interaction: discord.Interaction, country: str) -> None:
        cfg = await self.guard(interaction)
        if cfg is None:
            return
        c = self.resolve(country)
        if c is None:
            await ui.respond(
                interaction,
                embed=ui.error_embed("Unknown country. Choose one from autocomplete."),
                ephemeral=True,
            )
            return
        t = countries.state(self.doc(interaction.guild_id))["territories"].get(c.id)
        owner = int(t.get("owner", 0)) if isinstance(t, dict) else 0
        if owner:
            nyx.protect_report(interaction, self.econ, owner)
        owner_state = self.doc(interaction.guild_id).get("users", {}).get(str(owner), {})
        if owner and nyx.hidden(owner_state, interaction.user.id, owner):
            await ui.respond(interaction, embed=nyx.censored_embed(), ephemeral=True)
            return
        if not isinstance(t, dict):
            status = "**Unclaimed** — available for a first peaceful claim or military invasion."
            defense = countries.TIER_DEFENSE[c.tier]
        else:
            owner = int(t.get("owner", 0))
            status = f"Ruled by **{self.owner_name(interaction.guild, owner)}** · acquired by {t.get('method', 'claim')}"
            defense = countries.TIER_DEFENSE[countries.territory_tier(c, t)] + self.defense_power(
                interaction.guild_id, owner, t, c.id
            )
            recon = countries.parse_time(t.get("reconstruction_until"))
            if recon and recon > countries.now():
                status += f"\n🚧 Reconstruction until <t:{int(recon.timestamp())}:R>"
            armored = self._active_armored_garrison(interaction.guild_id, owner, c.id, require_ammo=False)
            if armored is not None:
                model, garrison = armored
                rounds = max(0, int(garrison.get("ammo", 0)))
                readiness = "loaded" if rounds else "empty — no defense bonus"
                status += f"\n🛞 {LABELS[model]} garrison · {readiness} · {rounds} round(s)"
            breach_until = countries.parse_time(t.get("m1a2_breached_until"))
            if breach_until is not None and breach_until > countries.now():
                breacher = self.owner_name(interaction.guild, t.get("m1a2_breached_by"))
                status += f"\n⚠️ Breached by **{breacher}** until <t:{int(breach_until.timestamp())}:R>"
        growth_pct = int(cfg.get("economy.country_development_gdp_pct", 3))
        level = countries.development_level(t)
        max_level = countries.development_cap(t, int(cfg.get("economy.country_development_max_level", 20)))
        current_gdp = countries.effective_gdp_b(c, t, growth_pct)
        if countries.has_growth_path(t):
            gdp_detail = f"${current_gdp:,}B current\n${countries.base_gdp_b(c, t):,.0f}B start → ${float(t['growth_target_gdp_b']):,.0f}B target"
        else:
            gdp_detail = f"${current_gdp:,}B current\n${c.gdp_b:,}B base"
        embed = ui.base_embed(title=f"{c.flag} {c.name}", description=status, color=cfg.color)
        embed.add_field(name="Prestige GDP", value=gdp_detail, inline=True)
        embed.add_field(name="Development", value=f"Level {level}/{max_level}", inline=True)
        embed.add_field(name="Tier", value=countries.territory_tier(c, t).title(), inline=True)
        embed.add_field(
            name="Economic value", value=f"🍩 {ui.format_donuts(countries.economic_value(c, t))}", inline=True
        )
        embed.add_field(name="Estimated defense", value=f"🛡️ {defense} power", inline=True)
        await ui.respond(interaction, embed=embed, ephemeral=nyx.active(owner_state))

    @country.command(name="claim", description="Peacefully purchase your first country.")
    @app_commands.autocomplete(country=country_autocomplete)
    async def claim(self, interaction: discord.Interaction, country: str) -> None:
        cfg = await self.guard(interaction)
        if cfg is None:
            return
        gid, uid = (interaction.guild_id, interaction.user.id)
        c, doc = (self.resolve(country), self.doc(gid))
        if c is None:
            await ui.respond(interaction, embed=ui.error_embed("Unknown country."), ephemeral=True)
            return
        if countries.owned(doc, uid):
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    "Only your first country can be claimed peacefully. Expand with `/country invade`."
                ),
                ephemeral=True,
            )
            return
        territories = countries.state(doc)["territories"]
        if c.id in territories:
            await ui.respond(
                interaction, embed=ui.error_embed("That country already has a ruler."), ephemeral=True
            )
            return
        u = self.user(gid, uid)
        if self.net(u) < c.price:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"Claiming {c.name} costs 🍩 **{ui.format_donuts(c.price)}**."),
                ephemeral=True,
            )
            return
        self.take(u, c.price)
        now = countries.now()
        territories[c.id] = {
            "owner": uid,
            "acquired_at": now.isoformat(),
            "last_collected_at": now.isoformat(),
            "method": "peaceful claim",
            "fortification": 0,
            "development_level": 0,
            "reconstruction_until": None,
        }
        countries.append_history(doc, f"{uid} peacefully claimed {c.name}")
        await self.econ.save(gid)
        await self.bot.ledger.record(gid, uid, -c.price, "country-claim", after=self.net(u))
        await ui.respond(
            interaction,
            embed=ui.base_embed(
                title=f"{c.flag} {c.name} claimed",
                description=f"{interaction.user.mention} is now its sovereign. GDP production has begun and will be delivered to the wallet automatically.\n\nExpand further only by military conquest.",
                color=cfg.color,
            ),
        )

    @country.command(name="portfolio", description="View your countries and passive production.")
    async def portfolio(self, interaction: discord.Interaction) -> None:
        cfg = await self.guard(interaction)
        if cfg is None:
            return
        await self.settle_income(interaction.guild_id, owner_ids={interaction.user.id})
        holdings = countries.owned(self.doc(interaction.guild_id), interaction.user.id)
        if not holdings:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(
                    "You rule no countries. Browse `/country atlas`, then make one peaceful `/country claim`."
                ),
                ephemeral=True,
            )
            return
        u = self.user(interaction.guild_id, interaction.user.id)
        lines, total_gdp = ([], 0)
        growth_pct = int(cfg.get("economy.country_development_gdp_pct", 3))
        current = countries.now()
        for index, (c, t) in enumerate(holdings):
            gdp = countries.effective_gdp_b(c, t, growth_pct)
            total_gdp += gdp
            gross = countries.gross_per_day(c, index, t, growth_pct)
            upkeep = countries.upkeep_per_day(c, plushie_perk(u, "country_upkeep"), t)
            daily_net = max(0, gross - upkeep)
            reconstruction = countries.parse_time(t.get("reconstruction_until"))
            if reconstruction is not None and reconstruction > current:
                income = f"+🍩 {ui.format_donuts(int(daily_net * 0.4))}/day during reconstruction until <t:{int(reconstruction.timestamp())}:R> · normal +🍩 {ui.format_donuts(daily_net)}/day"
            else:
                income = f"+🍩 {ui.format_donuts(daily_net)}/day net"
            level = countries.development_level(t)
            armored = ""
            assigned = self._active_armored_garrison(
                interaction.guild_id, interaction.user.id, c.id, require_ammo=False
            )
            if assigned is not None:
                model, tank = assigned
                rounds = max(0, int(tank.get("ammo", 0)))
                cap = int(cfg.get(f"economy.vehicle_{model}_ammo_cap", 6))
                armored = f" · {LABELS[model]} garrison {rounds}/{cap}{(' active' if rounds else ' empty')}"
            breach_until = countries.parse_time(t.get("m1a2_breached_until"))
            if breach_until is not None and breach_until > current:
                armored += f" · breached <t:{int(breach_until.timestamp())}:R>"
            lines.append(
                (
                    f"{c.flag} {c.name}",
                    "\n".join(
                        [
                            status_ui.line("Prestige GDP", f"${gdp:,}B"),
                            status_ui.line("Development", level),
                            status_ui.line("Wallet income", income),
                            status_ui.line(
                                "Production rate", f"{int(countries.production_multiplier(index) * 100)}%"
                            ),
                        ]
                    )
                    + ("\n" + status_ui.line("Defence", armored.lstrip(" ·")) if armored else ""),
                )
            )
        lines.append(
            (
                "➡️ Next action",
                "Use `/country develop` for GDP, `/country reinforce` for defence and `/country guide` for instructions.",
            )
        )
        pages = status_ui.report(
            f"👑 {interaction.user.display_name}'s Realm",
            [("Territories", lines)],
            color=cfg.color,
            checked_at=current,
            footer=f"{len(holdings)} territories · ${total_gdp:,}B combined prestige GDP · Income auto-delivered hourly",
        )
        view = status_ui.pager(pages, interaction.user.id)
        kwargs: Dict[str, Any] = {"embed": pages[0], "ephemeral": True}
        if view is not None:
            kwargs["view"] = view
        await ui.respond(interaction, **kwargs)

    @country.command(
        name="develop", description="Invest in a country to permanently grow its GDP and production."
    )
    @app_commands.autocomplete(country=country_autocomplete)
    @app_commands.describe(
        country="A country you rule", levels="Buy 1–5 levels, or use half/all based on available funds"
    )
    @app_commands.choices(
        levels=[
            app_commands.Choice(name="1 level", value="1"),
            app_commands.Choice(name="2 levels", value="2"),
            app_commands.Choice(name="3 levels", value="3"),
            app_commands.Choice(name="4 levels", value="4"),
            app_commands.Choice(name="5 levels", value="5"),
            app_commands.Choice(name="Half — spend up to half", value="half"),
            app_commands.Choice(name="Full / all — buy maximum affordable", value="all"),
        ]
    )
    async def develop(self, interaction: discord.Interaction, country: str, levels: str = "1") -> None:
        cfg = await self.guard(interaction)
        if cfg is None:
            return
        gid, uid, doc = (interaction.guild_id, interaction.user.id, self.doc(interaction.guild_id))
        c = self.resolve(country)
        territory = countries.state(doc)["territories"].get(c.id) if c else None
        if not c or not isinstance(territory, dict) or int(territory.get("owner", 0)) != uid:
            await ui.respond(
                interaction,
                embed=ui.error_embed("You can develop only a country you personally rule."),
                ephemeral=True,
            )
            return
        max_level = countries.development_cap(
            territory, int(cfg.get("economy.country_development_max_level", 20))
        )
        current_level = countries.development_level(territory)
        if current_level >= max_level:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(
                    f"{c.flag} **{c.name}** is already fully developed at level {max_level}."
                ),
                ephemeral=True,
            )
            return
        base_cost_pct = int(cfg.get("economy.country_development_base_cost_pct", 5))
        cost_growth_pct = int(cfg.get("economy.country_development_cost_growth_pct", 10))
        u = self.user(gid, uid)
        holdings = countries.owned(doc, uid)
        index = next((i for i, (held, _) in enumerate(holdings) if held.id == c.id))
        current = countries.now()
        accrued = (await self.settle_income(gid, current, {uid})).get(uid, 0)
        latest = countries.state(doc)["territories"].get(c.id)
        if (
            latest is not territory
            or int(latest.get("owner", 0)) != uid
            or countries.development_level(latest) != current_level
        ):
            await ui.respond(
                interaction,
                embed=ui.warn_embed(
                    "That country changed while its income was settling. Nothing was charged; run the command again."
                ),
                ephemeral=True,
            )
            return
        growth_pct = int(cfg.get("economy.country_development_gdp_pct", 3))
        available = self.net(u)
        raw_levels = str(levels).strip().lower()
        most_at_once = min(5, max_level - current_level)
        if raw_levels in {"all", "full", "max"}:
            budget, allocation = (available, "all available funds")
            buying = 0
        elif raw_levels in {"half", "h"}:
            budget, allocation = (available // 2, "half your available funds")
            buying = 0
        else:
            try:
                requested = int(raw_levels)
            except (TypeError, ValueError):
                await ui.respond(
                    interaction,
                    embed=ui.error_embed("Choose 1–5 development levels, `half`, or `all`."),
                    ephemeral=True,
                )
                return
            if requested < 1 or requested > 5:
                await ui.respond(
                    interaction,
                    embed=ui.error_embed("You may buy only 1–5 development levels at once."),
                    ephemeral=True,
                )
                return
            buying = min(requested, max_level - current_level)
            budget, allocation = (available, "your available funds")
        if buying == 0:
            for candidate in range(1, most_at_once + 1):
                candidate_cost = countries.development_cost(
                    c,
                    current_level,
                    candidate,
                    base_cost_pct=base_cost_pct,
                    growth_pct=cost_growth_pct,
                    territory=territory,
                )
                if candidate_cost <= budget:
                    buying = candidate
                else:
                    break
            if buying == 0:
                next_cost = countries.development_cost(
                    c,
                    current_level,
                    1,
                    base_cost_pct=base_cost_pct,
                    growth_pct=cost_growth_pct,
                    territory=territory,
                )
                await ui.respond(
                    interaction,
                    embed=ui.error_embed(
                        f"Using {allocation} cannot cover the next development level, which costs 🍩 **{ui.format_donuts(next_cost)}**."
                    ),
                    ephemeral=True,
                )
                return
        cost = countries.development_cost(
            c,
            current_level,
            buying,
            base_cost_pct=base_cost_pct,
            growth_pct=cost_growth_pct,
            territory=territory,
        )
        if available < cost:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"Development level {current_level + 1}"
                    + (f"–{current_level + buying}" if buying > 1 else "")
                    + f" costs 🍩 **{ui.format_donuts(cost)}**."
                ),
                ephemeral=True,
            )
            return
        self.take(u, cost)
        new_level = current_level + buying
        territory["development_level"] = new_level
        countries.append_history(doc, f"{uid} developed {c.name} to level {new_level}")
        await self.econ.save(gid)
        await self.bot.ledger.record(gid, uid, -cost, "country-development", after=self.net(u))
        new_gdp = countries.effective_gdp_b(c, territory, growth_pct)
        next_cost = (
            None
            if new_level >= max_level
            else countries.development_cost(
                c, new_level, 1, base_cost_pct=base_cost_pct, growth_pct=cost_growth_pct, territory=territory
            )
        )
        detail = f"**Level:** {current_level} → **{new_level}/{max_level}**\n**GDP:** ${countries.base_gdp_b(c, territory):,}B base → **${new_gdp:,}B**\n**Investment burned:** 🍩 **{ui.format_donuts(cost)}**"
        if countries.has_growth_path(territory):
            path_progress = countries.development_progress(territory) * 100
            daily_net = max(
                0,
                countries.gross_per_day(c, index, territory, growth_pct)
                - countries.upkeep_per_day(c, plushie_perk(u, "country_upkeep"), territory),
            )
            detail += f"\n**Real-GDP target:** ${float(territory['growth_target_gdp_b']):,.0f}B\n**Path progress:** {path_progress:.4f}%\n**Current net production:** 🍩 **{ui.format_donuts(daily_net)}/day**"
        else:
            detail += f"\n**Production boost:** +{new_level * growth_pct}% from development"
        if accrued:
            detail += f"\n**Automatic income delivered before upgrade:** 🍩 **{ui.format_donuts(accrued)}**"
        if next_cost is not None:
            detail += f"\n**Next level:** 🍩 **{ui.format_donuts(next_cost)}**"
        await ui.respond(
            interaction,
            embed=ui.base_embed(title=f"🏗️ {c.flag} {c.name} developed", description=detail, color=cfg.color),
            ephemeral=True,
        )

    @country.command(
        name="reinforce", description="Burn donuts strengthening your or an ally's country defenses."
    )
    @app_commands.autocomplete(country=country_autocomplete)
    @app_commands.describe(amount="Amount: 25k/2.5m/1b, half, or all; useful defense cap applies")
    async def reinforce(
        self,
        interaction: discord.Interaction,
        country: str,
        amount: app_commands.Transform[int, SpendTransformer],
    ) -> None:
        cfg = await self.guard(interaction)
        if cfg is None:
            return
        gid, uid, doc = (interaction.guild_id, interaction.user.id, self.doc(interaction.guild_id))
        c = self.resolve(country)
        t = countries.state(doc)["territories"].get(c.id) if c else None
        if not c or not isinstance(t, dict):
            await ui.respond(interaction, embed=ui.error_embed("That country is unclaimed."), ephemeral=True)
            return
        owner = int(t.get("owner", 0))
        if owner != uid and (not coalitions.are_allied(doc, uid, owner)):
            await ui.respond(
                interaction,
                embed=ui.error_embed("You may reinforce only your territory or a coalition ally's."),
                ephemeral=True,
            )
            return
        u = self.user(gid, uid)
        invested = max(0, int(t.get("fortification", 0)))
        remaining = max(0, FORTIFICATION_MAX_USEFUL - invested)
        if remaining == 0:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(
                    f"{c.flag} **{c.name}** already has the maximum useful fortification. No donuts were charged."
                ),
                ephemeral=True,
            )
            return
        requested = amount
        if amount < FORTIFICATION_MIN_SPEND and amount < remaining:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    "Country reinforcement requires at least 🍩 **1,000,000**, except for the final exact top-up."
                ),
                ephemeral=True,
            )
            return
        amount = min(amount, remaining)
        if self.net(u) < amount:
            await ui.respond(
                interaction, embed=ui.error_embed("You cannot fund that reinforcement."), ephemeral=True
            )
            return
        self.take(u, amount)
        t["fortification"] = invested + amount
        await self.econ.save(gid)
        await self.bot.ledger.record(gid, uid, -amount, "country-reinforce", after=self.net(u))
        guardrail = (
            f"\nRequested 🍩 **{ui.format_donuts(requested)}**; the useful-defense limit reduced the charge to 🍩 **{ui.format_donuts(amount)}**."
            if requested > amount
            else ""
        )
        await ui.respond(
            interaction,
            embed=ui.ok_embed(
                f"{c.flag} **{c.name}** received 🍩 **{ui.format_donuts(amount)}** in permanent fortifications.{guardrail}"
            ),
        )

    @country.command(
        name="invade",
        description="Invade a claimed or unclaimed country with a loaded vehicle and war chest.",
    )
    @app_commands.autocomplete(country=country_autocomplete)
    @app_commands.choices(
        vehicle=[app_commands.Choice(name=label, value=model) for model, label in LABELS.items()]
    )
    @app_commands.describe(war_chest="War chest: 25k/2.5m/1b, half, or all; useful combat cap applies")
    async def invade(
        self,
        interaction: discord.Interaction,
        country: str,
        vehicle: app_commands.Choice[str],
        war_chest: app_commands.Transform[int, SpendTransformer],
    ) -> None:
        cfg = await self.guard(interaction)
        if cfg is None:
            return
        gid, uid, doc = (interaction.guild_id, interaction.user.id, self.doc(interaction.guild_id))
        c = self.resolve(country)
        if c is None:
            await ui.respond(
                interaction,
                embed=ui.error_embed("Unknown country. Choose one from autocomplete."),
                ephemeral=True,
            )
            return
        territories = countries.state(doc)["territories"]
        stored_territory = territories.get(c.id)
        t = stored_territory if isinstance(stored_territory, dict) else None
        defender: Optional[int] = int(t.get("owner", 0)) if t is not None else None
        model = vehicle.value
        if defender is not None and (defender == uid or coalitions.are_allied(doc, uid, defender)):
            await ui.respond(
                interaction,
                embed=ui.error_embed("You cannot invade yourself or a coalition ally."),
                ephemeral=True,
            )
            return
        territory_value = countries.economic_value(c, t)
        minimum = max(1000000, territory_value // 20)
        requested_war_chest = war_chest
        maximum_useful = max(minimum, useful_war_chest_cap(territory_value))
        attacker = self.user(gid, uid)
        lockdown = countries.parse_time(attacker.get("strategic_lockdown_until"))
        if lockdown is not None and lockdown > countries.now():
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"Your strategic runway and operations hub are disabled by a B-52 strike until <t:{int(lockdown.timestamp())}:R>. Country invasions cannot launch."
                ),
                ephemeral=True,
            )
            return
        if lockdown is not None:
            attacker["strategic_lockdown_until"] = None
        campaign_ready = countries.parse_time(attacker.get("country_invasion_until"))
        if campaign_ready is not None and campaign_ready > countries.now():
            await ui.respond(
                interaction,
                embed=ui.warn_embed(
                    f"Your country campaign command is reorganizing until <t:{int(campaign_ready.timestamp())}:R>."
                ),
                ephemeral=True,
            )
            return
        if campaign_ready is not None:
            attacker["country_invasion_until"] = None
        if war_chest < minimum:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"This invasion requires an affordable war chest of at least 🍩 **{ui.format_donuts(minimum)}**."
                ),
                ephemeral=True,
            )
            return
        war_chest = min(war_chest, maximum_useful)
        if self.net(attacker) < war_chest:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"This invasion requires an affordable war chest of at least 🍩 **{ui.format_donuts(minimum)}**."
                ),
                ephemeral=True,
            )
            return
        fleet = attacker.get("vehicles", {}) if isinstance(attacker.get("vehicles"), dict) else {}
        weapon = fleet.get(model, {}) if isinstance(fleet.get(model), dict) else {}
        economy_cog = self.economy_cog()
        if economy_cog is not None:
            weapon = economy_cog._vehicle_settle(attacker, model)
        ready = bool(weapon.get("owned")) and (
            bool(weapon.get("armed")) if model == "b2" else int(weapon.get("ammo", 0)) > 0
        )
        if model in {"f22", "f35", "j20", "su57"}:
            patrol_until = countries.parse_time(weapon.get("patrol_until"))
            if (
                weapon.get("jet_damaged")
                or weapon.get("jet_repair_until")
                or weapon.get("loading_until")
                or (patrol_until and patrol_until > countries.now())
            ):
                ready = False
        if model in ARMORED_MODELS and (
            weapon.get(f"{model}_damaged")
            or countries.parse_time(weapon.get(f"{model}_repairing_until")) is not None
            or countries.parse_time(weapon.get("garrison_transfer_until")) is not None
            or weapon.get("garrison_country")
        ):
            ready = False
        if not ready:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"Your {LABELS[model]} is not operational and loaded."),
                ephemeral=True,
            )
            return
        campaign_started = countries.now()
        campaign_ready = campaign_started + dt.timedelta(minutes=invasion_cooldown_minutes(cfg))
        attacker["country_invasion_until"] = campaign_ready.isoformat()
        self.take(attacker, war_chest)
        if model == "b2":
            weapon["armed"] = False
        elif random.randint(1, 100) > plushie_perk(attacker, "ammo_preserve"):
            weapon["ammo"] = max(0, int(weapon.get("ammo", 0)) - 1)
        breach_bonus = 0
        breach_until = countries.parse_time(t.get("m1a2_breached_until")) if t is not None else None
        if (
            t is not None
            and breach_until is not None
            and (breach_until > campaign_started)
            and (int(t.get("m1a2_breached_by", 0) or 0) == uid)
        ):
            breach_model = str(t.get("armored_breach_model") or "m1a2")
            if breach_model not in ARMORED_MODELS:
                breach_model = "m1a2"
            breach_bonus = int(cfg.get(f"economy.vehicle_{breach_model}_breach_attack_bonus", 20))
        attack = (
            OFFENSE[model] + min(75, int(math.sqrt(war_chest / max(1, territory_value)) * 100)) + breach_bonus
        )
        defense = countries.TIER_DEFENSE[countries.territory_tier(c, t)]
        defender_garrison: Optional[Tuple[str, Dict[str, Any]]] = None
        if defender is not None and t is not None:
            defender_garrison = self._active_armored_garrison(gid, defender, c.id)
            defense += self.defense_power(gid, defender, t, c.id)
            if defender_garrison is not None:
                _, garrison_state = defender_garrison
                garrison_state["ammo"] = max(0, int(garrison_state.get("ammo", 0)) - 1)
            allied = coalitions.coalition_for(doc, defender)
            if allied:
                for ally in [int(x) for x in allied[1].get("members", []) if int(x) != defender]:
                    defense += min(15, self.defense_power(gid, ally, {}) // 4)
        public_chance = public_invasion_chance(attack, defense)
        resolution_chance = public_chance
        won = random.randint(1, 100) <= resolution_chance
        defender_income = 0
        if won:
            current = countries.now()
            if defender is None:
                t = {
                    "owner": uid,
                    "acquired_at": current.isoformat(),
                    "last_collected_at": current.isoformat(),
                    "method": "military occupation",
                    "fortification": 0,
                    "development_level": 0,
                    "reconstruction_until": (current + dt.timedelta(hours=24)).isoformat(),
                }
                territories[c.id] = t
                countries.append_history(doc, f"{uid} occupied unclaimed {c.name}")
                outcome = f"**VICTORY.** {interaction.user.mention} occupied unclaimed {c.flag} **{c.name}**. It begins at development level 0 with no fortification and produces at 40% during 24-hour reconstruction."
            else:
                defender_income, _ = self._settle_owner_income(gid, defender, current)
                old_ruler = self.owner_name(interaction.guild, defender)
                defender_user = self.user(gid, defender)
                defender_fleet = (
                    defender_user.get("vehicles", {})
                    if isinstance(defender_user.get("vehicles"), dict)
                    else {}
                )
                for tank_model in ARMORED_MODELS:
                    retreating_tank = (
                        defender_fleet.get(tank_model, {})
                        if isinstance(defender_fleet.get(tank_model), dict)
                        else {}
                    )
                    if economy_cog is not None:
                        retreating_tank = economy_cog._vehicle_settle(defender_user, tank_model)
                    if (
                        str(retreating_tank.get("garrison_country") or "") == c.id
                        or str(retreating_tank.get("garrison_transfer_target") or "") == c.id
                    ):
                        retreating_tank["garrison_country"] = None
                        retreating_tank["garrison_transfer_until"] = None
                        retreating_tank["garrison_transfer_target"] = None
                t.update(
                    {
                        "owner": uid,
                        "acquired_at": current.isoformat(),
                        "last_collected_at": current.isoformat(),
                        "method": "conquest",
                        "fortification": int(t.get("fortification", 0)) // 3,
                        "reconstruction_until": (current + dt.timedelta(hours=24)).isoformat(),
                        "m1a2_breached_by": None,
                        "m1a2_breached_until": None,
                        "armored_breach_model": None,
                    }
                )
                countries.append_history(doc, f"{uid} conquered {c.name} from {defender}")
                outcome = f"**VICTORY.** {interaction.user.mention} seized {c.flag} **{c.name}** from **{old_ruler}**. Production runs at 40% during 24-hour reconstruction."
            if len(countries.owned(doc, uid)) >= 3:
                badges.grant(attacker, "world_power")
            color = ui.COLOR_OK
        else:
            if defender is None:
                countries.append_history(doc, f"{uid} failed to occupy unclaimed {c.name}")
                outcome = f"**INVASION REPELLED.** Local forces kept {c.flag} **{c.name}** unclaimed. The attacking war chest and ammunition are gone."
            else:
                old_ruler = self.owner_name(interaction.guild, defender)
                countries.append_history(doc, f"{uid} failed to invade {c.name} held by {defender}")
                outcome = f"**INVASION REPELLED.** **{old_ruler}** retains {c.flag} **{c.name}**. The attacking war chest and ammunition are gone."
            color = ui.COLOR_BAD
        await self.econ.save(gid)
        if defender_income and defender is not None:
            defender_user = self.user(gid, defender)
            await self.bot.ledger.record(
                gid, defender, defender_income, "country-production", after=self.net(defender_user)
            )
        if won and defender is not None:
            await self.bot.ledger.record(
                gid,
                defender,
                0,
                "country-conquest",
                other=uid,
                actor=uid,
                detail=f"{LABELS[model]} · {c.name}",
            )
        await self.bot.ledger.record(gid, uid, -war_chest, "country-invasion", after=self.net(attacker))
        embed = ui.base_embed(title=f"⚔️ Battle for {c.name}", description=outcome, color=color)
        embed.add_field(
            name="Forces", value=f"{LABELS[model]} · 🍩 {ui.format_donuts(war_chest)} war chest", inline=False
        )
        embed.add_field(
            name="Resolution",
            value=f"Attack {attack} vs defense {defense} · success chance **{public_chance}%**",
            inline=False,
        )
        tactical_notes: List[str] = []
        if breach_bonus:
            tactical_notes.append(f"Armored breach contributed **+{breach_bonus} attack power**.")
        if defender_garrison is not None:
            garrison_model, _ = defender_garrison
            tactical_notes.append(
                f"Defending {LABELS[garrison_model]} contributed **+{int(cfg.get(f'economy.vehicle_{garrison_model}_garrison_defense', 25))} defense power** and spent one {('M829A4' if garrison_model == 'm1a2' else 'DM63')} round."
            )
        if tactical_notes:
            embed.add_field(name="Armored operations", value="\n".join(tactical_notes), inline=False)
        embed.add_field(
            name="Next country campaign",
            value=f"Command reorganized <t:{int(campaign_ready.timestamp())}:R>. Vehicle mission turnaround was not changed.",
            inline=False,
        )
        if requested_war_chest > war_chest:
            embed.add_field(
                name="Spending guardrail",
                value=f"Requested 🍩 {ui.format_donuts(requested_war_chest)}; automatically limited to 🍩 {ui.format_donuts(war_chest)} because a larger war chest would add no attack power.",
                inline=False,
            )
        await ui.respond(interaction, embed=embed)

    @country.command(name="leaderboard", description="Rank sovereigns by combined prestige GDP.")
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        cfg = await self.guard(interaction)
        if cfg is None:
            return
        totals: Dict[int, list[int]] = {}
        hidden = set()
        growth_pct = int(cfg.get("economy.country_development_gdp_pct", 3))
        for cid, t in countries.state(self.doc(interaction.guild_id))["territories"].items():
            if cid not in countries.BY_ID or not isinstance(t, dict):
                continue
            if nyx.active(self.doc(interaction.guild_id).get("users", {}).get(str(t.get("owner")), {})):
                continue
            uid = int(t.get("owner", 0))
            if uid in hidden:
                continue
            row = totals.setdefault(uid, [0, 0])
            row[0] += countries.effective_gdp_b(countries.BY_ID[cid], t, growth_pct)
            row[1] += 1
        ranked = sorted(totals.items(), key=lambda pair: pair[1][0], reverse=True)[:15]
        interaction.extras["szofie_nyx_public_subjects"] = [uid for uid, _ in ranked]
        lines = [
            f"**{i}.** {self.owner_name(interaction.guild, uid)} · ${vals[0]:,}B · {vals[1]} countr{('y' if vals[1] == 1 else 'ies')}"
            for i, (uid, vals) in enumerate(ranked, 1)
        ]
        await ui.respond(
            interaction,
            embed=ui.base_embed(
                title="🌐 Sovereign GDP Rankings",
                description="\n".join(lines) or "No countries have been claimed.",
                color=cfg.color,
            ),
        )

    @country.command(
        name="abandon", description="Abandon one of your countries; it becomes unclaimed with no refund."
    )
    @app_commands.autocomplete(country=country_autocomplete)
    async def abandon(self, interaction: discord.Interaction, country: str) -> None:
        cfg = await self.guard(interaction)
        if cfg is None:
            return
        c, doc = (self.resolve(country), self.doc(interaction.guild_id))
        territories = countries.state(doc)["territories"]
        t = territories.get(c.id) if c else None
        if not c or not isinstance(t, dict) or int(t.get("owner", 0)) != interaction.user.id:
            await ui.respond(
                interaction, embed=ui.error_embed("You do not rule that country."), ephemeral=True
            )
            return
        accrued, _ = self._settle_owner_income(interaction.guild_id, interaction.user.id, countries.now())
        economy_cog = self.economy_cog()
        user = self.user(interaction.guild_id, interaction.user.id)
        fleet = user.get("vehicles", {}) if isinstance(user.get("vehicles"), dict) else {}
        for model in ARMORED_MODELS:
            tank = fleet.get(model, {}) if isinstance(fleet.get(model), dict) else {}
            if economy_cog is not None:
                tank = economy_cog._vehicle_settle(user, model)
            if (
                str(tank.get("garrison_country") or "") == c.id
                or str(tank.get("garrison_transfer_target") or "") == c.id
            ):
                tank["garrison_country"] = None
                tank["garrison_transfer_until"] = None
                tank["garrison_transfer_target"] = None
        territories.pop(c.id)
        countries.append_history(doc, f"{interaction.user.id} abandoned {c.name}")
        await self.econ.save(interaction.guild_id)
        if accrued:
            u = self.user(interaction.guild_id, interaction.user.id)
            await self.bot.ledger.record(
                interaction.guild_id, interaction.user.id, accrued, "country-production", after=self.net(u)
            )
        income_note = (
            f" Pending country income of 🍩 **{ui.format_donuts(accrued)}** was delivered first."
            if accrued
            else ""
        )
        await ui.respond(
            interaction,
            embed=ui.warn_embed(
                f"{c.flag} **{c.name}** is now unclaimed. No purchase or fortification costs were refunded.{income_note}"
            ),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Countries(bot))
