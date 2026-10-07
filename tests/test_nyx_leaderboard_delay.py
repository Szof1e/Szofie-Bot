"""NYX removes public ranking rows for three hours without changing real funds."""

import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from cogs.economy import EconomyCog
from cogs.seasons import Seasons
from szofie import nyx, thor, ui
from szofie.economy import _default_user
from szofie.storage import Storage
from tests.test_system_integration import _FakeBot, _FakeInteraction


class NyxLeaderboardStateTests(unittest.TestCase):
    def setUp(self):
        self.now = thor.utcnow()
        self.user = _default_user(0)
        self.user.update(
            donuts=10**25 + 123,
            bank=10**24 + 456,
            deep_vault_balance=10**30,
            crown=True,
            equipped_title="certified",
        )
        nyx.normalize(self.user)["status"] = "orbit"
        self.doc = {"users": {"123": self.user}}

    def test_activation_omits_entire_row_until_exact_expiry_without_moving_money(self):
        before = copy.deepcopy(self.user)
        due = nyx.activate(self.doc, 123, {}, self.now)
        self.assertEqual(due, self.now + dt.timedelta(hours=3))
        self.assertIsNone(nyx.leaderboard_entry(self.user, self.now))
        self.assertNotIn("leaderboard_snapshot", self.user["nyx"])
        for key in ("donuts", "bank", "deep_vault_balance"):
            self.assertEqual(self.user[key], before[key])
        self.user.update(donuts=1, bank=2, equipped_title="grandmoff", crown=False)
        self.assertIsNone(nyx.leaderboard_entry(self.user, due - dt.timedelta(microseconds=1)))
        live = nyx.leaderboard_entry(self.user, due)
        self.assertEqual(live["wealth"], 3)
        self.assertIsNotNone(live["title"])
        self.assertFalse(live["crown"])

    def test_hiding_survives_restart_and_reactivation_without_snapshots(self):
        nyx.activate(self.doc, 123, {}, self.now)
        restored = json.loads(json.dumps(self.doc))
        user = restored["users"]["123"]
        self.assertIsNone(nyx.leaderboard_entry(user, self.now))
        user.update(donuts=777, bank=888)
        self.assertIsNone(nyx.leaderboard_entry(user, self.now + dt.timedelta(hours=2)))
        self.assertEqual(nyx.leaderboard_entry(user, self.now + dt.timedelta(hours=3))["wealth"], 1665)
        nyx.activate(restored, 123, {}, self.now + dt.timedelta(hours=20))
        self.assertIsNone(nyx.leaderboard_entry(user, self.now + dt.timedelta(hours=20)))
        self.assertEqual(nyx.leaderboard_entry(user, self.now + dt.timedelta(hours=23))["wealth"], 1665)

    def test_failed_activation_does_not_replace_snapshot_or_extend_the_field(self):
        nyx.activate(self.doc, 123, {}, self.now)
        before = copy.deepcopy(self.user)
        with self.assertRaises(ValueError):
            nyx.activate(self.doc, 123, {}, self.now + dt.timedelta(hours=1))
        self.assertEqual(self.user, before)

    def test_even_valid_legacy_snapshots_are_ignored_without_mutating_records(self):
        nyx.activate(self.doc, 123, {}, self.now)
        state = self.user["nyx"]
        legacy = {
            "wealth": 999,
            "activation_at": state["last_activation_at"],
            "captured_at": self.now.isoformat(),
            "title": None,
            "crown": False,
        }
        for broken in (None, {"wealth": "999"}, {"wealth": 999, "activation_at": "wrong"}, legacy):
            state["leaderboard_snapshot"] = broken
            before = copy.deepcopy(self.user)
            self.assertIsNone(nyx.leaderboard_entry(self.user, self.now))
            self.assertEqual(self.user, before)
        self.assertEqual(
            nyx.leaderboard_entry(self.user, self.now + dt.timedelta(hours=3))["wealth"],
            self.user["donuts"] + self.user["bank"],
        )

    def test_own_summary_says_hidden_without_publishing_a_frozen_amount(self):
        nyx.activate(self.doc, 123, {}, self.now)
        text = nyx.summary(self.user, {}, self.now)
        self.assertIn("Hidden entirely", text)
        self.assertNotIn("frozen", text)
        self.assertNotIn(ui.format_donuts(self.user["donuts"] + self.user["bank"]), text)
