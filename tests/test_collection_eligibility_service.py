"""Tests for the Rotation & Collection Health authoritative eligibility
implementation, CollectionEligibilityService.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.suggestion_database_configuration import CandidateSelectionMode
from watch_party_manager.persistence.rotation_repository import JsonRotationRepository
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.collection_eligibility_service import CollectionEligibilityService
from watch_party_manager.services.rotation_service import RotationService
from watch_party_manager.services.suggestion_service import SuggestionService

DATABASE_ID = 1
OTHER_DATABASE_ID = 2


class CollectionEligibilityServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.root = root
        self.suggestion_service = self._new_suggestion_service()
        self.rotation_service = self._new_rotation_service()
        self.eligibility_service = CollectionEligibilityService(self.suggestion_service, self.rotation_service)

    def _new_suggestion_service(self) -> SuggestionService:
        return SuggestionService(
            repository=JsonSuggestionRepository(self.root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(self.root / "suggestion_databases.json"),
        )

    def _new_rotation_service(self) -> RotationService:
        return RotationService(self.suggestion_service, repository=JsonRotationRepository(self.root / "rotations.json"))

    def _add(self, title: str, database_id: int = DATABASE_ID):
        result = self.suggestion_service.suggest(title, database_id=database_id, guild_id=100)
        self.assertTrue(result.success)
        return result.watch_item


class RotationPoolPeekTests(CollectionEligibilityServiceTestCase):
    def test_all_active_items_are_eligible_before_anything_is_presented(self) -> None:
        self._add("Alien")
        self._add("The Matrix")

        result = self.eligibility_service.peek(DATABASE_ID, CandidateSelectionMode.ROTATION_POOL)

        self.assertEqual(len(result.eligible), 2)
        self.assertEqual(len(result.rotation_cooldown), 0)

    def test_never_bootstraps_a_rotation(self) -> None:
        self._add("Alien")

        self.eligibility_service.peek(DATABASE_ID, CandidateSelectionMode.ROTATION_POOL)

        self.assertIsNone(self.rotation_service.get_open_rotation(DATABASE_ID))

    def test_presented_items_move_to_rotation_cooldown(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        self.rotation_service.record_presentation(DATABASE_ID, [item_a.id])

        result = self.eligibility_service.peek(DATABASE_ID, CandidateSelectionMode.ROTATION_POOL)

        self.assertEqual({item.id for item in result.eligible}, {item_b.id})
        self.assertEqual({item.id for item in result.rotation_cooldown}, {item_a.id})

    def test_vote_winners_and_retired_are_bucketed_separately(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        item_c = self._add("Inception")
        self.suggestion_service.record_vote_win(item_a.id, date.today())
        self.suggestion_service.reject_suggestion(item_b.id, 1)
        self.suggestion_service.reject_suggestion(item_b.id, 2)  # default threshold is 2

        result = self.eligibility_service.peek(DATABASE_ID, CandidateSelectionMode.ROTATION_POOL)

        self.assertEqual({item.id for item in result.vote_winners}, {item_a.id})
        self.assertEqual({item.id for item in result.retired}, {item_b.id})
        self.assertEqual({item.id for item in result.eligible}, {item_c.id})

    def test_reconciliation_identities_hold(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        item_c = self._add("Inception")
        item_d = self._add("Arrival")
        self.suggestion_service.record_vote_win(item_a.id, date.today())
        self.suggestion_service.reject_suggestion(item_b.id, 1)
        self.suggestion_service.reject_suggestion(item_b.id, 2)
        self.rotation_service.record_presentation(DATABASE_ID, [item_c.id])

        result = self.eligibility_service.peek(DATABASE_ID, CandidateSelectionMode.ROTATION_POOL)

        self.assertEqual(len(result.active), len(result.eligible) + len(result.rotation_cooldown))
        self.assertEqual(result.total, len(result.active) + len(result.vote_winners) + len(result.retired))
        self.assertEqual(result.total, 4)
        self.assertEqual({item.id for item in result.eligible}, {item_d.id})

    def test_watched_items_are_bucketed_separately_and_counted_in_total(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        self.suggestion_service.mark_suggestion_watched(item_a.id, date.today())

        result = self.eligibility_service.peek(DATABASE_ID, CandidateSelectionMode.ROTATION_POOL)

        self.assertEqual({item.id for item in result.watched}, {item_a.id})
        self.assertEqual({item.id for item in result.eligible}, {item_b.id})
        self.assertEqual(
            result.total, len(result.active) + len(result.vote_winners) + len(result.retired) + len(result.watched)
        )
        self.assertEqual(result.total, 2)

    def test_a_watched_vote_winner_is_bucketed_as_watched_not_vote_winner(self) -> None:
        # Watched is reachable from any prior status and always wins --
        # a suggestion that won a vote and was later confirmed watched
        # must not double-count in both buckets.
        item = self._add("Alien")
        self.suggestion_service.record_vote_win(item.id, date.today())
        self.suggestion_service.mark_suggestion_watched(item.id, date.today())

        result = self.eligibility_service.peek(DATABASE_ID, CandidateSelectionMode.ROTATION_POOL)

        self.assertEqual({i.id for i in result.watched}, {item.id})
        self.assertEqual(result.vote_winners, ())


class SoftRotationAndInfinitePoolPeekTests(CollectionEligibilityServiceTestCase):
    def test_soft_rotation_has_no_cooldown_bucket(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        self.rotation_service.record_presentation(DATABASE_ID, [item_a.id])

        result = self.eligibility_service.peek(DATABASE_ID, CandidateSelectionMode.SOFT_ROTATION)

        self.assertEqual({item.id for item in result.eligible}, {item_a.id, item_b.id})
        self.assertEqual(len(result.rotation_cooldown), 0)

    def test_infinite_pool_has_no_cooldown_bucket(self) -> None:
        self._add("Alien")
        self._add("The Matrix")

        result = self.eligibility_service.peek(DATABASE_ID, CandidateSelectionMode.INFINITE_POOL)

        self.assertEqual(len(result.eligible), 2)
        self.assertEqual(len(result.rotation_cooldown), 0)

    def test_infinite_pool_never_creates_rotation_state(self) -> None:
        self._add("Alien")

        self.eligibility_service.peek(DATABASE_ID, CandidateSelectionMode.INFINITE_POOL)

        self.assertIsNone(self.rotation_service.get_open_rotation(DATABASE_ID))

    def test_soft_rotation_and_infinite_pool_exclude_vote_winners(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        self.suggestion_service.record_vote_win(item_a.id, date.today())

        soft = self.eligibility_service.peek(DATABASE_ID, CandidateSelectionMode.SOFT_ROTATION)
        infinite = self.eligibility_service.peek(DATABASE_ID, CandidateSelectionMode.INFINITE_POOL)

        self.assertEqual({item.id for item in soft.eligible}, {item_b.id})
        self.assertEqual({item.id for item in infinite.eligible}, {item_b.id})


class ResolveRolloverTests(CollectionEligibilityServiceTestCase):
    def test_resolve_with_no_requested_count_does_not_roll_over(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        self.eligibility_service.resolve(DATABASE_ID, CandidateSelectionMode.ROTATION_POOL)
        self.rotation_service.record_presentation(DATABASE_ID, [item_a.id])

        result = self.eligibility_service.resolve(DATABASE_ID, CandidateSelectionMode.ROTATION_POOL)

        self.assertEqual({item.id for item in result.eligible}, {item_b.id})
        self.assertFalse(result.rollover_occurred)

    def test_resolve_rolls_over_when_requested_count_cannot_be_satisfied(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        self.eligibility_service.resolve(DATABASE_ID, CandidateSelectionMode.ROTATION_POOL)
        self.rotation_service.record_presentation(DATABASE_ID, [item_a.id])

        result = self.eligibility_service.resolve(
            DATABASE_ID, CandidateSelectionMode.ROTATION_POOL, requested_count=2
        )

        self.assertEqual({item.id for item in result.eligible}, {item_a.id, item_b.id})
        self.assertTrue(result.rollover_occurred)

    def test_resolve_does_not_roll_over_when_the_rotation_already_satisfies_the_request(self) -> None:
        self._add("Alien")
        self._add("The Matrix")

        result = self.eligibility_service.resolve(
            DATABASE_ID, CandidateSelectionMode.ROTATION_POOL, requested_count=2
        )

        self.assertFalse(result.rollover_occurred)

    def test_resolve_does_not_roll_over_when_it_would_not_help(self) -> None:
        # Nothing presented yet, so every pending suggestion the database
        # has is already in the current rotation -- rolling over could
        # not possibly admit more, so it must be a safe no-op even though
        # requested_count (5) is far more than the database actually has.
        self._add("Alien")

        result = self.eligibility_service.resolve(
            DATABASE_ID, CandidateSelectionMode.ROTATION_POOL, requested_count=5
        )

        self.assertFalse(result.rollover_occurred)
        self.assertEqual(len(result.eligible), 1)

    def test_soft_rotation_requested_count_never_rolls_over(self) -> None:
        item_a = self._add("Alien")
        self.rotation_service.record_presentation(DATABASE_ID, [item_a.id])

        result = self.eligibility_service.resolve(
            DATABASE_ID, CandidateSelectionMode.SOFT_ROTATION, requested_count=5
        )

        self.assertFalse(result.rollover_occurred)

    def test_multiple_databases_are_resolved_independently(self) -> None:
        item_a = self._add("Alien", database_id=DATABASE_ID)
        item_b = self._add("The Matrix", database_id=OTHER_DATABASE_ID)
        self.rotation_service.record_presentation(DATABASE_ID, [item_a.id])

        first = self.eligibility_service.peek(DATABASE_ID, CandidateSelectionMode.ROTATION_POOL)
        second = self.eligibility_service.peek(OTHER_DATABASE_ID, CandidateSelectionMode.ROTATION_POOL)

        self.assertEqual(len(first.eligible), 0)
        self.assertEqual({item.id for item in second.eligible}, {item_b.id})


class RestartSafetyTests(CollectionEligibilityServiceTestCase):
    def test_eligibility_survives_a_simulated_restart(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        self.rotation_service.record_presentation(DATABASE_ID, [item_a.id])

        second_suggestion_service = self._new_suggestion_service()
        second_rotation_service = self._new_rotation_service()
        second_eligibility_service = CollectionEligibilityService(second_suggestion_service, second_rotation_service)

        result = second_eligibility_service.peek(DATABASE_ID, CandidateSelectionMode.ROTATION_POOL)

        self.assertEqual({item.id for item in result.eligible}, {item_b.id})
        self.assertEqual({item.id for item in result.rotation_cooldown}, {item_a.id})


if __name__ == "__main__":
    unittest.main()
