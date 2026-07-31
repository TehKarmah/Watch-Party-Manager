"""Tests for the candidate-selection strategy architecture."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from datetime import date, timedelta

from watch_party_manager.domain.suggestion_database_configuration import CandidateSelectionMode
from watch_party_manager.domain.watch_item import MediaType, WatchItem, WatchItemStatus
from watch_party_manager.domain.watch_item_journey import WatchItemJourney
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.candidate_selection_strategy import (
    CompositeWeighting,
    FavorNewAdditionsStrategy,
    FavorOlderAdditionsStrategy,
    InfinitePoolStrategy,
    MIN_SUGGESTION_DATE_WEIGHT,
    NEUTRAL_WEIGHT,
    SUGGESTION_DATE_WEIGHT_HALF_LIFE_DAYS,
    build_candidate_selection_strategy,
)
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

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _add(self, title: str):
        result = self.suggestion_service.suggest(title, database_id=DATABASE_ID, guild_id=100)
        self.assertTrue(result.success)
        return result.watch_item

    def _item_suggested_days_ago(self, item_id: int, days: int) -> WatchItem:
        """A WatchItem with the given id, aged `days` from today via its
        journey's suggestion_date -- weight_for takes a WatchItem directly
        (never re-fetching from the repository), so this is the simplest
        way to exercise the suggestion-date weighting curve deterministically.
        """
        return WatchItem(
            title=f"Item {item_id}",
            media_type=MediaType.MOVIE,
            id=item_id,
            journey=WatchItemJourney(suggestion_date=date.today() - timedelta(days=days)),
        )


class InfinitePoolStrategyTests(CandidateSelectionStrategyTestCase):
    def test_candidate_pool_includes_every_eligible_suggestion(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        strategy = InfinitePoolStrategy(suggestion_source=self.suggestion_service)

        pool_ids = {item.id for item in strategy.candidate_pool(DATABASE_ID)}

        self.assertEqual(pool_ids, {item_a.id, item_b.id})

    def test_candidate_pool_excludes_a_vote_winner(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        self.suggestion_service.record_vote_win(item_a.id, date.today())
        strategy = InfinitePoolStrategy(suggestion_source=self.suggestion_service)

        pool_ids = {item.id for item in strategy.candidate_pool(DATABASE_ID)}

        self.assertEqual(pool_ids, {item_b.id})

    def test_candidate_pool_excludes_a_watched_item(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        self.suggestion_service.mark_suggestion_watched(item_a.id, date.today())
        strategy = InfinitePoolStrategy(suggestion_source=self.suggestion_service)

        pool_ids = {item.id for item in strategy.candidate_pool(DATABASE_ID)}

        self.assertEqual(pool_ids, {item_b.id})

    def test_weight_for_is_always_neutral(self) -> None:
        item = self._add("Alien")
        strategy = InfinitePoolStrategy(suggestion_source=self.suggestion_service)

        self.assertEqual(strategy.weight_for(item), NEUTRAL_WEIGHT)

    def test_candidate_pool_ignores_a_requested_count(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        strategy = InfinitePoolStrategy(suggestion_source=self.suggestion_service)

        pool_ids = {item.id for item in strategy.candidate_pool(DATABASE_ID, 5)}

        self.assertEqual(pool_ids, {item_a.id, item_b.id})


class FavorNewAdditionsStrategyTests(CandidateSelectionStrategyTestCase):
    def test_candidate_pool_includes_every_eligible_suggestion(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        strategy = FavorNewAdditionsStrategy(suggestion_source=self.suggestion_service)

        pool_ids = {item.id for item in strategy.candidate_pool(DATABASE_ID)}

        self.assertEqual(pool_ids, {item_a.id, item_b.id})

    def test_candidate_pool_excludes_a_vote_winner(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        self.suggestion_service.record_vote_win(item_a.id, date.today())
        strategy = FavorNewAdditionsStrategy(suggestion_source=self.suggestion_service)

        pool_ids = {item.id for item in strategy.candidate_pool(DATABASE_ID)}

        self.assertEqual(pool_ids, {item_b.id})

    def test_candidate_pool_excludes_a_watched_item(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        self.suggestion_service.mark_suggestion_watched(item_a.id, date.today())
        strategy = FavorNewAdditionsStrategy(suggestion_source=self.suggestion_service)

        pool_ids = {item.id for item in strategy.candidate_pool(DATABASE_ID)}

        self.assertEqual(pool_ids, {item_b.id})

    def test_candidate_pool_ignores_a_requested_count(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        strategy = FavorNewAdditionsStrategy(suggestion_source=self.suggestion_service)

        pool_ids = {item.id for item in strategy.candidate_pool(DATABASE_ID, 5)}

        self.assertEqual(pool_ids, {item_a.id, item_b.id})

    def test_weight_for_a_brand_new_suggestion_is_neutral(self) -> None:
        item = self._item_suggested_days_ago(1, days=0)
        strategy = FavorNewAdditionsStrategy(suggestion_source=self.suggestion_service)

        self.assertAlmostEqual(strategy.weight_for(item), NEUTRAL_WEIGHT)

    def test_weight_for_decays_as_the_suggestion_ages(self) -> None:
        newer = self._item_suggested_days_ago(1, days=1)
        older = self._item_suggested_days_ago(2, days=SUGGESTION_DATE_WEIGHT_HALF_LIFE_DAYS * 3)
        strategy = FavorNewAdditionsStrategy(suggestion_source=self.suggestion_service)

        self.assertGreater(strategy.weight_for(newer), strategy.weight_for(older))

    def test_weight_for_approaches_but_never_reaches_the_minimum(self) -> None:
        ancient = self._item_suggested_days_ago(1, days=SUGGESTION_DATE_WEIGHT_HALF_LIFE_DAYS * 50)
        strategy = FavorNewAdditionsStrategy(suggestion_source=self.suggestion_service)

        weight = strategy.weight_for(ancient)

        self.assertGreater(weight, MIN_SUGGESTION_DATE_WEIGHT)
        self.assertAlmostEqual(weight, MIN_SUGGESTION_DATE_WEIGHT, places=6)

    def test_weight_for_falls_back_to_neutral_when_suggestion_date_is_unset(self) -> None:
        # A legacy/imported record predating this field must never be
        # penalized just because its suggestion_date is unknown.
        item = WatchItem(title="Legacy", media_type=MediaType.MOVIE, id=1, journey=WatchItemJourney())
        strategy = FavorNewAdditionsStrategy(suggestion_source=self.suggestion_service)

        self.assertEqual(strategy.weight_for(item), NEUTRAL_WEIGHT)


class FavorOlderAdditionsStrategyTests(CandidateSelectionStrategyTestCase):
    def test_candidate_pool_includes_every_eligible_suggestion(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        strategy = FavorOlderAdditionsStrategy(suggestion_source=self.suggestion_service)

        pool_ids = {item.id for item in strategy.candidate_pool(DATABASE_ID)}

        self.assertEqual(pool_ids, {item_a.id, item_b.id})

    def test_candidate_pool_excludes_a_vote_winner(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        self.suggestion_service.record_vote_win(item_a.id, date.today())
        strategy = FavorOlderAdditionsStrategy(suggestion_source=self.suggestion_service)

        pool_ids = {item.id for item in strategy.candidate_pool(DATABASE_ID)}

        self.assertEqual(pool_ids, {item_b.id})

    def test_candidate_pool_excludes_a_watched_item(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        self.suggestion_service.mark_suggestion_watched(item_a.id, date.today())
        strategy = FavorOlderAdditionsStrategy(suggestion_source=self.suggestion_service)

        pool_ids = {item.id for item in strategy.candidate_pool(DATABASE_ID)}

        self.assertEqual(pool_ids, {item_b.id})

    def test_weight_for_a_brand_new_suggestion_is_the_minimum(self) -> None:
        item = self._item_suggested_days_ago(1, days=0)
        strategy = FavorOlderAdditionsStrategy(suggestion_source=self.suggestion_service)

        self.assertAlmostEqual(strategy.weight_for(item), MIN_SUGGESTION_DATE_WEIGHT, places=6)

    def test_weight_for_grows_as_the_suggestion_ages(self) -> None:
        # The mirror image of FavorNewAdditionsStrategy's decay test.
        newer = self._item_suggested_days_ago(1, days=1)
        older = self._item_suggested_days_ago(2, days=SUGGESTION_DATE_WEIGHT_HALF_LIFE_DAYS * 3)
        strategy = FavorOlderAdditionsStrategy(suggestion_source=self.suggestion_service)

        self.assertLess(strategy.weight_for(newer), strategy.weight_for(older))

    def test_weight_for_approaches_but_never_reaches_neutral(self) -> None:
        ancient = self._item_suggested_days_ago(1, days=SUGGESTION_DATE_WEIGHT_HALF_LIFE_DAYS * 50)
        strategy = FavorOlderAdditionsStrategy(suggestion_source=self.suggestion_service)

        weight = strategy.weight_for(ancient)

        self.assertLess(weight, NEUTRAL_WEIGHT)
        self.assertAlmostEqual(weight, NEUTRAL_WEIGHT, places=6)

    def test_weight_for_falls_back_to_neutral_when_suggestion_date_is_unset(self) -> None:
        item = WatchItem(title="Legacy", media_type=MediaType.MOVIE, id=1, journey=WatchItemJourney())
        strategy = FavorOlderAdditionsStrategy(suggestion_source=self.suggestion_service)

        self.assertEqual(strategy.weight_for(item), NEUTRAL_WEIGHT)


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
    def test_infinite_pool_mode_builds_an_infinite_pool_strategy(self) -> None:
        strategy = build_candidate_selection_strategy(CandidateSelectionMode.INFINITE_POOL, self.suggestion_service)
        self.assertIsInstance(strategy, InfinitePoolStrategy)

    def test_favor_new_additions_mode_builds_a_favor_new_additions_strategy(self) -> None:
        strategy = build_candidate_selection_strategy(
            CandidateSelectionMode.FAVOR_NEW_ADDITIONS, self.suggestion_service
        )
        self.assertIsInstance(strategy, FavorNewAdditionsStrategy)

    def test_favor_older_additions_mode_builds_a_favor_older_additions_strategy(self) -> None:
        strategy = build_candidate_selection_strategy(
            CandidateSelectionMode.FAVOR_OLDER_ADDITIONS, self.suggestion_service
        )
        self.assertIsInstance(strategy, FavorOlderAdditionsStrategy)


if __name__ == "__main__":
    unittest.main()
