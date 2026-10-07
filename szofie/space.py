"""Solar-system expedition rules and restart-safe player/world state.

Scientific labels are deliberately conservative: resource scores are game
indices, not claims of measured reserves or monetary valuations.
"""

from __future__ import annotations
import copy
import datetime as dt
import random
from dataclasses import dataclass
from typing import Any, Dict, Optional

QA = 10**15
QI = 10**18
LOAD_HOURS = 24
LOAD_BASE_COST = 50 * QA
TRANSPORT_COST = 100 * QA
XWING_COST = 30 * QA
XWING_BUILD_HOURS = 8
XWING_AMMO_COST = 5 * QA
XWING_AMMO_CAP = 3
REFIT_COST = 25 * QA
REFIT_HOURS = 12
MAX_INTERCEPTIONS = 2
INTERCEPTION_CHANCE = 25
BASE_COST = 100 * QA
MINE_COST = 150 * QA
LAB_COST = 250 * QA
TERRAFORM_COST = 500 * QA
CUTLASS_COST = 50 * QA
CUTLASS_BUILD_HOURS = 6
EXPEDITION_HOURS = 6
MINING_CATCHUP_CYCLES = 28
SPECIALISATION_CHANGE_COST = 25 * QA
SPECIALISATIONS = {
    "industrial": "+25% automatic materials",
    "research": "+10 percentage points rare discovery chance",
    "civilian": "+20% colony wallet production",
}


@dataclass(frozen=True)
class Body:
    key: str
    name: str
    kind: str
    zone: str
    resource: str
    score: int
    hazard: int
    landable: bool = True


_ROWS = [
    ("sun", "Sun", "star", "inner", "solar-energy", 0, 10, False),
    ("mercury", "Mercury", "planet", "inner", "metals", 5, 8, True),
    ("venus", "Venus", "planet", "inner", "silicates", 5, 10, True),
    ("earth", "Earth", "planet", "inner", "habitat", 0, 0, False),
    ("mars", "Mars", "planet", "inner", "water-ice", 7, 5, True),
    ("jupiter", "Jupiter", "planet", "outer", "atmospheric-gas", 8, 10, False),
    ("saturn", "Saturn", "planet", "outer", "atmospheric-gas", 7, 9, False),
    ("uranus", "Uranus", "planet", "far", "atmospheric-gas", 6, 9, False),
    ("neptune", "Neptune", "planet", "far", "atmospheric-gas", 6, 10, False),
    ("ceres", "Ceres", "dwarf", "belt", "water-ice", 8, 4, True),
    ("pluto", "Pluto", "dwarf", "far", "volatile-ice", 6, 8, True),
    ("haumea", "Haumea", "dwarf", "far", "volatile-ice", 5, 9, True),
    ("makemake", "Makemake", "dwarf", "far", "volatile-ice", 5, 9, True),
    ("eris", "Eris", "dwarf", "far", "volatile-ice", 5, 10, True),
    ("luna", "Moon", "moon", "inner", "water-ice", 5, 3, True),
    ("phobos", "Phobos", "moon", "inner", "silicates", 3, 4, True),
    ("deimos_moon", "Deimos (moon)", "moon", "inner", "silicates", 3, 4, True),
    ("io", "Io", "moon", "outer", "sulfur", 7, 10, True),
    ("europa_moon", "Europa (moon)", "moon", "outer", "water-ice", 8, 8, True),
    ("ganymede", "Ganymede", "moon", "outer", "water-ice", 8, 7, True),
    ("callisto", "Callisto", "moon", "outer", "water-ice", 7, 6, True),
    ("titan", "Titan", "moon", "outer", "hydrocarbons", 9, 7, True),
    ("enceladus", "Enceladus", "moon", "outer", "water-ice", 7, 8, True),
    ("rhea", "Rhea", "moon", "outer", "water-ice", 5, 7, True),
    ("dione", "Dione", "moon", "outer", "water-ice", 5, 7, True),
    ("iapetus", "Iapetus", "moon", "outer", "water-ice", 4, 8, True),
    ("mimas", "Mimas", "moon", "outer", "water-ice", 4, 8, True),
    ("tethys", "Tethys", "moon", "outer", "water-ice", 5, 7, True),
    ("miranda", "Miranda", "moon", "far", "water-ice", 4, 9, True),
    ("ariel", "Ariel", "moon", "far", "water-ice", 5, 8, True),
    ("umbriel", "Umbriel", "moon", "far", "water-ice", 4, 8, True),
    ("titania", "Titania", "moon", "far", "water-ice", 6, 8, True),
    ("oberon", "Oberon", "moon", "far", "water-ice", 5, 8, True),
    ("triton", "Triton", "moon", "far", "volatile-ice", 7, 9, True),
    ("charon", "Charon", "moon", "far", "volatile-ice", 5, 9, True),
    ("vesta", "Vesta", "asteroid", "belt", "silicates", 6, 4, True),
    ("pallas", "Pallas", "asteroid", "belt", "silicates", 5, 5, True),
    ("psyche", "Psyche", "asteroid", "belt", "metals", 8, 5, True),
    ("juno", "Juno", "asteroid", "belt", "silicates", 5, 5, True),
    ("hygiea", "Hygiea", "asteroid", "belt", "carbon", 6, 5, True),
    ("eros", "Eros", "asteroid", "inner", "silicates", 4, 5, True),
    ("bennu", "Bennu", "asteroid", "inner", "carbon", 5, 6, True),
    ("ryugu", "Ryugu", "asteroid", "inner", "carbon", 5, 6, True),
]
BODIES = {row[0]: Body(*row) for row in _ROWS}
GIANT_PLANETS = frozenset({"jupiter", "saturn", "uranus", "neptune"})
JPL_ALIASES = {
    "20000001": "ceres",
    "20000002": "pallas",
    "20000003": "juno",
    "20000004": "vesta",
    "20000010": "hygiea",
    "20000016": "psyche",
    "20000433": "eros",
    "20101955": "bennu",
    "20162173": "ryugu",
}


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def at(raw: Any) -> Optional[dt.datetime]:
    if not raw:
        return None
    try:
        value = dt.datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    return (
        value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value.astimezone(dt.timezone.utc)
    )


