"""Loan persistence, lossless Discord output and display-only privacy guards."""

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from cogs.loans import Loans, LoanOfferView
from cogs.countries import Countries
from tests.test_system_integration import _FakeBot, _FakeInteraction, _FakeUser, _status_reply_text
