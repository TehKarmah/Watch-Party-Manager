"""Tests for the automatic_backup job handler (Release Polish: Optional
Automatic Backups -- delivery half).

Covers actually creating the scheduled backup archive, pruning older
scheduled archives beyond the guild's configured retention count,
rechecking whether automatic backups are still enabled at execution
time, scheduling the job's own successor on success, and failure
handling. See test_backup_scheduling.py for the scheduling/reconciliation
layer this builds on.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from watch_party_manager.domain.guild_configuration import BackupConfig, GuildConfiguration
from watch_party_manager.persistence.guild_configuration_repository import (
    GuildConfigurationRepository,
)
from watch_party_manager.scheduler.automatic_backup_job_handler import AutomaticBackupJobHandler
from watch_party_manager.scheduler.backup_scheduling import AUTOMATIC_BACKUP_JOB_TYPE
from watch_party_manager.scheduler.job_handler import RetryableJobError
from watch_party_manager.scheduler.scheduled_job import JobResult, JobStatus, ScheduledJob
from watch_party_manager.scheduler.scheduler_service import SchedulerService
from watch_party_manager.services.backup_service import BackupError, BackupKind, BackupService
from watch_party_manager.services.setup_wizard_service import (
    BACKUP_INTERVAL_DAYS_EXTRA_FIELD,
    BACKUP_RETENTION_COUNT_EXTRA_FIELD,
)

NOW = datetime(2026, 7, 19, 12, tzinfo=timezone.utc)
GUILD_ID = 100


class MemorySchedulerRepository:
    """In-memory SchedulerRepository fake, matching test_backup_scheduling.py's."""

    def __init__(self) -> None:
        self.jobs: dict[str, ScheduledJob] = {}

    async def add(self, job: ScheduledJob) -> ScheduledJob:
        self.jobs[job.job_id] = job
        return job

    async def get_due(self, now: datetime, *, limit: int = 100) -> list[ScheduledJob]:
        return [
            job for job in self.jobs.values() if job.status is JobStatus.PENDING and job.run_at <= now
        ][:limit]

    async def claim(self, job_id: str, started_at: datetime) -> ScheduledJob | None:
        job = self.jobs[job_id]
        if job.status is not JobStatus.PENDING:
            return None
        claimed = job.with_changes(
            status=JobStatus.RUNNING, started_at=started_at, attempt_count=job.attempt_count + 1
        )
        self.jobs[job_id] = claimed
        return claimed

    async def complete(self, job_id: str, completed_at: datetime, result: JobResult) -> ScheduledJob:
        updated = self.jobs[job_id].with_changes(
            status=JobStatus.COMPLETED, completed_at=completed_at, result=result, last_error=None
        )
        self.jobs[job_id] = updated
        return updated

    async def retry(self, job_id: str, run_at: datetime, error: str) -> ScheduledJob:
        updated = self.jobs[job_id].with_changes(status=JobStatus.PENDING, run_at=run_at, last_error=error)
        self.jobs[job_id] = updated
        return updated

    async def fail(self, job_id: str, completed_at: datetime, error: str) -> ScheduledJob:
        updated = self.jobs[job_id].with_changes(
            status=JobStatus.FAILED, completed_at=completed_at, last_error=error
        )
        self.jobs[job_id] = updated
        return updated

    async def cancel(self, job_id: str, completed_at: datetime) -> ScheduledJob:
        updated = self.jobs[job_id].with_changes(
            status=JobStatus.CANCELLED, completed_at=completed_at, result=JobResult.CANCELLED
        )
        self.jobs[job_id] = updated
        return updated

    async def find_active_by_logical_key(self, logical_key: str) -> ScheduledJob | None:
        return next(
            (job for job in self.jobs.values() if job.logical_key == logical_key and job.is_active),
            None,
        )

    async def find_active_by_guild_and_type(self, guild_id: int, job_type: str) -> ScheduledJob | None:
        return next(
            (
                job
                for job in self.jobs.values()
                if job.guild_id == guild_id and job.job_type == job_type and job.is_active
            ),
            None,
        )


