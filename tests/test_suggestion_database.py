import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.suggestion_database import SuggestionDatabase


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SuggestionDatabaseModelTests(unittest.TestCase):
    def test_valid_suggestion_database(self) -> None:
        database = SuggestionDatabase(
            database_id=1,
            name="Sunday Watch Party",
            guild_id=100,
            channel_id=200,
        )

        self.assertEqual(database.database_id, 1)
        self.assertEqual(database.name, "Sunday Watch Party")
        self.assertEqual(database.guild_id, 100)
        self.assertEqual(database.channel_id, 200)
        self.assertTrue(database.active)
        self.assertIsNotNone(database.created_at.tzinfo)

    def test_active_defaults_to_true(self) -> None:
        database = SuggestionDatabase(database_id=1, name="Kung Fu Movies", guild_id=100, channel_id=200)
        self.assertTrue(database.active)

    def test_active_can_be_set_to_false(self) -> None:
        database = SuggestionDatabase(
            database_id=1, name="Halloween Movies", guild_id=100, channel_id=200, active=False
        )
        self.assertFalse(database.active)

    def test_name_is_trimmed(self) -> None:
        database = SuggestionDatabase(database_id=1, name="  Halloween Movies  ", guild_id=100, channel_id=200)
        self.assertEqual(database.name, "Halloween Movies")

    def test_rejects_empty_name(self) -> None:
        with self.assertRaises(ValueError):
            SuggestionDatabase(database_id=1, name="", guild_id=100, channel_id=200)

    def test_rejects_whitespace_only_name(self) -> None:
        with self.assertRaises(ValueError):
            SuggestionDatabase(database_id=1, name="   ", guild_id=100, channel_id=200)

    def test_rejects_non_positive_database_id(self) -> None:
        with self.assertRaises(ValueError):
            SuggestionDatabase(database_id=0, name="Sunday Watch Party", guild_id=100, channel_id=200)

    def test_rejects_non_positive_guild_id(self) -> None:
        with self.assertRaises(ValueError):
            SuggestionDatabase(database_id=1, name="Sunday Watch Party", guild_id=0, channel_id=200)

    def test_rejects_non_positive_channel_id(self) -> None:
        with self.assertRaises(ValueError):
            SuggestionDatabase(database_id=1, name="Sunday Watch Party", guild_id=100, channel_id=0)

    def test_rejects_naive_created_at(self) -> None:
        with self.assertRaises(ValueError):
            SuggestionDatabase(
                database_id=1,
                name="Sunday Watch Party",
                guild_id=100,
                channel_id=200,
                created_at=datetime(2026, 1, 1),
            )

    def test_accepts_a_timezone_aware_created_at(self) -> None:
        created_at = utc_now()
        database = SuggestionDatabase(
            database_id=1,
            name="Sunday Watch Party",
            guild_id=100,
            channel_id=200,
            created_at=created_at,
        )
        self.assertEqual(database.created_at, created_at)


if __name__ == "__main__":
    unittest.main()
