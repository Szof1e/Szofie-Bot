"""Per-user donut balances and inventory.

Reuses the atomic Storage engine, pointed at data/economy/ — separate from the
config so per-user game data never lands in the settings schema.
"""

from __future__ import annotations
import copy
import datetime as dt
import secrets
from pathlib import Path
from typing import Any, Dict, Optional
from . import space as space_state
from .air_dominance import JETS, recipe as jet_recipe
from .retired_items import remove_freezer, remove_safecrack
from .storage import Storage, shared_guild_aliases

ECON_DIR = Path(__file__).resolve().parent.parent / "data" / "economy"
CASINO_GAMES = ("slots", "wheel", "blackjack", "roulette")
CASINO_FEATURED_GAMES = ("wheel", "blackjack", "roulette")
CASINO_TOUR_GAMES = ("wheel", "blackjack", "roulette")


def _casino_bucket() -> Dict[str, Any]:
    return {
        "plays": 0,
        "wagered": 0,
        "net": 0,
        "wins": 0,
        "losses": 0,
        "pushes": 0,
        "biggest_win": 0,
        "jackpots": 0,
        "current_streak": 0,
        "best_streak": 0,
        "modes": {},
    }


def _normalize_casino_bucket(raw: Any) -> Dict[str, Any]:
    bucket = raw if isinstance(raw, dict) else {}
    for key, value in _casino_bucket().items():
        bucket.setdefault(key, value)
    if not isinstance(bucket.get("modes"), dict):
        bucket["modes"] = {}
    return bucket


