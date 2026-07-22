"""Builds and schedules the future jobs associated with a voting round.

FR-015 (Automatic Vote Scheduling) is scheduling-only: this module builds
ScheduledJob records and hands them to the existing SchedulerService, but
implements no job handlers, sends no reminders, and never closes a vote
itself. Executing these jobs is a separate, future milestone.

This is intentionally the one place that knows which jobs a vote needs
and how their logical keys/payloads are shaped. Adding a future job type
(non-voter reminders, watch reminders, etc.) means adding another
build_*_job() function here and appending its result inside
build_vote_scheduled_jobs() -- schedule_vote_jobs() and its caller need
no changes to support it.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Optional

from watch_party_manager.domain.guild_configuration import VoteNotificationsConfig
from watch_party_manager.domain.vote import VoteRound
from watch_party_manager.persistence.guild_configuration_repository import (
    GuildConfigurationRepository,
)
from watch_party_manager.scheduler.scheduled_job import ScheduledJob
from watch_party_manager.scheduler.scheduler_service import SchedulerService

CLOSE_VOTE_JOB_TYPE = "close_vote"
VOTE_REMINDER_JOB_TYPE = "vote_reminder"


def close_vote_logical_key(vote_id: int) -> str:
    """Build the logical key that makes a vote's close job idempotent."""
    return f"vote:{vote_id}:close"


def vote_reminder_logical_key(vote_id: int) -> str:
    """Build the logical key that makes a vote's reminder job idempotent."""
    return f"vote:{vote_id}:reminder"


def build_close_vote_job(vote_round: VoteRound, guild_id: int) -> Optional[ScheduledJob]:
    """Build the job that will close this vote once its deadline passes.

    The payload only carries what a future handler needs to locate the
    vote (vote_id) -- guild_id is already available as the job's own
    top-level field, so it isn't duplicated into the payload too.

    Args:
        vote_round: The just-created, just-persisted voting round.
        guild_id: The Discord guild this round belongs to.

    Returns:
        The close_vote job, or None if the round has no closes_at (there
        is nothing to schedule a close for).
    """
    if vote_round.closes_at is None:
        return None

    return ScheduledJob(
        guild_id=guild_id,
        job_type=CLOSE_VOTE_JOB_TYPE,
        logical_key=close_vote_logical_key(vote_round.id),
        run_at=vote_round.closes_at,
        payload={"vote_id": vote_round.id},
    )


def build_vote_reminder_job(
    vote_round: VoteRound,
    guild_id: int,
    *,
    reminder_enabled: bool,
    reminder_hours_before_close: int,
) -> Optional[ScheduledJob]:
    """Build the pre-close reminder job for this vote, if reminders are enabled.

    Args:
        vote_round: The just-created, just-persisted voting round.
        guild_id: The Discord guild this round belongs to.
        reminder_enabled: Whether vote-ending reminders are turned on for
            this guild (see resolve_vote_reminder_settings).
        reminder_hours_before_close: How many hours before the vote closes
            the reminder should fire.

    Returns:
        The vote_reminder job, or None if reminders are disabled or the
        round has no closes_at. A short-duration round combined with a
        long reminder lead time can still produce a job whose run_at
        falls in the past relative to now (though always strictly before
        closes_at, since reminder_hours_before_close is always positive)
        -- SchedulerService's own due-job polling already handles a
        past run_at correctly (it's simply immediately due), so no
        special-case guard is needed here for that.
    """
    if not reminder_enabled or vote_round.closes_at is None:
        return None

    run_at = vote_round.closes_at - timedelta(hours=reminder_hours_before_close)

    return ScheduledJob(
        guild_id=guild_id,
        job_type=VOTE_REMINDER_JOB_TYPE,
        logical_key=vote_reminder_logical_key(vote_round.id),
        run_at=run_at,
        payload={"vote_id": vote_round.id},
    )


