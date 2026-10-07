"""Death Star rules and restart-safe state. No Discord or private odds here."""

from __future__ import annotations
import copy
import datetime as dt
import random
from typing import Any, Dict, Optional
from . import continuity, imperial_star_destroyer as isd, space
from .economy import _default_user
from .amounts import format_donuts, format_purchase_shortfall

QI = 10**18
SX = 10**21
COMPONENTS = {
    "framework": {"name": "Orbital construction framework", "cost": 80 * SX, "hours": 96},
    "hull": {"name": "Armoured spherical hull", "cost": 120 * SX, "hours": 120},
    "reactor": {"name": "Main reactor complex", "cost": 150 * SX, "hours": 144},
    "propulsion": {"name": "Hyperdrive and propulsion network", "cost": 110 * SX, "hours": 120},
    "superlaser": {"name": "Superlaser focusing array", "cost": 120 * SX, "hours": 144},
    "command": {"name": "Command and targeting systems", "cost": 70 * SX, "hours": 72},
    "industry": {"name": "Habitation and industrial sectors", "cost": 90 * SX, "hours": 96},
    "shields": {"name": "Shield and defensive network", "cost": 60 * SX, "hours": 120},
}
DEPLOY_COST = 100 * SX // 8
ASSEMBLY_COST = 100 * SX


def project_cost() -> int:
    """Core project only: fabrication, eight deployments and final assembly."""
    return sum((spec["cost"] for spec in COMPONENTS.values())) + len(COMPONENTS) * DEPLOY_COST + ASSEMBLY_COST


CHARGE_COST = 100 * QI
TRANSPORT_COST = 25 * QI
SQUADRON_COST = 10 * QI
RECONSTRUCTION_COST = 250 * QI
MAX_BUILDING = 3
MAX_SQUADRONS = 3
MAX_DEFENDERS = 5
THOR_CONSTRUCTION_DESTRUCTION_PCT = 1
ASSEMBLY_HOURS = 72
SHOT_COOLDOWN_HOURS = 72
SQUADRON_HOURS = 12
CHARGE_HOURS = 24
REPAIR_HOURS = 48
REPAIR_COST = 200 * QI
ASSEMBLY_REPAIR_HOURS = 24
ASSEMBLY_REPAIR_COST = 100 * QI
DEVELOP_HOURS = 12
RECONSTRUCTION_DAYS = 7
RECONSTRUCTION_MATERIALS = 100
DEVELOP_MATERIALS = 25
GDP_BASE = 400 * QI
GDP_PER_LEVEL = 360 * QI


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def at(raw: Any) -> Optional[dt.datetime]:
    return space.at(raw)


def component() -> Dict[str, Any]:
    return {"status": "none", "ready_at": None, "window_id": None}


def cargo() -> Dict[str, Any]:
    return {
        "donuts": 0,
        "inventory": {},
        "plushies": {},
        "fish": {},
        "fish_meta": {},
        "rods": {},
        "rod_enchants": {},
        "vehicles": {},
        "materials": {},
    }


def default_state() -> Dict[str, Any]:
    return {
        "components": {k: component() for k in COMPONENTS},
        "operational": False,
        "assembling_until": None,
        "assembly_remaining": 0,
        "damaged": False,
        "damage_kind": None,
        "repairing_until": None,
        "active_window_id": None,
        "project_funds": 0,
        "contributions": {},
        "owner_paid": 0,
        "transport_owned": False,
        "transport_until": None,
        "squadron_stock": 0,
        "squadron_until": None,
        "charged": False,
        "charge_until": None,
        "last_fire_at": None,
        "location": "earth",
        "destination": None,
        "travel_until": None,
        "development": 0,
        "development_until": None,
        "income_at": None,
        "income_remainder": 0,
        "cargo": cargo(),
    }


def normalize(user: Dict[str, Any]) -> Dict[str, Any]:
    state = user.get("death_star")
    if not isinstance(state, dict):
        state = user["death_star"] = default_state()
    if "transport_deployed" not in state:
        state["transport_deployed"] = bool(
            state.get("transport_owned")
            and (
                state.get("operational")
                or any(
                    (
                        p.get("status") in {"launching", "orbit", "assembled"}
                        for p in state.get("components", {}).values()
                    )
                )
            )
        )
    for k, v in default_state().items():
        state.setdefault(k, copy.deepcopy(v))
    for k in COMPONENTS:
        state["components"].setdefault(k, component())
    return state


