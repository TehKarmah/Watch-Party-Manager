"""Tests for Discord Scheduled Events integration's Event Synchronization:
reacting to a linked Discord Scheduled Event being edited, cancelled,
completed, or deleted directly in Discord, keeping WASH's own watch
party record from silently diverging.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import discord

from watch_party_manager.bot import (
    handle_discord_scheduled_event_delete,
    handle_discord_scheduled_event_update,
)
from watch_party_manager.domain.watch_item import WatchItemStatus
from watch_party_manager.domain.watch_party import WatchPartyStatus
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.watch_party_repository import JsonWatchPartyRepository
from watch_party_manager.scheduler.scheduled_job import JobResult, JobStatus, ScheduledJob
from watch_party_manager.scheduler.scheduler_service import SchedulerService
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.watch_party_completion_service import WatchPartyCompletionService
from watch_party_manager.services.watch_party_service import WatchPartyService

GUILD_ID = 100


class FakeAnnouncementChannel:
    def __init__(self) -> None:
        self.sent_messages: list = []

    async def send(self, content) -> None:
        self.sent_messages.append(content)


class FakeSchedulerHost:
    def __init__(self, scheduler_service) -> None:
        self.scheduler_service = scheduler_service


class MemorySchedulerRepository:
    """In-memory SchedulerRepository fake, matching other test files' own."""

    def __init__(self) -> None:
        self.jobs: dict[str, ScheduledJob] = {}

    async def add(self, job: ScheduledJob) -> ScheduledJob:
        self.jobs[job.job_id] = job
        return job

    async def get_due(self, now: datetime, *, limit: int = 100) -> list[ScheduledJob]:
        return [j for j in self.jobs.values() if j.status is JobStatus.PENDING and j.run_at <= now][:limit]

    async def claim(self, job_id: str, started_at: datetime):
        job = self.jobs[job_id]
        if job.status is not JobStatus.PENDING:
            return None
        claimed = job.with_changes(status=JobStatus.RUNNING, started_at=started_at, attempt_count=job.attempt_count + 1)
        self.jobs[job_id] = claimed
        return claimed

    async def complete(self, job_id: str, completed_at: datetime, result: JobResult) -> ScheduledJob:
        updated = self.jobs[job_id].with_changes(status=JobStatus.COMPLETED, completed_at=completed_at, result=result, last_error=None)
        self.jobs[job_id] = updated
        return updated

    async def retry(self, job_id: str, run_at: datetime, error: str) -> ScheduledJob:
        updated = self.jobs[job_id].with_changes(status=JobStatus.PENDING, run_at=run_at, last_error=error)
        self.jobs[job_id] = updated
        return updated

    async def fail(self, job_id: str, completed_at: datetime, error: str) -> ScheduledJob:
        updated = self.jobs[job_id].with_changes(status=JobStatus.FAILED, completed_at=completed_at, last_error=error)
        self.jobs[job_id] = updated
        return updated

    async def cancel(self, job_id: str, completed_at: datetime) -> ScheduledJob:
        updated = self.jobs[job_id].with_changes(status=JobStatus.CANCELLED, completed_at=completed_at, result=JobResult.CANCELLED)
        self.jobs[job_id] = updated
        return updated

    async def find_active_by_logical_key(self, logical_key: str):
        return next((j for j in self.jobs.values() if j.logical_key == logical_key and j.is_active), None)


class FakeBot:
    def __init__(self, suggestion_service, watch_party_service, scheduler_service) -> None:
        self.suggestion_service = suggestion_service
        self.watch_party_service = watch_party_service
        self.watch_party_completion_service = WatchPartyCompletionService(watch_party_service, suggestion_service)
        self.guild_configuration_repository = None
        self.scheduler_host = FakeSchedulerHost(scheduler_service)
        self.announcement_channel = FakeAnnouncementChannel()

    def get_channel(self, channel_id):
        return self.announcement_channel

    async def fetch_channel(self, channel_id):
        return self.announcement_channel


class FakeScheduledEvent:
    def __init__(self, event_id, *, status, start_time, end_time) -> None:
        self.id = event_id
        self.status = status
        self.start_time = start_time
        self.end_time = end_time


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DiscordScheduledEventSyncTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.watch_party_service = WatchPartyService(
            self.suggestion_service, repository=JsonWatchPartyRepository(root / "watch_parties.json")
        )
        self.scheduler_repository = MemorySchedulerRepository()
        self.scheduler_service = SchedulerService(self.scheduler_repository)
        self.bot = FakeBot(self.suggestion_service, self.watch_party_service, self.scheduler_service)
        self.matrix = self.suggestion_service.suggest("The Matrix").watch_item

    def _schedule(self, *, discord_event_id=555, duration_minutes=90, scheduled_at=None):
        scheduled_at = scheduled_at or (utc_now() + timedelta(days=1))
        watch_party = self.watch_party_service.schedule_watch_party(
            watch_item_id=self.matrix.id,
            scheduled_at=scheduled_at,
            guild_id=GUILD_ID,
            channel_id=777,
            duration_minutes=duration_minutes,
        ).watch_party
        self.watch_party_service.set_discord_event_id(watch_party.id, discord_event_id)
        return self.watch_party_service.get_watch_party(watch_party.id)


