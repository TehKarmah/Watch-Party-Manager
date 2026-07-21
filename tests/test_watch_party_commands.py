"""Tests for FR-021: Watch Party Scheduling Commands.

Covers the Discord command layer added on top of the FR-020 WatchParty
foundation: scheduling, rescheduling, cancelling, and viewing a watch
party, including permission enforcement and end-to-end reminder-job
scheduling/replacement/removal through a real SchedulerService.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import (
    build_watch_party_status_text,
    handle_cancel_watch_party_completion,
    handle_reschedule_watch_party_completion,
    handle_schedule_watch_party_completion,
    parse_watch_party_schedule_time,
    perform_cancel_watch_party,
    perform_reschedule_watch_party,
    perform_schedule_watch_party,
    perform_watch_party_status,
)
from watch_party_manager.domain.watch_party import WatchParty, WatchPartyStatus
from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.watch_party_repository import JsonWatchPartyRepository
from watch_party_manager.scheduler.scheduled_job import JobResult, JobStatus, ScheduledJob
from watch_party_manager.scheduler.scheduler_service import SchedulerService
from watch_party_manager.scheduler.watch_party_scheduling import watch_party_reminder_logical_key
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.watch_party_service import WatchPartyService

WASH_CREW_ROLE_ID = 999


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, user_id: int, roles=()) -> None:
        self.id = user_id
        self.roles = list(roles)


class FakeResponse:
    def __init__(self) -> None:
        self.sent_message = None
        self.sent_ephemeral = None

    async def send_message(self, content, ephemeral: bool = False) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral


class FakeInteraction:
    def __init__(self, user_id: int = 1, is_wash_crew: bool = True, guild_id=100, channel_id=200) -> None:
        roles = [FakeRole(WASH_CREW_ROLE_ID)] if is_wash_crew else [FakeRole(1)]
        self.user = FakeMember(user_id, roles=roles)
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.response = FakeResponse()


class MemorySchedulerRepository:
    """In-memory SchedulerRepository fake, matching test_watch_party_scheduling.py's."""

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


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ParseWatchPartyScheduleTimeTests(unittest.TestCase):
    def test_parses_a_space_separated_date_and_time(self) -> None:
        parsed = parse_watch_party_schedule_time("2026-08-01 20:00")

        self.assertEqual(parsed, datetime(2026, 8, 1, 20, 0, tzinfo=timezone.utc))

    def test_parses_an_iso_t_separated_date_and_time(self) -> None:
        parsed = parse_watch_party_schedule_time("2026-08-01T20:00:00")

        self.assertEqual(parsed, datetime(2026, 8, 1, 20, 0, tzinfo=timezone.utc))

    def test_assumes_utc_when_no_offset_is_given(self) -> None:
        parsed = parse_watch_party_schedule_time("2026-08-01 20:00")

        self.assertEqual(parsed.tzinfo, timezone.utc)

    def test_converts_an_explicit_offset_to_utc(self) -> None:
        parsed = parse_watch_party_schedule_time("2026-08-01T20:00:00-05:00")

        self.assertEqual(parsed, datetime(2026, 8, 2, 1, 0, tzinfo=timezone.utc))

    def test_rejects_blank_input(self) -> None:
        with self.assertRaises(ValueError):
            parse_watch_party_schedule_time("   ")

    def test_rejects_unparseable_input(self) -> None:
        with self.assertRaises(ValueError):
            parse_watch_party_schedule_time("next friday at 8")


class WatchPartyCommandTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.watch_party_service = WatchPartyService(
            self.suggestion_service, repository=JsonWatchPartyRepository(root / "watch_parties.json")
        )
        self.matrix = self.suggestion_service.suggest(
            "The Matrix", imdb_url="https://www.imdb.com/title/tt0133093/"
        ).watch_item

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _wash_crew_member(self) -> FakeMember:
        return FakeMember(1, roles=[FakeRole(WASH_CREW_ROLE_ID)])

    def _regular_member(self) -> FakeMember:
        return FakeMember(1, roles=[FakeRole(1)])