def make_job(guild_id: int = GUILD_ID, run_at: datetime | None = None) -> ScheduledJob:
    if run_at is None:
        run_at = NOW
    return ScheduledJob(
        guild_id=guild_id,
        job_type=AUTOMATIC_BACKUP_JOB_TYPE,
        logical_key=f"automatic_backup:{guild_id}:{run_at.date().isoformat()}",
        run_at=run_at,
        payload={"guild_id": guild_id},
    )


class AutomaticBackupJobHandlerTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.data_directory = root / "data"
        self.backup_directory = self.data_directory / "backups"
        self.data_directory.mkdir(parents=True, exist_ok=True)
        (self.data_directory / "suggestions.json").write_text("{}", encoding="utf-8")

        self.backup_service = BackupService(self.data_directory, self.backup_directory)
        self.guild_configuration_repository = GuildConfigurationRepository(
            root / "guild_configurations.json"
        )
        self.scheduler_repository = MemorySchedulerRepository()
        self.scheduler_service = SchedulerService(self.scheduler_repository, clock=lambda: NOW)
        self.handler = AutomaticBackupJobHandler(
            self.backup_service, self.guild_configuration_repository, self.scheduler_service
        )

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _configure(self, *, enabled: bool = True, interval_days: int = 1, retention_count: int = 30) -> None:
        self.guild_configuration_repository.save(
            GuildConfiguration(
                guild_id=GUILD_ID,
                guild_name="Example Guild",
                backup=BackupConfig(
                    include_in_automatic_backups=enabled,
                    extra_fields={
                        BACKUP_INTERVAL_DAYS_EXTRA_FIELD: interval_days,
                        BACKUP_RETENTION_COUNT_EXTRA_FIELD: retention_count,
                    },
                ),
            )
        )


class ExecutionTests(AutomaticBackupJobHandlerTestCase):
    async def test_creates_a_scheduled_backup_archive(self) -> None:
        self._configure()

        result = await self.handler.execute(make_job())

        self.assertEqual(result.result, JobResult.EXECUTED)
        self.assertEqual(len(self.backup_service.list_backups(BackupKind.SCHEDULED)), 1)

    async def test_manual_backups_are_never_touched(self) -> None:
        self._configure()
        self.backup_service.create_backup(BackupKind.MANUAL)

        await self.handler.execute(make_job())

        self.assertEqual(len(self.backup_service.list_backups(BackupKind.MANUAL)), 1)

    async def test_skips_and_does_not_back_up_when_disabled(self) -> None:
        self._configure(enabled=False)

        result = await self.handler.execute(make_job())

        self.assertEqual(result.result, JobResult.SKIPPED_NOT_APPLICABLE)
        self.assertEqual(self.backup_service.list_backups(BackupKind.SCHEDULED), ())

    async def test_no_configuration_at_all_falls_back_to_enabled_by_default(self) -> None:
        # A guild the wizard's Backup Defaults step never reached still
        # has automatic backups enabled by default.
        result = await self.handler.execute(make_job())

        self.assertEqual(result.result, JobResult.EXECUTED)
        self.assertEqual(len(self.backup_service.list_backups(BackupKind.SCHEDULED)), 1)


