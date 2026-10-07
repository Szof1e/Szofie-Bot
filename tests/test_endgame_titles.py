"""Quintillion/sextillion prestige purchases stay exact, cosmetic and title-only."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from cogs.economy import EconomyCog
from szofie.storage import Storage
from szofie.titles import TITLE_BY_ID, equipped_label
from tests.test_system_integration import _FakeBot, _FakeInteraction

NEW_TITLES = {
    "sectormagnate": 10**18,
    "imperialpatron": 10**19,
    "grandmoff": 10**20,
    "galacticregent": 5 * 10**20,
    "galacticemperor": 2 * 10**21,
}


class EndgameTitleTests(unittest.IsolatedAsyncioTestCase):
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
        self.user = self.bot.economy.user(1, 123, 0)
        self.user.update(titles={}, equipped_title=None)

    async def test_purchases_need_no_projects_change_only_cosmetics_and_wallet_and_persist_exactly(self):
        self.assertFalse(self.user.get("isd", {}).get("operational"))
        self.assertFalse(self.user.get("death_star", {}).get("operational"))
        for title_id, price in NEW_TITLES.items():
            with self.subTest(title=title_id):
                self.user.update(donuts=price + 37, titles={}, equipped_title=None)
                before = copy.deepcopy(self.user)
                self.cog._log = AsyncMock()
                interaction = _FakeInteraction()
                await EconomyCog.buytitle.callback(self.cog, interaction, TITLE_BY_ID[title_id].name)
                expected = copy.deepcopy(before)
                expected.update(donuts=37, titles={title_id: 1}, equipped_title=title_id)
                self.assertEqual(self.user, expected)
                self.assertEqual(self.cog._log.call_args.args[2], -price)
                self.assertIn(TITLE_BY_ID[title_id].name, equipped_label(self.user))
                restored = Storage(self.root / "economy").load(1)["users"]["123"]
                self.assertEqual(restored, expected)

    async def test_insufficient_funds_and_duplicate_purchase_do_not_charge(self):
        for title_id, price in NEW_TITLES.items():
            self.user.update(donuts=price - 1, titles={}, equipped_title=None)
            before = copy.deepcopy(self.user)
            interaction = _FakeInteraction()
            await EconomyCog.buytitle.callback(self.cog, interaction, title_id)
            self.assertEqual(self.user, before)
            self.assertIn("costs", interaction.response.calls[-1]["embed"].description)
            self.user.update(donuts=price + 37, titles={title_id: 1})
            before = copy.deepcopy(self.user)
            duplicate = _FakeInteraction()
            await EconomyCog.buytitle.callback(self.cog, duplicate, title_id)
            self.assertEqual(self.user, before)
            self.assertIn("already own", duplicate.response.calls[-1]["embed"].description)

    async def test_new_titles_have_dedicated_page_and_autocomplete(self):
        self.assertNotEqual(TITLE_BY_ID["galacticemperor"].emoji, TITLE_BY_ID["crowned"].emoji)
        interaction = _FakeInteraction()
        await EconomyCog.titleshop.callback(self.cog, interaction)
        pager = interaction.response.calls[-1]["view"]
        self.addCleanup(pager.stop)
        self.assertEqual(len(pager.pages), 4)
        endgame = pager.pages[-1]
        self.assertIn("Imperial endgame", endgame.title)
        for title_id, price in NEW_TITLES.items():
            title = TITLE_BY_ID[title_id]
            self.assertEqual(title.price, price)
            self.assertIn("**" + title.name + "**", endgame.description)
            self.assertIn(self.cog.money(self.cfg, price), endgame.description)
            self.assertNotIn("**" + title.name + "**", "\n".join((p.description for p in pager.pages[:-1])))
            choices = await self.cog._purchasable_title_autocomplete(interaction, title.name)
            self.assertEqual([c.value for c in choices], [title_id])
        self.assertLessEqual(len(endgame.description), 4096)
        self.assertLessEqual(len(endgame), 6000)

    async def test_new_titles_never_appear_or_sell_in_regular_shop(self):
        interaction = _FakeInteraction()
        await EconomyCog.shop.callback(self.cog, interaction)
        listing = "\n".join((f.value for f in interaction.response.calls[-1]["embed"].fields))
        for title_id in NEW_TITLES:
            self.assertNotIn(TITLE_BY_ID[title_id].name, listing)
            self.assertFalse(await self.cog._buy_autocomplete(interaction, title_id))
            before = copy.deepcopy(self.user)
            purchase = _FakeInteraction()
            await EconomyCog.buy.callback(self.cog, purchase, title_id, 1)
            self.assertEqual(self.user, before)
            self.assertIn("/titleshop", purchase.response.calls[-1]["embed"].description)


if __name__ == "__main__":
    unittest.main()
