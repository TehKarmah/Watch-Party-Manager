"""Builds and schedules the reminder job associated with a watch party.

Mirrors vote_scheduling.py's role for voting reminders: this is the one
place that knows the watch_party_reminder job's shape and how to keep it
in sync with a watch party's current scheduled_at, reusing
SchedulerService directly rather than duplicating any of its scheduling,
deduplication, or cancellation logic (see FR-020's constraint against
duplicating the reminder infrastructure voting already established).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Optional

from watch_party_manager.domain.guild_configuration import WatchNotificationsConfig
from watch_party_manager.domain.watch_party import WatchParty
from watch_party_manager.persistence.guild_configuration_repository import (
    GuildConfigurationRepository,
)
from watch_party_manager.scheduler.scheduled_job import ScheduledJob
from watch_party_manager.scheduler.scheduler_service import SchedulerService

WATCH_PARTY_REMINDER_JOB_TYPE = "watch_party_reminder"
# Watch Party Lifecycle: fires once a watch party's expected end time
# (scheduled_at + duration_minutes, see WatchParty.ends_at) passes,
# automatically transitioning it to COMPLETED and its Watch Item to
# Watched -- see WatchPartyCompletionJobHandler.
WATCH_PARTY_COMPLETION_JOB_TYPE = "watch_party_completion"


def watch_party_reminder_logical_key(watch_party_id: int) -> str:
    """Build the logical key that makes a watch party's reminder job idempotent."""
    return f"watch_party:{watch_party_id}:reminder"


def watch_party_completion_logical_key(watch_party_id: int) -> str:
    """Build the logical key that makes a watch party's completion job idempotent."""
    return f"watch_party:{watch_party_id}:completion"


def build_watch_party_reminder_job(
    watch_party: WatchParty,
    guild_id: int,
    *,
    reminder_hours_before_watch: int,
) -> ScheduledJob:
    """Build the pre-watch-party reminder job for a scheduled watch party.

    Unlike build_vote_reminder_job, there's no "disabled" or "nothing to
    schedule" case handled here -- schedule_watch_party_reminder() (the
    caller) already checks reminder_enabled before ever calling this, and
    scheduled_at is a required WatchParty field, so a job can always be
    built once this is reached.

    Args:
        watch_party: The just-created (or just-rescheduled) watch party.
        guild_id: The Discord guild this watch party belongs to.
        reminder_hours_before_watch: How many hours before the watch
            party starts the reminder should fire.

    Returns:
        The watch_party_reminder job. As with build_vote_reminder_job, a
        short lead time on a near-term watch party can produce a run_at
        in the past -- SchedulerService's due-job polling already treats
        that as simply immediately due, so no special case is needed here.
    """
    run_at = watch_party.scheduled_at - timedelta(hours=reminder_hours_before_watch)

    return ScheduledJob(
        guild_id=guild_id,
        job_type=WATCH_PARTY_REMINDER_JOB_TYPE,
        logical_key=watch_party_reminder_logical_key(watch_party.id),
        run_at=run_at,
        payload={"watch_party_id": watch_party.id},
    )


def resolve_watch_party_reminder_settings(
    guild_configuration_repository: Optional[GuildConfigurationRepository],
    guild_id: int,
) -> tuple[bool, int]:
    """Look up (reminder_enabled, reminder_hours_before_watch) for a guild.

    Mirrors resolve_vote_reminder_settings exactly (see vote_scheduling.py),
    reading notifications.watch instead of notifications.vote. Falls back
    to WatchNotificationsConfig's own documented defaults (enabled, 1 hour
    before) when no guild_configuration_repository was supplied, or none
    exists for this guild yet.

    Args:
        guild_configuration_repository: Where to look up the guild's
            configuration, or None to always use the defaults.
        guild_id: The Discord guild to look up.

    Returns:
        (reminder_enabled, reminder_hours_before_watch).
    """
    configuration = (
        guild_configuration_repository.get(guild_id)
        if guild_configuration_repository is not None
        else None
    )
    if configuration is None:
        defaults = WatchNotificationsConfig()
        return defaults.enabled, defaults.reminder_hours_before_watch

    watch_notifications = configuration.notifications.watch
    return watch_notifications.enabled, watch_notifications.reminder_hours_before_watch


