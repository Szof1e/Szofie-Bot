"""Append-only donut transaction ledger — the economy's audit trail.

Every event that changes a user's net worth (wallet + bank) is appended as one
JSON line to data/ledger/<guild>.jsonl. Append-only and file-based, so entries
can never be edited or removed through Discord — when an accusation comes, the
ledger shows exactly what happened and who did it.

Recording must never break gameplay, so record() swallows its own errors.
"""

from __future__ import annotations
import asyncio
import datetime as dt
import hashlib
import json
import logging
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional
from .storage import shared_guild_aliases

log = logging.getLogger("szofie.ledger")
LEDGER_DIR = Path(__file__).resolve().parent.parent / "data" / "ledger"


def security_entry_key(entry: Dict[str, Any]) -> str:
    """Stable identity for a historical event; never depends on file line numbers."""
    original = {key: value for key, value in entry.items() if key != "security_actor"}
    return hashlib.sha256(
        json.dumps(original, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


VICTIM_SUBJECT_SECURITY_REASONS = frozenset(
    {
        "steal-success",
        "robbank-success",
        "safecrack-success",
        "icbm-hit",
        "vehicle-b2-hit",
        "vehicle-b52-hit",
        "vehicle-zumwalt-hit",
        "vehicle-virginia-hit",
        "vehicle-himars-hit",
        "vehicle-apache-hit",
        "vehicle-comanche-hit",
        "vehicle-mq9-hit",
        "vehicle-f15e-hit",
        "vehicle-a10-hit",
        "vehicle-su34-hit",
        "vehicle-c130j-hit",
        "vehicle-champ-hit",
        "vehicle-maldx-hit",
        "vehicle-lrhw-hit",
        "vehicle-xb70-hit",
        "vehicle-m1a2-hit",
        "vehicle-leopard2a7-hit",
        "vehicle-j20-hit",
        "vehicle-su57-hit",
        "copcall-hit",
        "country-conquest",
        "thor-hit",
        "nyx-intercept-hit",
        "deathstar-planet-hit",
    }
)
ATTACKER_SUBJECT_SECURITY_REASONS = frozenset(
    {
        "heist-success",
        "vehicle-u2-recon",
        "vehicle-sr71-recon",
        "space-xwing-hit",
        "space-fleet-hit",
        "space-fleet-precision-hit",
        "space-launch-intercept-hit",
        "isd-planetary-impact",
        "isd-cinder-impact",
        "hex",
    }
)
RECIPROCAL_SECURITY_REASONS = frozenset({"vehicle-deimos-recon"})
SECURITY_EVENT_REASONS = (
    VICTIM_SUBJECT_SECURITY_REASONS | ATTACKER_SUBJECT_SECURITY_REASONS | RECIPROCAL_SECURITY_REASONS
)


def incoming_security_actor(entry: Dict[str, Any], victim_id: int) -> Optional[int]:
    """Return the attacker for a successful event aimed at ``victim_id``."""
    reason = str(entry.get("reason", ""))
    victim_id = int(victim_id)
    try:
        if reason in VICTIM_SUBJECT_SECURITY_REASONS:
            if (
                reason in {"steal-success", "robbank-success", "safecrack-success"}
                and "delta" in entry
                and (int(entry["delta"]) >= 0)
            ):
                return None
            if int(entry.get("user", 0)) != victim_id:
                return None
            actor = entry.get("actor", entry.get("other"))
        elif reason in ATTACKER_SUBJECT_SECURITY_REASONS:
            if int(entry.get("other", 0)) != victim_id:
                return None
            actor = entry.get("actor", entry.get("user"))
        elif reason in RECIPROCAL_SECURITY_REASONS:
            subject = int(entry.get("user", 0))
            other = int(entry.get("other", 0))
            if victim_id == subject:
                actor = other
            elif victim_id == other:
                actor = subject
            else:
                return None
        else:
            return None
        actor_id = int(actor)
    except (TypeError, ValueError):
        return None
    return actor_id if actor_id > 0 and actor_id != victim_id else None


class Ledger:
    def __init__(self, directory: Path = LEDGER_DIR) -> None:
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)
        self._locks: Dict[int, asyncio.Lock] = {}
        self.aliases: Dict[int, int] = shared_guild_aliases()

    def _canon(self, guild_id: int) -> int:
        return self.aliases.get(guild_id, guild_id)

    def _path(self, guild_id: int) -> Path:
        return self.dir / f"{self._canon(guild_id)}.jsonl"

    def _lock(self, guild_id: int) -> asyncio.Lock:
        guild_id = self._canon(guild_id)
        if guild_id not in self._locks:
            self._locks[guild_id] = asyncio.Lock()
        return self._locks[guild_id]

    async def record(
        self,
        guild_id: int,
        user_id: int,
        delta: int,
        reason: str,
        *,
        after: Optional[int] = None,
        other: Optional[int] = None,
        actor: Optional[int] = None,
        detail: Optional[str] = None,
        occurred_at: Optional[dt.datetime] = None,
    ) -> None:
        """Append one transaction. `delta` is the change in net worth (+/-),
        `other` a counterparty, `actor` whoever caused it (if not the user)."""
        entry: Dict[str, Any] = {
            "ts": (occurred_at or dt.datetime.now(dt.timezone.utc)).isoformat(),
            "user": int(user_id),
            "delta": int(delta),
            "reason": reason,
        }
        if after is not None:
            entry["after"] = int(after)
        if other is not None:
            entry["other"] = int(other)
        if actor is not None and int(actor) != int(user_id):
            entry["actor"] = int(actor)
        if detail:
            entry["detail"] = str(detail)[:200]
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        try:
            async with self._lock(guild_id):
                await asyncio.to_thread(self._append, self._path(guild_id), line)
        except Exception:
            log.exception("Ledger append failed for guild %s", guild_id)

    @staticmethod
    def _append(path: Path, line: str) -> None:
        with path.open("a", encoding="utf-8") as fp:
            fp.write(line)

    async def prune(self, guild_id: int, days: int) -> int:
        """Drop entries older than `days`; returns how many were removed. Only
        rewrites the file when something actually ages out, so it's cheap to call
        often. Held under the guild lock so it never races an append."""
        if days <= 0:
            return 0
        path = self._path(guild_id)
        if not path.exists():
            return 0
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)

        def _do() -> int:
            kept: List[str] = []
            removed = 0
            with path.open("r", encoding="utf-8") as fp:
                for ln in fp:
                    s = ln.strip()
                    if not s:
                        continue
                    try:
                        entry = json.loads(s)
                        reason = str(entry.get("reason", ""))
                        if reason.startswith("coalition-transfer") or reason in SECURITY_EVENT_REASONS:
                            kept.append(s)
                            continue
                        ts = dt.datetime.fromisoformat(entry["ts"])
                    except Exception:
                        kept.append(s)
                        continue
                    if ts >= cutoff:
                        kept.append(s)
                    else:
                        removed += 1
            if removed:
                tmp = path.with_name(path.name + ".tmp")
                with tmp.open("w", encoding="utf-8") as fp:
                    if kept:
                        fp.write("\n".join(kept) + "\n")
                tmp.replace(path)
            return removed

        try:
            async with self._lock(guild_id):
                return await asyncio.to_thread(_do)
        except Exception:
            log.exception("Ledger prune failed for guild %s", guild_id)
            return 0

    async def read(
        self, guild_id: int, *, user: Optional[int] = None, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Most-recent entries, newest first. Filter to one user (as the subject
        or the counterparty) when given. Disk parsing runs off the event loop so a
        large audit trail cannot make unrelated Discord interactions stutter."""
        path = self._path(guild_id)
        if not path.exists():
            return []
        return await asyncio.to_thread(self._read, path, user, limit)

    async def read_security(self, guild_id: int, *, victim: int, limit: int = 20) -> List[Dict[str, Any]]:
        """Newest successful hostile events against one victim, newest first."""
        path = self._path(guild_id)
        if not path.exists():
            return []
        return await asyncio.to_thread(self._read_security, path, int(victim), limit)

    @staticmethod
    def _read_security(path: Path, victim: int, limit: int) -> List[Dict[str, Any]]:
        rows = deque(maxlen=max(1, int(limit)))
        try:
            with path.open("r", encoding="utf-8") as fp:
                for ln in fp:
                    try:
                        entry = json.loads(ln)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if not isinstance(entry, dict):
                        continue
                    actor = incoming_security_actor(entry, victim)
                    if actor is None:
                        continue
                    entry = dict(entry)
                    entry["security_actor"] = actor
                    rows.append(entry)
        except OSError:
            return []
        return list(reversed(rows))

    @staticmethod
    def _read(path: Path, user: Optional[int], limit: int) -> List[Dict[str, Any]]:
        rows = deque(maxlen=max(1, int(limit)))
        try:
            with path.open("r", encoding="utf-8") as fp:
                for ln in fp:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        e = json.loads(ln)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(e, dict):
                        continue
                    if user is not None and e.get("user") != user and (e.get("other") != user):
                        continue
                    rows.append(e)
        except OSError:
            return []
        return list(reversed(rows))
