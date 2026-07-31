"""Tests for FR-033B's candidate-selection strategy architecture."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from datetime import date, timedelta

from watch_party_manager.domain.suggestion_database_configuration import CandidateSelectionMode
from watch_party_manager.domain.watch_item import MediaType, WatchItem, WatchItemStatus
from watch_party_manager.domain.watch_item_journey import WatchItemJourney
from watch_party_manager.persistence.rotation_repository import JsonRotationRepository
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.candidate_selection_strategy import (
    CompositeWeighting,
    FavorNewAdditionsStrategy,
    FavorOlderAdditionsStrategy,
    InfinitePoolStrategy,
    MIN_SUGGESTION_DATE_WEIGHT,
    NEUTRAL_WEIGHT,
    RotationPoolStrategy,
    SOFT_ROTATION_PRESENTED_WEIGHT,
    SUGGESTION_DATE_WEIGHT_HALF_LIFE_DAYS,
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


class RotationPoolStrategyTests(CandidateSelectionStrategyTestCase):
    def test_candidate_pool_excludes_already_presented_items(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        strategy = RotationPoolStrategy(rotation_service=self.rotation_service)
        strategy.on_presented(DATABASE_ID, [item_a.id])

        pool_ids = {item.id for item in strategy.candidate_pool(DATABASE_ID)}

        self.assertEqual(pool_ids, {item_b.id})

    def test_candidate_pool_excludes_a_watched_item(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        strategy = RotationPoolStrategy(rotation_service=self.rotation_service)
        strategy.candidate_pool(DATABASE_ID)
        self.suggestion_service.mark_suggestion_watched(item_a.id, date.today())

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

    def test_candidate_pool_rolls_over_when_a_requested_count_cannot_be_satisfied(self) -> None:
        """Release-blocking rotation rollover fix: with no requested_count
        given, a rotation with pending items left (even below what a vote
        needs) is left alone -- see the exhaustion-only test above. Once
        a requested_count is supplied, the same call rolls the rotation
        forward instead, since it isn't fully exhausted but also can't
        satisfy the request.
        """
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        strategy = RotationPoolStrategy(rotation_service=self.rotation_service)
        strategy.candidate_pool(DATABASE_ID)
        strategy.on_presented(DATABASE_ID, [item_a.id])

        no_count_pool = strategy.candidate_pool(DATABASE_ID)
        self.assertEqual({candidate.id for candidate in no_count_pool}, {item_b.id})

        with_count_pool = strategy.candidate_pool(DATABASE_ID, 2)
        self.assertEqual({candidate.id for candidate in with_count_pool}, {item_a.id, item_b.id})


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

    def test_candidate_pool_excludes_a_vote_winner(self) -> None:
        # Rotation & Collection Health Audit bug fix: a Vote Winner must
        # never be selectable again in any mode, including Soft Rotation
        # (which otherwise excludes nothing) -- get_suggestions_for_
        # database's own default only ever excludes Archived, so this
        # exclusion must be explicit here.
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        self.suggestion_service.record_vote_win(item_a.id, date.today())
        strategy = SoftRotationStrategy(rotation_service=self.rotation_service, suggestion_source=self.suggestion_service)

        pool_ids = {item.id for item in strategy.candidate_pool(DATABASE_ID)}

        self.assertEqual(pool_ids, {item_b.id})

    def test_candidate_pool_excludes_a_watched_item(self) -> None:
        # Watched Button & Archive Workflow: a Watched item must never
        # be selectable again either, for the exact same reason as a
        # Vote Winner above.
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        self.suggestion_service.mark_suggestion_watched(item_a.id, date.today())
        strategy = SoftRotationStrategy(rotation_service=self.rotation_service, suggestion_source=self.suggestion_service)

        pool_ids = {item.id for item in strategy.candidate_pool(DATABASE_ID)}

        self.assertEqual(pool_ids, {item_b.id})

    def test_candidate_pool_ignores_a_requested_count_and_never_rolls_over(self) -> None:
        """Soft Rotation never excludes anything, so there is nothing for
        a requested vote size to roll over -- passing requested_count
        must not change the pool or create rotation state, preserving
        this mode's existing behavior exactly.
        """
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        strategy = SoftRotationStrategy(rotation_service=self.rotation_service, suggestion_source=self.suggestion_service)
        strategy.on_presented(DATABASE_ID, [item_a.id])

        pool_ids = {item.id for item in strategy.candidate_pool(DATABASE_ID, 5)}

        self.assertEqual(pool_ids, {item_a.id, item_b.id})


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

    def test_candidate_pool_ignores_a_requested_count(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        strategy = InfinitePoolStrategy(suggestion_source=self.suggestion_service)

        pool_ids = {item.id for item in strategy.candidate_pool(DATABASE_ID, 5)}

        self.assertEqual(pool_ids, {item_a.id, item_b.id})
        self.assertIsNone(self.rotation_service.get_open_rotation(DATABASE_ID))


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

    def test_candidate_pool_never_creates_rotation_state(self) -> None:
        self._add("Alien")
        strategy = FavorNewAdditionsStrategy(suggestion_source=self.suggestion_service)

        strategy.candidate_pool(DATABASE_ID)

        self.assertIsNone(self.rotation_service.get_open_rotation(DATABASE_ID))

    def test_on_presented_never_creates_rotation_state(self) -> None:
        item = self._add("Alien")
        strategy = FavorNewAdditionsStrategy(suggestion_source=self.suggestion_service)

        strategy.on_presented(DATABASE_ID, [item.id])

        self.assertIsNone(self.rotation_service.get_open_rotation(DATABASE_ID))

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

    def test_candidate_pool_never_creates_rotation_state(self) -> None:
        self._add("Alien")
        strategy = FavorOlderAdditionsStrategy(suggestion_source=self.suggestion_service)

        strategy.candidate_pool(DATABASE_ID)

        self.assertIsNone(self.rotation_service.get_open_rotation(DATABASE_ID))

    def test_on_presented_never_creates_rotation_state(self) -> None:
        item = self._add("Alien")
        strategy = FavorOlderAdditionsStrategy(suggestion_source=self.suggestion_service)

        strategy.on_presented(DATABASE_ID, [item.id])

        self.assertIsNone(self.rotation_service.get_open_rotation(DATABASE_ID))

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

    def test_favor_new_additions_mode_builds_a_favor_new_additions_strategy(self) -> None:
        strategy = build_candidate_selection_strategy(
            CandidateSelectionMode.FAVOR_NEW_ADDITIONS, self.rotation_service, self.suggestion_service
        )
        self.assertIsInstance(strategy, FavorNewAdditionsStrategy)

    def test_favor_older_additions_mode_builds_a_favor_older_additions_strategy(self) -> None:
        strategy = build_candidate_selection_strategy(
            CandidateSelectionMode.FAVOR_OLDER_ADDITIONS, self.rotation_service, self.suggestion_service
        )
        self.assertIsInstance(strategy, FavorOlderAdditionsStrategy)


if __name__ == "__main__":
    unittest.main()
