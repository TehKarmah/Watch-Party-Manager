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

from watch_party_manager.bot import (
    build_suggestion_confirmation_embed,
    sync_suggestion_status_embed,
    sync_vote_winner_status_embeds,
)
from watch_party_manager.domain.watch_item import MediaType, WatchItem, WatchItemStatus
from watch_party_manager.domain.watch_item_journey import WatchItemJourney
from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.rotation_service import RotationService
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.vote_completion_service import VoteCompletionResult

GUILD_ID = 100
CHANNEL_ID = 200


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
        self.assertEqual("🔴 Retired", self._status_value(embed))

    def test_vote_winner_shows_vote_winner(self) -> None:
        embed = build_suggestion_confirmation_embed(
            make_item(status=WatchItemStatus.VOTE_WINNER), database_name="Movies", suggested_by="<@1>"
        )
        self.assertEqual("🟣 Vote Winner", self._status_value(embed))

    def test_rotation_cooldown_shown_when_rotation_service_reports_it(self) -> None:
        class FakeRotationService:
            def is_in_rotation_cooldown(self, watch_item) -> bool:
                return True

        embed = build_suggestion_confirmation_embed(
            make_item(status=WatchItemStatus.SUGGESTED),
            database_name="Movies",
            suggested_by="<@1>",
            rotation_service=FakeRotationService(),
        )
        self.assertEqual("🟡 Rotation Cooldown", self._status_value(embed))


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
    def __init__(self, message: FakeMessage, *, fail_fetch: bool = False) -> None:
        self._message = message
        self._fail_fetch = fail_fetch
        self.sent_messages = []

    async def fetch_message(self, message_id):
        if self._fail_fetch:
            raise RuntimeError("simulated fetch failure")
        return self._message

    async def send(self, *args, **kwargs):
        self.sent_messages.append((args, kwargs))
        return FakeMessage(message_id=999)


class FakeBot:
    def __init__(self, suggestion_service: SuggestionService, channel: FakeChannel) -> None:
        self.suggestion_service = suggestion_service
        self.suggestion_database_configuration_repository = None
        self.rotation_service = None
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
        self.assertEqual("🔴 Retired", status_field.value)
        # Never recreates the message.
        self.assertEqual([], channel.sent_messages)

    async def test_no_op_when_no_message_reference_exists(self) -> None:
        watch_item = make_item(database_id=self.database.database_id)
        channel = FakeChannel(FakeMessage(message_id=1))
        bot = FakeBot(self.suggestion_service, channel)

        await sync_suggestion_status_embed(bot, watch_item)

        self.assertEqual(0, channel._message.edit_calls)

    async def test_silently_skips_an_unreachable_message(self) -> None:
        watch_item = self.suggestion_service.suggest(
            "Alien", database_id=self.database.database_id, guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).watch_item
        self.suggestion_service.set_confirmation_post_reference(watch_item.id, GUILD_ID, CHANNEL_ID, 555)
        watch_item = self.suggestion_service.get_suggestion(watch_item.id)

        channel = FakeChannel(FakeMessage(message_id=555), fail_fetch=True)
        bot = FakeBot(self.suggestion_service, channel)

        # Must not raise.
        await sync_suggestion_status_embed(bot, watch_item)


class SyncVoteWinnerStatusEmbedsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.database = self.suggestion_service.create_database("Movies", GUILD_ID, CHANNEL_ID).database

    async def test_syncs_every_winning_suggestion(self) -> None:
        winner = self.suggestion_service.suggest(
            "Alien", database_id=self.database.database_id, guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).watch_item
        self.suggestion_service.set_confirmation_post_reference(winner.id, GUILD_ID, CHANNEL_ID, 777)
        self.suggestion_service.record_vote_win(winner.id, None)
        winner = self.suggestion_service.get_suggestion(winner.id)

        message = FakeMessage(message_id=777)
        channel = FakeChannel(message)
        bot = FakeBot(self.suggestion_service, channel)

        result = VoteCompletionResult(
            vote_round=None, winning_suggestion_ids=[winner.id], standings=(), total_votes_cast=0
        )
        await sync_vote_winner_status_embeds(bot, result)

        self.assertEqual(1, message.edit_calls)
        status_field = next(field for field in message.edited_embed.fields if field.name == "Status")
        self.assertEqual("🟣 Vote Winner", status_field.value)


class RotationCooldownAutomaticallyRevertsTests(unittest.TestCase):
    """End-to-end coverage for Requirement 6: Rotation Cooldown is
    computed, not persisted, so it automatically reverts to Available
    once a fresh rotation begins -- no explicit "clear cooldown" step.
    """

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        from watch_party_manager.persistence.rotation_repository import JsonRotationRepository

        self.rotation_service = RotationService(
            self.suggestion_service, repository=JsonRotationRepository(root / "rotations.json")
        )
        self.database = self.suggestion_service.create_database("Movies", GUILD_ID, CHANNEL_ID).database

    def test_presented_item_enters_cooldown_then_reverts_on_a_fresh_rotation(self) -> None:
        item = self.suggestion_service.suggest("Alien", database_id=self.database.database_id).watch_item

        self.rotation_service.get_or_start_rotation(self.database.database_id)
        self.rotation_service.record_presentation(self.database.database_id, [item.id])
        presented_item = self.suggestion_service.get_suggestion(item.id)
        self.assertTrue(self.rotation_service.is_in_rotation_cooldown(presented_item))

        self.rotation_service.begin_next_rotation(self.database.database_id)
        refreshed_item = self.suggestion_service.get_suggestion(item.id)
        self.assertFalse(self.rotation_service.is_in_rotation_cooldown(refreshed_item))


if __name__ == "__main__":
    unittest.main()
