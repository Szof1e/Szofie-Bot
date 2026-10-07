"""Hex curse — `/hex` drains a target's luck across every RNG surface.

A hexed player has ``hexed_until`` (ISO timestamp) set on their economy doc. While
it's live, each luck-based action (slots, blackjack, roulette, wheel, steal, rob,
fishing) knocks their favourable odds down by a RANDOM 35–50%, rolled
fresh per action so the curse feels capricious. ``hex_cast_history`` enforces a
target-wide rolling-window cap across every caster. The cast fee is *burned* —
that's the donut sink; the cursed player then bleeds more to the house while it lasts.
"""

from __future__ import annotations
import datetime as dt
import random
from typing import Any, Dict, Optional

CUT_LO = 0.35
CUT_HI = 0.5


def _parse_time(value: Any) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return (
        parsed.replace(tzinfo=dt.timezone.utc)
        if parsed.tzinfo is None
        else parsed.astimezone(dt.timezone.utc)
    )


def recent_casts(
    user: Dict[str, Any],
    now: Optional[dt.datetime] = None,
    *,
    window_hours: float = 24,
    active_duration_hours: float = 3,
) -> list[dt.datetime]:
    """Normalize and return accepted casts inside the target's rolling window.

    When this feature first encounters an already-active legacy Hex, it derives
    one approximate cast timestamp from the expiry so the live curse immediately
    counts toward the new cap.
    """
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    else:
        current = current.astimezone(dt.timezone.utc)
    window = dt.timedelta(hours=max(1.0, float(window_hours)))
    cutoff = current - window
    parsed = []
    raw = user.get("hex_cast_history", [])
    if isinstance(raw, list):
        for value in raw:
            stamp = _parse_time(value)
            if stamp is not None and cutoff < stamp <= current:
                parsed.append(stamp)
    if not parsed:
        active_expiry = _parse_time(user.get("hexed_until"))
        if active_expiry is not None and active_expiry > current:
            estimated = active_expiry - dt.timedelta(hours=max(0.0, float(active_duration_hours)))
            parsed.append(max(cutoff + dt.timedelta(microseconds=1), min(current, estimated)))
    parsed.sort()
    user["hex_cast_history"] = [stamp.isoformat() for stamp in parsed]
    return parsed


def next_cast_at(
    user: Dict[str, Any],
    now: Optional[dt.datetime] = None,
    *,
    max_casts: int = 2,
    window_hours: float = 24,
    active_duration_hours: float = 3,
) -> Optional[dt.datetime]:
    """Return when the target may be Hexed again, or ``None`` when eligible."""
    casts = recent_casts(user, now, window_hours=window_hours, active_duration_hours=active_duration_hours)
    limit = max(1, int(max_casts))
    if len(casts) < limit:
        return None
    return casts[0] + dt.timedelta(hours=max(1.0, float(window_hours)))


def record_cast(
    user: Dict[str, Any],
    when: Optional[dt.datetime] = None,
    *,
    window_hours: float = 24,
    active_duration_hours: float = 3,
) -> None:
    """Record one accepted cast after the caller has checked the target cap."""
    current = when or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    else:
        current = current.astimezone(dt.timezone.utc)
    casts = recent_casts(
        user, current, window_hours=window_hours, active_duration_hours=active_duration_hours
    )
    casts.append(current)
    casts.sort()
    user["hex_cast_history"] = [stamp.isoformat() for stamp in casts]


def active_until(
    user: Dict[str, Any], now: Optional[dt.datetime] = None, *, user_id: Optional[int] = None
) -> Optional[dt.datetime]:
    """Return the active hex expiry in UTC, or ``None`` when inactive."""
    ts = user.get("hexed_until")
    if not ts:
        return None
    t = _parse_time(ts)
    if t is None:
        return None
    return t if (now or dt.datetime.now(dt.timezone.utc)) < t else None


def is_hexed(
    user: Dict[str, Any], now: Optional[dt.datetime] = None, *, user_id: Optional[int] = None
) -> bool:
    """True while the user's hex is still active."""
    return active_until(user, now, user_id=user_id) is not None


def penalty(user: Dict[str, Any], user_id: Optional[int] = None) -> float:
    """Luck reduction for ONE action: a fresh random 0.35–0.50 if hexed, else 0.0.

    Multiply a win/success probability by ``(1 - penalty)``, or use the value itself
    as the chance to flip a win into a loss on fixed-outcome games."""
    return random.uniform(CUT_LO, CUT_HI) if is_hexed(user, user_id=user_id) else 0.0
