"""Narrow cleanup of retired inventory state, including restored old saves."""

from __future__ import annotations
from typing import Any, Dict

FREEZER_CONFIG_KEYS = ("freezer_hours", "freezer_per_value", "price_freezer", "hold_cap_freezer")
SAFECRACK_CONFIG_KEYS = (
    "safecrack_rounds",
    "safecrack_seconds",
    "safecrack_max_percent",
    "safecrack_min_target",
    "safecrack_cooldown_minutes",
    "safecrack_victim_cooldown_minutes",
)


def remove_safecrack(user: Dict[str, Any]) -> bool:
    """Retire only obsolete cooldowns; keep assets, jail and historic ledger data."""
    changed = False
    for key in ("safecrack_at", "safecracked_at"):
        if key in user:
            user.pop(key)
            changed = True
    return changed


def remove_freezer(user: Dict[str, Any]) -> bool:
    """Remove only Freezers and their effect; preserve all other assets and ages."""
    changed = False
    if "bucket_freeze_until" in user:
        user.pop("bucket_freeze_until")
        changed = True
    stores = [user.get("inventory")]
    continuity = user.get("continuity")
    if isinstance(continuity, dict):
        stores.append(continuity.get("items"))
        payload = continuity.get("transfer_payload")
        if isinstance(payload, dict) and payload.get("kind") == "item" and (payload.get("id") == "freezer"):
            continuity.update(transfer_payload=None, transfer_action=None, transfer_until=None)
            changed = True
    for key in ("space", "death_star"):
        state = user.get(key)
        if isinstance(state, dict):
            for location in ("cargo", "load_manifest"):
                manifest = state.get(location)
                if isinstance(manifest, dict):
                    stores.append(manifest.get("inventory"))
    for inventory in stores:
        if isinstance(inventory, dict) and "freezer" in inventory:
            inventory.pop("freezer")
            changed = True
    return changed
