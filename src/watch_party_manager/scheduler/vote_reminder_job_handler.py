"""Scheduler handler for the vote_reminder job introduced in FR-015.

FR-017 executes the pre-close reminder for an open voting round when its
vote_reminder job becomes due. It reuses VoteService.get_round() to
recheck the round's current state, and format_datetime_for_display() for
the "Voting ends" text, so the reminder matches every other Discord
message that already shows a round's deadline.

See the module docstring of vote_scheduling.py for the job this handler
executes (created by schedule_vote_jobs() in FR-015).
"""

from __future__ import annotations

import logging
from typing import Optional, Protocol

from watch_party_manager.domain.vote import VoteRound, VoteRoundStatus
from watch_party_manager.services.discord_timestamp_formatter import (
    format_datetime_for_display,
)
from watch_party_manager.services.vote_service import VoteService

from .job_handler import JobExecutionResult
from .scheduled_job import JobResult, ScheduledJob


class DiscordChannelMessenger(Protocol):
    """The subset of a discord.Client this handler needs to post a reminder.

    Matches the same duck-typed contract bot.py's
    check_and_announce_expired_vote() already established for delivering a
    Discord message from a background job: get_channel()/fetch_channel()
    to resolve a channel by ID, and a .send(content) coroutine on the
    result. A real discord.Client/Bot satisfies this; tests can supply a
    lightweight fake.
    """

    def get_channel(self, channel_id: int) -> object: ...

    async def fetch_channel(self, channel_id: int) -> object: ...


def build_vote_reminder_text(vote_round: VoteRound) -> str:
    """Build the reminder message posted to a voting round's channel."""
    return "\n".join(
        (
            f"Reminder: Voting round {vote_round.id} is still open.",
            f"Voting ends: {format_datetime_for_display(vote_round.closes_at)}",
            "Cast your vote with /vote before it closes!",
        )
    )


class VoteReminderJobHandler:
    """Posts a pre-close voting reminder when its scheduled job is due.

    Registered under the "vote_reminder" job type (see
    watch_party_manager.scheduler.vote_scheduling.VOTE_REMINDER_JOB_TYPE)
    via SchedulerService.register_handler() -- the payload shape
    ({"vote_id": <int>}) established in FR-015 is unchanged.
    """

    def __init__(
        self,
        vote_service: VoteService,
        messenger: DiscordChannelMessenger,
        *,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """Initialize the handler.

        Args:
            vote_service: The vote service to look up the round through.
            messenger: Used to resolve the round's channel and send the
                reminder. A real discord.Client/Bot satisfies this.
            logger: Optional logger override, mainly for tests.
        """
        self._vote_service = vote_service
        self._messenger = messenger
        self._logger = logger or logging.getLogger(__name__)

    async def execute(self, job: ScheduledJob) -> JobExecutionResult:
        """Execute one claimed vote_reminder job.

        SchedulerService claims and completes each job exactly once under
        normal operation, so this never naturally re-runs for the same
        due job. It is still safe to call again for the same vote_id --
        e.g. if a job were manually re-queued -- because current state is
        always rechecked first: a round that no longer exists or has
        since closed (whether by CloseVoteJobHandler, a WASH Crew member
        closing it manually, or an earlier run of this same job) is
        treated as a successful no-op rather than an error, exactly per
        FR-017's requirements.

        Args:
            job: The claimed vote_reminder job. job.payload["vote_id"]
                must be present.

        Returns:
            JobExecutionResult(EXECUTED) if the reminder was posted.
            JobExecutionResult(SKIPPED_NOT_APPLICABLE) if the round no
            longer exists, has already closed, or has no channel to post
            the reminder to.

        Raises:
            KeyError: If the payload is missing "vote_id". Not retried --
                a malformed payload will never succeed no matter how many
                times it's retried.
        """
        vote_id = int(job.payload["vote_id"])

        vote_round = self._vote_service.get_round(vote_id)
        if vote_round is None:
            self._logger.info(
                "vote_reminder job for vote %s skipped: round no longer exists", vote_id
            )
            return JobExecutionResult(result=JobResult.SKIPPED_NOT_APPLICABLE)

        if vote_round.status == VoteRoundStatus.CLOSED:
            self._logger.info(
                "vote_reminder job for vote %s skipped: already closed", vote_id
            )
            return JobExecutionResult(result=JobResult.SKIPPED_NOT_APPLICABLE)

        if vote_round.channel_id is None:
            self._logger.warning(
                "vote_reminder job for vote %s skipped: round has no channel reference",
                vote_id,
            )
            return JobExecutionResult(result=JobResult.SKIPPED_NOT_APPLICABLE)

        channel = self._messenger.get_channel(vote_round.channel_id)
        if channel is None:
            channel = await self._messenger.fetch_channel(vote_round.channel_id)
        await channel.send(build_vote_reminder_text(vote_round))

        self._logger.info("Posted vote reminder for round %s", vote_id)
        return JobExecutionResult(result=JobResult.EXECUTED)
