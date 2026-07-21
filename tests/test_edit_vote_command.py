"""Tests for FR-023: Vote Editing & Administrative Controls.

Covers /edit_vote's core logic (perform_edit_vote_open,
perform_change_vote_end_time, perform_end_vote_now, perform_cancel_vote_now)
and their Discord-I/O completion wrappers (handle_change_vote_end_time_completion,
handle_end_vote_now_completion, handle_cancel_vote_now_completion). Does not
duplicate coverage already provided by test_vote_service.py (reschedule_round/
cancel_round), test_vote_scheduling.py (cancel_vote_jobs/reschedule_vote_jobs),
or test_vote_completion_announcement.py (the notice-building functions).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import (
    build_edit_vote_management_text,
    handle_cancel_vote_now_completion,
    handle_change_vote_end_time_completion,
    handle_end_vote_now_completion,
    parse_vote_end_time,
    perform_cancel_vote_now,
    perform_change_vote_end_time,
    perform_edit_vote_open,
    perform_end_vote_now,
)
from watch_party_manager.domain.vote import VoteRoundStatus, VoteVisibility
from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.scheduler.scheduled_job import JobResult, JobStatus, ScheduledJob
from watch_party_manager.scheduler.scheduler_service import SchedulerService
from watch_party_manager.scheduler.vote_scheduling import (
    close_vote_logical_key,
    schedule_vote_jobs,
    vote_reminder_logical_key,
)
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.vote_completion_service import VoteCompletionService
from watch_party_manager.services.vote_service import VoteService

WASH_CREW_ROLE_ID = 999


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, roles=()) -> None:
        self.roles = list(roles)


class FakeResponse:
    def __init__(self) -> None:
        self.sent_message = None
        self.sent_ephemeral = None
        self.sent_view = None

    async def send_message(self, content, ephemeral=False, view=None) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral
        self.sent_view = view


class FakeInteraction:
    def __init__(self, user=None) -> None:
        self.user = user if user is not None else FakeMember(roles=[FakeRole(WASH_CREW_ROLE_ID)])
        self.response = FakeResponse()


class FakeMessage:
    def __init__(self, message_id: int) -> None:
        self.id = message_id
        self.edited_content = None
        self.edited_view = "not-edited"

    async def edit(self, content=None, view="not-edited") -> None:
        self.edited_content = content
        self.edited_view = view


class FakeChannel:
    def __init__(self, message: FakeMessage) -> None:
        self.sent_messages = []
        self._message = message

    async def send(self, content) -> None:
        self.sent_messages.append(content)

    async def fetch_message(self, message_id):
        return self._message


class FakeBot:
    def __init__(self, channel: FakeChannel) -> None:
        self._channel = channel

    def get_channel(self, channel_id):
        return self._channel

    async def fetch_channel(self, channel_id):
        return self._channel


class MemorySchedulerRepository:
    """In-memory SchedulerRepository fake, matching test_vote_scheduling.py's."""

    def __init__(self) -> None:
        self.jobs: dict[str, ScheduledJob] = {}

    async def add(self, job: ScheduledJob) -> ScheduledJob:
        self.jobs[job.job_id] = job
        return job

    async def get_due(self, now: datetime, *, limit: int = 100) -> list[ScheduledJob]:
        return [
            job for job in self.jobs.values() if job.status is JobStatus.PENDING and job.run_at <= now
        ][:limit]

    async def claim(self, job_id: str, started_at: datetime):
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

    async def find_active_by_logical_key(self, logical_key: str):
        return next(
            (job for job in self.jobs.values() if job.logical_key == logical_key and job.is_active),
            None,
        )


class EditVoteTestCase(unittest.IsolatedAsyncioTestCase):
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
        self.vote_completion_service = VoteCompletionService(self.vote_service, self.suggestion_service)
        self.matrix = self.suggestion_service.suggest("The Matrix").watch_item
        self.inception = self.suggestion_service.suggest("Inception").watch_item
        self.arrival = self.suggestion_service.suggest("Arrival").watch_item

        self.scheduler_repository = MemorySchedulerRepository()
        self.scheduler_service = SchedulerService(self.scheduler_repository)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _authorized_user(self) -> FakeMember:
        return FakeMember(roles=[FakeRole(WASH_CREW_ROLE_ID)])

    def _unauthorized_user(self) -> FakeMember:
        return FakeMember(roles=[FakeRole(1)])

    def _open_round(self, *, with_message_reference=True, closes_at=None):
        if closes_at is None:
            closes_at = datetime.now(timezone.utc) + timedelta(days=7)
        created = self.vote_service.create_round(
            visibility=VoteVisibility.VISIBLE,
            closes_at=closes_at,
            candidate_suggestion_ids=[self.matrix.id, self.inception.id, self.arrival.id],
        )
        if with_message_reference:
            self.vote_service.attach_message_reference(
                created.vote_round.id, guild_id=100, channel_id=200, message_id=999
            )
        return self.vote_service.get_round(created.vote_round.id)


