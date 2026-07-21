"""Tests for FR-020: Watch Party Reminder Scheduling.

Covers the scheduling behavior this milestone adds -- building the
watch_party_reminder job for a watch party and handing it to the existing
SchedulerService, including keeping it in sync when a watch party is
rescheduled or cancelled. No reminder delivery is exercised here; see
test_watch_party_reminder_job_handler.py for that.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from watch_party_manager.domain.guild_configuration import (
    GuildConfiguration,
    NotificationsConfig,
    WatchNotificationsConfig,
)
from watch_party_manager.domain.watch_party import WatchParty
from watch_party_manager.persistence.guild_configuration_repository import (
    GuildConfigurationRepository,
)
from watch_party_manager.scheduler.scheduled_job import JobResult, JobStatus, ScheduledJob
from watch_party_manager.scheduler.scheduler_service import SchedulerService
from watch_party_manager.scheduler.watch_party_scheduling import (
    WATCH_PARTY_REMINDER_JOB_TYPE,
    build_watch_party_reminder_job,
    cancel_watch_party_reminder,
    reschedule_watch_party_reminder,
    resolve_watch_party_reminder_settings,
    schedule_watch_party_reminder,
    watch_party_reminder_logical_key,
)


class MemorySchedulerRepository:
    """In-memory SchedulerRepository fake, matching test_vote_scheduling.py's."""

    def __init__(self) -> None:
        self.jobs: dict[str, ScheduledJob] = {}

    async def add(self, job: ScheduledJob) -> ScheduledJob:
        self.jobs[job.job_id] = job
        return job

    async def get_due(self, now: datetime, *, limit: int = 100) -> list[ScheduledJob]:
        return [
            job
            for job in self.jobs.values()
            if job.status is JobStatus.PENDING and job.run_at <= now
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


def make_watch_party(
    watch_party_id: int = 1, scheduled_at: datetime | None = None, guild_id: int = 100
) -> WatchParty:
    if scheduled_at is None:
        scheduled_at = datetime.now(timezone.utc) + timedelta(days=1)
    return WatchParty(id=watch_party_id, watch_item_id=1, scheduled_at=scheduled_at, guild_id=guild_id)


class LogicalKeyTests(unittest.TestCase):
    def test_watch_party_reminder_logical_key_format(self) -> None:
        self.assertEqual(watch_party_reminder_logical_key(42), "watch_party:42:reminder")


class BuildWatchPartyReminderJobTests(unittest.TestCase):
    def test_builds_a_reminder_job_with_the_correct_type_and_key(self) -> None:
        watch_party = make_watch_party(watch_party_id=7)

        job = build_watch_party_reminder_job(watch_party, guild_id=100, reminder_hours_before_watch=1)

        self.assertEqual(job.job_type, WATCH_PARTY_REMINDER_JOB_TYPE)
        self.assertEqual(job.logical_key, "watch_party:7:reminder")
        self.assertEqual(job.guild_id, 100)

    def test_run_at_is_offset_before_the_watch_party_by_the_configured_hours(self) -> None:
        scheduled_at = datetime(2026, 8, 1, 20, tzinfo=timezone.utc)
        watch_party = make_watch_party(scheduled_at=scheduled_at)

        job = build_watch_party_reminder_job(watch_party, guild_id=100, reminder_hours_before_watch=1)

        self.assertEqual(job.run_at, scheduled_at - timedelta(hours=1))

    def test_payload_contains_only_the_watch_party_id(self) -> None:
        watch_party = make_watch_party(watch_party_id=7)

        job = build_watch_party_reminder_job(watch_party, guild_id=100, reminder_hours_before_watch=1)

        self.assertEqual(job.payload, {"watch_party_id": 7})

    def test_uses_the_configured_reminder_lead_time(self) -> None:
        scheduled_at = datetime(2026, 8, 1, 20, tzinfo=timezone.utc)
        watch_party = make_watch_party(scheduled_at=scheduled_at)

        job = build_watch_party_reminder_job(watch_party, guild_id=100, reminder_hours_before_watch=3)

        self.assertEqual(job.run_at, scheduled_at - timedelta(hours=3))


class ResolveWatchPartyReminderSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.file_path = Path(self._temp_dir.name) / "guild_configurations.json"
        self.repository = GuildConfigurationRepository(self.file_path)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_falls_back_to_documented_defaults_when_no_repository_is_given(self) -> None:
        enabled, hours = resolve_watch_party_reminder_settings(None, guild_id=100)

        defaults = WatchNotificationsConfig()
        self.assertEqual(enabled, defaults.enabled)
        self.assertEqual(hours, defaults.reminder_hours_before_watch)

    def test_defaults_to_one_hour_before(self) -> None:
        # FR-020's explicit default reminder interval.
        _, hours = resolve_watch_party_reminder_settings(None, guild_id=100)

        self.assertEqual(hours, 1)

    def test_falls_back_to_defaults_when_no_configuration_exists_for_the_guild(self) -> None:
        enabled, hours = resolve_watch_party_reminder_settings(self.repository, guild_id=100)

        defaults = WatchNotificationsConfig()
        self.assertEqual(enabled, defaults.enabled)
        self.assertEqual(hours, defaults.reminder_hours_before_watch)

    def test_uses_the_guilds_configured_settings_when_present(self) -> None:
        configuration = GuildConfiguration(
            guild_id=100,
            guild_name="Example Guild",
            notifications=NotificationsConfig(
                watch=WatchNotificationsConfig(enabled=False, reminder_hours_before_watch=2)
            ),
        )
        self.repository.save(configuration)

        enabled, hours = resolve_watch_party_reminder_settings(self.repository, guild_id=100)

        self.assertFalse(enabled)
        self.assertEqual(hours, 2)

    def test_a_different_guilds_settings_do_not_leak_across(self) -> None:
        self.repository.save(
            GuildConfiguration(
                guild_id=100,
                guild_name="Guild One",
                notifications=NotificationsConfig(
                    watch=WatchNotificationsConfig(reminder_hours_before_watch=2)
                ),
            )
        )

        enabled, hours = resolve_watch_party_reminder_settings(self.repository, guild_id=200)

        defaults = WatchNotificationsConfig()
        self.assertEqual(hours, defaults.reminder_hours_before_watch)


class ScheduleWatchPartyReminderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = MemorySchedulerRepository()
        self.scheduler_service = SchedulerService(self.repository)

    async def test_schedules_a_reminder_job_for_a_new_watch_party(self) -> None:
        watch_party = make_watch_party(watch_party_id=7)

        job = await schedule_watch_party_reminder(self.scheduler_service, watch_party, guild_id=100)

        self.assertIsNotNone(job)
        self.assertEqual(job.job_type, WATCH_PARTY_REMINDER_JOB_TYPE)
        self.assertEqual(len(self.repository.jobs), 1)

    async def test_returns_none_when_no_scheduler_service_is_given(self) -> None:
        watch_party = make_watch_party()

        job = await schedule_watch_party_reminder(None, watch_party, guild_id=100)

        self.assertIsNone(job)

    async def test_calling_schedule_twice_does_not_create_duplicate_jobs(self) -> None:
        # Idempotency comes entirely from SchedulerService.schedule()'s own
        # find_active_by_logical_key() check -- this test just confirms
        # schedule_watch_party_reminder() doesn't defeat or bypass it.
        watch_party = make_watch_party(watch_party_id=7)

        await schedule_watch_party_reminder(self.scheduler_service, watch_party, guild_id=100)
        await schedule_watch_party_reminder(self.scheduler_service, watch_party, guild_id=100)

        self.assertEqual(len(self.repository.jobs), 1)

    async def test_returns_none_when_reminders_are_disabled_via_guild_config(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        guild_configuration_repository = GuildConfigurationRepository(
            Path(temp_dir.name) / "guild_configurations.json"
        )
        guild_configuration_repository.save(
            GuildConfiguration(
                guild_id=100,
                guild_name="Example Guild",
                notifications=NotificationsConfig(watch=WatchNotificationsConfig(enabled=False)),
            )
        )
        watch_party = make_watch_party(watch_party_id=7)

        job = await schedule_watch_party_reminder(
            self.scheduler_service,
            watch_party,
            guild_id=100,
            guild_configuration_repository=guild_configuration_repository,
        )

        self.assertIsNone(job)
        self.assertEqual(len(self.repository.jobs), 0)


class RescheduleWatchPartyReminderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = MemorySchedulerRepository()
        self.scheduler_service = SchedulerService(self.repository)

    async def test_cancels_the_old_job_and_schedules_a_new_one(self) -> None:
        original = make_watch_party(watch_party_id=7, scheduled_at=datetime.now(timezone.utc) + timedelta(days=1))
        original_job = await schedule_watch_party_reminder(self.scheduler_service, original, guild_id=100)

        rescheduled = original.with_changes(scheduled_at=datetime.now(timezone.utc) + timedelta(days=5))
        new_job = await reschedule_watch_party_reminder(self.scheduler_service, rescheduled, guild_id=100)

        self.assertIsNotNone(new_job)
        self.assertNotEqual(new_job.job_id, original_job.job_id)
        self.assertEqual(self.repository.jobs[original_job.job_id].status, JobStatus.CANCELLED)
        self.assertEqual(self.repository.jobs[new_job.job_id].status, JobStatus.PENDING)

    async def test_new_job_run_at_reflects_the_new_schedule(self) -> None:
        original = make_watch_party(watch_party_id=7, scheduled_at=datetime.now(timezone.utc) + timedelta(days=1))
        await schedule_watch_party_reminder(self.scheduler_service, original, guild_id=100)

        new_time = datetime(2026, 9, 1, 20, tzinfo=timezone.utc)
        rescheduled = original.with_changes(scheduled_at=new_time)
        new_job = await reschedule_watch_party_reminder(self.scheduler_service, rescheduled, guild_id=100)

        self.assertEqual(new_job.run_at, new_time - timedelta(hours=1))

    async def test_only_one_active_job_exists_after_rescheduling(self) -> None:
        original = make_watch_party(watch_party_id=7, scheduled_at=datetime.now(timezone.utc) + timedelta(days=1))
        await schedule_watch_party_reminder(self.scheduler_service, original, guild_id=100)

        rescheduled = original.with_changes(scheduled_at=datetime.now(timezone.utc) + timedelta(days=5))
        await reschedule_watch_party_reminder(self.scheduler_service, rescheduled, guild_id=100)

        active_jobs = [job for job in self.repository.jobs.values() if job.is_active]
        self.assertEqual(len(active_jobs), 1)

    async def test_reschedule_still_works_when_no_reminder_was_previously_scheduled(self) -> None:
        # e.g. reminders were disabled when the watch party was first
        # created -- cancel_by_logical_key() is a safe no-op here.
        watch_party = make_watch_party(watch_party_id=7)

        job = await reschedule_watch_party_reminder(self.scheduler_service, watch_party, guild_id=100)

        self.assertIsNotNone(job)
        self.assertEqual(len(self.repository.jobs), 1)

    async def test_returns_none_when_no_scheduler_service_is_given(self) -> None:
        watch_party = make_watch_party()

        job = await reschedule_watch_party_reminder(None, watch_party, guild_id=100)

        self.assertIsNone(job)


class CancelWatchPartyReminderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = MemorySchedulerRepository()
        self.scheduler_service = SchedulerService(self.repository)

    async def test_cancels_the_active_reminder_job(self) -> None:
        watch_party = make_watch_party(watch_party_id=7)
        scheduled_job = await schedule_watch_party_reminder(self.scheduler_service, watch_party, guild_id=100)

        cancelled = await cancel_watch_party_reminder(self.scheduler_service, watch_party.id)

        self.assertIsNotNone(cancelled)
        self.assertEqual(cancelled.job_id, scheduled_job.job_id)
        self.assertEqual(self.repository.jobs[scheduled_job.job_id].status, JobStatus.CANCELLED)

    async def test_no_active_job_is_a_safe_no_op(self) -> None:
        cancelled = await cancel_watch_party_reminder(self.scheduler_service, watch_party_id=999)

        self.assertIsNone(cancelled)

    async def test_cancelling_twice_is_safe(self) -> None:
        watch_party = make_watch_party(watch_party_id=7)
        await schedule_watch_party_reminder(self.scheduler_service, watch_party, guild_id=100)

        first = await cancel_watch_party_reminder(self.scheduler_service, watch_party.id)
        second = await cancel_watch_party_reminder(self.scheduler_service, watch_party.id)

        self.assertIsNotNone(first)
        self.assertIsNone(second)

    async def test_returns_none_when_no_scheduler_service_is_given(self) -> None:
        cancelled = await cancel_watch_party_reminder(None, watch_party_id=7)

        self.assertIsNone(cancelled)


if __name__ == "__main__":
    unittest.main()
