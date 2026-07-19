from datetime import datetime, timezone
import unittest

from watch_party_manager.scheduler.scheduled_job import JobStatus, ScheduledJob


class ScheduledJobTests(unittest.TestCase):
    def test_job_normalizes_times_to_utc(self) -> None:
        run_at = datetime(2026, 7, 19, 12, tzinfo=timezone.utc)
        job = ScheduledJob(
            guild_id=123,
            job_type=" close_vote ",
            logical_key=" close_vote:1 ",
            run_at=run_at,
        )

        self.assertEqual(job.job_type, "close_vote")
        self.assertEqual(job.logical_key, "close_vote:1")
        self.assertEqual(job.run_at.tzinfo, timezone.utc)

    def test_pending_and_running_jobs_are_active(self) -> None:
        job = ScheduledJob(
            guild_id=123,
            job_type="close_vote",
            logical_key="close_vote:1",
            run_at=datetime.now(timezone.utc),
        )

        self.assertTrue(job.is_active)
        self.assertTrue(job.with_changes(status=JobStatus.RUNNING).is_active)
        self.assertFalse(job.with_changes(status=JobStatus.COMPLETED).is_active)

    def test_job_rejects_naive_run_at(self) -> None:
        with self.assertRaises(ValueError):
            ScheduledJob(
                guild_id=123,
                job_type="close_vote",
                logical_key="close_vote:1",
                run_at=datetime(2026, 7, 19, 12),
            )

    def test_job_rejects_nonpositive_guild_id(self) -> None:
        with self.assertRaises(ValueError):
            ScheduledJob(
                guild_id=0,
                job_type="close_vote",
                logical_key="close_vote:1",
                run_at=datetime.now(timezone.utc),
            )


if __name__ == "__main__":
    unittest.main()
