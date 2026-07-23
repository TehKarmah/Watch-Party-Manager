"""Tests for FR-033B's candidate-selection strategy architecture."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from watch_party_manager.domain.suggestion_database_configuration import CandidateSelectionMode
from watch_party_manager.persistence.rotation_repository import JsonRotationRepository
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.candidate_selection_strategy import (
    CompositeWeighting,
    InfinitePoolStrategy,
    NEUTRAL_WEIGHT,
    RotationPoolStrategy,
    SOFT_ROTATION_PRESENTED_WEIGHT,
    SoftRotationStrategy,
    build_candidate_selection_strategy,
)
from watch_party_manager.services.rotation_service import RotationService
from watch_party_manager.services.suggestion_service import SuggestionService

DATABASE_ID = 1


class CandidateSelectionStrategyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.rotation_service = RotationService(
            self.suggestion_service, repository=JsonRotationRepository(root / "rotations.json")
        )

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _add(self, title: str):
        result = self.suggestion_service.suggest(title, database_id=DATABASE_ID, guild_id=100)
        self.assertTrue(result.success)
        return result.watch_item


class RotationPoolStrategyTests(CandidateSelectionStrategyTestCase):
    def test_candidate_pool_excludes_already_presented_items(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        strategy = RotationPoolStrategy(rotation_service=self.rotation_service)
        strategy.on_presented(DATABASE_ID, [item_a.id])

        pool_ids = {item.id for item in strategy.candidate_pool(DATABASE_ID)}

        self.assertEqual(pool_ids, {item_b.id})

    def test_weight_for_is_always_neutral(self) -> None:
        item = self._add("Alien")
        strategy = RotationPoolStrategy(rotation_service=self.rotation_service)

        self.assertEqual(strategy.weight_for(item), NEUTRAL_WEIGHT)

    def test_on_presented_records_presentation_via_rotation_service(self) -> None:
        item = self._add("Alien")
        strategy = RotationPoolStrategy(rotation_service=self.rotation_service)

        strategy.on_presented(DATABASE_ID, [item.id])

        rotation = self.rotation_service.get_open_rotation(DATABASE_ID)
        refreshed = self.suggestion_service.get_suggestion(item.id)
        self.assertIn(rotation.id, refreshed.journey.rotation_history)

    def test_candidate_pool_triggers_automatic_fresh_rotation_when_exhausted(self) -> None:
        item = self._add("Alien")
        strategy = RotationPoolStrategy(rotation_service=self.rotation_service)
        strategy.candidate_pool(DATABASE_ID)
        strategy.on_presented(DATABASE_ID, [item.id])

        pool = strategy.candidate_pool(DATABASE_ID)

        # A fresh rotation re-includes the previously presented item.
        self.assertEqual({candidate.id for candidate in pool}, {item.id})


class SoftRotationStrategyTests(CandidateSelectionStrategyTestCase):
    def test_candidate_pool_includes_everything_including_presented_items(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        strategy = SoftRotationStrategy(rotation_service=self.rotation_service, suggestion_source=self.suggestion_service)
        strategy.on_presented(DATABASE_ID, [item_a.id])

        pool_ids = {item.id for item in strategy.candidate_pool(DATABASE_ID)}

        self.assertEqual(pool_ids, {item_a.id, item_b.id})

    def test_weight_for_is_neutral_before_presentation(self) -> None:
        item = self._add("Alien")
        strategy = SoftRotationStrategy(rotation_service=self.rotation_service, suggestion_source=self.suggestion_service)

        self.assertEqual(strategy.weight_for(item), NEUTRAL_WEIGHT)

    def test_weight_for_drops_after_presentation(self) -> None:
        item = self._add("Alien")
        strategy = SoftRotationStrategy(rotation_service=self.rotation_service, suggestion_source=self.suggestion_service)
        strategy.on_presented(DATABASE_ID, [item.id])

        refreshed = self.suggestion_service.get_suggestion(item.id)

        self.assertEqual(strategy.weight_for(refreshed), SOFT_ROTATION_PRESENTED_WEIGHT)

    def test_weight_for_is_never_zero(self) -> None:
        item = self._add("Alien")
        strategy = SoftRotationStrategy(rotation_service=self.rotation_service, suggestion_source=self.suggestion_service)
        strategy.on_presented(DATABASE_ID, [item.id])

        refreshed = self.suggestion_service.get_suggestion(item.id)

        self.assertGreater(strategy.weight_for(refreshed), 0.0)


class InfinitePoolStrategyTests(CandidateSelectionStrategyTestCase):
    def test_candidate_pool_includes_every_eligible_suggestion(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        strategy = InfinitePoolStrategy(suggestion_source=self.suggestion_service)

        pool_ids = {item.id for item in strategy.candidate_pool(DATABASE_ID)}

        self.assertEqual(pool_ids, {item_a.id, item_b.id})

    def test_weight_for_is_always_neutral(self) -> None:
        item = self._add("Alien")
        strategy = InfinitePoolStrategy(suggestion_source=self.suggestion_service)

        self.assertEqual(strategy.weight_for(item), NEUTRAL_WEIGHT)

    def test_on_presented_never_creates_rotation_state(self) -> None:
        item = self._add("Alien")
        strategy = InfinitePoolStrategy(suggestion_source=self.suggestion_service)

        strategy.on_presented(DATABASE_ID, [item.id])

        self.assertIsNone(self.rotation_service.get_open_rotation(DATABASE_ID))

    def test_candidate_pool_never_creates_rotation_state(self) -> None:
        self._add("Alien")
        strategy = InfinitePoolStrategy(suggestion_source=self.suggestion_service)

        strategy.candidate_pool(DATABASE_ID)

        self.assertIsNone(self.rotation_service.get_open_rotation(DATABASE_ID))


class CompositeWeightingTests(unittest.TestCase):
    def test_multiplies_every_factor_together(self) -> None:
        class HalfWeighting:
            def weight(self, watch_item) -> float:
                return 0.5

        class DoubleWeighting:
            def weight(self, watch_item) -> float:
                return 2.0

        composite = CompositeWeighting(factors=(HalfWeighting(), DoubleWeighting()))

        self.assertEqual(composite.weight(watch_item=None), 1.0)

    def test_an_empty_factor_list_is_neutral(self) -> None:
        composite = CompositeWeighting(factors=())

        self.assertEqual(composite.weight(watch_item=None), NEUTRAL_WEIGHT)


class BuildCandidateSelectionStrategyTests(CandidateSelectionStrategyTestCase):
    def test_rotation_pool_mode_builds_a_rotation_pool_strategy(self) -> None:
        strategy = build_candidate_selection_strategy(
            CandidateSelectionMode.ROTATION_POOL, self.rotation_service, self.suggestion_service
        )
        self.assertIsInstance(strategy, RotationPoolStrategy)

    def test_soft_rotation_mode_builds_a_soft_rotation_strategy(self) -> None:
        strategy = build_candidate_selection_strategy(
            CandidateSelectionMode.SOFT_ROTATION, self.rotation_service, self.suggestion_service
        )
        self.assertIsInstance(strategy, SoftRotationStrategy)

    def test_infinite_pool_mode_builds_an_infinite_pool_strategy(self) -> None:
        strategy = build_candidate_selection_strategy(
            CandidateSelectionMode.INFINITE_POOL, self.rotation_service, self.suggestion_service
        )
        self.assertIsInstance(strategy, InfinitePoolStrategy)


if __name__ == "__main__":
    unittest.main()
