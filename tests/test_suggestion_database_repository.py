import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.suggestion_database import SuggestionDatabase
from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class JsonSuggestionDatabaseRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        """Point each test at its own temporary file so tests never touch real data."""
        self._temp_dir = tempfile.TemporaryDirectory()
        self.file_path = Path(self._temp_dir.name) / "suggestion_databases.json"
        self.repository = JsonSuggestionDatabaseRepository(self.file_path)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_load_returns_empty_result_when_file_does_not_exist(self) -> None:
        self.assertFalse(self.file_path.exists())

        result = self.repository.load()
        self.assertEqual(result.databases, [])
        self.assertEqual(result.next_id, 1)

    def test_load_returns_empty_result_and_logs_when_json_is_malformed(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text("{ this is not valid json", encoding="utf-8")

        with self.assertLogs(
            "watch_party_manager.persistence.suggestion_database_repository", level="ERROR"
        ) as log_context:
            result = self.repository.load()

        self.assertEqual(result.databases, [])
        self.assertEqual(result.next_id, 1)
        self.assertTrue(any("suggestion databases" in message for message in log_context.output))

    def test_save_creates_the_file_and_parent_directory(self) -> None:
        nested_path = Path(self._temp_dir.name) / "nested" / "suggestion_databases.json"
        repository = JsonSuggestionDatabaseRepository(nested_path)

        repository.save(
            [SuggestionDatabase(database_id=1, name="Sunday Watch Party", guild_id=100, channel_id=200)],
            next_id=2,
        )

        self.assertTrue(nested_path.exists())

    def test_save_then_load_round_trips_a_single_database(self) -> None:
        self.repository.save(
            [SuggestionDatabase(database_id=1, name="Sunday Watch Party", guild_id=100, channel_id=200)],
            next_id=2,
        )

        result = self.repository.load()
        self.assertEqual(len(result.databases), 1)
        self.assertEqual(result.databases[0].name, "Sunday Watch Party")
        self.assertEqual(result.databases[0].guild_id, 100)
        self.assertEqual(result.databases[0].channel_id, 200)
        self.assertEqual(result.next_id, 2)

    def test_save_then_load_preserves_creation_order_of_multiple_databases(self) -> None:
        databases = [
            SuggestionDatabase(database_id=1, name="Sunday Watch Party", guild_id=100, channel_id=200),
            SuggestionDatabase(database_id=2, name="Kung Fu Movies", guild_id=100, channel_id=201),
            SuggestionDatabase(database_id=3, name="Halloween Movies", guild_id=100, channel_id=202),
        ]
        self.repository.save(databases, next_id=4)

        result = self.repository.load()
        names = [database.name for database in result.databases]
        self.assertEqual(names, ["Sunday Watch Party", "Kung Fu Movies", "Halloween Movies"])

    def test_save_then_load_round_trips_active_flag(self) -> None:
        self.repository.save(
            [
                SuggestionDatabase(
                    database_id=1, name="Retired Database", guild_id=100, channel_id=200, active=False
                )
            ],
            next_id=2,
        )

        result = self.repository.load()
        self.assertFalse(result.databases[0].active)

    def test_save_then_load_round_trips_created_at_accurately(self) -> None:
        created_at = datetime(2026, 7, 12, 9, 0, 0, tzinfo=timezone.utc)
        self.repository.save(
            [
                SuggestionDatabase(
                    database_id=1,
                    name="Sunday Watch Party",
                    guild_id=100,
                    channel_id=200,
                    created_at=created_at,
                )
            ],
            next_id=2,
        )

        result = self.repository.load()
        self.assertEqual(result.databases[0].created_at, created_at)

    def test_next_id_persists_and_is_not_reused(self) -> None:
        self.repository.save(
            [SuggestionDatabase(database_id=1, name="Sunday Watch Party", guild_id=100, channel_id=200)],
            next_id=2,
        )
        self.repository.save([], next_id=2)  # Database 1 removed, but ID must not be reused.

        result = self.repository.load()
        self.assertEqual(result.databases, [])
        self.assertEqual(result.next_id, 2)

    def test_empty_state_persists(self) -> None:
        self.repository.save([], next_id=1)

        result = self.repository.load()
        self.assertEqual(result.databases, [])
        self.assertEqual(result.next_id, 1)

    def test_human_readable_json_contains_expected_fields(self) -> None:
        self.repository.save(
            [SuggestionDatabase(database_id=1, name="Sunday Watch Party", guild_id=100, channel_id=200)],
            next_id=2,
        )

        raw_text = self.file_path.read_text(encoding="utf-8")
        self.assertIn('"database_id": 1', raw_text)
        self.assertIn('"name": "Sunday Watch Party"', raw_text)
        self.assertIn('"guild_id": 100', raw_text)
        self.assertIn('"channel_id": 200', raw_text)
        self.assertIn('"next_id": 2', raw_text)

    def test_loading_an_entry_without_an_active_field_defaults_to_active(self) -> None:
        # There's no legacy (pre-database) format for this file to migrate
        # from, since it's new in this milestone. The one forward-compat
        # case this repository does handle is a future/partial entry
        # missing "active" -- it should default to active rather than fail.
        now = utc_now()
        raw_json = f"""
        {{
          "next_id": 2,
          "databases": [
            {{
              "database_id": 1,
              "name": "Sunday Watch Party",
              "guild_id": 100,
              "channel_id": 200,
              "created_at": "{now.isoformat()}"
            }}
          ]
        }}
        """
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(raw_json, encoding="utf-8")

        result = self.repository.load()
        self.assertTrue(result.databases[0].active)


if __name__ == "__main__":
    unittest.main()
