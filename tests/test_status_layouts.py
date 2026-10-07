"""Readable status pages, complete content, private navigation and no rule changes."""

import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
from cogs.continuity import ContinuityCog
from cogs.death_star import DeathStarCog
from cogs.economy import EconomyCog
from cogs.events import Events
from cogs.imperial_star_destroyer import ImperialStarDestroyerCog
from cogs.space import SpaceCog
from cogs.thor import ThorCog
from szofie import (
    continuity,
    deathstar,
    imperial_star_destroyer as isd,
    nyx,
    space,
    space_fleet as fleet,
    status_ui,
    thor,
    ui,
)
from tests.test_system_integration import (
    _FakeBot,
    _FakeInteraction,
    _FakeUser,
    _status_reply_pages,
    _status_reply_text,
)


def assert_valid(test, pages):
    for page in pages:
        test.assertLessEqual(len(page), 6000)
        test.assertLessEqual(len(page.title or ""), 256)
        test.assertLessEqual(len(page.description or ""), 4096)
        test.assertLessEqual(len(page.fields), 25)
        for field in page.fields:
            test.assertLessEqual(len(field.name), 256)
            test.assertLessEqual(len(field.value), 1024)
            test.assertFalse(field.inline, "Status reports must stay one column on mobile")


class StatusFormattingTests(unittest.TestCase):
    def test_deadlines_include_local_finish_date_and_relative_time(self):
        due = dt.datetime(2030, 1, 2, 3, 4, tzinfo=dt.timezone.utc)
        expected = f"<t:{int(due.timestamp())}:f> · <t:{int(due.timestamp())}:R>"
        self.assertEqual(status_ui.deadline(due), expected)
        self.assertEqual(status_ui.deadline(due.isoformat()), expected)
        self.assertEqual(status_ui.deadline("invalid"), "Pending")
        self.assertEqual(status_ui.deadline(None), "No timer running")

    def test_overflow_is_lossless_and_stays_within_topic(self):
        values = ["\n".join((f"entry-{i}-{j}: " + "x" * 93 for j in range(50))) for i in range(6)]
        pages = status_ui.report(
            "Status",
            [
                ("Construction", [(f"Part {i}", value) for i, value in enumerate(values)]),
                ("Cargo", [("Payload", "LAST-CARGO-ITEM")]),
            ],
            footer="F" * 2000,
        )
        assert_valid(self, pages)
        fields = [field for page in pages[:-1] for field in page.fields]
        self.assertEqual("".join((field.value for field in fields)), "".join(values))
        self.assertIn("Cargo", pages[-1].title)
        self.assertEqual(pages[-1].fields[0].value, "LAST-CARGO-ITEM")
        self.assertTrue(all(("Construction" in page.title for page in pages[:-1])))

    def test_components_use_friendly_states_and_correct_window_clock(self):
        due = "2030-01-02T03:04:00+00:00"
        catalog = {"one": {"name": "First part"}, "two": {"name": "Second part"}}
        parts = {
            "one": {"status": "fabricating", "ready_at": due},
            "two": {"status": "launching", "window_id": "WINDOW"},
        }
        fields = status_ui.component_fields(catalog, parts, windows={"WINDOW": {"remaining_seconds": 3600}})
        self.assertIn("🏗️ Building", fields[0][1])
        self.assertIn(":f>", fields[0][1])
        self.assertIn("bot-online time remaining when checked", fields[1][1])
        self.assertIn("pauses offline", fields[1][1])


class StatusPagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_requester_only_navigation_and_topic_jump(self):
        pages = status_ui.report(
            "Status", [("Overview", [("State", "Ready")]), ("Construction", [("Part", "Building")])]
        )
        view = status_ui.StatusPager(pages, 123)
        try:
            outsider = _FakeInteraction(user_id=456)
            self.assertFalse(await view.interaction_check(outsider))
            self.assertEqual(view.index, 0)
            own = _FakeInteraction()
            own.response.edit_message = AsyncMock()
            self.assertTrue(await view.interaction_check(own))
            view.topic_select._values = ["1"]
            await view.select_topic(own)
            self.assertEqual(view.index, 1)
            self.assertTrue(view.next.disabled)
            self.assertEqual(own.response.edit_message.call_args.kwargs["embed"], pages[1])
        finally:
            view.stop()

    async def test_topic_jump_runs_with_live_censorship(self):
        pages = status_ui.report(
            "Status", [("Overview", [("State", "SECRET-HOLDING")]), ("Intel", [("Target", "SECRET-INTEL")])]
        )
        cloaked = False
        view = status_ui.StatusPager(
            pages, 123, page_guard=lambda index: nyx.censored_embed() if cloaked else None
        )
        try:
            cloaked = True
            interaction = _FakeInteraction()
            interaction.response.edit_message = AsyncMock()
            self.assertTrue(await view.interaction_check(interaction))
            view.topic_select._values = ["1"]
            await view.select_topic(interaction)
            self.assertTrue(all(("SECRET" not in str(page.to_dict()) for page in view.pages)))
            self.assertIn("censored", interaction.response.edit_message.call_args.kwargs["embed"].description)
        finally:
            view.stop()

    async def test_more_than_25_pages_keeps_menu_valid_and_all_pages_reachable(self):
        pages = status_ui.report("Status", [(f"Topic {i}", [("Entry", str(i))]) for i in range(55)])
        view = status_ui.StatusPager(pages, 123)
        try:
            for index in range(len(pages)):
                view.index = index
                view._sync()
                self.assertLessEqual(len(view.topic_select.options), 25)
                self.assertIn(str(index), [option.value for option in view.topic_select.options])
        finally:
            view.stop()


class StatusCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bot = _FakeBot(Path(self.temp.name))
        self.user = self.bot.economy.user(1, 123, 100)
        self.due = (space.now() + dt.timedelta(hours=12)).isoformat()

    def check_reply(self, interaction, *, private=True):
        reply = interaction.response.calls[-1]
        pages = _status_reply_pages(reply)
        assert_valid(self, pages)
        self.assertEqual(reply.get("ephemeral", False), private)
        view = reply.get("view")
        if view:
            self.addCleanup(view.stop)
        return reply

    async def test_deathstar_sections_keep_every_component_transport_timer_and_cargo(self):
        state = deathstar.normalize(self.user)
        for part in state["components"].values():
            part.update(status="fabricating", ready_at=self.due)
        state.update(transport_until=self.due, squadron_until=self.due, squadron_stock=2)
        state["cargo"].update(
            donuts=10**25, inventory={"uno": 5}, plushies={"labcoat21": 2}, materials={"ore": 8}
        )
        before = copy.deepcopy(state)
        cog = DeathStarCog(self.bot)
        cog.deliver_notices = AsyncMock()
        cog.image_reply = AsyncMock()
        interaction = _FakeInteraction()
        await DeathStarCog.status.callback(cog, interaction)
        pages = cog.image_reply.call_args.kwargs["pages"]
        assert_valid(self, pages)
        view = status_ui.StatusPager(pages, 123)
        self.addCleanup(view.stop)
        text = _status_reply_text({"embed": pages[0], "view": view})
        for part in deathstar.COMPONENTS.values():
            self.assertIn(part["name"], text)
        for label in (
            "Heavy Transport construction",
            "Squadron preparation",
            "Project escrow",
            "Onboard cargo",
        ):
            self.assertIn(label, text)
        self.assertNotIn("True", text)
        self.assertNotIn("False", text)
        after = copy.deepcopy(state)
        before.pop("income_at")
        after.pop("income_at")
        self.assertEqual(after, before)
        self.assertTrue(interaction.response.deferred["ephemeral"])

    async def test_deathstar_report_delivery_attaches_topic_pager(self):
        cog = DeathStarCog(self.bot)
        pages = status_ui.report("Station", [("Overview", [("State", "Ready")]), ("Cargo", [("Items", "3")])])
        interaction = _FakeInteraction()
        await ui.defer_response(interaction, ephemeral=True)
        await cog.image_reply(interaction, pages[0], pages=pages)
        view = interaction.original_edits[-1]["view"]
        self.addCleanup(view.stop)
        self.assertIsInstance(view, status_ui.StatusPager)
        self.assertEqual(view.author_id, 123)

    async def test_thor_overview_construction_ammo_service_and_defences(self):
        state = thor.normalize(self.user)
        state.update(
            operational=True,
            rods=3,
            chambered=False,
            resupply_until=self.due,
            resupply_qty=2,
            service_until=self.due,
            shots_since_service=3,
            gbi_stock=1,
            gbi_building_until=self.due,
            gbi_building_qty=1,
        )
        cog = ThorCog(self.bot)
        interaction = _FakeInteraction()
        await ThorCog.status.callback(cog, interaction)
        reply = self.check_reply(interaction)
        text = _status_reply_text(reply)
        for label in ("Launch readiness", "Kinetic magazine", "Platform servicing", "GBI/EKV", "Aegis BMD"):
            self.assertIn(label, text)
        self.assertEqual(
            [p.title.rsplit(" — ", 1)[-1] for p in _status_reply_pages(reply)],
            ["Overview", "Construction", "Ammunition and servicing", "Defences"],
        )

    async def test_isd_pager_censors_after_target_activates_nyx(self):
        target = self.bot.economy.user(1, 456, 100)
        isd.normalize(target)["components"]["shipyard"].update(status="fabricating", ready_at=self.due)
        cog = ImperialStarDestroyerCog(self.bot)
        interaction = _FakeInteraction()
        await ImperialStarDestroyerCog.status.callback(cog, interaction, _FakeUser(456))
        reply = self.check_reply(interaction, private=False)
        self.assertIn("Kuat Orbital Shipyard Core", _status_reply_text(reply))
        nyx.normalize(target).update(status="orbit", active_until=self.due)
        view = reply["view"]
        self.assertTrue(await view.interaction_check(_FakeInteraction()))
        self.assertTrue(all(("Kuat" not in str(p.to_dict()) for p in view.pages)))

    async def test_isd_public_pager_cloaks_when_own_nyx_activates_but_private_self_report_remains_readable(
        self,
    ):
        isd.normalize(self.user)["components"]["shipyard"].update(status="fabricating", ready_at=self.due)
        cog = ImperialStarDestroyerCog(self.bot)
        first = _FakeInteraction()
        await ImperialStarDestroyerCog.status.callback(cog, first)
        public = self.check_reply(first, private=False)
        nyx.normalize(self.user).update(status="orbit", active_until=self.due)
        self.assertTrue(await public["view"].interaction_check(_FakeInteraction()))
        self.assertNotIn("Kuat", _status_reply_text(public))
        refreshed = _FakeInteraction()
        await ImperialStarDestroyerCog.status.callback(cog, refreshed)
        private = self.check_reply(refreshed)
        self.assertTrue(await private["view"].interaction_check(_FakeInteraction()))
        self.assertIn("Kuat", _status_reply_text(private))

    async def test_fleet_keeps_each_ship_timers_with_payload_and_separates_intel_history(self):
        for key, spec in fleet.CRAFT.items():
            fleet.ship(self.user, key).update(
                owned=True,
                deployed=True,
                location="mars",
                payload=2,
                payload_until=self.due,
                repair_until=self.due,
            )
        state = fleet.normalize(self.user)
        state["history"] = [{"craft": "carrack", "kind": "survey", "body": "mars", "result": "UNIQUE-RESULT"}]
        state["recon"]["456"] = {"body": "mars", "expires_at": self.due, "rows": ["UNIQUE-INTEL"]}
        interaction = _FakeInteraction()
        await SpaceCog.fleet_status.callback(SpaceCog(self.bot), interaction)
        reply = self.check_reply(interaction)
        pages = _status_reply_pages(reply)
        for key, spec in fleet.CRAFT.items():
            page = next((p for p in pages if p.title.endswith(" — " + spec.name)))
            text = "\n".join((f.value for f in page.fields))
            self.assertIn(spec.payload_name, text)
            self.assertIn("**Repair:**", text)
            self.assertIn("**Payload prep:**", text)
        intel = next((p for p in pages if p.title.endswith(" — Intelligence")))
        history = next((p for p in pages if p.title.endswith(" — Mission history")))
        self.assertIn("UNIQUE-INTEL", str(intel.to_dict()))
        self.assertNotIn("UNIQUE-RESULT", str(intel.to_dict()))
        self.assertIn("UNIQUE-RESULT", str(history.to_dict()))

    async def test_small_storage_and_defence_reports_stay_single_page_and_keep_access(self):
        economy = object.__new__(EconomyCog)
        economy.bot, economy.econ = (self.bot, self.bot.economy)
        self.user.update(
            deep_vault_owned=True,
            deep_vault_balance=10**20,
            deep_vault_withdraw_amount=123,
            deep_vault_withdraw_at=self.due,
            s400_owned=True,
            s400_interceptors=1,
            aa_rockets_loaded=2,
            aa_rockets_stock=3,
        )
        continuity.normalize(self.user).update(owned=True, funds=10**13)
        for cog, command, private in (
            (economy, EconomyCog.deepvault_status, True),
            (economy, EconomyCog.aa_status, False),
            (economy, EconomyCog.aa_s400_status, False),
            (economy, EconomyCog.icbm_silo, False),
            (ContinuityCog(self.bot), ContinuityCog.status, True),
        ):
            with self.subTest(command=command.name):
                interaction = _FakeInteraction()
                await command.callback(cog, interaction)
                reply = self.check_reply(interaction, private=private)
                self.assertEqual(len(_status_reply_pages(reply)), 1)
                self.assertIn("Next action", _status_reply_text(reply))

    async def test_exploration_dashboard_keeps_its_existing_action_buttons(self):
        state = space.normalize(self.user)
        state.update(travel_until=self.due, destination="mars")
        interaction = _FakeInteraction()
        await SpaceCog.status.callback(SpaceCog(self.bot), interaction)
        reply = self.check_reply(interaction)
        self.assertEqual(
            {button.label for button in reply["view"].children},
            {"Atlas", "Colonies", "Expedition (6h)", "Next step", "Civilian contracts"},
        )
        text = _status_reply_text(reply)
        for label in (
            "Explorer and journey",
            "Current activity",
            "Onboard cargo",
            "Colonies and discoveries",
            "Arrival",
        ):
            self.assertIn(label, text)

    async def test_job_status_keeps_career_payday_licence_and_helper_in_one_column(self):
        cog = object.__new__(EconomyCog)
        cog.bot, cog.econ = (self.bot, self.bot.economy)
        employment = cog._employment(self.user)
        employment.update(active="engineering", manual_shifts=25, career_licence_tier=1)
        record = cog._career_record(employment, "engineering")
        record.update(xp=50, shifts=10, correct=8, earned=10**20)
        self.user.update(android21_helper=True, android21_helper_at=space.now().isoformat())
        interaction = _FakeInteraction()
        await EconomyCog.job_status.callback(cog, interaction)
        reply = self.check_reply(interaction)
        text = _status_reply_text(reply)
        for label in (
            "Promotion",
            "Today's payday",
            "Career record",
            "Advanced licence",
            "Autonomous Helper",
            "Next action",
        ):
            self.assertIn(label, text)
        self.assertEqual(len(_status_reply_pages(reply)), 1)

    async def test_event_status_separates_current_event_from_schedule(self):
        cog = object.__new__(Events)
        cog.bot, cog.econ = (self.bot, self.bot.economy)
        interaction = _FakeInteraction()
        await Events.event_status.callback(cog, interaction)
        reply = self.check_reply(interaction, private=False)
        self.assertEqual([field.name for field in reply["embed"].fields], ["📋 Current event", "⏳ Schedule"])
