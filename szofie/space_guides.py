"""Public space-guide orientation, ship recipes and requester-bound navigation.

This module accepts no player identity/configuration for rule presentation.
It does not resolve combat or change gameplay state.
"""

from typing import List
import discord
from . import space_fleet as fleet, ui


def orientation_sections():
    sections = [
        (
            "Civilian contracts & specimen research",
            "Open `/space status` → **Civilian contracts**, or the same button on `/space journal`. The board offers up to three daily UTC jobs eligible for your ships and surveyed bodies: Cutlass/Carrack Survey (+3 quadrillion), GR-75 Supply of at least 10 materials (+6 quadrillion), Prospector Mine (+5 quadrillion) or Vulture Salvage (+4 quadrillion), plus 2–3 existing materials. Take one contract, then start its matching `/space fleet mission` within the 48-hour deadline. Only a successful arrival pays; old missions, returned cargo and invalid salvage do not qualify. Payment goes automatically to wallet, on top of normal mission rewards. Costs and travel time are previewed before acceptance.\n\nFishing has two bucket deliveries daily UTC: `/fish` or `/bestiary` → **Contracts & research**. Surface deliveries pay 50–150 million; Abyss deliveries pay 500 million–1 billion. Research consumes specified specimens once: Biosensor adds +5 percentage points to rare civilian expedition/survey findings; Habitat converts 5 Void Jelly + 2 Abyssal Serpent into 2 local materials at your own completed base, three batches daily UTC. Esoteric archive entries are optional permanent trophies, never daily requirements.",
        ),
        (
            "Escort a megaproject component launch",
            "Optional `escort` and `escort_owner` fields on `/isd launch` and `/deathstar deploy` protect ONE component window. Use your own or a current coalition ally's healthy, idle, already-launched ship at Earth with ready packs. Choose ONE: X-wing screens 15% for 1 response; A-wing 25% for 2; Hammerhead 35% for 3; Arquitens 30% for 2. Up to that many packs are reserved immediately. Each enemy counter package consumes one response: first roll the screening chance; if not screened, roll the normal interception chance. Screening never becomes guaranteed immunity and doesn't reduce a fleet's entire attack. The assigned ship stays busy, cannot reload or swap during the window, and unused reserved packs return on resolution. This does not protect assembly, firing, THOR/NYX ascent or GR-75 loading. Local `/space fleet escort` missions remain separate and keep their existing rules.",
        ),
        (
            "Choose what you want to do",
            "**New to space?** Start with a Drake Cutlass: explore and collect field samples without an ISD.\n\n**Earn materials:** Prospector mines asteroids; Vulture salvages debris; Carrack surveys and scans players. **Move materials:** GR-75 runs colony supply convoys.\n\n**Hide Earth intel / degrade THOR:** build NYX with `/space build craft:NYX`, launch with `/space fleet launch`, then `/space fleet mission craft:NYX mission:Jam` (no body or target). You disappear entirely from public leaderboard/season rankings for the field's duration; actual funds still change. For the full manual, `/space fleet guide craft:NYX`.\n\n**Fight/protect ordinary ships:** X-wing/A-wing raid or escort; Hammerhead escorts; Interdictor delays convoys; Y-wing bombs one local objective; Arquitens attacks up to two hulls or patrols.\n\n**Own worlds and earn GDP:** an expedition-mode ISD is required for landing, bases, terraforming and claims. An ordinary ship cannot replace it. Death Star development is a separate artificial-moon system.\n\nUse the **topic dropdown** to jump to your ship's worked example, launch rules, cargo, colonies or combat. For one ship, `/space fleet guide craft:<ship>` opens its tutorial directly. Navigation lasts 10 minutes; rerun the guide after it expires. `/space status` = explorer/cargo/colonies; `/space fleet status` = ordinary ships and recon; `/arsenal` = your complete private arsenal. Prices and examples describe standard rules.",
        ),
        (
            "What attacks what?",
            "**Earth spacecraft launching** → `/space fleet intercept`: launched X-wing or A-wing; B-wing package also works against Hammerhead, Interdictor and Arquitens.\n**GR-75 loading an ISD at Earth** → `/space intercept`: X-wing only. This is a DIFFERENT window from hull launch.\n**Prospector mining / Vulture salvage / GR-75 supply in progress** → `/space fleet raid`: X-wing/A-wing reduce cargo; Interdictor delays arrival.\n**Docked ordinary ship** → Carrack recon, then Y-wing Bomb or Arquitens Broadside. Arquitens can also target active escort hulls.\n**Colony mine, lab or ordinary ammo depot** → Carrack recon, then Y-wing Bomb. Arquitens does NOT bomb buildings.\n**Protect local industry/ships** → `/space fleet escort`: X-wing, A-wing, Hammerhead or Arquitens at the SAME body for the SAME protected player. Only the strongest matching escort responds.\n\n**ISD project/weapon window** → B-wing package + `/isd intercept`. **Death Star section/assembly/shot window** → build `/deathstar squadron` in advance, then `/deathstar intercept window:<DS-ID>` (NOT an ordinary X-wing or B-wing). **THOR module ascent** → ready GBI/EKV + `/thor intercept`; descending rods use loaded Aegis SM-3 defenses.\n\n**NYX satellite ascent** → ready GBI/EKV + `/space fleet intercept craft:NYX counter:GBI/EKV`. Ordinary fighters/B-wings cannot intercept NYX; GBI here cannot intercept ordinary hulls.\n\nOrdinary space raids/bombing do not attack Earth wallets, vaults, THOR, ISD parts or Death Star hardware. THOR destroys ALL vehicles and vehicle builds still on Earth's ground, including unlaunched spacecraft. Once launched (including an open ascent window), in orbit or off-world, a vehicle and its onboard payload/missions are safe from THOR. An intercepted launch returns to ground risk. At most one exposed ISD part faces a 10% destruction roll. Separately, a landed ordinary THOR strike has a 1% chance to destroy one still-fabricating Death Star section; ready/deploying/orbiting/assembled sections survive that roll. NYX-degraded strikes do not apply it. Other cargo and server/planet wipes keep their existing rules; this is not Raven Rock.",
        ),
        (
            "Death Star section interception",
            "**Required counter:** the dedicated **Alliance Assault Squadron**, bought with `/deathstar squadron`: 10 quintillion donuts, 12h preparation, three ready packages maximum, one building at a time. You do NOT need an ISD or Death Star. Ordinary X-wing/Y-wing hulls, B-wing packages, AA, Patriot/S-400, GBI/EKV and Aegis cannot replace it.\n\n**Worked example:** `/deathstar squadron` → wait 12h → `/arsenal` to confirm ready stock → watch the section deployment warning → `/deathstar windows` → copy its `DS-...` ID → `/deathstar intercept window:<DS-ID>` (or press **Intercept** on the warning). No body, player target, ship travel or payload command is needed.\n\n**Deploying one section:** 2 bot-online hours; 15% per defender. A hit destroys only that section.\n**Assembling all sections:** 4 bot-online hours; 15% per defender. A hit damages assembly; sections survive.\n**Firing the superlaser:** 2 bot-online hours; 10% per defender. A hit stops the shot and disables the station.\n\nEvery attempt consumes one ready squadron, hit or miss. Up to five different defenders, one attempt each per window; three stockpiled packages do not mean three tries at the same window. Cannot intercept yourself or a coalition ally. These windows pause offline and close after their remaining online time. No open window means no squadron attack on that section; ready/grounded and settled orbiting sections are not valid windows. NYX hides intel but leaves opaque interception IDs usable. For construction, repair costs and the full walkthrough use `/deathstar guide`.",
        ),
        (
            "Know these terms before using a ship",
            "**Build** makes a hull at Earth. **Payload** buys its ammunition/service packs. **Launch** deploys it through a 30-minute public counter window. These are three separate steps.\n\n**Survey** (`/space survey`) unlocks a destination for YOU. It is not player intel. **Recon** (Carrack + Recon mission) scans ONE PLAYER at ONE BODY and unlocks Y-wing/Arquitens targets for 4h. U-2, Deimos and SR-71 Earth intel do not replace Carrack's local space scan.\n\n**Travel** (`/space travel`) moves your shared Cutlass/ISD explorer. **Fleet mission** sends ONE independent ordinary hull; it does not move the ISD, your other ships or its cargo. Use `/space fleet mission`, not `/space travel`, to send a Prospector or Carrack.\n\n**Field materials** are loose samples; **local materials** belong to one colony; **ISD cargo** is carried in its hold. Building/terraforming uses LOCAL materials. Use stow/offload or GR-75 Supply to move them.\n\n**Similar names, separate inventories:** the GR-75 is not the Death Star's Imperial Heavy Transport. An ordinary X-wing/Y-wing hull is not a `/deathstar squadron` package; B-wing packages come from `/isd counter-build`. GBI/EKV ascent interceptors are not Aegis SM-3 rod defenses.\n\nChoose named entries from Discord's dropdowns. Examples below show which fields to fill; `target` means the selected player, `body` the destination, and `objective` the target offered by autocomplete.",
        ),
    ]
    return sections[2:3] + sections[:2] + sections[3:]


