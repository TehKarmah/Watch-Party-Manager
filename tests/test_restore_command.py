"""Tests for FR-032B's /restore, /database_backup, and /database_restore
wiring in bot.py (handle_restore/handle_database_backup/handle_database_restore).

Mirrors test_membership_command.py's FakeInteraction pattern, extended
with a FakeResponse.defer() and a FakeAttachment that actually writes
bytes to disk (so the real BackupService can validate what gets
"uploaded"), since these three handlers are the only ones in the
project that need to download a Discord attachment before responding.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from watch_party_manager.bot import (
    handle_database_backup,
    handle_database_restore,
    handle_restore,
)
from watch_party_manager.domain.suggestion_database import SuggestionDatabase
from watch_party_manager.domain.watch_item import MediaType, WatchItem
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.backup_service import BackupKind, BackupScheduleSettings, BackupService
from watch_party_manager.services.database_backup_service import create_database_backup

GUILD_ID = 100
WASH_CREW_ROLE_ID = 999


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, user_id: int = 1, roles=()) -> None:
        self.id = user_id
        self.roles = list(roles)


class FakeAttachment:
    def __init__(self, filename: str, source_path: Path) -> None:
        self.filename = filename
        self._source_path = source_path

    async def save(self, destination: Path) -> None:
        destination.write_bytes(self._source_path.read_bytes())


class FakeFollowup:
    def __init__(self) -> None:
        self.sent = []

    async def send(self, content, ephemeral=False, view=None) -> None:
        self.sent.append((content, ephemeral, view))


class FakeResponse:
    def __init__(self) -> None:
        self.deferred = False
        self.sent_message = None
        self.sent_ephemeral = None
        self.sent_view = None
        self.sent_file = None

    async def defer(self, ephemeral=False, thinking=False) -> None:
        self.deferred = True

    async def send_message(self, content, ephemeral=False, view=None, file=None) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral
        self.sent_view = view
        self.sent_file = file


class FakeInteraction:
    def __init__(self, user=None, guild_id=GUILD_ID) -> None:
        self.user = user if user is not None else FakeMember(1)
        self.guild_id = guild_id
        self.response = FakeResponse()
        self.followup = FakeFollowup()


class FakeBot:
    def __init__(self, backup_service, database_repository, suggestion_repository, configuration_repository, wash_crew_role_id=WASH_CREW_ROLE_ID) -> None:
        self.backup_service = backup_service
        self.suggestion_database_repository = database_repository
        self.suggestion_repository = suggestion_repository
        self.suggestion_database_configuration_repository = configuration_repository
        self.wash_crew_role_id = wash_crew_role_id


class RestoreCommandTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self._temp_dir.name)
        self.data_directory = self.root / "data"
        self.backup_directory = self.data_directory / "backups"
        self.data_directory.mkdir(parents=True, exist_ok=True)
        (self.data_directory / "suggestions.json").write_text('{"suggestions": []}', encoding="utf-8")
        self.backup_service = BackupService(
            self.data_directory, self.backup_directory, settings=BackupScheduleSettings()
        )
        self.database_repository = JsonSuggestionDatabaseRepository(self.data_directory / "suggestion_databases.json")
        self.suggestion_repository = JsonSuggestionRepository(self.data_directory / "suggestions.json")
        self.configuration_repository = SuggestionDatabaseConfigurationRepository(
            self.data_directory / "suggestion_database_configurations.json"
        )
        self.bot = FakeBot(
            self.backup_service, self.database_repository, self.suggestion_repository, self.configuration_repository
        )

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _wash_crew_member(self) -> FakeMember:
        return FakeMember(1, roles=[FakeRole(WASH_CREW_ROLE_ID)])


class HandleRestoreTests(RestoreCommandTestCase):
    async def test_requires_exactly_one_of_filename_or_upload(self) -> None:
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_restore(interaction, self.bot, None, None)

        self.assertIn("exactly one", interaction.response.sent_message)
        self.assertFalse(interaction.response.deferred)

    async def test_non_wash_crew_is_rejected_before_deferring(self) -> None:
        interaction = FakeInteraction(user=FakeMember(1, roles=[]))

        await handle_restore(interaction, self.bot, "wash-manual-x.zip", None)

        self.assertIn("WASH Crew", interaction.response.sent_message)
        self.assertFalse(interaction.response.deferred)

    async def test_selecting_an_unknown_local_filename_reports_available_backups(self) -> None:
        self.backup_service.create_backup(BackupKind.MANUAL)
        real_backup = self.backup_service.list_backups()[0].name
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_restore(interaction, self.bot, "does-not-exist.zip", None)

        self.assertTrue(interaction.response.deferred)
        content, ephemeral, view = interaction.followup.sent[0]
        self.assertIn(real_backup, content)

    async def test_selecting_a_valid_local_backup_shows_a_confirmation_view(self) -> None:
        self.backup_service.create_backup(BackupKind.MANUAL)
        real_backup = self.backup_service.list_backups()[0].name
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_restore(interaction, self.bot, real_backup, None)

        content, ephemeral, view = interaction.followup.sent[0]
        self.assertIn("Restore Summary", content)
        self.assertIsNotNone(view)

    async def test_uploading_a_non_zip_file_is_rejected(self) -> None:
        source = self.root / "not-a-zip.txt"
        source.write_text("hello", encoding="utf-8")
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_restore(interaction, self.bot, None, FakeAttachment("not-a-zip.txt", source))

        content, ephemeral, view = interaction.followup.sent[0]
        self.assertIn(".zip", content)

    async def test_uploading_a_valid_backup_shows_a_confirmation_view(self) -> None:
        result = self.backup_service.create_backup(BackupKind.MANUAL)
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_restore(interaction, self.bot, None, FakeAttachment("upload.zip", result.archive_path))

        content, ephemeral, view = interaction.followup.sent[0]
        self.assertIn("Restore Summary", content)
        self.assertIsNotNone(view)

    async def test_confirming_an_uploaded_backup_restores_it(self) -> None:
        (self.data_directory / "suggestions.json").write_text('{"suggestions": [{"title": "Original"}]}', encoding="utf-8")
        result = self.backup_service.create_backup(BackupKind.MANUAL)
        (self.data_directory / "suggestions.json").write_text('{"suggestions": []}', encoding="utf-8")
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_restore(interaction, self.bot, None, FakeAttachment("upload.zip", result.archive_path))

        _, _, view = interaction.followup.sent[0]
        confirm_button = view.children[0]
        confirm_interaction = FakeInteraction(user=self._wash_crew_member())

        await confirm_button.callback(interaction=confirm_interaction)

        self.assertIn("Restored", confirm_interaction.response.sent_message)
        restored = (self.data_directory / "suggestions.json").read_text(encoding="utf-8")
        self.assertIn("Original", restored)

    async def test_cancelling_leaves_data_untouched(self) -> None:
        (self.data_directory / "suggestions.json").write_text('{"suggestions": [{"title": "Current"}]}', encoding="utf-8")
        self.backup_service.create_backup(BackupKind.MANUAL)
        real_backup = self.backup_service.list_backups()[0].name
        interaction = FakeInteraction(user=self._wash_crew_member())
        await handle_restore(interaction, self.bot, real_backup, None)
        _, _, view = interaction.followup.sent[0]
        cancel_button = view.children[1]
        cancel_interaction = FakeInteraction(user=self._wash_crew_member())

        await cancel_button.callback(interaction=cancel_interaction)

        self.assertIn("cancelled", cancel_interaction.response.sent_message.lower())
        content = (self.data_directory / "suggestions.json").read_text(encoding="utf-8")
        self.assertIn("Current", content)


class HandleDatabaseBackupTests(RestoreCommandTestCase):
    def _seed_database(self) -> SuggestionDatabase:
        database = SuggestionDatabase(database_id=1, name="Movie Night", guild_id=GUILD_ID, channel_id=555)
        self.database_repository.save([database], next_id=2)
        return database

    async def test_creates_and_attaches_a_scoped_backup(self) -> None:
        self._seed_database()
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_database_backup(interaction, self.bot, 1)

        self.assertIsNotNone(interaction.response.sent_file)
        self.assertIn("Movie Night", interaction.response.sent_message)
        self.assertTrue(interaction.response.sent_ephemeral)

    async def test_rejects_an_unknown_database(self) -> None:
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_database_backup(interaction, self.bot, 999)

        self.assertIsNone(interaction.response.sent_file)
        self.assertIn("No suggestion database", interaction.response.sent_message)

    async def test_non_wash_crew_is_rejected(self) -> None:
        self._seed_database()
        interaction = FakeInteraction(user=FakeMember(1, roles=[]))

        await handle_database_backup(interaction, self.bot, 1)

        self.assertIn("WASH Crew", interaction.response.sent_message)
        self.assertIsNone(interaction.response.sent_file)


class HandleDatabaseRestoreTests(RestoreCommandTestCase):
    def _seed_and_back_up(self) -> Path:
        database = SuggestionDatabase(database_id=1, name="Movie Night", guild_id=GUILD_ID, channel_id=555)
        self.database_repository.save([database], next_id=2)
        self.suggestion_repository.save(
            [WatchItem(title="Alien", media_type=MediaType.MOVIE, database_id=1, guild_id=GUILD_ID)], next_id=2
        )
        result = create_database_backup(
            self.backup_service,
            self.database_repository,
            self.suggestion_repository,
            self.configuration_repository,
            guild_id=GUILD_ID,
            database_id=1,
        )
        return result.creation.archive_path

    async def test_invalid_mode_is_rejected(self) -> None:
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_database_restore(interaction, self.bot, "not-a-mode", "some-file.zip", None)

        self.assertIn("Merge or Replace", interaction.response.sent_message)

    async def test_replace_via_upload_shows_confirmation_then_restores(self) -> None:
        archive_path = self._seed_and_back_up()
        # Simulate the database having been altered since the backup.
        self.database_repository.save([], next_id=2)
        self.suggestion_repository.save([], next_id=1)
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_database_restore(
            interaction, self.bot, "replace", None, FakeAttachment("db-backup.zip", archive_path)
        )

        content, ephemeral, view = interaction.followup.sent[0]
        self.assertIn("Replace this suggestion database", content)
        self.assertIsNotNone(view)

        confirm_interaction = FakeInteraction(user=self._wash_crew_member())
        await view.children[0].callback(interaction=confirm_interaction)

        self.assertIn("replaced", confirm_interaction.response.sent_message.lower())
        titles = {item.title for item in self.suggestion_repository.load().watch_items}
        self.assertIn("Alien", titles)

    async def test_rejects_a_full_backup_upload(self) -> None:
        (self.data_directory / "suggestions.json").write_text('{"suggestions": []}', encoding="utf-8")
        full_backup = self.backup_service.create_backup(BackupKind.MANUAL)
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_database_restore(
            interaction, self.bot, "merge", None, FakeAttachment("full.zip", full_backup.archive_path)
        )

        content, ephemeral, view = interaction.followup.sent[0]
        self.assertIn("Unsupported backup type", content)
        self.assertIsNone(view)

    async def test_non_wash_crew_is_rejected(self) -> None:
        interaction = FakeInteraction(user=FakeMember(1, roles=[]))

        await handle_database_restore(interaction, self.bot, "merge", "some-file.zip", None)

        self.assertIn("WASH Crew", interaction.response.sent_message)


if __name__ == "__main__":
    unittest.main()
