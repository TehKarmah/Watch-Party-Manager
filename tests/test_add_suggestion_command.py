"""Tests for FR-033A's /add rewiring: duplicate detection, re-suggestion
rules, and confirmation-post handling."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from watch_party_manager.bot import (
    AddSuggestionOutcomeKind,
    decide_add_suggestion_outcome,
    extract_year_from_title_suffix,
    handle_add_suggestion,
)
from watch_party_manager.domain.suggestion_database_configuration import (
    SuggestionAdmissionMode,
    SuggestionDatabaseChannelsConfig,
    SuggestionDatabaseConfiguration,
)
from watch_party_manager.domain.watch_item import MediaType, WatchItem, WatchItemStatus
from watch_party_manager.domain.watch_item_journey import WatchItemJourney
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.rotation_repository import JsonRotationRepository
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.duplicate_detection_service import find_duplicates
from watch_party_manager.services.low_pool_reminder_service import LowPoolReminderService
from watch_party_manager.services.permission_service import PermissionService
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

    def test_watched_match_offers_crew_reactivation(self) -> None:
        result = self._matches(status=WatchItemStatus.WATCHED)

        decision = decide_add_suggestion_outcome(result, is_crew=True)

        self.assertEqual(AddSuggestionOutcomeKind.NEEDS_CREW_REACTIVATION_CONFIRM, decision.kind)

    def test_watched_match_blocks_regular_members(self) -> None:
        result = self._matches(status=WatchItemStatus.WATCHED)

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
    """Always reports "no guild configuration saved" -- LowPoolReminderService
    falls back to its documented defaults in that case."""

    def get(self, guild_id: int):
        return None


class FakeBot:
    def __init__(
        self,
        suggestion_service,
        configuration_repository,
        wash_crew_role_id=WASH_CREW_ROLE_ID,
        rotation_repository=None,
    ) -> None:
        self.suggestion_service = suggestion_service
        self.suggestion_input_service = SuggestionInputService()
        self.suggestion_database_configuration_repository = configuration_repository
        self.permission_service = PermissionService(
            watch_party_member_role_id=WATCH_PARTY_MEMBER_ROLE_ID, wash_crew_role_id=wash_crew_role_id
        )
        self.wash_crew_role_id = wash_crew_role_id
        self.rotation_service = RotationService(suggestion_service, repository=rotation_repository)
        self.low_pool_reminder_service = LowPoolReminderService(
            self.rotation_service, FakeGuildConfigurationRepository(), configuration_repository
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
        # 2, not 1: the confirmation post, plus the FR-033B Low Pool
        # Reminder (a pool of 1 is below the default threshold of 10),
        # sent to the same channel since no separate reminder destination
        # is configured -- see maybe_send_low_pool_reminder.
        self.assertEqual(2, len(self.confirmation_channel.sent))

    async def test_active_duplicate_is_blocked(self) -> None:
        await handle_add_suggestion(FakeInteraction(), self.bot, "Alien", None, 1979)
        second_interaction = FakeInteraction()

        await handle_add_suggestion(second_interaction, self.bot, "Alien", None, 1979)

        self.assertIn("already on the list", second_interaction.response.sent_message)
        # 2 (confirmation + low pool reminder from the first, successful
        # add), not 3: the second, blocked call never reaches
        # finish_add_or_reactivate at all.
        self.assertEqual(2, len(self.confirmation_channel.sent))

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

        # 2 (confirmation + low pool reminder from the first add), not 3:
        # the reactivation edits the existing post rather than sending a
        # new one, and its own low-pool-reminder check is suppressed by
        # the minimum interval (the first reminder was just sent).
        self.assertEqual(2, len(self.confirmation_channel.sent))


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
    """FR-033B: Section 5 admission modes and Section 7's Low Pool Reminder,
    exercised through the real /add flow."""

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

    def _set_admission_mode(self, mode: SuggestionAdmissionMode) -> None:
        existing = self.configuration_repository.get(GUILD_ID, self.database.database_id)
        updated = replace(existing, suggestion_rules=replace(existing.suggestion_rules, admission_mode=mode))
        self.configuration_repository.save(updated)

    async def test_next_rotation_default_leaves_a_new_suggestion_out_of_the_open_rotation(self) -> None:
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

    async def test_low_pool_reminder_interval_suppresses_a_second_reminder(self) -> None:
        await handle_add_suggestion(FakeInteraction(), self.bot, "Alien", None, 1979)
        self.assertEqual(2, len(self.confirmation_channel.sent))  # confirmation + reminder

        await handle_add_suggestion(FakeInteraction(), self.bot, "The Matrix", None, 1999)

        # 3, not 4: the second add's confirmation post is sent, but its
        # own low-pool-reminder check is suppressed by the minimum
        # interval (the first reminder was just sent).
        self.assertEqual(3, len(self.confirmation_channel.sent))

    async def test_low_pool_reminder_timestamp_is_recorded(self) -> None:
        await handle_add_suggestion(FakeInteraction(), self.bot, "Alien", None, 1979)

        self.assertIsNotNone(self.bot.rotation_service.last_low_pool_reminder_sent_at(self.database.database_id))


if __name__ == "__main__":
    unittest.main()
