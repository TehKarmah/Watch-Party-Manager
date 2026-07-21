"""Tests for FR-016: Automatic Vote Close Handler.

Covers only the close_vote job handler this milestone adds -- locating a
vote by the job's payload, closing it, and determining its winner(s) by
reusing VoteService directly. No reminder execution, Discord
announcements, or new scheduler infrastructure are exercised here, since
none of that is implemented by this milestone.
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
        self.handler = CloseVoteJobHandler(self.vote_service)

        self.matrix = self.suggestion_service.suggest("The Matrix").watch_item
        self.inception = self.suggestion_service.suggest("Inception").watch_item

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _open_round(self, closes_at=None):
        if closes_at is None:
            closes_at = datetime.now(timezone.utc) + timedelta(days=1)
        return self.vote_service.create_round(
            visibility=VoteVisibility.VISIBLE,
            closes_at=closes_at,
            candidate_suggestion_ids=[self.matrix.id, self.inception.id],
        ).vote_round

    # --- Happy path: locate, verify, close, determine winner(s) --------------

    async def test_closes_an_open_round(self) -> None:
        vote_round = self._open_round()

        result = await self.handler.execute(make_job(vote_round.id))

        self.assertEqual(result, JobExecutionResult(result=JobResult.EXECUTED))
        self.assertEqual(self.vote_service.get_round(vote_round.id).status, VoteRoundStatus.CLOSED)

    async def test_returns_executed_result_on_success(self) -> None:
        vote_round = self._open_round()

        result = await self.handler.execute(make_job(vote_round.id))

        self.assertEqual(result.result, JobResult.EXECUTED)

    async def test_determines_a_single_winner(self) -> None:
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)
        self.vote_service.cast_vote(discord_user_id=2, suggestion_id=self.matrix.id)
        self.vote_service.cast_vote(discord_user_id=3, suggestion_id=self.inception.id)

        await self.handler.execute(make_job(vote_round.id))

        winners = self.vote_service.get_current_winners(vote_round.id)
        self.assertEqual(winners.winning_suggestion_ids, [self.matrix.id])

    async def test_supports_a_tie(self) -> None:
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)
        self.vote_service.cast_vote(discord_user_id=2, suggestion_id=self.inception.id)

        await self.handler.execute(make_job(vote_round.id))

        winners = self.vote_service.get_current_winners(vote_round.id)
        self.assertEqual(
            sorted(winners.winning_suggestion_ids), sorted([self.matrix.id, self.inception.id])
        )

    async def test_no_votes_cast_produces_no_winners_without_erroring(self) -> None:
        vote_round = self._open_round()

        result = await self.handler.execute(make_job(vote_round.id))

        self.assertEqual(result.result, JobResult.EXECUTED)
        winners = self.vote_service.get_current_winners(vote_round.id)
        self.assertEqual(winners.winning_suggestion_ids, [])

    async def test_closing_persists_the_updated_round(self) -> None:
        vote_round = self._open_round()

        await self.handler.execute(make_job(vote_round.id))

        reloaded_vote_service = VoteService(
            self.suggestion_service,
            repository=JsonVoteRepository(Path(self._temp_dir.name) / "voting.json"),
        )
        self.assertEqual(reloaded_vote_service.get_round(vote_round.id).status, VoteRoundStatus.CLOSED)

    # --- Already closed manually: successful no-op ----------------------------

    async def test_an_already_closed_round_is_a_successful_no_op(self) -> None:
        vote_round = self._open_round()
        self.vote_service.close_round(vote_round.id)

        result = await self.handler.execute(make_job(vote_round.id))

        self.assertEqual(result, JobExecutionResult(result=JobResult.SKIPPED_NOT_APPLICABLE))

    async def test_already_closed_round_does_not_raise(self) -> None:
        vote_round = self._open_round()
        self.vote_service.close_round(vote_round.id)

        # Should not raise -- this is the whole point of the no-op contract.
        await self.handler.execute(make_job(vote_round.id))

    # --- Vote no longer exists: successful no-op -------------------------------

    async def test_a_nonexistent_vote_is_a_successful_no_op(self) -> None:
        result = await self.handler.execute(make_job(vote_id=999))

        self.assertEqual(result, JobExecutionResult(result=JobResult.SKIPPED_NOT_APPLICABLE))

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
    directly -- i.e. that FR-016's registration actually takes effect.
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
        self.matrix = self.suggestion_service.suggest("The Matrix").watch_item
        self.inception = self.suggestion_service.suggest("Inception").watch_item

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    async def test_scheduler_run_once_executes_the_registered_handler(self) -> None:
        from watch_party_manager.scheduler.json_scheduler_repository import JsonSchedulerRepository
        from watch_party_manager.scheduler.scheduler_service import SchedulerService
        from watch_party_manager.scheduler.vote_scheduling import schedule_vote_jobs

        scheduler_repository = JsonSchedulerRepository(Path(self._temp_dir.name) / "scheduled_jobs.json")
        scheduler_service = SchedulerService(scheduler_repository)
        scheduler_service.register_handler("close_vote", CloseVoteJobHandler(self.vote_service))

        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        vote_round = self.vote_service.create_round(
            visibility=VoteVisibility.VISIBLE,
            closes_at=past,
            candidate_suggestion_ids=[self.matrix.id, self.inception.id],
        ).vote_round
        await schedule_vote_jobs(scheduler_service, vote_round, guild_id=100)

        processed = await scheduler_service.run_once()

        self.assertGreaterEqual(processed, 1)
        self.assertEqual(self.vote_service.get_round(vote_round.id).status, VoteRoundStatus.CLOSED)


if __name__ == "__main__":
    unittest.main()
