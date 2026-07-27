"""Scheduler handler for the watch_party_completion job (Watch Party Lifecycle).

Executes the automatic-completion job scheduled by
schedule_watch_party_completion() (see watch_party_scheduling.py) once a
watch party's expected end time passes. Mirrors CloseVoteJobHandler's
shape closely: delegates the actual completion logic to a dedicated
completion service (WatchPartyCompletionService) rather than duplicating
it here, and exposes an on_finalized hook so bot.py can keep the
suggestion's public confirmation post and any announcement in sync
without this scheduler package importing bot.py.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional

from watch_party_manager.services.watch_party_completion_service import (
    WatchPartyCompletionResult,
    WatchPartyCompletionService,
)

from .job_handler import JobExecutionResult
from .scheduled_job import JobResult, ScheduledJob

OnWatchPartyCompletionFinalized = Callable[[WatchPartyCompletionResult], Awaitable[None]]


class WatchPartyCompletionJobHandler:
    """Automatically completes a watch party once its end time is due.

    Registered under the "watch_party_completion" job type (see
    watch_party_manager.scheduler.watch_party_scheduling.
    WATCH_PARTY_COMPLETION_JOB_TYPE) via SchedulerService.register_handler()
    -- the payload shape ({"watch_party_id": <int>}) established by
    build_watch_party_completion_job() is what this reads.
    """

    def __init__(
        self,
        watch_party_completion_service: WatchPartyCompletionService,
        *,
        logger: Optional[logging.Logger] = None,
        on_finalized: Optional[OnWatchPartyCompletionFinalized] = None,
    ) -> None:
        """Initialize the handler.

        Args:
            watch_party_completion_service: Completes the watch party and
                marks its Watch Item Watched -- see that service's
                complete_watch_party(). The single source of that logic;
                never duplicated here.
            logger: Optional logger override, mainly for tests.
            on_finalized: Optional hook called with the completion result
                after the watch party has been completed. Defaults to
                None (no-op) so existing callers/tests keep working
                unchanged. bot.py uses this to sync the suggestion's
                public confirmation-post Status field and post a
                completion announcement -- kept as an injected callback
                rather than a direct call, since this scheduler package
                must not import from bot.py.
        """
        self._watch_party_completion_service = watch_party_completion_service
        self._logger = logger or logging.getLogger(__name__)
        self._on_finalized = on_finalized

    async def execute(self, job: ScheduledJob) -> JobExecutionResult:
        """Execute one claimed watch_party_completion job.

        No confirmation dialog, no "did this really happen" check --
        goal 6 is explicit: assume the watch party occurred. Correcting a
        wrong automatic completion afterward is a separate, explicit
        WASH Crew workflow (see bot.py), never something this handler
        second-guesses.

        Safe to run more than once for the same watch_party_id
        (idempotent): once a watch party is completed -- whether by this
        handler or a direct correction/reschedule -- a later execution
        for the same watch_party_id is a successful no-op rather than an
        error, because WatchPartyCompletionService.complete_watch_party()
        returns None for it.

        Args:
            job: The claimed watch_party_completion job.
                job.payload["watch_party_id"] must be present.

        Returns:
            JobExecutionResult(EXECUTED) if the watch party was completed
            by this call.
            JobExecutionResult(SKIPPED_NOT_APPLICABLE) if the watch party
            no longer exists or wasn't currently scheduled (already
            completed, or cancelled).

        Raises:
            KeyError: If the payload is missing "watch_party_id". Not
                retried -- a malformed payload will never succeed no
                matter how many times it's retried.
        """
        watch_party_id = int(job.payload["watch_party_id"])

        result = self._watch_party_completion_service.complete_watch_party(watch_party_id)
        if result is None:
            self._logger.info(
                "watch_party_completion job for watch party %s skipped: watch party no longer exists "
                "or was not currently scheduled",
                watch_party_id,
            )
            return JobExecutionResult(result=JobResult.SKIPPED_NOT_APPLICABLE)

        self._logger.info(
            "Completed watch party %s; watch item %s marked Watched",
            watch_party_id,
            result.watch_party.watch_item_id,
        )

        if self._on_finalized is not None:
            await self._on_finalized(result)

        return JobExecutionResult(result=JobResult.EXECUTED)
