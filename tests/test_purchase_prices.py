"""Purchase-price clarity without changing payments or public game rules."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
from cogs.death_star import DeathStarCog
from cogs.space import SpaceCog
from szofie import deathstar as ds, space, ui
from szofie.economy import _default_user
from tests.test_system_integration import _FakeBot, _FakeInteraction


class PaymentPriceTests(unittest.TestCase):
    def test_every_deathstar_payment_reports_cost_available_and_shortfall(self):
        costs = [
            ds.TRANSPORT_COST,
            ds.SQUADRON_COST,
            ds.CHARGE_COST,
            ds.REPAIR_COST,
            ds.ASSEMBLY_REPAIR_COST,
            ds.RECONSTRUCTION_COST,
            50 * ds.QI,
            ds.QI,
            123,
        ]
        for cost in costs:
            with self.subTest(cost=cost):
                user = _default_user(10)
                user.update(bank=20, deep_vault_balance=10**30)
                before = copy.deepcopy(user)
                with self.assertRaises(ValueError) as error:
                    ds.pay(user, cost)
                text = str(error.exception)
                self.assertIn(f"Required: **{ui.format_donuts(cost)} donuts**", text)
                self.assertIn("Available (wallet + bank): **30 donuts**", text)
                self.assertIn(f"Shortfall: **{ui.format_donuts(cost - 30)} donuts**", text)
                self.assertIn("Deep Vault is not spent automatically", text)
                self.assertEqual(user, before)

    def test_project_error_distinguishes_total_escrow_and_personal_share_exactly(self):
        user = _default_user(0)
        state = ds.normalize(user)
        cost = 10**24 + 1
        personal = (cost + 3) // 4
        state["project_funds"] = 10**30
        user.update(donuts=personal - 2, bank=1)
        before = copy.deepcopy(user)
        with self.assertRaises(ValueError) as error:
            ds.pay_project(user, state, cost)
        text = str(error.exception)
        self.assertIn(f"Total stage price: **{ui.format_donuts(cost)} donuts**", text)
        self.assertIn(f"Coalition funds applied: **{ui.format_donuts(cost - personal)} donuts**", text)
        self.assertIn(f"Required: **{ui.format_donuts(personal)} donuts**", text)
        self.assertIn("Shortfall: **1 donut**", text)
        self.assertEqual(user, before)

    def test_successful_payment_still_uses_wallet_then_bank(self):
        user = _default_user(100)
        user.update(bank=200, deep_vault_balance=999)
        ds.pay(user, 150)
        self.assertEqual((user["donuts"], user["bank"], user["deep_vault_balance"]), (0, 150, 999))

    def test_transport_price_is_visible_before_purchase_and_matches_guide(self):
        price = ui.format_donuts(ds.TRANSPORT_COST)
        self.assertIn(price, DeathStarCog.transport.description)
        self.assertLessEqual(len(DeathStarCog.transport.description), 100)
        pages = DeathStarCog.guide_pages(object.__new__(DeathStarCog))
        self.assertIn(f"{price} donuts, 24h", pages[0].description)


class PurchaseCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_transport_failure_delivers_price_without_starting_build(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = DeathStarCog(bot)
            cog.image_reply = AsyncMock()
            cog.deliver_notices = AsyncMock()
            user = bot.economy.user(1, 123, 0)
            user.update(donuts=5 * ds.QI, bank=5 * ds.QI)
            await DeathStarCog.transport.callback(cog, _FakeInteraction())
            text = cog.image_reply.call_args.args[1].description
            for phrase in (
                "Required: **25 quintillion",
                "Available (wallet + bank): **10 quintillion",
                "Shortfall: **15 quintillion",
            ):
                self.assertIn(phrase, text)
            self.assertIsNone(ds.normalize(user)["transport_until"])
            self.assertEqual(user["donuts"] + user["bank"], 10 * ds.QI)

    async def test_space_colony_guide_and_status_show_base_gdp_price(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = SpaceCog(bot)
            user = cog.user(1, 123)
            colony = space.colony(space.normalize(user), "mars")
            colony.update(development=4, gdp=10**30)
            pages = cog._exploration_pages(_FakeInteraction())
            guide = next((p for p in pages if "Claim, GDP and passive income" in p.title))
            self.assertIn("base GDP × N ÷ 4", guide.description)
            self.assertIn("25 quadrillion", guide.description)
            self.assertTrue(all((len(p) <= 6000 and len(p.description) <= 4096 for p in pages)))
            interaction = _FakeInteraction()
            await SpaceCog.colony_status.callback(cog, interaction, "mars")
            expected = space.gdp(space.BODIES["mars"].score, space.BODIES["mars"].hazard) * 5 // 4
            embed = interaction.response.calls[0]["embed"]
            text = "\n".join((field.value for field in embed.fields))
            self.assertIn(f"level 5 · {ui.format_donuts(expected)} donuts", text)

    async def test_space_development_failure_reports_exact_next_cost_without_payment(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = SpaceCog(bot)
            user = cog.user(1, 123)
            user.update(donuts=10, bank=20, deep_vault_balance=10**30)
            colony = space.colony(space.normalize(user), "mars")
            colony.update(development=4, gdp=10**30)
            space.galaxy(cog.doc(1))["claims"]["mars"] = 123
            interaction = _FakeInteraction()
            await SpaceCog.develop.callback(cog, interaction, "mars")
            expected = space.gdp(space.BODIES["mars"].score, space.BODIES["mars"].hazard) * 5 // 4
            text = interaction.response.calls[0]["content"]
            self.assertIn(f"Required: **{ui.format_donuts(expected)} donuts**", text)
            self.assertIn("Available (wallet + bank): **30 donuts**", text)
            self.assertIn(f"Shortfall: **{ui.format_donuts(expected - 30)} donuts**", text)
            self.assertEqual(colony["development"], 4)
            self.assertEqual((user["donuts"], user["bank"]), (10, 20))
