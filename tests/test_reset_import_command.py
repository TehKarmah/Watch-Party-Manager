"""Tests for FR-032C's /database_reset, /factory_reset, and /import wiring
in bot.py, plus /database_remove (Release Polish: Discord-native UX) --
co-located here since it shares the exact same database-picker fixtures
already built out for /database_reset.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from watch_party_manager.bot import (
    handle_database_remove,
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
from watch_party_manager.services.suggestion_service import SuggestionService

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
    def __init__(self, user=None, guild_id=GUILD_ID, guild=None) -> None:
        self.user = user if user is not None else FakeMember(1)
        self.guild_id = guild_id
        self.guild = guild
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
        self.suggestion_service = SuggestionService(
            repository=self.suggestion_repository, database_repository=self.suggestion_database_repository
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
        # Goes through suggestion_service (rather than writing straight to
        # suggestion_database_repository) so the service's in-memory cache
        # -- which handle_database_reset/handle_database_backup now read
        # from to populate their database picker -- stays consistent with
        # what's on disk.
        result = self.bot.suggestion_service.create_database(name, guild_id=GUILD_ID, channel_id=channel_id)
        assert result.success, result.message
        assert result.database.database_id == database_id
        return result.database


async def _select_database(view, database_id: int = 1) -> "FakeInteraction":
    """Simulate picking a database from a DatabaseAdminSelectView -- returns
    the fresh interaction its on_select callback produced.
    """
    select = view.children[0]
    select._values = [str(database_id)]
    select_interaction = FakeInteraction(user=FakeMember(1, roles=[FakeRole(WASH_CREW_ROLE_ID)]))
    await select.callback(interaction=select_interaction)
    return select_interaction


class HandleDatabaseResetTests(ResetImportCommandTestCase):
    async def test_non_wash_crew_is_rejected(self) -> None:
        self._seed_database()
        interaction = FakeInteraction(user=FakeMember(1, roles=[]))

        await handle_database_reset(interaction, self.bot)

        self.assertIn("WASH Crew", interaction.response.sent_message)

    async def test_no_databases_configured_shows_a_clear_message(self) -> None:
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_database_reset(interaction, self.bot)

        self.assertIn("No collections", interaction.response.sent_message)

    async def test_shows_a_database_picker_naming_each_database(self) -> None:
        self._seed_database(name="Movie Night")
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_database_reset(interaction, self.bot)

        self.assertIsNotNone(interaction.response.sent_view)
        select = interaction.response.sent_view.children[0]
        self.assertEqual(1, len(select.options))
        self.assertEqual("🎬 Movie Night", select.options[0].label)

    async def test_selecting_a_database_shows_a_summary_and_confirmation_view(self) -> None:
        self._seed_database(name="Movie Night")
        interaction = FakeInteraction(user=self._wash_crew_member())
        await handle_database_reset(interaction, self.bot)

        select_interaction = await _select_database(interaction.response.sent_view)

        self.assertIn("Movie Night", select_interaction.response.sent_message)
        self.assertIsNotNone(select_interaction.response.sent_view)

    async def test_summary_uses_proper_pluralization_not_a_literal_s_marker(self) -> None:
        # UX Polish: "2 suggestions", not the literal "2 suggestion(s)".
        self._seed_database(name="Movie Night")
        self.bot.suggestion_repository.save(
            [
                WatchItem(title="Alien", media_type=MediaType.MOVIE, database_id=1, guild_id=GUILD_ID),
                WatchItem(title="Aliens", media_type=MediaType.MOVIE, database_id=1, guild_id=GUILD_ID),
            ],
            next_id=3,
        )
        interaction = FakeInteraction(user=self._wash_crew_member())
        await handle_database_reset(interaction, self.bot)

        select_interaction = await _select_database(interaction.response.sent_view)

        self.assertIn("2 suggestions", select_interaction.response.sent_message)
        self.assertNotIn("suggestion(s)", select_interaction.response.sent_message)

    async def test_typing_reset_performs_the_reset(self) -> None:
        self._seed_database(name="Movie Night")
        self.bot.suggestion_repository.save(
            [WatchItem(title="Alien", media_type=MediaType.MOVIE, database_id=1, guild_id=GUILD_ID)], next_id=2
        )
        interaction = FakeInteraction(user=self._wash_crew_member())
        await handle_database_reset(interaction, self.bot)
        select_interaction = await _select_database(interaction.response.sent_view)
        view = select_interaction.response.sent_view

        confirm_interaction = await _submit_modal(view, 0, "RESET")

        self.assertIn("reset", confirm_interaction.response.sent_message.lower())
        self.assertEqual([], self.bot.suggestion_repository.load().watch_items)

    async def test_success_response_reports_the_actual_removed_count(self) -> None:
        self._seed_database(name="Movie Night")
        # Seeded through the live service, like every real /add would --
        # matches how bot.suggestion_service actually gets populated in
        # production, unlike a raw repository write it would never see.
        self.bot.suggestion_service.suggest("Alien", database_id=1, guild_id=GUILD_ID)
        self.bot.suggestion_service.suggest("Aliens", database_id=1, guild_id=GUILD_ID)
        self.bot.suggestion_service.suggest("Alien 3", database_id=1, guild_id=GUILD_ID)
        interaction = FakeInteraction(user=self._wash_crew_member())
        await handle_database_reset(interaction, self.bot)
        select_interaction = await _select_database(interaction.response.sent_view)
        view = select_interaction.response.sent_view

        confirm_interaction = await _submit_modal(view, 0, "RESET")

        # UX Polish: proper pluralization ("3 suggestions removed"), not
        # the literal "(s)" marker.
        self.assertIn("3 suggestions removed", confirm_interaction.response.sent_message)
        self.assertNotIn("suggestion(s)", confirm_interaction.response.sent_message)

    async def test_list_immediately_shows_the_empty_state_after_reset(self) -> None:
        # Regression for the reported bug: /database_reset reported
        # success but /list still showed the "removed" items, because
        # the write bypassed bot.suggestion_service's in-memory cache.
        self._seed_database(name="Movie Night")
        self.bot.suggestion_repository.save(
            [WatchItem(title="Dogma", media_type=MediaType.MOVIE, database_id=1, guild_id=GUILD_ID)], next_id=2
        )
        # Populate the live service's cache the same way a real command
        # would have, before the reset -- it must already know about the
        # item for this test to prove anything about staleness.
        self.bot.suggestion_service.reload_suggestions()
        self.assertEqual(1, self.bot.suggestion_service.suggestion_count_for_database(1))
        interaction = FakeInteraction(user=self._wash_crew_member())
        await handle_database_reset(interaction, self.bot)
        select_interaction = await _select_database(interaction.response.sent_view)
        view = select_interaction.response.sent_view

        await _submit_modal(view, 0, "RESET")

        # This mirrors exactly what /list reads: bot.suggestion_service's
        # live, in-memory state, not the repository file directly.
        self.assertEqual(0, self.bot.suggestion_service.suggestion_count_for_database(1))
        self.assertEqual([], self.bot.suggestion_service.get_suggestions_for_database(1))

    async def test_add_immediately_accepts_a_previously_removed_title(self) -> None:
        self._seed_database(name="Movie Night")
        self.bot.suggestion_repository.save(
            [WatchItem(title="Dogma", media_type=MediaType.MOVIE, database_id=1, guild_id=GUILD_ID)], next_id=2
        )
        self.bot.suggestion_service.reload_suggestions()
        interaction = FakeInteraction(user=self._wash_crew_member())
        await handle_database_reset(interaction, self.bot)
        select_interaction = await _select_database(interaction.response.sent_view)
        view = select_interaction.response.sent_view
        await _submit_modal(view, 0, "RESET")

        # This mirrors what /add's duplicate check reads -- the live
        # service, not a fresh repository read.
        result = self.bot.suggestion_service.suggest("Dogma", database_id=1, guild_id=GUILD_ID)

        self.assertTrue(result.success)

    async def test_restart_simulation_preserves_the_empty_state(self) -> None:
        self._seed_database(name="Movie Night")
        self.bot.suggestion_repository.save(
            [WatchItem(title="Dogma", media_type=MediaType.MOVIE, database_id=1, guild_id=GUILD_ID)], next_id=2
        )
        interaction = FakeInteraction(user=self._wash_crew_member())
        await handle_database_reset(interaction, self.bot)
        select_interaction = await _select_database(interaction.response.sent_view)
        view = select_interaction.response.sent_view
        await _submit_modal(view, 0, "RESET")

        # A brand-new SuggestionService loading only from disk -- simulates
        # the bot process restarting.
        restarted_service = SuggestionService(
            repository=self.bot.suggestion_repository, database_repository=self.bot.suggestion_database_repository
        )
        self.assertEqual(0, restarted_service.suggestion_count_for_database(1))

    async def test_picker_selection_targets_the_selected_collection_not_another(self) -> None:
        self._seed_database(database_id=1, name="Movies", channel_id=555)
        self._seed_database(database_id=2, name="TV Shows", channel_id=556)
        self.bot.suggestion_repository.save(
            [
                WatchItem(title="Dogma", media_type=MediaType.MOVIE, database_id=1, guild_id=GUILD_ID),
                WatchItem(title="Breaking Bad", media_type=MediaType.MOVIE, database_id=2, guild_id=GUILD_ID),
            ],
            next_id=3,
        )
        self.bot.suggestion_service.reload_suggestions()
        interaction = FakeInteraction(user=self._wash_crew_member())
        await handle_database_reset(interaction, self.bot)

        # Select the SECOND collection (TV Shows), not the first.
        select_interaction = await _select_database(interaction.response.sent_view, database_id=2)
        self.assertIn("TV Shows", select_interaction.response.sent_message)
        view = select_interaction.response.sent_view

        await _submit_modal(view, 0, "RESET")

        self.assertEqual(1, self.bot.suggestion_service.suggestion_count_for_database(1))
        self.assertEqual(0, self.bot.suggestion_service.suggestion_count_for_database(2))
        remaining_titles = {item.title for item in self.bot.suggestion_service.get_suggestions()}
        self.assertEqual({"Dogma"}, remaining_titles)

    async def test_wrong_confirmation_text_does_not_reset(self) -> None:
        self._seed_database(name="Movie Night")
        self.bot.suggestion_repository.save(
            [WatchItem(title="Alien", media_type=MediaType.MOVIE, database_id=1, guild_id=GUILD_ID)], next_id=2
        )
        interaction = FakeInteraction(user=self._wash_crew_member())
        await handle_database_reset(interaction, self.bot)
        select_interaction = await _select_database(interaction.response.sent_view)
        view = select_interaction.response.sent_view

        await _submit_modal(view, 0, "reset")  # wrong case

        self.assertEqual(1, len(self.bot.suggestion_repository.load().watch_items))

    async def test_cancel_leaves_data_unchanged(self) -> None:
        self._seed_database(name="Movie Night")
        self.bot.suggestion_repository.save(
            [WatchItem(title="Alien", media_type=MediaType.MOVIE, database_id=1, guild_id=GUILD_ID)], next_id=2
        )
        interaction = FakeInteraction(user=self._wash_crew_member())
        await handle_database_reset(interaction, self.bot)
        select_interaction = await _select_database(interaction.response.sent_view)
        view = select_interaction.response.sent_view

        cancel_interaction = FakeInteraction(user=self._wash_crew_member())
        await view.children[1].callback(cancel_interaction)

        self.assertIn("cancelled", cancel_interaction.response.sent_message.lower())
        self.assertEqual(1, len(self.bot.suggestion_repository.load().watch_items))


class ContextualResolutionAfterResetTests(ResetImportCommandTestCase):
    """/database_reset uses an explicit picker rather than contextual
    (channel-based) resolution (Release Polish: Discord-native UX) -- it
    must reset exactly the picker-selected collection regardless of
    which channel/thread the command was run from, and the collection's
    channel must still resolve correctly through contextual resolution
    (/add, /list, /start_vote, etc.) once the reset completes.
    """

    async def test_reset_targets_the_picker_selected_collection_from_an_unrelated_channel(self) -> None:
        self._seed_database(database_id=1, name="Movies", channel_id=555)
        self._seed_database(database_id=2, name="TV Shows", channel_id=556)
        self.bot.suggestion_service.suggest("Dogma", database_id=1, guild_id=GUILD_ID)
        self.bot.suggestion_service.suggest("Breaking Bad", database_id=2, guild_id=GUILD_ID)
        # Run the command from a channel that belongs to neither collection.
        interaction = FakeInteraction(user=self._wash_crew_member())
        interaction.channel_id = 999999

        await handle_database_reset(interaction, self.bot)
        select_interaction = await _select_database(interaction.response.sent_view, database_id=1)
        view = select_interaction.response.sent_view
        await _submit_modal(view, 0, "RESET")

        self.assertEqual(0, self.bot.suggestion_service.suggestion_count_for_database(1))
        self.assertEqual(1, self.bot.suggestion_service.suggestion_count_for_database(2))

    async def test_channel_still_resolves_to_the_retained_collection_after_reset(self) -> None:
        database = self._seed_database(database_id=1, name="Movies", channel_id=555)
        self.bot.suggestion_service.suggest("Dogma", database_id=1, guild_id=GUILD_ID)
        interaction = FakeInteraction(user=self._wash_crew_member())
        await handle_database_reset(interaction, self.bot)
        select_interaction = await _select_database(interaction.response.sent_view)
        view = select_interaction.response.sent_view
        await _submit_modal(view, 0, "RESET")

        resolution = self.bot.suggestion_service.resolve_database_for_channel(GUILD_ID, database.channel_id)

        self.assertIsNotNone(resolution.database)
        self.assertEqual(database.database_id, resolution.database.database_id)
        self.assertEqual("Movies", resolution.database.name)

    async def test_add_in_the_collections_home_channel_resolves_correctly_after_reset(self) -> None:
        database = self._seed_database(database_id=1, name="Movies", channel_id=555)
        self.bot.suggestion_service.suggest("Dogma", database_id=1, guild_id=GUILD_ID)
        interaction = FakeInteraction(user=self._wash_crew_member())
        await handle_database_reset(interaction, self.bot)
        select_interaction = await _select_database(interaction.response.sent_view)
        view = select_interaction.response.sent_view
        await _submit_modal(view, 0, "RESET")

        resolution = self.bot.suggestion_service.resolve_database_for_channel(GUILD_ID, database.channel_id)
        result = self.bot.suggestion_service.suggest(
            "Dogma", database_id=resolution.database.database_id, guild_id=GUILD_ID
        )

        self.assertTrue(result.success)


class HandleDatabaseRemoveTests(ResetImportCommandTestCase):
    async def test_non_wash_crew_is_rejected(self) -> None:
        self._seed_database()
        interaction = FakeInteraction(user=FakeMember(1, roles=[]))

        await handle_database_remove(interaction, self.bot)

        self.assertIn("WASH Crew", interaction.response.sent_message)

    async def test_no_databases_configured_shows_a_clear_message(self) -> None:
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_database_remove(interaction, self.bot)

        self.assertIsNone(interaction.response.sent_view)
        self.assertIn("No collections", interaction.response.sent_message)

    async def test_shows_a_database_picker_naming_each_database(self) -> None:
        self._seed_database(name="Movie Night")
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_database_remove(interaction, self.bot)

        self.assertIsNotNone(interaction.response.sent_view)
        select = interaction.response.sent_view.children[0]
        self.assertEqual(1, len(select.options))
        self.assertEqual("🎬 Movie Night", select.options[0].label)

    async def test_selecting_a_database_deactivates_it(self) -> None:
        self._seed_database(name="Movie Night")
        interaction = FakeInteraction(user=self._wash_crew_member())
        await handle_database_remove(interaction, self.bot)

        select_interaction = await _select_database(interaction.response.sent_view)

        self.assertIn("deactivated", select_interaction.response.sent_message)
        self.assertTrue(select_interaction.response.sent_ephemeral)
        self.assertFalse(self.bot.suggestion_service.get_database(1).active)

    async def test_selecting_a_database_preserves_its_suggestions(self) -> None:
        self._seed_database(name="Movie Night")
        self.bot.suggestion_repository.save(
            [WatchItem(title="Alien", media_type=MediaType.MOVIE, database_id=1, guild_id=GUILD_ID)], next_id=2
        )
        interaction = FakeInteraction(user=self._wash_crew_member())
        await handle_database_remove(interaction, self.bot)

        await _select_database(interaction.response.sent_view)

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

    async def test_live_state_is_cleared_immediately(self) -> None:
        # Same live-state-staleness bug as /database_reset: factory reset
        # writes suggestions.json/suggestion_databases.json directly, so
        # bot.suggestion_service must be explicitly resynced afterward.
        self.bot.guild_configuration_repository.save(GuildConfiguration(guild_id=GUILD_ID, guild_name="Guild"))
        self._seed_database(name="Movie Night")
        self.bot.suggestion_service.suggest("Dogma", database_id=1, guild_id=GUILD_ID)
        interaction = FakeInteraction(user=self._wash_crew_member())
        await handle_factory_reset(interaction, self.bot)
        view = interaction.response.sent_view

        await _submit_modal(view, 0, "RESET")

        self.assertEqual([], self.bot.suggestion_service.list_databases(GUILD_ID))
        self.assertEqual(0, len(self.bot.suggestion_service.get_suggestions()))


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
