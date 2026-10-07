"""Finite ordinary fleet screening, bound to one megaproject deployment window."""

import random
from . import coalitions, space, space_fleet as fleet

SCREENING = {"xwing": (15, 1), "awing": (25, 2), "hammerhead": (35, 3), "arquitens": (30, 2)}


def prepare(doc, builder, owner, key, current):
    if key not in SCREENING:
        raise ValueError("Choose an X-wing, A-wing, Hammerhead or Arquitens escort.")
    if owner != builder and (not coalitions.are_allied(doc, builder, owner)):
        raise ValueError("A launch escort must belong to you or a current coalition ally.")
    user = doc.get("users", {}).get(str(owner))
    if not user:
        raise ValueError("This escort owner has no fleet.")
    hull = fleet.ship(user, key)
    if (
        not fleet.owned(user, key)
        or not hull.get("deployed")
        or hull.get("location") != "earth"
        or hull.get("damaged")
        or hull.get("build_until")
        or hull.get("repair_until")
        or hull.get("launch")
        or hull.get("mission")
        or hull.get("payload_until")
        or (space.at(hull.get("combat_until")) and space.at(hull["combat_until"]) > current)
    ):
        raise ValueError("The escort must be launched, healthy, idle at Earth and out of combat turnaround.")
    count = min(SCREENING[key][1], fleet.payload(user, key))
    if count < 1:
        raise ValueError("Load at least one escort ammunition/service pack first.")
    return dict(
        owner=owner,
        craft=key,
        responses=count,
        screening=SCREENING[key][0],
        generation=str(user.get("cinder_wiped_at") or ""),
    )


def reserve(doc, record, window_id, prepared):
    if not prepared:
        return
    user = doc["users"][str(prepared["owner"])]
    hull = fleet.ship(user, prepared["craft"])
    fleet.set_payload(user, prepared["craft"], fleet.payload(user, prepared["craft"]) - prepared["responses"])
    hull["mission"] = dict(
        kind="launch-escort", window_id=window_id, body="earth", responses_remaining=prepared["responses"]
    )
    record["escort"] = dict(prepared, window_id=window_id)


def screen(doc, record, current, rng=random):
    value = record.get("escort")
    if not value or value["responses"] <= 0:
        return False
    owner = doc.get("users", {}).get(str(value["owner"]))
    if not owner or value["generation"] != str(owner.get("cinder_wiped_at") or ""):
        return False
    hull = fleet.ship(owner, value["craft"])
    if (
        not fleet.owned(owner, value["craft"])
        or hull.get("damaged")
        or (not hull.get("mission"))
        or (hull["mission"].get("kind") != "launch-escort")
        or (hull["mission"].get("window_id") != value["window_id"])
        or (
            value["owner"] != int(record["builder"])
            and (not coalitions.are_allied(doc, value["owner"], int(record["builder"])))
        )
    ):
        return False
    value["responses"] -= 1
    hull["mission"]["responses_remaining"] = value["responses"]
    return rng.randint(1, 100) <= value["screening"]


def release(doc, record):
    value = record.get("escort")
    if not value or value.get("released"):
        return
    value["released"] = True
    owner = doc.get("users", {}).get(str(value["owner"]))
    if not owner or value["generation"] != str(owner.get("cinder_wiped_at") or ""):
        return
    hull = fleet.ship(owner, value["craft"])
    if (
        not hull.get("mission")
        or hull["mission"].get("kind") != "launch-escort"
        or hull["mission"].get("window_id") != value["window_id"]
    ):
        return
    hull["mission"] = None
    if fleet.owned(owner, value["craft"]) and (not hull.get("damaged")):
        fleet.set_payload(
            owner,
            value["craft"],
            min(
                fleet.CRAFT[value["craft"]].capacity,
                fleet.payload(owner, value["craft"]) + value["responses"],
            ),
        )
