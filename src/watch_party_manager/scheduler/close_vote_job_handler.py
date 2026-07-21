"""Scheduler handler for the close_vote job introduced in FR-015.

FR-016 executes the backend Open -> Closed state transition for a voting
round when its close_vote job becomes due. Winner calculation (including
tie support) is never reimplemented here -- this handler only calls
VoteService.close_round() and VoteService.get_current_winners(), the
same methods every other part of the project already uses for exactly
this purpose.

Discord announcements and message edits are explicitly out of scope for
this milestone; see the module docstring of vote_scheduling.py for the
job this handler executes (created by schedule_vote_jobs() in FR-015).
"""

from __future__ import annotations

import logging
from typing import Optional

from watch_party_manager.domain.vote import VoteRoundStatus
from watch_party_manager.services.vote_service import VoteService

from .job_handler import JobExecutionResult
from .scheduled_job import JobResult, ScheduledJob


class CloseVoteJobHandler:
    """Closes a voting round and determines its winner(s) when due.

    Registered under the "close_vote" job type (see
    watch_party_manager.scheduler.vote_scheduling.CLOSE_VOTE_JOB_TYPE) via
    SchedulerService.register_handler() -- the payload shape
    ({"vote_id": <int>}) established in FR-015 is unchanged.
    """

    def __init__(self, vote_service: VoteService, *, logger: Optional[logging.Logger] = None) -> None:
        self._vote_service = vote_service
        self._logger = logger or logging.getLogger(__name__)

    async def execute(self, job: ScheduledJob) -> JobExecutionResult:
        """Execute one claimed close_vote job.

        Safe to run more than once for the same vote_id (idempotent):
        once a round is closed -- whether by this handler or manually via
        an admin command -- a later execution for the same vote_id is
        treated as a successful no-op rather than an error, per FR-016's
        requirements.

        Args:
            job: The claimed close_vote job. job.payload["vote_id"] must
                be present.

        Returns:
            JobExecutionResult(EXECUTED) if the round was closed by this
            call (with its winner(s) determined). JobExecutionResult
            (SKIPPED_NOT_APPLICABLE) if the round no longer exists, or
            was already closed before this job ran.

        Raises:
            KeyError: If the payload is missing "vote_id". Not retried --
                a malformed payload will never succeed no matter how many
                times it's retried.
            RuntimeError: If VoteService reports a failure that the
                existence/status checks below should already have
                prevented. This indicates a logic inconsistency rather
                than a transient condition, so it is also not retried.
        """
        vote_id = int(job.payload["vote_id"])

        vote_round = self._vote_service.get_round(vote_id)
        if vote_round is None:
            self._logger.info("close_vote job for vote %s skipped: round no longer exists", vote_id)
            return JobExecutionResult(result=JobResult.SKIPPED_NOT_APPLICABLE)

        if vote_round.status == VoteRoundStatus.CLOSED:
            self._logger.info("close_vote job for vote %s skipped: already closed", vote_id)
            return JobExecutionResult(result=JobResult.SKIPPED_NOT_APPLICABLE)

        close_result = self._vote_service.close_round(vote_id)
        if not close_result.success:
            raise RuntimeError(f"Failed to close vote {vote_id}: {close_result.message}")

        winner_result = self._vote_service.get_current_winners(vote_id)
        if not winner_result.success:
            raise RuntimeError(
                f"Failed to determine winner(s) for vote {vote_id}: {winner_result.message}"
            )

        self._logger.info(
            "Closed vote %s; winning suggestion id(s): %s",
            vote_id,
            winner_result.winning_suggestion_ids,
        )
        return JobExecutionResult(result=JobResult.EXECUTED)
