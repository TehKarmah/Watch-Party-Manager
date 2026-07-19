"""Persistence contract required by the scheduler service."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .scheduled_job import JobResult, ScheduledJob


class SchedulerRepository(Protocol):
    async def add(self, job: ScheduledJob) -> ScheduledJob:
        """Persist a job, rejecting duplicate active logical keys."""
        ...

    async def get_due(self, now: datetime, *, limit: int = 100) -> list[ScheduledJob]:
        """Return pending jobs whose run_at is now or earlier."""
        ...

    async def claim(self, job_id: str, started_at: datetime) -> ScheduledJob | None:
        """Atomically move a pending job to running.

        Return None when the job is no longer claimable.
        """
        ...

    async def complete(
        self,
        job_id: str,
        completed_at: datetime,
        result: JobResult,
    ) -> ScheduledJob:
        ...

    async def retry(
        self,
        job_id: str,
        run_at: datetime,
        error: str,
    ) -> ScheduledJob:
        ...

    async def fail(
        self,
        job_id: str,
        completed_at: datetime,
        error: str,
    ) -> ScheduledJob:
        ...

    async def cancel(
        self,
        job_id: str,
        completed_at: datetime,
    ) -> ScheduledJob:
        ...

    async def find_active_by_logical_key(
        self,
        logical_key: str,
    ) -> ScheduledJob | None:
        ...
