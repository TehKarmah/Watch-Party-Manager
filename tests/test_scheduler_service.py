from __future__ import annotations

from datetime import datetime, timezone
import unittest

from watch_party_manager.scheduler.job_handler import (
    JobExecutionResult,
    RetryableJobError,
)
from watch_party_manager.scheduler.scheduled_job import (
    JobResult,
    JobStatus,
    ScheduledJob,
)
from watch_party_manager.scheduler.scheduler_service import SchedulerService


NOW = datetime(2026, 7, 19, 12, tzinfo=timezone.utc)


class MemoryRepository:
    def __init__(self) -> None:
        self.jobs: dict[str, ScheduledJob] = {}

    async def add(self, job: ScheduledJob) -> ScheduledJob:
        self.jobs[job.job_id] = job
        return job

    async def get_due(self, now: datetime, *, limit: int = 100) -> list[ScheduledJob]:
        return [
            job
            for job in self.jobs.values()
            if job.status is JobStatus.PENDING and job.run_at <= now
        ][:limit]

    async def claim(self, job_id: str, started_at: datetime) -> ScheduledJob | None:
        job = self.jobs[job_id]
        if job.status is not JobStatus.PENDING:
            return None
        claimed = job.with_changes(
            status=JobStatus.RUNNING,
            started_at=started_at,
            attempt_count=job.attempt_count + 1,
        )
        self.jobs[job_id] = claimed
        return claimed

    async def complete(
        self,
        job_id: str,
        completed_at: datetime,
        result: JobResult,
    ) -> ScheduledJob:
        updated = self.jobs[job_id].with_changes(
            status=JobStatus.COMPLETED,
            completed_at=completed_at,
            result=result,
            last_error=None,
        )
        self.jobs[job_id] = updated
        return updated

    async def retry(self, job_id: str, run_at: datetime, error: str) -> ScheduledJob:
        updated = self.jobs[job_id].with_changes(
            status=JobStatus.PENDING,
            run_at=run_at,
            last_error=error,
        )
        self.jobs[job_id] = updated
        return updated

    async def fail(
        self,
        job_id: str,
        completed_at: datetime,
        error: str,
    ) -> ScheduledJob:
        updated = self.jobs[job_id].with_changes(
            status=JobStatus.FAILED,
            completed_at=completed_at,
            last_error=error,
        )
        self.jobs[job_id] = updated
        return updated

    async def cancel(self, job_id: str, completed_at: datetime) -> ScheduledJob:
        updated = self.jobs[job_id].with_changes(
            status=JobStatus.CANCELLED,
            completed_at=completed_at,
            result=JobResult.CANCELLED,
        )
        self.jobs[job_id] = updated
        return updated

    async def find_active_by_logical_key(
        self,
        logical_key: str,
    ) -> ScheduledJob | None:
        return next(
            (
                job
                for job in self.jobs.values()
                if job.logical_key == logical_key and job.is_active
            ),
            None,
        )


class SuccessfulHandler:
    async def execute(self, job: ScheduledJob) -> JobExecutionResult:
        return JobExecutionResult(JobResult.EXECUTED)


class SkippingHandler:
    async def execute(self, job: ScheduledJob) -> JobExecutionResult:
        return JobExecutionResult(JobResult.SKIPPED_NOT_APPLICABLE)


class RetryHandler:
    async def execute(self, job: ScheduledJob) -> JobExecutionResult:
        raise RetryableJobError("temporary outage")


class SchedulerServiceTests(unittest.IsolatedAsyncioTestCase):
    def make_job(self, *, logical_key: str = "close_vote:1") -> ScheduledJob:
        return ScheduledJob(
            guild_id=123,
            job_type="close_vote",
            logical_key=logical_key,
            run_at=NOW,
            created_at=NOW,
        )

    async def test_schedule_returns_existing_active_job_for_duplicate_key(self) -> None:
        repository = MemoryRepository()
        service = SchedulerService(repository, clock=lambda: NOW)
        first = await service.schedule(self.make_job())
        duplicate = await service.schedule(self.make_job())

        self.assertEqual(duplicate.job_id, first.job_id)
        self.assertEqual(len(repository.jobs), 1)

    async def test_run_once_executes_and_completes_due_job(self) -> None:
        repository = MemoryRepository()
        service = SchedulerService(repository, clock=lambda: NOW)
        service.register_handler("close_vote", SuccessfulHandler())
        job = await service.schedule(self.make_job())

        processed = await service.run_once()

        self.assertEqual(processed, 1)
        self.assertEqual(repository.jobs[job.job_id].status, JobStatus.COMPLETED)
        self.assertEqual(repository.jobs[job.job_id].result, JobResult.EXECUTED)

    async def test_handler_can_intentionally_skip_job(self) -> None:
        repository = MemoryRepository()
        service = SchedulerService(repository, clock=lambda: NOW)
        service.register_handler("close_vote", SkippingHandler())
        job = await service.schedule(self.make_job())

        await service.run_once()

        self.assertEqual(
            repository.jobs[job.job_id].result,
            JobResult.SKIPPED_NOT_APPLICABLE,
        )

    async def test_retryable_failure_returns_job_to_pending(self) -> None:
        repository = MemoryRepository()
        service = SchedulerService(repository, clock=lambda: NOW)
        service.register_handler("close_vote", RetryHandler())
        job = await service.schedule(self.make_job())

        await service.run_once()

        updated = repository.jobs[job.job_id]
        self.assertEqual(updated.status, JobStatus.PENDING)
        self.assertEqual(updated.run_at.minute, 1)
        self.assertEqual(updated.last_error, "temporary outage")

    async def test_missing_handler_fails_job(self) -> None:
        repository = MemoryRepository()
        service = SchedulerService(repository, clock=lambda: NOW)
        job = await service.schedule(self.make_job())

        await service.run_once()

        self.assertEqual(repository.jobs[job.job_id].status, JobStatus.FAILED)
        self.assertIn("No handler registered", repository.jobs[job.job_id].last_error)

    def test_duplicate_handler_registration_is_rejected(self) -> None:
        repository = MemoryRepository()
        service = SchedulerService(repository, clock=lambda: NOW)
        service.register_handler("close_vote", SuccessfulHandler())

        with self.assertRaises(ValueError):
            service.register_handler("close_vote", SuccessfulHandler())


if __name__ == "__main__":
    unittest.main()
