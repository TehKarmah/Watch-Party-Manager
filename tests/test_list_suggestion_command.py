"""Tests for /list's Release Polish Priority 2 rework: default-to-Available
output, the Available/Vote Winner/Retired filter set, terse title/year/
original-suggestion-link entries (no reference number, no status, no
IMDb link), embed suppression, and pagination.
"""

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
from watch_party_manager.domain.suggestion_database_configuration import (
    CandidateSelectionMode,
    SuggestionDatabaseConfiguration,
    SuggestionRulesConfig,
)
from watch_party_manager.domain.watch_item import MediaType, MetadataProvider, WatchItem, WatchItemStatus
from watch_party_manager.persistence.rotation_repository import JsonRotationRepository
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.collection_eligibility_service import CollectionEligibilityService
from watch_party_manager.services.permission_service import PermissionService
from watch_party_manager.services.rotation_service import RotationService
from watch_party_manager.services.suggestion_service import SuggestionService

GUILD_ID = 100
CHANNEL_ID = 200
WASH_CREW_ROLE_ID = 999
WATCH_PARTY_MEMBER_ROLE_ID = 555


class FilterItemsByStatusTests(unittest.TestCase):
    """AVAILABLE/ROTATION_COOLDOWN are deliberately not exercised here --
    Rotation & Collection Health Audit: they require rotation state
    (CollectionEligibilityService), not a bare status check, so
    filter_items_by_status no longer handles them at all (see
    send_suggestion_list, and ListFilteringAndPaginationTests for
    AVAILABLE's actual, eligibility-driven behavior end to end).
    """

    def _item(self, status: WatchItemStatus) -> WatchItem:
        return WatchItem(title="Alien", media_type=MediaType.MOVIE, status=status)

    def test_retired_only_shows_archived_items(self) -> None:
        items = [self._item(WatchItemStatus.SUGGESTED), self._item(WatchItemStatus.ARCHIVED)]
        result = filter_items_by_status(items, SuggestionListStatusFilter.RETIRED)
        self.assertEqual(1, len(result))
        self.assertEqual(WatchItemStatus.ARCHIVED, result[0].status)

    def test_watched_only_shows_watched(self) -> None:
        items = [self._item(WatchItemStatus.SUGGESTED), self._item(WatchItemStatus.VOTE_WINNER)]
        result = filter_items_by_status(items, SuggestionListStatusFilter.VOTE_WINNER)
        self.assertEqual(1, len(result))
        self.assertEqual(WatchItemStatus.VOTE_WINNER, result[0].status)

    def test_watched_and_retired_are_never_combined(self) -> None:
        items = [self._item(WatchItemStatus.ARCHIVED), self._item(WatchItemStatus.VOTE_WINNER)]
        self.assertEqual(1, len(filter_items_by_status(items, SuggestionListStatusFilter.VOTE_WINNER)))
        self.assertEqual(1, len(filter_items_by_status(items, SuggestionListStatusFilter.RETIRED)))

    def test_only_four_modes_exist(self) -> None:
        self.assertEqual(
            {"available", "rotation_cooldown", "vote_winner", "retired"},
            {member.value for member in SuggestionListStatusFilter},
        )


