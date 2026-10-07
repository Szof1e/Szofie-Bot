"""Ordinary spacecraft: persistent missions, bounded combat and finite salvage.

This module never resolves superweapon windows or damages terrestrial assets.
All timers are UTC deadlines; settlement consumes missions once after downtime.
"""

from __future__ import annotations
import copy
import datetime as dt
import random
from dataclasses import dataclass
from typing import Any, Dict, Optional
from . import coalitions, deathstar, nyx, space, imperial_star_destroyer as isd
from .currency import take_visible, visible_total
from .coalitions import are_allied as allied

Q = space.QA
RECON_HOURS = 4
ESCORT_HOURS = 6
DISABLE_HOURS = 4
MAX_RAIDS = 2
INTERDICTION_MINUTES = 60
HISTORY_LIMIT = 12
LEGACY = {"cutlass", "transport", "xwing"}
ARQUITENS_HIT_CHANCE = 70
ARQUITENS_RETALIATION_CHANCE = 30
ARQUITENS_COOLDOWN_HOURS = 3
LAUNCH_MINUTES = 30
LAUNCH_ATTEMPTS = 3
HEAVY_CRAFT = {"hammerhead", "interdictor", "arquitens"}
LAUNCH_COUNTERS = {"xwing": 35, "awing": 45, "bwing": 40}


@dataclass(frozen=True)
class Craft:
    name: str
    cost: int
    build_hours: int
    payload_name: str
    payload_cost: int
    capacity: int
    role: str
    repair_hours: int = 3
    escort_bonus: int = 0


CRAFT = {
    "cutlass": Craft(
        "Drake Cutlass", space.CUTLASS_COST, 6, "Survey supplies", Q, 3, "Starter field expeditions"
    ),
    "transport": Craft(
        "GR-75 transport", space.TRANSPORT_COST, 12, "Cargo service pack", 2 * Q, 3, "Colony material convoys"
    ),
    "xwing": Craft(
        "T-65 X-wing",
        space.XWING_COST,
        8,
        "Proton torpedo",
        space.XWING_AMMO_COST,
        3,
        "Raider or escort",
        escort_bonus=15,
    ),
    "prospector": Craft(
        "MISC Prospector", 75 * Q, 8, "Mining service pack", Q, 3, "High-yield asteroid mining"
    ),
    "vulture": Craft("Drake Vulture", 65 * Q, 8, "Salvage service pack", Q, 3, "Finite debris salvage"),
    "carrack": Craft(
        "Anvil Carrack",
        200 * Q,
        12,
        "Sensor calibration pack",
        2 * Q,
        3,
        "Deep surveys and local fleet recon",
        4,
    ),
    "awing": Craft(
        "RZ-1 A-wing",
        80 * Q,
        8,
        "Concussion missile",
        3 * Q,
        4,
        "Fast convoy interceptor or escort",
        escort_bonus=25,
    ),
    "ywing": Craft("BTL-A4 Y-wing", 150 * Q, 10, "Proton bomb", 5 * Q, 3, "One recon-guided local objective"),
    "hammerhead": Craft(
        "Sphyrna Hammerhead Corvette",
        500 * Q,
        18,
        "Defense service pack",
        8 * Q,
        3,
        "Heavy convoy escort or outpost patrol",
        6,
        35,
    ),
    "interdictor": Craft(
        "Imperial Interdictor",
        750 * Q,
        24,
        "Gravity-well service pack",
        10 * Q,
        2,
        "Delay one travelling ordinary convoy",
        8,
    ),
    "arquitens": Craft(
        "Imperial Arquitens-class Command Cruiser",
        600 * Q,
        20,
        "Turbolaser capacitor pack",
        10 * Q,
        4,
        "Two-hull broadside or two-response security patrol",
        6,
        30,
    ),
}


def normalize(user: Dict[str, Any]) -> Dict[str, Any]:
    state = space.normalize(user).setdefault("fleet", {})
    for key, value in (("ships", {}), ("recon", {}), ("history", [])):
        if not isinstance(state.get(key), type(value)):
            state[key] = copy.deepcopy(value)
    return state


def ship(user: Dict[str, Any], key: str) -> Dict[str, Any]:
    if key not in CRAFT:
        raise ValueError("Choose an ordinary spacecraft from the fleet list.")
    value = normalize(user)["ships"].setdefault(key, {})
    for field, default in (
        ("owned", False),
        ("build_until", None),
        ("damaged", False),
        ("repair_until", None),
        ("payload", 0),
        ("payload_until", None),
        ("payload_qty", 0),
        ("mission", None),
        ("location", "earth"),
    ):
        value.setdefault(field, default)
    value.setdefault("combat_until", None)
    if "deployed" not in value:
        state = space.normalize(user)
        has_hull = bool(state.get(f"{key}_owned")) if key in LEGACY else bool(value["owned"])
        constructing = state.get(f"{key}_build_until") if key in LEGACY else value["build_until"]
        if constructing:
            value["location"] = "earth"
        legacy_active = (
            key == "cutlass"
            and (
                state["expedition"]
                or state.get("travel_craft") == "cutlass"
                or (
                    state["location"] != "earth"
                    and (not user.get("imperial_star_destroyer", {}).get("operational"))
                )
            )
            or (key == "transport" and state["load_until"])
        )
        value["deployed"] = bool(
            has_hull
            and (not constructing)
            and (
                value["mission"]
                or value["location"] != "earth"
                or legacy_active
                or any((isinstance(r, dict) and r.get("craft") == key for r in normalize(user)["history"]))
            )
        )
    value.setdefault("launch", None)
    return value


def deployed(user: Dict[str, Any], key: str) -> bool:
    hull = ship(user, key)
    return bool(owned(user, key) and hull["deployed"] and (not hull["launch"]))


def require_deployed(user: Dict[str, Any], key: str) -> None:
    if not deployed(user, key):
        raise ValueError(
            "Launch this spacecraft from Earth with `/space fleet launch` and finish its 30-minute interception window first."
        )


