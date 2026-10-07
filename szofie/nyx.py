"""NYX orbital counter-intelligence: persistent real-time deadlines and masking."""

from __future__ import annotations
import datetime as dt
from typing import Any, Dict, Optional
from . import thor, titles

NAME = "NYX Orbital Counter-Intelligence Satellite"
BUILD_COST = 15000000000000000
LAUNCH_COST = 1500000000000000
BUILD_HOURS = 12
LAUNCH_MINUTES = 30
COOLDOWN_HOURS = 10
ACTIVE_HOURS = 3
CATEGORIES = ("donuts", "vehicles", "isd")
CENSORED = "NYX Ghost Protocol — intelligence censored. Holdings, projects, construction and deadlines are classified."
ART = {
    key: "nyx-" + key + ".png" for key in ("fabrication", "ready", "launch", "orbit", "active", "intercepted")
}


def default_state() -> Dict[str, Any]:
    return {
        "status": "none",
        "ready_at": None,
        "launch": None,
        "active_until": None,
        "last_activation_at": None,
    }


def normalize(user: Dict[str, Any]) -> Dict[str, Any]:
    state = user.get("nyx")
    if not isinstance(state, dict):
        state = user["nyx"] = default_state()
    for key, value in default_state().items():
        state.setdefault(key, value)
    if state["status"] not in {"none", "fabricating", "ready", "launching", "orbit"}:
        state.update(default_state())
    return state


def settle(user: Dict[str, Any], current: Optional[dt.datetime] = None) -> bool:
    current = current or thor.utcnow()
    state = normalize(user)
    due = thor.parse_time(state["ready_at"])
    if state["status"] == "fabricating" and due and (due <= current):
        state.update(status="ready", ready_at=None)
        return True
    return False


def active(user: Dict[str, Any], current: Optional[dt.datetime] = None) -> bool:
    state = user.get("nyx") or {}
    due = thor.parse_time(state.get("active_until"))
    current = current or thor.utcnow()
    started = thor.parse_time(state.get("last_activation_at"))
    return state.get("status") == "orbit" and bool(
        due and due > current and (not started or started <= current)
    )


def hidden(user, requester_id, subject_id, current=None):
    return int(requester_id) != int(subject_id) and active(user, current)


def leaderboard_entry(user, current=None):
    """Omit active NYX entirely; restore live wallet+bank at exact expiry.

    Legacy snapshots are ignored. This is read-only and never changes balances,
    season eligibility, cooldowns or the field's original expiration time.
    """
    if active(user, current):
        return None
    return {
        "wealth": int(user.get("donuts", 0)) + int(user.get("bank", 0)),
        "title": titles.equipped_label(user),
        "crown": bool(user.get("crown")),
    }


def blocks_snapshot(user, captured_at, current=None):
    """Reject fresh scans during a field and pre-field snapshots after expiry."""
    current = current or thor.utcnow()
    started = thor.parse_time((user.get("nyx") or {}).get("last_activation_at"))
    return active(user, current) or bool(captured_at and started and (captured_at <= started <= current))


def censored_embed():
    from . import ui

    return ui.base_embed(title="INTELLIGENCE CENSORED", description=CENSORED)


def protect_report(interaction, economy, *subject_ids):
    """Bind a status/build response to live subjects, not an early snapshot.

    Call before any await that precedes a player-specific report. Required
    launch/attack warnings must not opt in: their public timing is intentional.
    """
    if interaction.guild_id is None:
        return
    ids = tuple(dict.fromkeys((int(uid) for uid in subject_ids or (interaction.user.id,))))

    def subjects():
        users = economy.store.load(interaction.guild_id).get("users", {})
        return {uid: users.get(str(uid), {}) for uid in ids}

    interaction.extras["szofie_nyx_report"] = subjects


def report_policy(interaction, private=False):
    """Recheck censorship/visibility immediately before Discord delivery."""
    resolver = interaction.extras.get("szofie_nyx_report")
    subjects = resolver() if resolver else {}
    blocked = any((hidden(user, interaction.user.id, uid) for uid, user in subjects.items()))
    return (blocked, private or any((active(user) for user in subjects.values())))


