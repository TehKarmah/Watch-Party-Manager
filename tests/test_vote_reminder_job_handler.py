"""Tests for FR-017: Vote Reminder Job Handler.

Covers the vote_reminder job handler this milestone adds -- locating a
vote by the job's payload, verifying it still exists and is still open,
posting the reminder to its channel, and returning a successful no-op
when the round no longer exists or has already closed. Also verifies
registration during WatchPartyBot startup and execution through the real
SchedulerService.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from watch_party_manager.domain.vote import VoteRoundStatus, VoteVisibility
from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.scheduler.job_handler import JobExecutionResult
from watch_party_manager.scheduler.scheduled_job import JobResult, ScheduledJob
from watch_party_manager.scheduler.vote_reminder_job_handler import (
    VoteReminderJobHandler,
    build_vote_reminder_text,
)
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.vote_service import VoteService


def make_job(vote_id: int, run_at: datetime | None = None) -> ScheduledJob:
    if run_at is None:
        run_at = datetime.now(timezone.utc)
    return ScheduledJob(
        guild_id=100,
        job_type="vote_reminder",
        logical_key=f"vote:{vote_id}:reminder",
        run_at=run_at,
        payload={"vote_id": vote_id},
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


class VoteReminderJobHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.vote_service = VoteService(
            self.suggestion_service, repository=JsonVoteRepository(root / "voting.json")
        )
        self.matrix = self.suggestion_service.suggest("The Matrix").watch_item
        self.inception = self.suggestion_service.suggest("Inception").watch_item

        self.channel = FakeChannel()
        self.bot = FakeBot(self.channel)
        self.handler = VoteReminderJobHandler(self.vote_service, self.bot)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _open_round(self, closes_at=None, guild_id=100, channel_id=200):
        if closes_at is None:
            closes_at = datetime.now(timezone.utc) + timedelta(hours=1)
        vote_round = self.vote_service.create_round(
            visibility=VoteVisibility.VISIBLE,
            closes_at=closes_at,
            candidate_suggestion_ids=[self.matrix.id, self.inception.id],
        ).vote_round
        self.vote_service.attach_message_reference(
            vote_round.id, guild_id=guild_id, channel_id=channel_id, message_id=999
        )
        return self.vote_service.get_round(vote_round.id)

    # --- Happy path: locate, verify, post the reminder --------------------------

    async def test_posts_a_reminder_to_the_rounds_channel(self) -> None:
        vote_round = self._open_round()

        result = await self.handler.execute(make_job(vote_round.id))

        self.assertEqual(result, JobExecutionResult(result=JobResult.EXECUTED))
        self.assertEqual(len(self.channel.sent_messages), 1)

    async def test_reminder_mentions_the_round(self) -> None:
        vote_round = self._open_round()

        await self.handler.execute(make_job(vote_round.id))

        self.assertIn(f"Voting round {vote_round.id}", self.channel.sent_messages[0])

    async def test_reminder_includes_the_discord_native_close_timestamp(self) -> None:
        closes_at = datetime.now(timezone.utc) + timedelta(hours=3)
        vote_round = self._open_round(closes_at=closes_at)
        unix_timestamp = int(closes_at.timestamp())

        await self.handler.execute(make_job(vote_round.id))

        self.assertIn(f"<t:{unix_timestamp}:F>", self.channel.sent_messages[0])
        self.assertIn(f"<t:{unix_timestamp}:R>", self.channel.sent_messages[0])

    async def test_reminder_includes_a_call_to_action(self) -> None:
        vote_round = self._open_round()

        await self.handler.execute(make_job(vote_round.id))

        self.assertIn("/vote", self.channel.sent_messages[0])

    async def test_reminder_includes_the_original_vote_link(self) -> None:
        # _open_round() always attaches a message reference, so the link
        # is expected here.
        vote_round = self._open_round(guild_id=100, channel_id=200)

        await self.handler.execute(make_job(vote_round.id))

        self.assertIn("https://discord.com/channels/100/200/999", self.channel.sent_messages[0])

    async def test_falls_back_to_fetch_channel_when_get_channel_returns_none(self) -> None:
        vote_round = self._open_round()

        class FetchOnlyBot:
            def __init__(self, channel) -> None:
                self._channel = channel

            def get_channel(self, channel_id):
                return None

            async def fetch_channel(self, channel_id):
                return self._channel

        handler = VoteReminderJobHandler(self.vote_service, FetchOnlyBot(self.channel))

        result = await handler.execute(make_job(vote_round.id))

        self.assertEqual(result.result, JobResult.EXECUTED)
        self.assertEqual(len(self.channel.sent_messages), 1)

    # --- Vote no longer exists: successful no-op ---------------------------------

    async def test_a_nonexistent_vote_is_a_successful_no_op(self) -> None:
        result = await self.handler.execute(make_job(vote_id=999))

        self.assertEqual(result, JobExecutionResult(result=JobResult.SKIPPED_NOT_APPLICABLE))
        self.assertEqual(self.channel.sent_messages, [])

    # --- Already closed: successful no-op -----------------------------------------

    async def test_an_already_closed_round_is_a_successful_no_op(self) -> None:
        vote_round = self._open_round()
        self.vote_service.close_round(vote_round.id)

        result = await self.handler.execute(make_job(vote_round.id))

        self.assertEqual(result, JobExecutionResult(result=JobResult.SKIPPED_NOT_APPLICABLE))
        self.assertEqual(self.channel.sent_messages, [])

    async def test_already_closed_round_does_not_raise(self) -> None:
        vote_round = self._open_round()
        self.vote_service.close_round(vote_round.id)

        # Should not raise -- this is the whole point of the no-op contract.
        await self.handler.execute(make_job(vote_round.id))

    # --- No channel reference: successful no-op ------------------------------------

    async def test_a_round_with_no_channel_reference_is_a_successful_no_op(self) -> None:
        vote_round = self.vote_service.create_round(
            visibility=VoteVisibility.VISIBLE,
            closes_at=datetime.now(timezone.utc) + timedelta(hours=1),
            candidate_suggestion_ids=[self.matrix.id, self.inception.id],
        ).vote_round

        result = await self.handler.execute(make_job(vote_round.id))

        self.assertEqual(result, JobExecutionResult(result=JobResult.SKIPPED_NOT_APPLICABLE))
        self.assertEqual(self.channel.sent_messages, [])

    # --- Idempotency: rechecks current state on every call ------------------------

    async def test_running_the_job_again_after_the_round_closes_no_longer_reminds(self) -> None:
        vote_round = self._open_round()

        first = await self.handler.execute(make_job(vote_round.id))
        self.vote_service.close_round(vote_round.id)
        second = await self.handler.execute(make_job(vote_round.id))

        self.assertEqual(first.result, JobResult.EXECUTED)
        self.assertEqual(second.result, JobResult.SKIPPED_NOT_APPLICABLE)
        self.assertEqual(len(self.channel.sent_messages), 1)

    async def test_running_the_job_again_after_the_vote_is_gone_no_longer_reminds(self) -> None:
        vote_round = self._open_round()

        first = await self.handler.execute(make_job(vote_round.id))
        second = await self.handler.execute(make_job(vote_id=vote_round.id + 999))

        self.assertEqual(first.result, JobResult.EXECUTED)
        self.assertEqual(second.result, JobResult.SKIPPED_NOT_APPLICABLE)
        self.assertEqual(len(self.channel.sent_messages), 1)

    # --- Multiple rounds don't interfere -------------------------------------------

    async def test_a_reminder_job_for_one_round_never_reminds_about_a_different_round(self) -> None:
        first_round = self._open_round()

        await self.handler.execute(make_job(vote_id=first_round.id + 999))

        self.assertEqual(self.channel.sent_messages, [])

    # --- Payload handling ----------------------------------------------------------

    async def test_missing_vote_id_in_payload_raises(self) -> None:
        job = ScheduledJob(
            guild_id=100,
            job_type="vote_reminder",
            logical_key="vote:1:reminder",
            run_at=datetime.now(timezone.utc),
            payload={},
        )

        with self.assertRaises(KeyError):
            await self.handler.execute(job)


class BuildVoteReminderTextTests(unittest.TestCase):
    def _round(self, round_id=1, closes_at=None):
        from watch_party_manager.domain.vote import VoteRound

        if closes_at is None:
            closes_at = datetime(2026, 7, 20, 18, 0, tzinfo=timezone.utc)
        return VoteRound(id=round_id, closes_at=closes_at)

    def test_mentions_round_id(self) -> None:
        text = build_vote_reminder_text(self._round(round_id=42))

        self.assertIn("42", text)

    def test_includes_the_discord_timestamp_helper_output(self) -> None:
        closes_at = datetime(2026, 7, 20, 18, 0, tzinfo=timezone.utc)
        unix_timestamp = int(closes_at.timestamp())

        text = build_vote_reminder_text(self._round(closes_at=closes_at))

        self.assertIn(f"<t:{unix_timestamp}:F> (<t:{unix_timestamp}:R>)", text)

    def test_includes_a_call_to_action(self) -> None:
        text = build_vote_reminder_text(self._round())

        self.assertIn("/vote", text)

    def test_includes_the_original_vote_link_when_available(self) -> None:
        from watch_party_manager.domain.vote import VoteRound

        vote_round = VoteRound(
            id=1,
            closes_at=datetime(2026, 7, 20, 18, 0, tzinfo=timezone.utc),
            guild_id=100,
            channel_id=200,
            message_id=300,
        )

        text = build_vote_reminder_text(vote_round)

        self.assertIn("https://discord.com/channels/100/200/300", text)

    def test_omits_the_link_for_a_legacy_round_without_message_metadata(self) -> None:
        text = build_vote_reminder_text(self._round())

        self.assertNotIn("discord.com", text)


class VoteReminderJobHandlerSchedulerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Confirms the handler works when driven through the real
    SchedulerService.register_handler()/run_once() path, not just called
    directly -- i.e. that FR-017's registration actually takes effect, and
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
        self.vote_service = VoteService(
            self.suggestion_service, repository=JsonVoteRepository(root / "voting.json")
        )
        self.matrix = self.suggestion_service.suggest("The Matrix").watch_item
        self.inception = self.suggestion_service.suggest("Inception").watch_item

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    async def test_scheduler_run_once_executes_the_registered_handler(self) -> None:
        from watch_party_manager.scheduler.json_scheduler_repository import JsonSchedulerRepository
        from watch_party_manager.scheduler.scheduler_service import SchedulerService
        from watch_party_manager.scheduler.vote_scheduling import build_vote_reminder_job

        scheduler_repository = JsonSchedulerRepository(Path(self._temp_dir.name) / "scheduled_jobs.json")
        scheduler_service = SchedulerService(scheduler_repository)
        channel = FakeChannel()
        scheduler_service.register_handler(
            "vote_reminder", VoteReminderJobHandler(self.vote_service, FakeBot(channel))
        )

        closes_at = datetime.now(timezone.utc) + timedelta(hours=1)
        vote_round = self.vote_service.create_round(
            visibility=VoteVisibility.VISIBLE,
            closes_at=closes_at,
            candidate_suggestion_ids=[self.matrix.id, self.inception.id],
        ).vote_round
        self.vote_service.attach_message_reference(
            vote_round.id, guild_id=100, channel_id=200, message_id=999
        )
        job = build_vote_reminder_job(
            vote_round,
            guild_id=100,
            reminder_enabled=True,
            reminder_hours_before_close=48,  # in the past relative to closes_at - 1h
        )
        await scheduler_service.schedule(job)

        processed = await scheduler_service.run_once()

        self.assertGreaterEqual(processed, 1)
        self.assertEqual(len(channel.sent_messages), 1)

    async def test_repeated_polling_only_sends_one_reminder(self) -> None:
        from watch_party_manager.scheduler.json_scheduler_repository import JsonSchedulerRepository
        from watch_party_manager.scheduler.scheduler_service import SchedulerService
        from watch_party_manager.scheduler.vote_scheduling import build_vote_reminder_job

        scheduler_repository = JsonSchedulerRepository(Path(self._temp_dir.name) / "scheduled_jobs.json")
        scheduler_service = SchedulerService(scheduler_repository)
        channel = FakeChannel()
        scheduler_service.register_handler(
            "vote_reminder", VoteReminderJobHandler(self.vote_service, FakeBot(channel))
        )

        closes_at = datetime.now(timezone.utc) + timedelta(hours=1)
        vote_round = self.vote_service.create_round(
            visibility=VoteVisibility.VISIBLE,
            closes_at=closes_at,
            candidate_suggestion_ids=[self.matrix.id, self.inception.id],
        ).vote_round
        self.vote_service.attach_message_reference(
            vote_round.id, guild_id=100, channel_id=200, message_id=999
        )
        job = build_vote_reminder_job(
            vote_round,
            guild_id=100,
            reminder_enabled=True,
            reminder_hours_before_close=48,
        )
        await scheduler_service.schedule(job)

        first_processed = await scheduler_service.run_once()
        second_processed = await scheduler_service.run_once()

        self.assertEqual(first_processed, 1)
        self.assertEqual(second_processed, 0)
        self.assertEqual(len(channel.sent_messages), 1)


if __name__ == "__main__":
    unittest.main()
