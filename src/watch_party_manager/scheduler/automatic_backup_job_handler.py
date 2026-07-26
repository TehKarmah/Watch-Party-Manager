"""Scheduler handler for the automatic_backup job (Release Polish:
Optional Automatic Backups -- the Backup Defaults configuration
introduced by the Setup Wizard/`/config` becomes functional here, not
merely cosmetic).

Executes a guild's recurring automatic backup: rechecks whether
automatic backups are still enabled for this guild (a WASH Crew member
may have disabled them since this job was scheduled -- the scheduler's
own documented execution-time-validation principle, see
docs/architecture/scheduler.md), creates a BackupKind.SCHEDULED archive,
prunes older scheduled archives beyond the guild's configured retention
count, and -- only on success -- schedules its own successor via
backup_scheduling.reconcile_automatic_backup_schedule(), keeping the
recurrence going indefinitely without the scheduler itself needing any
special "recurring job" concept.

Manual /backup, restore, import, database reset, and factory-reset
safety backups all go through BackupService directly and are entirely
unaffected by this handler or by automatic backups being disabled.
"""

from __future__ import annotations

import logging
from typing import Optional

from watch_party_manager.persistence.guild_configuration_repository import (
    GuildConfigurationRepository,
)
from watch_party_manager.services.backup_service import BackupError, BackupKind, BackupService

from .backup_scheduling import reconcile_automatic_backup_schedule, resolve_automatic_backup_settings
from .job_handler import JobExecutionResult, RetryableJobError
from .scheduled_job import JobResult, ScheduledJob
from .scheduler_service import SchedulerService


class AutomaticBackupJobHandler:
    """Creates a guild's scheduled automatic backup when its job is due.

    Registered under the "automatic_backup" job type (see
    backup_scheduling.AUTOMATIC_BACKUP_JOB_TYPE) via
    SchedulerService.register_handler().
    """

    def __init__(
        self,
        backup_service: BackupService,
        guild_configuration_repository: GuildConfigurationRepository,
        scheduler_service: SchedulerService,
        *,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """Initialize the handler.

        Args:
            backup_service: Used to create the scheduled archive and
                prune older ones beyond the configured retention count.
            guild_configuration_repository: Used to recheck the guild's
                current automatic-backup settings at execution time.
            scheduler_service: Used to schedule this job's own successor
                once this occurrence completes successfully.
            logger: Optional logger override, mainly for tests.
        """
        self._backup_service = backup_service
        self._guild_configuration_repository = guild_configuration_repository
        self._scheduler_service = scheduler_service
        self._logger = logger or logging.getLogger(__name__)

    async def execute(self, job: ScheduledJob) -> JobExecutionResult:
        """Execute one claimed automatic_backup job.

        Rechecks whether automatic backups are still enabled for this
        guild before doing anything -- if they were disabled since this
        job was scheduled, this occurrence is skipped and, per Optional
        Automatic Backups, no successor is scheduled either (reconciling
        the schedule is reconcile_automatic_backup_schedule()'s job,
        triggered explicitly by /setup or /config, not this handler's).

        Args:
            job: The claimed automatic_backup job. job.guild_id is the
                guild this backup is for.

        Returns:
            JobExecutionResult(EXECUTED) if the backup was created (and
            its successor scheduled).
            JobExecutionResult(SKIPPED_NOT_APPLICABLE) if automatic
            backups are currently disabled for this guild.

        Raises:
            RetryableJobError: If creating the backup archive fails
                (e.g. a transient filesystem error) -- the scheduler's
                existing retry policy handles the retry schedule.
        """
        guild_id = job.guild_id

        enabled, _interval_days, retention_count = resolve_automatic_backup_settings(
            self._guild_configuration_repository, guild_id
        )
        if not enabled:
            self._logger.info(
                "automatic_backup job for guild %s skipped: automatic backups are disabled", guild_id
            )
            return JobExecutionResult(result=JobResult.SKIPPED_NOT_APPLICABLE)

        try:
            self._backup_service.create_backup(
                BackupKind.SCHEDULED, guild_id=guild_id, enforce_retention=False
            )
            self._backup_service.prune_backups(BackupKind.SCHEDULED, retention_limit=retention_count)
        except BackupError as exc:
            raise RetryableJobError(str(exc)) from exc

        self._logger.info("Created scheduled automatic backup for guild %s", guild_id)

        await reconcile_automatic_backup_schedule(
            self._scheduler_service,
            guild_id,
            guild_configuration_repository=self._guild_configuration_repository,
        )

        return JobExecutionResult(result=JobResult.EXECUTED)