async def schedule_watch_party_reminder(
    scheduler_service: Optional[SchedulerService],
    watch_party: WatchParty,
    guild_id: int,
    *,
    guild_configuration_repository: Optional[GuildConfigurationRepository] = None,
) -> Optional[ScheduledJob]:
    """Schedule the reminder job for a newly created watch party.

    Must only be called after the watch party has been successfully
    scheduled and persisted -- callers should call this immediately after
    WatchPartyService.schedule_watch_party() reports success, mirroring
    schedule_vote_jobs()'s existing contract for votes.

    Idempotency is entirely SchedulerService.schedule()'s existing
    responsibility (it checks for an existing active job under the same
    logical_key before creating another): this function does not
    duplicate that check, and calling it twice for the same watch party
    is always safe.

    Args:
        scheduler_service: The scheduler to schedule the job through. If
            None, scheduling is skipped entirely (a no-op) -- this keeps
            callers that don't yet have a scheduler to pass in working
            unchanged.
        watch_party: The just-created, just-persisted watch party.
        guild_id: The Discord guild this watch party belongs to.
        guild_configuration_repository: Used to resolve reminder timing
            for this guild; see resolve_watch_party_reminder_settings.

    Returns:
        The scheduled job (which may be a pre-existing job if one was
        already active under the same logical key), or None if
        scheduler_service was None or reminders are disabled for this guild.
    """
    if scheduler_service is None:
        return None

    reminder_enabled, reminder_hours_before_watch = resolve_watch_party_reminder_settings(
        guild_configuration_repository, guild_id
    )
    if not reminder_enabled:
        return None

    job = build_watch_party_reminder_job(
        watch_party, guild_id, reminder_hours_before_watch=reminder_hours_before_watch
    )
    return await scheduler_service.schedule(job)


async def reschedule_watch_party_reminder(
    scheduler_service: Optional[SchedulerService],
    watch_party: WatchParty,
    guild_id: int,
    *,
    guild_configuration_repository: Optional[GuildConfigurationRepository] = None,
) -> Optional[ScheduledJob]:
    """Replace a watch party's reminder job after it has been rescheduled.

    Cancels whatever reminder job is currently active under this watch
    party's logical key (a no-op if none is active -- e.g. reminders are
    disabled, or the original one already fired) and schedules a fresh
    one against the watch party's current scheduled_at. This is the
    documented "cancel the obsolete pending job and create the
    replacement" rescheduling policy (see docs/architecture/scheduler.md,
    "Cancellation & Rescheduling"), applied here rather than reimplemented.

    Args:
        scheduler_service: The scheduler to cancel/schedule through. If
            None, this is a no-op.
        watch_party: The watch party, already updated to its new
            scheduled_at (e.g. via WatchPartyService.reschedule_watch_party()).
        guild_id: The Discord guild this watch party belongs to.
        guild_configuration_repository: Used to resolve reminder timing
            for this guild; see resolve_watch_party_reminder_settings.

    Returns:
        The newly scheduled job, or None if scheduler_service was None or
        reminders are disabled for this guild.
    """
    if scheduler_service is None:
        return None

    await scheduler_service.cancel_by_logical_key(watch_party_reminder_logical_key(watch_party.id))
    return await schedule_watch_party_reminder(
        scheduler_service,
        watch_party,
        guild_id,
        guild_configuration_repository=guild_configuration_repository,
    )


async def cancel_watch_party_reminder(
    scheduler_service: Optional[SchedulerService],
    watch_party_id: int,
) -> Optional[ScheduledJob]:
    """Remove a watch party's scheduled reminder job.

    Used when a watch party is cancelled or deleted. Safe to call
    unconditionally: a no-op (returns None) if scheduler_service is None
    or no reminder job is currently active for this watch party -- e.g.
    reminders were disabled at schedule time, or it already fired.

    Args:
        scheduler_service: The scheduler to cancel the job through. If
            None, this is a no-op.
        watch_party_id: The watch party whose reminder should be removed.

    Returns:
        The cancelled job, or None if there was nothing to cancel.
    """
    if scheduler_service is None:
        return None
    return await scheduler_service.cancel_by_logical_key(
        watch_party_reminder_logical_key(watch_party_id)
    )


