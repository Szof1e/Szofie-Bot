"""Uncapped admin corrections retain authorization, exact math and audit records."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
import discord
from cogs.economy import EconomyCog
from szofie.betting import SignedAmountTransformer
from tests.test_system_integration import _FakeBot, _FakeInteraction, _FakeUser