def launch(user: Dict[str, Any], key: str, current: dt.datetime, token: str) -> Dict[str, Any]:
    hull = ship(user, key)
    if (
        not owned(user, key)
        or build_until(user, key)
        or hull["deployed"]
        or hull["launch"]
        or (hull["location"] != "earth")
        or legacy_busy(user, key)
        or any((hull.get(k) for k in ("damaged", "repair_until", "mission", "payload_until")))
    ):
        raise ValueError(
            "Launch an owned, undamaged, idle ground spacecraft at Earth. Finish construction/preparation first."
        )
    record = dict(
        id=token,
        craft=key,
        kind="launch",
        body="earth",
        started_at=current.isoformat(),
        ready_at=(current + dt.timedelta(minutes=LAUNCH_MINUTES)).isoformat(),
        attempts=[],
    )
    hull["launch"] = record
    return record


def launch_intercept(
    doc: Dict[str, Any],
    uid: int,
    target_uid: int,
    key: str,
    counter: str,
    current: dt.datetime,
    token: str,
    *,
    roll: Optional[int] = None,
) -> Dict[str, Any]:
    _hostile(doc, uid, target_uid)
    victim = _users(doc).get(str(target_uid))
    user = _users(doc).get(str(uid))
    if not victim or not user or key not in CRAFT or (counter not in LAUNCH_COUNTERS):
        raise ValueError("Choose an active spacecraft launch and a suitable interceptor.")
    hull = ship(victim, key)
    record = hull["launch"]
    if (
        not isinstance(record, dict)
        or not space.at(record.get("ready_at"))
        or space.at(record["ready_at"]) <= current
    ):
        raise ValueError("That spacecraft has no open Earth-launch interception window.")
    if str(uid) in record["attempts"] or len(record["attempts"]) >= LAUNCH_ATTEMPTS:
        raise ValueError("One attempt per player, at most three different interceptors per launch.")
    if counter == "bwing":
        if key not in HEAVY_CRAFT:
            raise ValueError(
                "B-wing Ion Assault Packages target heavy Hammerhead, Interdictor or Arquitens launches."
            )
        package = isd.normalize(user)
        if int(package["counter_stock"]) < 1:
            raise ValueError("Build a ready B-wing package with `/isd counter-build` first.")
    else:
        _use(user, counter)
    public_chance = LAUNCH_COUNTERS[counter] - (15 if key in HEAVY_CRAFT and counter != "bwing" else 0)
    hit = (roll if roll is not None else random.Random(token).randint(1, 100)) <= public_chance
    if counter == "bwing":
        package["counter_stock"] -= 1
    else:
        set_payload(user, counter, payload(user, counter) - 1)
        ship(user, counter)["mission"] = dict(
            id=token,
            craft=counter,
            kind="launch-intercept",
            body="earth",
            ready_at=(current + dt.timedelta(minutes=30)).isoformat(),
            result="Launch intercepted" if hit else "Launch escaped",
        )
    record["attempts"].append(str(uid))
    if hit:
        hull.update(launch=None, deployed=False, damaged=True, payload_until=None, payload_qty=0)
        set_payload(victim, key, 0)
        _history(victim, record, "Earth launch intercepted; repair then launch again", current)
    return dict(hit=hit, chance=public_chance, craft=key, counter=counter)


def owned(user: Dict[str, Any], key: str) -> bool:
    return (
        bool(space.normalize(user).get(f"{key}_owned")) if key in LEGACY else bool(ship(user, key)["owned"])
    )


def build_until(user: Dict[str, Any], key: str) -> Optional[str]:
    return (
        space.normalize(user).get(f"{key}_build_until") if key in LEGACY else ship(user, key)["build_until"]
    )


def payload(user: Dict[str, Any], key: str) -> int:
    return int(space.normalize(user)["xwing_ammo"]) if key == "xwing" else int(ship(user, key)["payload"])


def set_payload(user: Dict[str, Any], key: str, amount: int) -> None:
    if key == "xwing":
        space.normalize(user)["xwing_ammo"] = max(0, amount)
    else:
        ship(user, key)["payload"] = max(0, amount)


def _pay(user: Dict[str, Any], amount: int) -> None:
    balance = visible_total(user)
    if amount < 0 or balance < amount:
        from .amounts import format_donuts

        raise ValueError(
            f"Requires {format_donuts(amount)} donuts. Wallet + vault: {format_donuts(balance)}; short by {format_donuts(max(0, amount - balance))}."
        )
    take_visible(user, amount)


def commission(user: Dict[str, Any], key: str, current: dt.datetime) -> int:
    spec, hull = (CRAFT[key], ship(user, key))
    if owned(user, key) or build_until(user, key):
        raise ValueError("You already own or are building this spacecraft.")
    _pay(user, spec.cost)
    hull.update(deployed=False, launch=None, location="earth")
    due = (current + dt.timedelta(hours=spec.build_hours)).isoformat()
    if key in LEGACY:
        space.normalize(user)[f"{key}_build_until"] = due
    else:
        hull["build_until"] = due
    return spec.cost


def prepare(user: Dict[str, Any], key: str, qty: int, current: dt.datetime) -> int:
    hull, spec = (ship(user, key), CRAFT[key])
    if not owned(user, key) or hull["damaged"] or hull["repair_until"] or hull["mission"] or hull["launch"]:
        raise ValueError("Need a ready, undamaged, idle spacecraft.")
    if hull["payload_until"]:
        raise ValueError("Payload preparation is already running.")
    if key == "transport" and space.normalize(user)["load_until"]:
        raise ValueError("Your GR-75 is busy loading the ISD.")
    if not 1 <= qty <= spec.capacity - payload(user, key):
        raise ValueError(f"Choose a quantity within the remaining capacity ({spec.capacity}).")
    cost = qty * spec.payload_cost
    _pay(user, cost)
    if key == "xwing":
        set_payload(user, key, payload(user, key) + qty)
    else:
        hull.update(payload_qty=qty, payload_until=(current + dt.timedelta(minutes=30)).isoformat())
    return cost


