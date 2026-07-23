"""Tests for FR-020: Watch Party Reminder Scheduling (delivery half).

Covers the watch_party_reminder job handler: locating a watch party by
the job's payload, verifying it still exists and hasn't been cancelled,
posting the reminder to its channel (with title, Discord-formatted watch
time, and IMDb link when available), and failing gracefully -- without
raising -- when the watch party or its channel is unavailable.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.watch_party_repository import JsonWatchPartyRepository
from watch_party_manager.scheduler.job_handler import JobExecutionResult
from watch_party_manager.scheduler.scheduled_job import JobResult, ScheduledJob
from watch_party_manager.scheduler.watch_party_reminder_job_handler import (
    WatchPartyReminderJobHandler,
    build_watch_party_reminder_text,
)
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.watch_party_service import WatchPartyService


def make_job(watch_party_id: int, run_at: datetime | None = None) -> ScheduledJob:
    if run_at is None:
        run_at = datetime.now(timezone.utc)
    return ScheduledJob(
        guild_id=100,
        job_type="watch_party_reminder",
        logical_key=f"watch_party:{watch_party_id}:reminder",
        run_at=run_at,
        payload={"watch_party_id": watch_party_id},
    )


class FakeChannel:
    def __init__(self) -> None:
        self.sent_messages = []

    async def send(self, content) -> None:
        self.sent_messages.append(content)


class FakeBot:
    """Duck-typed stand-in for a discord.Client/Bot, matching
    DiscordChannelMessenger's minimal interface requirement.
    """

    def __init__(self, channel: FakeChannel | None = None) -> None:
        self._channel = channel

    def get_channel(self, channel_id):
        return self._channel

    async def fetch_channel(self, channel_id):
        return self._channel


class WatchPartyReminderJobHandlerTests(unittest.IsolatedAsyncioTestCase):
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

        self.channel = FakeChannel()
        self.bot = FakeBot(self.channel)
        self.handler = WatchPartyReminderJobHandler(self.watch_party_service, self.suggestion_service, self.bot)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _schedule(self, scheduled_at=None, guild_id=100, channel_id=200):
        if scheduled_at is None:
            scheduled_at = datetime.now(timezone.utc) + timedelta(hours=1)
        return self.watch_party_service.schedule_watch_party(
            watch_item_id=self.matrix.id,
            scheduled_at=scheduled_at,
            guild_id=guild_id,
            channel_id=channel_id,
        ).watch_party

    # --- Happy path: locate, verify, post the reminder --------------------------

    async def test_posts_a_reminder_to_the_watch_partys_channel(self) -> None:
        watch_party = self._schedule()

        result = await self.handler.execute(make_job(watch_party.id))

        self.assertEqual(result, JobExecutionResult(result=JobResult.EXECUTED))
        self.assertEqual(len(self.channel.sent_messages), 1)

    async def test_reminder_includes_the_movie_title(self) -> None:
        watch_party = self._schedule()

        await self.handler.execute(make_job(watch_party.id))

        self.assertIn("The Matrix", self.channel.sent_messages[0])

    async def test_reminder_includes_the_discord_native_watch_time(self) -> None:
        scheduled_at = datetime.now(timezone.utc) + timedelta(hours=3)
        watch_party = self._schedule(scheduled_at=scheduled_at)
        unix_timestamp = int(scheduled_at.timestamp())

        await self.handler.execute(make_job(watch_party.id))

        self.assertIn(f"<t:{unix_timestamp}:F>", self.channel.sent_messages[0])
        self.assertIn(f"<t:{unix_timestamp}:R>", self.channel.sent_messages[0])

    async def test_reminder_includes_the_imdb_link_when_available(self) -> None:
        watch_party = self._schedule()

        await self.handler.execute(make_job(watch_party.id))

        self.assertIn("[View on IMDb](https://www.imdb.com/title/tt0133093/)", self.channel.sent_messages[0])

    async def test_reminder_omits_imdb_link_when_not_available(self) -> None:
        inception = self.suggestion_service.suggest("Inception").watch_item
        watch_party = self.watch_party_service.schedule_watch_party(
            watch_item_id=inception.id,
            scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
            guild_id=100,
            channel_id=200,
        ).watch_party

        await self.handler.execute(make_job(watch_party.id))

        self.assertNotIn("IMDb", self.channel.sent_messages[0])

    async def test_falls_back_to_fetch_channel_when_get_channel_returns_none(self) -> None:
        watch_party = self._schedule()

        class FetchOnlyBot:
            def __init__(self, channel) -> None:
                self._channel = channel

            def get_channel(self, channel_id):
                return None

            async def fetch_channel(self, channel_id):
                return self._channel

        handler = WatchPartyReminderJobHandler(
            self.watch_party_service, self.suggestion_service, FetchOnlyBot(self.channel)
        )

        result = await handler.execute(make_job(watch_party.id))

        self.assertEqual(result.result, JobResult.EXECUTED)
        self.assertEqual(len(self.channel.sent_messages), 1)

    # --- Graceful failure: nonexistent watch party -------------------------------

    async def test_a_nonexistent_watch_party_is_a_successful_no_op(self) -> None:
        result = await self.handler.execute(make_job(watch_party_id=999))

        self.assertEqual(result, JobExecutionResult(result=JobResult.SKIPPED_NOT_APPLICABLE))
        self.assertEqual(self.channel.sent_messages, [])

    # --- Graceful failure: cancelled watch party ----------------------------------

    async def test_a_cancelled_watch_party_is_a_successful_no_op(self) -> None:
        watch_party = self._schedule()
        self.watch_party_service.cancel_watch_party(watch_party.id)

        result = await self.handler.execute(make_job(watch_party.id))

        self.assertEqual(result, JobExecutionResult(result=JobResult.SKIPPED_NOT_APPLICABLE))
        self.assertEqual(self.channel.sent_messages, [])

    async def test_cancelled_watch_party_does_not_raise(self) -> None:
        watch_party = self._schedule()
        self.watch_party_service.cancel_watch_party(watch_party.id)

        # Should not raise -- this is the whole point of the no-op contract.
        await self.handler.execute(make_job(watch_party.id))

    # --- Graceful failure: missing channel reference ------------------------------

    async def test_a_watch_party_with_no_channel_reference_is_a_successful_no_op(self) -> None:
        watch_party = self.watch_party_service.schedule_watch_party(
            watch_item_id=self.matrix.id,
            scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
            guild_id=100,
        ).watch_party

        result = await self.handler.execute(make_job(watch_party.id))

        self.assertEqual(result, JobExecutionResult(result=JobResult.SKIPPED_NOT_APPLICABLE))
        self.assertEqual(self.channel.sent_messages, [])

    # --- Graceful failure: watch item removed after scheduling --------------------

    async def test_still_sends_a_reminder_when_the_watch_item_was_removed(self) -> None:
        watch_party = self._schedule()
        self.suggestion_service.remove_suggestion_by_id(self.matrix.id)

        result = await self.handler.execute(make_job(watch_party.id))

        self.assertEqual(result.result, JobResult.EXECUTED)
        self.assertEqual(len(self.channel.sent_messages), 1)
        self.assertIn(f"Watch party #{watch_party.id}", self.channel.sent_messages[0])

    # --- Idempotency: rechecks current state on every call ------------------------

    async def test_running_the_job_again_after_cancellation_no_longer_reminds(self) -> None:
        watch_party = self._schedule()

        first = await self.handler.execute(make_job(watch_party.id))
        self.watch_party_service.cancel_watch_party(watch_party.id)
        second = await self.handler.execute(make_job(watch_party.id))

        self.assertEqual(first.result, JobResult.EXECUTED)
        self.assertEqual(second.result, JobResult.SKIPPED_NOT_APPLICABLE)
        self.assertEqual(len(self.channel.sent_messages), 1)

    # --- Multiple watch parties don't interfere -------------------------------------

    async def test_a_reminder_job_for_one_watch_party_never_reminds_about_a_different_one(self) -> None:
        first = self._schedule()

        await self.handler.execute(make_job(watch_party_id=first.id + 999))

        self.assertEqual(self.channel.sent_messages, [])

    # --- Payload handling ----------------------------------------------------------

    async def test_missing_watch_party_id_in_payload_raises(self) -> None:
        job = ScheduledJob(
            guild_id=100,
            job_type="watch_party_reminder",
            logical_key="watch_party:1:reminder",
            run_at=datetime.now(timezone.utc),
            payload={},
        )

        with self.assertRaises(KeyError):
            await self.handler.execute(job)


class BuildWatchPartyReminderTextTests(unittest.TestCase):
    def _watch_party(self, watch_party_id=1, scheduled_at=None):
        from watch_party_manager.domain.watch_party import WatchParty

        if scheduled_at is None:
            scheduled_at = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
        return WatchParty(id=watch_party_id, watch_item_id=1, scheduled_at=scheduled_at, guild_id=100)

    def _watch_item(self, title="The Matrix", imdb_url=None):
        from watch_party_manager.domain.watch_item import MediaType, MetadataProvider, WatchItem

        metadata_ids = {MetadataProvider.IMDB: imdb_url} if imdb_url else {}
        return WatchItem(title=title, media_type=MediaType.MOVIE, metadata_ids=metadata_ids)

    def test_includes_the_movie_title(self) -> None:
        text = build_watch_party_reminder_text(self._watch_party(), self._watch_item(title="The Matrix"))

        self.assertIn("The Matrix", text)

    def test_includes_the_discord_timestamp_helper_output(self) -> None:
        scheduled_at = datetime(2026, 7, 20, 20, 0, tzinfo=timezone.utc)
        unix_timestamp = int(scheduled_at.timestamp())

        text = build_watch_party_reminder_text(
            self._watch_party(scheduled_at=scheduled_at), self._watch_item()
        )

        self.assertIn(f"<t:{unix_timestamp}:F> (<t:{unix_timestamp}:R>)", text)

    def test_includes_imdb_link_when_available(self) -> None:
        text = build_watch_party_reminder_text(
            self._watch_party(),
            self._watch_item(imdb_url="https://www.imdb.com/title/tt0133093/"),
        )

        self.assertIn("[View on IMDb](https://www.imdb.com/title/tt0133093/)", text)

    def test_omits_imdb_link_when_not_available(self) -> None:
        text = build_watch_party_reminder_text(self._watch_party(), self._watch_item())

        self.assertNotIn("IMDb", text)

    def test_falls_back_to_watch_party_id_when_watch_item_is_unresolvable(self) -> None:
        text = build_watch_party_reminder_text(self._watch_party(watch_party_id=42), None)

        self.assertIn("Watch party #42", text)


class WatchPartyReminderJobHandlerSchedulerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Confirms the handler works when driven through the real
    SchedulerService.register_handler()/run_once() path, not just called
    directly -- i.e. that FR-020's registration actually takes effect, and
    that the scheduler's own job lifecycle (a completed job is never
    re-claimed) is what keeps repeated polling from sending duplicate
    reminders.
    """

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

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    async def test_scheduler_run_once_executes_the_registered_handler(self) -> None:
        from watch_party_manager.scheduler.json_scheduler_repository import JsonSchedulerRepository
        from watch_party_manager.scheduler.scheduler_service import SchedulerService
        from watch_party_manager.scheduler.watch_party_scheduling import build_watch_party_reminder_job

        scheduler_repository = JsonSchedulerRepository(Path(self._temp_dir.name) / "scheduled_jobs.json")
        scheduler_service = SchedulerService(scheduler_repository)
        channel = FakeChannel()
        scheduler_service.register_handler(
            "watch_party_reminder",
            WatchPartyReminderJobHandler(self.watch_party_service, self.suggestion_service, FakeBot(channel)),
        )

        watch_party = self.watch_party_service.schedule_watch_party(
            watch_item_id=self.matrix.id,
            scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
            guild_id=100,
            channel_id=200,
        ).watch_party
        job = build_watch_party_reminder_job(watch_party, guild_id=100, reminder_hours_before_watch=48)
        await scheduler_service.schedule(job)

        processed = await scheduler_service.run_once()

        self.assertEqual(processed, 1)
        self.assertEqual(len(channel.sent_messages), 1)

    async def test_repeated_polling_only_sends_one_reminder(self) -> None:
        from watch_party_manager.scheduler.json_scheduler_repository import JsonSchedulerRepository
        from watch_party_manager.scheduler.scheduler_service import SchedulerService
        from watch_party_manager.scheduler.watch_party_scheduling import build_watch_party_reminder_job

        scheduler_repository = JsonSchedulerRepository(Path(self._temp_dir.name) / "scheduled_jobs.json")
        scheduler_service = SchedulerService(scheduler_repository)
        channel = FakeChannel()
        scheduler_service.register_handler(
            "watch_party_reminder",
            WatchPartyReminderJobHandler(self.watch_party_service, self.suggestion_service, FakeBot(channel)),
        )

        watch_party = self.watch_party_service.schedule_watch_party(
            watch_item_id=self.matrix.id,
            scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
            guild_id=100,
            channel_id=200,
        ).watch_party
        job = build_watch_party_reminder_job(watch_party, guild_id=100, reminder_hours_before_watch=48)
        await scheduler_service.schedule(job)

        first_processed = await scheduler_service.run_once()
        second_processed = await scheduler_service.run_once()

        self.assertEqual(first_processed, 1)
        self.assertEqual(second_processed, 0)
        self.assertEqual(len(channel.sent_messages), 1)


if __name__ == "__main__":
    unittest.main()
