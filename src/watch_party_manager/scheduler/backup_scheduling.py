"""Builds and schedules WASH's automatic-backup job, and keeps it in sync
with a guild's current backup configuration (Release Polish: Optional
Automatic Backups).

Mirrors watch_party_scheduling.py's role for watch-party reminders: this
is the one place that knows the automatic_backup job's shape and how to
reconcile it with GuildConfiguration.backup's current settings, reusing
SchedulerService directly rather than duplicating its scheduling,
deduplication, or cancellation logic.

Unlike a vote or watch-party reminder (a one-shot job tied to a single
domain event), automatic backups are genuinely recurring: each successful
run schedules its own successor (see AutomaticBackupJobHandler.execute()
in automatic_backup_job_handler.py), using a fresh logical_key derived
from that occurrence's own run_at so it never collides with the job
currently executing (whose own key was built from an earlier run_at).
Reconciliation (startup, and every /setup or /config change) does not
need to know that key: it cancels whatever automatic_backup job is
currently active for the guild via
SchedulerService.cancel_active_by_guild_and_type(), independent of which
specific occurrence it represents.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from watch_party_manager.persistence.guild_configuration_repository import (
    GuildConfigurationRepository,
)
from watch_party_manager.services.setup_wizard_service import (
    BACKUP_INTERVAL_DAYS_EXTRA_FIELD,
    BACKUP_RETENTION_COUNT_EXTRA_FIELD,
)

from .scheduled_job import ScheduledJob
from .scheduler_service import SchedulerService

AUTOMATIC_BACKUP_JOB_TYPE = "automatic_backup"

# Mirrors BackupService's own documented defaults (DEFAULT_BACKUP_INTERVAL_DAYS,
# DEFAULT_RETENTION_LIMIT) -- kept as separate constants here (rather than
# importing those) since this module reasons about a *guild's* configured
# settings, not BackupService's own single shared-instance defaults.
DEFAULT_BACKUP_INTERVAL_DAYS = 1
DEFAULT_BACKUP_RETENTION_COUNT = 30


def automatic_backup_logical_key(guild_id: int, run_at: datetime) -> str:
    """Build one occurrence's logical key.

    Unique per guild and scheduled date, so a handler scheduling its own
    successor (a different date) never collides with the job it's
    currently running as (see module docstring).
    """
    return f"automatic_backup:{guild_id}:{run_at.date().isoformat()}"


def resolve_automatic_backup_settings(
    guild_configuration_repository: Optional[GuildConfigurationRepository],
    guild_id: int,
) -> tuple[bool, int, int]:
    """Look up (enabled, interval_days, retention_count) for a guild.

    Falls back to the documented defaults (enabled, 1 day, 30 kept) when
    no repository was supplied, no configuration exists yet for this
    guild, or the interval/retention were never explicitly configured --
    mirrors resolve_vote_reminder_settings's exact fallback convention.
    """
    configuration = (
        guild_configuration_repository.get(guild_id)
        if guild_configuration_repository is not None
        else None
    )
    if configuration is None:
        return True, DEFAULT_BACKUP_INTERVAL_DAYS, DEFAULT_BACKUP_RETENTION_COUNT

    backup = configuration.backup
    interval_days = backup.extra_fields.get(BACKUP_INTERVAL_DAYS_EXTRA_FIELD, DEFAULT_BACKUP_INTERVAL_DAYS)
    retention_count = backup.extra_fields.get(BACKUP_RETENTION_COUNT_EXTRA_FIELD, DEFAULT_BACKUP_RETENTION_COUNT)
    return backup.include_in_automatic_backups, interval_days, retention_count


def build_automatic_backup_job(
    guild_id: int, *, interval_days: int, now: Optional[datetime] = None
) -> ScheduledJob:
    """Build the next automatic-backup job, due interval_days from now."""
    current_time = now if now is not None else datetime.now(timezone.utc)
    run_at = current_time + timedelta(days=interval_days)
    return ScheduledJob(
        guild_id=guild_id,
        job_type=AUTOMATIC_BACKUP_JOB_TYPE,
        logical_key=automatic_backup_logical_key(guild_id, run_at),
        run_at=run_at,
        payload={"guild_id": guild_id},
    )


async def reconcile_automatic_backup_schedule(
    scheduler_service: Optional[SchedulerService],
    guild_id: int,
    *,
    guild_configuration_repository: Optional[GuildConfigurationRepository] = None,
    now: Optional[datetime] = None,
) -> Optional[ScheduledJob]:
    """Ensure exactly one automatic-backup job matches a guild's current
    configuration.

    Called at bot startup (for every guild with saved configuration) and
    immediately after /setup or /config changes backup settings, so a job
    is never left scheduled under stale settings and a guild is never
    left without one while enabled -- avoiding duplicate scheduled jobs
    after a restart or a settings change.

    Always cancels whatever automatic_backup job is currently active for
    this guild first (a no-op if none is active), then schedules a fresh
    one -- interval_days from now -- only if automatic backups are
    currently enabled. This is the same "cancel the obsolete pending job,
    create the replacement" policy already used for watch party reminders
    (see reschedule_watch_party_reminder in watch_party_scheduling.py),
    applied here since automatic backups have no single stable "current"
    run_at to update in place.

    Args:
        scheduler_service: The scheduler to cancel/schedule through. If
            None, this is a no-op (mirrors every other *_scheduling.py
            entry point's convention for callers with no scheduler yet).
        guild_id: The Discord guild whose schedule should be reconciled.
        guild_configuration_repository: Used to resolve the guild's
            current backup settings; see resolve_automatic_backup_settings.
        now: Injectable clock for deterministic tests.

    Returns:
        The freshly scheduled job, or None if scheduler_service was None
        or automatic backups are currently disabled for this guild.
    """
    if scheduler_service is None:
        return None

    await scheduler_service.cancel_active_by_guild_and_type(guild_id, AUTOMATIC_BACKUP_JOB_TYPE)

    enabled, interval_days, _retention_count = resolve_automatic_backup_settings(
        guild_configuration_repository, guild_id
    )
    if not enabled:
        return None

    job = build_automatic_backup_job(guild_id, interval_days=interval_days, now=now)
    return await scheduler_service.schedule(job)
