"""Scheduler handler for the close_vote job introduced in FR-015.

FR-016 executed the backend Open -> Closed state transition for a voting
round when its close_vote job becomes due. FR-018 completes the job:
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
"""

from __future__ import annotations

import logging
from typing import List, Optional, Protocol

from watch_party_manager.domain.watch_item import WatchItem
from watch_party_manager.services.vote_announcement_formatter import (
    build_vote_completion_announcement,
)
from watch_party_manager.services.vote_completion_service import VoteCompletionService

from .job_handler import DiscordChannelMessenger, JobExecutionResult
from .scheduled_job import JobResult, ScheduledJob


class WinningSuggestionLookup(Protocol):
    """The subset of SuggestionService needed to resolve winner(s) for the
    announcement (title and, when available, IMDb link).

    Kept minimal and Protocol-based, matching the project's existing
    dependency pattern (see SuggestionLookup in vote_service.py and
    JourneyRecorder in vote_completion_service.py), so this handler
    depends only on the one capability it actually uses.
    """

    def get_suggestion(self, suggestion_id: int) -> Optional[WatchItem]: ...


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
        suggestion_service: WinningSuggestionLookup,
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
            suggestion_service: Used to resolve winning suggestion IDs to
                the WatchItem(s) shown in the announcement.
            messenger: Used to resolve the round's channel and send the
                announcement. A real discord.Client/Bot satisfies this.
            logger: Optional logger override, mainly for tests.
        """
        self._vote_completion_service = vote_completion_service
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

        winning_items: List[WatchItem] = []
        for suggestion_id in result.winning_suggestion_ids:
            watch_item = self._suggestion_service.get_suggestion(suggestion_id)
            if watch_item is not None:
                winning_items.append(watch_item)

        self._logger.info(
            "Closed vote %s; winning suggestion id(s): %s", vote_id, result.winning_suggestion_ids
        )

        if result.vote_round.channel_id is None:
            self._logger.warning(
                "Voting round %s completed but has no channel reference; announcement not sent",
                vote_id,
            )
            return JobExecutionResult(result=JobResult.EXECUTED)

        announcement = build_vote_completion_announcement(
            result.vote_round, winning_items, result.standings, result.total_votes_cast
        )
        channel = self._messenger.get_channel(result.vote_round.channel_id)
        if channel is None:
            channel = await self._messenger.fetch_channel(result.vote_round.channel_id)
        await channel.send(announcement)
        self._logger.info("Announced completion of voting round %s", vote_id)

        return JobExecutionResult(result=JobResult.EXECUTED)
