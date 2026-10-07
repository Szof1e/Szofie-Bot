"""Small persistent activity boards. No Discord, account overrides or new currency."""

import datetime as dt
import random
import secrets

UTC = dt.timezone.utc
Q = 10**15
PROJECTS = {
    "biosensor": (
        "Biosensor calibration (+5pp civilian rare findings)",
        {"voidjelly": 10, "abyssalserpent": 3, "ghostleviathan": 1},
    ),
    "habitat": (
        "Habitat research (specimens → local materials)",
        {"voidjelly": 20, "abyssalserpent": 5, "ghostleviathan": 3},
    ),
}
ESOTERIC = ("mnemosynelantern", "eclipseglassfish", "ouroboroseel", "palecrownoarfish", "drownedoracleray")


def now():
    return dt.datetime.now(UTC)


def at(value):
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def root(user):
    value = user.get("activity_contracts")
    if not isinstance(value, dict):
        value = user["activity_contracts"] = {}
    for key in ("fish", "space", "research"):
        if not isinstance(value.get(key), dict):
            value[key] = {}
    return value


def research(user):
    return root(user).setdefault("research", {})


def rare_bonus(user):
    return 5 if research(user).get("biosensor") else 0


def fish_board(user, current=None):
    current = current or now()
    state = root(user).setdefault("fish", {})
    day = current.date().isoformat()
    if state.get("day") != day:
        rng = random.Random(secrets.token_hex(8))
        surface = rng.choice(
            ({"sardine": 6, "shrimp": 4}, {"perch": 5, "clam": 3}, {"crab": 2, "octopus": 2})
        )
        abyss = bool(user.get("rods", {}).get("abyssal") or user.get("rods", {}).get("mythicrod"))
        second = (
            rng.choice(({"voidjelly": 5, "abyssalserpent": 2}, {"voidjelly": 4, "ghostleviathan": 1}))
            if abyss
            else {"puffer": 2, "crab": 2}
        )
        state.clear()
        state.update(
            day=day,
            id=secrets.token_hex(8),
            offers=[
                {
                    "name": "Surface delivery",
                    "fish": surface,
                    "reward": rng.choice((50, 100, 150)) * 10**6,
                    "paid": False,
                },
                {
                    "name": "Abyss research delivery" if abyss else "Coastal delivery",
                    "fish": second,
                    "reward": rng.choice((500, 750, 1000)) * 10**6 if abyss else 150 * 10**6,
                    "paid": False,
                },
            ],
        )
    return state


def consume_fish(user, requirements):
    bucket = user.setdefault("fish", {})
    if any((int(bucket.get(key, 0)) < amount for key, amount in requirements.items())):
        raise ValueError("Your bucket does not contain all required specimens. Nothing was spent.")
    for key, amount in requirements.items():
        bucket[key] -= amount
        if bucket[key] == 0:
            bucket.pop(key)
            user.get("fish_meta", {}).pop(key, None)


def deliver_fish(user, board_id, index, current=None):
    board = fish_board(user, current)
    if board["id"] != board_id or not 0 <= index < len(board["offers"]):
        raise ValueError("This board has refreshed. Open your contracts again.")
    offer = board["offers"][index]
    if offer["paid"]:
        raise ValueError("That delivery has already been paid.")
    consume_fish(user, offer["fish"])
    offer["paid"] = True
    user["donuts"] = int(user.get("donuts", 0)) + offer["reward"]
    return offer["reward"]


def donate(user, project):
    state = research(user)
    if project in PROJECTS:
        if state.get(project):
            raise ValueError("This research project is already complete.")
        consume_fish(user, PROJECTS[project][1])
        state[project] = True
        return PROJECTS[project][0]
    if project.startswith("archive:") and project[8:] in ESOTERIC:
        key = project[8:]
        archive = state.setdefault("archive", [])
        if key in archive:
            raise ValueError("This specimen is already archived.")
        consume_fish(user, {key: 1})
        archive.append(key)
        return "Esoteric archive entry recorded permanently: " + key
    raise ValueError("Unknown research project.")


def habitat(user, doc, current=None):
    from . import space, deathstar

    current = current or now()
    state = research(user)
    if not state.get("habitat"):
        raise ValueError("Complete Habitat research first.")
    location = space.normalize(user)["location"]
    site = space.normalize(user)["colonies"].get(location, {})
    body = space.body(doc, location)
    if not body or not site.get("base") or deathstar.destroyed(doc, location):
        raise ValueError("Visit your own intact completed colony base first.")
    day = current.date().isoformat()
    count = int(state.get("culture_count", 0)) if state.get("culture_day") == day else 0
    if count >= 3:
        raise ValueError("Habitat culture can be produced three times per UTC day.")
    consume_fish(user, {"voidjelly": 5, "abyssalserpent": 2})
    state.update(culture_day=day, culture_count=count + 1)
    store = site.setdefault("materials", {})
    store[body.resource] = int(store.get(body.resource, 0)) + 2
    return "Produced 2 " + body.resource + " at your local colony."


