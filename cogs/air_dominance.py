"""Fifth-generation command helpers hosted by the existing /vehicle group."""

import datetime as dt
import random
import secrets
import discord
from szofie import air_dominance as air, coalitions, nyx, ui
from szofie.catalog import VEHICLE_CATALOG, CONVENTIONAL_AMMO_TARGETS
from szofie.combat import reset_generation
from szofie.thor import parse_time, utcnow


def available(cog, user, model, cfg, *, second=False):
    state = cog._vehicle_settle(user, model)
    air.settle(state)
    if not air.usable(state):
        return "Finish construction or repair this jet first."
    if parse_time(state.get("loading_until")):
        return "Finish the current mission-package loading cycle first."
    if int(state.get("ammo", 0)) < 1:
        return "Load a mission package with `/vehicle load` first."
    if air.patrol(user, state.get("patrol_target")):
        return "This Raptor is already on patrol."
    last = parse_time(state.get("last_deploy_at"))
    cd = cog._vehicle_cooldown_seconds(cfg, model)
    if not second and last and ((utcnow() - last).total_seconds() < cd):
        return f"This jet is in turnaround until <t:{int(last.timestamp() + cd)}:R>."
    from cogs.economy import _strategic_lockdown_until, _electronic_blackout_until

    if _strategic_lockdown_until(user):
        return "Your runway is under a B-52 lockdown."
    if model in {"f22", "f35"} and _electronic_blackout_until(user):
        return "Your air-dominance electronics are suppressed by CHAMP."
    return None


def patrol_candidate(cog, gid, target_id, incoming):
    chance = air.patrol_chance(incoming)
    if not chance:
        return None
    from cogs.economy import _electronic_blackout_until

    doc = cog.econ.store.load(gid)
    victim = doc.get("users", {}).get(str(target_id), {})
    if _electronic_blackout_until(victim):
        return None
    for uid, user in doc.get("users", {}).items():
        if str(uid) != str(target_id) and (not coalitions.are_allied(doc, int(uid), target_id)):
            continue
        state = air.patrol(user, target_id)
        if state and (not _electronic_blackout_until(user)):
            return (state, "patrol_until", "F-22A combat air patrol", chance, int(uid))
    return None


def strongest_patrol(cog, gid, target_id, model, ground_chance):
    choice = patrol_candidate(cog, gid, target_id, model)
    return choice if choice and choice[3] > ground_chance else None


def engage(choice, cfg, attacker_id, model=None):
    """One chosen defence, one spent resource, one private roll."""
    if not choice:
        return False
    state, key, _, public_chance, defender_id = choice
    if key == "patrol_until":
        state.update(patrol_until=None, patrol_target=None)
    else:
        state[key] = max(0, int(state.get(key, 0)) - 1)
    resolution_chance = public_chance
    resolution_chance = resolution_chance
    if key == "s400_interceptors" and model in {"b2", "b52"}:
        from cogs.economy import _b2_intercept_chance, _b52_intercept_chance

        resolver = _b2_intercept_chance if model == "b2" else _b52_intercept_chance
        resolution_chance = resolver(cfg, attacker_id, defender_id)
    return random.randint(1, 100) <= resolution_chance


