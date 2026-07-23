"""Tests for the FR-033B Rotation domain model."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from watch_party_manager.domain.rotation import Rotation, RotationStatus


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RotationTests(unittest.TestCase):
    def test_defaults(self) -> None:
        rotation = Rotation(id=1, database_id=10)

        self.assertEqual(rotation.status, RotationStatus.OPEN)
        self.assertIsNone(rotation.completed_at)
        self.assertEqual(rotation.assigned_suggestion_ids, ())

    def test_coerces_a_raw_string_status(self) -> None:
        rotation = Rotation(id=1, database_id=10, status="completed")

        self.assertEqual(rotation.status, RotationStatus.COMPLETED)

    def test_rejects_a_non_positive_id(self) -> None:
        with self.assertRaises(ValueError):
            Rotation(id=0, database_id=10)

    def test_rejects_a_non_positive_database_id(self) -> None:
        with self.assertRaises(ValueError):
            Rotation(id=1, database_id=0)

    def test_rejects_a_naive_started_at(self) -> None:
        with self.assertRaises(ValueError):
            Rotation(id=1, database_id=10, started_at=datetime.now())

    def test_rejects_a_naive_completed_at(self) -> None:
        with self.assertRaises(ValueError):
            Rotation(id=1, database_id=10, completed_at=datetime.now())

    def test_rejects_completed_at_before_started_at(self) -> None:
        now = utc_now()
        with self.assertRaises(ValueError):
            Rotation(id=1, database_id=10, started_at=now, completed_at=now - timedelta(hours=1))

    def test_accepts_completed_at_after_started_at(self) -> None:
        now = utc_now()
        rotation = Rotation(id=1, database_id=10, started_at=now, completed_at=now + timedelta(hours=1))

        self.assertEqual(rotation.completed_at, now + timedelta(hours=1))

    def test_deduplicates_assigned_suggestion_ids(self) -> None:
        rotation = Rotation(id=1, database_id=10, assigned_suggestion_ids=(5, 6, 5, 7))

        self.assertEqual(rotation.assigned_suggestion_ids, (5, 6, 7))

    def test_rejects_non_positive_assigned_suggestion_ids(self) -> None:
        with self.assertRaises(ValueError):
            Rotation(id=1, database_id=10, assigned_suggestion_ids=(1, 0))


if __name__ == "__main__":
    unittest.main()
