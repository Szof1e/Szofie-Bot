"""Channel recreation must not strand other modules on a deleted channel ID."""

import json
import tempfile
import unittest
from pathlib import Path
from cogs.moderation import Moderation
from tests.test_system_integration import _FakeBot


class NukeChannelReferenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_remaps_all_configured_destinations_and_persists_other_settings(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            bot = _FakeBot(root)
            cfg = bot.config.for_guild(1)
            destinations = (
                "general.log_channel",
                "economy.channel",
                "starboard.channel",
                "events.channel",
                "seasons.channel",
            )
            for key in destinations:
                cfg.set(key, 111)
            cfg.set("moderation.protected_channels", [111, 999])
            cfg.set("economy.slots_win_pct", 54)
            cfg.set("roles.mod", 111)
            changed = await Moderation(bot)._remap_channel(1, 111, 222)
            self.assertEqual(changed, 6)
            self.assertTrue(all((cfg.get(key) == 222 for key in destinations)))
            self.assertEqual(cfg.get("roles.mod"), 111)
            self.assertEqual(cfg.get("moderation.protected_channels"), [222, 999])
            self.assertEqual(cfg.get("economy.slots_win_pct"), 54)
            saved = json.loads((root / "config" / "1.json").read_text())
            self.assertEqual(saved["economy"]["channel"], 222)
            self.assertEqual(saved["seasons"]["channel"], 222)

    async def test_unrelated_and_empty_destinations_remain_unchanged(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cfg = bot.config.for_guild(1)
            cfg.set("starboard.channel", 333)
            cfg.set("events.channel", 444)
            cfg.set("economy.channel", 111)
            self.assertEqual(await Moderation(bot)._remap_channel(1, 111, 222), 1)
            self.assertEqual(cfg.get("starboard.channel"), 333)
            self.assertEqual(cfg.get("events.channel"), 444)
            self.assertIsNone(cfg.get("general.log_channel"))
            self.assertIsNone(cfg.get("seasons.channel"))
