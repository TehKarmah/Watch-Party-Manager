"""Tests for FR-028's /setup wizard service (services/setup_wizard_service.py).

Covers the full testing checklist from the FR-028 request: wizard flow
(complete/cancelled/resumed/restarted), each step in isolation, validation,
and completion (including atomicity and resumability guarantees).
"""

import tempfile
import unittest
from pathlib import Path

from watch_party_manager.domain.guild_configuration import GuildVoteVisibility, JoinMode
from watch_party_manager.domain.setup_wizard import SETUP_WIZARD_STEP_ORDER, SetupWizardStatus, SetupWizardStep
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

GUILD_ID = 100
OTHER_GUILD_ID = 200
WASH_CREW_ROLE_ID = 111
WATCH_PARTY_ROLE_ID = 222
DESTINATION_CHANNEL_ID = 400


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakePermissions:
    def __init__(self, view_channel: bool = True, send_messages: bool = True) -> None:
        self.view_channel = view_channel
        self.send_messages = send_messages


class FakeChannel:
    def __init__(self, channel_id: int, *, permissions: FakePermissions = None) -> None:
        self.id = channel_id
        self._permissions = permissions or FakePermissions()

    def permissions_for(self, member) -> FakePermissions:
        return self._permissions


class FakeGuild:
    """A minimal stand-in for the GuildLookup protocol setup_wizard_service.validate() needs."""

    def __init__(self, *, role_ids=(), channel_ids=(), channel_permissions=None) -> None:
        self._role_ids = set(role_ids)
        self._channels = {
            channel_id: FakeChannel(channel_id, permissions=(channel_permissions or {}).get(channel_id))
            for channel_id in channel_ids
        }
        self.me = object()

    def get_role(self, role_id):
        return FakeRole(role_id) if role_id in self._role_ids else None

    def get_channel_or_thread(self, channel_id):
        return self._channels.get(channel_id)


class SetupWizardServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        temp_path = Path(self._temp_dir.name)

        self.wizard_repository = SetupWizardRepository(temp_path / "setup_wizard_state.json")
        self.guild_configuration_repository = GuildConfigurationRepository(
            temp_path / "guild_configurations.json"
        )
        self.suggestion_database_configuration_repository = SuggestionDatabaseConfigurationRepository(
            temp_path / "suggestion_database_configurations.json"
        )
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(temp_path / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(temp_path / "suggestion_databases.json"),
        )
        self.service = SetupWizardService(
            self.wizard_repository,
            self.guild_configuration_repository,
            self.suggestion_service,
            self.suggestion_database_configuration_repository,
        )

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _create_database(self, guild_id=GUILD_ID, channel_id=DESTINATION_CHANNEL_ID, name="Movies"):
        result = self.suggestion_service.create_database(name, guild_id, channel_id)
        self.assertTrue(result.success, result.message)
        return result.database

    def _full_guild(self, *, extra_channel_ids=()) -> FakeGuild:
        return FakeGuild(
            role_ids={WASH_CREW_ROLE_ID, WATCH_PARTY_ROLE_ID},
            channel_ids={DESTINATION_CHANNEL_ID, *extra_channel_ids},
        )


