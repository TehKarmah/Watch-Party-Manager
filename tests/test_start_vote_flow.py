import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import (
    handle_customize_vote_submit,
    handle_start_vote_use_defaults,
    parse_optional_bool_field,
    parse_optional_int_field,
    parse_start_vote_overrides,
    parse_vote_reminder_hours_before_close,
)
from watch_party_manager.domain.vote import VoteVisibility
from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.services.nominee_selection_service import NomineeSelectionService
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.vote_service import VoteService
from watch_party_manager.start_vote_view import (
    START_VOTE_CHOICE_TIMEOUT_SECONDS,
    CustomizeVoteModal,
    StartVoteChoiceView,
)

WASH_CREW_ROLE_ID = 999


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, user_id: int, roles=()) -> None:
        self.id = user_id
        self.roles = list(roles)


class FakeResponse:
    def __init__(self) -> None:
        self.sent_message = None
        self.sent_ephemeral = None
        self.sent_modal = None

    async def send_message(self, content, ephemeral=False, view=None) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral

    async def send_modal(self, modal) -> None:
        self.sent_modal = modal


class FakeSentMessage:
    def __init__(self, message_id: int) -> None:
        self.id = message_id


class FakeInteraction:
    def __init__(self, user_id: int, guild_id=100, channel_id=200) -> None:
        self.user = FakeMember(user_id, roles=[FakeRole(WASH_CREW_ROLE_ID)])
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.response = FakeResponse()
        self._original_response = FakeSentMessage(message_id=9999)

    async def original_response(self):
        return self._original_response


class StartVoteFlowTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(Path(self._temp_dir.name) / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(
                Path(self._temp_dir.name) / "suggestion_databases.json"
            ),
        )
        self.vote_service = VoteService(
            self.suggestion_service, repository=JsonVoteRepository(Path(self._temp_dir.name) / "voting.json")
        )
        self.nominee_selection_service = NomineeSelectionService(self.suggestion_service, self.vote_service)
        self.database_id = self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=100, channel_id=200
        ).database.database_id
        for title in ("The Matrix", "Inception", "Interstellar", "Arrival", "Her"):
            self.suggestion_service.suggest(title, database_id=self.database_id)
        self.default_nominee_count = 3

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _interaction(self) -> FakeInteraction:
        return FakeInteraction(user_id=1)


class UseDefaultsTests(StartVoteFlowTestCase):
    async def test_use_defaults_creates_a_round_with_configured_values(self) -> None:
        interaction = self._interaction()

        await handle_start_vote_use_defaults(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
        )

        vote_round = self.vote_service.get_open_round()
        self.assertIsNotNone(vote_round)
        self.assertEqual(vote_round.visibility, VoteVisibility.VISIBLE)
        self.assertEqual(len(vote_round.candidate_suggestion_ids), self.default_nominee_count)

    async def test_use_defaults_uses_the_configured_duration(self) -> None:
        before = datetime.now(timezone.utc)
        interaction = self._interaction()

        await handle_start_vote_use_defaults(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
        )

        vote_round = self.vote_service.get_open_round()
        expected = before + timedelta(days=7)  # DEFAULT_VOTE_DURATION_DAYS
        self.assertAlmostEqual(vote_round.closes_at.timestamp(), expected.timestamp(), delta=5)

    async def test_use_defaults_sends_the_interactive_voting_post(self) -> None:
        interaction = self._interaction()

        await handle_start_vote_use_defaults(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
        )

        self.assertFalse(interaction.response.sent_ephemeral)
        self.assertIn("1. ", interaction.response.sent_message)

    async def test_use_defaults_still_enforces_wash_crew_permission(self) -> None:
        interaction = FakeInteraction(user_id=1)
        interaction.user = FakeMember(user_id=1, roles=[FakeRole(1)])  # not WASH Crew

        await handle_start_vote_use_defaults(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
        )

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIn("WASH Crew", interaction.response.sent_message)
        self.assertIsNone(self.vote_service.get_open_round())

    async def test_use_defaults_still_rejects_insufficient_suggestions(self) -> None:
        empty_suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(Path(self._temp_dir.name) / "empty_suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(
                Path(self._temp_dir.name) / "empty_suggestion_databases.json"
            ),
        )
        empty_vote_service = VoteService(
            empty_suggestion_service, repository=JsonVoteRepository(Path(self._temp_dir.name) / "empty_voting.json")
        )
        empty_database_id = empty_suggestion_service.create_database(
            "Sunday Watch Party", guild_id=100, channel_id=200
        ).database.database_id
        empty_suggestion_service.suggest("Only One", database_id=empty_database_id)
        selector = NomineeSelectionService(empty_suggestion_service, empty_vote_service)
        interaction = self._interaction()

        await handle_start_vote_use_defaults(
            interaction,
            empty_vote_service,
            empty_suggestion_service,
            selector,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
        )

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIn("At least 2", interaction.response.sent_message)
        self.assertIsNone(empty_vote_service.get_open_round())