# --- /edit_vote: open, permissions, and management response ---------------


class PerformEditVoteOpenTests(EditVoteTestCase):
    def test_unauthorized_user_is_rejected(self) -> None:
        self._open_round()

        message, ephemeral, vote_round = perform_edit_vote_open(
            self.vote_service, self.suggestion_service, self._unauthorized_user(), WASH_CREW_ROLE_ID
        )

        self.assertTrue(ephemeral)
        self.assertIn("WASH Crew", message)
        self.assertIsNone(vote_round)

    def test_unconfigured_role_fails_closed(self) -> None:
        self._open_round()

        message, ephemeral, vote_round = perform_edit_vote_open(
            self.vote_service, self.suggestion_service, self._authorized_user(), None
        )

        self.assertTrue(ephemeral)
        self.assertIn("not been configured", message)
        self.assertIsNone(vote_round)

    def test_no_active_vote_is_reported(self) -> None:
        message, ephemeral, vote_round = perform_edit_vote_open(
            self.vote_service, self.suggestion_service, self._authorized_user(), WASH_CREW_ROLE_ID
        )

        self.assertTrue(ephemeral)
        self.assertIn("no active", message.lower())
        self.assertIsNone(vote_round)

    def test_active_vote_returns_the_management_response(self) -> None:
        created_round = self._open_round()

        message, ephemeral, vote_round = perform_edit_vote_open(
            self.vote_service, self.suggestion_service, self._authorized_user(), WASH_CREW_ROLE_ID
        )

        self.assertTrue(ephemeral)
        self.assertIsNotNone(vote_round)
        self.assertEqual(vote_round.id, created_round.id)
        self.assertIn(f"Managing voting round {created_round.id}", message)
        self.assertIn("Votes cast:", message)
        self.assertIn("Voting ends:", message)

    def test_original_vote_link_is_shown_when_available(self) -> None:
        self._open_round(with_message_reference=True)

        message, _, _ = perform_edit_vote_open(
            self.vote_service, self.suggestion_service, self._authorized_user(), WASH_CREW_ROLE_ID
        )

        self.assertIn("https://discord.com/channels/100/200/999", message)

    def test_legacy_vote_without_link_metadata_omits_the_link(self) -> None:
        self._open_round(with_message_reference=False)

        message, _, _ = perform_edit_vote_open(
            self.vote_service, self.suggestion_service, self._authorized_user(), WASH_CREW_ROLE_ID
        )

        self.assertNotIn("discord.com", message)


class BuildEditVoteManagementTextTests(EditVoteTestCase):
    def test_shows_votes_cast_count(self) -> None:
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=111, suggestion_id=self.matrix.id)

        text = build_edit_vote_management_text(
            self.vote_service.get_round(vote_round.id), candidate_count=3
        )

        self.assertIn("Votes cast: 1", text)


# --- Change End Time --------------------------------------------------------


