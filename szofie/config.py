"""Per-guild settings with dot-notation access."""

from __future__ import annotations
import copy
import math
from typing import Any, Dict, List, Optional, Tuple
from .air_dominance import JETS, recipe
from .storage import Storage
from .retired_items import FREEZER_CONFIG_KEYS, SAFECRACK_CONFIG_KEYS

DEFAULTS: Dict[str, Any] = {
    "general": {
        "prefix": "!",
        "embed_color": "#B57EDC",
        "log_channel": None,
        "delete_command_replies": False,
        "ephemeral_errors": True,
    },
    "roles": {"mod": None},
    "moderation": {
        "enabled": True,
        "confirm_nuke": False,
        "nuke_owner_only": False,
        "protected_channels": [],
        "log_deletions": True,
        "nuke_media": True,
    },
    "starboard": {
        "enabled": True,
        "channel": None,
        "threshold": 1,
        "emoji": "⭐",
        "ignore_bots": True,
        "self_star": False,
    },
    "achievements": {"enabled": True, "announce": True},
    "economy": {
        "enabled": True,
        "nyx_build_cost": 15000000000000000,
        "nyx_launch_cost": 1500000000000000,
        "nyx_build_hours": 12,
        "nyx_launch_minutes": 30,
        "nyx_cooldown_hours": 10,
        "nyx_active_hours": 3,
        "currency_name": "donuts",
        "currency_emoji": "🍩",
        "starting_balance": 100,
        "daily_amount": 5500000,
        "daily_cooldown_hours": 12,
        "daily_streak_bonus_pct": 5,
        "daily_streak_max": 20,
        "work_min_amount": 25,
        "work_max_amount": 60,
        "work_cooldown_minutes": 10,
        "work_salary_tier_1": 275000,
        "work_salary_tier_2": 1100000,
        "work_salary_tier_3": 4400000,
        "work_salary_tier_4": 13200000,
        "work_salary_tier_5": 33000000,
        "work_xp_tier_2": 15,
        "work_xp_tier_3": 45,
        "work_xp_tier_4": 100,
        "work_xp_tier_5": 200,
        "work_correct_bonus_pct": 25,
        "work_incorrect_pay_pct": 70,
        "work_daily_bonus_shifts": 5,
        "work_daily_bonus_multiplier": 3,
        "android21_helper_price": 100000000000,
        "android21_helper_pay_pct": 70,
        "fish_cooldown_seconds": 300,
        "slots_min_bet": 10,
        "slots_max_bet": 10000000,
        "slots_cooldown_seconds": 3,
        "slots_boost_pct": 0,
        "slots_win_pct": 54,
        "blackjack_min_bet": 10,
        "blackjack_max_bet": 10000,
        "blackjack_win_profit_pct": 135,
        "blackjack_natural_pct": 300,
        "blackjack_charlie_profit_pct": 350,
        "blackjack_decks": 2,
        "blackjack_shuffle_at_pct": 25,
        "blackjack_bonus_777": 9,
        "blackjack_suited_bonus": 50,
        "blackjack_ace_bonus_777": 12,
        "blackjack_ace_suited_bonus": 75,
        "steal_success_chance": 60,
        "steal_max_percent": 25,
        "steal_min_target": 1,
        "steal_cooldown_minutes": 20,
        "steal_fail_fine": 500000,
        "lock_block_chance": 45,
        "uno_reverse_chance": 25,
        "bank_lock_block_chance": 15,
        "bank_uno_reverse_chance": 10,
        "uno_reverse_percent": 75,
        "uno_reverse_wealth_cap_percent": 10,
        "steal_fail_fine_bps": 50,
        "bank_rob_fail_fine_bps": 100,
        "jail_minutes": 60,
        "roulette_min_bet": 10,
        "roulette_max_bet": 10000,
        "roulette_even_profit_pct": 145,
        "roulette_dozen_profit_pct": 270,
        "roulette_straight_profit_pct": 4000,
        "roulette_jackpot_contribution_pct": 1,
        "casino_tour_enabled": True,
        "casino_tour_games": 3,
        "casino_tour_reward": 500000000,
        "casino_tour_wager_pct": 3,
        "casino_tour_streak_bonus_pct": 10,
        "casino_tour_streak_cap": 7,
        "enchant_max_per_type": 5,
        "fish_rot_hours": 24,
        "wheel_min_bet": 10,
        "wheel_max_bet": 10000,
        "wheel_house_cut": 2,
        "wheel_non_jackpot_rebate_pct": 6,
        "wheel_pot_seed": 1000,
        "wheel_jackpot_reseed": 100000000,
        "trivia_reward": 1100000,
        "trivia_speed_bonus_max": 550000,
        "trivia_daily_milestone": 5500000,
        "trivia_cooldown_minutes": 120,
        "trivia_seconds": 25,
        "bank_interest_percent": 1,
        "bank_interest_daily_cap": 1000000,
        "bank_rob_success_chance": 50,
        "bank_rob_max_percent": 40,
        "bank_rob_min_target": 100,
        "bank_rob_cooldown_minutes": 30,
        "bank_rob_fail_fine": 2000000,
        "hex_cost": 50000000,
        "hex_hours": 3,
        "hex_target_daily_cap": 2,
        "hex_target_window_hours": 24,
        "loan_max_interest": 50,
        "loan_max_active": 3,
        "loan_min_amount": 10,
        "collect_cooldown_minutes": 30,
        "garnish_percent": 25,
        "coalition_creation_cost": 50000000,
        "coalition_duration_days": 7,
        "coalition_max_members": 10,
        "coalition_invite_hours": 24,
        "coalition_rejoin_cooldown_hours": 24,
        "country_development_max_level": 20,
        "country_development_gdp_pct": 3,
        "country_development_base_cost_pct": 5,
        "country_development_cost_growth_pct": 10,
        "country_invasion_cooldown_min_minutes": 10,
        "country_invasion_cooldown_max_minutes": 15,
        "price_lock": 1000000,
        "price_uno": 2000000,
        "price_copcall": 5000000,
        "price_drill": 10000000,
        "price_getaway": 5000000,
        "hold_cap_lock": 8,
        "hold_cap_uno": 8,
        "hold_cap_copcall": 10,
        "hold_cap_getaway": 5,
        "hold_cap_drill": 5,
        "price_crown": 2500000000,
        "enchant_base_cost": 10000000,
        "icbm_build_cost": 750000000,
        "icbm_build_hours": 1,
        "icbm_cooldown_hours": 1,
        "icbm_max_stock": 3,
        "icbm_wallet_pct": 65,
        "icbm_bank_pct": 65,
        "icbm_item_pct": 30,
        "icbm_fish_pct": 40,
        "icbm_damage_cap": 0,
        "icbm_min_target_bank": 1000,
        "icbm_min_target_balance": 1000,
        "icbm_media": True,
        "aa_shield_cost": 25000000,
        "aa_shield_min_hours": 1,
        "aa_shield_max_hours": 3,
        "aa_shield_chance": 50,
        "aa_shield_vehicle_bonus": 10,
        "aa_rocket_cost": 1000000,
        "aa_rocket_build_minutes": 20,
        "aa_rocket_batch_max": 5,
        "aa_rocket_stock_cap": 10,
        "aa_rocket_load_cap": 5,
        "aa_rocket_chance": 30,
        "aa_salvo": False,
        "aa_media": True,
        "vehicle_b2_cost": 15000000000,
        "vehicle_b2_build_hours": 5,
        "vehicle_b2_arm_hours": 0.5,
        "vehicle_b2_cooldown_days": 1 / 24,
        "vehicle_b52_cost": 1000000000,
        "vehicle_b52_build_hours": 8,
        "vehicle_b52_cooldown_minutes": 120,
        "vehicle_b52_ammo_cost": 50000000,
        "vehicle_b52_load_minutes": 30,
        "vehicle_b52_ammo_cap": 2,
        "vehicle_b52_lockdown_minutes": 60,
        "vehicle_b52_intercept_chance": 50,
        "vehicle_b52_wallet_damage_pct": 25,
        "vehicle_b52_bank_damage_pct": 15,
        "vehicle_u2_cost": 250000000,
        "vehicle_u2_build_hours": 4,
        "vehicle_u2_cooldown_hours": 0.5,
        "u2_recon_window_hours": 3,
        "vehicle_deimos_cost": 80000000,
        "vehicle_deimos_build_hours": 1,
        "vehicle_deimos_cooldown_hours": 0.75,
        "deimos_success_chance": 60,
        "deimos_recon_window_hours": 4,
        "vehicle_sr71_cost": 400000000,
        "vehicle_sr71_build_hours": 4,
        "vehicle_sr71_cooldown_hours": 1,
        "vehicle_sr71_shotdown_chance": 20,
        "b2_min_target_value": 210000000,
        "deep_vault_cost": 500000000,
        "deep_vault_deposit_fee_pct": 2,
        "deep_vault_withdraw_minutes": 5,
        "s400_cost": 500000000,
        "s400_build_hours": 8,
        "s400_interceptor_cost": 40000000,
        "s400_interceptor_cap": 2,
        "s400_intercept_chance": 30,
        "vehicle_zumwalt_cost": 700000000,
        "vehicle_zumwalt_build_hours": 4,
        "vehicle_zumwalt_cooldown_minutes": 30,
        "vehicle_zumwalt_ammo_cost": 55000000,
        "vehicle_zumwalt_load_minutes": 10,
        "vehicle_zumwalt_ammo_cap": 3,
        "vehicle_zumwalt_damage_pct": 40,
        "vehicle_virginia_cost": 800000000,
        "vehicle_virginia_build_hours": 6,
        "vehicle_virginia_cooldown_minutes": 30,
        "vehicle_virginia_ammo_cost": 45000000,
        "vehicle_virginia_load_minutes": 10,
        "vehicle_virginia_ammo_cap": 3,
        "vehicle_virginia_rod_break_chance": 40,
        "vehicle_virginia_mythic_rod_break_chance": 20,
        "vehicle_himars_cost": 250000000,
        "vehicle_himars_build_hours": 2,
        "vehicle_himars_cooldown_minutes": 30,
        "vehicle_himars_ammo_cost": 20000000,
        "vehicle_himars_load_minutes": 0,
        "vehicle_himars_ammo_cap": 5,
        "vehicle_apache_cost": 200000000,
        "vehicle_apache_build_hours": 2,
        "vehicle_apache_cooldown_minutes": 20,
        "vehicle_apache_ammo_cost": 15000000,
        "vehicle_apache_load_minutes": 5,
        "vehicle_apache_ammo_cap": 6,
        "vehicle_apache_aa_intercept_chance": 30,
        "vehicle_apache_regular_aa_damage_pct": 50,
        "vehicle_apache_himars_damage": 2,
        "vehicle_comanche_upgrade_cost": 1000000000,
        "vehicle_comanche_upgrade_hours": 6,
        "vehicle_comanche_precision_damage_pct": 100,
        "vehicle_comanche_cooldown_hours": 8,
        "vehicle_comanche_jagms_per_mission": 2,
        "vehicle_comanche_intercept_chance_min": 20,
        "vehicle_comanche_intercept_chance_max": 25,
        "vehicle_comanche_repair_cost_min": 100000000,
        "vehicle_comanche_repair_cost_max": 200000000,
        "vehicle_comanche_repair_hours": 2,
        "vehicle_mq9_cost": 150000000,
        "vehicle_mq9_build_hours": 1.5,
        "vehicle_mq9_cooldown_minutes": 15,
        "vehicle_mq9_ammo_cost": 10000000,
        "vehicle_mq9_load_minutes": 0,
        "vehicle_mq9_ammo_cap": 5,
        "vehicle_mq9_item_damage_pct": 100,
        "vehicle_mq9_split_damage_pct": 60,
        "vehicle_mq9_aa_intercept_chance": 20,
        "vehicle_f15e_cost": 400000000,
        "vehicle_f15e_build_hours": 3.5,
        "vehicle_f15e_cooldown_minutes": 30,
        "vehicle_f15e_ammo_cost": 40000000,
        "vehicle_f15e_load_minutes": 10,
        "vehicle_f15e_ammo_cap": 3,
        "vehicle_f15e_damage_pct": 50,
        "vehicle_a10_cost": 12000000000,
        "vehicle_a10_build_hours": 12,
        "vehicle_a10_cooldown_minutes": 360,
        "vehicle_a10_ammo_cost": 1000000000,
        "vehicle_a10_load_minutes": 60,
        "vehicle_a10_ammo_cap": 2,
        "vehicle_a10_vehicle_destroy_chance": 70,
        "vehicle_a10_ammo_damage_pct": 70,
        "vehicle_a10_ammo_target_count": 2,
        "vehicle_a10_regular_aa_damage_pct": 75,
        "vehicle_a10_wallet_damage_pct": 20,
        "vehicle_a10_bank_damage_pct": 10,
        "vehicle_a10_aa_intercept_chance": 50,
        "vehicle_su34_cost": 18000000000,
        "vehicle_su34_build_hours": 16,
        "vehicle_su34_cooldown_minutes": 240,
        "vehicle_su34_ammo_cost": 1500000000,
        "vehicle_su34_load_minutes": 90,
        "vehicle_su34_ammo_cap": 2,
        "vehicle_su34_vehicle_destroy_chance": 50,
        "vehicle_su34_ammo_damage_pct": 45,
        "vehicle_su34_ammo_target_count": 2,
        "vehicle_su34_regular_aa_damage_pct": 50,
        "vehicle_su34_wallet_damage_pct": 15,
        "vehicle_su34_bank_damage_pct": 8,
        "vehicle_su34_s400_intercept_chance": 25,
        "vehicle_su34_patriot_intercept_chance": 20,
        "vehicle_c130j_cost": 900000000,
        "vehicle_c130j_build_hours": 5,
        "vehicle_c130j_cooldown_minutes": 90,
        "vehicle_c130j_ammo_cost": 120000000,
        "vehicle_c130j_load_minutes": 20,
        "vehicle_c130j_ammo_cap": 3,
        "vehicle_c130j_damage_pct": 40,
        "vehicle_c130j_target_count": 3,
        "vehicle_champ_cost": 650000000,
        "vehicle_champ_build_hours": 4,
        "vehicle_champ_cooldown_minutes": 120,
        "vehicle_champ_ammo_cost": 100000000,
        "vehicle_champ_load_minutes": 20,
        "vehicle_champ_ammo_cap": 2,
        "vehicle_champ_blackout_minutes": 45,
        "vehicle_maldx_cost": 350000000,
        "vehicle_maldx_build_hours": 2,
        "vehicle_maldx_cooldown_minutes": 30,
        "vehicle_maldx_ammo_cost": 50000000,
        "vehicle_maldx_load_minutes": 10,
        "vehicle_maldx_ammo_cap": 5,
        "vehicle_maldx_success_chance": 75,
        "vehicle_lrhw_cost": 1200000000,
        "vehicle_lrhw_build_hours": 6,
        "vehicle_lrhw_cooldown_minutes": 180,
        "vehicle_lrhw_ammo_cost": 500000000,
        "vehicle_lrhw_load_minutes": 30,
        "vehicle_lrhw_ammo_cap": 2,
        "vehicle_xb70_cost": 3200000000,
        "vehicle_xb70_build_hours": 10,
        "vehicle_xb70_cooldown_minutes": 240,
        "vehicle_xb70_ammo_cost": 250000000,
        "vehicle_xb70_load_minutes": 45,
        "vehicle_xb70_ammo_cap": 1,
        "vehicle_xb70_damage_min_pct": 70,
        "vehicle_xb70_damage_max_pct": 80,
        "vehicle_xb70_s400_intercept_chance": 10,
        "vehicle_m1a2_cost": 750000000,
        "vehicle_m1a2_build_hours": 6,
        "vehicle_m1a2_cooldown_minutes": 45,
        "vehicle_m1a2_ammo_cost": 40000000,
        "vehicle_m1a2_load_minutes": 20,
        "vehicle_m1a2_ammo_cap": 6,
        "vehicle_m1a2_fortification_damage_pct": 25,
        "vehicle_m1a2_breach_minutes": 30,
        "vehicle_m1a2_country_breach_cooldown_minutes": 60,
        "vehicle_m1a2_breach_attack_bonus": 20,
        "vehicle_m1a2_garrison_defense": 25,
        "vehicle_m1a2_garrison_move_minutes": 30,
        "vehicle_m1a2_withdraw_minutes": 15,
        "vehicle_m1a2_trophy_defeat_chance": 35,
        "vehicle_m1a2_damage_chance": 30,
        "vehicle_m1a2_repair_cost_min": 100000000,
        "vehicle_m1a2_repair_cost_max": 175000000,
        "vehicle_m1a2_repair_hours": 1.5,
        "vehicle_leopard2a7_cost": 750000000,
        "vehicle_leopard2a7_build_hours": 6,
        "vehicle_leopard2a7_cooldown_minutes": 45,
        "vehicle_leopard2a7_ammo_cost": 40000000,
        "vehicle_leopard2a7_load_minutes": 20,
        "vehicle_leopard2a7_ammo_cap": 6,
        "vehicle_leopard2a7_fortification_damage_pct": 30,
        "vehicle_leopard2a7_breach_minutes": 30,
        "vehicle_leopard2a7_country_breach_cooldown_minutes": 60,
        "vehicle_leopard2a7_breach_attack_bonus": 20,
        "vehicle_leopard2a7_garrison_defense": 25,
        "vehicle_leopard2a7_garrison_move_minutes": 30,
        "vehicle_leopard2a7_withdraw_minutes": 15,
        "vehicle_leopard2a7_damage_chance": 30,
        "vehicle_leopard2a7_repair_cost_min": 100000000,
        "vehicle_leopard2a7_repair_cost_max": 175000000,
        "vehicle_leopard2a7_repair_hours": 1.5,
        "vehicle_aegis_cost": 550000000,
        "vehicle_aegis_build_hours": 4,
        "vehicle_aegis_ammo_cost": 25000000,
        "vehicle_aegis_load_minutes": 0,
        "vehicle_aegis_ammo_cap": 3,
        "vehicle_aegis_intercept_chance": 30,
        "vehicle_p8_cost": 450000000,
        "vehicle_p8_build_hours": 4,
        "vehicle_p8_ammo_cost": 25000000,
        "vehicle_p8_load_minutes": 0,
        "vehicle_p8_ammo_cap": 3,
        "vehicle_p8_intercept_chance": 30,
        "vehicle_patriot_cost": 350000000,
        "vehicle_patriot_build_hours": 2.5,
        "vehicle_patriot_ammo_cost": 25000000,
        "vehicle_patriot_load_minutes": 0,
        "vehicle_patriot_ammo_cap": 3,
        "vehicle_patriot_intercept_chance": 40,
        "vehicle_javelin_cost": 200000000,
        "vehicle_javelin_build_hours": 3,
        "vehicle_javelin_ammo_cost": 25000000,
        "vehicle_javelin_load_minutes": 15,
        "vehicle_javelin_ammo_cap": 4,
        "vehicle_javelin_track_chance": 30,
        "thor_odin_cost": 5000000000000,
        "thor_odin_build_hours": 24,
        "thor_mjolnir_cost": 7000000000000,
        "thor_mjolnir_build_hours": 36,
        "thor_bifrost_cost": 5000000000000,
        "thor_bifrost_build_hours": 30,
        "thor_launch_cost": 2000000000000,
        "thor_ascent_minutes": 30,
        "thor_gbi_cost": 250000000000,
        "thor_gbi_build_hours": 0.5,
        "thor_gbi_stock_capacity": 2,
        "thor_gbi_chance_min": 10,
        "thor_gbi_chance_max": 30,
        "thor_gbi_max_interceptors": 3,
        "thor_gbi_block2_cost": 2000000000000,
        "thor_gbi_block2_hours": 12,
        "thor_gbi_block2_unit_cost": 500000000000,
        "thor_gbi_block2_chance_min": 20,
        "thor_gbi_block2_chance_max": 35,
        "thor_assembly_cost": 2000000000000,
        "thor_assembly_hours": 24,
        "thor_rod_cost": 15000000000000,
        "thor_rod_capacity": 6,
        "thor_resupply_one_hours": 6,
        "thor_resupply_two_hours": 9,
        "thor_resupply_three_hours": 12,
        "thor_chamber_hours": 1.5,
        "thor_strike_cooldown_hours": 3,
        "thor_service_after_shots": 3,
        "thor_service_cost": 2000000000000,
        "thor_service_hours": 12,
        "thor_bmd_refit_cost": 2000000000000,
        "thor_bmd_refit_hours": 18,
        "thor_sm3_cost": 250000000000,
        "thor_sm3_load_hours": 3,
        "thor_sm3_capacity": 3,
        "thor_sm3_intercept_chance": 30,
        "continuity_cost": 5000000000000,
        "continuity_build_hours": 24,
        "continuity_transfer_hours": 0.25,
        "continuity_funds_cap": 100000000000000,
        "continuity_plushie_cap": 5,
        "continuity_item_cap": 20,
        "continuity_thor_seal_hours": 12,
        "thor_media": True,
        "isd_launch_cost": 250000000000000000,
        "isd_launch_window_minutes": 60,
        "isd_launch_intercept_chance": 15,
        "isd_launch_max_interceptors": 5,
        "isd_assembly_cost": 2000000000000000000,
        "isd_assembly_hours": 96,
        "isd_assembly_window_minutes": 120,
        "isd_assembly_intercept_chance": 12,
        "isd_assembly_max_interceptors": 8,
        "isd_cinder_cost": 3000000000000000000,
        "isd_cinder_build_hours": 48,
        "isd_cinder_window_minutes": 120,
        "isd_cinder_intercept_chance": 10,
        "isd_cinder_max_interceptors": 10,
        "isd_coordination_min_defenders": 5,
        "isd_coordination_chance": 15,
        "isd_counter_cost": 45000000000000000,
        "isd_counter_build_hours": 4,
        "isd_counter_capacity": 2,
        "isd_repair_cost": 1000000000000000000,
        "isd_repair_hours": 72,
        "isd_contribution_cap": 1000000000000000000,
        "isd_media": True,
        "strategic_media": True,
        "channel": None,
        "ledger_retention_days": 7,
    },
    "events": {
        "enabled": True,
        "channel": None,
        "interval_hours": 4,
        "interval_min_hours": 1,
        "interval_max_hours": 2,
        "donut_drop_min": 2000000,
        "donut_drop_max": 5000000,
        "donut_drop_winners": 4,
        "bounty_bonus": 10000000,
        "bounty_min_worth": 50000000,
        "frenzy_sell_bonus": 50,
        "migration_rare_mult": 2,
        "crimewave_cut": 50,
        "interest_mult": 2,
    },
    "seasons": {"enabled": True, "channel": None, "length_days": 30, "prize": 0},
    "abyss": {
        "dive_cost": 1000000,
        "deep_dive_cost": 5000000,
        "deep_debris_cost": 2,
        "cooldown_seconds": 300,
    },
}
MANAGED_KEYS: set[str] = set()
for _jet in JETS:
    _recipe = recipe(_jet)
    for _field, _fallback in (
        ("cost", "fallback_cost"),
        ("build", "fallback_build"),
        ("ammo_cost", "fallback_ammo_cost"),
        ("load", "fallback_load"),
        ("cap", "fallback_cap"),
        ("cooldown", "fallback_cooldown"),
    ):
        DEFAULTS["economy"][_recipe[_field]] = _recipe[_fallback]
