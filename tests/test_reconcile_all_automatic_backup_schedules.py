"""Tests for reconcile_all_automatic_backup_schedules (Release Polish:
Optional Automatic Backups) -- the startup-time reconciliation loop that
guarantees every configured guild's automatic-backup schedule matches its
current settings after a restart, without needing a live Discord
connection or a full WatchPartyBot instance.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from watch_party_manager.bot import reconcile_all_automatic_backup_schedules
from watch_party_manager.domain.guild_configuration import BackupConfig, GuildConfiguration
from watch_party_manager.persistence.guild_configuration_repository import (
    GuildConfigurationRepository,
)
from watch_party_manager.scheduler.backup_scheduling import AUTOMATIC_BACKUP_JOB_TYPE
from watch_party_manager.scheduler.scheduled_job import JobResult, JobStatus, ScheduledJob
from watch_party_manager.scheduler.scheduler_service import SchedulerService

NOW = datetime(2026, 7, 19, 12, tzinfo=timezone.utc)


class MemorySchedulerRepository:
    """In-memory SchedulerRepository fake, matching the project's other tests."""

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


class FakeSchedulerHost:
    def __init__(self, scheduler_service: SchedulerService) -> None:
        self.scheduler_service = scheduler_service


class FakeBot:
    def __init__(self, guild_configuration_repository, scheduler_service) -> None:
        self.guild_configuration_repository = guild_configuration_repository
        self.scheduler_host = FakeSchedulerHost(scheduler_service)


class ReconcileAllAutomaticBackupSchedulesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.guild_configuration_repository = GuildConfigurationRepository(
            Path(self._temp_dir.name) / "guild_configurations.json"
        )
        self.scheduler_repository = MemorySchedulerRepository()
        self.scheduler_service = SchedulerService(self.scheduler_repository, clock=lambda: NOW)
        self.bot = FakeBot(self.guild_configuration_repository, self.scheduler_service)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    async def test_schedules_a_job_for_every_configured_guild(self) -> None:
        self.guild_configuration_repository.save(GuildConfiguration(guild_id=100, guild_name="Guild One"))
        self.guild_configuration_repository.save(GuildConfiguration(guild_id=200, guild_name="Guild Two"))

        await reconcile_all_automatic_backup_schedules(self.bot)

        active_jobs = [job for job in self.scheduler_repository.jobs.values() if job.is_active]
        self.assertEqual(len(active_jobs), 2)
        self.assertEqual({job.guild_id for job in active_jobs}, {100, 200})

    async def test_skips_scheduling_for_a_guild_that_disabled_automatic_backups(self) -> None:
        self.guild_configuration_repository.save(
            GuildConfiguration(
                guild_id=100,
                guild_name="Guild One",
                backup=BackupConfig(include_in_automatic_backups=False),
            )
        )

        await reconcile_all_automatic_backup_schedules(self.bot)

        self.assertEqual(len(self.scheduler_repository.jobs), 0)

    async def test_does_not_duplicate_an_already_active_job_across_restarts(self) -> None:
        # Simulates a second startup: reconciling twice must never leave
        # more than one active job per guild.
        self.guild_configuration_repository.save(GuildConfiguration(guild_id=100, guild_name="Guild One"))

        await reconcile_all_automatic_backup_schedules(self.bot)
        await reconcile_all_automatic_backup_schedules(self.bot)

        active_jobs = [job for job in self.scheduler_repository.jobs.values() if job.is_active]
        self.assertEqual(len(active_jobs), 1)

    async def test_one_guilds_failure_does_not_block_another_guilds_reconciliation(self) -> None:
        self.guild_configuration_repository.save(GuildConfiguration(guild_id=100, guild_name="Guild One"))
        self.guild_configuration_repository.save(GuildConfiguration(guild_id=200, guild_name="Guild Two"))

        original_find = self.scheduler_repository.find_active_by_guild_and_type

        async def flaky_find(guild_id: int, job_type: str):
            if guild_id == 100:
                raise RuntimeError("simulated failure for guild 100")
            return await original_find(guild_id, job_type)

        self.scheduler_repository.find_active_by_guild_and_type = flaky_find

        await reconcile_all_automatic_backup_schedules(self.bot)

        active_jobs = [job for job in self.scheduler_repository.jobs.values() if job.is_active]
        self.assertEqual({job.guild_id for job in active_jobs}, {200})

    async def test_no_configured_guilds_is_a_safe_no_op(self) -> None:
        await reconcile_all_automatic_backup_schedules(self.bot)

        self.assertEqual(len(self.scheduler_repository.jobs), 0)


if __name__ == "__main__":
    unittest.main()
