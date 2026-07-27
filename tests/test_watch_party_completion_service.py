"""Tests for WatchPartyCompletionService (Watch Party Lifecycle):
automatically transitioning a due watch party to COMPLETED and its Watch
Item to Watched, with a recorded watch date.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.watch_item import WatchItemStatus
from watch_party_manager.domain.watch_party import WatchPartyStatus
from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.watch_party_repository import JsonWatchPartyRepository
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.watch_party_completion_service import WatchPartyCompletionService
from watch_party_manager.services.watch_party_service import WatchPartyService


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WatchPartyCompletionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.watch_party_service = WatchPartyService(
            self.suggestion_service, repository=JsonWatchPartyRepository(root / "watch_parties.json")
        )
        self.completion_service = WatchPartyCompletionService(self.watch_party_service, self.suggestion_service)
        self.matrix = self.suggestion_service.suggest("The Matrix").watch_item
        self.suggestion_service.record_vote_win(self.matrix.id, utc_now().date())

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _schedule(self, *, scheduled_at=None, duration_minutes=90):
        return self.watch_party_service.schedule_watch_party(
            watch_item_id=self.matrix.id,
            scheduled_at=scheduled_at or (utc_now() - timedelta(hours=3)),
            guild_id=100,
            duration_minutes=duration_minutes,
        ).watch_party

    def test_completes_a_due_watch_party(self) -> None:
        watch_party = self._schedule()

        result = self.completion_service.complete_watch_party(watch_party.id)

        self.assertIsNotNone(result)
        self.assertEqual(result.watch_party.status, WatchPartyStatus.COMPLETED)

    def test_marks_the_watch_item_watched(self) -> None:
        watch_party = self._schedule()

        self.completion_service.complete_watch_party(watch_party.id)

        updated = self.suggestion_service.get_suggestion(self.matrix.id)
        self.assertEqual(updated.status, WatchItemStatus.WATCHED)

    def test_records_a_watch_date(self) -> None:
        watch_party = self._schedule()

        self.completion_service.complete_watch_party(watch_party.id)

        updated = self.suggestion_service.get_suggestion(self.matrix.id)
        self.assertEqual(len(updated.journey.watch_dates), 1)

    def test_watch_date_matches_the_watch_partys_end_time(self) -> None:
        scheduled_at = utc_now() - timedelta(hours=5)
        watch_party = self._schedule(scheduled_at=scheduled_at, duration_minutes=120)

        self.completion_service.complete_watch_party(watch_party.id)

        updated = self.suggestion_service.get_suggestion(self.matrix.id)
        expected_date = (scheduled_at + timedelta(minutes=120)).date()
        self.assertEqual(updated.journey.watch_dates[0], expected_date)

    def test_returns_none_for_a_nonexistent_watch_party(self) -> None:
        result = self.completion_service.complete_watch_party(999)

        self.assertIsNone(result)

    def test_is_idempotent(self) -> None:
        watch_party = self._schedule()
        self.completion_service.complete_watch_party(watch_party.id)

        result = self.completion_service.complete_watch_party(watch_party.id)

        self.assertIsNone(result)
        # No second watch date was recorded by the no-op second call.
        updated = self.suggestion_service.get_suggestion(self.matrix.id)
        self.assertEqual(len(updated.journey.watch_dates), 1)

    def test_does_not_complete_a_cancelled_watch_party(self) -> None:
        watch_party = self._schedule()
        self.watch_party_service.cancel_watch_party(watch_party.id)

        result = self.completion_service.complete_watch_party(watch_party.id)

        self.assertIsNone(result)
        updated = self.suggestion_service.get_suggestion(self.matrix.id)
        self.assertEqual(updated.status, WatchItemStatus.VOTE_WINNER)

    def test_does_not_override_an_already_archived_suggestion(self) -> None:
        # Mirrors record_vote_win's precedence rule: a manual archive
        # decision always wins over an automatic status transition.
        watch_party = self._schedule()
        self.suggestion_service.archive_suggestion(self.matrix.id)

        self.completion_service.complete_watch_party(watch_party.id)

        updated = self.suggestion_service.get_suggestion(self.matrix.id)
        self.assertEqual(updated.status, WatchItemStatus.ARCHIVED)
        # The watch date is still recorded even though status is untouched.
        self.assertEqual(len(updated.journey.watch_dates), 1)

    def test_result_includes_the_resolved_watch_item(self) -> None:
        watch_party = self._schedule()

        result = self.completion_service.complete_watch_party(watch_party.id)

        self.assertEqual(result.watch_item.id, self.matrix.id)


if __name__ == "__main__":
    unittest.main()