def conventional_choice(cog, gid, target_id, victim, cfg, model, attacker):
    """Compare ordinary chances before spending anything; preserve legacy rolls
    when no patrol exists. Missile platforms never become aircraft by accident.
    """
    selected_patrol = patrol_candidate(cog, gid, target_id, model)
    if selected_patrol is None:
        return None
    from cogs.economy import _public_light_aircraft_intercept_chance, _public_s400_intercept_chance

    choices = [selected_patrol]

    def add(state, field, name, chance, owned=True):
        if owned and int(state.get(field, 0)) > 0:
            choices.append((state, field, name, max(0, min(100, int(chance))), target_id))

    if model in {"mq9", "apache", "a10"}:
        base = int(
            cfg.get(
                "economy.vehicle_" + model + "_aa_intercept_chance",
                {"mq9": 20, "apache": 30, "a10": 50}[model],
            )
        )
        if model == "apache" and cog._vehicle_settle(attacker, "apache").get("comanche_upgraded"):
            base = int(cfg.get("economy.vehicle_comanche_intercept_chance_max", 25))
        add(
            victim,
            "aa_rockets_loaded",
            "Radar AA interceptor",
            _public_light_aircraft_intercept_chance(cfg, victim, base),
        )
    if model in {"b52", "su34", "xb70"}:
        cog._s400_settle(victim)
        chance = (
            _public_s400_intercept_chance(cfg, "b52")
            if model == "b52"
            else cfg.get("economy.vehicle_su34_s400_intercept_chance", 25)
            if model == "su34"
            else cfg.get("economy.vehicle_xb70_s400_intercept_chance", 10)
        )
        add(victim, "s400_interceptors", "S-400 40N6E", chance, victim.get("s400_owned"))
    if model in {"f15e", "c130j", "a10", "su34"}:
        state = cog._vehicle_settle(victim, "patriot")
        chance = (
            cfg.get("economy.vehicle_su34_patriot_intercept_chance", 20)
            if model == "su34"
            else cfg.get("economy.vehicle_patriot_intercept_chance", 40)
        )
        add(state, "ammo", "Patriot PAC-3 MSE", chance, state.get("owned"))
    return max(choices, key=lambda row: row[3])


def jet_defence(cog, gid, target_id, victim, cfg, model, *, second=False):
    from cogs.economy import _electronic_blackout_until

    if _electronic_blackout_until(victim):
        return None
    choices = []
    public_chance = 35 if second else 20
    cog._s400_settle(victim)
    if victim.get("s400_owned") and int(victim.get("s400_interceptors", 0)) > 0:
        choices.append((victim, "s400_interceptors", "S-400 40N6E", public_chance, target_id))
    patriot = cog._vehicle_settle(victim, "patriot")
    if patriot.get("owned") and int(patriot.get("ammo", 0)) > 0:
        choices.append((patriot, "ammo", "Patriot PAC-3 MSE", public_chance, target_id))
    patrol = patrol_candidate(cog, gid, target_id, model)
    if patrol:
        choices.append(patrol)
    return max(choices, key=lambda row: row[3]) if choices else None


def objective_error(cog, cfg, pilot, victim, target_id, model, objective):
    if nyx.active(victim) and (model in {"j20", "f35"} or (objective or "").startswith("vehicle:")):
        return "NYX Ghost Protocol blocks the target solution."
    recon = cog._active_recon(pilot, target_id, victim)
    if model == "f35":
        return None if recon else "Scan this target with U-2, Deimos or SR-71 first."
    if model == "j20":
        if not recon:
            return "A J-20 airframe hunt requires current U-2, Deimos or SR-71 recon."
        if objective not in air.AIRCRAFT:
            return "Choose one completed Earth aircraft; no builds or space assets."
        return (
            None
            if cog._vehicle_settle(victim, objective).get("owned")
            else "The selected aircraft is no longer present. Scan again."
        )
    kind, _, key = (objective or "").partition(":")
    if objective == "wallet":
        return None if int(victim.get("donuts", 0)) else "That wallet has no donuts to strike."
    if kind == "vehicle":
        if not recon:
            return "A completed-vehicle strike requires current recon."
        if key not in VEHICLE_CATALOG or key == "aegis":
            return "Choose a completed Earth vehicle, not THOR or orbital hardware."
        return (
            None
            if cog._vehicle_settle(victim, key).get("owned")
            else "The selected vehicle is no longer present."
        )
    stores = {k: n for k, _, _, n in cog._conventional_ammo_stores(cfg, victim)}
    if kind == "ammo" and key in CONVENTIONAL_AMMO_TARGETS:
        return None if stores.get(key, 0) else "That conventional ammunition pool is empty."
    if kind == "aa" and key in {"regular-aa", "s400", "aegis", "p8", "patriot", "javelin"}:
        return None if stores.get(key, 0) else "That conventional defence pool is empty."
    return "Use `vehicle:model`, `ammo:pool`, `aa:pool`, or `wallet`. THOR assets are excluded."


