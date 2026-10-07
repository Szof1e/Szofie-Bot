"""Player-safe manuals generated from the same public recipes as gameplay.

No account IDs, raw configuration dumps, or account-aware resolvers belong here.
Only explicitly named ordinary settings may enter these renderers.
"""

from typing import Any
from . import deathstar, imperial_star_destroyer as isd, space, space_fleet as fleet, thor, ui
from .catalog import VEHICLE_CATALOG
from .combat import ISD_GROUND_DESTRUCTION_PCT


def money(cfg: Any, key: str, fallback: int) -> str:
    return ui.format_donuts(int(cfg.get("economy." + key, fallback)))


def duration(cfg: Any, key: str, fallback: float, unit: str = "h") -> str:
    value = float(cfg.get("economy." + key, fallback))
    return "instant" if value <= 0 else f"{value:g}{unit}"


def vehicle_terms(cfg: Any, model: str) -> str:
    spec = VEHICLE_CATALOG[model]
    return f"{money(cfg, spec['cost'], spec['fallback_cost'])} donuts / {duration(cfg, spec['build'], spec['fallback_build'])}"


def ammo_terms(cfg: Any, model: str) -> str:
    spec = VEHICLE_CATALOG[model]
    cap = int(cfg.get("economy." + spec["cap"], spec["fallback_cap"]))
    return f"{money(cfg, spec['ammo_cost'], spec['fallback_ammo_cost'])} donuts each · {duration(cfg, spec['load'], spec['fallback_load'], 'm')} load · cap {cap}"


def vehicle_pages(cfg: Any, *, color: int):
    fields = []
    for model, spec in VEHICLE_CATALOG.items():
        text = vehicle_terms(cfg, model)
        if "ammo" in spec:
            text += f"\n{spec['ammo']}: {ammo_terms(cfg, model)}"
        elif model == "b2":
            text += f"\nLoadout: convert a finished ICBM using `/vehicle arm` — {duration(cfg, 'vehicle_b2_arm_hours', 1)} conversion."
        else:
            text += "\nNo ammunition required; use `/vehicle deploy`."
        fields.append((spec["label"], text, False))
    return ui.field_pages(
        "Vehicle recipe catalog", "Current server prices and preparation times.", fields, color=color
    )


