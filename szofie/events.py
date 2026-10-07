"""Server-wide random events — shared catalog and state helpers.

Runtime state lives on the *economy* guild doc root under ``"events"``::

    {"last_at": iso, "active": {"id", "expires_at"} | None,
     "bounty": {"target", "bonus"} | None}

The Events cog fires events on a timer and announces them; other cogs read the
active modifier through ``is_active()`` / ``bounty()`` to apply their effect at
the point of use. Kept dependency-free (stdlib only) so any cog can import it
without a circular reference.
"""

from __future__ import annotations
import datetime as dt
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Event:
    id: str
    emoji: str
    name: str
    kind: str
    weight: int
    duration_min: int
    blurb: str


CATALOG: List[Event] = [
    Event(
        "donutboom", "📈", "Donut Boom", "timed", 12, 45, "Career shifts pay **+25%** while demand surges."
    ),
    Event(
        "market",
        "📉",
        "Market Correction",
        "timed",
        8,
        30,
        "Career pay falls **15%** until the market stabilizes. A temporary economic setback.",
    ),
    Event("payday", "💼", "Payday Weekend", "timed", 10, 45, "Career shifts pay **+50%** for the duration."),
    Event(
        "shortage",
        "📦",
        "Supply Shortage",
        "timed",
        8,
        30,
        "Strategic vehicle and ammunition prices rise **15%** until supply recovers.",
    ),
    Event(
        "frenzy",
        "🎣",
        "Fishing Frenzy",
        "timed",
        16,
        45,
        "Fish sell for **+50%** — get casting and cash in before it ends!",
    ),
    Event(
        "migration",
        "🐟",
        "The Great Migration",
        "timed",
        14,
        45,
        "Rare-and-better fish are biting far more often. Cast now!",
    ),
    Event(
        "abyssal",
        "🌊",
        "Abyssal Disturbance",
        "timed",
        8,
        30,
        "Abyssal expeditions find rare creatures more often.",
    ),
    Event(
        "happyhour",
        "🎰",
        "High-Roller Night",
        "timed",
        12,
        45,
        "The Prize Wheel's house cut is **waived** — the best odds you'll ever get.",
    ),
    Event(
        "progressive",
        "💰",
        "Progressive Rush",
        "timed",
        8,
        45,
        "Casino games award **double reputation** toward high-roller ranks.",
    ),
    Event(
        "dealerchallenge",
        "🎴",
        "Dealer Challenge",
        "timed",
        8,
        45,
        "Play different casino games to accelerate Casino Reputation.",
    ),
    Event(
        "armsexpo",
        "🪖",
        "Arms Expo",
        "timed",
        8,
        45,
        "Vehicles and ammunition cost **15% less** while contracts are open.",
    ),
    Event(
        "mobilization",
        "🏭",
        "Factory Mobilization",
        "timed",
        8,
        45,
        "New vehicle construction and ammunition loading finish **25% faster**.",
    ),
    Event(
        "intel",
        "🛰️",
        "Intelligence Leak",
        "timed",
        7,
        30,
        "New U-2 target packages remain valid **50% longer**.",
    ),
    Event(
        "defensealert",
        "🚨",
        "Defense Alert",
        "timed",
        7,
        30,
        "All active air defenses gain **+10 percentage points** to intercept.",
    ),
    Event(
        "drop",
        "🍩",
        "Community Donut Drop",
        "instant",
        14,
        0,
        "A crate of donuts hit the server! First few to grab it split the loot.",
    ),
    Event(
        "bounty",
        "🎯",
        "Community Bounty",
        "instant",
        7,
        0,
        "A bounty's been placed — rob the target for a bonus reward.",
    ),
]
CATALOG_BY_ID: Dict[str, Event] = {e.id: e for e in CATALOG}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse(value: Optional[str]) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def state(doc: Dict[str, Any]) -> Dict[str, Any]:
    """The events sub-document, created on first use."""
    ev = doc.get("events")
    if not isinstance(ev, dict):
        ev = {"last_at": None, "active": None, "bounty": None, "last_event_id": None}
        doc["events"] = ev
    ev.pop("player_bounties", None)
    ev.setdefault("last_event_id", None)
    return ev


def active(doc: Dict[str, Any]) -> Optional[str]:
    """Id of the current timed event, or None. Auto-clears once expired."""
    ev = state(doc)
    a = ev.get("active")
    if not isinstance(a, dict):
        return None
    exp = _parse(a.get("expires_at"))
    if exp is None or exp <= _now():
        ev["active"] = None
        return None
    return a.get("id")


def active_until(doc: Dict[str, Any]) -> Optional[dt.datetime]:
    a = state(doc).get("active")
    return _parse(a.get("expires_at")) if isinstance(a, dict) else None


def is_active(doc: Dict[str, Any], event_id: str) -> bool:
    return active(doc) == event_id


def start_timed(doc: Dict[str, Any], event_id: str, duration_min: int) -> None:
    state(doc)["active"] = {
        "id": event_id,
        "expires_at": (_now() + dt.timedelta(minutes=duration_min)).isoformat(),
    }


def bounty(doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    b = state(doc).get("bounty")
    return b if isinstance(b, dict) else None


def set_bounty(doc: Dict[str, Any], target_id: int, bonus: int) -> None:
    state(doc)["bounty"] = {"target": str(target_id), "bonus": int(bonus)}


def clear_bounty(doc: Dict[str, Any]) -> None:
    state(doc)["bounty"] = None
