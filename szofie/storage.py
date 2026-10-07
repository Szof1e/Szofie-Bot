"""Atomic per-guild JSON persistence."""

from __future__ import annotations
import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict

log = logging.getLogger("szofie.storage")
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "guilds"


def shared_guild_aliases() -> Dict[int, int]:
    """Map every guild in ECON_SHARED_GUILDS (past the first) to the first one, so
    they all share a single economy. The first ID listed is the CANONICAL store —
    put the server whose existing data you want to keep first. Empty if fewer than
    two guilds are listed. Config is never aliased (channels stay per-server)."""
    raw = os.getenv("ECON_SHARED_GUILDS", "")
    ids: list[int] = []
    for chunk in raw.replace(",", " ").split():
        digits = "".join((c for c in chunk if c.isdigit()))
        if digits and int(digits) not in ids:
            ids.append(int(digits))
    if len(ids) < 2:
        return {}
    canon = ids[0]
    return {g: canon for g in ids[1:]}


class Storage:
    """Loads and saves one JSON document per guild.

    Everything is cached in memory; writes are debounced and atomic so a crash
    mid-write can never leave a truncated config behind.
    """

    def __init__(self, directory: Path = DATA_DIR, aliases: Dict[int, int] | None = None) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[int, Dict[str, Any]] = {}
        self._locks: Dict[int, asyncio.Lock] = {}
        self._dirty: set[int] = set()
        self._versions: Dict[int, int] = {}
        self.aliases: Dict[int, int] = dict(aliases or {})

    def _canon(self, guild_id: int) -> int:
        return self.aliases.get(guild_id, guild_id)

    def canonical_id(self, guild_id: int) -> int:
        """Public identity for callers that must deduplicate aliased guild work."""
        return self._canon(guild_id)

    def _path(self, guild_id: int) -> Path:
        return self.directory / f"{guild_id}.json"

    def _lock(self, guild_id: int) -> asyncio.Lock:
        if guild_id not in self._locks:
            self._locks[guild_id] = asyncio.Lock()
        return self._locks[guild_id]

    def load(self, guild_id: int) -> Dict[str, Any]:
        """Return the raw document for a guild, reading from disk on first use."""
        guild_id = self._canon(guild_id)
        if guild_id in self._cache:
            return self._cache[guild_id]
        path = self._path(guild_id)
        data: Dict[str, Any] = {}
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as fp:
                    data = json.load(fp)
                if not isinstance(data, dict):
                    raise ValueError("guild document root must be an object")
            except (ValueError, OSError) as exc:
                log.error("Could not read %s (%s) — starting from defaults.", path, exc)
                try:
                    path.rename(path.with_suffix(".json.corrupt"))
                except OSError:
                    pass
                data = {}
        self._cache[guild_id] = data
        return data

    def mark_dirty(self, guild_id: int) -> None:
        guild_id = self._canon(guild_id)
        self._dirty.add(guild_id)
        self._versions[guild_id] = self._versions.get(guild_id, 0) + 1

    async def save(self, guild_id: int) -> None:
        """Write a guild document to disk atomically."""
        guild_id = self._canon(guild_id)
        async with self._lock(guild_id):
            data = self._cache.get(guild_id)
            if data is None:
                return
            version = self._versions.get(guild_id, 0)
            payload = json.dumps(data, indent=2, ensure_ascii=False)
            await asyncio.to_thread(self._write, self._path(guild_id), payload)
            if self._versions.get(guild_id, 0) == version:
                self._dirty.discard(guild_id)

    @staticmethod
    def _write(path: Path, payload: str) -> None:
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fp:
                fp.write(payload)
                fp.flush()
                os.fsync(fp.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    async def flush(self) -> None:
        """Persist every guild with pending changes."""
        failures = []
        for guild_id in list(self._dirty):
            try:
                await self.save(guild_id)
            except Exception as exc:
                failures.append(exc)
        if failures:
            raise failures[0]
