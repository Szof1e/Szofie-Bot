"""Casino guides split individual long fields, not just whole embeds."""

import tempfile
import unittest
from pathlib import Path
from cogs.economy import EconomyCog
from tests.test_system_integration import _FakeBot, _FakeInteraction, _status_reply_text


class CasinoGuideLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_custom_currency_names_keep_complete_guide_within_discord_limits(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            bot.config.for_guild(1).set("economy.currency_name", "Imperial glazed cosmic donuts " * 5)
            cog = object.__new__(EconomyCog)
            cog.bot, cog.econ = (bot, bot.economy)
            interaction = _FakeInteraction()
            await EconomyCog.casino.callback(cog, interaction)
            reply = interaction.response.calls[-1]
            pager = reply["view"]
            try:
                for page in pager.pages:
                    self.assertLessEqual(len(page), 6000)
                    self.assertLessEqual(len(page.fields), 25)
                    self.assertTrue(
                        all((len(field.value) <= 1024 and len(field.name) <= 256 for field in page.fields))
                    )
                text = _status_reply_text(reply)
                for phrase in (
                    "Five-card Charlie",
                    "My plushies",
                    "Bragging rights",
                    "straight-up progressive jackpot",
                    "career",
                    "no upper limit",
                ):
                    self.assertIn(phrase, text)
                self.assertFalse(reply["ephemeral"])
            finally:
                pager.stop()
