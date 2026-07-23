"""Tests for RotationService (FR-033B rotation lifecycle, admission, progress)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from watch_party_manager.domain.rotation import RotationStatus
from watch_party_manager.domain.suggestion_database_configuration import SuggestionAdmissionMode
from watch_party_manager.domain.watch_item import WatchItem, WatchItemStatus
from watch_party_manager.persistence.rotation_repository import JsonRotationRepository
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.rotation_service import RotationService
from watch_party_manager.services.suggestion_service import SuggestionService

DATABASE_ID = 1


class RotationServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.rotation_repository = JsonRotationRepository(root / "rotations.json")
        self.rotation_service = RotationService(self.suggestion_service, repository=self.rotation_repository)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _add(self, title: str) -> WatchItem:
        result = self.suggestion_service.suggest(title, database_id=DATABASE_ID, guild_id=100)
        self.assertTrue(result.success)
        return result.watch_item


class RotationCreationTests(RotationServiceTestCase):
    def test_get_open_rotation_returns_none_before_any_rotation_exists(self) -> None:
        self.assertIsNone(self.rotation_service.get_open_rotation(DATABASE_ID))

    def test_get_or_start_rotation_bootstraps_the_first_rotation(self) -> None:
        self._add("Alien")
        self._add("The Matrix")

        rotation = self.rotation_service.get_or_start_rotation(DATABASE_ID)

        self.assertEqual(rotation.status, RotationStatus.OPEN)
        self.assertEqual(len(rotation.assigned_suggestion_ids), 2)

    def test_get_or_start_rotation_returns_the_same_open_rotation_on_repeat_calls(self) -> None:
        self._add("Alien")

        first = self.rotation_service.get_or_start_rotation(DATABASE_ID)
        second = self.rotation_service.get_or_start_rotation(DATABASE_ID)

        self.assertEqual(first.id, second.id)

    def test_a_fresh_database_yields_an_empty_rotation(self) -> None:
        rotation = self.rotation_service.get_or_start_rotation(DATABASE_ID)

        self.assertEqual(rotation.assigned_suggestion_ids, ())

    def test_different_databases_get_independent_rotations(self) -> None:
        self._add("Alien")

        rotation_a = self.rotation_service.get_or_start_rotation(DATABASE_ID)
        rotation_b = self.rotation_service.get_or_start_rotation(2)

        self.assertNotEqual(rotation_a.id, rotation_b.id)
        self.assertEqual(rotation_b.assigned_suggestion_ids, ())


class PresentationTrackingTests(RotationServiceTestCase):
    def test_record_presentation_appends_the_rotation_id_to_the_items_journey(self) -> None:
        item = self._add("Alien")
        rotation = self.rotation_service.get_or_start_rotation(DATABASE_ID)

        self.rotation_service.record_presentation(DATABASE_ID, [item.id])

        refreshed = self.suggestion_service.get_suggestion(item.id)
        self.assertIn(rotation.id, refreshed.journey.rotation_history)

    def test_presented_items_are_excluded_from_remaining_suggestions(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        self.rotation_service.get_or_start_rotation(DATABASE_ID)

        self.rotation_service.record_presentation(DATABASE_ID, [item_a.id])

        remaining_ids = {item.id for item in self.rotation_service.remaining_suggestions(DATABASE_ID)}
        self.assertEqual(remaining_ids, {item_b.id})


class RotationCompletionTests(RotationServiceTestCase):
    def test_completion_via_presentation_of_every_assigned_item(self) -> None:
        item = self._add("Alien")
        rotation = self.rotation_service.get_or_start_rotation(DATABASE_ID)
        self.rotation_service.record_presentation(DATABASE_ID, [item.id])

        self.assertTrue(self.rotation_service._is_exhausted(rotation))

    def test_completion_via_watched_status(self) -> None:
        item = self._add("Alien")
        rotation = self.rotation_service.get_or_start_rotation(DATABASE_ID)
        item.status = WatchItemStatus.WATCHED

        self.assertTrue(self.rotation_service._is_exhausted(rotation))

    def test_completion_via_retirement(self) -> None:
        item = self._add("Alien")
        rotation = self.rotation_service.get_or_start_rotation(DATABASE_ID)
        self.suggestion_service.reject_suggestion(item.id, 1, rejection_threshold=1)

        self.assertTrue(self.rotation_service._is_exhausted(rotation))

    def test_completion_via_administrative_archive(self) -> None:
        item = self._add("Alien")
        rotation = self.rotation_service.get_or_start_rotation(DATABASE_ID)
        self.suggestion_service.archive_suggestion(item.id)

        self.assertTrue(self.rotation_service._is_exhausted(rotation))

    def test_completion_via_removal(self) -> None:
        item = self._add("Alien")
        rotation = self.rotation_service.get_or_start_rotation(DATABASE_ID)
        self.suggestion_service.remove_suggestion_by_id(item.id)

        self.assertTrue(self.rotation_service._is_exhausted(rotation))

    def test_not_exhausted_while_any_item_is_still_pending(self) -> None:
        item_a = self._add("Alien")
        self._add("The Matrix")
        rotation = self.rotation_service.get_or_start_rotation(DATABASE_ID)
        self.rotation_service.record_presentation(DATABASE_ID, [item_a.id])

        self.assertFalse(self.rotation_service._is_exhausted(rotation))

    def test_an_empty_rotation_is_never_reported_exhausted(self) -> None:
        rotation = self.rotation_service.get_or_start_rotation(DATABASE_ID)

        self.assertFalse(self.rotation_service._is_exhausted(rotation))


class AutomaticFreshRotationTests(RotationServiceTestCase):
    def test_exhaustion_triggers_a_fresh_rotation_on_next_selection(self) -> None:
        item = self._add("Alien")
        first = self.rotation_service.get_or_start_rotation(DATABASE_ID)
        self.rotation_service.record_presentation(DATABASE_ID, [item.id])

        second = self.rotation_service.current_rotation_for_selection(DATABASE_ID)

        self.assertNotEqual(first.id, second.id)
        self.assertEqual(second.status, RotationStatus.OPEN)

    def test_the_outgoing_rotation_is_marked_completed(self) -> None:
        item = self._add("Alien")
        first = self.rotation_service.get_or_start_rotation(DATABASE_ID)
        self.rotation_service.record_presentation(DATABASE_ID, [item.id])

        self.rotation_service.current_rotation_for_selection(DATABASE_ID)

        completed_first = self.rotation_service._rotations[first.id]
        self.assertEqual(completed_first.status, RotationStatus.COMPLETED)
        self.assertIsNotNone(completed_first.completed_at)

    def test_a_fresh_rotation_re_includes_a_previously_presented_item(self) -> None:
        item = self._add("Alien")
        self.rotation_service.get_or_start_rotation(DATABASE_ID)
        self.rotation_service.record_presentation(DATABASE_ID, [item.id])

        fresh = self.rotation_service.current_rotation_for_selection(DATABASE_ID)

        self.assertIn(item.id, fresh.assigned_suggestion_ids)

    def test_no_transition_while_the_rotation_still_has_pending_items(self) -> None:
        item_a = self._add("Alien")
        self._add("The Matrix")
        first = self.rotation_service.get_or_start_rotation(DATABASE_ID)
        self.rotation_service.record_presentation(DATABASE_ID, [item_a.id])

        current = self.rotation_service.current_rotation_for_selection(DATABASE_ID)

        self.assertEqual(first.id, current.id)


class AdmissionModeTests(RotationServiceTestCase):
    def test_next_rotation_admission_is_a_no_op_when_a_rotation_is_open(self) -> None:
        self._add("Alien")
        rotation = self.rotation_service.get_or_start_rotation(DATABASE_ID)
        new_item = self._add("The Matrix")

        self.rotation_service.admit_suggestion(DATABASE_ID, new_item.id, SuggestionAdmissionMode.NEXT_ROTATION)

        refreshed = self.rotation_service.get_open_rotation(DATABASE_ID)
        self.assertNotIn(new_item.id, refreshed.assigned_suggestion_ids)
        self.assertEqual(refreshed.id, rotation.id)

    def test_next_rotation_admitted_items_join_the_following_fresh_rotation(self) -> None:
        item = self._add("Alien")
        self.rotation_service.get_or_start_rotation(DATABASE_ID)
        new_item = self._add("The Matrix")
        self.rotation_service.admit_suggestion(DATABASE_ID, new_item.id, SuggestionAdmissionMode.NEXT_ROTATION)
        self.rotation_service.record_presentation(DATABASE_ID, [item.id])

        fresh = self.rotation_service.current_rotation_for_selection(DATABASE_ID)

        self.assertIn(new_item.id, fresh.assigned_suggestion_ids)

    def test_join_current_rotation_admission_expands_the_open_rotation_immediately(self) -> None:
        self._add("Alien")
        self.rotation_service.get_or_start_rotation(DATABASE_ID)
        new_item = self._add("The Matrix")

        self.rotation_service.admit_suggestion(
            DATABASE_ID, new_item.id, SuggestionAdmissionMode.JOIN_CURRENT_ROTATION
        )

        refreshed = self.rotation_service.get_open_rotation(DATABASE_ID)
        self.assertIn(new_item.id, refreshed.assigned_suggestion_ids)

    def test_join_current_rotation_bootstraps_a_rotation_if_none_exists_yet(self) -> None:
        item = self._add("Alien")

        self.rotation_service.admit_suggestion(DATABASE_ID, item.id, SuggestionAdmissionMode.JOIN_CURRENT_ROTATION)

        rotation = self.rotation_service.get_open_rotation(DATABASE_ID)
        self.assertIsNotNone(rotation)
        self.assertIn(item.id, rotation.assigned_suggestion_ids)


class RotationProgressTests(RotationServiceTestCase):
    def test_progress_of_a_fresh_rotation(self) -> None:
        self._add("Alien")
        self._add("The Matrix")

        progress = self.rotation_service.rotation_progress(DATABASE_ID)

        self.assertEqual(progress.total, 2)
        self.assertEqual(progress.presented, 0)
        self.assertEqual(progress.remaining, 2)
        self.assertEqual(progress.retired, 0)
        self.assertEqual(progress.watched, 0)
        self.assertEqual(progress.completion_percentage, 0.0)

    def test_progress_reflects_presented_items(self) -> None:
        item_a = self._add("Alien")
        self._add("The Matrix")
        self.rotation_service.get_or_start_rotation(DATABASE_ID)
        self.rotation_service.record_presentation(DATABASE_ID, [item_a.id])

        progress = self.rotation_service.rotation_progress(DATABASE_ID)

        self.assertEqual(progress.presented, 1)
        self.assertEqual(progress.remaining, 1)
        self.assertEqual(progress.completion_percentage, 50.0)

    def test_progress_reflects_retired_items(self) -> None:
        item = self._add("Alien")
        self.rotation_service.get_or_start_rotation(DATABASE_ID)
        self.suggestion_service.reject_suggestion(item.id, 1, rejection_threshold=1)

        progress = self.rotation_service.rotation_progress(DATABASE_ID)

        self.assertEqual(progress.retired, 1)
        self.assertEqual(progress.remaining, 0)

    def test_progress_is_read_only_and_does_not_auto_transition(self) -> None:
        item = self._add("Alien")
        first = self.rotation_service.get_or_start_rotation(DATABASE_ID)
        self.rotation_service.record_presentation(DATABASE_ID, [item.id])

        self.rotation_service.rotation_progress(DATABASE_ID)

        still_open = self.rotation_service.get_open_rotation(DATABASE_ID)
        self.assertEqual(still_open.id, first.id)
        self.assertEqual(still_open.status, RotationStatus.OPEN)

    def test_progress_treats_a_removed_item_as_completion_without_double_counting(self) -> None:
        item = self._add("Alien")
        self.rotation_service.get_or_start_rotation(DATABASE_ID)
        self.suggestion_service.remove_suggestion_by_id(item.id)

        progress = self.rotation_service.rotation_progress(DATABASE_ID)

        self.assertEqual(progress.remaining, 0)
        self.assertEqual(progress.presented, 0)
        self.assertEqual(progress.retired, 0)
        self.assertEqual(progress.watched, 0)


class CandidateEligibilityTests(RotationServiceTestCase):
    def test_a_pending_item_is_eligible(self) -> None:
        item = self._add("Alien")
        self.rotation_service.get_or_start_rotation(DATABASE_ID)

        self.assertTrue(self.rotation_service.is_candidate_eligible(item, DATABASE_ID))

    def test_a_presented_item_is_not_eligible(self) -> None:
        item = self._add("Alien")
        self.rotation_service.get_or_start_rotation(DATABASE_ID)
        self.rotation_service.record_presentation(DATABASE_ID, [item.id])

        self.assertFalse(self.rotation_service.is_candidate_eligible(item, DATABASE_ID))

    def test_an_item_not_assigned_to_the_rotation_is_not_eligible(self) -> None:
        self.rotation_service.get_or_start_rotation(DATABASE_ID)
        unassigned_item = self._add("The Matrix")

        self.assertFalse(self.rotation_service.is_candidate_eligible(unassigned_item, DATABASE_ID))


class LowPoolReminderTimestampTests(RotationServiceTestCase):
    def test_no_reminder_has_been_sent_initially(self) -> None:
        self.assertIsNone(self.rotation_service.last_low_pool_reminder_sent_at(DATABASE_ID))

    def test_recording_a_reminder_persists_it(self) -> None:
        sent_at = datetime.now(timezone.utc)

        self.rotation_service.record_low_pool_reminder_sent(DATABASE_ID, sent_at)

        self.assertEqual(self.rotation_service.last_low_pool_reminder_sent_at(DATABASE_ID), sent_at)


class RotationPersistenceTests(RotationServiceTestCase):
    def test_rotation_state_survives_a_service_restart(self) -> None:
        item = self._add("Alien")
        rotation = self.rotation_service.get_or_start_rotation(DATABASE_ID)
        self.rotation_service.record_presentation(DATABASE_ID, [item.id])
        sent_at = datetime.now(timezone.utc)
        self.rotation_service.record_low_pool_reminder_sent(DATABASE_ID, sent_at)

        restarted_service = RotationService(self.suggestion_service, repository=self.rotation_repository)

        reloaded = restarted_service.get_open_rotation(DATABASE_ID)
        self.assertEqual(reloaded.id, rotation.id)
        self.assertEqual(reloaded.assigned_suggestion_ids, rotation.assigned_suggestion_ids)
        self.assertEqual(restarted_service.last_low_pool_reminder_sent_at(DATABASE_ID), sent_at)

    def test_rotation_data_is_a_plain_json_file_under_data_for_backup_compatibility(self) -> None:
        # BackupService sweeps every *.json file under data/ generically
        # (see services/backup_service.py) -- this just confirms
        # JsonRotationRepository writes a normal, independently loadable
        # JSON file with no special format needing backup-specific
        # handling.
        self._add("Alien")
        self.rotation_service.get_or_start_rotation(DATABASE_ID)

        self.assertTrue(self.rotation_repository._file_path.exists())
        self.assertEqual(self.rotation_repository._file_path.suffix, ".json")


if __name__ == "__main__":
    unittest.main()
