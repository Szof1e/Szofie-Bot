"""Title retirement is a sales change, never an asset wipe or price migration."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from discord import app_commands
from cogs.economy import EconomyCog
from szofie import ui
from szofie.storage import Storage
from szofie.titles import (
    TITLES,
    TITLE_BY_ID,
    PURCHASABLE_TITLES,
    EARNED_TITLES,
    RETIRED_TITLE_IDS,
    equipped_label,
    resolve_title,
)
from tests.test_system_integration import _FakeBot, _FakeInteraction


class TitleShopCleanupTests(unittest.IsolatedAsyncioTestCase):
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
        self.user = self.bot.economy.user(1, 123, 10**20)
        self.user.update(titles={}, equipped_title=None)

    @staticmethod
    def page_text(page):
        return "\n".join([page.description or ""] + [field.value for field in page.fields])

    async def test_exact_retirement_set_and_remaining_prices(self):
        self.assertEqual(
            RETIRED_TITLE_IDS,
            {
                "nibble",
                "tooth",
                "majinsnack",
                "subject",
                "assistant",
                "connoisseur",
                "apex",
                "devoured",
                "baron",
                "magnate",
                "candykingpin",
                "confectionerytitan",
                "majinfinancier",
                "donutoligarch",
                "planetarysweetlord",
                "galacticgourmand",
                "universalglutton",
                "cosmicfoodchain",
            },
        )
        self.assertEqual(
            {t.id: t.price for t in PURCHASABLE_TITLES},
            {
                "certified": 10000,
                "favorite": 15000000,
                "incarnate": 2000000000,
                "labdirector": 5000000000,
                "trillioncalorie": 1000000000000,
                "realitydevourer": 5000000000000,
                "hungerwithoutend": 100000000000000,
                "finaldessert": 1000000000000000,
                "android21equal": 5000000000000000,
                "economydevourer": 25000000000000000,
                "crowned": 2500000000,
                "sectormagnate": 1000000000000000000,
                "imperialpatron": 10000000000000000000,
                "grandmoff": 100000000000000000000,
                "galacticregent": 500000000000000000000,
                "galacticemperor": 2000000000000000000000,
            },
        )
        self.assertEqual(len(EARNED_TITLES), 14)
        self.assertEqual(len(TITLES), 48)

    async def test_shop_and_autocomplete_show_only_sixteen_active_titles(self):
        interaction = _FakeInteraction()
        await EconomyCog.titleshop.callback(self.cog, interaction)
        pager = interaction.response.calls[-1]["view"]
        self.addCleanup(pager.stop)
        self.assertEqual(len(pager.pages), 4)
        all_text = "\n".join((self.page_text(page) for page in pager.pages))
        for title in PURCHASABLE_TITLES:
            self.assertIn("**" + title.name + "**", all_text)
        for title in TITLES:
            if title.id in RETIRED_TITLE_IDS or title.price == 0:
                self.assertNotIn("**" + title.name + "**", all_text)
        choices = await self.cog._purchasable_title_autocomplete(interaction, "")
        self.assertEqual({choice.value for choice in choices}, {t.id for t in PURCHASABLE_TITLES})
        for title_id in RETIRED_TITLE_IDS:
            matches = await self.cog._purchasable_title_autocomplete(interaction, title_id)
            self.assertNotIn(title_id, {choice.value for choice in matches})
        for page in pager.pages:
            self.assertLessEqual(len(page), 6000)
            self.assertLessEqual(len(page.description), 4096)

    async def test_retired_id_prefix_and_full_name_purchases_are_blocked(self):
        before = copy.deepcopy(self.user)
        for title_id in RETIRED_TITLE_IDS:
            title = TITLE_BY_ID[title_id]
            for raw in (title_id, "title:" + title_id, title.name):
                with self.subTest(raw=raw):
                    interaction = _FakeInteraction()
                    await EconomyCog.buytitle.callback(self.cog, interaction, raw)
                    self.assertIn("no longer sold", interaction.response.calls[-1]["embed"].description)
                    self.assertEqual(self.user, before)

    async def test_earned_titles_cannot_be_purchased(self):
        before = copy.deepcopy(self.user)
        for title in EARNED_TITLES:
            interaction = _FakeInteraction()
            await EconomyCog.buytitle.callback(self.cog, interaction, title.id)
            self.assertIn("must be earned", interaction.response.calls[-1]["embed"].description)
            self.assertEqual(self.user, before)

    async def test_retired_titles_still_equip_and_persist(self):
        self.user["titles"] = {title_id: 1 for title_id in RETIRED_TITLE_IDS}
        wallet = self.user["donuts"]
        for title_id in RETIRED_TITLE_IDS:
            interaction = _FakeInteraction()
            await EconomyCog.titles_cmd.callback(self.cog, interaction, TITLE_BY_ID[title_id].name)
            self.assertEqual(self.user["equipped_title"], title_id)
            self.assertIn(TITLE_BY_ID[title_id].name, interaction.response.calls[-1]["embed"].description)
        self.assertEqual(self.user["donuts"], wallet)
        restored = Storage(self.root / "economy").load(1)["users"]["123"]
        self.assertEqual(restored["titles"], self.user["titles"])
        self.assertEqual(restored["equipped_title"], self.user["equipped_title"])

    async def test_retired_nonowner_gets_legacy_message_not_shop_instruction(self):
        interaction = _FakeInteraction()
        await EconomyCog.titles_cmd.callback(self.cog, interaction, "nibble")
        message = interaction.response.calls[-1]["embed"].description
        self.assertIn("no longer sold", message)
        self.assertNotIn("Buy it", message)
        self.assertIsNone(self.user["equipped_title"])

    async def test_renamed_title_preserves_id_ownership_and_storage(self):
        self.user["titles"]["economydevourer"] = 1
        self.user["equipped_title"] = "economydevourer"
        self.user["space"] = {"cargo": {"titles": {"economydevourer": 1}}}
        self.user["continuity"]["titles"] = {"economydevourer": 1}
        before = copy.deepcopy(self.user)
        self.assertIn("Economic Sovereign", equipped_label(self.user))
        for raw in ("economydevourer", "Economic Sovereign", "economicsovereign", "Devourer of Economies"):
            self.assertEqual(resolve_title(raw).id, "economydevourer")
        await self.bot.economy.save(1)
        restored = Storage(self.root / "economy").load(1)["users"]["123"]
        self.assertEqual(restored, before)
        self.assertIn("Economic Sovereign", equipped_label(restored))

    async def test_buy_new_name_uses_old_id_and_cannot_charge_twice(self):
        wallet = self.user["donuts"]
        cost = TITLE_BY_ID["economydevourer"].price
        interaction = _FakeInteraction()
        await EconomyCog.buytitle.callback(self.cog, interaction, "Economic Sovereign")
        self.assertEqual(self.user["donuts"], wallet - cost)
        self.assertEqual(self.user["titles"], {"economydevourer": 1})
        self.assertEqual(self.user["equipped_title"], "economydevourer")
        duplicate = _FakeInteraction()
        await EconomyCog.buytitle.callback(self.cog, duplicate, "economydevourer")
        self.assertIn("already own", duplicate.response.calls[-1]["embed"].description)
        self.assertEqual(self.user["donuts"], wallet - cost)

    async def test_earned_page_requirements_and_owned_legacy_collection_are_complete(self):
        self.user["titles"] = {title.id: 1 for title in TITLES}
        self.user["equipped_title"] = "nibble"
        interaction = _FakeInteraction()
        await EconomyCog.titles_cmd.callback(self.cog, interaction)
        pager = interaction.response.calls[-1]["view"]
        self.addCleanup(pager.stop)
        self.assertIsInstance(pager, ui.Paginator)
        self.assertEqual(pager.index, 0)
        text = "\n".join((self.page_text(page) for page in pager.pages))
        for title in TITLES:
            self.assertIn("**" + title.name + "**", text)
        self.assertIn("legacy", text)
        for title in EARNED_TITLES:
            self.assertIn(title.blurb, text)
        for page in pager.pages:
            self.assertLessEqual(len(page), 6000)
            self.assertTrue(all((len(field.value) <= 1024 for field in page.fields)))
        explicit = _FakeInteraction()
        await EconomyCog.titles_cmd.callback(
            self.cog, explicit, view=app_commands.Choice(name="Earned", value="earned")
        )
        earned_pager = explicit.response.calls[-1]["view"]
        self.addCleanup(earned_pager.stop)
        self.assertGreater(earned_pager.index, 0)
        self.assertIn("Earned titles", earned_pager.pages[earned_pager.index].title)

    async def test_pager_is_public_and_only_requester_can_change_pages(self):
        interaction = _FakeInteraction()
        await EconomyCog.titles_cmd.callback(self.cog, interaction)
        self.assertFalse(interaction.response.calls[-1]["ephemeral"])
        pager = interaction.response.calls[-1]["view"]
        self.addCleanup(pager.stop)
        self.assertTrue(await pager.interaction_check(_FakeInteraction(user_id=123)))
        outsider = _FakeInteraction(user_id=456)
        self.assertFalse(await pager.interaction_check(outsider))
        self.assertTrue(outsider.response.calls[-1]["ephemeral"])

    async def test_conflicting_title_and_achievement_view_does_not_equip(self):
        self.user["titles"]["certified"] = 1
        before = copy.deepcopy(self.user)
        interaction = _FakeInteraction()
        await EconomyCog.titles_cmd.callback(
            self.cog, interaction, "certified", app_commands.Choice(name="Earned", value="earned")
        )
        self.assertIn("not both", interaction.response.calls[-1]["embed"].description)
        self.assertEqual(self.user, before)


if __name__ == "__main__":
    unittest.main()