def record_casino_result(
    user: Dict[str, Any],
    game: str,
    wager: int,
    net: int,
    *,
    mode: Optional[str] = None,
    jackpot: bool = False,
    tour_enabled: bool = True,
    tour_required: int = 3,
    tour_reward: int = 500000000,
    tour_streak_bonus_pct: int = 10,
    tour_streak_cap: int = 7,
    tour_wager_pct: int = 3,
    today: Optional[dt.date] = None,
    reputation_multiplier: float = 1.0,
) -> Dict[str, Any]:
    """Persist one settled casino result and advance the bounded daily tour.

    This intentionally records results independently of the short-retention audit
    ledger, giving players true lifetime vanity statistics. The tour uses only
    the largest wager in each non-slot game that day, not repeat-bet turnover.
    """
    game = str(game).lower()
    wager = max(0, int(wager))
    net = int(net)
    stats = user.setdefault("casino_stats", {})
    if not isinstance(stats, dict):
        stats = user["casino_stats"] = {}
    overall = _normalize_casino_bucket(stats.setdefault("overall", {}))
    games = stats.setdefault("games", {})
    if not isinstance(games, dict):
        games = stats["games"] = {}
    per_game = _normalize_casino_bucket(games.setdefault(game, {}))
    for bucket in (overall, per_game):
        bucket["plays"] = int(bucket.get("plays", 0)) + 1
        bucket["wagered"] = int(bucket.get("wagered", 0)) + wager
        bucket["net"] = int(bucket.get("net", 0)) + net
        if net > 0:
            bucket["wins"] = int(bucket.get("wins", 0)) + 1
            bucket["current_streak"] = int(bucket.get("current_streak", 0)) + 1
            bucket["best_streak"] = max(int(bucket.get("best_streak", 0)), int(bucket["current_streak"]))
            bucket["biggest_win"] = max(int(bucket.get("biggest_win", 0)), net)
        elif net < 0:
            bucket["losses"] = int(bucket.get("losses", 0)) + 1
            bucket["current_streak"] = 0
        else:
            bucket["pushes"] = int(bucket.get("pushes", 0)) + 1
        if jackpot:
            bucket["jackpots"] = int(bucket.get("jackpots", 0)) + 1
        if mode:
            modes = bucket.setdefault("modes", {})
            key = str(mode).lower()
            modes[key] = int(modes.get(key, 0)) + 1
    now_day = today or dt.datetime.now(dt.timezone.utc).date()
    stats["last_played_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    result: Dict[str, Any] = {
        "tour_reward": 0,
        "tour_streak": 0,
        "tour_completed": False,
        "game_streak": int(per_game.get("current_streak", 0)),
    }
    size_rep = min(10, max(0, len(str(max(1, wager))) - 4))
    rep = max(1, int((2 + size_rep + (3 if net > 0 else 0)) * max(0.0, reputation_multiplier)))
    day_index = now_day.toordinal() % len(CASINO_FEATURED_GAMES)
    featured = CASINO_FEATURED_GAMES[day_index]
    if game == featured:
        rep *= 2
    user["casino_reputation"] = max(0, int(user.get("casino_reputation", 0))) + rep
    result.update(reputation=rep, featured_game=featured)
    rank = min(50, int((int(user["casino_reputation"]) / 100) ** 0.5) + 1)
    owned_titles = user.setdefault("titles", {})
    if rank >= 10:
        owned_titles.setdefault("highroller", 1)
    if rank >= 25:
        owned_titles.setdefault("casinoroyalty", 1)
    result["casino_rank"] = rank
    week = now_day.isocalendar()
    week_key = f"{week.year}-W{week.week:02d}"
    weekly = user.setdefault("casino_weekly", {})
    if not isinstance(weekly, dict) or weekly.get("week") != week_key:
        completed = int(weekly.get("completed", 0)) if isinstance(weekly, dict) else 0
        weekly = user["casino_weekly"] = {
            "week": week_key,
            "games": [],
            "claimed": False,
            "completed": completed,
        }
    week_games = weekly.setdefault("games", [])
    if game not in week_games:
        week_games.append(game)
    if len(set(week_games).intersection(CASINO_GAMES)) >= len(CASINO_GAMES) and (not weekly.get("claimed")):
        weekly["claimed"] = True
        weekly["completed"] = int(weekly.get("completed", 0)) + 1
        user["casino_reputation"] += 250
        result["weekly_reputation"] = 250
    if not tour_enabled or game not in CASINO_GAMES:
        return result
    tour = user.setdefault("casino_tour", {})
    if not isinstance(tour, dict):
        tour = user["casino_tour"] = {}
    day_key = now_day.isoformat()
    if tour.get("day") != day_key:
        tour["day"] = day_key
        tour["games"] = []
        tour["claimed"] = False
        tour["qualifying_wagers"] = {}
        tour["last_reward"] = 0
    played = tour.setdefault("games", [])
    if not isinstance(played, list):
        played = tour["games"] = []
    if game in CASINO_TOUR_GAMES and game not in played:
        played.append(game)
    qualifying = tour.setdefault("qualifying_wagers", {})
    if not isinstance(qualifying, dict):
        qualifying = tour["qualifying_wagers"] = {}
    if game in CASINO_TOUR_GAMES and (not tour.get("claimed")):
        qualifying[game] = max(0, int(qualifying.get(game, 0)), wager)
    required = max(1, min(len(CASINO_TOUR_GAMES), int(tour_required)))
    completed_games = set(played).intersection(CASINO_TOUR_GAMES)
    result.update(
        tour_progress=len(completed_games),
        tour_required=required,
        tour_missing=[g for g in CASINO_TOUR_GAMES if g not in completed_games],
        tour_claimed=bool(tour.get("claimed")),
    )
    if game not in CASINO_TOUR_GAMES or len(completed_games) < required or bool(tour.get("claimed")):
        result["tour_streak"] = int(tour.get("streak", 0))
        return result
    previous = tour.get("last_completed_day")
    try:
        previous_day = dt.date.fromisoformat(str(previous))
    except (TypeError, ValueError):
        previous_day = None
    streak = int(tour.get("streak", 0)) + 1 if previous_day == now_day - dt.timedelta(days=1) else 1
    tour["streak"] = streak
    tour["best_streak"] = max(int(tour.get("best_streak", 0)), streak)
    tour["completions"] = int(tour.get("completions", 0)) + 1
    tour["last_completed_day"] = day_key
    tour["claimed"] = True
    capped_steps = min(max(0, streak - 1), max(0, int(tour_streak_cap) - 1))
    eligible_wagers = sum((max(0, int(qualifying.get(g, 0))) for g in CASINO_TOUR_GAMES))
    reward = max(max(0, int(tour_reward)), eligible_wagers * max(0, int(tour_wager_pct)) // 100)
    reward += reward * max(0, int(tour_streak_bonus_pct)) * capped_steps // 100
    user["donuts"] = int(user.get("donuts", 0)) + reward
    tour["last_reward"] = reward
    result.update(tour_reward=reward, tour_streak=streak, tour_completed=True, tour_claimed=True)
    return result


def _default_user(starting: int) -> Dict[str, Any]:
    return {
        "donuts": int(starting),
        "bank": 0,
        "bank_interest_at": None,
        "inventory": {},
        "plushies": {},
        "fish": {},
        "rods": {},
        "equipped_rod": None,
        "rod_enchants": {},
        "vault_tier": 0,
        "fish_seen": {},
        "bestiary_done": False,
        "crown": False,
        "fish_at": None,
        "autofish": False,
        "badges": {},
        "daily_at": None,
        "work_at": None,
        "android21_helper": False,
        "android21_helper_at": None,
        "android21_helper_shifts": 0,
        "android21_helper_earned": 0,
        "helper_certification_tier": 0,
        "activity_contracts": {"fish": {}, "space": {}, "research": {}},
        "employment": {
            "active": None,
            "records": {},
            "day": None,
            "daily_shifts": 0,
            "daily_bonus_claimed": False,
            "pending_until": None,
            "manual_shifts": 0,
            "manual_work_at": None,
            "career_licence_tier": 0,
        },
        "steal_at": None,
        "bank_rob_at": None,
        "jailed_until": None,
        "season_wins": 0,
        "abyss_seen": {},
        "abyss_codex_done": False,
        "dive_at": None,
        "surface_fish_caught": 0,
        "deep_expeditions": 0,
        "fish_sale_lifetime": 0,
        "icbm_ready": 0,
        "icbm_building_at": None,
        "icbm_launch_at": None,
        "strategic_lockdown_until": None,
        "electronic_blackout_until": None,
        "country_invasion_until": None,
        "aa_shield_until": None,
        "aa_rockets_stock": 0,
        "aa_rockets_loaded": 0,
        "aa_rocket_build_at": None,
        "aa_rocket_build_qty": 0,
        "vehicles": {
            "b2": {
                "owned": False,
                "building_until": None,
                "armed": False,
                "arming_until": None,
                "last_deploy_at": None,
            },
            "b52": {
                "owned": False,
                "building_until": None,
                "last_deploy_at": None,
                "ammo": 0,
                "loading_until": None,
                "loading_qty": 0,
                "b52_repair_count": 0,
                "b52_damaged": False,
                "b52_repairing_until": None,
            },
            "u2": {"owned": False, "building_until": None, "last_deploy_at": None},
            "deimos": {"owned": False, "building_until": None, "last_deploy_at": None},
            "sr71": {"owned": False, "building_until": None, "last_deploy_at": None},
            "zumwalt": {
                "owned": False,
                "building_until": None,
                "last_deploy_at": None,
                "ammo": 0,
                "loading_until": None,
                "loading_qty": 0,
            },
            "virginia": {
                "owned": False,
                "building_until": None,
                "last_deploy_at": None,
                "ammo": 0,
                "loading_until": None,
                "loading_qty": 0,
            },
            "himars": {
                "owned": False,
                "building_until": None,
                "last_deploy_at": None,
                "ammo": 0,
                "loading_until": None,
                "loading_qty": 0,
            },
            "apache": {
                "owned": False,
                "building_until": None,
                "last_deploy_at": None,
                "ammo": 0,
                "loading_until": None,
                "loading_qty": 0,
                "comanche_upgraded": False,
                "comanche_upgrade_until": None,
                "comanche_damaged": False,
                "comanche_repair_cost": 0,
                "comanche_repairing_until": None,
            },
            "mq9": {
                "owned": False,
                "building_until": None,
                "last_deploy_at": None,
                "ammo": 0,
                "loading_until": None,
                "loading_qty": 0,
            },
            "f15e": {
                "owned": False,
                "building_until": None,
                "last_deploy_at": None,
                "ammo": 0,
                "loading_until": None,
                "loading_qty": 0,
            },
            "a10": {
                "owned": False,
                "building_until": None,
                "last_deploy_at": None,
                "ammo": 0,
                "loading_until": None,
                "loading_qty": 0,
            },
            "su34": {
                "owned": False,
                "building_until": None,
                "last_deploy_at": None,
                "ammo": 0,
                "loading_until": None,
                "loading_qty": 0,
            },
            "c130j": {
                "owned": False,
                "building_until": None,
                "last_deploy_at": None,
                "ammo": 0,
                "loading_until": None,
                "loading_qty": 0,
            },
            "champ": {
                "owned": False,
                "building_until": None,
                "last_deploy_at": None,
                "ammo": 0,
                "loading_until": None,
                "loading_qty": 0,
            },
            "maldx": {
                "owned": False,
                "building_until": None,
                "last_deploy_at": None,
                "ammo": 0,
                "loading_until": None,
                "loading_qty": 0,
            },
            "lrhw": {
                "owned": False,
                "building_until": None,
                "last_deploy_at": None,
                "ammo": 0,
                "loading_until": None,
                "loading_qty": 0,
            },
            "xb70": {
                "owned": False,
                "building_until": None,
                "last_deploy_at": None,
                "ammo": 0,
                "loading_until": None,
                "loading_qty": 0,
            },
            "aegis": {
                "owned": False,
                "building_until": None,
                "last_deploy_at": None,
                "ammo": 0,
                "loading_until": None,
                "loading_qty": 0,
                "bmd_owned": False,
                "bmd_building_until": None,
                "sm3_ammo": 0,
                "sm3_loading_until": None,
                "sm3_loading_qty": 0,
                "sm3_mode": "single",
            },
            "p8": {
                "owned": False,
                "building_until": None,
                "last_deploy_at": None,
                "ammo": 0,
                "loading_until": None,
                "loading_qty": 0,
            },
            "patriot": {
                "owned": False,
                "building_until": None,
                "last_deploy_at": None,
                "ammo": 0,
                "loading_until": None,
                "loading_qty": 0,
            },
        },
        "recon_targets": {},
        "sr71_construction_intel": {},
        "deep_vault_owned": False,
        "deep_vault_balance": 0,
        "deep_vault_withdraw_amount": 0,
        "deep_vault_withdraw_at": None,
        "s400_owned": False,
        "s400_building_at": None,
        "s400_interceptors": 0,
        "thor": {
            "components": {
                "odin": {"status": "none", "ready_at": None, "launch_id": None},
                "mjolnir": {"status": "none", "ready_at": None, "launch_id": None},
                "bifrost": {"status": "none", "ready_at": None, "launch_id": None},
            },
            "operational": False,
            "assembling_until": None,
            "rods": 0,
            "resupply_until": None,
            "resupply_qty": 0,
            "chambered": False,
            "chambering_until": None,
            "last_strike_at": None,
            "shots_since_service": 0,
            "service_until": None,
            "gbi_block2_owned": False,
            "gbi_block2_building_until": None,
        },
        "continuity": {
            "owned": False,
            "building_until": None,
            "sealed_until": None,
            "transfer_until": None,
            "transfer_action": None,
            "transfer_payload": None,
            "funds": 0,
            "items": {},
            "plushies": {},
            "rod": None,
            "vehicle": None,
        },
        "imperial_star_destroyer": {
            "components": {},
            "project_funds": 0,
            "contributions": {},
            "owner_paid": 0,
            "assembling_until": None,
            "operational": False,
            "arming_until": None,
            "armed": False,
            "damaged": False,
            "repairing_until": None,
            "active_window_id": None,
            "counter_stock": 0,
            "counter_building_until": None,
        },
        "space": {},
        "casino_stats": {},
        "casino_tour": {},
        "casino_reputation": 0,
        "casino_weekly": {},
        "trivia_stats": {},
        "hexed_until": None,
        "hexed_by": None,
        "hex_cast_history": [],
    }


class Economy:
    """Thin accessor over per-guild user documents."""

    def __init__(self) -> None:
        self.store = Storage(directory=ECON_DIR, aliases=shared_guild_aliases())

    def _users(self, guild_id: int) -> Dict[str, Any]:
        doc = self.store.load(guild_id)
        cleaned = False
        if "users" not in doc or not isinstance(doc["users"], dict):
            doc["users"] = {}
            cleaned = True
        retired = ("b2_target_cooldowns", "b2_protected_until", "roulette_last_bet")
        for user in doc["users"].values():
            if not isinstance(user, dict):
                continue
            cleaned = remove_freezer(user) or cleaned
            cleaned = remove_safecrack(user) or cleaned
            for key in retired:
                if key in user:
                    user.pop(key, None)
                    cleaned = True
        if cleaned:
            self.store.mark_dirty(guild_id)
        return doc["users"]

    def user(self, guild_id: int, user_id: int, starting: int) -> Dict[str, Any]:
        users = self._users(guild_id)
        key = str(user_id)
        changed = False
        if key not in users:
            users[key] = _default_user(starting)
            changed = True
        for k, v in _default_user(starting).items():
            if k not in users[key]:
                users[key][k] = v
                changed = True
        if changed:
            self.store.mark_dirty(guild_id)
        return users[key]

    def all_users(self, guild_id: int) -> Dict[str, Any]:
        return self._users(guild_id)

    def mark_dirty(self, guild_id: int) -> None:
        self.store.mark_dirty(guild_id)

    async def save(self, guild_id: int) -> None:
        self.store.mark_dirty(guild_id)
        await self.store.save(guild_id)

    async def flush(self) -> None:
        await self.store.flush()
