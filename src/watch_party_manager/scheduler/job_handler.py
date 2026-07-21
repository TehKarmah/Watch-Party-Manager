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


class DiscordChannelMessenger(Protocol):
    """The subset of a discord.Client a job handler needs to post a message.

    get_channel()/fetch_channel() resolve a channel by ID, and the result
    exposes a .send(content) coroutine. A real discord.Client/Bot satisfies
    this; tests can supply a lightweight fake. Shared here so every job
    handler that needs to post a message (e.g. CloseVoteJobHandler,
    VoteReminderJobHandler) depends on one definition.
    """

    def get_channel(self, channel_id: int) -> object: ...

    async def fetch_channel(self, channel_id: int) -> object: ...
