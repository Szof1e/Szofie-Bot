"""NYX protects reports at delivery and on cached-page navigation."""

import copy
import datetime as dt
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
import discord
from discord import app_commands
from cogs.death_star import DeathStarCog
from cogs.economy import EconomyCog
from cogs.imperial_star_destroyer import ImperialStarDestroyerCog
from cogs.space import SpaceCog
from cogs.thor import ThorCog
from szofie import deathstar, imperial_star_destroyer as isd, nyx, space_fleet as fleet, thor, ui
from szofie.plushies import PLUSHIES
from tests.test_system_integration import _FakeBot, _FakeInteraction, _FakeUser


class NyxReportDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bot = _FakeBot(Path(self.temp.name))
        self.bot.get_channel = lambda _uid: None
        self.econ = object.__new__(EconomyCog)
        self.econ.bot, self.econ.econ = (self.bot, self.bot.economy)
        self.doc = self.bot.economy.store.load(1)
        self.now = dt.datetime.now(dt.timezone.utc)
        self.due = self.now + dt.timedelta(hours=12)

    def user(self, uid=123):
        return self.bot.economy.user(1, uid, 100)

    def cloak(self, uid):
        user = self.user(uid)
        nyx.normalize(user).update(
            status="orbit", active_until=(self.now + dt.timedelta(hours=3)).isoformat()
        )
        return user

    def replies(self, interaction):
        return interaction.response.calls + interaction.followup.calls

    def text(self, interaction):
        return "\n".join(
            (json.dumps(call["embed"].to_dict()) for call in self.replies(interaction) if call.get("embed"))
        )

    def cleanup_views(self, interaction):
        for call in self.replies(interaction):
            if call.get("view"):
                call["view"].stop()
            for file in call.get("attachments", []):
                file.close()

    async def test_ordinary_s400_status_keeps_its_public_policy(self):
        user = self.user()
        user.update(s400_owned=True, s400_interceptors=1, s400_building_at=self.due.isoformat())
        interaction = _FakeInteraction()
        await EconomyCog.aa_s400_status.callback(self.econ, interaction)
        self.assertFalse(self.replies(interaction)[-1]["ephemeral"])
        self.assertIn(str(int(self.due.timestamp())), self.text(interaction))

    async def test_s400_activation_during_save_moves_final_report_to_private(self):
        user = self.user()
        user.update(s400_building_at=self.due.isoformat())
        original = self.econ.persist

        async def persist(gid):
            self.cloak(123)
            await original(gid)

        with patch.object(self.econ, "persist", side_effect=persist):
            interaction = _FakeInteraction()
            await EconomyCog.aa_s400_status.callback(self.econ, interaction)
        self.assertTrue(self.replies(interaction)[-1]["ephemeral"])
        for call in interaction.response.calls:
            if not call.get("ephemeral") and call.get("embed"):
                self.assertNotIn(str(int(self.due.timestamp())), json.dumps(call["embed"].to_dict()))

    async def test_isd_other_target_is_censored_and_self_deadlines_remain_private(self):
        user = self.cloak(456)
        isd.normalize(user)["components"]["shipyard"].update(
            status="fabricating", ready_at=self.due.isoformat()
        )
        cog = ImperialStarDestroyerCog(self.bot)
        other = _FakeInteraction()
        await ImperialStarDestroyerCog.status.callback(cog, other, _FakeUser(456))
        self.assertIn("intelligence censored", self.text(other))
        self.assertNotIn(str(int(self.due.timestamp())), self.text(other))
        own = _FakeInteraction(user_id=456)
        await ImperialStarDestroyerCog.status.callback(cog, own)
        self.assertTrue(self.replies(own)[-1]["ephemeral"])
        pager = self.replies(own)[-1]["view"]
        text = json.dumps([page.to_dict() for page in pager.pages])
        self.assertIn(str(int(self.due.timestamp())), text)
        self.cleanup_views(own)

    async def test_cloaked_build_confirmations_are_private_and_construction_still_commits(self):
        cases = (
            (self.econ, EconomyCog.aa_s400_build, (), lambda u: u.get("s400_building_at")),
            (
                self.econ,
                EconomyCog.vehicle_build,
                (app_commands.Choice(name="B-52", value="b52"),),
                lambda u: u["vehicles"]["b52"]["building_until"],
            ),
            (
                ThorCog(self.bot),
                ThorCog.fabricate,
                (app_commands.Choice(name="Odin", value="odin"),),
                lambda u: thor.normalize(u)["components"]["odin"]["ready_at"],
            ),
            (
                ImperialStarDestroyerCog(self.bot),
                ImperialStarDestroyerCog.fabricate,
                (app_commands.Choice(name="Shipyard", value="shipyard"),),
                lambda u: isd.normalize(u)["components"]["shipyard"]["ready_at"],
            ),
            (
                SpaceCog(self.bot),
                SpaceCog.build,
                (app_commands.Choice(name="A-wing", value="awing"),),
                lambda u: fleet.ship(u, "awing")["build_until"],
            ),
        )
        for cog, command, args, deadline in cases:
            with self.subTest(command=command.name):
                user = self.cloak(123)
                user["donuts"] = 10**27
                interaction = _FakeInteraction()
                await command.callback(cog, interaction, *args)
                self.assertTrue(self.replies(interaction)[-1]["ephemeral"])
                self.assertIsNotNone(deadline(user))
                self.cleanup_views(interaction)

    async def test_deathstar_build_report_privacy_preserves_paid_state_and_separate_warning_path(self):
        user = self.cloak(123)
        user["donuts"] = 10**27
        cog = DeathStarCog(self.bot)

        def action(doc, account, state, current):
            state["transport_until"] = self.due.isoformat()
            account["donuts"] -= deathstar.TRANSPORT_COST
            return ("Private construction deadline", None, "deathstar-transport", -deathstar.TRANSPORT_COST)

        interaction = _FakeInteraction()
        cog.deliver_notices = AsyncMock()
        await cog.run(interaction, action)
        self.assertTrue(self.replies(interaction)[-1]["ephemeral"])
        self.assertEqual(user["donuts"], 10**27 - deathstar.TRANSPORT_COST)
        cog.deliver_notices.assert_awaited_once_with(1)
        record = dict(
            id="WARN", builder=123, guild_id=1, kind="component", component="hull", remaining_seconds=7200
        )
        self.assertIn("<@123>", cog.warning(record).description)

    async def test_target_activation_during_slow_plushie_render_drops_image_and_caption(self):
        user = self.user(456)
        user["plushies"] = {PLUSHIES[0].id: 1}

        def render(_mine):
            self.cloak(456)
            return b"synthetic image"

        interaction = _FakeInteraction()
        with patch.object(self.econ, "_render_plushie_collage", side_effect=render):
            await EconomyCog.plushies_cmd.callback(self.econ, interaction, _FakeUser(456))
        reply = self.replies(interaction)[-1]
        self.assertIn("intelligence censored", self.text(interaction))
        self.assertTrue(reply["ephemeral"])
        self.assertNotIn("file", reply)
        self.assertNotIn("plushies (", self.text(interaction))

    async def test_target_censorship_closes_unsent_media_and_removes_all_other_payload_fields(self):
        self.cloak(456)
        interaction = _FakeInteraction()
        nyx.protect_report(interaction, self.bot.economy, 456)
        image = discord.File(io.BytesIO(b"secret image"), filename="secret.png")
        view = ui.Paginator([ui.base_embed(description="Secret build")], 123)
        original_close = discord.File.close
        with patch.object(discord.File, "close", autospec=True, side_effect=original_close) as close:
            await ui.respond(
                interaction,
                content="Secret caption",
                embed=ui.base_embed(description="Secret deadline"),
                file=image,
                view=view,
            )
            close.assert_called_once_with(image)
        reply = self.replies(interaction)[-1]
        self.assertNotIn("content", reply)
        self.assertNotIn("file", reply)
        self.assertNotIn("view", reply)
        self.assertTrue(view.is_finished())
        self.assertIn("intelligence censored", self.text(interaction))

    async def test_placeholder_await_rechecks_late_target_activation(self):
        self.user(456)
        interaction = _FakeInteraction()
        nyx.protect_report(interaction, self.bot.economy, 456)
        await ui.defer_response(interaction, ephemeral=True)
        original = interaction.edit_original_response

        async def placeholder(**kwargs):
            self.cloak(456)
            return await original(**kwargs)

        interaction.edit_original_response = placeholder
        await ui.respond(interaction, embed=ui.base_embed(description="Secret deadline"))
        self.assertTrue(self.replies(interaction)[-1]["ephemeral"])
        self.assertNotIn("Secret deadline", self.text(interaction))
        self.assertIn("intelligence censored", self.text(interaction))

    async def test_old_public_self_and_other_pagers_recheck_while_private_self_stays_readable(self):
        for subject, private in ((123, False), (456, False), (123, True)):
            with self.subTest(subject=subject, private=private):
                user = self.user(subject)
                nyx.normalize(user)["active_until"] = None
                interaction = _FakeInteraction()
                nyx.protect_report(interaction, self.bot.economy, subject)
                view = ui.Paginator([ui.base_embed(description="Secret build deadline")], 123)
                await ui.respond(interaction, embed=view.pages[0], view=view, ephemeral=private)
                self.cloak(subject)
                await view.interaction_check(_FakeInteraction())
                self.assertEqual(
                    "intelligence censored" in view.pages[0].description, not (subject == 123 and private)
                )
                view.stop()

    async def test_public_launch_list_old_pager_censors_newly_cloaked_builder(self):
        user = self.user(456)
        fleet.ship(user, "awing").update(
            owned=True, launch={"ready_at": self.due.isoformat(), "attempts": []}
        )
        interaction = _FakeInteraction()
        await SpaceCog.fleet_launches.callback(SpaceCog(self.bot), interaction)
        view = self.replies(interaction)[-1]["view"]
        self.assertIn("<@456>", view.pages[0].description)
        self.cloak(456)
        await view.interaction_check(_FakeInteraction())
        self.assertNotIn("<@456>", view.pages[0].description)
        self.assertNotIn(str(int(self.due.timestamp())), view.pages[0].description)
        view.stop()

    async def test_private_statuses_stay_readable_and_unchanged_for_cloaked_account(self):
        self.cloak(123)
        for cog, command in (
            (ThorCog(self.bot), ThorCog.status),
            (SpaceCog(self.bot), SpaceCog.status),
            (SpaceCog(self.bot), SpaceCog.fleet_status),
            (DeathStarCog(self.bot), DeathStarCog.status),
        ):
            with self.subTest(command=command.name):
                interaction = _FakeInteraction()
                args = (None,) if command == SpaceCog.fleet_status else ()
                await command.callback(cog, interaction, *args)
                self.assertTrue(self.replies(interaction)[-1]["ephemeral"])
                self.assertNotIn("intelligence censored", self.text(interaction))
                self.cleanup_views(interaction)

    async def test_report_lookup_is_live_and_expiry_restores_a_new_report(self):
        user = self.user(456)
        interaction = _FakeInteraction()
        nyx.protect_report(interaction, self.bot.economy, 456)
        self.doc["users"]["456"] = copy.deepcopy(user)
        self.cloak(456)
        await ui.respond(interaction, embed=ui.base_embed(description="Secret construction"))
        self.assertIn("intelligence censored", self.text(interaction))
        nyx.normalize(self.user(456))["active_until"] = self.now.isoformat()
        fresh = _FakeInteraction()
        nyx.protect_report(fresh, self.bot.economy, 456)
        await ui.respond(fresh, embed=ui.base_embed(description="Visible construction"))
        self.assertIn("Visible construction", self.text(fresh))
        self.assertFalse(self.replies(fresh)[-1]["ephemeral"])

    async def test_recon_activation_during_persistence_never_delivers_construction_or_vault_report(self):
        for model in ("u2", "sr71", "deimos"):
            for cloaked in (123, 456) if model == "deimos" else (456,):
                with self.subTest(model=model, cloaked=cloaked):
                    for uid in (123, 456):
                        nyx.normalize(self.user(uid)).update(
                            status="orbit", active_until=None, last_activation_at=None
                        )
                    pilot, victim = (self.user(123), self.user(456))
                    self.econ._vehicle_state(pilot, model).update(owned=True, last_deploy_at=None)
                    victim.update(deep_vault_owned=True, deep_vault_balance=987654321)
                    self.econ._vehicle_state(victim, "b2")["building_until"] = self.due.isoformat()

                    async def ledger(gid, uid, amount, reason, **kwargs):
                        if reason == "vehicle-" + model + "-recon":
                            nyx.activate(self.doc, cloaked, self.econ.cfg(1))

                    interaction = _FakeInteraction()
                    with (
                        patch.object(self.bot.ledger, "record", side_effect=ledger),
                        patch("cogs.economy.random.randint", return_value=1),
                        patch.object(self.econ, "_strategic_art", return_value=None),
                    ):
                        await getattr(self.econ, "_" + model + "_execute")(interaction, _FakeUser(456))
                    text = self.text(interaction)
                    self.assertIn("RECONNAISSANCE JAMMED", text)
                    self.assertNotIn("987,654,321", text)
                    self.assertNotIn(str(int(self.due.timestamp())), text)
                    self.assertNotIn("456", pilot["recon_targets"])
                    if model == "deimos":
                        self.assertNotIn("123", victim["recon_targets"])

    async def test_recon_activation_while_public_completion_is_sent_still_blocks_private_package(self):
        for model in ("u2", "sr71"):
            with self.subTest(model=model):
                nyx.normalize(self.user(456)).update(
                    status="orbit", active_until=None, last_activation_at=None
                )
                pilot, victim = (self.user(), self.user(456))
                self.econ._vehicle_state(pilot, model).update(owned=True, last_deploy_at=None)
                victim.update(deep_vault_owned=True, deep_vault_balance=987654321)
                interaction = _FakeInteraction()
                original = interaction.followup.send

                async def send(**kwargs):
                    await original(**kwargs)
                    if "complete" in (kwargs.get("embed").title or "").lower():
                        nyx.activate(self.doc, 456, self.econ.cfg(1))

                interaction.followup.send = send
                with patch.object(self.econ, "_strategic_art", return_value=None):
                    await getattr(self.econ, "_" + model + "_execute")(interaction, _FakeUser(456))
                self.assertIn("RECONNAISSANCE JAMMED", self.text(interaction))
                self.assertNotIn("987,654,321", self.text(interaction))
                self.assertNotIn("456", pilot["recon_targets"])

    async def test_self_private_pager_outsiders_are_still_denied(self):
        self.cloak(123)
        interaction = _FakeInteraction()
        nyx.protect_report(interaction, self.bot.economy)
        view = ui.Paginator([ui.base_embed(description="Private build")], 123)
        await ui.respond(interaction, embed=view.pages[0], view=view)
        self.assertFalse(await view.interaction_check(_FakeInteraction(user_id=456)))
        view.stop()
