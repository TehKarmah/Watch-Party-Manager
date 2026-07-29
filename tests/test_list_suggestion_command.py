"""Tests for /list's Watch Item Status Presentation rework: the six
status filters (Active Watch Items default, Eligible for Voting,
Rotation Cooldown, Vote Winners, Retired, All Watch Items), status
emoji on every entry, the Active Watch Items summary line, Vote
Winner win dates, embed suppression, and pagination.
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
from watch_party_manager.domain.suggestion_database_configuration import (
    CandidateSelectionMode,
    SuggestionDatabaseConfiguration,
    SuggestionRulesConfig,
)
from watch_party_manager.domain.watch_item import MediaType, MetadataProvider, WatchItem, WatchItemStatus
from watch_party_manager.domain.watch_item_journey import WatchItemJourney
from watch_party_manager.persistence.rotation_repository import JsonRotationRepository
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.collection_eligibility_service import (
    CollectionEligibility,
    CollectionEligibilityService,
)
from watch_party_manager.services.permission_service import PermissionService
from watch_party_manager.services.rotation_service import RotationService
from watch_party_manager.services.suggestion_display_status import SuggestionDisplayStatus
from watch_party_manager.services.suggestion_service import SuggestionService

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
        self.eligible_item = make_item(1)
        self.cooldown_item = make_item(2)
        self.winner_item = make_item(3, status=WatchItemStatus.VOTE_WINNER)
        self.retired_item = make_item(4, status=WatchItemStatus.ARCHIVED)
        self.eligibility = CollectionEligibility(
            database_id=1,
            mode=CandidateSelectionMode.ROTATION_POOL,
            eligible=(self.eligible_item,),
            rotation_cooldown=(self.cooldown_item,),
            vote_winners=(self.winner_item,),
            retired=(self.retired_item,),
        )

    def test_active_mixes_eligible_and_cooldown(self) -> None:
        entries = resolve_suggestion_list_entries(self.eligibility, SuggestionListStatusFilter.ACTIVE)
        items_and_statuses = {(item.id, status) for item, status in entries}
        self.assertEqual(
            {
                (self.eligible_item.id, SuggestionDisplayStatus.AVAILABLE),
                (self.cooldown_item.id, SuggestionDisplayStatus.ROTATION_COOLDOWN),
            },
            items_and_statuses,
        )

    def test_eligible_shows_only_eligible(self) -> None:
        entries = resolve_suggestion_list_entries(self.eligibility, SuggestionListStatusFilter.ELIGIBLE)
        self.assertEqual([(self.eligible_item, SuggestionDisplayStatus.AVAILABLE)], entries)

    def test_rotation_cooldown_shows_only_cooldown(self) -> None:
        entries = resolve_suggestion_list_entries(self.eligibility, SuggestionListStatusFilter.ROTATION_COOLDOWN)
        self.assertEqual([(self.cooldown_item, SuggestionDisplayStatus.ROTATION_COOLDOWN)], entries)

    def test_vote_winner_shows_only_winners(self) -> None:
        entries = resolve_suggestion_list_entries(self.eligibility, SuggestionListStatusFilter.VOTE_WINNER)
        self.assertEqual([(self.winner_item, SuggestionDisplayStatus.VOTE_WINNER)], entries)

    def test_retired_shows_only_retired(self) -> None:
        entries = resolve_suggestion_list_entries(self.eligibility, SuggestionListStatusFilter.RETIRED)
        self.assertEqual([(self.retired_item, SuggestionDisplayStatus.RETIRED)], entries)

    def test_all_shows_every_bucket(self) -> None:
        entries = resolve_suggestion_list_entries(self.eligibility, SuggestionListStatusFilter.ALL)
        items_and_statuses = {(item.id, status) for item, status in entries}
        self.assertEqual(
            {
                (self.eligible_item.id, SuggestionDisplayStatus.AVAILABLE),
                (self.cooldown_item.id, SuggestionDisplayStatus.ROTATION_COOLDOWN),
                (self.winner_item.id, SuggestionDisplayStatus.VOTE_WINNER),
                (self.retired_item.id, SuggestionDisplayStatus.RETIRED),
            },
            items_and_statuses,
        )

    def test_six_filters_exist(self) -> None:
        self.assertEqual(
            {"active", "eligible", "rotation_cooldown", "vote_winner", "retired", "all"},
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

    def test_rotation_cooldown_entry_uses_the_yellow_emoji(self) -> None:
        item = WatchItem(title="Arrival", media_type=MediaType.MOVIE, id=1, release_year=2016)
        line = build_suggestion_entry_line(item, SuggestionDisplayStatus.ROTATION_COOLDOWN)
        self.assertTrue(line.startswith("🟡 Arrival (2016)"))

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
        self, suggestion_service, wash_crew_role_id=WASH_CREW_ROLE_ID, rotation_repository=None
    ) -> None:
        self.suggestion_service = suggestion_service
        self.permission_service = PermissionService(
            watch_party_member_role_id=WATCH_PARTY_MEMBER_ROLE_ID, wash_crew_role_id=wash_crew_role_id
        )
        self.wash_crew_role_id = wash_crew_role_id
        self.suggestion_database_configuration_repository = None
        self.guild_configuration_repository = FakeGuildConfigurationRepository()
        self.rotation_service = RotationService(suggestion_service, repository=rotation_repository)
        self.collection_eligibility_service = CollectionEligibilityService(suggestion_service, self.rotation_service)


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
        self.bot = FakeBot(self.suggestion_service, rotation_repository=JsonRotationRepository(root / "rotations.json"))
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
        self.suggestion_service.suggest("The Matrix", database_id=database.database_id)
        self.suggestion_service.suggest("Inception", database_id=database.database_id)
        self.suggestion_service.suggest("Arrival", database_id=database.database_id)
        self.bot.rotation_service.get_or_start_rotation(database.database_id)
        self.bot.rotation_service.record_presentation(database.database_id, [item_a.id])
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "active", False)

        message = interaction.response.sent_message
        self.assertIn("🟢 Eligible for Voting: 3", message)
        self.assertIn("🟡 Rotation Cooldown: 1", message)

    async def test_active_mixes_eligible_and_cooldown_entries(self) -> None:
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        item_a = self.suggestion_service.suggest("Alien", database_id=database.database_id).watch_item
        self.suggestion_service.suggest("The Matrix", database_id=database.database_id)
        self.suggestion_service.suggest("Inception", database_id=database.database_id)
        self.suggestion_service.suggest("Arrival", database_id=database.database_id)
        self.bot.rotation_service.get_or_start_rotation(database.database_id)
        self.bot.rotation_service.record_presentation(database.database_id, [item_a.id])
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "active", False)

        message = interaction.response.sent_message
        self.assertIn("🟡 Alien", message)
        self.assertIn("🟢 The Matrix", message)

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

    async def test_eligible_mode_shows_only_eligible_items(self) -> None:
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        item_a = self.suggestion_service.suggest("Alien", database_id=database.database_id).watch_item
        for title in ("The Matrix", "Inception", "Arrival"):
            self.suggestion_service.suggest(title, database_id=database.database_id)
        self.bot.rotation_service.get_or_start_rotation(database.database_id)
        self.bot.rotation_service.record_presentation(database.database_id, [item_a.id])
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "eligible", False)

        message = interaction.response.sent_message
        self.assertNotIn("Alien", message)
        self.assertIn("The Matrix", message)

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
    """Rotation & Collection Health goal 4: /list's Eligible/Rotation
    Cooldown buckets must be resolved through the same authoritative
    CollectionEligibilityService /vote start uses -- never disagree.
    """

    async def test_a_presented_item_moves_from_eligible_to_rotation_cooldown(self) -> None:
        # 4 total suggestions, 1 presented -- 3 remain pending, exactly
        # satisfying the default candidate count (3), so this must NOT
        # trigger a rollover (which would re-include the presented item)
        # -- see test_list_rolls_over_the_same_way_vote_start_would for
        # that scenario instead.
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        item_a = self.suggestion_service.suggest("Alien", database_id=database.database_id).watch_item
        self.suggestion_service.suggest("The Matrix", database_id=database.database_id)
        self.suggestion_service.suggest("Inception", database_id=database.database_id)
        self.suggestion_service.suggest("Arrival", database_id=database.database_id)
        self.bot.rotation_service.get_or_start_rotation(database.database_id)
        self.bot.rotation_service.record_presentation(database.database_id, [item_a.id])

        eligible_interaction = FakeInteraction()
        await handle_list_suggestions(eligible_interaction, self.bot, "eligible", False)
        cooldown_interaction = FakeInteraction()
        await handle_list_suggestions(cooldown_interaction, self.bot, "rotation_cooldown", False)

        self.assertIn("The Matrix", eligible_interaction.response.sent_message)
        self.assertNotIn("Alien", eligible_interaction.response.sent_message)
        self.assertIn("Alien", cooldown_interaction.response.sent_message)
        self.assertNotIn("The Matrix", cooldown_interaction.response.sent_message)

    async def test_rotation_cooldown_is_always_empty_for_infinite_pool(self) -> None:
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        self.configuration_repository.save(
            SuggestionDatabaseConfiguration(
                guild_id=GUILD_ID,
                database_id=database.database_id,
                display_name="Movie Night",
                suggestion_rules=SuggestionRulesConfig(candidate_selection=CandidateSelectionMode.INFINITE_POOL),
            )
        )
        item = self.suggestion_service.suggest("Alien", database_id=database.database_id).watch_item
        self.bot.rotation_service.record_presentation(database.database_id, [item.id])
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "rotation_cooldown", False)

        self.assertIn("no watch items matching Rotation Cooldown", interaction.response.sent_message)

    async def test_list_rolls_over_the_same_way_vote_start_would(self) -> None:
        # The exact Rotation & Collection Health scenario: a rotation with
        # some, but not enough, pending suggestions to satisfy the
        # configured candidate count. /list must show the post-rollover
        # set, not the stale one, exactly like /vote start would.
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        item_a = self.suggestion_service.suggest("Alien", database_id=database.database_id).watch_item
        self.suggestion_service.suggest("The Matrix", database_id=database.database_id)
        self.suggestion_service.suggest("Inception", database_id=database.database_id)
        self.bot.rotation_service.get_or_start_rotation(database.database_id)
        self.bot.rotation_service.record_presentation(database.database_id, [item_a.id])
        # Only 2 of 3 remain pending -- fewer than the default candidate
        # count of 3, so /list must trigger the same rollover /vote start
        # would and show all 3 again as eligible.
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "eligible", False)

        message = interaction.response.sent_message
        self.assertIn("Alien", message)
        self.assertIn("The Matrix", message)
        self.assertIn("Inception", message)

    async def test_rollover_syncs_the_previously_cooled_down_items_public_post(self) -> None:
        # Suggestion Status Synchronization, transition 2: /list's own
        # rollover (see test_list_rolls_over_the_same_way_vote_start_would
        # above) must resync every suggestion it returns to eligibility --
        # not just report the correct eligibility bucket back to the
        # member who happened to run /list.
        class FakeMessage:
            def __init__(self, message_id: int) -> None:
                self.id = message_id
                self.edited_embed = None
                self.edit_calls = 0

            async def edit(self, *, embed=None, view=None) -> None:
                self.edited_embed = embed
                self.edit_calls += 1

        class FakeChannel:
            def __init__(self, message) -> None:
                self._message = message

            async def fetch_message(self, message_id):
                return self._message

        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        item_a = self.suggestion_service.suggest("Alien", database_id=database.database_id).watch_item
        self.suggestion_service.suggest("The Matrix", database_id=database.database_id)
        self.suggestion_service.suggest("Inception", database_id=database.database_id)
        self.suggestion_service.set_confirmation_post_reference(item_a.id, GUILD_ID, CHANNEL_ID, 555)
        self.bot.rotation_service.get_or_start_rotation(database.database_id)
        self.bot.rotation_service.record_presentation(database.database_id, [item_a.id])

        message = FakeMessage(message_id=555)
        channel = FakeChannel(message)
        self.bot.get_channel = lambda channel_id: channel

        # Only 2 of 3 remain pending -- fewer than the default candidate
        # count of 3, so this triggers the same rollover as the test above.
        interaction = FakeInteraction()
        await handle_list_suggestions(interaction, self.bot, "eligible", False)

        self.assertEqual(1, message.edit_calls)
        status_field = next(field for field in message.edited_embed.fields if field.name == "Status")
        self.assertEqual("🟢 Available", status_field.value)

    async def test_vote_winner_filter_never_bootstraps_a_rotation(self) -> None:
        # VOTE_WINNER/RETIRED are terminal, rotation-unaffected buckets --
        # peek(), not resolve(), so simply checking Vote Winners must
        # never create rotation state for a database that's never had a
        # vote yet.
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        self.suggestion_service.suggest("Alien", database_id=database.database_id)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "vote_winner", False)

        self.assertIsNone(self.bot.rotation_service.get_open_rotation(database.database_id))


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


if __name__ == "__main__":
    unittest.main()