class PerformScheduleWatchPartyTests(WatchPartyCommandTestCase):
    def test_schedules_successfully(self) -> None:
        message, ephemeral, watch_party = perform_schedule_watch_party(
            self.watch_party_service,
            self.suggestion_service,
            self._wash_crew_member(),
            WASH_CREW_ROLE_ID,
            guild_id=100,
            channel_id=200,
            watch_item_id=self.matrix.id,
            when="2026-08-01 20:00",
        )

        self.assertFalse(ephemeral)
        self.assertIsNotNone(watch_party)
        self.assertIn("The Matrix", message)
        self.assertEqual(watch_party.watch_item_id, self.matrix.id)
        self.assertEqual(watch_party.guild_id, 100)
        self.assertEqual(watch_party.channel_id, 200)

    def test_rejects_when_wash_crew_role_is_unconfigured(self) -> None:
        message, ephemeral, watch_party = perform_schedule_watch_party(
            self.watch_party_service,
            self.suggestion_service,
            self._wash_crew_member(),
            None,
            guild_id=100,
            channel_id=200,
            watch_item_id=self.matrix.id,
            when="2026-08-01 20:00",
        )

        self.assertTrue(ephemeral)
        self.assertIsNone(watch_party)

    def test_rejects_a_non_wash_crew_member(self) -> None:
        message, ephemeral, watch_party = perform_schedule_watch_party(
            self.watch_party_service,
            self.suggestion_service,
            self._regular_member(),
            WASH_CREW_ROLE_ID,
            guild_id=100,
            channel_id=200,
            watch_item_id=self.matrix.id,
            when="2026-08-01 20:00",
        )

        self.assertTrue(ephemeral)
        self.assertIsNone(watch_party)
        self.assertEqual(self.watch_party_service.get_current_watch_party(), None)

    def test_rejects_outside_a_guild(self) -> None:
        message, ephemeral, watch_party = perform_schedule_watch_party(
            self.watch_party_service,
            self.suggestion_service,
            self._wash_crew_member(),
            WASH_CREW_ROLE_ID,
            guild_id=None,
            channel_id=200,
            watch_item_id=self.matrix.id,
            when="2026-08-01 20:00",
        )

        self.assertTrue(ephemeral)
        self.assertIsNone(watch_party)

    def test_rejects_an_invalid_when_value(self) -> None:
        message, ephemeral, watch_party = perform_schedule_watch_party(
            self.watch_party_service,
            self.suggestion_service,
            self._wash_crew_member(),
            WASH_CREW_ROLE_ID,
            guild_id=100,
            channel_id=200,
            watch_item_id=self.matrix.id,
            when="not a date",
        )

        self.assertTrue(ephemeral)
        self.assertIsNone(watch_party)

    def test_rejects_a_nonexistent_watch_item(self) -> None:
        message, ephemeral, watch_party = perform_schedule_watch_party(
            self.watch_party_service,
            self.suggestion_service,
            self._wash_crew_member(),
            WASH_CREW_ROLE_ID,
            guild_id=100,
            channel_id=200,
            watch_item_id=999,
            when="2026-08-01 20:00",
        )

        self.assertTrue(ephemeral)
        self.assertIsNone(watch_party)


