"""Exercise real launch entry points with Discord's single-acknowledgement rule."""

import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import discord
from cogs.economy import EconomyCog
from tests.test_system_integration import _FakeBot, _FakeInteraction, _FakeUser


class IcbmAcknowledgementTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bot = _FakeBot(Path(self.temp.name))
        self.cog = object.__new__(EconomyCog)
        self.cog.bot, self.cog.econ = (self.bot, self.bot.economy)
        self.cfg = self.bot.config.for_guild(1)
        self.cfg.set("economy.icbm_media", False)
        self.cfg.set("economy.aa_media", False)
        self.attacker = self.cog.user(1, 123)
        self.attacker["icbm_ready"] = 1
        self.victim = self.cog.user(1, 456)
        self.victim.update(donuts=10000, bank=20000)

    def strict_interaction(self):
        interaction = _FakeInteraction()
        original_defer = interaction.response.defer

        async def defer_once(**kwargs):
            if interaction.response.is_done():
                raise discord.InteractionResponded(interaction)
            await original_defer(**kwargs)

        interaction.response.defer = AsyncMock(side_effect=defer_once)
        return interaction

    async def test_full_slash_launch_acknowledges_once_and_delivers_direct_hit(self):
        interaction = self.strict_interaction()
        with patch.object(self.cog, "_resolve_aa", return_value=(False, None, "")):
            await EconomyCog.icbm_launch.callback(self.cog, interaction, _FakeUser(456))
        self.assertEqual(interaction.response.defer.await_count, 1)
        self.assertEqual(len(interaction.original_edits), 1)
        self.assertIn("direct hit", interaction.original_edits[0]["embed"].title)
        self.assertEqual(self.attacker["icbm_ready"], 0)
        self.assertIsNotNone(self.attacker["icbm_launch_at"])
        self.assertLess(self.victim["donuts"], 10000)
        self.assertLess(self.victim["bank"], 20000)

    async def test_full_slash_interception_acknowledges_once_and_delivers_result(self):
        interaction = self.strict_interaction()
        before = copy.deepcopy(self.victim)
        with patch.object(self.cog, "_resolve_aa", return_value=(True, "rocket", "Radar AA")):
            await EconomyCog.icbm_launch.callback(self.cog, interaction, _FakeUser(456))
        self.assertEqual(interaction.response.defer.await_count, 1)
        self.assertEqual(len(interaction.original_edits), 1)
        self.assertIn("INTERCEPTED", interaction.original_edits[0]["embed"].title)
        self.assertEqual(self.attacker["icbm_ready"], 0)
        self.assertEqual(self.victim["donuts"], before["donuts"])
        self.assertEqual(self.victim["bank"], before["bank"])

    async def test_predeferred_core_launch_does_not_attempt_another_ack(self):
        interaction = self.strict_interaction()
        await interaction.response.defer(thinking=True)
        with patch.object(self.cog, "_resolve_aa", return_value=(False, None, "")):
            await self.cog._icbm_launch_run(interaction, self.cfg, _FakeUser(456))
        self.assertEqual(interaction.response.defer.await_count, 1)
        self.assertEqual(self.attacker["icbm_ready"], 0)
        self.assertEqual(len(interaction.original_edits), 1)

    async def test_failed_initial_ack_never_spends_missile_or_starts_cooldown(self):
        interaction = self.strict_interaction()
        interaction.response.defer = AsyncMock(side_effect=RuntimeError("ack failed"))
        before = copy.deepcopy(self.victim)
        with self.assertRaisesRegex(RuntimeError, "ack failed"):
            await EconomyCog.icbm_launch.callback(self.cog, interaction, _FakeUser(456))
        self.assertEqual(self.attacker["icbm_ready"], 1)
        self.assertIsNone(self.attacker["icbm_launch_at"])
        self.assertEqual(self.victim, before)

    async def test_rejected_target_does_not_spend_a_missile(self):
        interaction = self.strict_interaction()
        await EconomyCog.icbm_launch.callback(self.cog, interaction, _FakeUser(123))
        self.assertEqual(interaction.response.defer.await_count, 1)
        self.assertEqual(self.attacker["icbm_ready"], 1)
        self.assertIsNone(self.attacker["icbm_launch_at"])
