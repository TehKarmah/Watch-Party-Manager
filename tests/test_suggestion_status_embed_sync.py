"""Tests for Requirement 7: every suggestion embed shows its current
status, and the original post is edited in place (never recreated)
whenever that status changes.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import discord

from watch_party_manager.bot import (
    build_suggestion_confirmation_embed,
    handle_reject_suggestion,
    handle_suggestion_rejection_toggle,
    sync_suggestion_status_embed,
    sync_vote_completion_status_embeds,
    sync_vote_start_status_embeds,
)
from watch_party_manager.domain.watch_item import MediaType, WatchItem, WatchItemStatus
from watch_party_manager.domain.watch_item_journey import WatchItemJourney
from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.services.permission_service import PermissionService
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.vote_completion_service import VoteCompletionResult
from watch_party_manager.services.vote_service import VoteService

GUILD_ID = 100
CHANNEL_ID = 200
WATCH_PARTY_MEMBER_ROLE_ID = 555
WASH_CREW_ROLE_ID = 999


def make_item(*, status=WatchItemStatus.SUGGESTED, database_id=None, channel_id=None, message_id=None, journey=None):
    return WatchItem(
        title="Alien",
        media_type=MediaType.MOVIE,
        status=status,
        database_id=database_id,
        guild_id=GUILD_ID if database_id is not None else None,
        channel_id=channel_id,
        message_id=message_id,
        journey=journey or WatchItemJourney(),
    )


class BuildSuggestionConfirmationEmbedStatusFieldTests(unittest.TestCase):
    def _status_value(self, embed):
        return next(field.value for field in embed.fields if field.name == "Status")

    def test_suggested_shows_available(self) -> None:
        embed = build_suggestion_confirmation_embed(
            make_item(status=WatchItemStatus.SUGGESTED), database_name="Movies", suggested_by="<@1>"
        )
        self.assertEqual("🟢 Available", self._status_value(embed))

    def test_archived_shows_retired(self) -> None:
        embed = build_suggestion_confirmation_embed(
            make_item(status=WatchItemStatus.ARCHIVED), database_name="Movies", suggested_by="<@1>"
        )
        self.assertEqual("🗄️ Retired", self._status_value(embed))

    def test_vote_winner_shows_vote_winner(self) -> None:
        embed = build_suggestion_confirmation_embed(
            make_item(status=WatchItemStatus.VOTE_WINNER), database_name="Movies", suggested_by="<@1>"
        )
        self.assertEqual("🏆 Vote Winner", self._status_value(embed))

    def test_vote_winner_with_a_recorded_date_shows_the_won_line(self) -> None:
        from datetime import date

        embed = build_suggestion_confirmation_embed(
            make_item(
                status=WatchItemStatus.VOTE_WINNER, journey=WatchItemJourney(last_won_date=date(2026, 7, 28))
            ),
            database_name="Movies",
            suggested_by="<@1>",
        )
        self.assertEqual("🏆 Vote Winner\nWon: July 28, 2026", self._status_value(embed))

    def test_legacy_vote_winner_without_a_date_omits_the_won_line(self) -> None:
        embed = build_suggestion_confirmation_embed(
            make_item(status=WatchItemStatus.VOTE_WINNER), database_name="Movies", suggested_by="<@1>"
        )
        self.assertEqual("🏆 Vote Winner", self._status_value(embed))

    def test_in_an_active_vote_shown_when_vote_service_reports_an_open_round(self) -> None:
        class FakeVoteService:
            def get_open_round_for_suggestion(self, suggestion_id) -> object:
                return object()

        item = make_item(status=WatchItemStatus.SUGGESTED)
        item.id = 1
        embed = build_suggestion_confirmation_embed(
            item,
            database_name="Movies",
            suggested_by="<@1>",
            vote_service=FakeVoteService(),
        )
        self.assertEqual("🗳️ In an Active Vote", self._status_value(embed))


class BuildSuggestionConfirmationEmbedCollectionFieldTests(unittest.TestCase):
    """Release Candidate Polish, Requirement 3: every user-facing display
    of a collection (including this embed's Collection field) goes
    through the one shared format_collection_display() helper.
    """

    def _collection_value(self, embed):
        return next(field.value for field in embed.fields if field.name == "Collection")

    def test_standard_collection_shows_its_emoji(self) -> None:
        embed = build_suggestion_confirmation_embed(
            make_item(status=WatchItemStatus.SUGGESTED), database_name="Movies", suggested_by="<@1>"
        )
        self.assertEqual("🎬 Movies", self._collection_value(embed))

    def test_custom_collection_has_no_emoji(self) -> None:
        embed = build_suggestion_confirmation_embed(
            make_item(status=WatchItemStatus.SUGGESTED), database_name="Book Club Adaptations", suggested_by="<@1>"
        )
        self.assertEqual("Book Club Adaptations", self._collection_value(embed))


class FakeMessage:
    def __init__(self, message_id: int) -> None:
        self.id = message_id
        self.edited_embed = None
        self.edited_view = None
        self.edit_calls = 0
        self.fail_edit = False

    async def edit(self, *, embed=None, view=None) -> None:
        if self.fail_edit:
            raise RuntimeError("simulated edit failure")
        self.edited_embed = embed
        self.edited_view = view
        self.edit_calls += 1


class FakeChannel:
    def __init__(
        self,
        *messages: FakeMessage,
        fail_fetch: bool = False,
        fail_fetch_times: int = 0,
        fetch_exception_factory=None,
    ) -> None:
        """fail_fetch=True fails every call (a permanent condition in
        tests that don't care about retry behavior). fail_fetch_times
        fails only the first N calls, then succeeds -- used to simulate a
        transient failure that a retry recovers from.
        fetch_exception_factory controls which exception is raised
        (defaults to a generic RuntimeError, i.e. "some transient
        failure"); pass one that builds discord.NotFound/discord.Forbidden
        to simulate a permanent failure instead.
        """
        self._messages = {message.id: message for message in messages}
        self._fail_fetch = fail_fetch
        self._fail_fetch_times = fail_fetch_times
        self._fetch_exception_factory = fetch_exception_factory or (
            lambda: RuntimeError("simulated fetch failure")
        )
        self.fetch_attempts = 0
        self.sent_messages = []

    async def fetch_message(self, message_id):
        self.fetch_attempts += 1
        if self._fail_fetch or self.fetch_attempts <= self._fail_fetch_times:
            raise self._fetch_exception_factory()
        return self._messages[message_id]

    async def send(self, *args, **kwargs):
        self.sent_messages.append((args, kwargs))
        return FakeMessage(message_id=999)


class FakeHTTPResponse:
    status = 404
    reason = "Not Found"


class FakeForbiddenHTTPResponse:
    status = 403
    reason = "Forbidden"


def make_discord_not_found() -> discord.NotFound:
    return discord.NotFound(response=FakeHTTPResponse(), message="Unknown Message")


def make_discord_forbidden() -> discord.Forbidden:
    return discord.Forbidden(response=FakeForbiddenHTTPResponse(), message="Missing Access")


class FakeGuildConfigurationRepository:
    """No Admin Channel configured -- notify_crew_review's "gracefully no
    Admin Channel configured" path, matching every FakeBot user in this
    file that doesn't need to test the Crew Review notification itself.
    """

    def get(self, guild_id):
        return None


class FakeBot:
    def __init__(
        self, suggestion_service: SuggestionService, channel: FakeChannel, *, vote_service=None
    ) -> None:
        self.suggestion_service = suggestion_service
        self.suggestion_database_configuration_repository = None
        self.guild_configuration_repository = FakeGuildConfigurationRepository()
        self.wash_crew_role_id = None
        self.vote_service = vote_service
        self.permission_service = None
        self._channel = channel

    def get_channel(self, channel_id):
        return self._channel

    async def fetch_channel(self, channel_id):
        return self._channel


class SyncSuggestionStatusEmbedTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.database = self.suggestion_service.create_database("Movies", GUILD_ID, CHANNEL_ID).database

    async def test_edits_the_existing_message_in_place(self) -> None:
        watch_item = self.suggestion_service.suggest(
            "Alien", database_id=self.database.database_id, guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).watch_item
        self.suggestion_service.set_confirmation_post_reference(watch_item.id, GUILD_ID, CHANNEL_ID, 555)
        watch_item = self.suggestion_service.get_suggestion(watch_item.id)

        message = FakeMessage(message_id=555)
        channel = FakeChannel(message)
        bot = FakeBot(self.suggestion_service, channel)

        self.suggestion_service.archive_suggestion(watch_item.id)
        archived_item = self.suggestion_service.get_suggestion(watch_item.id)

        await sync_suggestion_status_embed(bot, archived_item)

        self.assertEqual(1, message.edit_calls)
        status_field = next(field for field in message.edited_embed.fields if field.name == "Status")
        self.assertEqual("🗄️ Retired", status_field.value)
        # Never recreates the message.
        self.assertEqual([], channel.sent_messages)

    async def test_reactivating_a_vote_winner_syncs_the_post_back_to_available(self) -> None:
        # Suggestion Status Synchronization, transition 5: Vote Winner ->
        # Eligible for Voting when a Vote Winner is re-added/restored
        # (e.g. via /edit_suggestion's Change Status -> Available).
        watch_item = self.suggestion_service.suggest(
            "Alien", database_id=self.database.database_id, guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).watch_item
        self.suggestion_service.set_confirmation_post_reference(watch_item.id, GUILD_ID, CHANNEL_ID, 555)
        self.suggestion_service.set_suggestion_status(watch_item.id, WatchItemStatus.VOTE_WINNER)

        message = FakeMessage(message_id=555)
        channel = FakeChannel(message)
        bot = FakeBot(self.suggestion_service, channel)

        self.suggestion_service.set_suggestion_status(watch_item.id, WatchItemStatus.SUGGESTED)
        restored_item = self.suggestion_service.get_suggestion(watch_item.id)

        await sync_suggestion_status_embed(bot, restored_item)

        self.assertEqual(1, message.edit_calls)
        status_field = next(field for field in message.edited_embed.fields if field.name == "Status")
        self.assertEqual("🟢 Available", status_field.value)

    async def test_restoring_a_retired_item_syncs_the_post_back_to_available(self) -> None:
        # Suggestion Status Synchronization, transition 6: Retired ->
        # Eligible for Voting when a Retired item is restored (e.g. via
        # /edit_suggestion's Change Status -> Available, or re-adding it
        # through /add's reactivation flow).
        watch_item = self.suggestion_service.suggest(
            "Alien", database_id=self.database.database_id, guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).watch_item
        self.suggestion_service.set_confirmation_post_reference(watch_item.id, GUILD_ID, CHANNEL_ID, 555)
        self.suggestion_service.archive_suggestion(watch_item.id)

        message = FakeMessage(message_id=555)
        channel = FakeChannel(message)
        bot = FakeBot(self.suggestion_service, channel)

        self.suggestion_service.reactivate_suggestion(watch_item.id)
        restored_item = self.suggestion_service.get_suggestion(watch_item.id)

        await sync_suggestion_status_embed(bot, restored_item)

        self.assertEqual(1, message.edit_calls)
        status_field = next(field for field in message.edited_embed.fields if field.name == "Status")
        self.assertEqual("🟢 Available", status_field.value)

    async def test_no_op_when_no_message_reference_exists(self) -> None:
        watch_item = make_item(database_id=self.database.database_id)
        message = FakeMessage(message_id=1)
        channel = FakeChannel(message)
        bot = FakeBot(self.suggestion_service, channel)

        await sync_suggestion_status_embed(bot, watch_item)

        self.assertEqual(0, message.edit_calls)

    async def test_gracefully_skips_an_unreachable_message_after_exhausting_retries(self) -> None:
        # A transient failure that never recovers (both attempts fail)
        # must not raise, and must be logged rather than silently
        # swallowed -- see test_recovers_after_one_transient_failure for
        # the "one failure, then success" case this is paired with.
        watch_item = self.suggestion_service.suggest(
            "Alien", database_id=self.database.database_id, guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).watch_item
        self.suggestion_service.set_confirmation_post_reference(watch_item.id, GUILD_ID, CHANNEL_ID, 555)
        watch_item = self.suggestion_service.get_suggestion(watch_item.id)

        channel = FakeChannel(FakeMessage(message_id=555), fail_fetch=True)
        bot = FakeBot(self.suggestion_service, channel)

        with self.assertLogs("watch_party_manager.bot", level="WARNING") as logs:
            await sync_suggestion_status_embed(bot, watch_item)

        self.assertEqual(2, channel.fetch_attempts)
        self.assertTrue(any(str(watch_item.id) in message for message in logs.output))
        self.assertTrue(any("555" in message for message in logs.output))

    async def test_recovers_after_one_transient_failure(self) -> None:
        # Reliability fix (rotation-state consistency audit): a transient
        # failure (network blip, Discord-side 5xx) on the first attempt
        # must not leave the post stale -- the bounded retry recovers it.
        watch_item = self.suggestion_service.suggest(
            "Alien", database_id=self.database.database_id, guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).watch_item
        self.suggestion_service.set_confirmation_post_reference(watch_item.id, GUILD_ID, CHANNEL_ID, 555)
        self.suggestion_service.archive_suggestion(watch_item.id)
        archived_item = self.suggestion_service.get_suggestion(watch_item.id)

        message = FakeMessage(message_id=555)
        channel = FakeChannel(message, fail_fetch_times=1)
        bot = FakeBot(self.suggestion_service, channel)

        await sync_suggestion_status_embed(bot, archived_item)

        self.assertEqual(2, channel.fetch_attempts)
        self.assertEqual(1, message.edit_calls)
        status_field = next(field for field in message.edited_embed.fields if field.name == "Status")
        self.assertEqual("🗄️ Retired", status_field.value)

    async def test_a_permanent_not_found_failure_is_not_retried(self) -> None:
        # A deleted message (discord.NotFound) is a permanent condition:
        # retrying can never succeed, so it must fail fast (one attempt)
        # rather than spend the retry budget, and must still be logged.
        watch_item = self.suggestion_service.suggest(
            "Alien", database_id=self.database.database_id, guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).watch_item
        self.suggestion_service.set_confirmation_post_reference(watch_item.id, GUILD_ID, CHANNEL_ID, 555)
        watch_item = self.suggestion_service.get_suggestion(watch_item.id)

        channel = FakeChannel(
            FakeMessage(message_id=555), fail_fetch=True, fetch_exception_factory=make_discord_not_found
        )
        bot = FakeBot(self.suggestion_service, channel)

        with self.assertLogs("watch_party_manager.bot", level="WARNING") as logs:
            await sync_suggestion_status_embed(bot, watch_item)

        self.assertEqual(1, channel.fetch_attempts)
        self.assertTrue(any(str(watch_item.id) in message for message in logs.output))

    async def test_a_permanent_forbidden_failure_is_not_retried(self) -> None:
        # Revoked channel permissions (discord.Forbidden) is likewise
        # permanent -- one attempt, not the full retry budget.
        watch_item = self.suggestion_service.suggest(
            "Alien", database_id=self.database.database_id, guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).watch_item
        self.suggestion_service.set_confirmation_post_reference(watch_item.id, GUILD_ID, CHANNEL_ID, 555)
        watch_item = self.suggestion_service.get_suggestion(watch_item.id)

        channel = FakeChannel(
            FakeMessage(message_id=555), fail_fetch=True, fetch_exception_factory=make_discord_forbidden
        )
        bot = FakeBot(self.suggestion_service, channel)

        with self.assertLogs("watch_party_manager.bot", level="WARNING") as logs:
            await sync_suggestion_status_embed(bot, watch_item)

        self.assertEqual(1, channel.fetch_attempts)
        self.assertTrue(any(str(watch_item.id) in message for message in logs.output))


class SyncVoteStartStatusEmbedsTests(unittest.IsolatedAsyncioTestCase):
    """Suggestion Status Synchronization, transition 1: a candidate
    presented into a new voting round is In an Active Vote (Rotation-
    removal Phase 2) by the time perform_start_vote returns -- its own
    public post must reflect that the moment the round opens, not only
    once the round later closes.
    """

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.database = self.suggestion_service.create_database("Movies", GUILD_ID, CHANNEL_ID).database
        self.vote_service = VoteService(
            self.suggestion_service, repository=JsonVoteRepository(root / "voting.json")
        )

    def _status_of(self, embed) -> str:
        return next(field.value for field in embed.fields if field.name == "Status")

    async def test_every_presented_candidate_is_synced_to_in_an_active_vote(self) -> None:
        first = self.suggestion_service.suggest(
            "Alien", database_id=self.database.database_id, guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).watch_item
        second = self.suggestion_service.suggest(
            "Predator", database_id=self.database.database_id, guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).watch_item
        self.suggestion_service.set_confirmation_post_reference(first.id, GUILD_ID, CHANNEL_ID, 777)
        self.suggestion_service.set_confirmation_post_reference(second.id, GUILD_ID, CHANNEL_ID, 778)

        # Mirrors what perform_start_vote's nominee selection already did
        # (opening a round with these candidates) before this function is
        # called.
        self.vote_service.create_round(
            candidate_suggestion_ids=[first.id, second.id], database_id=self.database.database_id
        )
        presented_first = self.suggestion_service.get_suggestion(first.id)
        presented_second = self.suggestion_service.get_suggestion(second.id)

        message_first = FakeMessage(message_id=777)
        message_second = FakeMessage(message_id=778)
        channel = FakeChannel(message_first, message_second)
        bot = FakeBot(self.suggestion_service, channel, vote_service=self.vote_service)

        await sync_vote_start_status_embeds(bot, [presented_first, presented_second])

        self.assertEqual(1, message_first.edit_calls)
        self.assertEqual("🗳️ In an Active Vote", self._status_of(message_first.edited_embed))
        self.assertEqual(1, message_second.edit_calls)
        self.assertEqual("🗳️ In an Active Vote", self._status_of(message_second.edited_embed))

    async def test_a_candidate_with_no_public_post_is_skipped_without_raising(self) -> None:
        candidate = self.suggestion_service.suggest(
            "Alien", database_id=self.database.database_id
        ).watch_item
        channel = FakeChannel()
        bot = FakeBot(self.suggestion_service, channel, vote_service=self.vote_service)

        # Must not raise even though `candidate` has no channel_id/message_id.
        await sync_vote_start_status_embeds(bot, [candidate])


class SyncVoteCompletionStatusEmbedsTests(unittest.IsolatedAsyncioTestCase):
    """Requirement 1: a losing nominee's own confirmation post must be
    refreshed right along with the winner's refresh to Vote Winner -- the
    original bug synced only the winner, leaving every losing nominee's
    post stuck showing whatever status it had before the round completed.
    Rotation-removal Phase 2: a losing nominee reverts to Available once
    the round closes (its own VoteService round is no longer open), not
    to Rotation Cooldown.
    """

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.database = self.suggestion_service.create_database("Movies", GUILD_ID, CHANNEL_ID).database
        self.vote_service = VoteService(
            self.suggestion_service, repository=JsonVoteRepository(root / "voting.json")
        )

    def _status_of(self, embed) -> str:
        return next(field.value for field in embed.fields if field.name == "Status")

    async def test_syncs_the_winner_to_vote_winner_and_the_loser_to_available(self) -> None:
        winner = self.suggestion_service.suggest(
            "Alien", database_id=self.database.database_id, guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).watch_item
        loser = self.suggestion_service.suggest(
            "Predator", database_id=self.database.database_id, guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).watch_item
        self.suggestion_service.set_confirmation_post_reference(winner.id, GUILD_ID, CHANNEL_ID, 777)
        self.suggestion_service.set_confirmation_post_reference(loser.id, GUILD_ID, CHANNEL_ID, 778)

        created = self.vote_service.create_round(
            candidate_suggestion_ids=[winner.id, loser.id], database_id=self.database.database_id
        )
        self.vote_service.close_round(created.vote_round.id)
        self.suggestion_service.record_vote_win(winner.id, None)

        winner_message = FakeMessage(message_id=777)
        loser_message = FakeMessage(message_id=778)
        channel = FakeChannel(winner_message, loser_message)
        bot = FakeBot(self.suggestion_service, channel, vote_service=self.vote_service)

        result = VoteCompletionResult(
            vote_round=created.vote_round, winning_suggestion_ids=[winner.id], standings=(), total_votes_cast=2
        )
        await sync_vote_completion_status_embeds(bot, result)

        self.assertEqual(1, winner_message.edit_calls)
        self.assertEqual("🏆 Vote Winner", self._status_of(winner_message.edited_embed))
        self.assertEqual(1, loser_message.edit_calls)
        self.assertEqual("🟢 Available", self._status_of(loser_message.edited_embed))

    async def test_a_recorded_won_date_is_reflected_in_the_refreshed_embed(self) -> None:
        from datetime import date

        winner = self.suggestion_service.suggest(
            "Alien", database_id=self.database.database_id, guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).watch_item
        other = self.suggestion_service.suggest(
            "Predator", database_id=self.database.database_id, guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).watch_item
        self.suggestion_service.set_confirmation_post_reference(winner.id, GUILD_ID, CHANNEL_ID, 777)
        created = self.vote_service.create_round(
            candidate_suggestion_ids=[winner.id, other.id], database_id=self.database.database_id
        )
        self.vote_service.close_round(created.vote_round.id)
        self.suggestion_service.record_vote_win(winner.id, date(2026, 7, 28))

        winner_message = FakeMessage(message_id=777)
        channel = FakeChannel(winner_message)
        bot = FakeBot(self.suggestion_service, channel, vote_service=self.vote_service)

        result = VoteCompletionResult(
            vote_round=created.vote_round, winning_suggestion_ids=[winner.id], standings=(), total_votes_cast=1
        )
        await sync_vote_completion_status_embeds(bot, result)

        self.assertEqual("🏆 Vote Winner\nWon: July 28, 2026", self._status_of(winner_message.edited_embed))


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, user_id: int, role_ids=()) -> None:
        self.id = user_id
        self.roles = [FakeRole(role_id) for role_id in role_ids]


class FakeResponse:
    def __init__(self) -> None:
        self.sent_message = None
        self.sent_ephemeral = None
        self.sent_view = None

    async def send_message(self, content=None, *, ephemeral=False, view=None) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral
        self.sent_view = view


class FakeRejectInteraction:
    def __init__(self, user: FakeMember, guild_id: int = GUILD_ID) -> None:
        self.user = user
        self.guild_id = guild_id
        self.response = FakeResponse()


class SuggestionRejectionThresholdEmbedSyncTests(unittest.IsolatedAsyncioTestCase):
    """Suggestion Status Synchronization, transition 4's two rejection
    paths: both /reject (handle_reject_suggestion) and the "I Won't
    Watch" button (handle_suggestion_rejection_toggle) must resync the
    suggestion's own public post -- not just its view/button -- when a
    rejection crosses the configured threshold and it moves to Pending
    Crew Review (New Review Workflow).
    """

    class FakeConfigRepository:
        class _Config:
            class suggestion_rules:
                rejection_threshold = 1

        def get(self, guild_id, database_id):
            return self._Config()

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.database = self.suggestion_service.create_database("Movies", GUILD_ID, CHANNEL_ID).database
        self.permission_service = PermissionService(
            watch_party_member_role_id=WATCH_PARTY_MEMBER_ROLE_ID, wash_crew_role_id=WASH_CREW_ROLE_ID
        )
        self.item = self.suggestion_service.suggest(
            "Alien", database_id=self.database.database_id, guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).watch_item
        self.suggestion_service.set_confirmation_post_reference(self.item.id, GUILD_ID, CHANNEL_ID, 555)

    def _status_of(self, embed) -> str:
        return next(field.value for field in embed.fields if field.name == "Status")

    async def test_reject_command_syncs_the_post_when_the_threshold_reaches_crew_review(self) -> None:
        message = FakeMessage(message_id=555)
        channel = FakeChannel(message)
        bot = FakeBot(self.suggestion_service, channel)
        bot.permission_service = self.permission_service
        bot.suggestion_database_configuration_repository = self.FakeConfigRepository()

        interaction = FakeRejectInteraction(FakeMember(1, [WATCH_PARTY_MEMBER_ROLE_ID]))
        await handle_reject_suggestion(interaction, bot, self.item.id)

        self.assertEqual(
            WatchItemStatus.PENDING_CREW_REVIEW, self.suggestion_service.get_suggestion(self.item.id).status
        )
        self.assertEqual(1, message.edit_calls)
        self.assertEqual("⚠️ Pending Crew Review", self._status_of(message.edited_embed))

    async def test_toggle_button_syncs_the_post_when_the_threshold_reaches_crew_review(self) -> None:
        message = FakeMessage(message_id=555)
        channel = FakeChannel(message)
        bot = FakeBot(self.suggestion_service, channel)
        bot.permission_service = self.permission_service

        interaction = FakeRejectInteraction(FakeMember(1, [WATCH_PARTY_MEMBER_ROLE_ID]))
        await handle_suggestion_rejection_toggle(
            interaction,
            self.suggestion_service,
            self.FakeConfigRepository(),
            self.item.id,
            permission_service=self.permission_service,
            bot=bot,
        )

        self.assertEqual(
            WatchItemStatus.PENDING_CREW_REVIEW, self.suggestion_service.get_suggestion(self.item.id).status
        )
        self.assertEqual(1, message.edit_calls)
        self.assertEqual("⚠️ Pending Crew Review", self._status_of(message.edited_embed))

    async def test_toggle_button_without_a_bot_falls_back_to_view_only_refresh(self) -> None:
        # Backward-compatible fallback: a caller with no bot instance
        # (e.g. a direct unit test) still gets the previous view-only
        # refresh rather than an error.
        class FakeViewOnlyMessage:
            def __init__(self) -> None:
                self.edited_view = "not-edited"

            async def edit(self, view="not-edited") -> None:
                self.edited_view = view

        interaction = FakeRejectInteraction(FakeMember(1, [WATCH_PARTY_MEMBER_ROLE_ID]))
        interaction.message = FakeViewOnlyMessage()

        await handle_suggestion_rejection_toggle(
            interaction,
            self.suggestion_service,
            self.FakeConfigRepository(),
            self.item.id,
            permission_service=self.permission_service,
        )

        self.assertNotEqual("not-edited", interaction.message.edited_view)


if __name__ == "__main__":
    unittest.main()