class PerformRescheduleWatchPartyTests(WatchPartyCommandTestCase):
    def _scheduled(self) -> WatchParty:
        return self.watch_party_service.schedule_watch_party(
            watch_item_id=self.matrix.id,
            scheduled_at=utc_now() + timedelta(days=1),
            guild_id=100,
            channel_id=200,
        ).watch_party

    def test_reschedules_successfully(self) -> None:
        watch_party = self._scheduled()

        message, ephemeral, updated = perform_reschedule_watch_party(
            self.watch_party_service, self._wash_crew_member(), WASH_CREW_ROLE_ID, watch_party.id, "2026-09-01 20:00"
        )

        self.assertFalse(ephemeral)
        self.assertEqual(updated.scheduled_at, datetime(2026, 9, 1, 20, 0, tzinfo=timezone.utc))
        self.assertEqual(updated.id, watch_party.id)

    def test_rejects_a_non_wash_crew_member(self) -> None:
        watch_party = self._scheduled()

        message, ephemeral, updated = perform_reschedule_watch_party(
            self.watch_party_service, self._regular_member(), WASH_CREW_ROLE_ID, watch_party.id, "2026-09-01 20:00"
        )

        self.assertTrue(ephemeral)
        self.assertIsNone(updated)

    def test_graceful_for_a_nonexistent_watch_party(self) -> None:
        message, ephemeral, updated = perform_reschedule_watch_party(
            self.watch_party_service, self._wash_crew_member(), WASH_CREW_ROLE_ID, 999, "2026-09-01 20:00"
        )

        self.assertTrue(ephemeral)
        self.assertIsNone(updated)
        self.assertIn("doesn't exist", message)

    def test_rejects_an_invalid_when_value(self) -> None:
        watch_party = self._scheduled()

        message, ephemeral, updated = perform_reschedule_watch_party(
            self.watch_party_service, self._wash_crew_member(), WASH_CREW_ROLE_ID, watch_party.id, "not a date"
        )

        self.assertTrue(ephemeral)
        self.assertIsNone(updated)


class PerformCancelWatchPartyTests(WatchPartyCommandTestCase):
    def _scheduled(self) -> WatchParty:
        return self.watch_party_service.schedule_watch_party(
            watch_item_id=self.matrix.id,
            scheduled_at=utc_now() + timedelta(days=1),
            guild_id=100,
            channel_id=200,
        ).watch_party

    def test_cancels_successfully(self) -> None:
        watch_party = self._scheduled()

        message, ephemeral = perform_cancel_watch_party(
            self.watch_party_service, self._wash_crew_member(), WASH_CREW_ROLE_ID, watch_party.id
        )

        self.assertFalse(ephemeral)
        self.assertEqual(
            self.watch_party_service.get_watch_party(watch_party.id).status, WatchPartyStatus.CANCELLED
        )

    def test_rejects_a_non_wash_crew_member(self) -> None:
        watch_party = self._scheduled()

        message, ephemeral = perform_cancel_watch_party(
            self.watch_party_service, self._regular_member(), WASH_CREW_ROLE_ID, watch_party.id
        )

        self.assertTrue(ephemeral)
        self.assertEqual(
            self.watch_party_service.get_watch_party(watch_party.id).status, WatchPartyStatus.SCHEDULED
        )

    def test_graceful_for_a_nonexistent_watch_party(self) -> None:
        message, ephemeral = perform_cancel_watch_party(
            self.watch_party_service, self._wash_crew_member(), WASH_CREW_ROLE_ID, 999
        )

        self.assertTrue(ephemeral)
        self.assertIn("doesn't exist", message)

    def test_graceful_for_an_already_cancelled_watch_party(self) -> None:
        watch_party = self._scheduled()
        perform_cancel_watch_party(self.watch_party_service, self._wash_crew_member(), WASH_CREW_ROLE_ID, watch_party.id)

        message, ephemeral = perform_cancel_watch_party(
            self.watch_party_service, self._wash_crew_member(), WASH_CREW_ROLE_ID, watch_party.id
        )

        self.assertTrue(ephemeral)
        self.assertIn("already cancelled", message)