OWNER_ONLY_SECTIONS = {"economy"}
OWNER_ONLY_KEYS = {"general.log_channel"}


def is_owner_only(path: str) -> bool:
    """True if this dotted config key is owner-locked (by section or by key)."""
    return path.split(".", 1)[0] in OWNER_ONLY_SECTIONS or path in OWNER_ONLY_KEYS


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class GuildConfig:
    """A live view over one guild's settings."""

    def __init__(self, manager: "ConfigManager", guild_id: int, data: Dict[str, Any]):
        self._manager = manager
        self.guild_id = guild_id
        self._data = data

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, path: str, value: Any) -> None:
        parts = path.split(".")
        node = self._data
        for part in parts[:-1]:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[parts[-1]] = value
        self._manager.storage.mark_dirty(self.guild_id)

    def raw(self) -> Dict[str, Any]:
        return self._data

    @property
    def color(self) -> int:
        raw = str(self.get("general.embed_color", "#B57EDC")).lstrip("#")
        try:
            return int(raw, 16)
        except ValueError:
            return 11894492


class ConfigManager:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage
        self._views: Dict[int, GuildConfig] = {}

    def for_guild(self, guild_id: int) -> GuildConfig:
        if guild_id not in self._views:
            stored = self.storage.load(guild_id)
            removed = False
            for retired in ("autoresponder", "scheduler", "snipe"):
                if retired in stored:
                    stored.pop(retired, None)
                    removed = True
            roles = stored.get("roles")
            if isinstance(roles, dict) and "ignored" in roles:
                roles.pop("ignored", None)
                removed = True
            moderation = stored.get("moderation")
            economy = stored.get("economy")
            migrations = stored.setdefault("_migrations", {})
            if isinstance(economy, dict):
                for retired in FREEZER_CONFIG_KEYS + SAFECRACK_CONFIG_KEYS:
                    if retired in economy:
                        economy.pop(retired, None)
                        removed = True
            if isinstance(moderation, dict):
                for retired in (
                    "confirm_purge",
                    "max_purge",
                    "confirm_ban",
                    "warn_dm",
                    "warn_expire_days",
                    "warn_timeout_at",
                    "warn_timeout_minutes",
                    "warn_escalate",
                ):
                    if retired in moderation:
                        moderation.pop(retired, None)
                        removed = True
            if removed:
                self.storage.mark_dirty(guild_id)
            merged = _deep_merge(DEFAULTS, stored)
            if stored != merged:
                self.storage.mark_dirty(guild_id)
            stored.clear()
            stored.update(merged)
            self._views[guild_id] = GuildConfig(self, guild_id, stored)
        return self._views[guild_id]

    async def save(self, guild_id: int) -> None:
        await self.storage.save(guild_id)

    async def flush(self) -> None:
        await self.storage.flush()

    @staticmethod
    def schema_keys() -> List[str]:
        """Flatten DEFAULTS into dotted, user-settable keys."""
        keys: List[str] = []

        def walk(node: Dict[str, Any], prefix: str = "") -> None:
            for key, value in node.items():
                path = f"{prefix}{key}"
                if isinstance(value, dict):
                    walk(value, f"{path}.")
                elif path not in MANAGED_KEYS:
                    keys.append(path)

        walk(DEFAULTS)
        return sorted(keys)

    @staticmethod
    def default_for(path: str) -> Any:
        node: Any = DEFAULTS
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                raise KeyError(path)
            node = node[part]
        return node

    @classmethod
    def coerce(cls, path: str, raw: str) -> Tuple[bool, Any, Optional[str]]:
        """Convert user text into the type the schema expects.

        Returns (ok, value, error_message).
        """
        try:
            default = cls.default_for(path)
        except KeyError:
            return (False, None, f"`{path}` is not a valid setting.")
        text = raw.strip()
        if text.lower() in {"none", "null", "clear", "off-none"} and (
            default is None or isinstance(default, (int, str))
        ):
            if default is None:
                return (True, None, None)
        if isinstance(default, bool):
            if text.lower() in {"true", "yes", "on", "1", "enable", "enabled"}:
                return (True, True, None)
            if text.lower() in {"false", "no", "off", "0", "disable", "disabled"}:
                return (True, False, None)
            return (False, None, "Expected a true/false value.")
        if isinstance(default, int):
            try:
                return (True, int(text), None)
            except ValueError:
                return (False, None, "Expected a whole number.")
        if isinstance(default, float):
            try:
                value = float(text)
            except ValueError:
                return (False, None, "Expected a finite decimal number.")
            if not math.isfinite(value):
                return (False, None, "Expected a finite decimal number.")
            return (True, value, None)
        if isinstance(default, list):
            if not text or text.lower() in {"none", "empty", "clear"}:
                return (True, [], None)
            items = [chunk.strip() for chunk in text.replace(",", " ").split() if chunk.strip()]
            parsed: List[Any] = []
            for item in items:
                digits = "".join((ch for ch in item if ch.isdigit()))
                if not digits:
                    return (False, None, f"`{item}` is not a valid ID or mention.")
                parsed.append(int(digits))
            return (True, parsed, None)
        if default is None:
            digits = "".join((ch for ch in text if ch.isdigit()))
            if not digits:
                return (False, None, "Expected an ID, a mention, or `none`.")
            return (True, int(digits), None)
        return (True, text, None)
