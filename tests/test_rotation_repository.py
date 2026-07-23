"""Tests for JsonRotationRepository (FR-033B rotation persistence)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from watch_party_manager.domain.rotation import Rotation, RotationStatus
from watch_party_manager.persistence.rotation_repository import JsonRotationRepository


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class JsonRotationRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.file_path = Path(self._temp_dir.name) / "rotations.json"
        self.repository = JsonRotationRepository(self.file_path)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_loading_a_missing_file_returns_empty_state(self) -> None:
        result = self.repository.load()

        self.assertEqual(result.rotations, [])
        self.assertEqual(result.next_rotation_id, 1)
        self.assertEqual(result.low_pool_reminder_last_sent_at, {})

    def test_save_then_load_round_trips_a_rotation(self) -> None:
        rotation = Rotation(
            id=1,
            database_id=10,
            status=RotationStatus.OPEN,
            assigned_suggestion_ids=(1, 2, 3),
        )
        self.repository.save([rotation], 2, {})

        loaded = self.repository.load()

        self.assertEqual(len(loaded.rotations), 1)
        self.assertEqual(loaded.rotations[0].id, 1)
        self.assertEqual(loaded.rotations[0].database_id, 10)
        self.assertEqual(loaded.rotations[0].assigned_suggestion_ids, (1, 2, 3))
        self.assertEqual(loaded.next_rotation_id, 2)

    def test_save_then_load_round_trips_a_completed_rotation(self) -> None:
        started = utc_now()
        completed = started
        rotation = Rotation(
            id=1, database_id=10, status=RotationStatus.COMPLETED, started_at=started, completed_at=completed
        )
        self.repository.save([rotation], 2, {})

        loaded = self.repository.load()

        self.assertEqual(loaded.rotations[0].status, RotationStatus.COMPLETED)
        self.assertIsNotNone(loaded.rotations[0].completed_at)

    def test_save_then_load_round_trips_the_low_pool_reminder_timestamp(self) -> None:
        sent_at = utc_now()
        self.repository.save([], 1, {10: sent_at})

        loaded = self.repository.load()

        self.assertEqual(loaded.low_pool_reminder_last_sent_at[10], sent_at)

    def test_save_creates_parent_directories(self) -> None:
        nested_path = Path(self._temp_dir.name) / "nested" / "rotations.json"
        repository = JsonRotationRepository(nested_path)

        repository.save([], 1, {})

        self.assertTrue(nested_path.exists())

    def test_a_corrupt_file_loads_as_empty_state_rather_than_crashing(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text("not valid json", encoding="utf-8")

        result = self.repository.load()

        self.assertEqual(result.rotations, [])
        self.assertEqual(result.next_rotation_id, 1)

    def test_save_overwrites_previous_contents(self) -> None:
        self.repository.save([Rotation(id=1, database_id=10)], 2, {})
        self.repository.save([Rotation(id=2, database_id=20)], 3, {})

        loaded = self.repository.load()

        self.assertEqual(len(loaded.rotations), 1)
        self.assertEqual(loaded.rotations[0].id, 2)


if __name__ == "__main__":
    unittest.main()
