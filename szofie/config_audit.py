"""Durable, tamper-evident audit trail for config changes.

The in-Discord audit (``bot.log_action``) posts to a channel — which anyone with
Manage-Channels can delete to cover their tracks. This module writes the same
events to an append-only file on the host filesystem, which Discord users cannot
reach. Every ``/config`` change *and every blocked attempt* leaves a line here
with who did it, when, and the before/after values.

One JSON object per line (JSONL). Append-only: we never rewrite the file, so a
tamperer would have to have shell access to the host to erase their trail.
"""

from __future__ import annotations
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger("szofie.config_audit")
_PATH = os.path.join("data", "config_audit.jsonl")


def _fmt(value: Any) -> Any:
    """Keep the record JSON-safe without losing information."""
    if value is None or isinstance(value, (str, int, float, bool, list, dict)):
        return value
    return str(value)


def record(
    *,
    action: str,
    actor_id: int,
    actor_name: str,
    guild_id: Optional[int],
    key: Optional[str] = None,
    before: Any = None,
    after: Any = None,
    allowed: bool = True,
    note: str = "",
) -> None:
    """Append one audit event. Never raises — auditing must not break /config."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "allowed": allowed,
        "actor_id": actor_id,
        "actor_name": actor_name,
        "guild_id": guild_id,
        "key": key,
        "before": _fmt(before),
        "after": _fmt(after),
        "note": note,
    }
    try:
        os.makedirs(os.path.dirname(_PATH), exist_ok=True)
        with open(_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        log.exception("Failed to write config audit entry: %s", entry)


def tail(limit: int = 15, guild_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Return the most recent entries (newest last), optionally per-guild."""
    try:
        with open(_PATH, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except FileNotFoundError:
        return []
    out: List[Dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if guild_id is not None and entry.get("guild_id") != guild_id:
            continue
        out.append(entry)
    return out[-limit:]