def default_user() -> Dict[str, Any]:
    return {
        "mode": "attack",
        "refit_until": None,
        "refit_target": None,
        "transport_owned": False,
        "transport_build_until": None,
        "xwing_owned": False,
        "xwing_build_until": None,
        "xwing_ammo": 0,
        "cutlass_owned": False,
        "cutlass_build_until": None,
        "expedition": None,
        "journal": {},
        "field_materials": {},
        "fleet": {},
        "field_mine_at": None,
        "milestones": [],
        "cargo": {
            "donuts": 0,
            "inventory": {},
            "plushies": {},
            "fish": {},
            "rods": {},
            "vehicles": {},
            "materials": {},
        },
        "load_until": None,
        "load_manifest": None,
        "load_hits": 0,
        "interceptors": [],
        "location": "earth",
        "travel_until": None,
        "destination": None,
        "travel_craft": None,
        "last_bombard_at": None,
        "colonies": {},
        "surveys": {},
    }


def normalize(user: Dict[str, Any]) -> Dict[str, Any]:
    value = user.get("space")
    if not isinstance(value, dict):
        value = user["space"] = default_user()
    for key, default in default_user().items():
        value.setdefault(key, copy.deepcopy(default))
    for key in ("cargo", "colonies", "surveys", "journal", "field_materials", "fleet"):
        if not isinstance(value.get(key), dict):
            value[key] = copy.deepcopy(default_user()[key])
    for key in ("inventory", "plushies", "fish", "rods", "vehicles", "materials"):
        if not isinstance(value["cargo"].get(key), dict):
            value["cargo"][key] = {}
    if value.get("mode") not in {"attack", "expedition"}:
        value["mode"] = "attack"
    return value