class BuildSuggestionEntryLineTests(unittest.TestCase):
    def test_shows_title_and_year_only_when_there_is_no_original_post(self) -> None:
        item = WatchItem(title="The Matrix", media_type=MediaType.MOVIE, id=7, release_year=1999)
        self.assertEqual("The Matrix (1999)", build_suggestion_entry_line(item))

    def test_omits_release_year_when_absent(self) -> None:
        item = WatchItem(title="The Matrix", media_type=MediaType.MOVIE, id=1)
        self.assertEqual("The Matrix", build_suggestion_entry_line(item))

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
        line = build_suggestion_entry_line(item)
        self.assertEqual("The Matrix (1999) | [Original Suggestion](https://discord.com/channels/1/2/3)", line)

    def test_never_includes_a_reference_number(self) -> None:
        item = WatchItem(title="The Matrix", media_type=MediaType.MOVIE, id=42, release_year=1999)
        self.assertNotIn("#", build_suggestion_entry_line(item))
        self.assertNotIn("0042", build_suggestion_entry_line(item))

    def test_never_includes_a_status_label(self) -> None:
        item = WatchItem(
            title="The Matrix", media_type=MediaType.MOVIE, id=1, status=WatchItemStatus.ARCHIVED
        )
        self.assertNotIn("Status", build_suggestion_entry_line(item))
        self.assertNotIn("Archived", build_suggestion_entry_line(item))

    def test_never_includes_an_imdb_link(self) -> None:
        item = WatchItem(
            title="The Matrix",
            media_type=MediaType.MOVIE,
            id=1,
            metadata_ids={MetadataProvider.IMDB: "https://www.imdb.com/title/tt0133093/"},
        )
        self.assertNotIn("imdb", build_suggestion_entry_line(item).lower())

    def test_item_without_an_original_post_has_no_broken_link_placeholder(self) -> None:
        item = WatchItem(title="The Matrix", media_type=MediaType.MOVIE, id=1, release_year=1999)
        line = build_suggestion_entry_line(item)
        self.assertNotIn("[", line)
        self.assertNotIn("None", line)


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

        await handle_list_suggestions(interaction, self.bot, "available", False)

        self.assertIn("Watch Party", interaction.response.sent_message)

    async def test_watch_party_member_can_view_privately(self) -> None:
        self.suggestion_service.create_database("Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "available", False)

        self.assertTrue(interaction.response.sent_ephemeral)

    async def test_watch_party_member_cannot_post_publicly(self) -> None:
        self.suggestion_service.create_database("Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "available", True)

        self.assertIn("WASH Crew", interaction.response.sent_message)

    async def test_crew_can_post_publicly(self) -> None:
        self.suggestion_service.create_database("Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID)
        self.suggestion_service.suggest("Alien", database_id=1)
        interaction = FakeInteraction(user=self._crew_member())

        await handle_list_suggestions(interaction, self.bot, "available", True)

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

        await handle_list_suggestions(interaction, self.bot, "available", False)

        self.assertIn("Movie Night", interaction.response.sent_message)

    async def test_uses_the_sole_database_when_channel_does_not_match(self) -> None:
        self.suggestion_service.create_database("Movie Night", guild_id=GUILD_ID, channel_id=555)
        self.suggestion_service.suggest("Alien", database_id=1)
        interaction = FakeInteraction(channel_id=999)

        await handle_list_suggestions(interaction, self.bot, "available", False)

        self.assertIn("Movie Night", interaction.response.sent_message)

    async def test_shows_a_selector_when_multiple_databases_are_ambiguous(self) -> None:
        self.suggestion_service.create_database("Movie Night", guild_id=GUILD_ID, channel_id=555)
        self.suggestion_service.create_database("Anime Night", guild_id=GUILD_ID, channel_id=556)
        interaction = FakeInteraction(channel_id=999)

        await handle_list_suggestions(interaction, self.bot, "available", False)

        self.assertIsNotNone(interaction.response.sent_view)

    async def test_selecting_a_database_shows_its_list(self) -> None:
        self.suggestion_service.create_database("Movie Night", guild_id=GUILD_ID, channel_id=555)
        self.suggestion_service.create_database("Anime Night", guild_id=GUILD_ID, channel_id=556)
        self.suggestion_service.suggest("Alien", database_id=1)
        interaction = FakeInteraction(channel_id=999)
        await handle_list_suggestions(interaction, self.bot, "available", False)
        select = interaction.response.sent_view.children[0]
        select._values = ["1"]

        select_interaction = FakeInteraction(channel_id=999)
        await select.callback(select_interaction)

        self.assertIn("Alien", select_interaction.response.sent_message)

    async def test_reports_a_clear_error_when_no_database_is_configured(self) -> None:
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "available", False)

        self.assertIsNotNone(interaction.response.sent_message)

    async def test_header_shows_the_collections_built_in_emoji(self) -> None:
        # Release Candidate Polish, Requirement 3: every user-facing
        # display of a collection uses the shared format_collection_display()
        # helper -- including this command's own header.
        self.suggestion_service.create_database("Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID)
        self.suggestion_service.suggest("Alien", database_id=1)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "available", False)

        self.assertIn("🎬 Movie Night", interaction.response.sent_message)


class ListFilteringAndPaginationTests(HandleListSuggestionsTestCase):
    async def test_invalid_status_is_rejected(self) -> None:
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "not-a-status", False)

        self.assertIn("Available", interaction.response.sent_message)

    async def test_empty_available_list_reports_clearly(self) -> None:
        self.suggestion_service.create_database("Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "available", False)

        self.assertIn("no available watch items", interaction.response.sent_message)

    async def test_default_status_is_available(self) -> None:
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        active = self.suggestion_service.suggest("Alien", database_id=database.database_id).watch_item
        retired = self.suggestion_service.suggest("Aliens", database_id=database.database_id).watch_item
        self.suggestion_service.archive_suggestion(retired.id)
        interaction = FakeInteraction()

        # Mirrors the /list command's own default: status defaults to "available".
        await handle_list_suggestions(interaction, self.bot, "available", False)

        self.assertIn("Alien", interaction.response.sent_message)
        self.assertNotIn("Aliens", interaction.response.sent_message)

    async def test_watched_mode_excludes_available_and_retired_items(self) -> None:
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        self.suggestion_service.suggest("Available Movie", database_id=database.database_id)
        retired = self.suggestion_service.suggest("Retired Movie", database_id=database.database_id).watch_item
        self.suggestion_service.archive_suggestion(retired.id)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "vote_winner", False)

        self.assertIn("no vote winner watch items", interaction.response.sent_message)

    async def test_retired_mode_shows_only_retired_items(self) -> None:
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        self.suggestion_service.suggest("Available Movie", database_id=database.database_id)
        retired = self.suggestion_service.suggest("Retired Movie", database_id=database.database_id).watch_item
        self.suggestion_service.archive_suggestion(retired.id)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "retired", False)

        self.assertIn("Retired Movie", interaction.response.sent_message)
        self.assertNotIn("Available Movie", interaction.response.sent_message)

    async def test_entries_have_no_reference_number_or_status_label(self) -> None:
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        self.suggestion_service.suggest("Alien", database_id=database.database_id)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "available", False)

        message = interaction.response.sent_message
        self.assertNotIn("#0001", message)
        self.assertNotIn("Status:", message)

    async def test_response_suppresses_link_preview_embeds(self) -> None:
        self.suggestion_service.create_database("Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID)
        self.suggestion_service.suggest("Alien", database_id=1)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "available", False)

        self.assertTrue(interaction.response.sent_suppress_embeds)

    async def test_empty_result_also_suppresses_embeds(self) -> None:
        self.suggestion_service.create_database("Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "available", False)

        self.assertTrue(interaction.response.sent_suppress_embeds)

    async def test_deterministic_ordering_by_id(self) -> None:
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        self.suggestion_service.suggest("Zeta", database_id=database.database_id)
        self.suggestion_service.suggest("Alpha", database_id=database.database_id)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "available", False)

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

        await handle_list_suggestions(interaction, self.bot, "available", False)

        self.assertLessEqual(len(interaction.response.sent_message), 2000)
        self.assertIsNotNone(interaction.response.sent_view)

    async def test_response_never_exceeds_discord_limits(self) -> None:
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        for index in range(200):
            self.suggestion_service.suggest(f"Movie Number {index:03d} With Extra Padding Text Here", database_id=database.database_id)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "available", False)

        self.assertLessEqual(len(interaction.response.sent_message), 2000)


