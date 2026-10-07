"""Shared plushie catalog and perk lookup.

Lives in szofie/ (not a cog) so the economy shop AND the game cogs that read
perks — blackjack, fishing — can all import it without cog-to-cog import cycles.

Plushies never stack: each perk sits on exactly one plushie, and `plushie_perk`
returns that plushie's value (it never sums). Owning all of them grants every
distinct perk at once, but no single perk ever doubles.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass(frozen=True)
class Plushie:
    id: str
    name: str
    emoji: str
    price: int
    perk: str
    value: int
    blurb: str


PLUSHIES: List[Plushie] = [
    Plushie(
        "explorer21",
        "Explorer 21",
        "🚀",
        0,
        "cosmetic",
        0,
        "Earned by exploring ten different worlds. Cosmetic only; never sold.",
    ),
    Plushie("sweettooth", "Sweet Tooth 21", "🍬", 500000, "daily", 25, "+25% donuts from /daily"),
    Plushie(
        "labcoat", "Lab Coat 21", "🧪", 200000000, "slots", 7, "+7 percentage points to your slots win chance"
    ),
    Plushie(
        "ace",
        "Ace 21",
        "🃏",
        25000000,
        "blackjack",
        1,
        "Blackjack Jackpot Booster: three 7s pay 12:1 (up from 7:1) and suited blackjacks pay a fat bonus",
    ),
    Plushie(
        "angler", "Angler 21", "🪝", 5000000, "fishing", 15, "+15% on everything you sell from your bucket"
    ),
    Plushie(
        "fortune",
        "Fortune 21",
        "🎡",
        25000000,
        "wheel",
        10,
        "Wheel pass: 10% chance a busted spin refunds your bet straight off the pot (up from 6%)",
    ),
    Plushie(
        "roulette",
        "Roulette 21",
        "🔴",
        25000000,
        "roulette",
        50,
        "La Partage: land on 0 and your even-money bets lose only half, not all",
    ),
    Plushie("overtime", "Overtime 21", "🛠️", 25000000, "work", 25, "+25% donuts from /work"),
    Plushie(
        "professor", "Professor 21", "🎓", 500000, "trivia", 5, "+5 seconds to answer every /trivia question"
    ),
    Plushie(
        "vaultkeeper",
        "Vault Keeper 21",
        "🏦",
        2500000,
        "interest",
        100,
        "Doubles your bank interest daily cap",
    ),
    Plushie("icebox", "Icebox 21", "🧊", 1000000, "rot", 50, "Your fish take 50% longer to spoil"),
    Plushie("majin", "Majin 21", "👿", 50000000, "steal", 20, "+20pp success on steals & bank heists"),
    Plushie(
        "guardian",
        "Guardian 21",
        "🛡️",
        50000000,
        "defense",
        25,
        "25% chance to block a wallet steal or vault robbery",
    ),
    Plushie("golden", "Golden Donut 21", "✨🍩", 75000000, "grab", 10, "+10pp to how much you steal or rob"),
    Plushie("houdini", "Houdini 21", "⛓️", 10000000, "jail", 40, "Jail sentences run 40% shorter for you"),
    Plushie(
        "catburglar",
        "Cat Burglar 21",
        "🕵️",
        5000000,
        "cosmetic",
        0,
        "Legacy collectible; no gameplay perk. No longer sold.",
    ),
    Plushie(
        "slick",
        "Slick 21",
        "🏃",
        50000000,
        "reload",
        25,
        "Your /steal and /robbank cooldowns are 25% shorter",
    ),
    Plushie(
        "warhead",
        "Warhead 21",
        "☢️",
        100000000,
        "icbm_speed",
        25,
        "ICBMs build 25% faster and their launch cooldown is 25% shorter",
    ),
    Plushie("silo", "Silo 21", "🛰️", 150000000, "icbm_stock", 1, "+1 to your ICBM silo cap"),
    Plushie(
        "radar",
        "Radar 21",
        "📡",
        75000000,
        "aa_intercept",
        10,
        "+10pp AA intercept chance — shield and rockets alike",
    ),
    Plushie(
        "autoloader",
        "Autoloader 21",
        "🚀",
        50000000,
        "aa_build",
        40,
        "Interceptor rockets build 40% faster, and your battery load cap is +2",
    ),
    Plushie(
        "scholar",
        "Scholar 21",
        "📚",
        250000000,
        "trivia_guard",
        1,
        "Once per UTC day, one wrong trivia answer will not break your streak",
    ),
    Plushie(
        "dealer",
        "Dealer 21",
        "🎴",
        500000000,
        "casino_rep",
        25,
        "+25% Casino Reputation from every settled game",
    ),
    Plushie(
        "diplomat",
        "Diplomat 21",
        "🤝",
        1000000000,
        "country_upkeep",
        15,
        "Reduces upkeep on every country you rule by 15%",
    ),
    Plushie(
        "cartographer",
        "Cartographer 21",
        "🗺️",
        1500000000,
        "country_capacity",
        25,
        "Country production stores for 60 hours instead of 48",
    ),
    Plushie(
        "mechanic",
        "Mechanic 21",
        "🔩",
        2000000000,
        "ammo_preserve",
        10,
        "10% chance for deployed vehicle ammunition to be preserved",
    ),
    Plushie(
        "admiral",
        "Admiral 21",
        "⚓",
        2000000000,
        "naval_cooldown",
        15,
        "Zumwalt and Virginia mission cooldowns are 15% shorter",
    ),
    Plushie(
        "strategist",
        "Strategist 21",
        "♟️",
        1500000000,
        "recon_duration",
        25,
        "U-2 and Deimos intelligence packages remain valid 25% longer",
    ),
    Plushie(
        "sovereign",
        "Sovereign 21",
        "👑",
        2500000000,
        "country_defense",
        10,
        "+10 defense power to every country you rule",
    ),
    Plushie(
        "perfect",
        "Perfect 21",
        "💟",
        500000000,
        "mythic",
        3,
        "MYTHIC: +3% to all your /daily, /work and fishing income",
    ),
]
PLUSHIE_BY_ID = {p.id: p for p in PLUSHIES}
RETIRED_PLUSHIE_IDS = frozenset({"catburglar"})


def plushie_perk(user: Dict[str, Any], perk: str) -> int:
    """The perk value if the user owns a plushie granting it, else 0."""
    owned = user.get("plushies", {}) or {}
    for p in PLUSHIES:
        if p.perk == perk and owned.get(p.id, 0) > 0:
            return p.value
    return 0
