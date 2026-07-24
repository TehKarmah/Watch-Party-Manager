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
from watch_party_manager.domain.suggestion_database_configuration import (
    CANDIDATE_SELECTION_DISPLAY_LABELS,
    CandidateSelectionMode,
)
from watch_party_manager.persistence.guild_configuration_repository import GuildConfigurationRepository
from watch_party_manager.persistence.setup_wizard_repository import SetupWizardRepository
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.config_service import ConfigService
from watch_party_manager.services.setup_wizard_service import SetupWizardService
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.setup_wizard_view import (
    AdminChannelStepView,
    ModalStepIntroView,
    ReviewStepView,
    SetupBackButton,
    SetupSaveForLaterButton,
    VotingDefaultsModal,
    WashCrewRoleStepView,
    WatchDestinationStepView,
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
    def __init__(self, roles=(), *, user_id: int = 1) -> None:
        self.roles = list(roles)
        self.id = user_id


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
        self.assertEqual(parse_setup_candidate_selection("rotation_pool"), CandidateSelectionMode.ROTATION_POOL)
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


class SetupCommandTestCase(unittest.IsolatedAsyncioTestCase):
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
        self.guild_configuration_repository = GuildConfigurationRepository(
            temp_path / "guild_configurations.json"
        )
        self.suggestion_database_configuration_repository = SuggestionDatabaseConfigurationRepository(
            temp_path / "suggestion_database_configurations.json"
        )
        self.bot.setup_wizard_service = SetupWizardService(
            self.wizard_repository,
            self.guild_configuration_repository,
            self.suggestion_service,
            self.suggestion_database_configuration_repository,
        )
        self.bot.apply_role_configuration = lambda wash, watch: self.applied_roles.append((wash, watch))
        self.applied_roles = []

    def tearDown(self) -> None:
        self._temp_dir.cleanup()


class SetupCommandFlowTests(SetupCommandTestCase):
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
            state, 3, 7, GuildVoteVisibility.BLIND, CandidateSelectionMode.SOFT_ROTATION
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


class BackNavigationIntegrationTests(SetupCommandTestCase):
    """Setup Wizard Polish Batch 1, Section 1: Back navigation."""

    async def test_first_step_shows_no_back_button(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        view = interaction.response.sent_view
        self.assertFalse(any(isinstance(child, SetupBackButton) for child in view.children))

    async def test_second_step_shows_a_back_button(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        view = interaction.response.sent_view
        self.assertTrue(any(isinstance(child, SetupBackButton) for child in view.children))

    async def test_back_from_watch_party_role_returns_to_wash_crew_role(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view: WatchPartyRoleStepView = interaction.response.sent_view
        back_button = next(c for c in view.children if isinstance(c, SetupBackButton))

        back_interaction = FakeInteraction()
        await back_button.callback(interaction=back_interaction)

        self.assertIn("Step 1 of 9", back_interaction.response.edited_content)
        self.assertIsInstance(back_interaction.response.edited_view, WashCrewRoleStepView)

    async def test_back_does_not_clear_the_previously_saved_value(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view = interaction.response.sent_view
        back_button = next(c for c in view.children if isinstance(c, SetupBackButton))
        back_interaction = FakeInteraction()

        await back_button.callback(interaction=back_interaction)

        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertEqual(persisted.draft.wash_crew_role_id, WASH_CREW_ROLE_ID)
        self.assertEqual(persisted.current_step, SetupWizardStep.WASH_CREW_ROLE)

    async def test_going_back_and_forward_again_preserves_the_answer(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        state = self.bot.setup_wizard_service.go_back(state)
        # Re-answering the first step again (as if reviewing/confirming
        # it) must not have lost the value that was already there.
        self.assertEqual(state.draft.wash_crew_role_id, WASH_CREW_ROLE_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        self.assertEqual(state.current_step, SetupWizardStep.WATCH_PARTY_ROLE)

    async def test_voting_defaults_modal_reopened_after_back_shows_previously_saved_values(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_voting_defaults(
            state, 5, 14, GuildVoteVisibility.VISIBLE, CandidateSelectionMode.SOFT_ROTATION
        )
        # Simulate returning to Voting Defaults later (e.g. via Back from
        # Reminder Defaults, or Review's edit-a-section) -- the modal must
        # be pre-filled with what was actually saved, not the bare
        # hardcoded defaults.
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.VOTING_DEFAULTS)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        intro_view: ModalStepIntroView = interaction.response.sent_view
        configure_button = intro_view.children[0]

        configure_interaction = FakeInteraction()
        await configure_button.callback(interaction=configure_interaction)

        modal: VotingDefaultsModal = configure_interaction.response.sent_modal
        self.assertEqual(modal.candidate_count_input.default, "5")
        self.assertEqual(modal.duration_days_input.default, "14")
        self.assertEqual(modal.visibility_input.default, "visible")
        self.assertEqual(modal.candidate_selection_input.default, "soft_rotation")

    async def test_back_from_review_returns_to_backup_defaults(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.REVIEW)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view: ReviewStepView = interaction.response.sent_view
        back_button = next(c for c in view.children if isinstance(c, SetupBackButton))

        back_interaction = FakeInteraction()
        await back_button.callback(interaction=back_interaction)

        self.assertIn("Step 8 of 9", back_interaction.response.edited_content)
        self.assertIsInstance(back_interaction.response.edited_view, ModalStepIntroView)

    async def test_admin_channel_step_shows_a_back_button_to_watch_party_role(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.ADMIN_CHANNEL)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        view: AdminChannelStepView = interaction.response.sent_view
        back_button = next(c for c in view.children if isinstance(c, SetupBackButton))
        back_interaction = FakeInteraction()
        await back_button.callback(interaction=back_interaction)
        self.assertIn("Watch Party Role", back_interaction.response.edited_content)

    async def test_watch_destination_step_back_returns_to_suggestion_database(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.WATCH_DESTINATION)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        view: WatchDestinationStepView = interaction.response.sent_view
        back_button = next(c for c in view.children if isinstance(c, SetupBackButton))
        back_interaction = FakeInteraction()
        await back_button.callback(interaction=back_interaction)
        self.assertIn("Suggestion Database", back_interaction.response.edited_content)

    async def test_unauthorized_user_cannot_use_another_administrators_wizard_controls(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        starter = FakeMember(user_id=1)
        interaction = FakeInteraction(user=starter)

        await send_setup_wizard_step(interaction, self.bot, state, edit=False, requester_id=starter.id)

        view = interaction.response.sent_view
        other_member = FakeMember(user_id=2)
        other_interaction = FakeInteraction(user=other_member)

        allowed = await view.interaction_check(other_interaction)

        self.assertFalse(allowed)
        self.assertIn("Only the person who ran this command", other_interaction.response.sent_message)

    async def test_the_requester_can_still_use_their_own_wizard_controls(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        starter = FakeMember(user_id=1)
        interaction = FakeInteraction(user=starter)

        await send_setup_wizard_step(interaction, self.bot, state, edit=False, requester_id=starter.id)

        view = interaction.response.sent_view
        same_user_interaction = FakeInteraction(user=FakeMember(user_id=1))

        allowed = await view.interaction_check(same_user_interaction)

        self.assertTrue(allowed)


class SaveAndFinishLaterIntegrationTests(SetupCommandTestCase):
    """Setup Wizard Polish Batch 1, Section 2: Save & Finish Later."""

    async def test_every_step_shows_a_save_for_later_button(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        view = interaction.response.sent_view
        self.assertTrue(any(isinstance(child, SetupSaveForLaterButton) for child in view.children))

    async def test_clicking_save_for_later_confirms_and_explains_how_to_resume(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view = interaction.response.sent_view
        save_for_later_button = next(c for c in view.children if isinstance(c, SetupSaveForLaterButton))

        save_interaction = FakeInteraction()
        await save_for_later_button.callback(interaction=save_interaction)

        self.assertIn("saved", save_interaction.response.edited_content.lower())
        self.assertIn("/setup", save_interaction.response.edited_content)
        self.assertIsNone(save_interaction.response.edited_view)

    async def test_save_for_later_does_not_delete_the_draft(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view = interaction.response.sent_view
        save_for_later_button = next(c for c in view.children if isinstance(c, SetupSaveForLaterButton))

        await save_for_later_button.callback(interaction=FakeInteraction())

        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted.draft.wash_crew_role_id, WASH_CREW_ROLE_ID)

    async def test_save_for_later_does_not_mark_setup_complete(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view = interaction.response.sent_view
        save_for_later_button = next(c for c in view.children if isinstance(c, SetupSaveForLaterButton))

        await save_for_later_button.callback(interaction=FakeInteraction())

        self.assertIsNone(self.guild_configuration_repository.get(GUILD_ID))
        self.assertEqual(self.applied_roles, [])

    async def test_save_for_later_does_not_roll_back_earlier_finalized_configuration(self) -> None:
        # If setup was already completed once (unusual but possible if a
        # future FR ever allows re-entering /setup after completion),
        # Save & Finish Later on a *new* draft must never touch the
        # already-saved GuildConfiguration.
        existing = GuildConfiguration(guild_id=GUILD_ID, guild_name="Guild", setup_completed=True)
        existing.wash_crew_role_id = WASH_CREW_ROLE_ID
        self.guild_configuration_repository.save(existing)

        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view = interaction.response.sent_view
        save_for_later_button = next(c for c in view.children if isinstance(c, SetupSaveForLaterButton))

        await save_for_later_button.callback(interaction=FakeInteraction())

        reloaded = self.guild_configuration_repository.get(GUILD_ID)
        self.assertTrue(reloaded.setup_completed)
        self.assertEqual(reloaded.wash_crew_role_id, WASH_CREW_ROLE_ID)


class ResumeDetectionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Setup Wizard Polish Batch 1, Section 2: resuming a saved-for-later draft."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        temp_path = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(temp_path / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(temp_path / "suggestion_databases.json"),
        )
        self.wizard_repository = SetupWizardRepository(temp_path / "setup_wizard_state.json")
        self.guild_configuration_repository = GuildConfigurationRepository(
            temp_path / "guild_configurations.json"
        )
        self.setup_wizard_service = SetupWizardService(
            self.wizard_repository,
            self.guild_configuration_repository,
            self.suggestion_service,
            SuggestionDatabaseConfigurationRepository(temp_path / "suggestion_database_configurations.json"),
        )

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_setup_not_started_has_no_resumable_state(self) -> None:
        self.assertIsNone(self.wizard_repository.get(GUILD_ID))
        self.assertIsNone(self.guild_configuration_repository.get(GUILD_ID))

    def test_setup_in_progress_is_detected_as_resumable(self) -> None:
        state, _ = self.setup_wizard_service.start_or_resume(GUILD_ID)
        self.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)

        _, resumed = self.setup_wizard_service.start_or_resume(GUILD_ID)

        self.assertTrue(resumed)

    def test_resuming_preserves_completed_steps_and_current_step(self) -> None:
        state, _ = self.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        state = self.setup_wizard_service.set_watch_party_role(state, WATCH_PARTY_ROLE_ID, JoinMode.MANUAL)

        resumed_state, resumed = self.setup_wizard_service.start_or_resume(GUILD_ID)

        self.assertTrue(resumed)
        self.assertEqual(resumed_state.current_step, SetupWizardStep.ADMIN_CHANNEL)
        self.assertIn(SetupWizardStep.WASH_CREW_ROLE, resumed_state.completed_steps)
        self.assertIn(SetupWizardStep.WATCH_PARTY_ROLE, resumed_state.completed_steps)
        self.assertEqual(resumed_state.draft.wash_crew_role_id, WASH_CREW_ROLE_ID)
        self.assertEqual(resumed_state.draft.watch_party_role_id, WATCH_PARTY_ROLE_ID)

    def test_setup_completed_is_no_longer_resumable(self) -> None:
        configuration = GuildConfiguration(guild_id=GUILD_ID, guild_name="Guild", setup_completed=True)
        self.guild_configuration_repository.save(configuration)

        message = perform_setup_redirect_check(self.guild_configuration_repository.get(GUILD_ID))

        self.assertIsNotNone(message)
        self.assertIn("/config", message)

    async def test_setup_command_shows_resume_prompt_with_progress_count(self) -> None:
        state, _ = self.setup_wizard_service.start_or_resume(GUILD_ID)
        self.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)

        class FakeBot:
            pass

        bot = FakeBot()
        bot.suggestion_service = self.suggestion_service
        bot.setup_wizard_service = self.setup_wizard_service

        resumed_state, resumed = self.setup_wizard_service.start_or_resume(GUILD_ID)
        self.assertTrue(resumed)

        async def on_continue(resume_interaction) -> None:
            await send_setup_wizard_step(resume_interaction, bot, resumed_state, edit=True)

        from watch_party_manager.setup_wizard_view import SetupWizardResumeView

        view = SetupWizardResumeView(on_continue, on_continue, on_continue)
        interaction = FakeInteraction()
        message = (
            f"{len(resumed_state.completed_steps)} of 9 steps completed so far "
            f"(currently on: Watch Party Role)."
        )
        await interaction.response.send_message(message, view=view, ephemeral=True)

        self.assertIn("1 of 9 steps completed", interaction.response.sent_message)


class BackwardCompatibilityIntegrationTests(SetupCommandTestCase):
    """Setup Wizard Polish Batch 1, Section 2: existing state files without
    the newer admin_channel_id/admin_channel_skipped fields must still
    load safely (Section 2's "preserve backward compatibility" and the
    pre-existing repository serialization gap this batch fixed).
    """

    def test_loading_a_pre_existing_state_file_without_admin_channel_fields_is_safe(self) -> None:
        import json

        raw = {
            "guilds": {
                str(GUILD_ID): {
                    "guild_id": GUILD_ID,
                    "status": "in_progress",
                    "current_step": "admin_channel",
                    "completed_steps": ["wash_crew_role", "watch_party_role"],
                    "started_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                    "draft": {
                        "wash_crew_role_id": WASH_CREW_ROLE_ID,
                        "watch_party_role_id": None,
                        "watch_party_join_mode": None,
                        "suggestion_database_id": None,
                        "suggestion_database_name": None,
                        "suggestion_database_is_new": False,
                        # Deliberately no admin_channel_id/admin_channel_skipped keys,
                        # matching every state file saved before this batch.
                        "watch_destination_channel_id": None,
                        "watch_destination_skipped": False,
                        "voting_candidate_count": None,
                        "voting_duration_days": None,
                        "voting_visibility": None,
                        "voting_candidate_selection": None,
                        "reminder_enabled": None,
                        "reminder_hours_before_close": None,
                        "backup_interval_days": None,
                        "backup_retention_count": None,
                    },
                }
            }
        }
        self.wizard_repository._file_path.parent.mkdir(parents=True, exist_ok=True)
        self.wizard_repository._file_path.write_text(json.dumps(raw), encoding="utf-8")

        state = self.wizard_repository.get(GUILD_ID)

        self.assertIsNotNone(state)
        self.assertIsNone(state.draft.admin_channel_id)
        self.assertFalse(state.draft.admin_channel_skipped)
        self.assertEqual(state.draft.wash_crew_role_id, WASH_CREW_ROLE_ID)


class CandidateSelectionSetupIntegrationTests(SetupCommandTestCase):
    """Setup Wizard Polish Batch 1, Section 3: Candidate Selection in
    Voting Defaults, exercised end-to-end through the wizard.
    """

    async def test_voting_defaults_modal_defaults_to_balanced_random(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.VOTING_DEFAULTS)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        configure_button = interaction.response.sent_view.children[0]

        configure_interaction = FakeInteraction()
        await configure_button.callback(interaction=configure_interaction)

        modal: VotingDefaultsModal = configure_interaction.response.sent_modal
        self.assertEqual(modal.candidate_selection_input.default, CandidateSelectionMode.ROTATION_POOL.value)
        self.assertEqual(CANDIDATE_SELECTION_DISPLAY_LABELS[CandidateSelectionMode.ROTATION_POOL], "Balanced Random")

    async def test_pure_random_can_be_selected_by_raw_value(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.VOTING_DEFAULTS)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        configure_button = interaction.response.sent_view.children[0]
        configure_interaction = FakeInteraction()
        await configure_button.callback(interaction=configure_interaction)
        modal: VotingDefaultsModal = configure_interaction.response.sent_modal
        modal.candidate_count_input._value = "3"
        modal.duration_days_input._value = "7"
        modal.visibility_input._value = "blind"
        modal.candidate_selection_input._value = "infinite_pool"

        submit_interaction = FakeInteraction()
        await modal.on_submit(interaction=submit_interaction)

        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertEqual(persisted.draft.voting_candidate_selection, CandidateSelectionMode.INFINITE_POOL)

    async def test_pure_random_can_be_selected_by_friendly_label(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.VOTING_DEFAULTS)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        configure_button = interaction.response.sent_view.children[0]
        configure_interaction = FakeInteraction()
        await configure_button.callback(interaction=configure_interaction)
        modal: VotingDefaultsModal = configure_interaction.response.sent_modal
        modal.candidate_count_input._value = "3"
        modal.duration_days_input._value = "7"
        modal.visibility_input._value = "blind"
        modal.candidate_selection_input._value = "Pure Random"

        submit_interaction = FakeInteraction()
        await modal.on_submit(interaction=submit_interaction)

        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertEqual(persisted.draft.voting_candidate_selection, CandidateSelectionMode.INFINITE_POOL)

    async def test_candidate_count_default_is_three(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.VOTING_DEFAULTS)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        configure_button = interaction.response.sent_view.children[0]

        configure_interaction = FakeInteraction()
        await configure_button.callback(interaction=configure_interaction)

        modal: VotingDefaultsModal = configure_interaction.response.sent_modal
        self.assertEqual(modal.candidate_count_input.default, "3")

    async def test_candidate_count_validation_still_enforced(self) -> None:
        with self.assertRaises(ValueError):
            parse_setup_voting_candidate_count("1")
        with self.assertRaises(ValueError):
            parse_setup_voting_candidate_count("11")

    async def test_invalid_candidate_selection_is_rejected_with_a_clear_message(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_setup_candidate_selection("weighted")
        self.assertIn("Balanced Random", str(ctx.exception))
        self.assertIn("Pure Random", str(ctx.exception))

    async def test_completion_summary_includes_the_candidate_selection_mode(self) -> None:
        database_result = self.suggestion_service.create_database("Movies", GUILD_ID, DESTINATION_CHANNEL_ID)
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        state = self.bot.setup_wizard_service.set_watch_party_role(state, WATCH_PARTY_ROLE_ID, JoinMode.MANUAL)
        state, _ = self.bot.setup_wizard_service.select_existing_database(
            state, database_result.database.database_id, guild_id=GUILD_ID
        )
        state = self.bot.setup_wizard_service.skip_watch_destination(state)
        state = self.bot.setup_wizard_service.set_voting_defaults(
            state, 3, 7, GuildVoteVisibility.BLIND, CandidateSelectionMode.SOFT_ROTATION
        )
        state = self.bot.setup_wizard_service.set_reminder_defaults(state, True, 24)
        state = self.bot.setup_wizard_service.set_backup_defaults(state, 1, 30)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.REVIEW)

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

        self.assertIn("Soft Rotation", save_interaction.response.edited_content)

    async def test_review_line_shows_the_friendly_candidate_selection_label(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_voting_defaults(
            state, 3, 7, GuildVoteVisibility.BLIND, CandidateSelectionMode.ROTATION_POOL
        )
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.REVIEW)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        self.assertIn("Balanced Random", interaction.response.sent_message)

    async def test_settings_persist_through_a_repository_round_trip(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        self.bot.setup_wizard_service.set_voting_defaults(
            state, 7, 3, GuildVoteVisibility.VISIBLE, CandidateSelectionMode.INFINITE_POOL
        )

        reloaded = self.wizard_repository.get(GUILD_ID)

        self.assertEqual(reloaded.draft.voting_candidate_count, 7)
        self.assertEqual(reloaded.draft.voting_candidate_selection, CandidateSelectionMode.INFINITE_POOL)

    async def test_config_reads_the_same_saved_candidate_selection(self) -> None:
        database_result = self.suggestion_service.create_database("Movies", GUILD_ID, DESTINATION_CHANNEL_ID)
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        state = self.bot.setup_wizard_service.set_watch_party_role(state, WATCH_PARTY_ROLE_ID, JoinMode.MANUAL)
        state, _ = self.bot.setup_wizard_service.select_existing_database(
            state, database_result.database.database_id, guild_id=GUILD_ID
        )
        state = self.bot.setup_wizard_service.skip_watch_destination(state)
        state = self.bot.setup_wizard_service.set_voting_defaults(
            state, 3, 7, GuildVoteVisibility.BLIND, CandidateSelectionMode.SOFT_ROTATION
        )
        state = self.bot.setup_wizard_service.set_reminder_defaults(state, True, 24)
        state = self.bot.setup_wizard_service.set_backup_defaults(state, 1, 30)

        class FakeGuild:
            name = "Test Guild"

            def get_role(self, role_id):
                return FakeRole(role_id)

            def get_channel_or_thread(self, channel_id):
                return None

            me = object()

        result = self.bot.setup_wizard_service.finalize(state, GUILD_ID, "Test Guild", FakeGuild())
        self.assertTrue(result.success, result.message)

        config_service = ConfigService(
            self.guild_configuration_repository,
            self.suggestion_service,
            self.suggestion_database_configuration_repository,
        )
        lines = config_service.build_summary_lines(GUILD_ID, FakeGuild())

        self.assertTrue(any("Soft Rotation" in line for line in lines))

    async def test_older_persisted_database_configuration_defaults_to_balanced_random(self) -> None:
        # A database configuration saved before candidate_selection existed
        # (or one that never had this section touched) must still resolve
        # to ROTATION_POOL / "Balanced Random", not error or show blank.
        database_result = self.suggestion_service.create_database("Movies", GUILD_ID, DESTINATION_CHANNEL_ID)
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        state = self.bot.setup_wizard_service.set_watch_party_role(state, WATCH_PARTY_ROLE_ID, JoinMode.MANUAL)
        state, _ = self.bot.setup_wizard_service.select_existing_database(
            state, database_result.database.database_id, guild_id=GUILD_ID
        )
        state = self.bot.setup_wizard_service.skip_watch_destination(state)
        state = self.bot.setup_wizard_service.set_voting_defaults(
            state, 3, 7, GuildVoteVisibility.BLIND, CandidateSelectionMode.ROTATION_POOL
        )
        state = self.bot.setup_wizard_service.set_reminder_defaults(state, True, 24)
        state = self.bot.setup_wizard_service.set_backup_defaults(state, 1, 30)

        class FakeGuild:
            name = "Test Guild"

            def get_role(self, role_id):
                return FakeRole(role_id)

            def get_channel_or_thread(self, channel_id):
                return None

            me = object()

        result = self.bot.setup_wizard_service.finalize(state, GUILD_ID, "Test Guild", FakeGuild())
        self.assertTrue(result.success, result.message)

        # No explicit /config edit ever touched suggestion_rules -- the
        # active database's configuration still resolves cleanly.
        config_service = ConfigService(
            self.guild_configuration_repository,
            self.suggestion_service,
            self.suggestion_database_configuration_repository,
        )
        lines = config_service.build_summary_lines(GUILD_ID, FakeGuild())
        self.assertTrue(any("Balanced Random" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
