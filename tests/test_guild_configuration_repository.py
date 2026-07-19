import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from watch_party_manager.domain.guild_configuration import GuildConfiguration
from watch_party_manager.persistence.guild_configuration_repository import (
    CURRENT_SCHEMA_VERSION,
    FutureSchemaVersionError,
    GuildConfigurationRepository,
)


class GuildConfigurationRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "nested" / "guild_configurations.json"
        self.repo = GuildConfigurationRepository(self.path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_missing_file_is_empty(self):
        self.assertIsNone(self.repo.get(1))
        self.assertFalse(self.repo.exists(1))
        self.assertEqual(self.repo.list_all(), [])

    def test_save_creates_file_and_round_trips_defaults(self):
        self.repo.save(GuildConfiguration(guild_id=1, guild_name="Guild"))
        loaded = self.repo.get(1)
        self.assertTrue(self.path.exists())
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.guild_name, "Guild")
        self.assertEqual(loaded.schema_version, CURRENT_SCHEMA_VERSION)
        self.assertEqual(loaded.voting_defaults.candidate_count, 3)

    def test_multiple_guilds_are_preserved(self):
        self.repo.save(GuildConfiguration(guild_id=1, guild_name="One"))
        self.repo.save(GuildConfiguration(guild_id=2, guild_name="Two"))
        self.assertEqual({item.guild_id for item in self.repo.list_all()}, {1, 2})

    def test_update_preserves_created_at_and_increments_version(self):
        created = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.repo.save(GuildConfiguration(guild_id=1, guild_name="One", created_at=created, updated_at=created))
        first = self.repo.get(1)
        self.repo.save(GuildConfiguration(guild_id=1, guild_name="Updated"))
        second = self.repo.get(1)
        self.assertEqual(second.created_at, first.created_at)
        self.assertEqual(second.configuration_version, first.configuration_version + 1)
        self.assertGreaterEqual(second.updated_at, first.updated_at)
        self.assertEqual(second.guild_name, "Updated")

    def test_failed_validation_does_not_modify_saved_file(self):
        self.repo.save(GuildConfiguration(guild_id=1, guild_name="One"))
        before = self.path.read_text(encoding="utf-8")
        with self.assertRaises(ValueError):
            GuildConfiguration(guild_id=1, guild_name=" ")
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_rejects_future_schema_version(self):
        self.path.parent.mkdir(parents=True)
        now = datetime.now(timezone.utc).isoformat()
        self.path.write_text(json.dumps({"guilds": {"1": {
            "schema_version": CURRENT_SCHEMA_VERSION + 1,
            "guild_id": 1, "guild_name": "Future", "created_at": now, "updated_at": now,
        }}}), encoding="utf-8")
        with self.assertRaises(FutureSchemaVersionError):
            self.repo.get(1)

    def test_missing_schema_version_defaults_to_one(self):
        self.path.parent.mkdir(parents=True)
        now = datetime.now(timezone.utc).isoformat()
        self.path.write_text(json.dumps({"guilds": {"1": {
            "guild_id": 1, "guild_name": "Legacy", "created_at": now, "updated_at": now,
        }}}), encoding="utf-8")
        self.assertEqual(self.repo.get(1).schema_version, 1)

    def test_unknown_fields_survive_load_and_save(self):
        self.path.parent.mkdir(parents=True)
        now = datetime.now(timezone.utc).isoformat()
        raw = {"guilds": {"1": {
            "schema_version": 1, "guild_id": 1, "guild_name": "Guild", "created_at": now, "updated_at": now,
            "future_top": {"enabled": True},
            "channels": {"announcements_channel_id": None, "log_channel_id": None, "future_channel": 42},
            "notifications": {"future_notifications": "keep", "vote": {"future_vote": 7}},
        }}}
        self.path.write_text(json.dumps(raw), encoding="utf-8")
        loaded = self.repo.get(1)
        self.repo.save(loaded)
        saved = json.loads(self.path.read_text(encoding="utf-8"))["guilds"]["1"]
        self.assertEqual(saved["future_top"], {"enabled": True})
        self.assertEqual(saved["channels"]["future_channel"], 42)
        self.assertEqual(saved["notifications"]["future_notifications"], "keep")
        self.assertEqual(saved["notifications"]["vote"]["future_vote"], 7)

    def test_malformed_file_fails_closed(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("not json", encoding="utf-8")
        self.assertEqual(self.repo.list_all(), [])

    def test_mismatched_guild_key_fails_closed(self):
        self.path.parent.mkdir(parents=True)
        now = datetime.now(timezone.utc).isoformat()
        self.path.write_text(json.dumps({"guilds": {"2": {
            "schema_version": 1, "guild_id": 1, "guild_name": "Guild", "created_at": now, "updated_at": now,
        }}}), encoding="utf-8")
        self.assertEqual(self.repo.list_all(), [])

    def test_atomic_write_leaves_no_temporary_file(self):
        self.repo.save(GuildConfiguration(guild_id=1, guild_name="Guild"))
        self.assertFalse(self.path.with_suffix(self.path.suffix + ".tmp").exists())


if __name__ == "__main__":
    unittest.main()