def windows(doc: Dict[str, Any]) -> Dict[str, Any]:
    return doc.setdefault("deathstar_windows", {})


def world(doc: Dict[str, Any]) -> Dict[str, Any]:
    root = doc.setdefault("deathstar_world", {})
    root.setdefault("destroyed", {})
    root.setdefault("reconstructions", {})
    root.setdefault("salvage", {})
    return root


def destroyed(doc: Dict[str, Any], key: str) -> bool:
    return key in world(doc)["destroyed"]


def gdp(state: Dict[str, Any]) -> int:
    return GDP_BASE + min(10, max(0, int(state["development"]))) * GDP_PER_LEVEL


def available(user: Dict[str, Any]) -> int:
    return max(0, int(user.get("donuts", 0))) + max(0, int(user.get("bank", 0)))


def pay(user: Dict[str, Any], cost: int) -> None:
    if cost < 0:
        raise ValueError("Purchase cost cannot be negative.")
    if available(user) < cost:
        raise ValueError(
            format_purchase_shortfall(cost, available(user)) + "\nDeep Vault is not spent automatically."
        )
    wallet = min(max(0, int(user.get("donuts", 0))), cost)
    user["donuts"] = int(user.get("donuts", 0)) - wallet
    user["bank"] = int(user.get("bank", 0)) - (cost - wallet)