class PerformChangeVoteEndTimeTests(EditVoteTestCase):
    def test_unauthorized_user_is_rejected(self) -> None:
        vote_round = self._open_round()

        message, ephemeral, updated = perform_change_vote_end_time(
            self.vote_service, self._unauthorized_user(), WASH_CREW_ROLE_ID, vote_round.id, "2027-01-01 12:00"
        )

        self.assertTrue(ephemeral)
        self.assertIn("WASH Crew", message)
        self.assertIsNone(updated)

    def test_preserves_the_rounds_identity(self) -> None:
        vote_round = self._open_round()

        _, _, updated = perform_change_vote_end_time(
            self.vote_service, self._authorized_user(), WASH_CREW_ROLE_ID, vote_round.id, "2027-01-01 12:00"
        )

        self.assertEqual(updated.id, vote_round.id)

    def test_preserves_submitted_votes(self) -> None:
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=111, suggestion_id=self.matrix.id)

        _, _, updated = perform_change_vote_end_time(
            self.vote_service, self._authorized_user(), WASH_CREW_ROLE_ID, vote_round.id, "2027-01-01 12:00"
        )

        self.assertEqual(updated.votes[111].suggestion_id, self.matrix.id)

    def test_updates_the_closing_time(self) -> None:
        vote_round = self._open_round()

        _, _, updated = perform_change_vote_end_time(
            self.vote_service, self._authorized_user(), WASH_CREW_ROLE_ID, vote_round.id, "2027-01-01 12:00"
        )

        self.assertEqual(updated.closes_at, datetime(2027, 1, 1, 12, tzinfo=timezone.utc))

    def test_rejects_a_past_closing_time(self) -> None:
        vote_round = self._open_round()

        message, ephemeral, updated = perform_change_vote_end_time(
            self.vote_service,
            self._authorized_user(),
            WASH_CREW_ROLE_ID,
            vote_round.id,
            "2020-01-01 12:00",
        )

        self.assertTrue(ephemeral)
        self.assertIn("future", message.lower())
        self.assertIsNone(updated)
        self.assertEqual(self.vote_service.get_round(vote_round.id).closes_at, vote_round.closes_at)

    def test_rejects_an_invalid_time_string(self) -> None:
        vote_round = self._open_round()

        message, ephemeral, updated = perform_change_vote_end_time(
            self.vote_service, self._authorized_user(), WASH_CREW_ROLE_ID, vote_round.id, "not a date"
        )

        self.assertTrue(ephemeral)
        self.assertIsNone(updated)
        self.assertEqual(self.vote_service.get_round(vote_round.id).closes_at, vote_round.closes_at)


