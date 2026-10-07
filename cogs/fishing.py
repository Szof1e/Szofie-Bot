"""Fishing — a collectible side business alongside the career economy.

Cast for a random catch from weighted surface and Abyss rarity tables. Rods,
enchants, events and configurable multipliers affect catches, sale prices and
cooldowns. Catches pile up in a "bucket"; sell them with /sell or /sellall.

Base fish values and rod prices live in the catalogs below. All balances and
inventory use the shared bot.economy store.
"""

from __future__ import annotations
import datetime as dt
import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import discord
from discord import app_commands
from discord.ext import commands, tasks
from szofie import badges, nyx, ui
from szofie import events as gevents
from szofie import hexes
from szofie import activity_contracts as activities, activity_ui
from szofie.plushies import plushie_perk
from szofie.titles import TITLE_BY_ID


@dataclass(frozen=True)
class Fish:
    id: str
    name: str
    emoji: str
    tier: str
    value: int
    weight: float


TIER_ORDER = ["mythic", "legendary", "epic", "rare", "uncommon", "common", "junk"]
TIER_RANK = {"junk": 0, "common": 1, "uncommon": 2, "rare": 3, "epic": 4, "legendary": 5, "mythic": 6}
TIER_EMOJI = {
    "junk": "🗑️",
    "common": "🐟",
    "uncommon": "🦀",
    "rare": "🦞",
    "epic": "🦈",
    "legendary": "🐋",
    "mythic": "🌀",
}
FISH: List[Fish] = [
    Fish("boot", "Old Boot", "🥾", "junk", 450, 90),
    Fish("can", "Rusty Can", "🥫", "junk", 450, 90),
    Fish("seaweed", "Seaweed", "🌿", "junk", 900, 70),
    Fish("sock", "Wet Sock", "🧦", "junk", 450, 50),
    Fish("sardine", "Sardine", "🐟", "common", 2300, 130),
    Fish("perch", "Perch", "🐠", "common", 3250, 120),
    Fish("shrimp", "Shrimp", "🦐", "common", 2750, 110),
    Fish("clam", "Clam", "🐚", "common", 4150, 90),
    Fish("crab", "Crab", "🦀", "uncommon", 8300, 60),
    Fish("octopus", "Octopus", "🐙", "uncommon", 11100, 50),
    Fish("puffer", "Pufferfish", "🐡", "uncommon", 10200, 45),
    Fish("seal", "Seal", "🦭", "uncommon", 13900, 25),
    Fish("lobster", "Lobster", "🦞", "rare", 32400, 22),
    Fish("squid", "Squid", "🦑", "rare", 46000, 18),
    Fish("gar", "Alligator Gar", "🐊", "rare", 51000, 12),
    Fish("pearl", "Pearl", "🦪", "rare", 74000, 8),
    Fish("shark", "Shark", "🦈", "epic", 130000, 4),
    Fish("turtle", "Ancient Turtle", "🐢", "epic", 120000, 3),
    Fish("chest", "Treasure Chest", "📦", "epic", 185000, 2),
    Fish("whale", "Whale", "🐋", "legendary", 555000, 0.5),
    Fish("leviathan", "Leviathan", "🐳", "legendary", 740000, 0.3),
    Fish("dragon", "Sea Dragon", "🐉", "legendary", 925000, 0.15),
    Fish("koi", "Android 21's Golden Koi", "👑", "legendary", 1110000, 0.05),
    Fish("bluefintuna", "Bluefin Tuna", "🐟", "rare", 70000, 6),
    Fish("giantoarfish", "Giant Oarfish", "🦬", "rare", 110000, 3),
    Fish("megalodontooth", "Megalodon Tooth", "🦷", "epic", 300000, 1.25),
    Fish("androidcapsule", "Sunken Android Capsule", "🧪", "epic", 450000, 0.6),
    Fish("celestialkoi", "Celestial Koi", "🌠", "legendary", 2500000, 0.02),
]
ABYSS_FISH: List[Fish] = [
    Fish("abyssdebris", "Abyssal Debris", "🪨", "junk", 74000, 5400),
    Fish("voidjelly", "Void Jelly", "🫧", "mythic", 740000, 2850),
    Fish("abyssalserpent", "Abyssal Serpent", "🐍", "mythic", 1300000, 1080),
    Fish("ghostleviathan", "Ghost Leviathan", "👻", "mythic", 2220000, 510),
    Fish("stareater", "Star-Eater Ray", "🌌", "mythic", 3700000, 158.4),
    Fish("voidcaller", "Voidcaller", "🌀", "mythic", 37000000, 1.6),
    Fish("hadalangler", "Hadal Angler", "🏮", "mythic", 5000000, 80),
    Fish("riftmanta", "Rift Manta", "🌌", "mythic", 8000000, 25),
    Fish("blacksmoker", "Black-Smoker Relic", "🏺", "mythic", 15000000, 5),
    Fish("worldserpent", "World Serpent", "🐍", "mythic", 100000000, 0.2),
    Fish("mnemosynelantern", "Mnemosyne Lanternfish", "🏮", "mythic", 20000000, 4),
    Fish("eclipseglassfish", "Eclipse Glassfish", "🌘", "mythic", 50000000, 1.6),
    Fish("ouroboroseel", "Ouroboros Eel", "🐍", "mythic", 140000000, 0.6),
    Fish("palecrownoarfish", "Pale-Crown Oarfish", "👑", "mythic", 300000000, 0.2),
    Fish("drownedoracleray", "Drowned Oracle Ray", "👁️", "mythic", 800000000, 0.12),
]
ABYSS_CODEX_IDS = ["voidjelly", "abyssalserpent", "ghostleviathan", "stareater"]
ABYSS_RELIC_IDS = ["hadalangler", "riftmanta", "blacksmoker"]
ABYSS_ESOTERIC_IDS = [
    "mnemosynelantern",
    "eclipseglassfish",
    "ouroboroseel",
    "palecrownoarfish",
    "drownedoracleray",
]
ABYSS_ESOTERIC_LORE = {
    "mnemosynelantern": "Its lantern glows with memories you have not lived.",
    "eclipseglassfish": "A black sun hangs where its transparent heart should be.",
    "ouroboroseel": "It closes its jaws around its tail, and the water folds inward.",
    "palecrownoarfish": "A bone-white crown rides above an oarfish older than any map.",
    "drownedoracleray": "Its single eye seems to remember tomorrow.",
}
FISH_BY_ID = {f.id: f for f in FISH + ABYSS_FISH}
RARE_PLUS_WEIGHT_MULTIPLIER = 2.0
ABYSS_MYTHIC_WEIGHT_MULTIPLIER = 2.0
FISH_SELL_PRICE_BPS = 68750


def base_sell_value(fish: Fish) -> int:
    """Base catalog sale value after the global fish-price adjustment."""
    return fish.value * FISH_SELL_PRICE_BPS // 10000


def abyss_catch_weights() -> List[Tuple[Fish, float]]:
    """The globally tuned Abyss pool used by every dive."""
    return [
        (fish, fish.weight * (ABYSS_MYTHIC_WEIGHT_MULTIPLIER if fish.tier == "mythic" else 1.0))
        for fish in ABYSS_FISH
    ]


def abyss_cast(rng: random.Random) -> Fish:
    """Pull a single catch from the Abyss loot table."""
    pairs = abyss_catch_weights()
    return rng.choices([fish for fish, _ in pairs], weights=[weight for _, weight in pairs])[0]


@dataclass(frozen=True)
class Rod:
    id: str
    name: str
    emoji: str
    price: int
    ability: str
    value: int
    blurb: str