class BuildWatchPartyStatusTextTests(unittest.TestCase):
    def _watch_party(self, watch_party_id=1, scheduled_at=None, status=WatchPartyStatus.SCHEDULED):
        if scheduled_at is None:
            scheduled_at = datetime(2026, 8, 1, 20, 0, tzinfo=timezone.utc)
        return WatchParty(id=watch_party_id, watch_item_id=1, scheduled_at=scheduled_at, guild_id=100, status=status)

    def _watch_item(self, title="The Matrix", imdb_url=None):
        from watch_party_manager.domain.watch_item import MediaType, MetadataProvider, WatchItem

        metadata_ids = {MetadataProvider.IMDB: imdb_url} if imdb_url else {}
        return WatchItem(title=title, media_type=MediaType.MOVIE, metadata_ids=metadata_ids)

    def test_includes_the_movie_title(self) -> None:
        text = build_watch_party_status_text(self._watch_party(), self._watch_item(title="The Matrix"))

        self.assertIn("The Matrix", text)

    def test_includes_the_discord_native_timestamp(self) -> None:
        scheduled_at = datetime(2026, 8, 1, 20, 0, tzinfo=timezone.utc)
        unix_timestamp = int(scheduled_at.timestamp())

        text = build_watch_party_status_text(self._watch_party(scheduled_at=scheduled_at), self._watch_item())

        self.assertIn(f"<t:{unix_timestamp}:F>", text)
        self.assertIn(f"<t:{unix_timestamp}:R>", text)

    def test_includes_the_imdb_link_when_available(self) -> None:
        text = build_watch_party_status_text(
            self._watch_party(), self._watch_item(imdb_url="https://www.imdb.com/title/tt0133093/")
        )

        self.assertIn("https://www.imdb.com/title/tt0133093/", text)

    def test_omits_imdb_link_when_not_available(self) -> None:
        text = build_watch_party_status_text(self._watch_party(), self._watch_item())

        self.assertNotIn("IMDb", text)

    def test_includes_the_current_status(self) -> None:
        text = build_watch_party_status_text(
            self._watch_party(status=WatchPartyStatus.CANCELLED), self._watch_item()
        )

        self.assertIn("Cancelled", text)

    def test_falls_back_when_the_watch_item_is_unresolvable(self) -> None:
        text = build_watch_party_status_text(self._watch_party(watch_party_id=7), None)

        self.assertIn("Watch item #1", text)


class PerformWatchPartyStatusTests(WatchPartyCommandTestCase):
    def test_reports_no_watch_party_scheduled(self) -> None:
        message = perform_watch_party_status(self.watch_party_service, self.suggestion_service)

        self.assertEqual(message, "No watch party is currently scheduled.")

    def test_reports_the_soonest_upcoming_watch_party(self) -> None:
        later = self.watch_party_service.schedule_watch_party(
            watch_item_id=self.matrix.id, scheduled_at=utc_now() + timedelta(days=10), guild_id=100
        ).watch_party
        sooner = self.watch_party_service.schedule_watch_party(
            watch_item_id=self.matrix.id, scheduled_at=utc_now() + timedelta(days=1), guild_id=100
        ).watch_party

        message = perform_watch_party_status(self.watch_party_service, self.suggestion_service)

        self.assertIn(f"Watch Party #{sooner.id}", message)
        self.assertNotIn(f"Watch Party #{later.id}", message)

    def test_ignores_cancelled_watch_parties(self) -> None:
        watch_party = self.watch_party_service.schedule_watch_party(
            watch_item_id=self.matrix.id, scheduled_at=utc_now() + timedelta(days=1), guild_id=100
        ).watch_party
        self.watch_party_service.cancel_watch_party(watch_party.id)

        message = perform_watch_party_status(self.watch_party_service, self.suggestion_service)

        self.assertEqual(message, "No watch party is currently scheduled.")


class HandleScheduleWatchPartyCompletionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.watch_party_service = WatchPartyService(
            self.suggestion_service, repository=JsonWatchPartyRepository(root / "watch_parties.json")
        )
        self.matrix = self.suggestion_service.suggest("The Matrix").watch_item
        self.scheduler_repository = MemorySchedulerRepository()
        self.scheduler_service = SchedulerService(self.scheduler_repository)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    async def test_schedules_the_watch_party_and_its_reminder_job(self) -> None:
        interaction = FakeInteraction(is_wash_crew=True)

        await handle_schedule_watch_party_completion(
            interaction,
            watch_party_service=self.watch_party_service,
            suggestion_service=self.suggestion_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            watch_item_id=self.matrix.id,
            when="2026-08-01 20:00",
            scheduler_service=self.scheduler_service,
        )

        self.assertFalse(interaction.response.sent_ephemeral)
        watch_party = self.watch_party_service.get_current_watch_party()
        self.assertIsNotNone(watch_party)
        logical_key = watch_party_reminder_logical_key(watch_party.id)
        self.assertEqual(len(self.scheduler_repository.jobs), 1)
        job = next(iter(self.scheduler_repository.jobs.values()))
        self.assertEqual(job.logical_key, logical_key)

    async def test_permission_failure_schedules_no_reminder_job(self) -> None:
        interaction = FakeInteraction(is_wash_crew=False)

        await handle_schedule_watch_party_completion(
            interaction,
            watch_party_service=self.watch_party_service,
            suggestion_service=self.suggestion_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            watch_item_id=self.matrix.id,
            when="2026-08-01 20:00",
            scheduler_service=self.scheduler_service,
        )

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertEqual(len(self.scheduler_repository.jobs), 0)

    async def test_works_without_a_scheduler_service(self) -> None:
        # scheduler_service defaults to None -- confirms scheduling is
        # simply skipped rather than raising.
        interaction = FakeInteraction(is_wash_crew=True)

        await handle_schedule_watch_party_completion(
            interaction,
            watch_party_service=self.watch_party_service,
            suggestion_service=self.suggestion_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            watch_item_id=self.matrix.id,
            when="2026-08-01 20:00",
        )

        self.assertFalse(interaction.response.sent_ephemeral)


class HandleRescheduleWatchPartyCompletionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.watch_party_service = WatchPartyService(
            self.suggestion_service, repository=JsonWatchPartyRepository(root / "watch_parties.json")
        )
        self.matrix = self.suggestion_service.suggest("The Matrix").watch_item
        self.scheduler_repository = MemorySchedulerRepository()
        self.scheduler_service = SchedulerService(self.scheduler_repository)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    async def _schedule(self) -> WatchParty:
        interaction = FakeInteraction(is_wash_crew=True)
        await handle_schedule_watch_party_completion(
            interaction,
            watch_party_service=self.watch_party_service,
            suggestion_service=self.suggestion_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            watch_item_id=self.matrix.id,
            when="2026-08-01 20:00",
            scheduler_service=self.scheduler_service,
        )
        return self.watch_party_service.get_current_watch_party()

    async def test_replaces_the_reminder_job_after_rescheduling(self) -> None:
        watch_party = await self._schedule()
        original_job = next(iter(self.scheduler_repository.jobs.values()))
        interaction = FakeInteraction(is_wash_crew=True)

        await handle_reschedule_watch_party_completion(
            interaction,
            watch_party_service=self.watch_party_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            watch_party_id=watch_party.id,
            when="2026-09-01 20:00",
            scheduler_service=self.scheduler_service,
        )

        self.assertFalse(interaction.response.sent_ephemeral)
        self.assertEqual(self.scheduler_repository.jobs[original_job.job_id].status, JobStatus.CANCELLED)
        active_jobs = [job for job in self.scheduler_repository.jobs.values() if job.is_active]
        self.assertEqual(len(active_jobs), 1)
        self.assertEqual(
            active_jobs[0].run_at,
            datetime(2026, 9, 1, 19, 0, tzinfo=timezone.utc),  # 1 hour before the new time
        )

    async def test_updates_the_watch_partys_scheduled_time(self) -> None:
        watch_party = await self._schedule()
        interaction = FakeInteraction(is_wash_crew=True)

        await handle_reschedule_watch_party_completion(
            interaction,
            watch_party_service=self.watch_party_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            watch_party_id=watch_party.id,
            when="2026-09-01 20:00",
            scheduler_service=self.scheduler_service,
        )

        updated = self.watch_party_service.get_watch_party(watch_party.id)
        self.assertEqual(updated.scheduled_at, datetime(2026, 9, 1, 20, 0, tzinfo=timezone.utc))

    async def test_preserves_the_watch_partys_identity(self) -> None:
        watch_party = await self._schedule()
        interaction = FakeInteraction(is_wash_crew=True)

        await handle_reschedule_watch_party_completion(
            interaction,
            watch_party_service=self.watch_party_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            watch_party_id=watch_party.id,
            when="2026-09-01 20:00",
            scheduler_service=self.scheduler_service,
        )

        updated = self.watch_party_service.get_watch_party(watch_party.id)
        self.assertEqual(updated.id, watch_party.id)
        self.assertEqual(updated.watch_item_id, watch_party.watch_item_id)

    async def test_permission_failure_does_not_touch_the_reminder_job(self) -> None:
        watch_party = await self._schedule()
        original_job = next(iter(self.scheduler_repository.jobs.values()))
        interaction = FakeInteraction(is_wash_crew=False)

        await handle_reschedule_watch_party_completion(
            interaction,
            watch_party_service=self.watch_party_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            watch_party_id=watch_party.id,
            when="2026-09-01 20:00",
            scheduler_service=self.scheduler_service,
        )

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertEqual(self.scheduler_repository.jobs[original_job.job_id].status, JobStatus.PENDING)


class HandleCancelWatchPartyCompletionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.watch_party_service = WatchPartyService(
            self.suggestion_service, repository=JsonWatchPartyRepository(root / "watch_parties.json")
        )
        self.matrix = self.suggestion_service.suggest("The Matrix").watch_item
        self.scheduler_repository = MemorySchedulerRepository()
        self.scheduler_service = SchedulerService(self.scheduler_repository)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    async def _schedule(self) -> WatchParty:
        interaction = FakeInteraction(is_wash_crew=True)
        await handle_schedule_watch_party_completion(
            interaction,
            watch_party_service=self.watch_party_service,
            suggestion_service=self.suggestion_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            watch_item_id=self.matrix.id,
            when="2026-08-01 20:00",
            scheduler_service=self.scheduler_service,
        )
        return self.watch_party_service.get_current_watch_party()

    async def test_marks_the_watch_party_cancelled(self) -> None:
        watch_party = await self._schedule()
        interaction = FakeInteraction(is_wash_crew=True)

        await handle_cancel_watch_party_completion(
            interaction,
            watch_party_service=self.watch_party_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            watch_party_id=watch_party.id,
            scheduler_service=self.scheduler_service,
        )

        self.assertFalse(interaction.response.sent_ephemeral)
        self.assertEqual(
            self.watch_party_service.get_watch_party(watch_party.id).status, WatchPartyStatus.CANCELLED
        )

    async def test_removes_the_pending_reminder_job(self) -> None:
        watch_party = await self._schedule()
        job = next(iter(self.scheduler_repository.jobs.values()))
        interaction = FakeInteraction(is_wash_crew=True)

        await handle_cancel_watch_party_completion(
            interaction,
            watch_party_service=self.watch_party_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            watch_party_id=watch_party.id,
            scheduler_service=self.scheduler_service,
        )

        self.assertEqual(self.scheduler_repository.jobs[job.job_id].status, JobStatus.CANCELLED)

    async def test_preserves_historical_data(self) -> None:
        # Cancelling must never delete the record -- only flip its status.
        watch_party = await self._schedule()
        interaction = FakeInteraction(is_wash_crew=True)

        await handle_cancel_watch_party_completion(
            interaction,
            watch_party_service=self.watch_party_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            watch_party_id=watch_party.id,
            scheduler_service=self.scheduler_service,
        )

        self.assertIsNotNone(self.watch_party_service.get_watch_party(watch_party.id))

    async def test_permission_failure_does_not_cancel_the_reminder_job(self) -> None:
        watch_party = await self._schedule()
        job = next(iter(self.scheduler_repository.jobs.values()))
        interaction = FakeInteraction(is_wash_crew=False)

        await handle_cancel_watch_party_completion(
            interaction,
            watch_party_service=self.watch_party_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            watch_party_id=watch_party.id,
            scheduler_service=self.scheduler_service,
        )

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertEqual(self.scheduler_repository.jobs[job.job_id].status, JobStatus.PENDING)
        self.assertEqual(
            self.watch_party_service.get_watch_party(watch_party.id).status, WatchPartyStatus.SCHEDULED
        )

    async def test_graceful_for_a_nonexistent_watch_party(self) -> None:
        interaction = FakeInteraction(is_wash_crew=True)

        await handle_cancel_watch_party_completion(
            interaction,
            watch_party_service=self.watch_party_service,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            watch_party_id=999,
            scheduler_service=self.scheduler_service,
        )

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIn("doesn't exist", interaction.response.sent_message)


if __name__ == "__main__":
    unittest.main()
