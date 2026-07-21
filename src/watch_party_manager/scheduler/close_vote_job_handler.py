"""Scheduler handler for the close_vote job introduced in FR-015.

FR-016 executed the backend Open -> Closed state transition for a voting
round when its close_vote job becomes due. FR-018 completed the job:
determining the winner(s), updating the Watch Item Journey, and posting
the completion announcement are handled here too now, by delegating to
VoteCompletionService.complete_round() rather than duplicating
VoteService.close_round()/get_current_winners() directly -- see that
method's docstring for why this is the single place a scheduler-driven
close is fully finalized, instead of splitting "close + determine winner"
and "record history + announce" across two divergent implementations.

FR-019 retired the older polling-based mechanism (a bot.py background
task that independently closed and announced expired rounds), making
this handler the sole automatic path for completing a voting round.

FR-026 replaced this handler's own announcement logic with a delegated
call to vote_completion_announcer.finalize_vote_completion() -- the same
function /edit_vote's "End Now" action (bot.py) calls -- so automatic and
manual completion always produce an identical presentation.
"""

from __future__ import annotations

import logging
from typing import Optional

from watch_party_manager.services.vote_completion_announcer import (
    DiscordChannelMessenger,
    ResultsMessageRecorder,
    SuggestionLookup,
    finalize_vote_completion,
)
from watch_party_manager.services.vote_completion_service import VoteCompletionService

from .job_handler import JobExecutionResult
from .scheduled_job import JobResult, ScheduledJob


class CloseVoteJobHandler:
    """Closes a voting round, determines its winner(s), and announces it.

    Registered under the "close_vote" job type (see
    watch_party_manager.scheduler.vote_scheduling.CLOSE_VOTE_JOB_TYPE) via
    SchedulerService.register_handler() -- the payload shape
    ({"vote_id": <int>}) established in FR-015 is unchanged.
    """

    def __init__(
        self,
        vote_completion_service: VoteCompletionService,
        vote_service: ResultsMessageRecorder,
        suggestion_service: SuggestionLookup,
        messenger: DiscordChannelMessenger,
        *,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """Initialize the handler.

        Args:
            vote_completion_service: Closes the round and determines its
                winner(s), Watch Item Journey updates, and standings --
                see VoteCompletionService.complete_round(). The single
                source of that logic; never duplicated here.
            vote_service: Used to persist the results announcement's
                message reference once it's sent -- see
                vote_completion_announcer.finalize_vote_completion().
            suggestion_service: Used to resolve winning suggestion IDs to
                the WatchItem(s) shown in the announcement.
            messenger: Used to resolve the round's channel and send/edit
                messages. A real discord.Client/Bot satisfies this.
            logger: Optional logger override, mainly for tests.
        """
        self._vote_completion_service = vote_completion_service
        self._vote_service = vote_service
        self._suggestion_service = suggestion_service
        self._messenger = messenger
        self._logger = logger or logging.getLogger(__name__)

    async def execute(self, job: ScheduledJob) -> JobExecutionResult:
        """Execute one claimed close_vote job.

        Safe to run more than once for the same vote_id (idempotent):
        once a round is closed -- whether by this handler or manually via
        an admin command -- VoteCompletionService.complete_round() returns
        None for it, so a later execution for the same vote_id is a
        successful no-op rather than an error or a repeated announcement.

        Args:
            job: The claimed close_vote job. job.payload["vote_id"] must
                be present.

        Returns:
            JobExecutionResult(EXECUTED) if the round was closed by this
            call. This still applies even if the round has no channel
            reference to announce to -- the close itself succeeded, only
            the announcement was skipped (logged as a warning).
            JobExecutionResult(SKIPPED_NOT_APPLICABLE) if the round no
            longer exists or was already closed before this call.

        Raises:
            KeyError: If the payload is missing "vote_id". Not retried --
                a malformed payload will never succeed no matter how many
                times it's retried.
        """
        vote_id = int(job.payload["vote_id"])

        result = self._vote_completion_service.complete_round(vote_id)
        if result is None:
            self._logger.info(
                "close_vote job for vote %s skipped: round no longer exists or already closed",
                vote_id,
            )
            return JobExecutionResult(result=JobResult.SKIPPED_NOT_APPLICABLE)

        self._logger.info(
            "Closed vote %s; winning suggestion id(s): %s", vote_id, result.winning_suggestion_ids
        )

        await finalize_vote_completion(self._vote_service, self._suggestion_service, self._messenger, result)

        return JobExecutionResult(result=JobResult.EXECUTED)