SHIP_TUTORIALS = {
    "cutlass": (
        "Explore before owning an ISD. Collect samples and discoveries, not bulk cargo or colony sovereignty.",
        "After launching: `/space survey body:mars` → `/space travel body:mars` → wait for arrival → `/space expedition` (free 6h field mission) → `/space journal`. On an intact asteroid, `/space mine` can prospect every 6h.\nAlternative: `/space fleet mission` → craft: Drake Cutlass → mission: Survey → body: mars. This independent survey spends one supply pack and does not use `/space travel`.",
        "Cannot land/build/terraform/claim or carry ISD bulk cargo. After obtaining an ISD, the main exploration route uses that ISD. Fleet Survey needs a supply pack; the free `/space expedition` does not. Returning the Cutlass to Earth requires relaunch before its next voyage.",
        "Its Earth launch can be intercepted. If idle at another body it can be a Y-wing/Arquitens target. The free field expedition has no random ship-loss roll; it is not an industrial raid target.",
    ),
    "transport": (
        "GR-75 has TWO jobs: load your ISD at Earth, or deliver local materials between colony bases.",
        "ISD cargo: operational ISD → `/space refit mode:Expedition` → at Earth use `/space load` with kind and amount/name → wait 24h → `/space travel` with the ISD.\nLocal supply: `/space fleet mission` → craft: GR-75 transport → mission: Supply → source: mars → body: ceres → amount: all. Both bases must exist; optional target selects an active ally's receiving colony.",
        "Supply needs one service pack and moves 1–100 already-owned materials. ISD loading is its own fee-based route, not the Supply mission. The GR-75 cannot do both jobs at once.",
        "Earth launch: X-wing/A-wing. ISD loading: `/space intercept` X-wing. Supply convoy: X-wing/A-wing raids or Interdictor delay; matching local escorts help. Docked hull: Y-wing/Arquitens.",
    ),
    "xwing": (
        "Raid industrial missions, escort yourself/allies, or intercept ordinary launches and ISD cargo loading.",
        "Payload buys proton torpedoes immediately. `/space fleet launches` → `/space fleet intercept` → target: launching player → craft: their launching hull → counter: T-65 X-wing.\nConvoy raid: `/space fleet raid` → craft: T-65 X-wing → target: player → victim_craft: MISC Prospector.\nProtection: `/space fleet escort` → craft: T-65 X-wing → body: psyche → optional target: ally.\nFor a GR-75's ISD loading window, use `/space intercept target:<player>` instead.",
        "One torpedo per action. Industrial raid base success 65%; escort reduces incoming success by 15 points for one response, up to 6h. Earth-launch and cargo-loading interception have different odds/attempt limits.",
        "Its launch is counterable. Docked/escort hull can be targeted by the appropriate bomber/cruiser. An escorted failed industrial raid can damage the X-wing. It is not a B-wing or Death Star squadron.",
    ),
    "prospector": (
        "Collect many asteroid materials without an ISD or colony. It does not produce colony GDP.",
        "`/space survey body:psyche` → `/space fleet mission` → craft: MISC Prospector → mission: Mine → body: psyche → wait → `/space fleet status`. Reward goes to your completed local base, otherwise field materials.",
        "One mining pack. Yield: 12 + twice resource score. Zone travel + 2h. Asteroids only, not planets/moons. Example Psyche: 28 metals before raid losses. Do not use `/space travel` for this ship.",
        "Earth launch: X-wing/A-wing. Mining in progress: X-wing/A-wing raid or Interdictor delay. Assign an escort at psyche for the Prospector's owner to help. Docked hull: Y-wing/Arquitens.",
    ),
    "vulture": (
        "Turn finite natural debris or eligible battle wrecks into materials and wallet donuts.",
        "`/space survey body:vesta` → `/space fleet wrecks body:vesta` → `/space fleet mission` → craft: Drake Vulture → mission: Salvage → body: vesta. Leave wreck blank for natural debris, or copy an eligible wreck ID. After arrival check `/space fleet status`.",
        "One salvage pack. One shared natural debris claim per body per UTC day: 8 metals + 2 quadrillion before raids. You cannot salvage your own/attacker/allied battle losses. A claimed wreck cannot be farmed again.",
        "Earth launch: X-wing/A-wing. Salvage in progress: X-wing/A-wing raid or Interdictor delay; matching escorts help. Docked hull: Y-wing/Arquitens.",
    ),
    "carrack": (
        "Better surveys, or local PLAYER reconnaissance that unlocks Y-wing and Arquitens strikes.",
        "Survey the destination first. `/space fleet mission` → craft: Anvil Carrack → mission: Recon → body: mars → target: player. Wait for arrival, then `/space fleet status` for private intel.\nWithin the next 4h, send your Y-wing Bomb or Arquitens Broadside against that SAME player at mars. For discoveries instead, choose mission: Survey with no target.",
        "One sensor pack. Recon/survey take 2/4/8/12h by zone. Recon covers local ships, payloads and structures only; checking expired intel does not renew it. Survey yields 6 + resource score materials, with 30% rare specimen chance.",
        "Earth launch: X-wing/A-wing. Docked hull: Y-wing/Arquitens. Its Recon/Survey assignment is NOT a mining/salvage/supply mission, so ordinary industrial raids do not target it.",
    ),
    "awing": (
        "A stronger fighter escort and ordinary Earth-launch interceptor; can also raid industrial missions.",
        "`/space fleet launches` → `/space fleet intercept` → select the launching player/hull → counter: RZ-1 A-wing.\nEscort: `/space fleet escort` → craft: RZ-1 A-wing → body: psyche → optional target: ally.\nRaid: `/space fleet raid` → craft: RZ-1 A-wing → target: player → victim_craft: Drake Vulture.",
        "One concussion missile per action. Launch intercept: 45% versus light/medium, 30% versus heavy hulls. Industrial raid base success 65%; escort reduces incoming success by 25 points, one response/up to 6h. Cannot use the original `/space intercept` GR-75 loading action.",
        "Its own launch can be intercepted. A docked/active escort hull can be a Y-wing/Arquitens target as applicable. Failed industrial raids against escorts may damage it.",
    ),
    "ywing": (
        "Precision-bomb ONE local mine, lab, ammo depot or docked ordinary spacecraft. No fresh Carrack scan = no target.",
        "Carrack Recon → wait → check `/space fleet status` → `/space fleet mission` → craft: BTL-A4 Y-wing → mission: Bomb → body: mars → target: scanned player → objective: choose Mine, Lab, Ammo depot or offered ship. Use the same player/body as the scan; check the result after the 1h flight.",
        "One proton bomb, 80% base hit chance. Structures stop for 4h; ammo depot loses its stock. Hull hits cause repairable damage and lose payloads. Does NOT target active escort hulls, travelling industry, Earth wealth or superweapons.",
        "A matching local escort reduces strike success; a failed strike against escorts can damage the Y-wing. Its launch and its idle docked hull can also be attacked.",
    ),
    "hammerhead": (
        "Strong ordinary local protection, not a superweapon interception package.",
        "Survey the patrol body → `/space fleet escort` → craft: Sphyrna Hammerhead Corvette → body: psyche → optional target: your coalition ally. This protects that player's industry/local hulls at psyche. `/space fleet recall` ends the assignment without refunding its pack.",
        "One defense service pack; up to 6h, one response. Incoming success drops by 35 percentage points. Only the strongest matching escort responds: multiple ships/coalition escorts do not stack. Local escorts are not launch-window defenses. For ISD/Death Star component deployment, use the separate optional escort fields.",
        "Heavy Earth launch: X-wing/A-wing/B-wing package. Idle docked hull: Y-wing/Arquitens. An active escort hull can be selected by Arquitens Broadside.",
    ),
    "interdictor": (
        "Delay a live Prospector mine, Vulture salvage or GR-75 supply convoy without deleting its wealth.",
        "`/space fleet raid` → craft: Imperial Interdictor → target: player → victim_craft: MISC Prospector, Drake Vulture or GR-75 transport. The chosen ship must have an ACTIVE industrial mission; you do not select a construction or superweapon window.",
        "One gravity-well pack. 70% base success; one successful lock adds exactly 1h to arrival. Cannot chain delays on the same mission. The Interdictor has a 2h turnaround. It cannot stop ISD/Death Star/THOR launches, charge windows or ordinary Earth-launch windows.",
        "Local escorts reduce success and may damage it after a miss. Heavy Earth launch: X-wing/A-wing/B-wing. Idle docked hull: Y-wing/Arquitens.",
    ),
    "arquitens": (
        "Attack up to TWO ordinary hulls, or patrol with TWO defensive responses. Does not bomb buildings.",
        "Broadside: Carrack Recon of player at mars → wait → `/space fleet mission` → craft: Imperial Arquitens-class Command Cruiser → mission: Broadside → body: mars → target: scanned player → objective: offered ship → optional second_objective: a DIFFERENT offered ship.\nPatrol: `/space fleet escort` → craft: Arquitens → body: psyche → optional target: ally.",
        "Broadside spends one capacitor pack, takes 1h, rolls 70% independently per selected hull; 3h cooldown AFTER arrival. Targets docked ordinary ships/active escorts, not travelling industry or buildings. Patrol spends TWO packs upfront, lasts up to 6h, reduces success by 30 points for up to TWO responses; no escort stacking.",
        "Matching escorts reduce broadside success; if it hits no hull against an escort, 30% attacker-damage risk. Heavy Earth launch: X-wing/A-wing/B-wing. Idle hull/active patrol can be a suitable local strike target. Repair costs 150 quadrillion and takes 6h.",
    ),
}