class ParseVoteEndTimeTests(unittest.TestCase):
    def test_rejects_a_time_in_the_past(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            parse_vote_end_time("2025-01-01 00:00", now=now)

    def test_accepts_a_time_in_the_future(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        parsed = parse_vote_end_time("2027-01-01 00:00", now=now)
        self.assertEqual(parsed, datetime(2027, 1, 1, tzinfo=timezone.utc))


class HandleChangeVoteEndTimeCompletionTests(EditVoteTestCase):
    async def _schedule_initial_jobs(self, vote_round):
        return await schedule_vote_jobs(self.scheduler_service, vote_round, guild_id=100)

    async def test_existing_jobs_are_cancelled_and_replacements_created(self) -> None:
        vote_round = self._open_round()
        await self._schedule_initial_jobs(vote_round)
        old_job_ids = {job.job_id for job in self.scheduler_repository.jobs.values()}

        message = FakeMessage(message_id=999)
        channel = FakeChannel(message)
        bot = FakeBot(channel)
        interaction = FakeInteraction(self._authorized_user())

        await handle_change_vote_end_time_completion(
            interaction,
            self.vote_service,
            self.suggestion_service,
            WASH_CREW_ROLE_ID,
            vote_round.id,
            "2027-01-01 12:00",
            bot,
            scheduler_service=self.scheduler_service,
        )

        old_statuses = {
            job.status for job_id, job in self.scheduler_repository.jobs.items() if job_id in old_job_ids
        }
        self.assertEqual(old_statuses, {JobStatus.CANCELLED})

        active_jobs = [job for job in self.scheduler_repository.jobs.values() if job.is_active]
        self.assertEqual(len(active_jobs), 2)

    async def test_replacement_jobs_reflect_the_new_deadline(self) -> None:
        vote_round = self._open_round()
        await self._schedule_initial_jobs(vote_round)

        message = FakeMessage(message_id=999)
        bot = FakeBot(FakeChannel(message))
        interaction = FakeInteraction(self._authorized_user())

        await handle_change_vote_end_time_completion(
            interaction,
            self.vote_service,
            self.suggestion_service,
            WASH_CREW_ROLE_ID,
            vote_round.id,
            "2027-01-01 12:00",
            bot,
            scheduler_service=self.scheduler_service,
        )

        close_job = next(
            job
            for job in self.scheduler_repository.jobs.values()
            if job.logical_key == close_vote_logical_key(vote_round.id) and job.is_active
        )
        reminder_job = next(
            job
            for job in self.scheduler_repository.jobs.values()
            if job.logical_key == vote_reminder_logical_key(vote_round.id) and job.is_active
        )
        self.assertEqual(close_job.run_at, datetime(2027, 1, 1, 12, tzinfo=timezone.utc))
        self.assertEqual(reminder_job.run_at, datetime(2027, 1, 1, 12, tzinfo=timezone.utc) - timedelta(hours=24))

    async def test_public_deadline_change_notice_is_posted_with_the_link(self) -> None:
        vote_round = self._open_round()

        message = FakeMessage(message_id=999)
        channel = FakeChannel(message)
        bot = FakeBot(channel)
        interaction = FakeInteraction(self._authorized_user())

        await handle_change_vote_end_time_completion(
            interaction,
            self.vote_service,
            self.suggestion_service,
            WASH_CREW_ROLE_ID,
            vote_round.id,
            "2027-01-01 12:00",
            bot,
        )

        self.assertEqual(len(channel.sent_messages), 1)
        self.assertIn("deadline has changed", channel.sent_messages[0])
        self.assertIn("https://discord.com/channels/100/200/999", channel.sent_messages[0])

    async def test_the_original_voting_message_is_updated(self) -> None:
        vote_round = self._open_round()

        message = FakeMessage(message_id=999)
        bot = FakeBot(FakeChannel(message))
        interaction = FakeInteraction(self._authorized_user())

        await handle_change_vote_end_time_completion(
            interaction,
            self.vote_service,
            self.suggestion_service,
            WASH_CREW_ROLE_ID,
            vote_round.id,
            "2027-01-01 12:00",
            bot,
        )

        self.assertIsNotNone(message.edited_content)
        self.assertIn(f"Voting round {vote_round.id}", message.edited_content)
        # The view is left intact (voting is still possible), unlike the
        # end-now/cancel paths which clear it.
        self.assertEqual(message.edited_view, "not-edited")

    async def test_a_failed_change_sends_no_public_notice(self) -> None:
        vote_round = self._open_round()

        message = FakeMessage(message_id=999)
        channel = FakeChannel(message)
        bot = FakeBot(channel)
        interaction = FakeInteraction(self._authorized_user())

        await handle_change_vote_end_time_completion(
            interaction,
            self.vote_service,
            self.suggestion_service,
            WASH_CREW_ROLE_ID,
            vote_round.id,
            "not a date",
            bot,
        )

        self.assertEqual(channel.sent_messages, [])
        self.assertTrue(interaction.response.sent_ephemeral)


# --- End Now -----------------------------------------------------------------


class PerformEndVoteNowTests(EditVoteTestCase):
    def test_unauthorized_user_is_rejected(self) -> None:
        vote_round = self._open_round()

        message, ephemeral, result = perform_end_vote_now(
            self.vote_completion_service, self._unauthorized_user(), WASH_CREW_ROLE_ID, vote_round.id
        )

        self.assertTrue(ephemeral)
        self.assertIn("WASH Crew", message)
        self.assertIsNone(result)
        self.assertEqual(self.vote_service.get_round(vote_round.id).status, VoteRoundStatus.OPEN)

    def test_completes_the_round_immediately(self) -> None:
        vote_round = self._open_round()

        _, _, result = perform_end_vote_now(
            self.vote_completion_service, self._authorized_user(), WASH_CREW_ROLE_ID, vote_round.id
        )

        self.assertIsNotNone(result)
        self.assertEqual(self.vote_service.get_round(vote_round.id).status, VoteRoundStatus.CLOSED)

    def test_preserves_existing_votes(self) -> None:
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=111, suggestion_id=self.matrix.id)

        perform_end_vote_now(
            self.vote_completion_service, self._authorized_user(), WASH_CREW_ROLE_ID, vote_round.id
        )

        self.assertEqual(
            self.vote_service.get_round(vote_round.id).votes[111].suggestion_id, self.matrix.id
        )

    def test_winner_calculation_matches_the_normal_completion_path(self) -> None:
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=111, suggestion_id=self.matrix.id)
        self.vote_service.cast_vote(discord_user_id=222, suggestion_id=self.matrix.id)
        self.vote_service.cast_vote(discord_user_id=333, suggestion_id=self.inception.id)

        _, _, result = perform_end_vote_now(
            self.vote_completion_service, self._authorized_user(), WASH_CREW_ROLE_ID, vote_round.id
        )

        self.assertEqual(result.winning_suggestion_ids, [self.matrix.id])

    def test_tie_calculation_matches_the_normal_completion_path(self) -> None:
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=111, suggestion_id=self.matrix.id)
        self.vote_service.cast_vote(discord_user_id=222, suggestion_id=self.inception.id)

        _, _, result = perform_end_vote_now(
            self.vote_completion_service, self._authorized_user(), WASH_CREW_ROLE_ID, vote_round.id
        )

        self.assertEqual(sorted(result.winning_suggestion_ids), sorted([self.matrix.id, self.inception.id]))

    def test_further_voting_is_rejected_after_ending(self) -> None:
        vote_round = self._open_round()

        perform_end_vote_now(
            self.vote_completion_service, self._authorized_user(), WASH_CREW_ROLE_ID, vote_round.id
        )

        result = self.vote_service.cast_vote(discord_user_id=111, suggestion_id=self.matrix.id)
        self.assertFalse(result.success)

    def test_repeated_invocation_does_not_recomplete(self) -> None:
        vote_round = self._open_round()

        perform_end_vote_now(
            self.vote_completion_service, self._authorized_user(), WASH_CREW_ROLE_ID, vote_round.id
        )
        message, ephemeral, result = perform_end_vote_now(
            self.vote_completion_service, self._authorized_user(), WASH_CREW_ROLE_ID, vote_round.id
        )

        self.assertIsNone(result)
        self.assertTrue(ephemeral)