RODS: List[Rod] = [
    Rod(
        "twinline",
        "Twin-Line Rod",
        "🎣",
        5000000,
        "double",
        50,
        "50% chance to reel in two fish at once, and fast 90-second casts.",
    ),
    Rod("lucky", "Lucky Rod", "🍀", 10000000, "rarity", 2, "Doubles your odds of a rare-or-better catch."),
    Rod(
        "merchant",
        "Merchant's Rod",
        "💰",
        25000000,
        "price",
        40,
        "Everything you catch sells for +40%, and fast 90-second casts.",
    ),
    Rod(
        "quickcast",
        "Quickcast Rod",
        "⚡",
        15000000,
        "cooldown",
        60,
        "Cuts the fishing cooldown from 5 minutes to 1.",
    ),
    Rod(
        "abyssal",
        "Abyssal Rod",
        "🌊",
        250000000,
        "abyss",
        5,
        "No junk ever, 5× legendary odds, more epics, and fast 2-minute casts.",
    ),
    Rod(
        "mythicrod",
        "Android 21's Reel",
        "✨🎣",
        2000000000,
        "abyss",
        5,
        "MYTHIC: everything the Abyssal Rod does, with a Keen enchant baked in — blessed by Android 21 herself.",
    ),
]
ROD_BY_ID = {r.id: r for r in RODS}
FISH_LINES = [
    "You cast a line into my tank, morsel. Let's see what bites.",
    "The water stirs... and Android 21 watches, hungry.",
    "Something tugs. You reel, you pray.",
    "A catch! Careful — some things in my waters bite back.",
    "The line goes taut. Reel it in, sweet thing.",
    "You dip your hook into the depths of my lab.",
]
ENCHANTS = {
    "sharp": ("⚔️ Sharp", "+15% sell value"),
    "swift": ("💨 Swift", "-20s cooldown"),
    "keen": ("🍀 Keen", "better rare-or-better odds"),
    "twin": ("🎣 Twin", "+20% chance to catch two"),
}
ENCHANT_BASE_COST = 10000000
VALUABLE_TIER_WEIGHT_MULTIPLIERS = {"rare": 0.92, "epic": 0.88, "legendary": 0.8}


def catch_weights(rod_id: Optional[str], enchants=(), rare_mult: float = 1.0) -> List[Tuple[Fish, float]]:
    """The weighted catch table, adjusted for the equipped rod (and its enchants)."""
    rod = ROD_BY_ID.get(rod_id or "")
    keen = list(enchants).count("keen")
    out: List[Tuple[Fish, float]] = []
    for f in FISH:
        w = f.weight * VALUABLE_TIER_WEIGHT_MULTIPLIERS.get(f.tier, 1.0)
        if TIER_RANK[f.tier] >= TIER_RANK["rare"]:
            w *= RARE_PLUS_WEIGHT_MULTIPLIER
        if rod:
            if rod.ability == "rarity" and TIER_RANK[f.tier] >= TIER_RANK["rare"]:
                w *= rod.value
                if f.tier == "rare":
                    w *= 1.2
            elif rod.ability == "abyss":
                if f.tier == "junk":
                    w = 0.0
                elif f.tier == "legendary":
                    w *= rod.value
                elif f.tier == "epic":
                    w *= 1.5
                elif f.tier == "common":
                    w *= 0.66
        if keen and w > 0 and (TIER_RANK[f.tier] >= TIER_RANK["rare"]):
            w *= 1.3**keen
        if rare_mult != 1.0 and w > 0 and (TIER_RANK[f.tier] >= TIER_RANK["rare"]):
            w *= rare_mult
        if w > 0:
            out.append((f, w))
    return out


def cast(rng: random.Random, rod_id: Optional[str], enchants=(), rare_mult: float = 1.0) -> List[Fish]:
    """Return the fish caught this cast (one, plus extras from a doubling rod / Twin enchants)."""
    pairs = catch_weights(rod_id, enchants, rare_mult)
    population = [f for f, _ in pairs]
    weights = [w for _, w in pairs]
    caught = [rng.choices(population, weights=weights, k=1)[0]]
    rod = ROD_BY_ID.get(rod_id or "")
    if rod and rod.ability == "double" and (rng.randint(1, 100) <= rod.value):
        caught.append(rng.choices(population, weights=weights, k=1)[0])
    for _ in range(list(enchants).count("twin")):
        if rng.randint(1, 100) <= 20:
            caught.append(rng.choices(population, weights=weights, k=1)[0])
    return caught


def effective_cooldown(base_seconds: int, rod_id: Optional[str], enchants=()) -> int:
    rod = ROD_BY_ID.get(rod_id or "")
    cd = base_seconds
    if rod and rod.ability == "cooldown":
        cd = min(cd, rod.value)
    elif rod and rod.ability == "abyss":
        cd = min(cd, 120)
    elif rod and rod.id in ("twinline", "merchant"):
        cd = min(cd, 90)
    swift = list(enchants).count("swift")
    if swift:
        cd = max(30, cd - 20 * swift)
    return cd


def sell_value(fish: Fish, rod_id: Optional[str], enchants=()) -> int:
    rod = ROD_BY_ID.get(rod_id or "")
    price = fish.value
    if rod and rod.ability == "price":
        price += fish.value * rod.value // 100
    price += fish.value * 15 * list(enchants).count("sharp") // 100
    return price * FISH_SELL_PRICE_BPS // 10000


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse(iso: Optional[str]) -> Optional[dt.datetime]:
    if not iso:
        return None
    try:
        return dt.datetime.fromisoformat(iso)
    except ValueError:
        return None


