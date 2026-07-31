"""Regression tests for status consistency across surfaces: an original
suggestion post must never disagree with /list about the same
suggestion's current status (Available, In an Active Vote, Vote Winner,
Retired, Watched).

Rotation-removal Phase 2: In an Active Vote replaces Rotation Cooldown as
the "computed, not persisted" status these two surfaces must agree on --
both are proven here to resolve it through the same authoritative source,
VoteService.get_open_round_for_suggestion(), via resolve_display_status()
(the post embed) and CollectionEligibilityService (the /list buckets),
rather than asserting against duplicated literals that could drift out of
sync with the real implementation.

Also covers the three call sites an earlier audit found bypassing that
authority by hard-coding no vote_service: build_duplicate_match_line
(/add's duplicate warning), build_removal_option_label (/remove's
selector), and SuggestionService.set_suggestion_status (/edit_suggestion's
Change Status confirmation) -- each would otherwise always report
Available for a suggestion that was actually In an Active Vote.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import (
    build_duplicate_match_line,
    build_removal_option_label,
    build_suggestion_confirmation_embed,
    resolve_suggestion_list_entries,
    SuggestionListStatusFilter,
)
from watch_party_manager.domain.watch_item import WatchItemStatus
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.services.collection_eligibility_service import CollectionEligibilityService
from watch_party_manager.services.duplicate_detection_service import (
    DuplicateMatch,
    DuplicateMatchCategory,
    DuplicateMatchKind,
)
from watch_party_manager.services.suggestion_display_status import SuggestionDisplayStatus
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.vote_service import VoteService

GUILD_ID = 100
CHANNEL_ID = 200


class SuggestionStatusConsistencyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.vote_service = VoteService(
            self.suggestion_service, repository=JsonVoteRepository(root / "voting.json")
        )
        self.eligibility_service = CollectionEligibilityService(self.suggestion_service, self.vote_service)
        self.database = self.suggestion_service.create_database("Movies", GUILD_ID, CHANNEL_ID).database

    def _status_field_value(self, item) -> str:
        embed = build_suggestion_confirmation_embed(
            item, database_name=self.database.name, suggested_by="<@1>", vote_service=self.vote_service
        )
        return next(field.value for field in embed.fields if field.name == "Status")

    def _list_bucket_for(self, item):
        eligibility = self.eligibility_service.get_eligibility(self.database.database_id)
        for status_filter in (
            SuggestionListStatusFilter.ELIGIBLE,
            SuggestionListStatusFilter.IN_ACTIVE_VOTE,
            SuggestionListStatusFilter.VOTE_WINNER,
            SuggestionListStatusFilter.RETIRED,
            SuggestionListStatusFilter.WATCHED,
        ):
            entries = resolve_suggestion_list_entries(eligibility, status_filter)
            for entry_item, display_status in entries:
                if entry_item.id == item.id:
                    return display_status
        return None


class OriginalPostMatchesListTests(SuggestionStatusConsistencyTestCase):
    def test_eligible_item_agrees_between_post_and_list(self) -> None:
        item = self.suggestion_service.suggest("Alien", database_id=self.database.database_id).watch_item

        self.assertIn("🟢 Available", self._status_field_value(item))
        self.assertEqual(self._list_bucket_for(item), SuggestionDisplayStatus.AVAILABLE)

    def test_in_an_active_vote_item_agrees_between_post_and_list(self) -> None:
        item = self.suggestion_service.suggest("Alien", database_id=self.database.database_id).watch_item
        other = self.suggestion_service.suggest("The Matrix", database_id=self.database.database_id).watch_item
        self.vote_service.create_round(
            candidate_suggestion_ids=[item.id, other.id], database_id=self.database.database_id
        )
        nominated_item = self.suggestion_service.get_suggestion(item.id)

        self.assertIn("🗳️ In an Active Vote", self._status_field_value(nominated_item))
        self.assertEqual(self._list_bucket_for(nominated_item), SuggestionDisplayStatus.IN_ACTIVE_VOTE)

    def test_vote_winner_item_agrees_between_post_and_list(self) -> None:
        item = self.suggestion_service.suggest("Alien", database_id=self.database.database_id).watch_item
        self.suggestion_service.set_suggestion_status(item.id, WatchItemStatus.VOTE_WINNER, self.vote_service)
        winner_item = self.suggestion_service.get_suggestion(item.id)

        self.assertIn("🏆 Vote Winner", self._status_field_value(winner_item))
        self.assertEqual(self._list_bucket_for(winner_item), SuggestionDisplayStatus.VOTE_WINNER)

    def test_retired_item_agrees_between_post_and_list(self) -> None:
        item = self.suggestion_service.suggest("Alien", database_id=self.database.database_id).watch_item
        self.suggestion_service.archive_suggestion(item.id)
        retired_item = self.suggestion_service.get_suggestion(item.id)

        self.assertIn("🗄️ Retired", self._status_field_value(retired_item))
        self.assertEqual(self._list_bucket_for(retired_item), SuggestionDisplayStatus.RETIRED)

    def test_watched_item_agrees_between_post_and_list(self) -> None:
        from datetime import date

        item = self.suggestion_service.suggest("Alien", database_id=self.database.database_id).watch_item
        self.suggestion_service.mark_suggestion_watched(item.id, date.today())
        watched_item = self.suggestion_service.get_suggestion(item.id)

        self.assertIn("✅ Watched", self._status_field_value(watched_item))
        self.assertEqual(self._list_bucket_for(watched_item), SuggestionDisplayStatus.WATCHED)

    def test_an_in_active_vote_item_reactivated_to_suggested_status_disagreement_bug_is_fixed(self) -> None:
        # The same class of bug the original rotation-cooldown audit
        # fixed, now for In an Active Vote: SuggestionService.
        # set_suggestion_status must not hard-code "not in an active
        # vote" -- moving an item back to SUGGESTED while it's still
        # genuinely nominated in an open round must report In an Active
        # Vote, not Available, matching both the post and /list.
        item = self.suggestion_service.suggest("Alien", database_id=self.database.database_id).watch_item
        other = self.suggestion_service.suggest("The Matrix", database_id=self.database.database_id).watch_item
        self.vote_service.create_round(
            candidate_suggestion_ids=[item.id, other.id], database_id=self.database.database_id
        )

        result = self.suggestion_service.set_suggestion_status(
            item.id, WatchItemStatus.SUGGESTED, self.vote_service
        )

        self.assertIn("In an Active Vote", result.message)
        self.assertNotIn("status set to Available", result.message)


class DuplicateWarningAndRemovalSelectorInActiveVoteTests(SuggestionStatusConsistencyTestCase):
    def test_duplicate_match_line_reports_the_real_in_an_active_vote_status(self) -> None:
        item = self.suggestion_service.suggest("Alien", database_id=self.database.database_id).watch_item
        other = self.suggestion_service.suggest("The Matrix", database_id=self.database.database_id).watch_item
        self.vote_service.create_round(
            candidate_suggestion_ids=[item.id, other.id], database_id=self.database.database_id
        )
        nominated_item = self.suggestion_service.get_suggestion(item.id)
        match = DuplicateMatch(
            watch_item=nominated_item,
            category=DuplicateMatchCategory.ACTIVE,
            kind=DuplicateMatchKind.TITLE_AND_YEAR,
        )

        line = build_duplicate_match_line(match, self.vote_service)

        self.assertIn("In an Active Vote", line)
        self.assertNotIn("status: 🟢 Available", line)

    def test_removal_option_label_reports_the_real_in_an_active_vote_status(self) -> None:
        item = self.suggestion_service.suggest("Alien", database_id=self.database.database_id).watch_item
        other = self.suggestion_service.suggest("The Matrix", database_id=self.database.database_id).watch_item
        self.vote_service.create_round(
            candidate_suggestion_ids=[item.id, other.id], database_id=self.database.database_id
        )
        nominated_item = self.suggestion_service.get_suggestion(item.id)

        label = build_removal_option_label(nominated_item, self.suggestion_service, self.vote_service)

        self.assertIn("In an Active Vote", label)


if __name__ == "__main__":
    unittest.main()
