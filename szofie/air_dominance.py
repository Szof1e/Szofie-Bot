"""Ordinary fifth-generation recipes and read-only targeting rules.

No account-specific odds or presentation overrides belong in this module.
"""

import datetime as dt
from . import nyx
from .thor import parse_time, utcnow

JETS = {
    "f22": ("Lockheed Martin F-22A Raptor", "F-22A Raptor", 750000000000, 24, 25000000000, 240),
    "f35": ("Lockheed Martin F-35A Lightning II", "F-35A Lightning II", 550000000000, 18, 20000000000, 180),
    "j20": ("Chengdu J-20 Mighty Dragon", "J-20 Mighty Dragon", 650000000000, 20, 30000000000, 240),
    "su57": ("Sukhoi Su-57 Felon", "Su-57 Felon", 450000000000, 16, 15000000000, 180),
}
AIRCRAFT = frozenset(
    {"b2", "b52", "u2", "sr71", "apache", "mq9", "f15e", "a10", "su34", "c130j", "xb70", "p8", *JETS}
)


def recipe(model):
    label, short, cost, hours, package, cooldown = JETS[model]
    prefix = "vehicle_" + model
    return dict(
        label=label,
        short=short,
        emoji="✈️",
        role="offense",
        cost=prefix + "_cost",
        build=prefix + "_build_hours",
        fallback_cost=cost,
        fallback_build=hours,
        ammo=short + " mission package",
        ammo_cost=prefix + "_ammo_cost",
        load=prefix + "_load_minutes",
        cap=prefix + "_ammo_cap",
        cooldown=prefix + "_cooldown_minutes",
        fallback_cooldown=cooldown,
        fallback_ammo_cost=package,
        fallback_load=30,
        fallback_cap=4,
    )


def usable(state):
    return bool(
        state.get("owned")
        and (not state.get("building_until"))
        and (not state.get("jet_damaged"))
        and (not state.get("jet_repair_until"))
    )


def settle(state, current=None):
    current = current or utcnow()
    due = parse_time(state.get("jet_repair_until"))
    if due and due <= current:
        state.update(jet_damaged=False, jet_repair_until=None)


def patrol_chance(model):
    if model not in AIRCRAFT:
        return 0
    return 20 if model == "b2" else 35 if model in JETS else 65


def patrol(user, protected_id, current=None):
    state = (user.get("vehicles") or {}).get("f22", {})
    due = parse_time(state.get("patrol_until"))
    if (
        usable(state)
        and due
        and (due > (current or utcnow()))
        and (str(state.get("patrol_target")) == str(protected_id))
    ):
        return state
    return None


def link(user, target_id, target, current=None):
    record = (user.get("fusion_links") or {}).get(str(target_id))
    if not isinstance(record, dict):
        return None
    current = current or utcnow()
    expiry, created = (parse_time(record.get("until")), parse_time(record.get("created")))
    if (
        not expiry
        or expiry <= current
        or nyx.blocks_snapshot(target, created, current)
        or (record.get("generation", "") != str(user.get("cinder_wiped_at") or ""))
    ):
        user.setdefault("fusion_links", {}).pop(str(target_id), None)
        return None
    return record


def vehicle_chance(user, target_id, target, ordinary, *, consume=True):
    record = link(user, target_id, target)
    value = min(95, ordinary + 15) if record else ordinary
    if record and consume:
        user["fusion_links"].pop(str(target_id), None)
    return value


def damage(state):
    state.update(
        jet_damaged=True, jet_repair_until=None, patrol_until=None, patrol_target=None, second_pass=None
    )


def clear_operations(user):
    user.pop("fusion_links", None)
    for model in JETS:
        state = (user.get("vehicles") or {}).get(model)
        if isinstance(state, dict):
            state.update(
                jet_damaged=False,
                jet_repair_until=None,
                patrol_until=None,
                patrol_target=None,
                second_pass=None,
            )


def invalidate_source(observer, source):
    records = observer.get("sr71_construction_intel") or {}
    for key, record in list(records.items()):
        if not isinstance(record, dict):
            continue
        sources = record.get("sources")
        if isinstance(sources, dict):
            sources.pop(source, None)
            live = {
                key: value
                for key, value in sources.items()
                if parse_time(value.get("expires_at")) and parse_time(value["expires_at"]) > utcnow()
            }
            if live:
                best = max(live, key=lambda key: parse_time(live[key]["expires_at"]))
                record.update(live[best], source=best, sources=live)
                observer.setdefault("recon_targets", {})[key] = record["expires_at"]
                continue
        if record.get("source") == source or (isinstance(sources, dict) and (not sources)):
            records.pop(key, None)
            (observer.get("recon_targets") or {}).pop(key, None)


def deadline(hours):
    return (utcnow() + dt.timedelta(hours=hours)).isoformat()
