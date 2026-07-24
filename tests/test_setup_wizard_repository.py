"""Tests for FR-028's /setup wizard persistence (persistence/setup_wizard_repository.py)."""

import json
import tempfile
import unittest
from pathlib import Path

from watch_party_manager.domain.guild_configuration import GuildVoteVisibility, JoinMode
from watch_party_manager.domain.setup_wizard import (
    SetupWizardDraft,
    SetupWizardState,
    SetupWizardStatus,
    SetupWizardStep,
)
from watch_party_manager.domain.suggestion_database_configuration import CandidateSelectionMode
from watch_party_manager.persistence.setup_wizard_repository import SetupWizardRepository


class SetupWizardRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "nested" / "setup_wizard_state.json"
        self.repo = SetupWizardRepository(self.path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_missing_file_returns_none(self):
        self.assertIsNone(self.repo.get(1))

    def test_save_and_get_round_trips_defaults(self):
        self.repo.save(SetupWizardState(guild_id=1))
        loaded = self.repo.get(1)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.guild_id, 1)
        self.assertEqual(loaded.status, SetupWizardStatus.IN_PROGRESS)
        self.assertEqual(loaded.current_step, SetupWizardStep.WASH_CREW_ROLE)

    def test_save_round_trips_a_fully_populated_draft(self):
        draft = SetupWizardDraft(
            wash_crew_role_id=111,
            watch_party_role_id=222,
            watch_party_join_mode=JoinMode.APPROVAL,
            suggestion_database_id=5,
            suggestion_database_name="Movies",
            suggestion_database_is_new=True,
            admin_channel_id=300,
            admin_channel_skipped=False,
            watch_destination_channel_id=400,
            watch_destination_skipped=False,
            voting_candidate_count=4,
            voting_duration_days=10,
            voting_visibility=GuildVoteVisibility.VISIBLE,
            voting_candidate_selection=CandidateSelectionMode.ROTATION_POOL,
            reminder_enabled=True,
            reminder_hours_before_close=48,
            backup_interval_days=2,
            backup_retention_count=15,
        )
        state = SetupWizardState(
            guild_id=1,
            status=SetupWizardStatus.IN_PROGRESS,
            current_step=SetupWizardStep.REVIEW,
            completed_steps=(SetupWizardStep.WASH_CREW_ROLE, SetupWizardStep.WATCH_PARTY_ROLE),
            draft=draft,
        )
        self.repo.save(state)
        loaded = self.repo.get(1)

        self.assertEqual(loaded.current_step, SetupWizardStep.REVIEW)
        self.assertEqual(
            loaded.completed_steps, (SetupWizardStep.WASH_CREW_ROLE, SetupWizardStep.WATCH_PARTY_ROLE)
        )
        self.assertEqual(loaded.draft.wash_crew_role_id, 111)
        self.assertEqual(loaded.draft.watch_party_join_mode, JoinMode.APPROVAL)
        self.assertEqual(loaded.draft.suggestion_database_name, "Movies")
        self.assertTrue(loaded.draft.suggestion_database_is_new)
        self.assertEqual(loaded.draft.admin_channel_id, 300)
        self.assertFalse(loaded.draft.admin_channel_skipped)
        self.assertEqual(loaded.draft.voting_visibility, GuildVoteVisibility.VISIBLE)
        self.assertEqual(loaded.draft.voting_candidate_selection, CandidateSelectionMode.ROTATION_POOL)
        self.assertTrue(loaded.draft.reminder_enabled)
        self.assertEqual(loaded.draft.reminder_hours_before_close, 48)
        self.assertEqual(loaded.draft.backup_interval_days, 2)
        self.assertEqual(loaded.draft.backup_retention_count, 15)

    def test_admin_channel_skipped_round_trips_independently_of_admin_channel_id(self):
        # Regression test: admin_channel_id/admin_channel_skipped were
        # missing from the repository's serialize/deserialize entirely --
        # a guild that skipped this step (or set it) lost that answer on
        # every reload, defeating Save & Finish Later for this step.
        draft = SetupWizardDraft(admin_channel_skipped=True)
        self.repo.save(SetupWizardState(guild_id=1, draft=draft))
        loaded = self.repo.get(1)
        self.assertTrue(loaded.draft.admin_channel_skipped)
        self.assertIsNone(loaded.draft.admin_channel_id)

    def test_round_trips_a_skipped_and_incomplete_draft(self):
        draft = SetupWizardDraft(watch_destination_skipped=True)
        self.repo.save(SetupWizardState(guild_id=1, draft=draft))
        loaded = self.repo.get(1)
        self.assertTrue(loaded.draft.watch_destination_skipped)
        self.assertIsNone(loaded.draft.watch_destination_channel_id)
        self.assertIsNone(loaded.draft.voting_visibility)
        self.assertIsNone(loaded.draft.watch_party_join_mode)

    def test_multiple_guilds_are_preserved_independently(self):
        self.repo.save(SetupWizardState(guild_id=1))
        self.repo.save(SetupWizardState(guild_id=2, current_step=SetupWizardStep.REVIEW))
        self.assertEqual(self.repo.get(1).current_step, SetupWizardStep.WASH_CREW_ROLE)
        self.assertEqual(self.repo.get(2).current_step, SetupWizardStep.REVIEW)

    def test_save_overwrites_existing_state_for_same_guild(self):
        self.repo.save(SetupWizardState(guild_id=1))
        self.repo.save(SetupWizardState(guild_id=1, current_step=SetupWizardStep.REVIEW))
        self.assertEqual(self.repo.get(1).current_step, SetupWizardStep.REVIEW)

    def test_delete_removes_state_and_reports_whether_it_existed(self):
        self.repo.save(SetupWizardState(guild_id=1))
        self.assertTrue(self.repo.delete(1))
        self.assertIsNone(self.repo.get(1))
        self.assertFalse(self.repo.delete(1))

    def test_malformed_file_fails_closed(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("not json", encoding="utf-8")
        self.assertIsNone(self.repo.get(1))

    def test_atomic_write_leaves_no_temporary_file(self):
        self.repo.save(SetupWizardState(guild_id=1))
        self.assertFalse(self.path.with_suffix(self.path.suffix + ".tmp").exists())

    def test_saved_file_is_valid_json_keyed_by_guild_id_string(self):
        self.repo.save(SetupWizardState(guild_id=42))
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIn("42", data["guilds"])


if __name__ == "__main__":
    unittest.main()