class CustomizeVoteTests(StartVoteFlowTestCase):
    async def test_customized_nominee_count(self) -> None:
        interaction = self._interaction()

        await handle_customize_vote_submit(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            nominee_count_text="5",
            duration_days_text=None,
            visibility_text=None,
        )

        vote_round = self.vote_service.get_open_round()
        self.assertIsNotNone(vote_round)
        self.assertEqual(len(vote_round.candidate_suggestion_ids), 5)

    async def test_customized_duration(self) -> None:
        before = datetime.now(timezone.utc)
        interaction = self._interaction()

        await handle_customize_vote_submit(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            nominee_count_text=None,
            duration_days_text="3",
            visibility_text=None,
        )

        vote_round = self.vote_service.get_open_round()
        expected = before + timedelta(days=3)
        self.assertAlmostEqual(vote_round.closes_at.timestamp(), expected.timestamp(), delta=5)

    async def test_customized_visibility(self) -> None:
        interaction = self._interaction()

        await handle_customize_vote_submit(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            nominee_count_text=None,
            duration_days_text=None,
            visibility_text="blind",
        )

        vote_round = self.vote_service.get_open_round()
        self.assertEqual(vote_round.visibility, VoteVisibility.BLIND)

    async def test_blank_fields_fall_back_to_defaults(self) -> None:
        interaction = self._interaction()

        await handle_customize_vote_submit(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            nominee_count_text="",
            duration_days_text="   ",
            visibility_text=None,
        )

        vote_round = self.vote_service.get_open_round()
        self.assertEqual(vote_round.visibility, VoteVisibility.VISIBLE)
        self.assertEqual(len(vote_round.candidate_suggestion_ids), self.default_nominee_count)

    async def test_invalid_nominee_count_is_rejected(self) -> None:
        interaction = self._interaction()

        await handle_customize_vote_submit(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            nominee_count_text="99",
            duration_days_text=None,
            visibility_text=None,
        )

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIn("between 2 and 10", interaction.response.sent_message)
        self.assertIsNone(self.vote_service.get_open_round())

    async def test_non_numeric_nominee_count_is_rejected_with_a_clear_message(self) -> None:
        interaction = self._interaction()

        await handle_customize_vote_submit(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            nominee_count_text="a lot",
            duration_days_text=None,
            visibility_text=None,
        )

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIn("not a whole number", interaction.response.sent_message)
        self.assertIsNone(self.vote_service.get_open_round())

    async def test_invalid_duration_is_rejected(self) -> None:
        interaction = self._interaction()

        await handle_customize_vote_submit(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            nominee_count_text=None,
            duration_days_text="0",
            visibility_text=None,
        )

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIsNone(self.vote_service.get_open_round())

    async def test_invalid_duration_above_maximum_is_rejected(self) -> None:
        interaction = self._interaction()

        await handle_customize_vote_submit(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            nominee_count_text=None,
            duration_days_text="45",
            visibility_text=None,
        )

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIsNone(self.vote_service.get_open_round())

    async def test_invalid_visibility_is_rejected(self) -> None:
        interaction = self._interaction()

        await handle_customize_vote_submit(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            nominee_count_text=None,
            duration_days_text=None,
            visibility_text="sideways",
        )

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIn("blind", interaction.response.sent_message.lower())
        self.assertIsNone(self.vote_service.get_open_round())

    async def test_customize_still_enforces_wash_crew_permission(self) -> None:
        interaction = self._interaction()
        interaction.user = FakeMember(user_id=1, roles=[FakeRole(1)])  # not WASH Crew

        await handle_customize_vote_submit(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            nominee_count_text="5",
            duration_days_text=None,
            visibility_text=None,
        )

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIn("WASH Crew", interaction.response.sent_message)
        self.assertIsNone(self.vote_service.get_open_round())

    async def test_defaults_unchanged_after_a_customized_vote(self) -> None:
        interaction = self._interaction()

        await handle_customize_vote_submit(
            interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            nominee_count_text="5",
            duration_days_text="2",
            visibility_text="blind",
        )
        first_round = self.vote_service.get_open_round()
        self.vote_service.close_round(first_round.id)

        # The configured default is passed in explicitly each time by
        # bot.py (from WatchPartyBot.default_nominee_count) -- nothing in
        # the customize path ever mutates it. A fresh "Use Defaults" call
        # afterward should still use the original default, not 5.
        self.assertEqual(self.default_nominee_count, 3)
        second_interaction = self._interaction()
        await handle_start_vote_use_defaults(
            second_interaction,
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
        )

        second_round = self.vote_service.get_open_round()
        self.assertEqual(second_round.visibility, VoteVisibility.VISIBLE)
        self.assertEqual(len(second_round.candidate_suggestion_ids), 3)


