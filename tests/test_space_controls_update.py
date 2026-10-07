"""Folded NYX routes, exact megaproject prices and restart-safe transfer migration."""

import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from discord import app_commands
from cogs.continuity import ContinuityCog
from cogs.death_star import DeathStarCog
from cogs.satellite import SatelliteCog
from cogs.space import SpaceCog
from szofie import continuity, deathstar as ds, nyx
from szofie.economy import _default_user
from szofie.storage import Storage
from tests.test_system_integration import _FakeBot, _FakeInteraction, _FakeUser


class SpaceControlRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bot = _FakeBot(Path(self.temp.name))
        self.cog = SpaceCog(self.bot)
        self.service = SatelliteCog(self.bot)
        self.bot.cogs["SatelliteCog"] = self.service

    def choice(self, value):
        return app_commands.Choice(name=value, value=value)

    async def test_no_separate_group_and_all_relevant_menus_have_nyx(self):
        self.assertEqual(self.service.get_app_commands(), [])
        for command in (
            SpaceCog.build,
            SpaceCog.fleet_launch,
            SpaceCog.fleet_guide,
            SpaceCog.fleet_status,
            SpaceCog.fleet_mission,
            SpaceCog.fleet_intercept,
        ):
            choices = next((p for p in command.parameters if p.name == "craft")).choices
            self.assertIn("nyx", {p.value for p in choices})
            self.assertLessEqual(len(choices), 25)
            self.assertTrue(all((len(p.name) <= 100 for p in choices)))
        body = next((p for p in SpaceCog.fleet_mission.parameters if p.name == "body"))
        self.assertFalse(body.required)

    async def test_build_launch_status_guide_route_to_shared_service(self):
        for command, action in (
            (SpaceCog.build, "build"),
            (SpaceCog.fleet_launch, "launch"),
            (SpaceCog.fleet_status, "status"),
            (SpaceCog.fleet_guide, "guide"),
        ):
            handler = AsyncMock()
            setattr(self.service, action, handler)
            interaction = _FakeInteraction()
            await command.callback(self.cog, interaction, self.choice("nyx"))
            handler.assert_awaited_once_with(interaction)

    async def test_real_folded_build_keeps_price_defer_and_persistent_project(self):
        self.bot.ledger.record = AsyncMock()
        user = self.service._user(1, 123)
        user["donuts"] = nyx.BUILD_COST + 100
        interaction = _FakeInteraction()
        await SpaceCog.build.callback(self.cog, interaction, self.choice("nyx"))
        self.assertIsNotNone(interaction.response.deferred)
        self.assertEqual(user["donuts"], 100)
        self.assertEqual(user["nyx"]["status"], "fabricating")
        self.assertNotIn("nyx", user.get("space_fleet", {}).get("ships", {}))
        self.assertEqual(self.bot.ledger.record.await_args.args[3], "nyx-build")

    async def test_jam_has_no_body_or_target_and_cannot_be_used_by_other_craft(self):
        self.service.activate = AsyncMock()
        interaction = _FakeInteraction()
        await SpaceCog.fleet_mission.callback(self.cog, interaction, self.choice("nyx"), self.choice("jam"))
        self.service.activate.assert_awaited_once_with(interaction)
        for craft, mission, body, target in (
            ("nyx", "recon", None, None),
            ("nyx", "jam", "earth", None),
            ("nyx", "jam", None, _FakeUser(456)),
            ("xwing", "jam", "earth", None),
            ("prospector", "mine", None, None),
        ):
            self.service.activate.reset_mock()
            interaction = _FakeInteraction()
            await SpaceCog.fleet_mission.callback(
                self.cog, interaction, self.choice(craft), self.choice(mission), body, target
            )
            self.service.activate.assert_not_awaited()
            self.assertTrue(interaction.response.calls[-1]["ephemeral"])

    async def test_nyx_gbi_and_fighter_counter_paths_cannot_cross(self):
        self.service.intercept = AsyncMock()
        target = _FakeUser(456)
        interaction = _FakeInteraction()
        await SpaceCog.fleet_intercept.callback(
            self.cog, interaction, target, self.choice("nyx"), self.choice("gbi")
        )
        self.service.intercept.assert_awaited_once_with(interaction, target)
        for craft, counter in (("nyx", "xwing"), ("nyx", "bwing"), ("prospector", "gbi")):
            self.service.intercept.reset_mock()
            interaction = _FakeInteraction()
            await SpaceCog.fleet_intercept.callback(
                self.cog, interaction, target, self.choice(craft), self.choice(counter)
            )
            self.service.intercept.assert_not_awaited()
            self.assertTrue(interaction.response.calls[-1]["ephemeral"])

    async def test_real_status_private_and_guide_public_use_folded_commands(self):
        for command, private in ((SpaceCog.fleet_status, True), (SpaceCog.fleet_guide, False)):
            interaction = _FakeInteraction()
            await command.callback(self.cog, interaction, self.choice("nyx"))
            reply = interaction.response.calls[-1]
            self.assertEqual(reply["ephemeral"], private)
            view = reply.get("view")
            if view:
                try:
                    content = "\n".join((p.description for p in view.pages))
                    self.assertNotIn("/satellite", content)
                    self.assertIn("/space build craft:NYX", content)
                finally:
                    view.stop()


class RavenTransferUpdateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 10, 5, 0, 0, tzinfo=dt.timezone.utc)

    async def test_new_store_and_retrieve_are_fifteen_minutes(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = ContinuityCog(bot)
            user = bot.economy.user(1, 123, 100)
            continuity.normalize(user)["owned"] = True
            with patch("cogs.continuity.continuity_state.utcnow", return_value=self.now):
                await ContinuityCog.store.callback(
                    cog, _FakeInteraction(), app_commands.Choice(name="Funds", value="funds"), None, "50"
                )
            state = user["continuity"]
            self.assertEqual(
                continuity.parse_time(state["transfer_until"]), self.now + dt.timedelta(minutes=15)
            )
            continuity.settle(user, bot.config.for_guild(1), self.now + dt.timedelta(minutes=15))
            later = self.now + dt.timedelta(minutes=16)
            with patch("cogs.continuity.continuity_state.utcnow", return_value=later):
                await ContinuityCog.retrieve.callback(
                    cog, _FakeInteraction(), app_commands.Choice(name="Funds", value="funds"), None, "all"
                )
            self.assertEqual(continuity.parse_time(state["transfer_until"]), later + dt.timedelta(minutes=15))
            self.assertEqual(state["transfer_payload"]["amount"], 50)


class DeathStarPricingTests(unittest.TestCase):
    def test_core_price_is_one_septillion_and_guide_uses_actual_costs(self):
        self.assertEqual(ds.project_cost(), 1000 * 10**21)
        self.assertTrue(all((isinstance(v["cost"], int) for v in ds.COMPONENTS.values())))
        cog = object.__new__(DeathStarCog)
        pages = cog.guide_pages()
        self.assertIn("1 septillion", pages[0].description)
        self.assertIn("12.5 sextillion", pages[1].description)
        assembly = next((page for page in pages if "Assemble and fund" in page.title))
        self.assertIn("100 sextillion", assembly.description)
        self.assertTrue(all((len(p) < 6000 and len(p.description) < 4096 for p in pages)))
        self.assertEqual(ds.CHARGE_COST, 100 * ds.QI)
        self.assertEqual(ds.GDP_BASE, 400 * ds.QI)
