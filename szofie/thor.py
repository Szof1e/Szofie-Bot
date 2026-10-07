"""Persistent state helpers for the Project THOR orbital weapon system."""

from __future__ import annotations
import datetime as dt
from typing import Any, Dict, Optional

COMPONENTS: Dict[str, Dict[str, Any]] = {
    "odin": {
        "name": "THOR-1 ODIN Command Module",
        "short": "ODIN Command Module",
        "role": "orbital command, targeting and communications",
        "cost_key": "thor_odin_cost",
        "cost": 5000000000000,
        "hours_key": "thor_odin_build_hours",
        "hours": 24,
        "art": "thor-odin-fabrication.png",
    },
    "mjolnir": {
        "name": "THOR-2 MJÖLNIR Magazine Module",
        "short": "MJÖLNIR Magazine Module",
        "role": "six-cell kinetic-penetrator storage and release",
        "cost_key": "thor_mjolnir_cost",
        "cost": 7000000000000,
        "hours_key": "thor_mjolnir_build_hours",
        "hours": 36,
        "art": "thor-mjolnir-fabrication.png",
    },
    "bifrost": {
        "name": "THOR-3 BIFRÖST Guidance Module",
        "short": "BIFRÖST Guidance Module",
        "role": "deorbit control, navigation and terminal guidance",
        "cost_key": "thor_bifrost_cost",
        "cost": 5000000000000,
        "hours_key": "thor_bifrost_build_hours",
        "hours": 30,
        "art": "thor-bifrost-fabrication.png",
    },
}
COMPONENT_STATUS = frozenset({"none", "fabricating", "ready", "launching", "orbit", "assembled"})
RESUPPLY_KEYS = {
    1: ("thor_resupply_one_hours", 6),
    2: ("thor_resupply_two_hours", 9),
    3: ("thor_resupply_three_hours", 12),
}


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_time(value: Any) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=dt.timezone.utc) if parsed.tzinfo is None else parsed


def default_component() -> Dict[str, Any]:
    return {"status": "none", "ready_at": None, "launch_id": None}


def default_state() -> Dict[str, Any]:
    return {
        "components": {key: default_component() for key in COMPONENTS},
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
        "gbi_stock": 0,
        "gbi_building_until": None,
        "gbi_building_qty": 0,
        "gbi_block2_owned": False,
        "gbi_block2_building_until": None,
    }


def normalize(user: Dict[str, Any]) -> Dict[str, Any]:
    state = user.get("thor")
    if not isinstance(state, dict):
        state = user["thor"] = default_state()
    defaults = default_state()
    for key, value in defaults.items():
        state.setdefault(key, value)
    components = state.get("components")
    if not isinstance(components, dict):
        components = state["components"] = {}
    for key in COMPONENTS:
        component = components.get(key)
        if not isinstance(component, dict):
            component = components[key] = default_component()
        for field, value in default_component().items():
            component.setdefault(field, value)
        if component.get("status") not in COMPONENT_STATUS:
            component.update(default_component())
    state["rods"] = max(0, int(state.get("rods", 0) or 0))
    state["resupply_qty"] = max(0, int(state.get("resupply_qty", 0) or 0))
    state["gbi_stock"] = max(0, int(state.get("gbi_stock", 0) or 0))
    state["gbi_building_qty"] = max(0, int(state.get("gbi_building_qty", 0) or 0))
    state["shots_since_service"] = max(0, int(state.get("shots_since_service", 0) or 0))
    state["operational"] = bool(state.get("operational"))
    state["chambered"] = bool(state.get("chambered"))
    state["gbi_block2_owned"] = bool(state.get("gbi_block2_owned"))
    return state


def settle(user: Dict[str, Any], cfg: Any, now: Optional[dt.datetime] = None) -> bool:
    """Lazily complete fabrication, assembly, resupply and chambering timers."""
    now = now or utcnow()
    state = normalize(user)
    changed = False
    for component in state["components"].values():
        ready_at = parse_time(component.get("ready_at"))
        if component.get("status") == "fabricating" and ready_at and (ready_at <= now):
            component.update(status="ready", ready_at=None)
            changed = True
    assembly = parse_time(state.get("assembling_until"))
    if not state.get("operational") and assembly and (assembly <= now):
        state["operational"] = True
        state["assembling_until"] = None
        for component in state["components"].values():
            component.update(status="assembled", ready_at=None, launch_id=None)
        changed = True
    resupply = parse_time(state.get("resupply_until"))
    if resupply and resupply <= now:
        capacity = max(1, int(cfg.get("economy.thor_rod_capacity", 6)))
        state["rods"] = min(capacity, int(state.get("rods", 0)) + int(state.get("resupply_qty", 0)))
        state["resupply_until"] = None
        state["resupply_qty"] = 0
        changed = True
    chambering = parse_time(state.get("chambering_until"))
    if chambering and chambering <= now:
        state["chambered"] = int(state.get("rods", 0)) > 0
        state["chambering_until"] = None
        changed = True
    if int(state.get("rods", 0)) <= 0 and state.get("chambered"):
        state["chambered"] = False
        changed = True
    gbi_capacity = max(1, int(cfg.get("economy.thor_gbi_stock_capacity", 2)))
    if int(state.get("gbi_stock", 0)) > gbi_capacity:
        state["gbi_stock"] = gbi_capacity
        changed = True
    gbi_ready = parse_time(state.get("gbi_building_until"))
    if gbi_ready and gbi_ready <= now:
        state["gbi_stock"] = min(
            gbi_capacity, int(state.get("gbi_stock", 0)) + int(state.get("gbi_building_qty", 0))
        )
        state["gbi_building_until"] = None
        state["gbi_building_qty"] = 0
        changed = True
    upgrade = parse_time(state.get("gbi_block2_building_until"))
    if upgrade and upgrade <= now:
        state["gbi_block2_owned"] = True
        state["gbi_block2_building_until"] = None
        changed = True
    service = parse_time(state.get("service_until"))
    if service and service <= now:
        state["service_until"] = None
        state["shots_since_service"] = 0
        changed = True
    return changed


def reset_ground_component(component: Dict[str, Any]) -> bool:
    """Destroy a module that has not left Earth; orbiting hardware survives."""
    if component.get("status") not in {"fabricating", "ready"}:
        return False
    component.clear()
    component.update(default_component())
    return True


def settle_aegis_bmd(state: Dict[str, Any], cfg: Any, now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """Normalize and lazily finish the Aegis THOR-defense refit and loading."""
    defaults = {
        "bmd_owned": False,
        "bmd_building_until": None,
        "sm3_ammo": 0,
        "sm3_loading_until": None,
        "sm3_loading_qty": 0,
        "sm3_mode": "single",
    }
    for key, value in defaults.items():
        state.setdefault(key, value)
    now = now or utcnow()
    bmd_ready = parse_time(state.get("bmd_building_until"))
    if bmd_ready and bmd_ready <= now:
        state["bmd_owned"] = bool(state.get("owned"))
        state["bmd_building_until"] = None
    loading = parse_time(state.get("sm3_loading_until"))
    if loading and loading <= now:
        state["sm3_ammo"] = max(0, int(state.get("sm3_ammo", 0))) + max(
            0, int(state.get("sm3_loading_qty", 0))
        )
        state["sm3_loading_until"] = None
        state["sm3_loading_qty"] = 0
    cap = max(1, int(cfg.get("economy.thor_sm3_capacity", 3)))
    state["sm3_ammo"] = min(cap, max(0, int(state.get("sm3_ammo", 0))))
    state["sm3_mode"] = "single"
    return state
