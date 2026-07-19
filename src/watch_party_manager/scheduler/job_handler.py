"""Interfaces and results used by scheduler job handlers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .scheduled_job import JobResult, ScheduledJob


class RetryableJobError(RuntimeError):
    """Signals a temporary failure that should use the retry policy."""


@dataclass(frozen=True, slots=True)
class JobExecutionResult:
    result: JobResult = JobResult.EXECUTED


class JobHandler(Protocol):
    async def execute(self, job: ScheduledJob) -> JobExecutionResult:
        """Execute one claimed job after rechecking current feature state."""
        ...
