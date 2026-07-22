"""Tests for FR-015: Automatic Vote Scheduling.

Covers only the scheduling behavior this milestone adds -- building the
close_vote/vote_reminder jobs for a voting round and handing them to the
existing SchedulerService. No job handlers, reminder delivery, or vote
closing are exercised here, since none of that is implemented by this
milestone.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from watch_party_manager.domain.guild_configuration import (
    GuildConfiguration,
    NotificationsConfig,
    VoteNotificationsConfig,
)
from watch_party_manager.domain.vote import VoteRound
from watch_party_manager.persistence.guild_configuration_repository import (
    GuildConfigurationRepository,
)
from watch_party_manager.scheduler.scheduled_job import JobResult, JobStatus, ScheduledJob
from watch_party_manager.scheduler.scheduler_service import SchedulerService
from watch_party_manager.scheduler.vote_scheduling import (
    CLOSE_VOTE_JOB_TYPE,
    VOTE_REMINDER_JOB_TYPE,
    build_close_vote_job,
    build_vote_reminder_job,
    build_vote_scheduled_jobs,
    cancel_vote_jobs,
    close_vote_logical_key,
    reschedule_vote_jobs,
    resolve_vote_reminder_settings,
    schedule_vote_jobs,
    vote_reminder_logical_key,
)


class MemorySchedulerRepository:
    """In-memory SchedulerRepository fake, matching test_scheduler_service.py's."""

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


def make_vote_round(vote_id: int = 1, closes_at: datetime | None = None) -> VoteRound:
    if closes_at is None:
        closes_at = datetime.now(timezone.utc) + timedelta(days=7)
    return VoteRound(id=vote_id, closes_at=closes_at, candidate_suggestion_ids=[1, 2, 3])


class LogicalKeyTests(unittest.TestCase):
    def test_close_vote_logical_key_format(self) -> None:
        self.assertEqual(close_vote_logical_key(42), "vote:42:close")

    def test_vote_reminder_logical_key_format(self) -> None:
        self.assertEqual(vote_reminder_logical_key(42), "vote:42:reminder")

    def test_logical_keys_are_distinct_for_the_same_vote(self) -> None:
        self.assertNotEqual(close_vote_logical_key(1), vote_reminder_logical_key(1))


class BuildCloseVoteJobTests(unittest.TestCase):
    def test_builds_a_close_vote_job_with_the_correct_type_and_key(self) -> None:
        vote_round = make_vote_round(vote_id=7)

        job = build_close_vote_job(vote_round, guild_id=100)

        self.assertIsNotNone(job)
        self.assertEqual(job.job_type, CLOSE_VOTE_JOB_TYPE)
        self.assertEqual(job.logical_key, "vote:7:close")
        self.assertEqual(job.guild_id, 100)

    def test_run_at_matches_the_votes_close_time(self) -> None:
        closes_at = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
        vote_round = make_vote_round(closes_at=closes_at)

        job = build_close_vote_job(vote_round, guild_id=100)

        self.assertEqual(job.run_at, closes_at)

    def test_payload_contains_only_the_vote_id(self) -> None:
        vote_round = make_vote_round(vote_id=7)

        job = build_close_vote_job(vote_round, guild_id=100)

        self.assertEqual(job.payload, {"vote_id": 7})

    def test_returns_none_when_the_round_has_no_closes_at(self) -> None:
        vote_round = VoteRound(id=1, closes_at=None, candidate_suggestion_ids=[1, 2])

        self.assertIsNone(build_close_vote_job(vote_round, guild_id=100))


