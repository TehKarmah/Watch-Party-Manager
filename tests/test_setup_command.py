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
    build_setup_preparation_text,
    build_setup_step_header,
    parse_setup_backup_interval_days,
    parse_setup_backup_retention_count,
    parse_setup_reminder_enabled,
    parse_setup_reminder_hours_before_close,
    parse_setup_voting_candidate_count,
    parse_setup_voting_duration_hours,
    parse_setup_voting_visibility,
    perform_setup_permission_check,
    perform_setup_redirect_check,
    resolve_startup_role_ids,
    send_setup_preparation_screen,
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
    SetupPreparationView,
    SetupSaveForLaterButton,
    VotingDefaultsIntroView,
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

    def test_voting_duration_hours_valid_and_invalid(self):
        # A bare number is interpreted as days, backward compatible with
        # this field's original meaning.
        self.assertEqual(parse_setup_voting_duration_hours("10"), 240)
        self.assertEqual(parse_setup_voting_duration_hours("4h"), 4)
        self.assertEqual(parse_setup_voting_duration_hours("3 days"), 72)
        with self.assertRaises(ValueError):
            parse_setup_voting_duration_hours("0")
        with self.assertRaises(ValueError):
            parse_setup_voting_duration_hours("31")  # 31 days = 744 hours, above the 720-hour maximum

    def test_voting_visibility_valid_and_invalid(self):
        self.assertEqual(parse_setup_voting_visibility("Blind"), GuildVoteVisibility.BLIND)
        with self.assertRaises(ValueError):
            parse_setup_voting_visibility("secret")

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
        # Regression test: Voting Defaults previously crashed with
        # discord.errors.HTTPException 400 ("Must be between 1 and 45 in
        # length") because VotingDefaultsModal's fourth TextInput label
        # was 46 characters (candidate selection has since moved to a
        # Discord Select on VotingDefaultsIntroView, dropping that field
        # from the modal entirely). This exercises the current path --
        # go_to_step -> send_setup_wizard_step -> VotingDefaultsIntroView's
        # Set Voting Defaults button -> on_configure ->
        # interaction.response.send_modal(...) -- and confirms every
        # field on the modal actually sent has a label within Discord's
        # 1-45 character limit.
        from watch_party_manager.domain.setup_wizard import SetupWizardStep
        from watch_party_manager.setup_wizard_view import VotingDefaultsIntroView, VotingDefaultsModal

        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.VOTING_DEFAULTS)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        self.assertIsInstance(interaction.response.sent_view, VotingDefaultsIntroView)
        configure_button = interaction.response.sent_view.children[1]

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
        self.assertIn("Collection: Incomplete", interaction.response.sent_message)
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
        intro_view: VotingDefaultsIntroView = interaction.response.sent_view
        self.assertEqual(intro_view.candidate_selection_select.selected, CandidateSelectionMode.SOFT_ROTATION)
        configure_button = intro_view.children[1]

        configure_interaction = FakeInteraction()
        await configure_button.callback(interaction=configure_interaction)

        modal: VotingDefaultsModal = configure_interaction.response.sent_modal
        self.assertEqual(modal.candidate_count_input.default, "5")
        self.assertEqual(modal.duration_input.default, "14 hours")
        self.assertEqual(modal.visibility_input.default, "visible")

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
        self.assertIn("Collection", back_interaction.response.edited_content)

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

    async def test_voting_defaults_dropdown_defaults_to_balanced_random(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.VOTING_DEFAULTS)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        intro_view: VotingDefaultsIntroView = interaction.response.sent_view
        self.assertEqual(intro_view.candidate_selection_select.selected, CandidateSelectionMode.ROTATION_POOL)
        self.assertEqual(CANDIDATE_SELECTION_DISPLAY_LABELS[CandidateSelectionMode.ROTATION_POOL], "Balanced Random")

    async def test_dropdown_displays_all_three_candidate_selection_modes(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.VOTING_DEFAULTS)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        intro_view: VotingDefaultsIntroView = interaction.response.sent_view
        option_values = {option.value for option in intro_view.candidate_selection_select.options}
        self.assertEqual(
            option_values,
            {
                CandidateSelectionMode.ROTATION_POOL.value,
                CandidateSelectionMode.SOFT_ROTATION.value,
                CandidateSelectionMode.INFINITE_POOL.value,
            },
        )

    async def test_pure_random_can_be_selected_via_the_dropdown_and_persists(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.VOTING_DEFAULTS)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        intro_view: VotingDefaultsIntroView = interaction.response.sent_view
        intro_view.candidate_selection_select._values = [CandidateSelectionMode.INFINITE_POOL.value]
        configure_button = intro_view.children[1]

        configure_interaction = FakeInteraction()
        await configure_button.callback(interaction=configure_interaction)
        modal: VotingDefaultsModal = configure_interaction.response.sent_modal
        modal.candidate_count_input._value = "3"
        modal.duration_input._value = "7"
        modal.visibility_input._value = "blind"

        submit_interaction = FakeInteraction()
        await modal.on_submit(interaction=submit_interaction)

        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertEqual(persisted.draft.voting_candidate_selection, CandidateSelectionMode.INFINITE_POOL)

    async def test_candidate_count_default_is_three(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.VOTING_DEFAULTS)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        configure_button = interaction.response.sent_view.children[1]

        configure_interaction = FakeInteraction()
        await configure_button.callback(interaction=configure_interaction)

        modal: VotingDefaultsModal = configure_interaction.response.sent_modal
        self.assertEqual(modal.candidate_count_input.default, "3")

    async def test_candidate_count_validation_still_enforced(self) -> None:
        with self.assertRaises(ValueError):
            parse_setup_voting_candidate_count("1")
        with self.assertRaises(ValueError):
            parse_setup_voting_candidate_count("11")

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

        # Candidate selection is per-database (Manage Databases), not a
        # guild-wide summary line -- read it back the same way /config's
        # Manage Databases screen would, by database_id.
        config_service = ConfigService(
            self.guild_configuration_repository,
            self.suggestion_service,
            self.suggestion_database_configuration_repository,
        )
        database_configuration = config_service.get_database_configuration(
            GUILD_ID, database_result.database.database_id
        )

        self.assertEqual(database_configuration.suggestion_rules.candidate_selection, CandidateSelectionMode.SOFT_ROTATION)

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
        # database's configuration still resolves cleanly.
        config_service = ConfigService(
            self.guild_configuration_repository,
            self.suggestion_service,
            self.suggestion_database_configuration_repository,
        )
        database_configuration = config_service.get_database_configuration(
            GUILD_ID, database_result.database.database_id
        )
        self.assertEqual(database_configuration.suggestion_rules.candidate_selection, CandidateSelectionMode.ROTATION_POOL)


