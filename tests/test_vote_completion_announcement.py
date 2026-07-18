import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import (
    build_vote_completion_announcement,
    check_and_announce_expired_vote,
    perform_vote_completion_check,
)
from watch_party_manager.domain.vote import VoteRound, VoteRoundStatus, VoteVisibility
from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.vote_completion_service import VoteCompletionService
from watch_party_manager.services.vote_service import StandingsEntry, VoteService


class FakeChannel:
    def __init__(self) -> None:
        self.sent_messages = []

    async def send(self, content) -> None:
        self.sent_messages.append(content)


class FakeBot:
    """Duck-typed stand-in for a discord.Client/Bot, matching
    check_and_announce_expired_vote's minimal interface requirement.
    """

    def __init__(self, channel: FakeChannel = None) -> None:
        self._channel = channel

    def get_channel(self, channel_id):
        return self._channel

    async def fetch_channel(self, channel_id):
        return self._channel


class BuildVoteCompletionAnnouncementTests(unittest.TestCase):
    def _round(self, round_id=1, visibility=VoteVisibility.VISIBLE):
        return VoteRound(id=round_id, status=VoteRoundStatus.CLOSED, visibility=visibility)

    def test_announces_a_single_winner(self) -> None:
        text = build_vote_completion_announcement(self._round(), ["The Matrix"], [], 3)

        self.assertIn("Winner: The Matrix", text)

    def test_announces_a_tie_with_all_winning_titles(self) -> None:
        text = build_vote_completion_announcement(self._round(), ["The Matrix", "Inception"], [], 2)

        self.assertIn("tie", text.lower())
        self.assertIn("The Matrix", text)
        self.assertIn("Inception", text)

    def test_announces_no_winner_when_no_votes_were_cast(self) -> None:
        text = build_vote_completion_announcement(self._round(), [], [], 0)

        self.assertIn("No votes were cast", text)
        self.assertNotIn("Winner:", text)

    def test_shows_total_votes_cast(self) -> None:
        text = build_vote_completion_announcement(self._round(), ["The Matrix"], [], 7)

        self.assertIn("Total votes cast: 7", text)

    def test_shows_standings_even_for_a_round_that_was_blind_while_open(self) -> None:
        # The round is closed by the time this is called, so blind voting's
        # "reveal only after voting has closed" rule is satisfied simply by
        # this function always showing standings -- there is no separate
        # branch needed for blind vs. visible here.
        standings = [StandingsEntry(suggestion_id=1, vote_count=2)]

        text = build_vote_completion_announcement(
            self._round(visibility=VoteVisibility.BLIND), ["The Matrix"], standings, 2
        )

        self.assertIn("Standings:", text)
        self.assertIn("Suggestion #1", text)

    def test_shows_standings_for_a_round_that_was_visible(self) -> None:
        standings = [StandingsEntry(suggestion_id=1, vote_count=2)]

        text = build_vote_completion_announcement(
            self._round(visibility=VoteVisibility.VISIBLE), ["The Matrix"], standings, 2
        )

        self.assertIn("Standings:", text)

    def test_mentions_round_id(self) -> None:
        text = build_vote_completion_announcement(self._round(round_id=42), ["The Matrix"], [], 1)

        self.assertIn("42", text)