class HandleEndVoteNowCompletionTests(EditVoteTestCase):
    async def test_pending_jobs_are_cancelled(self) -> None:
        vote_round = self._open_round()
        await schedule_vote_jobs(self.scheduler_service, vote_round, guild_id=100)

        message = FakeMessage(message_id=999)
        bot = FakeBot(FakeChannel(message))
        interaction = FakeInteraction(self._authorized_user())

        await handle_end_vote_now_completion(
            interaction,
            self.vote_completion_service,
            self.suggestion_service,
            WASH_CREW_ROLE_ID,
            vote_round.id,
            bot,
            scheduler_service=self.scheduler_service,
        )

        statuses = {job.status for job in self.scheduler_repository.jobs.values()}
        self.assertEqual(statuses, {JobStatus.CANCELLED})

    async def test_normal_completion_announcement_is_posted(self) -> None:
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=111, suggestion_id=self.matrix.id)

        message = FakeMessage(message_id=999)
        channel = FakeChannel(message)
        bot = FakeBot(channel)
        interaction = FakeInteraction(self._authorized_user())

        await handle_end_vote_now_completion(
            interaction, self.vote_completion_service, self.suggestion_service, WASH_CREW_ROLE_ID, vote_round.id, bot
        )

        self.assertEqual(len(channel.sent_messages), 1)
        self.assertIn("has closed!", channel.sent_messages[0])
        self.assertIn("Winner: The Matrix", channel.sent_messages[0])

    async def test_original_controls_are_disabled(self) -> None:
        vote_round = self._open_round()

        message = FakeMessage(message_id=999)
        bot = FakeBot(FakeChannel(message))
        interaction = FakeInteraction(self._authorized_user())

        await handle_end_vote_now_completion(
            interaction, self.vote_completion_service, self.suggestion_service, WASH_CREW_ROLE_ID, vote_round.id, bot
        )

        self.assertIsNone(message.edited_view)
        self.assertIsNotNone(message.edited_content)

    async def test_repeated_invocation_does_not_duplicate_the_announcement(self) -> None:
        vote_round = self._open_round()

        message = FakeMessage(message_id=999)
        channel = FakeChannel(message)
        bot = FakeBot(channel)

        await handle_end_vote_now_completion(
            FakeInteraction(self._authorized_user()),
            self.vote_completion_service,
            self.suggestion_service,
            WASH_CREW_ROLE_ID,
            vote_round.id,
            bot,
        )
        await handle_end_vote_now_completion(
            FakeInteraction(self._authorized_user()),
            self.vote_completion_service,
            self.suggestion_service,
            WASH_CREW_ROLE_ID,
            vote_round.id,
            bot,
        )

        self.assertEqual(len(channel.sent_messages), 1)


# --- Cancel Vote ---------------------------------------------------------------


