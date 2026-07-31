"""Tests for JsonEligiblePoolWarningStateRepository (Rotation-removal
Phase 2): the Eligible Pool Warning's own armed/disarmed state,
deliberately independent of rotations.json and every Rotation ID.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from watch_party_manager.persistence.eligible_pool_warning_state_repository import (
    JsonEligiblePoolWarningStateRepository,
)


class JsonEligiblePoolWarningStateRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.file_path = Path(self._temp_dir.name) / "eligible_pool_warning_state.json"
        self.repository = JsonEligiblePoolWarningStateRepository(self.file_path)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_loading_a_missing_file_returns_an_empty_set(self) -> None:
        self.assertEqual(self.repository.load(), set())

    def test_save_then_load_round_trips_armed_database_ids(self) -> None:
        self.repository.save({1, 2, 3})

        self.assertEqual(self.repository.load(), {1, 2, 3})

    def test_save_overwrites_previous_contents(self) -> None:
        self.repository.save({1, 2})
        self.repository.save({3})

        self.assertEqual(self.repository.load(), {3})

    def test_save_an_empty_set_clears_previously_armed_databases(self) -> None:
        self.repository.save({1, 2})
        self.repository.save(set())

        self.assertEqual(self.repository.load(), set())

    def test_save_creates_parent_directories(self) -> None:
        nested_path = Path(self._temp_dir.name) / "nested" / "eligible_pool_warning_state.json"
        repository = JsonEligiblePoolWarningStateRepository(nested_path)

        repository.save({1})

        self.assertTrue(nested_path.exists())

    def test_a_corrupt_file_loads_as_an_empty_set_rather_than_crashing(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text("not valid json", encoding="utf-8")

        self.assertEqual(self.repository.load(), set())


if __name__ == "__main__":
    unittest.main()
