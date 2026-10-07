"""Persistent helpers for the temporary coalition system.

Coalitions live at the economy document root, so guilds which share an economy
also share diplomacy.  The helpers intentionally know nothing about Discord;
both the command cog and hostile economy commands can use the same rules.
"""

from __future__ import annotations
import datetime as dt
from typing import Any, Dict, List, Optional, Tuple


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_time(value: Any) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def state(doc: Dict[str, Any]) -> Dict[str, Any]:
    root = doc.setdefault("coalitions", {})
    if not isinstance(root, dict):
        root = doc["coalitions"] = {}
    if not isinstance(root.get("groups"), dict):
        root["groups"] = {}
    if not isinstance(root.get("invites"), dict):
        root["invites"] = {}
    if not isinstance(root.get("cooldowns"), dict):
        root["cooldowns"] = {}
    try:
        root["next_id"] = max(1, int(root.get("next_id", 1)))
    except (TypeError, ValueError):
        root["next_id"] = 1
    return root


def cleanup(doc: Dict[str, Any], now: Optional[dt.datetime] = None) -> bool:
    """Remove expired coalitions/invites/cooldowns. Returns whether data changed."""
    now = now or utcnow()
    root = state(doc)
    changed = remember_membership(doc, now)
    groups = root["groups"]
    for cid, group in list(groups.items()):
        if not isinstance(group, dict):
            groups.pop(cid, None)
            changed = True
            continue
        members = group.get("members")
        if not isinstance(members, list) or not members:
            groups.pop(cid, None)
            changed = True
            continue
        expires = parse_time(group.get("expires_at"))
        if expires is None or expires <= now:
            groups.pop(cid, None)
            changed = True
    invites = root["invites"]
    for uid, pending in list(invites.items()):
        if not isinstance(pending, list):
            invites.pop(uid, None)
            changed = True
            continue
        live = [
            invite
            for invite in pending
            if isinstance(invite, dict)
            and str(invite.get("coalition_id")) in groups
            and ((parse_time(invite.get("expires_at")) or now) > now)
        ]
        if len(live) != len(pending):
            changed = True
        if live:
            invites[uid] = live
        else:
            invites.pop(uid, None)
    cooldowns = root["cooldowns"]
    for uid, expiry in list(cooldowns.items()):
        parsed = parse_time(expiry)
        if parsed is None or parsed <= now:
            cooldowns.pop(uid, None)
            changed = True
    return changed


def remember_membership(doc: Dict[str, Any], now: Optional[dt.datetime] = None) -> bool:
    """Keep treaty intervals so delayed missions use diplomacy at arrival time.

    Current commands continue using live groups. History never grants present-day
    access or revives expired coalitions; it is only for historical settlement.
    """
    now = now or utcnow()
    root = state(doc)
    history = root.setdefault("alliance_intervals", [])
    changed = False
    if not isinstance(history, list):
        history = root["alliance_intervals"] = []
        changed = True
    active = {
        str(row["id"]): row
        for row in history
        if isinstance(row, dict) and row.get("end") is None and ("id" in row)
    }
    latest = {str(row["id"]): row for row in history if isinstance(row, dict) and "id" in row}
    for cid in set(active) | set(root["groups"]):
        group = root["groups"].get(cid)
        try:
            members = (
                sorted({int(uid) for uid in group.get("members", [])}) if isinstance(group, dict) else []
            )
        except (TypeError, ValueError):
            members = []
        expiry = parse_time(group.get("expires_at")) if isinstance(group, dict) else None
        old = active.get(cid)
        prior = latest.get(cid)
        if (
            old is None
            and prior
            and (prior.get("members") == members)
            and (parse_time(prior.get("expires_at")) == expiry)
            and expiry
            and (expiry <= now)
        ):
            continue
        if old and (
            old.get("members") != members
            or parse_time(old.get("expires_at")) != expiry
            or (not expiry)
            or (expiry <= now)
        ):
            old_expiry = parse_time(old.get("expires_at")) or now
            old["end"] = min(now, old_expiry).isoformat()
            changed = True
        elif old:
            continue
        if members and expiry and (expiry > now or prior is None):
            start = (
                parse_time(group.get("created_at")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)
                if prior is None
                else now
            )
            history.append(
                {
                    "id": str(cid),
                    "members": members,
                    "start": start.isoformat(),
                    "end": expiry.isoformat() if expiry <= now else None,
                    "expires_at": expiry.isoformat(),
                }
            )
            changed = True
    closed = [row for row in history if isinstance(row, dict) and row.get("end") is not None]
    if len(closed) > 2000:
        removed = {id(row) for row in closed[:-2000]}
        history[:] = [row for row in history if id(row) not in removed]
        changed = True
    return changed


def are_allied_at(doc: Dict[str, Any], first_id: int, second_id: int, instant: dt.datetime) -> bool:
    """Historical pact membership; never restore expired live groups."""
    if int(first_id) == int(second_id):
        return False
    remember_membership(doc)
    for row in state(doc).get("alliance_intervals", []):
        if not isinstance(row, dict):
            continue
        start = parse_time(row.get("start"))
        end = parse_time(row.get("end")) or parse_time(row.get("expires_at"))
        if start and end and (start <= instant < end):
            members = row.get("members", [])
            if int(first_id) in members and int(second_id) in members:
                return True
    return False


def coalition_for(doc: Dict[str, Any], user_id: int) -> Optional[Tuple[str, Dict[str, Any]]]:
    cleanup(doc)
    uid = int(user_id)
    for cid, group in state(doc)["groups"].items():
        try:
            members = [int(member) for member in group.get("members", [])]
        except (TypeError, ValueError):
            continue
        if uid in members:
            return (str(cid), group)
    return None


def are_allied(doc: Dict[str, Any], first_id: int, second_id: int) -> bool:
    if int(first_id) == int(second_id):
        return False
    found = coalition_for(doc, first_id)
    if found is None:
        return False
    return int(second_id) in {int(member) for member in found[1].get("members", [])}


def cooldown_until(doc: Dict[str, Any], user_id: int) -> Optional[dt.datetime]:
    cleanup(doc)
    return parse_time(state(doc)["cooldowns"].get(str(int(user_id))))


def set_cooldown(doc: Dict[str, Any], user_id: int, hours: float) -> dt.datetime:
    expiry = utcnow() + dt.timedelta(hours=max(0.0, float(hours)))
    state(doc)["cooldowns"][str(int(user_id))] = expiry.isoformat()
    return expiry


def append_log(group: Dict[str, Any], actor_id: int, action: str, detail: str) -> None:
    entries = group.setdefault("log", [])
    if not isinstance(entries, list):
        entries = group["log"] = []
    entries.append(
        {
            "at": utcnow().isoformat(),
            "actor": int(actor_id),
            "action": str(action),
            "detail": str(detail)[:500],
        }
    )
    del entries[:-50]


def pending_invites(doc: Dict[str, Any], user_id: int) -> List[Dict[str, Any]]:
    cleanup(doc)
    pending = state(doc)["invites"].get(str(int(user_id)), [])
    return list(pending) if isinstance(pending, list) else []