async def result(cog, interaction, cfg, model, stage, text, *, view=None):
    embed = ui.base_embed(
        title=f"✈️ {air.JETS[model][1]} — {stage.title()}", description=text, color=cfg.color
    )
    art = cog._strategic_art(cfg, embed, model + "-" + stage)
    kwargs = dict(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    if art:
        kwargs["file"] = art
    if view:
        kwargs["view"] = view
    await ui.respond(interaction, ephemeral=stage not in {"mission", "damaged"}, **kwargs)


async def patrol(cog, interaction, target=None):
    cfg = await cog._guard(interaction, ephemeral=False)
    if cfg is None:
        return
    target = target or interaction.user
    gid = interaction.guild_id
    doc = cog.econ.store.load(gid)
    user = cog.user(gid, interaction.user.id)
    error = available(cog, user, "f22", cfg)
    if target.bot or (
        target.id != interaction.user.id and (not coalitions.are_allied(doc, interaction.user.id, target.id))
    ):
        error = "Patrol protects you or one current coalition member only."
    if patrol_candidate(cog, gid, target.id, "f22"):
        error = "A combat air patrol already protects this player; patrols cannot stack."
    if error:
        await ui.respond(interaction, embed=ui.error_embed(error), ephemeral=True)
        return
    state = cog._vehicle_settle(user, "f22")
    now = utcnow()
    state.update(
        ammo=int(state["ammo"]) - 1,
        last_deploy_at=now.isoformat(),
        patrol_until=(now + dt.timedelta(hours=2)).isoformat(),
        patrol_target=str(target.id),
    )
    await cog.persist(gid)
    await cog.bot.ledger.record(gid, interaction.user.id, 0, "vehicle-f22-patrol", after=cog._net(user))
    await result(
        cog,
        interaction,
        cfg,
        "f22",
        "mission",
        f"Protecting {target.display_name} until <t:{int((now + dt.timedelta(hours=2)).timestamp())}:R>. One package spent. One engagement ends the patrol, hit or miss. 65% conventional aircraft / 35% fifth-gen / 20% B-2. Strongest eligible defence engages, never stacks.",
    )


async def repair(cog, interaction, model, cfg):
    user = cog.user(interaction.guild_id, interaction.user.id)
    state = cog._vehicle_settle(user, model)
    if not state.get("owned") or not state.get("jet_damaged"):
        await ui.respond(
            interaction, embed=ui.error_embed("This jet does not require depot recovery."), ephemeral=True
        )
        return
    if state.get("jet_repair_until"):
        await ui.respond(interaction, embed=ui.warn_embed("A repair is already running."), ephemeral=True)
        return
    spec = VEHICLE_CATALOG[model]
    cost = int(cfg.get("economy." + spec["cost"], spec["fallback_cost"])) // 4
    if cog._net(user) < cost:
        await ui.respond(
            interaction, embed=ui.error_embed(f"Repair costs {cog.money(cfg, cost)}."), ephemeral=True
        )
        return
    cog._take(user, cost)
    state["jet_repair_until"] = air.deadline(2)
    await cog.persist(interaction.guild_id)
    await cog._log(interaction.guild_id, interaction.user.id, -cost, "vehicle-" + model + "-repair", user)
    await result(
        cog,
        interaction,
        cfg,
        model,
        "repair",
        f"Paid {cog.money(cfg, cost)}. Depot recovery completes <t:{int(parse_time(state['jet_repair_until']).timestamp())}:R>. Unspent packages remain aboard; the spent sortie package is not refunded.",
    )


class SecondPassModal(discord.ui.Modal, title="Su-57 second pass"):
    objective = discord.ui.TextInput(
        label="Different objective",
        placeholder="vehicle:apache / ammo:f15e / aa:s400 / wallet",
        max_length=50,
    )

    def __init__(self, view):
        super().__init__()
        self.parent = view

    async def on_submit(self, interaction):
        if interaction.user.id != self.parent.uid:
            await ui.respond(interaction, embed=ui.error_embed("This is not your sortie."), ephemeral=True)
            return
        cfg = await self.parent.cog._guard(interaction, ephemeral=False)
        if cfg is None:
            return
        await execute(
            self.parent.cog,
            interaction,
            self.parent.target,
            "su57",
            str(self.objective).strip().lower(),
            token=self.parent.token,
        )


class SecondPass(discord.ui.View):
    def __init__(self, cog, uid, target, token):
        super().__init__(timeout=300)
        self.cog, self.uid, self.target, self.token = (cog, uid, target, token)

    @discord.ui.button(label="Make another pass", style=discord.ButtonStyle.danger)
    async def again(self, interaction, button):
        if interaction.user.id != self.uid:
            await ui.respond(interaction, embed=ui.error_embed("This is not your sortie."), ephemeral=True)
            return
        await interaction.response.send_modal(SecondPassModal(self))


async def execute(cog, interaction, target, model, objective=None, *, recipient=None, token=None):
    gid = interaction.guild_id
    cfg = cog.cfg(gid)
    pilot = cog.user(gid, interaction.user.id)
    victim = cog.user(gid, target.id)
    state = cog._vehicle_settle(pilot, model)
    objective = (objective or "").lower().strip()
    second = token is not None
    doc = cog.econ.store.load(gid)
    error = available(cog, pilot, model, cfg, second=second)
    if target.id == interaction.user.id or target.bot:
        error = "Choose another human player."
    error = error or cog._coalition_attack_error(gid, interaction.user.id, target.id)
    pending = state.get("second_pass") or {}
    if second and (
        pending.get("token") != token
        or pending.get("target") != str(target.id)
        or pending.get("generation") != reset_generation(pilot)
        or (not parse_time(pending.get("until")))
        or (parse_time(pending["until"]) <= utcnow())
    ):
        error = "This second-pass authorization has expired or was already used."
    if second and pending.get("objective") == objective:
        error = "The second pass must use a different objective."
    recipient = recipient or interaction.user
    if (
        model == "f35"
        and recipient.id != interaction.user.id
        and (not coalitions.are_allied(doc, interaction.user.id, recipient.id))
    ):
        error = "Share a link only with a current coalition member."
    error = error or objective_error(cog, cfg, pilot, victim, target.id, model, objective)
    if error:
        await ui.respond(interaction, embed=ui.error_embed(error), ephemeral=True)
        return
    state["ammo"] = int(state["ammo"]) - 1
    if not second:
        state["last_deploy_at"] = utcnow().isoformat()
    state["second_pass"] = None
    victim_before = cog._net(victim)
    defence = jet_defence(cog, gid, target.id, victim, cfg, model, second=second)
    shot_down = engage(defence, cfg, interaction.user.id)
    text = ""
    view = None
    stage = "mission"
    hit = False
    if shot_down:
        air.damage(state)
        stage = "damaged"
        text = f"{defence[2]} forced the jet down. Repair costs 25% of the build price and takes 2 hours. No target damage."
    elif model == "f35":
        holder = cog.user(gid, recipient.id)
        holder.setdefault("fusion_links", {})[str(target.id)] = {
            "until": air.deadline(0.5),
            "created": utcnow().isoformat(),
            "source": str(interaction.user.id),
            "generation": reset_generation(holder),
        }
        text = f"Sensor-Fusion Link assigned to {recipient.display_name} against {target.display_name} for 30 minutes. "
        text += "The next completed conventional vehicle destruction roll gets +15 points (95% cap). One use; NYX invalidates it."
    else:
        if model == "j20" or objective.startswith("vehicle:"):
            key = objective if model == "j20" else objective.partition(":")[2]
            public_chance = air.vehicle_chance(pilot, target.id, victim, 80 if model == "j20" else 65)
            hit = random.randint(1, 100) <= public_chance
            if hit:
                destroyed = cog._vehicle_settle(victim, key)
                cog._destroy_vehicle(destroyed)
                if key in air.JETS:
                    destroyed.update(
                        jet_damaged=False, jet_repair_until=None, patrol_until=None, second_pass=None
                    )
                if key in {"u2", "deimos", "sr71"}:
                    air.invalidate_source(victim, key)
            text = f"{cog._vehicle_label(key)} " + ("destroyed." if hit else "survived the precision strike.")
        elif objective == "wallet":
            amount = int(victim.get("donuts", 0)) * 20 // 100
            victim["donuts"] = int(victim.get("donuts", 0)) - amount
            hit = amount > 0
            text = f"Wallet damage: {ui.format_donuts(amount)} donuts (20%). Other reserves untouched."
        else:
            key = objective.partition(":")[2]
            pool = next((row for row in cog._conventional_ammo_stores(cfg, victim) if row[0] == key))
            _, owner, field, count = pool
            lost = min(count, (count * 60 + 99) // 100)
            owner[field] = count - lost
            hit = lost > 0
            text = f"{lost}/{count} ready {CONVENTIONAL_AMMO_TARGETS[key]} destroyed (60%, rounded up)."
        if model == "su57" and hit and (not second):
            nonce = secrets.token_hex(12)
            state["second_pass"] = dict(
                token=nonce,
                target=str(target.id),
                objective=objective,
                generation=reset_generation(pilot),
                until=air.deadline(1 / 12),
            )
            view = SecondPass(cog, interaction.user.id, target, nonce)
            text += "\nOptional second pass: different objective, another package, 35% SAM risk. Authorization expires in 5 minutes."
    await cog.persist(gid)
    await cog.bot.ledger.record(
        gid, interaction.user.id, 0, "vehicle-" + model + "-launch", after=cog._net(pilot), other=target.id
    )
    if hit:
        await cog.bot.ledger.record(
            gid,
            target.id,
            cog._net(victim) - victim_before,
            "vehicle-" + model + "-hit",
            after=cog._net(victim),
            other=interaction.user.id,
            actor=interaction.user.id,
            detail=objective,
        )
    await result(cog, interaction, cfg, model, stage, text, view=view)


async def objectives(cog, interaction, current):
    model = str(getattr(getattr(interaction, "namespace", None), "vehicle", ""))
    target = getattr(getattr(interaction, "namespace", None), "target", None)
    target_id = getattr(target, "id", None)
    rows = []
    if target_id and interaction.guild_id:
        pilot = cog.user(interaction.guild_id, interaction.user.id)
        victim = cog.user(interaction.guild_id, target_id)
        if not nyx.active(victim) and cog._active_recon(pilot, target_id, victim):
            allowed = air.AIRCRAFT if model == "j20" else set(VEHICLE_CATALOG) - {"aegis"}
            for key in allowed:
                if cog._vehicle_settle(victim, key).get("owned"):
                    rows.append(
                        ("Recon — " + cog._vehicle_label(key), key if model == "j20" else "vehicle:" + key)
                    )
    if model == "su57":
        rows += [("Ammunition — " + label, "ammo:" + key) for key, label in CONVENTIONAL_AMMO_TARGETS.items()]
        rows += [
            ("Defence — " + key, "aa:" + key)
            for key in ("regular-aa", "s400", "aegis", "p8", "patriot", "javelin")
        ]
        rows += [("Wallet — 20%", "wallet")]
    needle = current.lower().strip()
    return [
        discord.app_commands.Choice(name=name[:100], value=value)
        for name, value in rows
        if not needle or needle in name.lower() or needle in value
    ][:25]
