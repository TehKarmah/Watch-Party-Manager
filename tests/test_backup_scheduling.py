"""Tests for Release Polish: Optional Automatic Backups' scheduling layer.

Covers building/reconciling the automatic_backup job and keeping it in
sync with a guild's current backup configuration, mirroring
test_watch_party_scheduling.py's structure. No backup creation is
exercised here; see test_automatic_backup_job_handler.py for that.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from watch_party_manager.domain.guild_configuration import BackupConfig, GuildConfiguration
from watch_party_manager.persistence.guild_configuration_repository import (
    GuildConfigurationRepository,
)
from watch_party_manager.scheduler.backup_scheduling import (
    AUTOMATIC_BACKUP_JOB_TYPE,
    automatic_backup_logical_key,
    build_automatic_backup_job,
    reconcile_automatic_backup_schedule,
    resolve_automatic_backup_settings,
)
from watch_party_manager.scheduler.scheduled_job import JobResult, JobStatus, ScheduledJob
from watch_party_manager.scheduler.scheduler_service import SchedulerService
from watch_party_manager.services.setup_wizard_service import (
    BACKUP_INTERVAL_DAYS_EXTRA_FIELD,
    BACKUP_RETENTION_COUNT_EXTRA_FIELD,
)

NOW = datetime(2026, 7, 19, 12, tzinfo=timezone.utc)


class MemorySchedulerRepository:
    """In-memory SchedulerRepository fake, matching test_watch_party_scheduling.py's."""

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


class LogicalKeyTests(unittest.TestCase):
    def test_logical_key_is_scoped_by_guild_and_date(self) -> None:
        run_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
        self.assertEqual(automatic_backup_logical_key(100, run_at), "automatic_backup:100:2026-08-01")

    def test_different_dates_produce_different_keys(self) -> None:
        first = automatic_backup_logical_key(100, datetime(2026, 8, 1, tzinfo=timezone.utc))
        second = automatic_backup_logical_key(100, datetime(2026, 8, 2, tzinfo=timezone.utc))
        self.assertNotEqual(first, second)


class BuildAutomaticBackupJobTests(unittest.TestCase):
    def test_builds_a_job_with_the_correct_type_and_guild(self) -> None:
        job = build_automatic_backup_job(100, interval_days=1, now=NOW)

        self.assertEqual(job.job_type, AUTOMATIC_BACKUP_JOB_TYPE)
        self.assertEqual(job.guild_id, 100)

    def test_run_at_is_interval_days_from_now(self) -> None:
        job = build_automatic_backup_job(100, interval_days=3, now=NOW)

        self.assertEqual(job.run_at, NOW + timedelta(days=3))

    def test_logical_key_matches_the_computed_run_at(self) -> None:
        job = build_automatic_backup_job(100, interval_days=1, now=NOW)

        self.assertEqual(job.logical_key, automatic_backup_logical_key(100, NOW + timedelta(days=1)))

    def test_payload_contains_the_guild_id(self) -> None:
        job = build_automatic_backup_job(100, interval_days=1, now=NOW)

        self.assertEqual(job.payload, {"guild_id": 100})


class ResolveAutomaticBackupSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.file_path = Path(self._temp_dir.name) / "guild_configurations.json"
        self.repository = GuildConfigurationRepository(self.file_path)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_falls_back_to_documented_defaults_when_no_repository_is_given(self) -> None:
        enabled, interval_days, retention_count = resolve_automatic_backup_settings(None, guild_id=100)

        self.assertTrue(enabled)
        self.assertEqual(interval_days, 1)
        self.assertEqual(retention_count, 30)

    def test_falls_back_to_defaults_when_no_configuration_exists_for_the_guild(self) -> None:
        enabled, interval_days, retention_count = resolve_automatic_backup_settings(
            self.repository, guild_id=100
        )

        self.assertTrue(enabled)
        self.assertEqual(interval_days, 1)
        self.assertEqual(retention_count, 30)

    def test_falls_back_to_defaults_when_interval_and_retention_were_never_configured(self) -> None:
        # A guild whose configuration exists but never reached the Backup
        # Defaults step -- include_in_automatic_backups still defaults to
        # True (Automatic Backups remain enabled by default).
        self.repository.save(GuildConfiguration(guild_id=100, guild_name="Example Guild"))

        enabled, interval_days, retention_count = resolve_automatic_backup_settings(
            self.repository, guild_id=100
        )

        self.assertTrue(enabled)
        self.assertEqual(interval_days, 1)
        self.assertEqual(retention_count, 30)

    def test_uses_the_guilds_configured_settings_when_present(self) -> None:
        self.repository.save(
            GuildConfiguration(
                guild_id=100,
                guild_name="Example Guild",
                backup=BackupConfig(
                    include_in_automatic_backups=True,
                    extra_fields={
                        BACKUP_INTERVAL_DAYS_EXTRA_FIELD: 7,
                        BACKUP_RETENTION_COUNT_EXTRA_FIELD: 10,
                    },
                ),
            )
        )

        enabled, interval_days, retention_count = resolve_automatic_backup_settings(
            self.repository, guild_id=100
        )

        self.assertTrue(enabled)
        self.assertEqual(interval_days, 7)
        self.assertEqual(retention_count, 10)

    def test_reports_disabled_when_the_guild_has_turned_it_off(self) -> None:
        self.repository.save(
            GuildConfiguration(
                guild_id=100,
                guild_name="Example Guild",
                backup=BackupConfig(include_in_automatic_backups=False),
            )
        )

        enabled, _interval_days, _retention_count = resolve_automatic_backup_settings(
            self.repository, guild_id=100
        )

        self.assertFalse(enabled)

    def test_a_different_guilds_settings_do_not_leak_across(self) -> None:
        self.repository.save(
            GuildConfiguration(
                guild_id=100,
                guild_name="Guild One",
                backup=BackupConfig(
                    include_in_automatic_backups=False,
                    extra_fields={BACKUP_INTERVAL_DAYS_EXTRA_FIELD: 7},
                ),
            )
        )

        enabled, interval_days, _retention_count = resolve_automatic_backup_settings(
            self.repository, guild_id=200
        )

        self.assertTrue(enabled)
        self.assertEqual(interval_days, 1)


class ReconcileAutomaticBackupScheduleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = MemorySchedulerRepository()
        self.scheduler_service = SchedulerService(self.repository, clock=lambda: NOW)
        self._temp_dir = tempfile.TemporaryDirectory()
        self.guild_configuration_repository = GuildConfigurationRepository(
            Path(self._temp_dir.name) / "guild_configurations.json"
        )

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    async def test_schedules_a_job_when_enabled_and_none_exists_yet(self) -> None:
        job = await reconcile_automatic_backup_schedule(
            self.scheduler_service, 100, guild_configuration_repository=self.guild_configuration_repository
        )

        self.assertIsNotNone(job)
        self.assertEqual(job.job_type, AUTOMATIC_BACKUP_JOB_TYPE)
        self.assertEqual(len(self.repository.jobs), 1)

    async def test_returns_none_when_no_scheduler_service_is_given(self) -> None:
        job = await reconcile_automatic_backup_schedule(None, 100)

        self.assertIsNone(job)

    async def test_returns_none_and_schedules_nothing_when_disabled(self) -> None:
        self.guild_configuration_repository.save(
            GuildConfiguration(
                guild_id=100,
                guild_name="Example Guild",
                backup=BackupConfig(include_in_automatic_backups=False),
            )
        )

        job = await reconcile_automatic_backup_schedule(
            self.scheduler_service, 100, guild_configuration_repository=self.guild_configuration_repository
        )

        self.assertIsNone(job)
        self.assertEqual(len(self.repository.jobs), 0)

    async def test_cancels_a_stale_job_when_disabling(self) -> None:
        # First reconcile while enabled schedules a job...
        first_job = await reconcile_automatic_backup_schedule(
            self.scheduler_service, 100, guild_configuration_repository=self.guild_configuration_repository
        )
        self.assertIsNotNone(first_job)

        # ...then the guild disables automatic backups, and reconciling
        # again must cancel that now-stale job rather than leaving it
        # scheduled forever.
        self.guild_configuration_repository.save(
            GuildConfiguration(
                guild_id=100,
                guild_name="Example Guild",
                backup=BackupConfig(include_in_automatic_backups=False),
            )
        )
        job = await reconcile_automatic_backup_schedule(
            self.scheduler_service, 100, guild_configuration_repository=self.guild_configuration_repository
        )

        self.assertIsNone(job)
        self.assertEqual(self.repository.jobs[first_job.job_id].status, JobStatus.CANCELLED)
        active_jobs = [j for j in self.repository.jobs.values() if j.is_active]
        self.assertEqual(active_jobs, [])

    async def test_re_enabling_schedules_a_fresh_job(self) -> None:
        self.guild_configuration_repository.save(
            GuildConfiguration(
                guild_id=100,
                guild_name="Example Guild",
                backup=BackupConfig(include_in_automatic_backups=False),
            )
        )
        await reconcile_automatic_backup_schedule(
            self.scheduler_service, 100, guild_configuration_repository=self.guild_configuration_repository
        )

        self.guild_configuration_repository.save(
            GuildConfiguration(
                guild_id=100,
                guild_name="Example Guild",
                backup=BackupConfig(include_in_automatic_backups=True),
            )
        )
        job = await reconcile_automatic_backup_schedule(
            self.scheduler_service, 100, guild_configuration_repository=self.guild_configuration_repository
        )

        self.assertIsNotNone(job)
        self.assertTrue(self.repository.jobs[job.job_id].status is JobStatus.PENDING)

    async def test_interval_change_reschedules_using_the_new_interval(self) -> None:
        self.guild_configuration_repository.save(
            GuildConfiguration(
                guild_id=100,
                guild_name="Example Guild",
                backup=BackupConfig(
                    include_in_automatic_backups=True,
                    extra_fields={BACKUP_INTERVAL_DAYS_EXTRA_FIELD: 1, BACKUP_RETENTION_COUNT_EXTRA_FIELD: 30},
                ),
            )
        )
        original_job = await reconcile_automatic_backup_schedule(
            self.scheduler_service,
            100,
            guild_configuration_repository=self.guild_configuration_repository,
            now=NOW,
        )
        self.assertEqual(original_job.run_at, NOW + timedelta(days=1))

        self.guild_configuration_repository.save(
            GuildConfiguration(
                guild_id=100,
                guild_name="Example Guild",
                backup=BackupConfig(
                    include_in_automatic_backups=True,
                    extra_fields={BACKUP_INTERVAL_DAYS_EXTRA_FIELD: 7, BACKUP_RETENTION_COUNT_EXTRA_FIELD: 30},
                ),
            )
        )
        updated_job = await reconcile_automatic_backup_schedule(
            self.scheduler_service,
            100,
            guild_configuration_repository=self.guild_configuration_repository,
            now=NOW,
        )

        self.assertEqual(updated_job.run_at, NOW + timedelta(days=7))
        self.assertEqual(self.repository.jobs[original_job.job_id].status, JobStatus.CANCELLED)

    async def test_reconciling_twice_without_changes_never_leaves_more_than_one_active_job(self) -> None:
        await reconcile_automatic_backup_schedule(
            self.scheduler_service, 100, guild_configuration_repository=self.guild_configuration_repository
        )
        await reconcile_automatic_backup_schedule(
            self.scheduler_service, 100, guild_configuration_repository=self.guild_configuration_repository
        )

        active_jobs = [job for job in self.repository.jobs.values() if job.is_active]
        self.assertEqual(len(active_jobs), 1)

    async def test_different_guilds_are_reconciled_independently(self) -> None:
        job_a = await reconcile_automatic_backup_schedule(
            self.scheduler_service, 100, guild_configuration_repository=self.guild_configuration_repository
        )
        job_b = await reconcile_automatic_backup_schedule(
            self.scheduler_service, 200, guild_configuration_repository=self.guild_configuration_repository
        )

        self.assertNotEqual(job_a.job_id, job_b.job_id)
        self.assertEqual(self.repository.jobs[job_a.job_id].status, JobStatus.PENDING)
        self.assertEqual(self.repository.jobs[job_b.job_id].status, JobStatus.PENDING)


if __name__ == "__main__":
    unittest.main()