def air_dominance_pages(cfg: Any, *, color: int):
    fields = [
        (
            "Loading mission packages",
            "After construction, `/vehicle load vehicle:<jet> qty:<units>` buys packages directly and starts one loading batch. Wait for it to finish; you cannot launch while loading. Each patrol, sensor-link sortie, aircraft hunt or Su-57 pass spends one loaded package, hit or miss. Prices and capacity are on the preparation page. Loading is not an attack and does not deploy the jet.",
            False,
        ),
        (
            "F-22A — Combat Air Patrol",
            "`/vehicle patrol target:<you or ally>` spends one package to protect one player for 2h. One interception ends the patrol, hit or miss: 65% conventional aircraft / 35% fifth-gen / 20% B-2. One patrol per protected player; no stacking. Missiles, submarines, THOR and space assets are ineligible.",
            False,
        ),
        (
            "F-35A — Sensor-Fusion Link",
            "Run U-2, Deimos or SR-71 recon, then `/vehicle link target:<enemy> recipient:<you or ally>`. Spends one package; link lasts 30m and adds 15 percentage points to the next completed conventional vehicle destruction roll (95% cap). Works with A-10, Su-34, J-20 or Su-57. NYX invalidates the link. No bonus to wallet damage, interception or superweapons.",
            False,
        ),
        (
            "J-20 — High-Value Airframe Hunt",
            "`/vehicle deploy vehicle:j20 target:<enemy> objective:<aircraft>` requires current recon. If the jet survives interception, one completed Earth aircraft has an 80% destruction roll. No collateral. Destroying a U-2 or SR-71 invalidates recon produced by that aircraft, while independent recon survives. Deimos is a tracker, not an eligible J-20 aircraft target.",
            False,
        ),
        (
            "Su-57 — Two-Pass Raid",
            "`/vehicle deploy vehicle:su57 target:<enemy> objective:<objective>`. Objectives: `vehicle:apache` (65% destruction, recon required), `ammo:f15e` (60% ready ammo), `aa:s400` (60% ready defence ammo), or `wallet` (20% wallet). Pool damage rounds up. After a successful first pass, the pilot-only Make another pass button authorizes a different objective within 5m, spending another package. First/second SAM shootdown: 20%/35%. Maximum two passes; one cooldown.",
            False,
        ),
        (
            "Worked combinations — no new jet buffs",
            "**Hunt:** U-2/Deimos/SR-71 recon → F-35 `/vehicle link` → J-20 hunt: after surviving interception, airframe destruction rises from 80% to 95%. For a battlefield sweep, A-10 rises 70%→85% or Su-34 50%→65% on that one vehicle roll. The 30-minute link is consumed once; it does not increase wallet/ammo damage or shootdown odds.\n**Protect:** F-22 `/vehicle patrol` on yourself or an ally; its 2-hour patrol has ONE engagement, 65% conventional / 35% fifth-gen / 20% B-2, with only the strongest eligible defence firing.\n**Suppress:** Su-57 targets one defence depot, then optionally a DIFFERENT target within 5m. The second pass costs another package and raises SAM shootdown from 20% to 35%. NYX invalidates recon and links; these combinations never target THOR assets.",
            False,
        ),
    ]
    pages = ui.field_pages(
        "Air Dominance — Missions",
        "Build → wait → load packages → wait → deploy. Check `/arsenal` privately for readiness. Mission results are public; only the pilot can use the second-pass button. Errors and personal receipts stay private.",
        fields,
        color=color,
    )
    preparation = []
    for model in ("f22", "f35", "j20", "su57"):
        spec = VEHICLE_CATALOG[model]
        cooldown = duration(cfg, spec["cooldown"], spec["fallback_cooldown"], "m")
        repair = ui.format_donuts(int(cfg.get("economy." + spec["cost"], spec["fallback_cost"])) // 4)
        preparation.append(
            (
                spec["short"],
                vehicle_terms(cfg, model)
                + "\n"
                + ammo_terms(cfg, model)
                + f"\nTurnaround: {cooldown}. Recoverable shootdown: {repair} donuts / 2h via `/vehicle repair`.",
                False,
            )
        )
    pages.extend(
        ui.field_pages(
            "Air Dominance — Preparation and Counters",
            "Loaded S-400/Patriot can engage new jets; regular AA cannot. No ready defence means no spontaneous shootdown. Only the strongest eligible patrol/SAM gets ONE attempt and spends its resource. CHAMP suppresses these defences. A patrol spends its package when activated; a second engagement is impossible. THOR destroys these Earth jets even on patrol. These jets cannot target THOR hardware, ISD/Death Star parts, NYX or spacecraft. Mission cooldowns are separate from country invasions. All four jets may support `/country invade` with one loaded package and the ordinary 10–15m country cooldown; this does not create links, patrols or extra passes. Aegis hulls are excluded from Su-57 vehicle targets to preserve their THOR BMD hardware; ordinary SM-6 ammo remains eligible.",
            preparation,
            color=color,
        )
    )
    return pages


def thor_pages(cfg: Any, *, color: int):
    price = lambda key, fallback: money(cfg, key, fallback) + " donuts"
    hours = lambda key, fallback: duration(cfg, key, fallback)
    count = lambda key, fallback: int(cfg.get("economy." + key, fallback))
    recipes = "\n".join(
        (
            f"• {spec['short']}: {price(spec['cost_key'], spec['cost'])} / {hours(spec['hours_key'], spec['hours'])}"
            for spec in thor.COMPONENTS.values()
        )
    )
    total = sum((count(s["cost_key"], s["cost"]) for s in thor.COMPONENTS.values()))
    total += len(thor.COMPONENTS) * count("thor_launch_cost", 2 * 10**12) + count(
        "thor_assembly_cost", 2 * 10**12
    )
    tracking = f"{count('thor_gbi_chance_min', 10)}–{count('thor_gbi_chance_max', 30)}%"
    block2 = f"{count('thor_gbi_block2_chance_min', 20)}–{count('thor_gbi_block2_chance_max', 35)}%"
    batch = " / ".join((hours(key, fallback) for key, fallback in thor.RESUPPLY_KEYS.values()))
    single = max(0, min(100, count("thor_sm3_intercept_chance", 30)))
    return [
        ui.base_embed(
            title="Project THOR 1/3 — Build and Reach Orbit",
            color=color,
            description=f"**1. Fabricate three unique modules, one at a time:**\n{recipes}\n\n**2. Launch each completed module:** `/thor launch` costs {price('thor_launch_cost', 2 * 10**12)} per module, with a public {duration(cfg, 'thor_ascent_minutes', 30, 'm')} ascent window. Use `/thor gbi-build` in advance: {price('thor_gbi_cost', 250 * 10**9)} each, {hours('thor_gbi_build_hours', 0.5)} per batch; ready capacity {count('thor_gbi_stock_capacity', 2)}. Up to {count('thor_gbi_max_interceptors', 3)} non-allied defenders commit one interceptor each. Ordinary tracking is {tracking}. GBI Block II costs {price('thor_gbi_block2_cost', 2 * 10**12)} / {hours('thor_gbi_block2_hours', 12)} and changes future interceptors to {price('thor_gbi_block2_unit_cost', 500 * 10**9)} / {block2}. A hit destroys the module and rocket.\n\n**3. Assemble in orbit:** `/thor orbital-assemble` costs {price('thor_assembly_cost', 2 * 10**12)} / {hours('thor_assembly_hours', 24)}. Uncontested platform total: {ui.format_donuts(total)} donuts.",
        ),
        ui.base_embed(
            title="Project THOR 2/3 — Load and Strike",
            color=ui.COLOR_BAD,
            description=f"`/thor resupply` delivers 1–3 tungsten rods at {price('thor_rod_cost', 15 * 10**12)} each. Batch delivery times (1 / 2 / 3): {batch}; magazine capacity {count('thor_rod_capacity', 6)}.\n\n`/thor chamber` readies one rod in {hours('thor_chamber_hours', 1.5)}. `/thor strike` has a {hours('thor_strike_cooldown_hours', 3)} attacker cooldown and **no target-value threshold**. After {count('thor_service_after_shots', 3)} shots, `/thor service` costs {price('thor_service_cost', 2 * 10**12)} / {hours('thor_service_hours', 12)}.\n\nA hit erases wallet, bank, deep-vault funds, functional items, plushies, fish, rods, enchants, Earth-ground vehicles (including unlaunched spacecraft), defenses, builds and ground ammunition. Launched, orbiting or off-world vehicles keep their payloads and missions. At most one fabricating or ready ISD component is threatened per strike, with a {ISD_GROUND_DESTRUCTION_PCT}% destruction chance. Separately, each landed ordinary strike has a {deathstar.THOR_CONSTRUCTION_DESTRUCTION_PCT}% chance to destroy ONE Death Star section still fabricating. Completed/ready, deploying, orbiting and assembled Death Star sections survive. Launched/orbiting/assembled ISD parts, vanity, countries, debts, diplomacy and orbiting THOR hardware survive. Active NYX Ghost Protocol limits a landed strike to one random category: donuts, Earth vehicles or one exposed ISD part. Empty categories never reroll. See `/space fleet guide craft:NYX`.",
        ),
        ui.base_embed(
            title="Project THOR 3/3 — Countermeasures",
            color=ui.COLOR_OK,
            description=f"**During ascent:** `/thor intercept` commits one already-paid GBI/EKV, {tracking} ordinary tracking. One attempt per player; {count('thor_gbi_max_interceptors', 3)} defenders maximum.\n\n**Against a fired rod:** Own a Flight III Aegis, then `/thor bmd-refit` ({price('thor_bmd_refit_cost', 2 * 10**12)} / {hours('thor_bmd_refit_hours', 18)}). `/thor bmd-load` buys RIM-161 SM-3 Block IIA: {price('thor_sm3_cost', 250 * 10**9)} each, {hours('thor_sm3_load_hours', 3)} loading, capacity {count('thor_sm3_capacity', 3)}. Automatically fires ONE missile per incoming rod: one {single}% interception attempt. Extra loaded missiles are reserved for later strikes, not extra attempts. Ammunition is consumed hit or miss.\n\nCoalition allies cannot attack or intercept one another. A B-52 lockdown blocks strikes, not construction/loading. Raven Rock protects a deliberately stored recovery loadout; retrieval seals for {hours('continuity_thor_seal_hours', 12)} after impact. Any recovery already in progress waits for the seal to end.",
        ),
    ]


def isd_pages(cfg: Any, *, color: int):
    price = lambda key, fallback: money(cfg, key, fallback) + " donuts"
    hours = lambda key, fallback: duration(cfg, key, fallback)
    minutes = lambda key, fallback: duration(cfg, key, fallback, "m")
    count = lambda key, fallback: int(cfg.get("economy." + key, fallback))
    construction = sum((spec["cost"] for spec in isd.COMPONENTS.values()))
    total = construction + len(isd.COMPONENTS) * count("isd_launch_cost", 250 * 10**15)
    total += count("isd_assembly_cost", 2 * 10**18) + count("isd_cinder_cost", 3 * 10**18)
    recipes = "\n".join(
        (
            f"• {spec['name']}: {ui.format_donuts(spec['cost'])} / {spec['hours']}h"
            for spec in isd.COMPONENTS.values()
        )
    )
    return [
        ui.base_embed(
            title="Imperial Star Destroyer 1/4 — Megaproject",
            color=color,
            description=f"Each player may run an independent project, with up to **{isd.MAX_COMPONENT_FABRICATIONS} different components building at once**.\n{recipes}\n\n**Command order:** `/isd fabricate` each component → wait → `/isd launch` each ready component → survive its window → `/isd assemble` → wait. This assembles in orbit; ordinary `/space fleet launch` is not used. Component total: {ui.format_donuts(construction)}. Minimum uncontested first shot: {ui.format_donuts(total)} donuts. A THOR strike threatens at most one fabricating/ready component at {ISD_GROUND_DESTRUCTION_PCT}% destruction chance. Launched, orbiting and assembled parts are safe from THOR. Allies may contribute up to {price('isd_contribution_cap', 10**18)} each; the owner personally pays at least 25% per stage. Contributions are final; ownership cannot transfer.",
        ),
        ui.base_embed(
            title="Imperial Star Destroyer 2/4 — Public Windows",
            color=color,
            description=f"Each module launch: {price('isd_launch_cost', 250 * 10**15)}, {minutes('isd_launch_window_minutes', 60)} bot-online interception window, {count('isd_launch_max_interceptors', 5)} defenders / {count('isd_launch_intercept_chance', 15)}% each.\n\nAssembly: {price('isd_assembly_cost', 2 * 10**18)} / {hours('isd_assembly_hours', 96)}, {minutes('isd_assembly_window_minutes', 120)} bot-online window, {count('isd_assembly_max_interceptors', 8)} defenders / {count('isd_assembly_intercept_chance', 12)}% each; a hit destroys one random component.\n\n**How to defend:** `/isd counter-build` buys a B-wing Ion Assault Package ({price('isd_counter_cost', 45 * 10**15)} / {hours('isd_counter_build_hours', 4)}, capacity {count('isd_counter_capacity', 2)}). Wait, copy the warning's window ID, then `/isd intercept window_id:<ID>`. You do not need an assembled ISD. Optional `/isd launch escort:<craft>` reserves one healthy launched Earth ship: X-wing 15%/1 response, A-wing 25%/2, Hammerhead 35%/3 or Arquitens 30%/2. One ready pack per response; screen first, then the normal intercept roll if unscreened. Self/coalition craft only, no stacking or refilling, unused packs return. Only component launches, not assembly or shots. See `/space guide`.",
        ),
        ui.base_embed(
            title="Imperial Star Destroyer 3/4 — Attack or Expedition",
            color=color,
            description=f"New ships start in attack mode. `/isd arm` builds a charge ({price('isd_cinder_cost', 3 * 10**18)} / {hours('isd_cinder_build_hours', 48)}). `/isd fire` confirms and warns the server for {minutes('isd_cinder_window_minutes', 120)} bot-online time. Up to {count('isd_cinder_max_interceptors', 10)} B-wing defenders get {count('isd_cinder_intercept_chance', 10)}% each; {count('isd_coordination_min_defenders', 5)} defenders unlock {count('isd_coordination_chance', 15)}% coordination for Cinder only. A defense consumes the charge and requires repair.\n\n`/space refit mode:Expedition` ({ui.format_donuts(space.REFIT_COST)} / {space.REFIT_HOURS}h) opens cargo, travel and colonies. Finish loading/travel and unload every category before switching back to attack mode. In attack mode, `/space bombard` spends a charge against your current colonisable world, with a one-hour public warning. For the full expedition interaction map use `/exploration` or `/space guide`; smaller hulls use `/space fleet guide craft:<ship>`.",
        ),
        ui.base_embed(
            title="Imperial Star Destroyer 4/4 — Impact",
            color=ui.COLOR_BAD,
            description="Operation Cinder erases every player's wallet, bank, Deep Vault, items, plushies, fish, rods, upgrades, vehicles, ammunition, badges, titles, countries, THOR hardware, construction, diplomacy, loans and economic progression. It destroys the firing ship, clears casino pots, and ends outstanding blackjack hands. **Only assets sealed inside an operational Raven Rock survive.** A technical recovery backup is created before impact. This is a full server reset, not an ordinary planetary bombardment.",
        ),
    ]


def public_catalog_markdown(cfg: Any) -> str:
    """Deterministic reference: default rules, never live/private data dumps."""
    lines = [
        "# Public recipe catalog",
        "",
        "Generated by `scripts/render_public_catalog.py`; do not edit by hand.",
        "Discord guides use current server settings. This file uses ordinary defaults.",
        "",
        "## Earth vehicles",
        "",
    ]
    for key, spec in VEHICLE_CATALOG.items():
        lines.extend([f"### {spec['label']}", "", vehicle_terms(cfg, key), ""])
        if "ammo" in spec:
            lines.extend([f"{spec['ammo']}: {ammo_terms(cfg, key)}", ""])
        elif key == "b2":
            lines.extend(
                [
                    f"Convert a finished ICBM with `/vehicle arm`: {duration(cfg, 'vehicle_b2_arm_hours', 1)}.",
                    "",
                ]
            )
    from . import nyx

    for pages in (
        air_dominance_pages(cfg, color=0),
        thor_pages(cfg, color=0),
        isd_pages(cfg, color=0),
        nyx.guide_pages(cfg),
    ):
        for page in pages:
            lines.extend([f"## {page.title}", "", page.description, ""])
            for field in page.fields:
                lines.extend([f"### {field.name}", "", field.value, ""])
    lines.extend(["## Ordinary space fleet", ""])
    for spec in fleet.CRAFT.values():
        lines.extend(
            [
                f"### {spec.name}",
                "",
                f"Build {ui.format_donuts(spec.cost)} / {spec.build_hours}h. {spec.payload_name}: {ui.format_donuts(spec.payload_cost)} each; capacity {spec.capacity}. Repair {ui.format_donuts(spec.cost // 4)} / {spec.repair_hours}h.",
                "",
            ]
        )
    return "\n".join(lines)
