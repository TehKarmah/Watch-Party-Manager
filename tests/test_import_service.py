"""Tests for FR-032C's import-from-another-WASH-instance service."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from watch_party_manager.domain.guild_configuration import GuildConfiguration, GuildChannelsConfig, WatchPartyRoleConfig
from watch_party_manager.domain.suggestion_database import SuggestionDatabase
from watch_party_manager.domain.watch_item import MediaType, WatchItem
from watch_party_manager.persistence.guild_configuration_repository import GuildConfigurationRepository
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.services.backup_service import BackupKind, BackupScheduleSettings, BackupService, BackupType
from watch_party_manager.services.import_service import (
    ImportMode,
    build_import_summary,
    import_backup,
)

SOURCE_GUILD_ID = 555
DEST_GUILD_ID = 100
CREATED_AT = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)


class ImportServiceTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self._temp_dir.name)

        self.source_data_directory = self.root / "source_data"
        self.source_backup_service = BackupService(
            self.source_data_directory,
            self.source_data_directory / "backups",
            settings=BackupScheduleSettings(),
        )
        self.source_database_repository = JsonSuggestionDatabaseRepository(
            self.source_data_directory / "suggestion_databases.json"
        )
        self.source_suggestion_repository = JsonSuggestionRepository(self.source_data_directory / "suggestions.json")
        self.source_configuration_repository = SuggestionDatabaseConfigurationRepository(
            self.source_data_directory / "suggestion_database_configurations.json"
        )
        self.source_vote_repository = JsonVoteRepository(self.source_data_directory / "voting.json")

        self.dest_data_directory = self.root / "dest_data"
        self.dest_backup_service = BackupService(
            self.dest_data_directory,
            self.dest_data_directory / "backups",
            settings=BackupScheduleSettings(),
        )
        self.dest_database_repository = JsonSuggestionDatabaseRepository(
            self.dest_data_directory / "suggestion_databases.json"
        )
        self.dest_suggestion_repository = JsonSuggestionRepository(self.dest_data_directory / "suggestions.json")
        self.dest_configuration_repository = SuggestionDatabaseConfigurationRepository(
            self.dest_data_directory / "suggestion_database_configurations.json"
        )
        self.dest_vote_repository = JsonVoteRepository(self.dest_data_directory / "voting.json")
        self.dest_guild_configuration_repository = GuildConfigurationRepository(
            self.dest_data_directory / "guild_configurations.json"
        )

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _seed_source_database(self, database_id=1, name="Movie Night", channel_id=555) -> SuggestionDatabase:
        database = SuggestionDatabase(
            database_id=database_id, name=name, guild_id=SOURCE_GUILD_ID, channel_id=channel_id, created_at=CREATED_AT
        )
        existing = [d for d in self.source_database_repository.load().databases if d.database_id != database_id]
        self.source_database_repository.save([*existing, database], next_id=database_id + 1)
        return database

    def _create_source_backup(self) -> Path:
        return self.source_backup_service.create_backup(guild_id=SOURCE_GUILD_ID, created_at=CREATED_AT).archive_path

    def _seed_dest_database(self, database_id=1, name="Movie Night", channel_id=777) -> SuggestionDatabase:
        database = SuggestionDatabase(
            database_id=database_id, name=name, guild_id=DEST_GUILD_ID, channel_id=channel_id, created_at=CREATED_AT
        )
        existing = [d for d in self.dest_database_repository.load().databases if d.database_id != database_id]
        self.dest_database_repository.save([*existing, database], next_id=database_id + 1)
        return database


class BuildImportSummaryTests(ImportServiceTestCase):
    async def test_valid_full_backup_summarizes_successfully(self) -> None:
        self._seed_source_database()
        self.source_suggestion_repository.save(
            [WatchItem(title="Alien", media_type=MediaType.MOVIE, database_id=1, guild_id=SOURCE_GUILD_ID)], next_id=2
        )
        archive_path = self._create_source_backup()

        summary = build_import_summary(self.dest_backup_service, archive_path)

        self.assertTrue(summary.is_valid)
        self.assertEqual(SOURCE_GUILD_ID, summary.guild_id)
        self.assertEqual(1, summary.suggestion_count)
        self.assertEqual(1, summary.suggestion_database_count)

    async def test_invalid_zip_is_rejected(self) -> None:
        bad_archive = self.root / "bad.zip"
        bad_archive.write_bytes(b"not a zip")

        summary = build_import_summary(self.dest_backup_service, bad_archive)

        self.assertFalse(summary.is_valid)

    async def test_rejects_a_suggestion_database_scoped_backup(self) -> None:
        result = self.source_backup_service.create_scoped_backup(
            {"suggestion_databases.json": b'{"databases": []}'},
            kind=BackupKind.MANUAL,
            backup_type=BackupType.SUGGESTION_DATABASE,
        )

        summary = build_import_summary(self.dest_backup_service, result.archive_path)

        self.assertFalse(summary.is_valid)
        self.assertTrue(any("Unsupported backup type" in error for error in summary.errors))


class ImportBackupValidationTests(ImportServiceTestCase):
    async def test_rejects_an_invalid_archive_without_creating_a_safety_backup(self) -> None:
        bad_archive = self.root / "bad.zip"
        bad_archive.write_bytes(b"not a zip")

        result = await import_backup(
            self.dest_backup_service,
            self.dest_database_repository,
            self.dest_suggestion_repository,
            self.dest_configuration_repository,
            self.dest_vote_repository,
            bad_archive,
            DEST_GUILD_ID,
            ImportMode.MERGE,
        )

        self.assertFalse(result.success)
        self.assertIn("validation failed", result.message.lower())
        self.assertEqual((), self.dest_backup_service.list_backups())


class MergeImportTests(ImportServiceTestCase):
    async def test_imports_a_new_database_and_its_suggestions(self) -> None:
        self._seed_source_database(name="Movie Night")
        self.source_suggestion_repository.save(
            [WatchItem(title="Alien", media_type=MediaType.MOVIE, database_id=1, guild_id=SOURCE_GUILD_ID)], next_id=2
        )
        archive_path = self._create_source_backup()

        result = await import_backup(
            self.dest_backup_service,
            self.dest_database_repository,
            self.dest_suggestion_repository,
            self.dest_configuration_repository,
            self.dest_vote_repository,
            archive_path,
            DEST_GUILD_ID,
            ImportMode.MERGE,
        )

        self.assertTrue(result.success)
        self.assertEqual(1, result.databases_imported)
        self.assertEqual(1, result.suggestions_imported)
        databases = self.dest_database_repository.load().databases
        self.assertEqual(["Movie Night"], [d.name for d in databases])
        self.assertEqual(DEST_GUILD_ID, databases[0].guild_id)
        suggestions = self.dest_suggestion_repository.load().watch_items
        self.assertEqual(["Alien"], [item.title for item in suggestions])
        self.assertEqual(DEST_GUILD_ID, suggestions[0].guild_id)

    async def test_matching_database_name_merges_suggestions_instead_of_creating_a_duplicate(self) -> None:
        self._seed_dest_database(database_id=9, name="Movie Night", channel_id=777)
        self._seed_source_database(database_id=1, name="Movie Night")
        self.source_suggestion_repository.save(
            [WatchItem(title="Alien", media_type=MediaType.MOVIE, database_id=1, guild_id=SOURCE_GUILD_ID)], next_id=2
        )
        archive_path = self._create_source_backup()

        result = await import_backup(
            self.dest_backup_service,
            self.dest_database_repository,
            self.dest_suggestion_repository,
            self.dest_configuration_repository,
            self.dest_vote_repository,
            archive_path,
            DEST_GUILD_ID,
            ImportMode.MERGE,
        )

        self.assertTrue(result.success)
        self.assertEqual(0, result.databases_imported)
        self.assertEqual(1, result.databases_skipped)
        databases = self.dest_database_repository.load().databases
        self.assertEqual(1, len(databases))  # no duplicate database created
        imported_suggestion = next(item for item in self.dest_suggestion_repository.load().watch_items if item.title == "Alien")
        self.assertEqual(9, imported_suggestion.database_id)

    async def test_conflicting_suggestion_titles_are_skipped_not_overwritten(self) -> None:
        self._seed_dest_database(database_id=9, name="Movie Night", channel_id=777)
        self.dest_suggestion_repository.save(
            [WatchItem(title="Alien", media_type=MediaType.MOVIE, database_id=9, guild_id=DEST_GUILD_ID)], next_id=2
        )
        self._seed_source_database(database_id=1, name="Movie Night")
        self.source_suggestion_repository.save(
            [WatchItem(title="Alien", media_type=MediaType.MOVIE, database_id=1, guild_id=SOURCE_GUILD_ID)], next_id=2
        )
        archive_path = self._create_source_backup()

        result = await import_backup(
            self.dest_backup_service,
            self.dest_database_repository,
            self.dest_suggestion_repository,
            self.dest_configuration_repository,
            self.dest_vote_repository,
            archive_path,
            DEST_GUILD_ID,
            ImportMode.MERGE,
        )

        self.assertTrue(result.success)
        self.assertEqual(0, result.suggestions_imported)
        self.assertEqual(1, result.suggestions_skipped)
        self.assertIn("Alien", result.conflict_titles)
        titles = [item.title for item in self.dest_suggestion_repository.load().watch_items]
        self.assertEqual(1, titles.count("Alien"))  # not duplicated

    async def test_preserves_unrelated_local_databases(self) -> None:
        self._seed_dest_database(database_id=9, name="Unrelated", channel_id=777)
        self._seed_source_database(database_id=1, name="Movie Night")
        archive_path = self._create_source_backup()

        await import_backup(
            self.dest_backup_service,
            self.dest_database_repository,
            self.dest_suggestion_repository,
            self.dest_configuration_repository,
            self.dest_vote_repository,
            archive_path,
            DEST_GUILD_ID,
            ImportMode.MERGE,
        )

        names = {d.name for d in self.dest_database_repository.load().databases}
        self.assertEqual({"Unrelated", "Movie Night"}, names)

    async def test_preserves_current_guild_configuration(self) -> None:
        self.dest_guild_configuration_repository.save(
            GuildConfiguration(
                guild_id=DEST_GUILD_ID,
                guild_name="Dest Guild",
                channels=GuildChannelsConfig(admin_channel_id=42),
                watch_party_role=WatchPartyRoleConfig(role_id=99),
            )
        )
        self._seed_source_database()
        archive_path = self._create_source_backup()

        await import_backup(
            self.dest_backup_service,
            self.dest_database_repository,
            self.dest_suggestion_repository,
            self.dest_configuration_repository,
            self.dest_vote_repository,
            archive_path,
            DEST_GUILD_ID,
            ImportMode.MERGE,
        )

        configuration = self.dest_guild_configuration_repository.get(DEST_GUILD_ID)
        self.assertEqual(42, configuration.channels.admin_channel_id)
        self.assertEqual(99, configuration.watch_party_role.role_id)

    async def test_reassigns_a_colliding_database_id(self) -> None:
        self._seed_dest_database(database_id=1, name="Something Else", channel_id=777)
        self._seed_source_database(database_id=1, name="Movie Night")  # same numeric ID, different guild/name
        archive_path = self._create_source_backup()

        result = await import_backup(
            self.dest_backup_service,
            self.dest_database_repository,
            self.dest_suggestion_repository,
            self.dest_configuration_repository,
            self.dest_vote_repository,
            archive_path,
            DEST_GUILD_ID,
            ImportMode.MERGE,
        )

        self.assertTrue(result.success)
        self.assertGreaterEqual(result.ids_reassigned, 1)
        ids = [d.database_id for d in self.dest_database_repository.load().databases]
        self.assertEqual(len(ids), len(set(ids)))  # no collision

    async def test_creates_a_safety_backup(self) -> None:
        self._seed_source_database()
        archive_path = self._create_source_backup()

        result = await import_backup(
            self.dest_backup_service,
            self.dest_database_repository,
            self.dest_suggestion_repository,
            self.dest_configuration_repository,
            self.dest_vote_repository,
            archive_path,
            DEST_GUILD_ID,
            ImportMode.MERGE,
        )

        self.assertIsNotNone(result.safety_backup)
        self.assertTrue(result.safety_backup.is_file())


class ReplaceImportTests(ImportServiceTestCase):
    async def test_replaces_this_guilds_portable_data(self) -> None:
        self._seed_dest_database(database_id=9, name="Old Database", channel_id=777)
        self.dest_suggestion_repository.save(
            [WatchItem(title="Old Movie", media_type=MediaType.MOVIE, database_id=9, guild_id=DEST_GUILD_ID)], next_id=10
        )
        self._seed_source_database(database_id=1, name="Movie Night")
        self.source_suggestion_repository.save(
            [WatchItem(title="Alien", media_type=MediaType.MOVIE, database_id=1, guild_id=SOURCE_GUILD_ID)], next_id=2
        )
        archive_path = self._create_source_backup()

        result = await import_backup(
            self.dest_backup_service,
            self.dest_database_repository,
            self.dest_suggestion_repository,
            self.dest_configuration_repository,
            self.dest_vote_repository,
            archive_path,
            DEST_GUILD_ID,
            ImportMode.REPLACE,
        )

        self.assertTrue(result.success)
        names = {d.name for d in self.dest_database_repository.load().databases}
        self.assertEqual({"Movie Night"}, names)
        titles = {item.title for item in self.dest_suggestion_repository.load().watch_items}
        self.assertEqual({"Alien"}, titles)

    async def test_preserves_other_guilds_data(self) -> None:
        other_guild_database = SuggestionDatabase(
            database_id=50, name="Other Guild DB", guild_id=999, channel_id=1234, created_at=CREATED_AT
        )
        self.dest_database_repository.save([other_guild_database], next_id=51)
        self._seed_source_database()
        archive_path = self._create_source_backup()

        await import_backup(
            self.dest_backup_service,
            self.dest_database_repository,
            self.dest_suggestion_repository,
            self.dest_configuration_repository,
            self.dest_vote_repository,
            archive_path,
            DEST_GUILD_ID,
            ImportMode.REPLACE,
        )

        remaining = self.dest_database_repository.load().databases
        self.assertIn(50, [d.database_id for d in remaining])

    async def test_preserves_current_guild_configuration(self) -> None:
        self.dest_guild_configuration_repository.save(
            GuildConfiguration(
                guild_id=DEST_GUILD_ID,
                guild_name="Dest Guild",
                channels=GuildChannelsConfig(admin_channel_id=42),
                watch_party_role=WatchPartyRoleConfig(role_id=99),
            )
        )
        self._seed_source_database()
        archive_path = self._create_source_backup()

        await import_backup(
            self.dest_backup_service,
            self.dest_database_repository,
            self.dest_suggestion_repository,
            self.dest_configuration_repository,
            self.dest_vote_repository,
            archive_path,
            DEST_GUILD_ID,
            ImportMode.REPLACE,
        )

        configuration = self.dest_guild_configuration_repository.get(DEST_GUILD_ID)
        self.assertEqual(42, configuration.channels.admin_channel_id)
        self.assertEqual(99, configuration.watch_party_role.role_id)

    async def test_creates_a_safety_backup(self) -> None:
        self._seed_source_database()
        archive_path = self._create_source_backup()

        result = await import_backup(
            self.dest_backup_service,
            self.dest_database_repository,
            self.dest_suggestion_repository,
            self.dest_configuration_repository,
            self.dest_vote_repository,
            archive_path,
            DEST_GUILD_ID,
            ImportMode.REPLACE,
        )

        self.assertIsNotNone(result.safety_backup)
        self.assertTrue(result.safety_backup.is_file())

    async def test_rejects_a_suggestion_database_scoped_backup(self) -> None:
        result = self.source_backup_service.create_scoped_backup(
            {"suggestion_databases.json": b'{"databases": []}'},
            kind=BackupKind.MANUAL,
            backup_type=BackupType.SUGGESTION_DATABASE,
        )

        import_result = await import_backup(
            self.dest_backup_service,
            self.dest_database_repository,
            self.dest_suggestion_repository,
            self.dest_configuration_repository,
            self.dest_vote_repository,
            result.archive_path,
            DEST_GUILD_ID,
            ImportMode.REPLACE,
        )

        self.assertFalse(import_result.success)
        self.assertIn("Unsupported backup type", import_result.message)


if __name__ == "__main__":
    unittest.main()