class CustomizeVoteReminderTests(StartVoteFlowTestCase):
    """FR-027: reminder overrides threaded through /start_vote's "Customize This Vote" flow."""

    async def _submit(self, reminder_enabled_text=None, reminder_hours_text=None) -> None:
        await handle_customize_vote_submit(
            self._interaction(),
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            default_nominee_count=self.default_nominee_count,
            nominee_count_text=None,
            duration_days_text=None,
            visibility_text=None,
            reminder_enabled_text=reminder_enabled_text,
            reminder_hours_text=reminder_hours_text,
        )

    async def test_default_reminder_is_enabled_when_not_customized(self) -> None:
        # Using defaults (no reminder override) leaves reminder_enabled as
        # None on the round -- resolved later against the guild's default,
        # which is itself enabled by default (see VoteNotificationsConfig).
        await self._submit()

        vote_round = self.vote_service.get_open_round()
        self.assertIsNone(vote_round.reminder_enabled)

    async def test_default_reminder_timing_is_not_overridden_when_not_customized(self) -> None:
        await self._submit()

        vote_round = self.vote_service.get_open_round()
        self.assertIsNone(vote_round.reminder_hours_before_close)

    async def test_custom_reminder_timing_is_stored_on_the_round(self) -> None:
        await self._submit(reminder_hours_text="4")

        vote_round = self.vote_service.get_open_round()
        self.assertEqual(vote_round.reminder_hours_before_close, 4)

    async def test_reminder_can_be_explicitly_disabled(self) -> None:
        await self._submit(reminder_enabled_text="no")

        vote_round = self.vote_service.get_open_round()
        self.assertEqual(vote_round.reminder_enabled, False)

    async def test_reminder_can_be_explicitly_enabled(self) -> None:
        await self._submit(reminder_enabled_text="yes")

        vote_round = self.vote_service.get_open_round()
        self.assertEqual(vote_round.reminder_enabled, True)

    async def test_invalid_reminder_hours_is_rejected_and_creates_no_round(self) -> None:
        await self._submit(reminder_hours_text="900")

        self.assertIsNone(self.vote_service.get_open_round())

    async def test_invalid_reminder_enabled_text_is_rejected_and_creates_no_round(self) -> None:
        await self._submit(reminder_enabled_text="maybe")

        self.assertIsNone(self.vote_service.get_open_round())