def galaxy(doc: Dict[str, Any]) -> Dict[str, Any]:
    value = doc.get("space_galaxy")
    if not isinstance(value, dict):
        value = doc["space_galaxy"] = {"claims": {}, "discovered": {}}
    value.setdefault("claims", {})
    value.setdefault("discovered", {})
    return value


def body(doc: Dict[str, Any], key: str) -> Optional[Body]:
    found = BODIES.get(key)
    if found:
        return found
    raw = galaxy(doc)["discovered"].get(key)
    if not isinstance(raw, dict):
        return None
    return Body(
        key,
        str(raw.get("name", key)),
        str(raw.get("kind", "asteroid")),
        str(raw.get("zone", "belt")),
        str(raw.get("resource", "unknown")),
        int(raw.get("score", 2)),
        int(raw.get("hazard", 7)),
        bool(raw.get("landable", False)),
    )


def colonisable(target: Body) -> bool:
    return target.landable or target.key in GIANT_PLANETS


def settle(user: Dict[str, Any], instant: Optional[dt.datetime] = None) -> bool:
    instant = instant or now()
    state = normalize(user)
    changed = False
    for end_key, flag_key in (
        ("transport_build_until", "transport_owned"),
        ("xwing_build_until", "xwing_owned"),
        ("cutlass_build_until", "cutlass_owned"),
    ):
        if at(state.get(end_key)) and at(state[end_key]) <= instant:
            state[end_key] = None
            state[flag_key] = True
            changed = True
    if at(state.get("refit_until")) and at(state["refit_until"]) <= instant:
        state["mode"] = state.get("refit_target") or "attack"
        state["refit_target"] = None
        state["refit_until"] = None
        changed = True
    if at(state.get("load_until")) and at(state["load_until"]) <= instant:
        manifest = state.get("load_manifest") or {}
        cargo = state["cargo"]
        cargo["donuts"] = int(cargo.get("donuts", 0)) + int(manifest.get("donuts", 0))
        for field in ("inventory", "plushies", "fish", "rods", "vehicles"):
            for key, amount in manifest.get(field, {}).items():
                if field in {"vehicles", "rods"}:
                    cargo[field][key] = amount
                else:
                    cargo[field][key] = int(cargo[field].get(key, 0)) + int(amount)
        state.update(load_until=None, load_manifest=None, load_hits=0, interceptors=[])
        changed = True
    if at(state.get("travel_until")) and at(state["travel_until"]) <= instant:
        state["location"] = state.get("destination") or state["location"]
        if state.get("travel_craft") == "cutlass":
            hull = state["fleet"].setdefault("ships", {}).setdefault("cutlass", {})
            hull["location"] = state["location"]
            if state["location"] == "earth":
                hull["deployed"] = False
        state["destination"] = None
        state["travel_until"] = None
        state["travel_craft"] = None
        changed = True
    return changed


def colony(state: Dict[str, Any], key: str) -> Dict[str, Any]:
    value = state["colonies"].setdefault(
        key,
        {
            "landed": False,
            "base": False,
            "mine": False,
            "lab": False,
            "terraform": False,
            "viability": 0,
            "materials": {},
            "mine_at": None,
            "income_at": None,
            "gdp": 0,
            "development": 0,
            "material_at": None,
            "material_remainder": 0,
            "specialisation": None,
        },
    )
    for field, default in (
        ("material_at", None),
        ("material_remainder", 0),
        ("specialisation", None),
        ("materials", {}),
    ):
        value.setdefault(field, copy.deepcopy(default))
    return value


def travel_hours(zone: str) -> int:
    return {"inner": 2, "belt": 4, "outer": 8, "far": 12}[zone]


def gdp(body_score: int, hazard: int) -> int:
    """Fictional colony GDP index, not an Earth-currency resource valuation."""
    return max(1, body_score * 100 - hazard * 30) * QA