def build_watch_party_completion_job(watch_party: WatchParty, guild_id: int) -> Optional[ScheduledJob]:
    """Build the job that will automatically complete this watch party
    once its expected end time passes (Watch Party Lifecycle).

    Returns None if the watch party has no known duration_minutes (see
    WatchParty.ends_at) -- there's no end time to schedule against, so
    automatic completion simply never happens for it (e.g. a watch party
    scheduled via /watch-party schedule, which doesn't collect a
    duration). This is a deliberate no-op, not an error.

    Args:
        watch_party: The just-created (or just-rescheduled) watch party.
        guild_id: The Discord guild this watch party belongs to.

    Returns:
        The watch_party_completion job, or None if no duration is known.
    """
    ends_at = watch_party.ends_at
    if ends_at is None:
        return None

    return ScheduledJob(
        guild_id=guild_id,
        job_type=WATCH_PARTY_COMPLETION_JOB_TYPE,
        logical_key=watch_party_completion_logical_key(watch_party.id),
        run_at=ends_at,
        payload={"watch_party_id": watch_party.id},
    )


async def schedule_watch_party_completion(
    scheduler_service: Optional[SchedulerService],
    watch_party: WatchParty,
    guild_id: int,
) -> Optional[ScheduledJob]:
    """Schedule the automatic-completion job for a newly created watch party.

    Must only be called after the watch party has been successfully
    scheduled and persisted, mirroring schedule_watch_party_reminder's
    same contract. Idempotency is entirely SchedulerService.schedule()'s
    existing responsibility, exactly as with the reminder job.

    Args:
        scheduler_service: The scheduler to schedule the job through. If
            None, scheduling is skipped entirely (a no-op).
        watch_party: The just-created, just-persisted watch party.
        guild_id: The Discord guild this watch party belongs to.

    Returns:
        The scheduled job, or None if scheduler_service was None or the
        watch party has no known duration (see build_watch_party_completion_job).
    """
    if scheduler_service is None:
        return None

    job = build_watch_party_completion_job(watch_party, guild_id)
    if job is None:
        return None
    return await scheduler_service.schedule(job)


async def reschedule_watch_party_completion(
    scheduler_service: Optional[SchedulerService],
    watch_party: WatchParty,
    guild_id: int,
) -> Optional[ScheduledJob]:
    """Replace a watch party's completion job after it has been rescheduled.

    Cancels whatever completion job is currently active under this watch
    party's logical key (a no-op if none is active -- e.g. no duration
    was known, or it already fired) and schedules a fresh one against the
    watch party's current ends_at. Mirrors
    reschedule_watch_party_reminder's identical "cancel the obsolete
    pending job and create the replacement" policy.

    Args:
        scheduler_service: The scheduler to cancel/schedule through. If
            None, this is a no-op.
        watch_party: The watch party, already updated to its new
            scheduled_at (e.g. via WatchPartyService.reschedule_watch_party()).
        guild_id: The Discord guild this watch party belongs to.

    Returns:
        The newly scheduled job, or None if scheduler_service was None or
        the watch party has no known duration.
    """
    if scheduler_service is None:
        return None

    await scheduler_service.cancel_by_logical_key(watch_party_completion_logical_key(watch_party.id))
    return await schedule_watch_party_completion(scheduler_service, watch_party, guild_id)


async def cancel_watch_party_completion(
    scheduler_service: Optional[SchedulerService],
    watch_party_id: int,
) -> Optional[ScheduledJob]:
    """Remove a watch party's scheduled completion job.

    Used when a watch party is cancelled. Safe to call unconditionally: a
    no-op (returns None) if scheduler_service is None or no completion
    job is currently active for this watch party.

    Args:
        scheduler_service: The scheduler to cancel the job through. If
            None, this is a no-op.
        watch_party_id: The watch party whose completion job should be removed.

    Returns:
        The cancelled job, or None if there was nothing to cancel.
    """
    if scheduler_service is None:
        return None
    return await scheduler_service.cancel_by_logical_key(
        watch_party_completion_logical_key(watch_party_id)
    )
