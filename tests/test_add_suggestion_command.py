"""Tests for FR-033A's /add rewiring: duplicate detection, re-suggestion
rules, and confirmation-post handling."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from datetime import date

from watch_party_manager.bot import (
    AddSuggestionOutcomeKind,
    build_duplicate_match_line,
    decide_add_suggestion_outcome,
    extract_year_from_title_suffix,
    handle_add_suggestion,
)
from watch_party_manager.domain.guild_configuration import GuildChannelsConfig, GuildConfiguration
from watch_party_manager.domain.suggestion_database_configuration import (
    CandidateSelectionMode,
    SuggestionAdmissionMode,
    SuggestionDatabaseChannelsConfig,
    SuggestionDatabaseConfiguration,
    SuggestionRulesConfig,
)
from watch_party_manager.domain.watch_item import MediaType, MetadataProvider, WatchItem, WatchItemStatus
from watch_party_manager.domain.watch_item_journey import WatchItemJourney
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.rotation_repository import JsonRotationRepository
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.collection_eligibility_service import CollectionEligibilityService
from watch_party_manager.services.duplicate_detection_service import find_duplicates
from watch_party_manager.services.permission_service import PermissionService
from watch_party_manager.services.eligible_pool_warning_service import EligiblePoolWarningService
from watch_party_manager.persistence.eligible_pool_warning_state_repository import (
    JsonEligiblePoolWarningStateRepository,
)
from watch_party_manager.services.rotation_service import RotationService
from watch_party_manager.services.suggestion_input_service import SuggestionInputService
from watch_party_manager.services.suggestion_service import SuggestionService

GUILD_ID = 100
CHANNEL_ID = 200
WASH_CREW_ROLE_ID = 999
WATCH_PARTY_MEMBER_ROLE_ID = 555


class ExtractYearFromTitleSuffixTests(unittest.TestCase):
    def test_extracts_a_trailing_year(self) -> None:
        self.assertEqual(1999, extract_year_from_title_suffix("The Matrix (1999)"))

    def test_returns_none_without_a_year_suffix(self) -> None:
        self.assertIsNone(extract_year_from_title_suffix("The Matrix"))

    def test_returns_none_for_a_year_mid_title(self) -> None:
        self.assertIsNone(extract_year_from_title_suffix("2001: A Space Odyssey"))


class DecideAddSuggestionOutcomeTests(unittest.TestCase):
    def _matches(self, title="Alien", year=1979, existing_year=1979, status=None, rejected=(), item_id=1):
        existing = WatchItem(
            title=title,
            media_type=MediaType.MOVIE,
            release_year=existing_year,
            status=status or WatchItemStatus.SUGGESTED,
            id=item_id,
            journey=WatchItemJourney(rejected_by_discord_user_ids=rejected),
        )
        return find_duplicates(title=title, release_year=year, imdb_url=None, existing_items=[existing])

    def test_no_matches_proceeds(self) -> None:
        result = find_duplicates(title="Alien", release_year=1979, imdb_url=None, existing_items=[])

        decision = decide_add_suggestion_outcome(result, is_crew=False)

        self.assertEqual(AddSuggestionOutcomeKind.PROCEED, decision.kind)

    def test_active_match_blocks_even_for_crew(self) -> None:
        result = self._matches(status=WatchItemStatus.SUGGESTED)

        decision = decide_add_suggestion_outcome(result, is_crew=True)

        self.assertEqual(AddSuggestionOutcomeKind.BLOCKED_ACTIVE, decision.kind)

    def test_archived_rejected_match_blocks_regular_members(self) -> None:
        result = self._matches(status=WatchItemStatus.ARCHIVED, rejected=(1, 2))

        decision = decide_add_suggestion_outcome(result, is_crew=False)

        self.assertEqual(AddSuggestionOutcomeKind.BLOCKED_NO_CREW_OVERRIDE, decision.kind)

    def test_archived_rejected_match_offers_crew_reactivation(self) -> None:
        result = self._matches(status=WatchItemStatus.ARCHIVED, rejected=(1, 2))

        decision = decide_add_suggestion_outcome(result, is_crew=True)

        self.assertEqual(AddSuggestionOutcomeKind.NEEDS_CREW_REACTIVATION_CONFIRM, decision.kind)
        # UX Polish: grammar fix -- "This title has been archived...",
        # not the previous "This title has archived..." (missing "been").
        self.assertIn("This title has been archived after being rejected", decision.message)

    def test_watched_match_offers_crew_reactivation(self) -> None:
        result = self._matches(status=WatchItemStatus.VOTE_WINNER)

        decision = decide_add_suggestion_outcome(result, is_crew=True)

        self.assertEqual(AddSuggestionOutcomeKind.NEEDS_CREW_REACTIVATION_CONFIRM, decision.kind)

    def test_watched_match_blocks_regular_members(self) -> None:
        result = self._matches(status=WatchItemStatus.VOTE_WINNER)

        decision = decide_add_suggestion_outcome(result, is_crew=False)

        self.assertEqual(AddSuggestionOutcomeKind.BLOCKED_NO_CREW_OVERRIDE, decision.kind)

    def test_possible_duplicate_blocks_regular_members(self) -> None:
        result = self._matches(year=None)

        decision = decide_add_suggestion_outcome(result, is_crew=False)

        self.assertEqual(AddSuggestionOutcomeKind.BLOCKED_POSSIBLE_NO_CREW, decision.kind)

    def test_possible_duplicate_offers_crew_override(self) -> None:
        result = self._matches(year=None)

        decision = decide_add_suggestion_outcome(result, is_crew=True)

        self.assertEqual(AddSuggestionOutcomeKind.NEEDS_CREW_POSSIBLE_CONFIRM, decision.kind)

    def test_watched_match_message_uses_the_vote_winner_block(self) -> None:
        # UI Polish (Duplicate/Reactivation View): a Vote Winner match's
        # message uses the richer Reference/status/links block, not the
        # old single-line "Reference | Title | imdb_url | status" format.
        result = self._matches(status=WatchItemStatus.VOTE_WINNER)

        decision = decide_add_suggestion_outcome(result, is_crew=True)

        self.assertIn("🏆 Vote Winner", decision.message)
        self.assertIn(f"Reference #{1:04d}", decision.message)


class BuildDuplicateMatchLineTests(unittest.TestCase):
    """UI Polish (Duplicate/Reactivation View): a Vote Winner match gets
    Reference, its 🏆 Vote Winner status (with win date, when recorded),
    and Original Suggestion/IMDb as clickable links, replacing the old
    single-line, raw-IMDb-URL format. Every other category is unchanged,
    except that it too now shows a labeled `[Original Suggestion](url)`
    link (to the original Discord message) instead of ever exposing a
    bare IMDb URL -- the IMDb preview card already shown alongside these
    responses covers IMDb linking, so this line does not repeat it.
    """

    def _match(self, category, **item_kwargs):
        from watch_party_manager.services.duplicate_detection_service import DuplicateMatch, DuplicateMatchCategory, DuplicateMatchKind

        item = WatchItem(title="Zombieland", media_type=MediaType.MOVIE, id=2, release_year=2009, **item_kwargs)
        return DuplicateMatch(watch_item=item, category=category, kind=DuplicateMatchKind.TITLE_AND_YEAR)

    def test_active_match_with_an_original_suggestion_link_shows_it_instead_of_a_raw_url(self) -> None:
        from watch_party_manager.services.duplicate_detection_service import DuplicateMatchCategory

        match = self._match(
            DuplicateMatchCategory.ACTIVE,
            status=WatchItemStatus.SUGGESTED,
            metadata_ids={MetadataProvider.IMDB: "https://www.imdb.com/title/tt1234567/"},
            guild_id=1,
            channel_id=2,
            message_id=3,
        )
        line = build_duplicate_match_line(match)
        # UX Polish: the established display-status vocabulary ("🟢
        # Available"), not the raw internal enum value ("Suggested").
        self.assertEqual(
            "Reference #0002 | Zombieland | [Original Suggestion](https://discord.com/channels/1/2/3) | "
            "status: 🟢 Available",
            line,
        )
        self.assertNotIn("https://www.imdb.com/title/tt1234567/", line)

    def test_active_match_without_an_original_suggestion_link_omits_it_gracefully(self) -> None:
        # A legacy suggestion recorded before message linking existed --
        # no guild_id/channel_id/message_id to build a link from.
        from watch_party_manager.services.duplicate_detection_service import DuplicateMatchCategory

        match = self._match(
            DuplicateMatchCategory.ACTIVE,
            status=WatchItemStatus.SUGGESTED,
            metadata_ids={MetadataProvider.IMDB: "https://www.imdb.com/title/tt1234567/"},
        )
        line = build_duplicate_match_line(match)
        self.assertEqual("Reference #0002 | Zombieland | status: 🟢 Available", line)
        self.assertNotIn("https://www.imdb.com/title/tt1234567/", line)

    def test_archived_other_match_keeps_the_original_single_line_format(self) -> None:
        from watch_party_manager.services.duplicate_detection_service import DuplicateMatchCategory

        match = self._match(DuplicateMatchCategory.ARCHIVED_OTHER, status=WatchItemStatus.ARCHIVED)
        line = build_duplicate_match_line(match)
        self.assertEqual("Reference #0002 | Zombieland | status: 🗄️ Retired", line)

    def test_vote_winner_match_uses_the_new_block(self) -> None:
        from watch_party_manager.services.duplicate_detection_service import DuplicateMatchCategory

        match = self._match(
            DuplicateMatchCategory.VOTE_WINNER,
            status=WatchItemStatus.VOTE_WINNER,
            journey=WatchItemJourney(last_won_date=date(2026, 7, 28)),
            guild_id=1,
            channel_id=2,
            message_id=3,
            metadata_ids={MetadataProvider.IMDB: "https://www.imdb.com/title/tt1234567/"},
        )
        line = build_duplicate_match_line(match)
        self.assertEqual(
            "Reference #0002\n"
            "🏆 Vote Winner\n"
            "Won: July 28, 2026\n"
            "[Original Suggestion](https://discord.com/channels/1/2/3)\n"
            "[IMDb](https://www.imdb.com/title/tt1234567/)",
            line,
        )

    def test_vote_winner_match_never_shows_the_raw_imdb_url_inline(self) -> None:
        from watch_party_manager.services.duplicate_detection_service import DuplicateMatchCategory

        match = self._match(
            DuplicateMatchCategory.VOTE_WINNER,
            status=WatchItemStatus.VOTE_WINNER,
            metadata_ids={MetadataProvider.IMDB: "https://www.imdb.com/title/tt1234567/"},
        )
        line = build_duplicate_match_line(match)
        self.assertNotIn("https://www.imdb.com/title/tt1234567/ |", line)
        self.assertIn("[IMDb](https://www.imdb.com/title/tt1234567/)", line)

    def test_legacy_vote_winner_without_a_won_date_omits_the_won_line(self) -> None:
        from watch_party_manager.services.duplicate_detection_service import DuplicateMatchCategory

        match = self._match(DuplicateMatchCategory.VOTE_WINNER, status=WatchItemStatus.VOTE_WINNER)
        line = build_duplicate_match_line(match)
        self.assertNotIn("Won", line)

    def test_vote_winner_without_an_original_post_omits_that_line(self) -> None:
        from watch_party_manager.services.duplicate_detection_service import DuplicateMatchCategory

        match = self._match(DuplicateMatchCategory.VOTE_WINNER, status=WatchItemStatus.VOTE_WINNER)
        line = build_duplicate_match_line(match)
        self.assertNotIn("Original Suggestion", line)

    def test_vote_winner_without_an_imdb_link_omits_that_line(self) -> None:
        from watch_party_manager.services.duplicate_detection_service import DuplicateMatchCategory

        match = self._match(
            DuplicateMatchCategory.VOTE_WINNER, status=WatchItemStatus.VOTE_WINNER, guild_id=1, channel_id=2, message_id=3
        )
        line = build_duplicate_match_line(match)
        self.assertNotIn("IMDb", line)
        self.assertIn("Original Suggestion", line)


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, role_ids=(), *, user_id: int = 1) -> None:
        self.roles = [FakeRole(role_id) for role_id in role_ids]
        self.id = user_id
        self.mention = f"<@{user_id}>"


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


class FakeGuildConfigurationRepository:
    """Reports "no guild configuration saved" by default --
    EligiblePoolWarningService's destination resolution then has no
    Admin/Home Channel to resolve, so it never fires (see
    EligiblePoolWarningService._resolve_destination_channel_id). A caller
    with an actual GuildConfiguration to report (e.g. one with an Admin
    Channel configured) may pass it in.
    """

    def __init__(self, configuration=None) -> None:
        self._configuration = configuration

    def get(self, guild_id: int):
        return self._configuration


class FakeBot:
    def __init__(
        self,
        suggestion_service,
        configuration_repository,
        wash_crew_role_id=WASH_CREW_ROLE_ID,
        rotation_repository=None,
        guild_configuration_repository=None,
    ) -> None:
        self.suggestion_service = suggestion_service
        self.suggestion_input_service = SuggestionInputService()
        self.suggestion_database_configuration_repository = configuration_repository
        self.permission_service = PermissionService(
            watch_party_member_role_id=WATCH_PARTY_MEMBER_ROLE_ID, wash_crew_role_id=wash_crew_role_id
        )
        self.wash_crew_role_id = wash_crew_role_id
        self.rotation_service = RotationService(suggestion_service, repository=rotation_repository)
        self.collection_eligibility_service = CollectionEligibilityService(suggestion_service, None)
        self.guild_configuration_repository = guild_configuration_repository or FakeGuildConfigurationRepository()
        self.eligible_pool_warning_service = EligiblePoolWarningService(
            self.collection_eligibility_service,
            JsonEligiblePoolWarningStateRepository(Path(tempfile.mkdtemp()) / "eligible_pool_warning_state.json"),
            self.guild_configuration_repository,
            configuration_repository,
            suggestion_service,
        )
        self._channels = {}

    def register_channel(self, channel) -> None:
        self._channels[channel.id] = channel

    def get_channel(self, channel_id):
        return self._channels.get(channel_id)

    async def fetch_channel(self, channel_id):
        channel = self._channels.get(channel_id)
        if channel is None:
            raise RuntimeError("channel not found")
        return channel


class FakeMessage:
    def __init__(self, message_id=300) -> None:
        self.id = message_id
        self.edited = None

    async def edit(self, embed=None, view=None) -> None:
        self.edited = (embed, view)


class FakeChannel:
    def __init__(self, channel_id) -> None:
        self.id = channel_id
        self.sent = []
        self._next_message_id = 300

    async def send(self, embed=None, view=None):
        message = FakeMessage(self._next_message_id)
        self._next_message_id += 1
        self.sent.append((embed, view, message))
        return message

    async def fetch_message(self, message_id):
        for _, _, message in self.sent:
            if message.id == message_id:
                return message
        raise RuntimeError("message not found")


class HandleAddSuggestionTestCase(unittest.IsolatedAsyncioTestCase):
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
        self.bot = FakeBot(
            self.suggestion_service,
            self.configuration_repository,
            rotation_repository=JsonRotationRepository(root / "rotations.json"),
        )
        self.database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _crew_member(self) -> FakeMember:
        return FakeMember([WASH_CREW_ROLE_ID])


class AddNoDestinationTests(HandleAddSuggestionTestCase):
    async def test_add_without_a_configured_suggestion_channel_still_saves_and_explains(self) -> None:
        interaction = FakeInteraction()

        await handle_add_suggestion(interaction, self.bot, "Alien", None, None)

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIn("added", interaction.response.sent_message.lower())
        self.assertIn("No public confirmation post", interaction.response.sent_message)
        # UX Polish: this collection-scoped message must say "collection",
        # the established user-facing term, not the internal "database".
        self.assertIn("configured for this collection", interaction.response.sent_message)
        self.assertNotIn("this database", interaction.response.sent_message)
        self.assertEqual(1, len(self.suggestion_service.get_suggestions_for_database(self.database.database_id)))

    async def test_add_records_the_submitter_and_creation_date(self) -> None:
        # FR-034: /add is the one path that populates journey.original_suggester
        # (the submitting member's Discord user ID) and journey.suggestion_date.
        interaction = FakeInteraction(user=FakeMember([WATCH_PARTY_MEMBER_ROLE_ID], user_id=42))

        await handle_add_suggestion(interaction, self.bot, "Alien", None, None)

        item = self.suggestion_service.get_suggestions_for_database(self.database.database_id)[0]
        self.assertEqual(item.journey.original_suggester, "42")
        self.assertIsNotNone(item.journey.suggestion_date)

    async def test_non_watch_party_member_is_rejected(self) -> None:
        interaction = FakeInteraction(user=FakeMember([]))

        await handle_add_suggestion(interaction, self.bot, "Alien", None, None)

        self.assertEqual(0, len(self.suggestion_service.get_suggestions_for_database(self.database.database_id)))

    async def test_ephemeral_ack_omits_the_link_when_no_post_was_created(self) -> None:
        # Graceful omission: nothing to link to when no suggestion
        # channel is configured, so no public confirmation post exists.
        interaction = FakeInteraction()

        await handle_add_suggestion(interaction, self.bot, "Alien", None, None)

        self.assertNotIn("[View Suggestion]", interaction.response.sent_message)


class AddWithDestinationTests(HandleAddSuggestionTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.configuration_repository.save(
            SuggestionDatabaseConfiguration(
                guild_id=GUILD_ID,
                database_id=self.database.database_id,
                display_name="Movie Night",
                channels=SuggestionDatabaseChannelsConfig(suggestion_channel_id=777),
            )
        )
        self.confirmation_channel = FakeChannel(777)
        self.bot.register_channel(self.confirmation_channel)

    async def test_add_posts_a_public_confirmation(self) -> None:
        interaction = FakeInteraction()

        await handle_add_suggestion(interaction, self.bot, "Alien", None, 1979)

        self.assertTrue(interaction.response.sent_ephemeral)
        # Just the confirmation post: the Rotation Low-Pool notification
        # (Rotation & Collection Health) posts to the guild's configured
        # Admin/Home Channel, never a collection's own suggestion channel
        # -- and no guild configuration is saved in this test class, so
        # it has no destination to resolve and never fires. See
        # AdmissionModeAndLowPoolReminderTests for the notification
        # actually firing.
        self.assertEqual(1, len(self.confirmation_channel.sent))

    async def test_ephemeral_ack_links_to_the_post_when_added_from_outside_its_destination(self) -> None:
        # UX Polish: /add invoked from CHANNEL_ID (200) while the
        # collection's suggestion channel is 777 -- the ephemeral ack
        # must link straight to the post it just created, not just
        # report a title and reference number.
        interaction = FakeInteraction()

        await handle_add_suggestion(interaction, self.bot, "Alien", None, 1979)

        item = self.suggestion_service.get_suggestions_for_database(self.database.database_id)[0]
        expected_link = f"https://discord.com/channels/{GUILD_ID}/777/{item.message_id}"
        self.assertIn(f"[View Suggestion]({expected_link})", interaction.response.sent_message)

    async def test_ephemeral_ack_omits_the_link_when_added_from_its_own_destination(self) -> None:
        # Avoid redundant wording -- a member already looking at the
        # destination channel doesn't need a link back to it.
        interaction = FakeInteraction(channel_id=777)

        await handle_add_suggestion(interaction, self.bot, "Alien", None, 1979)

        self.assertNotIn("[View Suggestion]", interaction.response.sent_message)

    async def test_active_duplicate_is_blocked(self) -> None:
        await handle_add_suggestion(FakeInteraction(), self.bot, "Alien", None, 1979)
        second_interaction = FakeInteraction()

        await handle_add_suggestion(second_interaction, self.bot, "Alien", None, 1979)

        self.assertIn("already in this collection", second_interaction.response.sent_message)
        self.assertIn("Reference #", second_interaction.response.sent_message)
        # 1 (confirmation from the first, successful add only): the
        # second, blocked call never reaches finish_add_or_reactivate at
        # all.
        self.assertEqual(1, len(self.confirmation_channel.sent))

    async def test_possible_duplicate_blocks_regular_member(self) -> None:
        await handle_add_suggestion(FakeInteraction(), self.bot, "Alien", None, 1979)
        second_interaction = FakeInteraction()

        await handle_add_suggestion(second_interaction, self.bot, "Alien", None, None)

        self.assertIn("might be a duplicate", second_interaction.response.sent_message)
        self.assertIsNone(second_interaction.response.sent_view)

    async def test_possible_duplicate_offers_crew_a_confirmation_view(self) -> None:
        await handle_add_suggestion(FakeInteraction(), self.bot, "Alien", None, 1979)
        crew_interaction = FakeInteraction(user=self._crew_member())

        await handle_add_suggestion(crew_interaction, self.bot, "Alien", None, None)

        self.assertIsNotNone(crew_interaction.response.sent_view)
        # UX Polish: "Add Anyway" isn't destructive (it just proceeds
        # past a possible-duplicate warning), so its confirm button must
        # not use the same alarming red/danger style /remove's genuinely
        # destructive confirmation uses.
        import discord

        self.assertEqual(crew_interaction.response.sent_view.children[0].style, discord.ButtonStyle.primary)

    async def test_crew_confirming_possible_duplicate_with_a_distinct_title_creates_a_new_suggestion(self) -> None:
        # "Alien" (1979) already exists. A candidate with the SAME exact
        # title and no year is a possible duplicate; confirming it would
        # collide with SuggestionService.suggest()'s own pre-existing
        # exact-title uniqueness constraint (see
        # test_crew_confirming_an_exact_title_possible_duplicate_still_blocks_on_the_uniqueness_constraint),
        # so this exercises the "add anyway" path with a distinct title
        # that still normalizes to a *different* comparison key just
        # like a genuinely different movie would (e.g. a sequel).
        await handle_add_suggestion(FakeInteraction(), self.bot, "Alien", None, 1979)
        crew_interaction = FakeInteraction(user=self._crew_member())
        await handle_add_suggestion(crew_interaction, self.bot, "Alien vs. Predator", None, None)

        # No existing item shares this normalized title, so no duplicate
        # warning is raised at all -- proceeds immediately.
        self.assertIsNone(crew_interaction.response.sent_view)
        items = self.suggestion_service.get_suggestions_for_database(self.database.database_id)
        self.assertEqual(2, len(items))

    async def test_crew_confirming_an_exact_title_possible_duplicate_still_blocks_on_the_uniqueness_constraint(
        self,
    ) -> None:
        # Known limitation: SuggestionService's storage is keyed by
        # (database_id, normalized title), so two records can never
        # share an exactly-matching title within one database --
        # confirming "add anyway" for a byte-identical title reports
        # that constraint rather than silently creating a second record.
        await handle_add_suggestion(FakeInteraction(), self.bot, "Alien", None, 1979)
        crew_interaction = FakeInteraction(user=self._crew_member())
        await handle_add_suggestion(crew_interaction, self.bot, "Alien", None, None)
        view = crew_interaction.response.sent_view

        confirm_interaction = FakeInteraction(user=self._crew_member())
        await view.children[0].callback(confirm_interaction)

        self.assertIn("already on the list", confirm_interaction.response.sent_message)
        items = self.suggestion_service.get_suggestions_for_database(self.database.database_id)
        self.assertEqual(1, len(items))

    async def test_archived_duplicate_reactivation_reuses_the_same_record(self) -> None:
        await handle_add_suggestion(FakeInteraction(), self.bot, "Alien", None, 1979)
        item = self.suggestion_service.get_suggestions_for_database(self.database.database_id)[0]
        self.suggestion_service.archive_suggestion(item.id)

        crew_interaction = FakeInteraction(user=self._crew_member())
        await handle_add_suggestion(crew_interaction, self.bot, "Alien", None, 1979)
        view = crew_interaction.response.sent_view
        self.assertIsNotNone(view)
        # UX Polish: "Reactivate" isn't destructive (it restores a
        # retired watch item), so it must not use the alarming red/
        # danger style reserved for genuinely destructive confirmations.
        import discord

        self.assertEqual(view.children[0].style, discord.ButtonStyle.primary)

        confirm_interaction = FakeInteraction(user=self._crew_member())
        await view.children[0].callback(confirm_interaction)

        items = self.suggestion_service.get_suggestions_for_database(self.database.database_id, include_archived=True)
        self.assertEqual(1, len(items))
        self.assertEqual(item.id, items[0].id)

    async def test_reactivation_reuses_the_existing_confirmation_post(self) -> None:
        await handle_add_suggestion(FakeInteraction(), self.bot, "Alien", None, 1979)
        item = self.suggestion_service.get_suggestions_for_database(self.database.database_id)[0]
        self.suggestion_service.archive_suggestion(item.id)

        crew_interaction = FakeInteraction(user=self._crew_member())
        await handle_add_suggestion(crew_interaction, self.bot, "Alien", None, 1979)
        confirm_interaction = FakeInteraction(user=self._crew_member())
        await crew_interaction.response.sent_view.children[0].callback(confirm_interaction)

        # Just 1: the reactivation edits the existing post rather than
        # sending a new one (no guild configuration is saved in this
        # test class, so the Rotation Low-Pool notification has no
        # destination to resolve and never fires -- see
        # AdmissionModeAndLowPoolReminderTests for it actually firing).
        self.assertEqual(1, len(self.confirmation_channel.sent))


class AddWithThreadDestinationTests(HandleAddSuggestionTestCase):
    """A configured suggestion destination may be a public thread rather
    than a text channel -- from /add's perspective these are the same
    kind of Discord messageable, so the confirmation post is created the
    same way either way (see post_suggestion_confirmation)."""

    def setUp(self) -> None:
        super().setUp()
        self.configuration_repository.save(
            SuggestionDatabaseConfiguration(
                guild_id=GUILD_ID,
                database_id=self.database.database_id,
                display_name="Movie Night",
                channels=SuggestionDatabaseChannelsConfig(suggestion_channel_id=888),
            )
        )
        self.confirmation_thread = FakeChannel(888)
        self.bot.register_channel(self.confirmation_thread)

    async def test_add_posts_a_public_confirmation_to_the_configured_thread(self) -> None:
        interaction = FakeInteraction()

        await handle_add_suggestion(interaction, self.bot, "Alien", None, 1979)

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertGreaterEqual(len(self.confirmation_thread.sent), 1)


class AddInvokedInsideTheConfiguredThreadTests(HandleAddSuggestionTestCase):
    """Release Polish Batch 2, Priority 1: reproduces the exact reported
    contradiction -- WASH (/config's "Suggestion Post Destination"
    summary) reports a thread as the configured destination for a
    database whose *original* channel is elsewhere, while /add, run
    inside that same thread, previously reported no suggestion database
    configured at all. The database's own creation-time channel and its
    later-configured post destination are two different fields
    (SuggestionDatabase.channel_id vs.
    SuggestionDatabaseConfiguration.channels.suggestion_channel_id);
    resolve_database_for_channel must recognize both.
    """

    def setUp(self) -> None:
        super().setUp()
        # self.database's home channel is CHANNEL_ID (200, from
        # HandleAddSuggestionTestCase.setUp) -- the configured
        # destination below is a different, later-added public thread.
        self.thread_id = 888
        self.configuration_repository.save(
            SuggestionDatabaseConfiguration(
                guild_id=GUILD_ID,
                database_id=self.database.database_id,
                display_name="Movie Night",
                channels=SuggestionDatabaseChannelsConfig(suggestion_channel_id=self.thread_id),
            )
        )
        self.confirmation_thread = FakeChannel(self.thread_id)
        self.bot.register_channel(self.confirmation_thread)

    async def test_add_run_inside_the_configured_thread_resolves_the_database(self) -> None:
        interaction = FakeInteraction(channel_id=self.thread_id)

        await handle_add_suggestion(interaction, self.bot, "Alien", None, 1979)

        self.assertNotIn("No suggestion database is available", interaction.response.sent_message or "")
        self.assertEqual(1, len(self.suggestion_service.get_suggestions_for_database(self.database.database_id)))

    async def test_add_run_inside_the_configured_thread_posts_the_confirmation_there(self) -> None:
        interaction = FakeInteraction(channel_id=self.thread_id)

        await handle_add_suggestion(interaction, self.bot, "Alien", None, 1979)

        self.assertGreaterEqual(len(self.confirmation_thread.sent), 1)

    async def test_add_run_inside_the_configured_thread_retains_the_original_post_link(self) -> None:
        interaction = FakeInteraction(channel_id=self.thread_id)

        await handle_add_suggestion(interaction, self.bot, "Alien", None, 1979)

        item = self.suggestion_service.get_suggestions_for_database(self.database.database_id)[0]
        self.assertEqual(item.channel_id, self.thread_id)
        self.assertIsNotNone(item.message_id)

    async def test_add_still_resolves_when_run_in_the_original_home_channel_too(self) -> None:
        # Database ownership (home channel) and destination lookup
        # (configured thread) must never disagree -- both resolve to the
        # exact same database.
        interaction = FakeInteraction(channel_id=CHANNEL_ID)

        await handle_add_suggestion(interaction, self.bot, "Alien", None, 1979)

        self.assertNotIn("No suggestion database is available", interaction.response.sent_message or "")
        self.assertEqual(1, len(self.suggestion_service.get_suggestions_for_database(self.database.database_id)))


class AddWithInaccessibleDestinationTests(HandleAddSuggestionTestCase):
    """The configured suggestion channel/thread may no longer be reachable
    (deleted, WASH kicked from it, permissions revoked). The suggestion
    must still be saved, and the member must get a clear, non-technical
    explanation -- never a raw exception."""

    def setUp(self) -> None:
        super().setUp()
        self.configuration_repository.save(
            SuggestionDatabaseConfiguration(
                guild_id=GUILD_ID,
                database_id=self.database.database_id,
                display_name="Movie Night",
                channels=SuggestionDatabaseChannelsConfig(suggestion_channel_id=999999),
            )
        )
        # Deliberately not registered with the bot, so get_channel/fetch_channel
        # behave as they would for a deleted or inaccessible channel.

    async def test_suggestion_is_saved_despite_the_inaccessible_destination(self) -> None:
        interaction = FakeInteraction()

        await handle_add_suggestion(interaction, self.bot, "Alien", None, 1979)

        self.assertEqual(1, len(self.suggestion_service.get_suggestions_for_database(self.database.database_id)))

    async def test_member_sees_a_clear_actionable_warning_not_a_raw_exception(self) -> None:
        interaction = FakeInteraction()

        await handle_add_suggestion(interaction, self.bot, "Alien", None, 1979)

        message = interaction.response.sent_message
        self.assertIn("could not post the public confirmation", message)
        self.assertNotIn("Traceback", message)
        self.assertNotIn("RuntimeError", message)


class AdmissionModeAndLowPoolReminderTests(HandleAddSuggestionTestCase):
    """FR-033B Section 5's admission modes, plus the Rotation Low-Pool
    notification (Rotation & Collection Health), exercised through the
    real /add flow. This is the one class in this file with an Admin
    Channel configured, so the notification can actually fire -- every
    other class's FakeGuildConfigurationRepository reports no guild
    configuration at all, so it never does (see AddWithDestinationTests).
    """

    ADMIN_CHANNEL_ID = 888

    def setUp(self) -> None:
        super().setUp()
        self.configuration_repository.save(
            SuggestionDatabaseConfiguration(
                guild_id=GUILD_ID,
                database_id=self.database.database_id,
                display_name="Movie Night",
                channels=SuggestionDatabaseChannelsConfig(suggestion_channel_id=777),
                # This class exercises admission modes and the Rotation
                # Low-Pool Notification, both rotation-specific concepts --
                # Rotation-removal Phase 1 changed the default mode to
                # Favor New Additions, which never creates rotation state,
                # so this must opt into a rotation-based mode explicitly.
                suggestion_rules=SuggestionRulesConfig(candidate_selection=CandidateSelectionMode.ROTATION_POOL),
            )
        )
        guild_configuration = GuildConfiguration(
            guild_id=GUILD_ID,
            guild_name="Test Guild",
            channels=GuildChannelsConfig(admin_channel_id=self.ADMIN_CHANNEL_ID),
        )
        rotation_path = Path(self._temp_dir.name) / "rotations.json"
        self.bot = FakeBot(
            self.suggestion_service,
            self.configuration_repository,
            rotation_repository=JsonRotationRepository(rotation_path),
            guild_configuration_repository=FakeGuildConfigurationRepository(guild_configuration),
        )
        self.confirmation_channel = FakeChannel(777)
        self.admin_channel = FakeChannel(self.ADMIN_CHANNEL_ID)
        self.bot.register_channel(self.confirmation_channel)
        self.bot.register_channel(self.admin_channel)

    def _set_admission_mode(self, mode: SuggestionAdmissionMode) -> None:
        existing = self.configuration_repository.get(GUILD_ID, self.database.database_id)
        updated = replace(existing, suggestion_rules=replace(existing.suggestion_rules, admission_mode=mode))
        self.configuration_repository.save(updated)

    async def test_next_rotation_leaves_a_new_suggestion_out_of_the_open_rotation(self) -> None:
        self._set_admission_mode(SuggestionAdmissionMode.NEXT_ROTATION)
        self.bot.rotation_service.get_or_start_rotation(self.database.database_id)

        await handle_add_suggestion(FakeInteraction(), self.bot, "Alien", None, 1979)

        item = self.suggestion_service.get_suggestions_for_database(self.database.database_id)[0]
        rotation = self.bot.rotation_service.get_open_rotation(self.database.database_id)
        self.assertNotIn(item.id, rotation.assigned_suggestion_ids)

    async def test_join_current_rotation_admits_the_new_suggestion_immediately(self) -> None:
        self._set_admission_mode(SuggestionAdmissionMode.JOIN_CURRENT_ROTATION)
        self.bot.rotation_service.get_or_start_rotation(self.database.database_id)

        await handle_add_suggestion(FakeInteraction(), self.bot, "Alien", None, 1979)

        item = self.suggestion_service.get_suggestions_for_database(self.database.database_id)[0]
        rotation = self.bot.rotation_service.get_open_rotation(self.database.database_id)
        self.assertIn(item.id, rotation.assigned_suggestion_ids)

    async def test_join_current_rotation_is_now_the_default_admission_mode(self) -> None:
        # Rotation-removal Phase 1: SuggestionRulesConfig's admission_mode
        # default moved from Next Rotation to Join Current Rotation, as the
        # "begin transitioning toward immediate eligibility" step for
        # collections still using a legacy rotation-based selection mode.
        self.bot.rotation_service.get_or_start_rotation(self.database.database_id)

        await handle_add_suggestion(FakeInteraction(), self.bot, "Alien", None, 1979)

        item = self.suggestion_service.get_suggestions_for_database(self.database.database_id)[0]
        rotation = self.bot.rotation_service.get_open_rotation(self.database.database_id)
        self.assertIn(item.id, rotation.assigned_suggestion_ids)

    async def test_low_pool_notification_never_bootstraps_a_rotation(self) -> None:
        # Rotation-removal Phase 2: the Eligible Pool Warning never
        # touches RotationService at all -- confirms no rotation gets
        # created as a side effect of evaluating it, regardless of
        # whether the warning itself fires.
        self._set_admission_mode(SuggestionAdmissionMode.NEXT_ROTATION)

        await handle_add_suggestion(FakeInteraction(), self.bot, "Alien", None, 1979)

        self.assertIsNone(self.bot.rotation_service.get_open_rotation(self.database.database_id))

    def _seed_low_pool_condition(self) -> None:
        """9 pre-existing suggestions -- comfortably below the default
        threshold (candidate_count 3 * multiplier 5 = 15) once one more
        is added by the caller.
        """
        for index in range(9):
            self.suggestion_service.suggest(f"Movie {index}", database_id=self.database.database_id)

    async def test_low_pool_notification_fires_once_then_suppresses_duplicates(self) -> None:
        self._seed_low_pool_condition()

        await handle_add_suggestion(FakeInteraction(), self.bot, "Alien", None, 1979)
        self.assertEqual(1, len(self.admin_channel.sent))
        self.assertIn("Eligible Pool Warning", self.admin_channel.sent[0][0])

        await handle_add_suggestion(FakeInteraction(), self.bot, "The Matrix", None, 1999)

        # Still just 1: already armed, and still below threshold.
        self.assertEqual(1, len(self.admin_channel.sent))

    async def test_low_pool_notification_fires_again_after_rising_above_threshold_and_dropping_again(self) -> None:
        self._seed_low_pool_condition()
        await handle_add_suggestion(FakeInteraction(), self.bot, "Alien", None, 1979)
        self.assertEqual(1, len(self.admin_channel.sent))

        # Add enough suggestions to rise back above the threshold (15),
        # and let a real add re-evaluate (and so re-arm/disarm) the
        # warning -- evaluation only happens as a side effect of /add,
        # never merely by suggestions existing.
        for index in range(19):
            self.suggestion_service.suggest(f"Extra {index}", database_id=self.database.database_id)
        await handle_add_suggestion(FakeInteraction(), self.bot, "The Matrix", None, 1999)
        self.assertEqual(1, len(self.admin_channel.sent))  # still 1: disarmed, not re-fired

        # Now drop back below threshold again.
        items = self.suggestion_service.get_suggestions_for_database(self.database.database_id)
        for item in items[:20]:
            self.suggestion_service.archive_suggestion(item.id)

        await handle_add_suggestion(FakeInteraction(), self.bot, "Inception", None, 2010)

        self.assertEqual(2, len(self.admin_channel.sent))


if __name__ == "__main__":
    unittest.main()