def guard_report_pager(interaction, view, private):
    """Recheck cached pages; a formerly public own report must not reveal a cloak."""
    if view is None or not hasattr(view, "page_guard"):
        return
    view._nyx_report_private = private
    if getattr(view, "_nyx_report_bound", False):
        return
    previous = view.page_guard

    def page_guard(index):
        blocked, needs_private = report_policy(interaction)
        if blocked or (needs_private and (not view._nyx_report_private)):
            return censored_embed()
        return previous(index) if previous else None

    view.page_guard = page_guard
    view._nyx_report_bound = True


def finish_launch(user: Dict[str, Any], current: Optional[dt.datetime] = None) -> Optional[bool]:
    """Return intercepted/orbit, or None when absent/not due. Resolve exactly once."""
    state = normalize(user)
    record = state.get("launch")
    if state["status"] != "launching" or not isinstance(record, dict) or (not record.get("announced")):
        return None
    due = thor.parse_time(record.get("resolves_at"))
    if not due or due > (current or thor.utcnow()):
        return None
    intercepted = any((row.get("success") for row in record.get("interceptors", [])))
    state.update(status="none" if intercepted else "orbit", launch=None, ready_at=None)
    return bool(intercepted)


def activate(
    doc: Dict[str, Any], user_id: int, cfg: Any, current: Optional[dt.datetime] = None
) -> dt.datetime:
    current = current or thor.utcnow()
    user = doc["users"][str(user_id)]
    state = normalize(user)
    if state["status"] != "orbit":
        raise ValueError("Build and successfully launch NYX before activating Ghost Protocol.")
    cooldown = max(1.0, float(cfg.get("economy.nyx_cooldown_hours", COOLDOWN_HOURS)))
    last = thor.parse_time(state["last_activation_at"])
    if last and last + dt.timedelta(hours=cooldown) > current:
        due = last + dt.timedelta(hours=cooldown)
        raise ValueError(f"Ghost Protocol is cooling down until <t:{int(due.timestamp())}:R>.")
    until = current + dt.timedelta(hours=max(0.01, float(cfg.get("economy.nyx_active_hours", ACTIVE_HOURS))))
    state.update(active_until=until.isoformat(), last_activation_at=current.isoformat())
    key = str(user_id)
    for observer in doc.get("users", {}).values():
        if isinstance(observer, dict):
            for field in ("recon_targets", "sr71_construction_intel", "fusion_links"):
                records = observer.get(field)
                if isinstance(records, dict):
                    records.pop(key, None)
            local_tracks = ((observer.get("space") or {}).get("fleet") or {}).get("recon")
            if isinstance(local_tracks, dict):
                local_tracks.pop(key, None)
    return until


def ground_impact(user: Dict[str, Any]) -> int:
    """A ground satellite is vulnerable; committed ascent/orbit is not terrestrial."""
    state = user.get("nyx") or {}
    if state.get("status") not in {"fabricating", "ready"}:
        return 0
    state.update(status="none", ready_at=None, launch=None, active_until=None)
    return 1


def summary(user: Dict[str, Any], cfg: Any, current: Optional[dt.datetime] = None) -> str:
    from . import status_ui

    current = current or thor.utcnow()
    settle(user, current)
    state = normalize(user)
    lines = ["**Status:** " + status_ui.state(state["status"])]
    due = thor.parse_time(state.get("ready_at"))
    if state["status"] == "launching":
        due = thor.parse_time((state.get("launch") or {}).get("resolves_at"))
    if due:
        lines.append(f"**Completes:** {status_ui.deadline(due)}")
    if active(user, current):
        due = thor.parse_time(state["active_until"])
        lines.append(f"**Ghost Protocol:** 🟢 Active\n**Camouflage expires:** {status_ui.deadline(due)}")
        lines.append("**Leaderboard visibility:** Hidden entirely until Ghost Protocol expires")
    else:
        lines.append("**Ghost Protocol:** ⚪ Inactive")
    last = thor.parse_time(state.get("last_activation_at"))
    if last:
        next_use = last + dt.timedelta(hours=float(cfg.get("economy.nyx_cooldown_hours", COOLDOWN_HOURS)))
        if next_use > current:
            lines.append(f"**Next activation:** {status_ui.deadline(next_use)}")
    return "\n".join(lines)


