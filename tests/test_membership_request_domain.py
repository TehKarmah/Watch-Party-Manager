"""Tests for FR-030's membership request domain model (domain/membership_request.py)."""

import unittest
from datetime import datetime, timezone

from watch_party_manager.domain.membership_request import MembershipRequest, MembershipRequestStatus


class MembershipRequestTests(unittest.TestCase):
    def test_defaults_to_pending_with_no_resolution(self) -> None:
        request = MembershipRequest(request_id=1, guild_id=100, user_id=200)
        self.assertEqual(request.status, MembershipRequestStatus.PENDING)
        self.assertTrue(request.is_pending)
        self.assertIsNone(request.resolved_at)
        self.assertIsNone(request.resolved_by_user_id)

    def test_rejects_non_positive_ids(self) -> None:
        with self.assertRaises(ValueError):
            MembershipRequest(request_id=0, guild_id=100, user_id=200)
        with self.assertRaises(ValueError):
            MembershipRequest(request_id=1, guild_id=-1, user_id=200)
        with self.assertRaises(ValueError):
            MembershipRequest(request_id=1, guild_id=100, user_id=0)

    def test_requires_timezone_aware_created_at(self) -> None:
        with self.assertRaises(ValueError):
            MembershipRequest(request_id=1, guild_id=100, user_id=200, created_at=datetime(2026, 1, 1))

    def test_resolved_request_requires_resolved_at_and_resolver(self) -> None:
        with self.assertRaises(ValueError):
            MembershipRequest(request_id=1, guild_id=100, user_id=200, status=MembershipRequestStatus.APPROVED)

    def test_resolved_at_must_be_timezone_aware(self) -> None:
        with self.assertRaises(ValueError):
            MembershipRequest(
                request_id=1,
                guild_id=100,
                user_id=200,
                status=MembershipRequestStatus.APPROVED,
                resolved_at=datetime(2026, 1, 1),
                resolved_by_user_id=999,
            )

    def test_fully_resolved_request_is_valid_and_not_pending(self) -> None:
        request = MembershipRequest(
            request_id=1,
            guild_id=100,
            user_id=200,
            status=MembershipRequestStatus.DENIED,
            resolved_at=datetime.now(timezone.utc),
            resolved_by_user_id=999,
        )
        self.assertFalse(request.is_pending)


if __name__ == "__main__":
    unittest.main()
