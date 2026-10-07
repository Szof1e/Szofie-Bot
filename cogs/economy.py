"""Donut economy — daily, slots, stealing, an anti-steal shop, perk plushies,
and a leaderboard.

Balances live in data/economy/ (see szofie/economy.py). Game maths that don't
touch Discord (the slot resolver, plushie perks) are module-level functions so
they're unit-testable off-network.
"""

from __future__ import annotations
import asyncio
import copy
import datetime as dt
import io
import json
import logging
import math
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import discord
from discord import app_commands
from discord.ext import commands, tasks
from szofie import (
    badges,
    checks,
    coalitions,
    continuity as continuity_state,
    countries as country_state,
    imperial_star_destroyer as isd_state,
    nyx,
    space as space_state,
    thor as thor_state,
    ui,
)
from szofie import events as gevents
from szofie import gamelocks
from szofie import hexes
from szofie.combat import reset_generation
from szofie.betting import (
    AmountParseError,
    AcknowledgedBetTransformer,
    BetTransformer,
    SignedAmountTransformer,
    SpendTransformer,
    parse_amount,
)
from szofie.economy import CASINO_FEATURED_GAMES, CASINO_GAMES, CASINO_TOUR_GAMES, record_casino_result
from cogs.fishing import ROD_BY_ID, RODS, ENCHANTS
from szofie.catalog import (
    ARMORED_MODELS,
    B52_REPAIR_COST_PCTS,
    B52_REPAIR_HOURS,
    COMANCHE_AMMO_TARGETS,
    CONSUMABLES,
    CONVENTIONAL_AMMO_TARGETS,
    DEPLOYABLE_VEHICLES,
    ITEM_HOLD_CAPS,
    LOADABLE_VEHICLES,
    PERMANENT_UTILITIES,
    VEHICLE_CATALOG,
)
from szofie import assets as asset_service
from szofie.currency import net_total, take_wallet_first, transfer_wallet
from szofie import guides
from szofie import status_ui
from szofie import progression, activity_ui
from szofie import air_dominance as air
from cogs import air_dominance as air_commands

log = logging.getLogger(__name__)
LEDGER_LABELS = {
    "daily": "daily claim",
    "work": "career shift",
    "career-licence": "advanced career licence",
    "helper-certification": "Android 21 career certification",
    "fishing-contract": "fishing contract delivery",
    "specimen-research": "specimen research / archive",
    "space-contract": "civilian space contract",
    "android21-helper-work": "Android 21 autonomous shift",
    "fish-sell": "fish sale",
    "slots": "slots",
    "roulette": "roulette",
    "dice": "dice",
    "blackjack": "blackjack",
    "wheel": "wheel",
    "trivia": "trivia",
    "casino-tour": "Casino Tour reward",
    "interest": "bank interest",
    "give-sent": "gave",
    "give-received": "received gift",
    "loan-sent": "lent",
    "loan-received": "borrowed",
    "repay-sent": "repaid",
    "repay-received": "loan repaid",
    "admin-adjust": "ADMIN ADJUST",
    "thor-recovery": "THOR recovery",
    "deep-vault-recovery": "Bedrock Vault recovery",
    "feature-retirement-refund": "retired-feature refund",
    "steal-success": "stole",
    "steal-caught": "steal fine",
    "steal-reversed": "steal reversed",
    "steal-blocked": "steal blocked",
    "steal-locked": "steal locked",
    "robbank-success": "robbed vault",
    "robbank-fail": "rob fine",
    "robbank-reversed": "heist reversed",
    "bail": "bail posted",
    "insurance": "insurance premium",
    "insurance-payout": "insurance payout",
    "heist-success": "heist take",
    "heist-fail": "heist fine",
    "safecrack-success": "safecracked vault",
    "safecrack-fail": "safecrack fine",
    "safecrack-reversed": "safecrack reversed",
    "vault-upgrade": "vault upgrade",
    "enchant": "rod enchant",
    "collect-received": "debt collected",
    "collect-paid": "debt seized",
    "garnish": "wages garnished",
    "event-drop": "donut drop",
    "abyss-dive": "abyss dive",
    "icbm-build": "ICBM commissioned",
    "icbm-launch": "ICBM launched",
    "icbm-hit": "ICBM strike",
    "aa-shield": "AA shield deployed",
    "aa-rocket-build": "AA rockets built",
    "vehicle-b2-build": "B-2 Spirit commissioned",
    "vehicle-b2-arm": "B61-12 payload armed",
    "vehicle-b2-launch": "B-2 strike launched",
    "vehicle-b2-lost": "B-2 Spirit shot down",
    "vehicle-b2-hit": "B-2 strategic strike",
    "vehicle-u2-build": "U-2S commissioned",
    "vehicle-b52-build": "B-52H Stratofortress commissioned",
    "vehicle-b52-load": "Mk 82 bomb sticks loaded",
    "vehicle-b52-launch": "B-52H runway strike launched",
    "vehicle-b52-hit": "B-52H strategic runway lockdown",
    "vehicle-b52-damaged": "B-52H recovered with battle damage",
    "vehicle-b52-repair": "B-52H depot repair started",
    "vehicle-b52-lost": "B-52H Stratofortress shot down",
    "vehicle-u2-recon": "U-2S reconnaissance",
    "vehicle-u2-lost": "U-2S shot down",
    "vehicle-deimos-build": "F3 DeathMARK Tracker commissioned",
    "vehicle-deimos-recon": "F3 DeathMARK reciprocal scan",
    "vehicle-deimos-lost": "F3 DeathMARK Tracker destroyed",
    "vehicle-sr71-build": "SR-71 Blackbird commissioned",
    "vehicle-sr71-recon": "SR-71 defensive reconnaissance",
    "vehicle-sr71-lost": "SR-71 Blackbird lost on reconnaissance",
    "vehicle-zumwalt-build": "DDG-1000 EMRG commissioned",
    "vehicle-zumwalt-load": "HVP rounds loaded",
    "vehicle-zumwalt-launch": "EMRG strike launched",
    "vehicle-zumwalt-hit": "EMRG kinetic strike",
    "vehicle-virginia-build": "Virginia SSN commissioned",
    "vehicle-virginia-load": "Mk 48 ADCAP loaded",
    "vehicle-virginia-launch": "submarine raid launched",
    "vehicle-virginia-hit": "Virginia fisheries raid",
    "vehicle-virginia-lost": "Virginia SSN destroyed",
    "vehicle-himars-build": "M142 HIMARS commissioned",
    "vehicle-himars-load": "M31A2 rockets loaded",
    "vehicle-himars-launch": "HIMARS strike launched",
    "vehicle-himars-hit": "HIMARS defense strike",
    "vehicle-apache-build": "AH-64E Apache Guardian commissioned",
    "vehicle-apache-load": "AGM-179A JAGM missiles loaded",
    "vehicle-apache-launch": "AH-64E tactical raid launched",
    "vehicle-apache-hit": "AH-64E ground-ammunition raid",
    "vehicle-apache-lost": "AH-64E Apache Guardian shot down",
    "vehicle-comanche-upgrade": "RAH-66 Comanche conversion",
    "vehicle-comanche-launch": "RAH-66 ammunition-suppression raid launched",
    "vehicle-comanche-hit": "RAH-66 ammunition-suppression raid",
    "vehicle-comanche-damaged": "RAH-66 Comanche forced down",
    "vehicle-comanche-repair": "RAH-66 Comanche repaired",
    "vehicle-mq9-build": "MQ-9B commissioned",
    "vehicle-mq9-load": "AGM-114R missiles loaded",
    "vehicle-mq9-launch": "MQ-9B strike launched",
    "vehicle-mq9-hit": "MQ-9B inventory strike",
    "vehicle-mq9-lost": "MQ-9B destroyed",
    "vehicle-f15e-build": "F-15E commissioned",
    "vehicle-f15e-load": "JASSM-ER missiles loaded",
    "vehicle-f15e-launch": "F-15E strike launched",
    "vehicle-f15e-hit": "F-15E financial strike",
    "vehicle-f15e-lost": "F-15E destroyed",
    "vehicle-a10-build": "A-10C Thunderbolt II commissioned",
    "vehicle-a10-load": "A-10C II CAS strike packages loaded",
    "vehicle-a10-launch": "A-10C battlefield sweep launched",
    "vehicle-a10-hit": "A-10C battlefield interdiction strike",
    "vehicle-a10-lost": "A-10C Thunderbolt II shot down",
    "vehicle-su34-build": "Su-34 Fullback commissioned",
    "vehicle-su34-load": "Su-34 interdiction packages loaded",
    "vehicle-su34-launch": "Su-34 interdiction mission launched",
    "vehicle-su34-hit": "Su-34 battlefield interdiction strike",
    "vehicle-su34-lost": "Su-34 Fullback shot down",
    "vehicle-c130j-build": "C-130J Rapid Dragon commissioned",
    "vehicle-c130j-load": "Rapid Dragon pallets loaded",
    "vehicle-c130j-launch": "Rapid Dragon raid launched",
    "vehicle-c130j-hit": "Rapid Dragon ammunition raid",
    "vehicle-champ-build": "CHAMP mission system commissioned",
    "vehicle-champ-load": "CHAMP microwave missiles loaded",
    "vehicle-champ-launch": "CHAMP strike launched",
    "vehicle-champ-hit": "CHAMP electronics blackout",
    "vehicle-maldx-build": "MALD-X launch cell commissioned",
    "vehicle-maldx-load": "MALD-X decoys loaded",
    "vehicle-maldx-launch": "MALD-X deception mission launched",
    "vehicle-maldx-hit": "MALD-X interceptor deception",
    "vehicle-maldx-failed": "MALD-X false track rejected",
    "vehicle-lrhw-build": "LRHW Dark Eagle battery commissioned",
    "vehicle-lrhw-load": "C-HGB rounds loaded",
    "vehicle-lrhw-launch": "Dark Eagle strike launched",
    "vehicle-lrhw-hit": "Dark Eagle construction strike",
    "vehicle-lrhw-miss": "Dark Eagle blind strike missed",
    "vehicle-xb70-build": "XB-70 Valkyrie commissioned",
    "vehicle-xb70-load": "XB-70 B53 bomb loaded",
    "vehicle-xb70-launch": "XB-70 strategic strike launched",
    "vehicle-xb70-hit": "XB-70 strategic strike",
    "vehicle-xb70-lost": "XB-70 Valkyrie shot down",
    "vehicle-m1a2-build": "M1A2 SEPv3 Abrams commissioned",
    "vehicle-m1a2-load": "M829A4 APFSDS-T rounds loaded",
    "vehicle-m1a2-launch": "M1A2 armored breach launched",
    "vehicle-m1a2-hit": "M1A2 armored breach",
    "vehicle-m1a2-damaged": "M1A2 recovered with battle damage",
    "vehicle-m1a2-repair": "M1A2 depot repair started",
    "vehicle-leopard2a7-build": "Leopard 2A7A1 commissioned",
    "vehicle-leopard2a7-load": "DM63 APFSDS-T rounds loaded",
    "vehicle-leopard2a7-launch": "Leopard 2A7A1 armored breach launched",
    "vehicle-leopard2a7-hit": "Leopard 2A7A1 armored breach",
    "vehicle-j20-hit": "J-20 high-value airframe hunt",
    "vehicle-su57-hit": "Su-57 precision raid",
    "vehicle-leopard2a7-damaged": "Leopard 2A7A1 recovered with battle damage",
    "vehicle-leopard2a7-repair": "Leopard 2A7A1 depot repair started",
    "vehicle-javelin-build": "FGM-148F Javelin Team commissioned",
    "vehicle-javelin-load": "FGM-148F Javelin missile loaded",
    "vehicle-aegis-build": "Aegis destroyer commissioned",
    "vehicle-aegis-load": "SM-6 interceptors loaded",
    "vehicle-p8-build": "P-8A Poseidon commissioned",
    "vehicle-p8-load": "Mk 54 torpedoes loaded",
    "vehicle-patriot-build": "Patriot battery commissioned",
    "vehicle-patriot-load": "PAC-3 MSE loaded",
    "deep-vault-build": "Bedrock Vault constructed",
    "deep-vault-deposit": "Bedrock Vault deposit",
    "deep-vault-withdraw": "Bedrock Vault withdrawal",
    "deep-vault-hit": "Bedrock Vault destroyed",
    "s400-build": "S-400 battery constructed",
    "s400-load": "40N6E interceptors loaded",
    "hex": "hex cast (burned)",
    "coalition-create": "coalition founded",
    "coalition-transfer-sent": "coalition transfer sent",
    "coalition-transfer-received": "coalition transfer received",
    "country-claim": "country claimed",
    "country-production": "GDP production",
    "country-invasion": "country invasion",
    "country-reinforce": "country reinforcement",
    "country-development": "country development",
    "country-conquest": "country conquered",
    "copcall-hit": "Cop Call attack",
    "thor-fabricate-odin": "THOR ODIN module fabricated",
    "thor-fabricate-mjolnir": "THOR MJÖLNIR module fabricated",
    "thor-fabricate-bifrost": "THOR BIFRÖST module fabricated",
    "thor-module-launch": "THOR module launched",
    "thor-module-intercepted": "THOR module intercepted",
    "thor-module-orbit": "THOR module reached orbit",
    "thor-gbi-build": "THOR launch interceptors constructed",
    "thor-gbi-fired": "THOR launch interceptor fired",
    "thor-assembly": "Project THOR orbital assembly",
    "thor-rod-resupply": "THOR kinetic rods supplied",
    "thor-bmd-refit": "Aegis BMD refit",
    "thor-sm3-load": "SM-3 Block IIA interceptors loaded",
    "thor-launch": "Project THOR kinetic strike launched",
    "thor-hit": "Project THOR kinetic strike",
    "nyx-intercept-hit": "NYX launch interception",
    "isd-contribution": "Imperial megaproject contribution",
    "isd-component-launch": "Imperial component launched",
    "isd-assembly": "Imperial Star Destroyer orbital assembly",
    "isd-cinder-charge": "Operation Cinder charge",
    "isd-counter-build": "B-wing Ion Assault Package constructed",
    "isd-counter-fired": "B-wing Ion Assault Package committed",
    "isd-repair": "Imperial Star Destroyer repair",
    "isd-cinder-impact": "Operation Cinder server reset",
    "isd-space-refit": "ISD role refit",
    "space-craft-build": "spacecraft constructed",
    "space-torpedo": "X-wing proton torpedo loaded",
    "space-cargo-load": "ISD cargo escrow and handling",
    "space-xwing-hit": "X-wing transport interception",
    "space-xwing-miss": "X-wing transport missed",
    "space-earth-launch": "Earth spacecraft launch",
    "space-launch-intercept-hit": "Earth spacecraft launch intercepted",
    "space-launch-intercept-miss": "Earth spacecraft launch escaped",
    "isd-planetary-impact": "ISD planetary bombardment",
    "space-survey": "celestial survey",
    "space-travel": "ISD expedition travel",
    "space-lander": "celestial lander or orbital platform",
    "space-site-build": "celestial settlement construction",
    "space-mining": "space mining payout",
    "space-terraform": "settlement viability development",
    "space-development": "colonial GDP development",
    "space-production": "colonial GDP production",
    "space-specialisation": "colony focus change",
    "deathstar-fabricate": "Death Star section fabrication",
    "deathstar-deploy": "Death Star section deployment",
    "deathstar-transport": "Imperial Heavy Transport construction",
    "deathstar-contribution": "Death Star project contribution",
    "deathstar-assembly": "Death Star orbital assembly",
    "deathstar-charge": "Death Star superlaser charge",
    "deathstar-fire": "Death Star firing sequence",
    "deathstar-squadron": "Alliance Assault Squadron preparation",
    "deathstar-interception": "Alliance Assault Squadron interception attempt",
    "deathstar-planet-hit": "Death Star planetary annihilation",
    "deathstar-production": "artificial moon GDP production",
    "deathstar-development": "Death Star industrial development",
    "deathstar-cargo": "Death Star cargo transfer",
    "deathstar-travel": "Death Star relocation",
    "deathstar-repair": "Death Star repair",
    "deathstar-reconstruction": "planetary reconstruction",
    "deathstar-salvage": "planetary debris salvage",
}
ATTACKLOG_METHODS = {
    "space-launch-intercept-hit": "Earth spacecraft launch interception",
    "space-fleet-hit": "Ordinary spacecraft raid / interdiction",
    "space-fleet-precision-hit": "Ordinary spacecraft precision strike / broadside",
    "deathstar-planet-hit": "DS-1 Death Star planet kill / civilisation reset",
    "steal-success": "Pocket theft",
    "robbank-success": "Majin Drill vault robbery",
    "safecrack-success": "Safecrack vault robbery",
    "heist-success": "Crew vault heist",
    "icbm-hit": "ICBM strike",
    "vehicle-b2-hit": "B-2 Spirit / B61-12 strike",
    "vehicle-b52-hit": "B-52H Stratofortress runway strike",
    "vehicle-u2-recon": "U-2S Dragon Lady reconnaissance",
    "vehicle-deimos-recon": "F3 DeathMARK reciprocal scan",
    "vehicle-sr71-recon": "SR-71 Blackbird defensive reconnaissance",
    "vehicle-zumwalt-hit": "DDG-1000 EMRG strike",
    "vehicle-virginia-hit": "Virginia-class submarine raid",
    "vehicle-himars-hit": "M142 HIMARS strike",
    "vehicle-apache-hit": "AH-64E Apache Guardian raid",
    "vehicle-comanche-hit": "RAH-66 Comanche ammunition-suppression raid",
    "vehicle-mq9-hit": "MQ-9B SkyGuardian strike",
    "vehicle-f15e-hit": "F-15E Strike Eagle attack",
    "vehicle-a10-hit": "A-10C Thunderbolt II battlefield sweep",
    "vehicle-su34-hit": "Su-34 Fullback interdiction strike",
    "vehicle-c130j-hit": "C-130J Rapid Dragon ammunition raid",
    "vehicle-champ-hit": "CHAMP electronics blackout",
    "vehicle-maldx-hit": "ADM-160 MALD-X interceptor deception",
    "vehicle-lrhw-hit": "LRHW Dark Eagle construction strike",
    "vehicle-xb70-hit": "XB-70 Valkyrie strategic strike",
    "vehicle-m1a2-hit": "M1A2 armored breach",
    "vehicle-leopard2a7-hit": "Leopard 2A7A1 armored breach",
    "vehicle-j20-hit": "J-20 Mighty Dragon airframe hunt",
    "vehicle-su57-hit": "Su-57 Felon precision raid",
    "hex": "Android 21 Hex",
    "copcall-hit": "Cop Call",
    "country-conquest": "Country conquest",
    "thor-hit": "Project THOR kinetic strike",
    "nyx-intercept-hit": "NYX launch interception",
    "isd-cinder-impact": "ISD Operation Cinder server strike",
    "space-xwing-hit": "X-wing transport interception",
    "isd-planetary-impact": "ISD planetary bombardment",
}
for _jet_model in air.JETS:
    _jet_label = str(VEHICLE_CATALOG[_jet_model]["short"])
    LEDGER_LABELS[f"vehicle-{_jet_model}-launch"] = f"{_jet_label} mission"
    LEDGER_LABELS[f"vehicle-{_jet_model}-repair"] = f"{_jet_label} repair"
LEDGER_LABELS["vehicle-f22-patrol"] = "F-22A combat air patrol"
LEDGER_LABELS.update(
    {reason: ATTACKLOG_METHODS[reason] for reason in ("vehicle-j20-hit", "vehicle-su57-hit")}
)


def _ledger_label(reason: str) -> str:
    if reason in LEDGER_LABELS:
        return LEDGER_LABELS[reason]
    if reason.startswith("buy:"):
        return f"bought {reason[4:]}"
    if reason.startswith("coalition-transfer:"):
        return "coalition logistics transfer"
    return reason


def _s400_intercept_chance(cfg: Any, defender_id: int) -> int:
    return _public_s400_intercept_chance(cfg)


def _public_strategic_chance(cfg: Any, key: str, fallback: int, bonus: int = 0) -> int:
    return max(0, min(100, int(cfg.get(key, fallback)) + int(bonus)))


def _public_s400_intercept_chance(cfg: Any, aircraft: str = "b2") -> int:
    """Return only the ordinary S-400 odds safe to expose in player-facing text."""
    if aircraft == "b52":
        key, fallback = ("economy.vehicle_b52_intercept_chance", 50)
    else:
        key, fallback = ("economy.s400_intercept_chance", 30)
    return _public_strategic_chance(cfg, key, fallback)


def _virginia_rod_break_chance(cfg: Any, defender_id: int, rod_id: str) -> int:
    """Return the Virginia raid's equipped-rod destruction chance."""
    key = (
        "economy.vehicle_virginia_mythic_rod_break_chance"
        if rod_id == "mythicrod"
        else "economy.vehicle_virginia_rod_break_chance"
    )
    chance = int(cfg.get(key, 20 if rod_id == "mythicrod" else 40))
    return max(0, min(100, chance))


def _b2_intercept_chance(cfg: Any, attacker_id: int, defender_id: int) -> int:
    return _public_s400_intercept_chance(cfg)


def _b52_intercept_chance(cfg: Any, attacker_id: int, defender_id: int) -> int:
    return _public_s400_intercept_chance(cfg, "b52")


def _u2_intercept_chance(cfg: Any, attacker_id: int, defender_id: int, defender: Dict[str, Any]) -> int:
    return _public_light_aircraft_intercept_chance(
        cfg, defender, int(cfg.get("economy.aa_rocket_chance", 30))
    )


def _aa_shield_vehicle_bonus(cfg: Any, defender: Dict[str, Any]) -> int:
    """Return the ordinary, public shield bonus for a loaded light-aircraft AA roll."""
    if _remaining(defender.get("aa_shield_until")) <= 0:
        return 0
    return max(0, min(100, int(cfg.get("economy.aa_shield_vehicle_bonus", 10))))


def _public_light_aircraft_intercept_chance(cfg: Any, defender: Dict[str, Any], base_chance: int) -> int:
    """Public regular-AA chance with public perks and the active-shield bonus.

    A loaded rocket remains mandatory and is spent by the calling mission. This
    helper accepts only ordinary configuration and active gameplay modifiers.
    """
    chance = (
        int(base_chance) + plushie_perk(defender, "aa_intercept") + _aa_shield_vehicle_bonus(cfg, defender)
    )
    return max(0, min(100, chance))


def _deimos_success_chance(cfg: Any) -> int:
    """Return Deimos' flat scan chance; no attacker or defense can modify it."""
    return max(0, min(100, int(cfg.get("economy.deimos_success_chance", 60))))


def _comanche_intercept_range(cfg: Any) -> Tuple[int, int]:
    """Return the inclusive low-observable interception range in ascending order."""
    low = max(0, min(100, int(cfg.get("economy.vehicle_comanche_intercept_chance_min", 20))))
    high = max(0, min(100, int(cfg.get("economy.vehicle_comanche_intercept_chance_max", 25))))
    return (low, high) if low <= high else (high, low)


def _comanche_rounds_per_mission(cfg: Any) -> int:
    """Return the ordinary all-store mission's required JAGM commitment."""
    return max(1, int(cfg.get("economy.vehicle_comanche_jagms_per_mission", 2)))


def _comanche_repair_quote(cfg: Any) -> int:
    """Roll and return a persistent whole-million Comanche repair quote."""
    low = max(0, int(cfg.get("economy.vehicle_comanche_repair_cost_min", 100000000)))
    high = max(0, int(cfg.get("economy.vehicle_comanche_repair_cost_max", 200000000)))
    if high < low:
        low, high = (high, low)
    million = 1000000
    low_units = (low + million - 1) // million
    high_units = high // million
    if high_units < low_units:
        return low
    return random.randint(low_units, high_units) * million


def _armored_prefix(model: str) -> str:
    if model not in ARMORED_MODELS:
        raise ValueError(f"Unsupported armored chassis: {model}")
    return model


def _armored_damage_fields(model: str) -> Tuple[str, str, str]:
    prefix = _armored_prefix(model)
    return (f"{prefix}_damaged", f"{prefix}_repair_cost", f"{prefix}_repairing_until")


def _armored_repair_quote(cfg: Any, model: str) -> int:
    """Roll and persist a whole-million armored depot-repair quote."""
    prefix = _armored_prefix(model)
    low = max(0, int(cfg.get(f"economy.vehicle_{prefix}_repair_cost_min", 100000000)))
    high = max(0, int(cfg.get(f"economy.vehicle_{prefix}_repair_cost_max", 175000000)))
    if high < low:
        low, high = (high, low)
    million = 1000000
    return random.randint((low + million - 1) // million, high // million) * million


def _b52_repair_terms(cfg: Any, repair_number: int) -> Tuple[int, int]:
    """Return the fixed cost and duration for one of three B-52 airframe repairs."""
    index = max(0, min(len(B52_REPAIR_COST_PCTS) - 1, int(repair_number) - 1))
    original_price = max(0, int(cfg.get("economy.vehicle_b52_cost", 1000000000)))
    return (original_price * B52_REPAIR_COST_PCTS[index] // 100, B52_REPAIR_HOURS[index])


def _mq9_items_destroyed(stack_size: int, damage_pct: int) -> int:
    """Resolve MQ-9 stack damage with floor rounding and a one-item minimum."""
    before = max(0, int(stack_size))
    if before <= 0:
        return 0
    pct = max(1, min(100, int(damage_pct)))
    return min(before, max(1, before * pct // 100))


def _icbm_target_eligible(cfg: Any, victim: Dict[str, Any]) -> Tuple[bool, int]:
    """A target qualifies when either visible money compartment reaches the floor."""
    floor = max(
        0, int(cfg.get("economy.icbm_min_target_balance", cfg.get("economy.icbm_min_target_bank", 1000)))
    )
    wallet = max(0, int(victim.get("donuts", 0)))
    bank = max(0, int(victim.get("bank", 0)))
    return (wallet >= floor or bank >= floor, floor)


PLUSHIE_DIR = Path(__file__).resolve().parent.parent / "assets" / "plushies"
ITEM_DIR = Path(__file__).resolve().parent.parent / "assets" / "items"
ICBM_DIR = Path(__file__).resolve().parent.parent / "assets" / "icbm"
AA_DIR = Path(__file__).resolve().parent.parent / "assets" / "aa"
STRATEGIC_DIR = Path(__file__).resolve().parent.parent / "assets" / "strategic"
DEIMOS_SUCCESS_LINES = (
    "You can run, but you can't hide.",
    "Found ya.",
    "Tracker on the prowl.",
    "I see you.",
)
DEIMOS_FAILURE_LINES = ("Tracker down.", "Tracker lost.", "Tracker shot down.", "Tracker jammed.")
AA_FAIL_REASONS: List[str] = [
    "📡 Radar jamming scrambled the lock — the warhead slipped through.",
    "🎯 The interceptors missed — the ICBM corkscrewed on terminal approach.",
    "🌧️ Chaff and flares decoyed the salvo wide.",
    "🔧 The targeting computer hiccupped and fired a beat too late.",
    "🛰️ The warhead spoofed the radar with a false signature.",
    "💨 A bad intercept angle — the rockets sailed clean past.",
    "⚡ An ECM burst blinded the battery at the worst possible moment.",
    "🎯 Terminal maneuvering threw the solution off — no kill.",
]
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")
from szofie.plushies import PLUSHIES, PLUSHIE_BY_ID, RETIRED_PLUSHIE_IDS, Plushie, plushie_perk
from szofie.titles import (
    TITLES,
    TITLE_BY_ID,
    RETIRED_TITLE_IDS,
    PURCHASABLE_TITLES,
    EARNED_TITLES,
    equipped_label,
    resolve_title,
    title_price,
    owns_title,
)

VAULT_TIERS: List[Tuple[int, int]] = [(8000, 1), (16000, 2), (32000, 3)]


async def _vehicle_build_autocomplete(interaction, current):
    needle = current.lower().strip()
    return [
        app_commands.Choice(name=spec["label"][:100], value=model)
        for model, spec in VEHICLE_CATALOG.items()
        if not needle or needle in model or needle in spec["label"].lower()
    ][:25]


async def _vehicle_load_autocomplete(interaction, current):
    needle = current.lower().strip()
    return [
        app_commands.Choice(name=VEHICLE_CATALOG[model]["short"], value=model)
        for model in LOADABLE_VEHICLES
        if not needle or needle in model or needle in VEHICLE_CATALOG[model]["short"].lower()
    ][:25]


async def _strategic_objective_autocomplete(
    interaction: discord.Interaction, current: str
) -> List[app_commands.Choice[str]]:
    """Offer focused-strike objectives and consumable IDs used by MQ-9B strikes."""
    selected = getattr(getattr(interaction, "namespace", None), "vehicle", "")
    selected = str(getattr(selected, "value", selected) or "")
    if selected in {"j20", "su57"}:
        cog = interaction.client.get_cog("Economy")
        return await air_commands.objectives(cog, interaction, current) if cog else []
    himars = [
        ("HIMARS — all loaded regular AA", "regular-aa"),
        ("HIMARS — one loaded S-400 interceptor", "s400"),
    ]
    apache = [
        ("Apache — 50% of loaded regular AA", "regular-aa"),
        ("Apache — one loaded S-400 interceptor", "s400"),
        ("Apache — one loaded Patriot interceptor", "patriot"),
        ("Apache — up to two loaded HIMARS rockets", "himars"),
    ]
    mq9 = [(f"MQ-9B — {data['name']}", item) for item, data in CONSUMABLES.items()]
    ground_attack = [
        (f"Blind vehicle objective — {VEHICLE_CATALOG[key]['short']}", key) for key in DEPLOYABLE_VEHICLES
    ]
    maldx = [
        (f"MALD-X — spoof one {label}", key)
        for key, label in {
            "regular-aa": "loaded Radar AA rocket",
            "s400": "loaded S-400 interceptor",
            "aegis": "loaded Aegis SM-6",
            "p8": "loaded P-8A Mk 54",
            "patriot": "loaded Patriot PAC-3 MSE",
            "javelin": "loaded FGM-148F Javelin",
        }.items()
    ]
    armored: List[Tuple[str, str]] = []
    if selected in ARMORED_MODELS:
        namespace = getattr(interaction, "namespace", None)
        selected_target = getattr(namespace, "target", None)
        target_id = getattr(selected_target, "id", None)
        if target_id is not None and interaction.guild_id is not None:
            client = getattr(interaction, "client", None)
            cog = client.get_cog("Economy") if client is not None else None
            if cog is not None:
                territories = country_state.state(cog.econ.store.load(interaction.guild_id))["territories"]
                for country in country_state.COUNTRIES:
                    territory = territories.get(country.id)
                    if isinstance(territory, dict) and int(territory.get("owner", 0)) == int(target_id):
                        label = "Abrams" if selected == "m1a2" else "Leopard"
                        armored.append((f"{label} breach — {country.flag} {country.name}", country.id))
    lrhw = [
        (f"Dark Eagle — destroy {VEHICLE_CATALOG[key]['short']} while building", key)
        for key in DEPLOYABLE_VEHICLES
        if key != "lrhw"
    ]
    if selected == "lrhw":
        namespace = getattr(interaction, "namespace", None)
        selected_target = getattr(namespace, "target", None)
        target_id = getattr(selected_target, "id", None)
        if target_id is None:
            raw_target = getattr(selected_target, "value", selected_target)
            try:
                target_id = int(raw_target)
            except (TypeError, ValueError):
                target_id = None
        client = getattr(interaction, "client", None)
        cog = client.get_cog("Economy") if client is not None else None
        if cog is not None and interaction.guild_id is not None and (target_id is not None):
            pilot = cog.user(interaction.guild_id, interaction.user.id)
            victim = cog.user(interaction.guild_id, int(target_id))
            intel = cog._active_construction_intel(pilot, int(target_id), victim)
            if intel is not None:
                tracked: List[Tuple[str, str]] = []
                for key, observed_ready in intel.get("builds", {}).items():
                    if key not in DEPLOYABLE_VEHICLES or key == "lrhw":
                        continue
                    state = cog._vehicle_settle(victim, key)
                    ready = _parse(state.get("building_until"))
                    observed = _parse(observed_ready)
                    if ready is None or ready <= _now() or observed is None or (ready != observed):
                        continue
                    tracked.append(
                        (
                            f"Recon track — {VEHICLE_CATALOG[key]['short']} · ready {ready.strftime('%H:%M UTC')}",
                            key,
                        )
                    )
                lrhw = tracked or [
                    ("Recon package — no active conventional builds detected", "no-active-builds")
                ]
    if selected in {"a10", "su34"}:
        namespace = getattr(interaction, "namespace", None)
        selected_target = getattr(namespace, "target", None)
        target_id = getattr(selected_target, "id", None)
        if target_id is None:
            raw_target = getattr(selected_target, "value", selected_target)
            try:
                target_id = int(raw_target)
            except (TypeError, ValueError):
                target_id = None
        client = getattr(interaction, "client", None)
        cog = client.get_cog("Economy") if client is not None else None
        if cog is not None and interaction.guild_id is not None and (target_id is not None):
            pilot = cog.user(interaction.guild_id, interaction.user.id)
            victim = cog.user(interaction.guild_id, int(target_id))
            if cog._active_recon(pilot, int(target_id), victim):
                identified = [
                    (f"Target package — {VEHICLE_CATALOG[key]['short']}", key)
                    for key in DEPLOYABLE_VEHICLES
                    if cog._vehicle_settle(victim, key).get("owned")
                ]
                ground_attack = identified or [
                    ("Target package — no completed conventional vehicles detected", "no-completed-vehicles")
                ]
    if selected == "himars":
        options = himars
    elif selected == "apache":
        options = apache
    elif selected == "mq9":
        options = mq9
    elif selected == "c130j":
        options = [("Rapid Dragon — distributed: 40% of three fullest depots", "distributed")]
        options += [
            (f"Concentrated — 80% of {label} (requires recon)", f"focused:{key}")
            for key, label in CONVENTIONAL_AMMO_TARGETS.items()
        ]
    elif selected == "maldx":
        options = maldx
    elif selected == "lrhw":
        options = lrhw
    elif selected in {"a10", "su34"}:
        options = ground_attack
    elif selected in ARMORED_MODELS:
        options = armored
    else:
        options = himars + apache + mq9 + maldx + lrhw
    needle = (current or "").lower().strip()
    return [
        app_commands.Choice(name=name, value=value)
        for name, value in options
        if not needle or needle in name.lower() or needle in value.lower()
    ][:25]


async def _strategic_secondary_autocomplete(
    interaction: discord.Interaction, current: str
) -> List[app_commands.Choice[str]]:
    """Offer valid second targets for MQ-9B split strikes."""
    namespace = getattr(interaction, "namespace", None)
    selected = getattr(namespace, "vehicle", "")
    selected = str(getattr(selected, "value", selected) or "")
    primary = str(getattr(namespace, "objective", "") or "").strip().lower()
    if selected != "mq9":
        return []
    options = [(f"MQ-9B split — {data['name']}", item) for item, data in CONSUMABLES.items()]
    needle = (current or "").lower().strip()
    return [
        app_commands.Choice(name=name, value=value)
        for name, value in options
        if value != primary and (not needle or needle in name.lower() or needle in value.lower())
    ][:25]


async def _owned_country_autocomplete(
    interaction: discord.Interaction, current: str
) -> List[app_commands.Choice[str]]:
    """Offer only countries currently ruled by the requesting player."""
    if interaction.guild_id is None:
        return []
    client = getattr(interaction, "client", None)
    cog = client.get_cog("Economy") if client is not None else None
    if cog is None:
        return []
    territories = country_state.state(cog.econ.store.load(interaction.guild_id))["territories"]
    needle = (current or "").casefold().strip()
    choices: List[app_commands.Choice[str]] = []
    for country in country_state.COUNTRIES:
        territory = territories.get(country.id)
        if not isinstance(territory, dict):
            continue
        if int(territory.get("owner", 0)) != int(interaction.user.id):
            continue
        label = f"{country.flag} {country.name}"
        if needle and needle not in label.casefold() and (needle not in country.id):
            continue
        choices.append(app_commands.Choice(name=label, value=country.id))
    return choices[:25]


SLOT_REEL: List[Tuple[str, int]] = [
    ("🍬", 39),
    ("🧪", 39),
    ("⭐", 39),
    ("🍭", 39),
    ("🍫", 39),
    ("🍩", 25),
    ("👿", 18),
    ("💠", 12),
]
_COMMONS = {"🍬", "🧪", "⭐", "🍭", "🍫"}
TRIPLE_PAYOUT = {"💠": 50, "👿": 15, "🍩": 8}
PAIR_PAYOUT = 2
COMMON_TRIPLE = 3
JACKPOT_MULT = TRIPLE_PAYOUT["💠"]


def _casino_display_snapshot(user: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    raw = user.get("casino_stats", {})
    return (raw if isinstance(raw, dict) else {}, max(0, int(user.get("casino_reputation", 0))))


WORK_CAREERS: Dict[str, Dict[str, Any]] = {
    "logistics": {
        "name": "Freight & Logistics",
        "emoji": "🚚",
        "roles": ("Courier", "Freight Operator", "Dispatcher", "Logistics Manager", "Port Director"),
        "summary": "Route cargo safely and keep the supply chain moving.",
    },
    "engineering": {
        "name": "Systems Engineering",
        "emoji": "🔧",
        "roles": (
            "Apprentice Mechanic",
            "Systems Technician",
            "Project Engineer",
            "Chief Engineer",
            "Program Director",
        ),
        "summary": "Diagnose faults and keep complicated machinery alive.",
    },
    "medicine": {
        "name": "Emergency Medicine",
        "emoji": "🚑",
        "roles": ("Hospital Porter", "EMT", "Paramedic", "Trauma Specialist", "Medical Director"),
        "summary": "Triage patients and make calm decisions under pressure.",
    },
    "security": {
        "name": "Protective Security",
        "emoji": "🛡️",
        "roles": (
            "Security Officer",
            "Patrol Supervisor",
            "Intelligence Analyst",
            "Security Commander",
            "Operations Director",
        ),
        "summary": "Protect facilities, investigate alarms and control access.",
    },
}
CAREER_LICENCES = progression.LICENCES
WORK_SCENARIOS: Dict[str, List[Dict[str, Any]]] = {
    "logistics": [
        {
            "prompt": "A cargo pallet shifts as your truck enters a busy motorway. What do you do?",
            "choices": ("Stop safely and secure it", "Drive faster to the depot", "Ignore the noise"),
            "correct": 0,
            "success": "You secured the load before anything was damaged.",
            "failure": "The cargo arrived, but damaged packaging cost you the performance bonus.",
        },
        {
            "prompt": "Your planned route is closed and the delivery has a firm deadline. What is the best move?",
            "choices": ("Use an approved alternate route", "Enter the closed road", "Abandon the shipment"),
            "correct": 0,
            "success": "Your approved detour delivered the shipment safely and on time.",
            "failure": "Dispatch recovered the shipment, but your decision delayed the delivery.",
        },
        {
            "prompt": "A refrigerated shipment is warming above its safe temperature. What comes first?",
            "choices": (
                "Transfer it to working refrigeration",
                "Change the temperature log",
                "Deliver it normally",
            ),
            "correct": 0,
            "success": "You protected the cold chain and saved the entire shipment.",
            "failure": "The shipment was recovered, but quality control rejected your handling.",
        },
    ],
    "engineering": [
        {
            "prompt": "A motor is overheating and producing a burning smell. What do you do first?",
            "choices": ("Shut it down and inspect it", "Increase its power", "Pour water on it"),
            "correct": 0,
            "success": "You isolated the fault before the motor suffered permanent damage.",
            "failure": "The emergency stop saved the machine, but your diagnosis missed the mark.",
        },
        {
            "prompt": "You must service an electrical control panel. What is the safe first step?",
            "choices": ("Isolate and lock out power", "Touch-test the wiring", "Work while it is live"),
            "correct": 0,
            "success": "Your lockout procedure kept the repair safe and clean.",
            "failure": "A supervisor intervened before anyone was hurt. No performance bonus today.",
        },
        {
            "prompt": "A repaired turbine now vibrates at operating speed. What should you check?",
            "choices": ("Alignment and balance", "Paint thickness", "The office thermostat"),
            "correct": 0,
            "success": "You corrected the imbalance and returned the turbine to service.",
            "failure": "The turbine stayed offline while another engineer corrected the problem.",
        },
    ],
    "medicine": [
        {
            "prompt": "Three patients arrive together. Who receives immediate attention?",
            "choices": (
                "The patient struggling to breathe",
                "A minor ankle sprain",
                "A routine prescription refill",
            ),
            "correct": 0,
            "success": "Your triage decision got the critical patient treated immediately.",
            "failure": "The team corrected the triage order, but your assessment needs work.",
        },
        {
            "prompt": "A medication label does not match the written dosage. What do you do?",
            "choices": ("Stop and verify the order", "Estimate the dosage", "Give both amounts"),
            "correct": 0,
            "success": "You caught the discrepancy before the medication reached the patient.",
            "failure": "A senior clinician caught the mismatch. Your shift continued under supervision.",
        },
        {
            "prompt": "An unconscious patient arrives in the emergency department. What is checked first?",
            "choices": ("Airway and breathing", "Their insurance paperwork", "Their meal preference"),
            "correct": 0,
            "success": "You stabilized the patient's airway while the trauma team assembled.",
            "failure": "The trauma team took over and stabilized the patient.",
        },
    ],
    "security": [
        {
            "prompt": "An unattended bag is found beside a crowded entrance. What do you do?",
            "choices": ("Isolate the area and report it", "Open it yourself", "Move it into the crowd"),
            "correct": 0,
            "success": "You secured the area and the response team cleared the bag safely.",
            "failure": "Your supervisor secured the scene before the situation escalated.",
        },
        {
            "prompt": "An alarm activates in a camera blind spot. What is the correct response?",
            "choices": ("Dispatch a safe verification", "Disable the alarm", "Assume it is false"),
            "correct": 0,
            "success": "Your patrol verified and contained the incident quickly.",
            "failure": "Another patrol resolved the alarm after your response caused a delay.",
        },
        {
            "prompt": "Someone follows an employee through a secure door without scanning a badge. What now?",
            "choices": ("Challenge and verify access", "Ignore them", "Prop the door open"),
            "correct": 0,
            "success": "You stopped an unauthorized access attempt at the door.",
            "failure": "Access control caught the visitor deeper inside the facility.",
        },
    ],
}


def spin(rng: random.Random) -> Tuple[List[str], int]:
    """Return (three symbols, payout multiplier). 0 = loss."""
    symbols = [s for s, _ in SLOT_REEL]
    weights = [w for _, w in SLOT_REEL]
    reels = rng.choices(symbols, weights=weights, k=3)
    a, b, c = reels
    if a == b == c:
        if a in TRIPLE_PAYOUT:
            return (reels, TRIPLE_PAYOUT[a])
        if a in _COMMONS:
            return (reels, COMMON_TRIPLE)
        return (reels, COMMON_TRIPLE)
    if a == b or a == c or b == c:
        return (reels, PAIR_PAYOUT)
    return (reels, 0)


def biased_spin(rng: random.Random, win_pct: int) -> Tuple[List[str], int]:
    want_win = rng.randint(1, 100) <= win_pct
    reels, mult = spin(rng)
    for _ in range(40):
        if (mult > 0) == want_win:
            break
        reels, mult = spin(rng)
    if (mult > 0) != want_win:
        symbols = [symbol for symbol, _ in SLOT_REEL]
        weights = [weight for _, weight in SLOT_REEL]
        if want_win:
            pair = rng.choices(symbols, weights=weights, k=1)[0]
            other = rng.choice([symbol for symbol in symbols if symbol != pair])
            reels, mult = ([pair, pair, other], PAIR_PAYOUT)
        else:
            reels, mult = (rng.sample(symbols, 3), 0)
    return (reels, mult)


def _hex_adjusted_slots_pct(user_id: int, user: Dict[str, Any], chance: Optional[int]) -> Optional[int]:
    hexcut = hexes.penalty(user, user_id)
    if not hexcut:
        return chance
    base = max(0, min(100, int(chance))) if chance is not None else 45
    return max(1, int(base * (1 - hexcut)))


WHEEL: List[Tuple[Any, int, str]] = [
    (0, 1100, "💥 Bust"),
    (0.5, 400, "😬 Half back"),
    (1, 50, "😛 Break even"),
    (2, 250, "🍬 2×"),
    (3, 120, "🍭 3×"),
    ("POT", 3, "🍩 JACKPOT — the whole pot!"),
]


def spin_wheel(rng: random.Random) -> Tuple[int, str]:
    index = rng.choices(range(len(WHEEL)), weights=[weight for _, weight, _ in WHEEL])[0]
    return (WHEEL[index][0], WHEEL[index][2])


def wheel_non_jackpot_rebate(bet: int, percent: int, result: Any) -> int:
    """House-funded rebate; the jackpot alone does not receive it."""
    if result == "POT":
        return 0
    return max(0, int(bet)) * max(0, min(100, int(percent))) // 100


ROULETTE_RED = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}


def roulette_profit_percent(
    base_ratio: int, even_percent: int = 145, dozen_percent: int = 270, straight_percent: int = 4000
) -> int:
    """Return configured profit, keeping split/street/corner bets unchanged.

    The parsed ratio 35 identifies a straight-up selection; it is not its live payout.
    """
    if base_ratio == 1:
        return max(0, int(even_percent))
    if base_ratio == 2:
        return max(0, int(dozen_percent))
    if base_ratio == 35:
        return max(0, int(straight_percent))
    return max(0, int(base_ratio)) * 100


def _roulette_ratio_text(profit_percent: int) -> str:
    whole, fractional = divmod(max(0, int(profit_percent)), 100)
    return f"{whole}.{fractional:02d}".rstrip("0").rstrip(".") if fractional else str(whole)


def _roulette_bets():
    """Map a named outside bet -> (label, payout_ratio, predicate(n))."""
    return {
        "red": ("Red", 1, lambda n: n in ROULETTE_RED),
        "black": ("Black", 1, lambda n: n != 0 and n not in ROULETTE_RED),
        "even": ("Even", 1, lambda n: n != 0 and n % 2 == 0),
        "odd": ("Odd", 1, lambda n: n % 2 == 1),
        "low": ("Low (1–18)", 1, lambda n: 1 <= n <= 18),
        "high": ("High (19–36)", 1, lambda n: 19 <= n <= 36),
        "1st12": ("1st dozen (1–12)", 2, lambda n: 1 <= n <= 12),
        "2nd12": ("2nd dozen (13–24)", 2, lambda n: 13 <= n <= 24),
        "3rd12": ("3rd dozen (25–36)", 2, lambda n: 25 <= n <= 36),
    }


def parse_roulette_space(space: str):
    """Return (label, payout_ratio, predicate) for a bet, or None if invalid.

    Accepts the named outside bets above, or a straight-up number 0–36.
    Ratio 35 is the selection type; roulette_profit_percent resolves its payout.
    """
    key = space.strip().lower().replace(" ", "")
    aliases = {
        "1st": "1st12",
        "2nd": "2nd12",
        "3rd": "3rd12",
        "firstdozen": "1st12",
        "seconddozen": "2nd12",
        "thirddozen": "3rd12",
    }
    key = aliases.get(key, key)
    named = _roulette_bets()
    if key in named:
        return named[key]
    if key.isdigit():
        n = int(key)
        if 0 <= n <= 36:
            return (f"Straight up {n}", 35, (lambda target: lambda x: x == target)(n))
    if ":" in key:
        kind, raw = key.split(":", 1)
        try:
            nums = tuple((int(part) for part in raw.replace(",", "-").split("-") if part))
        except ValueError:
            nums = ()
        valid = False
        ratio = 0
        if kind == "split" and len(nums) == 2 and (len(set(nums)) == 2):
            a, b = sorted(nums)
            valid = (
                a == 0
                and b in {1, 2, 3}
                or (1 <= a < b <= 36 and (b - a == 3 or (b - a == 1 and (a - 1) // 3 == (b - 1) // 3)))
            )
            ratio = 18
        elif kind == "street" and len(nums) == 3:
            a, b, c = sorted(nums)
            valid = a in range(1, 35, 3) and (a, b, c) == (a, a + 1, a + 2)
            ratio = 12
        elif kind == "corner" and len(nums) == 4:
            a, b, c, d = sorted(nums)
            valid = 1 <= a <= 32 and a % 3 != 0 and ((a, b, c, d) == (a, a + 1, a + 3, a + 4))
            ratio = 8
        if valid:
            targets = frozenset(nums)
            return (
                f"{kind.title()} {'/'.join(map(str, nums))}",
                ratio,
                (lambda values: lambda n: n in values)(targets),
            )
    return None


def roulette_color(n: int) -> str:
    if n == 0:
        return "🟩"
    return "🟥" if n in ROULETTE_RED else "⬛"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse(iso: Optional[str]) -> Optional[dt.datetime]:
    if not iso:
        return None
    try:
        return dt.datetime.fromisoformat(iso)
    except ValueError:
        return None


def _strategic_lockdown_until(user: Dict[str, Any]) -> Optional[dt.datetime]:
    """Return an active B-52 runway-lockdown expiry, cleaning stale state."""
    until = _parse(user.get("strategic_lockdown_until"))
    if until is None or until <= _now():
        user["strategic_lockdown_until"] = None
        return None
    return until


def _electronic_blackout_until(user: Dict[str, Any]) -> Optional[dt.datetime]:
    """Return an active CHAMP electronics-suppression expiry, cleaning stale state."""
    until = _parse(user.get("electronic_blackout_until"))
    if until is None or until <= _now():
        user["electronic_blackout_until"] = None
        return None
    return until


def _remaining(until: Optional[str]) -> float:
    when = _parse(until)
    if when is None:
        return 0.0
    return max(0.0, (when - _now()).total_seconds())


class RouletteRepeatView(discord.ui.View):
    """One-use replay and wallet-percentage controls for a roulette result."""

    def __init__(self, cog, user_id: int, bet: int, space: str):
        super().__init__(timeout=600)
        self.cog = cog
        self.user_id = int(user_id)
        self.bet = int(bet)
        self.space = str(space)
        self.used = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await ui.respond(
                interaction,
                embed=ui.error_embed("That repeat button belongs to another player."),
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Repeat bet", style=discord.ButtonStyle.primary, emoji="🔁")
    async def repeat(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._play(interaction, self.bet)

    @discord.ui.button(label="Bet 25% wallet", style=discord.ButtonStyle.secondary)
    async def quarter(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        balance = int(self.cog.user(interaction.guild_id, interaction.user.id)["donuts"])
        await self._play(interaction, max(1, balance // 4))

    @discord.ui.button(label="Bet 50% wallet", style=discord.ButtonStyle.secondary)
    async def half(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        balance = int(self.cog.user(interaction.guild_id, interaction.user.id)["donuts"])
        await self._play(interaction, max(1, balance // 2))

    @discord.ui.button(label="Bet All", style=discord.ButtonStyle.danger)
    async def bet_all(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        balance = int(self.cog.user(interaction.guild_id, interaction.user.id)["donuts"])
        await self._play(interaction, balance)

    async def _play(self, interaction: discord.Interaction, bet: int) -> None:
        if self.used:
            await ui.respond(
                interaction,
                embed=ui.warn_embed("That result was already replayed. Use the newest buttons."),
                ephemeral=True,
            )
            return
        self.used = True
        for child in self.children:
            child.disabled = True
        committed = await self.cog._roulette_run(interaction, bet, self.space, repeated=True)
        if committed is False:
            self.used = False
            for child in self.children:
                child.disabled = False
        message = getattr(interaction, "message", None)
        if message is not None:
            try:
                await message.edit(view=self)
            except discord.HTTPException:
                pass


class CasinoReplayView(discord.ui.View):
    """User-bound quick bets for the slot machine and prize wheel."""

    def __init__(self, cog, user_id: int, game: str, bet: int):
        super().__init__(timeout=600)
        self.cog = cog
        self.user_id = int(user_id)
        self.game = game
        self.bet = int(bet)
        self.message: Optional[discord.Message] = None

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except (discord.HTTPException, OSError, asyncio.TimeoutError):
                pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await ui.respond(
                interaction,
                embed=ui.error_embed("These bet buttons belong to another player."),
                ephemeral=True,
            )
            return False
        return True

    async def _play(self, interaction: discord.Interaction, bet: int) -> None:
        await ui.defer_response(interaction)
        if self.game == "slots":
            await self.cog.slots.callback(self.cog, interaction, bet)
        else:
            await self.cog.wheel.callback(self.cog, interaction, bet)

    @discord.ui.button(label="Same bet", style=discord.ButtonStyle.primary, emoji="🔁")
    async def same(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._play(interaction, self.bet)

    @discord.ui.button(label="Bet 25% wallet", style=discord.ButtonStyle.secondary)
    async def quarter(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await ui.defer_response(interaction)
        balance = int(self.cog.user(interaction.guild_id, interaction.user.id)["donuts"])
        await self._play(interaction, max(1, balance // 4))

    @discord.ui.button(label="Bet 50% wallet", style=discord.ButtonStyle.secondary)
    async def half(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await ui.defer_response(interaction)
        balance = int(self.cog.user(interaction.guild_id, interaction.user.id)["donuts"])
        await self._play(interaction, max(1, balance // 2))

    @discord.ui.button(label="Bet All", style=discord.ButtonStyle.danger)
    async def bet_all(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await ui.defer_response(interaction)
        balance = int(self.cog.user(interaction.guild_id, interaction.user.id)["donuts"])
        await self._play(interaction, balance)


class StrategicMissionConfirmView(discord.ui.View):
    """Confirmation gate for destructive strategic missions."""

    def __init__(
        self,
        cog: "EconomyCog",
        pilot_id: int,
        vehicle: str,
        target: discord.Member,
        objective: Optional[str] = None,
        secondary_objective: Optional[str] = None,
    ) -> None:
        super().__init__(timeout=120.0)
        self.cog = cog
        self.pilot_id = int(pilot_id)
        self.vehicle = vehicle
        self.target = target
        self.objective = objective
        self.secondary_objective = secondary_objective
        self.message: Optional[discord.Message] = None
        self.resolved = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.pilot_id:
            await ui.respond(
                interaction,
                embed=ui.error_embed("Only the mission commander can authorize this sortie."),
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self) -> None:
        if self.resolved or self.message is None:
            return
        self.resolved = True
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(
                embed=ui.warn_embed("Mission authorization expired. Nothing was spent."), view=self
            )
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Authorize mission", style=discord.ButtonStyle.danger, emoji="✈️")
    async def authorize(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.resolved:
            return
        error = self.cog._mission_preflight(
            interaction.guild_id,
            interaction.user.id,
            self.vehicle,
            self.target,
            self.objective,
            self.secondary_objective,
        )
        if error:
            self.resolved = True
            self.stop()
            await interaction.response.edit_message(embed=ui.error_embed(error), view=None)
            return
        self.resolved = True
        self.stop()
        vehicle_name = self.cog._vehicle_label(self.vehicle)
        if self.vehicle == "apache" and interaction.guild_id is not None:
            apache = self.cog._vehicle_settle(
                self.cog.user(interaction.guild_id, interaction.user.id), "apache"
            )
            if apache.get("comanche_upgraded"):
                vehicle_name = "RAH-66 COMANCHE"
        await interaction.response.edit_message(
            embed=ui.base_embed(
                title="✈️ Mission authorized",
                description=f"The **{vehicle_name}** sortie against {self.target.mention} is underway.",
                color=ui.COLOR_WARN,
            ),
            view=None,
        )
        if self.vehicle == "b2":
            await self.cog._b2_execute(interaction, self.target)
        elif self.vehicle == "u2":
            await self.cog._u2_execute(interaction, self.target)
        elif self.vehicle == "deimos":
            await self.cog._deimos_execute(interaction, self.target)
        elif self.vehicle == "sr71":
            await self.cog._sr71_execute(interaction, self.target)
        elif self.vehicle in ARMORED_MODELS:
            await self.cog._armored_execute(interaction, self.target, self.objective, self.vehicle)
        else:
            await self.cog._focused_vehicle_execute(
                interaction, self.target, self.vehicle, self.objective, self.secondary_objective
            )

    @discord.ui.button(label="Stand down", style=discord.ButtonStyle.secondary, emoji="✋")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.resolved:
            return
        self.resolved = True
        self.stop()
        await interaction.response.edit_message(
            embed=ui.warn_embed("Mission cancelled. Nothing was spent."), view=None
        )


class WorkShiftView(discord.ui.View):
    """One quick career decision; only the worker who started it may answer."""

    def __init__(
        self, cog: "EconomyCog", guild_id: int, worker_id: int, career: str, scenario: Dict[str, Any]
    ) -> None:
        super().__init__(timeout=60.0)
        self.cog = cog
        self.guild_id = int(guild_id)
        self.worker_id = int(worker_id)
        self.career = career
        self.generation = reset_generation(cog.user(guild_id, worker_id))
        self.scenario = scenario
        self.message: Optional[discord.Message] = None
        self.resolved = False
        for index, label in enumerate(scenario["choices"]):
            button = discord.ui.Button(
                label=str(label)[:80],
                style=discord.ButtonStyle.secondary,
                custom_id=f"work:{worker_id}:{career}:{index}",
            )

            async def choose(interaction: discord.Interaction, selected: int = index) -> None:
                await self._answer(interaction, selected)

            button.callback = choose
            self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.worker_id:
            await ui.respond(
                interaction, embed=ui.error_embed("This is someone else's shift."), ephemeral=True
            )
            return False
        return True

    async def _answer(self, interaction: discord.Interaction, selected: int) -> None:
        if self.resolved:
            return
        self.resolved = True
        self.stop()
        correct_index = int(self.scenario["correct"])
        for index, child in enumerate(self.children):
            child.disabled = True
            if index == correct_index:
                child.style = discord.ButtonStyle.success
            elif index == selected:
                child.style = discord.ButtonStyle.danger
        await interaction.response.defer()
        if reset_generation(self.cog.user(self.guild_id, self.worker_id)) != self.generation:
            await interaction.edit_original_response(
                embed=ui.warn_embed("This shift ended when the account was reset. Start a new shift."),
                view=self,
            )
            return
        embed = await self.cog._complete_work_shift(
            interaction, self.career, self.scenario, selected == correct_index
        )
        progress_view = CareerProgressView(self.cog, self.worker_id)
        await interaction.edit_original_response(embed=embed, view=progress_view)
        progress_view.message = await ui.response_message(interaction)

    async def on_timeout(self) -> None:
        if self.resolved or self.message is None:
            return
        self.resolved = True
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(
                embed=ui.warn_embed(
                    "The shift expired before you made a decision. No salary or XP was awarded."
                ),
                view=self,
            )
        except discord.HTTPException:
            pass
        if reset_generation(self.cog.user(self.guild_id, self.worker_id)) == self.generation:
            await self.cog._expire_work_shift(self.guild_id, self.worker_id)


class CareerProgressView(activity_ui.BoundView):
    def __init__(self, cog, uid):
        super().__init__(uid)
        self.cog = cog

    @discord.ui.button(label="View licences", style=discord.ButtonStyle.secondary)
    async def licences(self, interaction, button):
        await EconomyCog.job_licences.callback(self.cog, interaction)

    @discord.ui.button(label="Certify", style=discord.ButtonStyle.success)
    async def certify(self, interaction, button):
        await self.cog.certification_preview(interaction, helper=False)

    @discord.ui.button(label="Certify helper", style=discord.ButtonStyle.primary)
    async def helper(self, interaction, button):
        await self.cog.certification_preview(interaction, helper=True)


class EconomyCog(commands.Cog, name="Economy"):
    """Donuts, slots, stealing and a shop."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.econ = bot.economy
        self._slot_cd: Dict[int, float] = {}
        self._wheel_cd: Dict[int, float] = {}
        self.interest_sweep.start()
        self.autonomous_work_sweep.start()

    def _wheel_pot(self, guild_id: int) -> int:
        """The progressive wheel jackpot, stored on the guild's economy doc root."""
        return int(self.econ.store.load(guild_id).get("wheel_pot", 0))

    def _set_wheel_pot(self, guild_id: int, value: int) -> None:
        self.econ.store.load(guild_id)["wheel_pot"] = max(0, int(value))

    def _roulette_pot(self, guild_id: int) -> int:
        """Wager-scaled progressive prize for a winning straight-up bet."""
        return int(self.econ.store.load(guild_id).get("roulette_jackpot", 0))

    def _set_roulette_pot(self, guild_id: int, value: int) -> None:
        self.econ.store.load(guild_id)["roulette_jackpot"] = max(0, int(value))

    async def cog_unload(self) -> None:
        self.interest_sweep.cancel()
        self.autonomous_work_sweep.cancel()
        await self.econ.flush()

    def _accrue_interest(self, cfg, user: Dict[str, Any], mult: int = 1) -> int:
        """Credit whole-day interest to a vault. Idempotent per day.

        Returns donuts credited. Advances `bank_interest_at` by the number of
        full days consumed so on-touch and the background sweep never overlap.
        First contact just stamps the clock — no retroactive interest.
        """
        pct = int(cfg.get("economy.bank_interest_percent", 0) or 0)
        tier = int(user.get("vault_tier", 0))
        if 0 < tier <= len(VAULT_TIERS):
            pct += VAULT_TIERS[tier - 1][1]
        now = _now()
        last = _parse(user.get("bank_interest_at"))
        if last is None:
            user["bank_interest_at"] = now.isoformat()
            return 0
        days = int((now - last).total_seconds() // 86400)
        if days <= 0:
            return 0
        user["bank_interest_at"] = (last + dt.timedelta(days=days)).isoformat()
        if pct <= 0:
            return 0
        cap = int(cfg.get("economy.bank_interest_daily_cap", 0) or 0)
        if cap:
            cap += cap * plushie_perk(user, "interest") // 100
        credited = 0
        for _ in range(days):
            bank = user.get("bank", 0)
            if bank <= 0:
                break
            day_int = bank * pct // 100
            if cap:
                day_int = min(day_int, cap)
            if day_int <= 0:
                break
            day_int *= mult
            user["bank"] = bank + day_int
            credited += day_int
        return credited

    async def _accrue_and_log(self, cfg, guild_id: int, user_id: int, u) -> int:
        """Accrue interest and record any credit to the ledger."""
        mult = (
            int(cfg.get("events.interest_mult", 2))
            if gevents.is_active(self.econ.store.load(guild_id), "interest")
            else 1
        )
        credited = self._accrue_interest(cfg, u, mult)
        if credited:
            await self._log(guild_id, user_id, credited, "interest", u)
        return credited

    @tasks.loop(minutes=60)
    async def interest_sweep(self) -> None:
        """Keep every vault current so the leaderboard and robbers see real
        balances even when the owner isn't interacting — and trim old ledger
        entries so the audit log doesn't grow without bound."""
        seen: set[int] = set()
        for guild in list(self.bot.guilds):
            canonical = self.econ.store.canonical_id(guild.id)
            if canonical in seen:
                continue
            seen.add(canonical)
            days = int(self.cfg(guild.id).get("economy.ledger_retention_days", 7))
            if days > 0:
                await self.bot.ledger.prune(guild.id, days)
        seen.clear()
        for guild in list(self.bot.guilds):
            canonical = self.econ.store.canonical_id(guild.id)
            if canonical in seen:
                continue
            seen.add(canonical)
            cfg = self.cfg(guild.id)
            if not cfg.get("economy.enabled", True):
                continue
            if int(cfg.get("economy.bank_interest_percent", 0) or 0) <= 0:
                continue
            mult = (
                int(cfg.get("events.interest_mult", 2))
                if gevents.is_active(self.econ.store.load(guild.id), "interest")
                else 1
            )
            touched = False
            for uid, user in self.econ.all_users(guild.id).items():
                before = user.get("bank_interest_at")
                credited = self._accrue_interest(cfg, user, mult)
                if credited:
                    await self._log(guild.id, int(uid), credited, "interest", user)
                if credited > 0 or user.get("bank_interest_at") != before:
                    touched = True
            if touched:
                await self.persist(guild.id)

    @interest_sweep.before_loop
    async def _before_sweep(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=1)
    async def autonomous_work_sweep(self) -> None:
        """Settle permanent Android 21 helpers, including offline catch-up.

        A cadence anchor is advanced by whole work intervals rather than reset to
        wall-clock time, so restarts and late sweep ticks never discard earnings.
        Each shared economy is handled once even when the bot is in several of its
        aliased guilds.
        """
        seen: set[int] = set()
        for guild in list(self.bot.guilds):
            canonical = self.econ.store.canonical_id(guild.id)
            if canonical in seen:
                continue
            seen.add(canonical)
            cfg = self.cfg(guild.id)
            if not cfg.get("economy.enabled", True):
                continue
            touched = False
            payouts: List[Tuple[int, int, int, int]] = []
            users = self.econ.all_users(guild.id)
            for uid, user in users.items():
                gross, garnished, shifts, changed = self._settle_android21_helper(
                    cfg, guild.id, int(uid), user
                )
                touched = touched or changed
                if gross:
                    payouts.append((int(uid), gross, garnished, shifts))
            if touched:
                await self.persist(guild.id)
            for uid, gross, garnished, shifts in payouts:
                user = self.user(guild.id, uid)
                await self._log(
                    guild.id,
                    uid,
                    gross - garnished,
                    "android21-helper-work",
                    user,
                    detail=f"{shifts} automatic shift(s)",
                )

    @autonomous_work_sweep.before_loop
    async def _before_autonomous_work_sweep(self) -> None:
        await self.bot.wait_until_ready()

    def cfg(self, guild_id: int):
        return self.bot.config.for_guild(guild_id)

    def user(self, guild_id: int, user_id: int) -> Dict[str, Any]:
        cfg = self.cfg(guild_id)
        starting = int(cfg.get("economy.starting_balance", 100))
        user = self.econ.user(guild_id, user_id, starting)
        return user

    async def persist(self, guild_id: int) -> None:
        await self.econ.save(guild_id)

    def _coalition_attack_error(self, guild_id: int, attacker_id: int, target_id: int) -> Optional[str]:
        """Return the treaty block message when two users are active signatories."""
        doc = self.econ.store.load(guild_id)
        changed = coalitions.cleanup(doc)
        if changed:
            self.econ.mark_dirty(guild_id)
        if coalitions.are_allied(doc, attacker_id, target_id):
            found = coalitions.coalition_for(doc, attacker_id)
            name = str(found[1].get("name", "your coalition")) if found else "your coalition"
            return f"The **{name}** Collective Security Pact forbids attacks between signatories."
        return None

    def emoji(self, cfg) -> str:
        return str(cfg.get("economy.currency_emoji", "🍩"))

    def name(self, cfg) -> str:
        return str(cfg.get("economy.currency_name", "donuts"))

    def money(self, cfg, amount: int) -> str:
        return f"{self.emoji(cfg)} **{ui.format_donuts(amount)}** {self.name(cfg)}"

    @staticmethod
    def _employment(u: Dict[str, Any]) -> Dict[str, Any]:
        employment = u.setdefault("employment", {})
        if not isinstance(employment, dict):
            employment = u["employment"] = {}
        employment.setdefault("active", None)
        records = employment.setdefault("records", {})
        if not isinstance(records, dict):
            employment["records"] = {}
        today = _now().date().isoformat()
        if employment.get("day") != today:
            employment["day"] = today
            employment["daily_shifts"] = 0
            employment["daily_bonus_claimed"] = False
        employment.setdefault("daily_shifts", 0)
        employment.setdefault("daily_bonus_claimed", False)
        employment.setdefault("pending_until", None)
        if "manual_shifts" not in employment:
            total = sum(
                (
                    max(0, int(record.get("shifts", 0)))
                    for record in employment["records"].values()
                    if isinstance(record, dict)
                )
            )
            employment["manual_shifts"] = max(0, total - max(0, int(u.get("android21_helper_shifts", 0))))
        employment["manual_shifts"] = max(0, int(employment["manual_shifts"]))
        employment.setdefault("manual_work_at", None if u.get("android21_helper") else u.get("work_at"))
        employment["career_licence_tier"] = max(
            0, min(len(CAREER_LICENCES), int(employment.get("career_licence_tier", 0)))
        )
        return employment

    def _licence_unlocked(self, cfg: Any, employment: Dict[str, Any]) -> bool:
        records = employment.get("records", {})
        return isinstance(records, dict) and any(
            (
                isinstance(record, dict) and self._work_level(cfg, max(0, int(record.get("xp", 0)))) >= 5
                for record in records.values()
            )
        )

    @staticmethod
    def _career_record(employment: Dict[str, Any], career: str) -> Dict[str, Any]:
        records = employment.setdefault("records", {})
        record = records.setdefault(career, {})
        if not isinstance(record, dict):
            record = records[career] = {}
        for key in ("xp", "shifts", "correct", "earned"):
            record[key] = max(0, int(record.get(key, 0)))
        return record

    @staticmethod
    def _work_thresholds(cfg: Any) -> List[int]:
        raw = [
            0,
            int(cfg.get("economy.work_xp_tier_2", 15)),
            int(cfg.get("economy.work_xp_tier_3", 45)),
            int(cfg.get("economy.work_xp_tier_4", 100)),
            int(cfg.get("economy.work_xp_tier_5", 200)),
        ]
        thresholds = [0]
        for value in raw[1:]:
            thresholds.append(max(thresholds[-1] + 1, value))
        return thresholds

    def _work_level(self, cfg: Any, xp: int) -> int:
        level = 1
        for index, threshold in enumerate(self._work_thresholds(cfg), start=1):
            if int(xp) >= threshold:
                level = index
        return min(5, level)

    @staticmethod
    def _work_salary(cfg: Any, level: int) -> int:
        fallbacks = (275000, 1100000, 4400000, 13200000, 33000000)
        tier = max(1, min(5, int(level)))
        return max(1, int(cfg.get(f"economy.work_salary_tier_{tier}", fallbacks[tier - 1])))

    def _settle_android21_helper(
        self, cfg: Any, guild_id: int, user_id: int, u: Dict[str, Any], *, now: Optional[dt.datetime] = None
    ) -> Tuple[int, int, int, bool]:
        """Apply every whole automatic shift due for one helper owner.

        Returns ``(gross, garnished, shifts, changed)``. Automatic work uses 70%
        of each tier's base salary, awards one XP per shift, and deliberately
        skips decision/plushie/event/payday bonuses. The arithmetic is chunked by
        promotion tier, so even long offline periods settle in constant time.
        """
        if not u.get("android21_helper"):
            return (0, 0, 0, False)
        now = now or _now()
        employment = self._employment(u)
        active = employment.get("active")
        anchor = _parse(u.get("android21_helper_at"))
        if active not in WORK_CAREERS:
            if anchor is None or anchor > now:
                u["android21_helper_at"] = now.isoformat()
                return (0, 0, 0, True)
            return (0, 0, 0, False)
        if anchor is None or anchor > now:
            u["android21_helper_at"] = now.isoformat()
            return (0, 0, 0, True)
        interval = max(60, int(cfg.get("economy.work_cooldown_minutes", 10)) * 60)
        shifts = int((now - anchor).total_seconds() // interval)
        if shifts <= 0:
            return (0, 0, 0, False)
        record = self._career_record(employment, str(active))
        xp = int(record["xp"])
        remaining = shifts
        pay_pct = max(0, min(100, int(cfg.get("economy.android21_helper_pay_pct", 70))))
        gross = 0
        thresholds = self._work_thresholds(cfg)
        while remaining > 0:
            level = self._work_level(cfg, xp)
            if level < 5:
                until_promotion = max(1, thresholds[level] - xp)
                batch = min(remaining, until_promotion)
            else:
                batch = remaining
            per_shift = self._work_salary(cfg, level) * pay_pct // 100 + progression.helper_allowance(u)
            gross += per_shift * batch
            xp += batch
            remaining -= batch
        completed_at = anchor + dt.timedelta(seconds=shifts * interval)
        record["xp"] = xp
        record["shifts"] = int(record["shifts"]) + shifts
        record["correct"] = int(record["correct"]) + shifts
        record["earned"] = int(record["earned"]) + gross
        u["android21_helper_at"] = completed_at.isoformat()
        u["work_at"] = completed_at.isoformat()
        u["android21_helper_shifts"] = int(u.get("android21_helper_shifts", 0)) + shifts
        u["android21_helper_earned"] = int(u.get("android21_helper_earned", 0)) + gross
        u["donuts"] = int(u.get("donuts", 0)) + gross
        garnished = self._garnish(guild_id, user_id, u, gross)
        return (gross, garnished, shifts, True)

    async def _settle_and_log_android21_helper(
        self, cfg: Any, guild_id: int, user_id: int, u: Dict[str, Any]
    ) -> Tuple[int, int, int]:
        gross, garnished, shifts, changed = self._settle_android21_helper(cfg, guild_id, user_id, u)
        if changed:
            await self.persist(guild_id)
        if gross:
            await self._log(
                guild_id,
                user_id,
                gross - garnished,
                "android21-helper-work",
                u,
                detail=f"{shifts} automatic shift(s)",
            )
        return (gross, garnished, shifts)

    def _record_casino(
        self,
        cfg,
        u: Dict[str, Any],
        game: str,
        wager: int,
        net: int,
        *,
        mode: Optional[str] = None,
        jackpot: bool = False,
        guild_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        event_mult = 1.0
        if guild_id is not None:
            doc = self.econ.store.load(guild_id)
            if gevents.is_active(doc, "progressive"):
                event_mult *= 2
            if gevents.is_active(doc, "happyhour"):
                event_mult *= 1.5
            if gevents.is_active(doc, "dealerchallenge"):
                event_mult *= 1.5
        return record_casino_result(
            u,
            game,
            wager,
            net,
            mode=mode,
            jackpot=jackpot,
            tour_enabled=bool(cfg.get("economy.casino_tour_enabled", True)),
            tour_required=int(cfg.get("economy.casino_tour_games", 3)),
            tour_reward=int(cfg.get("economy.casino_tour_reward", 500000000)),
            tour_wager_pct=int(cfg.get("economy.casino_tour_wager_pct", 3)),
            tour_streak_bonus_pct=int(cfg.get("economy.casino_tour_streak_bonus_pct", 10)),
            tour_streak_cap=int(cfg.get("economy.casino_tour_streak_cap", 7)),
            reputation_multiplier=(1 + plushie_perk(u, "casino_rep") / 100) * event_mult,
        )

    def _tour_note(self, cfg, result: Dict[str, Any], subject_id: Optional[int] = None) -> str:
        reward = int(result.get("tour_reward", 0))
        if reward > 0 and subject_id is not None:
            reward = reward
        weekly = int(result.get("weekly_reputation", 0))
        notes: List[str] = []
        if reward > 0:
            notes.append(
                f"🎟️ **Casino Tour complete!** {self.money(cfg, reward)} awarded · daily streak **{int(result.get('tour_streak', 1))}**"
            )
        elif result.get("tour_required"):
            if result.get("tour_claimed"):
                notes.append("🎟️ **Daily Casino Tour:** reward already claimed. Resets at 00:00 UTC.")
            else:
                missing = ", ".join((game.title() for game in result.get("tour_missing", [])))
                notes.append(
                    f"🎟️ **Daily Casino Tour: {int(result.get('tour_progress', 0))}/{int(result['tour_required'])}** · Still needed: {missing}."
                )
        if weekly > 0:
            notes.append(f"🏆 **Weekly casino circuit complete!** +**{weekly} RP**")
        return "\n\n" + "\n".join(notes) if notes else ""

    _net = staticmethod(net_total)
    _take = staticmethod(take_wallet_first)

    @staticmethod
    def _vehicle_state(u: Dict[str, Any], model: str) -> Dict[str, Any]:
        """Return a normalized persistent state record for one vehicle model."""
        vehicles = u.setdefault("vehicles", {})
        state = vehicles.setdefault(model, {})
        defaults: Dict[str, Any] = {"owned": False, "building_until": None, "last_deploy_at": None}
        if model == "b2":
            defaults.update({"armed": False, "arming_until": None})
        if model == "b52":
            defaults.update({"b52_repair_count": 0, "b52_damaged": False, "b52_repairing_until": None})
        if model == "apache":
            defaults.update(
                {
                    "comanche_upgraded": False,
                    "comanche_upgrade_until": None,
                    "comanche_damaged": False,
                    "comanche_repair_cost": 0,
                    "comanche_repairing_until": None,
                }
            )
        if model in ARMORED_MODELS:
            damaged_key, cost_key, repairing_key = _armored_damage_fields(model)
            defaults.update(
                {
                    damaged_key: False,
                    cost_key: 0,
                    repairing_key: None,
                    "garrison_country": None,
                    "garrison_transfer_until": None,
                    "garrison_transfer_target": None,
                }
            )
        if model in LOADABLE_VEHICLES:
            defaults.update({"ammo": 0, "loading_until": None, "loading_qty": 0})
        if model in air.JETS:
            defaults.update(
                jet_damaged=False,
                jet_repair_until=None,
                patrol_until=None,
                patrol_target=None,
                second_pass=None,
            )
        for key, value in defaults.items():
            state.setdefault(key, value)
        return state

    def _vehicle_settle(self, u: Dict[str, Any], model: str) -> Dict[str, Any]:
        """Lazily finish construction and payload/ammunition integration."""
        state = self._vehicle_state(u, model)
        if model in air.JETS:
            air.settle(state)
        building = _parse(state.get("building_until"))
        if building is not None and _now() >= building:
            state["owned"] = True
            state["building_until"] = None
        if model == "b2":
            arming = _parse(state.get("arming_until"))
            if arming is not None and _now() >= arming:
                state["armed"] = True
                state["arming_until"] = None
        if model == "b52":
            repairing = _parse(state.get("b52_repairing_until"))
            if repairing is not None and _now() >= repairing:
                state["b52_damaged"] = False
                state["b52_repairing_until"] = None
        if model == "apache":
            upgrading = _parse(state.get("comanche_upgrade_until"))
            if upgrading is not None and _now() >= upgrading:
                state["comanche_upgraded"] = bool(state.get("owned"))
                state["comanche_upgrade_until"] = None
                state["comanche_damaged"] = False
                state["comanche_repair_cost"] = 0
                state["comanche_repairing_until"] = None
            repairing = _parse(state.get("comanche_repairing_until"))
            if repairing is not None and _now() >= repairing:
                state["comanche_damaged"] = False
                state["comanche_repair_cost"] = 0
                state["comanche_repairing_until"] = None
        if model in ARMORED_MODELS:
            damaged_key, cost_key, repairing_key = _armored_damage_fields(model)
            repairing = _parse(state.get(repairing_key))
            if repairing is not None and _now() >= repairing:
                state[damaged_key] = False
                state[cost_key] = 0
                state[repairing_key] = None
            transferring = _parse(state.get("garrison_transfer_until"))
            if transferring is not None and _now() >= transferring:
                target = state.get("garrison_transfer_target")
                state["garrison_country"] = str(target) if target else None
                state["garrison_transfer_until"] = None
                state["garrison_transfer_target"] = None
        if model in LOADABLE_VEHICLES:
            loading = _parse(state.get("loading_until"))
            if loading is not None and _now() >= loading:
                state["ammo"] = max(0, int(state.get("ammo", 0))) + max(0, int(state.get("loading_qty", 0)))
                state["loading_until"] = None
                state["loading_qty"] = 0
        return state

    @staticmethod
    def _vehicle_cooldown_seconds(cfg: Any, model: str, *, comanche: bool = False) -> float:
        if model == "apache" and comanche:
            return float(cfg.get("economy.vehicle_comanche_cooldown_hours", 8)) * 3600
        if model == "b2":
            return float(cfg.get("economy.vehicle_b2_cooldown_days", 1 / 24)) * 86400
        if model == "u2":
            return float(cfg.get("economy.vehicle_u2_cooldown_hours", 0.5)) * 3600
        if model == "deimos":
            return float(cfg.get("economy.vehicle_deimos_cooldown_hours", 0.75)) * 3600
        if model == "sr71":
            return float(cfg.get("economy.vehicle_sr71_cooldown_hours", 1)) * 3600
        spec = VEHICLE_CATALOG.get(model, {})
        key = spec.get("cooldown")
        return float(cfg.get(f"economy.{key}", spec.get("fallback_cooldown", 30)) if key else 0) * 60

    @staticmethod
    def _destroy_vehicle(state: Dict[str, Any]) -> None:
        if "jet_damaged" in state:
            state.update(
                jet_damaged=False,
                jet_repair_until=None,
                patrol_until=None,
                patrol_target=None,
                second_pass=None,
            )
        state["owned"] = False
        state["building_until"] = None
        state["last_deploy_at"] = None
        if "ammo" in state:
            state["ammo"] = 0
            state["loading_until"] = None
            state["loading_qty"] = 0
        if "armed" in state:
            state["armed"] = False
            state["arming_until"] = None
        if "comanche_upgraded" in state:
            state["comanche_upgraded"] = False
            state["comanche_upgrade_until"] = None
            state["comanche_damaged"] = False
            state["comanche_repair_cost"] = 0
            state["comanche_repairing_until"] = None
        if "b52_damaged" in state:
            state["b52_repair_count"] = 0
            state["b52_damaged"] = False
            state["b52_repairing_until"] = None
        if "bmd_owned" in state:
            state["bmd_owned"] = False
            state["bmd_building_until"] = None
            state["sm3_ammo"] = 0
            state["sm3_loading_until"] = None
            state["sm3_loading_qty"] = 0
        for model in ARMORED_MODELS:
            damaged_key, cost_key, repairing_key = _armored_damage_fields(model)
            if damaged_key not in state:
                continue
            state[damaged_key] = False
            state[cost_key] = 0
            state[repairing_key] = None
            state["garrison_country"] = None
            state["garrison_transfer_until"] = None
            state["garrison_transfer_target"] = None
            break

    @staticmethod
    def _vehicle_label(model: str) -> str:
        return str(VEHICLE_CATALOG.get(model, {}).get("short", model.upper()))

    @staticmethod
    def _deep_vault_total(u: Dict[str, Any]) -> int:
        return max(0, int(u.get("deep_vault_balance", 0)))

    def _deimos_defense_report(self, cfg: Any, u: Dict[str, Any]) -> str:
        """Return exact, public defensive readiness for a successful Deimos scan."""
        self._aa_settle(cfg, u)
        self._s400_settle(u)
        load_cap = int(cfg.get("economy.aa_rocket_load_cap", 5))
        if plushie_perk(u, "aa_build"):
            load_cap += 2
        loaded = max(0, int(u.get("aa_rockets_loaded", 0)))
        shield = _parse(u.get("aa_shield_until"))
        shield_text = (
            f"active until <t:{int(shield.timestamp())}:R>"
            if shield is not None and shield > _now()
            else "offline"
        )
        blackout = _electronic_blackout_until(u)
        lines = (
            []
            if blackout is None
            else [
                f"⚡ **CHAMP BLACKOUT:** conventional systems suppressed until <t:{int(blackout.timestamp())}:R>"
            ]
        )
        lines.append(f"📡 **Radar AA:** {loaded}/{load_cap} loaded · shield {shield_text}")
        s400_ready = _parse(u.get("s400_building_at"))
        if s400_ready is not None:
            s400_text = f"building · ready <t:{int(s400_ready.timestamp())}:R>"
        elif u.get("s400_owned"):
            cap = int(cfg.get("economy.s400_interceptor_cap", 2))
            ammo = max(0, int(u.get("s400_interceptors", 0)))
            s400_text = f"operational · {ammo}/{cap} loaded" if ammo else "operational · EMPTY"
        else:
            s400_text = "absent"
        lines.append(f"🛡️ **S-400:** {s400_text}")
        for model in ("aegis", "p8", "patriot"):
            spec = VEHICLE_CATALOG[model]
            state = self._vehicle_settle(u, model)
            ready = _parse(state.get("building_until"))
            if ready is not None:
                status = f"building · ready <t:{int(ready.timestamp())}:R>"
            elif not state.get("owned"):
                status = "absent"
            else:
                cap = int(cfg.get(f"economy.{spec['cap']}", int(spec["fallback_cap"])))
                ammo = max(0, int(state.get("ammo", 0)))
                status = f"operational · {ammo}/{cap} loaded" if ammo else "operational · EMPTY"
            lines.append(f"{spec['emoji']} **{spec['short']}:** {status}")
        return "\n".join(lines)

    @staticmethod
    def _continuity_exact_report(u: Dict[str, Any], cfg: Any, subject_id: Optional[int] = None) -> str:
        continuity_state.settle(u, cfg)
        state = continuity_state.normalize(u)
        if subject_id is not None:
            state = dict(state, funds=state.get("funds", 0))
        if not state.get("owned"):
            building = continuity_state.parse_time(state.get("building_until"))
            return (
                f"Building · ready <t:{int(building.timestamp())}:R>" if building else "No facility detected"
            )
        return continuity_state.exact_summary(
            state,
            names={
                "items": {key: str(spec["name"]) for key, spec in CONSUMABLES.items()},
                "plushies": {p.id: p.name for p in PLUSHIES},
                "rods": {r.id: r.name for r in RODS},
                "vehicles": {key: str(spec["short"]) for key, spec in VEHICLE_CATALOG.items()},
            },
        )

    def _sr71_defense_report(self, cfg: Any, u: Dict[str, Any]) -> Tuple[str, str]:
        """Return a complete strategic-defense snapshot using only public state.

        Readiness is derived from ordinary ownership, build and ammunition fields.
        """
        now = _now()
        self._aa_settle(cfg, u)
        self._s400_settle(u)
        thor_state.settle(u, cfg)
        continuity_state.settle(u, cfg)
        load_cap = int(cfg.get("economy.aa_rocket_load_cap", 5))
        if plushie_perk(u, "aa_build"):
            load_cap += 2
        stock_cap = int(cfg.get("economy.aa_rocket_stock_cap", 10))
        loaded = max(0, int(u.get("aa_rockets_loaded", 0)))
        stock = max(0, int(u.get("aa_rockets_stock", 0)))
        radar_state = "🟢 ACTIVE" if loaded else "🟠 INACTIVE — EMPTY"
        radar = f"📡 **Radar AA — {radar_state}**\n{loaded}/{load_cap} loaded · {stock}/{stock_cap} reserve"
        batch_ready = _parse(u.get("aa_rocket_build_at"))
        if batch_ready is not None:
            radar += f" · {max(0, int(u.get('aa_rocket_build_qty', 0)))} building <t:{int(batch_ready.timestamp())}:R>"
        shield = _parse(u.get("aa_shield_until"))
        shield_active = shield is not None and shield > now
        shield_line = (
            f"🛡️ **AA Shield — 🟢 ACTIVE** until <t:{int(shield.timestamp())}:R>"
            if shield_active
            else "🛡️ **AA Shield — ⚫ OFFLINE**"
        )
        s400_ready = _parse(u.get("s400_building_at"))
        if s400_ready is not None:
            s400 = f"☂️ **S-400 — 🏭 BUILDING** · ready <t:{int(s400_ready.timestamp())}:R>"
        elif not u.get("s400_owned"):
            s400 = "☂️ **S-400 — ⚫ ABSENT**"
        else:
            s400_cap = int(cfg.get("economy.s400_interceptor_cap", 2))
            s400_ammo = max(0, int(u.get("s400_interceptors", 0)))
            state = "🟢 ACTIVE" if s400_ammo else "🟠 INACTIVE — EMPTY"
            s400 = f"☂️ **S-400 — {state}**\n{s400_ammo}/{s400_cap} 40N6E loaded"

        def vehicle_line(model: str) -> str:
            spec = VEHICLE_CATALOG[model]
            state = self._vehicle_settle(u, model)
            building = _parse(state.get("building_until"))
            if building is not None:
                return f"{spec['emoji']} **{spec['short']} — 🏭 BUILDING** · ready <t:{int(building.timestamp())}:R>"
            if not state.get("owned"):
                return f"{spec['emoji']} **{spec['short']} — ⚫ ABSENT**"
            cap = int(cfg.get(f"economy.{spec['cap']}", int(spec["fallback_cap"])))
            ammo = max(0, int(state.get("ammo", 0)))
            readiness = "🟢 ACTIVE" if ammo else "🟠 INACTIVE — EMPTY"
            line = f"{spec['emoji']} **{spec['short']} — {readiness}**\n{ammo}/{cap} {spec['ammo']} loaded"
            loading = _parse(state.get("loading_until"))
            if loading is not None:
                line += (
                    f" · {max(0, int(state.get('loading_qty', 0)))} loading <t:{int(loading.timestamp())}:R>"
                )
            return line

        aegis = self._vehicle_settle(u, "aegis")
        thor_state.settle_aegis_bmd(aegis, cfg)
        bmd_refit = _parse(aegis.get("bmd_building_until"))
        if bmd_refit is not None:
            bmd = f"🌐 **Aegis BMD — 🔧 REFITTING** · ready <t:{int(bmd_refit.timestamp())}:R>"
        elif not aegis.get("owned") or not aegis.get("bmd_owned"):
            bmd = "🌐 **Aegis BMD — ⚫ ABSENT**"
        else:
            sm3_cap = int(cfg.get("economy.thor_sm3_capacity", 3))
            sm3 = max(0, int(aegis.get("sm3_ammo", 0)))
            readiness = "🟢 ACTIVE" if sm3 else "🟠 INACTIVE — EMPTY"
            bmd = f"🌐 **Aegis BMD — {readiness}**\n{sm3}/{sm3_cap} SM-3 Block IIA loaded"
            sm3_loading = _parse(aegis.get("sm3_loading_until"))
            if sm3_loading is not None:
                bmd += f" · {max(0, int(aegis.get('sm3_loading_qty', 0)))} loading <t:{int(sm3_loading.timestamp())}:R>"
        thor = thor_state.normalize(u)
        gbi_cap = int(cfg.get("economy.thor_gbi_stock_capacity", 2))
        gbi_stock = max(0, int(thor.get("gbi_stock", 0)))
        readiness = "🟢 ACTIVE" if gbi_stock else "🟠 INACTIVE — EMPTY"
        gbi = f"🚀 **GBI/EKV — {readiness}**\n{gbi_stock}/{gbi_cap} ready"
        gbi_building = thor_state.parse_time(thor.get("gbi_building_until"))
        if gbi_building is not None:
            gbi += f" · {max(0, int(thor.get('gbi_building_qty', 0)))} building <t:{int(gbi_building.timestamp())}:R>"
        blackout = _electronic_blackout_until(u)
        blackout_line = (
            f"⚡ **CHAMP BLACKOUT — ACTIVE** until <t:{int(blackout.timestamp())}:R>\nConventional systems below cannot engage. THOR counters remain online."
            if blackout is not None
            else ""
        )
        air_defense = "\n\n".join((line for line in (blackout_line, radar, shield_line, s400) if line))
        continuity = continuity_state.normalize(u)
        if continuity.get("owned"):
            loadout = "protected loadout present" if continuity_state.has_loadout(continuity) else "empty"
            continuity_line = f"🏔️ **Raven Rock — 🟢 OPERATIONAL**\n{loadout}; exact contents classified"
        else:
            building = continuity_state.parse_time(continuity.get("building_until"))
            continuity_line = (
                f"🏔️ **Raven Rock — 🏗️ BUILDING** · ready <t:{int(building.timestamp())}:R>"
                if building
                else "🏔️ **Raven Rock — ⚫ ABSENT**"
            )
        dedicated = "\n\n".join(
            (
                vehicle_line("aegis"),
                vehicle_line("p8"),
                vehicle_line("patriot"),
                vehicle_line("javelin"),
                bmd,
                gbi,
                continuity_line,
            )
        )
        return (air_defense, dedicated)

    @staticmethod
    def _arsenal_preparation_jobs(u: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """Neutral progress for the ephemeral self-only arsenal, never recon.

        These are existing queued jobs, not promises of future replenishment.
        Reading progress must never reschedule a job or change ammunition.
        """
        jobs = {}

        def add(label, raw, qty, *, kind="loading"):
            due = _parse(raw)
            if due is not None and int(qty or 0) > 0:
                jobs[label] = {"due": due, "qty": int(qty), "kind": kind}

        add("Radar AA", u.get("aa_rocket_build_at"), u.get("aa_rocket_build_qty"), kind="reserve")
        vehicles = u.get("vehicles") or {}
        for model, spec in VEHICLE_CATALOG.items():
            state = vehicles.get(model) or {}
            if not state.get("owned") or state.get("building_until"):
                continue
            if _parse(state.get("loading_until")) is not None:
                add(spec["short"], state.get("loading_until"), state.get("loading_qty"))
        aegis = vehicles.get("aegis") or {}
        if aegis.get("owned") and aegis.get("bmd_owned"):
            add("Aegis BMD", aegis.get("sm3_loading_until"), aegis.get("sm3_loading_qty"))
        thor = u.get("thor") or {}
        add("GBI/EKV", thor.get("gbi_building_until"), thor.get("gbi_building_qty"), kind="building")
        isd = u.get("imperial_star_destroyer") or {}
        add("B-wing interception packages", isd.get("counter_building_until"), 1, kind="building")
        station = u.get("death_star") or {}
        add("Alliance Assault Squadrons", station.get("squadron_until"), 1, kind="building")
        return jobs

    @staticmethod
    def _arsenal_defense_sections(report: str, jobs, *, aa_loaded: int, aa_stock: int) -> str:
        """Style self-inspection only; shared public snapshots stay unchanged."""
        blocks = []
        for block in report.split("\n\n"):
            rows = block.splitlines()
            if not rows:
                continue
            first = rows[0]
            label = ""
            if first.count("**") >= 2:
                prefix, heading, suffix = first.split("**", 2)
                label, separator, readiness = heading.partition(" — ")
                if separator:
                    job = jobs.get(label)
                    if job and "INACTIVE — EMPTY" in readiness:
                        readiness = (
                            "🟠 EMPTY — STOCK AVAILABLE TO LOAD"
                            if job["kind"] == "reserve" and aa_stock
                            else "🟠 EMPTY — RESERVE PRODUCTION"
                            if job["kind"] == "reserve"
                            else "🟠 RELOADING — NOT READY"
                        )
                    elif job and "ACTIVE" in readiness:
                        readiness += " · AMMUNITION PREPARING"
                    elif label == "Radar AA" and (not aa_loaded) and aa_stock:
                        readiness = "🟠 EMPTY — STOCK AVAILABLE TO LOAD"
                    first = f"{prefix}**{label}**\n{status_ui.line('Status', readiness + suffix)}"
            value = "\n".join([first] + [status_ui.line("Details", row) for row in rows[1:]])
            job = jobs.get(label)
            if job:
                label_text = "Reserve batch" if job["kind"] == "reserve" else "Next batch"
                value += "\n" + status_ui.line(
                    label_text, f"×{job['qty']} · completes {status_ui.deadline(job['due'])}"
                )
            if label == "Radar AA" and (not aa_loaded):
                action = (
                    "Use `/aa load` to arm the battery."
                    if aa_stock
                    else "Wait for the reserve batch, then use `/aa load`."
                    if job
                    else "Build rockets with `/aa build`, then use `/aa load`."
                )
                value += "\n" + status_ui.line("Next action", "Cold stock cannot defend. " + action)
            blocks.append(value)
        return "\n\n".join(blocks)

    @staticmethod
    def _parse_all_amount(raw: Optional[str], available: int) -> Optional[int]:
        """Resolve flexible shorthand through the shared amount parser."""
        try:
            return parse_amount(raw, available=available, default_all=True)
        except AmountParseError:
            return None

    def _deep_vault_settle(self, u: Dict[str, Any]) -> int:
        """Credit a matured withdrawal to the wallet. Returns the amount moved."""
        amount = max(0, int(u.get("deep_vault_withdraw_amount", 0)))
        ready = _parse(u.get("deep_vault_withdraw_at"))
        if amount <= 0 or ready is None or _now() < ready:
            return 0
        amount = min(amount, self._deep_vault_total(u))
        u["deep_vault_balance"] = self._deep_vault_total(u) - amount
        u["donuts"] = int(u.get("donuts", 0)) + amount
        u["deep_vault_withdraw_amount"] = 0
        u["deep_vault_withdraw_at"] = None
        return amount

    @staticmethod
    def _s400_settle(u: Dict[str, Any]) -> None:
        ready = _parse(u.get("s400_building_at"))
        if ready is not None and _now() >= ready:
            u["s400_owned"] = True
            u["s400_building_at"] = None

    @staticmethod
    def _active_recon(
        attacker: Dict[str, Any], target_id: int, target_state: Optional[Dict[str, Any]] = None
    ) -> bool:
        marks = attacker.setdefault("recon_targets", {})
        key = str(int(target_id))
        record = attacker.get("sr71_construction_intel", {}).get(key) or {}
        if target_state is not None and nyx.active(target_state):
            marks.pop(key, None)
            attacker.setdefault("sr71_construction_intel", {}).pop(key, None)
            return False
        expiry = _parse(marks.get(key))
        if expiry is None:
            legacy = attacker.setdefault("sr71_construction_intel", {}).get(key)
            if isinstance(legacy, dict):
                legacy_expiry = _parse(legacy.get("expires_at"))
                if legacy_expiry is not None and legacy_expiry > _now():
                    marks[key] = legacy_expiry.isoformat()
                    expiry = legacy_expiry
        if expiry is None or expiry <= _now():
            marks.pop(key, None)
            return False
        return True

    def _active_construction_intel(
        self, attacker: Dict[str, Any], target_id: int, target_state: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """Return construction tracks from any live strategic recon package."""
        records = attacker.setdefault("sr71_construction_intel", {})
        key = str(int(target_id))
        record = records.get(key)
        if target_state is not None and nyx.active(target_state):
            records.pop(key, None)
            attacker.setdefault("recon_targets", {}).pop(key, None)
            return None
        record = records.get(key)
        if not isinstance(record, dict) and target_state is not None:
            expiry = _parse(attacker.setdefault("recon_targets", {}).get(key))
            if expiry is not None and expiry > _now():
                self._grant_recon_package(attacker, target_id, target_state, expiry)
                record = records.get(key)
        if not isinstance(record, dict):
            records.pop(key, None)
            return None
        expiry = _parse(record.get("expires_at"))
        if expiry is None or expiry <= _now():
            records.pop(key, None)
            return None
        builds = record.get("builds")
        if not isinstance(builds, dict):
            record["builds"] = {}
        return record

    def _grant_recon_package(
        self,
        observer: Dict[str, Any],
        target_id: int,
        target_state: Dict[str, Any],
        expiry: dt.datetime,
        *,
        observed_at: Optional[dt.datetime] = None,
        source: Optional[str] = None,
    ) -> List[str]:
        """Grant every targeting benefit shared by U-2, Deimos and SR-71.

        The ordinary target marker authorizes located-vault attacks and filters
        completed-vehicle objectives. A parallel immutable construction snapshot
        guides Dark Eagle without exposing live hangar state through autocomplete.
        """
        observed_at = observed_at or _now()
        if nyx.active(target_state, observed_at):
            observer.setdefault("recon_targets", {}).pop(str(int(target_id)), None)
            observer.setdefault("sr71_construction_intel", {}).pop(str(int(target_id)), None)
            return []
        key = str(int(target_id))
        old = observer.setdefault("sr71_construction_intel", {}).get(key) or {}
        sources = copy.deepcopy(old.get("sources") or {})
        if old.get("source") and old.get("expires_at"):
            sources.setdefault(
                old["source"], {"expires_at": old["expires_at"], "builds": old.get("builds", {})}
            )
        previous = _parse(observer.setdefault("recon_targets", {}).get(key))
        observer["recon_targets"][key] = max(expiry, previous or expiry).isoformat()
        builds: Dict[str, str] = {}
        lines: List[str] = []
        for model in DEPLOYABLE_VEHICLES:
            if model == "lrhw":
                continue
            state = self._vehicle_settle(target_state, model)
            building = _parse(state.get("building_until"))
            if building is None or building <= observed_at:
                continue
            builds[model] = building.isoformat()
            lines.append(f"🏗️ **{self._vehicle_label(model)}** · ready <t:{int(building.timestamp())}:R>")
        if source:
            sources[source] = {"expires_at": expiry.isoformat(), "builds": builds}
        observer.setdefault("sr71_construction_intel", {})[str(int(target_id))] = {
            "expires_at": expiry.isoformat(),
            "builds": builds,
            "source": source,
            "sources": sources,
        }
        return lines

    def _mission_preflight(
        self,
        guild_id: Optional[int],
        pilot_id: int,
        model: str,
        target: discord.Member,
        objective: Optional[str] = None,
        secondary_objective: Optional[str] = None,
    ) -> Optional[str]:
        """Revalidate a vehicle mission immediately before its confirmation commits."""
        if guild_id is None:
            return "Strategic missions only work in a server."
        if target.id == pilot_id:
            return "You can't deploy a strategic aircraft against yourself."
        if target.bot:
            return "Bots don't have strategic holdings worth a sortie."
        treaty = self._coalition_attack_error(guild_id, pilot_id, target.id)
        if treaty:
            return treaty
        cfg = self.cfg(guild_id)
        pilot = self.user(guild_id, pilot_id)
        lockdown = _strategic_lockdown_until(pilot)
        if lockdown is not None:
            return f"Your strategic runway and operations hub are disabled by a B-52 strike until <t:{int(lockdown.timestamp())}:R>. You may build and load weapons, but cannot launch attacks."
        blackout = _electronic_blackout_until(pilot)
        if blackout is not None and model in {"u2", "deimos", "sr71"}:
            return f"Your intelligence electronics are suppressed by a CHAMP strike until <t:{int(blackout.timestamp())}:R>."
        state = self._vehicle_settle(pilot, model)
        label = self._vehicle_label(model)
        if not state.get("owned"):
            building = _parse(state.get("building_until"))
            if building is not None:
                return f"Your {label} is still under construction until <t:{int(building.timestamp())}:R>."
            return f"You don't own a {label}."
        if model == "apache":
            upgrading = _parse(state.get("comanche_upgrade_until"))
            if upgrading is not None:
                return f"Your Apache is unavailable while its RAH-66 Comanche conversion finishes <t:{int(upgrading.timestamp())}:R>."
        is_comanche = model == "apache" and bool(state.get("comanche_upgraded"))
        if model == "b52" and state.get("b52_damaged"):
            repairing = _parse(state.get("b52_repairing_until"))
            if repairing is not None:
                return f"Your B-52H is undergoing depot repair until <t:{int(repairing.timestamp())}:R>."
            return "Your B-52H is grounded with battle damage. Start its next phase with `/vehicle repair`."
        if is_comanche and state.get("comanche_damaged"):
            repairing = _parse(state.get("comanche_repairing_until"))
            if repairing is not None:
                return f"Your RAH-66 Comanche is undergoing depot repair until <t:{int(repairing.timestamp())}:R>."
            quote = max(0, int(state.get("comanche_repair_cost", 0)))
            return (
                f"Your RAH-66 Comanche is grounded with battle damage. Repair it with `/vehicle repair`"
                + (f" for {self.money(cfg, quote)}." if quote else ".")
            )
        if model in ARMORED_MODELS:
            damaged_key, cost_key, repairing_key = _armored_damage_fields(model)
            label = self._vehicle_label(model)
            repairing = _parse(state.get(repairing_key))
            if repairing is not None:
                return f"Your {label} is undergoing depot repair until <t:{int(repairing.timestamp())}:R>."
            if state.get(damaged_key):
                quote = max(0, int(state.get(cost_key, 0)))
                return f"Your {label} is grounded with battle damage. Repair it with `/vehicle repair`" + (
                    f" for {self.money(cfg, quote)}." if quote else "."
                )
            transfer = _parse(state.get("garrison_transfer_until"))
            if transfer is not None:
                return f"Your {label} is redeploying until <t:{int(transfer.timestamp())}:R>."
            if state.get("garrison_country"):
                return f"Your {label} is assigned to a country garrison. Use `/vehicle withdraw` first."
        secondary_objective = (secondary_objective or "").strip().lower() or None
        if secondary_objective and (not (is_comanche or model == "mq9")):
            return "A secondary target is available only to an MQ-9B."
        last = _parse(state.get("last_deploy_at"))
        cooldown = self._vehicle_cooldown_seconds(cfg, model, comanche=is_comanche)
        if model in {"zumwalt", "virginia"}:
            cooldown *= 1 - plushie_perk(pilot, "naval_cooldown") / 100
        if last is not None and (_now() - last).total_seconds() < cooldown:
            return f"The {label} is in turnaround until <t:{int(last.timestamp() + cooldown)}:R>."
        if model == "b2":
            arming = _parse(state.get("arming_until"))
            if arming is not None:
                return f"The B61-12 payload is still being integrated until <t:{int(arming.timestamp())}:R>."
            if not state.get("armed"):
                return "The B-2 has no B61-12 payload. Arm it with `/vehicle arm`."
            victim = self.user(guild_id, target.id)
            has_intel = self._active_recon(pilot, target.id, victim)
            eligible = self._net(victim) + (self._deep_vault_total(victim) if has_intel else 0)
            floor = int(cfg.get("economy.b2_min_target_value", 210000000))
            if eligible < floor:
                return f"Target value is below the **{ui.format_donuts(floor)}** minimum using the intelligence currently available to you."
            return None
        if model in {"u2", "deimos", "sr71"}:
            return None
        loading = _parse(state.get("loading_until"))
        if loading is not None:
            return f"The {label}'s ammunition finishes loading <t:{int(loading.timestamp())}:R>."
        spec = VEHICLE_CATALOG.get(model, {})
        required_rounds = _comanche_rounds_per_mission(cfg) if is_comanche else 1
        if int(state.get("ammo", 0)) < required_rounds:
            if is_comanche:
                return f"A Comanche all-store raid requires {required_rounds} loaded AGM-179A JAGMs. Use `/vehicle load`."
            return f"The {label} has no {spec.get('ammo', 'ammunition')} loaded. Use `/vehicle load`."
        victim = self.user(guild_id, target.id)
        objective = (objective or "").strip().lower()
        if model == "zumwalt":
            eligible = self._net(victim)
            if self._active_recon(pilot, target.id, victim):
                eligible += self._deep_vault_total(victim)
            if eligible <= 0:
                return "That target has no donut reserves within the current targeting solution."
        elif model == "virginia":
            fish = victim.get("fish", {}) or {}
            if not any((int(n or 0) > 0 for n in fish.values())) and (not victim.get("equipped_rod")):
                return "That target has no fish or equipped rod for the submarine to attack."
        elif model == "himars":
            if objective not in {"regular-aa", "s400"}:
                return "HIMARS requires objective `regular-aa` or `s400`."
            self._aa_settle(cfg, victim)
            self._s400_settle(victim)
            if objective == "regular-aa" and int(victim.get("aa_rockets_loaded", 0)) <= 0:
                return "That target has no loaded regular AA rockets."
            if objective == "s400" and int(victim.get("s400_interceptors", 0)) <= 0:
                return "That target has no loaded S-400 interceptor."
        elif model == "apache":
            if is_comanche:
                pass
            elif objective not in {"regular-aa", "s400", "patriot", "himars"}:
                return "Apache requires objective `regular-aa`, `s400`, `patriot`, or `himars`."
        elif model == "mq9":
            inventory = victim.get("inventory", {}) or {}
            if objective not in CONSUMABLES:
                return "MQ-9B requires a consumable from the `objective` autocomplete list."
            if secondary_objective is not None and secondary_objective not in CONSUMABLES:
                return "That secondary MQ-9B consumable target is invalid."
            if secondary_objective == objective:
                return "Choose two different consumable types for an MQ-9B split strike."
            required = 2 if secondary_objective else 1
            if int(state.get("ammo", 0)) < required:
                return f"An MQ-9B split strike requires {required} loaded AGM-114R Hellfires."
            for item in [objective, secondary_objective]:
                if item is not None and int(inventory.get(item, 0)) <= 0:
                    return f"That target has no `{item}` consumables to strike."
        elif model == "f15e" and self._net(victim) <= 0:
            return "That target has no visible wallet or bank reserves to strike."
        elif model in {"a10", "su34"}:
            if objective == "no-completed-vehicles":
                return "Your target package found no completed conventional vehicles to select. No ammunition was spent and no mission cooldown was started."
            if objective not in DEPLOYABLE_VEHICLES:
                return f"{self._vehicle_label(model)} requires a conventional vehicle model from the objective autocomplete list."
        elif model == "c130j":
            if objective not in (None, "", "distributed"):
                if not self._active_recon(pilot, target.id, victim):
                    return "Concentrated Rapid Dragon strikes require a current U-2, Deimos or SR-71 reconnaissance package."
                if not objective.startswith("focused:"):
                    return "Choose `distributed` or a concentrated depot from the objective list."
            stores = self._conventional_ammo_stores(cfg, victim)
            if objective and objective.startswith("focused:"):
                key = objective.split(":", 1)[1]
                if not any((store == key and amount > 0 for store, _, _, amount in stores)):
                    return (
                        "That recon-selected depot has no ready conventional ammunition. No pallet was spent."
                    )
            if not any((amount > 0 for _, _, _, amount in stores)):
                return "That target has no ready conventional ammunition for Rapid Dragon to strike."
        elif model == "maldx":
            valid = {"regular-aa", "s400", "aegis", "p8", "patriot", "javelin"}
            if objective not in valid:
                return (
                    "MALD-X requires objective `regular-aa`, `s400`, `aegis`, `p8`, `patriot`, or `javelin`."
                )
            stores = {key: amount for key, _, _, amount in self._conventional_ammo_stores(cfg, victim)}
            if stores.get(objective, 0) <= 0:
                return "That target has no ready interceptor in the selected defensive store."
        elif model == "lrhw":
            if objective not in DEPLOYABLE_VEHICLES or objective == "lrhw":
                return (
                    "Dark Eagle requires a conventional vehicle model from the objective autocomplete list."
                )
            intel = self._active_construction_intel(pilot, target.id, victim)
            if intel is not None:
                if objective not in intel.get("builds", {}):
                    return "That build was not identified by your active reconnaissance package. Choose one of its tracked objectives."
                target_state = self._vehicle_state(victim, objective)
                building = _parse(target_state.get("building_until"))
                observed = _parse(intel.get("builds", {}).get(objective))
                if building is None or building <= _now() or observed is None or (building != observed):
                    return "That reconnaissance construction track is stale because the build is no longer active. Run another reconnaissance pass before committing a guided shot."
        elif model in ARMORED_MODELS:
            country_id = objective
            territory = country_state.state(self.econ.store.load(guild_id))["territories"].get(country_id)
            country = country_state.BY_ID.get(country_id)
            if country is None:
                return "Choose one of the target player's claimed countries from the objective list."
            if not isinstance(territory, dict):
                return f"The {self._vehicle_label(model)} cannot breach an unclaimed country."
            if int(territory.get("owner", 0)) != int(target.id):
                return "That country is no longer ruled by the selected target."
            last_breach = country_state.parse_time(territory.get("m1a2_last_breached_at"))
            breach_cd = max(1, int(cfg.get(f"economy.vehicle_{model}_country_breach_cooldown_minutes", 60)))
            if (
                last_breach is not None
                and (country_state.now() - last_breach).total_seconds() < breach_cd * 60
            ):
                ready = last_breach + dt.timedelta(minutes=breach_cd)
                return f"That country cannot be breached again until <t:{int(ready.timestamp())}:R>."
        elif model == "xb70" and self._net(victim) <= 0:
            return "That target has no visible wallet or bank reserves to strike."
        return None

    def _conventional_ammo_stores(
        self, cfg: Any, victim: Dict[str, Any]
    ) -> List[Tuple[str, Dict[str, Any], str, int]]:
        """Return mutable conventional ammo stores; never includes Project THOR."""
        self._aa_settle(cfg, victim)
        self._s400_settle(victim)
        stores: List[Tuple[str, Dict[str, Any], str, int]] = [
            ("regular-aa", victim, "aa_rockets_loaded", max(0, int(victim.get("aa_rockets_loaded", 0)))),
            ("s400", victim, "s400_interceptors", max(0, int(victim.get("s400_interceptors", 0)))),
        ]
        for ammo_model in LOADABLE_VEHICLES:
            state = self._vehicle_settle(victim, ammo_model)
            stores.append((ammo_model, state, "ammo", max(0, int(state.get("ammo", 0)))))
        return stores

    def _offensive_ammo_stores(self, victim: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any], str, int]]:
        """Return loaded conventional offensive stores for ground-attack raids.

        Defensive magazines and every Project THOR field are deliberately absent.
        The B-2's nuclear payload and completed ICBMs are also strategic rather
        than conventional stores and remain outside these battlefield sweeps.
        """
        stores: List[Tuple[str, Dict[str, Any], str, int]] = []
        for ammo_model in LOADABLE_VEHICLES:
            spec = VEHICLE_CATALOG[ammo_model]
            if spec.get("role") != "offense":
                continue
            state = self._vehicle_settle(victim, ammo_model)
            stores.append((ammo_model, state, "ammo", max(0, int(state.get("ammo", 0)))))
        return stores

    def _comanche_ammunition_damage(self, cfg: Any, victim: Dict[str, Any]) -> Tuple[int, List[str]]:
        """Sweep conventional ready ammo while preserving all THOR assets."""
        pct = max(1, min(100, int(cfg.get("economy.vehicle_comanche_precision_damage_pct", 100))))
        total = 0
        lines: List[str] = []
        empty_categories = 0

        def hit(container: Dict[str, Any], key: str, label: str) -> None:
            nonlocal total
            before = max(0, int(container.get(key, 0) or 0))
            if before <= 0:
                return
            lost = _mq9_items_destroyed(before, pct)
            container[key] = before - lost
            total += lost
            lines.append(f"• {label}: **{lost:,} destroyed**")

        for objective in COMANCHE_AMMO_TARGETS:
            before_lines = len(lines)
            if objective == "icbm":
                self._icbm_settle(victim)
                hit(victim, "icbm_ready", "completed ICBMs")
            elif objective == "b61":
                b2 = self._vehicle_settle(victim, "b2")
                before = 1 if b2.get("armed") else 0
                lost = _mq9_items_destroyed(before, pct)
                if lost:
                    b2["armed"] = False
                    total += lost
                    lines.append("• B-2 B61-12 payload: **1 destroyed**")
            elif objective == "radar-aa":
                self._aa_settle(cfg, victim)
                hit(victim, "aa_rockets_loaded", "loaded Radar AA")
                hit(victim, "aa_rockets_stock", "reserve Radar AA")
            elif objective == "s400":
                self._s400_settle(victim)
                hit(victim, "s400_interceptors", "S-400 40N6E")
            else:
                state = self._vehicle_settle(victim, objective)
                hit(state, "ammo", str(VEHICLE_CATALOG[objective]["ammo"]))
            if len(lines) == before_lines:
                empty_categories += 1
        if not lines:
            lines.append("• No ready ammunition was found in any eligible store")
        if empty_categories:
            lines.append(f"• Empty categories skipped: **{empty_categories}**")
        return (total, [f"**All-store raid — {pct}% ammunition suppression:**", *lines])

    def _uno_reverse(self, cfg, thief: Dict[str, Any], victim: Dict[str, Any], attempted: int) -> int:
        """A triggered Uno pays the victim a % of what the thief tried to take,
        drawn from the thief's wallet then bank, capped and bounded by what they
        actually have. Returns the amount moved (a pure transfer — never minted)."""
        pct = int(cfg.get("economy.uno_reverse_percent", 75))
        amount = progression.reversal(
            thief, attempted, pct, int(cfg.get("economy.uno_reverse_wealth_cap_percent", 10))
        )
        if amount <= 0:
            return 0
        self._take(thief, amount)
        victim["donuts"] = int(victim.get("donuts", 0)) + amount
        return amount

    def _apply_jail(self, cfg, u: Dict[str, Any]) -> int:
        """Stamp (or extend) a jail sentence on `u`, shortened by their Houdini 21
        (jail perk). Stacks onto any time already being served. Returns until-ts."""
        minutes = int(cfg.get("economy.jail_minutes", 60))
        minutes = max(1, minutes - minutes * plushie_perk(u, "jail") // 100)
        cur = _parse(u.get("jailed_until"))
        base = cur if cur and cur > _now() else _now()
        u["jailed_until"] = (base + dt.timedelta(minutes=minutes)).isoformat()
        return int(_parse(u["jailed_until"]).timestamp())

    def _hold_cap(self, cfg, item: str) -> Optional[int]:
        """Max holdable count for a capped consumable, or None if it's uncapped."""
        return asset_service.hold_cap(cfg, item)

    def _bounty_hit(self, guild_id: int, thief: Dict[str, Any], victim_id: int) -> int:
        """If a Bounty event is out on `victim_id`, pay the thief the bonus and clear
        the bounty. Returns the bonus paid (0 if there was none)."""
        doc = self.econ.store.load(guild_id)
        b = gevents.bounty(doc)
        if not b or str(b.get("target")) != str(victim_id):
            return 0
        bonus = int(b.get("bonus", 0))
        thief["donuts"] = int(thief.get("donuts", 0)) + bonus
        gevents.clear_bounty(doc)
        return bonus

    def _overdue_debt(self, guild_id: int, borrower_id: int) -> int:
        """Total still owed on the borrower's past-due loans (for the public flag)."""
        now = _now()
        return sum(
            (
                int(l.get("owed", 0))
                for l in self.econ.store.load(guild_id).get("loans", [])
                if l.get("borrower") == borrower_id
                and int(l.get("owed", 0)) > 0
                and (_parse(l.get("due_at")) is not None and _parse(l["due_at"]) < now)
            )
        )

    def _garnish(self, guild_id: int, borrower_id: int, borrower: Dict[str, Any], gross: int) -> int:
        """Skim a % of fresh /daily or /work income to the borrower's overdue lenders.
        Pure transfer (borrower → lenders), capped at what's owed. Returns the amount taken."""
        pct = int(self.cfg(guild_id).get("economy.garnish_percent", 25))
        if pct <= 0 or gross <= 0:
            return 0
        now = _now()
        loans = self.econ.store.load(guild_id).get("loans", [])
        overdue = [
            l
            for l in loans
            if l.get("borrower") == borrower_id
            and int(l.get("owed", 0)) > 0
            and (_parse(l.get("due_at")) is not None and _parse(l["due_at"]) < now)
        ]
        if not overdue:
            return 0
        cut = min(gross * pct // 100, sum((int(l["owed"]) for l in overdue)))
        if cut <= 0:
            return 0
        borrower["donuts"] = int(borrower.get("donuts", 0)) - cut
        remaining = cut
        overdue.sort(key=lambda l: l.get("id", 0))
        for loan in overdue:
            if remaining <= 0:
                break
            take = min(remaining, int(loan["owed"]))
            loan["owed"] = int(loan["owed"]) - take
            lender = self.user(guild_id, loan["lender"])
            lender["donuts"] = int(lender.get("donuts", 0)) + take
            remaining -= take
        loans[:] = [l for l in loans if int(l.get("owed", 0)) > 0]
        return cut

    async def _log(
        self, guild_id, user_id, delta, reason, u, *, after_override: Optional[int] = None, **kw
    ) -> None:
        """Record a net-worth change to the audit ledger (no-op if unchanged)."""
        if delta == 0:
            return
        await self.bot.ledger.record(
            guild_id,
            user_id,
            delta,
            reason,
            after=self._net(u) if after_override is None else int(after_override),
            **kw,
        )

    async def _award(
        self,
        interaction: discord.Interaction,
        member: discord.abc.User,
        user: Dict[str, Any],
        *event_ids: str,
    ) -> None:
        """Grant event + wealth badges and announce publicly (config-gated)."""
        cfg = self.cfg(interaction.guild_id)
        if not cfg.get("achievements.enabled", True):
            return
        channel = interaction.channel if cfg.get("achievements.announce", True) else None
        newly = await badges.award(channel, member, user, cfg.color, *event_ids)
        if newly:
            await self.persist(interaction.guild_id)

    async def _guard(
        self,
        interaction: discord.Interaction,
        *,
        needs_channel: bool = True,
        game: Optional[str] = None,
        ephemeral: bool = False,
    ) -> Optional[Any]:
        if interaction.guild_id is None:
            await ui.respond(
                interaction, embed=ui.error_embed("The economy only works in a server."), ephemeral=True
            )
            return None
        cfg = self.cfg(interaction.guild_id)
        if not cfg.get("economy.enabled", True):
            await ui.respond(
                interaction, embed=ui.error_embed("The economy is turned off here."), ephemeral=True
            )
            return None
        if game and gamelocks.is_locked(game):
            await ui.respond(
                interaction,
                embed=ui.warn_embed(f"🔒 **{game.title()}** is locked down right now — check back later."),
                ephemeral=True,
            )
            return None
        if needs_channel:
            chan = cfg.get("economy.channel")
            if chan and interaction.channel_id != int(chan):
                await ui.respond(
                    interaction, embed=ui.error_embed(f"Play in <#{int(chan)}>."), ephemeral=True
                )
                return None
        await ui.defer_response(interaction, ephemeral=ephemeral)
        return cfg

    async def _resolve_target(
        self, interaction: discord.Interaction, member: Optional[discord.Member], user_id: Optional[str]
    ):
        if member is not None:
            return (member, None)
        if user_id and str(user_id).strip().isdigit() and (interaction.guild is not None):
            member = interaction.guild.get_member(int(str(user_id).strip()))
            if member is not None:
                return (member, None)
        return (None, "Choose a member of this server, or enter their user ID.")

    @app_commands.command(name="casino", description="Android 21 teaches you how to play.")
    async def casino(self, interaction: discord.Interaction) -> None:
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        cur = self.name(cfg)
        e = self.emoji(cfg)
        g = cfg.get
        embed = ui.base_embed(
            title=f"🍩 Welcome to Szofie's, little morsel",
            description=f"*Ahh, fresh meat. Come in, come in — sit while I explain the rules, before I decide whether you're a player or... dessert.* Everything here runs on **{cur}**, my favourite treat. You start with **{ui.format_donuts(int(g('economy.starting_balance', 100)))}** of them. Spend them, gamble them, steal them — just don't run out.",
            color=cfg.color,
        )
        embed.add_field(
            name="💼 Careers — `/jobs`, `/work`",
            value=f"Claim **{ui.format_donuts(int(g('economy.daily_amount', 5500000)))}** {cur} free every **{int(g('economy.daily_cooldown_hours', 12))}h** with `/daily`. For real income, browse four careers with `/jobs`, join one through `/job apply`, and complete a short decision-based `/work` shift every **{int(g('economy.work_cooldown_minutes', 10))} min**. Base salaries rise from **{ui.format_donuts(int(g('economy.work_salary_tier_1', 275000)))}** to **{ui.format_donuts(int(g('economy.work_salary_tier_5', 33000000)))}** {cur}; good decisions, promotions and the daily payday increase that further.",
            inline=False,
        )
        embed.add_field(
            name="🎣 Fishing — `/fish`",
            value=f"Cast a line at a base cooldown of **{int(g('economy.fish_cooldown_seconds', 300))} seconds** for a random catch — junk, fish, or something *legendary*. Most bites are small, but the rare ones are worth a fortune, so patience pays. Your haul sits in a `/bucket`; cash it in with `/sell` or dump it all with `/sellall`. Buy one of **{len(RODS)} rods** in `/shop` and equip it with `/rod` — each bends the odds or the price a different way. Leave `/autofish` on to cast for you at your rod's cooldown. **Fresh fish keep {float(g('economy.fish_rot_hours', 24)):g} online hours** before rod/plushie perks (I'll warn you at 1 day and 1 hour left) then rot — sell your haul in time. *Fishing is a valuable side business; careers are the dependable route to serious income.*",
            inline=False,
        )
        embed.add_field(
            name="🎰 The machine — `/slots <bet>`",
            value=f"Feed me **{ui.format_donuts(int(g('economy.slots_min_bet', 10)))}+** {cur} — **no upper limit** — and pull. Match my symbols:\n• any **two** alike — **2x**\n• three treats (🍬🧪⭐🍭🍫) — **3x**\n• three 🍩 — **8x**\n• three 👿 — **15x**\n• three 💠 — **50x**, the jackpot\nThe ordinary base match chance is **{int(g('economy.slots_win_pct', 54))}%**, before perks and hex. Winning combinations pay exactly the listed multiplier, with no additional house boost. Slots and wheel result buttons can repeat the stake or bet 25%, 50%, or all of your current wallet.",
            inline=False,
        )
        embed.add_field(
            name="🎡 The wheel — `/wheel <bet>`",
            value=f"Bet **{ui.format_donuts(int(g('economy.wheel_min_bet', 10)))}+** {cur} (**no ceiling**) — this wheel is a **jackpot pot**. **Every bet you make feeds the pot** after a small cut; wheel-slice payouts come back out of it:\n• 💥 **bust** (most spins) — your whole bet feeds the pot\n• 😬 **half back** · 😛 **break even** · 🍬 **2×** · 🍭 **3×**\n• 🍩 **JACKPOT** (about 1 in 640) — **scoop the ENTIRE pot**; the pot then **restarts at {ui.format_donuts(int(g('economy.wheel_jackpot_reseed', 100000)))}** so the next round's already worth chasing\nNon-jackpot spins refund **{int(g('economy.wheel_non_jackpot_rebate_pct', 6))}%** of your bet to your wallet. Losses pile up fast, so a jackpot is a life-changing payday. 🎡 **Fortune 21** refunds 10% of busts straight off the pot.",
            inline=False,
        )
        embed.add_field(
            name="🃏 Cards? — `/blackjack <bet>`",
            value=f"Bet **{ui.format_donuts(int(g('economy.blackjack_min_bet', 10)))}+** {cur} (**no ceiling**) and race my dealer to 21 without going over. Buttons let you **Hit** (draw), **Stand** (hold), **Surrender** (end immediately and recover half), or **Double Down** (double your bet for one last card). After settlement, **Deal Again** repeats the stake or **Rebet ×2** raises it without another slash command; 25%, 50%, and **Bet All** wallet bets are available too.\nA two-card 21 pays **{_roulette_ratio_text(int(g('economy.blackjack_natural_pct', 300)))}:1 profit**, a normal win pays **{_roulette_ratio_text(int(g('economy.blackjack_win_profit_pct', 135)))}:1 profit**, and a tie is a **push** — your bet comes back. My dealer stands on 17.\n💎 **Bonus hands:** a **suited** blackjack pays extra, and **three 7s** is an instant **JACKPOT** that always wins big.\nReach five cards without busting for a **{_roulette_ratio_text(int(g('economy.blackjack_charlie_profit_pct', 350)))}:1 Five-card Charlie** win.\nCards come from a real **shoe** dealt down over many hands, not a fresh deck each time — so the sharp among you can *count cards*, size your bets, and turn the odds in your favour. *Learn, little morsel, and the house is yours.*",
            inline=False,
        )
        embed.add_field(
            name="🎯 The wheel of numbers — `/roulette <bet> <space>`",
            value=f"Bet **{ui.format_donuts(int(g('economy.roulette_min_bet', 10)))}+** {cur} (**no ceiling**) on red/black, even/odd, low/high (**{_roulette_ratio_text(int(g('economy.roulette_even_profit_pct', 145)))}:1**), a dozen (**{_roulette_ratio_text(int(g('economy.roulette_dozen_profit_pct', 270)))}:1**), a single number (**{_roulette_ratio_text(int(g('economy.roulette_straight_profit_pct', 4000)))}:1**), or validated `split:8-11` (**18:1**), `street:13-14-15` (**12:1**) and `corner:17-18-20-21` (**8:1**) combinations. Green **0** misses outside bets. The house adds to a **straight-up progressive jackpot** based on each wager; hit your chosen number to scoop it. Results have **Repeat bet**, **25% wallet**, **50% wallet**, and **Bet All** buttons. *(My {PLUSHIE_BY_ID['roulette'].emoji} Roulette 21 plushie softens a zero.)*",
            inline=False,
        )
        embed.add_field(
            name="🏆 Records & the Tour — `/casinostats`",
            value=f"Every settled house game builds your private permanent record and **Casino Reputation**. Today's featured non-slot game awards double RP; complete all four games each week for **+250 RP**. Ranks 10 and 25 unlock exclusive titles. Settle a round of **wheel, blackjack and roulette** each UTC day to finish the **Casino Tour** for at least {self.money(cfg, int(g('economy.casino_tour_reward', 500000000)))}, or **{int(g('economy.casino_tour_wager_pct', 3))}%** of their combined highest daily wagers if greater, plus a growing daily streak bonus. Slots do not count toward the daily Tour. Casino results show your progress.",
            inline=False,
        )
        embed.add_field(
            name="🏦 The vault — `/bank`, `/deposit`, `/withdraw`",
            value=f"Tuck donuts into the bank and they're **safe from `/steal`** and earn **{int(g('economy.bank_interest_percent', 1))}%/day** interest — but not safe from a **🔩 Majin Drill**. Buy one ({self.money(cfg, int(g('economy.price_drill', 10000000)))}) and `/robbank` a rival to crack up to **{int(g('economy.bank_rob_max_percent', 20))}%** of their vault, **{int(g('economy.bank_rob_success_chance', 30))}%** of the time. The drill's spent either way — and if the alarm trips, you're fined and jailed. *My steal plushies count here too: a Guardian shields the vault, while Majin and Golden Donut sharpen a robber's odds and cut.*",
            inline=False,
        )
        embed.add_field(
            name="🥷 Hungry? — `/steal <user>`",
            value=f"Snatch up to **{int(g('economy.steal_max_percent', 25))}%** of a victim (they need at least {int(g('economy.steal_min_target', 50))} {cur}), **{int(g('economy.steal_success_chance', 40))}%** of the time, once every **{int(g('economy.steal_cooldown_minutes', 60))} min**. Get caught and you drop **{int(g('economy.steal_fail_fine', 500000))}** {cur}. But your prey may be *armed*...",
            inline=False,
        )
        embed.add_field(
            name="🛡️ Defending your donuts — `/shop`, `/buy`",
            value=f"🔒 **Lock** ({self.money(cfg, int(g('economy.price_lock')))}) — auto-blocks a wallet steal (**{int(g('economy.lock_block_chance', 45))}%**) or vault robbery (**{int(g('economy.bank_lock_block_chance', 15))}%**).\n🔄 **Uno Reverse** ({self.money(cfg, int(g('economy.price_uno')))}) — bounces a wallet steal (**{int(g('economy.uno_reverse_chance', 25))}%**) or vault robbery (**{int(g('economy.bank_uno_reverse_chance', 10))}%**) back on the thief.\n🚓 **Cop Call** ({self.money(cfg, int(g('economy.price_copcall')))}) — `/copcall user amount` stacks cards into **{int(g('economy.jail_minutes', 60))} min each** (no stealing/robbing). Use a **Getaway Car** with `/getaway` to escape early.\n*Robberies check: my Guardian plushie → your lock → your uno → luck.*",
            inline=False,
        )
        embed.add_field(
            name="🧸 My plushies — permanent perks",
            value="Always-on perks you buy once: boosts for /daily, /work, slots, blackjack, the wheel, roulette, fishing and bank interest, plus theft, defense and jail levers. Each perk sits on one plushie and never stacks. Browse them all with prices in `/shop`, and see your collection as a grid with `/plushies`.",
            inline=False,
        )
        embed.add_field(
            name="🏆 Bragging rights — `/leaderboard`",
            value=f"See who's hoarding the most {cur}. Climb it, or *eat* your way to the top. *Now off you go, sweet thing. Try not to get devoured.*",
            inline=False,
        )
        pages = ui.field_pages(
            embed.title,
            embed.description,
            [(field.name, field.value, field.inline) for field in embed.fields],
            color=cfg.color,
        )
        for index, guide_page in enumerate(pages, 1):
            guide_page.set_footer(
                text=f"Guide {index}/{len(pages)} · Configurable economy settings are owner-only via /config."
            )
        if len(pages) > 1:
            view = ui.Paginator(pages, interaction.user.id)
            await ui.respond(interaction, embed=pages[0], view=view)
        else:
            await ui.respond(interaction, embed=pages[0])

    @app_commands.command(name="casinostats", description="Privately view your lifetime casino records.")
    async def casino_stats(self, interaction: discord.Interaction) -> None:
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        member = interaction.user
        u = self.user(interaction.guild_id, member.id)
        stats, reputation = _casino_display_snapshot(u)
        overall = stats.get("overall", {}) if isinstance(stats.get("overall"), dict) else {}
        plays = int(overall.get("plays", 0))

        def signed(value: int) -> str:
            return f"+{ui.format_donuts(value)}" if value > 0 else f"{ui.format_donuts(value)}"

        games = stats.get("games", {}) if isinstance(stats.get("games"), dict) else {}
        labels = {
            "slots": "🎰 Slots",
            "wheel": "🎡 Wheel",
            "blackjack": "🃏 Blackjack",
            "roulette": "🎯 Roulette",
            "dice": "🎲 Dice (retired)",
        }
        display_games = list(CASINO_GAMES)
        legacy_dice = games.get("dice", {}) if isinstance(games.get("dice"), dict) else {}
        if int(legacy_dice.get("plays", 0)) > 0:
            display_games.append("dice")
        favourite = max(display_games, key=lambda game: int((games.get(game) or {}).get("plays", 0)))
        rank = min(50, int((reputation / 100) ** 0.5) + 1)
        featured = CASINO_FEATURED_GAMES[_now().date().toordinal() % len(CASINO_FEATURED_GAMES)]
        weekly = u.get("casino_weekly", {}) if isinstance(u.get("casino_weekly"), dict) else {}
        tour = u.get("casino_tour", {}) if isinstance(u.get("casino_tour"), dict) else {}
        today = _now().date().isoformat()
        played = tour.get("games", []) if tour.get("day") == today else []
        played = played if isinstance(played, list) else []
        required = max(1, min(len(CASINO_TOUR_GAMES), int(cfg.get("economy.casino_tour_games", 3))))
        claimed = bool(tour.get("claimed")) and tour.get("day") == today
        reward = int(cfg.get("economy.casino_tour_reward", 500000000))
        wager_pct = int(cfg.get("economy.casino_tour_wager_pct", 3))
        qualifying = tour.get("qualifying_wagers", {}) if tour.get("day") == today else {}
        qualifying = qualifying if isinstance(qualifying, dict) else {}
        qualifying_total = sum((max(0, int(qualifying.get(game, 0))) for game in CASINO_TOUR_GAMES))
        projected_reward = max(reward, qualifying_total * max(0, wager_pct) // 100)
        known_claim = claimed and "last_reward" in tour
        shown_reward = int(tour["last_reward"]) if known_claim else projected_reward
        shown_reward = shown_reward
        next_midnight = dt.datetime.combine(
            _now().date() + dt.timedelta(days=1), dt.time.min, tzinfo=dt.timezone.utc
        )
        history_summary = (
            f"**{plays:,}** settled games · **{ui.format_donuts(int(overall.get('wagered', 0)))}** total wagered\nLifetime net: **{self.emoji(cfg)} {signed(int(overall.get('net', 0)))}** · Favourite: **{labels[favourite]}**\nCurrent win streak: **{int(overall.get('current_streak', 0))}** · Best: **{int(overall.get('best_streak', 0))}** · Jackpots: **{int(overall.get('jackpots', 0))}**"
            if plays > 0
            else "No settled casino games yet. Your Casino Tour progress is still shown below."
        )
        embed = ui.base_embed(
            title=f"🏆 {member.display_name}'s Casino Record", description=history_summary, color=cfg.color
        )
        embed.add_field(
            name="🎖️ Casino Reputation",
            value=f"**Rank {rank}** · **{reputation:,} RP**\nToday's featured non-slot game: **{labels[featured]}** (double RP)\nWeekly circuit: **{len(set(weekly.get('games', []) or []).intersection(CASINO_GAMES))}/{len(CASINO_GAMES)}** games"
            + (" · ✅ +250 RP claimed" if weekly.get("claimed") else ""),
            inline=False,
        )
        checklist = " · ".join(
            (f"{('✅' if game in played else '⬜')} {labels[game]}" for game in CASINO_TOUR_GAMES)
        )
        progress = (
            "✅ **Tour complete — reward claimed!**"
            if claimed
            else f"**{len(set(played).intersection(CASINO_TOUR_GAMES))}/{required}** non-slot games completed"
        )
        embed.add_field(
            name="🎟️ Daily Casino Tour",
            value=f"{progress}\n{checklist}\n{('Awarded' if known_claim else 'Prize')}: **{self.money(cfg, shown_reward)}** {('including' if known_claim else 'before')} streak bonus (at least {self.money(cfg, reward)}; {wager_pct}% of each non-slot game's highest daily wager if greater) · Streak **{int(tour.get('streak', 0))}** · Best **{int(tour.get('best_streak', 0))}** · Completed **{int(tour.get('completions', 0))}**\nResets <t:{int(next_midnight.timestamp())}:R>",
            inline=False,
        )
        for game in display_games:
            bucket = games.get(game, {}) if isinstance(games.get(game), dict) else {}
            modes = bucket.get("modes", {}) if isinstance(bucket.get("modes"), dict) else {}
            extra = ""
            if modes and game in ("roulette", "dice"):
                fav_mode = max(modes, key=lambda key: int(modes[key]))
                extra = f"\nFavourite bet/mode: **{fav_mode.replace('_', ' ').title()}**"
            embed.add_field(
                name=labels[game],
                value=f"**{int(bucket.get('plays', 0)):,}** plays · {int(bucket.get('wins', 0)):,}W / {int(bucket.get('losses', 0)):,}L / {int(bucket.get('pushes', 0)):,}P\nWagered **{ui.format_donuts(int(bucket.get('wagered', 0)))}** · Net **{signed(int(bucket.get('net', 0)))}** · Best **+{ui.format_donuts(int(bucket.get('biggest_win', 0)))}**{extra}",
                inline=False,
            )
        embed.set_footer(text="Lifetime tracking began with the Casino Expansion update.")
        await ui.respond(interaction, embed=embed, ephemeral=True)

    @app_commands.command(name="daily", description="Claim your daily donuts.")
    async def daily(self, interaction: discord.Interaction) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        u = self.user(interaction.guild_id, interaction.user.id)
        cd = int(cfg.get("economy.daily_cooldown_hours", 12)) * 3600
        last = _parse(u.get("daily_at"))
        if last is not None:
            elapsed = (_now() - last).total_seconds()
            if elapsed < cd:
                ready = int(_now().timestamp() + (cd - elapsed))
                await ui.respond(
                    interaction,
                    embed=ui.warn_embed(f"Already claimed. Next one <t:{ready}:R>."),
                    ephemeral=True,
                )
                return
        now = _now()
        base = int(cfg.get("economy.daily_amount", 5500000))
        grace = cd * 3
        streak = int(u.get("daily_streak", 0))
        if last is not None and (now - last).total_seconds() <= grace:
            streak += 1
        else:
            streak = 1
        u["daily_streak"] = streak
        per = int(cfg.get("economy.daily_streak_bonus_pct", 5))
        cap = int(cfg.get("economy.daily_streak_max", 20))
        streak_pct = min(max(streak - 1, 0), cap) * per
        plush_pct = plushie_perk(u, "daily") + plushie_perk(u, "mythic")
        amount = base + base * (streak_pct + plush_pct) // 100
        u["donuts"] += amount
        u["daily_at"] = now.isoformat()
        garnished = self._garnish(interaction.guild_id, interaction.user.id, u, amount)
        await self.persist(interaction.guild_id)
        await self._log(interaction.guild_id, interaction.user.id, amount - garnished, "daily", u)
        desc = f"You claimed {self.money(cfg, amount)}."
        desc += f"\n🔥 Streak: **{streak} claim{('s' if streak != 1 else '')}**"
        if streak_pct:
            desc += f" — +{streak_pct}% bonus"
        if plush_pct:
            desc += f"\n🍬 Sweet Tooth bonus: +{plush_pct}%"
        if garnished:
            desc += f"\n🩸 {self.money(cfg, garnished)} garnished toward your overdue debt."
        desc += f"\nBalance: {self.money(cfg, u['donuts'])}"
        await ui.respond(interaction, embed=ui.ok_embed(desc, title="Daily donuts"))
        await self._award(interaction, interaction.user, u, "first_bite")

    @app_commands.command(name="jobs", description="Browse careers, salaries and promotion rules.")
    async def jobs(self, interaction: discord.Interaction) -> None:
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        salary_lines = [
            f"**Tier {level}:** {self.money(cfg, self._work_salary(cfg, level))}" for level in range(1, 6)
        ]
        embed = ui.base_embed(
            title="💼 Careers & Employment",
            description="Choose one career with `/job apply`, then use `/work`. Each shift asks one quick workplace question: good decisions earn full salary plus a performance bonus; mistakes still earn guaranteed pay. XP promotes you automatically.",
            color=cfg.color,
        )
        for career in WORK_CAREERS.values():
            embed.add_field(
                name=f"{career['emoji']} {career['name']}",
                value=f"{career['summary']}\n" + " → ".join(career["roles"]),
                inline=False,
            )
        embed.add_field(name="Salary per shift", value="\n".join(salary_lines), inline=True)
        embed.add_field(
            name="Progression",
            value=f"**Correct decision:** +3 XP · **mistake:** +1 XP\nComplete **{int(cfg.get('economy.work_daily_bonus_shifts', 5))} shifts** in one UTC day for a payday bonus.",
            inline=True,
        )
        embed.add_field(
            name="🦾 Android 21 Autonomous Helper",
            value="A permanent `/shop` utility that works every normal shift interval, even while you are offline. It deposits **70% base salary** into your wallet and earns **+1 XP** per shift, without decision, plushie, event or five-shift-payday bonuses.",
            inline=False,
        )
        embed.add_field(
            name="Advanced career licences",
            value="After reaching Tier 5 in any career, buy sequential account-wide licences with `/job certify`. Correct manual `/work` decisions earn a flat extra wallet bonus. Paid helper certification adds 25% of an owned licence bonus to automatic shifts. The 10–50 manual-shift gates are cumulative; Tier 5 is also required (200 XP by default).",
            inline=False,
        )
        embed.set_footer(
            text=f"One shift every {int(cfg.get('economy.work_cooldown_minutes', 10))} min · all careers pay equally"
        )
        await ui.respond(interaction, embed=embed)

    job_group = app_commands.Group(name="job", description="Choose and inspect your career.")

    @job_group.command(name="licences", description="View advanced career licence costs and requirements.")
    async def job_licences(self, interaction: discord.Interaction) -> None:
        cfg = await self._guard(interaction, needs_channel=False, ephemeral=True)
        if cfg is None:
            return
        u = self.user(interaction.guild_id, interaction.user.id)
        await self._settle_and_log_android21_helper(cfg, interaction.guild_id, interaction.user.id, u)
        employment = self._employment(u)
        owned = int(employment["career_licence_tier"])
        manual = int(employment["manual_shifts"])
        lines = []
        for index, (name, cost, bonus, shifts) in enumerate(CAREER_LICENCES, start=1):
            state = "owned" if index <= owned else "next" if index == owned + 1 else "locked"
            lines.append(
                f"**{index}. {name}** ({state}) — {self.money(cfg, cost)} · {shifts} manual shifts · +{ui.format_donuts(bonus)} per correct manual shift"
            )
        embed = ui.base_embed(
            title="Advanced career licences",
            description=f"**Your manual shifts:** {manual:,} · **Tier 5 reached:** {('yes' if self._licence_unlocked(cfg, employment) else 'no')}\n\n"
            + "\n".join(lines)
            + "\n\nBuy the next licence with `/job certify`. Only the highest owned bonus applies. The bonus is flat, wallet-paid, and excluded from payday, plushie and event multipliers. A certified Android 21 earns 25% of its certified licence bonus; automatic shifts never satisfy manual-shift requirements. See the helper certification button below.",
            color=cfg.color,
        )
        embed.add_field(
            name="Android 21 certification",
            value="Requires an owned helper and licence. Automatic shifts keep 70% base salary and add 25% of the highest certified licence bonus. Cumulative certification prices: 40B / 250B / 1.5 trillion / 10 trillion / 75 trillion. Previous certification cost is credited. Paid from wallet; existing offline shifts settle at the old rate before upgrading.",
            inline=False,
        )
        await self.persist(interaction.guild_id)
        await activity_ui.show(interaction, embed, CareerProgressView(self, interaction.user.id))

    async def certification_preview(self, interaction, *, helper):
        cfg = await self._guard(interaction, needs_channel=False, ephemeral=True)
        if cfg is None:
            return
        u = self.user(interaction.guild_id, interaction.user.id)
        await self._settle_and_log_android21_helper(cfg, interaction.guild_id, interaction.user.id, u)
        employment = self._employment(u)
        try:
            if helper:
                tier, cost = progression.helper_quote(u)
                title = "Certify Android 21 to " + CAREER_LICENCES[tier - 1][0]
                note = f"Adds 25% of this licence's flat bonus: {ui.format_donuts(CAREER_LICENCES[tier - 1][2] // 4)} per automatic shift, plus 70% base salary. Previous certification payments are credited. No manual-shift credit or other bonus multipliers."
            else:
                tier = employment["career_licence_tier"] + 1
                if tier > len(CAREER_LICENCES):
                    raise ValueError("You already own Orbital Enterprise.")
                name, cost, bonus, shifts = CAREER_LICENCES[tier - 1]
                if not self._licence_unlocked(cfg, employment):
                    raise ValueError("Reach Tier 5 in any career first.")
                if employment["manual_shifts"] < shifts:
                    raise ValueError(f"Requires {shifts} cumulative manual shifts.")
                title, note = (
                    "Purchase " + name,
                    f"Adds {ui.format_donuts(bonus)} per correct manual shift. Replaces the previous bonus.",
                )
        except ValueError as error:
            await ui.respond(interaction, embed=ui.warn_embed(str(error)), ephemeral=True)
            return
        generation = reset_generation(u)

        async def commit(click):
            fresh_cfg = await self._guard(click, needs_channel=False, ephemeral=True)
            if fresh_cfg is None:
                return
            current = self.user(click.guild_id, click.user.id)
            if reset_generation(current) != generation:
                await ui.respond(
                    click, content="This purchase expired after an account reset.", ephemeral=True
                )
                return
            if helper:
                await self._settle_and_log_android21_helper(fresh_cfg, click.guild_id, click.user.id, current)
                try:
                    latest, price = progression.helper_quote(current)
                    if latest != tier or price != cost:
                        raise ValueError("Your licence or certification changed. Open a fresh quote.")
                    if int(current.get("donuts", 0)) < price:
                        raise ValueError("Certification is paid from wallet; withdraw enough first.")
                except ValueError as error:
                    await ui.respond(click, content=str(error), ephemeral=True)
                    return
                current["donuts"] -= price
                current["helper_certification_tier"] = tier
                await self.persist(click.guild_id)
                await self._log(click.guild_id, click.user.id, -price, "helper-certification", current)
                await ui.respond(
                    click,
                    embed=ui.ok_embed(
                        "Helper certified. The new allowance applies to future automatic shifts only."
                    ),
                    ephemeral=True,
                )
            else:
                if self._employment(current)["career_licence_tier"] + 1 != tier:
                    await ui.respond(
                        click, content="This licence quote is no longer current.", ephemeral=True
                    )
                    return
                await self._purchase_licence(click)

        embed = ui.base_embed(
            title=title,
            description=f"Cost: {self.money(cfg, cost)} from wallet.\n{note}\nWallet shortfall: {ui.format_donuts(max(0, cost - int(u.get('donuts', 0))))}. Nothing spent until confirmation.",
        )
        await activity_ui.show(
            interaction, embed, activity_ui.Confirm(interaction.user.id, "Confirm purchase", commit)
        )

    @job_group.command(name="certify", description="Confirm purchase of your next advanced career licence.")
    async def job_certify(self, interaction: discord.Interaction) -> None:
        await self.certification_preview(interaction, helper=False)

    async def _purchase_licence(self, interaction: discord.Interaction) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        u = self.user(interaction.guild_id, interaction.user.id)
        await self._settle_and_log_android21_helper(cfg, interaction.guild_id, interaction.user.id, u)
        employment = self._employment(u)
        owned = int(employment["career_licence_tier"])
        if owned >= len(CAREER_LICENCES):
            await ui.respond(
                interaction,
                embed=ui.warn_embed("You already hold the highest career licence."),
                ephemeral=True,
            )
            return
        name, cost, bonus, shifts = CAREER_LICENCES[owned]
        if not self._licence_unlocked(cfg, employment):
            await ui.respond(
                interaction,
                embed=ui.warn_embed("Reach Tier 5 in any career before buying a licence."),
                ephemeral=True,
            )
            return
        manual = int(employment["manual_shifts"])
        if manual < shifts:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(
                    f"**{name}** requires {shifts} manual shifts; you have {manual}. Automatic helper shifts do not count."
                ),
                ephemeral=True,
            )
            return
        if int(u.get("donuts", 0)) < cost:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(f"**{name}** costs {self.money(cfg, cost)} from your wallet."),
                ephemeral=True,
            )
            return
        u["donuts"] = int(u["donuts"]) - cost
        employment["career_licence_tier"] = owned + 1
        await self.persist(interaction.guild_id)
        await self._log(interaction.guild_id, interaction.user.id, -cost, "career-licence", u, detail=name)
        await ui.respond(
            interaction,
            embed=ui.ok_embed(
                f"**{name}** purchased for {self.money(cfg, cost)}. Correct manual shifts now add **{ui.format_donuts(bonus)}** donuts directly to your wallet. This replaces your previous licence bonus.",
                title="Career certified",
            ),
            ephemeral=True,
        )

    @job_group.command(name="apply", description="Start a career or switch to another one.")
    @app_commands.describe(career="Career to enter")
    @app_commands.choices(
        career=[
            app_commands.Choice(name=f"{career['emoji']} {career['name']}", value=key)
            for key, career in WORK_CAREERS.items()
        ]
    )
    async def job_apply(self, interaction: discord.Interaction, career: app_commands.Choice[str]) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        u = self.user(interaction.guild_id, interaction.user.id)
        if u.get("android21_helper"):
            await self._settle_and_log_android21_helper(cfg, interaction.guild_id, interaction.user.id, u)
        employment = self._employment(u)
        selected = career.value
        info = WORK_CAREERS[selected]
        if employment.get("active") == selected:
            await ui.respond(
                interaction, embed=ui.warn_embed(f"You already work in **{info['name']}**."), ephemeral=True
            )
            return
        employment["active"] = selected
        if u.get("android21_helper"):
            now = _now().isoformat()
            u["android21_helper_at"] = now
            u["work_at"] = now
        record = self._career_record(employment, selected)
        level = self._work_level(cfg, int(record["xp"]))
        await self.persist(interaction.guild_id)
        returning = int(record["shifts"]) > 0
        await ui.respond(
            interaction,
            embed=ui.base_embed(
                title=f"{info['emoji']} {('Career resumed' if returning else 'Application accepted')}",
                description=f"{interaction.user.mention} is now **{info['roles'][level - 1]}** in **{info['name']}**.\n\nBase salary: {self.money(cfg, self._work_salary(cfg, level))} per shift · start with `/work`.",
                color=cfg.color,
            ),
        )

    @job_group.command(name="status", description="Privately inspect your career progress and earnings.")
    async def job_status(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction, needs_channel=False, ephemeral=True)
        if cfg is None:
            return
        u = self.user(interaction.guild_id, interaction.user.id)
        await self._settle_and_log_android21_helper(cfg, interaction.guild_id, interaction.user.id, u)
        employment = self._employment(u)
        active = employment.get("active")
        if active not in WORK_CAREERS:
            await ui.respond(
                interaction,
                embed=ui.warn_embed("You do not have a career yet. Browse `/jobs`, then use `/job apply`."),
                ephemeral=True,
            )
            return
        record = self._career_record(employment, active)
        info = WORK_CAREERS[active]
        xp = int(record["xp"])
        level = self._work_level(cfg, xp)
        thresholds = self._work_thresholds(cfg)
        if level < 5:
            progress = f"{xp}/{thresholds[level]} XP toward Tier {level + 1}"
        else:
            progress = f"{xp} XP · maximum tier"
        shifts = int(record["shifts"])
        accuracy = int(record["correct"]) * 100 // shifts if shifts else 0
        daily_target = max(1, int(cfg.get("economy.work_daily_bonus_shifts", 5)))
        daily_shifts = int(employment.get("daily_shifts", 0))
        payday = (
            "✅ Claimed"
            if employment.get("daily_bonus_claimed")
            else f"{min(daily_shifts, daily_target)}/{daily_target} shifts"
        )
        embed = ui.base_embed(
            title=f"{info['emoji']} {interaction.user.display_name}'s career",
            description=f"**{info['roles'][level - 1]}** · {info['name']}",
            color=cfg.color,
        )
        embed.add_field(name="📈 Promotion", value=progress, inline=False)
        embed.add_field(
            name="💰 Salary and today's payday",
            value=status_ui.line("Base salary", self.money(cfg, self._work_salary(cfg, level)))
            + "\n"
            + status_ui.line("Today's payday", payday),
            inline=False,
        )
        embed.add_field(
            name="📋 Career record",
            value=f"**{shifts}** shifts · **{accuracy}%** correct\nLifetime gross: {self.money(cfg, int(record['earned']))}",
            inline=False,
        )
        licence_tier = int(employment["career_licence_tier"])
        licence_label = CAREER_LICENCES[licence_tier - 1][0] if licence_tier else "None"
        embed.add_field(
            name="🎓 Advanced licence",
            value=f"**{licence_label}** · {int(employment['manual_shifts']):,} manual shifts\nUse `/job licences` for the next milestone.",
            inline=False,
        )
        if u.get("android21_helper"):
            anchor = _parse(u.get("android21_helper_at")) or _now()
            interval = max(60, int(cfg.get("economy.work_cooldown_minutes", 10)) * 60)
            next_shift = int(anchor.timestamp() + interval)
            embed.add_field(
                name="🦾 Autonomous Helper — ACTIVE",
                value=f"**Next wallet deposit:** {status_ui.deadline(dt.datetime.fromtimestamp(next_shift, dt.timezone.utc))}\n**Pay:** 70% base + {ui.format_donuts(progression.helper_allowance(u))} certified allowance/shift\nAutomatic shifts: **{int(u.get('android21_helper_shifts', 0)):,}** · gross earned: {self.money(cfg, int(u.get('android21_helper_earned', 0)))}",
                inline=False,
            )
        embed.add_field(
            name="➡️ Next action",
            value="Use `/work` for a shift, `/job licences` for your next milestone, or `/job apply` to switch careers without losing progress.",
            inline=False,
        )
        embed.set_footer(text="Private career report · finish dates use your local timezone")
        await self.persist(interaction.guild_id)
        await activity_ui.show(interaction, embed, CareerProgressView(self, interaction.user.id))

    def _licence_progress_text(self, cfg, user):
        e = self._employment(user)
        owned = e["career_licence_tier"]
        if owned >= len(CAREER_LICENCES):
            return f"Orbital Enterprise: +{ui.format_donuts(CAREER_LICENCES[-1][2])} per correct manual shift. Highest licence reached."
        name, cost, bonus, shifts = CAREER_LICENCES[owned]
        xp = max([int(v.get("xp", 0)) for v in e["records"].values() if isinstance(v, dict)] or [0])
        threshold = self._work_thresholds(cfg)[-1]
        return f"Next: **{name}** · +{ui.format_donuts(bonus)} per correct manual shift\nTier 5: {('reached' if self._licence_unlocked(cfg, e) else f'{xp}/{threshold} XP')} · Cumulative manual shifts: {e['manual_shifts']}/{shifts}\nCost: {ui.format_donuts(cost)} from wallet · shortfall {ui.format_donuts(max(0, cost - int(user.get('donuts', 0))))}. Withdraw from your own money stores if needed; helper shifts do not count toward the manual gate."

    @app_commands.command(name="work", description="Complete a career shift for salary and promotion XP.")
    async def work(self, interaction: discord.Interaction) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        u = self.user(interaction.guild_id, interaction.user.id)
        employment = self._employment(u)
        active = employment.get("active")
        if active not in WORK_CAREERS:
            await ui.respond(
                interaction,
                embed=ui.warn_embed("Choose a career first: browse `/jobs`, then use `/job apply`."),
                ephemeral=True,
            )
            return
        if u.get("android21_helper"):
            await self._settle_and_log_android21_helper(cfg, interaction.guild_id, interaction.user.id, u)
        pending = _parse(employment.get("pending_until"))
        if pending is not None and pending > _now():
            await ui.respond(
                interaction,
                embed=ui.warn_embed("Finish your current workplace decision first."),
                ephemeral=True,
            )
            return
        employment["pending_until"] = None
        cd = max(0, int(cfg.get("economy.work_cooldown_minutes", 10))) * 60
        last = _parse(employment.get("manual_work_at"))
        if last is not None:
            elapsed = (_now() - last).total_seconds()
            if elapsed < cd:
                ready = int(_now().timestamp() + (cd - elapsed))
                await ui.respond(
                    interaction, embed=ui.warn_embed(f"Your next shift starts <t:{ready}:R>."), ephemeral=True
                )
                return
        source = random.choice(WORK_SCENARIOS[active])
        order = list(range(len(source["choices"])))
        random.shuffle(order)
        scenario = dict(source)
        scenario["choices"] = tuple((source["choices"][index] for index in order))
        scenario["correct"] = order.index(int(source["correct"]))
        record = self._career_record(employment, active)
        level = self._work_level(cfg, int(record["xp"]))
        info = WORK_CAREERS[active]
        employment["pending_until"] = (_now() + dt.timedelta(seconds=65)).isoformat()
        await self.persist(interaction.guild_id)
        view = WorkShiftView(self, interaction.guild_id, interaction.user.id, active, scenario)
        embed = ui.base_embed(
            title=f"{info['emoji']} Shift: {info['roles'][level - 1]}",
            description=f"**Workplace situation**\n{scenario['prompt']}\n\nChoose your response within **60 seconds**. You are paid either way, but the best decision earns more salary and XP.",
            color=cfg.color,
        )
        await ui.respond(interaction, embed=embed, view=view)
        view.message = await ui.response_message(interaction)

    async def _expire_work_shift(self, guild_id: int, user_id: int) -> None:
        u = self.user(guild_id, user_id)
        employment = self._employment(u)
        pending = _parse(employment.get("pending_until"))
        if pending is not None and pending <= _now() + dt.timedelta(seconds=10):
            employment["pending_until"] = None
            await self.persist(guild_id)

    async def _complete_work_shift(
        self, interaction: discord.Interaction, career: str, scenario: Dict[str, Any], correct: bool
    ) -> discord.Embed:
        gid = interaction.guild_id
        cfg = self.cfg(gid)
        u = self.user(gid, interaction.user.id)
        employment = self._employment(u)
        employment["pending_until"] = None
        completed_at = _now().isoformat()
        u["work_at"] = completed_at
        employment["manual_work_at"] = completed_at
        employment["manual_shifts"] = int(employment["manual_shifts"]) + 1
        record = self._career_record(employment, career)
        info = WORK_CAREERS[career]
        old_level = self._work_level(cfg, int(record["xp"]))
        base = self._work_salary(cfg, old_level)
        if correct:
            performance_pct = max(100, int(cfg.get("economy.work_correct_bonus_pct", 25)) + 100)
            xp_gain = 3
        else:
            performance_pct = max(1, min(100, int(cfg.get("economy.work_incorrect_pay_pct", 70))))
            xp_gain = 1
        shift_pay = base * performance_pct // 100
        perk_pct = plushie_perk(u, "work") + plushie_perk(u, "mythic")
        perk_bonus = shift_pay * perk_pct // 100
        employment["daily_shifts"] = int(employment.get("daily_shifts", 0)) + 1
        daily_target = max(1, int(cfg.get("economy.work_daily_bonus_shifts", 5)))
        payday = 0
        if not employment.get("daily_bonus_claimed") and int(employment["daily_shifts"]) >= daily_target:
            multiplier = max(0, int(cfg.get("economy.work_daily_bonus_multiplier", 3)))
            payday = base * multiplier
            employment["daily_bonus_claimed"] = True
        gross = shift_pay + perk_bonus + payday
        pre_event_gross = gross
        active_event = gevents.active(self.econ.store.load(gid))
        event_pct = {"donutboom": 25, "payday": 50, "market": -15}.get(active_event or "", 0)
        if event_pct:
            gross = max(1, gross + gross * event_pct // 100)
        event_adjustment = gross - pre_event_gross
        licence_tier = int(employment["career_licence_tier"])
        licence_bonus = CAREER_LICENCES[licence_tier - 1][2] if correct and licence_tier else 0
        gross += licence_bonus
        record["xp"] = int(record["xp"]) + xp_gain
        record["shifts"] = int(record["shifts"]) + 1
        record["correct"] = int(record["correct"]) + int(correct)
        record["earned"] = int(record["earned"]) + gross
        new_level = self._work_level(cfg, int(record["xp"]))
        u["donuts"] = int(u.get("donuts", 0)) + gross
        garnished = self._garnish(gid, interaction.user.id, u, gross)
        await self.persist(gid)
        await self._log(gid, interaction.user.id, gross - garnished, "work", u)
        result = str(scenario["success"] if correct else scenario["failure"])
        lines = [
            result,
            "",
            f"**Shift salary:** {self.money(cfg, shift_pay)}",
            f"**Career XP:** +{xp_gain} · **Total:** {int(record['xp'])}",
        ]
        if perk_bonus:
            lines.append(f"**Plushie bonus:** {self.money(cfg, perk_bonus)}")
        if payday:
            lines.append(f"🎉 **Daily payday:** {self.money(cfg, payday)}")
        if event_adjustment:
            sign = "+" if event_adjustment > 0 else "−"
            lines.append(
                f"📡 **Active event ({event_pct:+d}%):** {sign}{self.money(cfg, abs(event_adjustment))}"
            )
        if licence_bonus:
            lines.append(
                f"**{CAREER_LICENCES[licence_tier - 1][0]} licence:** +{self.money(cfg, licence_bonus)} (flat)"
            )
        if garnished:
            lines.append(f"🩸 **Debt garnishment:** {self.money(cfg, garnished)}")
        if new_level > old_level:
            lines.extend(
                [
                    "",
                    f"⬆️ **PROMOTED:** {info['roles'][new_level - 1]}",
                    f"New base salary: {self.money(cfg, self._work_salary(cfg, new_level))}",
                ]
            )
        lines.extend(
            ["", self._licence_progress_text(cfg, u), "", f"**Wallet:** {self.money(cfg, int(u['donuts']))}"]
        )
        return ui.base_embed(
            title=f"✅ Shift complete — {info['name']}" if correct else f"🟠 Shift complete — {info['name']}",
            description="\n".join(lines),
            color=ui.COLOR_OK if correct else ui.COLOR_WARN,
        )

    @app_commands.command(name="balance", description="Check donuts, items, plushies and rods.")
    @app_commands.describe(user="Whose balance to check (defaults to you)")
    async def balance(self, interaction: discord.Interaction, user: Optional[discord.Member] = None) -> None:
        nyx.protect_report(interaction, self.econ, (user or interaction.user).id)
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        target = user or interaction.user
        u = self.user(interaction.guild_id, target.id)
        if nyx.hidden(u, interaction.user.id, target.id):
            await ui.respond(interaction, embed=nyx.censored_embed(), ephemeral=True)
            return
        await self._settle_and_log_android21_helper(cfg, interaction.guild_id, target.id, u)
        if await self._accrue_and_log(cfg, interaction.guild_id, target.id, u):
            await self.persist(interaction.guild_id)
        crown = "👑 " if u.get("crown") else ""
        embed = ui.base_embed(title=f"{crown}{target.display_name}'s stash", color=cfg.color)
        worn = equipped_label(u)
        if worn:
            embed.set_author(name=worn)
        embed.add_field(name="Wallet", value=self.money(cfg, u["donuts"]), inline=True)
        if u.get("bank", 0):
            embed.add_field(name="Bank", value=self.money(cfg, u["bank"]), inline=True)
        items = u.get("inventory", {}) or {}
        if any(items.values()):
            embed.add_field(
                name="Items",
                value="\n".join(
                    (
                        f"{CONSUMABLES[k]['emoji']} {CONSUMABLES[k]['name']} ×{n}"
                        for k, n in items.items()
                        if n and k in CONSUMABLES
                    )
                ),
                inline=True,
            )
        if u.get("android21_helper"):
            embed.add_field(
                name="🦾 Automation",
                value=f"Android 21 Autonomous Helper · **ACTIVE**\n{int(u.get('android21_helper_shifts', 0)):,} shifts · {self.money(cfg, int(u.get('android21_helper_earned', 0)))} gross",
                inline=True,
            )
        plushies = u.get("plushies", {}) or {}
        if any(plushies.values()):
            embed.add_field(
                name="Plushies",
                value="\n".join(
                    (
                        f"{PLUSHIE_BY_ID[k].emoji} {PLUSHIE_BY_ID[k].name}" + (f" ×{n}" if n > 1 else "")
                        for k, n in plushies.items()
                        if n and k in PLUSHIE_BY_ID
                    )
                ),
                inline=True,
            )
        rods = u.get("rods", {}) or {}
        if any(rods.values()):
            equipped = u.get("equipped_rod")
            ench_map = u.get("rod_enchants", {}) or {}

            def _elabels(rid: str) -> str:
                lst = ench_map.get(rid)
                if not lst:
                    return ""
                if isinstance(lst, str):
                    lst = [lst]
                counts: Dict[str, int] = {}
                for e in lst:
                    counts[e] = counts.get(e, 0) + 1
                parts = [
                    ENCHANTS[e][0].split()[0] + (f"×{n}" if n > 1 else "")
                    for e, n in counts.items()
                    if e in ENCHANTS
                ]
                return "  " + " ".join(parts) if parts else ""

            embed.add_field(
                name="Rods",
                value="\n".join(
                    (
                        f"{r.emoji} {r.name}" + (" ✅" if r.id == equipped else "") + _elabels(r.id)
                        for r in RODS
                        if rods.get(r.id)
                    )
                ),
                inline=True,
            )
        vt = int(u.get("vault_tier", 0))
        if vt > 0:
            bonus = VAULT_TIERS[min(vt, len(VAULT_TIERS)) - 1][1]
            embed.add_field(name="Vault", value=f"Tier {vt} · +{bonus}% interest rate", inline=True)
        debt = self._overdue_debt(interaction.guild_id, target.id)
        if debt > 0:
            embed.add_field(
                name="🩸 In debt",
                value=f"Owes {self.money(cfg, debt)} **overdue** — lenders can `/collect`.",
                inline=False,
            )
        if target.id == interaction.user.id:
            hex_until = hexes.active_until(u, user_id=target.id)
            if hex_until is not None:
                remaining = (hex_until - _now()).total_seconds()
                embed.add_field(
                    name="🩸 Hex status",
                    value=f"**HEXED** — {ui.human_duration(remaining)} remaining\nExpires <t:{int(hex_until.timestamp())}:R>",
                    inline=False,
                )
            else:
                if u.get("hexed_until") is not None or u.get("hexed_by") is not None:
                    u["hexed_until"] = None
                    u["hexed_by"] = None
                    await self.persist(interaction.guild_id)
                embed.add_field(name="🩸 Hex status", value="✅ Not hexed.", inline=False)
        earned = u.get("badges", {}) or {}
        if earned:
            shown = " ".join((badges.BADGE_BY_ID[b].emoji for b in earned if b in badges.BADGE_BY_ID))
            embed.add_field(
                name=f"Badges ({len(earned)}/{len(badges.BADGES)})",
                value=f"{shown}\nSee them all with `/badges`.",
                inline=False,
            )
        jail = _remaining(u.get("jailed_until"))
        if jail > 0:
            embed.add_field(name="Jailed", value=f"Can't steal for {ui.human_duration(jail)}", inline=False)
        if target.display_avatar:
            embed.set_thumbnail(url=target.display_avatar.url)
        await ui.respond(interaction, embed=embed, ephemeral=nyx.active(u))
        await self._award(interaction, target, u)

    @app_commands.command(name="slots", description="Spin the donut slot machine.")
    @app_commands.describe(bet="Bet: number, 25k/2.5m/1b, half, or all")
    async def slots(
        self, interaction: discord.Interaction, bet: app_commands.Transform[int, AcknowledgedBetTransformer]
    ) -> None:
        await ui.defer_response(interaction)
        cfg = await self._guard(interaction, game="slots")
        if cfg is None:
            return
        u = self.user(interaction.guild_id, interaction.user.id)
        lo = int(cfg.get("economy.slots_min_bet", 10))
        if bet < lo:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"Minimum bet is {ui.format_donuts(lo)} — no ceiling above that, bet as much as you dare."
                ),
                ephemeral=True,
            )
            return
        if bet > u["donuts"]:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"You only have {self.money(cfg, u['donuts'])}. Claim more with `/daily` or `/work`."
                ),
                ephemeral=True,
            )
            return
        cd = float(cfg.get("economy.slots_cooldown_seconds", 3) or 0)
        now = time.monotonic()
        if cd and interaction.user.id in self._slot_cd and (now - self._slot_cd[interaction.user.id] < cd):
            await ui.respond(
                interaction, embed=ui.warn_embed("The machine's still whirring. One sec."), ephemeral=True
            )
            return
        self._slot_cd[interaction.user.id] = now
        before = self._net(u)
        labcoat_display = plushie_perk(u, "slots")
        global_pct = int(cfg.get("economy.slots_win_pct", 54))
        win_pct = global_pct if global_pct > 0 else None
        labcoat = labcoat_display
        if labcoat:
            base = win_pct if win_pct is not None else 45
            win_pct = min(95, base + labcoat)
        win_pct = _hex_adjusted_slots_pct(interaction.user.id, u, win_pct)
        if win_pct is not None:
            reels, mult = biased_spin(random, win_pct)
        else:
            reels, mult = spin(random)
        events: List[str] = []
        if mult > 0:
            winnings = bet * mult
            boost = int(cfg.get("economy.slots_boost_pct", 0))
            if boost:
                winnings += winnings * boost // 100
            net = winnings - bet
            u["donuts"] += net
            if mult >= JACKPOT_MULT:
                events.append("jackpot")
            if net >= 5000:
                events.append("big_score")
            title = f"{''.join(reels)}  —  {mult}x!"
            desc = f"You won {self.money(cfg, winnings)} (net {ui.format_donuts(net, signed=True)})."
            if boost:
                desc += f"\n🎰 House boost: +{boost}%"
            embed = ui.base_embed(title=title, description=desc, color=ui.COLOR_OK)
        else:
            u["donuts"] -= bet
            embed = ui.base_embed(
                title=f"{''.join(reels)}  —  no match",
                description=f"You lost {self.money(cfg, bet)}.",
                color=ui.COLOR_BAD,
            )
        if labcoat_display:
            embed.description = (
                embed.description or ""
            ) + f"\n🧪 Lab Coat equipped: +{labcoat_display}pp slots perk"
        game_net = self._net(u) - before
        tour = self._record_casino(
            cfg,
            u,
            "slots",
            bet,
            game_net,
            mode="standard",
            jackpot=mult >= JACKPOT_MULT,
            guild_id=interaction.guild_id,
        )
        embed.description = (embed.description or "") + self._tour_note(cfg, tour, interaction.user.id)
        embed.set_footer(
            text=f"Balance: {ui.format_donuts(u['donuts'])} {self.name(cfg)} · Buttons last 10 min; run /slots again after a restart."
        )
        await self.persist(interaction.guild_id)
        tour_reward = int(tour.get("tour_reward", 0))
        await self._log(
            interaction.guild_id,
            interaction.user.id,
            game_net,
            "slots",
            u,
            after_override=self._net(u) - tour_reward,
        )
        if int(tour.get("tour_reward", 0)):
            await self._log(interaction.guild_id, interaction.user.id, tour_reward, "casino-tour", u)
        await self._send_slots_result(interaction, embed, bet)
        await self._award(interaction, interaction.user, u, *events)

    async def _send_slots_result(self, interaction, embed, bet) -> None:
        """Retry only delivery of the already-saved result, never the spin."""
        replay = CasinoReplayView(self, interaction.user.id, "slots", bet)
        try:
            await ui.respond(interaction, embed=embed, view=replay)
        except (discord.HTTPException, OSError, asyncio.TimeoutError) as exc:
            if isinstance(exc, discord.HTTPException) and exc.status < 500:
                replay.stop()
                raise
            log.warning(
                "Slots result delivery failed; retrying the saved result once (%s).", type(exc).__name__
            )
            try:
                await ui.respond(interaction, embed=embed, view=replay)
            except Exception:
                replay.stop()
                raise
        try:
            replay.message = await ui.response_message(interaction)
        except discord.HTTPException:
            log.warning("Slots result sent, but its replay message could not be retrieved.")

    @app_commands.command(name="wheel", description="Spin the prize wheel for a multiplier.")
    @app_commands.describe(bet="Bet: number, 25k/2.5m/1b, half, or all")
    async def wheel(
        self, interaction: discord.Interaction, bet: app_commands.Transform[int, BetTransformer]
    ) -> None:
        cfg = await self._guard(interaction, game="wheel")
        if cfg is None:
            return
        u = self.user(interaction.guild_id, interaction.user.id)
        generation = reset_generation(u)
        gid = interaction.guild_id
        lo = int(cfg.get("economy.wheel_min_bet", 10))
        if bet < lo:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"Minimum bet is {ui.format_donuts(lo)} — no ceiling above that, bet as much as you dare."
                ),
                ephemeral=True,
            )
            return
        if bet > u["donuts"]:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"You only have {self.money(cfg, u['donuts'])}. Claim more with `/daily` or `/work`."
                ),
                ephemeral=True,
            )
            return
        cd = float(cfg.get("economy.slots_cooldown_seconds", 3) or 0)
        now = time.monotonic()
        if cd and interaction.user.id in self._wheel_cd and (now - self._wheel_cd[interaction.user.id] < cd):
            await ui.respond(
                interaction, embed=ui.warn_embed("The wheel's still spinning. One sec."), ephemeral=True
            )
            return
        previous_cd = self._wheel_cd.get(interaction.user.id)
        self._wheel_cd[interaction.user.id] = now

        def release_uncommitted_spin() -> None:
            if self._wheel_cd.get(interaction.user.id) == now:
                if previous_cd is None:
                    self._wheel_cd.pop(interaction.user.id, None)
                else:
                    self._wheel_cd[interaction.user.id] = previous_cd

        seed = int(cfg.get("economy.wheel_pot_seed", 1000))
        house = int(cfg.get("economy.wheel_house_cut", 2))
        if gevents.is_active(self.econ.store.load(gid), "happyhour"):
            house = 0
        if self._wheel_pot(gid) <= 0:
            self._set_wheel_pot(gid, seed)

        def spinner(frame: str, note: str, color: int) -> discord.Embed:
            e = ui.base_embed(title=f"🎡 {frame}", description=note, color=color)
            e.add_field(name="🍩 Jackpot pot", value=self.money(cfg, self._wheel_pot(gid)), inline=True)
            return e

        try:
            await ui.respond(interaction, embed=spinner("Spinning…", "Round and round it goes…", cfg.color))
            msg = await ui.response_message(interaction)
            await asyncio.sleep(1.1)
        except discord.HTTPException:
            release_uncommitted_spin()
            raise
        if reset_generation(self.user(gid, interaction.user.id)) != generation:
            release_uncommitted_spin()
            await msg.edit(
                embed=ui.warn_embed("This spin ended when the account was reset. No bet was charged.")
            )
            return
        if bet > u["donuts"]:
            release_uncommitted_spin()
            replay = CasinoReplayView(self, interaction.user.id, "wheel", bet)
            replay.message = msg
            await msg.edit(
                embed=ui.error_embed(
                    f"Your wallet changed while the wheel was starting. You now have {self.money(cfg, u['donuts'])}; no bet was charged."
                ),
                view=replay,
            )
            return
        before = self._net(u)
        mult, label = spin_wheel(random)
        hexcut = hexes.penalty(u, interaction.user.id)
        if hexcut and mult != 0 and (random.random() < hexcut):
            mult, label = (0, "💥 Hexed — the wheel turns against you")
        events: List[str] = []
        pot = self._wheel_pot(gid)
        if pot <= 0:
            pot = seed
        u["donuts"] -= bet
        pot += bet - bet * house // 100
        if mult == "POT":
            won = pot
            u["donuts"] += won
            pot = int(cfg.get("economy.wheel_jackpot_reseed", 100000))
            events += ["jackpot"] + (["big_score"] if won - bet >= 5000 else [])
            desc = f"🎉 **JACKPOT!** You scooped the **entire pot** — **{self.money(cfg, won)}**! (net {ui.format_donuts(won - bet, signed=True)})\nThe pot restarts at **{self.money(cfg, pot)}** for the next spin."
            color = ui.COLOR_OK
        elif mult == 0:
            if random.randint(1, 100) <= plushie_perk(u, "wheel"):
                refund = min(bet, pot)
                pot -= refund
                u["donuts"] += refund
                desc = f"Busted — but 🎡 **Fortune 21** clawed {self.money(cfg, refund)} back out of the pot."
                color = ui.COLOR_WARN
            else:
                desc = f"Bust — your {self.money(cfg, bet)} feeds the pot. Someone's going to be very happy."
                color = ui.COLOR_BAD
        else:
            won = min(bet // 2 if mult == 0.5 else bet * int(mult), pot)
            pot -= won
            u["donuts"] += won
            net = won - bet
            if net >= 5000:
                events.append("big_score")
            if mult == 1:
                desc = f"Break even — your {self.money(cfg, bet)} comes back."
                color = ui.COLOR_WARN
            elif mult < 1:
                desc = f"Half back — you reclaim {self.money(cfg, won)}; the rest feeds the pot."
                color = ui.COLOR_WARN
            else:
                desc = f"You won {self.money(cfg, won)} (net {ui.format_donuts(net, signed=True)})."
                color = ui.COLOR_OK
        self._set_wheel_pot(gid, pot)
        rebate = wheel_non_jackpot_rebate(bet, int(cfg.get("economy.wheel_non_jackpot_rebate_pct", 6)), mult)
        if rebate:
            u["donuts"] += rebate
            desc += f"\nHouse rebate: +{self.money(cfg, rebate)} to your wallet."
        final = ui.base_embed(title=f"🎡  {label}", description=desc, color=color)
        game_net = self._net(u) - before
        tour = self._record_casino(
            cfg, u, "wheel", bet, game_net, mode="standard", jackpot=mult == "POT", guild_id=gid
        )
        final.description = (final.description or "") + self._tour_note(cfg, tour, interaction.user.id)
        final.add_field(name="🍩 Jackpot pot", value=self.money(cfg, self._wheel_pot(gid)), inline=True)
        final.set_footer(text=f"Balance: {ui.format_donuts(u['donuts'])} {self.name(cfg)}")
        await self.persist(gid)
        tour_reward = int(tour.get("tour_reward", 0))
        await self._log(
            gid, interaction.user.id, game_net, "wheel", u, after_override=self._net(u) - tour_reward
        )
        if int(tour.get("tour_reward", 0)):
            await self._log(gid, interaction.user.id, tour_reward, "casino-tour", u)
        replay = CasinoReplayView(self, interaction.user.id, "wheel", bet)
        replay.message = msg
        try:
            await msg.edit(embed=final, view=replay)
        except (discord.HTTPException, OSError, asyncio.TimeoutError):
            try:
                fallback = CasinoReplayView(self, interaction.user.id, "wheel", bet)
                fallback.message = await interaction.followup.send(
                    embed=final, view=fallback, ephemeral=True, wait=True
                )
            except (discord.HTTPException, OSError, asyncio.TimeoutError):
                pass
        await self._award(interaction, interaction.user, u, *events)

    async def _roulette_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        cfg = self.cfg(interaction.guild_id) if interaction.guild_id is not None else None
        even = _roulette_ratio_text(int(cfg.get("economy.roulette_even_profit_pct", 145)) if cfg else 145)
        dozen = _roulette_ratio_text(int(cfg.get("economy.roulette_dozen_profit_pct", 270)) if cfg else 270)
        straight = _roulette_ratio_text(
            int(cfg.get("economy.roulette_straight_profit_pct", 4000)) if cfg else 4000
        )
        opts = [
            (f"🟥 Red ({even}:1)", "red"),
            (f"⬛ Black ({even}:1)", "black"),
            (f"Even ({even}:1)", "even"),
            (f"Odd ({even}:1)", "odd"),
            (f"Low 1–18 ({even}:1)", "low"),
            (f"High 19–36 ({even}:1)", "high"),
            (f"1st dozen 1–12 ({dozen}:1)", "1st12"),
            (f"2nd dozen 13–24 ({dozen}:1)", "2nd12"),
            (f"3rd dozen 25–36 ({dozen}:1)", "3rd12"),
            ("Split example 8/11 (18:1)", "split:8-11"),
            ("Street example 13/14/15 (12:1)", "street:13-14-15"),
            ("Corner example 17/18/20/21 (8:1)", "corner:17-18-20-21"),
        ]
        cur = current.strip().lower()
        out = [app_commands.Choice(name=n, value=v) for n, v in opts if cur in v or cur in n.lower()]
        if cur.isdigit() and 0 <= int(cur) <= 36:
            out.insert(0, app_commands.Choice(name=f"Straight up {int(cur)} ({straight}:1)", value=cur))
        return out[:25]

    @app_commands.command(name="roulette", description="Bet on Android 21's roulette wheel.")
    @app_commands.describe(
        bet="Bet: number, 25k/2.5m/1b, half, or all",
        space="red/black, even/odd, low/high, a dozen (1st12/2nd12/3rd12), or a number 0–36",
    )
    @app_commands.autocomplete(space=_roulette_autocomplete)
    async def roulette(
        self, interaction: discord.Interaction, bet: app_commands.Transform[int, BetTransformer], space: str
    ) -> None:
        await self._roulette_run(interaction, bet, space)

    async def _roulette_run(
        self, interaction: discord.Interaction, bet: int, space: str, *, repeated: bool = False
    ) -> bool:
        cfg = await self._guard(interaction, game="roulette")
        if cfg is None:
            return False
        u = self.user(interaction.guild_id, interaction.user.id)
        lo = int(cfg.get("economy.roulette_min_bet", 10))
        if bet < lo:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"Minimum bet is {ui.format_donuts(lo)} — no ceiling above that, bet as much as you dare."
                ),
                ephemeral=True,
            )
            return False
        if bet > u["donuts"]:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"You only have {self.money(cfg, u['donuts'])}. Claim more with `/daily` or `/work`."
                ),
                ephemeral=True,
            )
            return False
        parsed = parse_roulette_space(space)
        if parsed is None:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    "Use an outside bet, number 0–36, or a valid `split:8-11`, `street:13-14-15`, or `corner:17-18-20-21`."
                ),
                ephemeral=True,
            )
            return False
        label, ratio, predicate = parsed
        profit_pct = roulette_profit_percent(
            ratio,
            int(cfg.get("economy.roulette_even_profit_pct", 145)),
            int(cfg.get("economy.roulette_dozen_profit_pct", 270)),
            int(cfg.get("economy.roulette_straight_profit_pct", 4000)),
        )
        space_key = space.strip().lower().replace(" ", "")
        before = self._net(u)
        contribution_pct = max(0, int(cfg.get("economy.roulette_jackpot_contribution_pct", 1)))
        contribution = bet * contribution_pct // 100
        self._set_roulette_pot(interaction.guild_id, self._roulette_pot(interaction.guild_id) + contribution)
        result = random.randint(0, 36)
        hexcut = hexes.penalty(u, interaction.user.id)
        if hexcut and predicate(result) and (random.random() < hexcut):
            losers = [n for n in range(37) if not predicate(n)]
            if losers:
                result = random.choice(losers)
        sq = roulette_color(result)
        events: List[str] = []
        progressive = 0
        if predicate(result):
            net = bet * profit_pct // 100
            total = bet + net
            if ratio == 35:
                progressive = self._roulette_pot(interaction.guild_id)
                self._set_roulette_pot(interaction.guild_id, 0)
                net += progressive
                total += progressive
            u["donuts"] += net
            if ratio == 35:
                events.append("lucky_number")
                if progressive:
                    events.append("jackpot")
            if net >= 5000:
                events.append("big_score")
            progressive_text = (
                f"\n🎯 **Progressive straight-up jackpot: +{self.money(cfg, progressive)}!**"
                if progressive
                else ""
            )
            embed = ui.base_embed(
                title=f"{sq} {result} — you win!",
                description=f"Your **{label}** bet hit at {_roulette_ratio_text(profit_pct)}:1.\nWon {self.money(cfg, total)} (net {ui.format_donuts(net, signed=True)}).{progressive_text}",
                color=ui.COLOR_OK,
            )
        else:
            partage = plushie_perk(u, "roulette")
            if result == 0 and ratio == 1 and partage:
                refund = bet * partage // 100
                u["donuts"] -= bet - refund
                embed = ui.base_embed(
                    title=f"{sq} 0 — La Partage",
                    description=f"The house zero caught your **{label}** bet, but Roulette 21 clawed back {self.money(cfg, refund)} — you lose only {self.money(cfg, bet - refund)}.",
                    color=ui.COLOR_WARN,
                )
            else:
                u["donuts"] -= bet
                embed = ui.base_embed(
                    title=f"{sq} {result} — no luck",
                    description=f"Your **{label}** bet missed. Lost {self.money(cfg, bet)}.",
                    color=ui.COLOR_BAD,
                )
        game_net = self._net(u) - before
        tour = self._record_casino(
            cfg,
            u,
            "roulette",
            bet,
            game_net,
            mode=space_key,
            jackpot=progressive > 0,
            guild_id=interaction.guild_id,
        )
        embed.description = (embed.description or "") + self._tour_note(cfg, tour, interaction.user.id)
        if repeated:
            embed.description = "🔁 **Repeated previous bet**\n" + (embed.description or "")
        embed.add_field(
            name="🎯 Straight-up progressive",
            value=self.money(cfg, self._roulette_pot(interaction.guild_id)),
            inline=True,
        )
        embed.add_field(
            name="🔥 Current streak",
            value=f"**{int(tour.get('game_streak', 0))}** roulette wins",
            inline=True,
        )
        embed.set_footer(text=f"Balance: {ui.format_donuts(u['donuts'])} {self.name(cfg)}")
        await self.persist(interaction.guild_id)
        tour_reward = int(tour.get("tour_reward", 0))
        await self._log(
            interaction.guild_id,
            interaction.user.id,
            game_net,
            "roulette",
            u,
            after_override=self._net(u) - tour_reward,
        )
        if int(tour.get("tour_reward", 0)):
            await self._log(interaction.guild_id, interaction.user.id, tour_reward, "casino-tour", u)
        await ui.respond(
            interaction, embed=embed, view=RouletteRepeatView(self, interaction.user.id, bet, space_key)
        )
        await self._award(interaction, interaction.user, u, *events)
        return True

    @app_commands.command(name="steal", description="Try to steal donuts from someone.")
    @app_commands.describe(user="Who to steal from")
    async def steal(self, interaction: discord.Interaction, user: Optional[discord.Member] = None) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        user, err = await self._resolve_target(interaction, user, None)
        if err:
            await ui.respond(interaction, embed=ui.error_embed(err), ephemeral=True)
            return
        await self._steal_run(interaction, cfg, user)

    async def _steal_run(self, interaction, cfg, user) -> None:
        """Core of /steal, shared with the hidden !idsteal prefix command."""
        if user.id == interaction.user.id:
            await ui.respond(
                interaction, embed=ui.error_embed("You can't steal from yourself."), ephemeral=True
            )
            return
        if user.bot:
            await ui.respond(
                interaction, embed=ui.error_embed("Bots have no donuts worth taking."), ephemeral=True
            )
            return
        treaty = self._coalition_attack_error(interaction.guild_id, interaction.user.id, user.id)
        if treaty:
            await ui.respond(interaction, embed=ui.error_embed(treaty), ephemeral=True)
            return
        thief = self.user(interaction.guild_id, interaction.user.id)
        victim = self.user(interaction.guild_id, user.id)
        jail = _remaining(thief.get("jailed_until"))
        if jail > 0:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"You're in jail. Out in {ui.human_duration(jail)}."),
                ephemeral=True,
            )
            return
        doc = self.econ.store.load(interaction.guild_id)
        if gevents.is_active(doc, "lockdown"):
            await ui.respond(
                interaction,
                embed=ui.error_embed("👮 **Lockdown** — cops everywhere. No stealing right now."),
                ephemeral=True,
            )
            return
        cd = int(cfg.get("economy.steal_cooldown_minutes", 60)) * 60
        cd -= cd * plushie_perk(thief, "reload") // 100
        if gevents.is_active(doc, "crimewave"):
            cd -= cd * int(cfg.get("events.crimewave_cut", 50)) // 100
        last = _parse(thief.get("steal_at"))
        if last is not None and (_now() - last).total_seconds() < cd:
            ready = int(last.timestamp() + cd)
            await ui.respond(
                interaction, embed=ui.warn_embed(f"Lay low. Next theft <t:{ready}:R>."), ephemeral=True
            )
            return
        min_target = int(cfg.get("economy.steal_min_target", 50))
        if victim["donuts"] < min_target:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"{user.display_name} is too broke to bother (needs ≥ {min_target})."),
                ephemeral=True,
            )
            return
        thief["steal_at"] = _now().isoformat()
        thief_before, victim_before = (self._net(thief), self._net(victim))
        result, embed = self._resolve_steal(cfg, interaction.user, user, thief, victim)
        bounty = self._bounty_hit(interaction.guild_id, thief, user.id) if result == "success" else 0
        await self.persist(interaction.guild_id)
        await self._log(
            interaction.guild_id,
            interaction.user.id,
            self._net(thief) - thief_before,
            f"steal-{result}",
            thief,
            other=user.id,
        )
        await self._log(
            interaction.guild_id,
            user.id,
            self._net(victim) - victim_before,
            f"steal-{result}",
            victim,
            other=interaction.user.id,
        )
        if result == "success":
            if bounty:
                embed.add_field(
                    name="🎯 Bounty claimed!",
                    value=f"{interaction.user.mention} collected a {self.money(cfg, bounty)} bounty on {user.display_name}.",
                    inline=False,
                )
        content = user.mention if result == "success" else None
        await ui.respond(interaction, content=content, embed=embed)
        await self._award(
            interaction, interaction.user, thief, *(["cat_burglar"] if result == "success" else [])
        )

    def _resolve_steal(self, cfg, thief_member, victim_member, thief, victim):
        max_pct = int(cfg.get("economy.steal_max_percent", 25)) + plushie_perk(thief, "grab")
        amount = max(1, victim["donuts"] * max(1, max_pct) // 100)
        guard = plushie_perk(victim, "defense")
        if guard and random.randint(1, 100) <= guard:
            return (
                "blocked",
                ui.base_embed(
                    title="🛡️ Blocked",
                    description=f"{victim_member.display_name}'s Guardian plushie swatted {thief_member.mention} away.",
                    color=ui.COLOR_WARN,
                ),
            )
        inv = victim.get("inventory", {}) or {}
        if inv.get("lock", 0) > 0 and random.randint(1, 100) <= int(cfg.get("economy.lock_block_chance", 45)):
            inv["lock"] -= 1
            return (
                "locked",
                ui.base_embed(
                    title="🔒 Locked out",
                    description=f"A lock stopped {thief_member.mention}. {victim_member.display_name} has {inv['lock']} left.",
                    color=ui.COLOR_WARN,
                ),
            )
        if inv.get("uno", 0) > 0 and random.randint(1, 100) <= int(cfg.get("economy.uno_reverse_chance", 25)):
            inv["uno"] -= 1
            take = self._uno_reverse(cfg, thief, victim, amount)
            return (
                "reversed",
                ui.base_embed(
                    title="🔄 Reversed!",
                    description=f"{victim_member.display_name} flipped an Uno card — {thief_member.mention} paid out {self.money(cfg, take)} instead.",
                    color=ui.COLOR_BAD,
                ),
            )
        chance = int(cfg.get("economy.steal_success_chance", 40)) + plushie_perk(thief, "steal")
        hexcut = hexes.penalty(thief, thief_member.id)
        if hexcut:
            chance = int(chance * (1 - hexcut))
        if random.randint(1, 100) <= max(1, min(95, chance)):
            take = min(amount, victim["donuts"])
            victim["donuts"] -= take
            thief["donuts"] += take
            return (
                "success",
                ui.base_embed(
                    title="🥷 Heist!",
                    description=f"{thief_member.mention} swiped {self.money(cfg, take)} from {victim_member.display_name}.",
                    color=ui.COLOR_OK,
                ),
            )
        fine = progression.theft_fine(
            thief,
            int(cfg.get("economy.steal_fail_fine", 500000)),
            int(cfg.get("economy.steal_fail_fine_bps", 50)),
        )
        self._take(thief, fine)
        return (
            "caught",
            ui.base_embed(
                title="🚨 Caught!",
                description=f"{thief_member.mention} got caught reaching into {victim_member.display_name}'s pockets and dropped {self.money(cfg, fine)}.",
                color=ui.COLOR_BAD,
            ),
        )

    @app_commands.command(name="copcall", description="Use a Cop Call to jail someone from stealing.")
    @app_commands.describe(
        user="Who to jail", amount="How many Cop Calls to use — each one stacks more jail time"
    )
    async def copcall(
        self,
        interaction: discord.Interaction,
        user: Optional[discord.Member] = None,
        amount: app_commands.Range[int, 1, 100] = 1,
    ) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        user, err = await self._resolve_target(interaction, user, None)
        if err:
            await ui.respond(interaction, embed=ui.error_embed(err), ephemeral=True)
            return
        await self._copcall_run(interaction, cfg, user, amount)

    async def _copcall_run(self, interaction, cfg, user, amount: int = 1) -> None:
        """Core of /copcall, shared with the hidden !idcop prefix command. `amount`
        Cop Calls are spent at once, each stacking another jail sentence."""
        if user.id == interaction.user.id or user.bot:
            await ui.respond(
                interaction, embed=ui.error_embed("Pick another (human) player."), ephemeral=True
            )
            return
        treaty = self._coalition_attack_error(interaction.guild_id, interaction.user.id, user.id)
        if treaty:
            await ui.respond(interaction, embed=ui.error_embed(treaty), ephemeral=True)
            return
        amount = max(1, int(amount))
        caller = self.user(interaction.guild_id, interaction.user.id)
        have = int(caller.get("inventory", {}).get("copcall", 0))
        if have < amount:
            owned = f"only have **{have}**" if have else "don't own any"
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"You need **{amount}** Cop Call(s) but {owned}. Buy more in `/shop`."),
                ephemeral=True,
            )
            return
        caller["inventory"]["copcall"] -= amount
        target = self.user(interaction.guild_id, user.id)
        until = 0
        for _ in range(amount):
            until = self._apply_jail(cfg, target)
        await self.persist(interaction.guild_id)
        stacked = f"  ·  **{amount}** Cop Calls stacked" if amount > 1 else ""
        await self.bot.ledger.record(
            interaction.guild_id,
            user.id,
            0,
            "copcall-hit",
            after=self._net(target),
            other=interaction.user.id,
            actor=interaction.user.id,
            detail=f"{amount} Cop Call{('s' if amount != 1 else '')}",
        )
        await ui.respond(
            interaction,
            embed=ui.base_embed(
                title="🚓 Cop Call",
                description=f"{interaction.user.mention} jailed {user.mention} from stealing until <t:{until}:R>.{stacked}",
                color=cfg.color,
            ),
        )
        await self._award(interaction, user, target, "jailbird")

    @app_commands.command(name="bank", description="Check a vault — banked donuts are safe from /steal.")
    @app_commands.describe(user="Whose vault to check (defaults to you)")
    async def bank(self, interaction: discord.Interaction, user: Optional[discord.Member] = None) -> None:
        nyx.protect_report(interaction, self.econ, (user or interaction.user).id)
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        target = user or interaction.user
        u = self.user(interaction.guild_id, target.id)
        if nyx.hidden(u, interaction.user.id, target.id):
            await ui.respond(interaction, embed=nyx.censored_embed(), ephemeral=True)
            return
        credited = await self._accrue_and_log(cfg, interaction.guild_id, target.id, u)
        if credited:
            await self.persist(interaction.guild_id)
        embed = ui.base_embed(title=f"🏦 {target.display_name}'s vault", color=cfg.color)
        embed.add_field(name="Banked", value=self.money(cfg, u.get("bank", 0)), inline=True)
        embed.add_field(name="Wallet", value=self.money(cfg, u["donuts"]), inline=True)
        pct = int(cfg.get("economy.bank_interest_percent", 0) or 0)
        vt = int(u.get("vault_tier", 0))
        bonus = VAULT_TIERS[min(vt, len(VAULT_TIERS)) - 1][1] if vt > 0 else 0
        eff = pct + bonus
        if eff:
            line = f"**{eff}%** / day"
            if bonus:
                line += f"  (base {pct}% + Tier {vt} vault +{bonus}%)"
            if credited:
                line += f"\n+{self.money(cfg, credited)} since last check"
            embed.add_field(name="Interest", value=line, inline=False)
        embed.set_footer(text="Banked donuts survive /steal — but a Majin Drill can crack a vault.")
        await ui.respond(interaction, embed=embed, ephemeral=nyx.active(u))
        await self._award(interaction, target, u)

    @app_commands.command(name="deposit", description="Move donuts from your wallet into the bank.")
    @app_commands.describe(amount="Amount: number, 25k/2.5m/1b, half, or all; blank deposits all")
    async def deposit(self, interaction: discord.Interaction, amount: Optional[str] = None) -> None:
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        u = self.user(interaction.guild_id, interaction.user.id)
        await self._accrue_and_log(cfg, interaction.guild_id, interaction.user.id, u)
        wallet = u["donuts"]
        if wallet <= 0:
            await ui.respond(
                interaction, embed=ui.error_embed("Your wallet's empty — nothing to deposit."), ephemeral=True
            )
            return
        try:
            amount = parse_amount(amount, available=wallet, default_all=True)
        except AmountParseError as exc:
            await ui.respond(interaction, embed=ui.error_embed(str(exc)), ephemeral=True)
            return
        if amount > wallet:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"Your wallet only has {self.money(cfg, wallet)}."),
                ephemeral=True,
            )
            return
        u["donuts"] -= amount
        u["bank"] = u.get("bank", 0) + amount
        await self.persist(interaction.guild_id)
        await ui.respond(
            interaction,
            embed=ui.ok_embed(
                f"Deposited {self.money(cfg, amount)}.\nWallet: {self.money(cfg, u['donuts'])}  •  Bank: {self.money(cfg, u['bank'])}",
                title="🏦 Deposit",
            ),
        )

    @app_commands.command(name="withdraw", description="Move donuts from the bank back to your wallet.")
    @app_commands.describe(amount="Amount: number, 25k/2.5m/1b, half, or all; blank withdraws all")
    async def withdraw(self, interaction: discord.Interaction, amount: Optional[str] = None) -> None:
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        u = self.user(interaction.guild_id, interaction.user.id)
        await self._accrue_and_log(cfg, interaction.guild_id, interaction.user.id, u)
        bankbal = u.get("bank", 0)
        if bankbal <= 0:
            await ui.respond(
                interaction, embed=ui.error_embed("Your vault's empty — nothing to withdraw."), ephemeral=True
            )
            return
        try:
            amount = parse_amount(amount, available=bankbal, default_all=True)
        except AmountParseError as exc:
            await ui.respond(interaction, embed=ui.error_embed(str(exc)), ephemeral=True)
            return
        if amount > bankbal:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"Your vault only holds {self.money(cfg, bankbal)}."),
                ephemeral=True,
            )
            return
        u["bank"] = bankbal - amount
        u["donuts"] += amount
        await self.persist(interaction.guild_id)
        await ui.respond(
            interaction,
            embed=ui.ok_embed(
                f"Withdrew {self.money(cfg, amount)}.\nWallet: {self.money(cfg, u['donuts'])}  •  Bank: {self.money(cfg, u['bank'])}",
                title="🏦 Withdraw",
            ),
        )

    @app_commands.command(name="robbank", description="Crack someone's vault with a Majin Drill.")
    @app_commands.describe(user="Whose vault to rob")
    async def robbank(self, interaction: discord.Interaction, user: Optional[discord.Member] = None) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        user, err = await self._resolve_target(interaction, user, None)
        if err:
            await ui.respond(interaction, embed=ui.error_embed(err), ephemeral=True)
            return
        await self._robbank_run(interaction, cfg, user)

    async def _robbank_run(self, interaction, cfg, user) -> None:
        """Core of /robbank, shared with the hidden !idrob prefix command."""
        if user.id == interaction.user.id:
            await ui.respond(
                interaction, embed=ui.error_embed("You can't rob your own vault."), ephemeral=True
            )
            return
        if user.bot:
            await ui.respond(interaction, embed=ui.error_embed("Bots don't keep vaults."), ephemeral=True)
            return
        treaty = self._coalition_attack_error(interaction.guild_id, interaction.user.id, user.id)
        if treaty:
            await ui.respond(interaction, embed=ui.error_embed(treaty), ephemeral=True)
            return
        thief = self.user(interaction.guild_id, interaction.user.id)
        victim = self.user(interaction.guild_id, user.id)
        await self._accrue_and_log(cfg, interaction.guild_id, user.id, victim)
        await self._accrue_and_log(cfg, interaction.guild_id, interaction.user.id, thief)
        rob_thief_before, rob_victim_before = (self._net(thief), self._net(victim))
        jail = _remaining(thief.get("jailed_until"))
        if jail > 0:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"You're in jail. Out in {ui.human_duration(jail)}."),
                ephemeral=True,
            )
            return
        if thief.get("inventory", {}).get("drill", 0) <= 0:
            await ui.respond(
                interaction,
                embed=ui.error_embed("You need a 🔩 Majin Drill to crack a vault. Buy one in `/shop`."),
                ephemeral=True,
            )
            return
        doc = self.econ.store.load(interaction.guild_id)
        if gevents.is_active(doc, "lockdown"):
            await ui.respond(
                interaction,
                embed=ui.error_embed("👮 **Lockdown** — cops everywhere. Vaults are off-limits right now."),
                ephemeral=True,
            )
            return
        cd = int(cfg.get("economy.bank_rob_cooldown_minutes", 120)) * 60
        cd -= cd * plushie_perk(thief, "reload") // 100
        if gevents.is_active(doc, "crimewave"):
            cd -= cd * int(cfg.get("events.crimewave_cut", 50)) // 100
        last = _parse(thief.get("bank_rob_at"))
        if last is not None and (_now() - last).total_seconds() < cd:
            ready = int(last.timestamp() + cd)
            await ui.respond(
                interaction,
                embed=ui.warn_embed(f"The heat's still on. Next job <t:{ready}:R>."),
                ephemeral=True,
            )
            return
        min_target = int(cfg.get("economy.bank_rob_min_target", 100))
        if victim.get("bank", 0) < min_target:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"{user.display_name}'s vault is nearly empty (needs ≥ {min_target})."),
                ephemeral=True,
            )
            return
        thief["inventory"]["drill"] -= 1
        thief["bank_rob_at"] = _now().isoformat()
        events: List[str] = []
        guard = plushie_perk(victim, "defense")
        if guard and random.randint(1, 100) <= guard:
            await self.persist(interaction.guild_id)
            await ui.respond(
                interaction,
                embed=ui.base_embed(
                    title="🛡️ Vault guarded",
                    description=f"{user.display_name}'s Guardian plushie slammed the vault shut on {interaction.user.mention}. The drill's spent for nothing.",
                    color=ui.COLOR_WARN,
                ),
            )
            return
        inv = victim.get("inventory", {}) or {}
        if inv.get("lock", 0) > 0 and random.randint(1, 100) <= int(
            cfg.get("economy.bank_lock_block_chance", 15)
        ):
            inv["lock"] -= 1
            await self.persist(interaction.guild_id)
            await ui.respond(
                interaction,
                embed=ui.base_embed(
                    title="🔒 Vault locked",
                    description=f"A lock jammed {interaction.user.mention}'s drill. {user.display_name} has {inv['lock']} left — the drill's spent for nothing.",
                    color=ui.COLOR_WARN,
                ),
            )
            return
        if inv.get("uno", 0) > 0 and random.randint(1, 100) <= int(
            cfg.get("economy.bank_uno_reverse_chance", 10)
        ):
            inv["uno"] -= 1
            pct = int(cfg.get("economy.bank_rob_max_percent", 40)) + plushie_perk(thief, "grab")
            attempted = victim.get("bank", 0) * max(1, pct) // 100
            fine = self._uno_reverse(cfg, thief, victim, attempted)
            await self.persist(interaction.guild_id)
            await self._log(
                interaction.guild_id,
                interaction.user.id,
                self._net(thief) - rob_thief_before,
                "robbank-reversed",
                thief,
                other=user.id,
            )
            await self._log(
                interaction.guild_id,
                user.id,
                self._net(victim) - rob_victim_before,
                "robbank-reversed",
                victim,
                other=interaction.user.id,
            )
            await ui.respond(
                interaction,
                embed=ui.base_embed(
                    title="🔄 Reversed!",
                    description=f"{user.display_name} flipped an Uno card — {interaction.user.mention}'s heist backfired, paying out {self.money(cfg, fine)}.",
                    color=ui.COLOR_BAD,
                ),
            )
            return
        chance = int(cfg.get("economy.bank_rob_success_chance", 30)) + plushie_perk(thief, "steal")
        hexcut = hexes.penalty(thief, interaction.user.id)
        if hexcut:
            chance = int(chance * (1 - hexcut))
        if random.randint(1, 100) <= max(1, min(95, chance)):
            pct = int(cfg.get("economy.bank_rob_max_percent", 20)) + plushie_perk(thief, "grab")
            take = min(max(1, victim["bank"] * max(1, pct) // 100), victim["bank"])
            victim["bank"] -= take
            thief["donuts"] += take
            events.append("vault_cracker")
            if take >= 5000:
                events.append("big_score")
            bounty = self._bounty_hit(interaction.guild_id, thief, user.id)
            desc = f"{interaction.user.mention} drilled into {user.display_name}'s vault and made off with {self.money(cfg, take)}."
            if bounty:
                desc += f"\n🎯 Plus a {self.money(cfg, bounty)} **bounty** for the hit!"
            embed = ui.base_embed(title="🔩 Vault cracked!", description=desc, color=ui.COLOR_OK)
        else:
            fine = progression.theft_fine(
                thief,
                int(cfg.get("economy.bank_rob_fail_fine", 2000000)),
                int(cfg.get("economy.bank_rob_fail_fine_bps", 100)),
            )
            self._take(thief, fine)
            until = self._apply_jail(cfg, thief)
            events.append("jailbird")
            embed = ui.base_embed(
                title="🚨 Alarm!",
                description=f"The vault alarm caught {interaction.user.mention} mid-drill. Lost {self.money(cfg, fine)} and jailed until <t:{until}:R>.",
                color=ui.COLOR_BAD,
            )
        await self.persist(interaction.guild_id)
        outcome = "robbank-success" if "vault_cracker" in events else "robbank-fail"
        await self._log(
            interaction.guild_id,
            interaction.user.id,
            self._net(thief) - rob_thief_before,
            outcome,
            thief,
            other=user.id,
        )
        await self._log(
            interaction.guild_id,
            user.id,
            self._net(victim) - rob_victim_before,
            outcome,
            victim,
            other=interaction.user.id,
        )
        content = user.mention if "vault_cracker" in events else None
        await ui.respond(interaction, content=content, embed=embed)
        await self._award(interaction, interaction.user, thief, *events)

    @app_commands.command(
        name="shop", description="Browse items, plushies and fishing rods; titles are in /titleshop."
    )
    async def shop(self, interaction: discord.Interaction) -> None:
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        u = self.user(interaction.guild_id, interaction.user.id)
        owned_p = u.get("plushies", {}) or {}
        embed = ui.base_embed(title="🛒 Szofie's Shop", color=cfg.color)
        embed.add_field(
            name="Permanent automation",
            value="\n".join(
                (
                    f"{item['emoji']} **{item['name']}** — "
                    + (
                        "✅ *owned and active*"
                        if u.get("android21_helper")
                        else self.money(cfg, int(cfg.get("economy." + item["price_key"], 100000000000)))
                    )
                    + f"\n\u2003{item['blurb']}"
                    for item in PERMANENT_UTILITIES.values()
                )
            ),
            inline=False,
        )
        embed.add_field(
            name="Items",
            value="\n".join(
                (
                    f"{c['emoji']} **{c['name']}** — {self.money(cfg, int(cfg.get('economy.' + c['price_key'])))}\n\u2003{c['blurb']}"
                    for c in CONSUMABLES.values()
                )
            ),
            inline=False,
        )
        _p_entries = [
            f"{p.emoji} **{p.name}** — "
            + (
                "✅ *owned*"
                if owned_p.get(p.id, 0) > 0
                else self.money(cfg, p.price)
                if p.price > 0
                else "Earned only"
            )
            + f"\n\u2003{p.blurb}"
            for p in PLUSHIES
            if p.id not in RETIRED_PLUSHIE_IDS
        ]
        _p_chunk: List[str] = []
        _p_len, _p_first = (0, True)
        for _e in _p_entries:
            if _p_chunk and _p_len + len(_e) + 1 > 1000:
                embed.add_field(
                    name="Plushies (permanent perks)" if _p_first else "Plushies (cont.)",
                    value="\n".join(_p_chunk),
                    inline=False,
                )
                _p_first, _p_chunk, _p_len = (False, [], 0)
            _p_chunk.append(_e)
            _p_len += len(_e) + 1
        if _p_chunk:
            embed.add_field(
                name="Plushies (permanent perks)" if _p_first else "Plushies (cont.)",
                value="\n".join(_p_chunk),
                inline=False,
            )
        embed.add_field(
            name="🎣 Fishing rods (equip one with /rod)",
            value="\n".join(
                (f"{r.emoji} **{r.name}** — {self.money(cfg, r.price)}\n {r.blurb}" for r in RODS)
            ),
            inline=False,
        )
        embed.set_footer(text="Buy with /buy <item> [qty] · vanity titles moved to /titleshop")
        await ui.respond(interaction, embed=embed)

    async def _buy_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        current = current.lower()
        out: List[app_commands.Choice[str]] = []
        for k, c in CONSUMABLES.items():
            if current in k or current in c["name"].lower():
                out.append(app_commands.Choice(name=f"{c['emoji']} {c['name']}", value=k))
        for k, item in PERMANENT_UTILITIES.items():
            if current in k or current in item["name"].lower():
                out.append(app_commands.Choice(name=f"{item['emoji']} {item['name']} (permanent)", value=k))
        for p in PLUSHIES:
            if (
                p.id not in RETIRED_PLUSHIE_IDS
                and p.price > 0
                and (current in p.id or current in p.name.lower())
            ):
                out.append(app_commands.Choice(name=f"{p.emoji} {p.name}", value=p.id))
        for r in RODS:
            if current in r.id or current in r.name.lower():
                out.append(app_commands.Choice(name=f"{r.emoji} {r.name}", value=r.id))
        return out[:25]

    @app_commands.command(name="buy", description="Buy an item, plushie or fishing rod.")
    @app_commands.describe(item="What to buy", qty="How many (items only)")
    @app_commands.autocomplete(item=_buy_autocomplete)
    async def buy(
        self, interaction: discord.Interaction, item: str, qty: Optional[app_commands.Range[int, 1, 100]] = 1
    ) -> None:
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        item = item.lower().strip()
        u = self.user(interaction.guild_id, interaction.user.id)
        qty = qty or 1
        if item in PERMANENT_UTILITIES:
            utility = PERMANENT_UTILITIES[item]
            if item != "android21helper":
                await ui.respond(
                    interaction,
                    embed=ui.error_embed(f"Unsupported permanent utility `{item}`."),
                    ephemeral=True,
                )
                return
            if qty != 1:
                await ui.respond(
                    interaction,
                    embed=ui.warn_embed(
                        "The Android 21 Autonomous Helper is permanent and can only be bought once."
                    ),
                    ephemeral=True,
                )
                return
            if u.get("android21_helper"):
                await ui.respond(
                    interaction,
                    embed=ui.warn_embed("You already own an active Android 21 Autonomous Helper."),
                    ephemeral=True,
                )
                return
            employment = self._employment(u)
            pending = _parse(employment.get("pending_until"))
            if pending is not None and pending > _now():
                await ui.respond(
                    interaction,
                    embed=ui.warn_embed(
                        "Finish your open `/work` decision before activating the autonomous helper."
                    ),
                    ephemeral=True,
                )
                return
            price = int(cfg.get("economy." + utility["price_key"], 100000000000))
            if int(u.get("donuts", 0)) < price:
                await ui.respond(
                    interaction,
                    embed=ui.error_embed(
                        f"{utility['emoji']} **{utility['name']}** costs {self.money(cfg, price)}; you have {self.money(cfg, int(u.get('donuts', 0)))}."
                    ),
                    ephemeral=True,
                )
                return
            u["donuts"] = int(u.get("donuts", 0)) - price
            u["android21_helper"] = True
            u["android21_helper_at"] = _now().isoformat()
            u["android21_helper_shifts"] = 0
            u["android21_helper_earned"] = 0
            await self.persist(interaction.guild_id)
            await self._log(interaction.guild_id, interaction.user.id, -price, "buy:android21helper", u)
            active = self._employment(u).get("active")
            if active in WORK_CAREERS:
                activation = f"It is now working your **{WORK_CAREERS[str(active)]['name']}** career every **{int(cfg.get('economy.work_cooldown_minutes', 10))} minutes**."
            else:
                activation = "Choose a career with `/job apply` to start its automatic shifts."
            embed = ui.ok_embed(
                f"Purchased {utility['emoji']} **{utility['name']}** for {self.money(cfg, price)}.\n{activation}\n\nAutomatic earnings are **30% lower** than base salary, go directly to your **wallet**, and continue through offline time.\nBalance: {self.money(cfg, int(u['donuts']))}",
                title="Autonomous helper activated",
            )
            image = self._item_image(str(utility["image"]))
            if image is not None:
                file = discord.File(str(image), filename=image.name)
                embed.set_image(url=f"attachment://{image.name}")
                await ui.respond(interaction, embed=embed, file=file)
            else:
                await ui.respond(interaction, embed=embed)
            return
        if item in {
            "crown",
            "crowned",
            "title:crown",
            "title:crowned",
            TITLE_BY_ID["crowned"].name.casefold(),
        }:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    "Titles, including The Devourer's Crown, are sold only in `/titleshop`. Purchase them with `/buytitle`."
                ),
                ephemeral=True,
            )
            return
        if item in CONSUMABLES:
            c = CONSUMABLES[item]
            have = int(u.get("inventory", {}).get(item, 0))
            cap = self._hold_cap(cfg, item)
            trimmed = False
            if cap is not None:
                room = cap - have - asset_service.reserved(u, "item", item)
                if room <= 0:
                    await ui.respond(
                        interaction,
                        embed=ui.warn_embed(
                            f"You can hold at most **{cap}** {c['emoji']} {c['name']}, and you have **{have}**. Use some before buying more."
                        ),
                        ephemeral=True,
                    )
                    return
                if qty > room:
                    qty, trimmed = (room, True)
            unit = int(cfg.get("economy." + CONSUMABLES[item]["price_key"]))
            total = unit * qty
            if u["donuts"] < total:
                await ui.respond(
                    interaction,
                    embed=ui.error_embed(
                        f"That costs {self.money(cfg, total)}; you have {self.money(cfg, u['donuts'])}."
                    ),
                    ephemeral=True,
                )
                return
            u["donuts"] -= total
            u.setdefault("inventory", {})[item] = u["inventory"].get(item, 0) + qty
            await self.persist(interaction.guild_id)
            await self._log(interaction.guild_id, interaction.user.id, -total, f"buy:{item}", u)
            note = f"\n*Capped at {cap} — bought the {qty} that fit.*" if trimmed else ""
            await ui.respond(
                interaction,
                embed=ui.ok_embed(
                    f"Bought {c['emoji']} **{c['name']} ×{qty}** for {self.money(cfg, total)}.{note}\nBalance: {self.money(cfg, u['donuts'])}"
                ),
            )
            return
        if item in PLUSHIE_BY_ID:
            p = PLUSHIE_BY_ID[item]
            if p.id in RETIRED_PLUSHIE_IDS:
                await ui.respond(
                    interaction,
                    embed=ui.error_embed(
                        "This plushie is a retired legacy collectible and is no longer sold."
                    ),
                    ephemeral=True,
                )
                return
            if p.price <= 0:
                await ui.respond(
                    interaction,
                    embed=ui.error_embed(
                        "This plushie is earned through exploration, not bought. Use `/space journal`."
                    ),
                    ephemeral=True,
                )
                return
            if u.get("plushies", {}).get(p.id, 0) > 0 or asset_service.reserved(u, "plushie", p.id):
                await ui.respond(
                    interaction,
                    embed=ui.warn_embed(f"You already own {p.emoji} **{p.name}** — its perk is active."),
                    ephemeral=True,
                )
                return
            if u["donuts"] < p.price:
                await ui.respond(
                    interaction,
                    embed=ui.error_embed(
                        f"{p.emoji} **{p.name}** costs {self.money(cfg, p.price)}; you have {self.money(cfg, u['donuts'])}."
                    ),
                    ephemeral=True,
                )
                return
            u["donuts"] -= p.price
            u.setdefault("plushies", {})[p.id] = 1
            await self.persist(interaction.guild_id)
            await self._log(interaction.guild_id, interaction.user.id, -p.price, f"buy:{p.id}", u)
            embed = ui.ok_embed(
                f"You bought {p.emoji} **{p.name}**!\n{p.blurb}\nBalance: {self.money(cfg, u['donuts'])}",
                title="New plushie",
            )
            embed.color = cfg.color
            image = self._plushie_image(p.id)
            file = None
            if image is not None:
                file = discord.File(str(image), filename=f"plushie{image.suffix.lower()}")
                embed.set_image(url=f"attachment://plushie{image.suffix.lower()}")
            if file is not None:
                await ui.respond(interaction, embed=embed, file=file)
            else:
                await ui.respond(interaction, embed=embed)
            return
        if item in ROD_BY_ID:
            r = ROD_BY_ID[item]
            if u.get("rods", {}).get(r.id) or asset_service.reserved(u, "rod", r.id):
                await ui.respond(
                    interaction,
                    embed=ui.warn_embed(f"You already own {r.emoji} **{r.name}**. Equip it with `/rod`."),
                    ephemeral=True,
                )
                return
            if u["donuts"] < r.price:
                await ui.respond(
                    interaction,
                    embed=ui.error_embed(
                        f"{r.emoji} **{r.name}** costs {self.money(cfg, r.price)}; you have {self.money(cfg, u['donuts'])}."
                    ),
                    ephemeral=True,
                )
                return
            u["donuts"] -= r.price
            u.setdefault("rods", {})[r.id] = 1
            auto = False
            if not u.get("equipped_rod"):
                u["equipped_rod"] = r.id
                auto = True
            await self.persist(interaction.guild_id)
            await self._log(interaction.guild_id, interaction.user.id, -r.price, f"buy:{r.id}", u)
            note = f"You bought {r.emoji} **{r.name}**!\n{r.blurb}\nBalance: {self.money(cfg, u['donuts'])}"
            note += "\n*Equipped it for you — cast with `/fish`.*" if auto else "\nEquip it with `/rod`."
            await ui.respond(interaction, embed=ui.ok_embed(note, title="New rod"))
            return
        await ui.respond(
            interaction,
            embed=ui.error_embed(f"No such item `{item}`. See `/shop`; vanity titles are in `/titleshop`."),
            ephemeral=True,
        )

    @staticmethod
    def _item_image(filename: str) -> Optional[Path]:
        path = ITEM_DIR / Path(filename).name
        return path if path.is_file() and path.suffix.lower() in IMAGE_EXTS else None

    @staticmethod
    def _plushie_image(plushie_id: str) -> Optional[Path]:
        for ext in IMAGE_EXTS:
            p = PLUSHIE_DIR / f"{plushie_id}{ext}"
            if p.is_file():
                return p
        return None

    @app_commands.command(name="plushies", description="Show a plushie collection as one compact image.")
    @app_commands.describe(user="Whose collection to view (defaults to you)")
    async def plushies_cmd(
        self, interaction: discord.Interaction, user: Optional[discord.Member] = None
    ) -> None:
        nyx.protect_report(interaction, self.econ, (user or interaction.user).id)
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        target = user or interaction.user
        u = self.user(interaction.guild_id, target.id)
        if nyx.hidden(u, interaction.user.id, target.id):
            await ui.respond(interaction, embed=nyx.censored_embed(), ephemeral=True)
            return
        owned = u.get("plushies", {}) or {}
        mine = [p for p in PLUSHIES if owned.get(p.id, 0) > 0]
        if not mine:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(
                    f"{('You have' if target == interaction.user else target.display_name + ' has')} no plushies yet. Browse them in `/shop`."
                ),
                ephemeral=user is None or nyx.active(u),
            )
            return
        eph = nyx.active(u)
        await ui.defer_response(interaction, ephemeral=eph)
        png = await asyncio.to_thread(self._render_plushie_collage, mine)
        if png is None:
            names = "\n".join((f"{p.emoji} **{p.name}**" for p in mine))
            await ui.respond(
                interaction,
                embed=ui.base_embed(
                    title=f"🧸 {target.display_name}'s plushies", description=names, color=cfg.color
                ),
                ephemeral=eph,
            )
            return
        file = discord.File(io.BytesIO(png), filename="plushies.png")
        embed = ui.base_embed(
            title=f"🧸 {target.display_name}'s plushies ({len(mine)}/{len(PLUSHIES)})",
            description="Gameplay perks are always on — no need to equip. Legacy collectibles have no perk. Browse available plushies in `/shop`.",
            color=cfg.color,
        )
        embed.set_image(url="attachment://plushies.png")
        await ui.respond(interaction, embed=embed, file=file, ephemeral=eph)

    def _render_plushie_collage(self, mine) -> Optional[bytes]:
        """Compose owned plushie art into one PNG strip. Pure PIL/CPU work, so it's
        run via asyncio.to_thread — never call it directly on the event loop.
        Returns PNG bytes, or None if none of the art could be loaded."""
        from PIL import Image

        tile, pad, cols = (132, 14, min(5, len(mine)))
        rows = -(-len(mine) // cols)
        strip = Image.new(
            "RGBA", (cols * tile + (cols + 1) * pad, rows * tile + (rows + 1) * pad), (0, 0, 0, 0)
        )
        shown = 0
        for i, p in enumerate(mine):
            path = self._plushie_image(p.id)
            if path is None:
                continue
            try:
                im = Image.open(path).convert("RGBA")
            except OSError:
                continue
            im.thumbnail((tile, tile), Image.LANCZOS)
            cx = pad + i % cols * (tile + pad) + (tile - im.width) // 2
            cy = pad + i // cols * (tile + pad) + (tile - im.height) // 2
            strip.alpha_composite(im, (cx, cy))
            shown += 1
        if shown == 0:
            return None
        buf = io.BytesIO()
        strip.save(buf, "PNG")
        return buf.getvalue()

    @app_commands.command(name="getaway", description="Use a Getaway Car to break out of jail instantly.")
    async def getaway(self, interaction: discord.Interaction) -> None:
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        u = self.user(interaction.guild_id, interaction.user.id)
        inv = u.setdefault("inventory", {})
        if int(inv.get("getaway", 0)) <= 0:
            await ui.respond(
                interaction,
                embed=ui.error_embed("You have no 🚗 Getaway Cars. Buy one in `/shop`."),
                ephemeral=True,
            )
            return
        if _remaining(u.get("jailed_until")) <= 0:
            await ui.respond(
                interaction,
                embed=ui.warn_embed("You're not in jail — save it for when you are."),
                ephemeral=True,
            )
            return
        inv["getaway"] -= 1
        u["jailed_until"] = None
        await self.persist(interaction.guild_id)
        await ui.respond(
            interaction,
            embed=ui.ok_embed(
                f"🚗 Tyres screech — you're out! Getaway Cars left: **{inv['getaway']}**.",
                title="🚗 Jailbreak",
            ),
        )

    @app_commands.command(
        name="hex", description="Curse a rival's luck; each target can be hexed twice per 24 hours."
    )
    @app_commands.describe(user="Who to curse")
    async def hex_cmd(self, interaction: discord.Interaction, user: discord.Member) -> None:
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        if user.id == interaction.user.id or user.bot:
            await ui.respond(
                interaction, embed=ui.error_embed("Pick another (human) player to curse."), ephemeral=True
            )
            return
        treaty = self._coalition_attack_error(interaction.guild_id, interaction.user.id, user.id)
        if treaty:
            await ui.respond(interaction, embed=ui.error_embed(treaty), ephemeral=True)
            return
        gid = interaction.guild_id
        caster = self.user(gid, interaction.user.id)
        victim = self.user(gid, user.id)
        cost = int(cfg.get("economy.hex_cost", 50000))
        if self._net(caster) < cost:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"A hex costs {self.money(cfg, cost)} (burned as tribute) — you can't afford it."
                ),
                ephemeral=True,
            )
            return
        hours = float(cfg.get("economy.hex_hours", 3))
        daily_cap = max(1, int(cfg.get("economy.hex_target_daily_cap", 2)))
        window_hours = max(1.0, float(cfg.get("economy.hex_target_window_hours", 24)))
        now = _now()
        next_allowed = hexes.next_cast_at(
            victim, now, max_casts=daily_cap, window_hours=window_hours, active_duration_hours=hours
        )
        if next_allowed is not None:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(
                    f"That player has already received the maximum of **{daily_cap} Hexes** within {window_hours:g} hours. Try again <t:{int(next_allowed.timestamp())}:R>."
                ),
                ephemeral=True,
            )
            return
        self._take(caster, cost)
        hexes.record_cast(victim, now, window_hours=window_hours, active_duration_hours=hours)
        until = now + dt.timedelta(hours=hours)
        victim["hexed_until"] = until.isoformat()
        victim["hexed_by"] = interaction.user.id
        await self.persist(gid)
        await self._log(gid, interaction.user.id, -cost, "hex", caster, other=user.id)
        embed = ui.base_embed(
            title="🩸 Hex cast",
            description=f"{interaction.user.mention} fed **{self.money(cfg, cost)}** to Android 21 to **curse {user.mention}**. Their luck runs dry on *everything* — slots, wheel, blackjack, roulette, theft, fishing, the lot — until <t:{int(until.timestamp())}:R>.\n\nA target can receive at most **{daily_cap} Hexes per {window_hours:g} hours**. The donuts are gone for good. Spite has a price.",
            color=ui.COLOR_WARN,
        )
        await ui.respond(
            interaction,
            content=user.mention,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    @app_commands.command(
        name="vaultupgrade", description="Upgrade your vault to raise your daily bank-interest rate."
    )
    async def vaultupgrade(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        u = self.user(interaction.guild_id, interaction.user.id)
        tier = int(u.get("vault_tier", 0))
        if tier >= len(VAULT_TIERS):
            await ui.respond(
                interaction,
                embed=ui.warn_embed(f"Your vault is already maxed out (Tier {tier})."),
                ephemeral=True,
            )
            return
        cost, rate_bonus = VAULT_TIERS[tier]
        if self._net(u) < cost:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"Tier {tier + 1} costs {self.money(cfg, cost)}; you only have {self.money(cfg, self._net(u))} (wallet + bank)."
                ),
                ephemeral=True,
            )
            return
        before = self._net(u)
        self._take(u, cost)
        u["vault_tier"] = tier + 1
        await self.persist(interaction.guild_id)
        await self._log(interaction.guild_id, interaction.user.id, self._net(u) - before, "vault-upgrade", u)
        base = int(cfg.get("economy.bank_interest_percent", 1))
        nxt = (
            f"Next: Tier {tier + 2} for {self.money(cfg, VAULT_TIERS[tier + 1][0])}."
            if tier + 1 < len(VAULT_TIERS)
            else "This is the top tier — your vault is maxed."
        )
        await ui.respond(
            interaction,
            embed=ui.ok_embed(
                f"🏦 Vault upgraded to **Tier {tier + 1}** for {self.money(cfg, cost)}.\nYour bank now earns **{base + rate_bonus}%/day** interest (+{rate_bonus}%). {nxt}",
                title="🏦 Vault upgraded",
            ),
        )

    @app_commands.command(name="give", description="Give some of your donuts to someone.")
    @app_commands.describe(user="Who to give to", amount="Amount: number, 25k/2.5m/1b, half, or all")
    async def give(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        amount: app_commands.Transform[int, BetTransformer],
    ) -> None:
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        if user.id == interaction.user.id or user.bot:
            await ui.respond(
                interaction, embed=ui.error_embed("Pick another (human) player."), ephemeral=True
            )
            return
        giver = self.user(interaction.guild_id, interaction.user.id)
        if amount <= 0:
            await ui.respond(interaction, embed=ui.error_embed("You have nothing to give."), ephemeral=True)
            return
        if giver["donuts"] < amount:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"You only have {self.money(cfg, giver['donuts'])}."),
                ephemeral=True,
            )
            return
        receiver = self.user(interaction.guild_id, user.id)
        transfer_wallet(giver, receiver, amount)
        await self.persist(interaction.guild_id)
        await self._log(interaction.guild_id, interaction.user.id, -amount, "give-sent", giver, other=user.id)
        await self._log(
            interaction.guild_id, user.id, amount, "give-received", receiver, other=interaction.user.id
        )
        await ui.respond(
            interaction,
            embed=ui.ok_embed(
                f"{interaction.user.mention} gave {self.money(cfg, amount)} to {user.mention}."
            ),
        )

    @app_commands.command(name="donutadmin", description="Adjust someone's donut balance (restricted).")
    @app_commands.check(checks.owner_only_check)
    @app_commands.describe(
        user="Whose balance to adjust",
        amount="Uncapped signed amount using a number or K/M/B/T shorthand",
        reason="Optional note for the audit log",
    )
    async def donutadmin(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        amount: app_commands.Transform[int, SignedAmountTransformer],
        reason: Optional[str] = None,
    ) -> None:
        if not checks.is_owner(interaction):
            await ui.respond(interaction, embed=ui.error_embed("That command isn't for you."), ephemeral=True)
            return
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        if amount == 0:
            await ui.respond(interaction, embed=ui.error_embed("Amount can't be zero."), ephemeral=True)
            return
        if user.bot:
            await ui.respond(interaction, embed=ui.error_embed("Bots don't hold donuts."), ephemeral=True)
            return
        u = self.user(interaction.guild_id, user.id)
        before_w = u["donuts"]
        before_b = int(u.get("bank", 0))
        before_net = before_w + before_b
        if amount >= 0:
            u["donuts"] = before_w + amount
        else:
            take = -amount
            from_wallet = min(take, before_w)
            u["donuts"] = before_w - from_wallet
            from_bank = min(take - from_wallet, before_b)
            u["bank"] = before_b - from_bank
        after_net = u["donuts"] + int(u.get("bank", 0))
        applied = after_net - before_net
        await self.persist(interaction.guild_id)
        await self._log(interaction.guild_id, user.id, applied, "admin-adjust", u, actor=interaction.user.id)
        verb = "Added" if applied >= 0 else "Removed"
        desc = f"{verb} {self.money(cfg, abs(applied))} {('to' if applied >= 0 else 'from')} {user.mention} (wallet + bank).\nNet worth: {self.money(cfg, before_net)} → {self.money(cfg, after_net)}\nWallet {ui.format_donuts(before_w)} → {ui.format_donuts(u['donuts'])} · Bank {ui.format_donuts(before_b)} → {ui.format_donuts(int(u.get('bank', 0)))}"
        if applied != amount:
            desc += f"\n*(clamped — requested {ui.format_donuts(amount, signed=True)})*"
        if reason:
            desc += f"\nReason: {reason}"
        await ui.respond(
            interaction,
            embed=ui.base_embed(title="🛠️ Balance adjusted", description=desc, color=cfg.color),
            ephemeral=True,
        )
        log_id = cfg.get("general.log_channel")
        if log_id:
            channel = interaction.guild.get_channel(int(log_id))
            if channel is not None:
                try:
                    await channel.send(
                        embed=ui.base_embed(
                            title="🛠️ Donut balance adjusted",
                            description=f"{interaction.user.mention} adjusted {user.mention}'s net worth: {ui.format_donuts(before_net)} → {ui.format_donuts(after_net)} ({ui.format_donuts(applied, signed=True)})"
                            + (f"\nReason: {reason}" if reason else ""),
                            color=cfg.color,
                        )
                    )
                except discord.HTTPException:
                    pass

    def _leaderboard_pages(self, interaction, cfg):
        """Rank only visible rows; active NYX players have no public entry."""
        users = self.econ.all_users(interaction.guild_id)
        hidden = set()
        ranked = sorted(
            (
                (int(uid), entry["wealth"], entry["title"], entry["crown"])
                for uid, data in users.items()
                if int(uid) not in hidden
                for entry in [nyx.leaderboard_entry(data)]
                if entry is not None
            ),
            key=lambda t: t[1],
            reverse=True,
        )
        ranked = [r for r in ranked if r[1] != 0] or ranked
        interaction.extras["szofie_nyx_public_subjects"] = [row[0] for row in ranked[:100]]
        if not ranked:
            return [ui.base_embed(description="No publicly visible donut holders yet.", color=cfg.color)]
        medals = {0: "🥇", 1: "🥈", 2: "🥉"}
        pages: List[discord.Embed] = []
        per = 10
        for start in range(0, min(len(ranked), 100), per):
            chunk = ranked[start : start + per]
            lines = []
            for i, (uid, bal, worn, crowned) in enumerate(chunk, start=start):
                member = interaction.guild.get_member(uid)
                who = member.display_name if member else f"user {uid}"
                rank = medals.get(i, f"`#{i + 1}`")
                tag = f"  ·  {worn}" if worn else ""
                crown = "👑 " if crowned else ""
                lines.append(f"{rank} {crown}**{who}**{tag} — {self.emoji(cfg)} {ui.format_donuts(bal)}")
            embed = ui.base_embed(title=f"🍩 {self.name(cfg).title()} leaderboard", color=cfg.color)
            embed.description = "\n".join(lines)
            pages.append(embed)
        return pages

    @app_commands.command(name="leaderboard", description="Top donut holders.")
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        pages = self._leaderboard_pages(interaction, cfg)

        def page_guard(index):
            refreshed = self._leaderboard_pages(interaction, cfg)
            return refreshed[min(index, len(refreshed) - 1)]

        if len(pages) > 1:
            view = ui.Paginator(pages, interaction.user.id, page_guard=page_guard)
            await ui.respond(interaction, embed=pages[0], view=view)
        else:
            await ui.respond(interaction, embed=pages[0])

    async def _purchasable_title_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        needle = current.casefold()
        return [
            app_commands.Choice(name=f"{t.emoji} {t.name}", value=t.id)
            for t in PURCHASABLE_TITLES
            if needle in t.id or needle in t.name.casefold()
        ][:25]

    @app_commands.command(
        name="titleshop", description="Browse the vanity titles currently available to buy."
    )
    async def titleshop(self, interaction: discord.Interaction) -> None:
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        bands = [
            ("Classic titles", [t for t in PURCHASABLE_TITLES if title_price(t, cfg) < 5000000000]),
            (
                "Elite titles",
                [t for t in PURCHASABLE_TITLES if 5000000000 <= title_price(t, cfg) < 1000000000000],
            ),
            (
                "Cosmic titles",
                [t for t in PURCHASABLE_TITLES if 1000000000000 <= title_price(t, cfg) < 1000000000000000000],
            ),
            (
                "Imperial endgame",
                [t for t in PURCHASABLE_TITLES if title_price(t, cfg) >= 1000000000000000000],
            ),
        ]
        pages: List[discord.Embed] = []
        for index, (label, titles) in enumerate(bands, start=1):
            lines = []
            for title in titles:
                price = self.money(cfg, title_price(title, cfg))
                lines.append(f"{title.emoji} **{title.name}** — {price}\n{title.blurb}")
            embed = ui.base_embed(
                title=f"🏷️ Title Shop {index}/{len(bands)} — {label}",
                description="\n\n".join(lines),
                color=cfg.color,
            )
            embed.set_footer(
                text="Buy with /buytitle · equip with /titles · achievements: /titles view:earned"
            )
            pages.append(embed)
        view = ui.Paginator(pages, interaction.user.id)
        await ui.respond(interaction, embed=pages[0], view=view)

    @app_commands.command(name="buytitle", description="Buy one vanity title from the dedicated title shop.")
    @app_commands.describe(title="Title to buy")
    @app_commands.autocomplete(title=_purchasable_title_autocomplete)
    async def buytitle(self, interaction: discord.Interaction, title: str) -> None:
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        t = resolve_title(title)
        if t is None:
            await ui.respond(
                interaction, embed=ui.error_embed("No such title. Browse `/titleshop`."), ephemeral=True
            )
            return
        if t.price <= 0:
            await ui.respond(
                interaction, embed=ui.error_embed(f"{t.emoji} **{t.name}** must be earned."), ephemeral=True
            )
            return
        if t.id in RETIRED_TITLE_IDS:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"{t.emoji} **{t.name}** is a legacy title and is no longer sold. Existing owners can still wear it with `/titles`."
                ),
                ephemeral=True,
            )
            return
        u = self.user(interaction.guild_id, interaction.user.id)
        is_crown = t.id == "crowned"
        already_owned = bool(u.get("crown")) if is_crown else owns_title(u, t)
        if already_owned:
            await ui.respond(
                interaction, embed=ui.warn_embed(f"You already own {t.emoji} **{t.name}**."), ephemeral=True
            )
            return
        price = title_price(t, cfg)
        if int(u.get("donuts", 0)) < price:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"{t.emoji} **{t.name}** costs {self.money(cfg, price)}; you have {self.money(cfg, int(u.get('donuts', 0)))}."
                ),
                ephemeral=True,
            )
            return
        u["donuts"] = int(u.get("donuts", 0)) - price
        u.setdefault("titles", {})[t.id] = 1
        if is_crown:
            u["crown"] = True
        auto = is_crown or not u.get("equipped_title")
        if auto:
            u["equipped_title"] = t.id
        await self.persist(interaction.guild_id)
        await self._log(
            interaction.guild_id,
            interaction.user.id,
            -price,
            "buy:crown" if is_crown else f"buy:title:{t.id}",
            u,
        )
        note = f"Purchased {t.emoji} **{t.name}** for {self.money(cfg, price)}.\n*{t.blurb}*\nBalance: {self.money(cfg, int(u.get('donuts', 0)))}"
        note += "\nNow equipped." if auto else "\nEquip it with `/titles`."
        if is_crown:
            note += "\nYour crown appears on `/balance` and `/leaderboard`. Verify holders with `/titles view:crown`."
        await ui.respond(interaction, embed=ui.ok_embed(note, title="New title"))

    async def _owned_title_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        """Only titles the player owns — this command wears them, /titleshop sells them."""
        u = self.user(interaction.guild_id, interaction.user.id)
        current = current.lower()
        out = []
        for t in TITLES:
            if owns_title(u, t) and (current in t.id or current in t.name.lower()):
                out.append(app_commands.Choice(name=f"{t.emoji} {t.name}", value=t.id))
        return out[:25]

    @app_commands.command(
        name="titles", description="See or wear your titles, browse achievements, or verify Crown holders."
    )
    @app_commands.describe(
        title="A title you own, to wear it — or 'none' to take it off",
        view="Your collection, earned-title requirements, or verified Crown holders",
    )
    @app_commands.autocomplete(title=_owned_title_autocomplete)
    @app_commands.choices(
        view=[
            app_commands.Choice(name="Earned titles and requirements", value="earned"),
            app_commands.Choice(name="Verified Crown holders", value="crown"),
        ]
    )
    async def titles_cmd(
        self,
        interaction: discord.Interaction,
        title: Optional[str] = None,
        view: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        if view is None or view.value == "earned":
            nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        if view is not None:
            if title:
                await ui.respond(
                    interaction,
                    embed=ui.error_embed("Choose a title to wear or a title view, not both."),
                    ephemeral=True,
                )
                return
        if view is not None and view.value == "crown":
            holders = [
                uid
                for uid, account in self.econ.all_users(interaction.guild_id).items()
                if account.get("crown") and (not nyx.active(account))
            ]
            interaction.extras["szofie_nyx_public_subjects"] = [int(uid) for uid in holders[:50]]
            price = int(cfg.get("economy.price_crown", 2500000000))
            if not holders:
                description = f"The throne sits **empty** — nobody has claimed the crown yet.\nClaim it for {self.money(cfg, price)} in `/titleshop` with `/buytitle title:crowned`."
            else:
                names = "\n".join((f"👑 <@{uid}>" for uid in holders[:50]))
                if len(holders) > 50:
                    names += f"\n…and {len(holders) - 50} more holders."
                description = f"The **verified** crown {('sits with' if len(holders) == 1 else 'is shared by')}:\n{names}\n\n*Anyone else sporting a 👑 is a pretender — the crown is **claimed**, not typed.*"
            await ui.respond(
                interaction,
                embed=ui.base_embed(
                    title="👑 The Devourer's Crown", description=description, color=cfg.color
                ),
            )
            return
        u = self.user(interaction.guild_id, interaction.user.id)
        owned = u.get("titles", {}) or {}
        if title:
            t = title.lower().strip()
            if t in {"none", "off", "clear", "remove"}:
                u["equipped_title"] = None
                await self.persist(interaction.guild_id)
                await ui.respond(interaction, embed=ui.ok_embed("Title removed — back to being a nobody."))
                return
            tt = resolve_title(t)
            if tt is None:
                await ui.respond(
                    interaction,
                    embed=ui.error_embed(
                        f"No such title `{title}`. Browse `/titleshop` or `/titles view:earned`."
                    ),
                    ephemeral=True,
                )
                return
            if not owns_title(u, tt):
                route = (
                    "This legacy title is no longer sold."
                    if tt.id in RETIRED_TITLE_IDS
                    else "Check its requirements in `/titles view:earned`."
                    if tt.price == 0
                    else "Buy it in `/titleshop`."
                )
                await ui.respond(
                    interaction,
                    embed=ui.error_embed(f"You don't own {tt.emoji} **{tt.name}**. {route}"),
                    ephemeral=True,
                )
                return
            u["equipped_title"] = tt.id
            await self.persist(interaction.guild_id)
            await ui.respond(interaction, embed=ui.ok_embed(f"Now wearing {tt.emoji} **{tt.name}**."))
            return
        equipped = u.get("equipped_title")
        collection_lines = []
        for t in TITLES:
            if owns_title(u, t):
                mark = " 👑 *worn*" if t.id == equipped else ""
                legacy = " · legacy" if t.id in RETIRED_TITLE_IDS else ""
                collection_lines.append(f"✅ {t.emoji} **{t.name}**{mark}{legacy}")
        earned_lines = [
            f"{('✅' if owned.get(t.id) else '🔒')} {t.emoji} **{t.name}** — {t.blurb}" for t in EARNED_TITLES
        ]
        pages = ui.field_pages(
            "🏷️ Your titles",
            "Owned legacy titles can still be worn.",
            [
                (
                    "Your collection",
                    "\n".join(collection_lines)
                    or "You don't own any titles yet. Browse `/titleshop` or earn the achievements on the next page.",
                    False,
                )
            ],
            color=cfg.color,
        )
        earned_index = len(pages)
        pages.extend(
            ui.field_pages(
                "🏅 Earned titles",
                "Earned through achievements, not bought.",
                [("Requirements", "\n".join(earned_lines), False)],
                color=cfg.color,
            )
        )
        for index, page in enumerate(pages, start=1):
            page.set_footer(
                text=f"Page {index}/{len(pages)} · buy: /titleshop · wear: /titles <name> · Crown: /titles view:crown"
            )
        initial_index = earned_index if view is not None and view.value == "earned" else 0
        pager = ui.Paginator(pages, interaction.user.id, initial_index=initial_index)
        await ui.respond(interaction, embed=pages[initial_index], view=pager)

    @app_commands.command(name="badges", description="See achievement badges — earned and locked.")
    @app_commands.describe(user="Whose badges to view (defaults to you)")
    async def badges_cmd(
        self, interaction: discord.Interaction, user: Optional[discord.Member] = None
    ) -> None:
        nyx.protect_report(interaction, self.econ, (user or interaction.user).id)
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        target = user or interaction.user
        u = self.user(interaction.guild_id, target.id)
        if nyx.hidden(u, interaction.user.id, target.id):
            await ui.respond(interaction, embed=nyx.censored_embed(), ephemeral=True)
            return
        earned = u.get("badges", {}) or {}
        lines: List[str] = []
        for b in badges.BADGES:
            if b.id in earned:
                lines.append(f"✅ {b.emoji} **{b.name}** — {b.desc}")
            else:
                lines.append(f"🔒 {b.emoji} {b.name} — *{b.desc}*")
        embed = ui.base_embed(
            title=f"🏅 {target.display_name}'s badges  ({len(earned)}/{len(badges.BADGES)})",
            description="\n".join(lines),
            color=cfg.color,
        )
        if target.display_avatar:
            embed.set_thumbnail(url=target.display_avatar.url)
        await ui.respond(interaction, embed=embed, ephemeral=nyx.active(u))
        await self._award(interaction, target, u)

    @app_commands.command(name="ledger", description="Audit trail of donut transactions (staff only).")
    @app_commands.describe(user="Filter to one member", limit="How many recent entries (max 40)")
    @app_commands.check(checks.admin_check)
    async def ledger(
        self,
        interaction: discord.Interaction,
        user: Optional[discord.Member] = None,
        limit: Optional[app_commands.Range[int, 1, 40]] = 20,
    ) -> None:
        if interaction.guild_id is None:
            await ui.respond(interaction, embed=ui.error_embed("Only works in a server."), ephemeral=True)
            return
        await ui.defer_response(interaction, ephemeral=True)
        entries = await self.bot.ledger.read(
            interaction.guild_id, user=user.id if user else None, limit=int(limit or 20)
        )
        if not entries:
            await ui.respond(
                interaction, embed=ui.warn_embed("No transactions recorded yet."), ephemeral=True
            )
            return
        title = f"📒 Ledger — {user.display_name}" if user else "📒 Donut ledger"
        pages = self._ledger_pages(self.cfg(interaction.guild_id), entries, title)
        if len(pages) > 1:
            view = ui.Paginator(pages, interaction.user.id)
            await ui.respond(interaction, embed=pages[0], view=view, ephemeral=True)
        else:
            await ui.respond(interaction, embed=pages[0], ephemeral=True)

    def _ledger_pages(self, cfg, entries, title, *, redact_hidden: bool = False) -> List[discord.Embed]:
        lines: List[str] = []
        classified = {"deep-vault-deposit", "deep-vault-withdraw", "deep-vault-hit"}
        for e in entries:
            protected = False
            when = _parse(e.get("ts"))
            stamp = f"<t:{int(when.timestamp())}:R>" if when else "?"
            delta = int(e.get("delta", 0))
            sign = f"+{ui.format_donuts(delta)}" if delta >= 0 else f"{ui.format_donuts(delta)}"
            reason = e.get("reason", "?")
            label = _ledger_label(reason)
            extra = ""
            if e.get("other"):
                extra += f" ↔ <@{e['other']}>"
            if e.get("actor"):
                extra += f" by <@{e['actor']}>"
            after = f" → {ui.format_donuts(e['after'])}" if "after" in e else ""
            if redact_hidden and reason in classified and (not protected):
                lines.append(f"{stamp} <@{e['user']}> **CLASSIFIED** *Bedrock Vault activity*")
            else:
                lines.append(f"{stamp} <@{e['user']}> **{sign}** *{label}*{extra}{after}")
        pages: List[discord.Embed] = []
        for start in range(0, len(lines), 12):
            embed = ui.base_embed(title=title, color=cfg.color)
            embed.description = "\n".join(lines[start : start + 12])
            embed.set_footer(text="Append-only · newest first · ADMIN ADJUST = /donutadmin")
            pages.append(embed)
        return pages

    @app_commands.command(
        name="attacklog", description="Publicly show who successfully attacked or robbed you, when, and how."
    )
    @app_commands.describe(limit="Number of successful incidents to show (max 40)")
    async def attacklog(
        self, interaction: discord.Interaction, limit: Optional[app_commands.Range[int, 1, 40]] = 20
    ) -> None:
        if interaction.guild_id is None:
            await ui.respond(
                interaction, embed=ui.error_embed("Attack logs only exist inside a server."), ephemeral=True
            )
            return
        await ui.defer_response(interaction)
        entries = await self.bot.ledger.read_security(
            interaction.guild_id, victim=interaction.user.id, limit=int(limit or 20)
        )
        if not entries:
            await ui.respond(
                interaction,
                embed=ui.ok_embed(
                    "No successful robberies or hostile actions are recorded against you.",
                    title="🛡️ Attack log clear",
                ),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        lines: List[str] = []
        for entry in entries:
            when = _parse(entry.get("ts"))
            stamp = (
                f"<t:{int(when.timestamp())}:F> · <t:{int(when.timestamp())}:R>" if when else "Unknown time"
            )
            attacker_id = int(entry["security_actor"])
            member = interaction.guild.get_member(attacker_id) if interaction.guild else None
            display = discord.utils.escape_markdown(member.display_name if member else f"User {attacker_id}")
            method = ATTACKLOG_METHODS.get(
                str(entry.get("reason", "")), str(entry.get("reason", "Unknown attack"))
            )
            detail = str(entry.get("detail", "")).strip()
            if detail:
                method += f" · {discord.utils.escape_markdown(detail)}"
            loss = max(0, -int(entry.get("delta", 0)))
            loss_text = f" · 🍩 **{ui.format_donuts(loss)}** lost" if loss else ""
            lines.append(f"{stamp}\n**{display}** · ID `{attacker_id}`\n**Method:** {method}{loss_text}")
        cfg = self.cfg(interaction.guild_id)
        pages: List[discord.Embed] = []
        for start in range(0, len(lines), 8):
            victim_name = discord.utils.escape_markdown(interaction.user.display_name)
            embed = ui.base_embed(
                title=f"🛡️ {victim_name}'s Successful Attack Log",
                description="\n\n".join(lines[start : start + 8]),
                color=cfg.color,
            )
            embed.set_footer(
                text=f"Successful incidents only · Page {start // 8 + 1}/{max(1, math.ceil(len(lines) / 8))} · Opened manually; no mentions sent"
            )
            pages.append(embed)
        silent = discord.AllowedMentions.none()
        if len(pages) > 1:
            await ui.respond(
                interaction,
                embed=pages[0],
                view=ui.Paginator(pages, interaction.user.id),
                allowed_mentions=silent,
            )
        else:
            await ui.respond(interaction, embed=pages[0], allowed_mentions=silent)

    @app_commands.command(
        name="publicledger", description="Post the donut ledger where everyone can see it (owner only)."
    )
    @app_commands.describe(user="Filter to one member")
    async def publicledger(
        self, interaction: discord.Interaction, user: Optional[discord.Member] = None
    ) -> None:
        if interaction.guild_id is None:
            await ui.respond(interaction, embed=ui.error_embed("Only works in a server."), ephemeral=True)
            return
        if not checks.is_privileged_owner(interaction):
            await ui.respond(
                interaction,
                embed=ui.error_embed("Only the server owner can post the ledger publicly."),
                ephemeral=True,
            )
            return
        await ui.defer_response(interaction)
        entries = await self.bot.ledger.read(interaction.guild_id, user=user.id if user else None, limit=20)
        if not entries:
            await ui.respond(
                interaction, embed=ui.warn_embed("No transactions recorded yet."), ephemeral=True
            )
            return
        title = f"📒 Public ledger — {user.display_name}" if user else "📒 Public donut ledger"
        pages = self._ledger_pages(self.cfg(interaction.guild_id), entries, title, redact_hidden=True)
        silent = discord.AllowedMentions.none()
        if len(pages) > 1:
            view = ui.Paginator(pages, interaction.user.id)
            await ui.respond(interaction, embed=pages[0], view=view, allowed_mentions=silent)
        else:
            await ui.respond(interaction, embed=pages[0], allowed_mentions=silent)

    def _random_icbm_gif(self) -> Optional[Path]:
        try:
            pool = [p for p in ICBM_DIR.iterdir() if p.suffix.lower() in IMAGE_EXTS]
        except OSError:
            return None
        return random.choice(pool) if pool else None

    def _icbm_launch_lock(self, guild_id: int, user_id: int) -> asyncio.Lock:
        """Return the per-silo lock that makes acknowledgement + commit atomic."""
        locks = getattr(self, "_icbm_launch_locks", None)
        if locks is None:
            locks = {}
            self._icbm_launch_locks = locks
        key = (int(guild_id), int(user_id))
        if key not in locks:
            locks[key] = asyncio.Lock()
        return locks[key]

    @staticmethod
    async def _acknowledge_icbm_launch(interaction) -> bool:
        """Acknowledge a real slash command before committing irreversible state.

        The common guard may have acknowledged the slash interaction already.
        Reuse that acknowledgement rather than deferring a second time. Prefix
        commands have no expiring token and retain normal channel delivery.
        """
        defer = getattr(interaction.response, "defer", None)
        edit = getattr(interaction, "edit_original_response", None)
        if interaction.extras.get("szofie_prefix") or not callable(defer) or (not callable(edit)):
            return False
        await ui.defer_response(interaction)
        return True

    @staticmethod
    async def _send_icbm_result(
        interaction,
        *,
        deferred: bool,
        embed: discord.Embed,
        content: str,
        allowed_mentions: discord.AllowedMentions,
        image_file: Optional[discord.File] = None,
    ) -> None:
        """Deliver a committed strike, retrying once without optional media."""

        async def deliver(result_embed: discord.Embed, attachment: Optional[discord.File]) -> None:
            if deferred:
                kwargs: Dict[str, Any] = {
                    "content": content,
                    "embed": result_embed,
                    "allowed_mentions": allowed_mentions,
                }
                if attachment is not None:
                    kwargs["attachments"] = [attachment]
                await interaction.edit_original_response(**kwargs)
            else:
                kwargs = {"content": content, "embed": result_embed, "allowed_mentions": allowed_mentions}
                if attachment is not None:
                    kwargs["file"] = attachment
                await ui.respond(interaction, **kwargs)

        try:
            await deliver(embed, image_file)
        except discord.HTTPException:
            if image_file is None:
                raise
            log.warning("ICBM result media upload failed; retrying without media", exc_info=True)
            fallback = embed.copy()
            fallback.set_image(url=None)
            if deferred:
                await interaction.edit_original_response(
                    content=content, embed=fallback, attachments=[], allowed_mentions=allowed_mentions
                )
            else:
                await ui.respond(
                    interaction, content=content, embed=fallback, allowed_mentions=allowed_mentions
                )
        finally:
            if image_file is not None:
                image_file.close()

    def _icbm_settle(self, u: Dict[str, Any]) -> int:
        """Move a finished build into the silo (lazy). Returns the silo count."""
        building = _parse(u.get("icbm_building_at"))
        if building is not None and _now() >= building:
            u["icbm_ready"] = int(u.get("icbm_ready", 0)) + 1
            u["icbm_building_at"] = None
        return int(u.get("icbm_ready", 0))

    @staticmethod
    def _icbm_cap(cfg, u: Dict[str, Any]) -> int:
        """Silo capacity for this user — base + Silo 21 plushie bonus."""
        return int(cfg.get("economy.icbm_max_stock", 3)) + plushie_perk(u, "icbm_stock")

    @staticmethod
    def _icbm_cooldown_secs(cfg, u: Dict[str, Any]) -> float:
        """Launch cooldown in seconds — base, cut by Warhead 21 if held."""
        cd = float(cfg.get("economy.icbm_cooldown_hours", 24)) * 3600
        speed = plushie_perk(u, "icbm_speed")
        if speed:
            cd -= cd * speed / 100
        return cd

    icbm_group = app_commands.Group(
        name="icbm", description="Build and launch ICBMs to obliterate a rival's holdings."
    )

    @icbm_group.command(
        name="build", description="Commission an ICBM — costs a fortune and takes hours to assemble."
    )
    async def icbm_build(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid = interaction.guild_id
        u = self.user(gid, interaction.user.id)
        ready = self._icbm_settle(u)
        building = _parse(u.get("icbm_building_at"))
        if building is not None:
            await self.persist(gid)
            await ui.respond(
                interaction,
                embed=ui.warn_embed(
                    f"A missile's already on the line — ready <t:{int(building.timestamp())}:R>. You build one at a time."
                ),
                ephemeral=True,
            )
            return
        max_stock = self._icbm_cap(cfg, u)
        if ready >= max_stock:
            await self.persist(gid)
            await ui.respond(
                interaction,
                embed=ui.warn_embed(
                    f"Your silo is full (**{ready}/{max_stock}**). Launch one before building more."
                ),
                ephemeral=True,
            )
            return
        cost = int(cfg.get("economy.icbm_build_cost", 750000000))
        if self._net(u) < cost:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"An ICBM costs {self.money(cfg, cost)}. You can't afford the program."),
                ephemeral=True,
            )
            return
        self._take(u, cost)
        hours = float(cfg.get("economy.icbm_build_hours", 2))
        speed = plushie_perk(u, "icbm_speed")
        if speed:
            hours -= hours * speed / 100
        done = _now() + dt.timedelta(hours=hours)
        u["icbm_building_at"] = done.isoformat()
        await self.persist(gid)
        await self._log(gid, interaction.user.id, -cost, "icbm-build", u)
        embed = ui.base_embed(
            title="🔧 ICBM — assembly begun",
            description=f"{interaction.user.mention}'s silo hums to life. The warhead is ready <t:{int(done.timestamp())}:R>.\n\n**Cost:** {self.money(cfg, cost)}\n**Silo:** {ready}/{max_stock} ready",
            color=cfg.color,
        )
        await ui.respond(interaction, embed=embed)

    @icbm_group.command(
        name="silo", description="Check your ICBM stockpile, build progress and launch cooldown."
    )
    async def icbm_silo(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        gid = interaction.guild_id
        u = self.user(gid, interaction.user.id)
        ready = self._icbm_settle(u)
        await self.persist(gid)
        max_stock = self._icbm_cap(cfg, u)
        embed = ui.base_embed(
            title=f"🛰️ {interaction.user.display_name}'s missile silo",
            description=f"**Ready to launch:** {ready}/{max_stock}",
            color=cfg.color,
        )
        building = _parse(u.get("icbm_building_at"))
        if building is not None:
            embed.add_field(
                name="🏗️ Missile construction",
                value=f"**Completes:** {status_ui.deadline(building)}",
                inline=False,
            )
        cd = self._icbm_cooldown_secs(cfg, u)
        last = _parse(u.get("icbm_launch_at"))
        if last is not None and (_now() - last).total_seconds() < cd:
            embed.add_field(
                name="⏳ Launch cooldown",
                value=f"**Launch controls return:** {status_ui.deadline(last + dt.timedelta(seconds=cd))}",
                inline=False,
            )
        else:
            embed.add_field(
                name="🚀 Launch controls",
                value="🟢 Armed and ready." if ready else "⚪ No ready missiles.",
                inline=False,
            )
        embed.add_field(
            name="➡️ Next action",
            value="Use `/icbm build` for another missile or `/icbm launch` to fire a ready one.",
            inline=False,
        )
        cost = int(cfg.get("economy.icbm_build_cost", 750000000))
        wpct = int(cfg.get("economy.icbm_wallet_pct", 30))
        bpct = int(cfg.get("economy.icbm_bank_pct", 50))
        embed.set_footer(
            text=f"Build cost {ui.format_donuts(cost)} {self.name(cfg)} · a strike burns {wpct}% wallet / {bpct}% vault (+ items & fish)"
        )
        await ui.respond(interaction, embed=embed, ephemeral=nyx.active(u))

    @icbm_group.command(
        name="launch", description="Fire a ready ICBM at a player, obliterating a chunk of their holdings."
    )
    @app_commands.describe(target="The player to strike")
    async def icbm_launch(
        self, interaction: discord.Interaction, target: Optional[discord.Member] = None
    ) -> None:
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        target, err = await self._resolve_target(interaction, target, None)
        if err:
            await ui.respond(interaction, embed=ui.error_embed(err), ephemeral=True)
            return
        await self._icbm_launch_run(interaction, cfg, target)

    async def _icbm_launch_run(self, interaction, cfg, target) -> None:
        """Core of /icbm launch, shared with the hidden !idnuke prefix command."""
        gid = interaction.guild_id
        if target.id == interaction.user.id:
            await ui.respond(
                interaction,
                embed=ui.error_embed("You can't nuke yourself. Probably for the best."),
                ephemeral=True,
            )
            return
        if target.bot:
            await ui.respond(
                interaction, embed=ui.error_embed("Bots have nothing worth a warhead."), ephemeral=True
            )
            return
        treaty = self._coalition_attack_error(gid, interaction.user.id, target.id)
        if treaty:
            await ui.respond(interaction, embed=ui.error_embed(treaty), ephemeral=True)
            return
        attacker = self.user(gid, interaction.user.id)
        lockdown = _strategic_lockdown_until(attacker)
        if lockdown is not None:
            await self.persist(gid)
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"Your strategic runway and operations hub are disabled by a B-52 strike until <t:{int(lockdown.timestamp())}:R>. You may prepare weapons, but cannot launch attacks."
                ),
                ephemeral=True,
            )
            return
        ready = self._icbm_settle(attacker)
        cd = self._icbm_cooldown_secs(cfg, attacker)
        last = _parse(attacker.get("icbm_launch_at"))
        if last is not None and (_now() - last).total_seconds() < cd:
            await self.persist(gid)
            await ui.respond(
                interaction,
                embed=ui.warn_embed(
                    f"The silo's still re-arming. Next launch <t:{int(last.timestamp() + cd)}:R>."
                ),
                ephemeral=True,
            )
            return
        if ready < 1:
            building = _parse(attacker.get("icbm_building_at"))
            await self.persist(gid)
            if building is not None:
                msg = f"No missile ready yet — one's assembling, ready <t:{int(building.timestamp())}:R>."
            else:
                msg = "You have no missiles. Build one with `/icbm build` first."
            await ui.respond(interaction, embed=ui.error_embed(msg), ephemeral=True)
            return
        victim = self.user(gid, target.id)
        eligible, floor = _icbm_target_eligible(cfg, victim)
        if not eligible:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"{target.display_name} needs at least {self.money(cfg, floor)} in either their wallet or bank to be worth a warhead."
                ),
                ephemeral=True,
            )
            return
        async with self._icbm_launch_lock(gid, interaction.user.id):
            ready = self._icbm_settle(attacker)
            last = _parse(attacker.get("icbm_launch_at"))
            if last is not None and (_now() - last).total_seconds() < cd:
                await self.persist(gid)
                await ui.respond(
                    interaction,
                    embed=ui.warn_embed(
                        f"The silo's still re-arming. Next launch <t:{int(last.timestamp() + cd)}:R>."
                    ),
                    ephemeral=True,
                )
                return
            if ready < 1:
                await self.persist(gid)
                await ui.respond(
                    interaction,
                    embed=ui.error_embed("You have no missiles. Build one with `/icbm build` first."),
                    ephemeral=True,
                )
                return
            eligible, floor = _icbm_target_eligible(cfg, victim)
            if not eligible:
                await ui.respond(
                    interaction,
                    embed=ui.error_embed(
                        f"{target.display_name} needs at least {self.money(cfg, floor)} in either their wallet or bank to be worth a warhead."
                    ),
                    ephemeral=True,
                )
                return
            deferred = await self._acknowledge_icbm_launch(interaction)
            attacker["icbm_ready"] = ready - 1
            attacker["icbm_launch_at"] = _now().isoformat()
        intercepted, aa_kind, aa_detail = self._resolve_aa(cfg, interaction.user.id, target.id, victim)
        if intercepted:
            await self.persist(gid)
            await self.bot.ledger.record(
                gid, interaction.user.id, 0, "icbm-launch", after=self._net(attacker), other=target.id
            )
            max_stock = self._icbm_cap(cfg, attacker)
            shot = ui.base_embed(
                title="🛡️ ICBM INTERCEPTED",
                description=f"{interaction.user.mention} launched an ICBM at {target.mention} — but their Radar AA **shot it down!** No damage. {interaction.user.display_name} burned the missile for nothing.",
                color=ui.COLOR_OK,
            )
            shot.add_field(
                name="Defense", value="🛡️ AA shield" if aa_kind == "shield" else f"🚀 {aa_detail}", inline=True
            )
            shot.add_field(
                name="Attacker's silo",
                value=f"{int(attacker.get('icbm_ready', 0))}/{max_stock} left",
                inline=True,
            )
            shot_file = None
            if cfg.get("economy.aa_media", True):
                path = self._random_aa_gif()
                if path is not None:
                    safe = f"aa{path.suffix.lower()}"
                    try:
                        shot_file = discord.File(str(path), filename=safe)
                    except OSError:
                        log.warning("Could not open ICBM interception media", exc_info=True)
                    else:
                        shot.set_image(url=f"attachment://{safe}")
            await self._send_icbm_result(
                interaction,
                deferred=deferred,
                embed=shot,
                content=f"{target.mention} {interaction.user.mention}",
                allowed_mentions=discord.AllowedMentions(users=True),
                image_file=shot_file,
            )
            return
        pct_clamp = lambda v: max(0, min(100, int(v)))
        wallet_pct = pct_clamp(cfg.get("economy.icbm_wallet_pct", 30))
        bank_pct = pct_clamp(cfg.get("economy.icbm_bank_pct", 50))
        item_pct = pct_clamp(cfg.get("economy.icbm_item_pct", 30))
        fish_pct = pct_clamp(cfg.get("economy.icbm_fish_pct", 40))
        cap = int(cfg.get("economy.icbm_damage_cap", 0) or 0)
        victim_before = self._net(victim)
        wallet_hit = int(victim.get("donuts", 0)) * wallet_pct // 100
        bank_hit = int(victim.get("bank", 0)) * bank_pct // 100
        if cap and wallet_hit + bank_hit > cap:
            over = wallet_hit + bank_hit - cap
            shave = min(bank_hit, over)
            bank_hit -= shave
            over -= shave
            wallet_hit -= min(wallet_hit, over)
        victim["donuts"] = int(victim.get("donuts", 0)) - wallet_hit
        victim["bank"] = int(victim.get("bank", 0)) - bank_hit
        money_dmg = wallet_hit + bank_hit
        inv = victim.get("inventory", {}) or {}
        items_lost = 0
        for k in list(inv):
            lost = int(inv.get(k, 0)) * item_pct // 100
            if lost:
                inv[k] = int(inv[k]) - lost
                items_lost += lost
        fish = victim.get("fish", {}) or {}
        fish_lost = 0
        for k in list(fish):
            lost = int(fish.get(k, 0)) * fish_pct // 100
            if lost:
                fish[k] = int(fish[k]) - lost
                fish_lost += lost
        await self.persist(gid)
        await self.bot.ledger.record(
            gid, interaction.user.id, 0, "icbm-launch", after=self._net(attacker), other=target.id
        )
        await self._log(
            gid, target.id, self._net(victim) - victim_before, "icbm-hit", victim, other=interaction.user.id
        )
        max_stock = self._icbm_cap(cfg, attacker)
        embed = ui.base_embed(
            title="☠️ ICBM — direct hit",
            description=f"{interaction.user.mention} launched an ICBM at {target.mention} — **impact confirmed.**\n\nA huge chunk of everything they had just went up in smoke. Nobody profits. Nobody's safe.",
            color=ui.COLOR_WARN,
        )
        lines = [f"🍩 **{ui.format_donuts(money_dmg)}** {self.name(cfg)} incinerated (wallet + vault)"]
        if items_lost:
            lines.append(f"📦 **{items_lost}** consumable(s) destroyed")
        if fish_lost:
            lines.append(f"🐟 **{fish_lost}** fish vaporized")
        embed.add_field(name="💥 Damage report", value="\n".join(lines), inline=False)
        if aa_kind is not None:
            embed.add_field(name="🛡️ AA failed", value=aa_detail, inline=False)
        embed.add_field(
            name="Silo", value=f"{int(attacker.get('icbm_ready', 0))}/{max_stock} ready", inline=True
        )
        embed.set_footer(
            text=f"Strike: {wallet_pct}% wallet · {bank_pct}% vault · {item_pct}% items · {fish_pct}% fish"
        )
        image_file = None
        if cfg.get("economy.icbm_media", True):
            path = self._random_icbm_gif()
            if path is not None:
                safe = f"icbm{path.suffix.lower()}"
                try:
                    image_file = discord.File(str(path), filename=safe)
                except OSError:
                    log.warning("Could not open ICBM strike media", exc_info=True)
                else:
                    embed.set_image(url=f"attachment://{safe}")
        await self._send_icbm_result(
            interaction,
            deferred=deferred,
            embed=embed,
            content=target.mention,
            allowed_mentions=discord.AllowedMentions(users=True),
            image_file=image_file,
        )

    def _random_aa_gif(self) -> Optional[Path]:
        try:
            pool = [p for p in AA_DIR.iterdir() if p.suffix.lower() in IMAGE_EXTS]
        except OSError:
            return None
        return random.choice(pool) if pool else None

    def _aa_settle(self, cfg, u: Dict[str, Any]) -> bool:
        keys = ("aa_rockets_stock", "aa_rocket_build_at", "aa_rocket_build_qty")
        before = tuple((u.get(key) for key in keys))
        done = _parse(u.get("aa_rocket_build_at"))
        if done is not None and _now() >= done:
            cap = max(0, int(cfg.get("economy.aa_rocket_stock_cap", 10)))
            stock = max(0, int(u.get("aa_rockets_stock", 0)))
            quantity = max(0, int(u.get("aa_rocket_build_qty", 0)))
            u["aa_rockets_stock"] = min(cap, stock + quantity)
            u["aa_rocket_build_at"] = None
            u["aa_rocket_build_qty"] = 0
        return tuple((u.get(key) for key in keys)) != before

    def _resolve_aa(self, cfg, attacker_id: int, defender_id: int, victim: Dict[str, Any]):
        """Roll the victim's Radar AA against one incoming ICBM. Mutates the victim
        (spends rockets; a shield is never consumed). Returns (intercepted, kind, detail)
        where kind is 'shield'/'rockets'/None and detail is a success blurb or fail reason."""
        if _electronic_blackout_until(victim) is not None:
            return (False, None, "")
        clamp = lambda v: max(0, min(100, int(v)))
        radar = plushie_perk(victim, "aa_intercept")
        if _remaining(victim.get("aa_shield_until")) > 0:
            chance = clamp(cfg.get("economy.aa_shield_chance", 50) + radar)
            chance = chance
            if random.randint(1, 100) <= chance:
                return (True, "shield", "AA shield")
            return (False, "shield", random.choice(AA_FAIL_REASONS))
        loaded = int(victim.get("aa_rockets_loaded", 0))
        if loaded > 0:
            p = clamp(cfg.get("economy.aa_rocket_chance", 30) + radar)
            p = p
            fired = loaded if bool(cfg.get("economy.aa_salvo", False)) else 1
            victim["aa_rockets_loaded"] = loaded - fired
            hit = any((random.randint(1, 100) <= p for _ in range(fired)))
            detail = f"{fired}-rocket salvo" if fired > 1 else "1 interceptor rocket"
            return (True, "rockets", detail) if hit else (False, "rockets", random.choice(AA_FAIL_REASONS))
        return (False, None, "")

    aa_group = app_commands.Group(
        name="aa", description="Radar AA — defend against ICBMs and light strategic aircraft."
    )

    @aa_group.command(
        name="shield", description="Raise a timed ICBM shield that also strengthens loaded light-aircraft AA."
    )
    async def aa_shield(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid = interaction.guild_id
        u = self.user(gid, interaction.user.id)
        if _remaining(u.get("aa_shield_until")) > 0:
            until = _parse(u.get("aa_shield_until"))
            await ui.respond(
                interaction,
                embed=ui.warn_embed(
                    f"Your shield's already up — active until <t:{int(until.timestamp())}:R>."
                ),
                ephemeral=True,
            )
            return
        cost = int(cfg.get("economy.aa_shield_cost", 25000000))
        if self._net(u) < cost:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"A shield costs {self.money(cfg, cost)}. You can't afford it."),
                ephemeral=True,
            )
            return
        self._take(u, cost)
        lo = float(cfg.get("economy.aa_shield_min_hours", 1))
        hi = float(cfg.get("economy.aa_shield_max_hours", 3))
        if hi < lo:
            lo, hi = (hi, lo)
        until = _now() + dt.timedelta(hours=random.uniform(lo, hi))
        u["aa_shield_until"] = until.isoformat()
        await self.persist(gid)
        await self._log(gid, interaction.user.id, -cost, "aa-shield", u)
        chance = int(cfg.get("economy.aa_shield_chance", 50))
        embed = ui.base_embed(
            title="🛡️ Radar AA Shield online",
            description=f"{interaction.user.mention} raised an AA shield — **active until <t:{int(until.timestamp())}:R>**. Every ICBM aimed their way has a **{chance}%** chance to be shot down. While a regular AA rocket is loaded, the shield also adds **+{int(cfg.get('economy.aa_shield_vehicle_bonus', 10))} percentage points** against U-2s, MQ-9Bs, Apaches and Comanches. The rocket is still consumed hit or miss.\n\n**Cost:** {self.money(cfg, cost)}",
            color=cfg.color,
        )
        await ui.respond(interaction, embed=embed)

    @aa_group.command(
        name="build",
        description="Manufacture interceptor rockets — cheaper defense, but they take time to build.",
    )
    @app_commands.describe(qty="How many rockets to build")
    async def aa_build(
        self, interaction: discord.Interaction, qty: app_commands.Range[int, 1, 50] = 1
    ) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid = interaction.guild_id
        u = self.user(gid, interaction.user.id)
        self._aa_settle(cfg, u)
        batch_max = int(cfg.get("economy.aa_rocket_batch_max", 5))
        stock_cap = int(cfg.get("economy.aa_rocket_stock_cap", 10))
        stock = int(u.get("aa_rockets_stock", 0))
        building = _parse(u.get("aa_rocket_build_at"))
        pending = int(u.get("aa_rocket_build_qty", 0)) if building is not None else 0
        room = min(max(0, batch_max - pending), max(0, stock_cap - stock - pending))
        if room <= 0:
            await self.persist(gid)
            if building is not None:
                msg = f"That batch is already full (**{pending}/{batch_max}** building) — ready <t:{int(building.timestamp())}:R>. Load it before building more."
            else:
                msg = f"Your stockpile is full (**{stock}/{stock_cap}**). Load some before building more."
            await ui.respond(interaction, embed=ui.warn_embed(msg), ephemeral=True)
            return
        want = min(int(qty), room)
        trimmed = want < int(qty)
        cost = want * int(cfg.get("economy.aa_rocket_cost", 300000))
        if self._net(u) < cost:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"Building {want} rocket(s) costs {self.money(cfg, cost)}; you can't afford it."
                ),
                ephemeral=True,
            )
            return
        self._take(u, cost)
        if building is not None:
            done = building
            u["aa_rocket_build_qty"] = pending + want
            title = "🔧 Interceptor rockets — batch topped up"
            body = f"{interaction.user.mention} added **{want}** to the line — **{pending + want}** now building, ready <t:{int(done.timestamp())}:R>."
        else:
            mins = float(cfg.get("economy.aa_rocket_build_minutes", 40))
            speed = plushie_perk(u, "aa_build")
            if speed:
                mins -= mins * speed / 100
            done = _now() + dt.timedelta(minutes=mins)
            u["aa_rocket_build_at"] = done.isoformat()
            u["aa_rocket_build_qty"] = want
            title = "🔧 Interceptor rockets — building"
            body = f"{interaction.user.mention} is manufacturing **{want}** interceptor rocket(s) — ready <t:{int(done.timestamp())}:R>."
        await self.persist(gid)
        await self._log(gid, interaction.user.id, -cost, "aa-rocket-build", u)
        note = f"\n*(Trimmed to {want} — batch max {batch_max}, room {room}.)*" if trimmed else ""
        embed = ui.base_embed(
            title=title,
            description=f"{body}\n\n**Cost:** {self.money(cfg, cost)}{note}\nWhen they're done, load them with `/aa load`.",
            color=cfg.color,
        )
        await ui.respond(interaction, embed=embed)

    @aa_group.command(name="load", description="Load built rockets into the battery so they can intercept.")
    @app_commands.describe(qty="How many rockets to load")
    async def aa_load(
        self, interaction: discord.Interaction, qty: app_commands.Range[int, 1, 50] = 1
    ) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        await ui.defer_response(interaction)
        gid = interaction.guild_id
        u = self.user(gid, interaction.user.id)
        self._aa_settle(cfg, u)
        stock = int(u.get("aa_rockets_stock", 0))
        if stock <= 0:
            await self.persist(gid)
            await ui.respond(
                interaction,
                embed=ui.warn_embed("You have no built rockets to load. Make some with `/aa build`."),
                ephemeral=True,
            )
            return
        load_cap = int(cfg.get("economy.aa_rocket_load_cap", 5)) + (2 if plushie_perk(u, "aa_build") else 0)
        loaded = int(u.get("aa_rockets_loaded", 0))
        room = max(0, load_cap - loaded)
        if room <= 0:
            await self.persist(gid)
            await ui.respond(
                interaction,
                embed=ui.warn_embed(f"The battery's full (**{loaded}/{load_cap}** loaded)."),
                ephemeral=True,
            )
            return
        moved = min(int(qty), stock, room)
        u["aa_rockets_stock"] = stock - moved
        u["aa_rockets_loaded"] = loaded + moved
        await self.persist(gid)
        salvo = bool(cfg.get("economy.aa_salvo", False))
        pchance = int(cfg.get("economy.aa_rocket_chance", 30))
        mode = f"fires all {loaded + moved} at once (salvo)" if salvo else "fires 1 per incoming missile"
        embed = ui.base_embed(
            title="🚀 Battery loaded",
            description=f"{interaction.user.mention} loaded **{moved}** rocket(s) — battery now **{loaded + moved}/{load_cap}**, stockpile **{u['aa_rockets_stock']}**.\n\nEach rocket is **{pchance}%** and the battery {mode}. Rockets are spent whether they hit or miss.",
            color=cfg.color,
        )
        await ui.respond(interaction, embed=embed)

    @aa_group.command(
        name="status", description="Your Radar AA — shield window, rockets building / stocked / loaded."
    )
    async def aa_status(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        gid = interaction.guild_id
        u = self.user(gid, interaction.user.id)
        self._aa_settle(cfg, u)
        await self.persist(gid)
        load_cap = int(cfg.get("economy.aa_rocket_load_cap", 5)) + (2 if plushie_perk(u, "aa_build") else 0)
        stock_cap = int(cfg.get("economy.aa_rocket_stock_cap", 10))
        embed = ui.base_embed(title=f"🛡️ {interaction.user.display_name}'s Radar AA", color=cfg.color)
        if _remaining(u.get("aa_shield_until")) > 0:
            until = _parse(u.get("aa_shield_until"))
            embed.add_field(
                name="🛡️ AA shield",
                value=f"🟢 Active\n**Expires:** {status_ui.deadline(until)}\n({int(cfg.get('economy.aa_shield_chance', 50))}% vs ICBM · +{int(cfg.get('economy.aa_shield_vehicle_bonus', 10))} points to loaded light-aircraft AA)",
                inline=False,
            )
        else:
            embed.add_field(name="🛡️ AA shield", value="⚪ None active", inline=False)
        embed.add_field(
            name="🚀 Rocket readiness",
            value=status_ui.line("Loaded battery", f"{int(u.get('aa_rockets_loaded', 0))}/{load_cap}")
            + "\n"
            + status_ui.line("Cold stockpile", f"{int(u.get('aa_rockets_stock', 0))}/{stock_cap}"),
            inline=False,
        )
        building = _parse(u.get("aa_rocket_build_at"))
        if building is not None:
            embed.add_field(
                name="🏗️ Rocket construction",
                value=f"**Batch:** {int(u.get('aa_rocket_build_qty', 0))} rocket(s)\n**Completes:** {status_ui.deadline(building)}",
                inline=False,
            )
        embed.add_field(
            name="➡️ Next action",
            value="Use `/aa build` to stock rockets, `/aa load` to arm the battery, or `/aa shield` for temporary protection.",
            inline=False,
        )
        salvo = bool(cfg.get("economy.aa_salvo", False))
        embed.set_footer(
            text=f"Rockets: {int(cfg.get('economy.aa_rocket_chance', 30))}% each · "
            + ("salvo mode ON" if salvo else "1 per incoming")
        )
        await ui.respond(interaction, embed=embed, ephemeral=nyx.active(u))

    @staticmethod
    def _strategic_art(cfg, embed: discord.Embed, event: str) -> Optional[discord.File]:
        """Attach project-owned event artwork when strategic media is enabled."""
        if not cfg.get("economy.strategic_media", True):
            return None
        names = {
            "b2": "b2-strike.png",
            "b52": "b52-runway-strike.png",
            "b52-intercept": "b52-intercept.png",
            "u2": "u2-recon.png",
            "deimos-success": "deimos-success.png",
            "deimos-failed": "deimos-failed.png",
            "sr71-build": "sr71-build.png",
            "sr71-recon": "sr71-recon.png",
            "sr71-shotdown": "sr71-shotdown.png",
            "s400": "s400-intercept.png",
            "zumwalt": "zumwalt-strike.png",
            "virginia": "virginia-raid.png",
            "himars": "himars-strike.png",
            "apache": "apache-strike.png",
            "apache-intercept": "apache-intercept.png",
            "comanche-upgrade": "comanche-upgrade.png",
            "comanche": "comanche-strike.png",
            "comanche-intercept": "comanche-intercept.png",
            "mq9": "mq9-strike.png",
            "f15e": "f15e-strike.png",
            "a10": "a10c-thunderbolt-strike.png",
            "su34": "su34-fullback-strike.png",
            "c130j": "c130j-rapid-dragon.png",
            "champ": "champ-microwave.png",
            "maldx": "maldx-decoy.png",
            "lrhw": "lrhw-dark-eagle.png",
            "xb70": "xb70-valkyrie.png",
            "m1a2": "m1a2-sepv3-abrams.png",
            "m1a2-breach": "m1a2-armored-breach.png",
            "m1a2-trophy": "m1a2-trophy-intercept.png",
            "m1a2-repair": "m1a2-damaged-repair.png",
            "leopard2a7": "leopard-2a7a1.png",
            "leopard2a7-breach": "leopard-armored-breach.png",
            "leopard2a7-intercept": "leopard-javelin-intercept.png",
            "leopard2a7-repair": "leopard-damaged-repair.png",
            "javelin": "fgm148f-javelin-team.png",
            "javelin-intercept": "fgm148f-javelin-intercept.png",
            "aegis": "aegis-intercept.png",
            "p8": "p8-intercept.png",
            "patriot": "patriot-intercept.png",
        }
        filename = event + ".png" if event.partition("-")[0] in air.JETS else names.get(event)
        if filename is None:
            return None
        path = STRATEGIC_DIR / filename
        if not path.is_file():
            return None
        embed.set_image(url=f"attachment://{filename}")
        return discord.File(str(path), filename=filename)

    vehicle_group = app_commands.Group(
        name="vehicle", description="Build, arm and deploy strategic military systems."
    )

    @vehicle_group.command(name="build", description="Commission a strategic vehicle or defense system.")
    @app_commands.describe(vehicle="System to commission")
    @app_commands.autocomplete(vehicle=_vehicle_build_autocomplete)
    async def vehicle_build(self, interaction: discord.Interaction, vehicle: str) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid = interaction.guild_id
        u = self.user(gid, interaction.user.id)
        model = str(getattr(vehicle, "value", vehicle)).lower().strip()
        if model not in VEHICLE_CATALOG:
            await ui.respond(
                interaction,
                embed=ui.error_embed("Choose a system from vehicle autocomplete."),
                ephemeral=True,
            )
            return
        state = self._vehicle_settle(u, model)
        spec = VEHICLE_CATALOG[model]
        label = str(spec["label"])
        building = _parse(state.get("building_until"))
        if asset_service.reserved(u, "vehicle", model):
            await ui.respond(
                interaction,
                embed=ui.warn_embed(
                    "That vehicle is reserved by an incoming Raven Rock recovery. Finish it first."
                ),
                ephemeral=True,
            )
            return
        if state.get("owned"):
            await ui.respond(
                interaction, embed=ui.warn_embed(f"You already own a **{label}**."), ephemeral=True
            )
            return
        if building is not None:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(
                    f"Your **{label}** is already under construction — ready <t:{int(building.timestamp())}:R>."
                ),
                ephemeral=True,
            )
            return
        cost = int(cfg.get(f"economy.{spec['cost']}", int(spec["fallback_cost"])))
        active_event = gevents.active(self.econ.store.load(gid))
        if active_event == "armsexpo":
            cost = cost * 85 // 100
        elif active_event == "shortage":
            cost = cost * 115 // 100
        if self._net(u) < cost:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"A **{label}** costs {self.money(cfg, cost)}."),
                ephemeral=True,
            )
            return
        before = self._net(u)
        self._take(u, cost)
        build_hours = float(cfg.get(f"economy.{spec['build']}", float(spec["fallback_build"])))
        if active_event == "mobilization":
            build_hours *= 0.75
        done = _now() + dt.timedelta(hours=build_hours)
        state["building_until"] = done.isoformat()
        state["owned"] = False
        if model == "b2":
            state["armed"] = False
            state["arming_until"] = None
        if model == "b52":
            state["b52_repair_count"] = 0
            state["b52_damaged"] = False
            state["b52_repairing_until"] = None
        if model in LOADABLE_VEHICLES:
            state["ammo"] = 0
            state["loading_until"] = None
            state["loading_qty"] = 0
        if model == "apache":
            state["comanche_upgraded"] = False
            state["comanche_upgrade_until"] = None
            state["comanche_damaged"] = False
            state["comanche_repair_cost"] = 0
            state["comanche_repairing_until"] = None
        if model in ARMORED_MODELS:
            damaged_key, cost_key, repairing_key = _armored_damage_fields(model)
            state[damaged_key] = False
            state[cost_key] = 0
            state[repairing_key] = None
            state["garrison_country"] = None
            state["garrison_transfer_until"] = None
            state["garrison_transfer_target"] = None
        await self.persist(gid)
        await self._log(gid, interaction.user.id, self._net(u) - before, f"vehicle-{model}-build", u)
        embed = ui.base_embed(
            title=f"🏭 {label} — construction begun",
            description=f"{interaction.user.mention} commissioned a **{label}** for {self.money(cfg, cost)}. It enters the hangar <t:{int(done.timestamp())}:R>.",
            color=cfg.color,
        )
        build_art = {
            "sr71": "sr71-build",
            "m1a2": "m1a2",
            "leopard2a7": "leopard2a7",
            "javelin": "javelin",
            "a10": "a10",
            "su34": "su34",
        }.get(model)
        if model in air.JETS:
            build_art = model + "-build"
        art = self._strategic_art(cfg, embed, build_art) if build_art else None
        if art is not None:
            await ui.respond(interaction, embed=embed, file=art)
        else:
            await ui.respond(interaction, embed=embed)

    @vehicle_group.command(name="load", description="Purchase and load ammunition for a strategic system.")
    @app_commands.describe(vehicle="System to arm", qty="Ammunition units to load")
    @app_commands.autocomplete(vehicle=_vehicle_load_autocomplete)
    async def vehicle_load(
        self, interaction: discord.Interaction, vehicle: str, qty: app_commands.Range[int, 1, 10] = 1
    ) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid = interaction.guild_id
        u = self.user(gid, interaction.user.id)
        model = str(getattr(vehicle, "value", vehicle)).lower().strip()
        if model not in LOADABLE_VEHICLES:
            await ui.respond(
                interaction,
                embed=ui.error_embed("Choose a loadable system from autocomplete."),
                ephemeral=True,
            )
            return
        spec = VEHICLE_CATALOG[model]
        state = self._vehicle_settle(u, model)
        label = str(spec["short"])
        if model in air.JETS and state.get("jet_damaged"):
            await ui.respond(
                interaction, embed=ui.error_embed("Repair this jet before loading packages."), ephemeral=True
            )
            return
        if not state.get("owned"):
            building = _parse(state.get("building_until"))
            msg = (
                f"Your {label} is under construction until <t:{int(building.timestamp())}:R>."
                if building is not None
                else f"You need an operational {label} first."
            )
            await ui.respond(interaction, embed=ui.error_embed(msg), ephemeral=True)
            return
        if model == "b52" and state.get("b52_damaged"):
            repairing = _parse(state.get("b52_repairing_until"))
            message = (
                f"The B-52H is undergoing depot repair until <t:{int(repairing.timestamp())}:R>."
                if repairing is not None
                else "The B-52H is grounded with battle damage. Use `/vehicle repair` first."
            )
            await ui.respond(interaction, embed=ui.error_embed(message), ephemeral=True)
            return
        if model == "apache":
            upgrading = _parse(state.get("comanche_upgrade_until"))
            if upgrading is not None:
                await ui.respond(
                    interaction,
                    embed=ui.error_embed(
                        f"The RAH-66 conversion occupies the aircraft until <t:{int(upgrading.timestamp())}:R>."
                    ),
                    ephemeral=True,
                )
                return
            if state.get("comanche_upgraded") and state.get("comanche_damaged"):
                repairing = _parse(state.get("comanche_repairing_until"))
                quote = max(0, int(state.get("comanche_repair_cost", 0)))
                await ui.respond(
                    interaction,
                    embed=ui.error_embed(
                        f"The RAH-66 Comanche is undergoing depot repair until <t:{int(repairing.timestamp())}:R>."
                        if repairing is not None
                        else "The RAH-66 Comanche is grounded with battle damage. Use `/vehicle repair`"
                        + (f" for {self.money(cfg, quote)}." if quote else ".")
                    ),
                    ephemeral=True,
                )
                return
        if model in ARMORED_MODELS:
            damaged_key, cost_key, repairing_key = _armored_damage_fields(model)
        else:
            damaged_key = cost_key = repairing_key = ""
        if model in ARMORED_MODELS and state.get(damaged_key):
            repairing = _parse(state.get(repairing_key))
            quote = max(0, int(state.get(cost_key, 0)))
            label = self._vehicle_label(model)
            message = (
                f"The {label} is undergoing depot repair until <t:{int(repairing.timestamp())}:R>."
                if repairing is not None
                else f"The {label} is grounded with battle damage. Use `/vehicle repair`"
                + (f" for {self.money(cfg, quote)}." if quote else ".")
            )
            await ui.respond(interaction, embed=ui.error_embed(message), ephemeral=True)
            return
        loading = _parse(state.get("loading_until"))
        if loading is not None:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(f"A loading cycle already completes <t:{int(loading.timestamp())}:R>."),
                ephemeral=True,
            )
            return
        cap = int(cfg.get(f"economy.{spec['cap']}", int(spec["fallback_cap"])))
        loaded = max(0, int(state.get("ammo", 0)))
        room = max(0, cap - loaded)
        if room <= 0:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(f"The {label} is fully loaded (**{loaded}/{cap}**)."),
                ephemeral=True,
            )
            return
        batch_cap = 2 if model in ARMORED_MODELS else 1 if model == "javelin" else int(qty)
        want = min(int(qty), room, batch_cap)
        unit = int(cfg.get(f"economy.{spec['ammo_cost']}", int(spec["fallback_ammo_cost"])))
        active_event = gevents.active(self.econ.store.load(gid))
        if active_event == "armsexpo":
            unit = unit * 85 // 100
        elif active_event == "shortage":
            unit = unit * 115 // 100
        cost = want * unit
        if self._net(u) < cost:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"Loading {want} × {spec['ammo']} costs {self.money(cfg, cost)}."),
                ephemeral=True,
            )
            return
        before = self._net(u)
        self._take(u, cost)
        minutes = float(cfg.get(f"economy.{spec['load']}", float(spec["fallback_load"])))
        if active_event == "mobilization":
            minutes *= 0.75
        if minutes <= 0:
            state["ammo"] = loaded + want
            ready_text = "Ammunition is ready immediately."
        else:
            done = _now() + dt.timedelta(minutes=minutes)
            state["loading_until"] = done.isoformat()
            state["loading_qty"] = want
            ready_text = f"Loading completes <t:{int(done.timestamp())}:R>."
        await self.persist(gid)
        await self._log(gid, interaction.user.id, self._net(u) - before, f"vehicle-{model}-load", u)
        if model in air.JETS:
            await air_commands.result(
                self,
                interaction,
                cfg,
                model,
                "load",
                f"Purchased {want} mission package(s) for {self.money(cfg, cost)}. {ready_text} Capacity: {loaded + want}/{cap}.",
            )
            return
        await ui.respond(
            interaction,
            embed=ui.base_embed(
                title=f"🔧 {spec['ammo']} loading",
                description=f"{interaction.user.mention} purchased **{want}** round(s) for {self.money(cfg, cost)}.\n{ready_text}\n**Capacity after loading:** {loaded + want}/{cap}",
                color=cfg.color,
            ),
        )

    @vehicle_group.command(
        name="patrol", description="Protect yourself or one coalition ally with an F-22 combat air patrol."
    )
    async def vehicle_patrol(
        self, interaction: discord.Interaction, target: Optional[discord.Member] = None
    ) -> None:
        await air_commands.patrol(self, interaction, target)

    @vehicle_group.command(
        name="link", description="Use an F-35 to create a single-use recon-linked vehicle strike bonus."
    )
    async def vehicle_link(
        self,
        interaction: discord.Interaction,
        target: discord.Member,
        recipient: Optional[discord.Member] = None,
    ) -> None:
        cfg = await self._guard(interaction, ephemeral=False)
        if cfg is None:
            return
        await air_commands.execute(self, interaction, target, "f35", recipient=recipient)

    @vehicle_group.command(
        name="upgrade", description="Convert your Apache into an RAH-66 Comanche ammunition hunter."
    )
    async def vehicle_upgrade(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid = interaction.guild_id
        u = self.user(gid, interaction.user.id)
        state = self._vehicle_settle(u, "apache")
        if not state.get("owned"):
            building = _parse(state.get("building_until"))
            message = (
                f"Your AH-64E is still being built until <t:{int(building.timestamp())}:R>."
                if building
                else "You need an operational AH-64E Apache Guardian first."
            )
            await ui.respond(interaction, embed=ui.error_embed(message), ephemeral=True)
            return
        if state.get("comanche_upgraded"):
            if state.get("comanche_damaged"):
                repairing = _parse(state.get("comanche_repairing_until"))
                quote = max(0, int(state.get("comanche_repair_cost", 0)))
                if repairing is not None:
                    message = f"Your RAH-66 Comanche is undergoing depot repair until <t:{int(repairing.timestamp())}:R>."
                else:
                    message = "Your RAH-66 Comanche is damaged. Use `/vehicle repair`"
                    if quote:
                        message += f" for {self.money(cfg, quote)}."
                    else:
                        message += "."
                await ui.respond(interaction, embed=ui.error_embed(message), ephemeral=True)
            else:
                await ui.respond(
                    interaction,
                    embed=ui.warn_embed("Your aircraft already carries the RAH-66 Comanche package."),
                    ephemeral=True,
                )
            return
        upgrading = _parse(state.get("comanche_upgrade_until"))
        if upgrading is not None:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(f"The conversion finishes <t:{int(upgrading.timestamp())}:R>."),
                ephemeral=True,
            )
            return
        loading = _parse(state.get("loading_until"))
        if loading is not None:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"Finish the active JAGM loading cycle <t:{int(loading.timestamp())}:R> first."
                ),
                ephemeral=True,
            )
            return
        cost = int(cfg.get("economy.vehicle_comanche_upgrade_cost", 1000000000))
        active_event = gevents.active(self.econ.store.load(gid))
        if active_event == "armsexpo":
            cost = cost * 85 // 100
        elif active_event == "shortage":
            cost = cost * 115 // 100
        if self._net(u) < cost:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"The RAH-66 Comanche conversion costs {self.money(cfg, cost)}."),
                ephemeral=True,
            )
            return
        before = self._net(u)
        self._take(u, cost)
        hours = float(cfg.get("economy.vehicle_comanche_upgrade_hours", 6))
        if active_event == "mobilization":
            hours *= 0.75
        done = _now() + dt.timedelta(hours=max(0.01, hours))
        state["comanche_upgraded"] = False
        state["comanche_upgrade_until"] = done.isoformat()
        state["comanche_damaged"] = False
        state["comanche_repair_cost"] = 0
        state["comanche_repairing_until"] = None
        await self.persist(gid)
        await self._log(gid, interaction.user.id, self._net(u) - before, "vehicle-comanche-upgrade", u)
        embed = ui.base_embed(
            title="RAH-66 Comanche conversion started",
            description=f"{interaction.user.mention}'s AH-64E entered a {self.money(cfg, cost)} low-observable conversion program. Existing loaded JAGMs remain aboard.\n\nConversion completes <t:{int(done.timestamp())}:F> · <t:{int(done.timestamp())}:R>\nOnce operational, {_comanche_rounds_per_mission(cfg)} JAGMs sweep every eligible ready ammunition and defensive-interceptor store in the target's arsenal.",
            color=cfg.color,
        )
        art = self._strategic_art(cfg, embed, "comanche-upgrade")
        kwargs: Dict[str, Any] = {"embed": embed}
        if art is not None:
            kwargs["file"] = art
        await ui.respond(interaction, **kwargs)

    @vehicle_group.command(name="repair", description="Repair a battle-damaged strategic vehicle.")
    @app_commands.describe(vehicle="Damaged vehicle; omit when only one needs repair")
    @app_commands.choices(
        vehicle=[
            app_commands.Choice(name="B-52H Stratofortress", value="b52"),
            app_commands.Choice(name="RAH-66 Comanche", value="comanche"),
            app_commands.Choice(name="M1A2 SEPv3 Abrams", value="m1a2"),
            app_commands.Choice(name="Leopard 2A7A1", value="leopard2a7"),
            *[app_commands.Choice(name=spec[1], value=model) for model, spec in air.JETS.items()],
        ]
    )
    async def vehicle_repair(
        self, interaction: discord.Interaction, vehicle: Optional[app_commands.Choice[str]] = None
    ) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid = interaction.guild_id
        u = self.user(gid, interaction.user.id)
        if vehicle is not None and vehicle.value in air.JETS:
            await air_commands.repair(self, interaction, vehicle.value, cfg)
            return
        b52 = self._vehicle_settle(u, "b52")
        apache = self._vehicle_settle(u, "apache")
        armored = {model: self._vehicle_settle(u, model) for model in ARMORED_MODELS}
        candidates: List[str] = []
        if b52.get("owned") and b52.get("b52_damaged"):
            candidates.append("b52")
        if apache.get("owned") and apache.get("comanche_upgraded") and apache.get("comanche_damaged"):
            candidates.append("comanche")
        for model, tank in armored.items():
            damaged_key, _, _ = _armored_damage_fields(model)
            if tank.get("owned") and tank.get(damaged_key):
                candidates.append(model)
        if vehicle is not None:
            selected = vehicle.value
        elif len(candidates) == 1:
            selected = candidates[0]
        elif len(candidates) > 1:
            await ui.respond(
                interaction,
                embed=ui.error_embed("Several vehicles need work. Choose one in the `vehicle` option."),
                ephemeral=True,
            )
            return
        else:
            await ui.respond(
                interaction,
                embed=ui.warn_embed("None of your supported vehicles currently require repairs."),
                ephemeral=True,
            )
            return
        if selected == "b52":
            if not b52.get("owned"):
                await ui.respond(
                    interaction,
                    embed=ui.error_embed("You do not own a recoverable B-52H airframe."),
                    ephemeral=True,
                )
                return
            if not b52.get("b52_damaged"):
                await ui.respond(
                    interaction, embed=ui.warn_embed("Your B-52H does not require repairs."), ephemeral=True
                )
                return
            repairing = _parse(b52.get("b52_repairing_until"))
            if repairing is not None:
                await ui.respond(
                    interaction,
                    embed=ui.warn_embed(
                        f"The B-52H depot repair already completes <t:{int(repairing.timestamp())}:R>."
                    ),
                    ephemeral=True,
                )
                return
            phase = max(1, min(3, int(b52.get("b52_repair_count", 1))))
            cost, hours = _b52_repair_terms(cfg, phase)
            if self._net(u) < cost:
                await ui.respond(
                    interaction,
                    embed=ui.error_embed(f"B-52H repair phase {phase}/3 costs {self.money(cfg, cost)}."),
                    ephemeral=True,
                )
                return
            before = self._net(u)
            self._take(u, cost)
            done = _now() + dt.timedelta(hours=hours)
            b52["b52_repairing_until"] = done.isoformat()
            await self.persist(gid)
            await self._log(gid, interaction.user.id, self._net(u) - before, "vehicle-b52-repair", u)
            await ui.respond(
                interaction,
                embed=ui.base_embed(
                    title=f"🔧 B-52H depot repair {phase}/3 started",
                    description=f"{interaction.user.mention} paid {self.money(cfg, cost)} ({B52_REPAIR_COST_PCTS[phase - 1]}% of the original airframe price).\n\nRepair completes <t:{int(done.timestamp())}:F> · <t:{int(done.timestamp())}:R>. All bomb sticks were lost and must be loaded again afterward.",
                    color=cfg.color,
                ),
            )
            return
        if selected in ARMORED_MODELS:
            tank = armored[selected]
            damaged_key, cost_key, repairing_key = _armored_damage_fields(selected)
            label = self._vehicle_label(selected)
            if not tank.get("owned"):
                await ui.respond(
                    interaction,
                    embed=ui.error_embed(f"You do not own a recoverable {label}."),
                    ephemeral=True,
                )
                return
            if not tank.get(damaged_key):
                await ui.respond(
                    interaction,
                    embed=ui.warn_embed(f"Your {label} does not require repairs."),
                    ephemeral=True,
                )
                return
            repairing = _parse(tank.get(repairing_key))
            if repairing is not None:
                await ui.respond(
                    interaction,
                    embed=ui.warn_embed(
                        f"The {label} depot repair already completes <t:{int(repairing.timestamp())}:R>."
                    ),
                    ephemeral=True,
                )
                return
            cost = max(0, int(tank.get(cost_key, 0)))
            if cost <= 0:
                cost = _armored_repair_quote(cfg, selected)
                tank[cost_key] = cost
            if self._net(u) < cost:
                await self.persist(gid)
                await ui.respond(
                    interaction,
                    embed=ui.error_embed(f"Restoring the {label} costs {self.money(cfg, cost)}."),
                    ephemeral=True,
                )
                return
            before = self._net(u)
            self._take(u, cost)
            hours = max(0.01, float(cfg.get(f"economy.vehicle_{selected}_repair_hours", 1.5)))
            done = _now() + dt.timedelta(hours=hours)
            tank[repairing_key] = done.isoformat()
            tank[cost_key] = 0
            await self.persist(gid)
            await self._log(gid, interaction.user.id, self._net(u) - before, f"vehicle-{selected}-repair", u)
            embed = ui.base_embed(
                title=f"🔧 {label} depot repair started",
                description=f"{interaction.user.mention} paid {self.money(cfg, cost)} to restore the battle-damaged {label}. Its surviving {VEHICLE_CATALOG[selected]['ammo']} rounds remain aboard. Repair completes <t:{int(done.timestamp())}:F> · <t:{int(done.timestamp())}:R>.",
                color=cfg.color,
            )
            art = self._strategic_art(
                cfg, embed, "m1a2-repair" if selected == "m1a2" else "leopard2a7-repair"
            )
            kwargs: Dict[str, Any] = {"embed": embed}
            if art is not None:
                kwargs["file"] = art
            await ui.respond(interaction, **kwargs)
            return
        if not apache.get("owned") or not apache.get("comanche_upgraded"):
            await ui.respond(
                interaction,
                embed=ui.error_embed("You do not own an operational RAH-66 Comanche."),
                ephemeral=True,
            )
            return
        if not apache.get("comanche_damaged"):
            await ui.respond(
                interaction,
                embed=ui.warn_embed("Your RAH-66 Comanche does not require repairs."),
                ephemeral=True,
            )
            return
        repairing = _parse(apache.get("comanche_repairing_until"))
        if repairing is not None:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(
                    f"The RAH-66 Comanche depot repair already completes <t:{int(repairing.timestamp())}:R>."
                ),
                ephemeral=True,
            )
            return
        cost = max(0, int(apache.get("comanche_repair_cost", 0)))
        if cost <= 0:
            cost = _comanche_repair_quote(cfg)
            apache["comanche_repair_cost"] = cost
        if self._net(u) < cost:
            await self.persist(gid)
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"Restoring the RAH-66 Comanche costs {self.money(cfg, cost)}."),
                ephemeral=True,
            )
            return
        before = self._net(u)
        self._take(u, cost)
        hours = max(0.01, float(cfg.get("economy.vehicle_comanche_repair_hours", 2)))
        done = _now() + dt.timedelta(hours=hours)
        apache["comanche_repairing_until"] = done.isoformat()
        apache["comanche_repair_cost"] = 0
        await self.persist(gid)
        await self._log(gid, interaction.user.id, self._net(u) - before, "vehicle-comanche-repair", u)
        await ui.respond(
            interaction,
            embed=ui.base_embed(
                title="🔧 RAH-66 Comanche depot repair started",
                description=f"{interaction.user.mention} paid {self.money(cfg, cost)} to repair the battle-damaged airframe. The Comanche package remains installed, but the aircraft stays grounded until <t:{int(done.timestamp())}:F> · <t:{int(done.timestamp())}:R>. Lost JAGMs must be reloaded separately.",
                color=cfg.color,
            ),
        )

    @vehicle_group.command(name="garrison", description="Station a frontline tank in one of your countries.")
    @app_commands.describe(vehicle="Armored vehicle", country="Country to defend")
    @app_commands.choices(
        vehicle=[
            app_commands.Choice(name="M1A2 SEPv3 Abrams", value="m1a2"),
            app_commands.Choice(name="Leopard 2A7A1", value="leopard2a7"),
        ]
    )
    @app_commands.autocomplete(country=_owned_country_autocomplete)
    async def vehicle_garrison(
        self, interaction: discord.Interaction, vehicle: app_commands.Choice[str], country: str
    ) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid = interaction.guild_id
        user = self.user(gid, interaction.user.id)
        model = vehicle.value
        tank = self._vehicle_settle(user, model)
        label = self._vehicle_label(model)
        damaged_key, _, repairing_key = _armored_damage_fields(model)
        if not tank.get("owned"):
            await ui.respond(
                interaction, embed=ui.error_embed(f"You need an operational {label} first."), ephemeral=True
            )
            return
        if tank.get(damaged_key) or _parse(tank.get(repairing_key)) is not None:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"Repair the battle-damaged {label} before assigning it."),
                ephemeral=True,
            )
            return
        transfer = _parse(tank.get("garrison_transfer_until"))
        if transfer is not None:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(
                    f"The {label} is already redeploying until <t:{int(transfer.timestamp())}:R>."
                ),
                ephemeral=True,
            )
            return
        other_model = next((item for item in ARMORED_MODELS if item != model))
        other_tank = self._vehicle_settle(user, other_model)
        other_transfer = _parse(other_tank.get("garrison_transfer_until"))
        if other_tank.get("garrison_country") or other_transfer is not None:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"Withdraw your {self._vehicle_label(other_model)} before assigning another frontline tank."
                ),
                ephemeral=True,
            )
            return
        last = _parse(tank.get("last_deploy_at"))
        cooldown = self._vehicle_cooldown_seconds(cfg, model)
        if last is not None and (_now() - last).total_seconds() < cooldown:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(
                    f"The {label} is still in mission turnaround until <t:{int(last.timestamp() + cooldown)}:R>."
                ),
                ephemeral=True,
            )
            return
        country_id = str(country).strip().casefold()
        selected = country_state.BY_ID.get(country_id)
        territory = country_state.state(self.econ.store.load(gid))["territories"].get(country_id)
        if (
            selected is None
            or not isinstance(territory, dict)
            or int(territory.get("owner", 0)) != int(interaction.user.id)
        ):
            await ui.respond(
                interaction, embed=ui.error_embed("Choose a country you currently rule."), ephemeral=True
            )
            return
        if tank.get("garrison_country") == country_id:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(f"Your {label} already garrisons {selected.flag} {selected.name}."),
                ephemeral=True,
            )
            return
        minutes = max(1, int(cfg.get(f"economy.vehicle_{model}_garrison_move_minutes", 30)))
        done = _now() + dt.timedelta(minutes=minutes)
        tank["garrison_country"] = None
        tank["garrison_transfer_target"] = country_id
        tank["garrison_transfer_until"] = done.isoformat()
        await self.persist(gid)
        await ui.respond(
            interaction,
            embed=ui.base_embed(
                title=f"{VEHICLE_CATALOG[model]['emoji']} {label} garrison movement ordered",
                description=f"{interaction.user.mention}'s {label} is moving to {selected.flag} **{selected.name}**. It becomes active <t:{int(done.timestamp())}:R> and contributes **+{int(cfg.get(f'economy.vehicle_{model}_garrison_defense', 25))} defense power** while loaded.",
                color=cfg.color,
            ),
        )

    @vehicle_group.command(name="withdraw", description="Withdraw a garrisoned frontline tank to its hangar.")
    @app_commands.describe(vehicle="Armored vehicle")
    @app_commands.choices(
        vehicle=[
            app_commands.Choice(name="M1A2 SEPv3 Abrams", value="m1a2"),
            app_commands.Choice(name="Leopard 2A7A1", value="leopard2a7"),
        ]
    )
    async def vehicle_withdraw(
        self, interaction: discord.Interaction, vehicle: app_commands.Choice[str]
    ) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid = interaction.guild_id
        user = self.user(gid, interaction.user.id)
        model = vehicle.value
        tank = self._vehicle_settle(user, model)
        label = self._vehicle_label(model)
        transfer = _parse(tank.get("garrison_transfer_until"))
        if transfer is not None:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(
                    f"The {label} is already redeploying until <t:{int(transfer.timestamp())}:R>."
                ),
                ephemeral=True,
            )
            return
        country_id = str(tank.get("garrison_country") or "")
        if not tank.get("owned") or not country_id:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(f"Your {label} is not assigned to a country garrison."),
                ephemeral=True,
            )
            return
        selected = country_state.BY_ID.get(country_id)
        minutes = max(1, int(cfg.get(f"economy.vehicle_{model}_withdraw_minutes", 15)))
        done = _now() + dt.timedelta(minutes=minutes)
        tank["garrison_country"] = None
        tank["garrison_transfer_target"] = None
        tank["garrison_transfer_until"] = done.isoformat()
        await self.persist(gid)
        await ui.respond(
            interaction,
            embed=ui.base_embed(
                title=f"{VEHICLE_CATALOG[model]['emoji']} {label} withdrawal ordered",
                description=f"The {label} is leaving {(selected.flag + ' ' + selected.name if selected else country_id.upper())} and returns to its hangar <t:{int(done.timestamp())}:R>. It provides no defense while moving.",
                color=cfg.color,
            ),
        )

    @vehicle_group.command(name="arm", description="Convert one completed ICBM into a B61-12 B-2 payload.")
    async def vehicle_arm(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid = interaction.guild_id
        u = self.user(gid, interaction.user.id)
        b2 = self._vehicle_settle(u, "b2")
        ready = self._icbm_settle(u)
        if not b2.get("owned"):
            await ui.respond(
                interaction, embed=ui.error_embed("You need an operational B-2 Spirit first."), ephemeral=True
            )
            return
        arming = _parse(b2.get("arming_until"))
        if b2.get("armed") or arming is not None:
            msg = (
                f"Payload integration finishes <t:{int(arming.timestamp())}:R>."
                if arming is not None
                else "The B-2 is already carrying a B61-12 payload."
            )
            await ui.respond(interaction, embed=ui.warn_embed(msg), ephemeral=True)
            return
        if ready < 1:
            await ui.respond(
                interaction,
                embed=ui.error_embed("No completed ICBM is available for payload conversion."),
                ephemeral=True,
            )
            return
        u["icbm_ready"] = ready - 1
        done = _now() + dt.timedelta(hours=float(cfg.get("economy.vehicle_b2_arm_hours", 1)))
        b2["arming_until"] = done.isoformat()
        b2["armed"] = False
        await self.persist(gid)
        await self.bot.ledger.record(gid, interaction.user.id, 0, "vehicle-b2-arm", after=self._net(u))
        embed = ui.base_embed(
            title="☢️ B61-12 payload conversion",
            description=f"{interaction.user.mention} surrendered one completed ICBM. Its strategic package will be integrated into the B-2 <t:{int(done.timestamp())}:R>.\n\n**ICBMs remaining:** {int(u.get('icbm_ready', 0))}",
            color=cfg.color,
        )
        await ui.respond(interaction, embed=embed)

    @vehicle_group.command(
        name="deploy", description="Authorize a strategic strike or reconnaissance mission."
    )
    @app_commands.describe(
        vehicle="System to deploy",
        target="Mission target",
        objective="Weapon objective; frontline tanks use the target country",
        secondary_objective="MQ-9B only: optional second consumable stack",
    )
    @app_commands.choices(
        vehicle=[
            app_commands.Choice(name="B-2 Spirit — strategic strike", value="b2"),
            app_commands.Choice(name="B-52H Stratofortress — one-hour attack shutdown", value="b52"),
            app_commands.Choice(name="U-2S Dragon Lady — reconnaissance", value="u2"),
            app_commands.Choice(name="F3 DeathMARK Tracker — reciprocal intelligence", value="deimos"),
            app_commands.Choice(name="SR-71A Blackbird — full defense reconnaissance", value="sr71"),
            app_commands.Choice(name="DDG-1000 EMRG — 40% donut strike", value="zumwalt"),
            app_commands.Choice(name="Virginia Block V — fisheries raid", value="virginia"),
            app_commands.Choice(name="M142 HIMARS — air-defense strike", value="himars"),
            app_commands.Choice(name="AH-64E Apache / RAH-66 Comanche — ammunition raid", value="apache"),
            app_commands.Choice(name="MQ-9B SkyGuardian — consumable strike", value="mq9"),
            app_commands.Choice(name="F-15E Strike Eagle — 50% visible-money strike", value="f15e"),
            app_commands.Choice(name="A-10C Thunderbolt II — high-risk battlefield sweep", value="a10"),
            app_commands.Choice(name="Su-34 Fullback — safer battlefield interdiction", value="su34"),
            app_commands.Choice(
                name="C-130J Rapid Dragon — distributed / concentrated ammunition raid", value="c130j"
            ),
            app_commands.Choice(name="CHAMP — 45-minute electronics blackout", value="champ"),
            app_commands.Choice(name="ADM-160 MALD-X — interceptor decoy", value="maldx"),
            app_commands.Choice(name="LRHW Dark Eagle — construction strike", value="lrhw"),
            app_commands.Choice(name="XB-70 Valkyrie — 70–80% visible-money strike", value="xb70"),
            app_commands.Choice(name="M1A2 SEPv3 Abrams — country armored breach", value="m1a2"),
            app_commands.Choice(name="Leopard 2A7A1 — stronger country armored breach", value="leopard2a7"),
        ]
    )
    @app_commands.autocomplete(
        objective=_strategic_objective_autocomplete, secondary_objective=_strategic_secondary_autocomplete
    )
    async def vehicle_deploy(
        self,
        interaction: discord.Interaction,
        vehicle: app_commands.Choice[str],
        target: discord.Member,
        objective: Optional[str] = None,
        secondary_objective: Optional[str] = None,
    ) -> None:
        cfg = await self._guard(interaction, ephemeral=vehicle.value not in air.JETS)
        if cfg is None:
            return
        if vehicle.value in air.JETS:
            if vehicle.value == "f22":
                await air_commands.patrol(self, interaction, target)
            else:
                await air_commands.execute(self, interaction, target, vehicle.value, objective)
            return
        error = self._mission_preflight(
            interaction.guild_id, interaction.user.id, vehicle.value, target, objective, secondary_objective
        )
        if error:
            await ui.respond(interaction, embed=ui.error_embed(error), ephemeral=True)
            return
        model = vehicle.value
        if model == "b2":
            intercept = _public_s400_intercept_chance(cfg, "b2")
            details = f"Authorize a **B-2 Spirit** strike against {target.mention}?\n\n• The armed B61-12 payload will be consumed.\n• Existing Radar AA is bypassed.\n• A loaded S-400 has a **{intercept}%** chance to destroy the B-2 permanently.\n• A hit wipes all eligible donuts, consumables and fish."
        elif model == "b52":
            minutes = int(cfg.get("economy.vehicle_b52_lockdown_minutes", 60))
            wallet_pct = int(cfg.get("economy.vehicle_b52_wallet_damage_pct", 25))
            bank_pct = int(cfg.get("economy.vehicle_b52_bank_damage_pct", 15))
            intercept = _public_s400_intercept_chance(cfg, "b52")
            details = f"Send a **B-52H Stratofortress** to carpet-bomb {target.mention}'s runway and strategic operations hub?\n\n• A hit blocks ICBM launches, vehicle deployments and country invasions for **{minutes} minutes**.\n• Carpet bombing destroys **{wallet_pct}% of wallet donuts** and **{bank_pct}% of bank-vault donuts**.\n• The Federal Reserve Bedrock Deep Vault is completely immune, even with reconnaissance.\n• Building, arming and loading remain available; automatic defenses stay online.\n• A loaded S-400 has a **{intercept}%** chance to force down the bomber.\n• Its first three shootdowns unlock depot repairs at 25%/50%/75% of the original price, taking 1/2/3 hours; the fourth destroys that airframe permanently.\n• One Mk 82 bomb stick is consumed and the two-hour turnaround starts either way.\n• Repeated hits reset the shutdown timer; their durations never add together."
        elif model == "u2":
            hours = float(cfg.get("economy.u2_recon_window_hours", 3))
            base = int(cfg.get("economy.aa_rocket_chance", 30))
            shield_bonus = int(cfg.get("economy.aa_shield_vehicle_bonus", 10))
            details = f"Authorize a **U-2S Dragon Lady** reconnaissance flight over {target.mention}?\n\nA loaded regular AA rocket gets one **{base}% base** interception attempt; an active AA Shield adds **+{shield_bonus} percentage points**. If it hits, your U-2S is permanently destroyed; otherwise you receive a private {hours:g}-hour unified target package for located-vault, completed-vehicle and construction strikes."
        elif model == "deimos":
            chance = _deimos_success_chance(cfg)
            hours = float(cfg.get("economy.deimos_recon_window_hours", 4))
            details = f"Release an **F3 DeathMARK Tracker** against {target.mention}?\n\n• The scan has a flat **{chance}%** chance to succeed; defenses cannot change it.\n• Success publicly reveals both exact Bedrock Vault balances and gives each player a {hours:g}-hour unified target package and defensive-readiness report on the other.\n• Failure reveals nothing and permanently destroys the tracker.\n• Both players are publicly pinged either way."
        elif model == "sr71":
            loss = max(0, min(100, int(cfg.get("economy.vehicle_sr71_shotdown_chance", 20))))
            cooldown = float(cfg.get("economy.vehicle_sr71_cooldown_hours", 1))
            details = f"Authorize an **SR-71A Blackbird** reconnaissance pass over {target.mention}?\n\n• Ground engagement requires a loaded S-400 40N6E or Patriot PAC-3 MSE; regular AA cannot. An interceptor is spent whether it hits or misses.\n• A missile engagement has a **{loss}%** aircraft-loss chance. Without a ready long-range missile, the pass cannot be shot down.\n• A successful pass privately reports every strategic defense as active, empty, building, offline or absent.\n• The snapshot includes ammunition and active build/load timers for Radar AA, AA Shield, S-400, Aegis/SM-6, P-8A, Patriot, Aegis BMD/SM-3 and GBI/EKV.\n• The {cooldown:g}-hour package locates the Bedrock Vault, identifies completed vehicles for A-10C/Su-34 and tracks conventional builds for Dark Eagle.\n• It also locates the Bedrock Vault and creates the same unified targeting package as U-2/Deimos. No ammunition is required.\n• The {cooldown:g}-hour mission cooldown starts whether the aircraft returns or is lost."
        elif model == "zumwalt":
            pct = max(1, min(99, int(cfg.get("economy.vehicle_zumwalt_damage_pct", 40))))
            details = f"Fire a **Hyper Velocity Projectile** at {target.mention}?\n\n• Destroys {pct}% of wallet and bank reserves.\n• Active U-2S or Deimos intelligence extends the strike into the Bedrock Vault.\n• A loaded Aegis/SM-6 defense has a 30% interception chance.\n• No victim protection period is granted."
        elif model == "virginia":
            intercept = int(cfg.get("economy.vehicle_p8_intercept_chance", 30))
            details = f"Send a **Virginia-class Block V SSN** against {target.mention}'s fisheries?\n\n• Wipes every stored fish and stops autofishing.\n• 40% equipped-rod destruction chance; 20% for Android 21's Reel.\n• A loaded P-8A/Mk 54 defense has a {intercept}% chance to destroy the submarine.\n• No victim protection period is granted."
        elif model == "himars":
            objective_label = "regular AA rockets" if objective == "regular-aa" else "an S-400 interceptor"
            details = f"Launch an **M31A2 GMLRS** strike at {target.mention}'s {objective_label}?\n\n• Loaded regular AA is completely cleared, or one S-400 round is destroyed.\n• A loaded Patriot PAC-3 MSE has a 40% interception chance.\n• No victim protection period is granted."
        elif model == "apache":
            apache = self._vehicle_settle(self.user(interaction.guild_id, interaction.user.id), "apache")
            if apache.get("comanche_upgraded"):
                low, high = _comanche_intercept_range(cfg)
                damage = int(cfg.get("economy.vehicle_comanche_precision_damage_pct", 100))
                rounds = _comanche_rounds_per_mission(cfg)
                cooldown_hours = float(cfg.get("economy.vehicle_comanche_cooldown_hours", 8))
                repair_hours = float(cfg.get("economy.vehicle_comanche_repair_hours", 2))
                details = f"Send an **RAH-66 Comanche** all-store raid against {target.mention}?\n\n• Destroys **{damage}%** of every eligible completed, reserve and loaded ammunition category.\n• Includes offensive payloads and ammunition loaded into defensive systems.\n• Project THOR modules, rods and every THOR countermeasure are outside the strike window, including GBI/EKVs and BMD SM-3s.\n• Loaded regular AA receives a variable **{low}–{high}% base** shootdown solution; an active AA Shield adds **+{int(cfg.get('economy.aa_shield_vehicle_bonus', 10))} points**.\n• An interception grounds the Comanche instead of deleting it; `/vehicle repair` costs a persistent 100–200M quote and takes {repair_hours:g} hours.\n• **{rounds} AGM-179A JAGMs** and the {cooldown_hours:g}-hour cooldown are committed either way.\n• No victim protection period is granted."
            else:
                objective_labels = {
                    "regular-aa": "50% of the loaded regular AA rockets",
                    "s400": "one loaded S-400 40N6E interceptor",
                    "patriot": "one loaded Patriot PAC-3 MSE interceptor",
                    "himars": "up to two loaded HIMARS M31A2 rockets",
                }
                intercept = int(cfg.get("economy.vehicle_apache_aa_intercept_chance", 30))
                details = f"Send an **AH-64E Apache Guardian** against {target.mention}'s ground ammunition?\n\n• Selected objective: **{objective_labels.get(objective or '', objective or 'invalid')}**.\n• The mission launches even if that ammunition depot is empty.\n• Loaded regular AA has a base **{intercept}%** chance to destroy the Apache permanently; an active AA Shield adds **+{int(cfg.get('economy.aa_shield_vehicle_bonus', 10))} points**.\n• One AGM-179A JAGM and the 20-minute mission cooldown are consumed either way.\n• No victim protection period is granted."
        elif model == "mq9":
            primary = (objective or "").strip().lower()
            secondary = (secondary_objective or "").strip().lower() or None
            split = secondary is not None
            damage_key = (
                "economy.vehicle_mq9_split_damage_pct" if split else "economy.vehicle_mq9_item_damage_pct"
            )
            damage = int(cfg.get(damage_key, 60 if split else 100))
            intercept = int(cfg.get("economy.vehicle_mq9_aa_intercept_chance", 20))
            selected = [str(CONSUMABLES[primary]["name"])]
            if secondary is not None:
                selected.append(str(CONSUMABLES[secondary]["name"]))
            rounds = 2 if split else 1
            details = f"Send an **MQ-9B SkyGuardian** after {target.mention}'s consumable stockpiles?\n\n• Selected: **{' + '.join(selected)}**.\n• Destroys **{damage}%** of each selected stack, rounded down with a one-item minimum.\n• Plushies, rods and permanent collectibles cannot be selected.\n• Loaded regular AA has a base **{intercept}%** chance to destroy the drone; an active AA Shield adds **+{int(cfg.get('economy.aa_shield_vehicle_bonus', 10))} points**.\n• **{rounds} AGM-114R Hellfire{('s' if rounds != 1 else '')}** and the 15-minute cooldown are committed either way.\n• No victim protection period is granted."
        elif model == "f15e":
            details = f"Launch an **F-15E Strike Eagle** mission against {target.mention}?\n\n• Destroys 50% of visible wallet and bank reserves.\n• Cannot penetrate the Bedrock Vault or destroy items and fish.\n• A loaded Patriot PAC-3 MSE has a 40% chance to destroy the aircraft.\n• No victim protection period is granted."
        elif model in {"a10", "su34"}:
            prefix = "vehicle_a10" if model == "a10" else "vehicle_su34"
            kill = int(cfg.get(f"economy.{prefix}_vehicle_destroy_chance", 70 if model == "a10" else 50))
            payload_pct = 100 if model == "a10" else 50
            ammo_pct = int(cfg.get(f"economy.{prefix}_ammo_damage_pct", 70 if model == "a10" else 45))
            aa_pct = int(cfg.get(f"economy.{prefix}_regular_aa_damage_pct", 75 if model == "a10" else 50))
            wallet_pct = int(cfg.get(f"economy.{prefix}_wallet_damage_pct", 20 if model == "a10" else 15))
            bank_pct = int(cfg.get(f"economy.{prefix}_bank_damage_pct", 10 if model == "a10" else 8))
            target_label = self._vehicle_label((objective or "").strip().lower())
            intel_note = (
                "Active reconnaissance package available."
                if self._active_recon(
                    self.user(interaction.guild_id, interaction.user.id),
                    target.id,
                    self.user(interaction.guild_id, target.id),
                )
                else "No active target package; this is a blind selection."
            )
            if model == "a10":
                counter = f"one loaded Radar AA rocket at **{int(cfg.get('economy.vehicle_a10_aa_intercept_chance', 50))}% base**; an active AA Shield adds **+{int(cfg.get('economy.aa_shield_vehicle_bonus', 10))} points**. If Radar AA is empty, one loaded Patriot receives its normal 40% roll"
            else:
                counter = f"one loaded S-400 at **{int(cfg.get('economy.vehicle_su34_s400_intercept_chance', 25))}%**. If S-400 is empty, one loaded Patriot receives a **{int(cfg.get('economy.vehicle_su34_patriot_intercept_chance', 20))}%** roll"
            details = f"Send a **{self._vehicle_label(model)}** battlefield-interdiction mission against {target.mention}?\n\n• Vehicle objective: **{target_label}**. {intel_note}\n• A hit has a **{kill}%** chance to destroy that completed vehicle; otherwise **{payload_pct}%** of its loaded payload is destroyed.\n• Destroys **{ammo_pct}%** of the two fullest remaining offensive-ammunition stores and **{aa_pct}%** of loaded and reserve Radar AA.\n• Also destroys one loaded S-400 or Patriot round, **{wallet_pct}% wallet** and **{bank_pct}% bank**.\n• Deep Vault funds, items, fish, rods, countries, unfinished vehicles and every THOR asset are immune.\n• Counter: {counter}. Only one defense fires.\n• An intercept permanently destroys the aircraft; the strike package and cooldown are committed either way.\n• No victim protection period is granted."
        elif model == "c130j":
            pct = int(cfg.get("economy.vehicle_c130j_damage_pct", 40))
            targets = int(cfg.get("economy.vehicle_c130j_target_count", 3))
            mode = (
                f"Concentrated: destroys **80%** of **{CONVENTIONAL_AMMO_TARGETS.get(objective.split(':', 1)[1], objective)}**; requires current recon."
                if objective and objective.startswith("focused:")
                else f"Distributed: finds the {targets} fullest conventional ammunition stores and destroys **{pct}%** of each."
            )
            details = f"Release a **Rapid Dragon pallet** against {target.mention}?\n\n• {mode}\n• It cannot target ICBMs, B61-12s, Project THOR, rods, GBI/EKVs or BMD SM-3s.\n• A loaded Patriot has a 40% chance to destroy the package; the C-130J survives.\n• One pallet and the 90-minute cooldown are committed either way."
        elif model == "champ":
            minutes = int(cfg.get("economy.vehicle_champ_blackout_minutes", 45))
            details = f"Launch a **CHAMP microwave missile** against {target.mention}?\n\n• A hit suppresses regular AA, AA Shield, S-400, Aegis, P-8A and Patriot for **{minutes} minutes**.\n• U-2, Deimos and SR-71 launches are also blocked during the blackout.\n• THOR, GBI/EKV, BMD SM-3, balances and stored ammunition are unharmed.\n• A loaded Patriot may intercept at 40% before the pulse; the launch system survives."
        elif model == "maldx":
            chance = int(cfg.get("economy.vehicle_maldx_success_chance", 75))
            details = f"Send an **ADM-160 MALD-X** against {target.mention}'s `{objective}` defense?\n\n• **{chance}%** chance to spoof the selected system into wasting one loaded interceptor.\n• Failure consumes only the decoy. It deals no direct damage.\n• THOR countermeasures cannot be selected."
        elif model == "lrhw":
            target_label = self._vehicle_label((objective or "").strip().lower())
            pilot = self.user(interaction.guild_id, interaction.user.id)
            guided = (
                self._active_construction_intel(pilot, target.id, self.user(interaction.guild_id, target.id))
                is not None
            )
            details = (
                f"Launch a **Dark Eagle C-HGB** at {target.mention}'s unfinished **{target_label}**?\n\n• A valid hit cancels that conventional vehicle build completely with no refund.\n• The hypersonic glide body bypasses ordinary defenses.\n• Completed vehicles and every Project THOR asset are immune.\n"
                + (
                    "• **Recon guided:** this build was confirmed by your active construction snapshot."
                    if guided
                    else "• **Blind targeting:** if your guess is wrong, the round and cooldown are still consumed."
                )
            )
        elif model in ARMORED_MODELS:
            country = country_state.BY_ID.get((objective or "").strip().casefold())
            fort_pct = int(
                cfg.get(f"economy.vehicle_{model}_fortification_damage_pct", 25 if model == "m1a2" else 30)
            )
            breach_minutes = int(cfg.get(f"economy.vehicle_{model}_breach_minutes", 30))
            attack_bonus = int(cfg.get(f"economy.vehicle_{model}_breach_attack_bonus", 20))
            track = int(cfg.get("economy.vehicle_javelin_track_chance", 30))
            label = self._vehicle_label(model)
            protection = (
                f" If it tracks, Trophy APS has a **{int(cfg.get('economy.vehicle_m1a2_trophy_defeat_chance', 35))}%** chance to defeat the missile and let the breach continue."
                if model == "m1a2"
                else " The Leopard has no Trophy reroll; a successful track stops the breach."
            )
            details = f"Send a **{label}** to breach {(country.flag + ' ' + country.name if country else 'the selected country')} under {target.mention}?\n\n• A successful assault removes **{fort_pct}% of current fortification** and marks the country Breached for **{breach_minutes} minutes**.\n• Only you receive **+{attack_bonus} attack power** when invading that country during the breach.\n• GDP and development are never damaged; unclaimed countries cannot be breached.\n• A loaded Javelin Team gets a **{track}% base** track.{protection}\n• A stopped assault can leave the {label} battle-damaged; repairs cost 100–175M and take 90 minutes.\n• One {VEHICLE_CATALOG[model]['ammo']} round and the 45-minute mission cooldown are committed either way."
        else:
            minimum = int(cfg.get("economy.vehicle_xb70_damage_min_pct", 70))
            maximum = int(cfg.get("economy.vehicle_xb70_damage_max_pct", 80))
            intercept = int(cfg.get("economy.vehicle_xb70_s400_intercept_chance", 10))
            details = f"Send an **XB-70 Valkyrie** against {target.mention}?\n\n• A hit rolls once and destroys **{minimum}–{maximum}%** of wallet and bank.\n• The Bedrock Vault, items, fish and vehicles are untouched.\n• Ground engagement requires a loaded S-400, with a public **{intercept}%** shootdown chance.\n• A successful intercept permanently destroys the Valkyrie and its B53 bomb."
        if model in air.AIRCRAFT:
            details += "\n• An active F-22 patrol can also engage: 65% conventional aircraft, 20% B-2. Only the strongest eligible patrol or ground defence gets one attempt."
        view = StrategicMissionConfirmView(
            self, interaction.user.id, model, target, objective, secondary_objective
        )
        await ui.respond(
            interaction,
            embed=ui.base_embed(title="⚠️ Mission authorization", description=details, color=ui.COLOR_WARN),
            view=view,
            ephemeral=True,
        )
        view.message = await ui.response_message(interaction)

    async def _jammed_recon(
        self, interaction: discord.Interaction, model: str, observer: Dict[str, Any], target: discord.Member
    ) -> None:
        """Finish a paid sortie without leaking any snapshot or granting a lock."""
        observer.setdefault("recon_targets", {}).pop(str(target.id), None)
        observer.setdefault("sr71_construction_intel", {}).pop(str(target.id), None)
        await self.persist(interaction.guild_id)
        await self.bot.ledger.record(
            interaction.guild_id,
            interaction.user.id,
            0,
            "nyx-recon-jammed",
            after=self._net(observer),
            other=target.id,
            detail=model + " reconnaissance jammed",
        )
        await interaction.followup.send(
            embed=ui.warn_embed(
                "NYX Ghost Protocol obscured the scan. No intelligence or targeting authorization was recovered. The mission cooldown was consumed; jamming itself did not destroy the scout. Deimos never exposes either participant when one is cloaked.",
                title="RECONNAISSANCE JAMMED",
            ),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _unusable_recon(
        self, interaction: discord.Interaction, model: str, pilot: Dict[str, Any], target: discord.Member
    ) -> None:
        key = str(target.id)
        record = pilot.get("sr71_construction_intel", {}).get(key) or {}
        if not isinstance(record, dict) or record.get("source") != "deimos":
            pilot.setdefault("recon_targets", {}).pop(key, None)
            pilot.setdefault("sr71_construction_intel", {}).pop(key, None)
        await self.persist(interaction.guild_id)
        await self.bot.ledger.record(
            interaction.guild_id,
            interaction.user.id,
            0,
            f"vehicle-{model}-inconclusive",
            after=self._net(pilot),
            other=target.id,
        )
        await interaction.followup.send(
            embed=ui.warn_embed(
                "The reconnaissance flight returned, but no usable intelligence was recovered. No target package was issued. The aircraft's mission turnaround still applies.",
                title="RECONNAISSANCE INCONCLUSIVE",
            ),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _u2_execute(self, interaction: discord.Interaction, target: discord.Member) -> None:
        gid = interaction.guild_id
        cfg = self.cfg(gid)
        error = self._mission_preflight(gid, interaction.user.id, "u2", target)
        if error:
            await interaction.followup.send(embed=ui.error_embed(error), ephemeral=True)
            return
        pilot = self.user(gid, interaction.user.id)
        craft = self._vehicle_settle(pilot, "u2")
        victim = self.user(gid, target.id)
        craft["last_deploy_at"] = _now().isoformat()
        self._aa_settle(cfg, victim)
        loaded = int(victim.get("aa_rockets_loaded", 0))
        engaged = loaded > 0 and _electronic_blackout_until(victim) is None
        chance = _u2_intercept_chance(cfg, interaction.user.id, target.id, victim)
        patrol_choice = air_commands.strongest_patrol(
            self,
            gid,
            target.id,
            "u2",
            _public_light_aircraft_intercept_chance(cfg, victim, int(cfg.get("economy.aa_rocket_chance", 30)))
            if engaged
            else 0,
        )
        if gevents.is_active(self.econ.store.load(gid), "defensealert"):
            chance = min(100, chance + 10)
        intercepted = False
        if patrol_choice:
            intercepted = air_commands.engage(patrol_choice, cfg, interaction.user.id, "u2")
        elif engaged:
            victim["aa_rockets_loaded"] = loaded - 1
            intercepted = random.randint(1, 100) <= chance
        if intercepted:
            craft["owned"] = False
            craft["building_until"] = None
            await self.persist(gid)
            await self.bot.ledger.record(
                gid, interaction.user.id, 0, "vehicle-u2-lost", after=self._net(pilot), other=target.id
            )
            embed = ui.base_embed(
                title="💥 U-2S SHOT DOWN",
                description=f"{interaction.user.mention}'s U-2S crossed into {target.mention}'s airspace. "
                + (
                    "An F-22A combat air patrol destroyed the aircraft. "
                    if patrol_choice
                    else "A regular AA interceptor found it at altitude and destroyed the aircraft. "
                )
                + "No intelligence was recovered.",
                color=ui.COLOR_BAD,
            )
            art = self._strategic_art(cfg, embed, "u2")
            kwargs: Dict[str, Any] = {
                "content": f"{interaction.user.mention} {target.mention}",
                "embed": embed,
                "allowed_mentions": discord.AllowedMentions(users=True),
            }
            if art is not None:
                kwargs["file"] = art
            await interaction.followup.send(**kwargs)
            return
        if nyx.active(victim):
            await self._jammed_recon(interaction, "u2", pilot, target)
            return
        recon_hours = float(cfg.get("economy.u2_recon_window_hours", 6))
        recon_hours *= 1 + plushie_perk(pilot, "recon_duration") / 100
        if gevents.is_active(self.econ.store.load(gid), "intel"):
            recon_hours *= 1.5
        expiry = _now() + dt.timedelta(hours=recon_hours)
        construction_lines = self._grant_recon_package(pilot, target.id, victim, expiry, source="u2")
        await self.persist(gid)
        await self.bot.ledger.record(
            gid, interaction.user.id, 0, "vehicle-u2-recon", after=self._net(pilot), other=target.id
        )
        if nyx.active(victim):
            await self._jammed_recon(interaction, "u2", pilot, target)
            return
        defense = (
            "Their AA interceptor missed and was consumed."
            if engaged
            else "No loaded AA interceptor challenged the flight."
        )
        public = ui.base_embed(
            title="🔭 U-2S reconnaissance complete",
            description=f"{interaction.user.mention}'s Dragon Lady completed a high-altitude pass over {target.mention}. {defense}",
            color=cfg.color,
        )
        art = self._strategic_art(cfg, public, "u2")
        kwargs = {
            "content": f"{target.mention}",
            "embed": public,
            "allowed_mentions": discord.AllowedMentions(users=True),
        }
        if art is not None:
            kwargs["file"] = art
        await interaction.followup.send(**kwargs)
        if nyx.active(victim):
            await self._jammed_recon(interaction, "u2", pilot, target)
            return
        deep = self._deep_vault_total(victim) if victim.get("deep_vault_owned") else 0
        intel = ui.base_embed(
            title="🛰️ Classified target package",
            description=f"**Target:** {target.display_name}\n**Federal Reserve Bedrock Vault:** "
            + (self.money(cfg, deep) if victim.get("deep_vault_owned") else "No facility detected")
            + f"\n**Strike authorization expires:** <t:{int(expiry.timestamp())}:R>\n\nThis package guides B-2 and DDG-1000 Deep Vault strikes, A-10C/Su-34 completed-vehicle targeting, and Dark Eagle construction strikes.",
            color=cfg.color,
        )
        intel.add_field(
            name="Tracked vehicle construction",
            value="\n".join(construction_lines)[:1024]
            if construction_lines
            else "No active conventional vehicle construction detected.",
            inline=False,
        )
        nyx.protect_report(interaction, self.econ, target.id)
        await ui.followup_report(interaction, embed=intel, ephemeral=True)

    async def _deimos_execute(self, interaction: discord.Interaction, target: discord.Member) -> None:
        """Resolve a public, reciprocal Deimos intelligence gamble."""
        gid = interaction.guild_id
        cfg = self.cfg(gid)
        error = self._mission_preflight(gid, interaction.user.id, "deimos", target)
        if error:
            await interaction.followup.send(embed=ui.error_embed(error), ephemeral=True)
            return
        attacker = self.user(gid, interaction.user.id)
        tracker = self._vehicle_settle(attacker, "deimos")
        victim = self.user(gid, target.id)
        await self._settle_deep_with_log(gid, interaction.user.id, attacker)
        await self._settle_deep_with_log(gid, target.id, victim)
        tracker["last_deploy_at"] = _now().isoformat()
        if nyx.active(victim) or nyx.active(attacker):
            await self._jammed_recon(interaction, "deimos", attacker, target)
            return
        success = random.randint(1, 100) <= _deimos_success_chance(cfg)
        if not success:
            tracker["owned"] = False
            tracker["building_until"] = None
            await self.persist(gid)
            await self.bot.ledger.record(
                gid,
                interaction.user.id,
                0,
                "vehicle-deimos-lost",
                after=self._net(attacker),
                other=target.id,
                detail="Scan failed; tracker destroyed",
            )
            line = random.choice(DEIMOS_FAILURE_LINES)
            embed = ui.base_embed(
                title="💥 DEATHMARK TRACKER DESTROYED",
                description=f'''*"{line}"*\n\n{interaction.user.mention}'s F3 tracker failed while hunting {target.mention}. **No intelligence was recovered.** The tracker is gone and must be rebuilt.''',
                color=ui.COLOR_BAD,
            )
            art = self._strategic_art(cfg, embed, "deimos-failed")
            kwargs: Dict[str, Any] = {
                "content": f"{interaction.user.mention} {target.mention}",
                "embed": embed,
                "allowed_mentions": discord.AllowedMentions(users=True),
            }
            if art is not None:
                kwargs["file"] = art
            await interaction.followup.send(**kwargs)
            return
        base_hours = float(cfg.get("economy.deimos_recon_window_hours", 4))
        attacker_hours = base_hours * (1 + plushie_perk(attacker, "recon_duration") / 100)
        victim_hours = base_hours * (1 + plushie_perk(victim, "recon_duration") / 100)
        now = _now()
        attacker_expiry = now + dt.timedelta(hours=attacker_hours)
        victim_expiry = now + dt.timedelta(hours=victim_hours)
        attacker_builds = self._grant_recon_package(
            attacker, target.id, victim, attacker_expiry, observed_at=now, source="deimos"
        )
        victim_builds = self._grant_recon_package(
            victim, interaction.user.id, attacker, victim_expiry, observed_at=now, source="deimos"
        )
        attacker_defenses = self._deimos_defense_report(cfg, attacker)
        victim_defenses = self._deimos_defense_report(cfg, victim)
        await self.persist(gid)
        await self.bot.ledger.record(
            gid,
            interaction.user.id,
            0,
            "vehicle-deimos-recon",
            after=self._net(attacker),
            other=target.id,
            detail="Reciprocal vault and defense exposure",
        )
        if nyx.active(victim) or nyx.active(attacker):
            victim.setdefault("recon_targets", {}).pop(str(interaction.user.id), None)
            victim.setdefault("sr71_construction_intel", {}).pop(str(interaction.user.id), None)
            await self._jammed_recon(interaction, "deimos", attacker, target)
            return
        attacker_deep = (
            self.money(cfg, self._deep_vault_total(attacker))
            if attacker.get("deep_vault_owned")
            else "No facility detected"
        )
        victim_deep = (
            self.money(cfg, self._deep_vault_total(victim))
            if victim.get("deep_vault_owned")
            else "No facility detected"
        )
        attacker_continuity = self._continuity_exact_report(attacker, cfg, interaction.user.id)
        victim_continuity = self._continuity_exact_report(victim, cfg, target.id)
        line = random.choice(DEIMOS_SUCCESS_LINES)
        embed = ui.base_embed(
            title="☠️ DEATHMARK TRACK — BOTH TARGETS EXPOSED",
            description=f'''*"{line}"*\n\n{interaction.user.mention}'s tracker found {target.mention}. The scan reflected both signatures, so **each player can now launch recon-authorized strikes against the other**.''',
            color=cfg.color,
        )
        embed.add_field(
            name=f"{interaction.user.display_name} — Bedrock Vault", value=attacker_deep, inline=False
        )
        embed.add_field(name=f"{target.display_name} — Bedrock Vault", value=victim_deep, inline=False)
        embed.add_field(
            name=f"{interaction.user.display_name} — Raven Rock",
            value=attacker_continuity[:1024],
            inline=False,
        )
        embed.add_field(
            name=f"{target.display_name} — Raven Rock", value=victim_continuity[:1024], inline=False
        )
        embed.add_field(
            name=f"{interaction.user.display_name} — Defensive Readiness",
            value=attacker_defenses,
            inline=False,
        )
        embed.add_field(
            name=f"{target.display_name} — Defensive Readiness", value=victim_defenses, inline=False
        )
        embed.add_field(
            name=f"{interaction.user.display_name} — Construction Tracks",
            value="\n".join(victim_builds)[:450]
            if victim_builds
            else "No active conventional vehicle construction detected.",
            inline=False,
        )
        embed.add_field(
            name=f"{target.display_name} — Construction Tracks",
            value="\n".join(attacker_builds)[:450]
            if attacker_builds
            else "No active conventional vehicle construction detected.",
            inline=False,
        )
        embed.add_field(
            name="Reciprocal strike authorization",
            value=f"{interaction.user.mention} may target {target.mention} until <t:{int(attacker_expiry.timestamp())}:R>.\n{target.mention} may target {interaction.user.mention} until <t:{int(victim_expiry.timestamp())}:R>.",
            inline=False,
        )
        strategist_hours = base_hours * 1.25
        embed.set_footer(
            text=f"Base window: {base_hours:g}h · Strategist 21 extends only its holder's package to {strategist_hours:g}h"
        )
        art = self._strategic_art(cfg, embed, "deimos-success")
        kwargs = {
            "content": f"{interaction.user.mention} {target.mention}",
            "embed": embed,
            "allowed_mentions": discord.AllowedMentions(users=True),
        }
        if art is not None:
            kwargs["file"] = art
        nyx.protect_report(interaction, self.econ, interaction.user.id, target.id)
        try:
            await ui.followup_report(interaction, **kwargs)
        finally:
            if art is not None:
                art.close()

    async def _sr71_execute(self, interaction: discord.Interaction, target: discord.Member) -> None:
        """Resolve a complete defense scan plus a unified target package."""
        gid = interaction.guild_id
        cfg = self.cfg(gid)
        error = self._mission_preflight(gid, interaction.user.id, "sr71", target)
        if error:
            await interaction.followup.send(embed=ui.error_embed(error), ephemeral=True)
            return
        pilot = self.user(gid, interaction.user.id)
        craft = self._vehicle_settle(pilot, "sr71")
        victim = self.user(gid, target.id)
        deployed_at = _now()
        craft["last_deploy_at"] = deployed_at.isoformat()
        defense_name: Optional[str] = None
        public_loss_chance = max(0, min(100, int(cfg.get("economy.vehicle_sr71_shotdown_chance", 20))))
        patrol_choice = air_commands.strongest_patrol(self, gid, target.id, "sr71", public_loss_chance)
        if patrol_choice:
            defense_name = patrol_choice[2]
        elif _electronic_blackout_until(victim) is None:
            self._s400_settle(victim)
            s400_loaded = max(0, int(victim.get("s400_interceptors", 0)))
            if victim.get("s400_owned") and s400_loaded:
                victim["s400_interceptors"] = s400_loaded - 1
                defense_name = "S-400 40N6E"
            else:
                patriot = self._vehicle_settle(victim, "patriot")
                patriot_loaded = max(0, int(patriot.get("ammo", 0)))
                if patriot.get("owned") and patriot_loaded:
                    patriot["ammo"] = patriot_loaded - 1
                    defense_name = "Patriot PAC-3 MSE"
        public_loss_chance = max(0, min(100, int(cfg.get("economy.vehicle_sr71_shotdown_chance", 20))))
        resolution_chance = public_loss_chance
        if defense_name:
            resolution_chance = resolution_chance
            resolution_chance = resolution_chance
        shot_down = (
            air_commands.engage(patrol_choice, cfg, interaction.user.id, "sr71")
            if patrol_choice
            else bool(defense_name) and random.randint(1, 100) <= resolution_chance
        )
        if shot_down:
            craft["owned"] = False
            craft["building_until"] = None
            await self.persist(gid)
            await self.bot.ledger.record(
                gid,
                interaction.user.id,
                0,
                "vehicle-sr71-lost",
                after=self._net(pilot),
                other=target.id,
                detail="Defense-reconnaissance aircraft lost",
            )
            embed = ui.base_embed(
                title="💥 SR-71A BLACKBIRD LOST",
                description=f"{target.mention}'s **{defense_name}** interceptor destroyed {interaction.user.mention}'s Blackbird during its high-speed pass. **No defensive intelligence was recovered.**",
                color=ui.COLOR_BAD,
            )
            art = self._strategic_art(cfg, embed, "sr71-shotdown")
            kwargs: Dict[str, Any] = {
                "content": f"{interaction.user.mention} {target.mention}",
                "embed": embed,
                "allowed_mentions": discord.AllowedMentions(users=True),
            }
            if art is not None:
                kwargs["file"] = art
            await interaction.followup.send(**kwargs)
            return
        if nyx.active(victim):
            await self._jammed_recon(interaction, "sr71", pilot, target)
            return
        air_defense, dedicated = self._sr71_defense_report(cfg, victim)
        active_patrol = air_commands.patrol_candidate(self, gid, target.id, "sr71")
        patrol_text = (
            "🟢 ACTIVE · one engagement available until "
            + status_ui.deadline(_parse(active_patrol[0]["patrol_until"]))
            if active_patrol
            else "⚫ NO ACTIVE COVER"
        )
        air_defense += "\n\n✈️ **F-22A combat air patrol**\n" + patrol_text
        intel_seconds = self._vehicle_cooldown_seconds(cfg, "sr71")
        intel_expiry = deployed_at + dt.timedelta(seconds=intel_seconds)
        construction_lines = self._grant_recon_package(
            pilot, target.id, victim, intel_expiry, observed_at=deployed_at, source="sr71"
        )
        await self.persist(gid)
        await self.bot.ledger.record(
            gid,
            interaction.user.id,
            0,
            "vehicle-sr71-recon",
            after=self._net(pilot),
            other=target.id,
            detail="Unified strategic target package and defense snapshot",
        )
        if nyx.active(victim):
            await self._jammed_recon(interaction, "sr71", pilot, target)
            return
        public = ui.base_embed(
            title="🛫 SR-71A RECONNAISSANCE COMPLETE",
            description=f"{interaction.user.mention}'s Blackbird completed a Mach 3 pass over {target.mention}. The aircraft escaped with a classified defensive snapshot.",
            color=cfg.color,
        )
        art = self._strategic_art(cfg, public, "sr71-recon")
        kwargs = {
            "content": target.mention,
            "embed": public,
            "allowed_mentions": discord.AllowedMentions(users=True),
        }
        if art is not None:
            kwargs["file"] = art
        await interaction.followup.send(**kwargs)
        if nyx.active(victim):
            await self._jammed_recon(interaction, "sr71", pilot, target)
            return
        report = ui.base_embed(
            title=f"🛰️ SR-71 defense map — {target.display_name}",
            description="Classified readiness snapshot and unified strategic target package.",
            color=cfg.color,
        )
        deep = self._deep_vault_total(victim) if victim.get("deep_vault_owned") else 0
        report.add_field(
            name="Federal Reserve Bedrock Vault",
            value=self.money(cfg, deep) if victim.get("deep_vault_owned") else "No facility detected",
            inline=False,
        )
        report.add_field(name="Integrated air defense", value=air_defense, inline=False)
        report.add_field(name="Dedicated strategic counters", value=dedicated, inline=False)
        report.add_field(
            name="Dark Eagle construction tracks",
            value="\n".join(construction_lines)[:1024]
            if construction_lines
            else "No active conventional vehicle construction detected.",
            inline=False,
        )
        report.set_footer(
            text=f"Package guides B-2, DDG-1000, A-10C, Su-34 and Dark Eagle targeting · expires {intel_expiry.strftime('%H:%M UTC')}"
        )
        nyx.protect_report(interaction, self.econ, target.id)
        await ui.followup_report(interaction, embed=report, ephemeral=True)

    async def _b2_execute(self, interaction: discord.Interaction, target: discord.Member) -> None:
        gid = interaction.guild_id
        cfg = self.cfg(gid)
        error = self._mission_preflight(gid, interaction.user.id, "b2", target)
        if error:
            await interaction.followup.send(embed=ui.error_embed(error), ephemeral=True)
            return
        attacker = self.user(gid, interaction.user.id)
        bomber = self._vehicle_settle(attacker, "b2")
        victim = self.user(gid, target.id)
        moved = self._deep_vault_settle(victim)
        has_intel = self._active_recon(attacker, target.id, victim)
        now = _now()
        bomber["armed"] = False
        bomber["arming_until"] = None
        bomber["last_deploy_at"] = now.isoformat()
        self._s400_settle(victim)
        s400_engaged = (
            _electronic_blackout_until(victim) is None
            and bool(victim.get("s400_owned"))
            and (int(victim.get("s400_interceptors", 0)) > 0)
        )
        patrol_choice = air_commands.strongest_patrol(
            self, gid, target.id, "b2", _public_s400_intercept_chance(cfg, "b2") if s400_engaged else 0
        )
        intercepted = False
        if patrol_choice:
            intercepted = air_commands.engage(patrol_choice, cfg, interaction.user.id, "b2")
        elif s400_engaged:
            victim["s400_interceptors"] = int(victim.get("s400_interceptors", 0)) - 1
            chance = _b2_intercept_chance(cfg, interaction.user.id, target.id)
            if gevents.is_active(self.econ.store.load(gid), "defensealert"):
                chance = min(100, chance + 10)
            intercepted = random.randint(1, 100) <= chance
        if intercepted:
            bomber["owned"] = False
            bomber["building_until"] = None
            await self.persist(gid)
            if moved:
                await self._log(gid, target.id, moved, "deep-vault-withdraw", victim)
            await self.bot.ledger.record(
                gid, interaction.user.id, 0, "vehicle-b2-launch", after=self._net(attacker), other=target.id
            )
            await self.bot.ledger.record(
                gid, interaction.user.id, 0, "vehicle-b2-lost", after=self._net(attacker), other=target.id
            )
            embed = ui.base_embed(
                title="💥 B-2 SPIRIT DESTROYED",
                description=f"{interaction.user.mention}'s B-2 penetrated every conventional defence around {target.mention} — until its bomb bay opened. "
                + (
                    "An **F-22A combat air patrol** destroyed the bomber. "
                    if patrol_choice
                    else "An **S-400 Triumf** fired a 40N6E interceptor and destroyed the bomber. "
                )
                + "\n\nNo damage. The aircraft and B61-12 payload are gone permanently.",
                color=ui.COLOR_BAD,
            )
            art = self._strategic_art(cfg, embed, "f22-mission" if patrol_choice else "s400")
            kwargs = {
                "content": f"{interaction.user.mention} {target.mention}",
                "embed": embed,
                "allowed_mentions": discord.AllowedMentions(users=True),
            }
            if art is not None:
                kwargs["file"] = art
            await interaction.followup.send(**kwargs)
            return
        visible_before = self._net(victim)
        deep_before = self._deep_vault_total(victim)
        victim["donuts"] = 0
        victim["bank"] = 0
        inventory = victim.get("inventory", {}) or {}
        items_lost = sum((max(0, int(n)) for n in inventory.values()))
        for key in list(inventory):
            inventory[key] = 0
        fish = victim.get("fish", {}) or {}
        fish_lost = sum((max(0, int(n)) for n in fish.values()))
        for key in list(fish):
            fish[key] = 0
        deep_lost = 0
        if has_intel and victim.get("deep_vault_owned"):
            deep_lost = deep_before
            victim["deep_vault_balance"] = 0
            victim["deep_vault_withdraw_amount"] = 0
            victim["deep_vault_withdraw_at"] = None
        await self.persist(gid)
        if moved:
            await self.bot.ledger.record(gid, target.id, moved, "deep-vault-withdraw", after=visible_before)
        await self.bot.ledger.record(
            gid, interaction.user.id, 0, "vehicle-b2-launch", after=self._net(attacker), other=target.id
        )
        await self.bot.ledger.record(
            gid,
            target.id,
            -visible_before,
            "vehicle-b2-hit",
            after=self._net(victim),
            other=interaction.user.id,
            actor=interaction.user.id,
        )
        if deep_lost:
            await self.bot.ledger.record(
                gid,
                target.id,
                -deep_lost,
                "deep-vault-hit",
                after=0,
                other=interaction.user.id,
                actor=interaction.user.id,
            )
        damage = [f"🍩 **{ui.format_donuts(visible_before)}** visible {self.name(cfg)} annihilated"]
        if deep_lost:
            damage.append("🏦 **CLASSIFIED hidden reserves** located and destroyed")
        if items_lost:
            damage.append(f"📦 **{items_lost}** consumable(s) destroyed")
        if fish_lost:
            damage.append(f"🐟 **{fish_lost}** fish destroyed")
        embed = ui.base_embed(
            title="☢️ B-2 SPIRIT — TOTAL STRIKE",
            description=f"{interaction.user.mention}'s B-2 crossed every conventional defence and released its B61-12 payload over {target.mention}. **Total destruction confirmed.**",
            color=ui.COLOR_BAD,
        )
        embed.add_field(name="Damage assessment", value="\n".join(damage), inline=False)
        if s400_engaged:
            embed.add_field(
                name="S-400 failed",
                value="The 40N6E interceptor missed as the bomb bay opened. It was consumed.",
                inline=False,
            )
        embed.set_footer(text="No target immunity is granted; only the bomber's sortie cooldown applies.")
        art = self._strategic_art(cfg, embed, "b2")
        kwargs = {
            "content": f"{target.mention}",
            "embed": embed,
            "allowed_mentions": discord.AllowedMentions(users=True),
        }
        if art is not None:
            kwargs["file"] = art
        await interaction.followup.send(**kwargs)
        if deep_lost:
            await interaction.followup.send(
                embed=ui.base_embed(
                    title="🛰️ Classified damage annex",
                    description=f"Your reconnaissance target package led the strike into {target.display_name}'s Bedrock Vault.\n**Hidden reserves destroyed:** {self.money(cfg, deep_lost)}",
                    color=cfg.color,
                ),
                ephemeral=True,
            )

    async def _armored_execute(
        self, interaction: discord.Interaction, target: discord.Member, objective: Optional[str], model: str
    ) -> None:
        """Resolve a frontline-tank breach and its dedicated Javelin counter."""
        if model not in ARMORED_MODELS:
            raise ValueError(f"Unsupported armored chassis: {model}")
        gid = interaction.guild_id
        cfg = self.cfg(gid)
        error = self._mission_preflight(gid, interaction.user.id, model, target, objective)
        if error:
            await interaction.followup.send(embed=ui.error_embed(error), ephemeral=True)
            return
        attacker = self.user(gid, interaction.user.id)
        tank = self._vehicle_settle(attacker, model)
        label = self._vehicle_label(model)
        damaged_key, cost_key, repairing_key = _armored_damage_fields(model)
        has_trophy = model == "m1a2"
        victim = self.user(gid, target.id)
        country_id = str(objective or "").strip().casefold()
        country = country_state.BY_ID[country_id]
        territories = country_state.state(self.econ.store.load(gid))["territories"]
        territory = territories[country_id]
        now = _now()
        tank["ammo"] = max(0, int(tank.get("ammo", 0)) - 1)
        tank["last_deploy_at"] = now.isoformat()
        javelin = self._vehicle_settle(victim, "javelin")
        engaged = (
            _electronic_blackout_until(victim) is None
            and bool(javelin.get("owned"))
            and (int(javelin.get("ammo", 0)) > 0)
        )
        tracked = False
        trophy_defeated = False
        public_track = max(0, min(100, int(cfg.get("economy.vehicle_javelin_track_chance", 30))))
        if engaged:
            javelin["ammo"] = max(0, int(javelin.get("ammo", 0)) - 1)
            track_chance = public_track
            track_chance = track_chance
            tracked = random.randint(1, 100) <= track_chance
            if tracked and has_trophy:
                trophy_chance = max(
                    0, min(100, int(cfg.get("economy.vehicle_m1a2_trophy_defeat_chance", 35)))
                )
                trophy_defeated = random.randint(1, 100) <= trophy_chance
        stopped = tracked and (not trophy_defeated)
        if stopped:
            damaged_chance = max(0, min(100, int(cfg.get(f"economy.vehicle_{model}_damage_chance", 30))))
            damaged = random.randint(1, 100) <= damaged_chance
            if damaged:
                quote = _armored_repair_quote(cfg, model)
                tank[damaged_key] = True
                tank[cost_key] = quote
                tank[repairing_key] = None
                tank["garrison_country"] = None
                tank["garrison_transfer_until"] = None
                tank["garrison_transfer_target"] = None
            await self.persist(gid)
            await self.bot.ledger.record(
                gid,
                interaction.user.id,
                0,
                f"vehicle-{model}-launch",
                after=self._net(attacker),
                other=target.id,
            )
            if damaged:
                await self.bot.ledger.record(
                    gid,
                    interaction.user.id,
                    0,
                    f"vehicle-{model}-damaged",
                    after=self._net(attacker),
                    other=target.id,
                )
            result = (
                f"The {label} withdrew with recoverable battle damage. `/vehicle repair` will cost {self.money(cfg, quote)} and take 90 minutes. Surviving onboard rounds remain loaded."
                if damaged
                else f"The {label} withdrew safely. Its fired {VEHICLE_CATALOG[model]['ammo']} round was lost and mission turnaround began."
            )
            counter_detail = (
                "Trophy APS failed to defeat the top-attack missile."
                if has_trophy
                else "The top-attack missile stopped the assault; this chassis has no Trophy reroll."
            )
            embed = ui.base_embed(
                title="🎯 FGM-148F JAVELIN — ARMORED ASSAULT STOPPED",
                description=f"{target.mention}'s Javelin Team tracked {interaction.user.mention}'s {label} before it could breach {country.flag} **{country.name}**. {counter_detail}\n\n**No fortification damage.** {result}",
                color=ui.COLOR_OK,
            )
            art = self._strategic_art(
                cfg, embed, "javelin-intercept" if model == "m1a2" else "leopard2a7-intercept"
            )
            kwargs: Dict[str, Any] = {
                "content": f"{interaction.user.mention} {target.mention}",
                "embed": embed,
                "allowed_mentions": discord.AllowedMentions(users=True),
            }
            if art is not None:
                kwargs["file"] = art
            await interaction.followup.send(**kwargs)
            return
        for other in territories.values():
            if isinstance(other, dict) and int(other.get("m1a2_breached_by", 0) or 0) == int(
                interaction.user.id
            ):
                other["m1a2_breached_by"] = None
                other["m1a2_breached_until"] = None
                other["armored_breach_model"] = None
        fort_before = max(0, int(territory.get("fortification", 0)))
        pct = max(
            0,
            min(
                100,
                int(
                    cfg.get(
                        f"economy.vehicle_{model}_fortification_damage_pct", 25 if model == "m1a2" else 30
                    )
                ),
            ),
        )
        fort_lost = min(fort_before, max(1, fort_before * pct // 100) if fort_before and pct else 0)
        territory["fortification"] = fort_before - fort_lost
        breach_minutes = max(1, int(cfg.get(f"economy.vehicle_{model}_breach_minutes", 30)))
        breach_until = now + dt.timedelta(minutes=breach_minutes)
        territory["m1a2_breached_by"] = int(interaction.user.id)
        territory["m1a2_breached_until"] = breach_until.isoformat()
        territory["m1a2_last_breached_at"] = now.isoformat()
        territory["armored_breach_model"] = model
        await self.persist(gid)
        await self.bot.ledger.record(
            gid, interaction.user.id, 0, f"vehicle-{model}-launch", after=self._net(attacker), other=target.id
        )
        await self.bot.ledger.record(
            gid,
            target.id,
            0,
            f"vehicle-{model}-hit",
            after=self._net(victim),
            other=interaction.user.id,
            actor=interaction.user.id,
            detail=f"{country.name} · {ui.format_donuts(fort_lost)} fortification destroyed",
        )
        defense_note = ""
        if engaged and tracked and trophy_defeated:
            defense_note = "\n\n**Trophy APS active:** the Javelin tracked and fired, but the Abrams' active protection system defeated the missile. The interceptor was consumed."
        elif engaged:
            defense_note = "\n\nThe Javelin Team fired but failed to establish a successful track. Its missile was consumed."
        embed = ui.base_embed(
            title=f"{VEHICLE_CATALOG[model]['emoji']} {label.upper()} ARMORED BREACH CONFIRMED",
            description=f"{interaction.user.mention}'s {label} broke through {target.mention}'s defenses in {country.flag} **{country.name}**.{defense_note}",
            color=ui.COLOR_BAD,
        )
        embed.add_field(
            name="Breach assessment",
            value=f"Fortification destroyed: **{ui.format_donuts(fort_lost)}/{ui.format_donuts(fort_before)}** ({pct}% of current strength)\nFortification remaining: **{ui.format_donuts(fort_before - fort_lost)}**\nAttacker-only invasion bonus: **+{int(cfg.get(f'economy.vehicle_{model}_breach_attack_bonus', 20))} power**\nBreach expires <t:{int(breach_until.timestamp())}:R>",
            inline=False,
        )
        embed.set_footer(
            text="GDP and development were untouched. This country can be breached again after 60 minutes."
        )
        art = self._strategic_art(
            cfg,
            embed,
            ("m1a2-trophy" if trophy_defeated else "m1a2-breach") if model == "m1a2" else "leopard2a7-breach",
        )
        kwargs = {
            "content": target.mention,
            "embed": embed,
            "allowed_mentions": discord.AllowedMentions(users=True),
        }
        if art is not None:
            kwargs["file"] = art
        await interaction.followup.send(**kwargs)

    async def _m1a2_execute(
        self, interaction: discord.Interaction, target: discord.Member, objective: Optional[str]
    ) -> None:
        """Compatibility wrapper for the original Abrams integration tests."""
        await self._armored_execute(interaction, target, objective, "m1a2")

    async def _focused_vehicle_execute(
        self,
        interaction: discord.Interaction,
        target: discord.Member,
        model: str,
        objective: Optional[str] = None,
        secondary_objective: Optional[str] = None,
    ) -> None:
        """Resolve one of the focused post-B-2 strategic vehicle missions."""
        gid = interaction.guild_id
        cfg = self.cfg(gid)
        error = self._mission_preflight(
            gid, interaction.user.id, model, target, objective, secondary_objective
        )
        if error:
            await interaction.followup.send(embed=ui.error_embed(error), ephemeral=True)
            return
        attacker = self.user(gid, interaction.user.id)
        weapon = self._vehicle_settle(attacker, model)
        is_comanche = model == "apache" and bool(weapon.get("comanche_upgraded"))
        secondary_objective = (secondary_objective or "").strip().lower() or None
        mission_code = "comanche" if is_comanche else model
        mission_label = "RAH-66 Comanche" if is_comanche else self._vehicle_label(model)
        victim = self.user(gid, target.id)
        moved = self._deep_vault_settle(victim)
        now = _now()
        split_guided_strike = bool(secondary_objective) and model == "mq9"
        if is_comanche:
            rounds_committed = _comanche_rounds_per_mission(cfg)
        else:
            rounds_committed = 2 if split_guided_strike else 1
        if model == "b52":
            rounds_spent = rounds_committed
        else:
            preserve_chance = plushie_perk(attacker, "ammo_preserve")
            rounds_spent = sum((random.randint(1, 100) > preserve_chance for _ in range(rounds_committed)))
        weapon["ammo"] = max(0, int(weapon.get("ammo", 0)) - rounds_spent)
        weapon["last_deploy_at"] = now.isoformat()
        defense_model: Optional[str] = None
        defense_name = ""
        defense_art = ""
        defense_chance = 0
        displayed_defense_chance = 0
        destroys_attacker = False
        engaged = False
        intercepted = False
        defenses_online = _electronic_blackout_until(victim) is None
        patrol_defense = air_commands.conventional_choice(self, gid, target.id, victim, cfg, model, attacker)
        if patrol_defense:
            engaged = True
            defense_name = patrol_defense[2]
            displayed_defense_chance = patrol_defense[3]
            intercepted = air_commands.engage(patrol_defense, cfg, interaction.user.id, model)
            destroys_attacker = model != "c130j" or patrol_defense[1] == "patrol_until"
            defense_art = "f22-mission" if patrol_defense[1] == "patrol_until" else "s400"
        elif model == "b52":
            self._s400_settle(victim)
            loaded = max(0, int(victim.get("s400_interceptors", 0)))
            engaged = defenses_online and bool(victim.get("s400_owned")) and (loaded > 0)
            if engaged:
                victim["s400_interceptors"] = loaded - 1
                displayed_defense_chance = _public_s400_intercept_chance(cfg, "b52")
                defense_chance = _b52_intercept_chance(cfg, interaction.user.id, target.id)
                if gevents.is_active(self.econ.store.load(gid), "defensealert"):
                    displayed_defense_chance = min(100, displayed_defense_chance + 10)
                    defense_chance = min(100, defense_chance + 10)
                    defense_chance = defense_chance
                intercepted = random.randint(1, 100) <= defense_chance
            defense_name = "S-400 40N6E interceptor"
            defense_art = "b52-intercept"
            destroys_attacker = True
        elif model in {"mq9", "apache"}:
            self._aa_settle(cfg, victim)
            loaded = int(victim.get("aa_rockets_loaded", 0))
            engaged = defenses_online and loaded > 0
            if engaged:
                victim["aa_rockets_loaded"] = loaded - 1
                if is_comanche:
                    low, high = _comanche_intercept_range(cfg)
                    base_chance = random.randint(low, high)
                else:
                    chance_key = (
                        "vehicle_apache_aa_intercept_chance"
                        if model == "apache"
                        else "vehicle_mq9_aa_intercept_chance"
                    )
                    fallback = 30 if model == "apache" else 20
                    base_chance = int(cfg.get(f"economy.{chance_key}", fallback))
                ordinary_chance = _public_light_aircraft_intercept_chance(cfg, victim, base_chance)
                displayed_defense_chance = ordinary_chance
                defense_chance = ordinary_chance
                defense_chance = defense_chance
                intercepted = random.randint(1, 100) <= defense_chance
            defense_name = (
                "Radar AA low-observable firing solution" if is_comanche else "Radar AA interceptor"
            )
            defense_art = (
                "comanche-intercept" if is_comanche else "apache-intercept" if model == "apache" else ""
            )
            destroys_attacker = True
        elif model in {"a10", "su34"}:
            if model == "a10":
                self._aa_settle(cfg, victim)
                loaded = max(0, int(victim.get("aa_rockets_loaded", 0)))
                engaged = defenses_online and loaded > 0
                if engaged:
                    victim["aa_rockets_loaded"] = loaded - 1
                    base = int(cfg.get("economy.vehicle_a10_aa_intercept_chance", 50))
                    displayed_defense_chance = _public_light_aircraft_intercept_chance(cfg, victim, base)
                    defense_name = "Radar AA interceptor"
                    defense_art = "apache-intercept"
                else:
                    patriot = self._vehicle_settle(victim, "patriot")
                    patriot_loaded = max(0, int(patriot.get("ammo", 0)))
                    engaged = defenses_online and bool(patriot.get("owned")) and (patriot_loaded > 0)
                    if engaged:
                        patriot["ammo"] = patriot_loaded - 1
                        displayed_defense_chance = _public_strategic_chance(
                            cfg, "economy.vehicle_patriot_intercept_chance", 40
                        )
                        defense_name = "Patriot PAC-3 MSE"
                        defense_art = "patriot"
            else:
                self._s400_settle(victim)
                loaded = max(0, int(victim.get("s400_interceptors", 0)))
                engaged = defenses_online and bool(victim.get("s400_owned")) and (loaded > 0)
                if engaged:
                    victim["s400_interceptors"] = loaded - 1
                    displayed_defense_chance = _public_strategic_chance(
                        cfg, "economy.vehicle_su34_s400_intercept_chance", 25
                    )
                    defense_name = "S-400 40N6E interceptor"
                    defense_art = "s400"
                else:
                    patriot = self._vehicle_settle(victim, "patriot")
                    patriot_loaded = max(0, int(patriot.get("ammo", 0)))
                    engaged = defenses_online and bool(patriot.get("owned")) and (patriot_loaded > 0)
                    if engaged:
                        patriot["ammo"] = patriot_loaded - 1
                        displayed_defense_chance = _public_strategic_chance(
                            cfg, "economy.vehicle_su34_patriot_intercept_chance", 20
                        )
                        defense_name = "Patriot PAC-3 MSE"
                        defense_art = "patriot"
            if engaged:
                defense_chance = displayed_defense_chance
                if gevents.is_active(self.econ.store.load(gid), "defensealert"):
                    displayed_defense_chance = min(100, displayed_defense_chance + 10)
                    defense_chance = min(100, defense_chance + 10)
                defense_chance = defense_chance
                intercepted = random.randint(1, 100) <= defense_chance
            destroys_attacker = True
        else:
            defense_map = {
                "zumwalt": ("aegis", "RIM-174B SM-6", "aegis", "vehicle_aegis_intercept_chance", 30, False),
                "virginia": ("p8", "P-8A Poseidon / Mk 54", "p8", "vehicle_p8_intercept_chance", 30, True),
                "himars": (
                    "patriot",
                    "Patriot PAC-3 MSE",
                    "patriot",
                    "vehicle_patriot_intercept_chance",
                    40,
                    False,
                ),
                "f15e": (
                    "patriot",
                    "Patriot PAC-3 MSE",
                    "patriot",
                    "vehicle_patriot_intercept_chance",
                    40,
                    True,
                ),
                "c130j": (
                    "patriot",
                    "Patriot PAC-3 MSE",
                    "patriot",
                    "vehicle_patriot_intercept_chance",
                    40,
                    False,
                ),
                "champ": (
                    "patriot",
                    "Patriot PAC-3 MSE",
                    "patriot",
                    "vehicle_patriot_intercept_chance",
                    40,
                    False,
                ),
            }
            defense = defense_map.get(model)
            if defense is not None:
                defense_model, defense_name, defense_art, chance_key, fallback, destroys_attacker = defense
                defense_state = self._vehicle_settle(victim, defense_model)
                engaged = (
                    defenses_online
                    and bool(defense_state.get("owned"))
                    and (int(defense_state.get("ammo", 0)) > 0)
                )
                if engaged:
                    defense_state["ammo"] = int(defense_state.get("ammo", 0)) - 1
                    displayed_defense_chance = _public_strategic_chance(
                        cfg, f"economy.{chance_key}", fallback
                    )
                    defense_chance = int(cfg.get(f"economy.{chance_key}", fallback))
                    if gevents.is_active(self.econ.store.load(gid), "defensealert"):
                        displayed_defense_chance = min(100, displayed_defense_chance + 10)
                        defense_chance = min(100, defense_chance + 10)
                    defense_chance = defense_chance
                    intercepted = random.randint(1, 100) <= defense_chance
        if model == "xb70" and (not patrol_defense):
            self._s400_settle(victim)
            loaded = max(0, int(victim.get("s400_interceptors", 0)))
            engaged = defenses_online and bool(victim.get("s400_owned")) and (loaded > 0)
            defense_name = "S-400 40N6E interceptor"
            defense_art = "s400"
            destroys_attacker = True
            if engaged:
                victim["s400_interceptors"] = loaded - 1
                displayed_defense_chance = max(
                    0, min(100, int(cfg.get("economy.vehicle_xb70_s400_intercept_chance", 10)))
                )
                defense_chance = displayed_defense_chance
                defense_chance = defense_chance
                intercepted = random.randint(1, 100) <= defense_chance
        if intercepted:
            repair_quote = 0
            b52_recoverable = False
            if destroys_attacker:
                if is_comanche:
                    repair_quote = _comanche_repair_quote(cfg)
                    weapon["comanche_damaged"] = True
                    weapon["comanche_repair_cost"] = repair_quote
                    weapon["comanche_repairing_until"] = None
                    weapon["ammo"] = 0
                    weapon["loading_until"] = None
                    weapon["loading_qty"] = 0
                elif model == "b52":
                    repairs_used = max(0, int(weapon.get("b52_repair_count", 0)))
                    if repairs_used < len(B52_REPAIR_COST_PCTS):
                        weapon["b52_repair_count"] = repairs_used + 1
                        weapon["b52_damaged"] = True
                        weapon["b52_repairing_until"] = None
                        weapon["owned"] = True
                        weapon["building_until"] = None
                        weapon["last_deploy_at"] = None
                        weapon["ammo"] = 0
                        weapon["loading_until"] = None
                        weapon["loading_qty"] = 0
                        b52_recoverable = True
                    else:
                        self._destroy_vehicle(weapon)
                        weapon["b52_repair_count"] = 0
                        weapon["b52_damaged"] = False
                        weapon["b52_repairing_until"] = None
                else:
                    self._destroy_vehicle(weapon)
            await self.persist(gid)
            if moved:
                await self.bot.ledger.record(
                    gid, target.id, moved, "deep-vault-withdraw", after=self._net(victim), actor=target.id
                )
            await self.bot.ledger.record(
                gid,
                interaction.user.id,
                0,
                f"vehicle-{mission_code}-launch",
                after=self._net(attacker),
                other=target.id,
            )
            if destroys_attacker:
                if is_comanche:
                    loss_reason = "vehicle-comanche-damaged"
                elif model == "b52" and b52_recoverable:
                    loss_reason = "vehicle-b52-damaged"
                else:
                    loss_reason = f"vehicle-{mission_code}-lost"
                await self.bot.ledger.record(
                    gid, interaction.user.id, 0, loss_reason, after=self._net(attacker), other=target.id
                )
            if is_comanche:
                result = "The Comanche was forced down with recoverable battle damage. Its conversion remains installed, but it is grounded pending depot repair. Check `/arsenal` for its status. All loaded JAGMs were lost."
            elif model == "b52" and b52_recoverable:
                result = "The B-52H was forced down and recovery crews secured the airframe. Check `/arsenal` for its depot-repair status. All bomb sticks were lost."
            elif destroys_attacker:
                result = "The attacking vehicle was destroyed permanently."
            else:
                result = "The incoming munition was destroyed; the launch platform survived."
            intercept_result = "forced it down" if is_comanche or b52_recoverable else "scored a kill"
            embed = ui.base_embed(
                title=f"🛡️ {defense_name.upper()} — INTERCEPT",
                description=f"{target.mention}'s **{defense_name}** engaged {interaction.user.mention}'s **{mission_label}** and {intercept_result}.\n\n**No target damage.** {result}",
                color=ui.COLOR_OK,
            )
            art: Optional[discord.File]
            if model == "mq9" and cfg.get("economy.aa_media", True):
                aa_path = self._random_aa_gif()
                if aa_path is not None:
                    aa_name = f"radar-aa{aa_path.suffix.lower()}"
                    embed.set_image(url=f"attachment://{aa_name}")
                    art = discord.File(str(aa_path), filename=aa_name)
                else:
                    art = None
            else:
                art = self._strategic_art(cfg, embed, defense_art or model)
            kwargs: Dict[str, Any] = {
                "content": f"{interaction.user.mention} {target.mention}",
                "embed": embed,
                "allowed_mentions": discord.AllowedMentions(users=True),
            }
            if art is not None:
                kwargs["file"] = art
            await interaction.followup.send(**kwargs)
            return
        damage_lines: List[str] = []
        ledger_delta = 0
        mission_hit_reason = f"vehicle-{mission_code}-hit"
        mission_outcome_title = "STRIKE CONFIRMED"
        if model == "b52":
            minutes = max(1, int(cfg.get("economy.vehicle_b52_lockdown_minutes", 60)))
            wallet_pct = max(0, min(100, int(cfg.get("economy.vehicle_b52_wallet_damage_pct", 25))))
            bank_pct = max(0, min(100, int(cfg.get("economy.vehicle_b52_bank_damage_pct", 15))))
            wallet_before = max(0, int(victim.get("donuts", 0)))
            bank_before = max(0, int(victim.get("bank", 0)))
            wallet_lost = max(1, wallet_before * wallet_pct // 100) if wallet_before and wallet_pct else 0
            bank_lost = max(1, bank_before * bank_pct // 100) if bank_before and bank_pct else 0
            victim["donuts"] = wallet_before - wallet_lost
            victim["bank"] = bank_before - bank_lost
            ledger_delta = -(wallet_lost + bank_lost)
            proposed = now + dt.timedelta(minutes=minutes)
            current = _parse(victim.get("strategic_lockdown_until"))
            until = max(proposed, current) if current is not None and current > now else proposed
            victim["strategic_lockdown_until"] = until.isoformat()
            damage_lines.append(
                f"💥 Strategic attacks and country invasions disabled for **{minutes} minutes**"
            )
            damage_lines.append(
                f"🍩 **{ui.format_donuts(wallet_lost)}** wallet donuts destroyed ({wallet_pct}%)"
            )
            damage_lines.append(
                f"🏦 **{ui.format_donuts(bank_lost)}** bank-vault donuts destroyed ({bank_pct}%)"
            )
            damage_lines.append("🔒 Federal Reserve Bedrock Deep Vault remained completely untouched")
            damage_lines.append(f"⏳ Runway and operations hub recover <t:{int(until.timestamp())}:R>")
            damage_lines.append("🛡️ Automatic defenses remain operational")
        elif model == "zumwalt":
            pct = max(1, min(99, int(cfg.get("economy.vehicle_zumwalt_damage_pct", 40))))
            wallet_before = max(0, int(victim.get("donuts", 0)))
            bank_before = max(0, int(victim.get("bank", 0)))
            wallet_lost = max(1, wallet_before * pct // 100) if wallet_before else 0
            bank_lost = max(1, bank_before * pct // 100) if bank_before else 0
            victim["donuts"] = wallet_before - wallet_lost
            victim["bank"] = bank_before - bank_lost
            ledger_delta = -(wallet_lost + bank_lost)
            damage_lines.append(
                f"🍩 **{ui.format_donuts(wallet_lost + bank_lost)}** visible {self.name(cfg)} destroyed ({pct}%)"
            )
            if self._active_recon(attacker, target.id, victim) and victim.get("deep_vault_owned"):
                deep_before = self._deep_vault_total(victim)
                deep_lost = max(1, deep_before * pct // 100) if deep_before else 0
                victim["deep_vault_balance"] = deep_before - deep_lost
                pending = max(0, int(victim.get("deep_vault_withdraw_amount", 0)))
                victim["deep_vault_withdraw_amount"] = min(pending, self._deep_vault_total(victim))
                if deep_lost:
                    damage_lines.append(
                        f"🏦 **CLASSIFIED:** {ui.format_donuts(deep_lost)} hidden reserves penetrated"
                    )
                    await self.bot.ledger.record(
                        gid,
                        target.id,
                        -deep_lost,
                        "deep-vault-hit",
                        after=self._deep_vault_total(victim),
                        other=interaction.user.id,
                        actor=interaction.user.id,
                    )
            else:
                damage_lines.append("🏦 Bedrock Vault not located; hidden reserves survived")
        elif model == "virginia":
            fish = victim.setdefault("fish", {})
            fish_lost = sum((max(0, int(n or 0)) for n in fish.values()))
            fish.clear()
            victim.setdefault("fish_meta", {}).clear()
            was_autofishing = bool(victim.get("autofish"))
            victim["autofish"] = False
            rod_id = str(victim.get("equipped_rod") or "")
            rod_destroyed = False
            if rod_id:
                chance = _virginia_rod_break_chance(cfg, target.id, rod_id)
                rod_destroyed = random.randint(1, 100) <= max(0, min(100, chance))
                if rod_destroyed:
                    victim.setdefault("rods", {}).pop(rod_id, None)
                    victim.setdefault("rod_enchants", {}).pop(rod_id, None)
                    victim["equipped_rod"] = None
            damage_lines.append(f"🐟 **{fish_lost:,}** stored fish destroyed")
            if was_autofishing:
                damage_lines.append("🎣 Autofishing operation shut down")
            if rod_destroyed:
                damage_lines.append(
                    f"💥 Equipped **{(ROD_BY_ID.get(rod_id).name if rod_id in ROD_BY_ID else rod_id)}** destroyed"
                )
            elif rod_id:
                damage_lines.append("🎣 Equipped rod survived the torpedo raid")
        elif model == "himars":
            objective = (objective or "").strip().lower()
            if objective == "regular-aa":
                lost = max(0, int(victim.get("aa_rockets_loaded", 0)))
                victim["aa_rockets_loaded"] = 0
                damage_lines.append(f"🚀 **{lost}** loaded regular AA rocket(s) destroyed")
            else:
                lost = min(1, max(0, int(victim.get("s400_interceptors", 0))))
                victim["s400_interceptors"] = max(0, int(victim.get("s400_interceptors", 0)) - lost)
                damage_lines.append(f"🛡️ **{lost}** loaded S-400 40N6E interceptor destroyed")
        elif model == "apache" and is_comanche:
            total, report = self._comanche_ammunition_damage(cfg, victim)
            damage_lines.extend(report)
            damage_lines.append(f"**Total ammunition units destroyed: {total}**")
            damage_lines.append("Project THOR modules, rods, GBI/EKVs and BMD SM-3 interceptors survived")
        elif model == "apache":
            objective = (objective or "").strip().lower()
            if objective == "regular-aa":
                before = max(0, int(victim.get("aa_rockets_loaded", 0)))
                pct = max(1, min(100, int(cfg.get("economy.vehicle_apache_regular_aa_damage_pct", 50))))
                lost = _mq9_items_destroyed(before, pct)
                victim["aa_rockets_loaded"] = max(0, before - lost)
                damage_lines.append(f"📡 **{lost}/{before}** remaining loaded regular AA rocket(s) destroyed")
            elif objective == "s400":
                before = max(0, int(victim.get("s400_interceptors", 0)))
                lost = min(1, before)
                victim["s400_interceptors"] = before - lost
                damage_lines.append(f"🛡️ **{lost}/{before}** loaded S-400 40N6E interceptor destroyed")
            elif objective == "patriot":
                patriot = self._vehicle_settle(victim, "patriot")
                before = max(0, int(patriot.get("ammo", 0)))
                lost = min(1, before)
                patriot["ammo"] = before - lost
                damage_lines.append(f"📡 **{lost}/{before}** loaded Patriot PAC-3 MSE interceptor destroyed")
            else:
                himars = self._vehicle_settle(victim, "himars")
                before = max(0, int(himars.get("ammo", 0)))
                maximum = max(1, int(cfg.get("economy.vehicle_apache_himars_damage", 2)))
                lost = min(maximum, before)
                himars["ammo"] = before - lost
                damage_lines.append(f"🚀 **{lost}/{before}** loaded HIMARS M31A2 rocket(s) destroyed")
        elif model == "mq9":
            inventory = victim.setdefault("inventory", {})
            items = [(objective or "").strip().lower()]
            if secondary_objective:
                items.append(secondary_objective)
            split = len(items) == 2
            key = "economy.vehicle_mq9_split_damage_pct" if split else "economy.vehicle_mq9_item_damage_pct"
            pct = max(1, min(100, int(cfg.get(key, 60 if split else 100))))
            total = 0
            for item in items:
                before = max(0, int(inventory.get(item, 0)))
                lost = _mq9_items_destroyed(before, pct)
                inventory[item] = max(0, before - lost)
                total += lost
                item_name = str(CONSUMABLES[item]["name"])
                damage_lines.append(f"📦 **{lost}/{before} {item_name}** consumable(s) destroyed ({pct}%)")
            damage_lines.append(f"**Total consumables destroyed: {total}**")
        elif model == "f15e":
            pct = max(1, min(99, int(cfg.get("economy.vehicle_f15e_damage_pct", 50))))
            wallet_before = max(0, int(victim.get("donuts", 0)))
            bank_before = max(0, int(victim.get("bank", 0)))
            wallet_lost = max(1, wallet_before * pct // 100) if wallet_before else 0
            bank_lost = max(1, bank_before * pct // 100) if bank_before else 0
            victim["donuts"] = wallet_before - wallet_lost
            victim["bank"] = bank_before - bank_lost
            ledger_delta = -(wallet_lost + bank_lost)
            damage_lines.append(
                f"🍩 **{ui.format_donuts(wallet_lost + bank_lost)}** visible {self.name(cfg)} destroyed ({pct}%)"
            )
            damage_lines.append("🏦 Federal Reserve Bedrock Vault was beyond the strike envelope")
        elif model in {"a10", "su34"}:
            prefix = "vehicle_a10" if model == "a10" else "vehicle_su34"
            vehicle_kill = max(
                0,
                min(
                    100,
                    int(cfg.get(f"economy.{prefix}_vehicle_destroy_chance", 70 if model == "a10" else 50)),
                ),
            )
            vehicle_kill = air.vehicle_chance(attacker, target.id, victim, vehicle_kill)
            vehicle_payload_pct = 100 if model == "a10" else 50
            ammo_pct = max(
                1, min(100, int(cfg.get(f"economy.{prefix}_ammo_damage_pct", 70 if model == "a10" else 45)))
            )
            ammo_targets = max(1, int(cfg.get(f"economy.{prefix}_ammo_target_count", 2)))
            aa_pct = max(
                1,
                min(
                    100, int(cfg.get(f"economy.{prefix}_regular_aa_damage_pct", 75 if model == "a10" else 50))
                ),
            )
            wallet_pct = max(
                0, min(100, int(cfg.get(f"economy.{prefix}_wallet_damage_pct", 20 if model == "a10" else 15)))
            )
            bank_pct = max(
                0, min(100, int(cfg.get(f"economy.{prefix}_bank_damage_pct", 10 if model == "a10" else 8)))
            )
            target_model = (objective or "").strip().lower()
            target_state = self._vehicle_settle(victim, target_model)
            target_label = self._vehicle_label(target_model)
            if target_state.get("owned"):
                if random.randint(1, 100) <= vehicle_kill:
                    self._destroy_vehicle(target_state)
                    damage_lines.append(
                        f"💥 Completed **{target_label}** permanently destroyed ({vehicle_kill}% roll)"
                    )
                else:
                    payload_lost = 0
                    payload_before = 0
                    if target_model == "b2":
                        payload_before = 1 if target_state.get("armed") else 0
                        payload_lost = _mq9_items_destroyed(payload_before, vehicle_payload_pct)
                        if payload_lost:
                            target_state["armed"] = False
                            target_state["arming_until"] = None
                    elif "ammo" in target_state:
                        payload_before = max(0, int(target_state.get("ammo", 0)))
                        payload_lost = _mq9_items_destroyed(payload_before, vehicle_payload_pct)
                        target_state["ammo"] = payload_before - payload_lost
                    damage_lines.append(
                        f"✈️ **{target_label}** survived the {vehicle_kill}% destruction roll; **{payload_lost}/{payload_before}** loaded payload unit(s) destroyed"
                    )
            else:
                damage_lines.append(
                    f"✈️ No completed **{target_label}** was present; the vehicle objective missed"
                )
            stores = sorted(
                (
                    entry
                    for entry in self._offensive_ammo_stores(victim)
                    if entry[0] != target_model and entry[3] > 0
                ),
                key=lambda entry: entry[3],
                reverse=True,
            )[:ammo_targets]
            ammo_total = 0
            for key, container, field, before in stores:
                lost = _mq9_items_destroyed(before, ammo_pct)
                container[field] = before - lost
                ammo_total += lost
                damage_lines.append(
                    f"📦 **{CONVENTIONAL_AMMO_TARGETS.get(key, key)}:** {lost}/{before} destroyed ({ammo_pct}%)"
                )
            if not stores:
                damage_lines.append("📦 No other loaded offensive-ammunition stores were found")
            else:
                damage_lines.append(
                    f"📦 **{ammo_total}** rounds destroyed across {len(stores)} offensive store(s)"
                )
            self._aa_settle(cfg, victim)
            regular_total = 0
            for field, label in (
                ("aa_rockets_loaded", "loaded Radar AA"),
                ("aa_rockets_stock", "reserve Radar AA"),
            ):
                before = max(0, int(victim.get(field, 0)))
                lost = _mq9_items_destroyed(before, aa_pct)
                victim[field] = before - lost
                regular_total += lost
                if before:
                    damage_lines.append(f"📡 **{label}:** {lost}/{before} destroyed ({aa_pct}%)")
            if regular_total == 0:
                damage_lines.append("📡 No remaining Radar AA rockets were found")
            self._s400_settle(victim)
            patriot = self._vehicle_settle(victim, "patriot")
            s400_ready = max(0, int(victim.get("s400_interceptors", 0)))
            patriot_ready = max(0, int(patriot.get("ammo", 0)))
            if s400_ready or patriot_ready:
                if s400_ready >= patriot_ready:
                    victim["s400_interceptors"] = s400_ready - 1
                    damage_lines.append("🛡️ **1 loaded S-400 40N6E interceptor destroyed**")
                else:
                    patriot["ammo"] = patriot_ready - 1
                    damage_lines.append("🛡️ **1 loaded Patriot PAC-3 MSE interceptor destroyed**")
            else:
                damage_lines.append("🛡️ No loaded S-400 or Patriot interceptor remained")
            wallet_before = max(0, int(victim.get("donuts", 0)))
            bank_before = max(0, int(victim.get("bank", 0)))
            wallet_lost = max(1, wallet_before * wallet_pct // 100) if wallet_before and wallet_pct else 0
            bank_lost = max(1, bank_before * bank_pct // 100) if bank_before and bank_pct else 0
            victim["donuts"] = wallet_before - wallet_lost
            victim["bank"] = bank_before - bank_lost
            ledger_delta = -(wallet_lost + bank_lost)
            damage_lines.extend(
                [
                    f"🍩 **{ui.format_donuts(wallet_lost)}** wallet donuts destroyed ({wallet_pct}%)",
                    f"🏦 **{ui.format_donuts(bank_lost)}** bank-vault donuts destroyed ({bank_pct}%)",
                    "🔒 Deep Vault, items, fish, rods, countries, unfinished vehicles and all THOR assets survived",
                ]
            )
        elif model == "c130j":
            pct = max(1, min(100, int(cfg.get("economy.vehicle_c130j_damage_pct", 40))))
            target_count = max(1, int(cfg.get("economy.vehicle_c130j_target_count", 3)))
            stores = sorted(
                self._conventional_ammo_stores(cfg, victim), key=lambda entry: entry[3], reverse=True
            )
            selected = [entry for entry in stores if entry[3] > 0][:target_count]
            if objective and objective.startswith("focused:"):
                pct = 80
                selected = [
                    entry for entry in stores if entry[0] == objective.split(":", 1)[1] and entry[3] > 0
                ][:1]
            total = 0
            for key, container, field, before in selected:
                lost = _mq9_items_destroyed(before, pct)
                container[field] = before - lost
                total += lost
                damage_lines.append(
                    f"📦 **{CONVENTIONAL_AMMO_TARGETS.get(key, key)}:** {lost}/{before} destroyed"
                )
            damage_lines.append(f"**Rapid Dragon total:** {total} rounds across {len(selected)} depots")
            damage_lines.append("Project THOR and its countermeasures were outside the target set")
        elif model == "champ":
            minutes = max(1, int(cfg.get("economy.vehicle_champ_blackout_minutes", 45)))
            proposed = now + dt.timedelta(minutes=minutes)
            current = _parse(victim.get("electronic_blackout_until"))
            until = max(proposed, current) if current is not None and current > now else proposed
            victim["electronic_blackout_until"] = until.isoformat()
            damage_lines.extend(
                [
                    f"⚡ Conventional strategic electronics suppressed for **{minutes} minutes**",
                    "🛡️ Radar AA, AA Shield, S-400, Aegis, P-8A and Patriot cannot engage",
                    "🛰️ U-2, Deimos and SR-71 launches are blocked during the blackout",
                    f"⏳ Systems restore <t:{int(until.timestamp())}:R>",
                    "Project THOR, balances and stored ammunition were unharmed",
                ]
            )
        elif model == "maldx":
            objective = (objective or "").strip().lower()
            chance = max(0, min(100, int(cfg.get("economy.vehicle_maldx_success_chance", 75))))
            success = random.randint(1, 100) <= chance
            store = next(
                (entry for entry in self._conventional_ammo_stores(cfg, victim) if entry[0] == objective),
                None,
            )
            if success and store is not None:
                _, container, field, before = store
                container[field] = max(0, before - 1)
                damage_lines.append(
                    f"👻 False track accepted: **1 {CONVENTIONAL_AMMO_TARGETS.get(objective, objective)}** consumed"
                )
            else:
                mission_hit_reason = "vehicle-maldx-failed"
                mission_outcome_title = "DECOY REJECTED"
                damage_lines.append("👻 The defense rejected the false track; no interceptor was consumed")
        elif model == "lrhw":
            target_model = (objective or "").strip().lower()
            target_state = self._vehicle_state(victim, target_model)
            building = _parse(target_state.get("building_until"))
            if building is not None and building > now:
                target_state["building_until"] = None
                target_state["owned"] = False
                intel = self._active_construction_intel(attacker, target.id, victim)
                if intel is not None:
                    intel.get("builds", {}).pop(target_model, None)
                damage_lines.extend(
                    [
                        f"🔥 Unfinished **{self._vehicle_label(target_model)}** destroyed in its construction bay",
                        "No build refund was issued",
                        "Completed vehicles and Project THOR assets were untouched",
                    ]
                )
            else:
                mission_hit_reason = "vehicle-lrhw-miss"
                mission_outcome_title = "EMPTY CONSTRUCTION BAY"
                damage_lines.extend(
                    [
                        f"🛰️ No active **{self._vehicle_label(target_model)}** build was present",
                        "The C-HGB round was expended and Dark Eagle entered cooldown",
                        "No target assets were damaged",
                    ]
                )
        elif model == "xb70":
            low = max(1, min(99, int(cfg.get("economy.vehicle_xb70_damage_min_pct", 70))))
            high = max(low, min(99, int(cfg.get("economy.vehicle_xb70_damage_max_pct", 80))))
            pct = random.randint(low, high)
            wallet_before = max(0, int(victim.get("donuts", 0)))
            bank_before = max(0, int(victim.get("bank", 0)))
            wallet_lost = max(1, wallet_before * pct // 100) if wallet_before else 0
            bank_lost = max(1, bank_before * pct // 100) if bank_before else 0
            victim["donuts"] = wallet_before - wallet_lost
            victim["bank"] = bank_before - bank_lost
            ledger_delta = -(wallet_lost + bank_lost)
            damage_lines.extend(
                [
                    f"🍩 **{ui.format_donuts(wallet_lost)}** wallet donuts destroyed ({pct}%)",
                    f"🏦 **{ui.format_donuts(bank_lost)}** bank-vault donuts destroyed ({pct}%)",
                    "🔒 Federal Reserve Bedrock Vault remained untouched",
                ]
            )
        await self.persist(gid)
        if moved:
            await self.bot.ledger.record(
                gid, target.id, moved, "deep-vault-withdraw", after=self._net(victim), actor=target.id
            )
        await self.bot.ledger.record(
            gid,
            interaction.user.id,
            0,
            f"vehicle-{mission_code}-launch",
            after=self._net(attacker),
            other=target.id,
        )
        if ledger_delta:
            await self._log(
                gid, target.id, ledger_delta, f"vehicle-{mission_code}-hit", victim, other=interaction.user.id
            )
        else:
            await self.bot.ledger.record(
                gid,
                target.id,
                0,
                mission_hit_reason,
                after=self._net(victim),
                other=interaction.user.id,
                actor=interaction.user.id,
            )
        defense_note = (
            f"\n\n{defense_name} engaged at **{displayed_defense_chance}%** but missed; its ammunition was consumed."
            if engaged
            else ""
        )
        embed = ui.base_embed(
            title=f"💥 {mission_label.upper()} — {mission_outcome_title}",
            description=f"{interaction.user.mention}'s **{mission_label}** completed its mission against {target.mention}.{defense_note}",
            color=ui.COLOR_BAD,
        )
        embed.add_field(name="Damage assessment", value="\n".join(damage_lines), inline=False)
        embed.set_footer(text="No victim protection period was granted.")
        art = self._strategic_art(cfg, embed, mission_code)
        kwargs = {
            "content": f"{target.mention}",
            "embed": embed,
            "allowed_mentions": discord.AllowedMentions(users=True),
        }
        if art is not None:
            kwargs["file"] = art
        await interaction.followup.send(**kwargs)

    deepvault_group = app_commands.Group(
        name="deepvault", description="Private hardened storage hidden from public wealth commands."
    )

    async def _settle_deep_with_log(self, guild_id: int, user_id: int, u: Dict[str, Any]) -> int:
        moved = self._deep_vault_settle(u)
        if moved:
            await self.persist(guild_id)
            await self._log(guild_id, user_id, moved, "deep-vault-withdraw", u)
        return moved

    @deepvault_group.command(name="build", description="Construct a hidden Federal Reserve Bedrock Vault.")
    async def deepvault_build(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        gid = interaction.guild_id
        u = self.user(gid, interaction.user.id)
        moved = await self._settle_deep_with_log(gid, interaction.user.id, u)
        if u.get("deep_vault_owned"):
            await ui.respond(
                interaction,
                embed=ui.warn_embed("You already control a Federal Reserve Bedrock Vault."),
                ephemeral=True,
            )
            return
        cost = int(cfg.get("economy.deep_vault_cost", 500000000))
        if self._net(u) < cost:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"The facility costs {self.money(cfg, cost)}."),
                ephemeral=True,
            )
            return
        before = self._net(u)
        self._take(u, cost)
        u["deep_vault_owned"] = True
        await self.persist(gid)
        await self._log(gid, interaction.user.id, self._net(u) - before, "deep-vault-build", u)
        await ui.respond(
            interaction,
            embed=ui.base_embed(
                title="🏦 Federal Reserve Bedrock Vault",
                description=f"Construction complete. The facility cost {self.money(cfg, cost)}.\n\nHoldings here are absent from every public balance and ranking, earn no interest, and cannot be spent until withdrawn.",
                color=cfg.color,
            ),
            ephemeral=True,
        )

    @deepvault_group.command(name="deposit", description="Move wallet donuts into hidden bedrock storage.")
    @app_commands.describe(amount="Amount: number, 25k/2.5m/1b, half, or all; blank deposits all")
    async def deepvault_deposit(self, interaction: discord.Interaction, amount: Optional[str] = None) -> None:
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        gid = interaction.guild_id
        u = self.user(gid, interaction.user.id)
        await self._settle_deep_with_log(gid, interaction.user.id, u)
        if not u.get("deep_vault_owned"):
            await ui.respond(
                interaction,
                embed=ui.error_embed("Build the facility with `/deepvault build` first."),
                ephemeral=True,
            )
            return
        overdue = self._overdue_debt(gid, interaction.user.id)
        if overdue > 0:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    f"You cannot conceal funds while owing {self.money(cfg, overdue)} in overdue loans."
                ),
                ephemeral=True,
            )
            return
        wallet = int(u.get("donuts", 0))
        amount = self._parse_all_amount(amount, wallet)
        if amount is None:
            await ui.respond(
                interaction,
                embed=ui.error_embed("Use a number, K/M/B/T shorthand, `half`, or `all`."),
                ephemeral=True,
            )
            return
        if amount <= 0 or amount > wallet:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"Your wallet contains {self.money(cfg, wallet)}."),
                ephemeral=True,
            )
            return
        pct = max(0, min(100, int(cfg.get("economy.deep_vault_deposit_fee_pct", 2))))
        fee = amount * pct // 100
        stored = amount - fee
        if stored <= 0:
            await ui.respond(
                interaction,
                embed=ui.error_embed("That deposit is too small after handling fees."),
                ephemeral=True,
            )
            return
        u["donuts"] = wallet - amount
        u["deep_vault_balance"] = self._deep_vault_total(u) + stored
        await self.persist(gid)
        await self._log(gid, interaction.user.id, -amount, "deep-vault-deposit", u)
        await ui.respond(
            interaction,
            embed=ui.base_embed(
                title="🏦 Classified deposit complete",
                description=f"**Stored:** {self.money(cfg, stored)}\n**Handling fee burned:** {self.money(cfg, fee)}\n**Hidden balance:** {self.money(cfg, self._deep_vault_total(u))}",
                color=cfg.color,
            ),
            ephemeral=True,
        )

    @deepvault_group.command(name="withdraw", description="Begin a delayed withdrawal from hidden storage.")
    @app_commands.describe(amount="Amount: number, 25k/2.5m/1b, half, or all; blank withdraws all")
    async def deepvault_withdraw(
        self, interaction: discord.Interaction, amount: Optional[str] = None
    ) -> None:
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        gid = interaction.guild_id
        u = self.user(gid, interaction.user.id)
        await self._settle_deep_with_log(gid, interaction.user.id, u)
        if not u.get("deep_vault_owned"):
            await ui.respond(
                interaction,
                embed=ui.error_embed("You do not own a Federal Reserve Bedrock Vault."),
                ephemeral=True,
            )
            return
        pending = max(0, int(u.get("deep_vault_withdraw_amount", 0)))
        pending_at = _parse(u.get("deep_vault_withdraw_at"))
        if pending and pending_at is not None:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(
                    f"A withdrawal of {self.money(cfg, pending)} is already processing and completes <t:{int(pending_at.timestamp())}:R>."
                ),
                ephemeral=True,
            )
            return
        balance = self._deep_vault_total(u)
        amount = self._parse_all_amount(amount, balance)
        if amount is None:
            await ui.respond(
                interaction,
                embed=ui.error_embed("Use a number, K/M/B/T shorthand, `half`, or `all`."),
                ephemeral=True,
            )
            return
        if amount <= 0 or amount > balance:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"The hidden facility contains {self.money(cfg, balance)}."),
                ephemeral=True,
            )
            return
        ready = _now() + dt.timedelta(minutes=float(cfg.get("economy.deep_vault_withdraw_minutes", 5)))
        u["deep_vault_withdraw_amount"] = amount
        u["deep_vault_withdraw_at"] = ready.isoformat()
        await self.persist(gid)
        await ui.respond(
            interaction,
            embed=ui.base_embed(
                title="🚚 Classified withdrawal scheduled",
                description=f"{self.money(cfg, amount)} reaches your wallet <t:{int(ready.timestamp())}:R>. Use `/deepvault status` after that time to receive it. It remains inside the facility—and vulnerable to a located B-2 or EMRG strike—until collected.",
                color=cfg.color,
            ),
            ephemeral=True,
        )

    @deepvault_group.command(name="status", description="Privately inspect your hidden storage facility.")
    async def deepvault_status(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        gid = interaction.guild_id
        u = self.user(gid, interaction.user.id)
        moved = await self._settle_deep_with_log(gid, interaction.user.id, u)
        if not u.get("deep_vault_owned"):
            await ui.respond(
                interaction,
                embed=ui.error_embed("No hidden facility exists. Build one with `/deepvault build`."),
                ephemeral=True,
            )
            return
        embed = ui.base_embed(
            title="🏦 Federal Reserve Bedrock Vault — CLASSIFIED",
            description=f"**Hidden balance:** {self.money(cfg, self._deep_vault_total(u))}",
            color=cfg.color,
        )
        if moved:
            embed.add_field(
                name="✅ Withdrawal delivered",
                value=f"{self.money(cfg, moved)} was moved into your wallet.",
                inline=False,
            )
        pending = max(0, int(u.get("deep_vault_withdraw_amount", 0)))
        ready = _parse(u.get("deep_vault_withdraw_at"))
        if pending and ready is not None:
            embed.add_field(
                name="⏳ Withdrawal processing",
                value=f"**Amount:** {self.money(cfg, pending)}\n**Completes:** {status_ui.deadline(ready)}",
                inline=False,
            )
        embed.add_field(
            name="➡️ Next action",
            value="Use `/deepvault deposit` or `/deepvault withdraw`; both accept `all`, `half` and amounts such as `25m`.",
            inline=False,
        )
        embed.set_footer(
            text="No interest · excluded from public net worth · U-2/Deimos/SR-71 intelligence enables B-2/EMRG penetration"
        )
        await ui.respond(interaction, embed=embed, ephemeral=True)

    @aa_group.command(
        name="s400-build", description="Construct an S-400 battery that can engage strategic bombers."
    )
    async def aa_s400_build(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid = interaction.guild_id
        u = self.user(gid, interaction.user.id)
        self._s400_settle(u)
        ready = _parse(u.get("s400_building_at"))
        if u.get("s400_owned"):
            await ui.respond(
                interaction,
                embed=ui.warn_embed("Your S-400 Triumf battery is already operational."),
                ephemeral=True,
            )
            return
        if ready is not None:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(f"The S-400 site completes <t:{int(ready.timestamp())}:R>."),
                ephemeral=True,
            )
            return
        cost = int(cfg.get("economy.s400_cost", 500000000))
        if self._net(u) < cost:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"An S-400 site costs {self.money(cfg, cost)}."),
                ephemeral=True,
            )
            return
        before = self._net(u)
        self._take(u, cost)
        done = _now() + dt.timedelta(hours=float(cfg.get("economy.s400_build_hours", 16)))
        u["s400_building_at"] = done.isoformat()
        u["s400_owned"] = False
        await self.persist(gid)
        await self._log(gid, interaction.user.id, self._net(u) - before, "s400-build", u)
        await ui.respond(
            interaction,
            embed=ui.base_embed(
                title="🛡️ S-400 Triumf — construction begun",
                description=f"{interaction.user.mention} commissioned a counter-stealth battery for {self.money(cfg, cost)}. It becomes operational <t:{int(done.timestamp())}:R>.",
                color=cfg.color,
            ),
        )

    @aa_group.command(name="s400-load", description="Purchase and load 40N6E strategic-bomber interceptors.")
    @app_commands.describe(qty="Interceptors to load")
    async def aa_s400_load(
        self, interaction: discord.Interaction, qty: app_commands.Range[int, 1, 10] = 1
    ) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction)
        if cfg is None:
            return
        gid = interaction.guild_id
        u = self.user(gid, interaction.user.id)
        self._s400_settle(u)
        if not u.get("s400_owned"):
            await ui.respond(
                interaction,
                embed=ui.error_embed("You need an operational S-400 battery first."),
                ephemeral=True,
            )
            return
        cap = int(cfg.get("economy.s400_interceptor_cap", 2))
        loaded = int(u.get("s400_interceptors", 0))
        room = max(0, cap - loaded)
        if room <= 0:
            await ui.respond(
                interaction,
                embed=ui.warn_embed(f"The S-400 is fully loaded (**{loaded}/{cap}**)."),
                ephemeral=True,
            )
            return
        want = min(int(qty), room)
        unit = int(cfg.get("economy.s400_interceptor_cost", 40000000))
        cost = want * unit
        if self._net(u) < cost:
            await ui.respond(
                interaction,
                embed=ui.error_embed(f"Loading {want} 40N6E interceptor(s) costs {self.money(cfg, cost)}."),
                ephemeral=True,
            )
            return
        before = self._net(u)
        self._take(u, cost)
        u["s400_interceptors"] = loaded + want
        await self.persist(gid)
        await self._log(gid, interaction.user.id, self._net(u) - before, "s400-load", u)
        note = "" if want == int(qty) else f" (trimmed to the {cap}-missile capacity)"
        await ui.respond(
            interaction,
            embed=ui.base_embed(
                title="🚀 S-400 loaded",
                description=f"{interaction.user.mention} loaded **{want}** 40N6E interceptor(s){note}.\n**Battery:** {loaded + want}/{cap}\n**B-2 kill chance:** {_public_s400_intercept_chance(cfg, 'b2')}% per strike\n**B-52H base kill chance:** {_public_s400_intercept_chance(cfg, 'b52')}% per strike\n**XB-70 kill chance:** {int(cfg.get('economy.vehicle_xb70_s400_intercept_chance', 10))}% per strike",
                color=cfg.color,
            ),
        )

    @aa_group.command(name="s400-status", description="Inspect your strategic-bomber air-defense battery.")
    async def aa_s400_status(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return
        gid = interaction.guild_id
        u = self.user(gid, interaction.user.id)
        self._s400_settle(u)
        await self.persist(gid)
        ready = _parse(u.get("s400_building_at"))
        if ready is not None:
            desc = f"**State:** 🏗️ Building\n**Completes:** {status_ui.deadline(ready)}"
        elif u.get("s400_owned"):
            cap = int(cfg.get("economy.s400_interceptor_cap", 2))
            desc = (
                "**State:** 🟢 Operational"
                if int(u.get("s400_interceptors", 0))
                else "**State:** 🟠 Ammunition empty"
            )
        else:
            desc = "**State:** ⚪ No S-400 battery constructed."
        embed = ui.base_embed(
            title=f"🛡️ {interaction.user.display_name}'s S-400 Triumf", description=desc, color=cfg.color
        )
        if u.get("s400_owned") and ready is None:
            embed.add_field(
                name="🚀 Loaded interceptors",
                value=f"**40N6E:** {int(u.get('s400_interceptors', 0))}/{cap}",
                inline=False,
            )
            embed.add_field(
                name="🛡️ Standard interception chances",
                value=f"**B-2 intercept chance:** {_public_s400_intercept_chance(cfg, 'b2')}%\n**B-52H base intercept chance:** {_public_s400_intercept_chance(cfg, 'b52')}%\n**XB-70 intercept chance:** {int(cfg.get('economy.vehicle_xb70_s400_intercept_chance', 10))}%",
                inline=False,
            )
        embed.add_field(
            name="➡️ Next action",
            value="Use `/aa s400-build` to construct a battery or `/aa s400-load` to prepare interceptors.",
            inline=False,
        )
        await ui.respond(interaction, embed=embed)

    @app_commands.command(name="warfare", description="Every strategic weapon, counter, and how to use it.")
    async def warfare(self, interaction: discord.Interaction) -> None:
        cfg = await self._guard(interaction, needs_channel=False)
        if cfg is None:
            return

        def compact(value: int | float) -> str:
            if type(value) is int:
                return ui.format_donuts(value)
            value = float(value)
            for divisor, suffix in ((1000000000, "B"), (1000000, "M"), (1000, "K")):
                if abs(value) >= divisor:
                    rendered = f"{value / divisor:.2f}".rstrip("0").rstrip(".")
                    return f"{rendered}{suffix}"
            return f"{value:g}"

        def price(key: str, fallback: int) -> str:
            return f"{compact(int(cfg.get(f'economy.{key}', fallback)))} {self.name(cfg)}"

        def hours(key: str, fallback: float) -> str:
            value = float(cfg.get(f"economy.{key}", fallback))
            return f"{compact(value)}h"

        def minutes(key: str, fallback: float) -> str:
            value = float(cfg.get(f"economy.{key}", fallback))
            return "instant" if value <= 0 else f"{compact(value)}m"

        def vehicle_terms(model: str) -> str:
            return guides.vehicle_terms(cfg, model)

        def ammo_terms(model: str) -> str:
            return guides.ammo_terms(cfg, model)

        icbm_wallet = int(cfg.get("economy.icbm_wallet_pct", 65))
        icbm_bank = int(cfg.get("economy.icbm_bank_pct", 65))
        icbm_items = int(cfg.get("economy.icbm_item_pct", 30))
        icbm_fish = int(cfg.get("economy.icbm_fish_pct", 40))
        icbm_cap = int(cfg.get("economy.icbm_damage_cap", 0) or 0)
        cap_note = f"; money cap {compact(icbm_cap)}" if icbm_cap else "; uncapped money damage"
        start = ui.base_embed(
            title="⚔️ Warfare Manual 1/8 — Start Here",
            description="Every attack needs a **platform** (the vehicle) and usually **ammunition**. The vehicle is reusable until a defense destroys it; ammunition is spent on every shot.",
            color=cfg.color,
        )
        start.add_field(
            name="✈️ Normal vehicle: exact order",
            value="**1. Build it:** run `/vehicle build` and select the vehicle.\n**2. Wait:** construction must finish in real time.\n**3. Load it:** run `/vehicle load`, select the same vehicle and buy ammunition.\n**4. Wait again if needed:** some ammunition has a loading timer.\n**5. Attack:** run `/vehicle deploy`, select the vehicle and target, then confirm.\n**6. Check it:** `/arsenal` privately shows every vehicle, defense and orbital project, including builds, loaded ammo and cooldowns. Use its page buttons.",
            inline=False,
        )
        start.add_field(
            name="⚠️ Three important exceptions",
            value=f"**B-2:** build the bomber, build an ICBM, then use `/vehicle arm`. This converts one finished ICBM into its B61-12 bomb. Only then can it deploy.\n**U-2:** build and deploy it. It carries no ammunition because it spies instead of attacking.\n**Deimos:** build and deploy it with no ammunition. It is a public {int(cfg.get('economy.deimos_success_chance', 60))}% reciprocal scan; failure permanently destroys the tracker.\n**SR-71:** build and deploy it with no ammunition for a complete private snapshot of the target's strategic defenses and the same unified targeting package.",
            inline=False,
        )
        start.add_field(
            name="🛰️ NYX counter-intelligence",
            value="`/space build craft:NYX` → wait → `/space fleet launch craft:NYX` → survive GBI interception → `/space fleet mission craft:NYX mission:Jam`. Ghost Protocol blocks third-party status/asset reports, coalition intel and all recon for 3 hours, every 10 hours. Old recon locks are invalidated; your own reports stay private. You disappear entirely from public leaderboard/season rankings until expiry; real funds still change normally. A landed THOR strike can then damage only one random category: donuts, Earth vehicles, or one exposed ISD part. See `/space fleet guide craft:NYX` for prices and the complete public manual.",
            inline=False,
        )
        start.add_field(
            name="🧭 What should I build first?",
            value="• Want to hit **money only**? Start with the **F-15E**.\n• Want to destroy one or two particular **item stacks**? Use the **MQ-9B**.\n• Want to wipe **fish and possibly a rod**? Use the **Virginia submarine**.\n• Want to attack **enemy air defense**? Use **HIMARS**.\n• Want flexible raids on **ground ammunition**? Use the **AH-64E Apache**.\n• Want a broad **vehicle, ammo, air-defense and donut raid**? Choose the **A-10C or Su-34**.\n• Want to stop every outgoing strategic attack for **one hour**? Use the **B-52H**.\n• Want a broad conventional wipe? Choose the **B-2**; **THOR/ISD** are separate megaprojects.\n• Want a risky public intelligence duel? Use the **F3 DeathMARK Tracker**.\n• Want to map every enemy defense before attacking? Use the **SR-71A Blackbird**.\n• Want to weaken or garrison a **country**? Choose an **Abrams or Leopard**.\n• Want electronic warfare or anti-construction strikes? See the **support-weapons page**.\n• Want to protect yourself cheaply? Start with **regular Radar AA**.",
            inline=False,
        )
        start.set_footer(text="All prices and timers below use the server's current live settings.")
        strategic = ui.base_embed(
            title="☢️ Warfare Manual 2/8 — Strategic Weapons",
            description="These are the broadest weapons. Read the numbered preparation steps carefully.",
            color=ui.COLOR_BAD,
        )
        strategic.add_field(
            name="🚀 ICBM — broad percentage damage",
            value=f"**Prepare:** `/icbm build` → pay {price('icbm_build_cost', 750000000)} → wait {hours('icbm_build_hours', 2)} → `/icbm launch`.\n**Damage:** destroys {icbm_wallet}% wallet, {icbm_bank}% bank, {icbm_items}% of every consumable stack, and {icbm_fish}% of fish{cap_note}.\n**Requirement:** target needs {price('icbm_min_target_balance', 1000)} in either wallet or bank.\n**Reuse:** launch cooldown {hours('icbm_cooldown_hours', 6)}.\n**Stopped by:** an active Radar AA shield or loaded regular AA rockets.",
            inline=False,
        )
        strategic.add_field(
            name="🦇 B-2 Spirit — total wipe",
            value=f"**Prepare:** `/vehicle build` B-2 ({vehicle_terms('b2')}) → build an ICBM → `/vehicle arm` → wait {hours('vehicle_b2_arm_hours', 1)} → `/vehicle deploy` B-2.\n**Damage:** wipes 100% of visible wallet, bank, consumables and fish. It reaches the Bedrock Vault only while you have an active U-2, Deimos or SR-71 target package.\n**Reuse:** bomber turnaround is {compact(float(cfg.get('economy.vehicle_b2_cooldown_days', 1 / 24)) * 24)}h, but every new strike needs another converted ICBM.\n**Ground counter:** a loaded S-400. Regular AA does nothing. An active F-22 patrol can also engage (20%). If intercepted, both the bomber and its bomb are permanently lost.",
            inline=False,
        )
        strategic.add_field(
            name="💣 B-52H — shut down attacks for one hour",
            value=f"**Prepare:** `/vehicle build` B-52H ({vehicle_terms('b52')}) → `/vehicle load` Mk 82 bomb sticks ({ammo_terms('b52')}) → `/vehicle deploy`.\n**Effect:** blocks the victim's ICBM launches, strategic vehicle deployments and country invasions for {minutes('vehicle_b52_lockdown_minutes', 60)}. Building, loading, economy commands and automatic defenses remain available. It also destroys {int(cfg.get('economy.vehicle_b52_wallet_damage_pct', 25))}% of wallet donuts and {int(cfg.get('economy.vehicle_b52_bank_damage_pct', 15))}% of bank-vault donuts. The Bedrock Deep Vault is always immune, even with reconnaissance. Repeated hits reset rather than add time.\n**Cooldown:** {minutes('vehicle_b52_cooldown_minutes', 120)} · **Counter:** loaded S-400 at a base {int(cfg.get('economy.vehicle_b52_intercept_chance', 50))}%. The first three shootdowns can be repaired for 25%/50%/75% of the original price in 1/2/3 hours. A fourth shootdown destroys that airframe permanently; a newly built replacement starts a fresh repair history.",
            inline=False,
        )
        strategic.add_field(
            name="🔭 U-2S Dragon Lady — reconnaissance, not damage",
            value=f"**Prepare:** `/vehicle build` U-2 ({vehicle_terms('u2')}) → wait → `/vehicle deploy` U-2. No ammunition is required.\n**Result:** privately creates a {hours('u2_recon_window_hours', 6)} unified package: it locates the Bedrock Vault, identifies completed vehicles for A-10C/Su-34 and records active construction for Dark Eagle.\n**Reuse:** cooldown {hours('vehicle_u2_cooldown_hours', 0.5)}.\n**Stopped by:** one loaded regular AA rocket. An active AA Shield adds +{int(cfg.get('economy.aa_shield_vehicle_bonus', 10))} points to that roll. A successful intercept permanently destroys the U-2.",
            inline=False,
        )
        strategic.add_field(
            name="☠️ F3 DeathMARK Tracker — reciprocal public scan",
            value=f"**Prepare:** `/vehicle build` F3 DeathMARK ({vehicle_terms('deimos')}) → wait → `/vehicle deploy` F3 DeathMARK. No ammunition is required.\n**Roll:** flat {int(cfg.get('economy.deimos_success_chance', 60))}% success; nothing modifies this roll.\n**Success:** publicly pings both players, exposes both exact Bedrock Vault balances, reveals both defensive-readiness reports, and gives each player a {hours('deimos_recon_window_hours', 4)} unified package against the other for B-2/DDG-1000, A-10C/Su-34 and Dark Eagle targeting. Strategist 21 extends only its holder's package to {compact(float(cfg.get('economy.deimos_recon_window_hours', 4)) * 1.25)}h.\n**Failure:** reveals nothing and permanently destroys the tracker. **Reuse:** {hours('vehicle_deimos_cooldown_hours', 0.75)} after a successful scan.",
            inline=False,
        )
        strategic.add_field(
            name="🛫 SR-71A Blackbird — complete defense map",
            value=f"**Prepare:** `/vehicle build` SR-71 ({vehicle_terms('sr71')}) → wait → `/vehicle deploy` SR-71. No ammunition is required.\n**Success:** privately reveals Radar AA, AA Shield, S-400, Aegis/SM-6, P-8A, Patriot, Aegis BMD/SM-3 and GBI/EKV readiness, ammunition and active timers. It locates the Bedrock Vault, identifies completed vehicles for A-10C/Su-34 targeting, and records active conventional builds for Dark Eagle for one SR-71 cooldown.\n**Risk:** one loaded S-400 or Patriot missile is spent for a {int(cfg.get('economy.vehicle_sr71_shotdown_chance', 20))}% interception attempt. S-400 fires first; regular AA cannot intercept it. **Reuse:** {hours('vehicle_sr71_cooldown_hours', 1)} mission cooldown.",
            inline=False,
        )
        focused = ui.base_embed(
            title="🎯 Warfare Manual 3/8 — Focused Weapons",
            description="For every system below: `/vehicle build` → wait → `/vehicle load` → wait if shown → `/vehicle deploy`. Strategic attacks do not grant separate victim immunity.",
            color=cfg.color,
        )
        focused.add_field(
            name="⚓ DDG-1000 EMRG — remove 40% of money",
            value=f"**Platform:** {vehicle_terms('zumwalt')} · **HVP:** {ammo_terms('zumwalt')}.\nDestroys {int(cfg.get('economy.vehicle_zumwalt_damage_pct', 40))}% of wallet and bank. With an active U-2, Deimos or SR-71 package, it also destroys the same percentage of the Bedrock Vault.\n**Cooldown:** {minutes('vehicle_zumwalt_cooldown_minutes', 30)} · **Counter:** loaded Aegis/SM-6. An intercept destroys the shot, not the ship.",
            inline=False,
        )
        focused.add_field(
            name="🌊 Virginia Block V — destroy fishing holdings",
            value=f"**Platform:** {vehicle_terms('virginia')} · **Mk 48:** {ammo_terms('virginia')}.\nWipes every fish, turns autofishing off, and can permanently destroy the equipped rod.\n**Cooldown:** {minutes('vehicle_virginia_cooldown_minutes', 30)} · **Counter:** loaded P-8A/Mk 54. A successful defense permanently destroys the submarine.",
            inline=False,
        )
        focused.add_field(
            name="🚀 M142 HIMARS — destroy air-defense ammunition",
            value=f"**Platform:** {vehicle_terms('himars')} · **M31A2:** {ammo_terms('himars')}.\nIn `/vehicle deploy`, fill the **objective** box with `regular-aa` to clear every loaded regular AA rocket, or `s400` to destroy one loaded S-400 interceptor.\n**Cooldown:** {minutes('vehicle_himars_cooldown_minutes', 30)} · **Counter:** loaded Patriot. An intercept destroys the rocket, not the HIMARS launcher.",
            inline=False,
        )
        focused.add_field(
            name="🚁 AH-64E Apache — raid ground ammunition",
            value=f"**Platform:** {vehicle_terms('apache')} · **AGM-179A JAGM:** {ammo_terms('apache')}.\nChoose objective `regular-aa`, `s400`, `patriot`, or `himars`. A hit destroys 50% of the remaining loaded regular AA, one S-400 round, one Patriot round, or up to two HIMARS rockets. The mission still launches when the selected depot is empty.\n**Cooldown:** {minutes('vehicle_apache_cooldown_minutes', 20)} · **Counter:** loaded regular AA at a base {int(cfg.get('economy.vehicle_apache_aa_intercept_chance', 30))}%. A successful defense permanently destroys the Apache. An active AA Shield adds +{int(cfg.get('economy.aa_shield_vehicle_bonus', 10))} points to the loaded-rocket roll.\n**RAH-66 conversion:** `/vehicle upgrade` costs {price('vehicle_comanche_upgrade_cost', 1000000000)} and takes {hours('vehicle_comanche_upgrade_hours', 6)}. {_comanche_rounds_per_mission(cfg)} JAGMs destroy {int(cfg.get('economy.vehicle_comanche_precision_damage_pct', 100))}% of every ready offensive-ammunition and defensive-interceptor category; no objective is needed. All THOR assets survive: modules, rods, GBI/EKVs, BMD SM-3s and their build/load queues. Loaded Radar AA gets a random {int(cfg.get('economy.vehicle_comanche_intercept_chance_min', 20))}–{int(cfg.get('economy.vehicle_comanche_intercept_chance_max', 25))}% base shootdown solution. An intercept grounds it and empties its JAGMs without removing the conversion; `/vehicle repair` costs {price('vehicle_comanche_repair_cost_min', 100000000)}–{price('vehicle_comanche_repair_cost_max', 200000000)} and takes {hours('vehicle_comanche_repair_hours', 2)}. **Comanche cooldown:** {hours('vehicle_comanche_cooldown_hours', 8)}.",
            inline=False,
        )
        focused.add_field(
            name="🛩️ MQ-9B — destroy one or two consumable types",
            value=f"**Platform:** {vehicle_terms('mq9')} · **Hellfire:** {ammo_terms('mq9')}.\nA one-Hellfire precision strike destroys {int(cfg.get('economy.vehicle_mq9_item_damage_pct', 100))}% of the consumable selected in **objective**. Add a different **secondary_objective** to spend two Hellfires and destroy {int(cfg.get('economy.vehicle_mq9_split_damage_pct', 60))}% of both stacks. Both fields have autocomplete. It cannot target plushies, rods, vehicles or ammunition.\n**Cooldown:** {minutes('vehicle_mq9_cooldown_minutes', 15)} · **Counter:** loaded regular AA at a base {int(cfg.get('economy.vehicle_mq9_aa_intercept_chance', 20))}%. A successful defense permanently destroys the drone. An active AA Shield adds +{int(cfg.get('economy.aa_shield_vehicle_bonus', 10))} points to the loaded-rocket roll.",
            inline=False,
        )
        focused.add_field(
            name="🦅 F-15E — remove 50% of visible money",
            value=f"**Platform:** {vehicle_terms('f15e')} · **JASSM-ER:** {ammo_terms('f15e')}.\nDestroys {int(cfg.get('economy.vehicle_f15e_damage_pct', 50))}% of wallet and bank. It cannot see or damage the Bedrock Vault, even with reconnaissance.\n**Cooldown:** {minutes('vehicle_f15e_cooldown_minutes', 30)} · **Counter:** loaded Patriot. A successful defense permanently destroys the aircraft.",
            inline=False,
        )
        focused.add_field(
            name="🐗 A-10C / 🦆 Su-34 — multirole battlefield raids",
            value=f"**A-10C:** {vehicle_terms('a10')} · **package:** {ammo_terms('a10')}. Select one completed conventional vehicle: {int(cfg.get('economy.vehicle_a10_vehicle_destroy_chance', 70))}% chance to destroy it; otherwise all of its loaded payload is destroyed. Also removes 70% from the two fullest other offensive stores, 75% of loaded/reserve Radar AA, one S-400/Patriot round, 20% wallet and 10% bank. Loaded Radar AA has a 50% base interception roll; Patriot is the fallback.\n**Su-34:** {vehicle_terms('su34')} · **package:** {ammo_terms('su34')}. Its safer sortie has a {int(cfg.get('economy.vehicle_su34_vehicle_destroy_chance', 50))}% vehicle-destruction roll, or destroys 50% of that vehicle's payload if it survives. It also removes 45% from two other offensive stores, 50% Radar AA, one S-400/Patriot round, 15% wallet and 8% bank. Loaded S-400 has a 25% interception roll; Patriot is the fallback at 20%.\nBoth launch even if the selected vehicle is absent. Deep Vault, items, fish, rods, countries, unfinished vehicles and every THOR asset are immune. A successful intercept permanently destroys the aircraft.",
            inline=False,
        )
        support = ui.base_embed(
            title="📡 Warfare Manual 4/8 — Electronic & Hypersonic Weapons",
            description="Build and load all five through `/vehicle build` and `/vehicle load`, then deploy them through `/vehicle deploy`. None can touch Project THOR hardware or countermeasures.",
            color=cfg.color,
        )
        support.add_field(
            name="📦 C-130J Rapid Dragon — choose a raid mode",
            value=f"**Platform:** {vehicle_terms('c130j')} · **Pallet:** {ammo_terms('c130j')}. Automatically destroys {int(cfg.get('economy.vehicle_c130j_damage_pct', 40))}% of the {int(cfg.get('economy.vehicle_c130j_target_count', 3))} fullest conventional ammunition stores. Leave objective blank or choose `distributed` for this mode. With current U-2/Deimos/SR-71 recon, choose `focused:<depot>` to destroy 80% of one ready conventional depot instead. Cooldown {minutes('vehicle_c130j_cooldown_minutes', 90)}. Loaded Patriot has a {int(cfg.get('economy.vehicle_patriot_intercept_chance', 40))}% shot at the package; the aircraft survives.",
            inline=False,
        )
        support.add_field(
            name="⚡ CHAMP — conventional electronics blackout",
            value=f"**System:** {vehicle_terms('champ')} · **Missile:** {ammo_terms('champ')}. A hit suppresses Radar AA, AA Shield, S-400, Aegis, P-8A, Patriot and Javelin for {minutes('vehicle_champ_blackout_minutes', 45)}, and blocks U-2/Deimos/SR-71 launches. Loaded Patriot can intercept the missile before detonation. THOR and stored ammunition remain intact.",
            inline=False,
        )
        support.add_field(
            name="👻 ADM-160 MALD-X — make a defense waste a shot",
            value=f"**Cell:** {vehicle_terms('maldx')} · **Decoy:** {ammo_terms('maldx')}. Choose `regular-aa`, `s400`, `aegis`, `p8`, `patriot` or `javelin` in **objective**. It has a flat {int(cfg.get('economy.vehicle_maldx_success_chance', 75))}% chance to consume one selected interceptor. It deals no direct damage and cannot select THOR defenses.",
            inline=False,
        )
        support.add_field(
            name="🔥 LRHW Dark Eagle — kill a build in progress",
            value=f"**Battery:** {vehicle_terms('lrhw')} · **C-HGB:** {ammo_terms('lrhw')}. Select a conventional vehicle in **objective**. Without construction intelligence this is a blind shot: a wrong guess still spends the round and cooldown. Any successful U-2, Deimos or SR-71 package temporarily filters the objective list to detected builds, including their completion times. A valid launch destroys that unfinished build with no refund, bypasses ordinary defenses, and cannot target completed vehicles or THOR.",
            inline=False,
        )
        support.add_field(
            name="🧊 XB-70 Valkyrie — high-speed strategic bomber",
            value=f"**Bomber:** {vehicle_terms('xb70')} · **B53:** {ammo_terms('xb70')}. A hit rolls {int(cfg.get('economy.vehicle_xb70_damage_min_pct', 70))}–{int(cfg.get('economy.vehicle_xb70_damage_max_pct', 80))}% once and removes that percentage from wallet and bank. The Bedrock Vault is immune. Ground engagement requires loaded S-400 missiles, at a public {int(cfg.get('economy.vehicle_xb70_s400_intercept_chance', 10))}% chance; a hit destroys the bomber.",
            inline=False,
        )
        armored = ui.base_embed(
            title="🛞 Warfare Manual 5/8 — Armored Country Warfare",
            description="Frontline tanks are reusable country specialists. Players may own both the Abrams and Leopard: use the Abrams for survivability and the Leopard for stronger breach power. Either can attack an enemy territory, but only one tank may serve as your active country garrison at a time.",
            color=ui.COLOR_WARN,
        )
        armored.add_field(
            name="Two independent frontline chassis",
            value=f"**M1A2 SEPv3 Abrams:** {vehicle_terms('m1a2')} · **M829A4:** {ammo_terms('m1a2')}. Destroys 25% fortification; Trophy may defeat a successful Javelin track.\n**Leopard 2A7A1:** {vehicle_terms('leopard2a7')} · **DM63:** {ammo_terms('leopard2a7')}. Destroys 30% fortification but has no Trophy reroll. Both hold six rounds, load two per batch and use a {minutes('vehicle_m1a2_cooldown_minutes', 45)} mission cooldown.",
            inline=False,
        )
        armored.add_field(
            name="Armored breach — attack a claimed country",
            value="Use `/vehicle deploy`, choose your tank, select the ruler as `target`, then choose one of that player's countries in `objective`. A success applies that chassis' fortification damage, never GDP or development, and marks the country Breached for 30 minutes. Only the breaching attacker gets +20 invasion power there. One attacker can maintain one marker; a new success replaces the old one. Each country accepts only one successful breach per 60 minutes.",
            inline=False,
        )
        armored.add_field(
            name="Country garrison",
            value="Use `/vehicle garrison` and choose one country you rule. Movement takes 30 minutes. A loaded garrison contributes +25 defense power and spends one tank round whenever that country is invaded. An empty garrison remains visible but contributes nothing. It cannot attack while assigned. `/vehicle withdraw` returns it to the hangar in 15 minutes.",
            inline=False,
        )
        armored.add_field(
            name="FGM-148F Javelin Team — dedicated counter",
            value=f"Build the team with `/vehicle build` ({vehicle_terms('javelin')}) and load missiles ({ammo_terms('javelin')}). It protects every country you rule and spends one missile when an armored breach arrives. Its public track chance is 30%. Abrams Trophy APS may defeat a track; the Leopard has no reroll. A stopped tank retreats safely 70% of the time; otherwise it needs a 100–175M, 90-minute repair.",
            inline=False,
        )
        defense = ui.base_embed(
            title="🛡️ Warfare Manual 6/8 — Defenses, Storage & Rules",
            description="You never press a button when attacked. If the correct defense is operational and loaded, the bot fires it automatically. Defensive ammunition is consumed whether it hits or misses.",
            color=ui.COLOR_OK,
        )
        defense.add_field(
            name="📡 Regular Radar AA — ICBM and light-aircraft defense",
            value=f"**Instant shield:** `/aa shield` costs {price('aa_shield_cost', 25000000)} and gives each incoming ICBM a {int(cfg.get('economy.aa_shield_chance', 50))}% interception roll while active.\n**Rocket battery:** `/aa build` ({price('aa_rocket_cost', 300000)} each, {minutes('aa_rocket_build_minutes', 40)}) → wait → `/aa load` → `/aa status`. Loaded rockets automatically engage ICBMs, U-2s, MQ-9Bs, Apaches, Comanches and A-10Cs. While the shield is active, it adds **+{int(cfg.get('economy.aa_shield_vehicle_bonus', 10))} points** to those aircraft rolls; one loaded rocket is still required and consumed, with no second roll.",
            inline=False,
        )
        defense.add_field(
            name="🛡️ S-400 — bomber defense",
            value=f"Run `/aa s400-build` ({price('s400_cost', 500000000)} / {hours('s400_build_hours', 16)}) → wait → `/aa s400-load` ({price('s400_interceptor_cost', 40000000)} each) → `/aa s400-status`.\nEach loaded interceptor has a {int(cfg.get('economy.s400_intercept_chance', 30))}% base chance against a B-2 and a {int(cfg.get('economy.vehicle_b52_intercept_chance', 50))}% base chance against a B-52H. Against the XB-70 it uses a dedicated {int(cfg.get('economy.vehicle_xb70_s400_intercept_chance', 10))}% solution; against the Su-34 it uses {int(cfg.get('economy.vehicle_su34_s400_intercept_chance', 25))}%.",
            inline=False,
        )
        defense.add_field(
            name="🚢 Dedicated vehicle defenses",
            value=f"Build and load these with `/vehicle build` and `/vehicle load`:\n• **Aegis + SM-6:** {int(cfg.get('economy.vehicle_aegis_intercept_chance', 30))}% against DDG-1000 EMRG shots.\n• **P-8A + Mk 54:** {int(cfg.get('economy.vehicle_p8_intercept_chance', 30))}% against Virginia submarines.\n• **Patriot + PAC-3 MSE:** {int(cfg.get('economy.vehicle_patriot_intercept_chance', 40))}% against HIMARS, F-15E, Rapid Dragon and CHAMP; it is also the A-10/Su-34 fallback when their primary counter is empty.",
            inline=False,
        )
        defense.add_field(
            name="🏦 Bedrock Vault and intelligence",
            value=f"`/deepvault build` costs {price('deep_vault_cost', 500000000)}. `/deepvault deposit` hides donuts from balance commands and leaderboards, but burns a {int(cfg.get('economy.deep_vault_deposit_fee_pct', 2))}% fee. Withdrawals take {minutes('deep_vault_withdraw_minutes', 5)}.\nSuccessful U-2 and SR-71 flights create private unified packages; Deimos exposes both players publicly and creates reciprocal packages. All three support B-2/DDG-1000 vault penetration, A-10C/Su-34 completed-vehicle targeting and Dark Eagle construction tracking. F-15Es and ordinary attacks still cannot reach the vault. If recon finds no completed vehicles, the A-10C/Su-34 objective list says so instead of showing blind targets.",
            inline=False,
        )
        defense.add_field(
            name="✅ Before attacking, check all five",
            value="**1.** Is construction finished?  **2.** Is ammunition loaded?  **3.** Is the vehicle off cooldown?\n**4.** Did you select the correct target and objective?  **5.** Does the target belong to your coalition? Coalition signatories cannot attack one another. **6.** Is your strategic runway free of a B-52 lockdown?\n\nUse `/arsenal` for all vehicles, defenses, orbital projects and interception craft, and `/deepvault status` for hidden storage.",
            inline=False,
        )
        defense.set_footer(
            text="A destroyed vehicle must be rebuilt. A missed or intercepted ammunition unit is gone."
        )
        recovery = ui.base_embed(
            title="Raven Rock continuity recovery",
            color=ui.COLOR_OK,
            description=f"Build `/continuity build` for {price('continuity_cost', 5 * 10**12)} / {hours('continuity_build_hours', 24)}. Use `/continuity store` before an attack to secure up to {price('continuity_funds_cap', 100 * 10**12)}, {int(cfg.get('economy.continuity_item_cap', 20))} consumables, {int(cfg.get('economy.continuity_plushie_cap', 5))} plushies, one rod with enchants and one eligible vehicle. Store/retrieve transfers take {hours('continuity_transfer_hours', 0.25)}. THOR seals retrieval for {hours('continuity_thor_seal_hours', 12)}; a pending outbound recovery waits until the seal expires. Incoming assets reserve their inventory slots. Any legacy conflict returns to bunker storage rather than replacing paid builds or enchants. Check `/continuity status` for private contents.\n\nDS-1 Death Star: use `/deathstar guide` for section construction, artificial-moon production, planet kills and squadron counters. To intercept sections, build `/deathstar squadron` (10 quintillion / 12h) BEFORE deployment, then `/deathstar windows` → `/deathstar intercept window:<DS-ID>` within 2 bot-online hours (15% per attempt). This is a dedicated package, not an ordinary X-wing/B-wing. THOR/ISD use different counters.",
        )
        sections = [start, strategic, focused, support, armored, defense]
        pages = []
        for section in sections:
            pages.extend(
                ui.field_pages(
                    section.title,
                    section.description,
                    [(field.name, field.value, field.inline) for field in section.fields],
                    color=section.color.value,
                )
            )
        pages.extend(guides.thor_pages(cfg, color=cfg.color))
        pages.extend(guides.air_dominance_pages(cfg, color=cfg.color))
        pages.extend(guides.isd_pages(cfg, color=cfg.color))
        pages.append(recovery)
        for index, page in enumerate(pages, 1):
            page.title = f"Warfare Manual {index}/{len(pages)} — {page.title.split(' — ', 1)[-1]}"
            page.set_footer(text="Current ordinary server recipes · only the requester can navigate")
        view = ui.Paginator(pages, interaction.user.id)
        await ui.respond(
            interaction, embed=pages[0], view=view, allowed_mentions=discord.AllowedMentions.none()
        )

    @app_commands.command(
        name="arsenal",
        description="Privately inspect all vehicles, weapons, orbital projects and interception craft.",
    )
    async def arsenal(self, interaction: discord.Interaction) -> None:
        nyx.protect_report(interaction, self.econ)
        cfg = await self._guard(interaction, needs_channel=False, ephemeral=True)
        if cfg is None:
            return
        await ui.defer_response(interaction, ephemeral=True)
        from szofie import deathstar

        space_cog = self.bot.get_cog("SpaceCog")
        if space_cog is not None:
            await space_cog.settle_fleet(interaction.guild_id)
        target = interaction.user
        u = self.user(interaction.guild_id, target.id)
        doc = self.econ.store.load(interaction.guild_id)
        now = _now()
        ready = self._icbm_settle(u)
        max_stock = self._icbm_cap(cfg, u)
        states = {model: self._vehicle_settle(u, model) for model in VEHICLE_CATALOG}
        thor_state.settle_aegis_bmd(states["aegis"], cfg)
        marks = sum(
            (
                1
                for tid in list(u.setdefault("recon_targets", {}))
                if str(tid).isdigit() and self._active_recon(u, int(tid))
            )
        )
        thor_state.settle(u, cfg, now)
        isd_state.settle(u, now)
        space_state.settle(u, now)
        moon_income = deathstar.settle(u, now)
        fields = []

        def add_field(*, name: str, value: str, inline: bool = False) -> None:
            fields.append((name, value, False))

        add_field(name="🛰️ NYX counter-intelligence satellite", value=nyx.summary(u, cfg, now))

        def line(label: str, value: Any) -> str:
            return f"**{label}:** {value}"

        def location_name(key: str) -> str:
            body = space_state.BODIES.get(key)
            return body.name if body else key

        def finish(raw: Any) -> str:
            when = _parse(raw)
            return f"<t:{int(when.timestamp())}:f> (<t:{int(when.timestamp())}:R>)" if when else "pending"

        def window_line(record: Optional[Dict[str, Any]]) -> str:
            if not record:
                return "Awaiting launch resolution"
            if not record.get("announced", True):
                return "Awaiting public launch warning"
            if "remaining_seconds" in record:
                return (
                    f"{ui.human_duration(max(0, int(record['remaining_seconds'])))} bot-online time remaining"
                )
            return f"Ends {finish(record.get('resolves_at'))}"

        def components(catalog, state, windows) -> str:
            lines = []
            for key, spec in catalog.items():
                part = state["components"][key]
                status = str(part.get("status", "none"))
                text = "Not built" if status == "none" else status.replace("_", " ").title()
                if part.get("ready_at"):
                    text += f" · completes {finish(part['ready_at'])}"
                if status == "launching":
                    text += f" · {window_line(windows.get(part.get('window_id') or part.get('launch_id')))}"
                lines.append(f"**{spec.get('short', spec['name'])}**\n{line('Status', text)}")
            return "\n\n".join(lines)

        lockdown = _strategic_lockdown_until(u)
        if lockdown is not None:
            add_field(
                name="💥 Strategic runway lockdown",
                value=line(
                    "Status",
                    f"Offensive launches and country invasions disabled until {finish(lockdown.isoformat())}",
                ),
                inline=False,
            )
        blackout = _electronic_blackout_until(u)
        if blackout is not None:
            add_field(
                name="⚡ CHAMP electronics blackout",
                value=line(
                    "Status",
                    f"Conventional defenses and intelligence-aircraft launches suppressed until {finish(blackout.isoformat())}",
                ),
                inline=False,
            )
        missile_lines = [f"**Ready:** {ready}/{max_stock}"]
        building = _parse(u.get("icbm_building_at"))
        if building is not None:
            missile_lines.append(f"**Assembly line:** completes <t:{int(building.timestamp())}:R>")
        last_launch = _parse(u.get("icbm_launch_at"))
        if last_launch:
            cooldown = last_launch + dt.timedelta(seconds=self._icbm_cooldown_secs(cfg, u))
            if cooldown > now:
                missile_lines.append(f"**Launch cooldown:** {finish(cooldown.isoformat())}")
        add_field(name="🚀 ICBM silo", value="\n".join(missile_lines))
        air_defenses, dedicated_defenses = self._sr71_defense_report(cfg, u)
        preparation = self._arsenal_preparation_jobs(u)
        if preparation:
            progress = []
            for label, job in sorted(preparation.items(), key=lambda row: row[1]["due"]):
                destination = (
                    "cold stockpile · battery still needs `/aa load`"
                    if job["kind"] == "reserve"
                    else job["kind"].title()
                )
                preparing = f"×{job['qty']} · {destination}"
                progress.append(
                    f"**{label}**\n{line('Preparing', preparing)}\n"
                    + line("Completes", status_ui.deadline(job["due"]))
                )
            add_field(name="⏳ Ammunition preparation", value="\n\n".join(progress))
        for heading, report in (
            ("🛡️ Air defenses", air_defenses),
            ("🛰️ Orbital and dedicated defenses", dedicated_defenses),
        ):
            add_field(
                name=heading,
                value=self._arsenal_defense_sections(
                    report,
                    preparation,
                    aa_loaded=int(u.get("aa_rockets_loaded", 0)),
                    aa_stock=int(u.get("aa_rockets_stock", 0)),
                ),
            )

        def vehicle_line(state: Dict[str, Any], model: str) -> str:
            construction = _parse(state.get("building_until"))
            if construction is not None:
                return (
                    line("Status", "🏭 Building")
                    + "\n"
                    + line("Completion", finish(construction.isoformat()))
                )
            if not state.get("owned"):
                return line("Status", "⚫ Not fielded")
            last = _parse(state.get("last_deploy_at"))
            seconds = self._vehicle_cooldown_seconds(
                cfg, model, comanche=model == "apache" and bool(state.get("comanche_upgraded"))
            )
            operational = "🟢 Mission-ready"
            turnaround = ""
            if last is not None and (_now() - last).total_seconds() < seconds:
                operational = f"🟠 Turnaround until <t:{int(last.timestamp() + seconds)}:R>"
                turnaround = "\n" + line(
                    "Cooldown", finish((last + dt.timedelta(seconds=seconds)).isoformat())
                )
            if model == "b2":
                arming = _parse(state.get("arming_until"))
                payload = (
                    f"B61-12 integration <t:{int(arming.timestamp())}:R>"
                    if arming is not None
                    else "☢️ B61-12 ARMED"
                    if state.get("armed")
                    else "Payload bay empty"
                )
                if arming is not None or not state.get("armed"):
                    operational = "🟠 Payload not ready"
                return line("Status", operational) + "\n" + line("Payload", payload) + turnaround
            if model in LOADABLE_VEHICLES:
                spec = VEHICLE_CATALOG[model]
                cap = int(cfg.get(f"economy.{spec['cap']}", int(spec["fallback_cap"])))
                extra = ""
                if model in air.JETS:
                    if state.get("jet_damaged"):
                        due = state.get("jet_repair_until")
                        operational = "🔧 Depot recovery" if due else "🔴 Damaged · `/vehicle repair`"
                        extra += "\n" + line(
                            "Repair",
                            finish(due)
                            if due
                            else self.money(
                                cfg, int(cfg.get("economy." + spec["cost"], spec["fallback_cost"])) // 4
                            )
                            + " · 2h",
                        )
                    patrol_due = _parse(state.get("patrol_until"))
                    if patrol_due and patrol_due > now:
                        operational = "🛡️ Combat air patrol"
                        extra += "\n" + line("Patrol", finish(patrol_due.isoformat()))
                if (
                    not int(state.get("ammo", 0))
                    and (not state.get("jet_damaged"))
                    and (not air.patrol(u, state.get("patrol_target")))
                ):
                    operational = "🟠 Ammunition empty"
                    if spec["short"] in preparation:
                        operational = "🟠 Reloading · ammunition not ready"
                if model == "b52" and state.get("b52_damaged"):
                    phase = max(1, min(3, int(state.get("b52_repair_count", 1))))
                    repairing = _parse(state.get("b52_repairing_until"))
                    cost, hours = _b52_repair_terms(cfg, phase)
                    if repairing is not None:
                        operational = f"🔧 Depot repair {phase}/3 until <t:{int(repairing.timestamp())}:R>"
                    else:
                        operational = f"🔴 Grounded · recoverable damage {phase}/3"
                        extra = "\n" + line(
                            "Repair", f"{self.money(cfg, cost)} · {hours}h · `/vehicle repair`"
                        )
                if model == "apache":
                    upgrading = _parse(state.get("comanche_upgrade_until"))
                    if upgrading is not None:
                        operational = "🔧 RAH-66 conversion in progress"
                        extra = "\n" + line("Upgrade", f"RAH-66 conversion {finish(upgrading.isoformat())}")
                    elif state.get("comanche_upgraded"):
                        if state.get("comanche_damaged"):
                            repairing = _parse(state.get("comanche_repairing_until"))
                            quote = max(0, int(state.get("comanche_repair_cost", 0)))
                            if repairing is not None:
                                operational = (
                                    f"🔧 RAH-66 depot repair until <t:{int(repairing.timestamp())}:R>"
                                )
                                extra = ""
                            else:
                                operational = "🔴 RAH-66 grounded"
                                extra = "\n" + line(
                                    "Repair", self.money(cfg, quote) if quote else "Assessment required"
                                )
                        else:
                            extra = "\n" + line("Upgrade", "RAH-66 Comanche package operational")
                if model in ARMORED_MODELS:
                    damaged_key, cost_key, repairing_key = _armored_damage_fields(model)
                    label = self._vehicle_label(model)
                    repairing = _parse(state.get(repairing_key))
                    transfer = _parse(state.get("garrison_transfer_until"))
                    if state.get(damaged_key):
                        if repairing is not None:
                            operational = f"🔧 {label} depot repair until <t:{int(repairing.timestamp())}:R>"
                            extra = ""
                        else:
                            quote = max(0, int(state.get(cost_key, 0)))
                            operational = f"🔴 {label} grounded · battle damage"
                            extra = "\n" + line("Repair", f"{self.money(cfg, quote)} · `/vehicle repair`")
                    elif transfer is not None:
                        operational = f"🟠 {label} redeploying until <t:{int(transfer.timestamp())}:R>"
                        destination = country_state.BY_ID.get(str(state.get("garrison_transfer_target")))
                        extra = "\n" + line("Destination", destination.name) if destination else ""
                    elif state.get("garrison_country"):
                        country = country_state.BY_ID.get(str(state.get("garrison_country")))
                        operational = "🛡️ Country garrison"
                        extra = "\n" + line("Garrison", f"{country.flag} {country.name}") if country else ""
                loading = _parse(state.get("loading_until"))
                if loading:
                    extra += "\n" + line(
                        "Loading",
                        f"{int(state.get('loading_qty', 0))} loading · completes {finish(loading.isoformat())}",
                    )
                elif spec["short"] in preparation:
                    job = preparation[spec["short"]]
                    extra += "\n" + line(
                        "Loading", f"×{job['qty']} · completes {status_ui.deadline(job['due'])}"
                    )
                return (
                    line("Status", operational)
                    + "\n"
                    + line("Ammunition", f"{int(state.get('ammo', 0))}/{cap} · {spec['ammo']}")
                    + extra
                    + turnaround
                )
            return line("Status", operational)

        links = [
            (key, air.link(u, key, self.user(interaction.guild_id, int(key))))
            for key in list(u.get("fusion_links", {}))
            if str(key).isdigit()
        ]
        links = [(key, record) for key, record in links if record]
        if links:
            add_field(
                name="📡 Sensor-Fusion Links",
                value="\n".join(
                    ("Single-use link · expires " + finish(record["until"]) for _, record in links)
                ),
            )
        for model in DEPLOYABLE_VEHICLES:
            spec = VEHICLE_CATALOG[model]
            add_field(
                name=f"{spec['emoji']} {spec['short']}", value=vehicle_line(states[model], model), inline=True
            )
        defenses = []
        for model in ("aegis", "p8", "patriot", "javelin"):
            spec = VEHICLE_CATALOG[model]
            defenses.append(f"**{spec['short']}**\n{vehicle_line(states[model], model)}")
        add_field(name="🛡️ Dedicated counter-systems", value="\n\n".join(defenses))
        thor = thor_state.normalize(u)
        thor_state.settle(u, cfg)
        assembly = thor_state.parse_time(thor.get("assembling_until"))
        if thor.get("operational"):
            platform = "🟢 Operational"
        elif assembly:
            platform = f"🛠️ Orbital assembly completes <t:{int(assembly.timestamp())}:R>"
        else:
            orbiting = sum(
                (
                    1
                    for component in thor["components"].values()
                    if component.get("status") in {"orbit", "assembled"}
                )
            )
            platform = f"⚫ Not assembled · {orbiting}/3 module(s) in orbit"
        chambering = thor_state.parse_time(thor.get("chambering_until"))
        cradle = (
            "ARMED"
            if thor.get("chambered")
            else f"loading <t:{int(chambering.timestamp())}:R>"
            if chambering
            else "empty"
        )
        gbi_building = thor_state.parse_time(thor.get("gbi_building_until"))
        gbi = f"{int(thor.get('gbi_stock', 0))}/{int(cfg.get('economy.thor_gbi_stock_capacity', 2))}"
        if gbi_building:
            gbi += f" · {int(thor.get('gbi_building_qty', 0))} building <t:{int(gbi_building.timestamp())}:R>"
        aegis = states.get("aegis", {})
        bmd = (
            f"SM-3 Block IIA ×{int(aegis.get('sm3_ammo', 0))}"
            if aegis.get("owned") and aegis.get("bmd_owned")
            else "not operational"
        )
        add_field(
            name="⚡ Project THOR",
            value=f"**Status:** {platform}\n**Magazine:** {int(thor.get('rods', 0))}/{int(cfg.get('economy.thor_rod_capacity', 6))} · release cradle {cradle}\n**GBI/EKV stock:** {gbi}\n**Aegis BMD:** {bmd}",
            inline=False,
        )
        add_field(
            name="🧩 THOR modules",
            value=components(thor_state.COMPONENTS, thor, doc.get("thor_launches", {})),
        )
        thor_timers = []
        for key, label in (
            ("resupply_until", f"Magazine batch ×{int(thor.get('resupply_qty', 0))}"),
            ("service_until", "Platform servicing"),
            ("gbi_block2_building_until", "GBI Block II upgrade"),
        ):
            if thor.get(key):
                thor_timers.append(line(label, finish(thor[key])))
        thor_timers.append(
            line("GBI Block II package", "Installed" if thor.get("gbi_block2_owned") else "Not installed")
        )
        last_shot = _parse(thor.get("last_strike_at"))
        if last_shot:
            next_shot = last_shot + dt.timedelta(
                hours=float(cfg.get("economy.thor_strike_cooldown_hours", 3))
            )
            if next_shot > now:
                thor_timers.append(line("Next THOR shot", finish(next_shot.isoformat())))
        thor_timers.append(line("Aegis BMD response", "One missile per incoming rod"))
        add_field(name="🔧 THOR support and turnaround", value="\n".join(thor_timers))
        isd = isd_state.normalize(u)
        space = space_state.normalize(u)
        isd_lines = [
            line(
                "Status",
                "Damaged" if isd["damaged"] else "Operational" if isd["operational"] else "Not assembled",
            ),
            line("Mode", str(space["mode"]).title()),
            line("Location", location_name(space["location"])),
            line("Payload", "Bombardment charge armed" if isd["armed"] else "Bombardment charge empty"),
            line("B-wing interception packages", f"{isd['counter_stock']}/2"),
        ]
        for state, key, label in (
            (isd, "assembling_until", "Orbital assembly"),
            (isd, "arming_until", "Charge integration"),
            (isd, "repairing_until", "Depot repair"),
            (isd, "counter_building_until", "B-wing package build"),
            (space, "refit_until", f"Refit to {space.get('refit_target')}"),
            (space, "travel_until", f"Travel to {space.get('destination')}"),
            (space, "load_until", "ISD cargo loading"),
        ):
            if state.get(key):
                isd_lines.append(line(label, finish(state[key])))
        if isd.get("active_window_id"):
            isd_lines.append(
                line(
                    "Interception window",
                    window_line(doc.get("isd_windows", {}).get(isd["active_window_id"])),
                )
            )
        add_field(name="🌌 Imperial Star Destroyer and B-wings", value="\n".join(isd_lines))
        add_field(
            name="🧩 ISD components", value=components(isd_state.COMPONENTS, isd, doc.get("isd_windows", {}))
        )
        from szofie import space_fleet

        for key, spec in space_fleet.CRAFT.items():
            hull = space_fleet.ship(u, key)
            construction = space_fleet.build_until(u, key)
            owned = space_fleet.owned(u, key)
            status = (
                "Building"
                if construction
                else "Repairing"
                if hull["repair_until"]
                else "Damaged"
                if hull["damaged"]
                else "Launching"
                if hull["launch"]
                else "Mission-ready"
                if owned and hull["deployed"]
                else "Grounded · launch required"
                if owned
                else "Not fielded"
            )
            location = (
                "Earth orbit"
                if hull["location"] == "earth" and hull["deployed"]
                else location_name(hull["location"])
            )
            craft_lines = [
                line("Status", status),
                line("Location", location),
                line("Ammunition", f"{space_fleet.payload(u, key)}/{spec.capacity} · {spec.payload_name}"),
            ]
            if key == "xwing":
                craft_lines.append(
                    line(
                        "X-wing proton torpedoes", f"{int(space['xwing_ammo'])}/{space_state.XWING_AMMO_CAP}"
                    )
                )
            mission = hull.get("mission")
            if isinstance(mission, dict):
                craft_lines.append(line("Mission", f"{mission['kind']} · {mission['body']}"))
                if mission["kind"] == "escort":
                    craft_lines.append(line("Patrol responses", mission.get("responses_remaining", 1)))
                if mission.get("objectives"):
                    craft_lines.append(line("Objectives", ", ".join(mission["objectives"])))
            if key == "cutlass" and isinstance(space.get("expedition"), dict):
                craft_lines.append(line("Field mission", "In progress · `/space status` for objectives"))
            for label, raw in (
                ("Construction", construction),
                ("Earth launch", (hull["launch"] or {}).get("ready_at")),
                ("Loading", hull["payload_until"]),
                ("Repair", hull["repair_until"]),
                ("Mission completion", (mission or {}).get("ready_at")),
                ("Cooldown", hull["combat_until"]),
            ):
                if raw:
                    detail = finish(raw)
                    if label == "Loading":
                        detail = f"{int(hull['payload_qty'])} loading · completes {detail}"
                    craft_lines.append(line(label, detail))
            add_field(name=f"🛰️ {spec.name}", value="\n".join(craft_lines))
        station = deathstar.normalize(u)
        ds_lines = [
            line(
                "Status",
                "Disabled"
                if station["damaged"]
                else "Operational"
                if station["operational"]
                else "Not assembled",
            ),
            line("Payload", "Superlaser charge armed" if station["charged"] else "Superlaser charge empty"),
            line("Alliance interception squadrons", f"{station['squadron_stock']}/{deathstar.MAX_SQUADRONS}"),
            line("Heavy transport", "Ready" if station["transport_owned"] else "Not owned"),
            line("Location", location_name(station["location"])),
        ]
        for key, label in (
            ("assembling_until", "Final assembly"),
            ("repairing_until", "Station repairs"),
            ("charge_until", "Superlaser charging"),
            ("squadron_until", "Alliance squadron build"),
            ("transport_until", "Heavy transport build"),
            ("travel_until", f"Travel to {station.get('destination')}"),
        ):
            if station.get(key):
                ds_lines.append(line(label, finish(station[key])))
        if station.get("assembly_remaining"):
            ds_lines.append(
                line("Assembly paused", f"{ui.human_duration(int(station['assembly_remaining']))} remaining")
            )
        last_fire = _parse(station.get("last_fire_at"))
        if last_fire:
            next_fire = last_fire + dt.timedelta(hours=deathstar.SHOT_COOLDOWN_HOURS)
            if next_fire > now:
                ds_lines.append(line("Cooldown", finish(next_fire.isoformat())))
        if station.get("active_window_id"):
            ds_lines.append(
                line(
                    "Interception window",
                    window_line(doc.get("deathstar_windows", {}).get(station["active_window_id"])),
                )
            )
        add_field(name="🌑 DS-1 Death Star and Alliance squadrons", value="\n".join(ds_lines))
        add_field(
            name="🧩 Death Star sections",
            value=components(deathstar.COMPONENTS, station, doc.get("deathstar_windows", {})),
        )
        for label, manifest in (
            ("ISD cargo", space["cargo"]),
            ("ISD loading manifest", space.get("load_manifest") or {}),
            ("Death Star cargo", station["cargo"]),
        ):
            packed = manifest.get("vehicles", {})
            if isinstance(packed, dict) and packed:
                lines = []
                for model, state in packed.items():
                    if model in VEHICLE_CATALOG and isinstance(state, dict):
                        lines.append(
                            f"**{VEHICLE_CATALOG[model]['short']}**\n{line('Storage', 'Stowed · unavailable until unloaded')}\n{vehicle_line(state, model)}"
                        )
                if lines:
                    add_field(name="📦 " + label + " — stowed vehicles", value="\n\n".join(lines))
        inv = u.get("inventory", {}) or {}
        offensive = []
        if int(inv.get("drill", 0)):
            offensive.append(line("Majin Drill", f"×{int(inv['drill'])}"))
        if int(inv.get("copcall", 0)):
            offensive.append(line("Cop Call", f"×{int(inv['copcall'])}"))
        if int(inv.get("hex", 0)):
            offensive.append(line("Hex", f"×{int(inv['hex'])}"))
        if offensive:
            add_field(name="⚔️ Raid equipment", value="\n".join(offensive))
        protections = [
            line(CONSUMABLES[key]["name"], f"×{int(inv[key])}")
            for key in ("lock", "uno", "getaway")
            if int(inv.get(key, 0)) and key in CONSUMABLES
        ]
        if protections:
            add_field(name="🛡️ Personal protection equipment", value="\n".join(protections))
        if marks:
            add_field(name="🛰️ Active target packages", value=line("Ready", f"{marks} active"))
        await self.persist(interaction.guild_id)
        if moon_income:
            await self._log(
                interaction.guild_id,
                target.id,
                moon_income,
                "deathstar-production",
                u,
                after_override=deathstar.available(u),
            )
        pages = ui.field_pages(
            f"{target.display_name}'s complete arsenal",
            "Private readiness report: all vehicles, ammunition, defenses and orbital projects. Use the page buttons. Completion timestamps are fixed deadlines; launch windows count bot-online time.",
            fields,
            color=ui.COLOR_WARN,
        )
        for index, page in enumerate(pages, 1):
            page.set_footer(
                text=f"Page {index}/{len(pages)} · Checked {now:%Y-%m-%d %H:%M UTC} · /warfare for instructions"
            )
        await ui.respond(
            interaction,
            ephemeral=True,
            embed=pages[0],
            view=ui.Paginator(pages, target.id, timeout=180),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="lockgame", description="Owner: lock or reopen a single gambling game.")
    @app_commands.describe(game="Which gambling game", lock="True to lock it down, False to reopen it")
    @app_commands.choices(game=[app_commands.Choice(name=g.title(), value=g) for g in gamelocks.GAMES])
    @app_commands.check(checks.is_owner)
    async def lockgame(
        self, interaction: discord.Interaction, game: app_commands.Choice[str], lock: bool
    ) -> None:
        gamelocks.set_locked(game.value, lock)
        still = gamelocks.locked_games()
        if lock:
            desc = f"🔒 **{game.name}** is now **locked down** — nobody can play it until you reopen it."
            color = ui.COLOR_WARN
        else:
            desc = f"🔓 **{game.name}** is **back open**."
            color = ui.COLOR_OK
        embed = ui.base_embed(title="Game lockdown", description=desc, color=color)
        embed.set_footer(
            text="Locked now: " + ", ".join((g.title() for g in still)) if still else "No games locked."
        )
        await ui.respond(interaction, embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EconomyCog(bot))