def daily_income(gdp_value: int) -> int:
    return int(gdp_value) // 80


def colony_daily_income(site: Dict[str, Any]) -> int:
    income = daily_income(int(site.get("gdp", 0)))
    return income * 120 // 100 if site.get("specialisation") == "civilian" else income


def settle_colony_income(
    user: Dict[str, Any], colony_state: Dict[str, Any], current: dt.datetime
) -> tuple[int, bool]:
    """Pay at most seven days once, preserving sub-hour time and donut fractions.

    Consume the *whole* elapsed window, not just the paid cap, so repeated sweeps
    cannot collect an unlimited backlog. Development uses this same settlement
    before changing GDP and therefore cannot bypass the catch-up limit.
    """
    anchor = at(colony_state.get("income_at"))
    if anchor is None:
        colony_state["income_at"] = current.isoformat()
        return (0, True)
    elapsed_hours = max(0, int((current - anchor).total_seconds() // 3600))
    if not elapsed_hours:
        return (0, False)
    numerator = max(0, colony_daily_income(colony_state)) * min(168, elapsed_hours)
    numerator += max(0, int(colony_state.get("income_remainder", 0)))
    payout, remainder = divmod(numerator, 24)
    user["donuts"] = int(user.get("donuts", 0)) + payout
    colony_state["income_at"] = (anchor + dt.timedelta(hours=elapsed_hours)).isoformat()
    colony_state["income_remainder"] = remainder
    return (payout, True)


def exploration_craft(user: Dict[str, Any]) -> Optional[str]:
    """One shared voyage: a starter cannot teleport an existing ISD or its cargo."""
    state = normalize(user)
    from . import imperial_star_destroyer as isd

    ship = isd.normalize(user)
    if state["refit_until"] or state["load_until"]:
        return None
    if ship.get("operational"):
        return (
            "isd"
            if state["mode"] == "expedition"
            and (not ship.get("damaged"))
            and (not ship.get("repairing_until"))
            else None
        )
    from . import space_fleet as fleet

    hull = fleet.ship(user, "cutlass")
    if (
        fleet.deployed(user, "cutlass")
        and (not any((hull.get(k) for k in ("damaged", "repair_until", "mission", "payload_until"))))
        and (not ship.get("assembling_until"))
        and (not any(state["cargo"].values()))
    ):
        return "cutlass"
    return None


def settle_materials(site: Dict[str, Any], target: Body, current: dt.datetime) -> tuple[int, bool]:
    """Restart-safe local production. Never pays donuts or changes manual mining CD."""
    if not site.get("mine"):
        return (0, False)
    disabled = at(site.get("mine_disabled_until"))
    if disabled and disabled > current:
        return (0, False)
    anchor = at(site.get("material_at"))
    if anchor is None:
        site["material_at"] = current.isoformat()
        return (0, True)
    cycles = max(0, int((current - anchor).total_seconds() // (6 * 3600)))
    if not cycles:
        return (0, False)
    multiplier = 125 if site.get("specialisation") == "industrial" else 100
    numerator = (4 + target.score) * multiplier * min(MINING_CATCHUP_CYCLES, cycles)
    numerator += int(site.get("material_remainder", 0))
    count, site["material_remainder"] = divmod(numerator, 100)
    materials = site.setdefault("materials", {})
    materials[target.resource] = int(materials.get(target.resource, 0)) + count
    site["material_at"] = (anchor + dt.timedelta(hours=6 * cycles)).isoformat()
    return (count, True)


def mission_kind(target: Body) -> str:
    if target.kind in {"asteroid", "dwarf"}:
        return "Geological sampling"
    if target.key in GIANT_PLANETS:
        return "Orbital atmospheric research"
    if "ice" in target.resource or target.resource == "hydrocarbons":
        return "Subsurface survey"
    return "Surface expedition"


def start_expedition(user: Dict[str, Any], target: Body, current: dt.datetime, token: str) -> Dict[str, Any]:
    state = normalize(user)
    if state["expedition"] or state["travel_until"] or (not exploration_craft(user)):
        raise ValueError(
            "Finish your voyage, refit or current expedition first; a ready explorer is required."
        )
    if target.key in {"earth", "sun"} or state["location"] != target.key:
        raise ValueError("Travel to a surveyed planet, moon or asteroid first.")
    site = state["colonies"].get(target.key) or {}
    lab_disabled = at(site.get("lab_disabled_until"))
    from .activity_contracts import rare_bonus

    rare_chance = (
        10
        + (10 if site.get("lab") and (not (lab_disabled and lab_disabled > current)) else 0)
        + (10 if site.get("specialisation") == "research" else 0)
        + rare_bonus(user)
    )
    roll = random.Random(token).randint(1, 100)
    result = "rare" if roll <= rare_chance else "inconclusive" if roll > 90 else "standard"
    record = {
        "id": token,
        "body": target.key,
        "name": target.name,
        "kind": mission_kind(target),
        "resource": target.resource,
        "result": result,
        "materials": 1 if result == "inconclusive" else 2 + target.score // 3,
        "ready_at": (current + dt.timedelta(hours=EXPEDITION_HOURS)).isoformat(),
    }
    state["expedition"] = record
    return record


def settle_expedition(user: Dict[str, Any], doc: Dict[str, Any], current: dt.datetime) -> bool:
    from . import deathstar

    state = normalize(user)
    record = state.get("expedition")
    if not isinstance(record, dict) or not at(record.get("ready_at")) or at(record["ready_at"]) > current:
        return False
    state["expedition"] = None
    if deathstar.destroyed(doc, record["body"]):
        return True
    target = body(doc, record["body"])
    if not target:
        return True
    store = state["field_materials"]
    site = state["colonies"].get(target.key)
    if site and site.get("base"):
        store = site.setdefault("materials", {})
    store[target.resource] = int(store.get(target.resource, 0)) + int(record["materials"])
    entry = state["journal"].setdefault(target.key, {"name": target.name, "missions": 0, "discoveries": []})
    entry["missions"] += 1
    labels = {
        "Geological sampling": "Mineral specimen",
        "Orbital atmospheric research": "Atmospheric sample",
        "Subsurface survey": "Cryogenic sample",
        "Surface expedition": "Geological specimen",
    }
    label = ("Unusual " if record["result"] == "rare" else "") + labels[record["kind"]]
    if label not in entry["discoveries"]:
        entry["discoveries"].append(label)
    entry["last_result"] = record["result"]
    entry["last_completed_at"] = record["ready_at"]
    award_exploration_milestones(user, record["ready_at"])
    return True


def award_exploration_milestones(user: Dict[str, Any], completed_at: str) -> None:
    """Share the existing one-time journal rewards across all survey craft."""
    from . import badges

    state = normalize(user)
    milestones = state["milestones"]
    for threshold, reward in ((3, "pathfinder"), (10, "explorer21"), (25, "solarsurveyor")):
        if len(state["journal"]) >= threshold and threshold not in milestones:
            milestones.append(threshold)
            if threshold == 10:
                user.setdefault("plushies", {})[reward] = 1
            else:
                user.setdefault("titles", {})[reward] = completed_at
    if len(state["journal"]) >= 3:
        badges.grant(user, "space_pathfinder")


def material_total(colony_state: Dict[str, Any]) -> int:
    return sum((max(0, int(value)) for value in colony_state.get("materials", {}).values()))


def consume_materials(colony_state: Dict[str, Any], amount: int) -> bool:
    """Consume a deterministic mix of locally deposited space resources."""
    if amount < 0 or material_total(colony_state) < amount:
        return False
    remaining = amount
    for key in sorted(colony_state.get("materials", {})):
        available = max(0, int(colony_state["materials"][key]))
        used = min(remaining, available)
        colony_state["materials"][key] = available - used
        remaining -= used
        if not remaining:
            break
    return True
