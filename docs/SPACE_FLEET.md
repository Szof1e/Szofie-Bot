# Ordinary space fleet

This expansion adds activities below the ISD/Death Star tier, using the existing solar-system catalogue, materials, colonies, journal, shared economy and coalition membership. Prices below are in quadrillion donuts. Purchases use wallet first, then the normal vault, not deepvault.

| New ship | Hull price | Construction | Role | Payload price | Capacity |
| --- | ---: | ---: | --- | ---: | ---: |
| MISC Prospector | 75 | 8h | Asteroid mining | 1 | 3 |
| Drake Vulture | 65 | 8h | Finite natural/battle salvage | 1 | 3 |
| Anvil Carrack | 200 | 12h | Surveys and local fleet recon | 2 | 3 |
| RZ-1 A-wing | 80 | 8h | Convoy raids and escort | 3 | 4 |
| BTL-A4 Y-wing | 150 | 10h | One recon-guided local objective | 5 | 3 |
| Sphyrna Hammerhead Corvette | 500 | 18h | Strongest ordinary escort/patrol | 8 | 3 |
| Imperial Interdictor | 750 | 24h | One-hour convoy delay | 10 | 2 |
| Imperial Arquitens-class Command Cruiser | 600 | 20h | Two-hull broadside / two-response patrol | 10 | 4 |

Existing Cutlass, GR-75 and X-wing ownership is reused, not copied into a second inventory. The X-wing uses its original torpedo stock and retains the original ISD loading interception command. B-wing ISD counterplay is unchanged; ready packages can also intercept heavy ordinary Earth launches.

## Start

1. `/space fleet guide` is a public topic-dropdown and button guide. Use its opening pages to choose a goal and see what attacks/protects what. `/space fleet guide craft:<ship>` opens that ship's worked example directly. The full `/exploration` and `/space guide` include the same ship tutorials, plus the ISD/colony walkthrough. Only the requester can navigate; controls last ten minutes.
2. `/space build craft:<ship>` buys an individual hull. Different ships can build concurrently.
3. `/space fleet payload craft:<ship> qty:<number>` prepares service packs/ammunition. Preparation takes 30 minutes; existing X-wing torpedo purchases remain immediate.
4. `/space fleet launch craft:<ship>` launches the completed, idle hull from Earth. It remains unusable for a 30-minute public interception window. Launching has no additional fee. Existing deployed hulls/voyages are preserved, but unused ground stock must launch.
5. `/space survey body:<name>` surveys the destination. Launched industrial craft can open this route without an ISD.
6. `/space fleet mission` dispatches mining, salvage, surveying, recon, supply, precision bombing or Arquitens broadsides. Industrial durations follow the existing travel zones; Prospector mining adds two hours. Y-wing bombing and Arquitens broadsides take one hour.
7. `/space fleet status` privately shows all hulls, launch/repair/mission timers, recent results and recon. Select a craft for its current-stage artwork. `/arsenal` and private coalition intel include the fleet.

`/space torpedo` was folded into `/space fleet payload` to respect Discord's 25-child limit for `/space`. Existing `/space intercept` remains available.

## Earth launch counterplay

`/space fleet launches` lists open windows publicly without showing other holdings. `/space fleet intercept target:<player> craft:<launching ship> counter:<interceptor>` commits an attempt.

| Counter | Ordinary light/medium hull | Hammerhead / Interdictor / Arquitens |
| --- | ---: | ---: |
| Launched X-wing + proton torpedo | 35% | 20% |
| Launched A-wing + concussion missile | 45% | 30% |
| Ready B-wing Ion Assault Package | Not suitable | 40% |

One attempt per player, three different players maximum; self/allied launches cannot be intercepted. Payloads/packages are spent on hits and misses; fighter sorties have a 30-minute turnaround. Hits abort launch, lose onboard payloads and leave a damaged ground hull, not a permanently lost purchase. Pay the normal 25%-cost depot repair, prepare payloads and relaunch. Grounded/launching craft cannot survey, travel, mine, escort, raid or load an ISD. A Cutlass returned to Earth must relaunch before its next trip.

Dedicated superweapon counterplay is retained: THOR modules use GBI/EKV windows, ISD components use B-wing windows, and Death Star sections use dedicated Alliance squadron windows. Counter packages deploy during their existing interception action; they do not create recursive interception windows. Ordinary launch counters do not replace those dedicated project systems.

## Industry and intelligence

