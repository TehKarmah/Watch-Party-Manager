"""Tests for the watch_party_completion scheduler job handler (Watch
Party Lifecycle): claiming a due job, completing the watch party through
WatchPartyCompletionService, and invoking the optional on_finalized hook
bot.py uses to sync the suggestion's status embed and post an
announcement.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.watch_party_repository import JsonWatchPartyRepository
from watch_party_manager.scheduler.scheduled_job import JobResult, ScheduledJob
from watch_party_manager.scheduler.watch_party_completion_job_handler import WatchPartyCompletionJobHandler
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.watch_party_completion_service import WatchPartyCompletionService
from watch_party_manager.services.watch_party_service import WatchPartyService


def make_job(watch_party_id: int, run_at: datetime | None = None) -> ScheduledJob:
    if run_at is None:
        run_at = datetime.now(timezone.utc)
    return ScheduledJob(
        guild_id=100,
        job_type="watch_party_completion",
        logical_key=f"watch_party:{watch_party_id}:completion",
        run_at=run_at,
        payload={"watch_party_id": watch_party_id},
    )


class WatchPartyCompletionJobHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.watch_party_service = WatchPartyService(
            self.suggestion_service, repository=JsonWatchPartyRepository(root / "watch_parties.json")
        )
        self.completion_service = WatchPartyCompletionService(self.watch_party_service, self.suggestion_service)
        self.matrix = self.suggestion_service.suggest("The Matrix").watch_item
        self.suggestion_service.record_vote_win(self.matrix.id, datetime.now(timezone.utc).date())

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _schedule(self, *, duration_minutes=90):
        return self.watch_party_service.schedule_watch_party(
            watch_item_id=self.matrix.id,
            scheduled_at=datetime.now(timezone.utc) - timedelta(hours=3),
            guild_id=100,
            duration_minutes=duration_minutes,
        ).watch_party

    async def test_completes_a_due_watch_party(self) -> None:
        watch_party = self._schedule()
        handler = WatchPartyCompletionJobHandler(self.completion_service)

        result = await handler.execute(make_job(watch_party.id))

        self.assertEqual(result.result, JobResult.EXECUTED)

    async def test_skips_a_nonexistent_watch_party(self) -> None:
        handler = WatchPartyCompletionJobHandler(self.completion_service)

        result = await handler.execute(make_job(999))

        self.assertEqual(result.result, JobResult.SKIPPED_NOT_APPLICABLE)

    async def test_skips_an_already_completed_watch_party(self) -> None:
        watch_party = self._schedule()
        handler = WatchPartyCompletionJobHandler(self.completion_service)
        await handler.execute(make_job(watch_party.id))

        result = await handler.execute(make_job(watch_party.id))

        self.assertEqual(result.result, JobResult.SKIPPED_NOT_APPLICABLE)

    async def test_calls_on_finalized_with_the_result(self) -> None:
        watch_party = self._schedule()
        received = []

        async def on_finalized(result) -> None:
            received.append(result)

        handler = WatchPartyCompletionJobHandler(self.completion_service, on_finalized=on_finalized)

        await handler.execute(make_job(watch_party.id))

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].watch_item.id, self.matrix.id)

    async def test_on_finalized_is_not_called_when_nothing_completed(self) -> None:
        received = []

        async def on_finalized(result) -> None:
            received.append(result)

        handler = WatchPartyCompletionJobHandler(self.completion_service, on_finalized=on_finalized)

        await handler.execute(make_job(999))

        self.assertEqual(received, [])

    async def test_missing_payload_key_raises(self) -> None:
        handler = WatchPartyCompletionJobHandler(self.completion_service)
        bad_job = ScheduledJob(
            guild_id=100, job_type="watch_party_completion", logical_key="bad", run_at=datetime.now(timezone.utc), payload={}
        )

        with self.assertRaises(KeyError):
            await handler.execute(bad_job)


if __name__ == "__main__":
    unittest.main()
