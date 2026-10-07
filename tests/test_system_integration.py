"""Cross-system regression checks for Szofie's shared economy.

These tests deliberately use temporary storage and pure helpers. They never touch
the live guild files or Discord network, so they are safe to run before every
restart.
"""

from __future__ import annotations
import asyncio
import copy
import datetime as dt
import json
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import discord
from discord import app_commands
from bot import COGS, Szofie
from cogs.coalitions import COALITION_MEMBER_HARD_CAP, CoalitionCog, coalition_member_limit
from cogs.countries import (
    OFFENSE,
    FORTIFICATION_MAX_USEFUL,
    Countries,
    invasion_cooldown_minutes,
    public_invasion_chance,
    useful_war_chest_cap,
)
from cogs.economy import (
    ATTACKLOG_METHODS,
    CAREER_LICENCES,
    B52_REPAIR_COST_PCTS,
    B52_REPAIR_HOURS,
    CONSUMABLES,
    DEIMOS_FAILURE_LINES,
    DEIMOS_SUCCESS_LINES,
    ITEM_HOLD_CAPS,
    LOADABLE_VEHICLES,
    PERMANENT_UTILITIES,
    CasinoReplayView,
    RouletteRepeatView,
    VEHICLE_CATALOG,
    WHEEL,
    EconomyCog,
    _aa_shield_vehicle_bonus,
    _b2_intercept_chance,
    _b52_intercept_chance,
    _b52_repair_terms,
    _casino_display_snapshot,
    _comanche_intercept_range,
    _comanche_rounds_per_mission,
    _comanche_repair_quote,
    _deimos_success_chance,
    _icbm_target_eligible,
    _hex_adjusted_slots_pct,
    _mq9_items_destroyed,
    _public_light_aircraft_intercept_chance,
    _public_s400_intercept_chance,
    _public_strategic_chance,
    _s400_intercept_chance,
    _strategic_objective_autocomplete,
    _strategic_lockdown_until,
    _u2_intercept_chance,
    _virginia_rod_break_chance,
    parse_roulette_space,
    roulette_profit_percent,
    wheel_non_jackpot_rebate,
    spin_wheel,
    biased_spin,
)
from cogs.blackjack import Blackjack, BlackjackReplayView, Card, settle as settle_blackjack
from cogs.fishing import (
    ABYSS_CODEX_IDS,
    ABYSS_ESOTERIC_IDS,
    ABYSS_ESOTERIC_LORE,
    ABYSS_FISH,
    ABYSS_MYTHIC_WEIGHT_MULTIPLIER,
    FISH,
    FISH_BY_ID,
    FISH_SELL_PRICE_BPS,
    RARE_PLUS_WEIGHT_MULTIPLIER,
    ROD_BY_ID,
    VALUABLE_TIER_WEIGHT_MULTIPLIERS,
    Fishing,
    abyss_catch_weights,
    base_sell_value,
    cast,
    catch_weights,
    sell_value,
)
from cogs.general import General
from cogs.continuity import ContinuityCog
from cogs.thor import ThorCog
from cogs.imperial_star_destroyer import ImperialStarDestroyerCog
from cogs.space import SpaceCog
from szofie import (
    coalitions,
    continuity as continuity_state,
    countries,
    events,
    hexes,
    imperial_star_destroyer as isd_state,
    space as space_state,
    thor as thor_state,
)
from szofie.betting import AmountParseError, SpendTransformer, parse_amount
from szofie.config import DEFAULTS, ConfigManager
from szofie.economy import CASINO_GAMES, CASINO_FEATURED_GAMES, Economy, record_casino_result
from szofie.ledger import Ledger, SECURITY_EVENT_REASONS, incoming_security_actor
from szofie.plushies import PLUSHIES, PLUSHIE_BY_ID, RETIRED_PLUSHIE_IDS, plushie_perk
from szofie.storage import Storage
from szofie.titles import TITLE_BY_ID


def setUpModule() -> None:
    global _game_lock_temp, _game_lock_patch, _test_media_files, _media_file_patch
    _game_lock_temp = tempfile.TemporaryDirectory()
    _game_lock_patch = patch("szofie.gamelocks.LOCK_FILE", Path(_game_lock_temp.name) / "game_locks.json")
    _game_lock_patch.start()
    # Mocked Discord transports do not perform discord.py's upload cleanup.
    # Retain and close attachments so the art-enabled suite does not leak handles.
    _test_media_files = []
    original_file_init = discord.File.__init__

    def track_file(instance, *args, **kwargs):
        original_file_init(instance, *args, **kwargs)
        _test_media_files.append(instance)

    _media_file_patch = patch.object(discord.File, "__init__", track_file)
    _media_file_patch.start()


def tearDownModule() -> None:
    _media_file_patch.stop()
    for media in _test_media_files:
        media.close()
    _test_media_files.clear()
    _game_lock_patch.stop()
    _game_lock_temp.cleanup()


class StorageAndSchemaTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_user_and_schema_backfill_persist_after_read_only_access(self):
        with tempfile.TemporaryDirectory() as raw:
            econ = Economy()
            econ.store = Storage(Path(raw))
            user = econ.user(1, 123, 100)
            await econ.flush()
            self.assertEqual(Storage(Path(raw)).load(1)["users"]["123"]["donuts"], 100)
            user.pop("android21_helper")
            econ.store.mark_dirty(1)
            await econ.store.flush()
            econ.user(1, 123, 100)
            await econ.flush()
            self.assertIn("android21_helper", Storage(Path(raw)).load(1)["users"]["123"])

    async def test_retired_freezers_are_removed_from_every_live_storage_without_other_losses(self) -> None:
        from szofie.retired_items import remove_freezer

        with tempfile.TemporaryDirectory() as raw:
            economy = Economy()
            economy.store = Storage(Path(raw))
            user = economy.user(1, 123, 100)
            user.update(
                inventory={"freezer": 50, "uno": 2},
                fish={"carp": 3},
                fish_meta={"carp": {"age": 7.0, "w1": False, "w2": False}},
                bucket_freeze_until="2099-01-01T00:00:00+00:00",
            )
            user["continuity"].update(
                items={"freezer": 5, "lock": 2},
                transfer_payload={"kind": "item", "id": "freezer", "amount": 1},
                transfer_action="retrieve",
                transfer_until="2099-01-01T00:00:00+00:00",
            )
            user["space"] = {
                "cargo": {"inventory": {"freezer": 3, "drill": 1}},
                "load_manifest": {"inventory": {"freezer": 4, "copcall": 2}},
            }
            user["death_star"] = {"cargo": {"inventory": {"freezer": 6, "getaway": 1}}}
            expected = copy.deepcopy(user)
            expected.pop("bucket_freeze_until")
            expected["inventory"].pop("freezer")
            expected["continuity"]["items"].pop("freezer")
            expected["continuity"].update(transfer_payload=None, transfer_action=None, transfer_until=None)
            expected["space"]["cargo"]["inventory"].pop("freezer")
            expected["space"]["load_manifest"]["inventory"].pop("freezer")
            expected["death_star"]["cargo"]["inventory"].pop("freezer")
            self.assertEqual(economy.all_users(1)["123"], expected)
            self.assertFalse(remove_freezer(user))
            await economy.flush()
            self.assertEqual(Storage(Path(raw)).load(1)["users"]["123"], expected)

    async def test_retired_freezer_settings_do_not_return_from_old_configs(self) -> None:
        from szofie.retired_items import FREEZER_CONFIG_KEYS

        with tempfile.TemporaryDirectory() as raw:
            storage = Storage(Path(raw))
            storage.load(1)["economy"] = {key: 123 for key in FREEZER_CONFIG_KEYS}
            storage.load(1)["economy"]["fish_rot_hours"] = 72
            manager = ConfigManager(storage)
            cfg = manager.for_guild(1)
            for key in FREEZER_CONFIG_KEYS:
                self.assertNotIn(key, cfg.raw()["economy"])
                self.assertNotIn(key, DEFAULTS["economy"])
            self.assertEqual(cfg.get("economy.fish_rot_hours"), 72)
            await manager.flush()
            self.assertFalse(set(FREEZER_CONFIG_KEYS) & set(Storage(Path(raw)).load(1)["economy"]))

    async def test_flush_saves_other_guilds_even_when_first_dirty_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = Storage(Path(raw))
            for guild_id in (1, 2):
                store.load(guild_id)["value"] = guild_id
                store.mark_dirty(guild_id)
            failed, healthy = list(store._dirty)
            original_write = store._write

            def write(path, payload):
                if path.name == f"{failed}.json":
                    raise OSError("Simulated failed guild write")
                original_write(path, payload)

            with patch.object(store, "_write", side_effect=write):
                with self.assertRaises(OSError):
                    await store.flush()
            self.assertEqual(json.loads((Path(raw) / f"{healthy}.json").read_text()), {"value": healthy})
            self.assertEqual(store._dirty, {failed})
            await store.flush()
            self.assertFalse(store._dirty)
            self.assertEqual(json.loads((Path(raw) / f"{failed}.json").read_text()), {"value": failed})

    async def test_retired_snipe_and_moderation_settings_are_not_reintroduced(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            storage = Storage(Path(raw) / "config")
            stored = storage.load(1)
            stored["snipe"] = {"enabled": True, "history": 5}
            stored["moderation"] = {
                "confirm_nuke": True,
                "confirm_purge": False,
                "max_purge": 1000,
                "warn_dm": True,
            }
            cfg = ConfigManager(storage).for_guild(1)
            self.assertNotIn("snipe", cfg.raw())
            self.assertNotIn("confirm_purge", cfg.raw()["moderation"])
            self.assertNotIn("max_purge", cfg.raw()["moderation"])
            self.assertNotIn("warn_dm", cfg.raw()["moderation"])
            self.assertTrue(cfg.get("moderation.confirm_nuke"))

    async def test_shared_alias_is_atomic_and_consistent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = Storage(Path(raw), aliases={2: 1})
            self.assertIs(store.load(1), store.load(2))
            store.load(2)["value"] = 42
            store.mark_dirty(2)
            await store.flush()
            self.assertEqual(json.loads((Path(raw) / "1.json").read_text())["value"], 42)
            self.assertFalse((Path(raw) / "2.json").exists())


class SecurityLedgerTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_attacklog_method_is_filterable_and_permanently_retained(self) -> None:
        self.assertEqual(set(ATTACKLOG_METHODS), set(SECURITY_EVENT_REASONS))
        with tempfile.TemporaryDirectory() as raw:
            ledger = Ledger(Path(raw))
            old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)).isoformat()
            reasons = [
                "vehicle-a10-hit",
                "vehicle-su34-hit",
                "vehicle-c130j-hit",
                "vehicle-champ-hit",
                "vehicle-maldx-hit",
                "vehicle-lrhw-hit",
                "vehicle-xb70-hit",
            ]
            rows = [{"ts": old, "reason": reason, "user": 200, "other": 100} for reason in reasons]
            rows += [
                {"ts": old, "reason": reason, "user": 100, "other": 200}
                for reason in ("space-xwing-hit", "isd-planetary-impact", "isd-cinder-impact")
            ]
            rows.append({"ts": old, "reason": "vehicle-a10-lost", "user": 200, "other": 100})
            path = Path(raw) / "1.jsonl"
            path.write_text("\n".join((json.dumps(row) for row in rows)) + "\n[]\nnull\n")
            self.assertEqual(await ledger.prune(1, 7), 1)
            security = await ledger.read_security(1, victim=200)
            self.assertEqual(len(security), 10)
            self.assertEqual({row["security_actor"] for row in security}, {100})
            self.assertEqual(len(await ledger.read(1)), 10)

    async def test_only_successful_incoming_events_resolve_an_attacker(self) -> None:
        victim = 200
        self.assertEqual(
            incoming_security_actor({"reason": "steal-success", "user": victim, "other": 100}, victim), 100
        )
        self.assertIsNone(
            incoming_security_actor({"reason": "steal-success", "user": 100, "other": victim}, victim)
        )
        self.assertEqual(
            incoming_security_actor({"reason": "heist-success", "user": 101, "other": victim}, victim), 101
        )
        self.assertEqual(
            incoming_security_actor({"reason": "vehicle-u2-recon", "user": 102, "other": victim}, victim), 102
        )
        self.assertEqual(
            incoming_security_actor({"reason": "vehicle-sr71-recon", "user": 108, "other": victim}, victim),
            108,
        )
        self.assertEqual(
            incoming_security_actor({"reason": "vehicle-deimos-recon", "user": 103, "other": victim}, victim),
            103,
        )
        self.assertEqual(
            incoming_security_actor({"reason": "vehicle-deimos-recon", "user": 103, "other": victim}, 103),
            victim,
        )
        self.assertEqual(
            incoming_security_actor(
                {"reason": "vehicle-b52-hit", "user": victim, "other": 104, "actor": 104}, victim
            ),
            104,
        )
        self.assertEqual(
            incoming_security_actor(
                {"reason": "vehicle-apache-hit", "user": victim, "other": 105, "actor": 105}, victim
            ),
            105,
        )
        self.assertEqual(
            incoming_security_actor(
                {"reason": "vehicle-comanche-hit", "user": victim, "other": 107, "actor": 107}, victim
            ),
            107,
        )
        self.assertEqual(
            incoming_security_actor(
                {"reason": "vehicle-leopard2a7-hit", "user": victim, "other": 109, "actor": 109}, victim
            ),
            109,
        )
        self.assertEqual(
            incoming_security_actor(
                {"reason": "thor-hit", "user": victim, "other": 106, "actor": 106}, victim
            ),
            106,
        )
        self.assertIsNone(
            incoming_security_actor({"reason": "robbank-fail", "user": victim, "other": 100}, victim)
        )
        self.assertIsNone(
            incoming_security_actor({"reason": "icbm-launch", "user": 100, "other": victim}, victim)
        )

    async def test_security_reader_filters_failures_and_preserves_exact_actor(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            ledger = Ledger(Path(raw))
            await ledger.record(1, 200, -500, "steal-success", other=100)
            await ledger.record(1, 100, 500, "steal-success", other=200)
            await ledger.record(1, 101, 250, "heist-success", other=200)
            await ledger.record(1, 100, -20, "steal-caught", other=200)
            await ledger.record(1, 102, 0, "vehicle-u2-recon", other=200)
            await ledger.record(1, 103, 0, "vehicle-deimos-recon", other=200)
            await ledger.record(1, 108, 0, "vehicle-sr71-recon", other=200)
            rows = await ledger.read_security(1, victim=200, limit=20)
            self.assertEqual([row["security_actor"] for row in rows], [108, 103, 102, 101, 100])
            self.assertEqual(
                [row["reason"] for row in rows],
                [
                    "vehicle-sr71-recon",
                    "vehicle-deimos-recon",
                    "vehicle-u2-recon",
                    "heist-success",
                    "steal-success",
                ],
            )
            reciprocal = await ledger.read_security(1, victim=103, limit=20)
            self.assertEqual(len(reciprocal), 1)
            self.assertEqual(reciprocal[0]["security_actor"], 200)


class AmountParsingTests(unittest.TestCase):
    def test_suffixes_are_exact_through_quadrillions(self) -> None:
        expected = {
            "25k": 25000,
            "2.5m": 2500000,
            "1b": 1000000000,
            "1.5t": 1500000000000,
            "1q": 1000000000000000,
            "9,876,543": 9876543,
            "0.000001m": 1,
        }
        for raw, amount in expected.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_amount(raw), amount)

    def test_half_full_and_all_use_the_supplied_balance(self) -> None:
        self.assertEqual(parse_amount("half", available=9000001), 4500000)
        self.assertEqual(parse_amount("all", available=9000001), 9000001)
        self.assertEqual(parse_amount("full", available=9000001), 9000001)
        self.assertEqual(parse_amount(None, available=9000001, default_all=True), 9000001)

    def test_invalid_and_fractional_donut_amounts_fail_closed(self) -> None:
        for raw in ("0", "-5", "nan", "1e9", "1.5", "all"):
            with self.subTest(raw=raw):
                with self.assertRaises(AmountParseError):
                    parse_amount(raw)
        self.assertEqual(parse_amount("-2.5m", allow_negative=True), -2500000)


class CatalogAndConfigurationTests(unittest.TestCase):
    def test_all_catalog_prices_and_counts_are_sane(self) -> None:
        self.assertTrue(
            all(
                (
                    p.price > 0
                    and p.value > 0
                    or (
                        (p.price == 0 or p.id in RETIRED_PLUSHIE_IDS)
                        and p.value == 0
                        and (p.perk == "cosmetic")
                    )
                    for p in PLUSHIES
                )
            )
        )
        self.assertTrue(all((c.price >= 100000000 and c.gdp_b > 0 for c in countries.COUNTRIES)))
        self.assertEqual(len(countries.COUNTRIES), 195)
        self.assertTrue(all((0 <= e.duration_min <= 60 and e.weight > 0 for e in events.CATALOG)))
        self.assertEqual(sum((weight for _, weight, _ in WHEEL)), 1923)
        self.assertAlmostEqual(WHEEL[-1][1] / 1923, 1 / 641)
        self.assertEqual(
            {result: weight for result, weight, _ in WHEEL},
            {0: 1100, 0.5: 400, 1: 50, 2: 250, 3: 120, "POT": 3},
        )
        self.assertLess(WHEEL[2][1] / 1923, 0.03)
        self.assertGreater(WHEEL[0][1] / 1923, 0.55)
        for model, spec in VEHICLE_CATALOG.items():
            self.assertGreater(int(spec["fallback_cost"]), 0, model)
            self.assertGreater(float(spec["fallback_build"]), 0, model)

    def test_configuration_contains_every_catalog_setting(self) -> None:
        economy = DEFAULTS["economy"]
        for model, spec in VEHICLE_CATALOG.items():
            self.assertIn(spec["cost"], economy, model)
            self.assertIn(spec["build"], economy, model)
            if model in LOADABLE_VEHICLES:
                self.assertIn(spec["ammo_cost"], economy, model)
                self.assertIn(spec["load"], economy, model)
                self.assertIn(spec["cap"], economy, model)
        self.assertEqual(economy["coalition_max_members"], 10)
        self.assertEqual(economy["daily_amount"], 5500000)
        self.assertEqual(economy["daily_cooldown_hours"], 12)
        self.assertEqual(economy["daily_streak_bonus_pct"], 5)
        self.assertEqual(economy["daily_streak_max"], 20)
        self.assertEqual(economy["work_salary_tier_1"], 275000)
        self.assertEqual(economy["work_salary_tier_2"], 1100000)
        self.assertEqual(economy["work_salary_tier_3"], 4400000)
        self.assertEqual(economy["work_salary_tier_4"], 13200000)
        self.assertEqual(economy["work_salary_tier_5"], 33000000)
        self.assertEqual(economy["android21_helper_price"], 100000000000)
        self.assertEqual(economy["android21_helper_pay_pct"], 70)
        self.assertEqual(set(PERMANENT_UTILITIES), {"android21helper"})
        self.assertEqual(economy["trivia_reward"], 1100000)
        self.assertEqual(economy["trivia_speed_bonus_max"], 550000)
        self.assertEqual(economy["trivia_daily_milestone"], 5500000)
        self.assertEqual(economy["trivia_cooldown_minutes"], 120)
        self.assertEqual(economy["deep_vault_withdraw_minutes"], 5)
        self.assertEqual(economy["vehicle_m1a2_cost"], 750000000)
        self.assertEqual(economy["vehicle_m1a2_ammo_cost"], 40000000)
        self.assertEqual(economy["vehicle_m1a2_ammo_cap"], 6)
        self.assertEqual(economy["vehicle_m1a2_fortification_damage_pct"], 25)
        self.assertEqual(economy["vehicle_m1a2_trophy_defeat_chance"], 35)
        self.assertEqual(economy["vehicle_leopard2a7_cost"], 750000000)
        self.assertEqual(economy["vehicle_leopard2a7_build_hours"], 6)
        self.assertEqual(economy["vehicle_leopard2a7_cooldown_minutes"], 45)
        self.assertEqual(economy["vehicle_leopard2a7_ammo_cost"], 40000000)
        self.assertEqual(economy["vehicle_leopard2a7_load_minutes"], 20)
        self.assertEqual(economy["vehicle_leopard2a7_ammo_cap"], 6)
        self.assertEqual(economy["vehicle_leopard2a7_fortification_damage_pct"], 30)
        self.assertEqual(economy["vehicle_leopard2a7_breach_minutes"], 30)
        self.assertEqual(economy["vehicle_leopard2a7_country_breach_cooldown_minutes"], 60)
        self.assertEqual(economy["vehicle_leopard2a7_breach_attack_bonus"], 20)
        self.assertEqual(economy["vehicle_leopard2a7_garrison_defense"], 25)
        self.assertEqual(economy["vehicle_javelin_cost"], 200000000)
        self.assertEqual(economy["vehicle_javelin_track_chance"], 30)
        self.assertEqual(economy["vehicle_b2_arm_hours"], 0.5)
        self.assertEqual(economy["vehicle_b52_cost"], 1000000000)
        self.assertEqual(economy["vehicle_b52_build_hours"], 8)
        self.assertEqual(economy["vehicle_b52_cooldown_minutes"], 120)
        self.assertEqual(economy["vehicle_b52_ammo_cost"], 50000000)
        self.assertEqual(economy["vehicle_b52_ammo_cap"], 2)
        self.assertEqual(economy["vehicle_b52_lockdown_minutes"], 60)
        self.assertEqual(economy["vehicle_b52_intercept_chance"], 50)
        self.assertEqual(B52_REPAIR_COST_PCTS, (25, 50, 75))
        self.assertEqual(B52_REPAIR_HOURS, (1, 2, 3))
        cfg = SimpleNamespace(
            get=lambda key, default=None: economy.get(key.removeprefix("economy."), default)
        )
        self.assertEqual(
            [_b52_repair_terms(cfg, phase) for phase in (1, 2, 3)],
            [(250000000, 1), (500000000, 2), (750000000, 3)],
        )
        self.assertEqual(economy["vehicle_b52_wallet_damage_pct"], 25)
        self.assertEqual(economy["vehicle_b52_bank_damage_pct"], 15)
        self.assertEqual(economy["vehicle_deimos_cost"], 80000000)
        self.assertEqual(economy["vehicle_deimos_build_hours"], 1)
        self.assertEqual(economy["vehicle_deimos_cooldown_hours"], 0.75)
        self.assertEqual(economy["deimos_success_chance"], 60)
        self.assertEqual(economy["deimos_recon_window_hours"], 4)
        self.assertEqual(economy["vehicle_sr71_cost"], 400000000)
        self.assertEqual(economy["vehicle_sr71_build_hours"], 4)
        self.assertEqual(economy["vehicle_sr71_cooldown_hours"], 1)
        self.assertEqual(economy["vehicle_sr71_shotdown_chance"], 20)
        self.assertEqual(EconomyCog._vehicle_cooldown_seconds(cfg, "sr71"), 3600)
        self.assertEqual(economy["vehicle_mq9_item_damage_pct"], 100)
        self.assertEqual(economy["vehicle_mq9_split_damage_pct"], 60)
        self.assertEqual(economy["vehicle_mq9_aa_intercept_chance"], 20)
        self.assertEqual(economy["vehicle_a10_cost"], 12000000000)
        self.assertEqual(economy["vehicle_a10_build_hours"], 12)
        self.assertEqual(economy["vehicle_a10_cooldown_minutes"], 360)
        self.assertEqual(economy["vehicle_a10_ammo_cost"], 1000000000)
        self.assertEqual(economy["vehicle_a10_ammo_cap"], 2)
        self.assertEqual(economy["vehicle_a10_vehicle_destroy_chance"], 70)
        self.assertEqual(economy["vehicle_a10_ammo_damage_pct"], 70)
        self.assertEqual(economy["vehicle_a10_regular_aa_damage_pct"], 75)
        self.assertEqual(economy["vehicle_a10_aa_intercept_chance"], 50)
        self.assertEqual(economy["vehicle_su34_cost"], 18000000000)
        self.assertEqual(economy["vehicle_su34_build_hours"], 16)
        self.assertEqual(economy["vehicle_su34_cooldown_minutes"], 240)
        self.assertEqual(economy["vehicle_su34_ammo_cost"], 1500000000)
        self.assertEqual(economy["vehicle_su34_ammo_cap"], 2)
        self.assertEqual(economy["vehicle_su34_vehicle_destroy_chance"], 50)
        self.assertEqual(economy["vehicle_su34_ammo_damage_pct"], 45)
        self.assertEqual(economy["vehicle_su34_regular_aa_damage_pct"], 50)
        self.assertEqual(economy["vehicle_su34_s400_intercept_chance"], 25)
        self.assertEqual(economy["vehicle_su34_patriot_intercept_chance"], 20)
        self.assertEqual(OFFENSE["mq9"], 42)
        self.assertEqual(OFFENSE["xb70"], 92)
        self.assertEqual(economy["vehicle_c130j_damage_pct"], 40)
        self.assertEqual(economy["vehicle_c130j_target_count"], 3)
        self.assertEqual(economy["vehicle_champ_blackout_minutes"], 45)
        self.assertEqual(economy["vehicle_maldx_success_chance"], 75)
        self.assertEqual(economy["vehicle_xb70_damage_min_pct"], 70)
        self.assertEqual(economy["vehicle_xb70_damage_max_pct"], 80)
        self.assertEqual(economy["vehicle_xb70_s400_intercept_chance"], 10)
        self.assertEqual(economy["vehicle_apache_cost"], 200000000)
        self.assertEqual(economy["vehicle_apache_build_hours"], 2)
        self.assertEqual(economy["vehicle_apache_cooldown_minutes"], 20)
        self.assertEqual(economy["vehicle_apache_ammo_cost"], 15000000)
        self.assertEqual(economy["vehicle_apache_ammo_cap"], 6)
        self.assertEqual(economy["vehicle_apache_aa_intercept_chance"], 30)
        self.assertEqual(economy["vehicle_p8_intercept_chance"], 30)
        self.assertEqual(economy["vehicle_comanche_upgrade_cost"], 1000000000)
        self.assertEqual(economy["vehicle_comanche_upgrade_hours"], 6)
        self.assertEqual(economy["vehicle_comanche_precision_damage_pct"], 100)
        self.assertEqual(economy["vehicle_comanche_cooldown_hours"], 8)
        self.assertEqual(economy["vehicle_comanche_jagms_per_mission"], 2)
        self.assertEqual(economy["vehicle_comanche_repair_cost_min"], 100000000)
        self.assertEqual(economy["vehicle_comanche_repair_cost_max"], 200000000)
        self.assertEqual(economy["vehicle_comanche_repair_hours"], 2)
        self.assertEqual(
            _comanche_intercept_range(
                SimpleNamespace(
                    get=lambda key, fallback=None: economy.get(key.removeprefix("economy."), fallback)
                )
            ),
            (20, 25),
        )
        self.assertEqual(
            _comanche_rounds_per_mission(
                SimpleNamespace(
                    get=lambda key, fallback=None: economy.get(key.removeprefix("economy."), fallback)
                )
            ),
            2,
        )
        self.assertEqual(EconomyCog._vehicle_cooldown_seconds(cfg, "apache"), 20 * 60)
        self.assertEqual(EconomyCog._vehicle_cooldown_seconds(cfg, "apache", comanche=True), 8 * 3600)
        with patch("cogs.economy.random.randint", return_value=150):
            self.assertEqual(
                _comanche_repair_quote(
                    SimpleNamespace(
                        get=lambda key, fallback=None: economy.get(key.removeprefix("economy."), fallback)
                    )
                ),
                150000000,
            )
        self.assertTrue({"b52", "apache"} <= set(LOADABLE_VEHICLES))
        self.assertEqual([_mq9_items_destroyed(size, 100) for size in (0, 1, 2, 3, 4, 8)], [0, 1, 2, 3, 4, 8])
        self.assertEqual([_mq9_items_destroyed(size, 60) for size in (0, 1, 2, 3, 4, 8)], [0, 1, 1, 1, 2, 4])
        self.assertEqual(
            set(DEIMOS_SUCCESS_LINES),
            {"You can run, but you can't hide.", "Found ya.", "Tracker on the prowl.", "I see you."},
        )
        self.assertEqual(
            set(DEIMOS_FAILURE_LINES),
            {"Tracker down.", "Tracker lost.", "Tracker shot down.", "Tracker jammed."},
        )
        self.assertEqual(COALITION_MEMBER_HARD_CAP, 10)
        configured = SimpleNamespace(
            get=lambda key, default=None: 10 if key == "economy.coalition_max_members" else default
        )
        oversized = SimpleNamespace(
            get=lambda key, default=None: 99 if key == "economy.coalition_max_members" else default
        )
        self.assertEqual(coalition_member_limit(configured), 10)
        self.assertEqual(coalition_member_limit(oversized), 10)

    def test_approved_sink_prices_and_country_caps_are_exact(self) -> None:
        economy = DEFAULTS["economy"]
        self.assertEqual(
            {
                "price_lock": economy["price_lock"],
                "price_uno": economy["price_uno"],
                "price_copcall": economy["price_copcall"],
                "price_drill": economy["price_drill"],
                "price_getaway": economy["price_getaway"],
            },
            {
                "price_lock": 1000000,
                "price_uno": 2000000,
                "price_copcall": 5000000,
                "price_drill": 10000000,
                "price_getaway": 5000000,
            },
        )
        self.assertEqual(economy["uno_reverse_wealth_cap_percent"], 10)
        self.assertEqual(economy["hold_cap_uno"], 8)
        self.assertEqual(ITEM_HOLD_CAPS["uno"], "hold_cap_uno")
        cfg = SimpleNamespace(
            get=lambda key, default=None: {
                "economy.uno_reverse_percent": 75,
                "economy.uno_reverse_wealth_cap_percent": economy["uno_reverse_wealth_cap_percent"],
            }.get(key, default)
        )
        thief = {"donuts": 10000000, "bank": 0}
        victim = {"donuts": 0, "bank": 0}
        moved = object.__new__(EconomyCog)._uno_reverse(cfg, thief, victim, 100000000)
        self.assertEqual(moved, 1000000)
        self.assertEqual((thief["donuts"], victim["donuts"]), (9000000, 1000000))
        self.assertEqual(economy["icbm_build_cost"], 750000000)
        self.assertEqual(economy["vehicle_aegis_ammo_cost"], 25000000)
        self.assertEqual(economy["vehicle_b2_build_hours"], 5)
        self.assertEqual(economy["aa_shield_cost"], 25000000)
        self.assertEqual(economy["vehicle_lrhw_ammo_cost"], 500000000)
        self.assertNotIn("insurance_premium", economy)
        self.assertNotIn("insurance_daily_cap", economy)
        self.assertEqual(economy["bank_interest_daily_cap"], 1000000)
        self.assertEqual(economy["casino_tour_reward"], 500000000)
        self.assertEqual(economy["casino_tour_wager_pct"], 3)
        self.assertEqual(economy["blackjack_win_profit_pct"], 135)
        self.assertEqual(economy["blackjack_natural_pct"], 300)
        self.assertEqual(economy["blackjack_charlie_profit_pct"], 350)
        self.assertEqual(economy["blackjack_bonus_777"], 9)
        self.assertEqual(economy["blackjack_suited_bonus"], 50)
        self.assertEqual(economy["roulette_even_profit_pct"], 145)
        self.assertEqual(economy["roulette_dozen_profit_pct"], 270)
        self.assertEqual(economy["roulette_straight_profit_pct"], 4000)
        self.assertEqual(economy["wheel_house_cut"], 2)
        self.assertEqual(economy["wheel_non_jackpot_rebate_pct"], 6)
        self.assertNotIn("dice_lucky_cap", economy)
        self.assertNotIn("bail_per_minute", economy)
        self.assertFalse(any((key.startswith("heist_") for key in economy)))
        self.assertEqual(economy["steal_fail_fine"], 500000)
        self.assertEqual(economy["bank_rob_fail_fine"], 2000000)
        self.assertEqual(economy["aa_shield_vehicle_bonus"], 10)
        self.assertEqual(
            (
                economy["country_invasion_cooldown_min_minutes"],
                economy["country_invasion_cooldown_max_minutes"],
            ),
            (10, 15),
        )
        self.assertEqual(
            {
                "odin": economy["thor_odin_build_hours"],
                "mjolnir": economy["thor_mjolnir_build_hours"],
                "bifrost": economy["thor_bifrost_build_hours"],
                "ascent_minutes": economy["thor_ascent_minutes"],
                "gbi_build": economy["thor_gbi_build_hours"],
                "assembly": economy["thor_assembly_hours"],
                "resupply_1": economy["thor_resupply_one_hours"],
                "resupply_2": economy["thor_resupply_two_hours"],
                "resupply_3": economy["thor_resupply_three_hours"],
                "chamber": economy["thor_chamber_hours"],
                "strike_cooldown": economy["thor_strike_cooldown_hours"],
                "bmd_refit": economy["thor_bmd_refit_hours"],
                "sm3_load": economy["thor_sm3_load_hours"],
            },
            {
                "odin": 24,
                "mjolnir": 36,
                "bifrost": 30,
                "ascent_minutes": 30,
                "gbi_build": 0.5,
                "assembly": 24,
                "resupply_1": 6,
                "resupply_2": 9,
                "resupply_3": 12,
                "chamber": 1.5,
                "strike_cooldown": 3,
                "bmd_refit": 18,
                "sm3_load": 3,
            },
        )
        self.assertEqual(economy["thor_gbi_stock_capacity"], 2)
        self.assertEqual(economy["thor_odin_cost"], 5000000000000)
        self.assertEqual(economy["thor_mjolnir_cost"], 7000000000000)
        self.assertEqual(economy["thor_bifrost_cost"], 5000000000000)
        self.assertEqual(economy["thor_launch_cost"], 2000000000000)
        self.assertEqual(economy["thor_assembly_cost"], 2000000000000)
        self.assertEqual(economy["thor_rod_cost"], 15000000000000)
        self.assertEqual(economy["thor_service_after_shots"], 3)
        self.assertEqual(economy["thor_service_cost"], 2000000000000)
        self.assertEqual(economy["thor_gbi_block2_chance_min"], 20)
        self.assertEqual(economy["thor_gbi_block2_chance_max"], 35)
        self.assertEqual(economy["continuity_cost"], 5000000000000)
        self.assertEqual(economy["continuity_funds_cap"], 100000000000000)
        self.assertEqual(DEFAULTS["abyss"]["dive_cost"], 1000000)
        self.assertEqual(DEFAULTS["abyss"]["deep_dive_cost"], 5000000)
        self.assertEqual(DEFAULTS["abyss"]["deep_debris_cost"], 2)
        self.assertEqual(economy["enchant_base_cost"], 10000000)
        self.assertEqual(FORTIFICATION_MAX_USEFUL, 1265625000)
        self.assertEqual(useful_war_chest_cap(100000000), 56250000)
        self.assertEqual(useful_war_chest_cap(1000000), 1000000)