- Prospector returns 12 + twice the asteroid resource score in materials, to the local colony store or field store. It cannot mine planets or replace colony GDP.
- Vulture consumes one finite wreck claim. Natural debris has one shared claim per body per UTC day: eight metal units and two quadrillion donuts paid to wallet. Battle salvage excludes the loser, attacker and their coalitions and is worth less than repair costs.
- Carrack surveys yield more materials and a 30% unusual-specimen chance; Cutlass fleet surveys retain a 10% chance. Both advance the existing one-time 3/10/25-world journal rewards.
- Carrack recon reveals the target's ordinary ships, payloads and completed local structures at one body for four hours from mission completion. It does not expose unrelated Earth assets or refresh when checked late.
- GR-75 convoys move 1–100 existing materials between two completed own/allied bases. `half` and `all` are supported within that limit. Departing cargo is escrowed immediately; it cannot also remain in the source store. If the alliance expires or the destination becomes unavailable, surviving cargo returns to the sender.

## Combat and repairs

- X-wing/A-wing raid active mining, salvage or supply missions. Base hit chance is 65%. A hit destroys 25% of remaining exposed cargo/reward; it does not transfer that wealth to the attacker.
- Interdictor base success is 70%. A successful attempt adds one fixed hour to an ordinary industrial mission. It cannot repeatedly delay the same convoy or alter superweapon interception windows.
- At most two hostile attempts per industrial mission, and one attempt per attacker. Interdictor attempts use that same limit.
- X-wing, A-wing and Hammerhead escorts last six hours, protecting one player at one body. Only the strongest responds once: 15, 25 or 35 percentage points off hostile success respectively. Self/allied assignments only, no coalition stacking.
- Y-wing requires fresh Carrack recon and one live objective: mine, lab, ordinary ammo depot or an idle docked ordinary ship. Base success is 80%, reduced by the responding escort. Mines/labs/depots are disrupted for four hours; depot stocks are destroyed. Hull hits require repair rather than repurchase.
- An escorted failed raid/strike has a 25% chance to damage the attacker. Damaged ships lose payloads and cannot deploy.
- `/space fleet repair` costs 25% of hull price. Repairs take three hours, except Carrack four, Hammerhead six and Interdictor eight.
- `/space fleet recall` recalls only escort duty, with no service-pack refund or combat-turnaround bypass.
- Successful raids and precision strikes enter the victim's existing manually checked attack log. No automatic pings.

## Local depots and existing wipes

`/space fleet depot` builds an ordinary ammo depot at a completed base for 50 quadrillion/6h. Stock up to six packs per craft type at normal payload prices; load an idle owned craft docked there. No THOR or superweapon ammunition is accepted.

Fleet weapons cannot damage Earth balances, THOR, ISD or Death Star assets. THOR destroys every Earth-ground hull/build, including ordinary spacecraft still waiting to launch, plus their loaded/pending payloads and repairs. A hull with an active launch window, deployed in Earth orbit, or located off-world survives THOR with its payloads, repair timers and missions intact. An intercepted/aborted launch returns to Earth-ground risk. Conventional vehicles already stowed aboard an orbital carrier also survive; vehicle cargo still in Earth loading escrow does not. Non-vehicle cargo retains its existing THOR exposure. At most one fabricating/ready ISD component faces a fixed 10% destruction chance per strike; launching/orbiting/assembled ISD parts remain safe. The Death Star Heavy Transport is grounded until its first section deployment, then remains deployed; ready/building Alliance squadron packages are ground stock. Other server/planet wipes retain their broader rules, and a Death Star-destroyed destination aborts outstanding missions without paying their rewards.

All deadlines persist in UTC and complete once after downtime. Materials, escrow and ammunition are shared with their existing systems. Public guidance is paginated and status/recon is requester-private.

Artwork: [gallery](../assets/space/fleet/gallery.html), [source credits](../assets/space/fleet/README.md), and [saved prompts](../assets/space/fleet/prompts.json).

## Reading the guide

Each ordinary spacecraft has a dedicated page with purpose, build/payload/launch preparation, commands and required fields, valid targets, results, limits, counters and repair costs. The interaction map separates Earth hull launches, GR-75 loading, industrial missions, local bombing/broadsides, escorts and dedicated superweapon windows. The glossary distinguishes destination survey from player recon, shared explorer travel from independent fleet missions, and field/local/ISD materials.

Carrack space recon, not U-2/Deimos/SR-71 Earth intel, unlocks local Y-wing/Arquitens objectives. Ordinary X-wing/Y-wing hulls are not Death Star squadron packages, B-wing packages are their own ISD counter inventory, and GBI/EKV ascent interception is separate from Aegis SM-3 rod defense. The GR-75 is not the Death Star Heavy Transport. A troubleshooting page explains missing recon/objectives, wrong window commands, busy/grounded hulls, local-material shortages and refit blockers.

The specialized `/isd guide` and `/deathstar guide` also include worked build/defense sequences and topic dropdowns. Guides explain the same rules used for gameplay.
