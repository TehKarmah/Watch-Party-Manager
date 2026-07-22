"""Tests for FR-030's membership request persistence
(persistence/membership_request_repository.py).
"""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from watch_party_manager.domain.membership_request import MembershipRequest, MembershipRequestStatus
from watch_party_manager.persistence.membership_request_repository import (
    FIRST_REQUEST_ID,
    MembershipRequestRepository,
)


class MembershipRequestRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "nested" / "membership_requests.json"
        self.repo = MembershipRequestRepository(self.path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_missing_file_loads_empty_with_first_id(self) -> None:
        result = self.repo.load()
        self.assertEqual(result.requests, [])
        self.assertEqual(result.next_id, FIRST_REQUEST_ID)

    def test_save_creates_file_and_round_trips(self) -> None:
        request = MembershipRequest(request_id=1, guild_id=100, user_id=200)
        self.repo.save([request], next_id=2)

        self.assertTrue(self.path.exists())
        result = self.repo.load()
        self.assertEqual(len(result.requests), 1)
        self.assertEqual(result.next_id, 2)
        loaded = result.requests[0]
        self.assertEqual(loaded.request_id, 1)
        self.assertEqual(loaded.guild_id, 100)
        self.assertEqual(loaded.user_id, 200)
        self.assertEqual(loaded.status, MembershipRequestStatus.PENDING)

    def test_round_trips_a_fully_resolved_request(self) -> None:
        resolved_at = datetime.now(timezone.utc)
        request = MembershipRequest(
            request_id=5,
            guild_id=100,
            user_id=200,
            status=MembershipRequestStatus.APPROVED,
            resolved_at=resolved_at,
            resolved_by_user_id=999,
            channel_id=300,
            message_id=400,
        )
        self.repo.save([request], next_id=6)

        loaded = self.repo.load().requests[0]
        self.assertEqual(loaded.status, MembershipRequestStatus.APPROVED)
        self.assertEqual(loaded.resolved_by_user_id, 999)
        self.assertEqual(loaded.channel_id, 300)
        self.assertEqual(loaded.message_id, 400)
        self.assertEqual(loaded.resolved_at, resolved_at)

    def test_multiple_requests_are_preserved(self) -> None:
        first = MembershipRequest(request_id=1, guild_id=100, user_id=200)
        second = MembershipRequest(request_id=2, guild_id=100, user_id=201)
        self.repo.save([first, second], next_id=3)

        result = self.repo.load()
        self.assertEqual({request.request_id for request in result.requests}, {1, 2})

    def test_malformed_file_fails_closed(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text("not json", encoding="utf-8")
        result = self.repo.load()
        self.assertEqual(result.requests, [])
        self.assertEqual(result.next_id, FIRST_REQUEST_ID)

    def test_atomic_write_leaves_no_temporary_file(self) -> None:
        request = MembershipRequest(request_id=1, guild_id=100, user_id=200)
        self.repo.save([request], next_id=2)
        self.assertFalse(self.path.with_suffix(self.path.suffix + ".tmp").exists())

    def test_next_id_defaults_when_missing_from_file(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"requests": []}', encoding="utf-8")
        result = self.repo.load()
        self.assertEqual(result.next_id, FIRST_REQUEST_ID)


class MembershipRequestRepositoryQueryTests(unittest.TestCase):
    """FR-030 refinement: repository-level retrieval, preparing for a
    future administration milestone (no new commands here)."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "membership_requests.json"
        self.repo = MembershipRequestRepository(self.path)

        now = datetime.now(timezone.utc)
        self.pending = MembershipRequest(request_id=1, guild_id=100, user_id=10)
        self.approved = MembershipRequest(
            request_id=2,
            guild_id=100,
            user_id=11,
            status=MembershipRequestStatus.APPROVED,
            resolved_at=now,
            resolved_by_user_id=999,
        )
        self.denied = MembershipRequest(
            request_id=3,
            guild_id=100,
            user_id=10,
            status=MembershipRequestStatus.DENIED,
            resolved_at=now,
            resolved_by_user_id=999,
        )
        self.other_guild_pending = MembershipRequest(request_id=4, guild_id=200, user_id=10)
        self.repo.save([self.pending, self.approved, self.denied, self.other_guild_pending], next_id=5)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_get_pending_returns_only_pending_requests(self) -> None:
        pending = self.repo.get_pending()
        self.assertEqual({request.request_id for request in pending}, {1, 4})

    def test_get_pending_can_be_scoped_to_a_guild(self) -> None:
        pending = self.repo.get_pending(100)
        self.assertEqual([request.request_id for request in pending], [1])

    def test_get_approved_returns_only_approved_requests(self) -> None:
        approved = self.repo.get_approved(100)
        self.assertEqual([request.request_id for request in approved], [2])

    def test_get_denied_returns_only_denied_requests(self) -> None:
        denied = self.repo.get_denied(100)
        self.assertEqual([request.request_id for request in denied], [3])

    def test_get_by_member_returns_every_status_for_that_member(self) -> None:
        history = self.repo.get_by_member(100, 10)
        self.assertEqual([request.request_id for request in history], [1, 3])

    def test_get_by_member_does_not_include_other_members(self) -> None:
        history = self.repo.get_by_member(100, 11)
        self.assertEqual([request.request_id for request in history], [2])


if __name__ == "__main__":
    unittest.main()
