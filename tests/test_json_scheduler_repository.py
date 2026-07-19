from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from watch_party_manager.scheduler.json_scheduler_repository import (
    DuplicateActiveJobError,
    InvalidSchedulerDataError,
    JsonSchedulerRepository,
    ScheduledJobNotFoundError,
)
from watch_party_manager.scheduler.scheduled_job import (
    JobResult,
    JobStatus,
    ScheduledJob,
)


NOW = datetime(2026, 7, 19, 12, tzinfo=timezone.utc)


class JsonSchedulerRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_directory = TemporaryDirectory()
        self.file_path = Path(self.temp_directory.name) / "scheduled_jobs.json"
        self.repository = JsonSchedulerRepository(self.file_path)

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def make_job(
        self,
        *,
        logical_key: str = "close_vote:1",
        run_at: datetime = NOW,
    ) -> ScheduledJob:
        return ScheduledJob(
            guild_id=123,
            job_type="close_vote",
            logical_key=logical_key,
            run_at=run_at,
            created_at=NOW,
            payload={"vote_id": "1"},
        )

    async def test_missing_file_behaves_as_empty_repository(self) -> None:
        self.assertEqual(await self.repository.list_all(), [])
        self.assertIsNone(
            await self.repository.find_active_by_logical_key("close_vote:1")
        )

    async def test_add_persists_and_round_trips_job(self) -> None:
        job = self.make_job()

        await self.repository.add(job)
        loaded = await self.repository.get_by_id(job.job_id)

        self.assertEqual(loaded, job)
        self.assertTrue(self.file_path.exists())

    async def test_file_uses_versioned_document_format(self) -> None:
        await self.repository.add(self.make_job())

        document = json.loads(self.file_path.read_text(encoding="utf-8"))

        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(len(document["jobs"]), 1)

    async def test_duplicate_active_logical_key_is_rejected(self) -> None:
        await self.repository.add(self.make_job())

        with self.assertRaises(DuplicateActiveJobError):
            await self.repository.add(self.make_job())

    async def test_completed_job_does_not_block_later_same_logical_key(self) -> None:
        first = await self.repository.add(self.make_job())
        claimed = await self.repository.claim(first.job_id, NOW)
        self.assertIsNotNone(claimed)
        await self.repository.complete(first.job_id, NOW, JobResult.EXECUTED)

        second = await self.repository.add(self.make_job())

        self.assertNotEqual(second.job_id, first.job_id)

    async def test_get_due_returns_pending_jobs_in_run_order(self) -> None:
        later = await self.repository.add(
            self.make_job(
                logical_key="close_vote:later",
                run_at=NOW - timedelta(minutes=1),
            )
        )
        earlier = await self.repository.add(
            self.make_job(
                logical_key="close_vote:earlier",
                run_at=NOW - timedelta(minutes=2),
            )
        )
        await self.repository.add(
            self.make_job(
                logical_key="close_vote:future",
                run_at=NOW + timedelta(minutes=1),
            )
        )

        due = await self.repository.get_due(NOW)

        self.assertEqual([job.job_id for job in due], [earlier.job_id, later.job_id])

    async def test_claim_increments_attempt_and_prevents_second_claim(self) -> None:
        job = await self.repository.add(self.make_job())

        first_claim = await self.repository.claim(job.job_id, NOW)
        second_claim = await self.repository.claim(job.job_id, NOW)

        self.assertIsNotNone(first_claim)
        self.assertEqual(first_claim.status, JobStatus.RUNNING)
        self.assertEqual(first_claim.attempt_count, 1)
        self.assertIsNone(second_claim)

    async def test_retry_returns_running_job_to_pending(self) -> None:
        job = await self.repository.add(self.make_job())
        await self.repository.claim(job.job_id, NOW)

        updated = await self.repository.retry(
            job.job_id,
            NOW + timedelta(minutes=1),
            "temporary outage",
        )

        self.assertEqual(updated.status, JobStatus.PENDING)
        self.assertEqual(updated.last_error, "temporary outage")
        self.assertIsNone(updated.completed_at)

    async def test_complete_records_result_and_completion_time(self) -> None:
        job = await self.repository.add(self.make_job())
        await self.repository.claim(job.job_id, NOW)

        updated = await self.repository.complete(
            job.job_id,
            NOW,
            JobResult.SKIPPED_NOT_APPLICABLE,
        )

        self.assertEqual(updated.status, JobStatus.COMPLETED)
        self.assertEqual(updated.result, JobResult.SKIPPED_NOT_APPLICABLE)
        self.assertEqual(updated.completed_at, NOW)

    async def test_fail_records_error(self) -> None:
        job = await self.repository.add(self.make_job())
        await self.repository.claim(job.job_id, NOW)

        updated = await self.repository.fail(job.job_id, NOW, "permanent")

        self.assertEqual(updated.status, JobStatus.FAILED)
        self.assertEqual(updated.last_error, "permanent")

    async def test_cancel_marks_active_job_cancelled(self) -> None:
        job = await self.repository.add(self.make_job())

        updated = await self.repository.cancel(job.job_id, NOW)

        self.assertEqual(updated.status, JobStatus.CANCELLED)
        self.assertEqual(updated.result, JobResult.CANCELLED)

    async def test_cancel_is_idempotent_for_terminal_job(self) -> None:
        job = await self.repository.add(self.make_job())
        await self.repository.claim(job.job_id, NOW)
        completed = await self.repository.complete(
            job.job_id,
            NOW,
            JobResult.EXECUTED,
        )

        unchanged = await self.repository.cancel(job.job_id, NOW)

        self.assertEqual(unchanged, completed)

    async def test_unknown_job_id_raises_clear_error(self) -> None:
        with self.assertRaises(ScheduledJobNotFoundError):
            await self.repository.claim("missing", NOW)

    async def test_invalid_json_raises_domain_error(self) -> None:
        self.file_path.write_text("{broken", encoding="utf-8")

        with self.assertRaises(InvalidSchedulerDataError):
            await self.repository.list_all()

    async def test_unknown_schema_version_is_rejected(self) -> None:
        self.file_path.write_text(
            json.dumps({"schema_version": 99, "jobs": []}),
            encoding="utf-8",
        )

        with self.assertRaises(InvalidSchedulerDataError):
            await self.repository.list_all()

    async def test_get_due_rejects_nonpositive_limit(self) -> None:
        with self.assertRaises(ValueError):
            await self.repository.get_due(NOW, limit=0)


if __name__ == "__main__":
    unittest.main()
