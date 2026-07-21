"""Scheduler handler for the watch_party_reminder job introduced in FR-020.

Executes the pre-watch-party reminder scheduled by
schedule_watch_party_reminder() (see watch_party_scheduling.py). Mirrors
VoteReminderJobHandler's shape closely -- reusing DiscordChannelMessenger
for delivery and format_datetime_for_display for the reminder's watch-time
text -- rather than duplicating that already-established reminder
infrastructure for a second feature.
"""

from __future__ import annotations

import logging
from typing import Optional, Protocol

from watch_party_manager.domain.watch_item import MetadataProvider, WatchItem
from watch_party_manager.domain.watch_party import WatchParty, WatchPartyStatus
from watch_party_manager.services.discord_timestamp_formatter import (
    format_datetime_for_display,
)
from watch_party_manager.services.watch_party_service import WatchPartyService

from .job_handler import DiscordChannelMessenger, JobExecutionResult
from .scheduled_job import JobResult, ScheduledJob


class WatchItemLookup(Protocol):
    """The subset of SuggestionService needed to resolve the reminder's
    movie title and IMDb link.

    Kept minimal and Protocol-based, matching the project's existing
    dependency pattern (see WinningSuggestionLookup in
    close_vote_job_handler.py), so this handler depends only on the one
    capability it actually uses.
    """

    def get_suggestion(self, suggestion_id: int) -> Optional[WatchItem]: ...


def build_watch_party_reminder_text(
    watch_party: WatchParty, watch_item: Optional[WatchItem]
) -> str:
    """Build the reminder message posted to a watch party's channel.

    Args:
        watch_party: The watch party the reminder is for.
        watch_item: The Watch Item being watched, if it could still be
            resolved. None if it was removed after the party was
            scheduled -- the message still identifies the watch party by
            its own ID rather than failing to send at all.
    """
    title = watch_item.title if watch_item is not None else f"Watch party #{watch_party.id}"
    lines = [f'Reminder: "{title}" starts {format_datetime_for_display(watch_party.scheduled_at)}!']

    if watch_item is not None:
        imdb_url = watch_item.metadata_ids.get(MetadataProvider.IMDB)
        if imdb_url:
            lines.append(f"[View on IMDb]({imdb_url})")

    return "\n".join(lines)


class WatchPartyReminderJobHandler:
    """Posts a pre-watch-party reminder when its scheduled job is due.

    Registered under the "watch_party_reminder" job type (see
    watch_party_manager.scheduler.watch_party_scheduling.
    WATCH_PARTY_REMINDER_JOB_TYPE) via SchedulerService.register_handler()
    -- the payload shape ({"watch_party_id": <int>}) established by
    build_watch_party_reminder_job() is what this reads.
    """

    def __init__(
        self,
        watch_party_service: WatchPartyService,
        watch_item_lookup: WatchItemLookup,
        messenger: DiscordChannelMessenger,
        *,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """Initialize the handler.

        Args:
            watch_party_service: Used to look up the watch party's
                current state.
            watch_item_lookup: Used to resolve the watch party's Watch
                Item for the reminder's title and IMDb link.
            messenger: Used to resolve the watch party's channel and send
                the reminder. A real discord.Client/Bot satisfies this.
            logger: Optional logger override, mainly for tests.
        """
        self._watch_party_service = watch_party_service
        self._watch_item_lookup = watch_item_lookup
        self._messenger = messenger
        self._logger = logger or logging.getLogger(__name__)

    async def execute(self, job: ScheduledJob) -> JobExecutionResult:
        """Execute one claimed watch_party_reminder job.

        Rechecks the watch party's current state before doing anything
        Discord-related, per the scheduler's execution-time validation
        principle (docs/architecture/scheduler.md): a watch party that no
        longer exists, was cancelled, or has no channel reference to post
        to is treated as a successful no-op rather than an error --
        satisfying FR-020's "fail gracefully without affecting the
        scheduler" requirement for missing Discord resources.

        Args:
            job: The claimed watch_party_reminder job.
                job.payload["watch_party_id"] must be present.

        Returns:
            JobExecutionResult(EXECUTED) if the reminder was posted.
            JobExecutionResult(SKIPPED_NOT_APPLICABLE) if the watch party
            no longer exists, was cancelled, or has no channel to post to.

        Raises:
            KeyError: If the payload is missing "watch_party_id". Not
                retried -- a malformed payload will never succeed no
                matter how many times it's retried.
        """
        watch_party_id = int(job.payload["watch_party_id"])

        watch_party = self._watch_party_service.get_watch_party(watch_party_id)
        if watch_party is None:
            self._logger.info(
                "watch_party_reminder job for watch party %s skipped: watch party no longer exists",
                watch_party_id,
            )
            return JobExecutionResult(result=JobResult.SKIPPED_NOT_APPLICABLE)

        if watch_party.status == WatchPartyStatus.CANCELLED:
            self._logger.info(
                "watch_party_reminder job for watch party %s skipped: watch party was cancelled",
                watch_party_id,
            )
            return JobExecutionResult(result=JobResult.SKIPPED_NOT_APPLICABLE)

        if watch_party.channel_id is None:
            self._logger.warning(
                "watch_party_reminder job for watch party %s skipped: watch party has no channel reference",
                watch_party_id,
            )
            return JobExecutionResult(result=JobResult.SKIPPED_NOT_APPLICABLE)

        watch_item = self._watch_item_lookup.get_suggestion(watch_party.watch_item_id)
        reminder_text = build_watch_party_reminder_text(watch_party, watch_item)

        channel = self._messenger.get_channel(watch_party.channel_id)
        if channel is None:
            channel = await self._messenger.fetch_channel(watch_party.channel_id)
        await channel.send(reminder_text)

        self._logger.info("Posted watch party reminder for watch party %s", watch_party_id)
        return JobExecutionResult(result=JobResult.EXECUTED)