def ship_sections():
    sections = []
    for key, spec in fleet.CRAFT.items():
        purpose, recipe, limits, counters = SHIP_TUTORIALS[key]
        pack_note = (
            "Service packs are for Fleet Survey, not the free field expedition. "
            if key == "cutlass"
            else "Service packs are for Supply; ISD loading uses its own fees instead. "
            if key == "transport"
            else ""
        )
        text = f"**What it does:** {purpose}\n\n**Get it ready:** `/space build` → craft: {spec.name} ({ui.format_donuts(spec.cost)} / {spec.build_hours}h). `/space fleet payload` buys {spec.payload_name}: {ui.format_donuts(spec.payload_cost)} each, capacity {spec.capacity}. {pack_note}Finish any preparation, then `/space fleet launch` and wait {fleet.LAUNCH_MINUTES} minutes.\n\n**How to use it:**\n{recipe}\n\n**Limits/results:** {limits}\n\n**What interacts with it:** {counters}\nOrdinary hull repair: {ui.format_donuts(spec.cost // 4)} / {spec.repair_hours}h. Prepare lost payloads again; a launch-damaged hull must relaunch."
        sections.append((spec.name, text))
    return sections


def troubleshooting_sections():
    return [
        (
            "Why will my command not work?",
            "**Grounded / launch required:** construction is not deployment. Use `/space fleet launch`, then wait for the public window to finish. Finish payload preparation first. An intercepted launch needs repair and a new launch.\n\n**No recon / no objectives:** use Carrack Recon, wait for arrival, then attack the SAME player at the SAME body within 4h. Y-wing needs a live mine/lab/depot or idle docked hull; Arquitens needs docked hulls/active escorts. A ship that departed, became damaged or is now allied is not a valid target.\n\n**No interception window:** `/space fleet launches` lists ordinary hull launches. `/space intercept` instead needs an ongoing GR-75 ISD load. ISD, Death Star and THOR have their OWN window commands and counters.\n\n**Busy / damaged / no payload:** `/space fleet status` shows the real assignment, repair and ammunition timers. One hull cannot do two jobs; repair, wait or prepare the correct payload.\n\n**Not enough materials:** field samples or materials on another body are not local colony stock. Use stow/offload or GR-75 Supply; check `/space colony`.\n\n**Cannot refit to attack:** finish the shared voyage/expedition/loading and unload EVERY cargo category, including materials. Use `/space unload` for ordinary assets and `/space offload` for materials.",
        )
    ]