def repair(user: Dict[str, Any], key: str, current: dt.datetime) -> int:
    hull, spec = (ship(user, key), CRAFT[key])
    if (
        not owned(user, key)
        or not hull["damaged"]
        or hull["repair_until"]
        or hull["mission"]
        or hull["launch"]
    ):
        raise ValueError("This spacecraft does not need an idle depot repair.")
    cost = spec.cost // 4
    _pay(user, cost)
    hull["repair_until"] = (current + dt.timedelta(hours=spec.repair_hours)).isoformat()
    return cost


def settle_hulls(user: Dict[str, Any], current: dt.datetime) -> bool:
    changed = space.settle(user, current)
    for key in list(normalize(user)["ships"]):
        if key not in CRAFT:
            continue
        hull = ship(user, key)
        record = hull["launch"]
        due = space.at(record.get("ready_at")) if isinstance(record, dict) else None
        if due and due <= current:
            hull.update(launch=None, deployed=True)
            _history(user, record, "Earth launch completed; available in orbit", due)
            changed = True
        for timer, flag in (("build_until", "owned"), ("repair_until", "damaged")):
            due = space.at(hull[timer])
            if due and due <= current:
                hull[timer] = None
                hull[flag] = timer == "build_until"
                changed = True
        due = space.at(hull["payload_until"])
        if due and due <= current:
            set_payload(user, key, min(CRAFT[key].capacity, payload(user, key) + int(hull["payload_qty"])))
            hull.update(payload_until=None, payload_qty=0)
            changed = True
        due = space.at(hull["combat_until"])
        if due and due <= current:
            hull["combat_until"] = None
            changed = True
    for uid, entry in list(normalize(user)["recon"].items()):
        due = space.at(entry.get("expires_at"))
        if not due or due <= current:
            normalize(user)["recon"].pop(uid, None)
            changed = True
    return changed


def _use(user: Dict[str, Any], key: str) -> Dict[str, Any]:
    hull = ship(user, key)
    require_deployed(user, key)
    if (
        not owned(user, key)
        or hull["damaged"]
        or hull["repair_until"]
        or hull["mission"]
        or hull["payload_until"]
    ):
        raise ValueError("Build, repair or finish the current assignment before deploying this spacecraft.")
    if payload(user, key) < 1:
        raise ValueError("Prepare a payload with `/space fleet payload` first.")
    if key == "transport" and space.normalize(user)["load_until"]:
        raise ValueError("Your GR-75 is busy loading the ISD.")
    if key == "cutlass" and (space.normalize(user)["expedition"] or space.normalize(user)["travel_until"]):
        raise ValueError("Your Cutlass is already on a field expedition or voyage.")
    return hull


def legacy_busy(user: Dict[str, Any], key: str) -> bool:
    state = space.normalize(user)
    if key == "transport":
        return bool(state["load_until"])
    if key == "cutlass":
        return bool(state["expedition"] or (state["travel_until"] and state.get("travel_craft") != "isd"))
    return False


def _history(user: Dict[str, Any], record: Dict[str, Any], result: str, current: dt.datetime) -> None:
    rows = normalize(user)["history"]
    rows.append(
        {
            "id": record["id"],
            "craft": record["craft"],
            "kind": record["kind"],
            "body": record["body"],
            "result": result,
            "at": current.isoformat(),
        }
    )
    del rows[:-HISTORY_LIMIT]


def _users(doc: Dict[str, Any]) -> Dict[str, Any]:
    return doc.setdefault("users", {})


def _hostile(doc: Dict[str, Any], uid: int, target: int) -> None:
    if uid == target or allied(doc, uid, target):
        raise ValueError("You cannot attack yourself or an active coalition member.")


def _intact(doc: Dict[str, Any], body_key: str) -> space.Body:
    target = space.body(doc, body_key)
    if not target or body_key in {"earth", "sun"} or deathstar.destroyed(doc, body_key):
        raise ValueError("Choose an intact surveyed space body other than Earth or the Sun.")
    return target


def _mission(
    user: Dict[str, Any],
    key: str,
    kind: str,
    body_key: str,
    token: str,
    current: dt.datetime,
    hours: float,
    **extra: Any,
) -> Dict[str, Any]:
    hull = _use(user, key)
    record = dict(
        id=token,
        craft=key,
        kind=kind,
        body=body_key,
        ready_at=(current + dt.timedelta(hours=hours)).isoformat(),
        started_at=current.isoformat(),
        raids=[],
        interdicted=False,
        **extra,
    )
    set_payload(user, key, payload(user, key) - 1)
    hull["mission"] = record
    return record


