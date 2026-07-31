"""Tests for CollectionEligibilityService, the authoritative eligibility
implementation (Rotation-removal Phase 2: computed entirely from
WatchItemStatus and VoteService, never RotationService).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.services.collection_eligibility_service import CollectionEligibilityService
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.vote_service import VoteService

DATABASE_ID = 1
OTHER_DATABASE_ID = 2


class CollectionEligibilityServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.root = root
        self.suggestion_service = self._new_suggestion_service()
        self.vote_service = self._new_vote_service()
        self.eligibility_service = CollectionEligibilityService(self.suggestion_service, self.vote_service)

    def _new_suggestion_service(self) -> SuggestionService:
        return SuggestionService(
            repository=JsonSuggestionRepository(self.root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(self.root / "suggestion_databases.json"),
        )

    def _new_vote_service(self) -> VoteService:
        return VoteService(self.suggestion_service, repository=JsonVoteRepository(self.root / "voting.json"))

    def _add(self, title: str, database_id: int = DATABASE_ID):
        result = self.suggestion_service.suggest(title, database_id=database_id, guild_id=100)
        self.assertTrue(result.success)
        return result.watch_item


class BasicBucketingTests(CollectionEligibilityServiceTestCase):
    def test_all_suggested_items_are_available_with_no_open_round(self) -> None:
        self._add("Alien")
        self._add("The Matrix")

        result = self.eligibility_service.get_eligibility(DATABASE_ID)

        self.assertEqual(len(result.available), 2)
        self.assertEqual(len(result.in_active_vote), 0)

    def test_vote_winners_and_retired_are_bucketed_separately(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        item_c = self._add("Inception")
        self.suggestion_service.record_vote_win(item_a.id, date.today())
        self.suggestion_service.reject_suggestion(item_b.id, 1)
        self.suggestion_service.reject_suggestion(item_b.id, 2)  # default threshold is 2

        result = self.eligibility_service.get_eligibility(DATABASE_ID)

        self.assertEqual({item.id for item in result.vote_winners}, {item_a.id})
        self.assertEqual({item.id for item in result.retired}, {item_b.id})
        self.assertEqual({item.id for item in result.available}, {item_c.id})

    def test_watched_items_are_bucketed_separately_and_counted_in_total(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        self.suggestion_service.mark_suggestion_watched(item_a.id, date.today())

        result = self.eligibility_service.get_eligibility(DATABASE_ID)

        self.assertEqual({item.id for item in result.watched}, {item_a.id})
        self.assertEqual({item.id for item in result.available}, {item_b.id})
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

        result = self.eligibility_service.get_eligibility(DATABASE_ID)

        self.assertEqual({i.id for i in result.watched}, {item.id})
        self.assertEqual(result.vote_winners, ())

    def test_reconciliation_identities_hold(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        item_c = self._add("Inception")
        item_d = self._add("Arrival")
        self.suggestion_service.record_vote_win(item_a.id, date.today())
        self.suggestion_service.reject_suggestion(item_b.id, 1)
        self.suggestion_service.reject_suggestion(item_b.id, 2)
        self.vote_service.create_round(candidate_suggestion_ids=[item_c.id, item_d.id], database_id=DATABASE_ID)

        result = self.eligibility_service.get_eligibility(DATABASE_ID)

        self.assertEqual(len(result.active), len(result.available) + len(result.in_active_vote))
        self.assertEqual(result.total, len(result.active) + len(result.vote_winners) + len(result.retired))
        self.assertEqual(result.total, 4)
        self.assertEqual({item.id for item in result.in_active_vote}, {item_c.id, item_d.id})

    def test_multiple_databases_are_resolved_independently(self) -> None:
        item_a = self._add("Alien", database_id=DATABASE_ID)
        item_b = self._add("The Matrix", database_id=OTHER_DATABASE_ID)

        first = self.eligibility_service.get_eligibility(DATABASE_ID)
        second = self.eligibility_service.get_eligibility(OTHER_DATABASE_ID)

        self.assertEqual({item.id for item in first.available}, {item_a.id})
        self.assertEqual({item.id for item in second.available}, {item_b.id})


class InActiveVoteBucketingTests(CollectionEligibilityServiceTestCase):
    def test_a_nominated_item_is_in_active_vote_not_available(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        self.vote_service.create_round(candidate_suggestion_ids=[item_a.id, item_b.id], database_id=DATABASE_ID)

        result = self.eligibility_service.get_eligibility(DATABASE_ID)

        self.assertEqual({item.id for item in result.in_active_vote}, {item_a.id, item_b.id})
        self.assertEqual(result.available, ())

    def test_an_item_not_nominated_stays_available_while_another_round_is_open(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        self._add("Inception", database_id=DATABASE_ID)
        self.vote_service.create_round(candidate_suggestion_ids=[item_a.id, item_b.id], database_id=DATABASE_ID)

        result = self.eligibility_service.get_eligibility(DATABASE_ID)

        self.assertEqual({item.id for item in result.in_active_vote}, {item_a.id, item_b.id})
        self.assertEqual(len(result.available), 1)

    def test_no_items_are_in_active_vote_once_the_round_closes(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        created = self.vote_service.create_round(
            candidate_suggestion_ids=[item_a.id, item_b.id], database_id=DATABASE_ID
        )
        self.vote_service.close_round(created.vote_round.id)

        result = self.eligibility_service.get_eligibility(DATABASE_ID)

        self.assertEqual(result.in_active_vote, ())
        self.assertEqual({item.id for item in result.available}, {item_a.id, item_b.id})

    def test_a_different_databases_open_round_never_affects_this_one(self) -> None:
        item_a = self._add("Alien", database_id=DATABASE_ID)
        item_b = self._add("The Matrix", database_id=OTHER_DATABASE_ID)
        other_item = self._add("Arrival", database_id=OTHER_DATABASE_ID)
        self.vote_service.create_round(
            candidate_suggestion_ids=[item_b.id, other_item.id], database_id=OTHER_DATABASE_ID
        )

        result = self.eligibility_service.get_eligibility(DATABASE_ID)

        self.assertEqual({item.id for item in result.available}, {item_a.id})
        self.assertEqual(result.in_active_vote, ())

    def test_eligible_pool_count_excludes_in_active_vote_items(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        self._add("Inception")
        self.vote_service.create_round(candidate_suggestion_ids=[item_a.id, item_b.id], database_id=DATABASE_ID)

        result = self.eligibility_service.get_eligibility(DATABASE_ID)

        self.assertEqual(result.eligible_pool_count, 1)

    def test_never_mutates_anything(self) -> None:
        # get_eligibility is purely read-only -- calling it repeatedly
        # must never change what a later call reports.
        self._add("Alien")

        first = self.eligibility_service.get_eligibility(DATABASE_ID)
        second = self.eligibility_service.get_eligibility(DATABASE_ID)

        self.assertEqual(len(first.available), len(second.available))


class NoVoteServiceTests(CollectionEligibilityServiceTestCase):
    def test_works_with_no_vote_service_configured(self) -> None:
        service = CollectionEligibilityService(self.suggestion_service, None)
        self._add("Alien")

        result = service.get_eligibility(DATABASE_ID)

        self.assertEqual(len(result.available), 1)
        self.assertEqual(result.in_active_vote, ())


class RestartSafetyTests(CollectionEligibilityServiceTestCase):
    def test_eligibility_survives_a_simulated_restart(self) -> None:
        item_a = self._add("Alien")
        item_b = self._add("The Matrix")
        self.vote_service.create_round(candidate_suggestion_ids=[item_a.id, item_b.id], database_id=DATABASE_ID)

        second_suggestion_service = self._new_suggestion_service()
        second_vote_service = VoteService(
            second_suggestion_service, repository=JsonVoteRepository(self.root / "voting.json")
        )
        second_eligibility_service = CollectionEligibilityService(second_suggestion_service, second_vote_service)

        result = second_eligibility_service.get_eligibility(DATABASE_ID)

        self.assertEqual({item.id for item in result.in_active_vote}, {item_a.id, item_b.id})


if __name__ == "__main__":
    unittest.main()
