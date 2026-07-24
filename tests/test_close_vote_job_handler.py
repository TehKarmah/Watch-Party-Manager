"""Tests for FR-016 (Automatic Vote Close Handler), FR-018 (Automatic Vote
Completion Announcement), and FR-026 (Vote Results Announcement Polish).

Covers the close_vote job handler: locating a vote by the job's payload,
closing it and determining its winner(s) via VoteCompletionService (never
duplicating winner calculation), and delegating the completion
presentation (original post update + single results announcement) to
vote_completion_announcer.finalize_vote_completion() -- the same function
/edit_vote's "End Now" action uses, so both paths are covered identically
here and in test_edit_vote_command.py.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from watch_party_manager.domain.vote import VoteRoundStatus, VoteVisibility
from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.scheduler.close_vote_job_handler import CloseVoteJobHandler
from watch_party_manager.scheduler.job_handler import JobExecutionResult
from watch_party_manager.scheduler.scheduled_job import JobResult, ScheduledJob
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.vote_completion_service import VoteCompletionService
from watch_party_manager.services.vote_service import VoteService


def make_job(vote_id: int, run_at: datetime | None = None) -> ScheduledJob:
    if run_at is None:
        run_at = datetime.now(timezone.utc)
    return ScheduledJob(
        guild_id=100,
        job_type="close_vote",
        logical_key=f"vote:{vote_id}:close",
        run_at=run_at,
        payload={"vote_id": vote_id},
    )


class FakeMessage:
    def __init__(self) -> None:
        self.edits: list[tuple[str, object]] = []

    async def edit(self, *, content=None, embed="not-set", view="not-set") -> None:
        self.edits.append((content, view))


class FakeChannel:
    def __init__(self, message: FakeMessage | None = None) -> None:
        self.message = message if message is not None else FakeMessage()
        self.sent_messages: list[str] = []
        self.sent_embeds: list[list] = []
        self._next_message_id = 9000

    async def fetch_message(self, message_id):
        return self.message

    async def send(self, *, content=None, embeds=None):
        self._next_message_id += 1
        self.sent_messages.append(content)
        self.sent_embeds.append(embeds or [])
        return FakeSentMessage(self._next_message_id)


class FakeSentMessage:
    def __init__(self, message_id: int) -> None:
        self.id = message_id


class FakeBot:
    """Duck-typed stand-in for a discord.Client/Bot, matching
    DiscordChannelMessenger's minimal interface requirement.
    """

    def __init__(self, channel: FakeChannel | None = None) -> None:
        self._channel = channel

    def get_channel(self, channel_id):
        return self._channel

    async def fetch_channel(self, channel_id):
        return self._channel


class CloseVoteJobHandlerTests(unittest.IsolatedAsyncioTestCase):
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
        self.channel = FakeChannel()
        self.bot = FakeBot(self.channel)
        self.handler = CloseVoteJobHandler(
            self.completion_service, self.vote_service, self.suggestion_service, self.bot
        )

        self.matrix = self.suggestion_service.suggest("The Matrix").watch_item
        self.inception = self.suggestion_service.suggest("Inception").watch_item

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _open_round(self, closes_at=None, guild_id=100, channel_id=200):
        if closes_at is None:
            closes_at = datetime.now(timezone.utc) + timedelta(days=1)
        vote_round = self.vote_service.create_round(
            visibility=VoteVisibility.VISIBLE,
            closes_at=closes_at,
            candidate_suggestion_ids=[self.matrix.id, self.inception.id],
        ).vote_round
        self.vote_service.attach_message_reference(
            vote_round.id, guild_id=guild_id, channel_id=channel_id, message_id=999
        )
        return self.vote_service.get_round(vote_round.id)

    # --- Happy path: locate, verify, close, determine winner(s), announce ----

    async def test_closes_an_open_round(self) -> None:
        vote_round = self._open_round()

        result = await self.handler.execute(make_job(vote_round.id))

        self.assertEqual(result, JobExecutionResult(result=JobResult.EXECUTED))
        self.assertEqual(self.vote_service.get_round(vote_round.id).status, VoteRoundStatus.CLOSED)

    async def test_returns_executed_result_on_success(self) -> None:
        vote_round = self._open_round()

        result = await self.handler.execute(make_job(vote_round.id))

        self.assertEqual(result.result, JobResult.EXECUTED)

    async def test_posts_exactly_one_completion_announcement_to_the_rounds_channel(self) -> None:
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)

        await self.handler.execute(make_job(vote_round.id))

        self.assertEqual(len(self.channel.sent_messages), 1)
        self.assertIn(f"Voting round {vote_round.id} has closed!", self.channel.sent_messages[0])

    async def test_determines_a_single_winner(self) -> None:
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)
        self.vote_service.cast_vote(discord_user_id=2, suggestion_id=self.matrix.id)
        self.vote_service.cast_vote(discord_user_id=3, suggestion_id=self.inception.id)

        await self.handler.execute(make_job(vote_round.id))

        winners = self.vote_service.get_current_winners(vote_round.id)
        self.assertEqual(winners.winning_suggestion_ids, [self.matrix.id])
        self.assertIn("Winner: The Matrix", self.channel.sent_messages[0])

    async def test_supports_a_tie(self) -> None:
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)
        self.vote_service.cast_vote(discord_user_id=2, suggestion_id=self.inception.id)

        await self.handler.execute(make_job(vote_round.id))

        winners = self.vote_service.get_current_winners(vote_round.id)
        self.assertEqual(
            sorted(winners.winning_suggestion_ids), sorted([self.matrix.id, self.inception.id])
        )
        announcement = self.channel.sent_messages[0]
        self.assertIn("tie", announcement.lower())
        self.assertIn("The Matrix", announcement)
        self.assertIn("Inception", announcement)

    async def test_no_votes_cast_produces_no_winners_and_says_so_in_the_announcement(self) -> None:
        vote_round = self._open_round()

        result = await self.handler.execute(make_job(vote_round.id))

        self.assertEqual(result.result, JobResult.EXECUTED)
        winners = self.vote_service.get_current_winners(vote_round.id)
        self.assertEqual(winners.winning_suggestion_ids, [])
        self.assertIn("No votes were cast", self.channel.sent_messages[0])

    async def test_announcement_links_the_winner_to_its_original_suggestion_not_imdb(self) -> None:
        self.suggestion_service.update_suggestion_identity(
            self.matrix.id, "The Matrix", "https://www.imdb.com/title/tt0133093/"
        )
        self.suggestion_service.attach_message_reference(self.matrix.id, message_id=500)
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)

        await self.handler.execute(make_job(vote_round.id))

        announcement = self.channel.sent_messages[0]
        self.assertNotIn("imdb.com", announcement)
        self.assertIn("The Matrix", announcement)

    async def test_falls_back_to_fetch_channel_when_get_channel_returns_none(self) -> None:
        vote_round = self._open_round()

        class FetchOnlyBot:
            def __init__(self, channel) -> None:
                self._channel = channel

            def get_channel(self, channel_id):
                return None

            async def fetch_channel(self, channel_id):
                return self._channel

        handler = CloseVoteJobHandler(
            self.completion_service, self.vote_service, self.suggestion_service, FetchOnlyBot(self.channel)
        )

        result = await handler.execute(make_job(vote_round.id))

        self.assertEqual(result.result, JobResult.EXECUTED)
        self.assertEqual(len(self.channel.sent_messages), 1)

    async def test_missing_channel_reference_still_closes_without_erroring(self) -> None:
        vote_round = self.vote_service.create_round(
            visibility=VoteVisibility.VISIBLE,
            closes_at=datetime.now(timezone.utc) + timedelta(days=1),
            candidate_suggestion_ids=[self.matrix.id, self.inception.id],
        ).vote_round

        result = await self.handler.execute(make_job(vote_round.id))

        self.assertEqual(result.result, JobResult.EXECUTED)
        self.assertEqual(self.vote_service.get_round(vote_round.id).status, VoteRoundStatus.CLOSED)
        self.assertEqual(self.channel.sent_messages, [])

    async def test_closing_persists_the_updated_round(self) -> None:
        vote_round = self._open_round()

        await self.handler.execute(make_job(vote_round.id))

        reloaded_vote_service = VoteService(
            self.suggestion_service,
            repository=JsonVoteRepository(Path(self._temp_dir.name) / "voting.json"),
        )
        self.assertEqual(reloaded_vote_service.get_round(vote_round.id).status, VoteRoundStatus.CLOSED)

    async def test_closing_updates_the_winners_watch_item_journey(self) -> None:
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)

        await self.handler.execute(make_job(vote_round.id))

        winner = self.suggestion_service.get_suggestion(self.matrix.id)
        self.assertEqual(winner.journey.times_won, 1)

    # --- FR-026: original post + results linking -------------------------------

    async def test_original_voting_post_is_closed_with_disabled_buttons(self) -> None:
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)

        await self.handler.execute(make_job(vote_round.id))

        first_content, first_view = self.channel.message.edits[0]
        self.assertIn("Voting Closed", first_content)
        self.assertIsNone(first_view)

    async def test_original_voting_post_is_later_linked_to_the_results_announcement(self) -> None:
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)

        await self.handler.execute(make_job(vote_round.id))

        self.assertEqual(len(self.channel.message.edits), 2)
        second_content, _ = self.channel.message.edits[1]
        self.assertIn("Results announcement:", second_content)

    async def test_results_message_reference_is_persisted(self) -> None:
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)

        await self.handler.execute(make_job(vote_round.id))

        self.assertIsNotNone(self.vote_service.get_round(vote_round.id).results_message_id)

    async def test_single_winner_announcement_includes_a_thumbnail(self) -> None:
        self.matrix.poster_url = "https://example.com/poster.jpg"
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)

        await self.handler.execute(make_job(vote_round.id))

        embeds = self.channel.sent_embeds[0]
        self.assertEqual(len(embeds), 1)
        self.assertEqual(embeds[0].thumbnail.url, "https://example.com/poster.jpg")

    async def test_tie_announcement_has_no_thumbnails(self) -> None:
        self.matrix.poster_url = "https://example.com/a.jpg"
        self.inception.poster_url = "https://example.com/b.jpg"
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)
        self.vote_service.cast_vote(discord_user_id=2, suggestion_id=self.inception.id)

        await self.handler.execute(make_job(vote_round.id))

        embeds = self.channel.sent_embeds[0]
        self.assertEqual(len(embeds), 2)
        for embed in embeds:
            self.assertIsNone(embed.thumbnail.url)

    # --- Already closed manually: successful no-op ----------------------------

    async def test_an_already_closed_round_is_a_successful_no_op(self) -> None:
        vote_round = self._open_round()
        self.vote_service.close_round(vote_round.id)

        result = await self.handler.execute(make_job(vote_round.id))

        self.assertEqual(result, JobExecutionResult(result=JobResult.SKIPPED_NOT_APPLICABLE))
        self.assertEqual(self.channel.sent_messages, [])

    async def test_already_closed_round_does_not_raise(self) -> None:
        vote_round = self._open_round()
        self.vote_service.close_round(vote_round.id)

        # Should not raise -- this is the whole point of the no-op contract.
        await self.handler.execute(make_job(vote_round.id))

    # --- Vote no longer exists: successful no-op -------------------------------

    async def test_a_nonexistent_vote_is_a_successful_no_op(self) -> None:
        result = await self.handler.execute(make_job(vote_id=999))

        self.assertEqual(result, JobExecutionResult(result=JobResult.SKIPPED_NOT_APPLICABLE))
        self.assertEqual(self.channel.sent_messages, [])

    # --- Idempotency: running twice is safe -------------------------------------

    async def test_running_the_job_twice_is_safe(self) -> None:
        vote_round = self._open_round()

        first = await self.handler.execute(make_job(vote_round.id))
        second = await self.handler.execute(make_job(vote_round.id))

        self.assertEqual(first.result, JobResult.EXECUTED)
        self.assertEqual(second.result, JobResult.SKIPPED_NOT_APPLICABLE)
        self.assertEqual(self.vote_service.get_round(vote_round.id).status, VoteRoundStatus.CLOSED)

    async def test_running_the_job_twice_does_not_change_the_winner(self) -> None:
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)

        await self.handler.execute(make_job(vote_round.id))
        await self.handler.execute(make_job(vote_round.id))

        winners = self.vote_service.get_current_winners(vote_round.id)
        self.assertEqual(winners.winning_suggestion_ids, [self.matrix.id])

    async def test_running_the_job_twice_only_sends_one_announcement(self) -> None:
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)

        await self.handler.execute(make_job(vote_round.id))
        await self.handler.execute(make_job(vote_round.id))

        self.assertEqual(len(self.channel.sent_messages), 1)

    async def test_running_the_job_twice_does_not_double_count_watch_history(self) -> None:
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)

        await self.handler.execute(make_job(vote_round.id))
        await self.handler.execute(make_job(vote_round.id))

        winner = self.suggestion_service.get_suggestion(self.matrix.id)
        self.assertEqual(winner.journey.times_won, 1)

    # --- Multiple rounds don't interfere -----------------------------------------

    async def test_closing_one_round_leaves_a_different_open_round_untouched(self) -> None:
        first_round = self._open_round()
        self.vote_service.close_round(first_round.id)
        second_round = self._open_round()

        await self.handler.execute(make_job(second_round.id))

        self.assertEqual(self.vote_service.get_round(first_round.id).status, VoteRoundStatus.CLOSED)
        self.assertEqual(self.vote_service.get_round(second_round.id).status, VoteRoundStatus.CLOSED)

    async def test_a_close_vote_job_for_one_round_never_closes_a_different_round(self) -> None:
        first_round = self._open_round()

        # A job whose payload references a round that doesn't exist must
        # never accidentally act on whatever round happens to be open.
        await self.handler.execute(make_job(vote_id=first_round.id + 999))

        self.assertEqual(self.vote_service.get_round(first_round.id).status, VoteRoundStatus.OPEN)

    # --- Payload handling ------------------------------------------------------------

    async def test_missing_vote_id_in_payload_raises(self) -> None:
        job = ScheduledJob(
            guild_id=100,
            job_type="close_vote",
            logical_key="vote:1:close",
            run_at=datetime.now(timezone.utc),
            payload={},
        )

        with self.assertRaises(KeyError):
            await self.handler.execute(job)


class CloseVoteJobHandlerSchedulerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Confirms the handler works when driven through the real
    SchedulerService.register_handler()/run_once() path, not just called
    directly -- i.e. that FR-016/FR-018's registration actually takes
    effect, and that the scheduler's own job lifecycle (a completed job is
    never re-claimed) is what keeps repeated polling from sending
    duplicate announcements.
    """

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

    async def test_scheduler_run_once_executes_the_registered_handler(self) -> None:
        from watch_party_manager.scheduler.json_scheduler_repository import JsonSchedulerRepository
        from watch_party_manager.scheduler.scheduler_service import SchedulerService
        from watch_party_manager.scheduler.vote_scheduling import build_close_vote_job

        scheduler_repository = JsonSchedulerRepository(Path(self._temp_dir.name) / "scheduled_jobs.json")
        scheduler_service = SchedulerService(scheduler_repository)
        channel = FakeChannel()
        scheduler_service.register_handler(
            "close_vote",
            CloseVoteJobHandler(self.completion_service, self.vote_service, self.suggestion_service, FakeBot(channel)),
        )

        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        vote_round = self.vote_service.create_round(
            visibility=VoteVisibility.VISIBLE,
            closes_at=past,
            candidate_suggestion_ids=[self.matrix.id, self.inception.id],
        ).vote_round
        self.vote_service.attach_message_reference(
            vote_round.id, guild_id=100, channel_id=200, message_id=999
        )
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)
        await scheduler_service.schedule(build_close_vote_job(vote_round, guild_id=100))

        processed = await scheduler_service.run_once()

        self.assertEqual(processed, 1)
        self.assertEqual(self.vote_service.get_round(vote_round.id).status, VoteRoundStatus.CLOSED)
        self.assertEqual(len(channel.sent_messages), 1)
        self.assertIn("Winner: The Matrix", channel.sent_messages[0])

    async def test_repeated_polling_only_sends_one_announcement(self) -> None:
        from watch_party_manager.scheduler.json_scheduler_repository import JsonSchedulerRepository
        from watch_party_manager.scheduler.scheduler_service import SchedulerService
        from watch_party_manager.scheduler.vote_scheduling import build_close_vote_job

        scheduler_repository = JsonSchedulerRepository(Path(self._temp_dir.name) / "scheduled_jobs.json")
        scheduler_service = SchedulerService(scheduler_repository)
        channel = FakeChannel()
        scheduler_service.register_handler(
            "close_vote",
            CloseVoteJobHandler(self.completion_service, self.vote_service, self.suggestion_service, FakeBot(channel)),
        )

        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        vote_round = self.vote_service.create_round(
            visibility=VoteVisibility.VISIBLE,
            closes_at=past,
            candidate_suggestion_ids=[self.matrix.id, self.inception.id],
        ).vote_round
        self.vote_service.attach_message_reference(
            vote_round.id, guild_id=100, channel_id=200, message_id=999
        )
        await scheduler_service.schedule(build_close_vote_job(vote_round, guild_id=100))

        first_processed = await scheduler_service.run_once()
        second_processed = await scheduler_service.run_once()

        self.assertEqual(first_processed, 1)
        self.assertEqual(second_processed, 0)
        self.assertEqual(len(channel.sent_messages), 1)


if __name__ == "__main__":
    unittest.main()
