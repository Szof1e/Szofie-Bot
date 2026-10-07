"""Persistent state helpers for the Imperial Star Destroyer megaproject."""

from __future__ import annotations
import copy
import datetime as dt
from typing import Any, Dict, Optional

COMPONENTS: Dict[str, Dict[str, Any]] = {
    "shipyard": {
        "name": "Kuat Orbital Shipyard Core",
        "cost": 1000000000000000000,
        "hours": 72,
        "role": "orbital fabrication and docking spine",
    },
    "hull": {
        "name": "Quadanium Hull Sections",
        "cost": 1500000000000000000,
        "hours": 96,
        "role": "armoured dagger-profile primary hull",
    },
    "reactor": {
        "name": "Solar Ionization Reactor",
        "cost": 2000000000000000000,
        "hours": 120,
        "role": "main reactor and power distribution network",
    },
    "propulsion": {
        "name": "KDY-150 Hyperdrive and Propulsion Array",
        "cost": 1000000000000000000,
        "hours": 72,
        "role": "hyperdrive, ion engines and manoeuvring systems",
    },
    "command": {
        "name": "Command Superstructure and Targeting Network",
        "cost": 750000000000000000,
        "hours": 72,
        "role": "bridge tower, sensors and fire control",
    },
    "cinder": {
        "name": "Base Delta Zero Bombardment Array",
        "cost": 2250000000000000000,
        "hours": 120,
        "role": "server-wide Operation Cinder weapons grid",
    },
}
COMPONENT_STATUSES = frozenset({"none", "fabricating", "ready", "launching", "orbit", "assembled"})
MAX_COMPONENT_FABRICATIONS = 2


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_time(raw: Any) -> Optional[dt.datetime]:
    if not raw:
        return None
    try:
        value = dt.datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value


def default_component() -> Dict[str, Any]:
    return {"status": "none", "ready_at": None, "window_id": None}


def default_state() -> Dict[str, Any]:
    return {
        "components": {key: default_component() for key in COMPONENTS},
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
    }


def normalize(user: Dict[str, Any]) -> Dict[str, Any]:
    state = user.get("imperial_star_destroyer")
    if not isinstance(state, dict):
        state = user["imperial_star_destroyer"] = default_state()
    defaults = default_state()
    for key, value in defaults.items():
        state.setdefault(key, copy.deepcopy(value))
    if not isinstance(state.get("components"), dict):
        state["components"] = {}
    for key in COMPONENTS:
        part = state["components"].get(key)
        if not isinstance(part, dict):
            part = state["components"][key] = default_component()
        for field, value in default_component().items():
            part.setdefault(field, value)
        if part.get("status") not in COMPONENT_STATUSES:
            part.clear()
            part.update(default_component())
    if not isinstance(state.get("contributions"), dict):
        state["contributions"] = {}
    for field in ("project_funds", "owner_paid", "counter_stock"):
        try:
            state[field] = max(0, int(state.get(field, 0)))
        except (TypeError, ValueError):
            state[field] = 0
    state["counter_stock"] = min(2, state["counter_stock"])
    for field in ("operational", "armed", "damaged"):
        state[field] = bool(state.get(field))
    return state


def settle(user: Dict[str, Any], now: Optional[dt.datetime] = None) -> bool:
    """Lazily settle long construction, arming, repair and counter-build timers."""
    now = now or utcnow()
    state = normalize(user)
    changed = False
    for part in state["components"].values():
        ready = parse_time(part.get("ready_at"))
        if part.get("status") == "fabricating" and ready and (ready <= now):
            part.update(status="ready", ready_at=None)
            changed = True
    assembly = parse_time(state.get("assembling_until"))
    if (
        assembly
        and assembly <= now
        and (not state.get("operational"))
        and (not state.get("active_window_id"))
    ):
        state["assembling_until"] = None
        state["operational"] = True
        for part in state["components"].values():
            part.update(status="assembled", ready_at=None, window_id=None)
        changed = True
    arming = parse_time(state.get("arming_until"))
    if arming and arming <= now:
        state["arming_until"] = None
        state["armed"] = bool(state.get("operational") and (not state.get("damaged")))
        changed = True
    repair = parse_time(state.get("repairing_until"))
    if repair and repair <= now:
        state["repairing_until"] = None
        state["damaged"] = False
        changed = True
    counter = parse_time(state.get("counter_building_until"))
    if counter and counter <= now:
        state["counter_building_until"] = None
        state["counter_stock"] = min(2, int(state.get("counter_stock", 0)) + 1)
        changed = True
    return changed


def project_started(state: Dict[str, Any]) -> bool:
    return bool(
        state.get("operational")
        or state.get("assembling_until")
        or state.get("arming_until")
        or state.get("armed")
        or state.get("damaged")
        or state.get("repairing_until")
        or int(state.get("project_funds", 0))
        or any((part.get("status") != "none" for part in state.get("components", {}).values()))
    )


def windows(doc: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    value = doc.get("isd_windows")
    if not isinstance(value, dict):
        value = doc["isd_windows"] = {}
    return value


def active_cinder(doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for record in windows(doc).values():
        if (
            isinstance(record, dict)
            and record.get("kind") == "cinder"
            and (float(record.get("remaining_seconds", 0)) > 0)
        ):
            return record
    return None
