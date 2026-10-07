"""Shared cosmetic-title catalog and helpers.

Titles are a pure donut *sink*: they buy nothing but bragging rights. Owning
one costs donuts for good and gives no gameplay perk — that's the point, it
drains the economy. A player keeps every title they buy but wears only one at a
time (see `equipped_title` on the user record), shown on /balance and the
leaderboard.

Lives in szofie/ (not a cog) so the economy shop and any future cog can read the
catalog without cog-to-cog import cycles — same reason as plushies.py / titles.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Title:
    id: str
    name: str
    emoji: str
    price: int
    blurb: str
    price_key: Optional[str] = None


TITLES: List[Title] = [
    Title(
        "pathfinder",
        "Solar Pathfinder",
        "🧭",
        0,
        "Completed field expeditions on three different Solar System bodies.",
    ),
    Title(
        "solarsurveyor",
        "Solar Surveyor",
        "🔭",
        0,
        "Completed field expeditions on twenty-five different Solar System bodies.",
    ),
    Title("certified", "Certified Donut", "🍩", 10000, "Officially a snack. Android 21 has noticed you."),
    Title("nibble", "Nibble", "🍪", 50000, "A little something to tide her over."),
    Title("tooth", "Sweet Tooth", "🦷", 100000, "You understand the assignment: sugar."),
    Title("majinsnack", "Majin Snack", "👿", 300000, "Pink, dangerous, and a bit peckish."),
    Title("subject", "Test Subject", "🧪", 750000, "Volunteered for the experiments. Brave. Foolish."),
    Title("assistant", "Lab Assistant", "🥼", 1500000, "Trusted near the beakers. Mostly."),
    Title("connoisseur", "Candy Connoisseur", "🍬", 3000000, "A refined palate for the finest confections."),
    Title("apex", "Apex Predator", "🦈", 7500000, "Top of the food chain — until she gets hungry."),
    Title("favorite", "Android 21's Favorite", "💗", 15000000, "She's saving you for a special occasion."),
    Title(
        "devoured",
        "The Devoured",
        "💀",
        30000000,
        "A legacy honour from Android 21's original title collection.",
    ),
    Title("baron", "Donut Baron", "💰", 75000000, "Tens of millions burned for a name. The vaults groan."),
    Title(
        "magnate",
        "Sugar Magnate",
        "💎",
        200000000,
        "Two hundred million torched on vanity. Even Android 21 blinks.",
    ),
    Title(
        "candykingpin",
        "Candy Kingpin",
        "🏰",
        500000000,
        "Half a billion gone. You run the whole sweet trade now.",
    ),
    Title(
        "incarnate",
        "The Devourer Incarnate",
        "😈",
        2000000000,
        "Two billion burned into legend. You rival the demon herself.",
    ),
    Title(
        "labdirector", "Lab Director", "🧪", 5000000000, "Five billion buys the keys to the whole laboratory."
    ),
    Title(
        "confectionerytitan",
        "Confectionery Titan",
        "🏭",
        10000000000,
        "An industrial empire built from sugar and nerve.",
    ),
    Title(
        "majinfinancier", "Majin Financier", "💹", 25000000000, "The markets move when your appetite does."
    ),
    Title("donutoligarch", "Donut Oligarch", "💰", 50000000000, "A private economy with your name on it."),
    Title(
        "planetarysweetlord",
        "Planetary Sweetlord",
        "🪐",
        100000000000,
        "One planet is barely enough pantry space.",
    ),
    Title(
        "galacticgourmand",
        "Galactic Gourmand",
        "🌌",
        250000000000,
        "A quarter-trillion course served between the stars.",
    ),
    Title(
        "universalglutton",
        "Universal Glutton",
        "💫",
        500000000000,
        "Half a trillion consumed for one line of text.",
    ),
    Title(
        "trillioncalorie",
        "Trillion-Calorie Menace",
        "🔥",
        1000000000000,
        "The first trillion tastes the sweetest.",
    ),
    Title(
        "realitydevourer",
        "Reality Devourer",
        "🕳️",
        5000000000000,
        "Reality itself is only another item on the menu.",
    ),
    Title(
        "cosmicfoodchain",
        "The Cosmic Food Chain",
        "☄️",
        25000000000000,
        "Everything above and below you is edible.",
    ),
    Title(
        "hungerwithoutend",
        "Hunger Without End",
        "⚫",
        100000000000000,
        "A hundred trillion vanished into an endless appetite.",
    ),
    Title(
        "finaldessert",
        "The Final Dessert",
        "🍰",
        1000000000000000,
        "A quadrillion donuts devoted to an unforgettable final course.",
    ),
    Title(
        "android21equal",
        "Android 21's Equal",
        "🧬",
        5000000000000000,
        "She finally considers sharing the laboratory with you.",
    ),
    Title(
        "economydevourer",
        "Economic Sovereign",
        "📉",
        25000000000000000,
        "Twenty-five quadrillion committed to a name that carries weight.",
    ),
    Title(
        "sectormagnate",
        "Sector Magnate",
        "🌌",
        1000000000000000000,
        "A quintillion committed to a fortune that reaches beyond a single world.",
    ),
    Title(
        "imperialpatron",
        "Imperial Patron",
        "🏛️",
        10000000000000000000,
        "Ten quintillion devoted to the prestige of an imperial benefactor.",
    ),
    Title(
        "grandmoff",
        "Grand Moff",
        "🎖️",
        100000000000000000000,
        "A hundred quintillion spent on a name associated with sector-wide authority.",
    ),
    Title(
        "galacticregent",
        "Galactic Regent",
        "⚜️",
        500000000000000000000,
        "Five hundred quintillion committed to a seat among the galactic elite.",
    ),
    Title(
        "galacticemperor",
        "Galactic Emperor",
        "🌑",
        2000000000000000000000,
        "Two sextillion devoted to imperial prestige. A title, not a weapon.",
    ),
    Title(
        "oceansbane", "The Devourer's Angler", "🎣", 0, "Caught every fish in the sea. Earned, never bought."
    ),
    Title(
        "crowned",
        "The Devourer's Crown",
        "👑",
        2500000000,
        "A crown beside your name on /balance and /leaderboard. Verify holders with /titles view:crown.",
        price_key="economy.price_crown",
    ),
    Title(
        "champion", "Season Champion", "🏆", 0, "Finished #1 in a season. Immortalised in the Hall of Fame."
    ),
    Title(
        "fathomless",
        "The Fathomless",
        "🌌",
        0,
        "Logged every creature of the Abyss. Fear nothing in the deep.",
    ),
    Title(
        "voidcaller", "Voidcaller", "🌀", 0, "Pulled the Voidcaller from the Abyss — one in five thousand."
    ),
    Title("scholar", "Trivia Scholar", "📚", 0, "Built a ten-answer trivia streak. Earned, never bought."),
    Title("highroller", "High Roller", "🎰", 0, "Reached Casino Reputation rank 10. Earned, never bought."),
    Title(
        "casinoroyalty", "Casino Royalty", "💎", 0, "Reached Casino Reputation rank 25. Earned, never bought."
    ),
    Title("masterangler", "Master Angler", "🎣", 0, "Caught 10,000 surface fish across a lifetime."),
    Title("hadalexplorer", "Hadal Explorer", "🌊", 0, "Completed ten Deep Expeditions into the Abyss."),
    Title(
        "relichunter", "Relic Hunter", "🏺", 0, "Recovered every classified relic from the expanded Abyss."
    ),
    Title(
        "onegotaway", "The One That Got Away", "🐍", 0, "Encountered the World Serpent and somehow returned."
    ),
    Title("fishmogul", "Fish Market Mogul", "📈", 0, "Earned ten billion donuts from fish sales."),
]
TITLE_BY_ID: Dict[str, Title] = {t.id: t for t in TITLES}
RETIRED_TITLE_IDS = frozenset(
    {
        "nibble",
        "tooth",
        "majinsnack",
        "subject",
        "assistant",
        "connoisseur",
        "apex",
        "devoured",
        "baron",
        "magnate",
        "candykingpin",
        "confectionerytitan",
        "majinfinancier",
        "donutoligarch",
        "planetarysweetlord",
        "galacticgourmand",
        "universalglutton",
        "cosmicfoodchain",
    }
)
PURCHASABLE_TITLES = tuple((t for t in TITLES if t.price > 0 and t.id not in RETIRED_TITLE_IDS))
EARNED_TITLES = tuple((t for t in TITLES if t.price == 0))


def resolve_title(raw: str) -> Optional[Title]:
    """Accept stable IDs, display names and the renamed title's old name."""
    key = raw.casefold().strip()
    if key.startswith("title:"):
        key = key.split(":", 1)[1].strip()
    if key in {"economicsovereign", "devourer of economies"}:
        key = "economydevourer"
    if key == "crown":
        key = "crowned"
    return TITLE_BY_ID.get(key) or next((t for t in TITLES if t.name.casefold() == key), None)


def title_price(title: Title, cfg: Any) -> int:
    """One shared source for shop listings, purchases and configurable prices."""
    return int(cfg.get(title.price_key, title.price)) if title.price_key else title.price


def owns_title(user: Dict[str, Any], title: Title) -> bool:
    """Legacy Crown holders remain recognized even without a backfilled title."""
    return bool((user.get("titles", {}) or {}).get(title.id) or (title.id == "crowned" and user.get("crown")))


def equipped_label(user: Dict[str, Any]) -> Optional[str]:
    """`emoji name` for the user's worn title, or None if they wear none."""
    tid = user.get("equipped_title")
    t = TITLE_BY_ID.get(tid or "")
    return f"{t.emoji} {t.name}" if t else None
