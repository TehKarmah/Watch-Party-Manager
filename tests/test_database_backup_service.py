"""Tests for FR-032B's single suggestion database backup/restore service."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from watch_party_manager.domain.suggestion_database import SuggestionDatabase
from watch_party_manager.domain.watch_item import MediaType, WatchItem
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.backup_service import BackupScheduleSettings, BackupService, BackupType
from watch_party_manager.services.database_backup_service import (
    DatabaseRestoreMode,
    create_database_backup,
    restore_database_backup,
    sanitize_database_name_for_filename,
)

GUILD_ID = 100
OTHER_GUILD_ID = 200
CREATED_AT = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)


class DatabaseBackupServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self._temp_dir.name)
        self.data_directory = self.root / "data"
        self.backup_directory = self.data_directory / "backups"
        self.backup_service = BackupService(
            self.data_directory, self.backup_directory, settings=BackupScheduleSettings()
        )
        self.database_repository = JsonSuggestionDatabaseRepository(self.data_directory / "suggestion_databases.json")
        self.suggestion_repository = JsonSuggestionRepository(self.data_directory / "suggestions.json")
        self.configuration_repository = SuggestionDatabaseConfigurationRepository(
            self.data_directory / "suggestion_database_configurations.json"
        )

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _seed_database(self, database_id=1, guild_id=GUILD_ID, name="Movie Night", channel_id=555) -> SuggestionDatabase:
        # save() overwrites the whole file, so accumulate with whatever
        # is already there rather than clobbering earlier seeded databases.
        database = SuggestionDatabase(
            database_id=database_id, name=name, guild_id=guild_id, channel_id=channel_id, created_at=CREATED_AT
        )
        existing = [d for d in self.database_repository.load().databases if d.database_id != database_id]
        self.database_repository.save([*existing, database], next_id=database_id + 1)
        return database

    def _seed_suggestions(self, database_id=1, guild_id=GUILD_ID, titles=("Alien", "Blade Runner")) -> list[WatchItem]:
        items = [
            WatchItem(title=title, media_type=MediaType.MOVIE, database_id=database_id, guild_id=guild_id)
            for title in titles
        ]
        self.suggestion_repository.save(items, next_id=len(items) + 1)
        return self.suggestion_repository.load().watch_items


class SanitizeDatabaseNameTests(unittest.TestCase):
    def test_keeps_safe_characters_and_collapses_whitespace(self) -> None:
        self.assertEqual("Movie_Night", sanitize_database_name_for_filename("Movie   Night"))

    def test_strips_unsafe_characters(self) -> None:
        self.assertEqual("Movie_Night", sanitize_database_name_for_filename("Movie / Night!?"))

    def test_falls_back_to_database_when_nothing_safe_remains(self) -> None:
        self.assertEqual("database", sanitize_database_name_for_filename("???"))


class CreateDatabaseBackupTests(DatabaseBackupServiceTestCase):
    def test_creates_a_scoped_backup_for_the_selected_database(self) -> None:
        self._seed_database()
        self._seed_suggestions()

        result = create_database_backup(
            self.backup_service,
            self.database_repository,
            self.suggestion_repository,
            self.configuration_repository,
            guild_id=GUILD_ID,
            database_id=1,
            created_at=CREATED_AT,
        )

        self.assertTrue(result.success)
        self.assertEqual(BackupType.SUGGESTION_DATABASE, result.creation.manifest.backup_type)
        self.assertEqual(1, result.creation.manifest.database_id)
        self.assertEqual("Movie Night", result.creation.manifest.database_name)
        self.assertEqual(GUILD_ID, result.creation.manifest.guild_id)

    def test_display_filename_uses_required_format(self) -> None:
        self._seed_database(name="Sunday Watch Party")
        self._seed_suggestions()

        result = create_database_backup(
            self.backup_service,
            self.database_repository,
            self.suggestion_repository,
            self.configuration_repository,
            guild_id=GUILD_ID,
            database_id=1,
            created_at=CREATED_AT,
        )

        self.assertEqual(
            "Watch_Party_Manager_Database_Backup_Sunday_Watch_Party_2026-07-22_12-00-00.zip",
            result.display_filename,
        )

    def test_rejects_an_unknown_database_id(self) -> None:
        result = create_database_backup(
            self.backup_service,
            self.database_repository,
            self.suggestion_repository,
            self.configuration_repository,
            guild_id=GUILD_ID,
            database_id=999,
        )

        self.assertFalse(result.success)
        self.assertIn("No suggestion database", result.message)

    def test_excludes_suggestions_from_other_databases(self) -> None:
        self._seed_database(database_id=1, name="A")
        self._seed_database(database_id=2, name="B")
        self.suggestion_repository.save(
            [
                WatchItem(title="Belongs to A", media_type=MediaType.MOVIE, database_id=1, guild_id=GUILD_ID),
                WatchItem(title="Belongs to B", media_type=MediaType.MOVIE, database_id=2, guild_id=GUILD_ID),
            ],
            next_id=3,
        )

        result = create_database_backup(
            self.backup_service,
            self.database_repository,
            self.suggestion_repository,
            self.configuration_repository,
            guild_id=GUILD_ID,
            database_id=1,
            created_at=CREATED_AT,
        )

        import zipfile
        import json

        with zipfile.ZipFile(result.creation.archive_path) as archive:
            payload = json.loads(archive.read("suggestions.json"))
        titles = [entry["title"] for entry in payload["suggestions"]]
        self.assertEqual(["Belongs to A"], titles)


class RestoreDatabaseBackupReplaceTests(DatabaseBackupServiceTestCase):
    def _create_backup_and_wipe(self) -> Path:
        self._seed_database(name="Movie Night")
        self._seed_suggestions(titles=("Alien", "Blade Runner"))
        result = create_database_backup(
            self.backup_service,
            self.database_repository,
            self.suggestion_repository,
            self.configuration_repository,
            guild_id=GUILD_ID,
            database_id=1,
            created_at=CREATED_AT,
        )
        # Simulate the database having since been altered/removed.
        self.database_repository.save([], next_id=2)
        self.suggestion_repository.save([], next_id=1)
        return result.creation.archive_path

    def test_replace_recreates_the_database_and_its_suggestions(self) -> None:
        archive_path = self._create_backup_and_wipe()

        result = restore_database_backup(
            self.backup_service,
            self.database_repository,
            self.suggestion_repository,
            self.configuration_repository,
            archive_path,
            guild_id=GUILD_ID,
            mode=DatabaseRestoreMode.REPLACE,
        )

        self.assertTrue(result.success)
        self.assertEqual(2, result.imported_count)
        databases = self.database_repository.load().databases
        self.assertEqual(["Movie Night"], [d.name for d in databases])
        titles = {item.title for item in self.suggestion_repository.load().watch_items}
        self.assertEqual({"Alien", "Blade Runner"}, titles)

    def test_replace_preserves_unrelated_databases(self) -> None:
        archive_path = self._create_backup_and_wipe()
        self._seed_database(database_id=2, name="Other DB", channel_id=777)
        self.suggestion_repository.save(
            [WatchItem(title="Untouched", media_type=MediaType.MOVIE, database_id=2, guild_id=GUILD_ID)], next_id=10
        )

        restore_database_backup(
            self.backup_service,
            self.database_repository,
            self.suggestion_repository,
            self.configuration_repository,
            archive_path,
            guild_id=GUILD_ID,
            mode=DatabaseRestoreMode.REPLACE,
        )

        databases = {d.name for d in self.database_repository.load().databases}
        self.assertEqual({"Movie Night", "Other DB"}, databases)
        titles = {item.title for item in self.suggestion_repository.load().watch_items}
        self.assertIn("Untouched", titles)

    def test_replace_creates_a_safety_backup(self) -> None:
        archive_path = self._create_backup_and_wipe()

        result = restore_database_backup(
            self.backup_service,
            self.database_repository,
            self.suggestion_repository,
            self.configuration_repository,
            archive_path,
            guild_id=GUILD_ID,
            mode=DatabaseRestoreMode.REPLACE,
        )

        self.assertIsNotNone(result.safety_backup)
        self.assertTrue(result.safety_backup.is_file())

    def test_rejects_a_full_backup_type(self) -> None:
        self.data_directory.mkdir(parents=True, exist_ok=True)
        (self.data_directory / "suggestions.json").write_text("{}", encoding="utf-8")
        full_backup = self.backup_service.create_backup(created_at=CREATED_AT)

        result = restore_database_backup(
            self.backup_service,
            self.database_repository,
            self.suggestion_repository,
            self.configuration_repository,
            full_backup.archive_path,
            guild_id=GUILD_ID,
            mode=DatabaseRestoreMode.REPLACE,
        )

        self.assertFalse(result.success)
        self.assertIn("not a suggestion database backup", result.message)

    def test_rejects_a_backup_from_a_different_guild(self) -> None:
        self._seed_database(guild_id=OTHER_GUILD_ID, name="Foreign DB")
        self._seed_suggestions(guild_id=OTHER_GUILD_ID)
        result = create_database_backup(
            self.backup_service,
            self.database_repository,
            self.suggestion_repository,
            self.configuration_repository,
            guild_id=OTHER_GUILD_ID,
            database_id=1,
            created_at=CREATED_AT,
        )

        restore_result = restore_database_backup(
            self.backup_service,
            self.database_repository,
            self.suggestion_repository,
            self.configuration_repository,
            result.creation.archive_path,
            guild_id=GUILD_ID,
            mode=DatabaseRestoreMode.REPLACE,
        )

        self.assertFalse(restore_result.success)
        self.assertIn("different Discord server", restore_result.message)


class RestoreDatabaseBackupMergeTests(DatabaseBackupServiceTestCase):
    def test_merge_imports_new_suggestions_into_the_existing_database(self) -> None:
        self._seed_database(name="Movie Night")
        self._seed_suggestions(titles=("Alien",))
        backup = create_database_backup(
            self.backup_service,
            self.database_repository,
            self.suggestion_repository,
            self.configuration_repository,
            guild_id=GUILD_ID,
            database_id=1,
            created_at=CREATED_AT,
        )
        # Someone adds a second suggestion after the backup was taken.
        existing = self.suggestion_repository.load()
        self.suggestion_repository.save(
            [*existing.watch_items, WatchItem(title="New Since Backup", media_type=MediaType.MOVIE, database_id=1, guild_id=GUILD_ID)],
            next_id=existing.next_id + 1,
        )

        result = restore_database_backup(
            self.backup_service,
            self.database_repository,
            self.suggestion_repository,
            self.configuration_repository,
            backup.creation.archive_path,
            guild_id=GUILD_ID,
            mode=DatabaseRestoreMode.MERGE,
        )

        self.assertTrue(result.success)
        titles = {item.title for item in self.suggestion_repository.load().watch_items}
        self.assertEqual({"Alien", "New Since Backup"}, titles)

    def test_merge_detects_and_skips_title_conflicts(self) -> None:
        self._seed_database(name="Movie Night")
        self._seed_suggestions(titles=("Alien", "Blade Runner"))
        backup = create_database_backup(
            self.backup_service,
            self.database_repository,
            self.suggestion_repository,
            self.configuration_repository,
            guild_id=GUILD_ID,
            database_id=1,
            created_at=CREATED_AT,
        )

        result = restore_database_backup(
            self.backup_service,
            self.database_repository,
            self.suggestion_repository,
            self.configuration_repository,
            backup.creation.archive_path,
            guild_id=GUILD_ID,
            mode=DatabaseRestoreMode.MERGE,
        )

        self.assertTrue(result.success)
        self.assertEqual(0, result.imported_count)
        self.assertEqual({"Alien", "Blade Runner"}, set(result.conflict_titles))
        # Nothing was duplicated.
        titles = [item.title for item in self.suggestion_repository.load().watch_items]
        self.assertEqual(2, len(titles))

    def test_merge_preserves_unrelated_databases(self) -> None:
        self._seed_database(database_id=1, name="Movie Night")
        self._seed_suggestions(database_id=1, titles=("Alien",))
        backup = create_database_backup(
            self.backup_service,
            self.database_repository,
            self.suggestion_repository,
            self.configuration_repository,
            guild_id=GUILD_ID,
            database_id=1,
            created_at=CREATED_AT,
        )
        self._seed_database(database_id=2, name="Other DB", channel_id=777)
        existing = self.suggestion_repository.load()
        self.suggestion_repository.save(
            [
                *existing.watch_items,
                WatchItem(title="Untouched", media_type=MediaType.MOVIE, database_id=2, guild_id=GUILD_ID),
            ],
            next_id=existing.next_id + 1,
        )

        restore_database_backup(
            self.backup_service,
            self.database_repository,
            self.suggestion_repository,
            self.configuration_repository,
            backup.creation.archive_path,
            guild_id=GUILD_ID,
            mode=DatabaseRestoreMode.MERGE,
        )

        titles = {item.title for item in self.suggestion_repository.load().watch_items if item.database_id == 2}
        self.assertIn("Untouched", titles)

    def test_merge_rejects_when_destination_database_does_not_exist(self) -> None:
        self._seed_database(name="Movie Night")
        self._seed_suggestions()
        backup = create_database_backup(
            self.backup_service,
            self.database_repository,
            self.suggestion_repository,
            self.configuration_repository,
            guild_id=GUILD_ID,
            database_id=1,
            created_at=CREATED_AT,
        )
        # Now remove the destination database entirely.
        self.database_repository.save([], next_id=2)

        result = restore_database_backup(
            self.backup_service,
            self.database_repository,
            self.suggestion_repository,
            self.configuration_repository,
            backup.creation.archive_path,
            guild_id=GUILD_ID,
            mode=DatabaseRestoreMode.MERGE,
        )

        self.assertFalse(result.success)
        self.assertIn("No existing suggestion database", result.message)

    def test_merge_reassigns_a_colliding_suggestion_id(self) -> None:
        self._seed_database(name="Movie Night")
        items = self._seed_suggestions(titles=("Alien",))
        backup = create_database_backup(
            self.backup_service,
            self.database_repository,
            self.suggestion_repository,
            self.configuration_repository,
            guild_id=GUILD_ID,
            database_id=1,
            created_at=CREATED_AT,
        )
        # Free up the backup's suggestion ID by reassigning it to an
        # unrelated suggestion in a different database, forcing the
        # merge to detect the collision and mint a fresh ID instead.
        colliding_id = items[0].id
        self._seed_database(database_id=2, name="Other DB", channel_id=888)
        self.suggestion_repository.save(
            [
                WatchItem(
                    id=colliding_id, title="Unrelated", media_type=MediaType.MOVIE, database_id=2, guild_id=GUILD_ID
                )
            ],
            next_id=colliding_id + 1,
        )

        result = restore_database_backup(
            self.backup_service,
            self.database_repository,
            self.suggestion_repository,
            self.configuration_repository,
            backup.creation.archive_path,
            guild_id=GUILD_ID,
            mode=DatabaseRestoreMode.MERGE,
        )

        self.assertTrue(result.success)
        self.assertEqual(1, result.imported_count)
        all_items = self.suggestion_repository.load().watch_items
        unrelated = next(item for item in all_items if item.title == "Unrelated")
        imported = next(item for item in all_items if item.title == "Alien")
        self.assertNotEqual(unrelated.id, imported.id)


if __name__ == "__main__":
    unittest.main()
