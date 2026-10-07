"""The Crown is sold with titles without changing its original persistent effect."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from discord import app_commands
from cogs.economy import EconomyCog
from szofie.plushies import PLUSHIE_BY_ID
from szofie.storage import Storage
from szofie.titles import TITLES, TITLE_BY_ID, equipped_label, title_price
from tests.test_system_integration import _FakeBot, _FakeInteraction


class CrownTitleShopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bot = _FakeBot(self.root)
        self.cog = object.__new__(EconomyCog)
        self.cog.bot, self.cog.econ = (self.bot, self.bot.economy)
        self.cfg = self.bot.config.for_guild(1)
        guard = patch.object(self.cog, "_guard", AsyncMock(return_value=self.cfg))
        guard.start()
        self.addCleanup(guard.stop)
        self.user = self.bot.economy.user(1, 123, 10**15)

    async def test_main_shop_contains_no_title_listings_including_crown(self):
        interaction = _FakeInteraction()
        await EconomyCog.shop.callback(self.cog, interaction)
        fields = "\n".join((f.value for f in interaction.response.calls[-1]["embed"].fields))
        for title in TITLES:
            self.assertNotIn("**" + title.name + "**", fields)
        for query in ("crown", "crowned", "Devourer's Crown"):
            choices = await self.cog._buy_autocomplete(interaction, query)
            self.assertFalse({"crown", "crowned"} & {choice.value for choice in choices})

    async def test_old_purchase_route_redirects_without_charging_or_granting(self):
        before = copy.deepcopy(self.user)
        for alias in ("crown", "crowned", "title:crowned", "The Devourer's Crown"):
            interaction = _FakeInteraction()
            await EconomyCog.buy.callback(self.cog, interaction, alias, 1)
            message = interaction.response.calls[-1]["embed"].description
            self.assertIn("/titleshop", message)
            self.assertIn("/buytitle", message)
            self.assertEqual(self.user, before)

    async def test_title_shop_uses_live_crown_price_and_correct_band(self):
        for price in (2500000000, 6000000000):
            self.cfg.set("economy.price_crown", price)
            self.assertEqual(title_price(TITLE_BY_ID["crowned"], self.cfg), price)
            interaction = _FakeInteraction()
            await EconomyCog.titleshop.callback(self.cog, interaction)
            pager = interaction.response.calls[-1]["view"]
            self.addCleanup(pager.stop)
            matching = [p for p in pager.pages if "**The Devourer's Crown**" in p.description]
            self.assertEqual(len(matching), 1)
            self.assertIn(self.cog.money(self.cfg, price), matching[0].description)
            self.assertIn("Classic" if price < 5000000000 else "Elite", matching[0].title)
            choices = await self.cog._purchasable_title_autocomplete(interaction, "crown")
            self.assertIn("crowned", {c.value for c in choices})

    async def test_purchase_grants_flag_title_and_forced_equip_at_configured_price(self):
        price = 3000000000
        self.cfg.set("economy.price_crown", price)
        self.user.update(titles={"certified": 1}, equipped_title="certified")
        wallet = self.user["donuts"]
        self.cog._log = AsyncMock()
        interaction = _FakeInteraction()
        await EconomyCog.buytitle.callback(self.cog, interaction, "crown")
        self.assertEqual(self.user["donuts"], wallet - price)
        self.assertTrue(self.user["crown"])
        self.assertEqual(self.user["titles"], {"certified": 1, "crowned": 1})
        self.assertEqual(self.user["equipped_title"], "crowned")
        self.assertIn("The Devourer's Crown", equipped_label(self.user))
        self.assertEqual(self.cog._log.call_args.args[2:4], (-price, "buy:crown"))
        restored = Storage(self.root / "economy").load(1)["users"]["123"]
        self.assertTrue(restored["crown"])
        self.assertEqual(restored["equipped_title"], "crowned")

    async def test_existing_holders_are_not_charged_again(self):
        self.user.update(crown=True, titles={"crowned": 1}, equipped_title="certified")
        before = copy.deepcopy(self.user)
        interaction = _FakeInteraction()
        await EconomyCog.buytitle.callback(self.cog, interaction, "crowned")
        self.assertEqual(self.user, before)
        self.assertIn("already own", interaction.response.calls[-1]["embed"].description)

    async def test_insufficient_funds_does_not_grant_any_crown_state(self):
        self.user["donuts"] = 1
        before = copy.deepcopy(self.user)
        interaction = _FakeInteraction()
        await EconomyCog.buytitle.callback(self.cog, interaction, "The Devourer's Crown")
        self.assertEqual(self.user, before)
        self.assertIn("costs", interaction.response.calls[-1]["embed"].description)

    async def test_flag_only_legacy_holder_can_still_view_and_equip_without_repurchase(self):
        self.user["crown"] = True
        self.user["titles"] = {}
        wallet = self.user["donuts"]
        choices = await self.cog._owned_title_autocomplete(_FakeInteraction(), "crown")
        self.assertIn("crowned", {choice.value for choice in choices})
        interaction = _FakeInteraction()
        await EconomyCog.titles_cmd.callback(self.cog, interaction, "crown")
        self.assertEqual(self.user["equipped_title"], "crowned")
        self.assertEqual(self.user["donuts"], wallet)
        duplicate = _FakeInteraction()
        await EconomyCog.buytitle.callback(self.cog, duplicate, "crown")
        self.assertEqual(self.user["donuts"], wallet)
        self.assertTrue(self.user["crown"])

    async def test_empty_crown_verification_points_to_title_shop(self):
        interaction = _FakeInteraction()
        await EconomyCog.titles_cmd.callback(
            self.cog, interaction, view=app_commands.Choice(name="Verified holders", value="crown")
        )
        description = interaction.response.calls[-1]["embed"].description
        self.assertIn("/titleshop", description)
        self.assertIn("/buytitle title:crowned", description)
        self.assertNotIn("`/shop`", description)

    async def test_plushie_with_same_id_as_earned_title_can_still_be_bought(self):
        wallet = self.user["donuts"]
        interaction = _FakeInteraction()
        with patch.object(self.cog, "_plushie_image", return_value=None):
            await EconomyCog.buy.callback(self.cog, interaction, "scholar", 1)
        self.assertEqual(self.user["plushies"]["scholar"], 1)
        self.assertEqual(self.user["donuts"], wallet - PLUSHIE_BY_ID["scholar"].price)


if __name__ == "__main__":
    unittest.main()