def pay_project(user: Dict[str, Any], state: Dict[str, Any], cost: int) -> int:
    escrow = min(int(state["project_funds"]), cost - (cost + 3) // 4)
    personal = cost - escrow
    try:
        pay(user, personal)
    except ValueError as exc:
        raise ValueError(
            f"Total stage price: **{format_donuts(cost)} donuts**\nCoalition funds applied: **{format_donuts(escrow)} donuts**\nThe required amount below is your personal share.\n"
            + str(exc)
        ) from exc
    state["project_funds"] -= escrow
    state["owner_paid"] += personal
    return personal


def settle_income(user: Dict[str, Any], current: dt.datetime) -> int:
    state = normalize(user)
    anchor = at(state["income_at"])
    if anchor is None:
        state["income_at"] = current.isoformat()
        return 0
    if current <= anchor:
        return 0
    if not state["operational"] or state["damaged"] or state["active_window_id"]:
        state["income_at"] = current.isoformat()
        return 0
    elapsed = int((current - anchor).total_seconds())
    if not elapsed:
        return 0
    numerator = gdp(state) // 80 * min(7 * 86400, elapsed) + int(state["income_remainder"])
    payout, remainder = divmod(numerator, 86400)
    user["donuts"] = int(user.get("donuts", 0)) + payout
    state["income_at"] = (anchor + dt.timedelta(seconds=elapsed)).isoformat()
    state["income_remainder"] = remainder
    return payout


def settle_components(state: Dict[str, Any], current: dt.datetime) -> None:
    """Ready sections are no longer fabricating, including after offline time."""
    for part in state.get("components", {}).values():
        if part["status"] == "fabricating" and at(part["ready_at"]) and (at(part["ready_at"]) <= current):
            part.update(status="ready", ready_at=None)


def thor_construction_impact(
    user: Dict[str, Any],
    current: Optional[dt.datetime] = None,
    rng=None,
    *,
    resolution_pct: float = THOR_CONSTRUCTION_DESTRUCTION_PCT,
) -> Dict[str, int]:
    result = {"at_risk": 0, "destroyed": 0}
    state = user.get("death_star")
    if not isinstance(state, dict):
        return result
    settle_components(state, current or now())
    parts = [part for part in state.get("components", {}).values() if part.get("status") == "fabricating"]
    if not parts:
        return result
    result["at_risk"] = 1
    rng = rng or random
    roll_sides = 1000 if 0 < resolution_pct < 1 else 100
    threshold = round(max(0, min(100, resolution_pct)) * roll_sides / 100)
    if rng.randint(1, roll_sides) <= threshold:
        part = rng.choice(parts)
        part.clear()
        part.update(component())
        result["destroyed"] = 1
    return result


def settle(user: Dict[str, Any], current: Optional[dt.datetime] = None) -> int:
    """Complete offline deadlines and accrue exact, once-only station production."""
    current = current or now()
    state = normalize(user)
    settle_components(state, current)
    for timer, flag in (("transport_until", "transport_owned"), ("charge_until", "charged")):
        if at(state[timer]) and at(state[timer]) <= current:
            state[timer] = None
            state[flag] = True
    if at(state["squadron_until"]) and at(state["squadron_until"]) <= current:
        state["squadron_stock"] = min(MAX_SQUADRONS, int(state["squadron_stock"]) + 1)
        state["squadron_until"] = None
    if at(state["travel_until"]) and at(state["travel_until"]) <= current:
        state["location"] = state["destination"]
        state.update(destination=None, travel_until=None)
    repaired = at(state["repairing_until"])
    if repaired and repaired <= current:
        if state["damage_kind"] == "assembly":
            state["assembling_until"] = (
                repaired + dt.timedelta(seconds=state["assembly_remaining"])
            ).isoformat()
            state["assembly_remaining"] = 0
        state.update(damaged=False, damage_kind=None, repairing_until=None, income_at=repaired.isoformat())
    completed = at(state["assembling_until"])
    if completed and completed <= current and (not state["damaged"]) and (not state["active_window_id"]):
        state.update(operational=True, assembling_until=None, income_at=completed.isoformat())
        for part in state["components"].values():
            part.update(status="assembled", ready_at=None, window_id=None)
    anchor = at(state["income_at"])
    cutoff = current - dt.timedelta(days=7)
    if anchor and anchor < cutoff:
        state["income_at"] = cutoff.isoformat()
    developing = at(state["development_until"])
    payout = 0
    if developing and developing <= current:
        payout += settle_income(user, developing)
        state["development"] = min(10, int(state["development"]) + 1)
        state["development_until"] = None
    return payout + settle_income(user, current)


def settle_world(doc: Dict[str, Any], current: Optional[dt.datetime] = None) -> list[str]:
    current = current or now()
    root = world(doc)
    completed = []
    for key, record in list(root["reconstructions"].items()):
        if at(record.get("ready_at")) and at(record["ready_at"]) <= current:
            root["destroyed"].pop(key, None)
            root["reconstructions"].pop(key)
            space.galaxy(doc)["claims"].pop(key, None)
            completed.append(key)
    return completed


def wipe_planet(
    doc: Dict[str, Any],
    key: str,
    attacker: int,
    sovereign: Optional[int],
    cfg: Any,
    current: Optional[dt.datetime] = None,
) -> list[int]:
    """Erase local sites; reset only the locked-in sovereign. Preserve history/debt."""
    current = current or now()
    if key in {"earth", "sun"} or key.startswith("deathstar-"):
        raise ValueError("The starting world, Sun and artificial moons cannot be planet-kill targets.")
    if destroyed(doc, key):
        raise ValueError("That body is already a debris field.")
    users = doc.get("users", {})
    affected = []
    for uid, user in users.items():
        if not isinstance(user, dict):
            continue
        colonies = (user.get("space") or {}).get("colonies", {})
        expedition = user.get("space") or {}
        if (expedition.get("expedition") or {}).get("body") == key:
            expedition["expedition"] = None
        if key in colonies:
            affected.append(int(uid))
            colonies.pop(key)
    if sovereign is not None and str(sovereign) in users:
        victim = users[str(sovereign)]
        continuity.settle(victim, cfg, current)
        bunker = copy.deepcopy(continuity.normalize(victim))
        fresh = _default_user(0)
        fresh.update(titles={}, equipped_title=None)
        for history in ("casino_stats", "trivia_stats"):
            if history in victim:
                fresh[history] = copy.deepcopy(victim[history])
        if bunker["owned"]:
            bunker.update(
                transfer_until=None,
                transfer_action=None,
                transfer_payload=None,
                sealed_until=(current + dt.timedelta(hours=24)).isoformat(),
            )
            fresh["continuity"] = bunker
        fresh["cinder_wiped_at"] = current.isoformat()
        victim.clear()
        victim.update(fresh)
        if sovereign not in affected:
            affected.append(sovereign)
        territories = doc.get("countries", {}).get("territories", {})
        for country, data in list(territories.items()):
            if int(data.get("owner", 0)) == sovereign:
                territories.pop(country)
        for body_key, owner in list(space.galaxy(doc)["claims"].items()):
            if str(owner) == str(sovereign):
                space.galaxy(doc)["claims"].pop(body_key)
        for field in ("deathstar_windows", "isd_windows", "thor_launches"):
            for window_id, record in list(doc.get(field, {}).items()):
                if any((str(record.get(k, "")) == str(sovereign) for k in ("builder", "owner", "user_id"))):
                    doc[field].pop(window_id)
        for body_key, record in list(world(doc)["reconstructions"].items()):
            if str(record.get("builder", "")) == str(sovereign):
                world(doc)["reconstructions"].pop(body_key)
    space.galaxy(doc)["claims"].pop(key, None)
    world(doc)["destroyed"][key] = {"at": current.isoformat(), "attacker": attacker, "sovereign": sovereign}
    return affected


def transfer_cargo(user: Dict[str, Any], action: str, kind: str, asset: Optional[str], amount: int) -> None:
    """Move a typed cargo asset without copying ownership, enchants or ammunition."""
    hold = normalize(user)["cargo"]
    source, destination = (user, hold) if action == "load" else (hold, user)
    if action not in {"load", "unload"} or amount <= 0:
        raise ValueError("Choose load/unload and a positive quantity.")
    if kind == "donuts":
        if int(source.get("donuts", 0)) < amount:
            raise ValueError("Not enough wallet/cargo donuts.")
        source["donuts"] -= amount
        destination["donuts"] = int(destination.get("donuts", 0)) + amount
        return
    if kind not in {"inventory", "plushies", "fish", "rods", "vehicles", "materials"} or not asset:
        raise ValueError("Specify a valid cargo kind and asset ID.")
    if kind == "materials":
        source = space.normalize(user)["cargo"] if action == "load" else hold
        destination = hold if action == "load" else space.normalize(user)["cargo"]
    bucket = source.setdefault(kind, {})
    target = destination.setdefault(kind, {})
    if kind == "vehicles":
        vehicle = bucket.get(asset)
        if amount != 1 or not isinstance(vehicle, dict) or (not vehicle.get("owned")):
            raise ValueError("Transfer one owned vehicle at a time.")
        if asset in target and target[asset].get("owned"):
            raise ValueError("A vehicle of that model is already at the destination.")
        if any(
            (
                vehicle.get(k)
                for k in (
                    "building_until",
                    "loading_until",
                    "arming_until",
                    "sm3_loading_until",
                    "comanche_upgrade_until",
                    "bmd_building_until",
                    "garrison_country",
                    "garrison_transfer_until",
                    "comanche_damaged",
                    "b52_damaged",
                    "m1a2_damaged",
                    "leopard2a7_damaged",
                )
            )
        ):
            raise ValueError("Finish construction, loading, garrison transfer or repair first.")
        target[asset] = copy.deepcopy(vehicle)
        bucket.pop(asset)
        return
    have = int(bucket.get(asset, 0))
    if amount > have:
        raise ValueError("You do not have that quantity.")
    if kind == "rods":
        if amount != 1 or int(target.get(asset, 0)):
            raise ValueError("The rod already exists at the destination; transfer one at a time.")
        destination.setdefault("rod_enchants", {})[asset] = source.setdefault("rod_enchants", {}).pop(
            asset, []
        )
        if action == "load" and user.get("equipped_rod") == asset:
            user["equipped_rod"] = None
    if kind == "fish" and asset in source.get("fish_meta", {}):
        prior = destination.setdefault("fish_meta", {}).get(asset, {})
        incoming = source["fish_meta"][asset]
        destination["fish_meta"][asset] = {
            "age": max(float(prior.get("age", 0)), float(incoming.get("age", 0))),
            "w1": False,
            "w2": False,
        }
    bucket[asset] = have - amount
    target[asset] = int(target.get(asset, 0)) + amount
    if not bucket[asset]:
        bucket.pop(asset)
        if kind == "fish":
            source.get("fish_meta", {}).pop(asset, None)