def build_vote_scheduled_jobs(
    vote_round: VoteRound,
    guild_id: int,
    *,
    reminder_enabled: bool,
    reminder_hours_before_close: int,
) -> list[ScheduledJob]:
    """Build every job a newly created voting round needs scheduled.

    Kept as a plain, Discord- and scheduler-free function so the exact
    set of jobs produced for a given round is directly unit-testable
    without an event loop or a real SchedulerService.

    Args:
        vote_round: The just-created, just-persisted voting round.
        guild_id: The Discord guild this round belongs to.
        reminder_enabled: Whether vote-ending reminders are turned on for
            this guild.
        reminder_hours_before_close: How many hours before close the
            reminder should fire.

    Returns:
        The jobs to schedule, in the order they should be scheduled.
        Never includes a job that build_*_job() decided not to create.
    """
    jobs: list[ScheduledJob] = []

    close_job = build_close_vote_job(vote_round, guild_id)
    if close_job is not None:
        jobs.append(close_job)

    reminder_job = build_vote_reminder_job(
        vote_round,
        guild_id,
        reminder_enabled=reminder_enabled,
        reminder_hours_before_close=reminder_hours_before_close,
    )
    if reminder_job is not None:
        jobs.append(reminder_job)

    return jobs


def resolve_vote_reminder_settings(
    guild_configuration_repository: Optional[GuildConfigurationRepository],
    guild_id: int,
    *,
    round_reminder_enabled: Optional[bool] = None,
    round_reminder_hours_before_close: Optional[int] = None,
) -> tuple[bool, int]:
    """Resolve (reminder_enabled, reminder_hours_before_close) for a round.

    FR-027: a voting round may override either setting individually via
    its own "Customize This Vote" fields (see bot.py's
    CustomizeVoteModal); each is resolved independently, falling through
    to the guild's configured default, and finally to
    VoteNotificationsConfig's own documented defaults (enabled, 24 hours)
    when no guild_configuration_repository was supplied, or none exists
    for this guild yet -- there is currently no way for WASH Crew to have
    configured guild-wide defaults (no /setup or /config command exists
    yet), so an unconfigured guild is the common case today, not an error
    condition.

    Args:
        guild_configuration_repository: Where to look up the guild's
            configuration, or None to always use the defaults.
        guild_id: The Discord guild to look up.
        round_reminder_enabled: The round's own override, or None to use
            the guild's configured value.
        round_reminder_hours_before_close: The round's own override, or
            None to use the guild's configured value.

    Returns:
        (reminder_enabled, reminder_hours_before_close).
    """
    configuration = (
        guild_configuration_repository.get(guild_id)
        if guild_configuration_repository is not None
        else None
    )
    if configuration is None:
        defaults = VoteNotificationsConfig()
        guild_enabled, guild_hours = defaults.vote_ending_reminder, defaults.reminder_hours_before_close
    else:
        vote_notifications = configuration.notifications.vote
        guild_enabled = vote_notifications.vote_ending_reminder
        guild_hours = vote_notifications.reminder_hours_before_close

    enabled = round_reminder_enabled if round_reminder_enabled is not None else guild_enabled
    hours = round_reminder_hours_before_close if round_reminder_hours_before_close is not None else guild_hours
    return enabled, hours


