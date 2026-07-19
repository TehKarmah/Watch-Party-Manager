"""Application service for dispatching persistent scheduled jobs."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timezone

from .job_handler import JobHandler, RetryableJobError
from .retry_policy import RetryPolicy
from .scheduled_job import JobResult, ScheduledJob
from .scheduler_repository import SchedulerRepository

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SchedulerService:
    def __init__(
        self,
        repository: SchedulerRepository,
        *,
        retry_policy: RetryPolicy | None = None,
        poll_interval_seconds: int = 60,
        clock: Clock = utc_now,
        logger: logging.Logger | None = None,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")

        self._repository = repository
        self._retry_policy = retry_policy or RetryPolicy()
        self._poll_interval_seconds = poll_interval_seconds
        self._clock = clock
        self._logger = logger or logging.getLogger(__name__)
        self._handlers: dict[str, JobHandler] = {}

    @property
    def poll_interval_seconds(self) -> int:
        return self._poll_interval_seconds

    def register_handler(self, job_type: str, handler: JobHandler) -> None:
        normalized = job_type.strip()
        if not normalized:
            raise ValueError("job_type is required")
        if normalized in self._handlers:
            raise ValueError(f"handler already registered for {normalized}")
        self._handlers[normalized] = handler

    async def schedule(self, job: ScheduledJob) -> ScheduledJob:
        existing = await self._repository.find_active_by_logical_key(job.logical_key)
        if existing is not None:
            return existing
        return await self._repository.add(job)

    async def run_once(self, *, limit: int = 100) -> int:
        now = self._clock()
        due_jobs = await self._repository.get_due(now, limit=limit)
        processed = 0

        for due_job in due_jobs:
            claimed = await self._repository.claim(due_job.job_id, now)
            if claimed is None:
                continue

            processed += 1
            await self._execute_claimed(claimed)

        return processed

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self._poll_interval_seconds,
                )
            except TimeoutError:
                continue

    async def _execute_claimed(self, job: ScheduledJob) -> None:
        handler = self._handlers.get(job.job_type)
        if handler is None:
            await self._repository.fail(
                job.job_id,
                self._clock(),
                f"No handler registered for job type: {job.job_type}",
            )
            self._logger.error("No handler registered for %s", job.job_type)
            return

        try:
            execution = await handler.execute(job)
        except RetryableJobError as exc:
            await self._handle_retryable_failure(job, exc)
        except Exception as exc:
            await self._repository.fail(job.job_id, self._clock(), str(exc))
            self._logger.exception(
                "Scheduled job %s failed permanently",
                job.job_id,
            )
        else:
            await self._repository.complete(
                job.job_id,
                self._clock(),
                execution.result,
            )

    async def _handle_retryable_failure(
        self,
        job: ScheduledJob,
        error: Exception,
    ) -> None:
        delay = self._retry_policy.delay_after_failure(job.attempt_count)
        if delay is None:
            await self._repository.fail(job.job_id, self._clock(), str(error))
            self._logger.error(
                "Scheduled job %s exhausted retries: %s",
                job.job_id,
                error,
            )
            return

        await self._repository.retry(
            job.job_id,
            self._clock() + delay,
            str(error),
        )
        self._logger.warning(
            "Scheduled job %s will retry after failure: %s",
            job.job_id,
            error,
        )
