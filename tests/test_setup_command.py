"""Tests for FR-028's /setup wiring in bot.py.

Covers the pure/testable pieces bot.py adds for the wizard: the
permission gate, the field parsers each modal submission uses, the
progress-header/completion-summary builders, the startup role fallback,
and send_setup_wizard_step's per-step Discord rendering (exercised with
fake interactions instead of a live Discord connection, mirroring
test_edit_vote_command.py's FakeInteraction/FakeResponse pattern).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from watch_party_manager.bot import (
    WatchPartyBot,
    build_setup_completion_summary,
    build_setup_step_header,
    parse_setup_backup_interval_days,
    parse_setup_backup_retention_count,
    parse_setup_candidate_selection,
    parse_setup_reminder_enabled,
    parse_setup_reminder_hours_before_close,
    parse_setup_voting_candidate_count,
    parse_setup_voting_duration_days,
    parse_setup_voting_visibility,
    perform_setup_permission_check,
    perform_setup_redirect_check,
    resolve_startup_role_ids,
    send_setup_wizard_step,
)
from watch_party_manager.domain.guild_configuration import (
    GuildConfiguration,
    GuildVoteVisibility,
    JoinMode,
)
from watch_party_manager.domain.setup_wizard import SetupWizardDraft, SetupWizardState, SetupWizardStep
from watch_party_manager.domain.suggestion_database_configuration import CandidateSelectionMode
from watch_party_manager.persistence.guild_configuration_repository import GuildConfigurationRepository
from watch_party_manager.persistence.setup_wizard_repository import SetupWizardRepository
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.setup_wizard_service import SetupWizardService
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.setup_wizard_view import (
    ReviewStepView,
    WashCrewRoleStepView,
    WatchPartyRoleStepView,
)

GUILD_ID = 100
WASH_CREW_ROLE_ID = 111
WATCH_PARTY_ROLE_ID = 222
DESTINATION_CHANNEL_ID = 400


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, roles=()) -> None:
        self.roles = list(roles)


class FakeResponse:
    def __init__(self) -> None:
        self.sent_message = None
        self.sent_ephemeral = None
        self.sent_view = None
        self.edited_content = None
        self.edited_view = "not-edited"
        self.sent_modal = None

    async def send_message(self, content, ephemeral=False, view=None) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral
        self.sent_view = view

    async def edit_message(self, content=None, view=None) -> None:
        self.edited_content = content
        self.edited_view = view

    async def send_modal(self, modal) -> None:
        self.sent_modal = modal


class FakeInteraction:
    def __init__(self, user=None, guild=None) -> None:
        self.user = user if user is not None else FakeMember()
        self.response = FakeResponse()
        self.guild = guild


class WatchPartyBotWiringTests(unittest.TestCase):
    def test_constructor_wires_up_the_setup_wizard_service(self) -> None:
        bot = WatchPartyBot(token="test-token")
        self.assertIsInstance(bot.setup_wizard_service, SetupWizardService)

    def test_apply_role_configuration_updates_bot_and_permission_service(self) -> None:
        bot = WatchPartyBot(token="test-token")
        bot.apply_role_configuration(WASH_CREW_ROLE_ID, WATCH_PARTY_ROLE_ID)
        self.assertEqual(bot.wash_crew_role_id, WASH_CREW_ROLE_ID)
        self.assertEqual(bot.watch_party_member_role_id, WATCH_PARTY_ROLE_ID)
        self.assertEqual(bot.permission_service.wash_crew_role_id, WASH_CREW_ROLE_ID)
        self.assertEqual(bot.permission_service.watch_party_member_role_id, WATCH_PARTY_ROLE_ID)


class PerformSetupPermissionCheckTests(unittest.TestCase):
    def test_anyone_may_run_setup_before_a_wash_crew_role_is_configured(self):
        message, blocked = perform_setup_permission_check(FakeMember(), None)
        self.assertFalse(blocked)

    def test_wash_crew_members_may_run_setup_once_configured(self):
        member = FakeMember(roles=[FakeRole(WASH_CREW_ROLE_ID)])
        message, blocked = perform_setup_permission_check(member, WASH_CREW_ROLE_ID)
        self.assertFalse(blocked)

    def test_non_wash_crew_members_are_blocked_once_configured(self):
        member = FakeMember(roles=[FakeRole(999)])
        message, blocked = perform_setup_permission_check(member, WASH_CREW_ROLE_ID)
        self.assertTrue(blocked)
        self.assertIn("WASH Crew role", message)


class PerformSetupRedirectCheckTests(unittest.TestCase):
    """FR-029: completed setup must redirect to /config instead of restarting."""

    def test_no_configuration_yet_does_not_redirect(self) -> None:
        self.assertIsNone(perform_setup_redirect_check(None))

    def test_incomplete_setup_does_not_redirect(self) -> None:
        configuration = GuildConfiguration(guild_id=GUILD_ID, guild_name="Guild", setup_completed=False)
        self.assertIsNone(perform_setup_redirect_check(configuration))

    def test_completed_setup_redirects_to_config(self) -> None:
        configuration = GuildConfiguration(guild_id=GUILD_ID, guild_name="Guild", setup_completed=True)
        message = perform_setup_redirect_check(configuration)
        self.assertIsNotNone(message)
        self.assertIn("/config", message)
        self.assertIn("already been completed", message)


class ResolveStartupRoleIdsTests(unittest.TestCase):
    def _configuration(self) -> GuildConfiguration:
        config = GuildConfiguration(guild_id=GUILD_ID, guild_name="Guild")
        config.wash_crew_role_id = WASH_CREW_ROLE_ID
        config.watch_party_role.role_id = WATCH_PARTY_ROLE_ID
        return config

    def test_env_vars_win_when_both_are_set(self):
        wash_crew, watch_party = resolve_startup_role_ids(1, 2, self._configuration())
        self.assertEqual((wash_crew, watch_party), (1, 2))

    def test_falls_back_to_guild_configuration_when_env_vars_are_unset(self):
        wash_crew, watch_party = resolve_startup_role_ids(None, None, self._configuration())
        self.assertEqual((wash_crew, watch_party), (WASH_CREW_ROLE_ID, WATCH_PARTY_ROLE_ID))

    def test_mixes_sources_when_only_one_env_var_is_set(self):
        wash_crew, watch_party = resolve_startup_role_ids(1, None, self._configuration())
        self.assertEqual((wash_crew, watch_party), (1, WATCH_PARTY_ROLE_ID))

    def test_stays_none_when_no_configuration_exists(self):
        wash_crew, watch_party = resolve_startup_role_ids(None, None, None)
        self.assertEqual((wash_crew, watch_party), (None, None))


class ParseSetupFieldsTests(unittest.TestCase):
    def test_voting_candidate_count_valid_and_invalid(self):
        self.assertEqual(parse_setup_voting_candidate_count("4"), 4)
        with self.assertRaises(ValueError):
            parse_setup_voting_candidate_count("1")
        with self.assertRaises(ValueError):
            parse_setup_voting_candidate_count("abc")

    def test_voting_duration_days_valid_and_invalid(self):
        self.assertEqual(parse_setup_voting_duration_days("10"), 10)
        with self.assertRaises(ValueError):
            parse_setup_voting_duration_days("0")
        with self.assertRaises(ValueError):
            parse_setup_voting_duration_days("31")

    def test_voting_visibility_valid_and_invalid(self):
        self.assertEqual(parse_setup_voting_visibility("Blind"), GuildVoteVisibility.BLIND)
        with self.assertRaises(ValueError):
            parse_setup_voting_visibility("secret")

    def test_candidate_selection_valid_and_invalid(self):
        self.assertEqual(parse_setup_candidate_selection("random"), CandidateSelectionMode.RANDOM)
        with self.assertRaises(ValueError):
            parse_setup_candidate_selection("weighted")

    def test_reminder_enabled_required_and_invalid(self):
        self.assertTrue(parse_setup_reminder_enabled("yes"))
        self.assertFalse(parse_setup_reminder_enabled("no"))
        with self.assertRaises(ValueError):
            parse_setup_reminder_enabled("")
        with self.assertRaises(ValueError):
            parse_setup_reminder_enabled("maybe")

    def test_reminder_hours_before_close_valid_and_invalid(self):
        self.assertEqual(parse_setup_reminder_hours_before_close("48"), 48)
        with self.assertRaises(ValueError):
            parse_setup_reminder_hours_before_close("0")
        with self.assertRaises(ValueError):
            parse_setup_reminder_hours_before_close("721")

    def test_backup_interval_days_valid_and_invalid(self):
        self.assertEqual(parse_setup_backup_interval_days("2"), 2)
        with self.assertRaises(ValueError):
            parse_setup_backup_interval_days("0")
        with self.assertRaises(ValueError):
            parse_setup_backup_interval_days("31")

    def test_backup_retention_count_valid_and_invalid(self):
        self.assertEqual(parse_setup_backup_retention_count("15"), 15)
        with self.assertRaises(ValueError):
            parse_setup_backup_retention_count("0")
        with self.assertRaises(ValueError):
            parse_setup_backup_retention_count("101")


class BuildSetupStepHeaderTests(unittest.TestCase):
    def test_shows_position_and_title_for_first_step(self):
        state = SetupWizardState(guild_id=GUILD_ID)
        header = build_setup_step_header(state)
        self.assertIn("Step 1 of 9", header)
        self.assertIn("WASH Crew Role", header)

    def test_shows_position_and_title_for_review_step(self):
        state = SetupWizardState(guild_id=GUILD_ID, current_step=SetupWizardStep.REVIEW)
        header = build_setup_step_header(state)
        self.assertIn("Step 9 of 9", header)
        self.assertIn("Review", header)


class BuildSetupCompletionSummaryTests(unittest.TestCase):
    def test_summary_distinguishes_skipped_destination(self):
        config = GuildConfiguration(guild_id=GUILD_ID, guild_name="Guild")
        config.wash_crew_role_id = WASH_CREW_ROLE_ID
        config.watch_party_role.role_id = WATCH_PARTY_ROLE_ID
        config.watch_party_role.join_mode = JoinMode.MANUAL
        draft = SetupWizardDraft(
            suggestion_database_id=1,
            suggestion_database_name="Movies",
            watch_destination_skipped=True,
            backup_interval_days=1,
            backup_retention_count=30,
        )
        summary = build_setup_completion_summary(config, draft)
        self.assertIn(f"<@&{WASH_CREW_ROLE_ID}>", summary)
        self.assertIn("Skipped (configure later)", summary)
        self.assertIn("Movies", summary)


class SetupCommandFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        temp_path = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(temp_path / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(temp_path / "suggestion_databases.json"),
        )

        class FakeBot:
            pass

        self.bot = FakeBot()
        self.bot.suggestion_service = self.suggestion_service
        self.wizard_repository = SetupWizardRepository(temp_path / "setup_wizard_state.json")
        self.bot.setup_wizard_service = SetupWizardService(
            self.wizard_repository,
            GuildConfigurationRepository(temp_path / "guild_configurations.json"),
            self.suggestion_service,
            SuggestionDatabaseConfigurationRepository(temp_path / "suggestion_database_configurations.json"),
        )
        self.bot.apply_role_configuration = lambda wash, watch: self.applied_roles.append((wash, watch))
        self.applied_roles = []

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    async def test_first_step_is_sent_as_a_new_ephemeral_message(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        self.assertIn("Step 1 of 9", interaction.response.sent_message)
        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIsInstance(interaction.response.sent_view, WashCrewRoleStepView)

    async def test_selecting_the_wash_crew_role_advances_to_watch_party_role_step(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view: WashCrewRoleStepView = interaction.response.sent_view

        class FakeRoleValue:
            id = WASH_CREW_ROLE_ID

        role_select = view.children[0]
        role_select._values = [FakeRoleValue()]
        select_interaction = FakeInteraction()
        await role_select.callback(interaction=select_interaction)

        self.assertIn("Step 2 of 9", select_interaction.response.edited_content)
        self.assertIsInstance(select_interaction.response.edited_view, WatchPartyRoleStepView)

    async def test_admin_channel_step_renders_and_advances_to_suggestion_database(self) -> None:
        from watch_party_manager.domain.setup_wizard import SetupWizardStep
        from watch_party_manager.setup_wizard_view import AdminChannelStepView

        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.ADMIN_CHANNEL)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        self.assertIn("Step 3 of 9", interaction.response.sent_message)
        self.assertIn("Admin Channel", interaction.response.sent_message)
        self.assertIsInstance(interaction.response.sent_view, AdminChannelStepView)

        skip_button = interaction.response.sent_view.children[1]
        skip_interaction = FakeInteraction()
        await skip_button.callback(interaction=skip_interaction)

        self.assertIn("Step 4 of 9", skip_interaction.response.edited_content)

    async def test_voting_defaults_step_sends_a_modal_with_valid_component_labels(self) -> None:
        # Regression test: Step 6 (Voting Defaults) previously crashed with
        # discord.errors.HTTPException 400 ("Must be between 1 and 45 in
        # length") because VotingDefaultsModal's fourth TextInput label
        # was 46 characters. This exercises the exact path that failed --
        # go_to_step -> send_setup_wizard_step -> ModalStepIntroView's
        # button -> on_configure -> interaction.response.send_modal(...)
        # -- and confirms every field on the modal actually sent has a
        # label within Discord's 1-45 character limit.
        from watch_party_manager.domain.setup_wizard import SetupWizardStep
        from watch_party_manager.setup_wizard_view import ModalStepIntroView, VotingDefaultsModal

        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.VOTING_DEFAULTS)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        self.assertIsInstance(interaction.response.sent_view, ModalStepIntroView)
        configure_button = interaction.response.sent_view.children[0]

        configure_interaction = FakeInteraction()
        await configure_button.callback(interaction=configure_interaction)

        sent_modal = configure_interaction.response.sent_modal
        self.assertIsInstance(sent_modal, VotingDefaultsModal)
        for child in sent_modal.children:
            label = getattr(child, "label", None)
            if label is not None:
                self.assertGreaterEqual(len(label), 1)
                self.assertLessEqual(len(label), 45)

    async def test_cancel_deletes_wizard_state_and_edits_the_message(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view = interaction.response.sent_view
        cancel_button = view.children[-1]

        cancel_interaction = FakeInteraction()
        await cancel_button.callback(interaction=cancel_interaction)

        self.assertIn("cancelled", cancel_interaction.response.edited_content)
        self.assertIsNone(cancel_interaction.response.edited_view)
        self.assertIsNone(self.wizard_repository.get(GUILD_ID))

    async def test_review_step_shows_configured_and_incomplete_sections(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.REVIEW)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        self.assertIn(f"WASH Crew Role: Configured (<@&{WASH_CREW_ROLE_ID}>)", interaction.response.sent_message)
        self.assertIn("Suggestion Database: Incomplete", interaction.response.sent_message)
        self.assertIsInstance(interaction.response.sent_view, ReviewStepView)

    async def test_save_with_incomplete_draft_shows_issues_and_returns_to_the_failing_step(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.REVIEW)
        interaction = FakeInteraction(guild=None)
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view: ReviewStepView = interaction.response.sent_view
        save_button = view.children[0]

        class FakeGuild:
            name = "Test Guild"

            def get_role(self, role_id):
                return None

            def get_channel_or_thread(self, channel_id):
                return None

            me = object()

        save_interaction = FakeInteraction(guild=FakeGuild())
        await save_button.callback(interaction=save_interaction)

        self.assertIn("could not be saved", save_interaction.response.edited_content)
        self.assertEqual(self.applied_roles, [])

    async def test_save_with_a_complete_and_valid_draft_applies_role_configuration(self) -> None:
        database_result = self.suggestion_service.create_database("Movies", GUILD_ID, DESTINATION_CHANNEL_ID)
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        state = self.bot.setup_wizard_service.set_watch_party_role(state, WATCH_PARTY_ROLE_ID, JoinMode.MANUAL)
        state, _ = self.bot.setup_wizard_service.select_existing_database(
            state, database_result.database.database_id, guild_id=GUILD_ID
        )
        state = self.bot.setup_wizard_service.skip_watch_destination(state)
        state = self.bot.setup_wizard_service.set_voting_defaults(
            state, 3, 7, GuildVoteVisibility.BLIND, CandidateSelectionMode.BALANCED_RANDOM
        )
        state = self.bot.setup_wizard_service.set_reminder_defaults(state, True, 24)
        state = self.bot.setup_wizard_service.set_backup_defaults(state, 1, 30)

        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view: ReviewStepView = interaction.response.sent_view
        save_button = view.children[0]

        class FakeGuild:
            name = "Test Guild"

            def get_role(self, role_id):
                return FakeRole(role_id)

            def get_channel_or_thread(self, channel_id):
                return None

            me = object()

        save_interaction = FakeInteraction(guild=FakeGuild())
        await save_button.callback(interaction=save_interaction)

        self.assertIn("WASH Setup Complete", save_interaction.response.edited_content)
        self.assertIsNone(save_interaction.response.edited_view)
        self.assertEqual(self.applied_roles, [(WASH_CREW_ROLE_ID, WATCH_PARTY_ROLE_ID)])


if __name__ == "__main__":
    unittest.main()