async def schedule_vote_jobs(
    scheduler_service: Optional[SchedulerService],
    vote_round: VoteRound,
    guild_id: int,
    *,
    guild_configuration_repository: Optional[GuildConfigurationRepository] = None,
) -> list[ScheduledJob]:
    """Schedule every job a newly created voting round needs.

    Must only be called after the round has been successfully created
    and persisted -- callers should call this immediately after
    VoteService.create_round() reports success (see
    handle_start_vote_completion in bot.py), so a vote that failed to
    create never gets an orphaned scheduled job.

    Idempotency is entirely SchedulerService.schedule()'s existing
    responsibility (it checks for an existing active job under the same
    logical_key before creating another): this function does not
    duplicate that check, and calling it twice for the same round is
    always safe.

    Args:
        scheduler_service: The scheduler to schedule jobs through. If
            None, scheduling is skipped entirely (a no-op) -- this keeps
            existing callers that don't yet have a scheduler to pass in
            working unchanged.
        vote_round: The just-created, just-persisted voting round.
        guild_id: The Discord guild this round belongs to.
        guild_configuration_repository: Used to resolve reminder timing
            for this guild; see resolve_vote_reminder_settings.

    Returns:
        The jobs that were scheduled (each as returned by
        SchedulerService.schedule(), which may be a pre-existing job if
        one was already active under the same logical key). Empty if
        scheduler_service was None.
    """
    if scheduler_service is None:
        return []

    reminder_enabled, reminder_hours_before_close = resolve_vote_reminder_settings(
        guild_configuration_repository,
        guild_id,
        round_reminder_enabled=vote_round.reminder_enabled,
        round_reminder_hours_before_close=vote_round.reminder_hours_before_close,
    )
    jobs = build_vote_scheduled_jobs(
        vote_round,
        guild_id,
        reminder_enabled=reminder_enabled,
        reminder_hours_before_close=reminder_hours_before_close,
    )

    scheduled: list[ScheduledJob] = []
    for job in jobs:
        scheduled.append(await scheduler_service.schedule(job))
    return scheduled


async def cancel_vote_jobs(scheduler_service: Optional[SchedulerService], round_id: int) -> None:
    """Remove a round's scheduled close_vote and vote_reminder jobs, if any.

    Used by FR-023's /edit_vote when a round is ended early or cancelled,
    so no stale job later fires for a round that's no longer open. Safe
    to call unconditionally: each cancellation is independently a no-op
    if no active job exists under that logical key (e.g. reminders were
    disabled, or a job already fired) -- mirrors
    cancel_watch_party_reminder's same contract for watch parties.

    Args:
        scheduler_service: The scheduler to cancel jobs through. If None,
            this is a no-op.
        round_id: The voting round whose jobs should be removed.
    """
    if scheduler_service is None:
        return
    await scheduler_service.cancel_by_logical_key(close_vote_logical_key(round_id))
    await scheduler_service.cancel_by_logical_key(vote_reminder_logical_key(round_id))


async def reschedule_vote_jobs(
    scheduler_service: Optional[SchedulerService],
    vote_round: VoteRound,
    guild_id: int,
    *,
    guild_configuration_repository: Optional[GuildConfigurationRepository] = None,
) -> list[ScheduledJob]:
    """Replace a round's close_vote/vote_reminder jobs after its deadline changed.

    Cancels whatever close_vote and vote_reminder jobs are currently
    active for this round (see cancel_vote_jobs -- a no-op for either if
    none is active) and schedules fresh ones against the round's current
    closes_at. This is the same "cancel the obsolete pending job and
    create the replacement" rescheduling policy already applied for
    watch parties (see watch_party_scheduling.reschedule_watch_party_reminder),
    applied here rather than reimplemented.

    Must only be called after vote_round.closes_at has already been
    updated and persisted (see VoteService.reschedule_round()).

    Args:
        scheduler_service: The scheduler to cancel/schedule through. If
            None, this is a no-op.
        vote_round: The round, already updated to its new closes_at.
        guild_id: The Discord guild this round belongs to.
        guild_configuration_repository: Used to resolve reminder timing
            for this guild; see resolve_vote_reminder_settings.

    Returns:
        The newly scheduled jobs (see schedule_vote_jobs), or an empty
        list if scheduler_service was None.
    """
    if scheduler_service is None:
        return []

    await cancel_vote_jobs(scheduler_service, vote_round.id)
    return await schedule_vote_jobs(
        scheduler_service,
        vote_round,
        guild_id,
        guild_configuration_repository=guild_configuration_repository,
    )