class EconomyIntegrationTests(unittest.TestCase):
    def test_b52_lockdown_is_one_hour_and_blocks_vehicle_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            pilot = bot.economy.user(1, 123, 100)
            pilot["vehicles"]["apache"].update(owned=True, ammo=1)
            pilot["strategic_lockdown_until"] = (
                dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=60)
            ).isoformat()
            expiry = _strategic_lockdown_until(pilot)
            self.assertIsNotNone(expiry)
            error = cog._mission_preflight(1, 123, "apache", _FakeUser(456), "s400")
            self.assertIn("disabled by a B-52 strike", error)
            pilot["strategic_lockdown_until"] = None
            self.assertIsNone(cog._mission_preflight(1, 123, "apache", _FakeUser(456), "s400"))
            self.assertIn(
                "Apache requires objective", cog._mission_preflight(1, 123, "apache", _FakeUser(456), "fish")
            )
            pilot["vehicles"]["apache"].update(comanche_upgraded=True, ammo=2)
            self.assertIsNone(cog._mission_preflight(1, 123, "apache", _FakeUser(456)))
            pilot["vehicles"]["apache"]["ammo"] = 1
            self.assertIn("requires 2 loaded", cog._mission_preflight(1, 123, "apache", _FakeUser(456)))
            pilot["vehicles"]["apache"]["ammo"] = 2
            self.assertIsNone(cog._mission_preflight(1, 123, "apache", _FakeUser(456), "icbm", "s400"))
            self.assertIsNone(cog._mission_preflight(1, 123, "apache", _FakeUser(456), "icbm", "icbm"))
            pilot["vehicles"]["mq9"].update(owned=True, ammo=2)
            target = bot.economy.user(1, 456, 100)
            target["inventory"].update(lock=5, uno=4)
            self.assertIsNone(cog._mission_preflight(1, 123, "mq9", _FakeUser(456), "lock"))
            self.assertIsNone(cog._mission_preflight(1, 123, "mq9", _FakeUser(456), "lock", "uno"))
            self.assertIn(
                "two different", cog._mission_preflight(1, 123, "mq9", _FakeUser(456), "lock", "lock")
            )
            self.assertIn(
                "autocomplete list", cog._mission_preflight(1, 123, "mq9", _FakeUser(456), "not-an-item")
            )
            pilot["vehicles"]["mq9"]["ammo"] = 1
            self.assertIn(
                "requires 2 loaded", cog._mission_preflight(1, 123, "mq9", _FakeUser(456), "lock", "uno")
            )
            for model in ("a10", "su34", "c130j", "champ", "maldx", "lrhw", "xb70"):
                pilot["vehicles"][model].update(owned=True, ammo=1)
            target["aa_rockets_loaded"] = 2
            target["donuts"] = 1000
            target["vehicles"]["xb70"]["building_until"] = (
                dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
            ).isoformat()
            self.assertIsNone(cog._mission_preflight(1, 123, "c130j", _FakeUser(456)))
            self.assertIsNone(cog._mission_preflight(1, 123, "champ", _FakeUser(456)))
            self.assertIsNone(cog._mission_preflight(1, 123, "maldx", _FakeUser(456), "regular-aa"))
            self.assertIsNone(cog._mission_preflight(1, 123, "lrhw", _FakeUser(456), "xb70"))
            self.assertIsNone(cog._mission_preflight(1, 123, "xb70", _FakeUser(456)))
            self.assertIsNone(cog._mission_preflight(1, 123, "a10", _FakeUser(456), "f15e"))
            self.assertIsNone(cog._mission_preflight(1, 123, "su34", _FakeUser(456), "b52"))
            self.assertIn(
                "objective autocomplete",
                cog._mission_preflight(1, 123, "a10", _FakeUser(456), "not-a-vehicle"),
            )
            pilot["vehicles"]["sr71"].update(owned=True, last_deploy_at=None)
            pilot["electronic_blackout_until"] = (
                dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=45)
            ).isoformat()
            self.assertIn("CHAMP strike", cog._mission_preflight(1, 123, "sr71", _FakeUser(456)))

    def test_b2_has_only_its_normal_sortie_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            pilot = bot.economy.user(1, 123, 100)
            target = bot.economy.user(1, 456, 100)
            pilot["vehicles"]["b2"].update(owned=True, armed=True, last_deploy_at=None)
            target["donuts"] = 210000000
            pilot["b2_target_cooldowns"] = {
                "456": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)).isoformat()
            }
            target["b2_protected_until"] = (
                dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=14)
            ).isoformat()
            self.assertIsNone(cog._mission_preflight(1, 123, "b2", _FakeUser(456)))
            self.assertNotIn("b2_target_cooldowns", bot.economy.user(1, 123, 100))
            self.assertNotIn("b2_protected_until", bot.economy.user(1, 456, 100))
            self.assertNotIn("b2_victim_protection_days", DEFAULTS["economy"])
            self.assertNotIn("b2_same_target_days", DEFAULTS["economy"])

    def test_casino_reputation_tour_weekly_and_titles_coexist(self) -> None:
        user = {"plushies": {"dealer": 1}}
        first_week = dt.date(2026, 8, 30)
        last = None
        for game in CASINO_GAMES:
            last = record_casino_result(
                user,
                game,
                10000000,
                1000000,
                today=first_week,
                reputation_multiplier=1 + plushie_perk(user, "casino_rep") / 100,
                tour_reward=500,
                tour_required=3,
            )
        self.assertTrue(user["casino_weekly"]["claimed"])
        self.assertEqual(last["weekly_reputation"], 250)
        self.assertTrue(user["casino_tour"]["claimed"])
        reputation_after_first = user["casino_reputation"]
        for game in CASINO_GAMES:
            record_casino_result(user, game, 10000000, -1, today=first_week + dt.timedelta(days=7))
        self.assertEqual(user["casino_weekly"]["completed"], 2)
        self.assertGreater(user["casino_reputation"], reputation_after_first)

    def test_featured_game_rotates_only_among_non_slot_games(self) -> None:
        for offset in range(6):
            day = dt.date(2026, 9, 29) + dt.timedelta(days=offset)
            user = {"donuts": 0}
            slot_result = record_casino_result(user, "slots", 100, 0, today=day)
            featured = slot_result["featured_game"]
            self.assertIn(featured, CASINO_FEATURED_GAMES)
            self.assertNotEqual(featured, "slots")
            featured_result = record_casino_result(user, featured, 100, 0, today=day)
            self.assertEqual(featured_result["reputation"], slot_result["reputation"] * 2)

    def test_blackjack_special_hand_payouts_use_new_rates(self) -> None:
        dealer = [Card("10", "♣️"), Card("7", "♦️")]
        natural = [Card("A", "♠️"), Card("K", "♥️")]
        suited = [Card("A", "♠️"), Card("K", "♠️")]
        charlie = [Card(rank, "♠️") for rank in ("2", "3", "4", "5", "6")]
        sevens = [Card("7", suit) for suit in ("♠️", "♥️", "♦️")]
        self.assertEqual(settle_blackjack(natural, dealer, 100, True, natural_pct=225), (325, "blackjack"))
        self.assertEqual(
            settle_blackjack(suited, dealer, 100, True, natural_pct=225, suited_bonus=50),
            (375, "suited_blackjack"),
        )
        self.assertEqual(
            settle_blackjack(charlie, dealer, 100, False, charlie_pct=225), (325, "five_card_charlie")
        )
        self.assertEqual(settle_blackjack(sevens, dealer, 100, False, bonus_777=9), (1000, "lucky777"))

    def test_casino_tour_scales_once_from_highest_non_slot_wagers(self) -> None:
        user = {"donuts": 0}
        today = dt.date(2026, 9, 29)
        record_casino_result(user, "wheel", 1000000000000, 0, today=today)
        record_casino_result(user, "wheel", 10000000000000, 0, today=today)
        record_casino_result(user, "wheel", 100, 0, today=today)
        record_casino_result(user, "blackjack", 2000000000000, 0, today=today)
        slots_result = record_casino_result(
            user, "slots", 9000000000000000, 0, today=today, tour_reward=2000000, tour_wager_pct=1
        )
        self.assertEqual(slots_result["tour_reward"], 0)
        self.assertEqual(slots_result["tour_progress"], 2)
        result = record_casino_result(
            user, "roulette", 10, 0, today=today, tour_reward=2000000, tour_wager_pct=1
        )
        self.assertEqual(result["tour_reward"], 120000000000)
        self.assertEqual(user["donuts"], 120000000000)
        self.assertEqual(user["casino_tour"]["last_reward"], 120000000000)
        self.assertEqual(
            user["casino_tour"]["qualifying_wagers"],
            {"wheel": 10000000000000, "blackjack": 2000000000000, "roulette": 10},
        )
        again = record_casino_result(user, "roulette", 50000000000000, 0, today=today)
        self.assertEqual(again["tour_reward"], 0)
        self.assertEqual(user["donuts"], 120000000000)
        next_day = today + dt.timedelta(days=1)
        for game in ("wheel", "blackjack"):
            record_casino_result(user, game, 10, 0, today=next_day, tour_reward=2000000, tour_wager_pct=1)
        next_result = record_casino_result(
            user, "roulette", 10, 0, today=next_day, tour_reward=2000000, tour_wager_pct=1
        )
        self.assertEqual(next_result["tour_reward"], 2200000)
        self.assertEqual(
            user["casino_tour"]["qualifying_wagers"], {"wheel": 10, "blackjack": 10, "roulette": 10}
        )

    def test_new_tour_requires_all_non_slot_games_and_preserves_earlier_claims(self) -> None:
        today = dt.date(2026, 10, 1)
        user = {"donuts": 0}
        record_casino_result(user, "slots", 10**21, 0, today=today)
        first = record_casino_result(user, "wheel", 10000000000, 0, today=today)
        self.assertEqual(first["tour_progress"], 1)
        self.assertEqual(set(first["tour_missing"]), {"blackjack", "roulette"})
        second = record_casino_result(user, "blackjack", 20000000000, 0, today=today)
        self.assertEqual(second["tour_progress"], 2)
        self.assertEqual(user["donuts"], 0)
        last = record_casino_result(user, "roulette", 30000000000, 0, today=today)
        self.assertEqual(last["tour_reward"], 1800000000)
        self.assertTrue(last["tour_claimed"])
        self.assertEqual(last["tour_progress"], 3)
        for game in CASINO_GAMES:
            replay = record_casino_result(user, game, 10**21, 0, today=today)
            self.assertEqual(replay["tour_reward"], 0)
        self.assertEqual(user["donuts"], 1800000000)
        minimum_user = {"donuts": 0}
        for game in ("roulette", "blackjack", "wheel"):
            minimum = record_casino_result(minimum_user, game, 10, -10, today=today)
        self.assertEqual(minimum["tour_reward"], 500000000)
        legacy = {
            "donuts": 150000000,
            "casino_tour": {
                "day": today.isoformat(),
                "games": ["slots", "wheel", "blackjack"],
                "claimed": True,
                "last_reward": 150000000,
            },
        }
        result = record_casino_result(legacy, "roulette", 10**21, 0, today=today)
        self.assertTrue(result["tour_claimed"])
        self.assertEqual(result["tour_reward"], 0)
        self.assertEqual(legacy["donuts"], 150000000)
        self.assertEqual(legacy["casino_tour"]["last_reward"], 150000000)

    def test_new_blackjack_payouts_preserve_ties_surrender_and_plushie_bonuses(self) -> None:
        player = [Card("10", "♠️"), Card("8", "♥️")]
        dealer = [Card("10", "♦️"), Card("7", "♣️")]
        self.assertEqual(settle_blackjack(player, dealer, 100, False), (235, "win"))
        self.assertEqual(settle_blackjack(player, dealer, 200, False), (470, "win"))
        dealer_bust = dealer + [Card("10", "♣️")]
        self.assertEqual(settle_blackjack(player, dealer_bust, 100, False), (235, "dealer_bust"))
        self.assertEqual(settle_blackjack(player, player, 100, False), (100, "push"))
        suited = [Card("A", "♠️"), Card("K", "♠️")]
        self.assertEqual(settle_blackjack(suited, dealer, 100, True), (400, "blackjack"))
        self.assertEqual(
            settle_blackjack(suited, dealer, 100, True, suited_bonus=50), (450, "suited_blackjack")
        )
        self.assertEqual(
            settle_blackjack(suited, dealer, 100, True, suited_bonus=75), (475, "suited_blackjack")
        )
        for bonus in (9, 12):
            sevens = [Card("7", suit) for suit in ("♠️", "♥️", "♦️")]
            self.assertEqual(
                settle_blackjack(sevens, dealer, 100, False, bonus_777=bonus), (100 * (1 + bonus), "lucky777")
            )

    def test_casino_payout_buffs_keep_combination_bets_and_wheel_unchanged(self) -> None:
        self.assertEqual(roulette_profit_percent(1), 145)
        self.assertEqual(roulette_profit_percent(2), 270)
        self.assertEqual(roulette_profit_percent(18), 1800)
        self.assertEqual(roulette_profit_percent(12), 1200)
        self.assertEqual(roulette_profit_percent(8), 800)
        self.assertEqual(roulette_profit_percent(35), 4000)
        self.assertEqual(wheel_non_jackpot_rebate(1000, 6, 0), 60)
        self.assertEqual(wheel_non_jackpot_rebate(1000, 6, 2), 60)
        self.assertEqual(wheel_non_jackpot_rebate(1000, 6, "POT"), 0)
        self.assertEqual(
            [(mult, weight) for mult, weight, label in WHEEL],
            [(0, 1100), (0.5, 400), (1, 50), (2, 250), (3, 120), ("POT", 3)],
        )
        self.assertEqual(CASINO_FEATURED_GAMES, ("wheel", "blackjack", "roulette"))

    def test_blackjack_five_card_charlie_wins_without_dealer_comparison(self) -> None:
        player = [Card(rank, "♠️") for rank in ("2", "3", "4", "5", "6")]
        dealer = [Card("A", "♠️"), Card("K", "♥️")]
        self.assertEqual(settle_blackjack(player, dealer, 100, natural=False), (450, "five_card_charlie"))
        bust = [Card(rank, "♠️") for rank in ("10", "10", "2", "2", "2")]
        self.assertEqual(settle_blackjack(bust, dealer, 100, natural=False), (0, "bust"))

    def test_roulette_combinations_accept_only_real_layouts(self) -> None:
        valid = {"split:8-11": (18, 8), "street:13-14-15": (12, 14), "corner:17-18-20-21": (8, 20)}
        for raw, (ratio, winner) in valid.items():
            parsed = parse_roulette_space(raw)
            self.assertIsNotNone(parsed)
            self.assertEqual(parsed[1], ratio)
            self.assertTrue(parsed[2](winner))
        for raw in ("split:1-5", "street:1-2-4", "corner:1-2-3-4", "37"):
            self.assertIsNone(parse_roulette_space(raw), raw)

    def test_forced_slot_outcomes_match_requested_win_state(self) -> None:
        for want_win in (False, True):
            for seed in range(50):
                _, multiplier = biased_spin(random.Random(seed), 100 if want_win else 0)
                self.assertEqual(multiplier > 0, want_win)

    def test_deep_vault_settlement_is_idempotent(self) -> None:
        cog = object.__new__(EconomyCog)
        past = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)).isoformat()
        user = {
            "donuts": 5,
            "deep_vault_balance": 100,
            "deep_vault_withdraw_amount": 70,
            "deep_vault_withdraw_at": past,
        }
        self.assertEqual(cog._deep_vault_settle(user), 70)
        self.assertEqual(user["donuts"], 75)
        self.assertEqual(user["deep_vault_balance"], 30)
        self.assertEqual(cog._deep_vault_settle(user), 0)


