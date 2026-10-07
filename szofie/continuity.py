"""Restart-safe state helpers for the Raven Rock Continuity Complex."""

from __future__ import annotations
import copy
import datetime as dt
from typing import Any, Dict, Optional
from .amounts import format_donuts
from .assets import hold_cap


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


def default_state() -> Dict[str, Any]:
    return {
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
    }


def normalize(user: Dict[str, Any]) -> Dict[str, Any]:
    state = user.get("continuity")
    if not isinstance(state, dict):
        state = user["continuity"] = default_state()
    for key, value in default_state().items():
        state.setdefault(key, copy.deepcopy(value))
    state["owned"] = bool(state.get("owned"))
    state["funds"] = max(0, int(state.get("funds", 0) or 0))
    for key in ("items", "plushies"):
        if not isinstance(state.get(key), dict):
            state[key] = {}
        state[key] = {str(k): max(0, int(v or 0)) for k, v in state[key].items() if int(v or 0) > 0}
    return state


def _complete_transfer(user: Dict[str, Any], state: Dict[str, Any], cfg: Any) -> None:
    action = state.get("transfer_action")
    payload = state.get("transfer_payload")
    if not isinstance(payload, dict):
        return
    kind = payload.get("kind")
    if action == "store":
        if kind == "funds":
            state["funds"] += int(payload.get("amount", 0))
        elif kind in {"item", "plushie"}:
            bucket = state["items" if kind == "item" else "plushies"]
            key = str(payload.get("id", ""))
            bucket[key] = int(bucket.get(key, 0)) + int(payload.get("amount", 0))
        elif kind == "rod":
            state["rod"] = copy.deepcopy(payload.get("data"))
        elif kind == "vehicle":
            state["vehicle"] = copy.deepcopy(payload.get("data"))
    elif action == "retrieve":
        if kind == "funds":
            user["donuts"] = int(user.get("donuts", 0)) + int(payload.get("amount", 0))
        elif kind == "item":
            inv = user.setdefault("inventory", {})
            key = str(payload.get("id", ""))
            amount = max(0, int(payload.get("amount", 0)))
            cap = hold_cap(cfg, key)
            received = amount if cap is None else min(amount, max(0, cap - int(inv.get(key, 0))))
            inv[key] = int(inv.get(key, 0)) + received
            if amount > received:
                state["items"][key] = int(state["items"].get(key, 0)) + amount - received
        elif kind == "plushie":
            plushies = user.setdefault("plushies", {})
            key = str(payload.get("id", ""))
            if int(plushies.get(key, 0)):
                state["plushies"][key] = int(state["plushies"].get(key, 0)) + int(payload.get("amount", 0))
            else:
                plushies[key] = int(payload.get("amount", 0))
        elif kind == "rod":
            data = payload.get("data") or {}
            rid = str(data.get("id", ""))
            if rid:
                if int(user.setdefault("rods", {}).get(rid, 0)):
                    state["rod"] = copy.deepcopy(data)
                else:
                    user["rods"][rid] = 1
                    user.setdefault("rod_enchants", {})[rid] = list(data.get("enchants", []))
        elif kind == "vehicle":
            data = payload.get("data") or {}
            model = str(data.get("model", ""))
            vehicle_state = data.get("state")
            if model and isinstance(vehicle_state, dict):
                current = user.setdefault("vehicles", {}).get(model, {})
                if current.get("owned") or current.get("building_until"):
                    state["vehicle"] = copy.deepcopy(data)
                else:
                    user["vehicles"][model] = copy.deepcopy(vehicle_state)


def settle(user: Dict[str, Any], cfg: Any, now: Optional[dt.datetime] = None) -> bool:
    now = now or utcnow()
    state = normalize(user)
    changed = False
    building = parse_time(state.get("building_until"))
    if building and building <= now:
        state["owned"] = True
        state["building_until"] = None
        changed = True
    transfer = parse_time(state.get("transfer_until"))
    sealed = parse_time(state.get("sealed_until"))
    if transfer and state.get("transfer_action") == "retrieve" and sealed and (transfer < sealed):
        transfer = sealed
        state["transfer_until"] = sealed.isoformat()
        changed = True
    if transfer and transfer <= now:
        _complete_transfer(user, state, cfg)
        state["transfer_until"] = None
        state["transfer_action"] = None
        state["transfer_payload"] = None
        changed = True
    if sealed and sealed <= now:
        state["sealed_until"] = None
        changed = True
    return changed


def thor_impact(user: Dict[str, Any], cfg: Any, now: Optional[dt.datetime] = None) -> None:
    """Seal an existing bunker after impact without exposing or deleting contents."""
    state = normalize(user)
    if not state.get("owned"):
        return
    hours = max(0.0, float(cfg.get("economy.continuity_thor_seal_hours", 12)))
    state["sealed_until"] = ((now or utcnow()) + dt.timedelta(hours=hours)).isoformat()
    transfer = parse_time(state.get("transfer_until"))
    sealed = parse_time(state["sealed_until"])
    if transfer and state.get("transfer_action") == "retrieve" and (transfer < sealed):
        state["transfer_until"] = sealed.isoformat()


def has_loadout(state: Dict[str, Any]) -> bool:
    return bool(
        int(state.get("funds", 0))
        or state.get("items")
        or state.get("plushies")
        or state.get("rod")
        or state.get("vehicle")
        or state.get("transfer_payload")
    )


def exact_summary(state: Dict[str, Any], *, names: Optional[Dict[str, Dict[str, str]]] = None) -> str:
    """Authorized content summary, with funds in readable donut display units."""
    names = names or {}
    item_names = names.get("items", {})
    plushie_names = names.get("plushies", {})
    rod_names = names.get("rods", {})
    vehicle_names = names.get("vehicles", {})
    items = (
        ", ".join((f"{item_names.get(key, key)} ×{amount}" for key, amount in state.get("items", {}).items()))
        or "none"
    )
    plushies = ", ".join((plushie_names.get(key, key) for key in state.get("plushies", {}))) or "none"
    rod = state.get("rod") or {}
    vehicle = state.get("vehicle") or {}
    lines = [
        f"Funds **{format_donuts(int(state.get('funds', 0)))}**",
        f"Items: {items}",
        f"Plushies: {plushies}",
        f"Rod: {rod_names.get(str(rod.get('id')), str(rod.get('id') or 'none'))}",
        f"Vehicle: {vehicle_names.get(str(vehicle.get('model')), str(vehicle.get('model') or 'none'))}",
    ]
    return "\n".join(lines)
