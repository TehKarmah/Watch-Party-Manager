"""Tests for FR-032C's /database_reset, /factory_reset, and /import wiring in bot.py."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from watch_party_manager.bot import (
    handle_database_reset,
    handle_factory_reset,
    handle_import,
)
from watch_party_manager.domain.guild_configuration import GuildConfiguration
from watch_party_manager.domain.suggestion_database import SuggestionDatabase
from watch_party_manager.domain.watch_item import MediaType, WatchItem
from watch_party_manager.persistence.guild_configuration_repository import GuildConfigurationRepository
from watch_party_manager.persistence.membership_request_repository import MembershipRequestRepository
from watch_party_manager.persistence.setup_wizard_repository import SetupWizardRepository
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.persistence.watch_party_repository import JsonWatchPartyRepository
from watch_party_manager.scheduler.json_scheduler_repository import JsonSchedulerRepository
from watch_party_manager.services.backup_service import BackupScheduleSettings, BackupService
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
        self.sent_modal = None

    async def defer(self, ephemeral=False, thinking=False) -> None:
        self.deferred = True

    async def send_message(self, content, ephemeral=False, view=None) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral
        self.sent_view = view

    async def send_modal(self, modal) -> None:
        self.sent_modal = modal


class FakeInteraction:
    def __init__(self, user=None, guild_id=GUILD_ID) -> None:
        self.user = user if user is not None else FakeMember(1)
        self.guild_id = guild_id
        self.response = FakeResponse()
        self.followup = FakeFollowup()


class FakeBot:
    def __init__(self, *, root: Path, wash_crew_role_id=WASH_CREW_ROLE_ID) -> None:
        self.data_directory = root / "data"
        self.backup_service = BackupService(
            self.data_directory, self.data_directory / "backups", settings=BackupScheduleSettings()
        )
        self.suggestion_database_repository = JsonSuggestionDatabaseRepository(
            self.data_directory / "suggestion_databases.json"
        )
        self.suggestion_repository = JsonSuggestionRepository(self.data_directory / "suggestions.json")
        self.suggestion_database_configuration_repository = SuggestionDatabaseConfigurationRepository(
            self.data_directory / "suggestion_database_configurations.json"
        )
        self.guild_configuration_repository = GuildConfigurationRepository(
            self.data_directory / "guild_configurations.json"
        )
        self.setup_wizard_repository = SetupWizardRepository(self.data_directory / "setup_wizard_state.json")
        self.vote_repository = JsonVoteRepository(self.data_directory / "voting.json")
        self.membership_request_repository = MembershipRequestRepository(
            self.data_directory / "membership_requests.json"
        )
        self.watch_party_repository = JsonWatchPartyRepository(self.data_directory / "watch_parties.json")
        self.scheduler_repository = JsonSchedulerRepository(self.data_directory / "scheduled_jobs.json")
        self.wash_crew_role_id = wash_crew_role_id


async def _submit_modal(view, index: int, text: str):
    """Click a DestructiveConfirmationView/ImportModeChoiceView button that
    opens a modal, type `text` into it, and submit -- returns the fresh
    interaction the modal submission produced.
    """
    open_interaction = FakeInteraction(user=FakeMember(1, roles=[FakeRole(WASH_CREW_ROLE_ID)]))
    await view.children[index].callback(open_interaction)
    modal = open_interaction.response.sent_modal
    modal.confirmation_input._value = text
    submit_interaction = FakeInteraction(user=FakeMember(1, roles=[FakeRole(WASH_CREW_ROLE_ID)]))
    await modal.on_submit(submit_interaction)
    return submit_interaction


class ResetImportCommandTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self._temp_dir.name)
        self.bot = FakeBot(root=self.root)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _wash_crew_member(self) -> FakeMember:
        return FakeMember(1, roles=[FakeRole(WASH_CREW_ROLE_ID)])

    def _seed_database(self, database_id=1, name="Movie Night", channel_id=555) -> SuggestionDatabase:
        database = SuggestionDatabase(database_id=database_id, name=name, guild_id=GUILD_ID, channel_id=channel_id)
        self.bot.suggestion_database_repository.save([database], next_id=database_id + 1)
        return database


class HandleDatabaseResetTests(ResetImportCommandTestCase):
    async def test_non_wash_crew_is_rejected(self) -> None:
        self._seed_database()
        interaction = FakeInteraction(user=FakeMember(1, roles=[]))

        await handle_database_reset(interaction, self.bot, 1)

        self.assertIn("WASH Crew", interaction.response.sent_message)

    async def test_unknown_database_is_rejected(self) -> None:
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_database_reset(interaction, self.bot, 999)

        self.assertIn("No suggestion database", interaction.response.sent_message)

    async def test_shows_a_summary_and_confirmation_view(self) -> None:
        self._seed_database(name="Movie Night")
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_database_reset(interaction, self.bot, 1)

        self.assertIn("Movie Night", interaction.response.sent_message)
        self.assertIsNotNone(interaction.response.sent_view)

    async def test_typing_reset_performs_the_reset(self) -> None:
        self._seed_database(name="Movie Night")
        self.bot.suggestion_repository.save(
            [WatchItem(title="Alien", media_type=MediaType.MOVIE, database_id=1, guild_id=GUILD_ID)], next_id=2
        )
        interaction = FakeInteraction(user=self._wash_crew_member())
        await handle_database_reset(interaction, self.bot, 1)
        view = interaction.response.sent_view

        confirm_interaction = await _submit_modal(view, 0, "RESET")

        self.assertIn("reset", confirm_interaction.response.sent_message.lower())
        self.assertEqual([], self.bot.suggestion_repository.load().watch_items)

    async def test_wrong_confirmation_text_does_not_reset(self) -> None:
        self._seed_database(name="Movie Night")
        self.bot.suggestion_repository.save(
            [WatchItem(title="Alien", media_type=MediaType.MOVIE, database_id=1, guild_id=GUILD_ID)], next_id=2
        )
        interaction = FakeInteraction(user=self._wash_crew_member())
        await handle_database_reset(interaction, self.bot, 1)
        view = interaction.response.sent_view

        await _submit_modal(view, 0, "reset")  # wrong case

        self.assertEqual(1, len(self.bot.suggestion_repository.load().watch_items))

    async def test_cancel_leaves_data_unchanged(self) -> None:
        self._seed_database(name="Movie Night")
        self.bot.suggestion_repository.save(
            [WatchItem(title="Alien", media_type=MediaType.MOVIE, database_id=1, guild_id=GUILD_ID)], next_id=2
        )
        interaction = FakeInteraction(user=self._wash_crew_member())
        await handle_database_reset(interaction, self.bot, 1)
        view = interaction.response.sent_view

        cancel_interaction = FakeInteraction(user=self._wash_crew_member())
        await view.children[1].callback(cancel_interaction)

        self.assertIn("cancelled", cancel_interaction.response.sent_message.lower())
        self.assertEqual(1, len(self.bot.suggestion_repository.load().watch_items))


class HandleFactoryResetTests(ResetImportCommandTestCase):
    async def test_non_wash_crew_is_rejected(self) -> None:
        interaction = FakeInteraction(user=FakeMember(1, roles=[]))

        await handle_factory_reset(interaction, self.bot)

        self.assertIn("WASH Crew", interaction.response.sent_message)

    async def test_shows_a_summary_and_confirmation_view(self) -> None:
        self.bot.guild_configuration_repository.save(GuildConfiguration(guild_id=GUILD_ID, guild_name="Guild"))
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_factory_reset(interaction, self.bot)

        self.assertIn("Factory Reset", interaction.response.sent_message)
        self.assertIsNotNone(interaction.response.sent_view)

    async def test_typing_reset_performs_the_factory_reset(self) -> None:
        self.bot.guild_configuration_repository.save(GuildConfiguration(guild_id=GUILD_ID, guild_name="Guild"))
        self._seed_database()
        interaction = FakeInteraction(user=self._wash_crew_member())
        await handle_factory_reset(interaction, self.bot)
        view = interaction.response.sent_view

        confirm_interaction = await _submit_modal(view, 0, "RESET")

        self.assertIn("complete", confirm_interaction.response.sent_message.lower())
        self.assertIsNone(self.bot.guild_configuration_repository.get(GUILD_ID))
        self.assertEqual([], self.bot.suggestion_database_repository.load().databases)

    async def test_cancel_leaves_data_unchanged(self) -> None:
        self.bot.guild_configuration_repository.save(GuildConfiguration(guild_id=GUILD_ID, guild_name="Guild"))
        interaction = FakeInteraction(user=self._wash_crew_member())
        await handle_factory_reset(interaction, self.bot)
        view = interaction.response.sent_view

        cancel_interaction = FakeInteraction(user=self._wash_crew_member())
        await view.children[1].callback(cancel_interaction)

        self.assertIn("cancelled", cancel_interaction.response.sent_message.lower())
        self.assertIsNotNone(self.bot.guild_configuration_repository.get(GUILD_ID))


class HandleImportTests(ResetImportCommandTestCase):
    def _create_full_backup(self) -> Path:
        self._seed_database(name="Movie Night")
        self.bot.suggestion_repository.save(
            [WatchItem(title="Alien", media_type=MediaType.MOVIE, database_id=1, guild_id=GUILD_ID)], next_id=2
        )
        return self.bot.backup_service.create_backup(guild_id=GUILD_ID).archive_path

    async def test_non_wash_crew_is_rejected(self) -> None:
        interaction = FakeInteraction(user=FakeMember(1, roles=[]))

        await handle_import(interaction, self.bot, FakeAttachment("backup.zip", self.root / "nope.zip"))

        self.assertIn("WASH Crew", interaction.response.sent_message)
        self.assertFalse(interaction.response.deferred)

    async def test_non_zip_upload_is_rejected(self) -> None:
        source = self.root / "not-a-zip.txt"
        source.write_text("hello", encoding="utf-8")
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_import(interaction, self.bot, FakeAttachment("not-a-zip.txt", source))

        self.assertIn(".zip", interaction.response.sent_message)

    async def test_valid_backup_shows_mode_choice_view(self) -> None:
        archive_path = self._create_full_backup()
        # Reset the destination data so the import target starts empty.
        self.bot.suggestion_database_repository.save([], next_id=1)
        self.bot.suggestion_repository.save([], next_id=1)
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_import(interaction, self.bot, FakeAttachment("backup.zip", archive_path))

        content, ephemeral, view = interaction.followup.sent[0]
        self.assertIn("Restore Summary", content)
        self.assertIsNotNone(view)

    async def test_merge_click_imports_data(self) -> None:
        archive_path = self._create_full_backup()
        self.bot.suggestion_database_repository.save([], next_id=1)
        self.bot.suggestion_repository.save([], next_id=1)
        interaction = FakeInteraction(user=self._wash_crew_member())
        await handle_import(interaction, self.bot, FakeAttachment("backup.zip", archive_path))
        _, _, view = interaction.followup.sent[0]

        merge_interaction = FakeInteraction(user=self._wash_crew_member())
        await view.children[0].callback(merge_interaction)

        self.assertIn("Merge import complete", merge_interaction.response.sent_message)
        titles = {item.title for item in self.bot.suggestion_repository.load().watch_items}
        self.assertIn("Alien", titles)

    async def test_replace_requires_typed_confirmation(self) -> None:
        archive_path = self._create_full_backup()
        interaction = FakeInteraction(user=self._wash_crew_member())
        await handle_import(interaction, self.bot, FakeAttachment("backup.zip", archive_path))
        _, _, view = interaction.followup.sent[0]

        confirm_interaction = await _submit_modal(view, 1, "REPLACE")

        self.assertIn("Replace import complete", confirm_interaction.response.sent_message)

    async def test_cancel_does_not_import(self) -> None:
        archive_path = self._create_full_backup()
        original_databases = self.bot.suggestion_database_repository.load().databases
        interaction = FakeInteraction(user=self._wash_crew_member())
        await handle_import(interaction, self.bot, FakeAttachment("backup.zip", archive_path))
        _, _, view = interaction.followup.sent[0]

        cancel_interaction = FakeInteraction(user=self._wash_crew_member())
        await view.children[2].callback(cancel_interaction)

        self.assertIn("cancelled", cancel_interaction.response.sent_message.lower())
        self.assertEqual(original_databases, self.bot.suggestion_database_repository.load().databases)

    async def test_rejects_a_suggestion_database_scoped_backup(self) -> None:
        self._seed_database()
        result = create_database_backup(
            self.bot.backup_service,
            self.bot.suggestion_database_repository,
            self.bot.suggestion_repository,
            self.bot.suggestion_database_configuration_repository,
            guild_id=GUILD_ID,
            database_id=1,
        )
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_import(interaction, self.bot, FakeAttachment("backup.zip", result.creation.archive_path))

        content, ephemeral, view = interaction.followup.sent[0]
        self.assertIn("Unsupported backup type", content)
        self.assertIsNone(view)


if __name__ == "__main__":
    unittest.main()