class RetentionPruningTests(AutomaticBackupJobHandlerTestCase):
    async def test_prunes_scheduled_backups_beyond_the_configured_retention_count(self) -> None:
        self._configure(retention_count=2)
        for hour in range(3):
            self.backup_service.create_backup(
                BackupKind.SCHEDULED,
                created_at=datetime(2026, 7, 18, hour, tzinfo=timezone.utc),
                enforce_retention=False,
            )
        self.assertEqual(len(self.backup_service.list_backups(BackupKind.SCHEDULED)), 3)

        await self.handler.execute(make_job())

        # 3 pre-existing + 1 just created by execute() = 4, pruned to 2.
        self.assertEqual(len(self.backup_service.list_backups(BackupKind.SCHEDULED)), 2)

    async def test_uses_the_guilds_own_retention_count_not_the_services_default(self) -> None:
        # BackupService's own default scheduled_retention_limit is 30;
        # the guild's configured retention count of 1 must win.
        self._configure(retention_count=1)
        self.backup_service.create_backup(
            BackupKind.SCHEDULED,
            created_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
            enforce_retention=False,
        )

        await self.handler.execute(make_job())

        self.assertEqual(len(self.backup_service.list_backups(BackupKind.SCHEDULED)), 1)


class SuccessorSchedulingTests(AutomaticBackupJobHandlerTestCase):
    async def test_schedules_its_own_successor_on_success(self) -> None:
        self._configure(interval_days=3)
        before = datetime.now(timezone.utc)

        await self.handler.execute(make_job())

        active_jobs = [job for job in self.scheduler_repository.jobs.values() if job.is_active]
        self.assertEqual(len(active_jobs), 1)
        expected = before + timedelta(days=3)
        self.assertAlmostEqual(active_jobs[0].run_at.timestamp(), expected.timestamp(), delta=5)

    async def test_does_not_schedule_a_successor_when_disabled(self) -> None:
        self._configure(enabled=False)

        await self.handler.execute(make_job())

        active_jobs = [job for job in self.scheduler_repository.jobs.values() if job.is_active]
        self.assertEqual(active_jobs, [])

    async def test_successor_does_not_collide_with_the_currently_running_job(self) -> None:
        # Regression for the release-blocking self-collision risk: the
        # currently executing job (this one) is still "running" in a
        # real scheduler run (claimed before execute() is called) -- the
        # successor's logical_key must differ so scheduling it never
        # silently no-ops against the job it's the successor of.
        self._configure(interval_days=1)
        current_job = make_job(run_at=NOW - timedelta(days=1))

        await self.handler.execute(current_job)

        active_jobs = [job for job in self.scheduler_repository.jobs.values() if job.is_active]
        self.assertEqual(len(active_jobs), 1)
        self.assertNotEqual(active_jobs[0].logical_key, current_job.logical_key)


class FailureHandlingTests(AutomaticBackupJobHandlerTestCase):
    async def test_a_backup_error_raises_a_retryable_job_error(self) -> None:
        self._configure()

        with patch.object(
            self.backup_service, "create_backup", side_effect=BackupError("disk full")
        ):
            with self.assertRaises(RetryableJobError):
                await self.handler.execute(make_job())

    async def test_a_failed_backup_does_not_schedule_a_successor(self) -> None:
        self._configure()

        with patch.object(
            self.backup_service, "create_backup", side_effect=BackupError("disk full")
        ):
            with self.assertRaises(RetryableJobError):
                await self.handler.execute(make_job())

        self.assertEqual(len(self.scheduler_repository.jobs), 0)

    async def test_running_the_handler_through_the_scheduler_retries_on_failure(self) -> None:
        # End-to-end: SchedulerService.run_once() claims the job, the
        # handler raises RetryableJobError, and the scheduler's existing
        # retry policy takes over -- nothing backup-specific needed here.
        self._configure()
        job = await self.scheduler_service.schedule(make_job(run_at=NOW))
        scheduler = SchedulerService(self.scheduler_repository, clock=lambda: NOW)
        scheduler.register_handler(AUTOMATIC_BACKUP_JOB_TYPE, self.handler)

        with patch.object(
            self.backup_service, "create_backup", side_effect=BackupError("disk full")
        ):
            await scheduler.run_once()

        refreshed = self.scheduler_repository.jobs[job.job_id]
        self.assertEqual(refreshed.status, JobStatus.PENDING)
        self.assertIsNotNone(refreshed.last_error)


if __name__ == "__main__":
    unittest.main()
