"""Achievements — a shared badge catalog and award helpers.

Kept standalone so every game cog — Economy, Blackjack,
Hold'em — can grant and announce badges through one path without importing each
other. Earned badges live on the per-user economy document as
`{"badges": {badge_id: iso_timestamp}}`.
"""

from __future__ import annotations
import datetime as dt
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import discord


@dataclass(frozen=True)
class Badge:
    id: str
    emoji: str
    name: str
    desc: str


BADGES: List[Badge] = [
    Badge(
        "space_pathfinder", "🧭", "Solar Pathfinder", "Complete field expeditions on three different worlds."
    ),
    Badge("first_bite", "🍩", "First Bite", "Claim your first daily donuts."),
    Badge("jackpot", "🎰", "Jackpot", "Hit the three-donut slots jackpot."),
    Badge("big_score", "💥", "Big Score", "Win 5,000+ donuts in a single game."),
    Badge("twenty_one", "🃏", "Twenty-One", "Win with a natural blackjack."),
    Badge("card_shark", "♠️", "Card Shark", "Win a hand of blackjack."),
    Badge("lucky_number", "🎯", "Lucky Number", "Win a straight-up number on roulette (40:1)."),
    Badge("cat_burglar", "🤑", "Cat Burglar", "Pull off a successful steal."),
    Badge("vault_cracker", "🔩", "Vault Cracker", "Crack open someone's bank vault."),
    Badge("jailbird", "🚨", "Jailbird", "Get caught and thrown in jail."),
    Badge("rock_bottom", "💸", "Rock Bottom", "Hit zero net worth. Ouch."),
    Badge("fat_stack", "🏦", "Fat Stack", "Reach 10,000 net worth."),
    Badge("whale", "🐋", "Whale", "Reach 100,000 net worth."),
    Badge("first_catch", "🎣", "First Catch", "Reel in your first catch with /fish."),
    Badge("legendary_angler", "🐉", "Legendary Angler", "Land a legendary catch."),
    Badge("oceans_bane", "🐋", "Ocean's Bane", "Catch every fish in the sea — the full bestiary."),
    Badge("champion", "🏆", "Season Champion", "Finished #1 in a season."),
    Badge("voidcaller", "🌀", "Voidcaller", "Pulled the Voidcaller from the Abyss."),
    Badge("abyss_codex", "🌌", "Fathomless", "Logged every creature of the Abyss."),
    Badge("trivia_scholar", "📚", "Trivia Scholar", "Build a 10-answer trivia streak."),
    Badge("world_power", "🌍", "World Power", "Rule three countries at once."),
]
BADGE_BY_ID: Dict[str, Badge] = {b.id: b for b in BADGES}


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def grant(user: Dict[str, Any], badge_id: str) -> Optional[Badge]:
    """Award a badge if the user doesn't already have it.

    Returns the Badge when it was newly earned, else None. Idempotent — safe to
    call on every jackpot / win / steal without duplicating.
    """
    if badge_id not in BADGE_BY_ID:
        return None
    earned = user.setdefault("badges", {})
    if not isinstance(earned, dict):
        earned = {}
        user["badges"] = earned
    if badge_id in earned:
        return None
    earned[badge_id] = _now_iso()
    return BADGE_BY_ID[badge_id]


def grant_wealth(user: Dict[str, Any]) -> List[Badge]:
    """Grant any net-worth milestone badges the user now qualifies for."""
    net = int(user.get("donuts", 0)) + int(user.get("bank", 0))
    out: List[Badge] = []
    if net == 0:
        out.append(grant(user, "rock_bottom"))
    if net >= 10000:
        out.append(grant(user, "fat_stack"))
    if net >= 100000:
        out.append(grant(user, "whale"))
    return [b for b in out if b is not None]


def announce_embed(member: discord.abc.User, badge: Badge, color: int) -> discord.Embed:
    return discord.Embed(
        title="🏅 Badge unlocked!",
        description=f"{member.mention} earned {badge.emoji} **{badge.name}**\n*{badge.desc}*",
        color=color,
    )


async def award(
    channel: Optional[discord.abc.Messageable],
    member: discord.abc.User,
    user: Dict[str, Any],
    color: int,
    *event_ids: str,
) -> List[Badge]:
    """Grant the named event badges plus any wealth milestones, announcing each
    newly earned one in `channel` (pass None to grant silently).

    Returns the list of newly earned badges so the caller can persist if needed.
    """
    newly: List[Badge] = []
    for bid in event_ids:
        b = grant(user, bid)
        if b is not None:
            newly.append(b)
    newly.extend(grant_wealth(user))
    if channel is not None:
        for b in newly:
            try:
                await channel.send(embed=announce_embed(member, b, color))
            except discord.HTTPException:
                pass
    return newly