def start(
    doc: Dict[str, Any],
    uid: int,
    key: str,
    kind: str,
    body_key: str,
    current: dt.datetime,
    token: str,
    *,
    target_uid: Optional[int] = None,
    objective: Optional[str] = None,
    source: Optional[str] = None,
    amount: int = 0,
    wreck_id: Optional[str] = None,
    second_objective: Optional[str] = None,
) -> Dict[str, Any]:
    user = _users(doc)[str(uid)]
    target_body = _intact(doc, body_key)
    _use(user, key)
    if second_objective and kind != "broadside":
        raise ValueError("A second objective is only available for an Arquitens broadside.")
    if body_key not in space.normalize(user)["surveys"]:
        raise ValueError("Survey this destination with `/space survey` first.")
    hours = max(2, space.travel_hours(target_body.zone))
    if kind == "mine" and key == "prospector":
        if target_body.kind != "asteroid":
            raise ValueError("The Prospector mines intact asteroids, not planets or moons.")
        return _mission(
            user,
            key,
            kind,
            body_key,
            token,
            current,
            hours + 2,
            materials={target_body.resource: 12 + 2 * target_body.score},
        )
    if kind == "survey" and key in {"cutlass", "carrack"}:
        from .activity_contracts import rare_bonus

        return _mission(
            user,
            key,
            kind,
            body_key,
            token,
            current,
            hours,
            materials={target_body.resource: 6 + target_body.score if key == "carrack" else 2},
            rare=random.Random(token).randint(1, 100) <= (30 if key == "carrack" else 10) + rare_bonus(user),
        )
    if kind == "salvage" and key == "vulture":
        root = space.galaxy(doc).setdefault("wrecks", {})
        if wreck_id:
            wreck = root.get(wreck_id)
            if not wreck or wreck["body"] != body_key or wreck.get("claimed_by"):
                raise ValueError("This wreck is missing, already claimed or at another body.")
            excluded = {int(v) for v in wreck.get("excluded", [])}
            if uid in excluded or any((allied(doc, uid, v) for v in excluded)):
                raise ValueError("You cannot salvage your own or allied battle losses.")
        else:
            wreck_id = f"natural-{body_key}-{current.date().isoformat()}"
            if root.get(wreck_id, {}).get("claimed_by"):
                raise ValueError("Today's natural debris at this body has already been claimed.")
            wreck = root.setdefault(
                wreck_id, {"body": body_key, "materials": {"metals": 8}, "donuts": 2 * Q, "excluded": []}
            )
        record = _mission(
            user,
            key,
            kind,
            body_key,
            token,
            current,
            hours,
            materials=copy.deepcopy(wreck["materials"]),
            payout=int(wreck["donuts"]),
            wreck=wreck_id,
        )
        wreck["claimed_by"] = str(uid)
        return record
    if kind == "supply" and key == "transport":
        receiver = _users(doc).get(str(target_uid or uid))
        if not receiver or (target_uid not in {None, uid} and (not allied(doc, uid, target_uid))):
            raise ValueError("Deliver only to your own or an active ally's colony.")
        source_site = space.normalize(user)["colonies"].get(source or "")
        dest_site = space.normalize(receiver)["colonies"].get(body_key)
        if (
            not source_site
            or not source_site.get("base")
            or (not dest_site)
            or (not dest_site.get("base"))
            or (source == body_key)
            or deathstar.destroyed(doc, source or "")
        ):
            raise ValueError("Supply routes need two different intact completed colony bases.")
        if not 1 <= amount <= min(100, space.material_total(source_site)):
            raise ValueError("Choose 1–100 available local materials for this convoy.")
        moved, remaining = ({}, amount)
        for resource in sorted(source_site["materials"]):
            count = min(remaining, max(0, int(source_site["materials"][resource])))
            if count:
                moved[resource] = count
            remaining -= count
            if not remaining:
                break
        record = _mission(
            user,
            key,
            kind,
            body_key,
            token,
            current,
            hours,
            source=source,
            recipient=str(target_uid or uid),
            materials=moved,
        )
        for resource, count in moved.items():
            source_site["materials"][resource] -= count
        return record
    if kind == "recon" and key == "carrack":
        if not target_uid or str(target_uid) not in _users(doc):
            raise ValueError("Choose a player with a space presence to scan.")
        _hostile(doc, uid, target_uid)
        return _mission(user, key, kind, body_key, token, current, hours, target=str(target_uid))
    if kind == "bomb" and key == "ywing":
        if not target_uid or str(target_uid) not in _users(doc):
            raise ValueError("Choose a target player.")
        _hostile(doc, uid, target_uid)
        intel = normalize(user)["recon"].get(str(target_uid), {})
        expires = space.at(intel.get("expires_at"))
        if nyx.blocks_snapshot(
            _users(doc)[str(target_uid)],
            expires - dt.timedelta(hours=RECON_HOURS) if expires else None,
            current,
        ):
            normalize(user)["recon"].pop(str(target_uid), None)
            raise ValueError(nyx.CENSORED)
        if (
            intel.get("body") != body_key
            or not space.at(intel.get("expires_at"))
            or space.at(intel["expires_at"]) <= current
        ):
            raise ValueError("A fresh Carrack scan of this player at this body is required (4h intel).")
        if objective not in objectives(_users(doc)[str(target_uid)], body_key, current):
            raise ValueError(
                "Select one live local mine, lab, ammunition depot or docked ordinary spacecraft."
            )
        return _mission(
            user, key, kind, body_key, token, current, 1, target=str(target_uid), objective=objective
        )
    if kind == "broadside" and key == "arquitens":
        if not target_uid or str(target_uid) not in _users(doc):
            raise ValueError("Choose a target player.")
        _hostile(doc, uid, target_uid)
        intel = normalize(user)["recon"].get(str(target_uid), {})
        expires = space.at(intel.get("expires_at"))
        if nyx.blocks_snapshot(
            _users(doc)[str(target_uid)],
            expires - dt.timedelta(hours=RECON_HOURS) if expires else None,
            current,
        ):
            normalize(user)["recon"].pop(str(target_uid), None)
            raise ValueError(nyx.CENSORED)
        if (
            intel.get("body") != body_key
            or not space.at(intel.get("expires_at"))
            or space.at(intel["expires_at"]) <= current
        ):
            raise ValueError("A fresh Carrack scan of this player at this body is required (4h intel).")
        cooldown = space.at(ship(user, key)["combat_until"])
        if cooldown and cooldown > current:
            raise ValueError(f"Arquitens broadside cooldown ends {cooldown.isoformat()}.")
        selected = [v for v in (objective, second_objective) if v]
        available = broadside_objectives(_users(doc)[str(target_uid)], body_key, current)
        if (
            not 1 <= len(selected) <= 2
            or len(set(selected)) != len(selected)
            or any((v not in available for v in selected))
        ):
            raise ValueError(
                "Select one or two different live docked spacecraft or active escorts at this body. Travelling industrial ships and superweapon equipment cannot be targeted."
            )
        return _mission(
            user, key, kind, body_key, token, current, 1, target=str(target_uid), objectives=selected
        )
    raise ValueError("That mission does not match this spacecraft's role. Check `/space fleet guide`.")