class VoteCompletionCheckIntegrationTests(unittest.IsolatedAsyncioTestCase):
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

    def _open_expired_round(self, guild_id=100, channel_id=200):
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        result = self.vote_service.create_round(
            visibility=VoteVisibility.VISIBLE,
            closes_at=past,
            candidate_suggestion_ids=[self.matrix.id, self.inception.id],
        )
        self.vote_service.attach_message_reference(
            result.vote_round.id, guild_id=guild_id, channel_id=channel_id, message_id=999
        )
        return result.vote_round

    # --- perform_vote_completion_check (Discord-free core) ----------------------

    def test_perform_check_returns_none_when_nothing_is_due(self) -> None:
        outcome = perform_vote_completion_check(self.completion_service, self.suggestion_service)
        self.assertIsNone(outcome)

    def test_perform_check_returns_the_round_and_announcement_text(self) -> None:
        self._open_expired_round()
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)

        outcome = perform_vote_completion_check(
            self.completion_service, self.suggestion_service, now=datetime.now(timezone.utc)
        )

        self.assertIsNotNone(outcome)
        vote_round, announcement = outcome
        self.assertEqual(vote_round.status, VoteRoundStatus.CLOSED)
        self.assertIn("Winner: The Matrix", announcement)

    # --- check_and_announce_expired_vote (Discord I/O wrapper) ------------------

    async def test_sends_the_announcement_to_the_rounds_channel(self) -> None:
        self._open_expired_round(guild_id=100, channel_id=200)
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)
        channel = FakeChannel()
        bot = FakeBot(channel)

        sent = await check_and_announce_expired_vote(
            bot, self.completion_service, self.suggestion_service, now=datetime.now(timezone.utc)
        )

        self.assertTrue(sent)
        self.assertEqual(len(channel.sent_messages), 1)
        self.assertIn("Winner: The Matrix", channel.sent_messages[0])

    async def test_returns_false_and_sends_nothing_when_no_round_is_due(self) -> None:
        channel = FakeChannel()
        bot = FakeBot(channel)

        sent = await check_and_announce_expired_vote(bot, self.completion_service, self.suggestion_service)

        self.assertFalse(sent)
        self.assertEqual(channel.sent_messages, [])

    async def test_falls_back_to_fetch_channel_when_get_channel_returns_none(self) -> None:
        self._open_expired_round()
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)

        class FetchOnlyBot:
            def __init__(self, channel) -> None:
                self._channel = channel

            def get_channel(self, channel_id):
                return None

            async def fetch_channel(self, channel_id):
                return self._channel

        channel = FakeChannel()
        bot = FetchOnlyBot(channel)

        sent = await check_and_announce_expired_vote(
            bot, self.completion_service, self.suggestion_service, now=datetime.now(timezone.utc)
        )

        self.assertTrue(sent)
        self.assertEqual(len(channel.sent_messages), 1)

    async def test_repeated_calls_only_send_one_announcement(self) -> None:
        self._open_expired_round()
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)
        channel = FakeChannel()
        bot = FakeBot(channel)

        await check_and_announce_expired_vote(
            bot, self.completion_service, self.suggestion_service, now=datetime.now(timezone.utc)
        )
        second_call_result = await check_and_announce_expired_vote(
            bot, self.completion_service, self.suggestion_service, now=datetime.now(timezone.utc)
        )

        self.assertFalse(second_call_result)
        self.assertEqual(len(channel.sent_messages), 1)

    # --- Restart safety end-to-end -----------------------------------------------

    async def test_restart_safety_detects_and_announces_after_simulated_restart(self) -> None:
        # The round expired "while the bot was offline" -- deadline is a
        # full day in the past, simulating a restart happening long after
        # the scheduled close.
        far_past = datetime.now(timezone.utc) - timedelta(days=1)
        result = self.vote_service.create_round(
            visibility=VoteVisibility.VISIBLE,
            closes_at=far_past,
            candidate_suggestion_ids=[self.matrix.id, self.inception.id],
        )
        self.vote_service.attach_message_reference(
            result.vote_round.id, guild_id=100, channel_id=200, message_id=999
        )
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)

        # Simulate the restart: fresh services reloaded from the same
        # persisted files, fresh completion service, as setup_hook would
        # construct on a real boot.
        root = Path(self._temp_dir.name)
        restarted_suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        restarted_vote_service = VoteService(
            restarted_suggestion_service, repository=JsonVoteRepository(root / "voting.json")
        )
        restarted_completion_service = VoteCompletionService(
            restarted_vote_service, restarted_suggestion_service
        )
        channel = FakeChannel()
        bot = FakeBot(channel)

        sent = await check_and_announce_expired_vote(
            bot, restarted_completion_service, restarted_suggestion_service
        )

        self.assertTrue(sent)
        self.assertEqual(len(channel.sent_messages), 1)
        self.assertIn("Winner: The Matrix", channel.sent_messages[0])
        self.assertEqual(restarted_vote_service.get_open_round(), None)

        winner = restarted_suggestion_service.get_suggestion(self.matrix.id)
        self.assertEqual(winner.journey.times_won, 1)

    async def test_missing_channel_reference_still_completes_the_round_without_erroring(self) -> None:
        # A round with no channel_id (e.g. an older persisted round from
        # before message references existed) must not crash the check --
        # it should still close and record history, just skip sending.
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        self.vote_service.create_round(
            visibility=VoteVisibility.VISIBLE,
            closes_at=past,
            candidate_suggestion_ids=[self.matrix.id, self.inception.id],
        )
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)
        bot = FakeBot(channel=None)

        sent = await check_and_announce_expired_vote(
            bot, self.completion_service, self.suggestion_service, now=datetime.now(timezone.utc)
        )

        self.assertTrue(sent)
        self.assertEqual(self.vote_service.get_open_round(), None)


if __name__ == "__main__":
    unittest.main()