class DiplomacyAndCountryTests(unittest.TestCase):
    def test_coalitions_block_allied_attacks_and_expire_cleanly(self) -> None:
        doc = {}
        root = coalitions.state(doc)
        future = coalitions.utcnow() + dt.timedelta(days=1)
        root["groups"]["1"] = {
            "name": "Test",
            "members": [1, 2],
            "founder": 1,
            "expires_at": future.isoformat(),
        }
        self.assertTrue(coalitions.are_allied(doc, 1, 2))
        self.assertFalse(coalitions.are_allied(doc, 1, 3))
        self.assertTrue(coalitions.cleanup(doc, future + dt.timedelta(seconds=1)))
        self.assertFalse(coalitions.are_allied(doc, 1, 2))

    def test_country_production_has_no_empire_size_penalty(self) -> None:
        doc = {}
        now = countries.now()
        picks = countries.COUNTRIES[:6]
        for index, country in enumerate(picks):
            countries.state(doc)["territories"][country.id] = {
                "owner": 7,
                "acquired_at": (now + dt.timedelta(seconds=index)).isoformat(),
                "last_collected_at": now.isoformat(),
                "fortification": 0,
            }
        holdings = countries.owned(doc, 7)
        self.assertEqual(len(holdings), 6)
        self.assertEqual(
            [countries.production_multiplier(i) for i in range(6)], [1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
        )
        for index, (country, _) in enumerate(holdings):
            self.assertGreater(countries.gross_per_day(country, index), countries.upkeep_per_day(country))

    def test_country_development_grows_gdp_and_output_with_escalating_costs(self) -> None:
        country = countries.BY_ID["plw"]
        territory = {"development_level": 10}
        self.assertAlmostEqual(countries.effective_gdp_b(country, territory, 3), country.gdp_b * 1.3)
        self.assertGreater(
            countries.gross_per_day(country, 0, territory, 3), countries.gross_per_day(country, 0)
        )
        self.assertGreater(countries.development_cost(country, 10), countries.development_cost(country, 0))

    def test_normalized_starter_countries_have_equal_economies(self) -> None:
        normalized = {
            "base_gdp_b": 500,
            "economic_value": 35000000000,
            "tier_override": "regional",
            "development_level": 0,
        }
        mongolia = countries.BY_ID["mng"]
        india = countries.BY_ID["ind"]
        for measure in (
            lambda c: countries.effective_gdp_b(c, normalized),
            lambda c: countries.gross_per_day(c, 0, normalized),
            lambda c: countries.upkeep_per_day(c, 0, normalized),
            lambda c: countries.development_cost(c, 0, territory=normalized),
        ):
            self.assertEqual(measure(mongolia), measure(india))
        self.assertEqual(countries.territory_tier(mongolia, normalized), "regional")
        self.assertEqual(countries.territory_tier(india, normalized), "regional")

    def test_slow_starter_path_begins_at_one_million_and_ends_at_real_gdp(self) -> None:
        basis = 86956522
        india = countries.BY_ID["ind"]
        territory = {
            "base_gdp_b": 1,
            "growth_target_gdp_b": india.gdp_b,
            "development_level": 0,
            "development_max_level": 50,
            "growth_curve_power": 3,
            "economic_value": basis,
            "economic_target_value": basis * india.gdp_b,
            "development_cost_growth_pct": 0,
            "fair_start_upkeep": True,
        }
        start_net = countries.gross_per_day(india, 0, territory) - countries.upkeep_per_day(
            india, 0, territory
        )
        self.assertEqual(start_net, 1000000)
        self.assertEqual(
            countries.gross_per_day(india, 0, territory) - countries.upkeep_per_day(india, 50, territory),
            1000000,
        )
        self.assertEqual(countries.effective_gdp_b(india, territory), 1)
        territory["development_level"] = 1
        self.assertLess(countries.effective_gdp_b(india, territory), 2)
        territory["development_level"] = 5
        self.assertLess(countries.effective_gdp_b(india, territory), 5)
        territory["development_level"] = 50
        self.assertEqual(countries.effective_gdp_b(india, territory), india.gdp_b)


class FishingIntegrationTests(unittest.TestCase):
    def test_esoteric_abyss_catches_are_optional_rare_and_high_value(self) -> None:
        self.assertEqual(len(ABYSS_CODEX_IDS), 4)
        self.assertEqual(len(ABYSS_ESOTERIC_IDS), 5)
        self.assertEqual(len(FISH_BY_ID), len(FISH) + len(ABYSS_FISH))
        self.assertEqual(set(ABYSS_ESOTERIC_IDS), set(ABYSS_ESOTERIC_LORE))
        self.assertTrue(set(ABYSS_ESOTERIC_IDS).isdisjoint(ABYSS_CODEX_IDS))
        weights = {fish.id: weight for fish, weight in abyss_catch_weights()}
        total_weight = sum(weights.values())
        prices = [base_sell_value(FISH_BY_ID[fid]) for fid in ABYSS_ESOTERIC_IDS]
        self.assertEqual(prices, sorted(prices))
        self.assertGreaterEqual(prices[0], 100000000)
        self.assertGreaterEqual(prices[-1], 5000000000)
        for fid in ABYSS_ESOTERIC_IDS:
            self.assertEqual(FISH_BY_ID[fid].tier, "mythic")
            self.assertLess(weights[fid] / total_weight, 0.001)

    def test_valuable_catch_weights_are_doubled_globally(self) -> None:
        self.assertEqual(RARE_PLUS_WEIGHT_MULTIPLIER, 2.0)
        surface = {fish.id: weight for fish, weight in catch_weights(None, [], 1.0)}
        for fish in FISH:
            expected = fish.weight * VALUABLE_TIER_WEIGHT_MULTIPLIERS.get(fish.tier, 1.0)
            if fish.tier in {"rare", "epic", "legendary"}:
                expected *= 2
            self.assertAlmostEqual(surface[fish.id], expected)
        self.assertEqual(ABYSS_MYTHIC_WEIGHT_MULTIPLIER, 2.0)
        abyss = {fish.id: weight for fish, weight in abyss_catch_weights()}
        for fish in ABYSS_FISH:
            expected = fish.weight * (2 if fish.tier == "mythic" else 1)
            self.assertAlmostEqual(abyss[fish.id], expected)

    def test_global_fish_sell_prices_are_scaled_for_the_current_economy(self) -> None:
        self.assertEqual(FISH_SELL_PRICE_BPS, 68750)
        for fish in FISH_BY_ID.values():
            old_price = fish.value * 625 // 100
            expected = fish.value * 68750 // 10000
            self.assertEqual(base_sell_value(fish), expected)
            self.assertEqual(sell_value(fish, None, []), expected)
            self.assertGreaterEqual(expected, old_price * 110 // 100)
            boosted_base = fish.value + fish.value * 40 // 100 + fish.value * 30 // 100
            self.assertEqual(sell_value(fish, "merchant", ["sharp", "sharp"]), boosted_base * 68750 // 10000)
        bucket = {"fish": {"boot": 2, "voidcaller": 1}, "rod_enchants": {}, "plushies": {}}
        self.assertEqual(
            object.__new__(Fishing)._bucket_value(bucket, None, 123),
            2 * base_sell_value(FISH_BY_ID["boot"]) + base_sell_value(FISH_BY_ID["voidcaller"]),
        )

    def test_every_rod_can_cast_and_price_every_fish(self) -> None:
        for rod_id in [None, *ROD_BY_ID]:
            catches = cast(random.Random(42), rod_id, [], 1.0)
            self.assertTrue(catches)
            for fish in FISH_BY_ID.values():
                self.assertGreaterEqual(sell_value(fish, rod_id, []), 0)

    def test_expanded_fishing_catalog_rods_and_titles_match_the_rebalance(self) -> None:
        self.assertTrue(
            {"bluefintuna", "giantoarfish", "megalodontooth", "androidcapsule", "celestialkoi"}
            <= {fish.id for fish in FISH}
        )
        self.assertTrue(
            {"hadalangler", "riftmanta", "blacksmoker", "worldserpent"} <= {fish.id for fish in ABYSS_FISH}
        )
        self.assertEqual(ROD_BY_ID["twinline"].price, 5000000)
        self.assertEqual(ROD_BY_ID["abyssal"].price, 250000000)
        self.assertEqual(ROD_BY_ID["mythicrod"].price, 2000000000)
        self.assertEqual(TITLE_BY_ID["labdirector"].price, 5000000000)
        self.assertEqual(TITLE_BY_ID["economydevourer"].price, 25000000000000000)
        self.assertEqual(TITLE_BY_ID["fishmogul"].price, 0)


class CommandTreeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._imported_cogs = {
            name: module for name, module in sys.modules.items() if name == "cogs" or name.startswith("cogs.")
        }

    async def asyncTearDown(self) -> None:
        for name in list(sys.modules):
            if name.startswith("cogs.") and name not in self._imported_cogs:
                sys.modules.pop(name, None)
        sys.modules.update(self._imported_cogs)
        for name, module in self._imported_cogs.items():
            if "." in name:
                parent, attribute = name.rsplit(".", 1)
                if parent in sys.modules:
                    setattr(sys.modules[parent], attribute, module)

    async def test_every_extension_loads_and_discord_command_cap_is_respected(self) -> None:
        bot = Szofie()
        failures = []
        for extension in COGS:
            try:
                await bot.load_extension(extension)
            except Exception as exc:
                failures.append((extension, repr(exc)))
        commands = bot.tree.get_commands()
        self.assertFalse(failures)
        self.assertLessEqual(len(commands), 100)
        names = [command.name for command in commands]
        self.assertEqual(len(names), len(set(names)))
        country = bot.get_cog("Countries")
        self.assertIsNotNone(country)
        self.assertEqual(len(country.country.commands), 10)
        self.assertIn("cogs.moderation", COGS)
        self.assertNotIn("cogs.discipline", COGS)
        for removed in {
            "purge",
            "lock",
            "unlock",
            "slowmode",
            "warn",
            "warnings",
            "delwarn",
            "clearwarns",
            "timeout",
            "untimeout",
            "kick",
            "ban",
            "unban",
            "wanted",
            "lucky",
            "duel",
            "bounty",
            "bounties",
            "rouletteagain",
            "trigger",
            "schedule",
            "dice",
            "heist",
            "bail",
            "insure",
            "casinotour",
            "about",
            "setup",
            "flip",
            "fact",
            "snipe",
            "crown",
            "halloffame",
            "freezer",
        }:
            self.assertNotIn(removed, names)
        self.assertIn("nuke", names)
        self.assertIn("8ball", names)
        self.assertIn("titleshop", names)
        self.assertIn("buytitle", names)
        self.assertIn("view", {parameter.name for parameter in bot.tree.get_command("titles").parameters})
        self.assertIn("view", {parameter.name for parameter in bot.tree.get_command("season").parameters})
        self.assertIn("continuity", names)
        self.assertIn("exploration", names)
        self.assertIn("deathstar", names)
        self.assertEqual(len(bot.tree.get_command("deathstar").commands), 18)
        self.assertNotIn("hangar", {command.name for command in bot.tree.get_command("vehicle").commands})
        self.assertEqual(bot.tree.get_command("arsenal").parameters, [])
        for retained_loan_command in {"loan", "repay", "collect", "loans"}:
            self.assertIn(retained_loan_command, names)
        self.assertNotIn("collect", {command.name for command in country.country.commands})
        await bot.close()


class _CaptureResponse:
    def __init__(self) -> None:
        self.calls = []
        self.initial_calls = []
        self.deferred = None

    async def send_message(self, **kwargs) -> None:
        if self.is_done():
            raise RuntimeError("Interaction already acknowledged; use edit or followup")
        self.calls.append(kwargs)
        self.initial_calls.append(kwargs)

    async def defer(self, **kwargs) -> None:
        if self.is_done():
            raise RuntimeError("Interaction already acknowledged; cannot defer twice")
        self.deferred = kwargs

    def is_done(self) -> bool:
        return self.deferred is not None or bool(self.calls)


class _FakeUser:
    def __init__(self, user_id: int = 123) -> None:
        self.id = user_id
        self.display_name = f"Integration Tester {user_id}"
        self.mention = f"<@{user_id}>"
        self.bot = False
        self.display_avatar = None


class _FakeGuild:
    def get_member(self, user_id):
        return None


class _CaptureFollowup:
    def __init__(self, interaction=None) -> None:
        self.calls = []
        self.interaction = interaction

    async def send(self, **kwargs) -> None:
        self.calls.append(kwargs)
        if self.interaction and self.interaction.extras.get("szofie_privacy_transition"):
            self.interaction.response.calls.append(kwargs)


class _FakeInteraction:
    def __init__(self, guild_id: int = 1, user_id: int = 123) -> None:
        self.guild_id = guild_id
        self.channel_id = 1
        self.channel = None
        self.user = _FakeUser(user_id)
        self.guild = _FakeGuild()
        self.response = _CaptureResponse()
        self.extras = {}
        self.followup = _CaptureFollowup(self)
        self.original_edits = []

    async def edit_original_response(self, **kwargs) -> None:
        self.original_edits.append(kwargs)
        if self.response.deferred is not None and (not self.extras.get("szofie_privacy_transition")):
            call = dict(kwargs, ephemeral=self.response.deferred.get("ephemeral", False))
            if call.get("attachments"):
                call["file"] = call["attachments"][0]
            self.response.calls.append(call)

    async def delete_original_response(self) -> None:
        self.original_deleted = True

    async def original_response(self):
        return _FakeMessage()


class _FakeMessage:
    def __init__(self) -> None:
        self.edits = []
        self.channel = None

    async def edit(self, **kwargs) -> None:
        self.edits.append(kwargs)


class _FakeLedger:
    async def record(self, *args, **kwargs) -> None:
        return None


class _FakeBot:
    def __init__(self, root: Path) -> None:
        self.config = ConfigManager(Storage(root / "config"))
        self.economy = Economy()
        self.economy.store = Storage(root / "economy")
        self.ledger = _FakeLedger()
        self.cogs = {}

    def get_cog(self, name: str):
        return self.cogs.get(name)


def _status_reply_pages(reply):
    view = reply.get("view")
    return view.pages if view is not None and hasattr(view, "pages") else [reply["embed"]]


def _status_reply_text(reply):
    return "\n".join(
        (
            (page.description or "")
            + "\n"
            + "\n".join((field.name + "\n" + field.value for field in page.fields))
            for page in _status_reply_pages(reply)
        )
    )


class InteractionDeadlineTests(unittest.IsolatedAsyncioTestCase):
    def strict_interaction(self, *, command: str = "", uid: int = 123):
        interaction = _FakeInteraction(user_id=uid)
        interaction.command = SimpleNamespace(name=command)
        initial_send = interaction.response.send_message
        original_edit = interaction.edit_original_response

        async def send(**kwargs):
            self.assertFalse(interaction.response.is_done(), "Cannot send a second initial response")
            await initial_send(**kwargs)

        async def edit(**kwargs):
            self.assertTrue(interaction.response.is_done(), "Complete only after acknowledgement")
            self.assertNotIn("ephemeral", kwargs)
            self.assertNotIn("file", kwargs)
            await original_edit(**kwargs)

        interaction.response.send_message = send
        interaction.edit_original_response = edit
        return interaction

    async def test_thor_resupply_and_chamber_ack_before_slow_save_and_commit_once(self):
        for command in ("resupply", "chamber"):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as raw:
                bot = _FakeBot(Path(raw))
                cog = ThorCog(bot)
                user = bot.economy.user(1, 123, 0)
                cost = DEFAULTS["economy"]["thor_rod_cost"]
                user["donuts"] = 10 * cost
                state = thor_state.normalize(user)
                state.update(operational=True, rods=3)
                interaction = self.strict_interaction(command=command)
                original_save = bot.economy.save
                saving, release = (asyncio.Event(), asyncio.Event())

                async def slow_save(gid):
                    self.assertEqual(interaction.response.deferred, {"thinking": True, "ephemeral": False})
                    saving.set()
                    await release.wait()
                    await original_save(gid)

                bot.economy.save = slow_save
                bot.ledger.record = AsyncMock()
                callback = getattr(ThorCog, command).callback
                task = asyncio.create_task(
                    callback(cog, interaction, 2) if command == "resupply" else callback(cog, interaction)
                )
                await asyncio.wait_for(saving.wait(), timeout=1)
                self.assertEqual(interaction.response.initial_calls, [])
                self.assertFalse(task.done())
                release.set()
                await task
                self.assertEqual(len(interaction.original_edits), 1)
                for picture in interaction.original_edits[0].get("attachments", []):
                    picture.close()
                bot.economy.save = original_save
                again = self.strict_interaction(command=command)
                await (callback(cog, again, 2) if command == "resupply" else callback(cog, again))
                if command == "resupply":
                    self.assertEqual(user["donuts"], 8 * cost)
                    self.assertEqual(state["resupply_qty"], 2)
                    bot.ledger.record.assert_awaited_once()
                else:
                    self.assertEqual(user["donuts"], 10 * cost)
                    self.assertEqual(state["rods"], 3)
                    self.assertIsNotNone(state["chambering_until"])
                self.assertTrue(again.followup.calls[0]["ephemeral"])
                self.assertTrue(again.original_deleted)

    async def test_ack_failure_prevents_thor_purchase_or_chamber_mutation(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = ThorCog(bot)
            user = bot.economy.user(1, 123, 0)
            user["donuts"] = 10 * DEFAULTS["economy"]["thor_rod_cost"]
            thor_state.normalize(user).update(operational=True, rods=3)
            for command in ("resupply", "chamber"):
                before = copy.deepcopy(user)
                interaction = self.strict_interaction(command=command)
                interaction.response.defer = AsyncMock(side_effect=RuntimeError("ack failed"))
                callback = getattr(ThorCog, command).callback
                with self.assertRaisesRegex(RuntimeError, "ack failed"):
                    await (
                        callback(cog, interaction, 1) if command == "resupply" else callback(cog, interaction)
                    )
                self.assertEqual(user, before)

    async def test_deferred_public_errors_never_edit_private_content_into_public_message(self):
        from szofie import ui

        interaction = self.strict_interaction()
        await ui.defer_response(interaction)
        await ui.respond(interaction, content="Private inventory details", ephemeral=True)
        self.assertEqual(interaction.original_edits, [{"content": "Preparing your response…"}])
        self.assertEqual(interaction.followup.calls[0]["content"], "Private inventory details")
        self.assertTrue(interaction.followup.calls[0]["ephemeral"])
        self.assertTrue(interaction.original_deleted)

    async def test_space_dashboard_buttons_acknowledge_before_settlement_io(self):
        from cogs.space import ExplorationDashboard

        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = SpaceCog(bot)
            view = ExplorationDashboard(cog, 123)
            user = bot.economy.user(1, 123, 0)
            space_state.normalize(user)["transport_build_until"] = (
                space_state.now() - dt.timedelta(seconds=1)
            ).isoformat()
            interaction = self.strict_interaction()
            original_save = bot.economy.save

            async def save(gid):
                self.assertEqual(interaction.response.deferred, {"thinking": True, "ephemeral": True})
                await original_save(gid)

            bot.economy.save = save
            await view.next_step.callback(interaction)
            self.assertEqual(interaction.response.initial_calls, [])
            self.assertEqual(len(interaction.original_edits), 1)
            view.stop()

    async def test_space_guard_settlement_and_craft_save_ack_before_io(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = SpaceCog(bot)
            user = bot.economy.user(1, 123, 0)
            user["donuts"] = 100 * space_state.QA
            state = space_state.normalize(user)
            state["transport_build_until"] = (space_state.now() - dt.timedelta(seconds=1)).isoformat()
            interaction = self.strict_interaction(command="build")
            original_save = bot.economy.save
            saves = []

            async def slow_save(gid):
                self.assertEqual(interaction.response.deferred, {"thinking": True, "ephemeral": False})
                saves.append(gid)
                await asyncio.sleep(0)
                await original_save(gid)

            bot.economy.save = slow_save
            await SpaceCog.build.callback(
                cog, interaction, app_commands.Choice(name="Drake Cutlass", value="cutlass")
            )
            self.assertEqual(len(saves), 2)
            self.assertEqual(user["donuts"], 50 * space_state.QA)
            self.assertEqual(interaction.response.initial_calls, [])
            self.assertEqual(len(interaction.original_edits), 1)
            for picture in interaction.original_edits[0].get("attachments", []):
                picture.close()

    async def test_private_space_status_and_public_guide_keep_visibility(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = SpaceCog(bot)
            private = self.strict_interaction(command="status")
            await SpaceCog.status.callback(cog, private)
            self.assertEqual(private.response.deferred, {"thinking": True, "ephemeral": True})
            self.assertEqual(private.response.initial_calls, [])
            private.original_edits[0]["view"].stop()
            for callback in (SpaceCog.guide.callback, SpaceCog.exploration.callback, SpaceCog.atlas.callback):
                public = self.strict_interaction()
                await callback(cog, public)
                self.assertEqual(public.response.deferred, {"thinking": True, "ephemeral": False})
                self.assertEqual(public.response.initial_calls, [])
                public.original_edits[0]["view"].stop()

    async def test_space_lock_wait_is_acknowledged_before_purchase(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = SpaceCog(bot)
            user = bot.economy.user(1, 123, 0)
            user["donuts"] = 100 * space_state.QA
            interaction = self.strict_interaction(command="build")
            lock = cog.lock(1)
            await lock.acquire()
            task = asyncio.create_task(
                SpaceCog.build.callback(
                    cog, interaction, app_commands.Choice(name="Drake Cutlass", value="cutlass")
                )
            )
            try:
                for _ in range(10):
                    await asyncio.sleep(0)
                    if interaction.response.is_done():
                        break
                self.assertTrue(interaction.response.is_done())
                self.assertFalse(task.done())
                self.assertEqual(user["donuts"], 100 * space_state.QA)
            finally:
                lock.release()
            await task
            self.assertEqual(user["donuts"], 50 * space_state.QA)
            for picture in interaction.original_edits[0].get("attachments", []):
                picture.close()


class SpaceCommandIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cutlass_command_route_missions_and_colony_gate(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = SpaceCog(bot)
            user = bot.economy.user(1, 123, 0)
            user["donuts"] = 100 * space_state.QA
            state = space_state.normalize(user)
            with patch.object(cog, "send"):
                await SpaceCog.build.callback(
                    cog, _FakeInteraction(), app_commands.Choice(name="Drake Cutlass", value="cutlass")
                )
            self.assertEqual(user["donuts"], 50 * space_state.QA)
            self.assertFalse(state["cutlass_owned"])
            state["cutlass_build_until"] = (space_state.now() - dt.timedelta(seconds=1)).isoformat()
            from szofie import space_fleet as fleet

            space_state.settle(user)
            fleet.launch(user, "cutlass", space_state.now() - dt.timedelta(minutes=31), "cutlass-launch")
            await SpaceCog.survey.callback(cog, _FakeInteraction(), "mars")
            self.assertIn("mars", state["surveys"])
            with patch.object(cog, "send"):
                await SpaceCog.travel.callback(cog, _FakeInteraction(), "mars")
            self.assertEqual(user["donuts"], 46 * space_state.QA)
            state["travel_until"] = (space_state.now() - dt.timedelta(seconds=1)).isoformat()
            with patch.object(cog, "send"):
                await SpaceCog.expedition.callback(cog, _FakeInteraction())
            self.assertEqual(state["expedition"]["body"], "mars")
            blocked = _FakeInteraction()
            await SpaceCog.land.callback(cog, blocked)
            self.assertFalse(state["colonies"].get("mars", {}).get("landed"))
            self.assertIn("Cutlass cannot", blocked.response.calls[-1]["content"])
            state["expedition"]["ready_at"] = (space_state.now() - dt.timedelta(seconds=1)).isoformat()
            dashboard = _FakeInteraction()
            await SpaceCog.status.callback(cog, dashboard)
            call = dashboard.response.calls[-1]
            self.assertTrue(call["ephemeral"])
            self.assertEqual(len(call["view"].children), 5)
            self.assertIn("mars", state["journal"])
            _assert_embed_valid(self, call["embed"])
            self.assertFalse(await call["view"].interaction_check(_FakeInteraction(user_id=456)))
            journal = _FakeInteraction()
            await SpaceCog.journal.callback(cog, journal)
            for page in journal.response.calls[0]["view"].pages:
                _assert_embed_valid(self, page)
            reloaded = Storage(bot.economy.store.directory).load(1)
            self.assertIn("mars", reloaded["users"]["123"]["space"]["journal"])

    async def test_focus_changes_settle_old_rate_and_charge_once(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = SpaceCog(bot)
            user = bot.economy.user(1, 123, 0)
            user["donuts"] = 50 * space_state.QA
            site = space_state.colony(space_state.normalize(user), "mars")
            space_state.galaxy(bot.economy.store.load(1))["claims"]["mars"] = 123
            site.update(
                gdp=8000,
                mine=True,
                materials={},
                income_at=(space_state.now() - dt.timedelta(days=1)).isoformat(),
                material_at=(space_state.now() - dt.timedelta(hours=6)).isoformat(),
            )
            await SpaceCog.specialise.callback(
                cog, _FakeInteraction(), "mars", app_commands.Choice(name="Industrial", value="industrial")
            )
            self.assertEqual(user["donuts"], 50 * space_state.QA + 100)
            self.assertEqual(site["materials"]["water-ice"], 11)
            self.assertEqual(site["specialisation"], "industrial")
            await SpaceCog.specialise.callback(
                cog, _FakeInteraction(), "mars", app_commands.Choice(name="Civilian", value="civilian")
            )
            self.assertEqual(user["donuts"], 25 * space_state.QA + 100)
            self.assertEqual(space_state.colony_daily_income(site), 120)

    async def test_unload_preserves_item_caps_and_duplicate_rod_enchants(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = SpaceCog(bot)
            user = bot.economy.user(1, 123, 0)
            state = space_state.normalize(user)
            from cogs.economy import ITEM_HOLD_CAPS

            cap_key = ITEM_HOLD_CAPS["uno"]
            bot.config.for_guild(1).set(f"economy.{cap_key}", 8)
            user["inventory"]["uno"] = 8
            state["cargo"]["inventory"]["uno"] = 8
            await SpaceCog.unload.callback(
                cog, _FakeInteraction(), app_commands.Choice(name="Items", value="inventory")
            )
            self.assertEqual(user["inventory"]["uno"], 8)
            self.assertEqual(state["cargo"]["inventory"]["uno"], 8)
            user["rods"]["abyssal"] = 1
            user["rod_enchants"]["abyssal"] = ["sharp"]
            state["cargo"]["rods"]["abyssal"] = {"enchants": [], "equipped": False}
            await SpaceCog.unload.callback(
                cog, _FakeInteraction(), app_commands.Choice(name="Rods", value="rods")
            )
            self.assertEqual(user["rod_enchants"]["abyssal"], ["sharp"])
            self.assertIn("abyssal", state["cargo"]["rods"])

    async def test_warnings_pause_until_delivered_and_failed_backup_is_retryable(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            bot.get_channel = lambda cid: None
            cog = ImperialStarDestroyerCog(bot)
            user = bot.economy.user(1, 123, 0)
            state = isd_state.normalize(user)
            state.update(operational=True, active_window_id="TEST")
            doc = bot.economy.store.load(1)
            record = {
                "kind": "planetary",
                "body": "mars",
                "guild_id": 1,
                "builder": 123,
                "remaining_seconds": 120,
                "channel_id": 1,
                "announced": False,
                "interceptors": [],
            }
            isd_state.windows(doc)["TEST"] = record
            await cog._advance_windows(1, 15)
            self.assertEqual(record["remaining_seconds"], 120)
            record.update(announced=True, remaining_seconds=0)
            snapshot = copy.deepcopy(doc)
            with patch.object(cog, "_write_backup", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    await cog._resolve_window(1, "TEST")
            self.assertEqual(doc, snapshot)
            with (
                patch.object(cog, "_write_backup"),
                patch.object(bot.economy.store, "_write", side_effect=OSError("disk full")),
            ):
                with self.assertRaises(OSError):
                    await cog._resolve_window(1, "TEST")
            self.assertEqual(doc, snapshot)

    async def test_mission_sweep_and_mining_work_through_guild_alias_once(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            bot.economy.store.aliases = {2: 1}
            bot.guilds = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
            cog = SpaceCog(bot)
            user = bot.economy.user(1, 123, 0)
            state = space_state.normalize(user)
            state.update(cutlass_owned=True, location="mars")
            record = space_state.start_expedition(
                user, space_state.BODIES["mars"], space_state.now(), "offline"
            )
            record["ready_at"] = (space_state.now() - dt.timedelta(days=2)).isoformat()
            site = space_state.colony(state, "ceres")
            site.update(mine=True, material_at=(space_state.now() - dt.timedelta(days=30)).isoformat())
            await cog.income_loop.coro(cog)
            self.assertIsNone(state["expedition"])
            self.assertEqual(state["journal"]["mars"]["missions"], 1)
            self.assertEqual(site["materials"]["water-ice"], 12 * 28)
            snapshot = copy.deepcopy(state)
            await cog.income_loop.coro(cog)
            self.assertEqual(state, snapshot)

    async def test_public_warning_delivery_resumes_countdown_after_delivery(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            channel = SimpleNamespace(send=AsyncMock())
            bot.get_channel = lambda cid: channel
            cog = ImperialStarDestroyerCog(bot)
            record = {
                "kind": "assembly",
                "guild_id": 1,
                "builder": 123,
                "remaining_seconds": 120,
                "channel_id": 1,
                "announced": False,
                "interceptors": [],
            }
            isd_state.windows(bot.economy.store.load(1))["TEST"] = record
            await cog._advance_windows(1, 15)
            self.assertTrue(record["announced"])
            self.assertEqual(record["remaining_seconds"], 120)
            channel.send.assert_awaited_once()
            await cog._advance_windows(1, 300)
            self.assertEqual(record["remaining_seconds"], 105)

    async def test_exploration_guide_has_complete_requester_bound_pages(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            cog = SpaceCog(_FakeBot(Path(raw)))
            interaction = _FakeInteraction(user_id=123)
            await SpaceCog.exploration.callback(cog, interaction)
            call = interaction.response.calls[0]
            self.assertFalse(call["ephemeral"])
            pages = call["view"].pages
            self.assertEqual(len(pages), 13 + len(SpaceCog.fleet_pages()))
            self.assertEqual(call["view"].author_id, 123)
            for page in pages:
                _assert_embed_valid(self, page)
            content = "\n".join((page.description for page in pages))
            for command in (
                "/isd fabricate",
                "/isd launch",
                "/isd assemble",
                "/space refit",
                "/space load",
                "/space intercept",
                "/space survey",
                "/space travel",
                "/space land",
                "/space build-site",
                "/space mine",
                "/space stow",
                "/space offload",
                "/space terraform",
                "/space claim",
                "/space develop",
                "/space unload",
                "/space bombard",
                "/isd intercept",
            ):
                self.assertIn(command, content)
            self.assertNotIn("owner_", content)
            self.assertNotIn("autoref", content.lower())
            outsider = _FakeInteraction(user_id=456)
            self.assertFalse(await call["view"].interaction_check(outsider))
            self.assertIn("Run the command yourself", outsider.response.calls[0]["embed"].description)
            existing = _FakeInteraction(user_id=123)
            await SpaceCog.guide.callback(cog, existing)
            self.assertFalse(existing.response.calls[0]["ephemeral"])
            self.assertEqual(
                [page.description for page in existing.response.calls[0]["view"].pages],
                [page.description for page in pages],
            )

    async def test_expedition_refit_and_donut_load_escrow_are_restart_safe(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = SpaceCog(bot)
            user = bot.economy.user(1, 123, 0)
            user["donuts"] = 200 * space_state.QA
            user["bank"] = 100 * space_state.QA
            isd_state.normalize(user)["operational"] = True
            interaction = _FakeInteraction()
            with patch.object(cog, "send"):
                await SpaceCog.refit.callback(
                    cog, interaction, app_commands.Choice(name="Expedition", value="expedition")
                )
            state = space_state.normalize(user)
            self.assertEqual(state["mode"], "attack")
            self.assertEqual(state["refit_target"], "expedition")
            state["refit_until"] = (space_state.now() - dt.timedelta(seconds=1)).isoformat()
            space_state.settle(user)
            self.assertEqual(state["mode"], "expedition")
            state["transport_owned"] = True
            from szofie import space_fleet as fleet

            fleet.ship(user, "transport")["deployed"] = True
            interaction = _FakeInteraction()
            with patch.object(cog, "send"):
                await SpaceCog.load.callback(
                    cog, interaction, app_commands.Choice(name="Donuts", value="donuts"), "100qa", None
                )
            self.assertEqual(state["load_manifest"]["donuts"], 100 * space_state.QA)
            self.assertEqual(user["donuts"], 24 * space_state.QA)
            self.assertEqual(user["bank"], 100 * space_state.QA)
            self.assertEqual(state["cargo"]["donuts"], 0)
            state["load_until"] = (space_state.now() - dt.timedelta(seconds=1)).isoformat()
            space_state.settle(user)
            self.assertEqual(state["cargo"]["donuts"], 100 * space_state.QA)
            self.assertIsNone(state["load_manifest"])
            await SpaceCog.unload.callback(
                cog, _FakeInteraction(), app_commands.Choice(name="Donuts", value="donuts")
            )
            self.assertEqual(state["cargo"]["donuts"], 0)
            interaction = _FakeInteraction()
            with patch.object(cog, "send"):
                await SpaceCog.refit.callback(
                    cog, interaction, app_commands.Choice(name="Attack", value="attack")
                )
            self.assertEqual(state["refit_target"], "attack")

    async def test_planetary_window_erases_only_targeted_world(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            bot.get_channel = lambda channel_id: None
            cog = ImperialStarDestroyerCog(bot)
            attacker = bot.economy.user(1, 123, 0)
            isd_state.normalize(attacker)["operational"] = True
            victim = bot.economy.user(1, 456, 0)
            galaxy = space_state.galaxy(bot.economy.store.load(1))
            galaxy["claims"].update(mars=456, ceres=456)
            space_state.colony(space_state.normalize(victim), "mars")["gdp"] = 10
            space_state.colony(space_state.normalize(victim), "ceres")["gdp"] = 20
            isd_state.windows(bot.economy.store.load(1))["PLANET-TEST"] = {
                "kind": "planetary",
                "guild_id": 1,
                "builder": 123,
                "body": "mars",
                "channel_id": 1,
                "remaining_seconds": 0,
                "interceptors": [],
            }
            with patch.object(cog, "_write_backup"):
                await cog._resolve_window(1, "PLANET-TEST")
            self.assertNotIn("mars", galaxy["claims"])
            self.assertEqual(galaxy["claims"]["ceres"], 456)
            self.assertNotIn("mars", space_state.normalize(victim)["colonies"])
            self.assertIn("ceres", space_state.normalize(victim)["colonies"])

    async def test_xwing_hit_delays_loading_without_duplicating_cargo(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = SpaceCog(bot)
            attacker = bot.economy.user(1, 123, 0)
            defender = bot.economy.user(1, 456, 0)
            own = space_state.normalize(attacker)
            own.update(xwing_owned=True, xwing_ammo=1)
            from szofie import space_fleet as fleet

            fleet.ship(attacker, "xwing")["deployed"] = True
            state = space_state.normalize(defender)
            state.update(
                transport_owned=True,
                load_until=(space_state.now() + dt.timedelta(hours=12)).isoformat(),
                load_manifest={
                    "donuts": 1000,
                    "inventory": {"lock": 20},
                    "plushies": {"dealer": 1},
                    "fish": {},
                    "rods": {},
                    "vehicles": {},
                },
            )
            interaction = _FakeInteraction(user_id=123)
            with (
                patch("cogs.space.random.randint", return_value=1),
                patch.object(cog, "art", return_value=None),
            ):
                await SpaceCog.intercept.callback(cog, interaction, _FakeUser(456))
            self.assertEqual(own["xwing_ammo"], 0)
            self.assertEqual(state["load_manifest"]["donuts"], 950)
            self.assertEqual(state["load_manifest"]["inventory"]["lock"], 19)
            self.assertEqual(state["load_manifest"]["plushies"]["dealer"], 1)
            self.assertFalse(state["transport_owned"])
            self.assertGreater(
                space_state.at(state["load_until"]), space_state.now() + dt.timedelta(hours=23)
            )

    async def test_claim_income_arrives_in_wallet_once_per_elapsed_hour(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            bot.guilds = [SimpleNamespace(id=1)]
            cog = SpaceCog(bot)
            user = bot.economy.user(1, 123, 0)
            state = space_state.normalize(user)
            colony = space_state.colony(state, "mars")
            colony["gdp"] = space_state.gdp(7, 5)
            colony["income_at"] = (space_state.now() - dt.timedelta(hours=24, minutes=1)).isoformat()
            space_state.galaxy(bot.economy.store.load(1))["claims"]["mars"] = 123
            await cog.income_loop.coro(cog)
            self.assertEqual(user["donuts"], space_state.daily_income(colony["gdp"]))
            await cog.income_loop.coro(cog)
            self.assertEqual(user["donuts"], space_state.daily_income(colony["gdp"]))


class ImperialStarDestroyerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_explains_online_windows_and_displays_assembly_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = ImperialStarDestroyerCog(bot)
            user = bot.economy.user(1, 123, 0)
            state = isd_state.normalize(user)
            ready = isd_state.utcnow() + dt.timedelta(hours=30)
            for part in state["components"].values():
                part.update(status="orbit")
            state.update(assembling_until=ready.isoformat(), active_window_id="ASSEMBLY-TEST")
            isd_state.windows(bot.economy.store.load(1))["ASSEMBLY-TEST"] = {"remaining_seconds": 3600}
            interaction = _FakeInteraction()
            await ImperialStarDestroyerCog.status.callback(cog, interaction, None)
            embed = interaction.response.calls[-1]["embed"]
            _assert_embed_valid(self, embed)
            text = _status_reply_text(interaction.response.calls[-1])
            self.assertIn("**Orbital assembly:**", text)
            self.assertIn(f"<t:{int(ready.timestamp())}:f>", text)
            self.assertIn("bot-online time remaining when checked · pauses offline", text)

    async def test_multiple_players_can_fabricate_independent_projects_concurrently(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = ImperialStarDestroyerCog(bot)
            first = bot.economy.user(1, 123, 100)
            second = bot.economy.user(1, 456, 100)
            first["donuts"] = 10**19
            second["donuts"] = 10**19
            first_interaction = _FakeInteraction(user_id=123)
            second_interaction = _FakeInteraction(user_id=456)
            with patch.object(cog, "_respond_art"):
                await ImperialStarDestroyerCog.fabricate.callback(
                    cog,
                    first_interaction,
                    app_commands.Choice(name="Kuat Orbital Shipyard Core", value="shipyard"),
                )
                await ImperialStarDestroyerCog.fabricate.callback(
                    cog, second_interaction, app_commands.Choice(name="Quadanium Hull Sections", value="hull")
                )
            self.assertEqual(isd_state.normalize(first)["components"]["shipyard"]["status"], "fabricating")
            self.assertEqual(isd_state.normalize(second)["components"]["hull"]["status"], "fabricating")
            self.assertEqual(len(cog._projects(1)), 2)

    async def test_each_player_can_fabricate_two_different_components_but_not_three(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = ImperialStarDestroyerCog(bot)
            user = bot.economy.user(1, 123, 100)
            user["donuts"] = 10**19
            with patch.object(cog, "_respond_art"):
                for key in ("shipyard", "hull"):
                    interaction = _FakeInteraction()
                    await ImperialStarDestroyerCog.fabricate.callback(
                        cog,
                        interaction,
                        app_commands.Choice(name=isd_state.COMPONENTS[key]["name"], value=key),
                    )
                third = _FakeInteraction()
                await ImperialStarDestroyerCog.fabricate.callback(
                    cog,
                    third,
                    app_commands.Choice(name=isd_state.COMPONENTS["reactor"]["name"], value="reactor"),
                )
            state = isd_state.normalize(user)
            self.assertEqual(
                {key for key, part in state["components"].items() if part["status"] == "fabricating"},
                {"shipyard", "hull"},
            )
            self.assertEqual(state["components"]["reactor"]["status"], "none")
            self.assertIn("up to 2", third.response.calls[0]["embed"].description)
            state["components"]["shipyard"]["ready_at"] = (
                isd_state.utcnow() - dt.timedelta(seconds=1)
            ).isoformat()
            self.assertTrue(isd_state.settle(user))
            self.assertEqual(state["components"]["shipyard"]["status"], "ready")
            self.assertEqual(state["components"]["hull"]["status"], "fabricating")
            with patch.object(cog, "_respond_art"):
                await ImperialStarDestroyerCog.fabricate.callback(
                    cog,
                    _FakeInteraction(),
                    app_commands.Choice(name=isd_state.COMPONENTS["reactor"]["name"], value="reactor"),
                )
            self.assertEqual(state["components"]["reactor"]["status"], "fabricating")

    async def test_contribution_can_target_one_of_multiple_allied_projects(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = ImperialStarDestroyerCog(bot)
            contributor = bot.economy.user(1, 123, 100)
            first = bot.economy.user(1, 456, 100)
            second = bot.economy.user(1, 789, 100)
            contributor["donuts"] = 2000000
            isd_state.normalize(first)["components"]["shipyard"]["status"] = "ready"
            isd_state.normalize(second)["components"]["hull"]["status"] = "ready"
            coalitions.state(bot.economy.store.load(1))["groups"]["1"] = {
                "members": [123, 456, 789],
                "expires_at": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)).isoformat(),
            }
            interaction = _FakeInteraction(user_id=123)
            await ImperialStarDestroyerCog.contribute.callback(cog, interaction, "1m", _FakeUser(789))
            self.assertEqual(isd_state.normalize(first)["project_funds"], 0)
            self.assertEqual(isd_state.normalize(second)["project_funds"], 1000000)
            self.assertEqual(contributor["donuts"], 1000000)

    def test_bwing_counter_package_cost_is_forty_five_quadrillion(self) -> None:
        self.assertEqual(int(DEFAULTS["economy"]["isd_counter_cost"]), 45000000000000000)

    def test_first_shot_price_is_exactly_fifteen_quintillion(self) -> None:
        component_total = sum((int(spec["cost"]) for spec in isd_state.COMPONENTS.values()))
        launch_total = int(DEFAULTS["economy"]["isd_launch_cost"]) * len(isd_state.COMPONENTS)
        total = (
            component_total
            + launch_total
            + int(DEFAULTS["economy"]["isd_assembly_cost"])
            + int(DEFAULTS["economy"]["isd_cinder_cost"])
        )
        self.assertEqual(component_total, 8500000000000000000)
        self.assertEqual(total, 15000000000000000000)

    def test_component_and_counter_timers_settle_restart_safely(self) -> None:
        user = {"imperial_star_destroyer": isd_state.default_state()}
        state = isd_state.normalize(user)
        past = (isd_state.utcnow() - dt.timedelta(seconds=1)).isoformat()
        state["components"]["shipyard"].update(status="fabricating", ready_at=past)
        state["counter_building_until"] = past
        self.assertTrue(isd_state.settle(user))
        self.assertEqual(state["components"]["shipyard"]["status"], "ready")
        self.assertEqual(state["counter_stock"], 1)
        self.assertIsNone(state["counter_building_until"])

    def test_cinder_wipes_all_exposed_state_but_restores_raven_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = ImperialStarDestroyerCog(bot)
            first = bot.economy.user(1, 123, 100)
            second = bot.economy.user(1, 456, 100)
            first.update(
                donuts=10**18,
                bank=99,
                deep_vault_balance=77,
                inventory={"lock": 3},
                plushies={"ace": 1},
                fish={"tuna": 8},
                badges={"winner": "now"},
                titles={"emperor": 1},
                crown=True,
                vault_tier=7,
            )
            first["thor"]["operational"] = True
            first["vehicles"]["b2"]["owned"] = True
            bunker = continuity_state.normalize(first)
            bunker.update(owned=True, funds=15000000000000, items={"lock": 2}, plushies={"ace": 1})
            second.update(
                donuts=500, bank=200, deep_vault_owned=True, deep_vault_balance=300, titles={"victim": 1}
            )
            doc = bot.economy.store.load(1)
            doc["countries"] = {"territories": {"prt": {"owner": 123}}, "history": []}
            doc["coalitions"] = {"groups": {"1": {"members": [123, 456]}}}
            doc["loans"] = [{"borrower": 456, "owed": 1000}]
            space_state.galaxy(doc)["claims"]["mars"] = 123
            space_state.normalize(first)["cargo"]["donuts"] = 999
            snapshot = copy.deepcopy(bunker)
            record = {"guild_id": 1, "raven_snapshots": {"123": snapshot}}
            cog._apply_cinder_wipe(doc, record)
            wiped = doc["users"]["123"]
            self.assertEqual(wiped["donuts"], 0)
            self.assertEqual(wiped["bank"], 0)
            self.assertEqual(wiped["deep_vault_balance"], 0)
            self.assertFalse(wiped["deep_vault_owned"])
            self.assertEqual(wiped["inventory"], {})
            self.assertEqual(wiped["plushies"], {})
            self.assertEqual(wiped["fish"], {})
            self.assertEqual(wiped["badges"], {})
            self.assertNotIn("titles", wiped)
            self.assertFalse(wiped["crown"])
            self.assertFalse(wiped["vehicles"]["b2"]["owned"])
            self.assertFalse(wiped["thor"]["operational"])
            self.assertTrue(wiped["continuity"]["owned"])
            self.assertEqual(wiped["continuity"]["funds"], 15000000000000)
            self.assertEqual(wiped["continuity"]["items"], {"lock": 2})
            self.assertEqual(doc["countries"]["territories"], {})
            self.assertEqual(doc["coalitions"]["groups"], {})
            self.assertEqual(doc["loans"], [])
            self.assertEqual(space_state.galaxy(doc)["claims"], {})
            self.assertEqual(space_state.normalize(wiped)["cargo"]["donuts"], 0)
            self.assertEqual(doc["users"]["456"]["donuts"], 0)

    async def test_online_window_decrements_only_when_tick_is_explicitly_advanced(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = ImperialStarDestroyerCog(bot)
            isd_state.windows(bot.economy.store.load(1))["TEST"] = {
                "kind": "component",
                "guild_id": 1,
                "builder": 123,
                "component": "shipyard",
                "channel_id": 1,
                "remaining_seconds": 60.0,
                "interceptors": [],
            }
            await cog._advance_windows(1, 15.0)
            self.assertEqual(isd_state.windows(bot.economy.store.load(1))["TEST"]["remaining_seconds"], 45.0)

    def test_thor_risks_only_one_ground_isd_part_at_ten_percent_boundaries(self) -> None:
        for roll, destroyed in ((1, True), (10, True), (11, False), (100, False)):
            with self.subTest(roll=roll):
                with tempfile.TemporaryDirectory() as raw:
                    bot = _FakeBot(Path(raw))
                    victim = bot.economy.user(1, 456, 100)
                    state = isd_state.normalize(victim)
                    state["components"]["shipyard"].update(
                        status="fabricating", ready_at=(isd_state.utcnow() + dt.timedelta(days=1)).isoformat()
                    )
                    state["components"]["reactor"].update(status="ready")
                    state["components"]["command"].update(status="launching", window_id="ISD-L-TEST")
                    state["components"]["hull"].update(status="orbit")
                    state["components"]["cinder"].update(status="assembled")
                    state["counter_stock"] = 2
                    components_before = copy.deepcopy(state["components"])
                    with (
                        patch("cogs.thor.random.choice", side_effect=lambda parts: parts[0]),
                        patch("cogs.thor.random.randint", return_value=roll),
                    ):
                        summary = ThorCog._wipe_target(victim, bot.config.for_guild(1))
                    self.assertEqual(summary["ground_isd_at_risk"], 1)
                    self.assertEqual(summary["ground_isd_destroyed"], int(destroyed))
                    self.assertEqual(
                        state["components"]["shipyard"]["status"], "none" if destroyed else "fabricating"
                    )
                    for key in ("reactor", "command", "hull", "cinder"):
                        self.assertEqual(state["components"][key], components_before[key])
                    self.assertEqual(state["counter_stock"], 0)

    def test_thor_cannot_risk_orbiting_or_assembled_isd_parts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            victim = bot.economy.user(1, 456, 100)
            state = isd_state.normalize(victim)
            state["components"]["command"].update(status="launching", window_id="ISD-L-TEST")
            state["components"]["hull"].update(status="orbit")
            state["components"]["cinder"].update(status="assembled")
            components_before = copy.deepcopy(state["components"])
            with patch("cogs.thor.random.choice") as choice:
                summary = ThorCog._wipe_target(victim, bot.config.for_guild(1))
            choice.assert_not_called()
            self.assertEqual(summary["ground_isd_at_risk"], 0)
            self.assertEqual(summary["ground_isd_destroyed"], 0)
            self.assertEqual(state["components"], components_before)


class ContinuityCommandIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_raven_rock_accepts_hundred_trillion_but_rejects_overflow(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = ContinuityCog(bot)
            user = bot.economy.user(1, 123, 100)
            user.update(donuts=100000000000000, bank=0)
            bunker = continuity_state.normalize(user)
            bunker["owned"] = True
            store = _FakeInteraction(user_id=123)
            await ContinuityCog.store.callback(
                cog, store, app_commands.Choice(name="Funds", value="funds"), None, "all"
            )
            self.assertEqual(user["donuts"], 0)
            self.assertEqual(bunker["transfer_payload"], {"kind": "funds", "amount": 100000000000000})
            self.assertIn("Secure **funds** transfer started", store.response.calls[0]["embed"].description)
            bunker["transfer_until"] = (continuity_state.utcnow() - dt.timedelta(seconds=1)).isoformat()
            self.assertTrue(continuity_state.settle(user, bot.config.for_guild(1)))
            self.assertEqual(bunker["funds"], 100000000000000)
            user["donuts"] = 1
            overflow = _FakeInteraction(user_id=123)
            await ContinuityCog.store.callback(
                cog, overflow, app_commands.Choice(name="Funds", value="funds"), None, "1"
            )
            self.assertTrue(overflow.response.calls[0]["ephemeral"])
            self.assertIn(
                "Protected funds cannot exceed 100 trillion", overflow.response.calls[0]["embed"].description
            )
            self.assertEqual(user["donuts"], 1)


class AutonomousHelperIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_helper_catches_up_offline_into_wallet_and_promotes_by_tier(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            cfg = bot.config.for_guild(1)
            user = bot.economy.user(1, 123, 100)
            employment = cog._employment(user)
            employment["active"] = "logistics"
            record = cog._career_record(employment, "logistics")
            record["xp"] = 14
            user["android21_helper"] = True
            now = dt.datetime(2026, 9, 23, 12, 0, tzinfo=dt.timezone.utc)
            user["android21_helper_at"] = (now - dt.timedelta(minutes=30)).isoformat()
            gross, garnished, shifts, changed = cog._settle_android21_helper(cfg, 1, 123, user, now=now)
            expected = 275000 * 70 // 100 + 2 * (1100000 * 70 // 100)
            self.assertTrue(changed)
            self.assertEqual((gross, garnished, shifts), (expected, 0, 3))
            self.assertEqual(user["donuts"], 100 + expected)
            self.assertEqual(user["bank"], 0)
            self.assertEqual(record["xp"], 17)
            self.assertEqual(record["shifts"], 3)
            self.assertEqual(record["correct"], 3)
            self.assertEqual(user["android21_helper_shifts"], 3)
            self.assertEqual(user["android21_helper_earned"], expected)
            self.assertEqual(dt.datetime.fromisoformat(user["android21_helper_at"]), now)
            self.assertEqual(cog._settle_android21_helper(cfg, 1, 123, user, now=now), (0, 0, 0, False))

    async def test_helper_purchase_is_singleton_and_manual_work_remains_available(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            price = DEFAULTS["economy"]["android21_helper_price"]
            user = bot.economy.user(1, 123, 100)
            user["donuts"] = price + 123
            cog._employment(user)["active"] = "engineering"
            purchase = _FakeInteraction(user_id=123)
            await EconomyCog.buy.callback(cog, purchase, "android21helper", 1)
            self.assertTrue(user["android21_helper"])
            self.assertEqual(user["donuts"], 123)
            self.assertIsNotNone(user["android21_helper_at"])
            purchase_call = purchase.response.calls[0]
            self.assertIn("Autonomous helper activated", purchase_call["embed"].title)
            self.assertIn("wallet", purchase_call["embed"].description)
            self.assertIsNotNone(purchase_call.get("file"))
            self.assertEqual(purchase_call["file"].filename, "android21-autonomous-helper.png")
            work = _FakeInteraction(user_id=123)
            await EconomyCog.work.callback(cog, work)
            self.assertIn("Workplace situation", work.response.calls[0]["embed"].description)
            self.assertIsNotNone(work.response.calls[0]["view"])
            duplicate = _FakeInteraction(user_id=123)
            await EconomyCog.buy.callback(cog, duplicate, "android21helper", 1)
            self.assertIn("already own", duplicate.response.calls[0]["embed"].description)

    async def test_licence_manual_history_purchase_and_flat_bonus(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            user = bot.economy.user(1, 123, 0)
            employment = user["employment"]
            employment["active"] = "engineering"
            record = cog._career_record(employment, "engineering")
            record.update(xp=200, shifts=15, correct=14)
            user["android21_helper_shifts"] = 5
            employment.pop("manual_shifts")
            self.assertEqual(cog._employment(user)["manual_shifts"], 10)
            self.assertEqual(cog._employment(user)["manual_shifts"], 10)
            user["donuts"] = CAREER_LICENCES[0][1]
            purchase = _FakeInteraction()
            await EconomyCog.job_certify.callback(cog, purchase)
            self.assertEqual(employment["career_licence_tier"], 0)
            await purchase.response.calls[-1]["view"].commit(_FakeInteraction())
            self.assertEqual(employment["career_licence_tier"], 1)
            self.assertEqual(user["donuts"], 0)
            scenario = {"success": "Correct", "failure": "Wrong"}
            correct_result = await cog._complete_work_shift(_FakeInteraction(), "engineering", scenario, True)
            first_bonus = CAREER_LICENCES[0][2]
            self.assertIn(f"{first_bonus:,}", correct_result.description)
            self.assertEqual(employment["manual_shifts"], 11)
            self.assertEqual(user["donuts"], 33000000 * 125 // 100 + first_bonus)
            before = user["donuts"]
            wrong_result = await cog._complete_work_shift(_FakeInteraction(), "engineering", scenario, False)
            self.assertNotIn("licence:", wrong_result.description)
            self.assertEqual(user["donuts"] - before, 33000000 * 70 // 100)

    async def test_licence_gates_are_sequential_and_helper_does_not_earn_bonus(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            cfg = bot.config.for_guild(1)
            user = bot.economy.user(1, 123, 0)
            employment = cog._employment(user)
            employment["active"] = "medicine"
            record = cog._career_record(employment, "medicine")
            record["xp"] = 200
            employment["manual_shifts"] = 19
            user["donuts"] = sum((spec[1] for spec in CAREER_LICENCES))
            purchase = _FakeInteraction()
            await EconomyCog.job_certify.callback(cog, purchase)
            await purchase.response.calls[-1]["view"].commit(_FakeInteraction())
            self.assertEqual(employment["career_licence_tier"], 1)
            blocked = _FakeInteraction()
            await EconomyCog.job_certify.callback(cog, blocked)
            self.assertEqual(employment["career_licence_tier"], 1)
            self.assertIn("20 cumulative manual shifts", blocked.response.calls[0]["embed"].description)
            user["android21_helper"] = True
            now = dt.datetime.now(dt.timezone.utc)
            user["android21_helper_at"] = (now - dt.timedelta(minutes=20)).isoformat()
            before = user["donuts"]
            gross, _, shifts, _ = cog._settle_android21_helper(cfg, 1, 123, user, now=now)
            self.assertEqual(shifts, 2)
            self.assertEqual(gross, 2 * 33000000 * 70 // 100)
            self.assertEqual(user["donuts"] - before, gross)
            self.assertEqual(employment["manual_shifts"], 19)

    async def test_helper_cannot_be_bought_during_an_open_manual_shift(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            price = DEFAULTS["economy"]["android21_helper_price"]
            user = bot.economy.user(1, 123, 100)
            user["donuts"] = price
            employment = cog._employment(user)
            employment["active"] = "medicine"
            employment["pending_until"] = (
                dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60)
            ).isoformat()
            interaction = _FakeInteraction(user_id=123)
            await EconomyCog.buy.callback(cog, interaction, "android21helper", 1)
            self.assertFalse(user["android21_helper"])
            self.assertEqual(user["donuts"], price)
            self.assertIn("Finish your open", interaction.response.calls[0]["embed"].description)


class FishingCommandIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_removed_freezer_cannot_be_bought_and_old_effect_does_not_pause_rot(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            bot.guilds = [SimpleNamespace(id=1)]
            economy = object.__new__(EconomyCog)
            economy.bot, economy.econ = (bot, bot.economy)
            user = bot.economy.user(1, 123, 100)
            user.update(
                donuts=1000000,
                bucket_freeze_until="2099-01-01T00:00:00+00:00",
                fish={FISH[0].id: 2},
                fish_meta={FISH[0].id: {"age": 0, "w1": False, "w2": False}},
            )
            interaction = _FakeInteraction()
            await EconomyCog.buy.callback(economy, interaction, "freezer", 1)
            self.assertNotIn("freezer", CONSUMABLES)
            self.assertNotIn("freezer", ITEM_HOLD_CAPS)
            self.assertEqual(user["donuts"], 1000000)
            fishing = object.__new__(Fishing)
            fishing.bot, fishing.econ = (bot, bot.economy)
            await Fishing.rot_loop.coro(fishing)
            self.assertEqual(user["fish_meta"][FISH[0].id]["age"], 1.0)
            self.assertEqual(user["fish"][FISH[0].id], 2)
            self.assertNotIn("bucket_freeze_until", user)

    async def test_bestiary_shows_surface_codex_and_all_abyss_discoveries(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = Fishing(bot)
            user = bot.economy.user(1, 123, 100)
            user["fish_seen"] = {FISH[0].id: True}
            user["abyss_seen"] = {ABYSS_CODEX_IDS[0]: True, "abyssdebris": True, "voidcaller": True}
            interaction = _FakeInteraction(user_id=123)
            await Fishing.bestiary.callback(cog, interaction, None)
            call = interaction.response.calls[0]
            embed = call["embed"]
            _assert_embed_valid(self, embed)
            self.assertTrue(call["ephemeral"])
            self.assertIn(f"Surface Bestiary — 1/{len(FISH)}", embed.description)
            self.assertIn(f"Abyss Codex — 1/{len(ABYSS_CODEX_IDS)}", embed.description)
            abyss_fields = {field.name: field.value for field in embed.fields}
            codex_value = next(
                (value for name, value in abyss_fields.items() if name.startswith("🌌 Abyss Codex"))
            )
            bonus_value = abyss_fields["🔎 Other Abyss Discoveries"]
            esoteric_value = next(
                (value for name, value in abyss_fields.items() if "Esoteric Depths" in name)
            )
            for fid in ABYSS_CODEX_IDS:
                self.assertIn(FISH_BY_ID[fid].name, codex_value)
            for fish in ABYSS_FISH:
                self.assertIn(fish.name, codex_value + bonus_value + esoteric_value)
            self.assertIn("✅", bonus_value)
            self.assertIn("not required", bonus_value)
            self.assertIn("Optional trophies", esoteric_value)


class StrategicVehicleIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_arsenal_is_private_lossless_and_requester_bound(self) -> None:
        from szofie import deathstar, ui

        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            user = bot.economy.user(1, 123, 100)
            future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=4)).isoformat()
            for model in VEHICLE_CATALOG:
                state = cog._vehicle_state(user, model)
                state.update(owned=True, ammo=1, loading_qty=2, loading_until=future)
            user["vehicles"]["apache"].update(
                comanche_upgraded=True, comanche_damaged=True, comanche_repair_cost=150000000
            )
            user["vehicles"]["b2"].update(armed=True)
            thor = thor_state.normalize(user)
            thor.update(rods=3, chambered=True, resupply_until=future, resupply_qty=2, gbi_stock=2)
            isd = isd_state.normalize(user)
            isd.update(counter_stock=2, counter_building_until=future)
            station = deathstar.normalize(user)
            station.update(squadron_stock=2, squadron_until=future, charge_until=future)
            for state in (thor, isd, station):
                for part in state["components"].values():
                    part.update(status="fabricating", ready_at=future)
            space = space_state.normalize(user)
            space.update(xwing_owned=True, xwing_ammo=3, cutlass_build_until=future, transport_owned=True)
            space["cargo"]["vehicles"]["a10"] = copy.deepcopy(user["vehicles"]["a10"])
            interaction = _FakeInteraction()
            await EconomyCog.arsenal.callback(cog, interaction)
            self.assertEqual(interaction.response.deferred, {"thinking": True, "ephemeral": True})
            self.assertEqual(interaction.response.initial_calls, [])
            call = interaction.original_edits[0]
            pager = call["view"]
            self.assertIsInstance(pager, ui.Paginator)
            self.assertGreater(len(pager.pages), 1)
            self.assertEqual(pager.author_id, 123)
            text = "\n".join(
                (field.name + "\n" + field.value for page in pager.pages for field in page.fields)
            )
            plain_text = text.replace("**", "")
            for spec in VEHICLE_CATALOG.values():
                self.assertIn(spec["short"], text)
            for catalog in (thor_state.COMPONENTS, isd_state.COMPONENTS, deathstar.COMPONENTS):
                for spec in catalog.values():
                    self.assertIn(spec.get("short", spec["name"]), text)
            for phrase in (
                "B-wing interception packages: 2/2",
                "X-wing proton torpedoes: 3/3",
                "Alliance interception squadrons: 2/3",
                "ISD cargo — stowed vehicles",
                "RAH-66 grounded",
                "Magazine batch ×2",
                "loading · completes",
            ):
                self.assertIn(phrase, plain_text)
            self.assertNotIn("/vehicle hangar", text)
            for page in pager.pages:
                _assert_embed_valid(self, page)
            self.assertFalse(await pager.interaction_check(_FakeInteraction(user_id=456)))
            self.assertTrue(await pager.interaction_check(interaction))
            pager.stop()

    async def test_arsenal_earth_and_space_entries_use_aligned_labeled_rows(self) -> None:
        from szofie import space_fleet

        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            user = bot.economy.user(1, 123, 100)
            future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)).isoformat()
            cog._vehicle_state(user, "a10").update(owned=True, ammo=1, loading_until=future, loading_qty=1)
            hull = space_fleet.ship(user, "arquitens")
            hull.update(
                owned=True,
                deployed=True,
                location="mars",
                payload=2,
                mission={
                    "kind": "escort",
                    "body": "mars",
                    "ready_at": future,
                    "responses_remaining": 2,
                    "objectives": ["mine", "research"],
                },
            )
            interaction = _FakeInteraction()
            await EconomyCog.arsenal.callback(cog, interaction)
            pager = interaction.original_edits[0]["view"]
            try:
                fields = [field for page in pager.pages for field in page.fields]
                self.assertTrue(all((not field.inline for field in fields)))
                earth = next((field.value for field in fields if field.name.endswith("A-10C Thunderbolt II")))
                orbital = next(
                    (
                        field.value
                        for field in fields
                        if field.name.endswith(space_fleet.CRAFT["arquitens"].name)
                    )
                )
                for value in (earth, orbital):
                    self.assertTrue(value.startswith("**Status:**"))
                    self.assertIn("**Ammunition:**", value)
                self.assertIn("**Loading:**", earth)
                for phrase in (
                    "**Location:** Mars",
                    "**Mission:** escort · mars",
                    "**Patrol responses:** 2",
                    "**Objectives:** mine, research",
                    "**Mission completion:** <t:",
                ):
                    self.assertIn(phrase, orbital)
                air = next((field.value for field in fields if field.name.endswith("Air defenses")))
                self.assertIn("**Radar AA**\n**Status:** 🟠 INACTIVE — EMPTY", air)
                text = "\n".join((field.value for field in fields))
                self.assertIn("**B-wing interception packages:**", text)
                self.assertIn("**Alliance interception squadrons:**", text)
                for page in pager.pages:
                    _assert_embed_valid(self, page)
            finally:
                pager.stop()

    async def test_arsenal_settles_and_persists_orbital_builds_before_reporting(self) -> None:
        from szofie import deathstar

        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            user = bot.economy.user(1, 123, 100)
            past = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)).isoformat()
            isd = isd_state.normalize(user)
            isd["components"]["shipyard"].update(status="fabricating", ready_at=past)
            isd["counter_building_until"] = past
            station = deathstar.normalize(user)
            station.update(squadron_until=past, charge_until=past)
            space_state.normalize(user)["xwing_build_until"] = past
            interaction = _FakeInteraction()
            await EconomyCog.arsenal.callback(cog, interaction)
            saved = Storage(bot.economy.store.directory).load(1)["users"]["123"]
            self.assertEqual(saved["imperial_star_destroyer"]["components"]["shipyard"]["status"], "ready")
            self.assertEqual(saved["imperial_star_destroyer"]["counter_stock"], 1)
            self.assertEqual(saved["death_star"]["squadron_stock"], 1)
            self.assertTrue(saved["death_star"]["charged"])
            self.assertTrue(saved["space"]["xwing_owned"])
            interaction.original_edits[0]["view"].stop()

    async def test_arsenal_moon_income_is_persisted_and_logged_once(self) -> None:
        from szofie import deathstar

        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            bot.ledger.record = AsyncMock()
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            user = bot.economy.user(1, 123, 100)
            current = dt.datetime.now(dt.timezone.utc)
            station = deathstar.normalize(user)
            station.update(operational=True, income_at=(current - dt.timedelta(days=1)).isoformat())
            with patch("cogs.economy._now", return_value=current):
                for _ in range(2):
                    interaction = _FakeInteraction()
                    await EconomyCog.arsenal.callback(cog, interaction)
                    interaction.original_edits[0]["view"].stop()
            payout = deathstar.gdp(station) // 80
            self.assertEqual(user["donuts"], 100 + payout)
            saved = Storage(bot.economy.store.directory).load(1)["users"]["123"]
            self.assertEqual(saved["donuts"], user["donuts"])
            bot.ledger.record.assert_awaited_once_with(
                1, 123, payout, "deathstar-production", after=deathstar.available(user)
            )

    async def test_arsenal_field_pages_preserve_long_fields_and_embed_limits(self) -> None:
        from szofie import ui

        value = "\n".join((f"Vehicle {index}: " + "x" * 70 for index in range(90)))
        fields = [("Cargo", value, False)] + [(f"Extra {i}", "y" * 800, False) for i in range(30)]
        pages = ui.field_pages("Complete arsenal", "Private report", fields)
        reconstructed = "".join(
            (field.value for page in pages for field in page.fields if field.name.startswith("Cargo"))
        )
        self.assertEqual(reconstructed, value)
        self.assertEqual(
            sum((field.name.startswith("Extra ") for page in pages for field in page.fields)), 30
        )
        for page in pages:
            _assert_embed_valid(self, page)

    async def test_ordinary_player_can_build_both_frontline_chassis(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            user = bot.economy.user(1, 123, 100)
            user["donuts"] = 2000000000
            cog._vehicle_state(user, "m1a2").update(owned=True)
            interaction = _FakeInteraction(user_id=123)
            await EconomyCog.vehicle_build.callback(
                cog, interaction, app_commands.Choice(name="Leopard 2A7A1", value="leopard2a7")
            )
            call = interaction.response.calls[0]
            self.assertIn("construction begun", call["embed"].title)
            self.assertEqual(user["donuts"], 1250000000)
            self.assertTrue(user["vehicles"]["m1a2"]["owned"])
            self.assertFalse(user["vehicles"]["leopard2a7"]["owned"])
            self.assertIsNotNone(user["vehicles"]["leopard2a7"]["building_until"])
            attached = call.get("file")
            if attached is not None:
                attached.close()

    async def test_leopard_breach_uses_dm63_and_destroys_thirty_percent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            attacker = bot.economy.user(1, 123, 100)
            bot.economy.user(1, 456, 100)
            cog._vehicle_state(attacker, "leopard2a7").update(owned=True, ammo=2)
            doc = bot.economy.store.load(1)
            current = countries.now().isoformat()
            territory = {
                "owner": 456,
                "acquired_at": current,
                "last_collected_at": current,
                "fortification": 100,
                "development_level": 7,
            }
            countries.state(doc)["territories"]["fji"] = territory
            interaction = _FakeInteraction(user_id=123)
            await cog._armored_execute(interaction, _FakeUser(456), "fji", "leopard2a7")
            self.assertEqual(territory["fortification"], 70)
            self.assertEqual(territory["development_level"], 7)
            self.assertEqual(territory["m1a2_breached_by"], 123)
            self.assertEqual(territory["armored_breach_model"], "leopard2a7")
            self.assertEqual(attacker["vehicles"]["leopard2a7"]["ammo"], 1)
            self.assertIn(
                "LEOPARD 2A7A1 ARMORED BREACH CONFIRMED", interaction.followup.calls[0]["embed"].title
            )
            attached = interaction.followup.calls[0].get("file")
            if attached is not None:
                attached.close()

    async def test_leopard_javelin_track_has_no_trophy_reroll(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            attacker = bot.economy.user(1, 123, 100)
            victim = bot.economy.user(1, 456, 100)
            cog._vehicle_state(attacker, "leopard2a7").update(owned=True, ammo=3)
            cog._vehicle_state(victim, "javelin").update(owned=True, ammo=1)
            doc = bot.economy.store.load(1)
            current = countries.now().isoformat()
            territory = {
                "owner": 456,
                "acquired_at": current,
                "last_collected_at": current,
                "fortification": 100,
                "development_level": 0,
            }
            countries.state(doc)["territories"]["fji"] = territory
            interaction = _FakeInteraction(user_id=123)
            with patch("cogs.economy.random.randint", side_effect=[1, 100]) as rolls:
                await cog._armored_execute(interaction, _FakeUser(456), "fji", "leopard2a7")
            self.assertEqual(rolls.call_count, 2)
            self.assertEqual(territory["fortification"], 100)
            self.assertNotIn("m1a2_breached_by", territory)
            self.assertEqual(attacker["vehicles"]["leopard2a7"]["ammo"], 2)
            self.assertEqual(victim["vehicles"]["javelin"]["ammo"], 0)
            embed = interaction.followup.calls[0]["embed"]
            self.assertIn("ARMORED ASSAULT STOPPED", embed.title)
            self.assertIn("no Trophy reroll", embed.description)
            attached = interaction.followup.calls[0].get("file")
            if attached is not None:
                attached.close()

    async def test_m1a2_breach_reduces_current_fortification_and_marks_attacker(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            attacker = bot.economy.user(1, 123, 100)
            victim = bot.economy.user(1, 456, 100)
            cog._vehicle_state(attacker, "m1a2").update(owned=True, ammo=2)
            doc = bot.economy.store.load(1)
            current = countries.now().isoformat()
            territory = {
                "owner": 456,
                "acquired_at": current,
                "last_collected_at": current,
                "fortification": 100,
                "development_level": 7,
            }
            countries.state(doc)["territories"]["fji"] = territory
            interaction = _FakeInteraction(user_id=123)
            await cog._m1a2_execute(interaction, _FakeUser(456), "fji")
            self.assertEqual(territory["fortification"], 75)
            self.assertEqual(territory["development_level"], 7)
            self.assertEqual(territory["m1a2_breached_by"], 123)
            self.assertIsNotNone(territory["m1a2_breached_until"])
            self.assertEqual(attacker["vehicles"]["m1a2"]["ammo"], 1)
            self.assertIn("ARMORED BREACH CONFIRMED", interaction.followup.calls[0]["embed"].title)
            attached = interaction.followup.calls[0].get("file")
            if attached is not None:
                attached.close()

    async def test_javelin_stop_can_damage_m1a2_without_erasing_remaining_ammo(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            attacker = bot.economy.user(1, 123, 100)
            victim = bot.economy.user(1, 456, 100)
            cog._vehicle_state(attacker, "m1a2").update(owned=True, ammo=3)
            cog._vehicle_state(victim, "javelin").update(owned=True, ammo=2)
            doc = bot.economy.store.load(1)
            current = countries.now().isoformat()
            territory = {
                "owner": 456,
                "acquired_at": current,
                "last_collected_at": current,
                "fortification": 100,
                "development_level": 0,
            }
            countries.state(doc)["territories"]["fji"] = territory
            interaction = _FakeInteraction(user_id=123)
            with patch("cogs.economy.random.randint", side_effect=[1, 100, 1, 150]):
                await cog._m1a2_execute(interaction, _FakeUser(456), "fji")
            tank = attacker["vehicles"]["m1a2"]
            self.assertEqual(territory["fortification"], 100)
            self.assertNotIn("m1a2_breached_by", territory)
            self.assertTrue(tank["m1a2_damaged"])
            self.assertEqual(tank["m1a2_repair_cost"], 150000000)
            self.assertEqual(tank["ammo"], 2)
            self.assertEqual(victim["vehicles"]["javelin"]["ammo"], 1)
            self.assertIn("ARMORED ASSAULT STOPPED", interaction.followup.calls[0]["embed"].title)
            attached = interaction.followup.calls[0].get("file")
            if attached is not None:
                attached.close()

    async def test_sr71_success_grants_unified_targeting_and_defense_report(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            attacker = cog.user(1, 123)
            victim = cog.user(1, 456)
            attacker["vehicles"]["sr71"].update(owned=True)
            attacker["vehicles"]["lrhw"].update(owned=True, ammo=1)
            victim.update(
                aa_rockets_loaded=2,
                aa_rockets_stock=3,
                s400_owned=True,
                s400_interceptors=1,
                deep_vault_owned=True,
                deep_vault_balance=5000,
            )
            victim["vehicles"]["b52"].update(owned=True, ammo=1)
            victim["vehicles"]["aegis"].update(owned=True, ammo=2, bmd_owned=True, sm3_ammo=1)
            victim["vehicles"]["p8"].update(owned=True, ammo=1)
            victim["vehicles"]["patriot"].update(owned=True, ammo=1)
            build_ready = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
            victim["vehicles"]["xb70"].update(owned=False, building_until=build_ready.isoformat())
            thor_state.normalize(victim)["gbi_stock"] = 1
            interaction = _FakeInteraction(user_id=123)
            with patch("cogs.economy.random.randint", return_value=100):
                await cog._sr71_execute(interaction, _FakeUser(456))
            self.assertTrue(attacker["vehicles"]["sr71"]["owned"])
            self.assertEqual(victim["s400_interceptors"], 0)
            self.assertEqual(victim["vehicles"]["patriot"]["ammo"], 1)
            self.assertIsNotNone(attacker["vehicles"]["sr71"]["last_deploy_at"])
            self.assertEqual(len(interaction.followup.calls), 2)
            self.assertIn("RECONNAISSANCE COMPLETE", interaction.followup.calls[0]["embed"].title)
            classified = interaction.followup.calls[1]
            self.assertTrue(classified.get("ephemeral"))
            report = "\n".join((field.value for field in classified["embed"].fields))
            for label in (
                "Radar AA",
                "AA Shield",
                "S-400",
                "Flight III Aegis",
                "P-8A Poseidon",
                "Patriot PAC-3 MSE",
                "Aegis BMD",
                "GBI/EKV",
            ):
                self.assertIn(label, report)
            self.assertIn("5,000", report)
            self.assertIn("XB-70 Valkyrie", report)
            self.assertIn(
                "Dark Eagle construction tracks", {field.name for field in classified["embed"].fields}
            )
            self.assertIn(str(456), attacker["recon_targets"])
            construction = attacker["sr71_construction_intel"]["456"]
            self.assertEqual(set(construction["builds"]), {"xb70"})
            bot.cogs["Economy"] = cog
            autocomplete = _FakeInteraction(user_id=123)
            autocomplete.client = bot
            autocomplete.namespace = SimpleNamespace(vehicle="lrhw", target=_FakeUser(456))
            choices = await _strategic_objective_autocomplete(autocomplete, "")
            self.assertEqual([choice.value for choice in choices], ["xb70"])
            self.assertIn("Recon track", choices[0].name)
            self.assertIsNone(cog._mission_preflight(1, 123, "lrhw", _FakeUser(456), "xb70"))
            self.assertIn("not identified", cog._mission_preflight(1, 123, "lrhw", _FakeUser(456), "b52"))
            vehicle_picker = _FakeInteraction(user_id=123)
            vehicle_picker.client = bot
            vehicle_picker.namespace = SimpleNamespace(vehicle="a10", target=_FakeUser(456))
            choices = await _strategic_objective_autocomplete(vehicle_picker, "")
            self.assertEqual([choice.value for choice in choices], ["b52"])
            self.assertIn("Target package", choices[0].name)
            for call in interaction.followup.calls:
                attached = call.get("file")
                if attached is not None:
                    attached.close()

    async def test_u2_success_grants_completed_vehicle_and_construction_targeting(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            attacker = cog.user(1, 123)
            victim = cog.user(1, 456)
            attacker["vehicles"]["u2"].update(owned=True)
            victim.update(deep_vault_owned=True, deep_vault_balance=9000)
            victim["vehicles"]["f15e"].update(owned=True, ammo=1)
            victim["vehicles"]["xb70"]["building_until"] = (
                dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
            ).isoformat()
            interaction = _FakeInteraction(user_id=123)
            await cog._u2_execute(interaction, _FakeUser(456))
            self.assertIn("456", attacker["recon_targets"])
            self.assertEqual(set(attacker["sr71_construction_intel"]["456"]["builds"]), {"xb70"})
            classified = interaction.followup.calls[1]["embed"]
            self.assertIn("A-10C/Su-34", classified.description)
            self.assertIn("XB-70 Valkyrie", classified.fields[0].value)
            for call in interaction.followup.calls:
                attached = call.get("file")
                if attached is not None:
                    attached.close()

    async def test_deimos_success_grants_reciprocal_unified_targeting(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            attacker = cog.user(1, 123)
            victim = cog.user(1, 456)
            attacker["vehicles"]["deimos"].update(owned=True)
            attacker["vehicles"]["b52"].update(owned=True)
            victim["vehicles"]["f15e"].update(owned=True)
            attacker["vehicles"]["xb70"]["building_until"] = (
                dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
            ).isoformat()
            victim["vehicles"]["c130j"]["building_until"] = (
                dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)
            ).isoformat()
            interaction = _FakeInteraction(user_id=123)
            with patch("cogs.economy.random.randint", return_value=1):
                await cog._deimos_execute(interaction, _FakeUser(456))
            self.assertIn("456", attacker["recon_targets"])
            self.assertIn("123", victim["recon_targets"])
            self.assertEqual(set(attacker["sr71_construction_intel"]["456"]["builds"]), {"c130j"})
            self.assertEqual(set(victim["sr71_construction_intel"]["123"]["builds"]), {"xb70"})
            report = interaction.followup.calls[0]["embed"]
            _assert_embed_valid(self, report)
            self.assertIn("C-130J Rapid Dragon", "\n".join((field.value for field in report.fields)))
            self.assertIn("XB-70 Valkyrie", "\n".join((field.value for field in report.fields)))
            attached = interaction.followup.calls[0].get("file")
            if attached is not None:
                attached.close()

    async def test_active_legacy_recon_packages_are_promoted_without_rescan(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            attacker = cog.user(1, 123)
            victim = cog.user(1, 456)
            attacker["vehicles"]["lrhw"].update(owned=True, ammo=1)
            ready = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
            expiry = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)
            victim["vehicles"]["xb70"]["building_until"] = ready.isoformat()
            attacker["recon_targets"]["456"] = expiry.isoformat()
            self.assertIsNone(cog._mission_preflight(1, 123, "lrhw", _FakeUser(456), "xb70"))
            self.assertEqual(set(attacker["sr71_construction_intel"]["456"]["builds"]), {"xb70"})
            attacker["recon_targets"].pop("456", None)
            self.assertTrue(cog._active_recon(attacker, 456))
            self.assertIn("456", attacker["recon_targets"])

    async def test_unified_package_remains_available_after_b2_vault_strike(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            attacker = cog.user(1, 123)
            victim = cog.user(1, 456)
            attacker["vehicles"]["b2"].update(owned=True, armed=True)
            victim.update(donuts=210000000, deep_vault_owned=True, deep_vault_balance=5000)
            expiry = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
            cog._grant_recon_package(attacker, 456, victim, expiry)
            interaction = _FakeInteraction(user_id=123)
            await cog._b2_execute(interaction, _FakeUser(456))
            self.assertEqual(victim["deep_vault_balance"], 0)
            self.assertTrue(cog._active_recon(attacker, 456))
            self.assertIn("456", attacker["sr71_construction_intel"])
            attached = interaction.followup.calls[0].get("file")
            if attached is not None:
                attached.close()

    async def test_sr71_failed_pass_destroys_aircraft_and_preserves_cooldown_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            attacker = cog.user(1, 123)
            attacker["vehicles"]["sr71"].update(owned=True)
            victim = cog.user(1, 456)
            victim.update(s400_owned=True, s400_interceptors=1, aa_rockets_loaded=3)
            interaction = _FakeInteraction(user_id=123)
            with patch("cogs.economy.random.randint", return_value=1):
                await cog._sr71_execute(interaction, _FakeUser(456))
            sr71 = attacker["vehicles"]["sr71"]
            self.assertFalse(sr71["owned"])
            self.assertIsNone(sr71["building_until"])
            self.assertIsNotNone(sr71["last_deploy_at"])
            self.assertEqual(victim["s400_interceptors"], 0)
            self.assertEqual(victim["aa_rockets_loaded"], 3)
            self.assertEqual(len(interaction.followup.calls), 1)
            self.assertIn("BLACKBIRD LOST", interaction.followup.calls[0]["embed"].title)
            self.assertIn("S-400 40N6E", interaction.followup.calls[0]["embed"].description)
            attached = interaction.followup.calls[0].get("file")
            if attached is not None:
                attached.close()

    async def test_sr71_ignores_regular_aa_and_unloaded_long_range_batteries(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            pilot = cog.user(1, 123)
            pilot["vehicles"]["sr71"].update(owned=True)
            victim = cog.user(1, 456)
            victim.update(aa_rockets_loaded=5, s400_owned=True, s400_interceptors=0)
            victim["vehicles"]["patriot"].update(owned=True, ammo=0)
            interaction = _FakeInteraction(user_id=123)
            with patch("cogs.economy.random.randint") as roll:
                await cog._sr71_execute(interaction, _FakeUser(456))
            roll.assert_not_called()
            self.assertTrue(pilot["vehicles"]["sr71"]["owned"])
            self.assertEqual(victim["aa_rockets_loaded"], 5)
            self.assertEqual(len(interaction.followup.calls), 2)
            self.assertIn("RECONNAISSANCE COMPLETE", interaction.followup.calls[0]["embed"].title)
            for call in interaction.followup.calls:
                attached = call.get("file")
                if attached is not None:
                    attached.close()

    async def test_sr71_patriot_uses_one_missile_even_on_a_miss(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            pilot = cog.user(1, 123)
            pilot["vehicles"]["sr71"].update(owned=True)
            victim = cog.user(1, 456)
            victim["vehicles"]["patriot"].update(owned=True, ammo=1)
            interaction = _FakeInteraction(user_id=123)
            with patch("cogs.economy.random.randint", return_value=100) as roll:
                await cog._sr71_execute(interaction, _FakeUser(456))
            roll.assert_called_once_with(1, 100)
            self.assertEqual(victim["vehicles"]["patriot"]["ammo"], 0)
            self.assertTrue(pilot["vehicles"]["sr71"]["owned"])
            self.assertEqual(len(interaction.followup.calls), 2)
            for call in interaction.followup.calls:
                attached = call.get("file")
                if attached is not None:
                    attached.close()

    async def test_sr71_patriot_hit_and_blackout_gate(self) -> None:
        for blackout in (False, True):
            with self.subTest(blackout=blackout):
                with tempfile.TemporaryDirectory() as raw:
                    bot = _FakeBot(Path(raw))
                    cog = object.__new__(EconomyCog)
                    cog.bot, cog.econ = (bot, bot.economy)
                    pilot = cog.user(1, 123)
                    pilot["vehicles"]["sr71"].update(owned=True)
                    victim = cog.user(1, 456)
                    victim.update(s400_owned=True, s400_interceptors=0, aa_rockets_loaded=4)
                    victim["vehicles"]["patriot"].update(owned=True, ammo=1)
                    if blackout:
                        victim["electronic_blackout_until"] = (
                            dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
                        ).isoformat()
                    interaction = _FakeInteraction(user_id=123)
                    with patch("cogs.economy.random.randint", return_value=1) as roll:
                        await cog._sr71_execute(interaction, _FakeUser(456))
                    self.assertEqual(victim["aa_rockets_loaded"], 4)
                    self.assertEqual(victim["vehicles"]["patriot"]["ammo"], 1 if blackout else 0)
                    self.assertEqual(pilot["vehicles"]["sr71"]["owned"], blackout)
                    if blackout:
                        roll.assert_not_called()
                        self.assertEqual(len(interaction.followup.calls), 2)
                    else:
                        roll.assert_called_once_with(1, 100)
                        self.assertIn("Patriot PAC-3 MSE", interaction.followup.calls[0]["embed"].description)
                    for call in interaction.followup.calls:
                        attached = call.get("file")
                        if attached is not None:
                            attached.close()

    async def test_dark_eagle_blind_miss_spends_round_and_starts_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            attacker = cog.user(1, 123)
            victim = cog.user(1, 456)
            attacker["vehicles"]["lrhw"].update(owned=True, ammo=1, last_deploy_at=None)
            self.assertIsNone(cog._mission_preflight(1, 123, "lrhw", _FakeUser(456), "xb70"))
            interaction = _FakeInteraction(user_id=123)
            with patch("cogs.economy.random.randint", return_value=100):
                await cog._focused_vehicle_execute(interaction, _FakeUser(456), "lrhw", "xb70")
            self.assertEqual(attacker["vehicles"]["lrhw"]["ammo"], 0)
            self.assertIsNotNone(attacker["vehicles"]["lrhw"]["last_deploy_at"])
            self.assertFalse(victim["vehicles"]["xb70"]["owned"])
            self.assertIsNone(victim["vehicles"]["xb70"]["building_until"])
            result = interaction.followup.calls[0]["embed"]
            self.assertIn("EMPTY CONSTRUCTION BAY", result.title)
            self.assertIn("round was expended", result.fields[0].value)
            attached = interaction.followup.calls[0].get("file")
            if attached is not None:
                attached.close()

    async def test_dark_eagle_sr71_guided_hit_destroys_only_the_tracked_build(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            attacker = cog.user(1, 123)
            victim = cog.user(1, 456)
            attacker["vehicles"]["lrhw"].update(owned=True, ammo=1, last_deploy_at=None)
            ready = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
            victim["vehicles"]["xb70"].update(owned=False, building_until=ready.isoformat())
            attacker["sr71_construction_intel"]["456"] = {
                "expires_at": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat(),
                "builds": {"xb70": ready.isoformat()},
            }
            interaction = _FakeInteraction(user_id=123)
            with patch("cogs.economy.random.randint", return_value=100):
                await cog._focused_vehicle_execute(interaction, _FakeUser(456), "lrhw", "xb70")
            self.assertEqual(attacker["vehicles"]["lrhw"]["ammo"], 0)
            self.assertFalse(victim["vehicles"]["xb70"]["owned"])
            self.assertIsNone(victim["vehicles"]["xb70"]["building_until"])
            self.assertEqual(attacker["sr71_construction_intel"]["456"]["builds"], {})
            result = interaction.followup.calls[0]["embed"]
            self.assertIn("STRIKE CONFIRMED", result.title)
            self.assertIn("destroyed in its construction bay", result.fields[0].value)
            attached = interaction.followup.calls[0].get("file")
            if attached is not None:
                attached.close()

    async def test_icbm_ack_failure_does_not_spend_missile_or_start_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            attacker = cog.user(1, 123)
            victim = cog.user(1, 456)
            attacker["icbm_ready"] = 1
            victim.update(donuts=10000, bank=0)
            interaction = _FakeInteraction(user_id=123)

            async def fail_defer(**kwargs) -> None:
                raise RuntimeError("Discord acknowledgement failed")

            interaction.response.defer = fail_defer
            with self.assertRaisesRegex(RuntimeError, "acknowledgement failed"):
                await cog._icbm_launch_run(interaction, bot.config.for_guild(1), _FakeUser(456))
            self.assertEqual(attacker["icbm_ready"], 1)
            self.assertIsNone(attacker.get("icbm_launch_at"))
            self.assertEqual(victim["donuts"], 10000)

    async def test_icbm_acknowledges_before_committing_and_edits_original_response(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            attacker = cog.user(1, 123)
            victim = cog.user(1, 456)
            attacker["icbm_ready"] = 1
            victim.update(donuts=10000, bank=0)
            interaction = _FakeInteraction(user_id=123)
            with patch.object(cog, "_random_icbm_gif", return_value=None):
                await cog._icbm_launch_run(interaction, bot.config.for_guild(1), _FakeUser(456))
            self.assertEqual(interaction.response.deferred, {"thinking": True, "ephemeral": False})
            self.assertEqual(len(interaction.original_edits), 1)
            self.assertEqual(attacker["icbm_ready"], 0)
            self.assertIsNotNone(attacker.get("icbm_launch_at"))
            self.assertLess(victim["donuts"], 10000)

    async def test_icbm_media_failure_retries_committed_result_without_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            media = Path(raw) / "strike.gif"
            media.write_bytes(b"GIF89a")
            image_file = discord.File(str(media), filename="strike.gif")
            embed = discord.Embed(title="ICBM result")
            embed.set_image(url="attachment://strike.gif")
            interaction = _FakeInteraction(user_id=123)
            edits = []

            async def flaky_edit(**kwargs) -> None:
                edits.append(kwargs)
                if len(edits) == 1:
                    response = SimpleNamespace(status=503, reason="Service Unavailable")
                    raise discord.HTTPException(response, "upload failed")

            interaction.edit_original_response = flaky_edit
            with self.assertLogs("cogs.economy", level="WARNING"):
                await EconomyCog._send_icbm_result(
                    interaction,
                    deferred=True,
                    embed=embed,
                    content="<@456>",
                    allowed_mentions=discord.AllowedMentions(users=True),
                    image_file=image_file,
                )
            self.assertEqual(len(edits), 2)
            self.assertIsNone(edits[1]["embed"].image.url)

    async def test_b52_hit_sets_one_hour_lockdown_and_spends_payload(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            attacker = bot.economy.user(1, 123, 100)
            victim = bot.economy.user(1, 456, 100)
            attacker["vehicles"]["b52"].update(owned=True, ammo=1)
            victim.update(donuts=1000, bank=1000, deep_vault_owned=True, deep_vault_balance=1000)
            interaction = _FakeInteraction(user_id=123)
            before = dt.datetime.now(dt.timezone.utc)
            await cog._focused_vehicle_execute(interaction, _FakeUser(456), "b52")
            expiry = dt.datetime.fromisoformat(victim["strategic_lockdown_until"])
            self.assertGreaterEqual((expiry - before).total_seconds(), 3599)
            self.assertLessEqual((expiry - before).total_seconds(), 3601)
            self.assertEqual(attacker["vehicles"]["b52"]["ammo"], 0)
            self.assertEqual(victim["donuts"], 750)
            self.assertEqual(victim["bank"], 850)
            self.assertEqual(victim["deep_vault_balance"], 1000)
            self.assertEqual(len(interaction.followup.calls), 1)
            victim["icbm_ready"] = 1
            blocked_launch = _FakeInteraction(user_id=456)
            await cog._icbm_launch_run(blocked_launch, bot.config.for_guild(1), _FakeUser(789))
            self.assertEqual(victim["icbm_ready"], 1)
            self.assertIn("disabled by a B-52 strike", blocked_launch.response.calls[0]["embed"].description)
            attached = interaction.followup.calls[0].get("file")
            if attached is not None:
                attached.close()

    async def test_a10_combined_raid_uses_seventy_percent_vehicle_kill_and_preserves_thor(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            attacker = bot.economy.user(1, 123, 100)
            victim = bot.economy.user(1, 456, 100)
            attacker["vehicles"]["a10"].update(owned=True, ammo=1)
            victim.update(
                donuts=1000,
                bank=1000,
                aa_rockets_loaded=4,
                aa_rockets_stock=4,
                s400_owned=True,
                s400_interceptors=2,
            )
            victim["vehicles"]["f15e"].update(owned=True, ammo=5)
            victim["vehicles"]["b52"].update(owned=True, ammo=10)
            victim["vehicles"]["c130j"].update(owned=True, ammo=8)
            victim["vehicles"]["patriot"].update(owned=True, ammo=3)
            victim["vehicles"]["xb70"]["building_until"] = (
                dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
            ).isoformat()
            protected = thor_state.normalize(victim)
            protected.update(operational=True, rods=6, chambered=True, gbi_stock=2)
            before_thor = json.loads(json.dumps(victim["thor"]))
            interaction = _FakeInteraction(user_id=123)
            with patch("cogs.economy.random.randint", side_effect=[100, 100, 1]):
                await cog._focused_vehicle_execute(interaction, _FakeUser(456), "a10", "f15e")
            self.assertEqual(attacker["vehicles"]["a10"]["ammo"], 0)
            self.assertFalse(victim["vehicles"]["f15e"]["owned"])
            self.assertEqual(victim["vehicles"]["b52"]["ammo"], 3)
            self.assertEqual(victim["vehicles"]["c130j"]["ammo"], 3)
            self.assertEqual(victim["aa_rockets_loaded"], 1)
            self.assertEqual(victim["aa_rockets_stock"], 1)
            self.assertEqual(victim["s400_interceptors"], 2)
            self.assertEqual(victim["vehicles"]["patriot"]["ammo"], 2)
            self.assertEqual((victim["donuts"], victim["bank"]), (800, 900))
            self.assertIsNotNone(victim["vehicles"]["xb70"]["building_until"])
            self.assertEqual(victim["thor"], before_thor)
            report = interaction.followup.calls[0]["embed"].fields[0].value
            self.assertIn("70% roll", report)
            self.assertIn("all THOR assets survived", report)
            attached = interaction.followup.calls[0].get("file")
            if attached is not None:
                attached.close()

    async def test_su34_combined_raid_uses_fifty_percent_vehicle_kill_and_preserves_thor(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            attacker = bot.economy.user(1, 123, 100)
            victim = bot.economy.user(1, 456, 100)
            attacker["vehicles"]["su34"].update(owned=True, ammo=1)
            victim.update(donuts=1000, bank=1000, aa_rockets_loaded=4, aa_rockets_stock=4)
            victim["vehicles"]["b52"].update(owned=True, ammo=4)
            victim["vehicles"]["c130j"].update(owned=True, ammo=4)
            victim["vehicles"]["f15e"].update(owned=True, ammo=3)
            victim["vehicles"]["xb70"]["building_until"] = (
                dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
            ).isoformat()
            protected = thor_state.normalize(victim)
            protected.update(operational=True, rods=4, chambered=True, gbi_stock=1)
            before_thor = json.loads(json.dumps(victim["thor"]))
            interaction = _FakeInteraction(user_id=123)
            with patch("cogs.economy.random.randint", side_effect=[100, 1]):
                await cog._focused_vehicle_execute(interaction, _FakeUser(456), "su34", "b52")
            self.assertEqual(attacker["vehicles"]["su34"]["ammo"], 0)
            self.assertFalse(victim["vehicles"]["b52"]["owned"])
            self.assertEqual(victim["vehicles"]["c130j"]["ammo"], 3)
            self.assertEqual(victim["vehicles"]["f15e"]["ammo"], 2)
            self.assertEqual(victim["aa_rockets_loaded"], 2)
            self.assertEqual(victim["aa_rockets_stock"], 2)
            self.assertEqual((victim["donuts"], victim["bank"]), (850, 920))
            self.assertIsNotNone(victim["vehicles"]["xb70"]["building_until"])
            self.assertEqual(victim["thor"], before_thor)
            report = interaction.followup.calls[0]["embed"].fields[0].value
            self.assertIn("50% roll", report)
            self.assertIn("all THOR assets survived", report)
            attached = interaction.followup.calls[0].get("file")
            if attached is not None:
                attached.close()

    async def test_b52_three_repairs_escalate_then_fourth_shootdown_destroys_airframe(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            attacker = bot.economy.user(1, 123, 100)
            defender = bot.economy.user(1, 456, 100)
            attacker.update(donuts=4000000000, bank=0)
            b52 = attacker["vehicles"]["b52"]
            b52.update(owned=True, ammo=1)
            defender.update(s400_owned=True, s400_interceptors=1)
            expected_costs = (250000000, 500000000, 750000000)
            for phase, expected_cost in enumerate(expected_costs, 1):
                strike = _FakeInteraction(user_id=123)
                with patch("cogs.economy.random.randint", return_value=1):
                    await cog._focused_vehicle_execute(strike, _FakeUser(456), "b52")
                self.assertTrue(b52["owned"])
                self.assertTrue(b52["b52_damaged"])
                self.assertEqual(b52["b52_repair_count"], phase)
                self.assertEqual(b52["ammo"], 0)
                attached = strike.followup.calls[0].get("file")
                if attached is not None:
                    attached.close()
                before = attacker["donuts"]
                repair = _FakeInteraction(user_id=123)
                await EconomyCog.vehicle_repair.callback(
                    cog, repair, app_commands.Choice(name="B-52H Stratofortress", value="b52")
                )
                self.assertEqual(attacker["donuts"], before - expected_cost)
                self.assertIsNotNone(b52["b52_repairing_until"])
                self.assertIn(f"depot repair {phase}/3 started", repair.response.calls[0]["embed"].title)
                b52["b52_repairing_until"] = (
                    dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
                ).isoformat()
                cog._vehicle_settle(attacker, "b52")
                self.assertFalse(b52["b52_damaged"])
                b52.update(ammo=1, last_deploy_at=None)
                defender["s400_interceptors"] = 1
            final_strike = _FakeInteraction(user_id=123)
            with patch("cogs.economy.random.randint", return_value=1):
                await cog._focused_vehicle_execute(final_strike, _FakeUser(456), "b52")
            self.assertFalse(b52["owned"])
            self.assertFalse(b52["b52_damaged"])
            self.assertEqual(b52["b52_repair_count"], 0)
            self.assertIn("destroyed permanently", final_strike.followup.calls[0]["embed"].description)
            attached = final_strike.followup.calls[0].get("file")
            if attached is not None:
                attached.close()
            rebuild = _FakeInteraction(user_id=123)
            await EconomyCog.vehicle_build.callback(
                cog, rebuild, app_commands.Choice(name="B-52H Stratofortress", value="b52")
            )
            self.assertIsNotNone(b52["building_until"])
            self.assertEqual(b52["b52_repair_count"], 0)

    async def test_active_aa_shield_boosts_loaded_mq9_interception_by_ten_points(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            attacker = bot.economy.user(1, 123, 100)
            victim = bot.economy.user(1, 456, 100)
            attacker["vehicles"]["mq9"].update(owned=True, ammo=1)
            victim["inventory"]["lock"] = 4
            victim["aa_rockets_loaded"] = 1
            victim["aa_shield_until"] = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat()
            interaction = _FakeInteraction(user_id=123)
            with patch("cogs.economy.random.randint", side_effect=[100, 25]):
                await cog._focused_vehicle_execute(interaction, _FakeUser(456), "mq9", "lock")
            self.assertFalse(attacker["vehicles"]["mq9"]["owned"])
            self.assertEqual(victim["aa_rockets_loaded"], 0)
            self.assertEqual(victim["inventory"]["lock"], 4)
            self.assertIn("INTERCEPT", interaction.followup.calls[0]["embed"].title)
            attached = interaction.followup.calls[0].get("file")
            if attached is not None:
                attached.close()

    async def test_zumwalt_destroys_forty_percent_of_visible_donuts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            attacker = bot.economy.user(1, 123, 100)
            victim = bot.economy.user(1, 456, 100)
            attacker["vehicles"]["zumwalt"].update(owned=True, ammo=1)
            victim.update(donuts=1000, bank=500, deep_vault_owned=True, deep_vault_balance=2000)
            interaction = _FakeInteraction(user_id=123)
            await cog._focused_vehicle_execute(interaction, _FakeUser(456), "zumwalt")
            self.assertEqual(victim["donuts"], 600)
            self.assertEqual(victim["bank"], 300)
            self.assertEqual(victim["deep_vault_balance"], 2000)
            self.assertEqual(attacker["vehicles"]["zumwalt"]["ammo"], 0)
            attached = interaction.followup.calls[0].get("file")
            if attached is not None:
                attached.close()

    async def test_apache_can_destroy_a_loaded_s400_round(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            attacker = bot.economy.user(1, 123, 100)
            victim = bot.economy.user(1, 456, 100)
            attacker["vehicles"]["apache"].update(owned=True, ammo=1)
            victim.update(s400_owned=True, s400_interceptors=1, aa_rockets_loaded=0)
            interaction = _FakeInteraction(user_id=123)
            await cog._focused_vehicle_execute(interaction, _FakeUser(456), "apache", "s400")
            self.assertEqual(victim["s400_interceptors"], 0)
            self.assertEqual(attacker["vehicles"]["apache"]["ammo"], 0)
            self.assertEqual(len(interaction.followup.calls), 1)
            attached = interaction.followup.calls[0].get("file")
            if attached is not None:
                attached.close()

    async def test_mq9_precision_and_split_strikes_use_their_approved_damage(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            attacker = bot.economy.user(1, 123, 100)
            victim = bot.economy.user(1, 456, 100)
            mq9 = attacker["vehicles"]["mq9"]
            mq9.update(owned=True, ammo=1)
            victim["inventory"].update(lock=5, uno=5, drill=5)
            precision = _FakeInteraction(user_id=123)
            with patch("cogs.economy.random.randint", return_value=100):
                await cog._focused_vehicle_execute(precision, _FakeUser(456), "mq9", "lock")
            self.assertEqual(victim["inventory"]["lock"], 0)
            self.assertEqual(victim["inventory"]["uno"], 5)
            self.assertEqual(victim["inventory"]["drill"], 5)
            self.assertEqual(mq9["ammo"], 0)
            report = precision.followup.calls[0]["embed"].fields[0].value
            self.assertIn("5/5 Lock", report)
            self.assertIn("(100%)", report)
            attached = precision.followup.calls[0].get("file")
            if attached is not None:
                attached.close()
            mq9.update(ammo=2, last_deploy_at=None)
            victim["inventory"].update(lock=5, uno=5)
            split = _FakeInteraction(user_id=123)
            with patch("cogs.economy.random.randint", side_effect=[100, 100]):
                await cog._focused_vehicle_execute(split, _FakeUser(456), "mq9", "lock", "uno")
            self.assertEqual(victim["inventory"]["lock"], 2)
            self.assertEqual(victim["inventory"]["uno"], 2)
            self.assertEqual(victim["inventory"]["drill"], 5)
            self.assertEqual(mq9["ammo"], 0)
            report = split.followup.calls[0]["embed"].fields[0].value
            self.assertIn("3/5 Lock", report)
            self.assertIn("3/5 Uno Reverse", report)
            self.assertEqual(report.count("(60%)"), 2)
            attached = split.followup.calls[0].get("file")
            if attached is not None:
                attached.close()

    async def test_comanche_upgrade_costs_one_billion_and_settles_lazily(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            user = bot.economy.user(1, 123, 100)
            user.update(donuts=1200000000, bank=0)
            user["vehicles"]["apache"].update(owned=True, ammo=2)
            interaction = _FakeInteraction(user_id=123)
            await EconomyCog.vehicle_upgrade.callback(cog, interaction)
            apache = user["vehicles"]["apache"]
            self.assertEqual(user["donuts"], 200000000)
            self.assertFalse(apache["comanche_upgraded"])
            self.assertIsNotNone(apache["comanche_upgrade_until"])
            self.assertEqual(apache["ammo"], 2)
            apache["comanche_upgrade_until"] = (
                dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
            ).isoformat()
            cog._vehicle_settle(user, "apache")
            self.assertTrue(apache["comanche_upgraded"])
            self.assertIsNone(apache["comanche_upgrade_until"])
            attached = interaction.response.calls[0].get("file")
            if attached is not None:
                attached.close()

    async def test_comanche_raid_destroys_conventional_ammo_but_preserves_thor_assets(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            attacker = bot.economy.user(1, 123, 100)
            victim = bot.economy.user(1, 456, 100)
            attacker["vehicles"]["apache"].update(owned=True, ammo=2, comanche_upgraded=True)
            victim.update(
                icbm_ready=4, aa_rockets_loaded=4, aa_rockets_stock=4, s400_owned=True, s400_interceptors=4
            )
            victim["icbm_building_at"] = (
                dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
            ).isoformat()
            victim["vehicles"]["b2"].update(owned=True, armed=True)
            for model in (
                "b52",
                "zumwalt",
                "virginia",
                "himars",
                "apache",
                "mq9",
                "f15e",
                "a10",
                "su34",
                "aegis",
                "p8",
                "patriot",
            ):
                victim["vehicles"][model].update(owned=True, ammo=4)
            victim["vehicles"]["p8"].update(
                loading_qty=2,
                loading_until=(dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat(),
            )
            victim["vehicles"]["aegis"].update(
                bmd_owned=True,
                sm3_ammo=3,
                sm3_loading_qty=2,
                sm3_loading_until=(dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat(),
            )
            victim_thor = thor_state.normalize(victim)
            victim_thor.update(
                operational=True,
                rods=6,
                chambered=True,
                gbi_stock=1,
                gbi_building_until=(thor_state.utcnow() + dt.timedelta(hours=1)).isoformat(),
                gbi_building_qty=1,
            )
            interaction = _FakeInteraction(user_id=123)
            with patch("cogs.economy.random.randint", side_effect=[100, 100, 20, 100]):
                await cog._focused_vehicle_execute(interaction, _FakeUser(456), "apache")
            self.assertEqual(attacker["vehicles"]["apache"]["ammo"], 0)
            self.assertEqual(victim["icbm_ready"], 0)
            self.assertIsNotNone(victim["icbm_building_at"])
            self.assertEqual(victim["aa_rockets_loaded"], 0)
            self.assertEqual(victim["aa_rockets_stock"], 0)
            self.assertEqual(victim["s400_interceptors"], 0)
            self.assertFalse(victim["vehicles"]["b2"]["armed"])
            for model in (
                "b52",
                "zumwalt",
                "virginia",
                "himars",
                "apache",
                "mq9",
                "f15e",
                "a10",
                "su34",
                "aegis",
                "p8",
                "patriot",
            ):
                self.assertEqual(victim["vehicles"][model]["ammo"], 0, model)
            self.assertEqual(victim["vehicles"]["p8"]["loading_qty"], 2)
            self.assertTrue(victim["vehicles"]["aegis"]["bmd_owned"])
            self.assertEqual(victim["vehicles"]["aegis"]["sm3_ammo"], 3)
            self.assertEqual(victim["vehicles"]["aegis"]["sm3_loading_qty"], 2)
            self.assertIsNotNone(victim["vehicles"]["aegis"]["sm3_loading_until"])
            self.assertEqual(victim["thor"]["rods"], 6)
            self.assertTrue(victim["thor"]["chambered"])
            self.assertEqual(victim_thor["gbi_stock"], 1)
            self.assertEqual(victim_thor["gbi_building_qty"], 1)
            self.assertIsNotNone(victim_thor["gbi_building_until"])
            self.assertIn("RAH-66 COMANCHE", interaction.followup.calls[0]["embed"].title)
            report = interaction.followup.calls[0]["embed"].fields[0].value
            self.assertIn("All-store raid — 100%", report)
            self.assertIn("GBI/EKVs and BMD SM-3 interceptors survived", report)
            self.assertLessEqual(len(report), 1024)
            attached = interaction.followup.calls[0].get("file")
            if attached is not None:
                attached.close()

    async def test_comanche_legacy_targets_are_ignored_and_two_jagms_are_committed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            attacker = bot.economy.user(1, 123, 100)
            victim = bot.economy.user(1, 456, 100)
            attacker["vehicles"]["apache"].update(owned=True, ammo=3, comanche_upgraded=True)
            victim.update(icbm_ready=5, s400_owned=True, s400_interceptors=5)
            victim["vehicles"]["himars"].update(owned=True, ammo=4)
            interaction = _FakeInteraction(user_id=123)
            with patch("cogs.economy.random.randint", side_effect=[100, 100]):
                await cog._focused_vehicle_execute(interaction, _FakeUser(456), "apache", "icbm", "s400")
            self.assertEqual(attacker["vehicles"]["apache"]["ammo"], 1)
            self.assertEqual(victim["icbm_ready"], 0)
            self.assertEqual(victim["s400_interceptors"], 0)
            self.assertEqual(victim["vehicles"]["himars"]["ammo"], 0)
            report = interaction.followup.calls[0]["embed"].fields[0].value
            self.assertIn("All-store raid — 100%", report)
            attached = interaction.followup.calls[0].get("file")
            if attached is not None:
                attached.close()

    async def test_intercepted_comanche_is_repairable_for_its_persistent_quote(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            attacker = bot.economy.user(1, 123, 100)
            victim = bot.economy.user(1, 456, 100)
            attacker.update(donuts=500000000, bank=0)
            attacker["vehicles"]["apache"].update(owned=True, ammo=3, comanche_upgraded=True)
            victim.update(icbm_ready=3, aa_rockets_loaded=1)
            strike = _FakeInteraction(user_id=123)
            with patch("cogs.economy.random.randint", side_effect=[100, 100, 20, 1, 150]):
                await cog._focused_vehicle_execute(strike, _FakeUser(456), "apache", "icbm")
            state = attacker["vehicles"]["apache"]
            self.assertTrue(state["owned"])
            self.assertTrue(state["comanche_upgraded"])
            self.assertTrue(state["comanche_damaged"])
            self.assertEqual(state["comanche_repair_cost"], 150000000)
            self.assertEqual(state["ammo"], 0)
            result = strike.followup.calls[0]["embed"].description
            self.assertIn("recoverable battle damage", result)
            self.assertIn("Check `/arsenal`", result)
            self.assertNotIn("150,000,000", result)
            self.assertNotIn("destroyed permanently", result)
            attached = strike.followup.calls[0].get("file")
            if attached is not None:
                attached.close()
            repair = _FakeInteraction(user_id=123)
            await EconomyCog.vehicle_repair.callback(cog, repair)
            self.assertTrue(state["comanche_damaged"])
            self.assertEqual(state["comanche_repair_cost"], 0)
            repairing = dt.datetime.fromisoformat(state["comanche_repairing_until"])
            self.assertAlmostEqual(
                (repairing - dt.datetime.now(dt.timezone.utc)).total_seconds(), 2 * 3600, delta=2
            )
            self.assertTrue(state["comanche_upgraded"])
            self.assertTrue(state["owned"])
            self.assertEqual(attacker["donuts"], 350000000)
            self.assertIn("stays grounded until", repair.response.calls[0]["embed"].description)
            self.assertIn("undergoing depot repair", cog._mission_preflight(1, 123, "apache", _FakeUser(456)))
            duplicate = _FakeInteraction(user_id=123)
            await EconomyCog.vehicle_repair.callback(cog, duplicate)
            self.assertEqual(attacker["donuts"], 350000000)
            self.assertIn("already completes", duplicate.response.calls[0]["embed"].description)
            state["comanche_repairing_until"] = (
                dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
            ).isoformat()
            cog._vehicle_settle(attacker, "apache")
            self.assertFalse(state["comanche_damaged"])
            self.assertIsNone(state["comanche_repairing_until"])


class CoalitionCommandIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_intel_pages_large_coalition_without_truncating_sections(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = CoalitionCog(bot)
            doc = bot.economy.store.load(1)
            coalitions.state(doc)["groups"]["1"] = {
                "name": "Large Intel Test",
                "founder": 123,
                "members": [123, *range(200, 209)],
                "expires_at": (coalitions.utcnow() + dt.timedelta(days=1)).isoformat(),
                "log": [],
            }
            resources = "\n".join((f"Resource {index}: " + "R" * 45 for index in range(70)))
            arsenal = "\n".join((f"Vehicle {index}: " + "A" * 55 for index in range(70)))
            fishing = "\n".join((f"Rod {index}: " + "F" * 45 for index in range(40)))
            continuity = "\n".join((f"Shelter {index}: " + "C" * 45 for index in range(60)))
            with patch.object(
                cog,
                "_asset_lines",
                return_value=("Wallet 123 · Vault 456", resources, arsenal, fishing, continuity),
            ):
                interaction = _FakeInteraction(user_id=123)
                await CoalitionCog.intel.callback(cog, interaction)
            self.assertEqual(interaction.response.deferred, {"ephemeral": True, "thinking": True})
            edit = interaction.original_edits[0]
            self.assertEqual(edit["view"].author_id, 123)
            pages = edit["view"].pages
            self.assertGreater(len(pages), 10)
            for page in pages:
                _assert_embed_valid(self, page)
            first_member_pages = []
            for page in pages:
                if "User 200" in page.title:
                    break
                first_member_pages.append(page)
            for heading, original in (
                ("📦 Resources", resources),
                ("⚔️ Full strategic posture", arsenal),
                ("🎣 Fisheries", fishing),
                ("🏔️ Raven Rock continuity loadout", continuity),
            ):
                rebuilt = "".join(
                    (
                        field.value
                        for page in first_member_pages
                        for field in page.fields
                        if field.name.startswith(heading)
                    )
                )
                self.assertEqual(rebuilt, original)
            outsider = _FakeInteraction(user_id=999)
            await CoalitionCog.intel.callback(cog, outsider)
            self.assertIsNone(outsider.response.deferred)
            self.assertTrue(outsider.response.calls[0]["ephemeral"])
            self.assertFalse(outsider.original_edits)

    async def test_tenth_member_can_join_but_eleventh_cannot(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = CoalitionCog(bot)
            doc = bot.economy.store.load(1)
            root = coalitions.state(doc)
            expiry = (coalitions.utcnow() + dt.timedelta(days=1)).isoformat()
            root["groups"]["1"] = {
                "name": "Ten Player Test",
                "founder": 1,
                "members": list(range(1, 10)),
                "expires_at": expiry,
                "log": [],
            }
            root["invites"]["10"] = [{"coalition_id": "1", "invited_by": 1, "expires_at": expiry}]
            tenth = _FakeInteraction(user_id=10)
            await CoalitionCog.join.callback(cog, tenth, None)
            self.assertEqual(root["groups"]["1"]["members"], list(range(1, 11)))
            self.assertIn("10/10", tenth.response.calls[0]["embed"].description)
            root["invites"]["11"] = [{"coalition_id": "1", "invited_by": 1, "expires_at": expiry}]
            eleventh = _FakeInteraction(user_id=11)
            await CoalitionCog.join.callback(cog, eleventh, None)
            self.assertEqual(len(root["groups"]["1"]["members"]), 10)
            self.assertIn("filled up", eleventh.response.calls[0]["embed"].description)

    async def test_coalition_transfer_allows_recipient_to_own_both_tanks(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            economy_cog = object.__new__(EconomyCog)
            economy_cog.bot, economy_cog.econ = (bot, bot.economy)
            bot.cogs["Economy"] = economy_cog
            cog = CoalitionCog(bot)
            donor = bot.economy.user(1, 123, 100)
            receiver = bot.economy.user(1, 456, 100)
            economy_cog._vehicle_state(donor, "leopard2a7").update(owned=True)
            economy_cog._vehicle_state(receiver, "m1a2").update(owned=True)
            error = cog._vehicle_transfer_error(bot.config.for_guild(1), donor, receiver, "leopard2a7", 123)
            self.assertIsNone(error)

    async def test_thor_modules_rods_gbis_and_sm3_follow_coalition_logistics_rules(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            economy_cog = object.__new__(EconomyCog)
            economy_cog.bot, economy_cog.econ = (bot, bot.economy)
            bot.cogs["Economy"] = economy_cog
            cog = CoalitionCog(bot)
            doc = bot.economy.store.load(1)
            coalitions.state(doc)["groups"]["1"] = {
                "name": "Orbital Pact",
                "founder": 123,
                "members": [123, 456],
                "expires_at": (coalitions.utcnow() + dt.timedelta(days=1)).isoformat(),
                "log": [],
            }
            donor = bot.economy.user(1, 123, 100)
            receiver = bot.economy.user(1, 456, 100)
            donor_thor = thor_state.normalize(donor)
            receiver_thor = thor_state.normalize(receiver)
            donor_thor["components"]["odin"].update(status="ready")
            module = _FakeInteraction(user_id=123)
            await CoalitionCog.transfer.callback(cog, module, _FakeUser(456), "thor-component:odin", "1")
            self.assertEqual(donor_thor["components"]["odin"]["status"], "none")
            self.assertEqual(receiver_thor["components"]["odin"]["status"], "ready")
            donor_thor.update(operational=True, rods=3, chambered=True)
            receiver_thor.update(operational=True, rods=1)
            rods = _FakeInteraction(user_id=123)
            await CoalitionCog.transfer.callback(cog, rods, _FakeUser(456), "ammo:thor", "2")
            self.assertEqual(donor_thor["rods"], 1)
            self.assertEqual(receiver_thor["rods"], 3)
            self.assertTrue(donor_thor["chambered"])
            donor_thor["gbi_stock"] = 2
            receiver_thor["gbi_stock"] = 0
            gbi = _FakeInteraction(user_id=123)
            await CoalitionCog.transfer.callback(cog, gbi, _FakeUser(456), "ammo:gbi", "1")
            self.assertEqual(donor_thor["gbi_stock"], 1)
            self.assertEqual(receiver_thor["gbi_stock"], 1)
            donor["vehicles"]["aegis"].update(owned=True, bmd_owned=True, sm3_ammo=2)
            receiver["vehicles"]["aegis"].update(owned=True, bmd_owned=True, sm3_ammo=1)
            missile = _FakeInteraction(user_id=123)
            await CoalitionCog.transfer.callback(cog, missile, _FakeUser(456), "ammo:sm3", "1")
            self.assertEqual(donor["vehicles"]["aegis"]["sm3_ammo"], 1)
            self.assertEqual(receiver["vehicles"]["aegis"]["sm3_ammo"], 2)

    async def test_ordinary_s400_transfer_keeps_loaded_ammo(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = CoalitionCog(bot)
            doc = bot.economy.store.load(1)
            coalitions.state(doc)["groups"]["1"] = {
                "name": "Logistics",
                "founder": 123,
                "members": [123, 456],
                "expires_at": (coalitions.utcnow() + dt.timedelta(days=1)).isoformat(),
                "log": [],
            }
            donor = bot.economy.user(1, 123, 100)
            receiver = bot.economy.user(1, 456, 100)
            donor.update(s400_owned=True, s400_interceptors=2)
            interaction = _FakeInteraction()
            await CoalitionCog.transfer.callback(cog, interaction, _FakeUser(456), "vehicle:s400", "1")
            self.assertFalse(donor["s400_owned"])
            self.assertEqual(donor["s400_interceptors"], 0)
            self.assertTrue(receiver["s400_owned"])
            self.assertEqual(receiver["s400_interceptors"], 2)

    async def test_thor_transfer_respects_recipient_paid_resupply_capacity(self):
        for incoming, accepted in ((1, True), (2, False)):
            with self.subTest(incoming=incoming), tempfile.TemporaryDirectory() as raw:
                bot = _FakeBot(Path(raw))
                cog = CoalitionCog(bot)
                doc = bot.economy.store.load(1)
                coalitions.state(doc)["groups"]["1"] = {
                    "name": "Logistics",
                    "founder": 123,
                    "members": [123, 456],
                    "expires_at": (coalitions.utcnow() + dt.timedelta(days=1)).isoformat(),
                    "log": [],
                }
                source = thor_state.normalize(bot.economy.user(1, 123, 100))
                dest = thor_state.normalize(bot.economy.user(1, 456, 100))
                source.update(operational=True, rods=3)
                future = (coalitions.utcnow() + dt.timedelta(hours=1)).isoformat()
                dest.update(operational=True, rods=2, resupply_qty=3, resupply_until=future)
                interaction = _FakeInteraction()
                await CoalitionCog.transfer.callback(
                    cog, interaction, _FakeUser(456), "ammo:thor", str(incoming)
                )
                self.assertEqual(dest["rods"], 2 + (incoming if accepted else 0))
                self.assertEqual(source["rods"], 3 - (incoming if accepted else 0))
                self.assertEqual(dest["resupply_qty"], 3)
                self.assertEqual(dest["resupply_until"], future)

    async def test_transfer_respects_pending_icbm_and_radar_aa_builds(self):
        for asset, starting, pending, incoming, accepted in (
            ("ammo:icbm", 1, 1, 1, True),
            ("ammo:icbm", 1, 1, 2, False),
            ("ammo:aa-stock", 4, 5, 1, True),
            ("ammo:aa-stock", 4, 5, 2, False),
        ):
            with self.subTest(asset=asset, incoming=incoming), tempfile.TemporaryDirectory() as raw:
                bot = _FakeBot(Path(raw))
                economy_cog = object.__new__(EconomyCog)
                economy_cog.bot, economy_cog.econ = (bot, bot.economy)
                bot.cogs["Economy"] = economy_cog
                cog = CoalitionCog(bot)
                doc = bot.economy.store.load(1)
                coalitions.state(doc)["groups"]["1"] = {
                    "name": "Logistics",
                    "founder": 123,
                    "members": [123, 456],
                    "expires_at": (coalitions.utcnow() + dt.timedelta(days=1)).isoformat(),
                    "log": [],
                }
                source, dest = (bot.economy.user(1, 123, 100), bot.economy.user(1, 456, 100))
                future = (coalitions.utcnow() + dt.timedelta(hours=1)).isoformat()
                if asset == "ammo:icbm":
                    field, timer = ("icbm_ready", "icbm_building_at")
                else:
                    field, timer = ("aa_rockets_stock", "aa_rocket_build_at")
                    dest["aa_rocket_build_qty"] = pending
                source[field] = 3
                dest[field], dest[timer] = (starting, future)
                interaction = _FakeInteraction()
                await CoalitionCog.transfer.callback(cog, interaction, _FakeUser(456), asset, str(incoming))
                self.assertEqual(dest[field], starting + (incoming if accepted else 0))
                self.assertEqual(source[field], 3 - (incoming if accepted else 0))
                self.assertEqual(dest[timer], future)


class ThorIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_service_and_block2_timers_settle_restart_safely(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            user = bot.economy.user(1, 123, 100)
            state = thor_state.normalize(user)
            past = (thor_state.utcnow() - dt.timedelta(seconds=1)).isoformat()
            state.update(
                shots_since_service=3,
                service_until=past,
                gbi_block2_owned=False,
                gbi_block2_building_until=past,
            )
            self.assertTrue(thor_state.settle(user, bot.config.for_guild(1)))
            self.assertEqual(state["shots_since_service"], 0)
            self.assertIsNone(state["service_until"])
            self.assertTrue(state["gbi_block2_owned"])
            self.assertIsNone(state["gbi_block2_building_until"])

    async def test_gbi_batch_builds_to_two_and_intercept_consumes_ready_stock(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = ThorCog(bot)
            user = bot.economy.user(1, 123, 100)
            user.update(donuts=600000000000, bank=0)
            build = _FakeInteraction(user_id=123)
            await ThorCog.gbi_build.callback(cog, build, 2)
            state = thor_state.normalize(user)
            self.assertEqual(user["donuts"], 100000000000)
            self.assertEqual(state["gbi_stock"], 0)
            self.assertEqual(state["gbi_building_qty"], 2)
            self.assertIsNotNone(state["gbi_building_until"])
            build_due = thor_state.parse_time(state["gbi_building_until"])
            self.assertAlmostEqual((build_due - thor_state.utcnow()).total_seconds(), 30 * 60, delta=2)
            state["gbi_building_until"] = (thor_state.utcnow() - dt.timedelta(seconds=1)).isoformat()
            self.assertTrue(thor_state.settle(user, cog.cfg(1)))
            self.assertEqual(state["gbi_stock"], 2)
            self.assertEqual(state["gbi_building_qty"], 0)
            due = (thor_state.utcnow() + dt.timedelta(minutes=30)).isoformat()
            cog._launches(cog._doc(1))["THR-STOCK"] = {
                "builder": 456,
                "component": "odin",
                "resolves_at": due,
                "channel_id": 1,
                "interceptors": [],
            }
            before = user["donuts"]
            intercept = _FakeInteraction(user_id=123)
            with patch("cogs.thor.random.randint", side_effect=[20, 100]):
                await ThorCog.intercept.callback(cog, intercept, "THR-STOCK")
            self.assertEqual(user["donuts"], before)
            self.assertEqual(state["gbi_stock"], 1)
            record = cog._launches(cog._doc(1))["THR-STOCK"]
            self.assertEqual(len(record["interceptors"]), 1)
            self.assertIn("Ready stock remaining:** 1/2", intercept.response.calls[0]["embed"].description)
            status = _FakeInteraction(user_id=123)
            await ThorCog.status.callback(cog, status)
            status_embed = status.response.calls[0]["embed"]
            _assert_embed_valid(self, status_embed)
            gbi_field = next(
                (
                    field
                    for page in _status_reply_pages(status.response.calls[0])
                    for field in page.fields
                    if "GBI/EKV" in field.name
                )
            )
            self.assertIn("Ready:** 1/2", gbi_field.value)
            guide = _FakeInteraction(user_id=123)
            await ThorCog.guide.callback(cog, guide)
            guide_pages = guide.response.calls[0]["view"].pages
            for page in guide_pages:
                _assert_embed_valid(self, page)
            self.assertIn("/thor gbi-build", guide_pages[0].description)

    async def test_launch_resolution_is_persistent_and_does_not_reroll(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = ThorCog(bot)
            builder = bot.economy.user(1, 123, 100)
            state = thor_state.normalize(builder)
            due = (thor_state.utcnow() - dt.timedelta(seconds=1)).isoformat()
            state["components"]["odin"].update(status="launching", launch_id="THR-HIT")
            cog._launches(cog._doc(1))["THR-HIT"] = {
                "builder": 123,
                "component": "odin",
                "resolves_at": due,
                "channel_id": 1,
                "interceptors": [{"user": 456, "chance": 10, "roll": 1, "success": True}],
            }
            self.assertTrue(await cog._resolve_launch(1, "THR-HIT", announce=False))
            self.assertEqual(state["components"]["odin"]["status"], "none")
            self.assertNotIn("THR-HIT", cog._launches(cog._doc(1)))
            state["components"]["odin"].update(status="launching", launch_id="THR-SAFE")
            cog._launches(cog._doc(1))["THR-SAFE"] = {
                "builder": 123,
                "component": "odin",
                "resolves_at": due,
                "channel_id": 1,
                "interceptors": [{"user": 456, "chance": 30, "roll": 90, "success": False}],
            }
            self.assertFalse(await cog._resolve_launch(1, "THR-SAFE", announce=False))
            self.assertEqual(state["components"]["odin"]["status"], "orbit")

    async def test_thor_strike_has_no_balance_floor_and_wipes_plushies_and_terrestrial_assets(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = ThorCog(bot)
            attacker = bot.economy.user(1, 123, 100)
            victim = bot.economy.user(1, 456, 100)
            attack_state = thor_state.normalize(attacker)
            attack_state.update(operational=True, rods=1, chambered=True)
            victim.update(
                donuts=0,
                bank=0,
                deep_vault_owned=True,
                deep_vault_balance=1,
                s400_owned=True,
                s400_interceptors=2,
                android21_helper=True,
                android21_helper_at=thor_state.utcnow().isoformat(),
            )
            victim["inventory"]["lock"] = 3
            victim["fish"]["cod"] = 4
            victim["rods"]["basicrod"] = 1
            victim["rod_enchants"]["basicrod"] = ["lucky"]
            victim["plushies"].update(android21=1, labcoat21=2)
            victim["employment"]["career_licence_tier"] = 3
            victim["vehicles"]["b2"].update(owned=True, armed=True)
            bunker = continuity_state.normalize(victim)
            bunker.update(
                owned=True,
                funds=2000000000,
                items={"lock": 1},
                plushies={"labcoat21": 1},
                rod={"id": "abyssal", "enchants": ["sharp"]},
            )
            victim_thor = thor_state.normalize(victim)
            victim_thor.update(
                operational=True,
                rods=2,
                chambered=True,
                gbi_stock=1,
                gbi_building_until=(thor_state.utcnow() + dt.timedelta(hours=1)).isoformat(),
                gbi_building_qty=1,
            )
            interaction = _FakeInteraction(user_id=123)
            await ThorCog.strike.callback(cog, interaction, _FakeUser(456))
            self.assertEqual(victim["deep_vault_balance"], 0)
            self.assertEqual(victim["inventory"], {})
            self.assertFalse(victim["android21_helper"])
            self.assertIsNone(victim["android21_helper_at"])
            self.assertEqual(victim["employment"]["career_licence_tier"], 0)
            self.assertEqual(victim["fish"], {})
            self.assertEqual(victim["rods"], {})
            self.assertFalse(victim["vehicles"]["b2"]["owned"])
            self.assertEqual(victim["plushies"], {})
            self.assertEqual(bunker["funds"], 2000000000)
            self.assertEqual(bunker["items"], {"lock": 1})
            self.assertEqual(bunker["plushies"], {"labcoat21": 1})
            self.assertIsNotNone(continuity_state.parse_time(bunker["sealed_until"]))
            self.assertTrue(victim_thor["operational"])
            self.assertEqual(victim_thor["rods"], 2)
            self.assertEqual(victim_thor["gbi_stock"], 0)
            self.assertIsNone(victim_thor["gbi_building_until"])
            self.assertEqual(victim_thor["gbi_building_qty"], 0)
            self.assertEqual(attack_state["rods"], 0)
            self.assertFalse(attack_state["chambered"])
            self.assertEqual(len(interaction.followup.calls), 2)
            impact_report = interaction.followup.calls[-1]["embed"].description
            self.assertIn("3** plushies destroyed", impact_report)
            self.assertNotIn("Plushies, vanity collectibles", impact_report)
            for call in interaction.followup.calls:
                attached = call.get("file")
                if attached is not None:
                    attached.close()

    async def test_thor_strike_is_fully_resolved_before_release_art_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = ThorCog(bot)
            attacker = bot.economy.user(1, 123, 100)
            victim = bot.economy.user(1, 456, 100)
            attack_state = thor_state.normalize(attacker)
            attack_state.update(operational=True, rods=1, chambered=True)
            victim.update(donuts=123456, deep_vault_balance=789000)
            victim["inventory"]["lock"] = 2
            interaction = _FakeInteraction(user_id=123)

            async def fail_delivery(**kwargs) -> None:
                attached = kwargs.get("file")
                if attached is not None:
                    attached.close()
                raise RuntimeError("simulated Discord delivery failure")

            interaction.followup.send = fail_delivery
            with self.assertRaisesRegex(RuntimeError, "delivery failure"):
                await ThorCog.strike.callback(cog, interaction, _FakeUser(456))
            self.assertEqual(attack_state["rods"], 0)
            self.assertFalse(attack_state["chambered"])
            self.assertIsNotNone(attack_state["last_strike_at"])
            self.assertEqual(victim["donuts"], 0)
            self.assertEqual(victim["deep_vault_balance"], 0)
            self.assertEqual(victim["inventory"], {})

    async def test_operational_aegis_bmd_consumes_one_sm3_on_intercept(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = ThorCog(bot)
            attacker = bot.economy.user(1, 123, 100)
            victim = bot.economy.user(1, 456, 100)
            thor_state.normalize(attacker).update(operational=True, rods=1, chambered=True)
            victim.update(donuts=999, bank=888, deep_vault_balance=777)
            victim["plushies"]["android21"] = 1
            victim["vehicles"]["aegis"].update(owned=True, bmd_owned=True, sm3_ammo=2)
            interaction = _FakeInteraction(user_id=123)
            with patch("cogs.thor.random.randint", return_value=1):
                await ThorCog.strike.callback(cog, interaction, _FakeUser(456))
            self.assertEqual(victim["vehicles"]["aegis"]["sm3_ammo"], 1)
            self.assertEqual(victim["donuts"], 999)
            self.assertEqual(victim["bank"], 888)
            self.assertEqual(victim["deep_vault_balance"], 777)
            self.assertEqual(victim["plushies"]["android21"], 1)
            self.assertIn("EXO-ATMOSPHERIC INTERCEPT", interaction.followup.calls[-1]["embed"].title)
            for call in interaction.followup.calls:
                attached = call.get("file")
                if attached is not None:
                    attached.close()


class MoneyCommandIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejected_roulette_replay_remains_usable_until_a_game_commits(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            player = bot.economy.user(1, 123, 100)
            player["donuts"] = 0
            view = RouletteRepeatView(cog, 123, 100, "red")
            rejected = _FakeInteraction()
            rejected.message = _FakeMessage()
            await view.bet_all.callback(rejected)
            self.assertFalse(view.used)
            self.assertTrue(all((not button.disabled for button in view.children)))
            self.assertEqual(player["donuts"], 0)
            self.assertIn("Minimum bet", rejected.response.calls[-1]["embed"].description)
            player["donuts"] = 1000
            accepted = _FakeInteraction()
            accepted.message = _FakeMessage()
            with patch("cogs.economy.random.randint", return_value=1):
                await view.repeat.callback(accepted)
            self.assertTrue(view.used)
            self.assertTrue(all((button.disabled for button in view.children)))
            self.assertEqual(player["donuts"], 1145)
            await view.repeat.callback(_FakeInteraction())
            self.assertEqual(player["donuts"], 1145)

    async def test_wheel_failed_initial_reply_never_charges_or_leaves_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            cog._wheel_cd = {}
            player = bot.economy.user(1, 123, 100)
            player["donuts"] = 1000
            cog._set_wheel_pot(1, 1000)
            interaction = _FakeInteraction()
            failure = discord.HTTPException(
                SimpleNamespace(status=503, reason="Unavailable"), "Temporary response failure"
            )
            with patch.object(interaction, "edit_original_response", side_effect=failure):
                with self.assertRaises(discord.HTTPException):
                    await EconomyCog.wheel.callback(cog, interaction, 1000)
            self.assertEqual(player["donuts"], 1000)
            self.assertEqual(cog._wheel_pot(1), 1000)
            self.assertNotIn(123, cog._wheel_cd)

    async def test_wheel_half_back_is_exact_for_quintillion_and_unlimited_wagers(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            player = bot.economy.user(1, 123, 100)
            for wager in (10**18 + 3, 10**400 + 3):
                player["donuts"] = wager
                player["plushies"] = {}
                cog._wheel_cd = {123: -1000}
                cog._set_wheel_pot(1, 1000)
                with (
                    patch("cogs.economy.spin_wheel", return_value=(0.5, "Half back")),
                    patch("cogs.economy.asyncio.sleep", return_value=None),
                ):
                    await EconomyCog.wheel.callback(cog, _FakeInteraction(), wager)
                expected = wager // 2 + wager * 6 // 100
                self.assertEqual(player["donuts"], expected)
                self.assertEqual(cog._wheel_pot(1), 1000 + wager - wager * 2 // 100 - wager // 2)

    async def test_large_casino_displays_keep_exact_balances_and_wagers(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            player = bot.economy.user(1, 123, 100)
            wager = 133 * 10**12 + 123
            bank = 2 * 10**18 + 789
            player.update(donuts=wager, bank=bank)
            interaction = _FakeInteraction()
            with patch("cogs.economy.random.randint", return_value=1):
                await cog._roulette_run(interaction, wager, "red")
            exact_profit = wager * 145 // 100
            self.assertEqual(player["donuts"], wager + exact_profit)
            self.assertEqual(player["bank"], bank)
            self.assertEqual(player["casino_stats"]["games"]["roulette"]["wagered"], wager)
            self.assertEqual(player["casino_stats"]["games"]["roulette"]["net"], exact_profit)
            embed = interaction.response.calls[-1]["embed"]
            self.assertIn("325.85 trillion", embed.description)
            self.assertIn("192.85 trillion", embed.description)
            self.assertIn("325.85 trillion", embed.footer.text)
            self.assertNotIn(format(wager, ","), embed.description)
            _assert_embed_valid(self, embed)

    async def test_wheel_remains_pot_funded_without_new_house_shortfall_payments(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            cog._wheel_cd = {123: -1000}
            player = bot.economy.user(1, 123, 100)
            player["donuts"] = 1000
            cog._set_wheel_pot(1, 1000)
            interaction = _FakeInteraction()
            with (
                patch("cogs.economy.spin_wheel", return_value=(3, "3×")),
                patch("cogs.economy.asyncio.sleep", return_value=None),
            ):
                await EconomyCog.wheel.callback(cog, interaction, 1000)
            self.assertEqual(player["donuts"], 2040)
            self.assertEqual(cog._wheel_pot(1), 0)

    async def test_roulette_straight_buff_keeps_progressive_and_updates_autocomplete(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            player = bot.economy.user(1, 123, 100)
            player["donuts"] = 10000
            cog._set_roulette_pot(1, 999)
            interaction = _FakeInteraction()
            with patch("cogs.economy.random.randint", return_value=7):
                await cog._roulette_run(interaction, 100, "7")
            self.assertEqual(player["donuts"], 15000)
            self.assertEqual(cog._roulette_pot(1), 0)
            text = interaction.response.calls[-1]["embed"].description
            self.assertIn("40:1", text)
            self.assertIn("progressive", text.lower())
            self.assertIn("Daily Casino Tour: 1/3", text)
            for query, expected in (("7", "40:1"), ("red", "1.45:1"), ("1st12", "2.7:1")):
                choices = await cog._roulette_autocomplete(_FakeInteraction(), query)
                self.assertTrue(any((expected in choice.name for choice in choices)))

    async def test_blackjack_live_settlement_buffs_normal_and_doubled_stakes(self) -> None:
        for doubled in (False, True):
            with tempfile.TemporaryDirectory() as raw:
                bot = _FakeBot(Path(raw))
                cog = Blackjack(bot)
                user = bot.economy.user(1, 123, 1000)
                interaction = _FakeInteraction()
                shoe = [Card("2", "♠️") for _ in range(20)]
                with patch.object(cog, "_prep_shoe", return_value=(shoe, False)):
                    await cog._start_hand(interaction, 100)
                view = interaction.original_edits[-1]["view"]
                view.player = [Card("10", "♠️"), Card("8", "♥️")]
                view.dealer = [Card("10", "♦️"), Card("7", "♣️")]
                if doubled:
                    await view.double.callback(_FakeInteraction())
                else:
                    await view._resolve()
                self.assertEqual(user["donuts"], 1270 if doubled else 1135)
                self.assertFalse(cog.active)
                text = view.message.edits[-1]["embed"].description
                self.assertIn("Daily Casino Tour: 1/3", text)

    async def test_bet_all_routes_current_wallet_to_every_existing_game_handler(self) -> None:
        wallet = {"donuts": 1000, "bank": 900000, "deep_vault_balance": 800000}
        slots = AsyncMock()
        wheel = AsyncMock()
        roulette = AsyncMock()
        blackjack = AsyncMock()
        cog = SimpleNamespace(
            user=lambda guild_id, user_id: wallet,
            slots=SimpleNamespace(callback=slots),
            wheel=SimpleNamespace(callback=wheel),
            _roulette_run=roulette,
            _start_hand=blackjack,
        )
        interaction = _FakeInteraction()
        session = {"hands": 3, "net": 100, "wins": 1, "losses": 1, "pushes": 1}
        views = [
            CasinoReplayView(cog, 123, "slots", 100),
            CasinoReplayView(cog, 123, "wheel", 100),
            RouletteRepeatView(cog, 123, 100, "red"),
            BlackjackReplayView(cog, interaction.user, 100, session),
        ]
        current_balance = 10**21 + 17
        wallet["donuts"] = current_balance
        for view in views:
            self.assertTrue(await view.interaction_check(interaction))
            self.assertEqual([button.label for button in view.children].count("Bet All"), 1)
            self.assertLessEqual(len(view.children), 5)
            await view.bet_all.callback(interaction)
        slots.assert_awaited_once_with(cog, interaction, current_balance)
        wheel.assert_awaited_once_with(cog, interaction, current_balance)
        roulette.assert_awaited_once_with(interaction, current_balance, "red", repeated=True)
        blackjack.assert_awaited_once_with(interaction, current_balance, edit=True, session=session)
        await views[2].bet_all.callback(interaction)
        self.assertEqual(roulette.await_count, 1)

    async def test_bet_all_buttons_remain_requester_bound(self) -> None:
        cog = SimpleNamespace(user=AsyncMock())
        for view in (
            CasinoReplayView(cog, 123, "slots", 100),
            CasinoReplayView(cog, 123, "wheel", 100),
            RouletteRepeatView(cog, 123, 100, "red"),
            BlackjackReplayView(cog, _FakeInteraction().user, 100, {}),
        ):
            outsider = _FakeInteraction(user_id=456)
            self.assertFalse(await view.interaction_check(outsider))
            self.assertTrue(outsider.response.calls[-1]["ephemeral"])
        cog.user.assert_not_called()

    async def test_bet_all_empty_or_below_minimum_wallet_never_spends_savings(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            cog._slot_cd, cog._wheel_cd = ({}, {})
            cards = Blackjack(bot)
            player = bot.economy.user(1, 123, 100)
            player["bank"] = 900000
            player["deep_vault_balance"] = 800000
            for balance in (0, 5):
                player["donuts"] = balance
                for game in ("slots", "wheel", "roulette", "blackjack"):
                    interaction = _FakeInteraction()
                    if game == "roulette":
                        view = RouletteRepeatView(cog, 123, 100, "red")
                    elif game == "blackjack":
                        view = BlackjackReplayView(cards, interaction.user, 100, {})
                    else:
                        view = CasinoReplayView(cog, 123, game, 100)
                    await view.bet_all.callback(interaction)
                    self.assertIn("Minimum bet", interaction.response.calls[-1]["embed"].description)
                    self.assertEqual(player["donuts"], balance)
                    self.assertEqual(player["bank"], 900000)
                    self.assertEqual(player["deep_vault_balance"], 800000)
            self.assertFalse(cog._slot_cd)
            self.assertFalse(cog._wheel_cd)
            self.assertFalse(cards.active)

    async def test_blackjack_bet_all_escrows_wallet_and_disables_unfunded_double(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = Blackjack(bot)
            player = bot.economy.user(1, 123, 100)
            player.update(donuts=900, bank=500000)
            interaction = _FakeInteraction()
            interaction.message = _FakeMessage()
            session = {"hands": 2, "net": 0, "wins": 1, "losses": 1, "pushes": 0}
            replay = BlackjackReplayView(cog, interaction.user, 100, session)
            shoe = [Card("2", "♠️") for _ in range(20)]
            with patch.object(cog, "_prep_shoe", return_value=(shoe, False)):
                await replay.bet_all.callback(interaction)
            dealt = interaction.original_edits[-1]["view"]
            self.assertEqual(dealt.bet, 900)
            self.assertIs(dealt.session, session)
            self.assertEqual(player["donuts"], 0)
            self.assertEqual(player["bank"], 500000)
            self.assertTrue(dealt.double.disabled)
            self.assertTrue(interaction.response.is_done())
            self.assertIn(cog.hand_key(1, 123), cog.active)
            player["donuts"] = 100
            await replay.bet_all.callback(_FakeInteraction())
            self.assertEqual(player["donuts"], 100)

    async def test_casino_guide_explains_buffs_within_discord_embed_limit(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            interaction = _FakeInteraction()
            await EconomyCog.casino.callback(cog, interaction)
            call = interaction.response.calls[0]
            pages = call["view"].pages if call.get("view") else [call["embed"]]
            for page in pages:
                _assert_embed_valid(self, page)
            casino_text = " ".join((field.value for page in pages for field in page.fields))
            for phrase in (
                "Five-card Charlie",
                "1.35:1",
                "1.45:1",
                "2.7:1",
                "40:1",
                "Non-jackpot spins refund",
            ):
                self.assertIn(phrase, casino_text)
            self.assertIn("6 rods", casino_text)
            self.assertIn("24 online hours", casino_text)
            self.assertIn("50%", casino_text)

    async def test_wheel_house_rebate_and_jackpot_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            cog._wheel_cd = {123: -1000, 456: -1000}
            player = bot.economy.user(1, 123, 100)
            player["donuts"] = 1000
            message = _FakeMessage()
            interaction = _FakeInteraction(user_id=123)

            async def original_response():
                return message

            interaction.original_response = original_response
            with (
                patch("cogs.economy.spin_wheel", return_value=(0, "Bust")),
                patch("cogs.economy.asyncio.sleep", return_value=None),
            ):
                await EconomyCog.wheel.callback(cog, interaction, 100)
            self.assertEqual(player["donuts"], 906)
            self.assertEqual(cog._wheel_pot(1), 1098)
            self.assertIn("House rebate", message.edits[-1]["embed"].description)
            self.assertIsInstance(message.edits[-1]["view"], CasinoReplayView)
            player2 = bot.economy.user(1, 456, 100)
            player2["donuts"] = 1000
            jackpot_message = _FakeMessage()
            jackpot_interaction = _FakeInteraction(user_id=456)

            async def jackpot_response():
                return jackpot_message

            jackpot_interaction.original_response = jackpot_response
            with (
                patch("cogs.economy.spin_wheel", return_value=("POT", "Jackpot")),
                patch("cogs.economy.asyncio.sleep", return_value=None),
            ):
                await EconomyCog.wheel.callback(cog, jackpot_interaction, 100)
            self.assertEqual(player2["donuts"], 2096)
            self.assertNotIn("House rebate", jackpot_message.edits[-1]["embed"].description)

    async def test_roulette_outside_and_dozen_payouts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            player = bot.economy.user(1, 123, 100)
            player["donuts"] = 1000
            with patch("cogs.economy.random.randint", return_value=1):
                red = _FakeInteraction(user_id=123)
                await cog._roulette_run(red, 100, "red")
                dozen = _FakeInteraction(user_id=123)
                await cog._roulette_run(dozen, 100, "1st12")
            self.assertEqual(player["donuts"], 1415)
            self.assertIn("1.45:1", red.response.calls[0]["embed"].description)
            self.assertIn("2.7:1", dozen.response.calls[0]["embed"].description)

    async def test_roulette_result_replaces_repeat_command_with_owned_button(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            player = bot.economy.user(1, 123, 100)
            player["donuts"] = 1000
            interaction = _FakeInteraction(user_id=123)
            with patch("cogs.economy.random.randint", return_value=1):
                await cog._roulette_run(interaction, 10, "red")
            call = interaction.response.calls[0]
            self.assertIsInstance(call.get("view"), RouletteRepeatView)
            self.assertEqual(call["view"].user_id, 123)
            self.assertEqual(call["view"].bet, 10)
            self.assertEqual(call["view"].space, "red")
            self.assertEqual(
                [button.label for button in call["view"].children],
                ["Repeat bet", "Bet 25% wallet", "Bet 50% wallet", "Bet All"],
            )

    async def test_hex_target_is_capped_at_two_casts_per_rolling_day(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            victim_id = 456
            fixed_now = dt.datetime(2026, 9, 23, 12, 0, tzinfo=dt.timezone.utc)
            for caster_id in (101, 102, 103):
                bot.economy.user(1, caster_id, 100).update(donuts=100000000, bank=0)
            with patch("cogs.economy._now", return_value=fixed_now):
                first = _FakeInteraction(user_id=101)
                await EconomyCog.hex_cmd.callback(cog, first, _FakeUser(victim_id))
            with patch("cogs.economy._now", return_value=fixed_now + dt.timedelta(hours=4)):
                second = _FakeInteraction(user_id=102)
                await EconomyCog.hex_cmd.callback(cog, second, _FakeUser(victim_id))
            with patch("cogs.economy._now", return_value=fixed_now + dt.timedelta(hours=8)):
                blocked = _FakeInteraction(user_id=103)
                await EconomyCog.hex_cmd.callback(cog, blocked, _FakeUser(victim_id))
            victim = bot.economy.user(1, victim_id, 100)
            self.assertEqual(len(victim["hex_cast_history"]), 2)
            self.assertEqual(bot.economy.user(1, 101, 100)["donuts"], 50000000)
            self.assertEqual(bot.economy.user(1, 102, 100)["donuts"], 50000000)
            self.assertEqual(bot.economy.user(1, 103, 100)["donuts"], 100000000)
            denial = blocked.response.calls[0]["embed"].description
            self.assertIn("maximum of **2 Hexes**", denial)
            with patch("cogs.economy._now", return_value=fixed_now + dt.timedelta(hours=24, seconds=1)):
                accepted = _FakeInteraction(user_id=103)
                await EconomyCog.hex_cmd.callback(cog, accepted, _FakeUser(victim_id))
            self.assertEqual(bot.economy.user(1, 103, 100)["donuts"], 50000000)
            self.assertEqual(len(victim["hex_cast_history"]), 2)

    def test_active_legacy_hex_counts_toward_new_target_cap(self) -> None:
        now = dt.datetime(2026, 9, 23, 12, 0, tzinfo=dt.timezone.utc)
        victim = {"hexed_until": (now + dt.timedelta(hours=2)).isoformat(), "hex_cast_history": []}
        self.assertIsNone(
            hexes.next_cast_at(victim, now, max_casts=2, window_hours=24, active_duration_hours=3)
        )
        self.assertEqual(len(victim["hex_cast_history"]), 1)
        hexes.record_cast(victim, now, window_hours=24, active_duration_hours=3)
        self.assertIsNotNone(
            hexes.next_cast_at(victim, now, max_casts=2, window_hours=24, active_duration_hours=3)
        )

    async def test_uno_purchase_respects_eight_card_hold_cap(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            user = bot.economy.user(1, 123, 100)
            user.update(donuts=20000000, bank=0)
            user["inventory"]["uno"] = 7
            interaction = _FakeInteraction(user_id=123)
            await EconomyCog.buy.callback(cog, interaction, "uno", 5)
            self.assertEqual(user["inventory"]["uno"], 8)
            self.assertEqual(user["donuts"], 18000000)
            description = interaction.response.calls[0]["embed"].description
            self.assertIn("Capped at 8", description)
            self.assertIn("bought the 1 that fit", description)
            blocked = _FakeInteraction(user_id=123)
            await EconomyCog.buy.callback(cog, blocked, "uno", 1)
            self.assertEqual(user["inventory"]["uno"], 8)
            self.assertEqual(user["donuts"], 18000000)
            self.assertIn("hold at most **8**", blocked.response.calls[0]["embed"].description)

    async def test_ordinary_slots_use_54_percent_base_and_61_with_labcoat(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            cog._slot_cd, cog._wheel_cd = ({}, {})
            cog._slot_cd[123] = -100.0

            async def no_award(*args, **kwargs) -> None:
                return None

            cog._award = no_award
            user = bot.economy.user(1, 123, 100)
            user.update(donuts=1000, bank=0)
            ordinary = _FakeInteraction(user_id=123)
            with patch("cogs.economy.biased_spin", return_value=(["🍬", "⭐", "🍫"], 0)) as mocked_spin:
                await EconomyCog.slots.callback(cog, ordinary, 100)
            self.assertEqual(mocked_spin.call_args.args[1], 54)
            replay_view = ordinary.response.calls[0].get("view")
            self.assertIsInstance(replay_view, CasinoReplayView)
            self.assertEqual(replay_view.bet, 100)
            self.assertEqual(
                [button.label for button in replay_view.children],
                ["Same bet", "Bet 25% wallet", "Bet 50% wallet", "Bet All"],
            )
            outsider = _FakeInteraction(user_id=456)
            self.assertFalse(await replay_view.interaction_check(outsider))
            self.assertTrue(outsider.response.calls[0]["ephemeral"])
            cog._slot_cd[123] = -100.0
            quick = _FakeInteraction(user_id=123)
            with patch("cogs.economy.biased_spin", return_value=(["🍬", "⭐", "🍫"], 0)):
                await replay_view.quarter.callback(quick)
            self.assertEqual(quick.response.calls[0]["view"].bet, 225)
            self.assertEqual(user["donuts"], 675)
            user["plushies"]["labcoat"] = 1
            cog._slot_cd[123] = -100.0
            equipped = _FakeInteraction(user_id=123)
            with patch("cogs.economy.biased_spin", return_value=(["🍬", "⭐", "🍫"], 0)) as mocked_spin:
                await EconomyCog.slots.callback(cog, equipped, 100)
            self.assertEqual(mocked_spin.call_args.args[1], 61)
            self.assertIn(
                "Lab Coat equipped: +7pp slots perk", equipped.response.calls[0]["embed"].description
            )

    async def test_deep_vault_withdrawal_is_scheduled_for_five_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            user = bot.economy.user(1, 123, 100)
            user.update(
                deep_vault_owned=True,
                deep_vault_balance=10000,
                deep_vault_withdraw_amount=0,
                deep_vault_withdraw_at=None,
            )
            interaction = _FakeInteraction(user_id=123)
            before = dt.datetime.now(dt.timezone.utc)
            await EconomyCog.deepvault_withdraw.callback(cog, interaction, "all")
            ready = dt.datetime.fromisoformat(user["deep_vault_withdraw_at"])
            self.assertAlmostEqual((ready - before).total_seconds(), 300, delta=2)
            self.assertEqual(user["deep_vault_withdraw_amount"], 10000)
            self.assertTrue(interaction.response.calls[0]["ephemeral"])

    async def test_attacklog_is_public_silent_self_only_and_success_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            bot.ledger = Ledger(Path(raw) / "ledger")
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            await bot.ledger.record(1, 123, -2500000, "robbank-success", other=456)
            await bot.ledger.record(1, 789, -10, "robbank-fail", other=123)
            await bot.ledger.record(1, 321, 0, "vehicle-u2-recon", other=123)
            interaction = _FakeInteraction(user_id=123)
            await EconomyCog.attacklog.callback(cog, interaction, 20)
            self.assertEqual(interaction.response.deferred, {"thinking": True, "ephemeral": False})
            self.assertEqual(len(interaction.original_edits), 1)
            call = interaction.original_edits[0]
            self.assertNotIn("ephemeral", call)
            description = call["embed"].description
            self.assertIn("ID `456`", description)
            self.assertIn("Majin Drill vault robbery", description)
            self.assertIn("ID `321`", description)
            self.assertIn("U-2S Dragon Lady reconnaissance", description)
            self.assertNotIn("ID `789`", description)
            self.assertNotIn("User 789", description)
            self.assertNotIn("<@", description)

    async def test_aa_shield_charges_twenty_five_million_flat(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            user = bot.economy.user(1, 123, 100)
            user.update(donuts=25500000, bank=0, aa_shield_until=None)
            interaction = _FakeInteraction(user_id=123)
            await EconomyCog.aa_shield.callback(cog, interaction)
            self.assertEqual(user["donuts"], 500000)
            self.assertIsNotNone(user["aa_shield_until"])
            self.assertIn("25,000,000", interaction.response.calls[0]["embed"].description)

    async def test_deposit_and_withdraw_use_exact_shorthand_and_correct_balances(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            user = bot.economy.user(1, 123, 100)
            user.update(donuts=10000000, bank=4000000)
            deposit = _FakeInteraction(user_id=123)
            await EconomyCog.deposit.callback(cog, deposit, "2.5m")
            self.assertEqual(user["donuts"], 7500000)
            self.assertEqual(user["bank"], 6500000)
            withdraw = _FakeInteraction(user_id=123)
            await EconomyCog.withdraw.callback(cog, withdraw, "half")
            self.assertEqual(user["donuts"], 10750000)
            self.assertEqual(user["bank"], 3250000)


def _assert_embed_valid(test: unittest.TestCase, embed) -> None:
    test.assertLessEqual(len(embed), 6000)
    test.assertLessEqual(len(embed.fields), 25)
    test.assertLessEqual(len(embed.title or ""), 256)
    test.assertLessEqual(len(embed.description or ""), 4096)
    for field in embed.fields:
        test.assertLessEqual(len(field.name), 256)
        test.assertLessEqual(len(field.value), 1024)


class PlayerFacingOutputTests(unittest.IsolatedAsyncioTestCase):
    async def test_balance_privately_reports_active_and_expired_hex_status(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            economy = object.__new__(EconomyCog)
            economy.bot = bot
            economy.econ = bot.economy
            user = economy.user(1, 123)
            future = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=3)
            user["hexed_until"] = future.isoformat()
            user["hexed_by"] = 456
            active_interaction = _FakeInteraction(user_id=123)
            await EconomyCog.balance.callback(economy, active_interaction, None)
            active_embed = active_interaction.response.calls[0]["embed"]
            active_field = next((field for field in active_embed.fields if field.name == "🩸 Hex status"))
            self.assertIn("HEXED", active_field.value)
            self.assertIn("remaining", active_field.value)
            self.assertIn(f"<t:{int(future.timestamp())}:R>", active_field.value)
            user["hexed_until"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)).isoformat()
            expired_interaction = _FakeInteraction(user_id=123)
            await EconomyCog.balance.callback(economy, expired_interaction, None)
            expired_embed = expired_interaction.response.calls[0]["embed"]
            expired_field = next((field for field in expired_embed.fields if field.name == "🩸 Hex status"))
            self.assertEqual(expired_field.value, "✅ Not hexed.")
            self.assertIsNone(user["hexed_until"])
            self.assertIsNone(user["hexed_by"])
            observer = _FakeInteraction(user_id=999)
            await EconomyCog.balance.callback(economy, observer, _FakeUser(123))
            observed_embed = observer.response.calls[0]["embed"]
            self.assertNotIn("🩸 Hex status", [field.name for field in observed_embed.fields])

    async def test_help_warfare_and_atlas_stay_within_discord_limits(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            help_interaction = _FakeInteraction()
            await General.help_cmd.callback(General(bot), help_interaction, None)
            self.assertEqual(len(help_interaction.response.calls), 1)
            self.assertFalse(help_interaction.response.calls[0]["ephemeral"])
            _assert_embed_valid(self, help_interaction.response.calls[0]["embed"])
            category_interaction = _FakeInteraction()
            await General.help_cmd.callback(
                General(bot),
                category_interaction,
                app_commands.Choice(name="Solar System", value="Solar System"),
            )
            self.assertFalse(category_interaction.response.calls[0]["ephemeral"])
            economy = object.__new__(EconomyCog)
            economy.bot = bot
            economy.econ = bot.economy
            warfare_interaction = _FakeInteraction()
            await EconomyCog.warfare.callback(economy, warfare_interaction)
            call = warfare_interaction.response.calls[0]
            self.assertGreaterEqual(len(call["view"].pages), 8)
            for page in call["view"].pages:
                _assert_embed_valid(self, page)
            casino_interaction = _FakeInteraction()
            await EconomyCog.casino.callback(economy, casino_interaction)
            casino_call = casino_interaction.response.calls[0]
            casino_pages = casino_call["view"].pages if casino_call.get("view") else [casino_call["embed"]]
            for page in casino_pages:
                _assert_embed_valid(self, page)
            stats_interaction = _FakeInteraction()
            stats_user = economy.user(1, stats_interaction.user.id)
            stats_user["casino_tour"] = {
                "day": dt.datetime.now(dt.timezone.utc).date().isoformat(),
                "games": ["slots", "wheel"],
                "claimed": False,
                "streak": 2,
                "best_streak": 4,
                "completions": 6,
            }
            await EconomyCog.casino_stats.callback(economy, stats_interaction)
            stats_embed = stats_interaction.response.calls[0]["embed"]
            _assert_embed_valid(self, stats_embed)
            tour_field = next((field for field in stats_embed.fields if field.name == "🎟️ Daily Casino Tour"))
            self.assertIn("1/3", tour_field.value)
            self.assertNotIn("Slots", tour_field.value)
            self.assertIn("Streak **2**", tour_field.value)
            shop_interaction = _FakeInteraction()
            await EconomyCog.shop.callback(economy, shop_interaction)
            _assert_embed_valid(self, shop_interaction.response.calls[0]["embed"])
            country_cog = Countries(bot)
            guide_interaction = _FakeInteraction()
            await Countries.guide.callback(country_cog, guide_interaction)
            guide_pages = guide_interaction.response.calls[0]["view"].pages
            self.assertEqual(len(guide_pages), 4)
            for page in guide_pages:
                _assert_embed_valid(self, page)
            country_text = " ".join((field.value for page in guide_pages for field in page.fields))
            self.assertNotIn("enters its regular mission cooldown", country_text)
            self.assertIn("separate country-campaign cooldown", country_text)
            for label in OFFENSE:
                self.assertIn(str(OFFENSE[label]), country_text)
            for atlas_page in range(1, 14):
                atlas_interaction = _FakeInteraction()
                await Countries.atlas.callback(country_cog, atlas_interaction, atlas_page)
                atlas_call = atlas_interaction.response.calls[0]
                atlas_embed = atlas_call["embed"]
                atlas_view = atlas_call["view"]
                _assert_embed_valid(self, atlas_embed)
                self.assertTrue(atlas_embed.description)
                self.assertEqual(len(atlas_view.pages), 13)
                self.assertEqual(atlas_view.index, atlas_page - 1)
                self.assertEqual(atlas_view.counter.label, f"{atlas_page}/13")
                self.assertEqual(atlas_view.prev.disabled, atlas_page == 1)
                self.assertEqual(atlas_view.next.disabled, atlas_page == 13)
            now = countries.now().isoformat()
            doc = bot.economy.store.load(1)
            for country in countries.COUNTRIES:
                countries.state(doc)["territories"][country.id] = {
                    "owner": 123,
                    "acquired_at": now,
                    "last_collected_at": now,
                    "fortification": 0,
                    "development_level": 20,
                }
            portfolio_interaction = _FakeInteraction()
            await Countries.portfolio.callback(country_cog, portfolio_interaction)
            portfolio_pages = portfolio_interaction.response.calls[0]["view"].pages
            self.assertGreater(len(portfolio_pages), 1)
            for page in portfolio_pages:
                _assert_embed_valid(self, page)


class CountryCommandIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_leopard_garrison_adds_defense_and_spends_one_dm63_round(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            country_cog = Countries(bot)
            attacker = bot.economy.user(1, 123, 100)
            attacker.update(donuts=100000000, bank=0)
            attacker["vehicles"]["b2"].update(owned=True, armed=True)
            defender_id = 456
            defender = bot.economy.user(1, defender_id, 100)
            defender["vehicles"].setdefault("leopard2a7", {}).update(
                owned=True,
                ammo=2,
                garrison_country="fji",
                garrison_transfer_until=None,
                leopard2a7_damaged=False,
            )
            doc = bot.economy.store.load(1)
            current = countries.now().isoformat()
            countries.state(doc)["territories"]["fji"] = {
                "owner": defender_id,
                "acquired_at": current,
                "last_collected_at": current,
                "fortification": 0,
                "development_level": 0,
            }
            interaction = _FakeInteraction(user_id=123)
            with patch("cogs.countries.random.randint", return_value=100):
                await Countries.invade.callback(
                    country_cog,
                    interaction,
                    "fji",
                    app_commands.Choice(name="B-2 Spirit", value="b2"),
                    20000000,
                )
            self.assertEqual(defender["vehicles"]["leopard2a7"]["ammo"], 1)
            embed = interaction.response.calls[0]["embed"]
            armored = next((field.value for field in embed.fields if field.name == "Armored operations"))
            self.assertIn("+25 defense power", armored)
            self.assertIn("spent one DM63 round", armored)

    async def test_m1a2_garrison_adds_defense_and_spends_one_round(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            country_cog = Countries(bot)
            attacker = bot.economy.user(1, 123, 100)
            attacker.update(donuts=100000000, bank=0)
            attacker["vehicles"]["b2"].update(owned=True, armed=True)
            defender_id = 456
            defender = bot.economy.user(1, defender_id, 100)
            defender["vehicles"].setdefault("m1a2", {}).update(
                owned=True, ammo=2, garrison_country="fji", garrison_transfer_until=None, m1a2_damaged=False
            )
            doc = bot.economy.store.load(1)
            current = countries.now().isoformat()
            countries.state(doc)["territories"]["fji"] = {
                "owner": defender_id,
                "acquired_at": current,
                "last_collected_at": current,
                "fortification": 0,
                "development_level": 0,
            }
            interaction = _FakeInteraction(user_id=123)
            with patch("cogs.countries.random.randint", return_value=100):
                await Countries.invade.callback(
                    country_cog,
                    interaction,
                    "fji",
                    app_commands.Choice(name="B-2 Spirit", value="b2"),
                    20000000,
                )
            self.assertEqual(defender["vehicles"]["m1a2"]["ammo"], 1)
            embed = interaction.response.calls[0]["embed"]
            armored = next((field.value for field in embed.fields if field.name == "Armored operations"))
            self.assertIn("+25 defense power", armored)
            self.assertIn("spent one M829A4 round", armored)

    async def test_active_m1a2_breach_grants_only_the_breacher_bonus(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            country_cog = Countries(bot)
            attacker = bot.economy.user(1, 123, 100)
            attacker.update(donuts=100000000, bank=0)
            attacker["vehicles"]["b2"].update(owned=True, armed=True)
            doc = bot.economy.store.load(1)
            current = countries.now()
            countries.state(doc)["territories"]["fji"] = {
                "owner": 456,
                "acquired_at": current.isoformat(),
                "last_collected_at": current.isoformat(),
                "fortification": 0,
                "development_level": 0,
                "m1a2_breached_by": 123,
                "m1a2_breached_until": (current + dt.timedelta(minutes=30)).isoformat(),
            }
            interaction = _FakeInteraction(user_id=123)
            with patch("cogs.countries.random.randint", return_value=100):
                await Countries.invade.callback(
                    country_cog,
                    interaction,
                    "fji",
                    app_commands.Choice(name="B-2 Spirit", value="b2"),
                    20000000,
                )
            embed = interaction.response.calls[0]["embed"]
            armored = next((field.value for field in embed.fields if field.name == "Armored operations"))
            self.assertIn("+20 attack power", armored)

    async def test_automatic_country_income_caps_catchup_and_tracks_reconstruction(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            country_cog = Countries(bot)
            owner = bot.economy.user(1, 123, 100)
            owner["donuts"] = 0
            current = countries.now()
            territory = {
                "owner": 123,
                "acquired_at": (current - dt.timedelta(days=4)).isoformat(),
                "last_collected_at": (current - dt.timedelta(hours=72)).isoformat(),
                "fortification": 0,
                "development_level": 0,
                "reconstruction_until": (current - dt.timedelta(hours=24)).isoformat(),
            }
            countries.state(bot.economy.store.load(1))["territories"]["plw"] = territory
            palau = countries.BY_ID["plw"]
            daily_net = countries.gross_per_day(palau, 0, territory) - countries.upkeep_per_day(
                palau, 0, territory
            )
            payouts = await country_cog.settle_income(1, current)
            self.assertEqual(payouts[123], int(daily_net * 1.4))
            self.assertEqual(owner["donuts"], payouts[123])
            self.assertEqual(territory["last_collected_at"], current.isoformat())
            self.assertGreaterEqual(territory["income_remainder"], 0)
            self.assertLess(territory["income_remainder"], 1)

    async def test_country_spending_accepts_half_all_and_shorthand(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            user = bot.economy.user(1, 123, 100)
            user.update(donuts=10000000, bank=4000000)
            interaction = _FakeInteraction(user_id=123)
            interaction.client = bot
            transformer = SpendTransformer()
            self.assertEqual(await transformer.transform(interaction, "half"), 7000000)
            self.assertEqual(await transformer.transform(interaction, "full"), 14000000)
            self.assertEqual(await transformer.transform(interaction, "2.5m"), 2500000)

    async def test_claim_auto_income_and_country_cooldown_share_one_economy(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            economy_cog = object.__new__(EconomyCog)
            economy_cog.bot = bot
            economy_cog.econ = bot.economy
            bot.cogs["Economy"] = economy_cog
            country_cog = Countries(bot)
            owner = bot.economy.user(1, 1, 100)
            owner["donuts"] = 200000000
            claim_interaction = _FakeInteraction(user_id=1)
            await Countries.claim.callback(country_cog, claim_interaction, "plw")
            territory = countries.state(bot.economy.store.load(1))["territories"]["plw"]
            self.assertEqual(territory["owner"], 1)
            self.assertEqual(owner["donuts"], 100000000)
            one_country_portfolio = _FakeInteraction(user_id=1)
            await Countries.portfolio.callback(country_cog, one_country_portfolio)
            self.assertNotIn("view", one_country_portfolio.response.calls[0])
            territory["last_collected_at"] = (countries.now() - dt.timedelta(hours=24)).isoformat()
            before = owner["donuts"]
            payouts = await country_cog.settle_income(1, countries.now())
            self.assertGreater(payouts[1], 0)
            self.assertGreater(owner["donuts"], before)
            develop_interaction = _FakeInteraction(user_id=1)
            before_development = owner["donuts"]
            await Countries.develop.callback(country_cog, develop_interaction, "plw", 2)
            self.assertEqual(territory["development_level"], 2)
            self.assertLess(owner["donuts"], before_development)
            self.assertGreater(
                countries.effective_gdp_b(countries.BY_ID["plw"], territory), countries.BY_ID["plw"].gdp_b
            )
            owner.update(donuts=40000000, bank=0)
            half_develop = _FakeInteraction(user_id=1)
            await Countries.develop.callback(country_cog, half_develop, "plw", "half")
            self.assertEqual(territory["development_level"], 5)
            self.assertEqual(owner["donuts"], 20500000)
            defender = bot.economy.user(1, 2, 100)
            current = countries.now()
            countries.state(bot.economy.store.load(1))["territories"]["fji"] = {
                "owner": 2,
                "acquired_at": current.isoformat(),
                "last_collected_at": current.isoformat(),
                "fortification": 0,
            }
            vehicle = owner["vehicles"]["himars"]
            vehicle.update(
                owned=True,
                ammo=2,
                building_until=None,
                loading_until=None,
                last_deploy_at=current.isoformat(),
            )
            owner["donuts"] = 100000000
            invade_interaction = _FakeInteraction(user_id=1)
            with (
                patch("cogs.countries.random.randint", return_value=1),
                patch("cogs.countries.secrets.randbelow", return_value=2),
            ):
                await Countries.invade.callback(
                    country_cog,
                    invade_interaction,
                    "fji",
                    app_commands.Choice(name="M142 HIMARS", value="himars"),
                    20000000,
                )
            response_text = invade_interaction.response.calls[0]["embed"].description or ""
            self.assertIn("VICTORY", response_text)
            self.assertEqual(owner["donuts"], 80000000)
            self.assertEqual(vehicle["ammo"], 1)
            self.assertEqual(vehicle["last_deploy_at"], current.isoformat())
            campaign_ready = countries.parse_time(owner["country_invasion_until"])
            self.assertIsNotNone(campaign_ready)
            remaining = (campaign_ready - countries.now()).total_seconds() / 60
            self.assertGreater(remaining, 11.9)
            self.assertLess(remaining, 12.1)
            owner["strategic_lockdown_until"] = (countries.now() + dt.timedelta(hours=1)).isoformat()
            blocked_invasion = _FakeInteraction(user_id=1)
            await Countries.invade.callback(
                country_cog,
                blocked_invasion,
                "afg",
                app_commands.Choice(name="M142 HIMARS", value="himars"),
                20000000,
            )
            self.assertIn(
                "disabled by a B-52 strike", blocked_invasion.response.calls[0]["embed"].description
            )
            self.assertEqual(owner["donuts"], 80000000)
            self.assertEqual(vehicle["ammo"], 1)

    async def test_conquest_settles_defenders_pending_income_before_transfer(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            economy_cog = object.__new__(EconomyCog)
            economy_cog.bot = bot
            economy_cog.econ = bot.economy
            bot.cogs["Economy"] = economy_cog
            country_cog = Countries(bot)
            current = countries.now()
            attacker = bot.economy.user(1, 123, 100)
            attacker.update(donuts=100000000, bank=0)
            attacker["vehicles"]["himars"].update(owned=True, ammo=1, building_until=None, loading_until=None)
            defender = bot.economy.user(1, 456, 100)
            defender.update(donuts=0, bank=0)
            territory = {
                "owner": 456,
                "acquired_at": (current - dt.timedelta(days=2)).isoformat(),
                "last_collected_at": (current - dt.timedelta(days=1)).isoformat(),
                "fortification": 0,
                "development_level": 0,
                "reconstruction_until": None,
            }
            countries.state(bot.economy.store.load(1))["territories"]["fji"] = territory
            expected = max(
                0,
                countries.gross_per_day(countries.BY_ID["fji"], 0, territory)
                - countries.upkeep_per_day(countries.BY_ID["fji"], 0, territory),
            )
            interaction = _FakeInteraction(user_id=123)
            with (
                patch("cogs.countries.countries.now", return_value=current),
                patch("cogs.countries.random.randint", return_value=1),
                patch("cogs.countries.secrets.randbelow", return_value=0),
            ):
                await Countries.invade.callback(
                    country_cog,
                    interaction,
                    "fji",
                    app_commands.Choice(name="M142 HIMARS", value="himars"),
                    20000000,
                )
            self.assertEqual(territory["owner"], 123)
            self.assertEqual(defender["donuts"], expected)
            self.assertEqual(territory["last_collected_at"], current.isoformat())

    async def test_abandon_settles_pending_income_before_country_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            country_cog = Countries(bot)
            current = countries.now()
            owner = bot.economy.user(1, 123, 100)
            owner.update(donuts=0, bank=0)
            territory = {
                "owner": 123,
                "acquired_at": (current - dt.timedelta(days=2)).isoformat(),
                "last_collected_at": (current - dt.timedelta(days=1)).isoformat(),
                "fortification": 0,
                "development_level": 0,
                "reconstruction_until": None,
            }
            doc = bot.economy.store.load(1)
            countries.state(doc)["territories"]["plw"] = territory
            expected = max(
                0,
                countries.gross_per_day(countries.BY_ID["plw"], 0, territory)
                - countries.upkeep_per_day(countries.BY_ID["plw"], 0, territory),
            )
            interaction = _FakeInteraction(user_id=123)
            with patch("cogs.countries.countries.now", return_value=current):
                await Countries.abandon.callback(country_cog, interaction, "plw")
            self.assertNotIn("plw", countries.state(doc)["territories"])
            self.assertEqual(owner["donuts"], expected)
            self.assertIn(f"{expected:,}", interaction.response.calls[0]["embed"].description)

    async def test_portfolio_shows_reconstruction_adjusted_current_income(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            country_cog = Countries(bot)
            current = countries.now()
            bot.economy.user(1, 123, 100)
            countries.state(bot.economy.store.load(1))["territories"]["plw"] = {
                "owner": 123,
                "acquired_at": current.isoformat(),
                "last_collected_at": current.isoformat(),
                "fortification": 0,
                "development_level": 0,
                "reconstruction_until": (current + dt.timedelta(hours=3)).isoformat(),
            }
            interaction = _FakeInteraction(user_id=123)
            with patch("cogs.countries.countries.now", return_value=current):
                await Countries.portfolio.callback(country_cog, interaction)
            description = _status_reply_text(interaction.response.calls[0])
            self.assertIn("during reconstruction", description)
            self.assertIn("normal +", description)

    async def test_develop_revalidates_country_after_income_settlement(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            country_cog = Countries(bot)
            current = countries.now()
            owner = bot.economy.user(1, 123, 100)
            owner.update(donuts=100000000, bank=0)
            territory = {
                "owner": 123,
                "acquired_at": current.isoformat(),
                "last_collected_at": current.isoformat(),
                "fortification": 0,
                "development_level": 0,
                "reconstruction_until": None,
            }
            countries.state(bot.economy.store.load(1))["territories"]["plw"] = territory

            async def change_owner_while_settling(*args, **kwargs):
                territory["owner"] = 456
                return {}

            country_cog.settle_income = change_owner_while_settling
            interaction = _FakeInteraction(user_id=123)
            await Countries.develop.callback(country_cog, interaction, "plw", "1")
            self.assertEqual(owner["donuts"], 100000000)
            self.assertEqual(territory["development_level"], 0)
            self.assertIn("Nothing was charged", interaction.response.calls[0]["embed"].description)

    async def test_reinforcement_guardrail_charges_only_useful_amount(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            country_cog = Countries(bot)
            user = bot.economy.user(1, 123, 100)
            user.update(donuts=100000000, bank=0)
            current = countries.now().isoformat()
            territory = {
                "owner": 123,
                "acquired_at": current,
                "last_collected_at": current,
                "fortification": FORTIFICATION_MAX_USEFUL - 5625000,
            }
            countries.state(bot.economy.store.load(1))["territories"]["fji"] = territory
            interaction = _FakeInteraction(user_id=123)
            await Countries.reinforce.callback(country_cog, interaction, "fji", 100000000)
            self.assertEqual(territory["fortification"], FORTIFICATION_MAX_USEFUL)
            self.assertEqual(user["donuts"], 94375000)
            self.assertIn("reduced the charge", interaction.response.calls[0]["embed"].description)
            second = _FakeInteraction(user_id=123)
            await Countries.reinforce.callback(country_cog, second, "fji", 1000000)
            self.assertEqual(user["donuts"], 94375000)
            self.assertIn("No donuts were charged", second.response.calls[0]["embed"].description)

    async def test_invasion_guardrail_charges_only_effective_war_chest(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            country_cog = Countries(bot)
            attacker = bot.economy.user(1, 123, 100)
            attacker.update(donuts=1000000000, bank=0)
            attacker["vehicles"]["b2"].update(owned=True, armed=True)
            current = countries.now().isoformat()
            territory = {
                "owner": 456,
                "acquired_at": current,
                "last_collected_at": current,
                "fortification": 0,
            }
            countries.state(bot.economy.store.load(1))["territories"]["fji"] = territory
            value = countries.economic_value(countries.BY_ID["fji"], territory)
            cap = useful_war_chest_cap(value)
            interaction = _FakeInteraction(user_id=123)
            await Countries.invade.callback(
                country_cog,
                interaction,
                "fji",
                app_commands.Choice(name="B-2 Spirit", value="b2"),
                1000000000,
            )
            self.assertEqual(attacker["donuts"], 1000000000 - cap)
            self.assertFalse(attacker["vehicles"]["b2"]["armed"])
            embed = interaction.response.calls[0]["embed"]
            self.assertTrue(any((field.name == "Spending guardrail" for field in embed.fields)))
            self.assertIn(f"🍩 {cap:,} war chest", embed.fields[0].value)

    async def test_unclaimed_country_can_be_occupied_by_invasion(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            country_cog = Countries(bot)
            attacker = bot.economy.user(1, 123, 100)
            attacker.update(donuts=100000000, bank=0)
            attacker["vehicles"]["b2"].update(owned=True, armed=True)
            doc = bot.economy.store.load(1)
            self.assertNotIn("fji", countries.state(doc)["territories"])
            interaction = _FakeInteraction(user_id=123)
            with patch("cogs.countries.random.randint", return_value=1):
                await Countries.invade.callback(
                    country_cog,
                    interaction,
                    "fji",
                    app_commands.Choice(name="B-2 Spirit", value="b2"),
                    20000000,
                )
            territory = countries.state(doc)["territories"]["fji"]
            self.assertEqual(territory["owner"], 123)
            self.assertEqual(territory["method"], "military occupation")
            self.assertEqual(territory["development_level"], 0)
            self.assertEqual(territory["fortification"], 0)
            self.assertGreater(countries.parse_time(territory["reconstruction_until"]), countries.now())
            self.assertEqual(attacker["donuts"], 80000000)
            self.assertFalse(attacker["vehicles"]["b2"]["armed"])
            embed = interaction.response.calls[0]["embed"]
            self.assertIn("occupied unclaimed", embed.description)
            expected_defense = countries.TIER_DEFENSE[countries.BY_ID["fji"].tier]
            self.assertIn(f"vs defense {expected_defense}", embed.fields[1].value)

    async def test_failed_unclaimed_invasion_consumes_resources_and_leaves_it_free(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            country_cog = Countries(bot)
            attacker = bot.economy.user(1, 123, 100)
            attacker.update(donuts=25000000, bank=0)
            attacker["vehicles"]["b2"].update(owned=True, armed=True)
            doc = bot.economy.store.load(1)
            interaction = _FakeInteraction(user_id=123)
            with patch("cogs.countries.random.randint", return_value=100):
                await Countries.invade.callback(
                    country_cog,
                    interaction,
                    "plw",
                    app_commands.Choice(name="B-2 Spirit", value="b2"),
                    5000000,
                )
            self.assertNotIn("plw", countries.state(doc)["territories"])
            self.assertEqual(attacker["donuts"], 20000000)
            self.assertFalse(attacker["vehicles"]["b2"]["armed"])
            self.assertIsNone(attacker["vehicles"]["b2"]["last_deploy_at"])
            self.assertIsNotNone(attacker["country_invasion_until"])
            self.assertIn("kept 🇵🇼 **Palau** unclaimed", interaction.response.calls[0]["embed"].description)


class AuditRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_colony_production_respects_disabled_economy(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            bot.guilds = [SimpleNamespace(id=1)]
            bot.config.for_guild(1).set("economy.enabled", False)
            user = bot.economy.user(1, 123, 0)
            colony = space_state.colony(space_state.normalize(user), "mars")
            anchor = (space_state.now() - dt.timedelta(days=1)).isoformat()
            colony.update(gdp=8000, income_at=anchor)
            space_state.galaxy(bot.economy.store.load(1))["claims"]["mars"] = 123
            cog = SpaceCog(bot)
            await cog.income_loop.coro(cog)
            self.assertEqual(user["donuts"], 0)
            self.assertEqual(colony["income_at"], anchor)

    async def test_colony_catchup_cap_is_consumed_once_and_rounding_is_carried(self) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        user = {"donuts": 0}
        colony = {"gdp": 8000, "income_at": (now - dt.timedelta(days=30, minutes=17)).isoformat()}
        amount, changed = space_state.settle_colony_income(user, colony, now)
        self.assertTrue(changed)
        self.assertEqual(amount, 700)
        self.assertEqual(space_state.at(colony["income_at"]), now - dt.timedelta(minutes=17))
        self.assertEqual(space_state.settle_colony_income(user, colony, now), (0, False))
        self.assertEqual(user["donuts"], 700)
        user, colony = ({"donuts": 0}, {"gdp": 8000, "income_at": now.isoformat()})
        for hour in range(1, 25):
            space_state.settle_colony_income(user, colony, now + dt.timedelta(hours=hour))
        self.assertEqual(user["donuts"], 100)
        self.assertEqual(colony["income_remainder"], 0)

    async def test_development_uses_the_same_capped_pre_upgrade_income(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = SpaceCog(bot)
            user = bot.economy.user(1, 123, 0)
            user["donuts"] = 10**20
            colony = space_state.colony(space_state.normalize(user), "mars")
            colony.update(gdp=8000, income_at=(space_state.now() - dt.timedelta(days=30)).isoformat())
            space_state.galaxy(bot.economy.store.load(1))["claims"]["mars"] = 123
            cost = space_state.gdp(7, 5) // 4
            await SpaceCog.develop.callback(cog, _FakeInteraction(), "mars")
            self.assertEqual(user["donuts"], 10**20 - cost + 700)
            self.assertEqual(colony["development"], 1)
            before = user["donuts"]
            self.assertEqual(space_state.settle_colony_income(user, colony, space_state.now()), (0, False))
            self.assertEqual(user["donuts"], before)

    async def test_cinder_success_is_recorded_for_each_victim_without_network_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            bot.ledger = Ledger(Path(raw) / "ledger")
            bot.get_channel = lambda channel_id: None
            cog = ImperialStarDestroyerCog(bot)
            builder = bot.economy.user(1, 123, 0)
            bot.economy.user(1, 456, 500)
            isd_state.normalize(builder).update(operational=True, active_window_id="CINDER-TEST")
            isd_state.windows(bot.economy.store.load(1))["CINDER-TEST"] = {
                "kind": "cinder",
                "guild_id": 1,
                "builder": 123,
                "remaining_seconds": 0,
                "interceptors": [],
                "channel_id": 1,
            }
            with patch.object(cog, "_write_backup"):
                await cog._resolve_window(1, "CINDER-TEST")
            rows = await bot.ledger.read_security(1, victim=456)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["reason"], "isd-cinder-impact")
            self.assertEqual(rows[0]["security_actor"], 123)

    async def test_window_loop_continues_after_one_guild_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = ImperialStarDestroyerCog(bot)
            bot.guilds = [SimpleNamespace(id=1), SimpleNamespace(id=2)]

            async def ready():
                return None

            bot.wait_until_ready = ready
            closed = iter((False, True))
            bot.is_closed = lambda: next(closed)
            with (
                patch("cogs.imperial_star_destroyer.asyncio.sleep", return_value=None),
                patch.object(
                    cog, "_advance_windows", side_effect=(RuntimeError("temporary failure"), None)
                ) as advance,
                self.assertLogs("szofie.isd", level="ERROR"),
            ):
                await cog._window_loop()
            self.assertEqual(advance.await_count, 2)
            self.assertEqual([call.args[0] for call in advance.await_args_list], [1, 2])

    async def test_alias_servers_share_one_blackjack_hand_and_cannot_double_twice(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            bot.economy.store.aliases = {2: 1}
            cog = Blackjack(bot)
            user = bot.economy.user(1, 123, 1000)
            interaction = _FakeInteraction()
            shoe = [Card("2", "♠️") for _ in range(20)]
            with patch.object(cog, "_prep_shoe", return_value=(shoe, False)):
                await cog._start_hand(interaction, 100)
            view = interaction.original_edits[-1]["view"]
            self.assertTrue(interaction.response.is_done())
            second_server = _FakeInteraction(guild_id=2)
            await cog._start_hand(second_server, 100)
            self.assertEqual(user["donuts"], 900)
            self.assertIn("Finish your current hand", second_server.response.calls[-1]["embed"].description)
            with patch.object(view, "_resolve", return_value=None) as resolve:
                await view.double.callback(_FakeInteraction())
                await view.double.callback(_FakeInteraction())
                self.assertEqual(resolve.await_count, 1)
            self.assertEqual(user["donuts"], 800)
            cards = len(view.player)
            await view.hit.callback(_FakeInteraction())
            await view.surrender.callback(_FakeInteraction())
            self.assertEqual(len(view.player), cards)
            self.assertEqual(user["donuts"], 800)
            await view._resolve()
            self.assertFalse(cog.active)
            settled_balance = user["donuts"]
            await view.double.callback(_FakeInteraction())
            await view.hit.callback(_FakeInteraction())
            self.assertEqual(user["donuts"], settled_balance)

    async def test_overlapping_blackjack_deals_are_rejected_before_a_second_stake(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            cog = Blackjack(_FakeBot(Path(raw)))
            lock = asyncio.Lock()
            cog._deal_locks[1, 123] = lock
            async with lock:
                interaction = _FakeInteraction()
                await cog._start_hand(interaction, 100)
            self.assertIn("already being dealt", interaction.response.calls[-1]["embed"].description)
            self.assertFalse(cog.active)


if __name__ == "__main__":
    unittest.main()