class SetupPreparationScreenIntegrationTests(SetupCommandTestCase):
    """Setup Wizard UX Polish: the preparation screen shown before Step 1."""

    async def test_preparation_screen_explains_the_recommended_roles(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        interaction = FakeInteraction()

        await send_setup_preparation_screen(interaction, self.bot, state)

        self.assertIn("Watch Party", interaction.response.sent_message)
        self.assertIn("WASH Crew", interaction.response.sent_message)
        self.assertIn("Save & Finish Later", interaction.response.sent_message)
        self.assertIsInstance(interaction.response.sent_view, SetupPreparationView)
        self.assertTrue(interaction.response.sent_ephemeral)

    async def test_begin_setup_proceeds_to_step_one(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        interaction = FakeInteraction()
        await send_setup_preparation_screen(interaction, self.bot, state)
        begin_button = interaction.response.sent_view.children[0]

        begin_interaction = FakeInteraction()
        await begin_button.callback(interaction=begin_interaction)

        self.assertIn("Step 1 of 9", begin_interaction.response.edited_content)
        self.assertIsInstance(begin_interaction.response.edited_view, WashCrewRoleStepView)

    async def test_cancel_from_preparation_screen_discards_the_draft(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        interaction = FakeInteraction()
        await send_setup_preparation_screen(interaction, self.bot, state)
        cancel_button = interaction.response.sent_view.children[1]

        cancel_interaction = FakeInteraction()
        await cancel_button.callback(interaction=cancel_interaction)

        self.assertIn("cancelled", cancel_interaction.response.edited_content)
        self.assertIsNone(cancel_interaction.response.edited_view)
        self.assertIsNone(self.wizard_repository.get(GUILD_ID))

    async def test_build_setup_preparation_text_mentions_reusing_existing_channels(self) -> None:
        text = build_setup_preparation_text()
        self.assertIn("existing channels", text)


class WatchPartyRoleWordingTests(SetupCommandTestCase):
    """Setup Wizard UX Polish: the Watch Party role must not be described
    as optional -- it gates every participant command.
    """

    async def test_step_body_does_not_describe_the_role_as_optional(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        self.assertNotIn("(optional)", interaction.response.sent_message)
        self.assertIn("only WASH Crew can use them", interaction.response.sent_message)

    async def test_role_select_placeholder_does_not_say_optional(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        role_select = interaction.response.sent_view.children[0]
        self.assertNotIn("optional", role_select.placeholder)

    async def test_the_role_can_still_technically_be_left_unset(self) -> None:
        # Leaving it unset must remain possible (min_values=0) even though
        # it's no longer described as optional -- WASH Crew can always
        # configure it later via /config.
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_watch_party_role(state, None, JoinMode.SELF_SERVICE)
        self.assertIsNone(state.draft.watch_party_role_id)


class WatchDestinationStepIntegrationTests(SetupCommandTestCase):
    """Setup Wizard UX Polish: Watch Destination wording, navigation
    controls, and the new create-new-thread option.
    """

    class FakeThread:
        def __init__(self, thread_id: int) -> None:
            self.id = thread_id

    class FakeHTTPResponse:
        status = 403
        reason = "Forbidden"

    class FakeParentChannel:
        def __init__(self, *, thread_id: int = 999, fail: bool = False) -> None:
            self._thread_id = thread_id
            self._fail = fail
            self.created_with = None

        async def create_thread(self, *, name, type):
            self.created_with = (name, type)
            if self._fail:
                import discord

                raise discord.HTTPException(
                    response=WatchDestinationStepIntegrationTests.FakeHTTPResponse(), message="boom"
                )
            return WatchDestinationStepIntegrationTests.FakeThread(self._thread_id)

    class FakeGuildWithChannel:
        def __init__(self, channel) -> None:
            self._channel = channel

        def get_channel(self, channel_id):
            return self._channel

    async def _render_step(self):
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.WATCH_DESTINATION)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        return interaction

    async def test_body_explains_history_and_discussion_and_mentions_thread_creation(self) -> None:
        interaction = await self._render_step()
        self.assertIn("history and discussion", interaction.response.sent_message)
        self.assertIn("create a new thread", interaction.response.sent_message)

    async def test_shows_back_and_save_for_later(self) -> None:
        interaction = await self._render_step()
        view = interaction.response.sent_view
        self.assertTrue(any(isinstance(child, SetupBackButton) for child in view.children))
        self.assertTrue(any(isinstance(child, SetupSaveForLaterButton) for child in view.children))

    async def test_selecting_an_existing_channel_or_thread_advances_and_persists(self) -> None:
        interaction = await self._render_step()
        view = interaction.response.sent_view
        channel_select = view.children[0]
        channel_select._values = [type("FakeChannelValue", (), {"id": DESTINATION_CHANNEL_ID})()]

        select_interaction = FakeInteraction()
        await channel_select.callback(interaction=select_interaction)

        self.assertIn("Step 6 of 9", select_interaction.response.edited_content)
        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertEqual(persisted.draft.watch_destination_channel_id, DESTINATION_CHANNEL_ID)
        self.assertFalse(persisted.draft.watch_destination_skipped)

    async def test_create_new_thread_end_to_end_persists_the_new_threads_id(self) -> None:
        interaction = await self._render_step()
        create_thread_button = interaction.response.sent_view.children[1]

        create_interaction = FakeInteraction()
        await create_thread_button.callback(interaction=create_interaction)
        parent_select_view = create_interaction.response.edited_view
        parent_select = parent_select_view.children[0]
        parent_select._values = [type("FakeChannelValue", (), {"id": 500})()]

        parent_interaction = FakeInteraction()
        await parent_select.callback(interaction=parent_interaction)
        name_modal = parent_interaction.response.sent_modal
        name_modal.name_input._value = "Watched Movies"

        fake_channel = self.FakeParentChannel(thread_id=777)
        submit_interaction = FakeInteraction(guild=self.FakeGuildWithChannel(fake_channel))
        await name_modal.on_submit(interaction=submit_interaction)

        self.assertEqual(fake_channel.created_with, ("Watched Movies", __import__("discord").ChannelType.public_thread))
        self.assertIn("Step 6 of 9", submit_interaction.response.edited_content)
        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertEqual(persisted.draft.watch_destination_channel_id, 777)

    async def test_create_new_thread_failure_shows_an_error_and_stays_on_the_step(self) -> None:
        interaction = await self._render_step()
        create_thread_button = interaction.response.sent_view.children[1]

        create_interaction = FakeInteraction()
        await create_thread_button.callback(interaction=create_interaction)
        parent_select = create_interaction.response.edited_view.children[0]
        parent_select._values = [type("FakeChannelValue", (), {"id": 500})()]

        parent_interaction = FakeInteraction()
        await parent_select.callback(interaction=parent_interaction)
        name_modal = parent_interaction.response.sent_modal
        name_modal.name_input._value = "Watched Movies"

        failing_channel = self.FakeParentChannel(fail=True)
        submit_interaction = FakeInteraction(guild=self.FakeGuildWithChannel(failing_channel))
        await name_modal.on_submit(interaction=submit_interaction)

        self.assertIn("Could not create the thread", submit_interaction.response.edited_content)
        self.assertIsInstance(submit_interaction.response.edited_view, WatchDestinationStepView)
        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertIsNone(persisted.draft.watch_destination_channel_id)

    async def test_skip_still_works(self) -> None:
        interaction = await self._render_step()
        skip_button = interaction.response.sent_view.children[2]

        skip_interaction = FakeInteraction()
        await skip_button.callback(interaction=skip_interaction)

        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertTrue(persisted.draft.watch_destination_skipped)


class GuidedCollectionCreationIntegrationTests(SetupCommandTestCase):
    """Contextual Database Resolution: Setup Wizard's guided "what type of
    collection" flow, replacing the old direct "Create a Suggestion
    Database" name prompt. Every option still ends up calling
    SetupWizardService.create_new_database() unchanged.
    """

    async def _reach_collection_type_choice(self):
        from watch_party_manager.setup_wizard_view import CollectionTypeChoiceView, SuggestionDatabaseChoiceView

        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.SUGGESTION_DATABASE)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        self.assertIsInstance(interaction.response.sent_view, SuggestionDatabaseChoiceView)
        create_new_button = interaction.response.sent_view.children[1]

        type_interaction = FakeInteraction()
        await create_new_button.callback(interaction=type_interaction)
        self.assertIsInstance(type_interaction.response.edited_view, CollectionTypeChoiceView)
        return type_interaction.response.edited_view

    async def _reach_channel_select_from_creation_choice(self, creation_choice_view):
        # DestinationCreationChoiceView: [Create New Channel, Use Existing
        # Channel, Use Existing Thread, Cancel]. Tests exercise the
        # existing-channel branch, matching the old single-step flow.
        existing_channel_button = creation_choice_view.children[1]
        self.assertEqual(existing_channel_button.label, "Use Existing Channel")
        existing_interaction = FakeInteraction()
        await existing_channel_button.callback(interaction=existing_interaction)
        return existing_interaction.response.edited_view.children[0]

    async def test_movies_option_creates_a_database_named_movies(self) -> None:
        type_view = await self._reach_collection_type_choice()
        movies_button = type_view.children[0]
        self.assertEqual(movies_button.label, "Movies (Recommended)")

        movies_interaction = FakeInteraction()
        await movies_button.callback(interaction=movies_interaction)
        channel_select = await self._reach_channel_select_from_creation_choice(
            movies_interaction.response.edited_view
        )

        class FakeChannelValue:
            id = DESTINATION_CHANNEL_ID

        channel_select._values = [FakeChannelValue()]
        channel_interaction = FakeInteraction()
        await channel_select.callback(interaction=channel_interaction)

        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertEqual(persisted.draft.suggestion_database_name, "Movies")
        databases = self.suggestion_service.list_databases(GUILD_ID)
        self.assertEqual(databases[0].name, "Movies")

    async def test_tv_shows_option_creates_a_database_named_tv_shows(self) -> None:
        type_view = await self._reach_collection_type_choice()
        tv_shows_button = type_view.children[1]
        self.assertEqual(tv_shows_button.label, "TV Shows")

        tv_interaction = FakeInteraction()
        await tv_shows_button.callback(interaction=tv_interaction)
        channel_select = await self._reach_channel_select_from_creation_choice(
            tv_interaction.response.edited_view
        )

        class FakeChannelValue:
            id = DESTINATION_CHANNEL_ID

        channel_select._values = [FakeChannelValue()]
        channel_interaction = FakeInteraction()
        await channel_select.callback(interaction=channel_interaction)

        databases = self.suggestion_service.list_databases(GUILD_ID)
        self.assertEqual(databases[0].name, "TV Shows")

    async def test_special_collection_option_asks_for_a_name_first(self) -> None:
        from watch_party_manager.setup_wizard_view import CreateDatabaseNameModal

        type_view = await self._reach_collection_type_choice()
        special_button = type_view.children[2]
        self.assertEqual(special_button.label, "Special Collection")

        special_interaction = FakeInteraction()
        await special_button.callback(interaction=special_interaction)

        modal = special_interaction.response.sent_modal
        self.assertIsInstance(modal, CreateDatabaseNameModal)
        modal.name_input._value = "Horror Movies"
        submit_interaction = FakeInteraction()
        await modal.on_submit(interaction=submit_interaction)
        channel_select = await self._reach_channel_select_from_creation_choice(
            submit_interaction.response.edited_view
        )

        class FakeChannelValue:
            id = DESTINATION_CHANNEL_ID

        channel_select._values = [FakeChannelValue()]
        channel_interaction = FakeInteraction()
        await channel_select.callback(interaction=channel_interaction)

        databases = self.suggestion_service.list_databases(GUILD_ID)
        self.assertEqual(databases[0].name, "Horror Movies")

    async def test_custom_option_asks_for_any_name(self) -> None:
        from watch_party_manager.setup_wizard_view import CreateDatabaseNameModal

        type_view = await self._reach_collection_type_choice()
        custom_button = type_view.children[3]
        self.assertEqual(custom_button.label, "Custom")

        custom_interaction = FakeInteraction()
        await custom_button.callback(interaction=custom_interaction)

        modal = custom_interaction.response.sent_modal
        self.assertIsInstance(modal, CreateDatabaseNameModal)
        modal.name_input._value = "Book Club Adaptations"
        submit_interaction = FakeInteraction()
        await modal.on_submit(interaction=submit_interaction)
        channel_select = await self._reach_channel_select_from_creation_choice(
            submit_interaction.response.edited_view
        )

        class FakeChannelValue:
            id = DESTINATION_CHANNEL_ID

        channel_select._values = [FakeChannelValue()]
        channel_interaction = FakeInteraction()
        await channel_select.callback(interaction=channel_interaction)

        databases = self.suggestion_service.list_databases(GUILD_ID)
        self.assertEqual(databases[0].name, "Book Club Adaptations")

    async def test_import_existing_explains_running_import_separately(self) -> None:
        from watch_party_manager.setup_wizard_view import ImportExistingDatabaseNoticeView

        type_view = await self._reach_collection_type_choice()
        import_button = type_view.children[4]
        self.assertEqual(import_button.label, "Import Existing Database")

        import_interaction = FakeInteraction()
        await import_button.callback(interaction=import_interaction)

        self.assertIn("/import", import_interaction.response.edited_content)
        self.assertIsInstance(import_interaction.response.edited_view, ImportExistingDatabaseNoticeView)

    async def test_import_existing_back_button_returns_to_collection_type_choice(self) -> None:
        from watch_party_manager.setup_wizard_view import CollectionTypeChoiceView

        type_view = await self._reach_collection_type_choice()
        import_button = type_view.children[4]
        import_interaction = FakeInteraction()
        await import_button.callback(interaction=import_interaction)
        back_button = next(c for c in import_interaction.response.edited_view.children if getattr(c, "label", None) == "Back")

        back_interaction = FakeInteraction()
        await back_button.callback(interaction=back_interaction)

        self.assertIsInstance(back_interaction.response.edited_view, CollectionTypeChoiceView)

    async def test_creating_a_second_database_on_an_already_used_channel_is_rejected(self) -> None:
        # Conflict Prevention: a channel/thread can never route to two
        # databases at once, even when the second one is created through
        # the guided flow during setup.
        self.suggestion_service.create_database("Movies", GUILD_ID, DESTINATION_CHANNEL_ID)
        type_view = await self._reach_collection_type_choice()
        tv_shows_button = type_view.children[1]

        tv_interaction = FakeInteraction()
        await tv_shows_button.callback(interaction=tv_interaction)
        channel_select = await self._reach_channel_select_from_creation_choice(
            tv_interaction.response.edited_view
        )

        class FakeChannelValue:
            id = DESTINATION_CHANNEL_ID

        channel_select._values = [FakeChannelValue()]
        channel_interaction = FakeInteraction()
        await channel_select.callback(interaction=channel_interaction)

        # create_new_database's failure re-renders the current step with
        # a clear error rather than silently advancing.
        self.assertIn("already has a suggestion database", channel_interaction.response.edited_content)
        databases = self.suggestion_service.list_databases(GUILD_ID)
        self.assertEqual(len(databases), 1)
        self.assertEqual(databases[0].name, "Movies")


class SuggestionDestinationCreationIntegrationTests(SetupCommandTestCase):
    """Contextual Collection Model Refinement: after choosing a collection
    type, administrators choose HOW the one required suggestion
    destination is obtained -- create a new channel (with a suggested or
    custom name), or pick an existing channel/thread -- never forced into
    a default.
    """

    class FakeChannel:
        def __init__(self, channel_id: int) -> None:
            self.id = channel_id

    class FakeHTTPResponse:
        status = 403
        reason = "Forbidden"

    class FakeGuild:
        def __init__(self, *, channel_id: int = 888, fail: bool = False) -> None:
            self._channel_id = channel_id
            self._fail = fail
            self.created_with = None

        async def create_text_channel(self, *, name):
            self.created_with = name
            if self._fail:
                import discord

                raise discord.HTTPException(
                    response=SuggestionDestinationCreationIntegrationTests.FakeHTTPResponse(), message="boom"
                )
            return SuggestionDestinationCreationIntegrationTests.FakeChannel(self._channel_id)

    async def _reach_creation_choice(self):
        from watch_party_manager.setup_wizard_view import CollectionTypeChoiceView, SuggestionDatabaseChoiceView

        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.SUGGESTION_DATABASE)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        self.assertIsInstance(interaction.response.sent_view, SuggestionDatabaseChoiceView)
        create_new_button = interaction.response.sent_view.children[1]

        type_interaction = FakeInteraction()
        await create_new_button.callback(interaction=type_interaction)
        self.assertIsInstance(type_interaction.response.edited_view, CollectionTypeChoiceView)

        tv_shows_button = type_interaction.response.edited_view.children[1]
        tv_interaction = FakeInteraction()
        await tv_shows_button.callback(interaction=tv_interaction)
        return tv_interaction.response.edited_view

    async def test_creation_choice_offers_create_existing_channel_and_existing_thread(self) -> None:
        from watch_party_manager.setup_wizard_view import DestinationCreationChoiceView

        creation_choice_view = await self._reach_creation_choice()
        self.assertIsInstance(creation_choice_view, DestinationCreationChoiceView)
        labels = [getattr(c, "label", None) for c in creation_choice_view.children]
        self.assertIn("Create New Channel (Recommended)", labels)
        self.assertIn("Use Existing Channel", labels)
        self.assertIn("Use Existing Thread", labels)

    async def test_use_existing_thread_offers_a_thread_only_picker(self) -> None:
        import discord

        creation_choice_view = await self._reach_creation_choice()
        use_thread_button = creation_choice_view.children[2]
        self.assertEqual(use_thread_button.label, "Use Existing Thread")

        thread_interaction = FakeInteraction()
        await use_thread_button.callback(interaction=thread_interaction)
        thread_select = thread_interaction.response.edited_view.children[0]
        self.assertEqual(
            set(thread_select.channel_types),
            {discord.ChannelType.public_thread, discord.ChannelType.private_thread},
        )

    async def test_create_new_channel_offers_suggested_names_and_custom_name(self) -> None:
        from watch_party_manager.setup_wizard_view import (
            CUSTOM_DESTINATION_NAME_VALUE,
            DestinationNameChoiceView,
        )

        creation_choice_view = await self._reach_creation_choice()
        create_button = creation_choice_view.children[0]
        self.assertEqual(create_button.label, "Create New Channel (Recommended)")

        create_interaction = FakeInteraction()
        await create_button.callback(interaction=create_interaction)
        self.assertIsInstance(create_interaction.response.edited_view, DestinationNameChoiceView)
        name_select = create_interaction.response.edited_view.children[0]
        values = [option.value for option in name_select.options]
        self.assertIn("TV Suggestions", values)
        self.assertIn("Movie Suggestions", values)
        self.assertIn("Halloween", values)
        self.assertIn(CUSTOM_DESTINATION_NAME_VALUE, values)

    async def test_choosing_a_suggested_name_creates_the_channel_and_the_collection(self) -> None:
        creation_choice_view = await self._reach_creation_choice()
        create_button = creation_choice_view.children[0]
        create_interaction = FakeInteraction()
        await create_button.callback(interaction=create_interaction)
        name_select = create_interaction.response.edited_view.children[0]

        fake_guild = self.FakeGuild(channel_id=4242)
        name_select._values = ["TV Suggestions"]
        name_interaction = FakeInteraction(guild=fake_guild)
        await name_select.callback(interaction=name_interaction)

        self.assertEqual(fake_guild.created_with, "TV Suggestions")
        databases = self.suggestion_service.list_databases(GUILD_ID)
        self.assertEqual(databases[0].name, "TV Shows")
        self.assertEqual(databases[0].channel_id, 4242)

    async def test_choosing_custom_name_asks_for_a_name_then_creates_the_channel(self) -> None:
        from watch_party_manager.setup_wizard_view import CUSTOM_DESTINATION_NAME_VALUE, CreateDatabaseNameModal

        creation_choice_view = await self._reach_creation_choice()
        create_button = creation_choice_view.children[0]
        create_interaction = FakeInteraction()
        await create_button.callback(interaction=create_interaction)
        name_select = create_interaction.response.edited_view.children[0]

        name_select._values = [CUSTOM_DESTINATION_NAME_VALUE]
        custom_interaction = FakeInteraction()
        await name_select.callback(interaction=custom_interaction)
        modal = custom_interaction.response.sent_modal
        self.assertIsInstance(modal, CreateDatabaseNameModal)

        fake_guild = self.FakeGuild(channel_id=5150)
        modal.name_input._value = "Season Premieres"
        submit_interaction = FakeInteraction(guild=fake_guild)
        await modal.on_submit(interaction=submit_interaction)

        self.assertEqual(fake_guild.created_with, "Season Premieres")
        databases = self.suggestion_service.list_databases(GUILD_ID)
        self.assertEqual(databases[0].channel_id, 5150)

    async def test_channel_creation_failure_shows_an_error_and_stays_recoverable(self) -> None:
        from watch_party_manager.setup_wizard_view import DestinationCreationChoiceView

        creation_choice_view = await self._reach_creation_choice()
        create_button = creation_choice_view.children[0]
        create_interaction = FakeInteraction()
        await create_button.callback(interaction=create_interaction)
        name_select = create_interaction.response.edited_view.children[0]

        fake_guild = self.FakeGuild(fail=True)
        name_select._values = ["Halloween"]
        name_interaction = FakeInteraction(guild=fake_guild)
        await name_select.callback(interaction=name_interaction)

        self.assertIn("Could not create the channel", name_interaction.response.edited_content)
        self.assertIsInstance(name_interaction.response.edited_view, DestinationCreationChoiceView)
        databases = self.suggestion_service.list_databases(GUILD_ID)
        self.assertEqual(databases, [])


if __name__ == "__main__":
    unittest.main()
