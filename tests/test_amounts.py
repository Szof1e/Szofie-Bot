"""Readable donut displays must never change numeric accounting or asset counts."""

import unittest
from types import SimpleNamespace
from cogs.economy import EconomyCog
from cogs.death_star import fmt as format_deathstar
from cogs.events import Events
from cogs.fishing import Fishing
from cogs.imperial_star_destroyer import _fmt as format_isd
from cogs.loans import Loans
from cogs.seasons import Seasons
from cogs.space import fmt as format_space
from cogs.thor import _fmt as format_thor
from cogs.trivia import Trivia
from szofie.amounts import format_donuts, format_purchase_shortfall
from szofie.betting import AmountParseError, parse_amount
from szofie.continuity import exact_summary
from szofie import ui


class DonutDisplayTests(unittest.TestCase):
    def test_smaller_amounts_keep_comma_grouping(self):
        for value in (0, 1, 999, 1000, 25000000, 999999999999):
            self.assertEqual(format_donuts(value), format(value, ","))
            self.assertEqual(format_donuts(-value), format(-value, ","))

    def test_named_units_and_trimmed_decimals(self):
        cases = {
            10**12: "1 trillion",
            133 * 10**12: "133 trillion",
            133120000000000: "133.12 trillion",
            1250000000000000: "1.25 quadrillion",
            2 * 10**18: "2 quintillion",
            123 * 10**21: "123 sextillion",
            10**24: "1 septillion",
            10**30: "1 nonillion",
            10**63: "1 vigintillion",
            10**66: "1 unvigintillion",
            10**69: "1 duovigintillion",
            10**72: "1 tresvigintillion",
            10**75: "1 quattuorvigintillion",
            10**78: "1 quinquavigintillion",
            10**81: "1 sesvigintillion",
            10**84: "1 septemvigintillion",
            10**87: "1 octovigintillion",
            10**90: "1 novemvigintillion",
            10**93: "1 trigintillion",
            133 * 10**93: "133 trigintillion",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(format_donuts(value), expected)
                self.assertEqual(format_donuts(-value), "-" + expected)
                self.assertEqual(format_donuts(value, signed=True), "+" + expected)

    def test_rounding_uses_exact_integer_arithmetic_and_promotes_units(self):
        self.assertEqual(format_donuts(1234999999999), "1.23 trillion")
        self.assertEqual(format_donuts(1235000000000), "1.24 trillion")
        self.assertEqual(format_donuts(999994999999999), "999.99 trillion")
        self.assertEqual(format_donuts(999995000000000), "1 quadrillion")
        self.assertEqual(format_donuts(999999999999), "999,999,999,999")
        self.assertEqual(format_donuts(0, signed=True), "+0")
        self.assertEqual(format_donuts(-1000, signed=True), "-1,000")

    def test_unlimited_amounts_do_not_overflow_to_float_or_infinity(self):
        self.assertEqual(format_donuts(10**96), "1 × 10^96")
        self.assertEqual(format_donuts(10**400 + 123), "1 × 10^400")
        self.assertEqual(format_donuts(9995 * 10**397), "1 × 10^401")

    def test_display_does_not_change_input_shorthand_or_exact_values(self):
        amount = 133 * 10**12 + 123
        self.assertEqual(format_donuts(amount), "133 trillion")
        self.assertEqual(parse_amount(str(amount)), amount)
        self.assertEqual(parse_amount("133t"), 133 * 10**12)
        self.assertEqual(parse_amount("1.25q"), 1250000000000000)

    def test_every_new_unit_rounds_and_promotes_without_losing_accounting_digits(self):
        units = (
            "unvigintillion",
            "duovigintillion",
            "tresvigintillion",
            "quattuorvigintillion",
            "quinquavigintillion",
            "sesvigintillion",
            "septemvigintillion",
            "octovigintillion",
            "novemvigintillion",
            "trigintillion",
        )
        for power, unit in zip(range(66, 94, 3), units):
            scale = 10**power
            with self.subTest(unit=unit):
                value = scale * 125 // 100 + 123
                self.assertEqual(format_donuts(value), f"1.25 {unit}")
                self.assertEqual(format_donuts(-value), f"-1.25 {unit}")
                self.assertEqual(parse_amount(str(value)), value)
                self.assertEqual(parse_amount("all", available=value), value)
                self.assertEqual(parse_amount("half", available=value), value // 2)
                self.assertEqual(format_donuts(scale * 99999 // 100), f"999.99 {unit}")
        self.assertEqual(format_donuts(999995 * 10**87), "1 trigintillion")
        self.assertEqual(format_donuts(999995 * 10**90), "1 × 10^96")

    def test_trigintillion_money_helpers_and_purchase_errors_stay_readable(self):
        cfg = SimpleNamespace(get=lambda key, default=None: default)
        value = 133 * 10**93 + 123
        for cog_type in (EconomyCog, Fishing, Loans, Trivia, Events, Seasons):
            cog = object.__new__(cog_type)
            self.assertIn("133 trigintillion", cog.money(cfg, value))
        for formatter in (format_isd, format_space, format_thor, format_deathstar, ui.format_donuts):
            self.assertEqual(formatter(value), "133 trigintillion")
        summary = exact_summary({"funds": value})
        self.assertIn("133 trigintillion", summary)
        error = format_purchase_shortfall(2 * 10**93, 10**93)
        self.assertIn("Required: **2 trigintillion donuts**", error)
        self.assertIn("Shortfall: **1 trigintillion donuts**", error)
        self.assertLess(len(error), 200)

    def test_very_long_inputs_do_not_lose_digits_to_decimal_context(self):
        amount = 10**100 + 123
        self.assertEqual(parse_amount(str(amount)), amount)
        self.assertEqual(parse_amount(str(amount) + "qi"), amount * 10**18)
        self.assertEqual(parse_amount("-" + str(amount), allow_negative=True), -amount)
        self.assertEqual(
            parse_amount("123456789012345678901234567890.125t"), 123456789012345678901234567890125000000000
        )

    def test_tiny_fraction_is_rejected_instead_of_rounded_to_whole_donut(self):
        with self.assertRaises(AmountParseError):
            parse_amount("123456789012345678901234567890.0000000000001t")

    def test_money_helpers_and_orbital_helpers_share_the_same_units(self):
        cfg = SimpleNamespace(get=lambda key, default=None: default)
        for cog_type in (EconomyCog, Fishing, Loans, Trivia, Events, Seasons):
            cog = object.__new__(cog_type)
            self.assertIn("133 trillion", cog.money(cfg, 133 * 10**12))
            self.assertIn("1.25 quadrillion", cog.money(cfg, 1250000000000000))
        for formatter in (format_isd, format_space, format_thor):
            self.assertEqual(formatter(133 * 10**12), "133 trillion")
            self.assertEqual(formatter(10**18), "1 quintillion")

    def test_authorized_continuity_summary_only_compacts_funds_not_items(self):
        amount = 133 * 10**12 + 123
        state = {"funds": amount, "items": {"uno": 10**12}}
        summary = exact_summary(state)
        self.assertIn("Funds **133 trillion**", summary)
        self.assertIn("uno ×1000000000000", summary)
        self.assertEqual(state["funds"], amount)


if __name__ == "__main__":
    unittest.main()