def objectives(user: Dict[str, Any], body_key: str, current: dt.datetime) -> list[str]:
    site = space.normalize(user)["colonies"].get(body_key, {})
    values = []
    for key in ("mine", "lab", "ammo_depot"):
        disabled = space.at(site.get(f"{key}_disabled_until"))
        if site.get(key) and (not disabled or disabled <= current):
            values.append(key)
    for key in CRAFT:
        hull = ship(user, key)
        if (
            owned(user, key)
            and hull["location"] == body_key
            and (not legacy_busy(user, key))
            and (not any((hull.get(k) for k in ("mission", "damaged", "repair_until", "payload_until"))))
        ):
            values.append(f"ship:{key}")
    return values


def broadside_objectives(user: Dict[str, Any], body_key: str, current: dt.datetime) -> list[str]:
    """Only docked hulls and active escorts; never industrial mission escrow."""
    values = [v for v in objectives(user, body_key, current) if v.startswith("ship:")]
    for key in CRAFT:
        hull = ship(user, key)
        record = hull["mission"]
        if (
            owned(user, key)
            and (not hull["damaged"])
            and (not hull["repair_until"])
            and isinstance(record, dict)
            and (record.get("kind") == "escort")
            and (record.get("body") == body_key)
            and space.at(record.get("ready_at"))
            and (space.at(record["ready_at"]) > current)
            and (not space.at(record.get("started_at")) or space.at(record["started_at"]) <= current)
        ):
            values.append(f"ship:{key}")
    return values


def snapshot(user: Dict[str, Any], body_key: str, current: dt.datetime) -> list[str]:
    if nyx.active(user, current):
        return [nyx.CENSORED]
    rows = []
    site = space.normalize(user)["colonies"].get(body_key, {})
    for name in ("base", "mine", "lab", "ammo_depot"):
        if site.get(name):
            end = space.at(site.get(f"{name}_disabled_until"))
            rows.append(
                f"{name.replace('_', ' ').title()}: {('disabled' if end and end > current else 'active')}"
            )
    for key, spec in CRAFT.items():
        hull = ship(user, key)
        mission = hull["mission"]
        if owned(user, key) and (
            hull["location"] == body_key or (isinstance(mission, dict) and mission["body"] == body_key)
        ):
            status = (
                f"{mission['kind']} until {mission.get('ready_at') or 'deployment window resolves'}"
                if mission
                else "damaged"
                if hull["damaged"]
                else "docked"
            )
            rows.append(f"{spec.name}: {status} · payload {payload(user, key)}/{spec.capacity}")
    return rows or ["No ordinary fleet or completed colony structures detected here."]


def escort(
    doc: Dict[str, Any], uid: int, key: str, protected: int, body_key: str, current: dt.datetime, token: str
) -> Dict[str, Any]:
    if not CRAFT[key].escort_bonus:
        raise ValueError("Use an X-wing, A-wing, Hammerhead or Arquitens for escort duty.")
    _intact(doc, body_key)
    if protected != uid and (not allied(doc, uid, protected)):
        raise ValueError("Escort yourself or an active coalition ally.")
    if str(protected) not in _users(doc):
        raise ValueError("This player has no space account.")
    user = _users(doc)[str(uid)]
    if body_key not in space.normalize(user)["surveys"]:
        raise ValueError("Survey this patrol body first.")
    if key == "arquitens" and payload(user, key) < 2:
        raise ValueError("Arquitens security patrol requires two capacitor packs.")
    record = _mission(
        user,
        key,
        "escort",
        body_key,
        token,
        current,
        ESCORT_HOURS,
        protected=str(protected),
        responses_remaining=2 if key == "arquitens" else 1,
    )
    if key == "arquitens":
        set_payload(user, key, payload(user, key) - 1)
    return record


def _defenders(doc: Dict[str, Any], protected: int, body_key: str, current: dt.datetime):
    values = []
    for raw_uid, user in _users(doc).items():
        if int(raw_uid) != protected and (
            not coalitions.are_allied_at(doc, int(raw_uid), protected, current)
        ):
            continue
        for key in ("xwing", "awing", "hammerhead", "arquitens"):
            hull = ship(user, key)
            record = hull["mission"]
            if (
                owned(user, key)
                and (not hull["damaged"])
                and isinstance(record, dict)
                and (record["kind"] == "escort")
                and (record["body"] == body_key)
                and (str(record["protected"]) == str(protected))
                and (not space.at(record.get("started_at")) or space.at(record["started_at"]) <= current)
                and (int(record.get("responses_remaining", 1)) > 0)
                and (space.at(record["ready_at"]) > current)
            ):
                values.append((user, key, record))
    return sorted(values, key=lambda v: CRAFT[v[1]].escort_bonus, reverse=True)[:1]


def _respond_escorts(defenders, body_key: str, current: dt.datetime, encounter: str) -> None:
    for defender, key, record in defenders:
        hull = ship(defender, key)
        if hull["damaged"] or hull["mission"] is not record:
            continue
        record["responses_remaining"] = max(0, int(record.get("responses_remaining", 1)) - 1)
        hull["location"] = body_key
        if not record["responses_remaining"]:
            hull["mission"] = None
        _history(
            defender,
            record,
            f"Escort responded to {encounter}; {record['responses_remaining']} response(s) remaining",
            current,
        )