def number_pages(pages: List[discord.Embed], label: str) -> List[discord.Embed]:
    for index, page in enumerate(pages, 1):
        heading = (page.title or "Guide").split(" — ", 1)[-1]
        page.title = f"{label} {index}/{len(pages)} — {heading}"
        page.set_footer(text="Topic dropdown or page buttons · only the requester can navigate")
    return pages


class GuideTopicSelect(discord.ui.Select):
    def __init__(self, pages: List[discord.Embed], offset: int, count: int):
        options = [
            discord.SelectOption(
                label=f"{index + 1}. {(pages[index].title or 'Guide').split(' — ', 1)[-1]}"[:100],
                value=str(index),
            )
            for index in range(offset, offset + count)
        ]
        super().__init__(
            placeholder=f"Jump to a topic (pages {offset + 1}–{offset + count})",
            options=options,
            row=1 + offset // 25,
        )

    async def callback(self, interaction: discord.Interaction):
        pager = self.view
        index = int(self.values[0])
        if not 0 <= index < len(pager.pages):
            await interaction.response.send_message(
                "That guide page is unavailable. Run the guide again.", ephemeral=True
            )
            return
        pager.index = index
        pager._sync()
        await interaction.response.edit_message(embed=pager.pages[index], view=pager)


class SpaceGuidePager(ui.Paginator):
    """Local guide navigation; does not change other bot pagers or their privacy."""

    def __init__(self, pages: List[discord.Embed], author_id: int, *, initial_index: int = 0):
        if len(pages) > 100:
            raise ValueError("Space guide has too many topics for this pager.")
        super().__init__(pages, author_id, timeout=600, initial_index=initial_index)
        for child in self.children:
            child.row = 0
        for offset in range(0, len(self.pages), 25):
            self.add_item(GuideTopicSelect(self.pages, offset, min(25, len(self.pages) - offset)))