class PerformCancelVoteNowTests(EditVoteTestCase):
    def test_unauthorized_user_is_rejected(self) -> None:
        vote_round = self._open_round()

        message, ephemeral, updated = perform_cancel_vote_now(
            self.vote_service, self._unauthorized_user(), WASH_CREW_ROLE_ID, vote_round.id
        )

        self.assertTrue(ephemeral)
        self.assertIn("WASH Crew", message)
        self.assertIsNone(updated)
        self.assertEqual(self.vote_service.get_round(vote_round.id).status, VoteRoundStatus.OPEN)

    def test_round_becomes_cancelled(self) -> None:
        vote_round = self._open_round()

        _, _, updated = perform_cancel_vote_now(
            self.vote_service, self._authorized_user(), WASH_CREW_ROLE_ID, vote_round.id
        )

        self.assertEqual(updated.status, VoteRoundStatus.CANCELLED)

    def test_preserves_existing_votes(self) -> None:
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=111, suggestion_id=self.matrix.id)

        perform_cancel_vote_now(self.vote_service, self._authorized_user(), WASH_CREW_ROLE_ID, vote_round.id)

        self.assertEqual(
            self.vote_service.get_round(vote_round.id).votes[111].suggestion_id, self.matrix.id
        )

    def test_produces_no_winner(self) -> None:
        vote_round = self._open_round()
        self.vote_service.cast_vote(discord_user_id=111, suggestion_id=self.matrix.id)

        perform_cancel_vote_now(self.vote_service, self._authorized_user(), WASH_CREW_ROLE_ID, vote_round.id)

        self.assertIsNone(self.vote_service.get_round(vote_round.id).winning_suggestion_id)

    def test_further_voting_is_rejected(self) -> None:
        vote_round = self._open_round()

        perform_cancel_vote_now(self.vote_service, self._authorized_user(), WASH_CREW_ROLE_ID, vote_round.id)

        result = self.vote_service.cast_vote(discord_user_id=111, suggestion_id=self.matrix.id)
        self.assertFalse(result.success)

    def test_repeated_invocation_is_safe(self) -> None:
        vote_round = self._open_round()

        perform_cancel_vote_now(self.vote_service, self._authorized_user(), WASH_CREW_ROLE_ID, vote_round.id)
        message, ephemeral, updated = perform_cancel_vote_now(
            self.vote_service, self._authorized_user(), WASH_CREW_ROLE_ID, vote_round.id
        )

        self.assertIsNone(updated)
        self.assertTrue(ephemeral)
        self.assertEqual(self.vote_service.get_round(vote_round.id).status, VoteRoundStatus.CANCELLED)


class HandleCancelVoteNowCompletionTests(EditVoteTestCase):
    async def test_pending_jobs_are_cancelled(self) -> None:
        vote_round = self._open_round()
        await schedule_vote_jobs(self.scheduler_service, vote_round, guild_id=100)

        message = FakeMessage(message_id=999)
        bot = FakeBot(FakeChannel(message))
        interaction = FakeInteraction(self._authorized_user())

        await handle_cancel_vote_now_completion(
            interaction,
            self.vote_service,
            WASH_CREW_ROLE_ID,
            vote_round.id,
            bot,
            scheduler_service=self.scheduler_service,
        )

        statuses = {job.status for job in self.scheduler_repository.jobs.values()}
        self.assertEqual(statuses, {JobStatus.CANCELLED})

    async def test_public_cancellation_notice_is_posted_with_the_link(self) -> None:
        vote_round = self._open_round()

        message = FakeMessage(message_id=999)
        channel = FakeChannel(message)
        bot = FakeBot(channel)
        interaction = FakeInteraction(self._authorized_user())

        await handle_cancel_vote_now_completion(
            interaction, self.vote_service, WASH_CREW_ROLE_ID, vote_round.id, bot
        )

        self.assertEqual(len(channel.sent_messages), 1)
        self.assertIn("cancelled", channel.sent_messages[0].lower())
        self.assertIn("https://discord.com/channels/100/200/999", channel.sent_messages[0])

    async def test_original_controls_are_disabled(self) -> None:
        vote_round = self._open_round()

        message = FakeMessage(message_id=999)
        bot = FakeBot(FakeChannel(message))
        interaction = FakeInteraction(self._authorized_user())

        await handle_cancel_vote_now_completion(
            interaction, self.vote_service, WASH_CREW_ROLE_ID, vote_round.id, bot
        )

        self.assertIsNone(message.edited_view)
        self.assertIn("cancelled", message.edited_content.lower())

    async def test_repeated_invocation_is_safe_and_does_not_duplicate_the_notice(self) -> None:
        vote_round = self._open_round()

        message = FakeMessage(message_id=999)
        channel = FakeChannel(message)
        bot = FakeBot(channel)

        await handle_cancel_vote_now_completion(
            FakeInteraction(self._authorized_user()), self.vote_service, WASH_CREW_ROLE_ID, vote_round.id, bot
        )
        await handle_cancel_vote_now_completion(
            FakeInteraction(self._authorized_user()), self.vote_service, WASH_CREW_ROLE_ID, vote_round.id, bot
        )

        self.assertEqual(len(channel.sent_messages), 1)


if __name__ == "__main__":
    unittest.main()