def space_board(user, doc, current=None):
    from . import space, space_fleet as fleet, deathstar, coalitions

    current = current or now()
    state = root(user).setdefault("space", {})
    active = state.get("active")
    if (
        active
        and (not at(active.get("expires_at")) or at(active["expires_at"]) <= current)
        and (not active.get("completed"))
    ):
        state["active"] = None
    day = current.date().isoformat()
    if state.get("day") != day or not state.get("offers"):
        offers = []
        surveyed = space.normalize(user)["surveys"]
        bodies = [
            b
            for key in surveyed
            for b in [space.body(doc, key)]
            if b and key not in {"earth", "sun"} and (not deathstar.destroyed(doc, key))
        ]
        ships = [k for k in fleet.CRAFT if fleet.owned(user, k)]
        rng = random.Random(secrets.token_hex(8))
        for b in rng.sample(bodies, min(len(bodies), 20)):
            if any((k in ships for k in ("cutlass", "carrack"))) and (
                not any((o["kind"] == "survey" for o in offers))
            ):
                offers.append(
                    dict(kind="survey", body=b.key, reward=3 * Q, materials=2, name="Civilian survey")
                )
            if (
                b.kind == "asteroid"
                and "prospector" in ships
                and (not any((o["kind"] == "mine" for o in offers)))
            ):
                offers.append(
                    dict(kind="mine", body=b.key, reward=5 * Q, materials=3, name="Asteroid mining")
                )
            if "vulture" in ships and (not any((o["kind"] == "salvage" for o in offers))):
                offers.append(
                    dict(kind="salvage", body=b.key, reward=4 * Q, materials=2, name="Debris recovery")
                )
        sites = [
            k
            for k, v in space.normalize(user)["colonies"].items()
            if v.get("base") and (not deathstar.destroyed(doc, k))
        ]
        uid = next((int(key) for key, value in doc.get("users", {}).items() if value is user), None)
        allies = [
            value
            for key, value in doc.get("users", {}).items()
            if value is user or (uid is not None and coalitions.are_allied(doc, uid, int(key)))
        ]
        destinations = [
            k
            for u in allies
            for k, v in space.normalize(u)["colonies"].items()
            if v.get("base") and k in surveyed and (not deathstar.destroyed(doc, k))
        ]
        if "transport" in ships and any(
            (
                space.material_total(space.normalize(user)["colonies"][k]) >= 10
                and any((dest != k for dest in destinations))
                for k in sites
            )
        ):
            dest = next(
                (
                    dest
                    for dest in destinations
                    if any(
                        (
                            k != dest and space.material_total(space.normalize(user)["colonies"][k]) >= 10
                            for k in sites
                        )
                    )
                )
            )
            offers.insert(
                1,
                dict(
                    kind="supply", body=dest, reward=6 * Q, materials=3, name="Material delivery", amount=10
                ),
            )
        state.update(day=day, id=secrets.token_hex(8), offers=offers[:3], paid=[])
    return state


def accept_space(user, doc, board_id, index, current=None):
    from . import space, space_fleet as fleet, deathstar

    current = current or now()
    board = space_board(user, doc, current)
    active = board.get("active")
    if active and (not active.get("completed")):
        raise ValueError("Finish or let your current 48-hour contract expire first.")
    if board["id"] != board_id or index in board["paid"] or (not 0 <= index < len(board["offers"])):
        raise ValueError("This offer expired or was already accepted. Open a fresh board.")
    offer = dict(board["offers"][index])
    craft = {
        "survey": ("cutlass", "carrack"),
        "supply": ("transport",),
        "mine": ("prospector",),
        "salvage": ("vulture",),
    }[offer["kind"]]
    if (
        not any((fleet.owned(user, key) for key in craft))
        or offer["body"] not in space.normalize(user)["surveys"]
        or deathstar.destroyed(doc, offer["body"])
    ):
        raise ValueError("This destination or required craft is no longer available. Nothing was accepted.")
    offer.update(
        id=secrets.token_hex(8),
        accepted_at=current.isoformat(),
        expires_at=(current + dt.timedelta(hours=48)).isoformat(),
        completed=False,
    )
    board["paid"].append(index)
    board["active"] = offer
    return offer


def complete_space(user, record, resolved_at):
    """Consume one successful new mission credit. Reward is committed once with settlement."""
    from . import space

    board = root(user).setdefault("space", {})
    active = board.get("active")
    started = at(record.get("started_at"))
    if (
        not active
        or active.get("completed")
        or (not started)
        or (not at(active.get("accepted_at")))
        or (not at(active.get("expires_at")))
        or (started < at(active["accepted_at"]))
        or (resolved_at > at(active["expires_at"]))
        or (record.get("kind") != active["kind"])
        or (record.get("body") != active["body"])
        or (
            active["kind"] == "supply"
            and sum(record.get("materials", {}).values()) < active.get("amount", 10)
        )
    ):
        return 0
    active.update(completed=True, mission_id=record["id"], completed_at=resolved_at.isoformat())
    user["donuts"] = int(user.get("donuts", 0)) + active["reward"]
    store = space.normalize(user)["field_materials"]
    site = space.normalize(user)["colonies"].get(active["body"], {})
    if site.get("base"):
        store = site.setdefault("materials", {})
    resource = next(iter(record.get("materials", {})), "metals")
    store[resource] = int(store.get(resource, 0)) + active["materials"]
    board["earned"] = int(board.get("earned", 0)) + active["reward"]
    return active["reward"]