class WizardFlowTests(SetupWizardServiceTestCase):
    def test_start_or_resume_creates_a_fresh_state(self):
        state, resumed = self.service.start_or_resume(GUILD_ID)
        self.assertFalse(resumed)
        self.assertEqual(state.current_step, SetupWizardStep.WASH_CREW_ROLE)
        self.assertEqual(self.wizard_repository.get(GUILD_ID).guild_id, GUILD_ID)

    def test_start_or_resume_returns_existing_in_progress_state(self):
        first, _ = self.service.start_or_resume(GUILD_ID)
        updated = self.service.set_wash_crew_role(first, WASH_CREW_ROLE_ID)

        resumed_state, resumed = self.service.start_or_resume(GUILD_ID)
        self.assertTrue(resumed)
        self.assertEqual(resumed_state.draft.wash_crew_role_id, WASH_CREW_ROLE_ID)
        self.assertEqual(resumed_state.current_step, updated.current_step)

    def test_restart_discards_existing_draft(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        self.service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)

        restarted = self.service.restart(GUILD_ID)
        self.assertEqual(restarted.current_step, SetupWizardStep.WASH_CREW_ROLE)
        self.assertIsNone(restarted.draft.wash_crew_role_id)
        self.assertEqual(restarted.completed_steps, ())

    def test_cancel_discards_in_progress_state(self):
        self.service.start_or_resume(GUILD_ID)
        self.assertTrue(self.service.cancel(GUILD_ID))
        self.assertIsNone(self.wizard_repository.get(GUILD_ID))

    def test_cancel_reports_false_when_nothing_to_cancel(self):
        self.assertFalse(self.service.cancel(GUILD_ID))

    def test_go_to_step_jumps_directly_without_altering_the_draft(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        state = self.service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        jumped = self.service.go_to_step(state, SetupWizardStep.WASH_CREW_ROLE)
        self.assertEqual(jumped.current_step, SetupWizardStep.WASH_CREW_ROLE)
        self.assertEqual(jumped.draft.wash_crew_role_id, WASH_CREW_ROLE_ID)

    def test_complete_setup_end_to_end(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        state = self.service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        state = self.service.set_watch_party_role(state, WATCH_PARTY_ROLE_ID, JoinMode.MANUAL)
        state, _ = self.service.create_new_database(state, "Movies", DESTINATION_CHANNEL_ID, guild_id=GUILD_ID)
        state = self.service.set_watch_destination(state, DESTINATION_CHANNEL_ID)
        state = self.service.set_voting_defaults(
            state, 4, 10, GuildVoteVisibility.VISIBLE, CandidateSelectionMode.ROTATION_POOL
        )
        state = self.service.set_reminder_defaults(state, True, 48)
        state = self.service.set_backup_defaults(state, 2, 15)
        self.assertEqual(state.current_step, SetupWizardStep.REVIEW)

        guild = self._full_guild()
        result = self.service.finalize(state, GUILD_ID, "Test Guild", guild)

        self.assertTrue(result.success)
        self.assertTrue(result.configuration.setup_completed)
        self.assertIsNone(self.wizard_repository.get(GUILD_ID))


class BackNavigationServiceTests(SetupWizardServiceTestCase):
    """Setup Wizard Polish Batch 1, Section 1: previous_step/go_back."""

    def test_previous_step_is_none_at_the_first_step(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        self.assertIsNone(self.service.previous_step(state))

    def test_previous_step_returns_the_step_before_in_walkthrough_order(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        state = self.service.go_to_step(state, SetupWizardStep.WATCH_DESTINATION)
        self.assertEqual(self.service.previous_step(state), SetupWizardStep.SUGGESTION_DATABASE)

    def test_previous_step_for_review_is_backup_defaults(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        state = self.service.go_to_step(state, SetupWizardStep.REVIEW)
        self.assertEqual(self.service.previous_step(state), SetupWizardStep.BACKUP_DEFAULTS)

    def test_go_back_is_a_no_op_at_the_first_step(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        result = self.service.go_back(state)
        self.assertEqual(result.current_step, SetupWizardStep.WASH_CREW_ROLE)
        self.assertEqual(result, state)

    def test_go_back_moves_to_the_previous_step(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        state = self.service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        self.assertEqual(state.current_step, SetupWizardStep.WATCH_PARTY_ROLE)

        back = self.service.go_back(state)

        self.assertEqual(back.current_step, SetupWizardStep.WASH_CREW_ROLE)

    def test_go_back_never_touches_the_draft_or_completed_steps(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        state = self.service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)

        back = self.service.go_back(state)

        self.assertEqual(back.draft, state.draft)
        self.assertEqual(back.completed_steps, state.completed_steps)

    def test_go_back_persists_the_new_current_step(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        state = self.service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)

        self.service.go_back(state)

        self.assertEqual(self.wizard_repository.get(GUILD_ID).current_step, SetupWizardStep.WASH_CREW_ROLE)

    def test_go_back_repeatedly_walks_all_the_way_to_the_first_step(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        state = self.service.go_to_step(state, SetupWizardStep.REVIEW)

        for _ in range(len(SETUP_WIZARD_STEP_ORDER)):
            state = self.service.go_back(state)

        self.assertEqual(state.current_step, SetupWizardStep.WASH_CREW_ROLE)


class SaveForLaterServiceTests(SetupWizardServiceTestCase):
    """Setup Wizard Polish Batch 1, Section 2: save_for_later."""

    def test_save_for_later_keeps_status_in_progress(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        state = self.service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)

        saved = self.service.save_for_later(state)

        self.assertEqual(saved.status, SetupWizardStatus.IN_PROGRESS)

    def test_save_for_later_does_not_change_the_current_step_or_draft(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        state = self.service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)

        saved = self.service.save_for_later(state)

        self.assertEqual(saved.current_step, state.current_step)
        self.assertEqual(saved.draft, state.draft)

    def test_save_for_later_persists_to_the_repository(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        state = self.service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)

        self.service.save_for_later(state)

        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted.draft.wash_crew_role_id, WASH_CREW_ROLE_ID)

    def test_save_for_later_refreshes_updated_at(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        saved = self.service.save_for_later(state)
        self.assertGreaterEqual(saved.updated_at, state.updated_at)


class WashCrewRoleStepTests(SetupWizardServiceTestCase):
    def test_selecting_a_role_advances_and_completes_the_step(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        updated = self.service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        self.assertEqual(updated.draft.wash_crew_role_id, WASH_CREW_ROLE_ID)
        self.assertEqual(updated.current_step, SetupWizardStep.WATCH_PARTY_ROLE)
        self.assertIn(SetupWizardStep.WASH_CREW_ROLE, updated.completed_steps)

    def test_missing_role_id_is_incomplete_on_review(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        lines = self.service.build_review_lines(state)
        self.assertIn("WASH Crew Role: Incomplete", lines)

    def test_invalid_role_fails_validation(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        state = self.service.set_wash_crew_role(state, 999999)
        guild = FakeGuild(role_ids=set(), channel_ids={DESTINATION_CHANNEL_ID})
        issues = self.service.validate(state, guild)
        self.assertTrue(any(issue.step == SetupWizardStep.WASH_CREW_ROLE for issue in issues))


class WatchPartyRoleStepTests(SetupWizardServiceTestCase):
    def test_selecting_a_role_and_join_mode_persists_both(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        updated = self.service.set_watch_party_role(state, WATCH_PARTY_ROLE_ID, JoinMode.APPROVAL)
        self.assertEqual(updated.draft.watch_party_role_id, WATCH_PARTY_ROLE_ID)
        self.assertEqual(updated.draft.watch_party_join_mode, JoinMode.APPROVAL)
        self.assertEqual(updated.current_step, SetupWizardStep.ADMIN_CHANNEL)

    def test_role_is_optional(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        updated = self.service.set_watch_party_role(state, None, JoinMode.SELF_SERVICE)
        self.assertIsNone(updated.draft.watch_party_role_id)
        lines = self.service.build_review_lines(updated)
        self.assertIn("Watch Party Role: Incomplete", lines)

    def test_invalid_role_fails_validation(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        state = self.service.set_watch_party_role(state, 999999, JoinMode.MANUAL)
        guild = FakeGuild(role_ids=set(), channel_ids={DESTINATION_CHANNEL_ID})
        issues = self.service.validate(state, guild)
        self.assertTrue(any(issue.step == SetupWizardStep.WATCH_PARTY_ROLE for issue in issues))


class SuggestionDatabaseStepTests(SetupWizardServiceTestCase):
    def test_selecting_an_existing_database_advances(self):
        database = self._create_database()
        state, _ = self.service.start_or_resume(GUILD_ID)
        updated, message = self.service.select_existing_database(state, database.database_id, guild_id=GUILD_ID)
        self.assertEqual(updated.draft.suggestion_database_id, database.database_id)
        self.assertFalse(updated.draft.suggestion_database_is_new)
        self.assertEqual(updated.current_step, SetupWizardStep.WATCH_DESTINATION)

    def test_creating_a_new_database_advances_and_marks_it_new(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        updated, message = self.service.create_new_database(
            state, "Movies", DESTINATION_CHANNEL_ID, guild_id=GUILD_ID
        )
        self.assertTrue(updated.draft.suggestion_database_is_new)
        self.assertEqual(updated.draft.suggestion_database_name, "Movies")
        self.assertEqual(updated.current_step, SetupWizardStep.WATCH_DESTINATION)

    def test_creating_a_database_that_fails_does_not_advance(self):
        self._create_database(name="Movies")
        state, _ = self.service.start_or_resume(GUILD_ID)
        updated, message = self.service.create_new_database(
            state, "Movies", DESTINATION_CHANNEL_ID + 1, guild_id=GUILD_ID
        )
        self.assertEqual(updated.current_step, state.current_step)
        self.assertIsNone(updated.draft.suggestion_database_id)
        self.assertIn("already exists", message)

    def test_selecting_an_inactive_database_reactivates_it(self):
        database = self._create_database()
        self.suggestion_service.deactivate_database(database.database_id, GUILD_ID)
        self.assertFalse(self.suggestion_service.get_database(database.database_id).active)

        state, _ = self.service.start_or_resume(GUILD_ID)
        self.service.select_existing_database(state, database.database_id, guild_id=GUILD_ID)

        self.assertTrue(self.suggestion_service.get_database(database.database_id).active)

    def test_selecting_a_database_from_another_guild_fails(self):
        database = self._create_database(guild_id=OTHER_GUILD_ID)
        state, _ = self.service.start_or_resume(GUILD_ID)
        updated, message = self.service.select_existing_database(state, database.database_id, guild_id=GUILD_ID)
        self.assertIsNone(updated.draft.suggestion_database_id)
        self.assertIn("doesn't exist", message)

    def test_missing_database_fails_validation(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        guild = self._full_guild()
        issues = self.service.validate(state, guild)
        self.assertTrue(
            any(
                issue.step == SetupWizardStep.SUGGESTION_DATABASE and "No suggestion database" in issue.message
                for issue in issues
            )
        )


class AdminChannelStepTests(SetupWizardServiceTestCase):
    def test_selecting_a_channel_advances(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        updated = self.service.set_admin_channel(state, DESTINATION_CHANNEL_ID)
        self.assertEqual(updated.draft.admin_channel_id, DESTINATION_CHANNEL_ID)
        self.assertFalse(updated.draft.admin_channel_skipped)
        self.assertEqual(updated.current_step, SetupWizardStep.SUGGESTION_DATABASE)

    def test_skipping_advances_and_marks_skipped(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        updated = self.service.skip_admin_channel(state)
        self.assertTrue(updated.draft.admin_channel_skipped)
        self.assertIsNone(updated.draft.admin_channel_id)
        self.assertEqual(updated.current_step, SetupWizardStep.SUGGESTION_DATABASE)

    def test_review_line_reflects_configured_skipped_and_incomplete_states(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        self.assertIn("Admin Channel: Incomplete", self.service.build_review_lines(state))

        skipped = self.service.skip_admin_channel(state)
        self.assertIn("Admin Channel: Skipped", self.service.build_review_lines(skipped))

        configured = self.service.set_admin_channel(state, DESTINATION_CHANNEL_ID)
        self.assertIn(
            f"Admin Channel: Configured (<#{DESTINATION_CHANNEL_ID}>)",
            self.service.build_review_lines(configured),
        )

    def test_invalid_channel_fails_validation(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        state = self.service.set_admin_channel(state, 555)
        guild = FakeGuild(role_ids=set(), channel_ids=set())
        issues = self.service.validate(state, guild)
        self.assertTrue(any(issue.step == SetupWizardStep.ADMIN_CHANNEL for issue in issues))

    def test_skipped_admin_channel_is_not_a_validation_failure(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        state = self.service.skip_admin_channel(state)
        guild = self._full_guild()
        issues = self.service.validate(state, guild)
        self.assertFalse(any(issue.step == SetupWizardStep.ADMIN_CHANNEL for issue in issues))

    def test_configured_admin_channel_is_persisted_to_guild_configuration(self):
        database = self._create_database()
        state, _ = self.service.start_or_resume(GUILD_ID)
        state = self.service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        state = self.service.set_watch_party_role(state, WATCH_PARTY_ROLE_ID, JoinMode.SELF_SERVICE)
        state = self.service.set_admin_channel(state, DESTINATION_CHANNEL_ID)
        state, _ = self.service.select_existing_database(state, database.database_id, guild_id=GUILD_ID)
        state = self.service.set_watch_destination(state, DESTINATION_CHANNEL_ID)
        state = self.service.set_voting_defaults(
            state, 3, 7, GuildVoteVisibility.BLIND, CandidateSelectionMode.SOFT_ROTATION
        )
        state = self.service.set_reminder_defaults(state, True, 24)
        state = self.service.set_backup_defaults(state, 1, 30)

        guild = self._full_guild()
        result = self.service.finalize(state, GUILD_ID, "Test Guild", guild)

        self.assertTrue(result.success)
        self.assertEqual(result.configuration.channels.admin_channel_id, DESTINATION_CHANNEL_ID)


class WatchDestinationStepTests(SetupWizardServiceTestCase):
    def test_selecting_a_channel_advances(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        updated = self.service.set_watch_destination(state, DESTINATION_CHANNEL_ID)
        self.assertEqual(updated.draft.watch_destination_channel_id, DESTINATION_CHANNEL_ID)
        self.assertFalse(updated.draft.watch_destination_skipped)
        self.assertEqual(updated.current_step, SetupWizardStep.VOTING_DEFAULTS)

    def test_selecting_a_thread_id_advances_the_same_way_as_a_channel(self):
        thread_id = 987654321
        state, _ = self.service.start_or_resume(GUILD_ID)
        updated = self.service.set_watch_destination(state, thread_id)
        self.assertEqual(updated.draft.watch_destination_channel_id, thread_id)

    def test_skipping_advances_and_marks_skipped(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        updated = self.service.skip_watch_destination(state)
        self.assertTrue(updated.draft.watch_destination_skipped)
        self.assertIsNone(updated.draft.watch_destination_channel_id)
        self.assertEqual(updated.current_step, SetupWizardStep.VOTING_DEFAULTS)

    def test_invalid_channel_fails_validation(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        state = self.service.set_watch_destination(state, 555)
        guild = FakeGuild(role_ids=set(), channel_ids=set())
        issues = self.service.validate(state, guild)
        self.assertTrue(any(issue.step == SetupWizardStep.WATCH_DESTINATION for issue in issues))

    def test_insufficient_permissions_fails_validation(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        state = self.service.set_watch_destination(state, DESTINATION_CHANNEL_ID)
        guild = FakeGuild(
            role_ids=set(),
            channel_ids={DESTINATION_CHANNEL_ID},
            channel_permissions={DESTINATION_CHANNEL_ID: FakePermissions(send_messages=False)},
        )
        issues = self.service.validate(state, guild)
        self.assertTrue(
            any(
                issue.step == SetupWizardStep.WATCH_DESTINATION and "permission" in issue.message
                for issue in issues
            )
        )


class VotingDefaultsStepTests(SetupWizardServiceTestCase):
    def test_defaults_are_saved_and_restored(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        updated = self.service.set_voting_defaults(
            state, 5, 14, GuildVoteVisibility.BLIND, CandidateSelectionMode.SOFT_ROTATION
        )
        self.assertEqual(updated.draft.voting_candidate_count, 5)
        self.assertEqual(updated.draft.voting_duration_days, 14)
        self.assertEqual(updated.draft.voting_visibility, GuildVoteVisibility.BLIND)
        self.assertEqual(updated.draft.voting_candidate_selection, CandidateSelectionMode.SOFT_ROTATION)
        self.assertEqual(updated.current_step, SetupWizardStep.REMINDER_DEFAULTS)

        reloaded = self.wizard_repository.get(GUILD_ID)
        self.assertEqual(reloaded.draft.voting_candidate_count, 5)


class ReminderDefaultsStepTests(SetupWizardServiceTestCase):
    def test_enabled_reminder_is_saved(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        updated = self.service.set_reminder_defaults(state, True, 24)
        self.assertTrue(updated.draft.reminder_enabled)
        self.assertEqual(updated.draft.reminder_hours_before_close, 24)
        self.assertEqual(updated.current_step, SetupWizardStep.BACKUP_DEFAULTS)

    def test_disabled_reminder_is_saved(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        updated = self.service.set_reminder_defaults(state, False, 24)
        self.assertFalse(updated.draft.reminder_enabled)

    def test_timing_is_saved(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        updated = self.service.set_reminder_defaults(state, True, 72)
        self.assertEqual(updated.draft.reminder_hours_before_close, 72)


class BackupDefaultsStepTests(SetupWizardServiceTestCase):
    def test_interval_is_saved(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        updated = self.service.set_backup_defaults(state, 3, 20)
        self.assertEqual(updated.draft.backup_interval_days, 3)
        self.assertEqual(updated.current_step, SetupWizardStep.REVIEW)

    def test_retention_is_saved(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        updated = self.service.set_backup_defaults(state, 3, 20)
        self.assertEqual(updated.draft.backup_retention_count, 20)


class ValidationTests(SetupWizardServiceTestCase):
    def test_valid_draft_produces_no_issues(self):
        database = self._create_database()
        state, _ = self.service.start_or_resume(GUILD_ID)
        state = self.service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        state = self.service.set_watch_party_role(state, WATCH_PARTY_ROLE_ID, JoinMode.SELF_SERVICE)
        state, _ = self.service.select_existing_database(state, database.database_id, guild_id=GUILD_ID)
        state = self.service.set_watch_destination(state, DESTINATION_CHANNEL_ID)

        guild = self._full_guild()
        self.assertEqual(self.service.validate(state, guild), [])

    def test_incomplete_optional_sections_are_not_validation_failures(self):
        database = self._create_database()
        state, _ = self.service.start_or_resume(GUILD_ID)
        state, _ = self.service.select_existing_database(state, database.database_id, guild_id=GUILD_ID)
        state = self.service.skip_watch_destination(state)

        guild = self._full_guild()
        self.assertEqual(self.service.validate(state, guild), [])

    def test_setup_cannot_finalize_while_validation_fails(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        state = self.service.set_wash_crew_role(state, 999999)
        guild = self._full_guild()
        result = self.service.finalize(state, GUILD_ID, "Test Guild", guild)
        self.assertFalse(result.success)
        self.assertTrue(result.issues)


class CompletionTests(SetupWizardServiceTestCase):
    def _complete_state(self):
        database = self._create_database()
        state, _ = self.service.start_or_resume(GUILD_ID)
        state = self.service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        state = self.service.set_watch_party_role(state, WATCH_PARTY_ROLE_ID, JoinMode.MANUAL)
        state, _ = self.service.select_existing_database(state, database.database_id, guild_id=GUILD_ID)
        state = self.service.set_watch_destination(state, DESTINATION_CHANNEL_ID)
        state = self.service.set_voting_defaults(
            state, 4, 10, GuildVoteVisibility.VISIBLE, CandidateSelectionMode.ROTATION_POOL
        )
        state = self.service.set_reminder_defaults(state, True, 24)
        state = self.service.set_backup_defaults(state, 1, 30)
        return state

    def test_configuration_is_saved_on_success(self):
        state = self._complete_state()
        guild = self._full_guild()
        result = self.service.finalize(state, GUILD_ID, "Test Guild", guild)

        self.assertTrue(result.success)
        saved = self.guild_configuration_repository.get(GUILD_ID)
        self.assertEqual(saved.wash_crew_role_id, WASH_CREW_ROLE_ID)
        self.assertEqual(saved.watch_party_role.role_id, WATCH_PARTY_ROLE_ID)
        self.assertEqual(saved.watch_party_role.join_mode, JoinMode.MANUAL)
        self.assertEqual(saved.voting_defaults.candidate_count, 4)
        self.assertTrue(saved.notifications.vote.vote_ending_reminder)
        self.assertEqual(
            saved.backup.extra_fields["automatic_backup_interval_days"], 1
        )
        self.assertEqual(saved.backup.extra_fields["backup_retention_count"], 30)

    def test_skipped_items_are_preserved_in_the_review_summary(self):
        database = self._create_database()
        state, _ = self.service.start_or_resume(GUILD_ID)
        state = self.service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        state = self.service.set_watch_party_role(state, WATCH_PARTY_ROLE_ID, JoinMode.MANUAL)
        state, _ = self.service.select_existing_database(state, database.database_id, guild_id=GUILD_ID)
        state = self.service.skip_watch_destination(state)

        lines = self.service.build_review_lines(state)
        self.assertIn("Watched Movie Destination: Skipped", lines)

    def test_partial_configuration_is_not_persisted_after_validation_failure(self):
        state = self._complete_state()
        broken = self.service.set_wash_crew_role(state, 999999)
        broken = self.service.go_to_step(broken, SetupWizardStep.REVIEW)
        guild = self._full_guild()

        result = self.service.finalize(broken, GUILD_ID, "Test Guild", guild)

        self.assertFalse(result.success)
        self.assertIsNone(self.guild_configuration_repository.get(GUILD_ID))
        self.assertIsNotNone(self.wizard_repository.get(GUILD_ID))

    def test_resumable_progress_is_restored_correctly(self):
        state, _ = self.service.start_or_resume(GUILD_ID)
        self.service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)

        resumed_state, resumed = self.service.start_or_resume(GUILD_ID)
        self.assertTrue(resumed)
        self.assertEqual(resumed_state.draft.wash_crew_role_id, WASH_CREW_ROLE_ID)
        self.assertEqual(resumed_state.current_step, SetupWizardStep.WATCH_PARTY_ROLE)

    def test_finalize_deletes_wizard_state_so_setup_is_no_longer_resumable(self):
        state = self._complete_state()
        guild = self._full_guild()
        self.service.finalize(state, GUILD_ID, "Test Guild", guild)
        self.assertIsNone(self.wizard_repository.get(GUILD_ID))


if __name__ == "__main__":
    unittest.main()
