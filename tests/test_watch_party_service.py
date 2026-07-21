import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.watch_party import WatchPartyStatus
from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.watch_party_repository import JsonWatchPartyRepository
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.watch_party_service import WatchPartyService


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WatchPartyServiceTests(unittest.TestCase):
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
        self.matrix = self.suggestion_service.suggest("The Matrix").watch_item

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    # --- schedule_watch_party ----------------------------------------------------

    def test_schedules_a_watch_party(self) -> None:
        scheduled_at = utc_now() + timedelta(days=1)

        result = self.watch_party_service.schedule_watch_party(
            watch_item_id=self.matrix.id, scheduled_at=scheduled_at, guild_id=100, channel_id=200
        )

        self.assertTrue(result.success)
        self.assertIsNotNone(result.watch_party)
        self.assertEqual(result.watch_party.watch_item_id, self.matrix.id)
        self.assertEqual(result.watch_party.scheduled_at, scheduled_at)
        self.assertEqual(result.watch_party.guild_id, 100)
        self.assertEqual(result.watch_party.channel_id, 200)
        self.assertEqual(result.watch_party.status, WatchPartyStatus.SCHEDULED)

    def test_assigns_sequential_ids(self) -> None:
        first = self.watch_party_service.schedule_watch_party(
            watch_item_id=self.matrix.id, scheduled_at=utc_now() + timedelta(days=1), guild_id=100
        )
        second = self.watch_party_service.schedule_watch_party(
            watch_item_id=self.matrix.id, scheduled_at=utc_now() + timedelta(days=2), guild_id=100
        )

        self.assertEqual(second.watch_party.id, first.watch_party.id + 1)

    def test_rejects_scheduling_for_a_nonexistent_watch_item(self) -> None:
        result = self.watch_party_service.schedule_watch_party(
            watch_item_id=999, scheduled_at=utc_now() + timedelta(days=1), guild_id=100
        )

        self.assertFalse(result.success)
        self.assertIsNone(result.watch_party)

    def test_channel_id_is_optional(self) -> None:
        result = self.watch_party_service.schedule_watch_party(
            watch_item_id=self.matrix.id, scheduled_at=utc_now() + timedelta(days=1), guild_id=100
        )

        self.assertTrue(result.success)
        self.assertIsNone(result.watch_party.channel_id)

    def test_schedule_persists_the_watch_party(self) -> None:
        scheduled_at = utc_now() + timedelta(days=1)
        self.watch_party_service.schedule_watch_party(
            watch_item_id=self.matrix.id, scheduled_at=scheduled_at, guild_id=100
        )

        reloaded = WatchPartyService(
            self.suggestion_service,
            repository=JsonWatchPartyRepository(Path(self._temp_dir.name) / "watch_parties.json"),
        )
        self.assertEqual(reloaded.get_watch_party(1).scheduled_at, scheduled_at)

    # --- get_watch_party -----------------------------------------------------------

    def test_get_watch_party_returns_none_for_unknown_id(self) -> None:
        self.assertIsNone(self.watch_party_service.get_watch_party(999))

    # --- reschedule_watch_party ------------------------------------------------------

    def test_reschedules_a_watch_party(self) -> None:
        created = self.watch_party_service.schedule_watch_party(
            watch_item_id=self.matrix.id, scheduled_at=utc_now() + timedelta(days=1), guild_id=100
        ).watch_party
        new_time = utc_now() + timedelta(days=5)

        result = self.watch_party_service.reschedule_watch_party(created.id, new_time)

        self.assertTrue(result.success)
        self.assertEqual(result.watch_party.scheduled_at, new_time)
        self.assertEqual(self.watch_party_service.get_watch_party(created.id).scheduled_at, new_time)

    def test_reschedule_fails_for_a_nonexistent_watch_party(self) -> None:
        result = self.watch_party_service.reschedule_watch_party(999, utc_now() + timedelta(days=1))

        self.assertFalse(result.success)

    def test_reschedule_fails_for_a_cancelled_watch_party(self) -> None:
        created = self.watch_party_service.schedule_watch_party(
            watch_item_id=self.matrix.id, scheduled_at=utc_now() + timedelta(days=1), guild_id=100
        ).watch_party
        self.watch_party_service.cancel_watch_party(created.id)

        result = self.watch_party_service.reschedule_watch_party(created.id, utc_now() + timedelta(days=5))

        self.assertFalse(result.success)

    def test_reschedule_persists_the_change(self) -> None:
        created = self.watch_party_service.schedule_watch_party(
            watch_item_id=self.matrix.id, scheduled_at=utc_now() + timedelta(days=1), guild_id=100
        ).watch_party
        new_time = utc_now() + timedelta(days=5)
        self.watch_party_service.reschedule_watch_party(created.id, new_time)

        reloaded = WatchPartyService(
            self.suggestion_service,
            repository=JsonWatchPartyRepository(Path(self._temp_dir.name) / "watch_parties.json"),
        )
        self.assertEqual(reloaded.get_watch_party(created.id).scheduled_at, new_time)

    # --- cancel_watch_party ------------------------------------------------------------

    def test_cancels_a_watch_party(self) -> None:
        created = self.watch_party_service.schedule_watch_party(
            watch_item_id=self.matrix.id, scheduled_at=utc_now() + timedelta(days=1), guild_id=100
        ).watch_party

        result = self.watch_party_service.cancel_watch_party(created.id)

        self.assertTrue(result.success)
        self.assertEqual(self.watch_party_service.get_watch_party(created.id).status, WatchPartyStatus.CANCELLED)

    def test_cancel_fails_for_a_nonexistent_watch_party(self) -> None:
        result = self.watch_party_service.cancel_watch_party(999)

        self.assertFalse(result.success)

    def test_cancel_fails_for_an_already_cancelled_watch_party(self) -> None:
        created = self.watch_party_service.schedule_watch_party(
            watch_item_id=self.matrix.id, scheduled_at=utc_now() + timedelta(days=1), guild_id=100
        ).watch_party
        self.watch_party_service.cancel_watch_party(created.id)

        result = self.watch_party_service.cancel_watch_party(created.id)

        self.assertFalse(result.success)

    def test_cancel_persists_the_change(self) -> None:
        created = self.watch_party_service.schedule_watch_party(
            watch_item_id=self.matrix.id, scheduled_at=utc_now() + timedelta(days=1), guild_id=100
        ).watch_party
        self.watch_party_service.cancel_watch_party(created.id)

        reloaded = WatchPartyService(
            self.suggestion_service,
            repository=JsonWatchPartyRepository(Path(self._temp_dir.name) / "watch_parties.json"),
        )
        self.assertEqual(reloaded.get_watch_party(created.id).status, WatchPartyStatus.CANCELLED)


if __name__ == "__main__":
    unittest.main()
