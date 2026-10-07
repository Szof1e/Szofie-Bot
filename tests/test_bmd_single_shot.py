"""One-round BMD responses, including legacy modes and persistent conversion."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from cogs.thor import ThorCog
from szofie import guides, thor
from szofie.storage import Storage
from tests.test_system_integration import _FakeBot, _FakeInteraction, _FakeUser


class SingleShotBmdTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_modes_and_full_magazines_get_exactly_one_attempt(self):
        for ammo in (1, 2, 3):
            for mode in ("single", "salvo"):
                for roll, expected in ((30, True), (31, False)):
                    with self.subTest(ammo=ammo, mode=mode, roll=roll), tempfile.TemporaryDirectory() as raw:
                        bot = _FakeBot(Path(raw))
                        cog = ThorCog(bot)
                        attacker = bot.economy.user(1, 123, 0)
                        thor.normalize(attacker).update(operational=True, rods=1, chambered=True)
                        victim = bot.economy.user(1, 456, 0)
                        victim["donuts"] = 10000
                        victim["vehicles"]["aegis"].update(
                            owned=True, bmd_owned=True, sm3_ammo=ammo, sm3_mode=mode
                        )
                        interaction = _FakeInteraction()
                        with patch("cogs.thor.random.randint", side_effect=[roll, 1]) as rolls:
                            await ThorCog.strike.callback(cog, interaction, _FakeUser(456))
                        self.assertEqual(rolls.call_count, 1)
                        report = interaction.followup.calls[-1]["embed"]
                        self.assertEqual("EXO-ATMOSPHERIC INTERCEPT" in report.title, expected)
                        if expected:
                            self.assertEqual(victim["vehicles"]["aegis"]["sm3_ammo"], ammo - 1)
                            self.assertIn("Interception chance:** 30%", report.description)
                            self.assertIn("SM-3s fired:** 1", report.description)
                        else:
                            self.assertEqual(victim["donuts"], 0)

    async def test_settlement_retires_legacy_mode_without_changing_valid_stock_or_jobs(self):
        state = {
            "owned": True,
            "bmd_owned": True,
            "sm3_ammo": 3,
            "sm3_mode": "salvo",
            "sm3_loading_until": "2099-01-01T00:00:00+00:00",
            "sm3_loading_qty": 1,
        }
        before = copy.deepcopy(state)
        thor.settle_aegis_bmd(state, {})
        for key, value in before.items():
            self.assertEqual(state[key], "single" if key == "sm3_mode" else value)

    async def test_mode_command_removed_and_guides_are_single_shot_only(self):
        self.assertNotIn("bmd-mode", {command.name for command in ThorCog.thor.commands})
        text = "\n".join((page.description for page in guides.thor_pages({}, color=0)))
        self.assertIn("ONE missile per incoming rod", text)
        self.assertNotIn("bmd-mode", text)
        self.assertNotIn("51%", text)
        self.assertNotIn("salvo", text)
