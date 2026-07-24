"""Tests for FR-026's shared completion-presentation orchestrator.

Covers vote_completion_announcer.finalize_vote_completion() directly:
the original voting post update (closed indicator, disabled buttons,
winner, standings), the single results announcement (text + embeds), the
results-link-back edit, and graceful handling of missing/failing Discord
state. CloseVoteJobHandler and bot.py's /edit_vote "End Now" handler both
call this function unchanged -- see test_close_vote_job_handler.py and
test_edit_vote_command.py for confirmation that each path produces
identical output through it.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.vote import VoteVisibility
from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.vote_completion_announcer import finalize_vote_completion
from watch_party_manager.services.vote_completion_service import VoteCompletionService
from watch_party_manager.services.vote_service import VoteService


class SimulatedDiscordError(Exception):
    pass


class FakeVotingMessage:
    def __init__(self, *, fail: bool = False) -> None:
        self.edits: list[tuple[str, object]] = []
        self._fail = fail

    async def edit(self, *, content=None, embed="not-set", view="not-set") -> None:
        if self._fail:
            raise SimulatedDiscordError("cannot edit message")
        self.edits.append((content, view))


class FakeChannel:
    def __init__(self, message: FakeVotingMessage | None = None, *, fetch_message_fails=False, send_fails=False) -> None:
        self._message = message
        self._fetch_message_fails = fetch_message_fails
        self._send_fails = send_fails
        self.sent_messages: list[dict] = []
        self._next_message_id = 9000

    async def fetch_message(self, message_id):
        if self._fetch_message_fails or self._message is None:
            raise SimulatedDiscordError("message not found")
        return self._message

    async def send(self, *, content=None, embeds=None):
        if self._send_fails:
            raise SimulatedDiscordError("cannot send")
        self._next_message_id += 1
        sent = {"content": content, "embeds": embeds or [], "id": self._next_message_id}
        self.sent_messages.append(sent)
        return FakeSentMessage(sent["id"])


class FakeSentMessage:
    def __init__(self, message_id: int) -> None:
        self.id = message_id


class FakeBot:
    def __init__(self, channel: FakeChannel | None = None, *, fetch_channel_fails=False) -> None:
        self._channel = channel
        self._fetch_channel_fails = fetch_channel_fails

    def get_channel(self, channel_id):
        return self._channel

    async def fetch_channel(self, channel_id):
        if self._fetch_channel_fails:
            raise SimulatedDiscordError("channel not found")
        return self._channel


class FinalizeVoteCompletionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.vote_service = VoteService(
            self.suggestion_service, repository=JsonVoteRepository(root / "voting.json")
        )
        self.completion_service = VoteCompletionService(self.vote_service, self.suggestion_service)
        self.matrix = self.suggestion_service.suggest("The Matrix").watch_item
        self.inception = self.suggestion_service.suggest("Inception").watch_item

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _open_round(self, *, with_message_reference=True, guild_id=100, channel_id=200, message_id=999):
        vote_round = self.vote_service.create_round(
            visibility=VoteVisibility.VISIBLE,
            closes_at=datetime.now(timezone.utc) + timedelta(days=1),
            candidate_suggestion_ids=[self.matrix.id, self.inception.id],
        ).vote_round
        if with_message_reference:
            self.vote_service.attach_message_reference(
                vote_round.id, guild_id=guild_id, channel_id=channel_id, message_id=message_id
            )
        return self.vote_service.get_round(vote_round.id)

    def _complete(self, round_id):
        return self.completion_service.complete_round(round_id)

    # --- Exactly one announcement --------------------------------------------------

    async def test_posts_exactly_one_results_announcement(self) -> None:
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)
        result = self._complete(vote_round.id)
        message = FakeVotingMessage()
        channel = FakeChannel(message)
        bot = FakeBot(channel)

        await finalize_vote_completion(self.vote_service, self.suggestion_service, bot, result)

        self.assertEqual(len(channel.sent_messages), 1)

    async def test_announcement_includes_the_original_vote_link(self) -> None:
        vote_round = self._open_round(guild_id=100, channel_id=200, message_id=999)
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)
        result = self._complete(vote_round.id)
        message = FakeVotingMessage()
        channel = FakeChannel(message)
        bot = FakeBot(channel)

        await finalize_vote_completion(self.vote_service, self.suggestion_service, bot, result)

        self.assertIn(
            "https://discord.com/channels/100/200/999", channel.sent_messages[0]["content"]
        )

    async def test_single_winner_announcement_includes_a_thumbnail_embed(self) -> None:
        self.suggestion_service.get_suggestion(self.matrix.id).poster_url = "https://example.com/poster.jpg"
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)
        result = self._complete(vote_round.id)
        message = FakeVotingMessage()
        channel = FakeChannel(message)
        bot = FakeBot(channel)

        await finalize_vote_completion(self.vote_service, self.suggestion_service, bot, result)

        embeds = channel.sent_messages[0]["embeds"]
        self.assertEqual(len(embeds), 1)
        self.assertEqual(embeds[0].thumbnail.url, "https://example.com/poster.jpg")

    async def test_tie_announcement_has_no_thumbnails(self) -> None:
        self.suggestion_service.get_suggestion(self.matrix.id).poster_url = "https://example.com/a.jpg"
        self.suggestion_service.get_suggestion(self.inception.id).poster_url = "https://example.com/b.jpg"
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)
        self.vote_service.cast_vote(discord_user_id=2, suggestion_id=self.inception.id)
        result = self._complete(vote_round.id)
        message = FakeVotingMessage()
        channel = FakeChannel(message)
        bot = FakeBot(channel)

        await finalize_vote_completion(self.vote_service, self.suggestion_service, bot, result)

        embeds = channel.sent_messages[0]["embeds"]
        self.assertEqual(len(embeds), 2)
        for embed in embeds:
            self.assertIsNone(embed.thumbnail.url)

    async def test_persists_the_results_message_reference(self) -> None:
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)
        result = self._complete(vote_round.id)
        message = FakeVotingMessage()
        channel = FakeChannel(message)
        bot = FakeBot(channel)

        await finalize_vote_completion(self.vote_service, self.suggestion_service, bot, result)

        stored = self.vote_service.get_round(vote_round.id)
        self.assertIsNotNone(stored.results_message_id)
        self.assertEqual(stored.results_message_id, channel.sent_messages[0]["id"])

    # --- Original voting post update -----------------------------------------------

    async def test_original_post_is_marked_closed_with_disabled_buttons(self) -> None:
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)
        result = self._complete(vote_round.id)
        message = FakeVotingMessage()
        channel = FakeChannel(message)
        bot = FakeBot(channel)

        await finalize_vote_completion(self.vote_service, self.suggestion_service, bot, result)

        self.assertGreaterEqual(len(message.edits), 1)
        first_content, first_view = message.edits[0]
        self.assertIn("Voting Closed", first_content)
        self.assertIsNone(first_view)

    async def test_original_post_shows_the_winner_and_final_standings(self) -> None:
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)
        result = self._complete(vote_round.id)
        message = FakeVotingMessage()
        channel = FakeChannel(message)
        bot = FakeBot(channel)

        await finalize_vote_completion(self.vote_service, self.suggestion_service, bot, result)

        first_content, _ = message.edits[0]
        self.assertIn("Winner: The Matrix", first_content)
        self.assertIn("Final Standings:", first_content)

    async def test_original_post_is_edited_again_with_the_results_link(self) -> None:
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)
        result = self._complete(vote_round.id)
        message = FakeVotingMessage()
        channel = FakeChannel(message)
        bot = FakeBot(channel)

        await finalize_vote_completion(self.vote_service, self.suggestion_service, bot, result)

        self.assertEqual(len(message.edits), 2)
        second_content, second_view = message.edits[1]
        self.assertIn("Results announcement:", second_content)
        self.assertIsNone(second_view)

    async def test_first_edit_happens_before_the_announcement_is_sent(self) -> None:
        # The original post must not include a results link in its FIRST
        # edit -- the announcement (and its message ID) doesn't exist yet
        # at that point.
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)
        result = self._complete(vote_round.id)
        message = FakeVotingMessage()
        channel = FakeChannel(message)
        bot = FakeBot(channel)

        await finalize_vote_completion(self.vote_service, self.suggestion_service, bot, result)

        first_content, _ = message.edits[0]
        self.assertNotIn("Results announcement:", first_content)

    # --- Bug fix propagates end-to-end ----------------------------------------------

    async def test_never_says_no_votes_were_cast_when_the_winner_is_removed_after_closing(self) -> None:
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)
        result = self._complete(vote_round.id)
        # Simulate the winning suggestion disappearing after the round closed.
        self.suggestion_service._suggestions.pop(self.matrix.id, None)
        message = FakeVotingMessage()
        channel = FakeChannel(message)
        bot = FakeBot(channel)

        await finalize_vote_completion(self.vote_service, self.suggestion_service, bot, result)

        self.assertNotIn("No votes were cast", channel.sent_messages[0]["content"])
        self.assertIn("Total votes cast: 1", channel.sent_messages[0]["content"])

    # --- Graceful handling of missing/failing Discord state --------------------------

    async def test_no_channel_reference_sends_nothing_and_does_not_raise(self) -> None:
        vote_round = self._open_round(with_message_reference=False)
        result = self._complete(vote_round.id)
        bot = FakeBot(None)

        await finalize_vote_completion(self.vote_service, self.suggestion_service, bot, result)
        # No assertion needed beyond "did not raise".

    async def test_missing_original_message_still_sends_the_announcement(self) -> None:
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)
        result = self._complete(vote_round.id)
        channel = FakeChannel(message=None, fetch_message_fails=True)
        bot = FakeBot(channel)

        await finalize_vote_completion(self.vote_service, self.suggestion_service, bot, result)

        self.assertEqual(len(channel.sent_messages), 1)

    async def test_channel_resolution_failure_sends_nothing_and_does_not_raise(self) -> None:
        vote_round = self._open_round()
        result = self._complete(vote_round.id)
        bot = FakeBot(None, fetch_channel_fails=True)

        await finalize_vote_completion(self.vote_service, self.suggestion_service, bot, result)
        # No assertion needed beyond "did not raise".

    async def test_send_failure_does_not_raise_and_does_not_persist_a_results_reference(self) -> None:
        vote_round = self._open_round()
        result = self._complete(vote_round.id)
        message = FakeVotingMessage()
        channel = FakeChannel(message, send_fails=True)
        bot = FakeBot(channel)

        await finalize_vote_completion(self.vote_service, self.suggestion_service, bot, result)

        self.assertIsNone(self.vote_service.get_round(vote_round.id).results_message_id)

    async def test_original_message_edit_failure_does_not_block_the_announcement(self) -> None:
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)
        result = self._complete(vote_round.id)
        message = FakeVotingMessage(fail=True)
        channel = FakeChannel(message)
        bot = FakeBot(channel)

        await finalize_vote_completion(self.vote_service, self.suggestion_service, bot, result)

        self.assertEqual(len(channel.sent_messages), 1)


if __name__ == "__main__":
    unittest.main()