class OnScheduledEventUpdateCompletionTests(DiscordScheduledEventSyncTestCase):
    async def test_completed_status_marks_the_suggestion_watched(self) -> None:
        watch_party = self._schedule()
        event = FakeScheduledEvent(
            watch_party.discord_event_id,
            status=discord.EventStatus.completed,
            start_time=watch_party.scheduled_at,
            end_time=watch_party.ends_at,
        )

        await handle_discord_scheduled_event_update(self.bot, event, event)

        updated_item = self.suggestion_service.get_suggestion(self.matrix.id)
        self.assertEqual(updated_item.status, WatchItemStatus.WATCHED)
        self.assertEqual(
            self.watch_party_service.get_watch_party(watch_party.id).status, WatchPartyStatus.COMPLETED
        )

    async def test_completed_status_is_idempotent(self) -> None:
        watch_party = self._schedule()
        event = FakeScheduledEvent(
            watch_party.discord_event_id,
            status=discord.EventStatus.completed,
            start_time=watch_party.scheduled_at,
            end_time=watch_party.ends_at,
        )
        await handle_discord_scheduled_event_update(self.bot, event, event)

        await handle_discord_scheduled_event_update(self.bot, event, event)  # must not raise or double-post

        self.assertEqual(len(self.bot.announcement_channel.sent_messages), 1)

    async def test_completed_status_posts_a_completion_announcement(self) -> None:
        watch_party = self._schedule()
        event = FakeScheduledEvent(
            watch_party.discord_event_id,
            status=discord.EventStatus.completed,
            start_time=watch_party.scheduled_at,
            end_time=watch_party.ends_at,
        )

        await handle_discord_scheduled_event_update(self.bot, event, event)

        self.assertEqual(len(self.bot.announcement_channel.sent_messages), 1)
        self.assertIn("watched", self.bot.announcement_channel.sent_messages[0])

    async def test_completed_status_cancels_the_pending_internal_completion_job(self) -> None:
        watch_party = self._schedule()
        from watch_party_manager.scheduler.watch_party_scheduling import (
            schedule_watch_party_completion,
            watch_party_completion_logical_key,
        )
        await schedule_watch_party_completion(self.scheduler_service, watch_party, GUILD_ID)
        event = FakeScheduledEvent(
            watch_party.discord_event_id,
            status=discord.EventStatus.completed,
            start_time=watch_party.scheduled_at,
            end_time=watch_party.ends_at,
        )

        await handle_discord_scheduled_event_update(self.bot, event, event)

        active = await self.scheduler_repository.find_active_by_logical_key(
            watch_party_completion_logical_key(watch_party.id)
        )
        self.assertIsNone(active)

    async def test_unknown_event_id_is_a_no_op(self) -> None:
        event = FakeScheduledEvent(
            999999, status=discord.EventStatus.completed, start_time=utc_now(), end_time=utc_now()
        )

        await handle_discord_scheduled_event_update(self.bot, event, event)  # must not raise

        self.assertEqual(len(self.bot.announcement_channel.sent_messages), 0)


class OnScheduledEventUpdateCancellationTests(DiscordScheduledEventSyncTestCase):
    async def test_canceled_status_cancels_the_watch_party(self) -> None:
        watch_party = self._schedule()
        event = FakeScheduledEvent(
            watch_party.discord_event_id,
            status=discord.EventStatus.canceled,
            start_time=watch_party.scheduled_at,
            end_time=watch_party.ends_at,
        )

        await handle_discord_scheduled_event_update(self.bot, event, event)

        self.assertEqual(
            self.watch_party_service.get_watch_party(watch_party.id).status, WatchPartyStatus.CANCELLED
        )

    async def test_canceled_status_posts_a_cancellation_announcement(self) -> None:
        watch_party = self._schedule()
        event = FakeScheduledEvent(
            watch_party.discord_event_id,
            status=discord.EventStatus.canceled,
            start_time=watch_party.scheduled_at,
            end_time=watch_party.ends_at,
        )

        await handle_discord_scheduled_event_update(self.bot, event, event)

        self.assertEqual(len(self.bot.announcement_channel.sent_messages), 1)
        self.assertIn("cancelled", self.bot.announcement_channel.sent_messages[0])

    async def test_canceled_status_is_idempotent(self) -> None:
        watch_party = self._schedule()
        event = FakeScheduledEvent(
            watch_party.discord_event_id,
            status=discord.EventStatus.canceled,
            start_time=watch_party.scheduled_at,
            end_time=watch_party.ends_at,
        )
        await handle_discord_scheduled_event_update(self.bot, event, event)

        await handle_discord_scheduled_event_update(self.bot, event, event)

        self.assertEqual(len(self.bot.announcement_channel.sent_messages), 1)


