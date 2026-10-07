"""Fifth-generation mission acknowledgements and results are public, not intel."""

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from discord import app_commands
from cogs import air_dominance as commands
from cogs.economy import EconomyCog
from szofie import air_dominance as air
from tests.test_system_integration import _FakeBot, _FakeInteraction, _FakeUser


class PublicJetMissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bot = _FakeBot(Path(self.temp.name))
        self.cog = object.__new__(EconomyCog)
        self.cog.bot, self.cog.econ = (self.bot, self.bot.economy)
        self.bot.cogs["Economy"] = self.cog
        self.cog._strategic_art = lambda *_args: None
        self.pilot = self.bot.economy.user(1, 123, 10**15)
        self.victim = self.bot.economy.user(1, 456, 1000000)
        self.target = _FakeUser(456)
        for model in air.JETS:
            self.ready(self.pilot, model)
        self.ready(self.victim, "apache")
        self.recon(self.pilot)

    def ready(self, user, model):
        state = self.cog._vehicle_state(user, model)
        state.update(owned=True, ammo=4, loading_until=None, last_deploy_at=None)
        return state

    def recon(self, user):
        self.cog._grant_recon_package(
            user, 456, self.victim, dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1), source="sr71"
        )

    def assert_public(self, interaction):
        self.assertFalse(interaction.response.deferred["ephemeral"])
        reply = interaction.response.calls[-1]
        self.assertFalse(reply["ephemeral"])
        self.assertFalse(
            interaction.followup.calls, "Public missions should complete the public original response"
        )
        self.assertNotIn("szofie_nyx_report", interaction.extras)
        if reply.get("view"):
            reply["view"].stop()
        return reply

    async def deploy(self, model, objective=None, uid=123, target=None):
        interaction = _FakeInteraction(user_id=uid)
        with patch("cogs.air_dominance.random.randint", return_value=100):
            await EconomyCog.vehicle_deploy.callback(
                self.cog,
                interaction,
                app_commands.Choice(name=model, value=model),
                target or self.target,
                objective,
            )
        return interaction

    async def test_all_four_deploy_routes_acknowledge_and_finish_publicly(self):
        for model, objective in (("f22", None), ("f35", None), ("j20", "apache"), ("su57", "wallet")):
            with self.subTest(model=model):
                target = _FakeUser(123) if model == "f22" else self.target
                interaction = await self.deploy(model, objective, target=target)
                reply = self.assert_public(interaction)
                self.assertIn(air.JETS[model][1], reply["embed"].title)
                self.assertEqual(self.pilot["vehicles"][model]["ammo"], 3)

    async def test_dedicated_patrol_and_link_commands_are_public(self):
        patrol = _FakeInteraction()
        await EconomyCog.vehicle_patrol.callback(self.cog, patrol)
        self.assert_public(patrol)
        link = _FakeInteraction()
        await EconomyCog.vehicle_link.callback(self.cog, link, self.target)
        self.assert_public(link)

    async def test_su57_modal_second_pass_is_public_and_only_spends_once(self):
        first = await self.deploy("su57", "wallet")
        view = first.response.calls[-1]["view"]
        modal = commands.SecondPassModal(view)
        modal.objective._value = "ammo:apache"
        second = _FakeInteraction()
        with patch("cogs.air_dominance.random.randint", return_value=100):
            await modal.on_submit(second)
        self.assert_public(second)
        self.assertEqual(self.pilot["vehicles"]["su57"]["ammo"], 2)
        self.assertIsNone(self.pilot["vehicles"]["su57"]["second_pass"])
        rejected = _FakeInteraction()
        await modal.on_submit(rejected)
        self.assertTrue(rejected.followup.calls[-1]["ephemeral"])
        self.assertEqual(self.pilot["vehicles"]["su57"]["ammo"], 2)
        view.stop()

    async def test_interception_outcome_is_public_without_private_odds(self):
        self.victim.update(s400_owned=True, s400_interceptors=2)
        interaction = _FakeInteraction()
        with patch("cogs.air_dominance.random.randint", return_value=1):
            await EconomyCog.vehicle_deploy.callback(
                self.cog, interaction, app_commands.Choice(name="Su-57", value="su57"), self.target, "wallet"
            )
        reply = self.assert_public(interaction)
        self.assertIn("forced the jet down", reply["embed"].description)
        self.assertTrue(self.pilot["vehicles"]["su57"]["jet_damaged"])

    async def test_private_receipts_and_rejected_missions_stay_private(self):
        cfg = self.cog.cfg(1)
        for stage in ("build", "load", "repair"):
            interaction = _FakeInteraction()
            await commands.result(self.cog, interaction, cfg, "f22", stage, "Receipt")
            self.assertTrue(interaction.response.calls[-1]["ephemeral"])
        self.pilot["vehicles"]["su57"]["ammo"] = 0
        interaction = await self.deploy("su57", "wallet")
        self.assertTrue(interaction.followup.calls[-1]["ephemeral"])
        self.assertEqual(self.victim["donuts"], 1000000)

    async def test_slow_persistence_is_acknowledged_publicly_before_commit_finishes(self):
        async def save(_gid):
            self.assertEqual(interaction.response.deferred, {"thinking": True, "ephemeral": False})

        interaction = _FakeInteraction()
        self.cog.persist = AsyncMock(side_effect=save)
        await EconomyCog.vehicle_link.callback(self.cog, interaction, self.target)
        self.assert_public(interaction)

    def test_guide_describes_public_missions_and_pilot_only_controls(self):
        from szofie import guides

        pages = guides.air_dominance_pages(self.cog.cfg(1), color=0)
        text = "\n".join(((p.description or "") + " ".join((f.value for f in p.fields)) for p in pages))
        self.assertIn("Mission results are public", text)
        self.assertIn("pilot-only Make another pass", text)
        self.assertNotIn("confirmations/results are private", text)
        self.assertNotIn("private Make another pass", text)
