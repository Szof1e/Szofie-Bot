"""Shared chronology/reset barriers for destructive gameplay events."""

import datetime as dt
from typing import Any, Dict, Optional

ISD_GROUND_DESTRUCTION_PCT = 10


def reset_generation(user: Dict[str, Any]) -> str:
    return str(user.get("cinder_wiped_at") or "")


def settle_before_impact(doc: Dict[str, Any], current: dt.datetime) -> None:
    """Resolve overdue world missions before snapshotting destructive damage."""
    from . import space_fleet

    space_fleet.settle_world(doc, current)


def settle_target_before_impact(
    user: Dict[str, Any],
    cfg: Any,
    current: Optional[dt.datetime] = None,
    doc: Optional[Dict[str, Any]] = None,
    user_id: int = 0,
) -> None:
    from . import space, space_fleet, continuity

    current = current or space.now()
    for key in space_fleet.CRAFT:
        space_fleet.ship(user, key)
    settle_before_impact(doc if doc is not None else {"users": {str(user_id): user}}, current)
    space.settle(user, current)
    space_fleet.settle_hulls(user, current)
    continuity.settle(user, cfg, current)