def _damage(
    doc: Dict[str, Any],
    user: Dict[str, Any],
    uid: int,
    key: str,
    attacker: int,
    body_key: str,
    token: str,
    current: dt.datetime,
) -> None:
    hull = ship(user, key)
    if hull["damaged"]:
        return
    lost = payload(user, key)
    hull.update(damaged=True, payload_until=None, payload_qty=0, mission=None, location=body_key)
    set_payload(user, key, 0)
    root = space.galaxy(doc).setdefault("wrecks", {})
    excluded = {uid, attacker}
    for member in (uid, attacker):
        for candidate in _users(doc):
            if coalitions.are_allied_at(doc, member, int(candidate), current):
                excluded.add(int(candidate))
    root[f"wreck-{token}-{uid}-{key}"] = {
        "body": body_key,
        "materials": {"metals": 6},
        "donuts": min(CRAFT[key].cost // 40, max(Q, lost * CRAFT[key].payload_cost // 10)),
        "excluded": sorted(excluded),
        "at": current.isoformat(),
    }
    if len(root) > 500:
        removable = [
            old
            for old in root
            if not old.startswith("natural-") or not old.endswith(current.date().isoformat())
        ]
        for old in removable[: len(root) - 500]:
            root.pop(old, None)


def raid(
    doc: Dict[str, Any],
    uid: int,
    key: str,
    target_uid: int,
    victim_craft: str,
    current: dt.datetime,
    token: str,
    *,
    roll: Optional[int] = None,
) -> Dict[str, Any]:
    if key not in {"xwing", "awing", "interdictor"}:
        raise ValueError("Use an X-wing/A-wing to raid, or an Interdictor to delay a convoy.")
    _hostile(doc, uid, target_uid)
    victim = _users(doc).get(str(target_uid))
    if not victim or victim_craft not in CRAFT:
        raise ValueError("Choose a real active ordinary spacecraft mission.")
    target_hull = ship(victim, victim_craft)
    record = target_hull["mission"]
    if (
        not isinstance(record, dict)
        or record["kind"] not in {"mine", "supply", "salvage"}
        or space.at(record["ready_at"]) <= current
    ):
        raise ValueError("This craft has no active mining, salvage or material convoy to intercept.")
    body_key = record["body"]
    _intact(doc, body_key)
    if len(record["raids"]) >= MAX_RAIDS or str(uid) in record["raids"]:
        raise ValueError("This mission has reached its raid limit or you already raided it.")
    if key == "interdictor" and record["interdicted"]:
        raise ValueError("This convoy has already been delayed; the delay cannot be chained.")
    user = _users(doc)[str(uid)]
    hull = _use(user, key)
    defenders = _defenders(doc, target_uid, body_key, current)
    public_chance = (70 if key == "interdictor" else 65) - sum(
        (CRAFT[k].escort_bonus for _, k, _ in defenders)
    )
    hit = (roll if roll is not None else random.Random(token).randint(1, 100)) <= public_chance
    set_payload(user, key, payload(user, key) - 1)
    record["raids"].append(str(uid))
    hull["location"] = body_key
    own_record = dict(
        id=token,
        craft=key,
        kind="interdict" if key == "interdictor" else "raid",
        body=body_key,
        ready_at=(current + dt.timedelta(hours=2)).isoformat(),
        result="Hit" if hit else "Missed",
    )
    hull["mission"] = own_record
    if hit:
        if key == "interdictor":
            record["ready_at"] = (
                space.at(record["ready_at"]) + dt.timedelta(minutes=INTERDICTION_MINUTES)
            ).isoformat()
            record["interdicted"] = True
        else:
            for resource, count in list(record.get("materials", {}).items()):
                record["materials"][resource] = int(count) - int(count) * 25 // 100
            record["payout"] = int(record.get("payout", 0)) * 75 // 100
    elif defenders:
        if random.Random(token + "-damage").randint(1, 100) <= 25:
            _damage(doc, user, uid, key, target_uid, body_key, token, current)
            _history(user, own_record, "Intercepted; depot repair required", current)
    _respond_escorts(defenders, body_key, current, "a raid")
    return {
        "hit": hit,
        "chance": public_chance,
        "damaged": hull["damaged"],
        "body": body_key,
        "delayed_until": record["ready_at"] if hit and key == "interdictor" else None,
    }


def _store(user: Dict[str, Any], body_key: str, materials: Dict[str, int]) -> None:
    site = space.normalize(user)["colonies"].get(body_key, {})
    bag = site.setdefault("materials", {}) if site.get("base") else space.normalize(user)["field_materials"]
    for key, count in materials.items():
        bag[key] = int(bag.get(key, 0)) + max(0, int(count))


def _settle_timers(doc: Dict[str, Any], instant: dt.datetime) -> bool:
    changed = False
    for user in _users(doc).values():
        if not isinstance(user, dict):
            continue
        changed = settle_hulls(user, instant) or changed
        for site in space.normalize(user)["colonies"].values():
            if not isinstance(site, dict):
                continue
            due = space.at(site.get("ammo_depot_until"))
            if due and due <= instant:
                site.update(ammo_depot_until=None, ammo_depot=True)
                changed = True
    return changed


def settle_world(doc: Dict[str, Any], current: dt.datetime, *, events: Optional[list] = None) -> bool:
    changed = coalitions.remember_membership(doc)
    pending = []
    for raw_uid, user in list(_users(doc).items()):
        if not isinstance(user, dict):
            continue
        for key in list(normalize(user)["ships"]):
            if key not in CRAFT:
                continue
            record = ship(user, key)["mission"]
            due = space.at(record.get("ready_at")) if isinstance(record, dict) else None
            if due and due <= current:
                pending.append((due, raw_uid, key, user, record))
    for resolved_at, raw_uid, key, user, record in sorted(
        pending, key=lambda row: (row[0], str(row[4].get("id", "")), row[1], row[2])
    ):
        changed = _settle_timers(doc, resolved_at) or changed
        hull = ship(user, key)
        if hull["mission"] is not record:
            continue
        hull["mission"] = None
        changed = True
        body_key = record["body"]
        if deathstar.destroyed(doc, body_key):
            _history(
                user,
                record,
                "Mission aborted: destination destroyed; exposed mission cargo lost",
                resolved_at,
            )
            continue
        hull["location"] = body_key
        kind = record["kind"]
        result = "Completed"
        contract_eligible = False
        if kind in {"mine", "salvage", "survey"}:
            contract_eligible = True
            _store(user, body_key, record.get("materials", {}))
            if kind == "salvage":
                user["donuts"] = int(user.get("donuts", 0)) + int(record.get("payout", 0))
            if kind == "survey":
                entry = space.normalize(user)["journal"].setdefault(
                    body_key, {"name": space.body(doc, body_key).name, "missions": 0, "discoveries": []}
                )
                entry["missions"] += 1
                label = "Unusual deep-space specimen" if record.get("rare") else "Deep-space specimen"
                if label not in entry["discoveries"]:
                    entry["discoveries"].append(label)
                entry["last_result"] = "rare" if record.get("rare") else "standard"
                entry["last_completed_at"] = record["ready_at"]
                space.award_exploration_milestones(user, record["ready_at"])
            result = f"Collected {sum(record.get('materials', {}).values())} materials" + (
                " and salvage donuts" if kind == "salvage" else ""
            )
        elif kind == "supply":
            receiver = _users(doc).get(record["recipient"])
            site = space.normalize(receiver)["colonies"].get(body_key, {}) if receiver else {}
            if site.get("base") and (
                record["recipient"] == raw_uid
                or coalitions.are_allied_at(doc, int(raw_uid), int(record["recipient"]), resolved_at)
            ):
                _store(receiver, body_key, record["materials"])
                result = "Material convoy delivered"
                contract_eligible = True
            else:
                _store(user, record.get("source", body_key), record["materials"])
                result = "Delivery unavailable; surviving cargo returned to sender"
        elif kind == "recon":
            target = _users(doc).get(record["target"])
            if target and (
                nyx.blocks_snapshot(target, resolved_at, current) or nyx.active(target, resolved_at)
            ):
                normalize(user)["recon"].pop(record["target"], None)
                result = nyx.CENSORED
            elif target:
                normalize(user)["recon"][record["target"]] = {
                    "body": body_key,
                    "expires_at": (
                        space.at(record["ready_at"]) + dt.timedelta(hours=RECON_HOURS)
                    ).isoformat(),
                    "rows": snapshot(target, body_key, resolved_at),
                }
                result = "Local fleet scan complete; intel lasts 4h"
        elif kind == "bomb":
            target = _users(doc).get(record["target"])
            if (
                not target
                or coalitions.are_allied_at(doc, int(raw_uid), int(record["target"]), resolved_at)
                or record["objective"] not in objectives(target, body_key, resolved_at)
            ):
                result = "Bombing aborted: objective unavailable or now allied"
            else:
                defenders = _defenders(doc, int(record["target"]), body_key, resolved_at)
                chance = 80 - sum((CRAFT[k].escort_bonus for _, k, _ in defenders))
                hit = random.Random(record["id"]).randint(1, 100) <= chance
                if hit:
                    objective = record["objective"]
                    if objective.startswith("ship:"):
                        _damage(
                            doc,
                            target,
                            int(record["target"]),
                            objective[5:],
                            int(raw_uid),
                            body_key,
                            record["id"],
                            resolved_at,
                        )
                    else:
                        site = space.normalize(target)["colonies"][body_key]
                        due = resolved_at + dt.timedelta(hours=DISABLE_HOURS)
                        if objective == "mine":
                            target_body = space.body(doc, body_key)
                            space.settle_materials(site, target_body, resolved_at)
                            anchor = space.at(site.get("material_at")) or resolved_at
                            site["material_at"] = (anchor + dt.timedelta(hours=DISABLE_HOURS)).isoformat()
                        site[f"{objective}_disabled_until"] = due.isoformat()
                        if objective == "ammo_depot":
                            site["ammo_stock"] = {}
                    result = f"Precision hit: {objective}; depot repair or 4h disruption"
                    if events is not None:
                        events.append(
                            {
                                "attacker": int(raw_uid),
                                "victim": int(record["target"]),
                                "detail": f"BTL-A4 Y-wing / {objective} at {body_key}",
                                "at": resolved_at,
                            }
                        )
                else:
                    result = "Precision strike intercepted or missed"
                    if defenders and random.Random(record["id"] + "-damage").randint(1, 100) <= 25:
                        _damage(
                            doc,
                            user,
                            int(raw_uid),
                            key,
                            int(record["target"]),
                            body_key,
                            record["id"],
                            resolved_at,
                        )
                        result += "; Y-wing needs repair"
                _respond_escorts(defenders, body_key, resolved_at, "a precision strike")
        elif kind == "broadside":
            hull["combat_until"] = (resolved_at + dt.timedelta(hours=ARQUITENS_COOLDOWN_HOURS)).isoformat()
            target = _users(doc).get(record["target"])
            if not target or coalitions.are_allied_at(doc, int(raw_uid), int(record["target"]), resolved_at):
                result = "Broadside aborted: target unavailable or now allied"
            else:
                available = broadside_objectives(target, body_key, resolved_at)
                selected = [v for v in record["objectives"] if v in available]
                if not selected:
                    result = "Broadside aborted: selected spacecraft no longer available"
                else:
                    defenders = _defenders(doc, int(record["target"]), body_key, resolved_at)
                    chance = ARQUITENS_HIT_CHANCE - sum((CRAFT[k].escort_bonus for _, k, _ in defenders))
                    hits = []
                    for objective in selected:
                        if random.Random(record["id"] + "-" + objective).randint(1, 100) <= chance:
                            _damage(
                                doc,
                                target,
                                int(record["target"]),
                                objective[5:],
                                int(raw_uid),
                                body_key,
                                record["id"],
                                resolved_at,
                            )
                            hits.append(CRAFT[objective[5:]].name)
                    result = (
                        "Broadside hit: " + ", ".join(hits) if hits else "Broadside intercepted or missed"
                    )
                    if hits and events is not None:
                        events.append(
                            {
                                "attacker": int(raw_uid),
                                "victim": int(record["target"]),
                                "detail": "Arquitens broadside / " + ", ".join(hits) + f" at {body_key}",
                                "at": resolved_at,
                            }
                        )
                    if (
                        not hits
                        and defenders
                        and (
                            random.Random(record["id"] + "-damage").randint(1, 100)
                            <= ARQUITENS_RETALIATION_CHANCE
                        )
                    ):
                        _damage(
                            doc,
                            user,
                            int(raw_uid),
                            key,
                            int(record["target"]),
                            body_key,
                            record["id"],
                            resolved_at,
                        )
                        result += "; Arquitens needs repair"
                    _respond_escorts(defenders, body_key, resolved_at, "an Arquitens broadside")
        elif kind in {"raid", "interdict", "launch-intercept"}:
            result = record.get("result", "Completed")
        if contract_eligible:
            from .activity_contracts import complete_space

            bonus = complete_space(user, record, resolved_at)
            if bonus:
                from .amounts import format_donuts

                result += f"; civilian contract paid {format_donuts(bonus)} + bonus materials"
        _history(user, record, result, resolved_at)
    changed = _settle_timers(doc, current) or changed
    return changed


def depot(
    user: Dict[str, Any],
    doc: Dict[str, Any],
    body_key: str,
    action: str,
    key: Optional[str],
    qty: int,
    current: dt.datetime,
) -> int:
    _intact(doc, body_key)
    site = space.normalize(user)["colonies"].get(body_key, {})
    if not site.get("base"):
        raise ValueError("Build a completed colony base at this body first.")
    if action == "build":
        if site.get("ammo_depot") or site.get("ammo_depot_until"):
            raise ValueError("This base already owns or is building an ammunition depot.")
        cost = 50 * Q
        _pay(user, cost)
        site.update(ammo_depot_until=(current + dt.timedelta(hours=6)).isoformat(), ammo_stock={})
        return cost
    due = space.at(site.get("ammo_depot_disabled_until"))
    if not site.get("ammo_depot") or (due and due > current):
        raise ValueError("Finish building or wait for your depot's disabled period to end.")
    if key not in CRAFT or not 1 <= qty <= 6:
        raise ValueError("Choose an ordinary spacecraft and 1–6 service packs/ammunition.")
    stock = site.setdefault("ammo_stock", {})
    count = int(stock.get(key, 0))
    if action == "stock":
        if count + qty > 6:
            raise ValueError("A local depot stores at most six payloads per craft type.")
        cost = CRAFT[key].payload_cost * qty
        _pay(user, cost)
        stock[key] = count + qty
        return cost
    if action == "load":
        hull = ship(user, key)
        if (
            not owned(user, key)
            or legacy_busy(user, key)
            or hull["location"] != body_key
            or any((hull.get(k) for k in ("mission", "damaged", "repair_until", "payload_until")))
        ):
            raise ValueError("Load an owned, undamaged, idle hull docked at this body's depot.")
        if qty > count or payload(user, key) + qty > CRAFT[key].capacity:
            raise ValueError("Insufficient local stock or onboard capacity.")
        stock[key] = count - qty
        set_payload(user, key, payload(user, key) + qty)
        return 0
    raise ValueError("Choose depot build, stock or load.")


def wipe(user: Dict[str, Any]) -> None:
    """Erase all ordinary fleet state for a full server/planet reset."""
    space.normalize(user)["fleet"] = {}


def thor_impact(user: Dict[str, Any]) -> Dict[str, int]:
    """Destroy Earth-ground hulls only, preserving launched hulls and missions.

    `location == earth` also represents Earth ORBIT for deployed hulls. A
    launch window is already airborne; a damaged aborted launch is grounded.
    Normalize every legacy hull before changing any legacy ownership fields.
    """
    state = space.normalize(user)
    hulls = {key: ship(user, key) for key in CRAFT}
    losses = {"vehicles": 0, "ammo": 0}
    for key, hull in hulls.items():
        constructing = build_until(user, key)
        if not constructing and (hull["launch"] or hull["deployed"] or hull["location"] != "earth"):
            continue
        losses["vehicles"] += int(bool(owned(user, key) or constructing))
        losses["ammo"] += max(0, payload(user, key)) + max(0, int(hull["payload_qty"]))
        normalize(user)["ships"].pop(key, None)
        if key in LEGACY:
            state[f"{key}_owned"] = False
            state[f"{key}_build_until"] = None
        if key == "xwing":
            state["xwing_ammo"] = 0
        if key == "cutlass":
            state["expedition"] = None
            if state.get("travel_craft") == "cutlass":
                state.update(travel_craft=None, travel_until=None, destination=None, location="earth")
    return losses


def summary(user: Dict[str, Any]) -> list[str]:
    rows = []
    for key, spec in CRAFT.items():
        hull = ship(user, key)
        due = build_until(user, key)
        if not owned(user, key) and (not due):
            continue
        status = (
            "building"
            if due
            else "repairing"
            if hull["repair_until"]
            else "damaged"
            if hull["damaged"]
            else "launching"
            if hull["launch"]
            else "ready"
            if hull["deployed"]
            else "grounded; launch required"
        )
        mission = hull["mission"]
        location = "Earth orbit" if hull["location"] == "earth" and hull["deployed"] else hull["location"]
        rows.append(
            f"{spec.name}: {status} · payload {payload(user, key)}/{spec.capacity} · {location}"
            + (f" · {mission['kind']} at {mission['body']}" if mission else "")
            + (
                f" · {mission.get('responses_remaining', 1)} patrol response(s)"
                if mission and mission["kind"] == "escort"
                else ""
            )
            + (
                f" · window {mission['window_id']} · {mission.get('responses_remaining', 0)} reserved launch responses"
                if mission and mission["kind"] == "launch-escort"
                else ""
            )
            + (
                f" · targets {', '.join(mission['objectives'])}"
                if mission and mission.get("objectives")
                else ""
            )
        )
    return rows
