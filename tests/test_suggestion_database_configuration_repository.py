"""Tests for SuggestionDatabaseConfigurationRepository."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from watch_party_manager.domain.guild_configuration import GuildVoteVisibility, TieBehavior
from watch_party_manager.domain.suggestion_database_configuration import (
    SuggestionDatabaseChannelsConfig,
    SuggestionDatabaseConfiguration,
    SuggestionDatabasePermissionsConfig,
    VotingOverridesConfig,
)
from watch_party_manager.persistence.guild_configuration_repository import FutureSchemaVersionError
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SuggestionDatabaseConfigurationRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.file_path = Path(self._temp_dir.name) / "suggestion_database_configurations.json"
        self.repository = SuggestionDatabaseConfigurationRepository(self.file_path)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _config(self, guild_id=100, database_id=1, display_name="Movies", **kwargs) -> SuggestionDatabaseConfiguration:
        return SuggestionDatabaseConfiguration(
            guild_id=guild_id, database_id=database_id, display_name=display_name, **kwargs
        )

    # --- Missing file / empty repository ------------------------------------------

    def test_get_returns_none_when_file_does_not_exist(self) -> None:
        self.assertFalse(self.file_path.exists())
        self.assertIsNone(self.repository.get(100, 1))

    def test_exists_is_false_when_file_does_not_exist(self) -> None:
        self.assertFalse(self.repository.exists(100, 1))

    def test_list_for_guild_is_empty_when_file_does_not_exist(self) -> None:
        self.assertEqual(self.repository.list_for_guild(100), [])

    def test_list_all_is_empty_when_file_does_not_exist(self) -> None:
        self.assertEqual(self.repository.list_all(), [])

    def test_get_returns_none_for_an_unknown_database_in_a_non_empty_store(self) -> None:
        self.repository.save(self._config())
        self.assertIsNone(self.repository.get(100, 999))

    # --- Automatic file creation --------------------------------------------------

    def test_save_creates_the_file(self) -> None:
        self.repository.save(self._config())
        self.assertTrue(self.file_path.exists())

    def test_save_creates_the_parent_directory(self) -> None:
        nested_path = Path(self._temp_dir.name) / "nested" / "suggestion_database_configurations.json"
        repository = SuggestionDatabaseConfigurationRepository(nested_path)

        repository.save(self._config())

        self.assertTrue(nested_path.exists())

    # --- Create / round trip ----------------------------------------------------

    def test_save_then_get_round_trips_a_minimal_configuration(self) -> None:
        self.repository.save(self._config())

        loaded = self.repository.get(100, 1)

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.guild_id, 100)
        self.assertEqual(loaded.database_id, 1)
        self.assertEqual(loaded.display_name, "Movies")

    def test_save_then_get_round_trips_every_section(self) -> None:
        original = self._config(
            channels=SuggestionDatabaseChannelsConfig(suggestion_channel_id=10, voting_channel_id=10),
            voting_overrides=VotingOverridesConfig(
                candidate_count=5,
                duration_hours=48,
                visibility=GuildVoteVisibility.BLIND,
                max_vote_changes=2,
                tie_behavior=TieBehavior.ALL_WINNERS,
            ),
            permissions=SuggestionDatabasePermissionsConfig(moderator_role_ids=(5, 6)),
        )

        self.repository.save(original)
        loaded = self.repository.get(100, 1)

        self.assertEqual(loaded.channels.suggestion_channel_id, 10)
        self.assertEqual(loaded.voting_overrides.candidate_count, 5)
        self.assertEqual(loaded.voting_overrides.duration_hours, 48)
        self.assertEqual(loaded.voting_overrides.visibility, GuildVoteVisibility.BLIND)
        self.assertEqual(loaded.voting_overrides.tie_behavior, TieBehavior.ALL_WINNERS)
        self.assertEqual(loaded.permissions.moderator_role_ids, (5, 6))

    def test_optional_channels_serialize_as_null(self) -> None:
        self.repository.save(self._config())

        raw = json.loads(self.file_path.read_text(encoding="utf-8"))
        channels = raw["guilds"]["100"]["databases"]["1"]["channels"]
        self.assertIsNone(channels["suggestion_channel_id"])

    # --- Update / versioning / immutability --------------------------------------

    def test_save_again_updates_the_existing_configuration(self) -> None:
        self.repository.save(self._config())
        self.repository.save(self._config(display_name="Movies Renamed"))

        loaded = self.repository.get(100, 1)
        self.assertEqual(loaded.display_name, "Movies Renamed")

    def test_configuration_version_starts_at_one(self) -> None:
        self.repository.save(self._config())
        self.assertEqual(self.repository.get(100, 1).configuration_version, 1)

    def test_configuration_version_increments_on_each_update(self) -> None:
        self.repository.save(self._config())
        self.repository.save(self._config())
        self.repository.save(self._config())

        self.assertEqual(self.repository.get(100, 1).configuration_version, 3)

    def test_configuration_version_supplied_by_the_caller_is_ignored_on_update(self) -> None:
        self.repository.save(self._config())
        self.repository.save(self._config(configuration_version=99))

        self.assertEqual(self.repository.get(100, 1).configuration_version, 2)

    def test_updated_at_refreshes_on_every_save(self) -> None:
        self.repository.save(self._config())
        first_updated_at = self.repository.get(100, 1).updated_at

        self.repository.save(self._config())
        second_updated_at = self.repository.get(100, 1).updated_at

        self.assertGreaterEqual(second_updated_at, first_updated_at)

    def test_created_at_is_preserved_across_updates(self) -> None:
        self.repository.save(self._config())
        original_created_at = self.repository.get(100, 1).created_at

        self.repository.save(self._config(created_at=datetime(2020, 1, 1, tzinfo=timezone.utc)))

        self.assertEqual(self.repository.get(100, 1).created_at, original_created_at)

    def test_guild_id_and_database_id_are_immutable_identity(self) -> None:
        # There is no "rename" operation: guild_id/database_id together
        # are the record's identity, so saving a configuration with a
        # different database_id creates a second, distinct record rather
        # than altering the first one's identity.
        self.repository.save(self._config(database_id=1))
        self.repository.save(self._config(database_id=2))

        self.assertIsNotNone(self.repository.get(100, 1))
        self.assertIsNotNone(self.repository.get(100, 2))
        self.assertEqual(len(self.repository.list_for_guild(100)), 2)

    def test_update_persists_across_reload(self) -> None:
        self.repository.save(self._config())
        self.repository.save(self._config(display_name="Movies Renamed"))

        reloaded_repository = SuggestionDatabaseConfigurationRepository(self.file_path)
        self.assertEqual(reloaded_repository.get(100, 1).display_name, "Movies Renamed")

    # --- Deactivation / reactivation preserved data --------------------------------

    def test_deactivating_a_database_preserves_its_configuration(self) -> None:
        self.repository.save(self._config(active=True))
        self.repository.save(self._config(active=False))

        loaded = self.repository.get(100, 1)
        self.assertFalse(loaded.active)
        self.assertEqual(loaded.display_name, "Movies")

    def test_reactivating_a_database_is_supported(self) -> None:
        self.repository.save(self._config(active=False))
        self.repository.save(self._config(active=True))

        self.assertTrue(self.repository.get(100, 1).active)

    # --- exists -----------------------------------------------------------------

    def test_exists_is_true_after_saving(self) -> None:
        self.repository.save(self._config())
        self.assertTrue(self.repository.exists(100, 1))

    def test_exists_is_false_for_a_different_database(self) -> None:
        self.repository.save(self._config())
        self.assertFalse(self.repository.exists(100, 999))

    # --- list_for_guild / list_all / multiple guilds and databases ------------------

    def test_list_for_guild_returns_only_that_guilds_databases(self) -> None:
        self.repository.save(self._config(guild_id=100, database_id=1))
        self.repository.save(self._config(guild_id=100, database_id=2))
        self.repository.save(self._config(guild_id=200, database_id=1))

        guild_100_configs = self.repository.list_for_guild(100)

        self.assertEqual(len(guild_100_configs), 2)
        self.assertTrue(all(config.guild_id == 100 for config in guild_100_configs))

    def test_list_all_returns_every_configuration_across_guilds(self) -> None:
        self.repository.save(self._config(guild_id=100, database_id=1))
        self.repository.save(self._config(guild_id=100, database_id=2))
        self.repository.save(self._config(guild_id=200, database_id=1))

        self.assertEqual(len(self.repository.list_all()), 3)

    def test_multiple_databases_per_guild_are_independently_retrievable(self) -> None:
        self.repository.save(self._config(guild_id=100, database_id=1, display_name="Movies"))
        self.repository.save(self._config(guild_id=100, database_id=2, display_name="Anime"))

        self.assertEqual(self.repository.get(100, 1).display_name, "Movies")
        self.assertEqual(self.repository.get(100, 2).display_name, "Anime")

    def test_multiple_guilds_do_not_interfere_with_each_other(self) -> None:
        self.repository.save(self._config(guild_id=100, database_id=1, display_name="Guild One Movies"))
        self.repository.save(self._config(guild_id=200, database_id=1, display_name="Guild Two Movies"))

        self.repository.save(
            self._config(guild_id=100, database_id=1, display_name="Guild One Movies Renamed")
        )

        self.assertEqual(self.repository.get(100, 1).display_name, "Guild One Movies Renamed")
        self.assertEqual(self.repository.get(200, 1).display_name, "Guild Two Movies")

    # --- Malformed JSON ------------------------------------------------------------

    def test_get_returns_none_and_logs_when_json_is_malformed(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text("{ this is not valid json", encoding="utf-8")

        with self.assertLogs(
            "watch_party_manager.persistence.suggestion_database_configuration_repository", level="ERROR"
        ) as log_context:
            result = self.repository.get(100, 1)

        self.assertIsNone(result)
        self.assertTrue(
            any("suggestion database configurations" in message for message in log_context.output)
        )

    def test_list_all_returns_empty_when_json_is_malformed(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text("not json at all", encoding="utf-8")

        self.assertEqual(self.repository.list_all(), [])

    def test_get_returns_none_when_expected_key_is_missing(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text('{"not_guilds": {}}', encoding="utf-8")

        self.assertIsNone(self.repository.get(100, 1))

    # --- Atomic writes / failed validation preserving prior state ------------------

    def test_atomic_write_leaves_no_temporary_file_behind(self) -> None:
        self.repository.save(self._config())

        temporary_path = self.file_path.with_suffix(self.file_path.suffix + ".tmp")
        self.assertFalse(temporary_path.exists())
        self.assertTrue(self.file_path.exists())

    def test_failed_validation_never_reaches_or_modifies_the_persisted_file(self) -> None:
        self.repository.save(self._config())
        original_contents = self.file_path.read_text(encoding="utf-8")

        with self.assertRaises(ValueError):
            # Invalid before it could ever reach repository.save(): the
            # domain model itself refuses to construct.
            SuggestionDatabaseConfiguration(guild_id=100, database_id=1, display_name="")

        self.assertEqual(self.file_path.read_text(encoding="utf-8"), original_contents)

    def test_failed_validation_on_a_nested_section_never_modifies_the_persisted_file(self) -> None:
        self.repository.save(self._config())
        original_contents = self.file_path.read_text(encoding="utf-8")

        with self.assertRaises(ValueError):
            VotingOverridesConfig(candidate_count=999)

        self.assertEqual(self.file_path.read_text(encoding="utf-8"), original_contents)

    # --- Future schema version rejection --------------------------------------------

    def test_rejects_a_future_schema_version(self) -> None:
        now_iso = utc_now().isoformat()
        future_json = json.dumps(
            {
                "guilds": {
                    "100": {
                        "databases": {
                            "1": {
                                "schema_version": 999,
                                "guild_id": 100,
                                "database_id": 1,
                                "display_name": "Movies",
                                "created_at": now_iso,
                                "updated_at": now_iso,
                            }
                        }
                    }
                }
            }
        )
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(future_json, encoding="utf-8")

        with self.assertRaises(FutureSchemaVersionError):
            self.repository.get(100, 1)

    def test_future_schema_version_rejection_propagates_from_list_all_too(self) -> None:
        now_iso = utc_now().isoformat()
        future_json = json.dumps(
            {
                "guilds": {
                    "100": {
                        "databases": {
                            "1": {
                                "schema_version": 999,
                                "guild_id": 100,
                                "database_id": 1,
                                "display_name": "Movies",
                                "created_at": now_iso,
                                "updated_at": now_iso,
                            }
                        }
                    }
                }
            }
        )
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(future_json, encoding="utf-8")

        with self.assertRaises(FutureSchemaVersionError):
            self.repository.list_all()

    # --- Migration seam --------------------------------------------------------------

    def test_missing_schema_version_defaults_to_version_one(self) -> None:
        now_iso = utc_now().isoformat()
        legacy_json = json.dumps(
            {
                "guilds": {
                    "100": {
                        "databases": {
                            "1": {
                                "guild_id": 100,
                                "database_id": 1,
                                "display_name": "Movies",
                                "created_at": now_iso,
                                "updated_at": now_iso,
                            }
                        }
                    }
                }
            }
        )
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(legacy_json, encoding="utf-8")

        loaded = self.repository.get(100, 1)

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.schema_version, 1)

    def test_migration_registry_is_empty_for_the_current_schema_version(self) -> None:
        self.assertEqual(SuggestionDatabaseConfigurationRepository._MIGRATIONS, {})

    def test_schema_version_below_one_is_rejected(self) -> None:
        now_iso = utc_now().isoformat()
        invalid_json = json.dumps(
            {
                "guilds": {
                    "100": {
                        "databases": {
                            "1": {
                                "schema_version": 0,
                                "guild_id": 100,
                                "database_id": 1,
                                "display_name": "Movies",
                                "created_at": now_iso,
                                "updated_at": now_iso,
                            }
                        }
                    }
                }
            }
        )
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(invalid_json, encoding="utf-8")

        self.assertIsNone(self.repository.get(100, 1))

    # --- Unknown field / unknown nested field preservation -----------------------------

    def test_unknown_top_level_fields_are_preserved_across_a_save_reload_cycle(self) -> None:
        now_iso = utc_now().isoformat()
        entry_with_unknown_field = json.dumps(
            {
                "guilds": {
                    "100": {
                        "databases": {
                            "1": {
                                "schema_version": 1,
                                "guild_id": 100,
                                "database_id": 1,
                                "display_name": "Movies",
                                "created_at": now_iso,
                                "updated_at": now_iso,
                                "a_future_top_level_field": "keep me",
                            }
                        }
                    }
                }
            }
        )
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(entry_with_unknown_field, encoding="utf-8")

        loaded = self.repository.get(100, 1)
        self.assertEqual(loaded.extra_fields.get("a_future_top_level_field"), "keep me")

        # Re-save and confirm it survives another round trip.
        self.repository.save(loaded)
        reloaded = self.repository.get(100, 1)
        self.assertEqual(reloaded.extra_fields.get("a_future_top_level_field"), "keep me")

    def test_unknown_nested_fields_are_preserved_across_a_save_reload_cycle(self) -> None:
        now_iso = utc_now().isoformat()
        entry_with_unknown_nested_field = json.dumps(
            {
                "guilds": {
                    "100": {
                        "databases": {
                            "1": {
                                "schema_version": 1,
                                "guild_id": 100,
                                "database_id": 1,
                                "display_name": "Movies",
                                "created_at": now_iso,
                                "updated_at": now_iso,
                                "voting_overrides": {"a_future_voting_field": "keep me too"},
                            }
                        }
                    }
                }
            }
        )
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(entry_with_unknown_nested_field, encoding="utf-8")

        loaded = self.repository.get(100, 1)
        self.assertEqual(loaded.voting_overrides.extra_fields.get("a_future_voting_field"), "keep me too")

        self.repository.save(loaded)
        reloaded = self.repository.get(100, 1)
        self.assertEqual(
            reloaded.voting_overrides.extra_fields.get("a_future_voting_field"), "keep me too"
        )

    # --- Human-readable JSON -------------------------------------------------------

    def test_json_is_nested_by_guild_id_then_database_id(self) -> None:
        self.repository.save(self._config())

        raw = json.loads(self.file_path.read_text(encoding="utf-8"))
        self.assertIn("100", raw["guilds"])
        self.assertIn("1", raw["guilds"]["100"]["databases"])

    def test_human_readable_json_contains_expected_fields(self) -> None:
        self.repository.save(self._config())

        raw_text = self.file_path.read_text(encoding="utf-8")
        self.assertIn('"guild_id": 100', raw_text)
        self.assertIn('"database_id": 1', raw_text)
        self.assertIn('"display_name": "Movies"', raw_text)

    # --- FR-032C: delete() / delete_for_guild() for reset/import ---------------

    def test_delete_removes_a_single_record(self) -> None:
        self.repository.save(self._config(guild_id=100, database_id=1))

        removed = self.repository.delete(100, 1)

        self.assertTrue(removed)
        self.assertIsNone(self.repository.get(100, 1))

    def test_delete_returns_false_when_nothing_to_remove(self) -> None:
        self.assertFalse(self.repository.delete(100, 999))

    def test_delete_for_guild_removes_only_that_guilds_records(self) -> None:
        self.repository.save(self._config(guild_id=100, database_id=1))
        self.repository.save(self._config(guild_id=100, database_id=2))
        self.repository.save(self._config(guild_id=200, database_id=1))

        removed_count = self.repository.delete_for_guild(100)

        self.assertEqual(2, removed_count)
        self.assertEqual([], self.repository.list_for_guild(100))
        self.assertEqual(1, len(self.repository.list_for_guild(200)))

    def test_delete_for_guild_returns_zero_when_nothing_to_remove(self) -> None:
        self.assertEqual(0, self.repository.delete_for_guild(999))


if __name__ == "__main__":
    unittest.main()