class StartVoteChoiceViewTests(unittest.IsolatedAsyncioTestCase):
    async def _noop(self, interaction) -> None:
        pass

    async def test_choice_view_has_two_buttons(self) -> None:
        view = StartVoteChoiceView(self._noop, self._noop)
        self.assertEqual(len(view.children), 2)

    async def test_choice_view_uses_the_expected_timeout(self) -> None:
        view = StartVoteChoiceView(self._noop, self._noop)
        self.assertEqual(view.timeout, START_VOTE_CHOICE_TIMEOUT_SECONDS)

    async def test_choice_buttons_have_stable_labels_and_custom_ids(self) -> None:
        view = StartVoteChoiceView(self._noop, self._noop)
        self.assertEqual(
            [(button.label, button.custom_id) for button in view.children],
            [
                ("Use Defaults", "wpm_start_vote_use_defaults"),
                ("Customize This Vote", "wpm_start_vote_customize"),
            ],
        )

    async def test_use_defaults_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_use_defaults(interaction) -> None:
            calls.append("used_defaults")

        view = StartVoteChoiceView(on_use_defaults, self._noop)
        await view.children[0].callback(interaction=object())

        self.assertEqual(calls, ["used_defaults"])

    async def test_customize_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_customize(interaction) -> None:
            calls.append("customize")

        view = StartVoteChoiceView(self._noop, on_customize)
        await view.children[1].callback(interaction=object())

        self.assertEqual(calls, ["customize"])

    async def test_interaction_cancellation_creates_no_round(self) -> None:
        # Simply constructing the choice view (as /start_vote does) and
        # never invoking either callback -- as happens if the member never
        # clicks anything, or the view times out -- must never create a
        # round on its own.
        StartVoteChoiceView(self._noop, self._noop)
        # No assertion needed beyond "this doesn't raise and does
        # nothing" -- there's no vote_service in scope here at all,
        # which is exactly the point: nothing can be created without an
        # explicit choice.


class CustomizeVoteModalTests(unittest.TestCase):
    async def _noop(
        self, interaction, nominee_count_text, duration_days_text, visibility_text,
        reminder_enabled_text, reminder_hours_text,
    ) -> None:
        pass

    def test_modal_has_five_fields(self) -> None:
        modal = CustomizeVoteModal(self._noop)
        self.assertEqual(len(modal.children), 5)

    def test_modal_fields_are_all_optional(self) -> None:
        modal = CustomizeVoteModal(self._noop)
        self.assertTrue(all(not field.required for field in modal.children))

    def test_includes_reminder_fields(self) -> None:
        modal = CustomizeVoteModal(self._noop)
        self.assertIn(modal.reminder_enabled_input, modal.children)
        self.assertIn(modal.reminder_hours_input, modal.children)


class CustomizeVoteModalSubmitTests(unittest.IsolatedAsyncioTestCase):
    async def test_submit_forwards_all_five_raw_values(self) -> None:
        received = []

        async def on_submit(interaction, nominee_count_text, duration_days_text, visibility_text,
                             reminder_enabled_text, reminder_hours_text) -> None:
            received.append(
                (nominee_count_text, duration_days_text, visibility_text, reminder_enabled_text, reminder_hours_text)
            )

        modal = CustomizeVoteModal(on_submit)
        modal.nominee_count_input._value = "5"
        modal.duration_days_input._value = "3"
        modal.visibility_input._value = "blind"
        modal.reminder_enabled_input._value = "no"
        modal.reminder_hours_input._value = "12"

        await modal.on_submit(interaction=object())

        self.assertEqual(received, [("5", "3", "blind", "no", "12")])



class ParseOptionalIntFieldTests(unittest.TestCase):
    def test_returns_none_for_none(self) -> None:
        self.assertIsNone(parse_optional_int_field(None))

    def test_returns_none_for_blank_string(self) -> None:
        self.assertIsNone(parse_optional_int_field("   "))

    def test_parses_a_valid_integer(self) -> None:
        self.assertEqual(parse_optional_int_field("5"), 5)

    def test_strips_whitespace(self) -> None:
        self.assertEqual(parse_optional_int_field("  7  "), 7)

    def test_rejects_non_numeric_text(self) -> None:
        with self.assertRaises(ValueError):
            parse_optional_int_field("abc")