class OnScheduledEventUpdateRescheduleTests(DiscordScheduledEventSyncTestCase):
    async def test_a_changed_start_time_syncs_the_watch_party(self) -> None:
        watch_party = self._schedule()
        new_start = watch_party.scheduled_at + timedelta(days=3)
        event = FakeScheduledEvent(
            watch_party.discord_event_id,
            status=discord.EventStatus.scheduled,
            start_time=new_start,
            end_time=new_start + timedelta(minutes=90),
        )

        await handle_discord_scheduled_event_update(self.bot, event, event)

        updated = self.watch_party_service.get_watch_party(watch_party.id)
        self.assertEqual(updated.scheduled_at, new_start)

    async def test_a_changed_end_time_syncs_the_duration(self) -> None:
        watch_party = self._schedule(duration_minutes=90)
        event = FakeScheduledEvent(
            watch_party.discord_event_id,
            status=discord.EventStatus.scheduled,
            start_time=watch_party.scheduled_at,
            end_time=watch_party.scheduled_at + timedelta(minutes=150),
        )

        await handle_discord_scheduled_event_update(self.bot, event, event)

        updated = self.watch_party_service.get_watch_party(watch_party.id)
        self.assertEqual(updated.duration_minutes, 150)

    async def test_an_unchanged_event_does_not_touch_the_watch_party(self) -> None:
        watch_party = self._schedule()
        event = FakeScheduledEvent(
            watch_party.discord_event_id,
            status=discord.EventStatus.scheduled,
            start_time=watch_party.scheduled_at,
            end_time=watch_party.ends_at,
        )

        await handle_discord_scheduled_event_update(self.bot, event, event)

        updated = self.watch_party_service.get_watch_party(watch_party.id)
        self.assertEqual(updated.scheduled_at, watch_party.scheduled_at)
        self.assertEqual(updated.duration_minutes, watch_party.duration_minutes)

    async def test_active_status_does_not_touch_the_watch_party(self) -> None:
        watch_party = self._schedule()
        event = FakeScheduledEvent(
            watch_party.discord_event_id,
            status=discord.EventStatus.active,
            start_time=watch_party.scheduled_at + timedelta(hours=1),  # would differ if synced
            end_time=watch_party.ends_at,
        )

        await handle_discord_scheduled_event_update(self.bot, event, event)

        updated = self.watch_party_service.get_watch_party(watch_party.id)
        self.assertEqual(updated.scheduled_at, watch_party.scheduled_at)
        self.assertEqual(updated.status, WatchPartyStatus.SCHEDULED)


class OnScheduledEventDeleteTests(DiscordScheduledEventSyncTestCase):
    async def test_deleting_the_event_cancels_the_watch_party(self) -> None:
        watch_party = self._schedule()
        event = FakeScheduledEvent(
            watch_party.discord_event_id,
            status=discord.EventStatus.scheduled,
            start_time=watch_party.scheduled_at,
            end_time=watch_party.ends_at,
        )

        await handle_discord_scheduled_event_delete(self.bot, event)

        self.assertEqual(
            self.watch_party_service.get_watch_party(watch_party.id).status, WatchPartyStatus.CANCELLED
        )

    async def test_deleting_the_event_posts_a_cancellation_announcement(self) -> None:
        watch_party = self._schedule()
        event = FakeScheduledEvent(
            watch_party.discord_event_id,
            status=discord.EventStatus.scheduled,
            start_time=watch_party.scheduled_at,
            end_time=watch_party.ends_at,
        )

        await handle_discord_scheduled_event_delete(self.bot, event)

        self.assertEqual(len(self.bot.announcement_channel.sent_messages), 1)
        self.assertIn("cancelled", self.bot.announcement_channel.sent_messages[0])

    async def test_unknown_event_id_is_a_no_op(self) -> None:
        event = FakeScheduledEvent(
            999999, status=discord.EventStatus.scheduled, start_time=utc_now(), end_time=utc_now()
        )

        await handle_discord_scheduled_event_delete(self.bot, event)  # must not raise

        self.assertEqual(len(self.bot.announcement_channel.sent_messages), 0)

    async def test_already_cancelled_watch_party_is_a_no_op(self) -> None:
        watch_party = self._schedule()
        self.watch_party_service.cancel_watch_party(watch_party.id)
        event = FakeScheduledEvent(
            watch_party.discord_event_id,
            status=discord.EventStatus.scheduled,
            start_time=watch_party.scheduled_at,
            end_time=watch_party.ends_at,
        )

        await handle_discord_scheduled_event_delete(self.bot, event)

        self.assertEqual(len(self.bot.announcement_channel.sent_messages), 0)


if __name__ == "__main__":
    unittest.main()
