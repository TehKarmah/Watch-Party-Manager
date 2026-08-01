"""Tests for /list's Watch Item Status Presentation: the six status
filters (Active Watch Items default, Eligible for Voting, In an Active
Vote, Vote Winners, Retired, All Watch Items), status emoji on every
entry, the Active Watch Items summary line, Vote Winner win dates, embed
suppression, and pagination.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from watch_party_manager.bot import (
    SuggestionListStatusFilter,
    build_suggestion_entry_line,
    handle_list_suggestions,
    resolve_suggestion_list_entries,
)
from watch_party_manager.domain.watch_item import MediaType, MetadataProvider, WatchItem, WatchItemStatus
from watch_party_manager.domain.watch_item_journey import WatchItemJourney
from watch_party_manager.pagination_view import PaginatedListView
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.services.collection_eligibility_service import (
    CollectionEligibility,
    CollectionEligibilityService,
)
from watch_party_manager.services.permission_service import PermissionService
from watch_party_manager.services.suggestion_display_status import SuggestionDisplayStatus
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.vote_service import VoteService
from watch_party_manager.suggestion_selection_view import ListDatabaseSelectView

GUILD_ID = 100
CHANNEL_ID = 200
WASH_CREW_ROLE_ID = 999
WATCH_PARTY_MEMBER_ROLE_ID = 555


def make_item(item_id: int, status: WatchItemStatus = WatchItemStatus.SUGGESTED, **kwargs) -> WatchItem:
    return WatchItem(title=f"Item {item_id}", media_type=MediaType.MOVIE, id=item_id, status=status, **kwargs)


class ResolveSuggestionListEntriesTests(unittest.TestCase):
    """Pure-function tests: given an already-computed CollectionEligibility,
    which bucket(s) does each filter show, paired with the right display status.
    """

    def setUp(self) -> None:
        self.available_item = make_item(1)
        self.in_active_vote_item = make_item(2)
        self.winner_item = make_item(3, status=WatchItemStatus.VOTE_WINNER)
        self.retired_item = make_item(4, status=WatchItemStatus.ARCHIVED)
        self.watched_item = make_item(5, status=WatchItemStatus.WATCHED)
        self.pending_crew_review_item = make_item(6, status=WatchItemStatus.PENDING_CREW_REVIEW)
        self.eligibility = CollectionEligibility(
            database_id=1,
            available=(self.available_item,),
            in_active_vote=(self.in_active_vote_item,),
            vote_winners=(self.winner_item,),
            retired=(self.retired_item,),
            watched=(self.watched_item,),
            pending_crew_review=(self.pending_crew_review_item,),
        )

    def test_active_mixes_available_and_in_active_vote(self) -> None:
        entries = resolve_suggestion_list_entries(self.eligibility, SuggestionListStatusFilter.ACTIVE)
        items_and_statuses = {(item.id, status) for item, status in entries}
        self.assertEqual(
            {
                (self.available_item.id, SuggestionDisplayStatus.AVAILABLE),
                (self.in_active_vote_item.id, SuggestionDisplayStatus.IN_ACTIVE_VOTE),
            },
            items_and_statuses,
        )

    def test_eligible_shows_only_available(self) -> None:
        entries = resolve_suggestion_list_entries(self.eligibility, SuggestionListStatusFilter.ELIGIBLE)
        self.assertEqual([(self.available_item, SuggestionDisplayStatus.AVAILABLE)], entries)

    def test_in_active_vote_shows_only_in_active_vote(self) -> None:
        entries = resolve_suggestion_list_entries(self.eligibility, SuggestionListStatusFilter.IN_ACTIVE_VOTE)
        self.assertEqual([(self.in_active_vote_item, SuggestionDisplayStatus.IN_ACTIVE_VOTE)], entries)

    def test_vote_winner_shows_only_winners(self) -> None:
        entries = resolve_suggestion_list_entries(self.eligibility, SuggestionListStatusFilter.VOTE_WINNER)
        self.assertEqual([(self.winner_item, SuggestionDisplayStatus.VOTE_WINNER)], entries)

    def test_retired_shows_only_retired(self) -> None:
        entries = resolve_suggestion_list_entries(self.eligibility, SuggestionListStatusFilter.RETIRED)
        self.assertEqual([(self.retired_item, SuggestionDisplayStatus.RETIRED)], entries)

    def test_watched_shows_only_watched(self) -> None:
        entries = resolve_suggestion_list_entries(self.eligibility, SuggestionListStatusFilter.WATCHED)
        self.assertEqual([(self.watched_item, SuggestionDisplayStatus.WATCHED)], entries)

    def test_pending_crew_review_shows_only_pending_crew_review(self) -> None:
        entries = resolve_suggestion_list_entries(self.eligibility, SuggestionListStatusFilter.PENDING_CREW_REVIEW)
        self.assertEqual(
            [(self.pending_crew_review_item, SuggestionDisplayStatus.PENDING_CREW_REVIEW)], entries
        )

    def test_all_shows_every_bucket(self) -> None:
        entries = resolve_suggestion_list_entries(self.eligibility, SuggestionListStatusFilter.ALL)
        items_and_statuses = {(item.id, status) for item, status in entries}
        self.assertEqual(
            {
                (self.available_item.id, SuggestionDisplayStatus.AVAILABLE),
                (self.in_active_vote_item.id, SuggestionDisplayStatus.IN_ACTIVE_VOTE),
                (self.winner_item.id, SuggestionDisplayStatus.VOTE_WINNER),
                (self.retired_item.id, SuggestionDisplayStatus.RETIRED),
                (self.watched_item.id, SuggestionDisplayStatus.WATCHED),
                (self.pending_crew_review_item.id, SuggestionDisplayStatus.PENDING_CREW_REVIEW),
            },
            items_and_statuses,
        )

    def test_eight_filters_exist(self) -> None:
        self.assertEqual(
            {
                "active",
                "eligible",
                "in_active_vote",
                "pending_crew_review",
                "vote_winner",
                "retired",
                "watched",
                "all",
            },
            {member.value for member in SuggestionListStatusFilter},
        )


class BuildSuggestionEntryLineTests(unittest.TestCase):
    def test_shows_title_year_and_emoji_with_no_original_post(self) -> None:
        item = WatchItem(title="The Matrix", media_type=MediaType.MOVIE, id=7, release_year=1999)
        self.assertEqual(
            "🟢 The Matrix (1999)", build_suggestion_entry_line(item, SuggestionDisplayStatus.AVAILABLE)
        )

    def test_omits_release_year_when_absent(self) -> None:
        item = WatchItem(title="The Matrix", media_type=MediaType.MOVIE, id=1)
        self.assertEqual("🟢 The Matrix", build_suggestion_entry_line(item, SuggestionDisplayStatus.AVAILABLE))

    def test_includes_a_clean_original_suggestion_link_when_available(self) -> None:
        item = WatchItem(
            title="The Matrix",
            media_type=MediaType.MOVIE,
            id=1,
            release_year=1999,
            guild_id=1,
            channel_id=2,
            message_id=3,
        )
        line = build_suggestion_entry_line(item, SuggestionDisplayStatus.AVAILABLE)
        self.assertEqual(
            "🟢 The Matrix (1999) | [Original Suggestion](https://discord.com/channels/1/2/3)", line
        )

    def test_never_includes_a_reference_number(self) -> None:
        item = WatchItem(title="The Matrix", media_type=MediaType.MOVIE, id=42, release_year=1999)
        line = build_suggestion_entry_line(item, SuggestionDisplayStatus.AVAILABLE)
        self.assertNotIn("#", line)
        self.assertNotIn("0042", line)

    def test_never_includes_an_imdb_link(self) -> None:
        item = WatchItem(
            title="The Matrix",
            media_type=MediaType.MOVIE,
            id=1,
            metadata_ids={MetadataProvider.IMDB: "https://www.imdb.com/title/tt0133093/"},
        )
        self.assertNotIn("imdb", build_suggestion_entry_line(item, SuggestionDisplayStatus.AVAILABLE).lower())

    def test_item_without_an_original_post_has_no_broken_link_placeholder(self) -> None:
        item = WatchItem(title="The Matrix", media_type=MediaType.MOVIE, id=1, release_year=1999)
        line = build_suggestion_entry_line(item, SuggestionDisplayStatus.AVAILABLE)
        self.assertNotIn("[", line)
        self.assertNotIn("None", line)

    def test_in_an_active_vote_entry_uses_the_ballot_box_emoji(self) -> None:
        item = WatchItem(title="Arrival", media_type=MediaType.MOVIE, id=1, release_year=2016)
        line = build_suggestion_entry_line(item, SuggestionDisplayStatus.IN_ACTIVE_VOTE)
        self.assertTrue(line.startswith("🗳️ Arrival (2016)"))

    def test_retired_entry_uses_the_archive_box_emoji(self) -> None:
        item = WatchItem(title="Old Movie", media_type=MediaType.MOVIE, id=1)
        line = build_suggestion_entry_line(item, SuggestionDisplayStatus.RETIRED)
        self.assertTrue(line.startswith("🗄️ Old Movie"))

    def test_vote_winner_entry_uses_the_trophy_emoji_and_shows_the_won_date(self) -> None:
        item = WatchItem(
            title="Zombieland",
            media_type=MediaType.MOVIE,
            id=1,
            release_year=2009,
            status=WatchItemStatus.VOTE_WINNER,
            journey=WatchItemJourney(last_won_date=date(2026, 7, 28)),
        )
        line = build_suggestion_entry_line(item, SuggestionDisplayStatus.VOTE_WINNER)
        self.assertEqual("🏆 Zombieland (2009)\nWon: July 28, 2026", line)

    def test_legacy_vote_winner_without_a_won_date_omits_the_won_line(self) -> None:
        item = WatchItem(
            title="Zombieland",
            media_type=MediaType.MOVIE,
            id=1,
            release_year=2009,
            status=WatchItemStatus.VOTE_WINNER,
        )
        line = build_suggestion_entry_line(item, SuggestionDisplayStatus.VOTE_WINNER)
        self.assertEqual("🏆 Zombieland (2009)", line)
        self.assertNotIn("Won", line)


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
        self.sent_suppress_embeds = None
        self.edited_content = None
        self.edited_view = None

    async def send_message(self, content, ephemeral=False, view=None, suppress_embeds=False) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral
        self.sent_view = view
        self.sent_suppress_embeds = suppress_embeds

    async def edit_message(self, content=None, view=None) -> None:
        self.edited_content = content
        self.edited_view = view


class FakeInteraction:
    def __init__(self, user=None, guild_id=GUILD_ID, channel_id=CHANNEL_ID, guild=None) -> None:
        self.user = user if user is not None else FakeMember([WATCH_PARTY_MEMBER_ROLE_ID])
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.guild = guild
        self.response = FakeResponse()


class FakeGuildConfigurationRepository:
    """Always reports "no guild configuration saved" -- callers fall back
    to their documented defaults (e.g. DEFAULT_VOTE_CANDIDATE_COUNT)."""

    def get(self, guild_id: int):
        return None


class FakeBot:
    def __init__(
        self,
        suggestion_service,
        wash_crew_role_id=WASH_CREW_ROLE_ID,
        vote_service=None,
    ) -> None:
        self.suggestion_service = suggestion_service
        self.permission_service = PermissionService(
            watch_party_member_role_id=WATCH_PARTY_MEMBER_ROLE_ID, wash_crew_role_id=wash_crew_role_id
        )
        self.wash_crew_role_id = wash_crew_role_id
        self.suggestion_database_configuration_repository = None
        self.guild_configuration_repository = FakeGuildConfigurationRepository()
        self.vote_service = vote_service
        self.collection_eligibility_service = CollectionEligibilityService(suggestion_service, vote_service)


class HandleListSuggestionsTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.configuration_repository = SuggestionDatabaseConfigurationRepository(
            root / "suggestion_database_configurations.json"
        )
        self.vote_service = VoteService(
            self.suggestion_service, repository=JsonVoteRepository(root / "voting.json")
        )
        self.bot = FakeBot(
            self.suggestion_service,
            vote_service=self.vote_service,
        )
        self.bot.suggestion_database_configuration_repository = self.configuration_repository

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

    async def test_crew_can_view_retired_privately(self) -> None:
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        item = self.suggestion_service.suggest("Alien", database_id=database.database_id).watch_item
        self.suggestion_service.archive_suggestion(item.id)
        interaction = FakeInteraction(user=self._crew_member())

        await handle_list_suggestions(interaction, self.bot, "retired", False)

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIn("Alien", interaction.response.sent_message)

    async def test_member_can_view_retired_privately(self) -> None:
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        item = self.suggestion_service.suggest("Alien", database_id=database.database_id).watch_item
        self.suggestion_service.archive_suggestion(item.id)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "retired", False)

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

    async def test_header_shows_the_collections_built_in_emoji(self) -> None:
        # Release Candidate Polish, Requirement 3: every user-facing
        # display of a collection uses the shared format_collection_display()
        # helper -- including this command's own header.
        self.suggestion_service.create_database("Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID)
        self.suggestion_service.suggest("Alien", database_id=1)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "active", False)

        self.assertIn("🎬 Movie Night", interaction.response.sent_message)


class ListFilteringAndPaginationTests(HandleListSuggestionsTestCase):
    async def test_invalid_status_is_rejected(self) -> None:
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "not-a-status", False)

        self.assertIn("Active Watch Items", interaction.response.sent_message)

    async def test_empty_active_list_reports_clearly(self) -> None:
        self.suggestion_service.create_database("Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "active", False)

        self.assertIn("Active Watch Items", interaction.response.sent_message)

    async def test_default_status_is_active(self) -> None:
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        self.suggestion_service.suggest("Alien", database_id=database.database_id)
        retired = self.suggestion_service.suggest("Aliens", database_id=database.database_id).watch_item
        self.suggestion_service.archive_suggestion(retired.id)
        interaction = FakeInteraction()

        # Mirrors the /list command's own default: status defaults to "active".
        await handle_list_suggestions(interaction, self.bot, "active", False)

        self.assertIn("Alien", interaction.response.sent_message)
        self.assertNotIn("Aliens", interaction.response.sent_message)

    async def test_active_shows_a_summary_line_before_the_list(self) -> None:
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        item_a = self.suggestion_service.suggest("Alien", database_id=database.database_id).watch_item
        other = self.suggestion_service.suggest("The Matrix", database_id=database.database_id).watch_item
        self.suggestion_service.suggest("Inception", database_id=database.database_id)
        self.suggestion_service.suggest("Arrival", database_id=database.database_id)
        self.bot.vote_service.create_round(
            candidate_suggestion_ids=[item_a.id, other.id], database_id=database.database_id
        )
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "active", False)

        message = interaction.response.sent_message
        self.assertIn("🟢 Eligible for Voting: 2", message)
        self.assertIn("🗳️ In an Active Vote: 2", message)

    async def test_active_mixes_available_and_in_active_vote_entries(self) -> None:
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        item_a = self.suggestion_service.suggest("Alien", database_id=database.database_id).watch_item
        other = self.suggestion_service.suggest("The Matrix", database_id=database.database_id).watch_item
        self.suggestion_service.suggest("Inception", database_id=database.database_id)
        self.bot.vote_service.create_round(
            candidate_suggestion_ids=[item_a.id, other.id], database_id=database.database_id
        )
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "active", False)

        message = interaction.response.sent_message
        self.assertIn("🗳️ Alien", message)
        self.assertIn("🟢 Inception", message)

    async def test_vote_winner_mode_excludes_active_and_retired_items(self) -> None:
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        self.suggestion_service.suggest("Available Movie", database_id=database.database_id)
        retired = self.suggestion_service.suggest("Retired Movie", database_id=database.database_id).watch_item
        self.suggestion_service.archive_suggestion(retired.id)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "vote_winner", False)

        self.assertIn("no watch items matching Vote Winners", interaction.response.sent_message)

    async def test_vote_winner_entries_show_the_trophy_and_won_date(self) -> None:
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        winner = self.suggestion_service.suggest("Zombieland", database_id=database.database_id).watch_item
        self.suggestion_service.record_vote_win(winner.id, date(2026, 7, 28))
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "vote_winner", False)

        message = interaction.response.sent_message
        self.assertIn("🏆 Zombieland", message)
        self.assertIn("Won: July 28, 2026", message)

    async def test_retired_mode_shows_only_retired_items(self) -> None:
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        self.suggestion_service.suggest("Available Movie", database_id=database.database_id)
        retired = self.suggestion_service.suggest("Retired Movie", database_id=database.database_id).watch_item
        self.suggestion_service.archive_suggestion(retired.id)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "retired", False)

        message = interaction.response.sent_message
        self.assertIn("Retired Movie", message)
        self.assertNotIn("Available Movie", message)
        self.assertIn("🗄️", message)

    async def test_watched_mode_shows_only_watched_items(self) -> None:
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        self.suggestion_service.suggest("Available Movie", database_id=database.database_id)
        watched = self.suggestion_service.suggest("Watched Movie", database_id=database.database_id).watch_item
        self.suggestion_service.mark_suggestion_watched(watched.id, date.today())
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "watched", False)

        message = interaction.response.sent_message
        self.assertIn("Watched Movie", message)
        self.assertNotIn("Available Movie", message)
        self.assertIn("✅", message)

    async def test_eligible_mode_shows_only_available_items(self) -> None:
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        item_a = self.suggestion_service.suggest("Alien", database_id=database.database_id).watch_item
        other = self.suggestion_service.suggest("The Matrix", database_id=database.database_id).watch_item
        self.suggestion_service.suggest("Inception", database_id=database.database_id)
        self.bot.vote_service.create_round(
            candidate_suggestion_ids=[item_a.id, other.id], database_id=database.database_id
        )
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "eligible", False)

        message = interaction.response.sent_message
        self.assertNotIn("Alien", message)
        self.assertIn("Inception", message)

    async def test_all_mode_shows_every_watch_item(self) -> None:
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        self.suggestion_service.suggest("Available Movie", database_id=database.database_id)
        winner = self.suggestion_service.suggest("Winner Movie", database_id=database.database_id).watch_item
        self.suggestion_service.record_vote_win(winner.id, date(2026, 7, 28))
        retired = self.suggestion_service.suggest("Retired Movie", database_id=database.database_id).watch_item
        self.suggestion_service.archive_suggestion(retired.id)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "all", False)

        message = interaction.response.sent_message
        self.assertIn("Available Movie", message)
        self.assertIn("Winner Movie", message)
        self.assertIn("Retired Movie", message)
        self.assertIn("All Watch Items", message)

    async def test_entries_have_no_reference_number(self) -> None:
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        self.suggestion_service.suggest("Alien", database_id=database.database_id)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "active", False)

        message = interaction.response.sent_message
        self.assertNotIn("#0001", message)

    async def test_response_suppresses_link_preview_embeds(self) -> None:
        self.suggestion_service.create_database("Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID)
        self.suggestion_service.suggest("Alien", database_id=1)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "active", False)

        self.assertTrue(interaction.response.sent_suppress_embeds)

    async def test_empty_result_also_suppresses_embeds(self) -> None:
        self.suggestion_service.create_database("Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "active", False)

        self.assertTrue(interaction.response.sent_suppress_embeds)

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
        for index in range(120):
            self.suggestion_service.suggest(
                f"Movie Number {index:03d} With A Reasonably Long Padded Title", database_id=database.database_id
            )
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


class ListEligibilityParityTests(HandleListSuggestionsTestCase):
    """/list's Eligible/In an Active Vote buckets must be resolved
    through the same authoritative CollectionEligibilityService every
    other command uses -- never disagree.
    """

    async def test_a_nominated_item_moves_from_eligible_to_in_an_active_vote(self) -> None:
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        item_a = self.suggestion_service.suggest("Alien", database_id=database.database_id).watch_item
        other = self.suggestion_service.suggest("The Matrix", database_id=database.database_id).watch_item
        self.suggestion_service.suggest("Inception", database_id=database.database_id)
        self.bot.vote_service.create_round(
            candidate_suggestion_ids=[item_a.id, other.id], database_id=database.database_id
        )

        eligible_interaction = FakeInteraction()
        await handle_list_suggestions(eligible_interaction, self.bot, "eligible", False)
        active_vote_interaction = FakeInteraction()
        await handle_list_suggestions(active_vote_interaction, self.bot, "in_active_vote", False)

        self.assertIn("Inception", eligible_interaction.response.sent_message)
        self.assertNotIn("Alien", eligible_interaction.response.sent_message)
        self.assertIn("Alien", active_vote_interaction.response.sent_message)
        self.assertNotIn("Inception", active_vote_interaction.response.sent_message)

    async def test_never_shows_rotation_cooldown_text(self) -> None:
        # Rotation Cooldown no longer exists as a display concept -- a
        # non-nominated, non-terminal item always simply reads as
        # Available.
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        self.suggestion_service.suggest("Alien", database_id=database.database_id)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "active", False)

        message = interaction.response.sent_message
        self.assertNotIn("Rotation Cooldown", message)
        self.assertIn("🟢 Alien", message)


class ListInActiveVoteEndToEndTests(HandleListSuggestionsTestCase):
    """End to end: a suggestion currently nominated in a real open voting
    round shows as In an Active Vote on /list, taking priority over
    whichever bucket it would otherwise be in.
    """

    async def test_a_nominated_suggestion_shows_in_an_active_vote_and_leaves_the_eligible_filter(self) -> None:
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        item = self.suggestion_service.suggest("Alien", database_id=database.database_id).watch_item
        other = self.suggestion_service.suggest("The Matrix", database_id=database.database_id).watch_item
        self.suggestion_service.suggest("Inception", database_id=database.database_id)
        self.bot.vote_service.create_round(
            candidate_suggestion_ids=[item.id, other.id], database_id=database.database_id
        )

        active_vote_interaction = FakeInteraction()
        await handle_list_suggestions(active_vote_interaction, self.bot, "in_active_vote", False)
        eligible_interaction = FakeInteraction()
        await handle_list_suggestions(eligible_interaction, self.bot, "eligible", False)

        self.assertIn("🗳️ Alien", active_vote_interaction.response.sent_message)
        self.assertNotIn("🟢 Alien", eligible_interaction.response.sent_message)
        self.assertNotIn("Alien", eligible_interaction.response.sent_message)
        self.assertIn("Inception", eligible_interaction.response.sent_message)

    async def test_without_a_vote_service_attribute_the_item_shows_its_ordinary_status(self) -> None:
        # FakeBot fixtures across the suite don't all define vote_service
        # -- send_suggestion_list must read it defensively (getattr) so
        # /list keeps working unchanged for them.
        del self.bot.vote_service
        self.bot.collection_eligibility_service = CollectionEligibilityService(self.suggestion_service, None)
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        self.suggestion_service.suggest("Alien", database_id=database.database_id)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "eligible", False)

        self.assertIn("🟢 Alien", interaction.response.sent_message)


class ListSwitchCollectionTests(HandleListSuggestionsTestCase):
    async def test_switch_collection_button_appears_with_multiple_databases(self) -> None:
        self.suggestion_service.create_database("Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID)
        self.suggestion_service.create_database("TV Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID + 1)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "active", False)

        self.assertIsNotNone(interaction.response.sent_view)
        custom_ids = [item.custom_id for item in interaction.response.sent_view.children]
        self.assertIn("wpm_list_switch_collection", custom_ids)

    async def test_switch_collection_button_absent_with_only_one_database(self) -> None:
        self.suggestion_service.create_database("Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID)
        self.suggestion_service.suggest("Alien", database_id=1)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "active", False)

        if interaction.response.sent_view is not None:
            custom_ids = [item.custom_id for item in interaction.response.sent_view.children]
            self.assertNotIn("wpm_list_switch_collection", custom_ids)

    async def test_switching_shows_the_other_collections_list(self) -> None:
        first = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        second = self.suggestion_service.create_database(
            "TV Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID + 1
        ).database
        self.suggestion_service.suggest("Alien", database_id=first.database_id)
        self.suggestion_service.suggest("Breaking Bad", database_id=second.database_id)
        interaction = FakeInteraction()
        await handle_list_suggestions(interaction, self.bot, "active", False)
        switch_button = next(
            item for item in interaction.response.sent_view.children
            if item.custom_id == "wpm_list_switch_collection"
        )

        switch_interaction = FakeInteraction()
        await switch_button.callback(interaction=switch_interaction)
        select = switch_interaction.response.edited_view.children[0]
        select._values = [str(second.database_id)]
        select_interaction = FakeInteraction()
        await select.callback(interaction=select_interaction)

        self.assertIn("Breaking Bad", select_interaction.response.edited_content)

    async def test_switching_acknowledges_the_interaction_on_a_paginated_list(self) -> None:
        # Regression coverage: Switch Collection must still ack the
        # interaction (never leave Discord waiting) when /list's own
        # results span multiple pages -- a distinct code path
        # (PaginatedListView instead of a plain discord.ui.View) from the
        # single-page case the other tests in this class cover.
        first = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        second = self.suggestion_service.create_database(
            "TV Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID + 1
        ).database
        for index in range(60):
            self.suggestion_service.suggest(
                f"A Very Long Watch Item Title Number {index} With Extra Padding To Force Pagination",
                database_id=first.database_id,
            )
        self.suggestion_service.suggest("Breaking Bad", database_id=second.database_id)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "active", False)

        self.assertIsInstance(interaction.response.sent_view, PaginatedListView)
        switch_button = next(
            item for item in interaction.response.sent_view.children
            if getattr(item, "custom_id", None) == "wpm_list_switch_collection"
        )

        switch_interaction = FakeInteraction()
        await switch_button.callback(interaction=switch_interaction)

        self.assertIsInstance(switch_interaction.response.edited_view, ListDatabaseSelectView)
        self.assertEqual(switch_interaction.response.edited_content, "Choose a collection:")


if __name__ == "__main__":
    unittest.main()