class ParseStartVoteOverridesTests(unittest.TestCase):
    def test_blank_values_resolve_to_defaults(self) -> None:
        self.assertEqual(
            parse_start_vote_overrides(None, "   ", ""),
            (None, None, "visible", None, None),
        )

    def test_values_are_trimmed_and_parsed(self) -> None:
        self.assertEqual(
            parse_start_vote_overrides(" 5 ", " 3 ", " blind "),
            (5, 3, "blind", None, None),
        )

    def test_numeric_parse_errors_are_preserved(self) -> None:
        with self.assertRaisesRegex(ValueError, "not a whole number"):
            parse_start_vote_overrides("many", None, None)

    # --- FR-027: reminder overrides -------------------------------------------

    def test_blank_reminder_fields_resolve_to_none(self) -> None:
        nominee_count, duration_days, visibility, reminder_enabled, reminder_hours = parse_start_vote_overrides(
            None, None, None, "", "  "
        )
        self.assertIsNone(reminder_enabled)
        self.assertIsNone(reminder_hours)

    def test_reminder_enabled_yes_is_parsed_true(self) -> None:
        *_, reminder_enabled, _ = parse_start_vote_overrides(None, None, None, "yes", None)
        self.assertTrue(reminder_enabled)

    def test_reminder_enabled_no_is_parsed_false(self) -> None:
        *_, reminder_enabled, _ = parse_start_vote_overrides(None, None, None, "no", None)
        self.assertFalse(reminder_enabled)

    def test_reminder_hours_is_parsed_as_an_integer(self) -> None:
        *_, reminder_hours = parse_start_vote_overrides(None, None, None, None, "12")
        self.assertEqual(reminder_hours, 12)

    def test_invalid_reminder_enabled_text_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "yes' or 'no'"):
            parse_start_vote_overrides(None, None, None, "maybe", None)

    def test_invalid_reminder_hours_text_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "not a whole number"):
            parse_start_vote_overrides(None, None, None, None, "soon")


class ParseOptionalBoolFieldTests(unittest.TestCase):
    def test_returns_none_for_none(self) -> None:
        self.assertIsNone(parse_optional_bool_field(None))

    def test_returns_none_for_blank_string(self) -> None:
        self.assertIsNone(parse_optional_bool_field("   "))

    def test_parses_yes_variants_as_true(self) -> None:
        for value in ["yes", "y", "true", "on", "enable", "enabled", "YES", " Yes "]:
            with self.subTest(value=value):
                self.assertTrue(parse_optional_bool_field(value))

    def test_parses_no_variants_as_false(self) -> None:
        for value in ["no", "n", "false", "off", "disable", "disabled", "NO", " No "]:
            with self.subTest(value=value):
                self.assertFalse(parse_optional_bool_field(value))

    def test_rejects_unrecognized_text(self) -> None:
        with self.assertRaisesRegex(ValueError, "yes' or 'no'"):
            parse_optional_bool_field("maybe")


class ParseVoteReminderHoursBeforeCloseTests(unittest.TestCase):
    def test_none_resolves_to_none(self) -> None:
        self.assertIsNone(parse_vote_reminder_hours_before_close(None))

    def test_accepts_a_value_within_bounds(self) -> None:
        self.assertEqual(parse_vote_reminder_hours_before_close(24), 24)

    def test_accepts_the_minimum_bound(self) -> None:
        self.assertEqual(parse_vote_reminder_hours_before_close(1), 1)

    def test_accepts_the_maximum_bound(self) -> None:
        self.assertEqual(parse_vote_reminder_hours_before_close(720), 720)

    def test_rejects_zero(self) -> None:
        with self.assertRaises(ValueError):
            parse_vote_reminder_hours_before_close(0)

    def test_rejects_a_value_above_the_maximum(self) -> None:
        with self.assertRaises(ValueError):
            parse_vote_reminder_hours_before_close(721)


if __name__ == "__main__":
    unittest.main()
