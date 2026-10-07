from __future__ import annotations
import json
from pathlib import Path
from typing import List

LOCK_FILE = Path(__file__).resolve().parent.parent / "data" / "game_locks.json"
GAMES = ("slots", "wheel", "blackjack", "roulette")


def locked_games() -> List[str]:
    """The currently locked games (empty if the file is missing/corrupt)."""
    try:
        data = json.loads(LOCK_FILE.read_text())
    except (FileNotFoundError, ValueError, OSError):
        return []
    return [g for g in data if g in GAMES] if isinstance(data, list) else []


def is_locked(game: str) -> bool:
    return game in locked_games()


def set_locked(game: str, locked: bool) -> None:
    """Add or remove a game from the lock list, written atomically."""
    current = set(locked_games())
    if locked:
        current.add(game)
    else:
        current.discard(game)
    tmp = LOCK_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(current)))
    tmp.replace(LOCK_FILE)
