"""Shared asset caps and reservations for delayed Raven Rock recovery."""

from typing import Any, Dict, Optional


def hold_cap(cfg: Any, item: str) -> Optional[int]:
    from .catalog import ITEM_HOLD_CAPS

    key = ITEM_HOLD_CAPS.get(item)
    return max(0, int(cfg.get("economy." + key, 0))) if key else None


def recovery_payload(user: Dict[str, Any]) -> Dict[str, Any]:
    state = user.get("continuity", {})
    if isinstance(state, dict) and state.get("transfer_action") == "retrieve":
        value = state.get("transfer_payload")
        if isinstance(value, dict) and state.get("transfer_until"):
            return value
    return {}


def reserved(user: Dict[str, Any], kind: str, identifier: str) -> int:
    payload = recovery_payload(user)
    if payload.get("kind") != kind:
        return 0
    if kind in {"item", "plushie"}:
        return max(0, int(payload.get("amount", 0))) if payload.get("id") == identifier else 0
    data = payload.get("data") or {}
    field = "model" if kind == "vehicle" else "id"
    return int(isinstance(data, dict) and data.get(field) == identifier)


def inventory_space(cfg: Any, user: Dict[str, Any], item: str) -> Optional[int]:
    cap = hold_cap(cfg, item)
    if cap is None:
        return None
    owned = int((user.get("inventory") or {}).get(item, 0))
    return max(0, cap - owned - reserved(user, "item", item))


def vehicle_unavailable(user: Dict[str, Any], model: str) -> bool:
    state = (user.get("vehicles") or {}).get(model, {})
    return bool(state.get("owned") or state.get("building_until") or reserved(user, "vehicle", model))