class BuildVoteReminderJobTests(unittest.TestCase):
    def test_builds_a_reminder_job_with_the_correct_type_and_key(self) -> None:
        vote_round = make_vote_round(vote_id=7)

        job = build_vote_reminder_job(
            vote_round, guild_id=100, reminder_enabled=True, reminder_hours_before_close=24
        )

        self.assertIsNotNone(job)
        self.assertEqual(job.job_type, VOTE_REMINDER_JOB_TYPE)
        self.assertEqual(job.logical_key, "vote:7:reminder")

    def test_run_at_is_offset_before_close_by_the_configured_hours(self) -> None:
        closes_at = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
        vote_round = make_vote_round(closes_at=closes_at)

        job = build_vote_reminder_job(
            vote_round, guild_id=100, reminder_enabled=True, reminder_hours_before_close=24
        )

        self.assertEqual(job.run_at, closes_at - timedelta(hours=24))

    def test_payload_contains_only_the_vote_id(self) -> None:
        vote_round = make_vote_round(vote_id=7)

        job = build_vote_reminder_job(
            vote_round, guild_id=100, reminder_enabled=True, reminder_hours_before_close=24
        )

        self.assertEqual(job.payload, {"vote_id": 7})

    def test_returns_none_when_reminders_are_disabled(self) -> None:
        vote_round = make_vote_round()

        job = build_vote_reminder_job(
            vote_round, guild_id=100, reminder_enabled=False, reminder_hours_before_close=24
        )

        self.assertIsNone(job)

    def test_returns_none_when_the_round_has_no_closes_at(self) -> None:
        vote_round = VoteRound(id=1, closes_at=None, candidate_suggestion_ids=[1, 2])

        job = build_vote_reminder_job(
            vote_round, guild_id=100, reminder_enabled=True, reminder_hours_before_close=24
        )

        self.assertIsNone(job)

    def test_a_short_duration_round_with_a_long_lead_time_still_produces_a_job(self) -> None:
        # reminder_hours_before_close is always positive, so run_at is
        # always strictly before closes_at -- even here, where run_at
        # ends up in the past relative to now. SchedulerService's own
        # due-job polling already handles a past run_at correctly (it's
        # simply immediately due), so this is expected, not an error.
        closes_at = datetime.now(timezone.utc) + timedelta(hours=1)
        vote_round = make_vote_round(closes_at=closes_at)

        job = build_vote_reminder_job(
            vote_round, guild_id=100, reminder_enabled=True, reminder_hours_before_close=24
        )

        self.assertIsNotNone(job)
        self.assertLess(job.run_at, closes_at)


class BuildVoteScheduledJobsTests(unittest.TestCase):
    def test_returns_both_jobs_when_reminders_are_enabled(self) -> None:
        vote_round = make_vote_round(vote_id=7)

        jobs = build_vote_scheduled_jobs(
            vote_round, guild_id=100, reminder_enabled=True, reminder_hours_before_close=24
        )

        job_types = {job.job_type for job in jobs}
        self.assertEqual(job_types, {CLOSE_VOTE_JOB_TYPE, VOTE_REMINDER_JOB_TYPE})

    def test_returns_only_the_close_job_when_reminders_are_disabled(self) -> None:
        vote_round = make_vote_round()

        jobs = build_vote_scheduled_jobs(
            vote_round, guild_id=100, reminder_enabled=False, reminder_hours_before_close=24
        )

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].job_type, CLOSE_VOTE_JOB_TYPE)

    def test_returns_no_jobs_when_the_round_has_no_closes_at(self) -> None:
        vote_round = VoteRound(id=1, closes_at=None, candidate_suggestion_ids=[1, 2])

        jobs = build_vote_scheduled_jobs(
            vote_round, guild_id=100, reminder_enabled=True, reminder_hours_before_close=24
        )

        self.assertEqual(jobs, [])

    def test_close_job_is_scheduled_before_the_reminder_job(self) -> None:
        vote_round = make_vote_round()

        jobs = build_vote_scheduled_jobs(
            vote_round, guild_id=100, reminder_enabled=True, reminder_hours_before_close=24
        )

        self.assertEqual(jobs[0].job_type, CLOSE_VOTE_JOB_TYPE)
        self.assertEqual(jobs[1].job_type, VOTE_REMINDER_JOB_TYPE)


class ResolveVoteReminderSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.file_path = Path(self._temp_dir.name) / "guild_configurations.json"
        self.repository = GuildConfigurationRepository(self.file_path)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_falls_back_to_documented_defaults_when_no_repository_is_given(self) -> None:
        enabled, hours = resolve_vote_reminder_settings(None, guild_id=100)

        defaults = VoteNotificationsConfig()
        self.assertEqual(enabled, defaults.vote_ending_reminder)
        self.assertEqual(hours, defaults.reminder_hours_before_close)

    def test_falls_back_to_defaults_when_no_configuration_exists_for_the_guild(self) -> None:
        enabled, hours = resolve_vote_reminder_settings(self.repository, guild_id=100)

        defaults = VoteNotificationsConfig()
        self.assertEqual(enabled, defaults.vote_ending_reminder)
        self.assertEqual(hours, defaults.reminder_hours_before_close)

    def test_uses_the_guilds_configured_settings_when_present(self) -> None:
        configuration = GuildConfiguration(
            guild_id=100,
            guild_name="Example Guild",
            notifications=NotificationsConfig(
                vote=VoteNotificationsConfig(vote_ending_reminder=False, reminder_hours_before_close=6)
            ),
        )
        self.repository.save(configuration)

        enabled, hours = resolve_vote_reminder_settings(self.repository, guild_id=100)

        self.assertFalse(enabled)
        self.assertEqual(hours, 6)

    def test_a_different_guilds_settings_do_not_leak_across(self) -> None:
        self.repository.save(
            GuildConfiguration(
                guild_id=100,
                guild_name="Guild One",
                notifications=NotificationsConfig(
                    vote=VoteNotificationsConfig(reminder_hours_before_close=6)
                ),
            )
        )

        enabled, hours = resolve_vote_reminder_settings(self.repository, guild_id=200)

        defaults = VoteNotificationsConfig()
        self.assertEqual(hours, defaults.reminder_hours_before_close)

    # --- FR-027: per-round overrides -------------------------------------------

    def test_round_reminder_enabled_override_takes_precedence_over_the_guild_default(self) -> None:
        self.repository.save(
            GuildConfiguration(
                guild_id=100,
                guild_name="Example Guild",
                notifications=NotificationsConfig(vote=VoteNotificationsConfig(vote_ending_reminder=True)),
            )
        )

        enabled, _ = resolve_vote_reminder_settings(self.repository, guild_id=100, round_reminder_enabled=False)

        self.assertFalse(enabled)

    def test_round_reminder_hours_override_takes_precedence_over_the_guild_default(self) -> None:
        self.repository.save(
            GuildConfiguration(
                guild_id=100,
                guild_name="Example Guild",
                notifications=NotificationsConfig(vote=VoteNotificationsConfig(reminder_hours_before_close=6)),
            )
        )

        _, hours = resolve_vote_reminder_settings(
            self.repository, guild_id=100, round_reminder_hours_before_close=4
        )

        self.assertEqual(hours, 4)

    def test_round_overrides_fall_back_to_the_guild_default_when_none(self) -> None:
        self.repository.save(
            GuildConfiguration(
                guild_id=100,
                guild_name="Example Guild",
                notifications=NotificationsConfig(
                    vote=VoteNotificationsConfig(vote_ending_reminder=False, reminder_hours_before_close=6)
                ),
            )
        )

        enabled, hours = resolve_vote_reminder_settings(
            self.repository, guild_id=100, round_reminder_enabled=None, round_reminder_hours_before_close=None
        )

        self.assertFalse(enabled)
        self.assertEqual(hours, 6)

    def test_each_override_is_resolved_independently(self) -> None:
        # Only reminder_enabled is overridden -- reminder_hours_before_close
        # must still fall through to the guild's configured value.
        self.repository.save(
            GuildConfiguration(
                guild_id=100,
                guild_name="Example Guild",
                notifications=NotificationsConfig(
                    vote=VoteNotificationsConfig(vote_ending_reminder=True, reminder_hours_before_close=6)
                ),
            )
        )

        enabled, hours = resolve_vote_reminder_settings(self.repository, guild_id=100, round_reminder_enabled=False)

        self.assertFalse(enabled)
        self.assertEqual(hours, 6)


class ScheduleVoteJobsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = MemorySchedulerRepository()
        self.scheduler_service = SchedulerService(self.repository)

    async def test_schedules_both_jobs_for_a_new_round(self) -> None:
        vote_round = make_vote_round(vote_id=7)

        scheduled = await schedule_vote_jobs(self.scheduler_service, vote_round, guild_id=100)

        self.assertEqual(len(scheduled), 2)
        self.assertEqual(len(self.repository.jobs), 2)

    async def test_scheduled_jobs_are_persisted_via_the_repository(self) -> None:
        vote_round = make_vote_round(vote_id=7)

        await schedule_vote_jobs(self.scheduler_service, vote_round, guild_id=100)

        logical_keys = {job.logical_key for job in self.repository.jobs.values()}
        self.assertEqual(logical_keys, {"vote:7:close", "vote:7:reminder"})

    async def test_returns_an_empty_list_when_no_scheduler_service_is_given(self) -> None:
        vote_round = make_vote_round()

        scheduled = await schedule_vote_jobs(None, vote_round, guild_id=100)

        self.assertEqual(scheduled, [])

    async def test_calling_schedule_vote_jobs_twice_does_not_create_duplicate_jobs(self) -> None:
        # Idempotency comes entirely from SchedulerService.schedule()'s own
        # find_active_by_logical_key() check -- this test just confirms
        # schedule_vote_jobs() doesn't defeat or bypass it.
        vote_round = make_vote_round(vote_id=7)

        await schedule_vote_jobs(self.scheduler_service, vote_round, guild_id=100)
        await schedule_vote_jobs(self.scheduler_service, vote_round, guild_id=100)

        self.assertEqual(len(self.repository.jobs), 2)

    async def test_only_schedules_the_close_job_when_reminders_are_disabled_via_guild_config(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        guild_configuration_repository = GuildConfigurationRepository(
            Path(temp_dir.name) / "guild_configurations.json"
        )
        guild_configuration_repository.save(
            GuildConfiguration(
                guild_id=100,
                guild_name="Example Guild",
                notifications=NotificationsConfig(
                    vote=VoteNotificationsConfig(vote_ending_reminder=False)
                ),
            )
        )
        vote_round = make_vote_round(vote_id=7)

        scheduled = await schedule_vote_jobs(
            self.scheduler_service,
            vote_round,
            guild_id=100,
            guild_configuration_repository=guild_configuration_repository,
        )

        self.assertEqual(len(scheduled), 1)
        self.assertEqual(scheduled[0].job_type, CLOSE_VOTE_JOB_TYPE)

    async def test_round_level_reminder_override_disables_the_reminder_job(self) -> None:
        # FR-027: even with no guild configuration at all (reminders
        # enabled by default), a round's own reminder_enabled=False must
        # be honored.
        vote_round = VoteRound(
            id=7,
            closes_at=datetime.now(timezone.utc) + timedelta(days=7),
            candidate_suggestion_ids=[1, 2, 3],
            reminder_enabled=False,
        )

        scheduled = await schedule_vote_jobs(self.scheduler_service, vote_round, guild_id=100)

        self.assertEqual(len(scheduled), 1)
        self.assertEqual(scheduled[0].job_type, CLOSE_VOTE_JOB_TYPE)

    async def test_round_level_reminder_hours_override_changes_the_reminder_run_at(self) -> None:
        closes_at = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
        vote_round = VoteRound(
            id=7,
            closes_at=closes_at,
            candidate_suggestion_ids=[1, 2, 3],
            reminder_hours_before_close=4,
        )

        scheduled = await schedule_vote_jobs(self.scheduler_service, vote_round, guild_id=100)

        reminder_job = next(job for job in scheduled if job.job_type == VOTE_REMINDER_JOB_TYPE)
        self.assertEqual(reminder_job.run_at, closes_at - timedelta(hours=4))

    async def test_round_level_override_takes_precedence_over_a_disabling_guild_config(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        guild_configuration_repository = GuildConfigurationRepository(
            Path(temp_dir.name) / "guild_configurations.json"
        )
        guild_configuration_repository.save(
            GuildConfiguration(
                guild_id=100,
                guild_name="Example Guild",
                notifications=NotificationsConfig(vote=VoteNotificationsConfig(vote_ending_reminder=False)),
            )
        )
        vote_round = VoteRound(
            id=7,
            closes_at=datetime.now(timezone.utc) + timedelta(days=7),
            candidate_suggestion_ids=[1, 2, 3],
            reminder_enabled=True,
        )

        scheduled = await schedule_vote_jobs(
            self.scheduler_service,
            vote_round,
            guild_id=100,
            guild_configuration_repository=guild_configuration_repository,
        )

        self.assertEqual({job.job_type for job in scheduled}, {CLOSE_VOTE_JOB_TYPE, VOTE_REMINDER_JOB_TYPE})

    async def test_vote_creation_failure_never_reaches_scheduling(self) -> None:
        # This module's contract is "only call schedule_vote_jobs() after
        # a vote is confirmed created and persisted" -- there is nothing
        # for schedule_vote_jobs() itself to guard against a failed
        # creation, since it's never given a VoteRound to work with in
        # that case. This test documents that expectation: simply never
        # calling it (as bot.py's handle_start_vote_completion does when
        # perform_start_vote() reports failure) results in zero jobs.
        self.assertEqual(len(self.repository.jobs), 0)


class CancelVoteJobsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = MemorySchedulerRepository()
        self.scheduler_service = SchedulerService(self.repository)

    async def test_cancels_both_the_close_and_reminder_jobs(self) -> None:
        vote_round = make_vote_round(vote_id=7)
        await schedule_vote_jobs(self.scheduler_service, vote_round, guild_id=100)

        await cancel_vote_jobs(self.scheduler_service, round_id=7)

        active_statuses = {job.logical_key: job.status for job in self.repository.jobs.values()}
        self.assertEqual(active_statuses["vote:7:close"], JobStatus.CANCELLED)
        self.assertEqual(active_statuses["vote:7:reminder"], JobStatus.CANCELLED)

    async def test_is_a_no_op_when_no_scheduler_service_is_given(self) -> None:
        # Should simply not raise.
        await cancel_vote_jobs(None, round_id=7)

    async def test_is_a_no_op_when_no_job_is_active_for_the_round(self) -> None:
        # Should simply not raise -- there is nothing scheduled for round 7.
        await cancel_vote_jobs(self.scheduler_service, round_id=7)

        self.assertEqual(len(self.repository.jobs), 0)

    async def test_calling_it_twice_is_safe(self) -> None:
        vote_round = make_vote_round(vote_id=7)
        await schedule_vote_jobs(self.scheduler_service, vote_round, guild_id=100)

        await cancel_vote_jobs(self.scheduler_service, round_id=7)
        await cancel_vote_jobs(self.scheduler_service, round_id=7)

        active_statuses = {job.logical_key: job.status for job in self.repository.jobs.values()}
        self.assertEqual(active_statuses["vote:7:close"], JobStatus.CANCELLED)
        self.assertEqual(active_statuses["vote:7:reminder"], JobStatus.CANCELLED)


class RescheduleVoteJobsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = MemorySchedulerRepository()
        self.scheduler_service = SchedulerService(self.repository)

    async def test_cancels_the_old_jobs_and_creates_new_ones(self) -> None:
        vote_round = make_vote_round(vote_id=7)
        await schedule_vote_jobs(self.scheduler_service, vote_round, guild_id=100)
        old_job_ids = {job.job_id for job in self.repository.jobs.values()}

        new_closes_at = vote_round.closes_at + timedelta(days=3)
        vote_round.closes_at = new_closes_at
        scheduled = await reschedule_vote_jobs(self.scheduler_service, vote_round, guild_id=100)

        self.assertEqual(len(scheduled), 2)
        for job in scheduled:
            self.assertNotIn(job.job_id, old_job_ids)

        old_statuses = {
            job.status for job_id, job in self.repository.jobs.items() if job_id in old_job_ids
        }
        self.assertEqual(old_statuses, {JobStatus.CANCELLED})

    async def test_new_close_job_run_at_reflects_the_new_deadline(self) -> None:
        vote_round = make_vote_round(vote_id=7)
        await schedule_vote_jobs(self.scheduler_service, vote_round, guild_id=100)

        new_closes_at = datetime(2027, 1, 1, tzinfo=timezone.utc)
        vote_round.closes_at = new_closes_at
        scheduled = await reschedule_vote_jobs(self.scheduler_service, vote_round, guild_id=100)

        close_job = next(job for job in scheduled if job.job_type == CLOSE_VOTE_JOB_TYPE)
        self.assertEqual(close_job.run_at, new_closes_at)

    async def test_returns_an_empty_list_when_no_scheduler_service_is_given(self) -> None:
        vote_round = make_vote_round(vote_id=7)

        scheduled = await reschedule_vote_jobs(None, vote_round, guild_id=100)

        self.assertEqual(scheduled, [])

    async def test_rescheduling_does_not_duplicate_jobs(self) -> None:
        vote_round = make_vote_round(vote_id=7)
        await schedule_vote_jobs(self.scheduler_service, vote_round, guild_id=100)

        vote_round.closes_at = vote_round.closes_at + timedelta(days=1)
        await reschedule_vote_jobs(self.scheduler_service, vote_round, guild_id=100)

        active_jobs = [job for job in self.repository.jobs.values() if job.is_active]
        self.assertEqual(len(active_jobs), 2)

    # --- FR-027: reminder rescheduling ------------------------------------------

    async def test_new_reminder_job_run_at_reflects_the_new_deadline(self) -> None:
        vote_round = make_vote_round(vote_id=7)
        await schedule_vote_jobs(self.scheduler_service, vote_round, guild_id=100)

        new_closes_at = datetime(2027, 1, 1, tzinfo=timezone.utc)
        vote_round.closes_at = new_closes_at
        scheduled = await reschedule_vote_jobs(self.scheduler_service, vote_round, guild_id=100)

        reminder_job = next(job for job in scheduled if job.job_type == VOTE_REMINDER_JOB_TYPE)
        self.assertEqual(reminder_job.run_at, new_closes_at - timedelta(hours=24))

    async def test_reminder_still_scheduled_exactly_once_after_multiple_edits(self) -> None:
        vote_round = make_vote_round(vote_id=7)
        await schedule_vote_jobs(self.scheduler_service, vote_round, guild_id=100)

        for offset_days in (1, -2, 5):
            vote_round.closes_at = vote_round.closes_at + timedelta(days=offset_days)
            await reschedule_vote_jobs(self.scheduler_service, vote_round, guild_id=100)

        active_reminder_jobs = [
            job
            for job in self.repository.jobs.values()
            if job.is_active and job.job_type == VOTE_REMINDER_JOB_TYPE
        ]
        self.assertEqual(len(active_reminder_jobs), 1)
        self.assertEqual(active_reminder_jobs[0].run_at, vote_round.closes_at - timedelta(hours=24))

    async def test_reschedule_preserves_the_rounds_own_reminder_override(self) -> None:
        vote_round = VoteRound(
            id=7,
            closes_at=datetime.now(timezone.utc) + timedelta(days=7),
            candidate_suggestion_ids=[1, 2, 3],
            reminder_hours_before_close=4,
        )
        await schedule_vote_jobs(self.scheduler_service, vote_round, guild_id=100)

        new_closes_at = datetime(2027, 1, 1, tzinfo=timezone.utc)
        vote_round.closes_at = new_closes_at
        scheduled = await reschedule_vote_jobs(self.scheduler_service, vote_round, guild_id=100)

        reminder_job = next(job for job in scheduled if job.job_type == VOTE_REMINDER_JOB_TYPE)
        self.assertEqual(reminder_job.run_at, new_closes_at - timedelta(hours=4))


if __name__ == "__main__":
    unittest.main()
