"""Tests for FR-033A's /list rewiring: permissions, database selection,
status filters, richer entries, and pagination."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from watch_party_manager.bot import (
    SuggestionListStatusFilter,
    build_suggestion_entry_line,
    filter_items_by_status,
    handle_list_suggestions,
)
from watch_party_manager.domain.watch_item import MediaType, MetadataProvider, WatchItem, WatchItemStatus
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.permission_service import PermissionService
from watch_party_manager.services.suggestion_service import SuggestionService

GUILD_ID = 100
CHANNEL_ID = 200
WASH_CREW_ROLE_ID = 999
WATCH_PARTY_MEMBER_ROLE_ID = 555


class FilterItemsByStatusTests(unittest.TestCase):
    def _item(self, status: WatchItemStatus) -> WatchItem:
        return WatchItem(title="Alien", media_type=MediaType.MOVIE, status=status)

    def test_active_excludes_archived_and_watched(self) -> None:
        items = [
            self._item(WatchItemStatus.SUGGESTED),
            self._item(WatchItemStatus.ARCHIVED),
            self._item(WatchItemStatus.WATCHED),
        ]
        result = filter_items_by_status(items, SuggestionListStatusFilter.ACTIVE)
        self.assertEqual(1, len(result))

    def test_archived_only_shows_archived(self) -> None:
        items = [self._item(WatchItemStatus.SUGGESTED), self._item(WatchItemStatus.ARCHIVED)]
        result = filter_items_by_status(items, SuggestionListStatusFilter.ARCHIVED)
        self.assertEqual(1, len(result))
        self.assertEqual(WatchItemStatus.ARCHIVED, result[0].status)

    def test_watched_only_shows_watched(self) -> None:
        items = [self._item(WatchItemStatus.SUGGESTED), self._item(WatchItemStatus.WATCHED)]
        result = filter_items_by_status(items, SuggestionListStatusFilter.WATCHED)
        self.assertEqual(1, len(result))
        self.assertEqual(WatchItemStatus.WATCHED, result[0].status)

    def test_all_shows_everything(self) -> None:
        items = [
            self._item(WatchItemStatus.SUGGESTED),
            self._item(WatchItemStatus.ARCHIVED),
            self._item(WatchItemStatus.WATCHED),
        ]
        result = filter_items_by_status(items, SuggestionListStatusFilter.ALL)
        self.assertEqual(3, len(result))


class BuildSuggestionEntryLineTests(unittest.TestCase):
    def test_includes_reference_and_title(self) -> None:
        item = WatchItem(title="Alien", media_type=MediaType.MOVIE, id=7)
        line = build_suggestion_entry_line(item)
        self.assertIn("#0007", line)
        self.assertIn("Alien", line)

    def test_includes_release_year_when_present(self) -> None:
        item = WatchItem(title="Alien", media_type=MediaType.MOVIE, id=1, release_year=1979)
        self.assertIn("1979", build_suggestion_entry_line(item))

    def test_omits_release_year_when_absent(self) -> None:
        item = WatchItem(title="Alien", media_type=MediaType.MOVIE, id=1)
        line = build_suggestion_entry_line(item)
        self.assertNotIn("(", line)

    def test_includes_imdb_link_when_present(self) -> None:
        item = WatchItem(
            title="Alien",
            media_type=MediaType.MOVIE,
            id=1,
            metadata_ids={MetadataProvider.IMDB: "https://www.imdb.com/title/tt0078748/"},
        )
        self.assertIn("tt0078748", build_suggestion_entry_line(item))

    def test_includes_original_post_link_when_available(self) -> None:
        item = WatchItem(
            title="Alien", media_type=MediaType.MOVIE, id=1, guild_id=1, channel_id=2, message_id=3
        )
        self.assertIn("discord.com/channels/1/2/3", build_suggestion_entry_line(item))

    def test_always_includes_status(self) -> None:
        item = WatchItem(title="Alien", media_type=MediaType.MOVIE, id=1, status=WatchItemStatus.ARCHIVED)
        self.assertIn("Status: Archived", build_suggestion_entry_line(item))


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, role_ids=(), *, user_id: int = 1) -> None:
        self.roles = [FakeRole(role_id) for role_id in role_ids]
        self.id = user_id


class FakeResponse:
    def __init__(self) -> None:
        self.sent_message = None
        self.sent_ephemeral = None
        self.sent_view = None

    async def send_message(self, content, ephemeral=False, view=None) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral
        self.sent_view = view


class FakeInteraction:
    def __init__(self, user=None, guild_id=GUILD_ID, channel_id=CHANNEL_ID) -> None:
        self.user = user if user is not None else FakeMember([WATCH_PARTY_MEMBER_ROLE_ID])
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.response = FakeResponse()


class FakeBot:
    def __init__(self, suggestion_service, wash_crew_role_id=WASH_CREW_ROLE_ID) -> None:
        self.suggestion_service = suggestion_service
        self.permission_service = PermissionService(
            watch_party_member_role_id=WATCH_PARTY_MEMBER_ROLE_ID, wash_crew_role_id=wash_crew_role_id
        )
        self.wash_crew_role_id = wash_crew_role_id


class HandleListSuggestionsTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.bot = FakeBot(self.suggestion_service)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _crew_member(self) -> FakeMember:
        return FakeMember([WASH_CREW_ROLE_ID])


class ListPermissionTests(HandleListSuggestionsTestCase):
    async def test_non_watch_party_member_is_rejected(self) -> None:
        interaction = FakeInteraction(user=FakeMember([]))

        await handle_list_suggestions(interaction, self.bot, "active", False)

        self.assertIn("Watch Party", interaction.response.sent_message)

    async def test_watch_party_member_can_view_privately(self) -> None:
        self.suggestion_service.create_database("Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "active", False)

        self.assertTrue(interaction.response.sent_ephemeral)

    async def test_watch_party_member_cannot_post_publicly(self) -> None:
        self.suggestion_service.create_database("Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "active", True)

        self.assertIn("WASH Crew", interaction.response.sent_message)

    async def test_crew_can_post_publicly(self) -> None:
        self.suggestion_service.create_database("Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID)
        self.suggestion_service.suggest("Alien", database_id=1)
        interaction = FakeInteraction(user=self._crew_member())

        await handle_list_suggestions(interaction, self.bot, "active", True)

        self.assertFalse(interaction.response.sent_ephemeral)

    async def test_crew_can_view_archived_privately(self) -> None:
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        item = self.suggestion_service.suggest("Alien", database_id=database.database_id).watch_item
        self.suggestion_service.archive_suggestion(item.id)
        interaction = FakeInteraction(user=self._crew_member())

        await handle_list_suggestions(interaction, self.bot, "archived", False)

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIn("Alien", interaction.response.sent_message)

    async def test_member_can_view_archived_privately(self) -> None:
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        item = self.suggestion_service.suggest("Alien", database_id=database.database_id).watch_item
        self.suggestion_service.archive_suggestion(item.id)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "archived", False)

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIn("Alien", interaction.response.sent_message)


class ListDatabaseSelectionTests(HandleListSuggestionsTestCase):
    async def test_uses_the_channel_matched_database_automatically(self) -> None:
        self.suggestion_service.create_database("Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID)
        self.suggestion_service.suggest("Alien", database_id=1)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "active", False)

        self.assertIn("Movie Night", interaction.response.sent_message)

    async def test_uses_the_sole_database_when_channel_does_not_match(self) -> None:
        self.suggestion_service.create_database("Movie Night", guild_id=GUILD_ID, channel_id=555)
        self.suggestion_service.suggest("Alien", database_id=1)
        interaction = FakeInteraction(channel_id=999)

        await handle_list_suggestions(interaction, self.bot, "active", False)

        self.assertIn("Movie Night", interaction.response.sent_message)

    async def test_shows_a_selector_when_multiple_databases_are_ambiguous(self) -> None:
        self.suggestion_service.create_database("Movie Night", guild_id=GUILD_ID, channel_id=555)
        self.suggestion_service.create_database("Anime Night", guild_id=GUILD_ID, channel_id=556)
        interaction = FakeInteraction(channel_id=999)

        await handle_list_suggestions(interaction, self.bot, "active", False)

        self.assertIsNotNone(interaction.response.sent_view)

    async def test_selecting_a_database_shows_its_list(self) -> None:
        self.suggestion_service.create_database("Movie Night", guild_id=GUILD_ID, channel_id=555)
        self.suggestion_service.create_database("Anime Night", guild_id=GUILD_ID, channel_id=556)
        self.suggestion_service.suggest("Alien", database_id=1)
        interaction = FakeInteraction(channel_id=999)
        await handle_list_suggestions(interaction, self.bot, "active", False)
        select = interaction.response.sent_view.children[0]
        select._values = ["1"]

        select_interaction = FakeInteraction(channel_id=999)
        await select.callback(select_interaction)

        self.assertIn("Alien", select_interaction.response.sent_message)

    async def test_reports_a_clear_error_when_no_database_is_configured(self) -> None:
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "active", False)

        self.assertIsNotNone(interaction.response.sent_message)


class ListFilteringAndPaginationTests(HandleListSuggestionsTestCase):
    async def test_invalid_status_is_rejected(self) -> None:
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "not-a-status", False)

        self.assertIn("Active", interaction.response.sent_message)

    async def test_empty_active_list_reports_clearly(self) -> None:
        self.suggestion_service.create_database("Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "active", False)

        self.assertIn("no active watch items", interaction.response.sent_message)

    async def test_all_filter_includes_archived_and_watched(self) -> None:
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        active = self.suggestion_service.suggest("Alien", database_id=database.database_id).watch_item
        archived = self.suggestion_service.suggest("Aliens", database_id=database.database_id).watch_item
        self.suggestion_service.archive_suggestion(archived.id)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "all", False)

        self.assertIn("Alien", interaction.response.sent_message)
        self.assertIn("Aliens", interaction.response.sent_message)

    async def test_deterministic_ordering_by_id(self) -> None:
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        self.suggestion_service.suggest("Zeta", database_id=database.database_id)
        self.suggestion_service.suggest("Alpha", database_id=database.database_id)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "active", False)

        message = interaction.response.sent_message
        self.assertLess(message.index("Zeta"), message.index("Alpha"))

    async def test_large_list_paginates_without_a_hard_cap(self) -> None:
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        for index in range(40):
            self.suggestion_service.suggest(f"Movie Number {index:03d} With A Long Title", database_id=database.database_id)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "active", False)

        self.assertLessEqual(len(interaction.response.sent_message), 2000)
        self.assertIsNotNone(interaction.response.sent_view)

    async def test_response_never_exceeds_discord_limits(self) -> None:
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        for index in range(200):
            self.suggestion_service.suggest(f"Movie Number {index:03d} With Extra Padding Text Here", database_id=database.database_id)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "active", False)

        self.assertLessEqual(len(interaction.response.sent_message), 2000)


if __name__ == "__main__":
    unittest.main()
