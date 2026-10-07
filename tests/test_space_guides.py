"""Clear public instructions, complete ship coverage and safe topic navigation."""

import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import discord
from discord import app_commands
from cogs.space import SpaceCog
from cogs.imperial_star_destroyer import ImperialStarDestroyerCog
from cogs.death_star import DeathStarCog
from szofie import deathstar as ds, space_fleet as fleet, space_guides, ui
from tests.test_system_integration import _FakeBot, _FakeInteraction


class GuideContentTests(unittest.TestCase):
    def test_deathstar_counter_tutorial_matches_actual_recipe_and_stage_rules(self):
        pages = DeathStarCog.guide_pages(object.__new__(DeathStarCog))
        tutorial = next((page for page in pages if "Intercept sections step by step" in page.title))
        for phrase in (
            "/deathstar squadron",
            ui.format_donuts(ds.SQUADRON_COST),
            f"{ds.SQUADRON_HOURS}h",
            f"{ds.MAX_SQUADRONS} ready",
            f"{ds.MAX_DEFENDERS} different defenders",
            "one attempt",
            "15%",
            "2 bot-online-hour",
            "/arsenal",
            "/deathstar windows",
            "/deathstar intercept window:<DS-ID>",
            "NOT the builder or component name",
            "NO ISD",
            "hit or miss",
            "three attempts",
            "Self/coalition-allied",
            "fabricating/ready",
            "already in orbit",
            "pause",
            "NYX",
        ):
            self.assertIn(phrase, tutorial.description)
        repair = next((page for page in pages if "Defend and repair" in page.title)).description
        for phrase in (
            "Section deployment",
            "Final assembly",
            "Superlaser firing",
            "4 bot-online hours",
            "10%",
            "100 quintillion, 24h",
            "200 quintillion, 48h",
            "sections survive",
        ):
            self.assertIn(phrase, repair)
        for page in pages:
            self.assertLessEqual(len(page.description), 4096)
            self.assertLessEqual(len(page), 6000)

    def test_deathstar_warning_names_counter_prep_command_and_exact_window(self):
        cog = object.__new__(DeathStarCog)
        for kind, chance, consequence in (
            ("component", 15, "only this deploying section"),
            ("assembly", 15, "All eight sections survive"),
            ("fire", 10, "disables the station"),
        ):
            record = {
                "id": "DS-EXAMPLE",
                "kind": kind,
                "builder": 456,
                "remaining_seconds": 7200,
                "body": "mars",
                "sovereign": 789,
            }
            warning = cog.warning(record)
            for phrase in (
                "/deathstar squadron",
                "10 quintillion, 12h",
                "Prepare it in advance",
                "/deathstar intercept window:DS-EXAMPLE",
                "ordinary X-wings, B-wings, AA and GBI/EKV",
                f"{chance}%",
                "hit or miss",
                "coalition ally",
                consequence,
            ):
                self.assertIn(phrase, warning.description)
            self.assertLessEqual(len(warning), 6000)

    def test_main_space_guide_has_dedicated_deathstar_interception_topic(self):
        sections = dict(space_guides.orientation_sections())
        text = sections["Death Star section interception"]
        for phrase in (
            "/deathstar squadron",
            "/deathstar windows",
            "window:<DS-ID>",
            "12h",
            "2 bot-online hours",
            "4 bot-online hours",
            "15%",
            "10%",
            "five different defenders",
            "not mean three tries",
            "No open window",
            "Patriot/S-400",
            "GBI/EKV",
            "Aegis",
        ):
            self.assertIn(phrase, text)
        self.assertLessEqual(len(text), 4096)

    def test_each_ship_has_one_tutorial_with_commands_targets_and_counters(self):
        self.assertEqual(set(space_guides.SHIP_TUTORIALS), set(fleet.CRAFT))
        sections = dict(space_guides.ship_sections())
        for key, spec in fleet.CRAFT.items():
            with self.subTest(craft=key):
                text = sections[spec.name]
                for phrase in (
                    "What it does:",
                    "Get it ready:",
                    "How to use it:",
                    "Limits/results:",
                    "What interacts with it:",
                    "/space build",
                    "/space fleet launch",
                    "repair",
                ):
                    self.assertIn(phrase, text)
                self.assertIn(str(spec.capacity), text)
                self.assertLess(len(text), 4096)

    def test_all_interaction_families_and_intel_distinctions_are_explicit(self):
        text = "\n".join((t for _, t in space_guides.orientation_sections()))
        for phrase in (
            "/space fleet intercept",
            "/space intercept",
            "/space fleet raid",
            "/space fleet escort",
            "Y-wing",
            "Arquitens",
            "/isd intercept",
            "/deathstar intercept",
            "/thor intercept",
            "SM-3",
            "B-wing",
            "Alliance Assault Squadron",
            "GBI/EKV",
            "U-2, Deimos and SR-71",
            "do not replace Carrack",
            "not player intel",
            "independent ordinary hull",
        ):
            self.assertIn(phrase, text)

    def test_optional_ship_selector_covers_the_entire_catalogue(self):
        options = next((p for p in SpaceCog.fleet_guide.parameters if p.name == "craft"))
        self.assertFalse(options.required)
        self.assertEqual({v.value for v in options.choices}, set(fleet.CRAFT) | {"nyx"})


class GuideNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_topic_menus_cover_all_main_pages_without_duplicates(self):
        with tempfile.TemporaryDirectory() as raw:
            cog = SpaceCog(_FakeBot(Path(raw)))
            interaction = _FakeInteraction()
            await SpaceCog.guide.callback(cog, interaction)
            response = interaction.response.calls[-1]
            self.assertFalse(response["ephemeral"])
            pager = response["view"]
            try:
                self.assertIsInstance(pager, space_guides.SpaceGuidePager)
                selects = [child for child in pager.children if isinstance(child, discord.ui.Select)]
                values = [int(option.value) for select in selects for option in select.options]
                self.assertEqual(values, list(range(len(pager.pages))))
                self.assertTrue(all((len(s.options) <= 25 for s in selects)))
                self.assertTrue(
                    all((len(option.label) <= 100 for select in selects for option in select.options))
                )
                self.assertTrue(all((child.row <= 4 for child in pager.children)))
                headings = [page.title.split(" — ", 1)[-1] for page in pager.pages]
                for heading in (
                    "Choose what you want to do",
                    "What attacks what?",
                    "Know these terms before using a ship",
                ):
                    self.assertEqual(headings.count(heading), 1)
                for index, page in enumerate(pager.pages, 1):
                    self.assertTrue(page.title.startswith(f"Space Guide {index}/{len(pager.pages)}"))
            finally:
                pager.stop()

    async def test_topic_selection_and_arrows_remain_in_sync(self):
        pages = SpaceCog.fleet_pages()
        pager = space_guides.SpaceGuidePager(pages, 123)
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=123), response=SimpleNamespace(edit_message=AsyncMock())
        )
        try:
            select = next((child for child in pager.children if isinstance(child, discord.ui.Select)))
            select._values = [str(len(pages) - 1)]
            self.assertTrue(await pager.interaction_check(interaction))
            await select.callback(interaction)
            self.assertEqual(pager.index, len(pages) - 1)
            self.assertTrue(pager.next.disabled)
            self.assertFalse(pager.prev.disabled)
            self.assertEqual(pager.counter.label, f"{len(pages)}/{len(pages)}")
            interaction.response.edit_message.assert_awaited_with(embed=pages[-1], view=pager)
            await pager.prev.callback(interaction)
            self.assertEqual(pager.index, len(pages) - 2)
            self.assertFalse(pager.next.disabled)
        finally:
            pager.stop()

    async def test_outsider_cannot_control_public_guide(self):
        pager = space_guides.SpaceGuidePager(SpaceCog.fleet_pages(), 123)
        try:
            outsider = _FakeInteraction(user_id=456)
            self.assertFalse(await pager.interaction_check(outsider))
            self.assertEqual(pager.index, 0)
            self.assertTrue(outsider.response.calls[-1]["ephemeral"])
        finally:
            pager.stop()

    async def test_second_topic_menu_selects_the_final_main_guide_page(self):
        with tempfile.TemporaryDirectory() as raw:
            cog = SpaceCog(_FakeBot(Path(raw)))
            awaitable = _FakeInteraction()
            await SpaceCog.exploration.callback(cog, awaitable)
            pager = awaitable.response.calls[-1]["view"]
            try:
                selects = [child for child in pager.children if isinstance(child, discord.ui.Select)]
                self.assertGreater(len(selects), 1)
                final = selects[-1]
                final._values = [final.options[-1].value]
                click = SimpleNamespace(
                    user=SimpleNamespace(id=123), response=SimpleNamespace(edit_message=AsyncMock())
                )
                await final.callback(click)
                self.assertEqual(pager.index, len(pager.pages) - 1)
                self.assertIn("Why will my command not work?", pager.pages[pager.index].title)
                self.assertTrue(pager.next.disabled)
            finally:
                pager.stop()

    async def test_direct_ship_guide_starts_on_the_correct_tutorial(self):
        with tempfile.TemporaryDirectory() as raw:
            cog = SpaceCog(_FakeBot(Path(raw)))
            for key, spec in fleet.CRAFT.items():
                interaction = _FakeInteraction()
                await SpaceCog.fleet_guide.callback(
                    cog, interaction, app_commands.Choice(name=spec.name, value=key)
                )
                reply = interaction.response.calls[-1]
                pager = reply["view"]
                try:
                    self.assertEqual(reply["embed"].title.split(" — ", 1)[-1], spec.name)
                    self.assertIs(pager.pages[pager.index], reply["embed"])
                    self.assertEqual(pager.author_id, interaction.user.id)
                    self.assertNotEqual(pager.index, 0)
                finally:
                    pager.stop()

    async def test_specialized_guides_have_topic_navigation_and_counter_instructions(self):
        with tempfile.TemporaryDirectory() as raw:
            bot = _FakeBot(Path(raw))
            cog = ImperialStarDestroyerCog(bot)
            interaction = _FakeInteraction()
            with patch.object(cog, "_guard", new=AsyncMock(return_value=bot.config.for_guild(1))):
                await ImperialStarDestroyerCog.guide.callback(cog, interaction)
            reply = interaction.response.calls[-1]
            pager = reply["view"]
            try:
                self.assertIsInstance(pager, space_guides.SpaceGuidePager)
                text = "\n".join((page.description for page in pager.pages))
                for phrase in (
                    "Command order:",
                    "How to defend:",
                    "/isd counter-build",
                    "window_id:",
                    "ordinary `/space fleet launch`",
                    "/space guide",
                    "/space fleet guide",
                ):
                    self.assertIn(phrase, text)
                self.assertTrue(
                    all((len(page) <= 6000 and len(page.description) <= 4096 for page in pager.pages))
                )
            finally:
                pager.stop()
            station = DeathStarCog(bot)
            interaction = _FakeInteraction()
            await DeathStarCog.guide.callback(station, interaction)
            reply = interaction.response.calls[-1]
            pager = reply["view"]
            try:
                self.assertIsInstance(pager, space_guides.SpaceGuidePager)
                text = "\n".join((page.description for page in pager.pages))
                for phrase in (
                    "Heavy Transport is NOT a GR-75",
                    "Worked defense:",
                    "/deathstar squadron",
                    "/deathstar windows",
                    "window:",
                    "not your ordinary X-wing/Y-wing hulls",
                ):
                    self.assertIn(phrase, text)
                self.assertTrue(
                    all((len(page) <= 6000 and len(page.description) <= 4096 for page in pager.pages))
                )
            finally:
                pager.stop()


if __name__ == "__main__":
    unittest.main()
