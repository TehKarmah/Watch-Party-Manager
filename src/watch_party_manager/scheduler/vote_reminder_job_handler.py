"""Scheduler handler for the vote_reminder job introduced in FR-015.

FR-017 executes the pre-close reminder for an open voting round when its
vote_reminder job becomes due. It reuses VoteService.get_round() to
recheck the round's current state, and format_datetime_for_display() for
the "Voting ends" text, so the reminder matches every other Discord
message that already shows a round's deadline.

FR-027 (Configurable Vote Reminders) added the round's current standings
to the reminder content, and a persisted reminder_sent_at guard on
VoteRound so a reminder is posted at most once for a round's entire
lifetime, even if its scheduled job is somehow re-queued or replayed
after a WASH or scheduler restart -- see VoteService.mark_reminder_sent().

See the module docstring of vote_scheduling.py for the job this handler
executes (created by schedule_vote_jobs() in FR-015, with FR-027's
per-round reminder_enabled/reminder_hours_before_close overrides applied
via resolve_vote_reminder_settings()).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional, Protocol

from watch_party_manager.domain.vote import VoteRound, VoteRoundStatus
from watch_party_manager.domain.watch_item import WatchItem
from watch_party_manager.services.discord_timestamp_formatter import (
    format_datetime_for_display,
)
from watch_party_manager.services.vote_announcement_formatter import (
    build_vote_link,
    build_vote_reminder_standings_lines,
)
from watch_party_manager.services.vote_service import StandingsEntry, VoteService

from .job_handler import DiscordChannelMessenger, JobExecutionResult
from .scheduled_job import JobResult, ScheduledJob


class SuggestionLookup(Protocol):
    """The subset of SuggestionService needed to resolve a round's
    candidates for the "Current standings" section.

    Kept minimal and Protocol-based, matching the project's existing
    dependency pattern (see the same-named Protocol in
    vote_completion_announcer.py), so this handler depends only on the
    one capability it actually uses.
    """

    def get_suggestion(self, suggestion_id: int) -> Optional[WatchItem]: ...


def _resolve_candidates(suggestion_service: SuggestionLookup, vote_round: VoteRound) -> List[WatchItem]:
    """Resolve a round's persisted nominees, skipping any that no longer exist."""
    resolved: List[WatchItem] = []
    for suggestion_id in vote_round.candidate_suggestion_ids:
        watch_item = suggestion_service.get_suggestion(suggestion_id)
        if watch_item is not None:
            resolved.append(watch_item)
    return resolved


def build_vote_reminder_text(
    vote_round: VoteRound, candidates: List[WatchItem], standings: Optional[List[StandingsEntry]]
) -> str:
    """Build the reminder message posted to a voting round's channel.

    Args:
        vote_round: The still-open round the reminder is for.
        candidates: Every nominee in the round, in button order -- used
            to show titles in the "Current standings" section.
        standings: The current vote tally (VoteService.calculate_standings()),
            or None/empty if nobody has voted yet.

    Returns:
        The reminder text: time remaining and closing timestamp (both via
        format_datetime_for_display's native Discord timestamp), current
        standings (see build_vote_reminder_standings_lines -- withheld for
        a blind round, per the project's existing visibility rule), a
        call to action, and a link to the original voting post when available.
    """
    lines = [
        f"Reminder: Voting round {vote_round.id} is still open.",
        f"Voting ends: {format_datetime_for_display(vote_round.closes_at)}",
    ]
    lines.extend(build_vote_reminder_standings_lines(vote_round, candidates, standings))
    lines.append("")
    lines.append("Cast your vote with /vote before it closes!")
    link = build_vote_link(vote_round)
    if link:
        lines.append(f"Original post: {link}")
    return "\n".join(lines)


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
        suggestion_service: SuggestionLookup,
        messenger: DiscordChannelMessenger,
        *,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """Initialize the handler.

        Args:
            vote_service: The vote service to look up the round through,
                and to record that its reminder was sent (see
                VoteService.mark_reminder_sent()).
            suggestion_service: Used to resolve the round's candidates
                for the "Current standings" section.
            messenger: Used to resolve the round's channel and send the
                reminder. A real discord.Client/Bot satisfies this.
            logger: Optional logger override, mainly for tests.
        """
        self._vote_service = vote_service
        self._suggestion_service = suggestion_service
        self._messenger = messenger
        self._logger = logger or logging.getLogger(__name__)

    async def execute(self, job: ScheduledJob) -> JobExecutionResult:
        """Execute one claimed vote_reminder job.

        SchedulerService claims and completes each job exactly once under
        normal operation, so this never naturally re-runs for the same
        due job. It is still safe to call again for the same vote_id --
        e.g. if a job were manually re-queued, or replayed after a
        restart -- because current state is always rechecked first: a
        round that no longer exists, has since closed (whether by
        CloseVoteJobHandler, a WASH Crew member closing it manually, or
        an earlier run of this same job), or whose reminder has already
        been sent (see VoteRound.reminder_sent_at,
        VoteService.mark_reminder_sent()) is treated as a successful
        no-op rather than an error or a duplicate post -- FR-027's "at
        most once, even across restarts" guarantee.

        Args:
            job: The claimed vote_reminder job. job.payload["vote_id"]
                must be present.

        Returns:
            JobExecutionResult(EXECUTED) if the reminder was posted.
            JobExecutionResult(SKIPPED_NOT_APPLICABLE) if the round no
            longer exists, has already closed, its reminder was already
            sent, or it has no channel to post the reminder to.

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

        if vote_round.status != VoteRoundStatus.OPEN:
            self._logger.info(
                "vote_reminder job for vote %s skipped: round is %s, not open",
                vote_id,
                vote_round.status.value,
            )
            return JobExecutionResult(result=JobResult.SKIPPED_NOT_APPLICABLE)

        if vote_round.reminder_sent_at is not None:
            self._logger.info(
                "vote_reminder job for vote %s skipped: reminder already sent at %s",
                vote_id,
                vote_round.reminder_sent_at,
            )
            return JobExecutionResult(result=JobResult.SKIPPED_NOT_APPLICABLE)

        if vote_round.channel_id is None:
            self._logger.warning(
                "vote_reminder job for vote %s skipped: round has no channel reference",
                vote_id,
            )
            return JobExecutionResult(result=JobResult.SKIPPED_NOT_APPLICABLE)

        candidates = _resolve_candidates(self._suggestion_service, vote_round)
        standings_result = self._vote_service.calculate_standings(vote_id)
        standings = standings_result.standings if standings_result.success else None

        channel = self._messenger.get_channel(vote_round.channel_id)
        if channel is None:
            channel = await self._messenger.fetch_channel(vote_round.channel_id)
        await channel.send(build_vote_reminder_text(vote_round, candidates, standings))

        self._vote_service.mark_reminder_sent(vote_id, datetime.now(timezone.utc))

        self._logger.info("Posted vote reminder for round %s", vote_id)
        return JobExecutionResult(result=JobResult.EXECUTED)