def guide_pages(cfg: Any):
    from . import ui

    price = lambda key, fallback: ui.format_donuts(int(cfg.get("economy." + key, fallback)))
    build = float(cfg.get("economy.nyx_build_hours", BUILD_HOURS))
    window = float(cfg.get("economy.nyx_launch_minutes", LAUNCH_MINUTES))
    duration = float(cfg.get("economy.nyx_active_hours", ACTIVE_HOURS))
    cooldown = float(cfg.get("economy.nyx_cooldown_hours", COOLDOWN_HOURS))
    return [
        ui.base_embed(
            title="NYX 1/3 — Build and launch",
            description=f"`/space build craft:NYX` costs {price('nyx_build_cost', BUILD_COST)} donuts and takes {build:g} hours. `/space fleet launch craft:NYX` costs {price('nyx_launch_cost', LAUNCH_COST)} more and opens a public {window:g}-minute window. Ground construction/ready hardware can be destroyed by THOR. One satellite per player; no transfers or coalition protection broadcasting.\n\nDefenders build ready GBI/EKVs with `/thor gbi-build`, then `/space fleet intercept target:<builder> craft:NYX counter:GBI/EKV` during ascent. One attempt per defender, up to three defenders; ordinary GBI tracking applies (Block II upgrades apply). Miss or hit consumes one ready missile. Allies cannot intercept. A hit destroys the launch and satellite; rebuild both. After reaching orbit, ordinary targeted attacks cannot destroy NYX.",
        ),
        ui.base_embed(
            title="NYX 2/3 — Ghost Protocol",
            description=f"`/space fleet mission craft:NYX mission:Jam` hides your intelligence for {duration:g} hours, once every {cooldown:g} hours measured from activation. Leave body, target and objectives blank. No ammo or recurring activation fee. `/space fleet status craft:NYX` and `/arsenal` show readiness. Deadlines use real elapsed time, including downtime.\n\nU-2, Deimos and SR-71 reveal no new balances, defences, vehicles or construction while jammed. Stored recon locks and construction snapshots are invalidated when you activate; scan again after expiry. If either Deimos participant is cloaked, neither side is revealed. Jamming spends the mission cooldown, but does not itself destroy the scout. Normal interception still applies.\n\nRecon menus revert to ordinary blind objectives. Previously posted public bot status/asset reports are permanently censored, including their attachments and page buttons. Fresh reports are required after expiry. Screenshots, copied text and messages outside the bot's control cannot be erased; other-player status/asset reports, coalition intel and Carrack scans are also censored. Your own status, construction, loading and repair reports remain readable privately, including their completion times. Cloaked targets' reports show no build stages or deadlines; old pagers recheck the field before revealing another page. You are omitted entirely from `/leaderboard` and public `/season` rankings while Ghost Protocol is active: no name, amount, rank or placeholder row. At expiry, your live entry returns automatically. Gains, spending and losses still change your real funds; Deep Vault/Raven Rock are never included in wealth rankings. Season rewards still use real wealth. Other aggregate lists omit cloaked holdings. Required launch/attack warnings remain; command-based window inspections show only opaque interception IDs and counter instructions, not the cloaked builder or component.",
        ),
        ui.base_embed(
            title="NYX 3/3 — THOR degraded targeting",
            description="Aegis intercepts the rod normally first. If THOR lands while Ghost Protocol is active at impact, it randomly selects ONE category, each with a one-in-three chance:\n\n**Donuts:** exposed wallet, bank, deepvault and donut cargo are erased; Raven Rock survives.\n**Vehicles:** Earth vehicles/builds/repairs/upgrades and their loaded or loading ammo are destroyed; launched/off-world craft survive.\n**ISD part:** at most one fabricating/ready Earth component faces its ordinary survival roll, not guaranteed destruction.\n\nNo reroll if the selected category is empty. Other categories, fish, rods, plushies, loose items, spare ammo, THOR modules and Death Star sections survive the degraded strike. Conventional attacks are not blocked. ISD and Death Star civilisation wipes are unchanged and can erase NYX. No post-impact immunity.",
        ),
    ]