class Fishing(commands.Cog):
    """Cast, catch, sell — and kit out with rods."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.econ = bot.economy

    async def cog_load(self) -> None:
        self.autofish_loop.start()
        self.rot_loop.start()

    async def cog_unload(self) -> None:
        self.autofish_loop.cancel()
        self.rot_loop.cancel()

    def cfg(self, guild_id: int):
        return self.bot.config.for_guild(guild_id)

    def user(self, guild_id: int, user_id: int) -> Dict[str, Any]:
        starting = int(self.cfg(guild_id).get("economy.starting_balance", 100))
        return self.econ.user(guild_id, user_id, starting)

    def emoji(self, cfg) -> str:
        return str(cfg.get("economy.currency_emoji", "🍩"))

    def name(self, cfg) -> str:
        return str(cfg.get("economy.currency_name", "donuts"))

    def money(self, cfg, amount: int) -> str:
        return f"{self.emoji(cfg)} **{ui.format_donuts(amount)}** {self.name(cfg)}"

    async def persist(self, guild_id: int) -> None:
        await self.econ.save(guild_id)

    async def _guard(
        self, interaction: discord.Interaction, *, needs_channel: bool = True, ephemeral: bool = False
    ):
        await ui.defer_response(interaction, ephemeral=ephemeral)
        if interaction.guild_id is None:
            await ui.respond(
                interaction, embed=ui.error_embed("Fishing only works in a server."), ephemeral=True
            )
            return None
        cfg = self.cfg(interaction.guild_id)
        if not cfg.get("economy.enabled", True):
            await ui.respond(
                interaction, embed=ui.error_embed("The economy is turned off here."), ephemeral=True
            )
            return None
        if needs_channel:
            chan = cfg.get("economy.channel")
            if chan and interaction.channel_id != int(chan):
                await ui.respond(
                    interaction, embed=ui.error_embed(f"Fish in <#{int(chan)}>."), ephemeral=True
                )
                return None
        return cfg

    async def _award(self, interaction, member, user, *events) -> None:
        cfg = self.cfg(interaction.guild_id)
        if not cfg.get("achievements.enabled", True):
            return
        channel = interaction.channel if cfg.get("achievements.announce", True) else None
        newly = await badges.award(channel, member, user, cfg.color, *events)
        if newly:
            await self.persist(interaction.guild_id)

    def _stock(self, u: Dict[str, Any], fish_id: str) -> None:
        """Add one caught fish, starting its freshness clock if it's a new stack.
        Topping up an existing stack keeps the old clock (so hoarding still rots)."""
        bucket = u.setdefault("fish", {})
        meta = u.setdefault("fish_meta", {})
        was = int(bucket.get(fish_id, 0))
        bucket[fish_id] = was + 1
        if was <= 0 or not isinstance(meta.get(fish_id), dict):
            meta[fish_id] = {"age": 0.0, "w1": False, "w2": False}
        u.setdefault("fish_seen", {})[fish_id] = True

    def _check_bestiary(self, u: Dict[str, Any]) -> bool:
        """Grant the bestiary reward if it was just completed. Returns True if newly done."""
        if u.get("bestiary_done"):
            return False
        seen = u.get("fish_seen", {}) or {}
        if sum((1 for f in FISH if seen.get(f.id))) < len(FISH):
            return False
        u["bestiary_done"] = True
        u.setdefault("titles", {})["oceansbane"] = 1
        badges.grant(u, "oceans_bane")
        return True

    def _check_abyss_codex(self, u: Dict[str, Any]) -> bool:
        """Grant the Abyss Codex reward (the four regular mythics) if just completed."""
        if u.get("abyss_codex_done"):
            return False
        seen = u.get("abyss_seen", {}) or {}
        if not all((seen.get(fid) for fid in ABYSS_CODEX_IDS)):
            return False
        u["abyss_codex_done"] = True
        u.setdefault("titles", {})["fathomless"] = 1
        badges.grant(u, "abyss_codex")
        return True

    @staticmethod
    def _check_fishing_titles(u: Dict[str, Any]) -> List[str]:
        """Grant the expanded fishing milestones and return newly earned IDs."""
        owned = u.setdefault("titles", {})
        seen = u.get("abyss_seen", {}) or {}
        checks = {
            "masterangler": int(u.get("surface_fish_caught", 0)) >= 10000,
            "hadalexplorer": int(u.get("deep_expeditions", 0)) >= 10,
            "relichunter": all((seen.get(fid) for fid in ABYSS_RELIC_IDS)),
            "onegotaway": bool(seen.get("worldserpent")),
            "fishmogul": int(u.get("fish_sale_lifetime", 0)) >= 10000000000,
        }
        newly: List[str] = []
        for title_id, earned in checks.items():
            if earned and (not owned.get(title_id)):
                owned[title_id] = 1
                newly.append(title_id)
        return newly

    @staticmethod
    def _enchants(u: Dict[str, Any], rod_id: Optional[str], uid: Optional[int] = None) -> List[str]:
        raw = (u.get("rod_enchants", {}) or {}).get(rod_id or "") or []
        ench = [raw] if isinstance(raw, str) else list(raw)
        if rod_id == "mythicrod":
            ench.append("keen")
        return ench

    def _bucket_value(self, user: Dict[str, Any], rod_id: Optional[str], uid: Optional[int] = None) -> int:
        """What the bucket would actually sell for — matches /sell exactly: per-fish
        rod+enchant value, then the plushie/bestiary bonus on the total. (The transient
        Frenzy event bonus is not baked in; it's a surprise on top at sale time.)"""
        total = 0
        ench = self._enchants(user, rod_id, uid)
        for fid, n in (user.get("fish", {}) or {}).items():
            f = FISH_BY_ID.get(fid)
            if f:
                total += sell_value(f, rod_id, ench) * int(n)
        fbonus = (
            plushie_perk(user, "fishing")
            + plushie_perk(user, "mythic")
            + (2 if user.get("bestiary_done") else 0)
        )
        total += total * fbonus // 100
        return total

    def _sale_breakdown(self, cfg, rod_id, ench, raw: int, final: int, fbonus: int, frenzy_pct: int) -> str:
        """Spell out where a sale's bonus came from, so players can see every
        enchant/plushie/event boost was applied (base -> final, itemised)."""
        if raw <= 0:
            return ""
        parts = []
        rod = ROD_BY_ID.get(rod_id or "")
        if rod and rod.ability == "price":
            parts.append(f"{rod.emoji} {rod.name} +{rod.value}%")
        sharp = list(ench).count("sharp")
        if sharp:
            parts.append(f"⚔️ Sharp ×{sharp} +{15 * sharp}%")
        if fbonus:
            parts.append(f"🧸 plushie perks +{fbonus}%")
        if frenzy_pct:
            parts.append(f"🎣 Frenzy +{frenzy_pct}%")
        pct = (final - raw) * 100 // raw
        head = f"📊 Base {self.money(cfg, raw)} → {self.money(cfg, final)} (**+{pct}%**)"
        return head + ("\n" + "  ·  ".join(parts) if parts else "")

    @app_commands.command(name="fish", description="Cast a line for a random catch.")
    async def fish(self, interaction: discord.Interaction) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        u = self.user(interaction.guild_id, interaction.user.id)
        rod_id = u.get("equipped_rod")
        ench = self._enchants(u, rod_id, interaction.user.id)
        base = int(cfg.get("economy.fish_cooldown_seconds", 300))
        cd = effective_cooldown(base, rod_id, ench)
        last = _parse(u.get("fish_at"))
        if last is not None:
            elapsed = (_now() - last).total_seconds()
            if elapsed < cd:
                ready = int(_now().timestamp() + (cd - elapsed))
                await ui.respond(
                    interaction,
                    embed=ui.warn_embed(f"Your line's still out. Cast again <t:{ready}:R>."),
                    ephemeral=True,
                )
                return
        rare_mult = (
            float(cfg.get("events.migration_rare_mult", 2))
            if gevents.is_active(self.econ.store.load(interaction.guild_id), "migration")
            else 1.0
        )
        hexcut = hexes.penalty(u, interaction.user.id)
        if hexcut:
            rare_mult *= 1 - hexcut
        caught = cast(random, rod_id, ench, rare_mult)
        for f in caught:
            self._stock(u, f.id)
        u["surface_fish_caught"] = int(u.get("surface_fish_caught", 0)) + len(caught)
        u["fish_at"] = _now().isoformat()
        bestiary_done = self._check_bestiary(u)
        new_titles = self._check_fishing_titles(u)
        await self.persist(interaction.guild_id)
        lines = "\n".join(
            (
                f"{TIER_EMOJI[f.tier]} {f.emoji} **{f.name}** — worth {self.money(cfg, sell_value(f, rod_id, ench))}"
                for f in caught
            )
        )
        desc = f"*{random.choice(FISH_LINES)}*\n\n{lines}"
        best = max(caught, key=lambda f: TIER_RANK[f.tier])
        color = ui.COLOR_OK if TIER_RANK[best.tier] >= TIER_RANK["rare"] else cfg.color
        embed = ui.base_embed(title="🎣 Fishing", description=desc, color=color)
        if bestiary_done:
            embed.add_field(
                name="🐋 BESTIARY COMPLETE!",
                value="You've caught **every fish in the sea**! Earned the **Ocean's Bane** badge, the **The Devourer's Angler** title (`/titles`), and +2% fish sales forever.",
                inline=False,
            )
        if new_titles:
            embed.add_field(
                name="New fishing title",
                value=" · ".join((TITLE_BY_ID[t].name for t in new_titles if t in TITLE_BY_ID)),
                inline=False,
            )
        rod = ROD_BY_ID.get(rod_id or "")
        embed.set_footer(
            text=f"Rod: {(rod.name if rod else 'bare hands')} · /bestiary tracks your collection"
        )
        view = activity_ui.Entry(self, interaction.user.id, "fish")
        await ui.respond(interaction, embed=embed, view=view)
        view.message = await ui.response_message(interaction)
        events = ["first_catch"]
        if any((f.tier == "legendary" for f in caught)):
            events.append("legendary_angler")
        await self._award(interaction, interaction.user, u, *events)

    @app_commands.command(
        name="dive", description="Brave the Abyss for mythic catches — needs the Abyssal Rod or 21's Reel."
    )
    @app_commands.describe(depth="Standard dive, or a two-draw Deep Expedition")
    @app_commands.choices(
        depth=[
            app_commands.Choice(name="Standard dive", value="standard"),
            app_commands.Choice(name="Deep Expedition", value="deep"),
        ]
    )
    async def dive(
        self, interaction: discord.Interaction, depth: Optional[app_commands.Choice[str]] = None
    ) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        u = self.user(interaction.guild_id, interaction.user.id)
        rod_id = u.get("equipped_rod")
        if rod_id not in ("abyssal", "mythicrod"):
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    "The Abyss only answers the 🌊 **Abyssal Rod** or ✨🎣 **Android 21's Reel**. Equip one with `/rod` first."
                ),
                ephemeral=True,
            )
            return
        cd = int(cfg.get("abyss.cooldown_seconds", 300))
        last = _parse(u.get("dive_at"))
        if last is not None and (_now() - last).total_seconds() < cd:
            ready = int(last.timestamp() + cd)
            await ui.respond(
                interaction,
                embed=ui.warn_embed(f"The Abyss must settle. Dive again <t:{ready}:R>."),
                ephemeral=True,
            )
            return
        deep = bool(depth and depth.value == "deep")
        if deep and (not u.get("abyss_codex_done")):
            await ui.respond(
                interaction,
                embed=ui.error_embed("Complete the regular Abyss Codex before attempting a Deep Expedition."),
                ephemeral=True,
            )
            return
        debris_cost = int(cfg.get("abyss.deep_debris_cost", 2)) if deep else 0
        debris_have = int((u.get("fish", {}) or {}).get("abyssdebris", 0))
        if deep and debris_have < debris_cost:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"A Deep Expedition needs **{debris_cost} Abyssal Debris**; your bucket has {debris_have}."
                ),
                ephemeral=True,
            )
            return
        cost = int(cfg.get("abyss.deep_dive_cost", 5000000) if deep else cfg.get("abyss.dive_cost", 1000000))
        if int(u.get("donuts", 0)) < cost:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"A dive costs {self.money(cfg, cost)} (fuel & risk) — you have {self.money(cfg, u.get('donuts', 0))}. (Withdraw from `/bank` if it's in your vault.)"
                ),
                ephemeral=True,
            )
            return
        u["donuts"] = int(u.get("donuts", 0)) - cost
        u["dive_at"] = _now().isoformat()
        if deep:
            u["fish"]["abyssdebris"] = debris_have - debris_cost
            if u["fish"]["abyssdebris"] <= 0:
                del u["fish"]["abyssdebris"]
                u.get("fish_meta", {}).pop("abyssdebris", None)
            u["deep_expeditions"] = int(u.get("deep_expeditions", 0)) + 1
        f = abyss_cast(random)
        if deep:
            second = abyss_cast(random)
            if base_sell_value(second) > base_sell_value(f):
                f = second
        if gevents.is_active(self.econ.store.load(interaction.guild_id), "abyssal"):
            event_draw = abyss_cast(random)
            if event_draw.value > f.value:
                f = event_draw
        hexcut = hexes.penalty(u, interaction.user.id)
        if hexcut and random.random() < hexcut:
            f2 = abyss_cast(random)
            if TIER_RANK[f2.tier] < TIER_RANK[f.tier]:
                f = f2
        self._stock(u, f.id)
        u.setdefault("abyss_seen", {})[f.id] = True
        void = f.id == "voidcaller"
        if void:
            badges.grant(u, "voidcaller")
            u.setdefault("titles", {})["voidcaller"] = 1
        codex_done = self._check_abyss_codex(u)
        new_titles = self._check_fishing_titles(u)
        await self.persist(interaction.guild_id)
        await self.bot.ledger.record(
            interaction.guild_id,
            interaction.user.id,
            -cost,
            "abyss-dive",
            after=int(u.get("donuts", 0)) + int(u.get("bank", 0)),
        )
        ench = self._enchants(u, rod_id, interaction.user.id)
        worth = sell_value(f, rod_id, ench)
        if void:
            embed = ui.base_embed(
                title="🌀 THE VOIDCALLER!",
                description=f"The deep tears open — you've pulled the **Voidcaller** itself!\n{TIER_EMOJI[f.tier]} {f.emoji} **{f.name}** — worth {self.money(cfg, worth)} in your bucket.\n\nYou earned the 🌀 **Voidcaller** badge & title. A once-in-thousands catch.",
                color=ui.COLOR_OK,
            )
            content = interaction.user.mention
        else:
            esoteric_lore = ABYSS_ESOTERIC_LORE.get(f.id)
            lore_line = f"*{esoteric_lore}*\n\n" if esoteric_lore else ""
            debris_note = f" and {debris_cost} Abyssal Debris consumed" if deep else ""
            embed = ui.base_embed(
                title="🌑 Esoteric Depths"
                if esoteric_lore
                else "🌊 Deep Expedition"
                if deep
                else "🌊 The Abyss",
                description=f"You {('completed a two-draw Deep Expedition' if deep else 'dove into the deep')} ({self.money(cfg, cost)} spent{debris_note})…\n\n{lore_line}{TIER_EMOJI[f.tier]} {f.emoji} **{f.name}** — worth {self.money(cfg, worth)}.",
                color=ui.COLOR_OK if f.tier == "mythic" else cfg.color,
            )
            content = None
        if codex_done:
            embed.add_field(
                name="🌌 ABYSS CODEX COMPLETE!",
                value="You've logged every creature of the Abyss! Earned the **Fathomless** badge and title (`/titles`).",
                inline=False,
            )
        if new_titles:
            embed.add_field(
                name="New fishing title",
                value=" · ".join((TITLE_BY_ID[t].name for t in new_titles if t in TITLE_BY_ID)),
                inline=False,
            )
        embed.set_footer(text="Dive again after the cooldown · sell your haul with /sellall")
        view = activity_ui.Entry(self, interaction.user.id, "fish")
        await ui.respond(interaction, content=content, embed=embed, view=view)
        view.message = await ui.response_message(interaction)

    @app_commands.command(name="bucket", description="See your fishing haul and its value.")
    @app_commands.describe(user="Whose bucket to view (defaults to you)")
    async def bucket(self, interaction: discord.Interaction, user: Optional[discord.Member] = None) -> None:
        nyx.protect_report(interaction, self.econ, (user or interaction.user).id)
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        target = user or interaction.user
        u = self.user(interaction.guild_id, target.id)
        if nyx.hidden(u, interaction.user.id, target.id):
            await ui.respond(interaction, embed=nyx.censored_embed(), ephemeral=True)
            return
        rod_id = u.get("equipped_rod")
        bucket = u.get("fish", {}) or {}
        rot_h = float(cfg.get("economy.fish_rot_hours", 48))
        rot_h = rot_h * (100 + plushie_perk(u, "rot")) // 100
        meta = u.get("fish_meta", {}) or {}

        def fresh_label(fid: str) -> str:
            """Per-stack freshness left, in online-time (empty if rot is off)."""
            if rot_h <= 0:
                return ""
            m = meta.get(fid)
            age = float(m.get("age", 0.0)) if isinstance(m, dict) else 0.0
            rem = max(0.0, rot_h - age)
            if rem <= 1:
                return "  ·  ⏰ **<1h!**"
            if rem < 24:
                return f"  ·  🕒 ~{int(rem)}h"
            d, h = (int(rem // 24), int(rem % 24))
            return f"  ·  🕒 ~{d}d {h}h" if h else f"  ·  🕒 ~{d}d"

        embed = ui.base_embed(title=f"🪣 {target.display_name}'s bucket", color=cfg.color)
        if not any(bucket.values()):
            embed.description = "*Empty. Cast a line with `/fish`.*"
        else:
            for tier in TIER_ORDER:
                items = [
                    f"{FISH_BY_ID[fid].emoji} {FISH_BY_ID[fid].name} ×{n}{fresh_label(fid)}"
                    for fid, n in bucket.items()
                    if n and fid in FISH_BY_ID and (FISH_BY_ID[fid].tier == tier)
                ]
                if items:
                    embed.add_field(
                        name=f"{TIER_EMOJI[tier]} {tier.title()}", value="\n".join(items), inline=False
                    )
            desc = f"Sell value (with your enchant & plushie bonuses): {self.money(cfg, self._bucket_value(u, rod_id, target.id))}"
            if rot_h > 0:
                desc += "\n🕒 = fresh time left (counts down only while I'm online)."
            embed.description = desc
        rod = ROD_BY_ID.get(rod_id or "")
        embed.set_footer(text=f"Equipped rod: {(rod.name if rod else 'none — try /rod')}")
        view = activity_ui.Entry(self, interaction.user.id, "fish")
        await ui.respond(interaction, embed=embed, view=view, ephemeral=user is None or nyx.active(u))
        view.message = await ui.response_message(interaction)

    async def activity_board(self, interaction):
        cfg = await self._guard(interaction, needs_channel=False, ephemeral=True)
        if cfg is None:
            return
        u = self.user(interaction.guild_id, interaction.user.id)
        board = activities.fish_board(u)
        research = activities.research(u)
        lines = [
            "Two deliveries per UTC day. Bucket fish are consumed on submission. Sale prices and plushie perks are unchanged. Research is optional and pays no donuts."
        ]
        options = []
        for i, offer in enumerate(board["offers"]):
            req = ", ".join((f"{n} {FISH_BY_ID[k].name}" for k, n in offer["fish"].items()))
            lines.append(
                f"\n**{offer['name']}** — {ui.format_donuts(offer['reward'])} donuts · {('paid' if offer['paid'] else 'available')}\n{req}"
            )
            if not offer["paid"]:
                options.append((offer["name"], f"deliver:{i}"))
        for key, (label, req) in activities.PROJECTS.items():
            terms = ", ".join((f"{n} {FISH_BY_ID[k].name}" for k, n in req.items()))
            lines.append(f"\n**{label}** — {('complete' if research.get(key) else terms)}")
            if not research.get(key):
                options.append((label, key))
        if research.get("habitat"):
            lines.append(
                "\nHabitat culture: 5 Void Jelly + 2 Abyssal Serpent → 2 local materials; own completed base required; three batches/UTC day."
            )
            options.append(("Produce habitat culture", "culture"))
        archive = research.get("archive", [])
        names = ", ".join((FISH_BY_ID[k].name for k in archive if k in FISH_BY_ID)) or "none"
        lines.append(
            f"\n**Esoteric archive:** {len(archive)}/5 · {names}. Donate each rare trophy once for a permanent record; never required for daily contracts."
        )
        for key in activities.ESOTERIC:
            if key not in archive and int(u.get("fish", {}).get(key, 0)) > 0:
                options.append(("Archive " + FISH_BY_ID[key].name, "archive:" + key))
        await self.persist(interaction.guild_id)
        await activity_ui.show(
            interaction,
            ui.base_embed(title="Fishing contracts & specimen research", description="\n".join(lines)),
            activity_ui.Board(self, interaction.user.id, options, board["id"]),
        )

    async def activity_preview(self, interaction, token, action):
        cfg = await self._guard(interaction, needs_channel=False, ephemeral=True)
        if cfg is None:
            return
        u = self.user(interaction.guild_id, interaction.user.id)
        board = activities.fish_board(u)
        if board["id"] != token:
            await ui.respond(
                interaction, content="Board refreshed. Open a fresh contracts board.", ephemeral=True
            )
            return
        if action.startswith("deliver:"):
            index = int(action.split(":")[1])
            offer = board["offers"][index]
            req = offer["fish"]
            note = f"Pays {ui.format_donuts(offer['reward'])} donuts once."
        elif action in activities.PROJECTS:
            note, req = activities.PROJECTS[action]
        elif action == "culture":
            req = {"voidjelly": 5, "abyssalserpent": 2}
            note = "Produces 2 materials at your current own colony base; maximum three batches per UTC day."
        elif action.startswith("archive:") and action[8:] in activities.ESOTERIC:
            req = {action[8:]: 1}
            note = "Permanent optional archive entry; this trophy is consumed."
        else:
            await ui.respond(interaction, content="Unknown project.", ephemeral=True)
            return
        location = u.get("space", {}).get("location")

        async def commit(click):
            if await self._guard(click, needs_channel=False) is None:
                return
            current = self.user(click.guild_id, click.user.id)
            try:
                if activities.fish_board(current)["id"] != token:
                    raise ValueError("This board refreshed or the account was reset. Open a fresh board.")
                if action.startswith("deliver:"):
                    delta = activities.deliver_fish(current, token, index)
                    message = f"Delivery complete: {ui.format_donuts(delta)} donuts paid to wallet."
                elif action == "culture":
                    if current.get("space", {}).get("location") != location:
                        raise ValueError("You moved. Open a fresh habitat quote.")
                    delta = 0
                    message = activities.habitat(current, self.econ.store.load(click.guild_id))
                else:
                    delta = 0
                    message = activities.donate(current, action)
            except ValueError as error:
                await ui.respond(click, content=str(error), ephemeral=True)
                return
            await self.persist(click.guild_id)
            await self.bot.ledger.record(
                click.guild_id,
                click.user.id,
                delta,
                "fishing-contract" if delta else "specimen-research",
                after=int(current.get("donuts", 0)) + int(current.get("bank", 0)),
                detail=action,
            )
            await ui.respond(click, embed=ui.ok_embed(message), ephemeral=True)

        description = (
            note
            + "\n\nConsumes: "
            + ", ".join(
                (
                    f"{n} {FISH_BY_ID[k].name} (bucket: {int(u.get('fish', {}).get(k, 0))})"
                    for k, n in req.items()
                )
            )
        )
        await activity_ui.show(
            interaction,
            ui.base_embed(title="Confirm specimen submission", description=description),
            activity_ui.Confirm(interaction.user.id, "Submit specimens", commit),
        )

    @app_commands.command(
        name="bestiary", description="Track every fish species you've caught — collect them all!"
    )
    @app_commands.describe(user="Whose bestiary to view (defaults to you)")
    async def bestiary(self, interaction: discord.Interaction, user: Optional[discord.Member] = None) -> None:
        nyx.protect_report(interaction, self.econ, (user or interaction.user).id)
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        target = user or interaction.user
        u = self.user(interaction.guild_id, target.id)
        if nyx.hidden(u, interaction.user.id, target.id):
            await ui.respond(interaction, embed=nyx.censored_embed(), ephemeral=True)
            return
        seen = u.get("fish_seen", {}) or {}
        got = sum((1 for f in FISH if seen.get(f.id)))
        total = len(FISH)
        abyss_seen = u.get("abyss_seen", {}) or {}
        abyss_got = sum((1 for fid in ABYSS_CODEX_IDS if abyss_seen.get(fid)))
        abyss_total = len(ABYSS_CODEX_IDS)
        embed = ui.base_embed(
            title=f"📖 {target.display_name}'s Bestiary",
            color=ui.COLOR_OK if got >= total and abyss_got >= abyss_total else cfg.color,
        )
        if got >= total:
            surface_status = f"🌊 **Surface Bestiary — {got}/{total}: COMPLETE!**\nEvery fish in the sea, caught. You are **Ocean's Bane**."
        else:
            surface_status = f"🌊 **Surface Bestiary — {got}/{total}**\nCatch all **{total}** species to earn the **Ocean's Bane** badge, the **The Devourer's Angler** title, and **+2% fish sales** forever."
        if abyss_got >= abyss_total:
            abyss_status = f"🌌 **Abyss Codex — {abyss_got}/{abyss_total}: COMPLETE!**\nEvery required Abyss creature logged. You are **The Fathomless**."
        else:
            abyss_status = f"🌌 **Abyss Codex — {abyss_got}/{abyss_total}**\nLog the four required mythic creatures with `/dive` to earn the **Fathomless** badge and **The Fathomless** title."
        embed.description = f"{surface_status}\n\n{abyss_status}"
        for tier in TIER_ORDER:
            items = [
                f"{('✅' if seen.get(f.id) else '❓')} {f.emoji} {f.name}" for f in FISH if f.tier == tier
            ]
            if items:
                embed.add_field(
                    name=f"{TIER_EMOJI[tier]} {tier.title()}", value="\n".join(items), inline=True
                )
        codex_items = [FISH_BY_ID[fid] for fid in ABYSS_CODEX_IDS]
        embed.add_field(
            name=f"🌌 Abyss Codex — {abyss_got}/{abyss_total}",
            value="\n".join(
                (f"{('✅' if abyss_seen.get(f.id) else '❓')} {f.emoji} {f.name}" for f in codex_items)
            ),
            inline=False,
        )
        bonus_items = [
            f for f in ABYSS_FISH if f.id not in ABYSS_CODEX_IDS and f.id not in ABYSS_ESOTERIC_IDS
        ]
        embed.add_field(
            name="🔎 Other Abyss Discoveries",
            value="\n".join(
                (f"{('✅' if abyss_seen.get(f.id) else '❓')} {f.emoji} {f.name}" for f in bonus_items)
            )
            + "\n*These discoveries are not required for Codex completion.*",
            inline=False,
        )
        esoteric_items = [FISH_BY_ID[fid] for fid in ABYSS_ESOTERIC_IDS]
        esoteric_got = sum((1 for fish in esoteric_items if abyss_seen.get(fish.id)))
        embed.add_field(
            name=f"🌑 Esoteric Depths — {esoteric_got}/{len(esoteric_items)}",
            value="\n".join(
                (
                    f"{('✅' if abyss_seen.get(fish.id) else '❓')} {fish.emoji} {fish.name} — {self.money(cfg, base_sell_value(fish))}"
                    for fish in esoteric_items
                )
            )
            + "\n*Optional trophies; existing Codex and relic titles are unchanged.*",
            inline=False,
        )
        await ui.respond(interaction, embed=embed, ephemeral=user is None or nyx.active(u))

    async def _fish_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        u = self.user(interaction.guild_id, interaction.user.id)
        bucket = u.get("fish", {}) or {}
        cur = current.lower()
        out = []
        for fid, n in bucket.items():
            f = FISH_BY_ID.get(fid)
            if f and n and (cur in f.name.lower() or cur in fid):
                out.append(app_commands.Choice(name=f"{f.emoji} {f.name} ×{n}", value=fid))
        return out[:25]

    @app_commands.command(name="sell", description="Sell fish from your bucket.")
    @app_commands.describe(fish="Which catch to sell", quantity="How many (blank = all of them)")
    @app_commands.autocomplete(fish=_fish_autocomplete)
    async def sell(
        self,
        interaction: discord.Interaction,
        fish: str,
        quantity: Optional[app_commands.Range[int, 1, 1000000]] = None,
    ) -> None:
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        f = FISH_BY_ID.get(fish.lower().strip())
        if f is None:
            await ui.respond(
                interaction, embed=ui.error_embed("No such catch. Pick one from the list."), ephemeral=True
            )
            return
        u = self.user(interaction.guild_id, interaction.user.id)
        have = int(u.get("fish", {}).get(f.id, 0))
        if have <= 0:
            await ui.respond(
                interaction, embed=ui.error_embed(f"You have no {f.emoji} {f.name} to sell."), ephemeral=True
            )
            return
        qty = min(int(quantity), have) if quantity else have
        rid = u.get("equipped_rod")
        ench = self._enchants(u, rid, interaction.user.id)
        raw = base_sell_value(f) * qty
        earned = sell_value(f, rid, ench) * qty
        _fbonus = (
            plushie_perk(u, "fishing") + plushie_perk(u, "mythic") + (2 if u.get("bestiary_done") else 0)
        )
        earned += earned * _fbonus // 100
        frenzy_pct = 0
        if gevents.is_active(self.econ.store.load(interaction.guild_id), "frenzy"):
            frenzy_pct = int(cfg.get("events.frenzy_sell_bonus", 50))
            earned += earned * frenzy_pct // 100
        u["fish"][f.id] = have - qty
        if u["fish"][f.id] <= 0:
            del u["fish"][f.id]
            u.get("fish_meta", {}).pop(f.id, None)
        u["donuts"] += earned
        u["fish_sale_lifetime"] = int(u.get("fish_sale_lifetime", 0)) + earned
        new_titles = self._check_fishing_titles(u)
        await self.persist(interaction.guild_id)
        await self.bot.ledger.record(
            interaction.guild_id,
            interaction.user.id,
            earned,
            "fish-sell",
            after=int(u.get("donuts", 0)) + int(u.get("bank", 0)),
        )
        note = f"Sold {f.emoji} **{f.name} ×{qty}** for {self.money(cfg, earned)}.\nBalance: {self.money(cfg, u['donuts'])}"
        breakdown = self._sale_breakdown(cfg, rid, ench, raw, earned, _fbonus, frenzy_pct)
        if breakdown:
            note += f"\n\n{breakdown}"
        if new_titles:
            note += "\n\nNew title: " + ", ".join(
                (TITLE_BY_ID[t].name for t in new_titles if t in TITLE_BY_ID)
            )
        await ui.respond(interaction, embed=ui.ok_embed(note, title="💰 Sold"))
        await self._award(interaction, interaction.user, u)

    @app_commands.command(name="sellall", description="Sell your whole bucket at once.")
    @app_commands.describe(keep="Optionally protect your best catches from the sale")
    @app_commands.choices(
        keep=[
            app_commands.Choice(name="Sell everything", value="all"),
            app_commands.Choice(name="Keep rare and above", value="rare"),
            app_commands.Choice(name="Keep legendary only", value="legendary"),
        ]
    )
    async def sellall(
        self, interaction: discord.Interaction, keep: Optional[app_commands.Choice[str]] = None
    ) -> None:
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        u = self.user(interaction.guild_id, interaction.user.id)
        bucket = u.get("fish", {}) or {}
        rod_id = u.get("equipped_rod")
        keep_at = {"rare": TIER_RANK["rare"], "legendary": TIER_RANK["legendary"]}.get(
            keep.value if keep else "all", 99
        )
        ench = self._enchants(u, rod_id, interaction.user.id)
        earned = 0
        raw = 0
        sold_count = 0
        for fid in list(bucket.keys()):
            f = FISH_BY_ID.get(fid)
            n = int(bucket.get(fid, 0))
            if not f or n <= 0 or TIER_RANK[f.tier] >= keep_at:
                continue
            raw += base_sell_value(f) * n
            earned += sell_value(f, rod_id, ench) * n
            sold_count += n
            del bucket[fid]
            u.get("fish_meta", {}).pop(fid, None)
        if sold_count == 0:
            await ui.respond(
                interaction, embed=ui.warn_embed("Nothing to sell (or you kept it all)."), ephemeral=True
            )
            return
        _fbonus = (
            plushie_perk(u, "fishing") + plushie_perk(u, "mythic") + (2 if u.get("bestiary_done") else 0)
        )
        earned += earned * _fbonus // 100
        frenzy_pct = 0
        if gevents.is_active(self.econ.store.load(interaction.guild_id), "frenzy"):
            frenzy_pct = int(cfg.get("events.frenzy_sell_bonus", 50))
            earned += earned * frenzy_pct // 100
        u["donuts"] += earned
        u["fish_sale_lifetime"] = int(u.get("fish_sale_lifetime", 0)) + earned
        new_titles = self._check_fishing_titles(u)
        await self.persist(interaction.guild_id)
        await self.bot.ledger.record(
            interaction.guild_id,
            interaction.user.id,
            earned,
            "fish-sell",
            after=int(u.get("donuts", 0)) + int(u.get("bank", 0)),
        )
        note = f"Sold **{sold_count}** catch(es) for {self.money(cfg, earned)}."
        if keep:
            note += f"\n*Kept: {keep.name.lower()}.*"
        note += f"\nBalance: {self.money(cfg, u['donuts'])}"
        breakdown = self._sale_breakdown(cfg, rod_id, ench, raw, earned, _fbonus, frenzy_pct)
        if breakdown:
            note += f"\n\n{breakdown}"
        if new_titles:
            note += "\n\nNew title: " + ", ".join(
                (TITLE_BY_ID[t].name for t in new_titles if t in TITLE_BY_ID)
            )
        await ui.respond(interaction, embed=ui.ok_embed(note, title="💰 Bucket sold"))
        await self._award(interaction, interaction.user, u)

    async def _owned_rod_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        """Only rods the player owns — this command equips, it doesn't sell."""
        u = self.user(interaction.guild_id, interaction.user.id)
        owned = u.get("rods", {}) or {}
        cur = current.lower()
        return [
            app_commands.Choice(name=f"{r.emoji} {r.name}", value=r.id)
            for r in RODS
            if owned.get(r.id) and (cur in r.name.lower() or cur in r.id)
        ][:25]

    @app_commands.command(
        name="rod", description="Show off your rods & enchants, peek at someone else's, or equip one."
    )
    @app_commands.describe(
        rod="A rod you own, to equip it — leave blank to just show your rods",
        user="Whose rods to show off (defaults to you)",
    )
    @app_commands.autocomplete(rod=_owned_rod_autocomplete)
    async def rod(
        self,
        interaction: discord.Interaction,
        rod: Optional[str] = None,
        user: Optional[discord.Member] = None,
    ) -> None:
        nyx.protect_report(interaction, self.econ, (user or interaction.user).id)
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        target = user or interaction.user
        is_self = target.id == interaction.user.id
        tu = self.user(interaction.guild_id, target.id)
        if nyx.hidden(tu, interaction.user.id, target.id):
            await ui.respond(interaction, embed=nyx.censored_embed(), ephemeral=True)
            return
        owned = tu.get("rods", {}) or {}
        if rod is None or not is_self:
            title = "🎣 Your rods" if is_self else f"🎣 {target.display_name}'s rods"
            embed = ui.base_embed(title=title, color=cfg.color)
            if not any(owned.values()):
                embed.description = (
                    "You don't own any rods yet. Buy one from `/shop`."
                    if is_self
                    else f"{target.display_name} doesn't own any rods yet."
                )
            else:
                equipped = tu.get("equipped_rod")
                ench_map = tu.get("rod_enchants", {}) or {}

                def _esummary(rid: str) -> str:
                    lst = ench_map.get(rid)
                    if not lst:
                        return ""
                    if isinstance(lst, str):
                        lst = [lst]
                    counts: Dict[str, int] = {}
                    for e in lst:
                        counts[e] = counts.get(e, 0) + 1
                    parts = [
                        ENCHANTS[e][0] + (f" ×{n}" if n > 1 else "")
                        for e, n in counts.items()
                        if e in ENCHANTS
                    ]
                    return "  ·  " + ", ".join(parts) if parts else ""

                embed.description = "\n".join(
                    (
                        f"{r.emoji} **{r.name}**"
                        + ("  ✅ *equipped*" if r.id == equipped else "")
                        + f" — {r.blurb}"
                        + _esummary(r.id)
                        for r in RODS
                        if owned.get(r.id)
                    )
                )
                if is_self:
                    embed.set_footer(text="Equip with /rod <name> · enchant with /enchant · buy in /shop")
            await ui.respond(interaction, embed=embed, ephemeral=nyx.active(tu))
            return
        r = ROD_BY_ID.get(rod.lower().strip())
        if r is None:
            await ui.respond(interaction, embed=ui.error_embed("No such rod. See `/shop`."), ephemeral=True)
            return
        if not owned.get(r.id):
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"You don't own {r.emoji} **{r.name}**. Buy it from `/shop`."),
                ephemeral=True,
            )
            return
        tu["equipped_rod"] = r.id
        await self.persist(interaction.guild_id)
        await ui.respond(interaction, embed=ui.ok_embed(f"Equipped {r.emoji} **{r.name}**.\n{r.blurb}"))

    @app_commands.command(name="enchants", description="Browse every rod enchant and what it does.")
    async def enchants(self, interaction: discord.Interaction) -> None:
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        cap = int(cfg.get("economy.enchant_max_per_type", 5))
        details = {
            "sharp": f"**+15% sell value** per stack — additive, so {cap}× = +{15 * cap}% value.",
            "swift": "**−20s cooldown** per stack (floored at 30s) — cast more often.",
            "keen": f"**×1.3 rare-or-better odds** per stack — multiplies, so {cap}× ≈ ×{round(1.3**cap, 1)}.",
            "twin": "**+20% chance to catch a second fish** per stack — each rolls on its own.",
        }
        embed = ui.base_embed(
            title="✨ Rod Enchants",
            description=f"Bolt abilities onto any rod you own with `/enchant`. Stack up to **{cap} of each type per rod** — mix and match freely.",
            color=cfg.color,
        )
        for k, (label, _) in ENCHANTS.items():
            embed.add_field(name=label, value=details[k], inline=False)
        u = self.user(interaction.guild_id, interaction.user.id)
        owned = sum(
            (len(v if isinstance(v, list) else [v]) for v in (u.get("rod_enchants", {}) or {}).values())
        )
        base = int(cfg.get("economy.enchant_base_cost", ENCHANT_BASE_COST))
        embed.add_field(
            name="💰 Cost",
            value=f"Each enchant costs **{self.money(cfg, base)}** × (enchants you already own + 1) — it climbs the more you own.\nYou own **{owned}**, so your next one costs **{self.money(cfg, base * (owned + 1))}**.",
            inline=False,
        )
        embed.set_footer(text="See your rods' enchants in /balance · apply with /enchant")
        await ui.respond(interaction, embed=embed, ephemeral=True)

    @app_commands.command(
        name="enchant", description="Bolt another ability onto a rod you own (up to 5 of each per rod)."
    )
    @app_commands.describe(rod="A rod you own", enchant="The enchant to apply")
    @app_commands.autocomplete(rod=_owned_rod_autocomplete)
    @app_commands.choices(
        enchant=[
            app_commands.Choice(name=f"{label} — {desc}", value=k) for k, (label, desc) in ENCHANTS.items()
        ]
    )
    async def enchant(
        self, interaction: discord.Interaction, rod: str, enchant: app_commands.Choice[str]
    ) -> None:
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        u = self.user(interaction.guild_id, interaction.user.id)
        r = ROD_BY_ID.get(rod.lower().strip())
        if r is None or not (u.get("rods", {}) or {}).get(r.id):
            await ui.respond(
                interaction,
                embed=ui.error_embed("You don't own that rod. Buy it in `/shop` first."),
                ephemeral=True,
            )
            return
        enchants = u.setdefault("rod_enchants", {})
        for k, v in list(enchants.items()):
            if isinstance(v, str):
                enchants[k] = [v]
        current = enchants.setdefault(r.id, [])
        cap = int(cfg.get("economy.enchant_max_per_type", 5))
        if current.count(enchant.value) >= cap:
            label, _ = ENCHANTS[enchant.value]
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"{r.emoji} **{r.name}** already has the max **{cap}× {label}**. Try a different enchant, or enchant another rod."
                ),
                ephemeral=True,
            )
            return
        owned_total = sum((len(v) for v in enchants.values()))
        cost = int(cfg.get("economy.enchant_base_cost", ENCHANT_BASE_COST)) * (owned_total + 1)
        if u["donuts"] < cost:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"Your #{owned_total + 1} enchant costs {self.money(cfg, cost)}; you have {self.money(cfg, u['donuts'])}. (Withdraw from `/bank` first if it's in your vault.)"
                ),
                ephemeral=True,
            )
            return
        u["donuts"] -= cost
        current.append(enchant.value)
        await self.persist(interaction.guild_id)
        await self.bot.ledger.record(
            interaction.guild_id,
            interaction.user.id,
            -cost,
            "enchant",
            after=int(u.get("donuts", 0)) + int(u.get("bank", 0)),
        )
        label, desc = ENCHANTS[enchant.value]
        await ui.respond(
            interaction,
            embed=ui.ok_embed(
                f"Enchanted {r.emoji} **{r.name}** with **{label}** ({desc}) for {self.money(cfg, cost)}.",
                title="✨ Rod enchanted",
            ),
        )

    @app_commands.command(
        name="autofish",
        description="Cast automatically at your rod's cooldown — catches pile into your bucket.",
    )
    @app_commands.describe(state="Turn the auto-caster on or off (blank toggles it)")
    @app_commands.choices(
        state=[app_commands.Choice(name="on", value="on"), app_commands.Choice(name="off", value="off")]
    )
    async def autofish(
        self, interaction: discord.Interaction, state: Optional[app_commands.Choice[str]] = None
    ) -> None:
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        u = self.user(interaction.guild_id, interaction.user.id)
        want = state.value == "on" if state else not u.get("autofish")
        u["autofish"] = want
        await self.persist(interaction.guild_id)
        rod = ROD_BY_ID.get(u.get("equipped_rod") or "")
        base = int(cfg.get("economy.fish_cooldown_seconds", 300))
        cd = effective_cooldown(
            base, u.get("equipped_rod"), self._enchants(u, u.get("equipped_rod"), interaction.user.id)
        )
        if want:
            desc = f"Casting on its own every **{cd // 60 or 1} min** ({(rod.name if rod else 'bare hands')}). Catches drop straight into your bucket — come back and `/sell` when it's full.\n\n*Runs while I'm online. Turn it off with `/autofish off`.*"
            embed = ui.ok_embed(desc, title="🎣 Auto-fishing on")
        else:
            embed = ui.base_embed(
                title="🎣 Auto-fishing off",
                description="Reeled the line in. Cast manually with `/fish` any time.",
                color=cfg.color,
            )
        await ui.respond(interaction, embed=embed, ephemeral=True)

    @tasks.loop(seconds=30)
    async def autofish_loop(self) -> None:
        """Cast for every opted-in player whose rod cooldown has elapsed. Silent —
        catches fill the bucket, badges grant without announcing, one write per
        guild per tick. Missed time while offline is not retroactively awarded."""
        now = _now()
        seen: set[int] = set()
        for guild in list(self.bot.guilds):
            gid = guild.id
            canonical = self.econ.store.canonical_id(gid)
            if canonical in seen:
                continue
            seen.add(canonical)
            cfg = self.cfg(gid)
            if not cfg.get("economy.enabled", True):
                continue
            base = int(cfg.get("economy.fish_cooldown_seconds", 300))
            announce_on = cfg.get("achievements.enabled", True) and cfg.get("achievements.announce", True)
            rare_mult = (
                float(cfg.get("events.migration_rare_mult", 2))
                if gevents.is_active(self.econ.store.load(gid), "migration")
                else 1.0
            )
            changed = False
            unlocks: List[Tuple[int, List[Any], bool]] = []
            for uid, u in self.econ.all_users(gid).items():
                if not u.get("autofish"):
                    continue
                rod_id = u.get("equipped_rod")
                ench = self._enchants(u, rod_id, int(uid))
                cd = effective_cooldown(base, rod_id, ench)
                last = _parse(u.get("fish_at"))
                if last is not None and (now - last).total_seconds() < cd:
                    continue
                user_rare = rare_mult
                hexcut = hexes.penalty(u, int(uid))
                if hexcut:
                    user_rare *= 1 - hexcut
                caught = cast(random, rod_id, ench, user_rare)
                for f in caught:
                    self._stock(u, f.id)
                u["surface_fish_caught"] = int(u.get("surface_fish_caught", 0)) + len(caught)
                bestiary_done = self._check_bestiary(u)
                self._check_fishing_titles(u)
                u["fish_at"] = now.isoformat()
                newly = [badges.grant(u, "first_catch")]
                if any((f.tier == "legendary" for f in caught)):
                    newly.append(badges.grant(u, "legendary_angler"))
                newly = [b for b in newly if b is not None]
                changed = True
                if announce_on and (newly or bestiary_done):
                    unlocks.append((int(uid), newly, bestiary_done))
            if changed:
                await self.persist(gid)
            for uid, newly, best in unlocks:
                await self._announce_unlocks(guild, cfg, uid, newly, best)

    @autofish_loop.before_loop
    async def _before_autofish(self) -> None:
        await self.bot.wait_until_ready()

    async def _announce_unlocks(self, guild, cfg, uid: int, newly, bestiary_done: bool) -> None:
        """Surface badges an autofisher just earned — the loop grants silently, but a
        one-time unlock (a legendary, the full bestiary) deserves the same fanfare a
        manual /fish gives. Posts to the economy channel, else the system channel."""
        chan_id = cfg.get("economy.channel")
        channel = guild.get_channel(int(chan_id)) if chan_id else None
        if channel is None:
            channel = guild.system_channel
        if channel is None:
            return
        try:
            if bestiary_done:
                embed = ui.base_embed(
                    title="🐋 BESTIARY COMPLETE!",
                    description=f"<@{uid}> auto-fished **every fish in the sea**! Earned the **Ocean's Bane** badge, the **The Devourer's Angler** title (`/titles`), and +2% fish sales forever.",
                    color=ui.COLOR_OK,
                )
                await channel.send(content=f"<@{uid}>", embed=embed)
            for b in newly:
                embed = ui.base_embed(
                    title="🏅 Badge unlocked!",
                    description=f"<@{uid}> earned {b.emoji} **{b.name}**\n*{b.desc}*",
                    color=cfg.color,
                )
                await channel.send(content=f"<@{uid}>", embed=embed)
        except discord.HTTPException:
            pass

    @staticmethod
    def _fish_list(pairs: List[Tuple[str, int]]) -> str:
        return ", ".join(
            (f"{FISH_BY_ID[fid].emoji} {FISH_BY_ID[fid].name} ×{n}" for fid, n in pairs if fid in FISH_BY_ID)
        )[:1000]

    async def _notify_rot(self, guild, cfg, uid: int, w1n, w2n, rotted) -> None:
        return
        chan_id = cfg.get("economy.channel")
        channel = guild.get_channel(int(chan_id)) if chan_id else None
        if channel is None:
            channel = guild.system_channel
        if channel is None:
            return
        lines: List[str] = []
        if rotted:
            lines.append(f"🦨 **Spoiled:** {self._fish_list(rotted)} rotted away.")
        if w2n:
            lines.append(f"⏰ **Last call —** {self._fish_list(w2n)} rots in about **1 hour**!")
        if w1n:
            lines.append(f"🐟 {self._fish_list(w1n)} rots in about **1 day**.")
        if not lines:
            return
        lines.append("Sell with `/sellall` before your catches spoil.")
        embed = ui.base_embed(title="🪣 Bucket check", description="\n".join(lines), color=cfg.color)
        try:
            await channel.send(content=f"<@{uid}>", embed=embed)
        except discord.HTTPException:
            pass

    @tasks.loop(minutes=60)
    async def rot_loop(self) -> None:
        """Age every bucket by one online hour; warn at ~1 day and ~1 hour left, then
        spoil. Ages accumulate only while this loop runs, so downtime never rots fish
        or skips a warning. A stack can only
        rot once its 1-hour warning has actually gone out."""
        tick = 1.0
        seen: set[int] = set()
        for guild in list(self.bot.guilds):
            gid = guild.id
            canonical = self.econ.store.canonical_id(gid)
            if canonical in seen:
                continue
            seen.add(canonical)
            cfg = self.cfg(gid)
            rot_h = float(cfg.get("economy.fish_rot_hours", 48))
            if rot_h <= 0 or not cfg.get("economy.enabled", True):
                continue
            changed = False
            notices: List[Tuple[int, list, list, list]] = []
            for uid, u in self.econ.all_users(gid).items():
                bucket = u.get("fish", {}) or {}
                if not any(bucket.values()):
                    continue
                meta = u.setdefault("fish_meta", {})
                rot_hu = rot_h * (100 + plushie_perk(u, "rot")) // 100
                warn1_u, warn2_u = (max(0.0, rot_hu - 24.0), max(0.0, rot_hu - 1.0))
                w1n, w2n, rotted = ([], [], [])
                for fid in list(bucket.keys()):
                    n = int(bucket.get(fid, 0))
                    if n <= 0:
                        meta.pop(fid, None)
                        continue
                    m = meta.get(fid)
                    if not isinstance(m, dict):
                        m = {"age": 0.0, "w1": False, "w2": False}
                    meta[fid] = m
                    m["age"] = float(m.get("age", 0.0)) + tick
                    changed = True
                    if m["age"] >= rot_hu and m.get("w2"):
                        rotted.append((fid, n))
                        del bucket[fid]
                        meta.pop(fid, None)
                    elif m["age"] >= warn2_u and (not m.get("w2")):
                        m["w1"] = True
                        m["w2"] = True
                        w2n.append((fid, n))
                    elif m["age"] >= warn1_u and (not m.get("w1")):
                        m["w1"] = True
                        w1n.append((fid, n))
                if w1n or w2n or rotted:
                    notices.append((int(uid), w1n, w2n, rotted))
            if changed:
                await self.persist(gid)
            for uid, w1n, w2n, rotted in notices:
                await self._notify_rot(guild, cfg, uid, w1n, w2n, rotted)

    @rot_loop.before_loop
    async def _before_rot(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Fishing(bot))
