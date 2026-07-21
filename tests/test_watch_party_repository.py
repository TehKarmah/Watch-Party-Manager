import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.watch_party import WatchParty, WatchPartyStatus
from watch_party_manager.persistence.watch_party_repository import JsonWatchPartyRepository


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class JsonWatchPartyRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.file_path = Path(self._temp_dir.name) / "watch_parties.json"
        self.repository = JsonWatchPartyRepository(self.file_path)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_load_returns_empty_state_when_file_does_not_exist(self) -> None:
        self.assertFalse(self.file_path.exists())

        result = self.repository.load()

        self.assertEqual(result.watch_parties, [])
        self.assertEqual(result.next_id, 1)

    def test_load_returns_empty_state_and_logs_when_json_is_malformed(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text("{ this is not valid json", encoding="utf-8")

        with self.assertLogs(
            "watch_party_manager.persistence.watch_party_repository", level="ERROR"
        ) as log_context:
            result = self.repository.load()

        self.assertEqual(result.watch_parties, [])
        self.assertEqual(result.next_id, 1)
        self.assertTrue(any("watch party" in message for message in log_context.output))

    def test_save_creates_the_file_and_parent_directory(self) -> None:
        nested_path = Path(self._temp_dir.name) / "nested" / "watch_parties.json"
        repository = JsonWatchPartyRepository(nested_path)

        repository.save([], next_id=1)

        self.assertTrue(nested_path.exists())

    def test_save_then_load_round_trips_a_single_watch_party(self) -> None:
        scheduled_at = datetime(2026, 7, 25, 20, 0, tzinfo=timezone.utc)
        watch_party = WatchParty(
            id=1,
            watch_item_id=42,
            scheduled_at=scheduled_at,
            guild_id=100,
            channel_id=200,
        )

        self.repository.save([watch_party], next_id=2)
        result = self.repository.load()

        self.assertEqual(len(result.watch_parties), 1)
        loaded = result.watch_parties[0]
        self.assertEqual(loaded.id, 1)
        self.assertEqual(loaded.watch_item_id, 42)
        self.assertEqual(loaded.scheduled_at, scheduled_at)
        self.assertEqual(loaded.guild_id, 100)
        self.assertEqual(loaded.channel_id, 200)
        self.assertEqual(loaded.status, WatchPartyStatus.SCHEDULED)
        self.assertEqual(result.next_id, 2)

    def test_round_trips_a_cancelled_watch_party(self) -> None:
        watch_party = WatchParty(
            id=1,
            watch_item_id=1,
            scheduled_at=utc_now() + timedelta(days=1),
            guild_id=1,
            status=WatchPartyStatus.CANCELLED,
        )

        self.repository.save([watch_party], next_id=2)
        result = self.repository.load()

        self.assertEqual(result.watch_parties[0].status, WatchPartyStatus.CANCELLED)

    def test_round_trips_a_watch_party_with_no_channel(self) -> None:
        watch_party = WatchParty(
            id=1, watch_item_id=1, scheduled_at=utc_now() + timedelta(days=1), guild_id=1
        )

        self.repository.save([watch_party], next_id=2)
        result = self.repository.load()

        self.assertIsNone(result.watch_parties[0].channel_id)

    def test_round_trips_multiple_watch_parties_preserving_order(self) -> None:
        first = WatchParty(id=1, watch_item_id=1, scheduled_at=utc_now() + timedelta(days=1), guild_id=1)
        second = WatchParty(id=2, watch_item_id=2, scheduled_at=utc_now() + timedelta(days=2), guild_id=1)

        self.repository.save([first, second], next_id=3)
        result = self.repository.load()

        self.assertEqual([wp.id for wp in result.watch_parties], [1, 2])

    def test_save_overwrites_previous_contents(self) -> None:
        self.repository.save(
            [WatchParty(id=1, watch_item_id=1, scheduled_at=utc_now() + timedelta(days=1), guild_id=1)],
            next_id=2,
        )

        self.repository.save([], next_id=1)
        result = self.repository.load()

        self.assertEqual(result.watch_parties, [])
        self.assertEqual(result.next_id, 1)


if __name__ == "__main__":
    unittest.main()