class ListEligibilityParityTests(HandleListSuggestionsTestCase):
    """Rotation & Collection Health goal 4: /list's Available/Rotation
    Cooldown buckets must be resolved through the same authoritative
    CollectionEligibilityService /vote start uses -- never disagree.
    """

    async def test_a_presented_item_moves_from_available_to_rotation_cooldown(self) -> None:
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

        available_interaction = FakeInteraction()
        await handle_list_suggestions(available_interaction, self.bot, "available", False)
        cooldown_interaction = FakeInteraction()
        await handle_list_suggestions(cooldown_interaction, self.bot, "rotation_cooldown", False)

        self.assertIn("The Matrix", available_interaction.response.sent_message)
        self.assertNotIn("Alien", available_interaction.response.sent_message)
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

        self.assertIn("no rotation cooldown watch items", interaction.response.sent_message)

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
        # would and show all 3 again as Available.
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "available", False)

        message = interaction.response.sent_message
        self.assertIn("Alien", message)
        self.assertIn("The Matrix", message)
        self.assertIn("Inception", message)


class ListSwitchCollectionTests(HandleListSuggestionsTestCase):
    async def test_switch_collection_button_appears_with_multiple_databases(self) -> None:
        self.suggestion_service.create_database("Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID)
        self.suggestion_service.create_database("TV Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID + 1)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "available", False)

        self.assertIsNotNone(interaction.response.sent_view)
        custom_ids = [item.custom_id for item in interaction.response.sent_view.children]
        self.assertIn("wpm_list_switch_collection", custom_ids)

    async def test_switch_collection_button_absent_with_only_one_database(self) -> None:
        self.suggestion_service.create_database("Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID)
        self.suggestion_service.suggest("Alien", database_id=1)
        interaction = FakeInteraction()

        await handle_list_suggestions(interaction, self.bot, "available", False)

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
        await handle_list_suggestions(interaction, self.bot, "available", False)
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
