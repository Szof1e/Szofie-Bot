"""NYX censorship across ordinary reports, orbital projects, cached intel and paging."""

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from cogs.coalitions import CoalitionCog
from cogs.countries import Countries
from cogs.death_star import DeathStarCog
from cogs.economy import EconomyCog
from cogs.fishing import Fishing
from cogs.imperial_star_destroyer import ImperialStarDestroyerCog
from cogs.space import SpaceCog
from szofie import (
    coalitions,
    countries,
    deathstar as ds,
    imperial_star_destroyer as isd,
    nyx,
    space,
    space_fleet as fleet,
)
from tests.test_system_integration import _FakeBot, _FakeInteraction, _FakeUser


class NyxIntelCensorshipTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bot = _FakeBot(Path(self.temp.name))
        self.doc = self.bot.economy.store.load(1)
        self.now = space.now()
        self.econ = object.__new__(EconomyCog)
        self.econ.bot, self.econ.econ = (self.bot, self.bot.economy)
        self.bot.cogs["Economy"] = self.econ

    def user(self, uid):
        return self.bot.economy.user(1, uid, 100)

    def cloak(self, uid):
        user = self.user(uid)
        nyx.normalize(user).update(
            status="orbit",
            last_activation_at=self.now.isoformat(),
            active_until=(self.now + dt.timedelta(hours=3)).isoformat(),
        )
        return user

    def content(self, interaction):
        texts = []
        for call in interaction.response.calls + interaction.followup.calls:
            if call.get("embed"):
                texts.append(json.dumps(call["embed"].to_dict()))
            if call.get("view"):
                texts.extend((json.dumps(page.to_dict()) for page in call["view"].pages))
        return "\n".join(texts)

    async def test_own_isd_readable_but_private_and_project_counts_exclude_cloaked_users(self):
        cog = ImperialStarDestroyerCog(self.bot)
        own = self.cloak(123)
        isd.normalize(own)["components"]["shipyard"]["status"] = "ready"
        isd.normalize(self.cloak(456))["components"]["hull"]["status"] = "ready"
        interaction = _FakeInteraction()
        await ImperialStarDestroyerCog.status.callback(cog, interaction)
        content = self.content(interaction)
        self.assertIn("Project owner", content)
        self.assertIn("**Active player projects:** 1", content)
        self.assertTrue(interaction.response.calls[-1]["ephemeral"])

    async def test_balance_bank_badges_and_plushies_do_not_leak(self):
        user = self.cloak(456)
        user.update(donuts=987654321, bank=123456789)
        for command in (EconomyCog.balance, EconomyCog.bank, EconomyCog.badges_cmd, EconomyCog.plushies_cmd):
            interaction = _FakeInteraction()
            await command.callback(self.econ, interaction, _FakeUser(456))
            content = self.content(interaction)
            self.assertIn("intelligence censored", content)
            self.assertNotIn("987", content)
            self.assertNotIn("123,456", content)
            self.assertNotIn("456's", content)

    async def test_fishing_reports_censored_before_building_any_loadout(self):
        cog = Fishing(self.bot)
        user = self.cloak(456)
        user.update(fish={"cod": 123}, rods={"abyssal": 1}, equipped_rod="abyssal")
        for command in (Fishing.bucket, Fishing.bestiary):
            interaction = _FakeInteraction()
            await command.callback(cog, interaction, _FakeUser(456))
            self.assertIn("intelligence censored", self.content(interaction))
            self.assertNotIn("abyssal", self.content(interaction).lower())
        interaction = _FakeInteraction()
        await Fishing.rod.callback(cog, interaction, None, _FakeUser(456))
        self.assertIn("intelligence censored", self.content(interaction))

    async def test_deathstar_window_and_inspection_hide_builder_body_and_phase(self):
        cog = DeathStarCog(self.bot)
        self.cloak(456)
        record = {
            "id": "DS-BLIND",
            "guild_id": 1,
            "builder": 456,
            "kind": "fire",
            "component": "reactor",
            "body": "mars",
            "sovereign": 789,
            "remaining_seconds": 7200,
            "announced": True,
        }
        ds.windows(self.doc)[record["id"]] = record
        interaction = _FakeInteraction()
        await cog.inspect_window(interaction, "DS-BLIND")
        content = self.content(interaction)
        self.assertIn("DS-BLIND", content)
        self.assertIn("/deathstar intercept", content)
        for secret in ("456", "789", "mars", "reactor", "**fire**"):
            self.assertNotIn(secret, content)
        self.assertIn("<@456>", cog.warning(record).description)
        self.assertIn("mars", cog.warning(record).description)

    async def test_coalition_is_not_a_bypass_and_preopened_pager_rechecks(self):
        cog = CoalitionCog(self.bot)
        self.user(123)
        ally = self.user(456)
        ally["donuts"] = 876543210
        coalitions.state(self.doc)["groups"]["1"] = {
            "members": [123, 456],
            "expires_at": (self.now + dt.timedelta(days=1)).isoformat(),
        }
        interaction = _FakeInteraction()
        await CoalitionCog.intel.callback(cog, interaction)
        view = interaction.response.calls[-1]["view"]
        self.assertIsNotNone(view)
        try:
            self.assertIn("876,543,210", self.content(interaction))
            self.cloak(456)
            self.assertTrue(await view.interaction_check(_FakeInteraction()))
            content = "\n".join((json.dumps(page.to_dict()) for page in view.pages))
            self.assertNotIn("876,543,210", content)
            self.assertIn("intelligence censored", content)
            outsider = _FakeInteraction(user_id=789)
            self.assertFalse(await view.interaction_check(outsider))
        finally:
            view.stop()
        interaction = _FakeInteraction()
        await CoalitionCog.intel.callback(cog, interaction)
        try:
            self.assertNotIn("876,543,210", self.content(interaction))
        finally:
            interaction.response.calls[-1]["view"].stop()

    async def test_activation_invalidates_space_recon_and_preserves_unrelated_targets(self):
        self.cloak(456)
        observer = self.user(123)
        fleet.normalize(observer)["recon"] = {"456": {"rows": ["SECRET"]}, "789": {"rows": ["KEEP"]}}
        nyx.normalize(self.user(456))["last_activation_at"] = None
        nyx.activate(self.doc, 456, self.bot.config.for_guild(1), self.now)
        self.assertNotIn("456", fleet.normalize(observer)["recon"])
        self.assertIn("789", fleet.normalize(observer)["recon"])

    async def test_carrack_scan_at_impact_is_jammed_and_cannot_grant_cached_intel(self):
        observer = self.user(123)
        target = self.cloak(456)
        hull = fleet.ship(observer, "carrack")
        hull.update(owned=True, deployed=True, payload=1)
        space.normalize(observer)["surveys"]["mars"] = {}
        mission = fleet.start(self.doc, 123, "carrack", "recon", "mars", self.now, "scan-nyx", target_uid=456)
        due = space.at(mission["ready_at"])
        fleet.settle_world(self.doc, due)
        self.assertNotIn("456", fleet.normalize(observer)["recon"])
        self.assertEqual(fleet.snapshot(target, "mars", due), [nyx.CENSORED])
        self.assertFalse(hull["damaged"])
        self.assertIn("intelligence censored", fleet.normalize(observer)["history"][-1]["result"])

    async def test_fleet_status_and_objectives_cannot_show_stored_recon(self):
        cog = SpaceCog(self.bot)
        observer = self.user(123)
        self.cloak(456)
        fleet.normalize(observer)["recon"]["456"] = {
            "body": "mars",
            "rows": ["SECRET PAYLOAD 2468"],
            "expires_at": (self.now + dt.timedelta(hours=4)).isoformat(),
        }
        interaction = _FakeInteraction()
        interaction.namespace = SimpleNamespace(target=_FakeUser(456), body="mars")
        self.assertEqual(await cog.fleet_objectives(interaction, ""), [])
        await SpaceCog.fleet_status.callback(cog, interaction)
        try:
            self.assertNotIn("SECRET PAYLOAD", self.content(interaction))
            self.assertNotIn("456", fleet.normalize(observer)["recon"])
        finally:
            interaction.response.calls[-1]["view"].stop()

    async def test_atlas_omits_cloaked_artificial_moon_and_claim_owner(self):
        cog = SpaceCog(self.bot)
        target = self.cloak(456)
        ds.normalize(target).update(operational=True, location="mars")
        space.galaxy(self.doc)["claims"]["mars"] = 456
        interaction = _FakeInteraction()
        await SpaceCog.atlas.callback(cog, interaction)
        try:
            content = self.content(interaction)
            self.assertNotIn("deathstar-456", content)
            self.assertNotIn("<@456>", content)
            self.assertIn("claimed", content)
        finally:
            interaction.response.calls[-1]["view"].stop()

    async def test_country_inspection_redacts_owner_defence_and_progress(self):
        cog = Countries(self.bot)
        self.cloak(456)
        country = countries.COUNTRIES[0]
        countries.state(self.doc)["territories"][country.id] = {
            "owner": 456,
            "development_level": 10,
            "fortification": 999,
        }
        interaction = _FakeInteraction()
        await Countries.inspect.callback(cog, interaction, country.id)
        self.assertIn("intelligence censored", self.content(interaction))
        self.assertNotIn("Estimated defense", self.content(interaction))
        self.assertNotIn("999", self.content(interaction))

    async def test_public_atlas_pagers_recheck_nyx_before_showing_cached_pages(self):
        self.user(456)
        ds.normalize(self.user(456)).update(operational=True, location="mars")
        space.galaxy(self.doc)["claims"]["mars"] = 456
        countries.state(self.doc)["territories"][countries.COUNTRIES[0].id] = {"owner": 456}
        for command, cog in ((SpaceCog.atlas, SpaceCog(self.bot)), (Countries.atlas, Countries(self.bot))):
            nyx.normalize(self.user(456))["active_until"] = None
            interaction = _FakeInteraction()
            await command.callback(cog, interaction)
            view = interaction.response.calls[-1]["view"]
            try:
                self.assertIn("456", self.content(interaction))
                self.cloak(456)
                self.assertTrue(await view.interaction_check(_FakeInteraction()))
                cached = "\n".join((json.dumps(page.to_dict()) for page in view.pages))
                self.assertNotIn("456", cached)
                self.assertIn("intelligence censored", cached)
            finally:
                view.stop()

    async def test_own_empty_plushies_report_is_private_even_with_explicit_self_target(self):
        self.cloak(123)
        interaction = _FakeInteraction()
        await EconomyCog.plushies_cmd.callback(self.econ, interaction, interaction.user)
        self.assertTrue(interaction.response.calls[-1]["ephemeral"])

    async def test_expiry_restores_new_isd_report_and_active_start_is_not_retroactive(self):
        user = self.cloak(456)
        state = nyx.normalize(user)
        self.assertFalse(nyx.active(user, self.now - dt.timedelta(seconds=1)))
        self.assertTrue(nyx.active(user, self.now))
        self.assertFalse(nyx.active(user, self.now + dt.timedelta(hours=3)))
        state["active_until"] = (self.now - dt.timedelta(seconds=1)).isoformat()
        interaction = _FakeInteraction()
        await ImperialStarDestroyerCog.status.callback(
            ImperialStarDestroyerCog(self.bot), interaction, _FakeUser(456)
        )
        self.assertNotIn("intelligence censored", self.content(interaction))
        self.assertIn("no active", self.content(interaction))

    async def test_expired_field_never_revives_pre_activation_space_intel(self):
        user = self.cloak(456)
        later = self.now + dt.timedelta(hours=3, minutes=5)
        self.assertFalse(nyx.active(user, later))
        self.assertTrue(nyx.blocks_snapshot(user, self.now - dt.timedelta(minutes=5), later))
        self.assertFalse(nyx.blocks_snapshot(user, later, later))
        self.assertFalse(
            nyx.blocks_snapshot(user, self.now - dt.timedelta(minutes=5), self.now - dt.timedelta(seconds=1))
        )

    async def test_carrack_delayed_settlement_after_expiry_does_not_restore_older_scan(self):
        observer = self.user(123)
        self.user(456)
        fleet.ship(observer, "carrack").update(owned=True, deployed=True, payload=1)
        space.normalize(observer)["surveys"]["mars"] = {}
        mission = fleet.start(
            self.doc,
            123,
            "carrack",
            "recon",
            "mars",
            self.now - dt.timedelta(hours=4),
            "delayed-scan",
            target_uid=456,
        )
        self.cloak(456)
        fleet.settle_world(self.doc, self.now + dt.timedelta(hours=3, minutes=1))
        self.assertNotIn("456", fleet.normalize(observer)["recon"])
        self.assertIn("intelligence censored", fleet.normalize(observer)["history"][-1]["result"])
        self.assertLess(space.at(mission["ready_at"]), self.now)
