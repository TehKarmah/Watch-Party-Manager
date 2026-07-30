"""Regression tests for the rotation-state consistency audit: an original
suggestion post must never disagree with /list about the same suggestion's
current status (Eligible, Rotation Cooldown, Vote Winner, Retired, Watched).

Both surfaces are proven here to resolve status through the same
authoritative source -- RotationService.is_in_rotation_cooldown(), via
resolve_display_status() (the post embed) and CollectionEligibilityService
(the /list buckets) -- rather than asserting against duplicated literals
that could drift out of sync with the real implementation.

Also covers the three call sites the audit found bypassing that authority
by hard-coding in_rotation_cooldown=False: build_duplicate_match_line
(/add's duplicate warning), build_removal_option_label (/remove's
selector), and SuggestionService.set_suggestion_status (/edit_suggestion's
Change Status confirmation) -- each used to always report Available for a
suggestion that was actually on Rotation Cooldown.
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
from watch_party_manager.domain.suggestion_database_configuration import CandidateSelectionMode
from watch_party_manager.domain.watch_item import WatchItemStatus
from watch_party_manager.persistence.rotation_repository import JsonRotationRepository
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.collection_eligibility_service import CollectionEligibilityService
from watch_party_manager.services.duplicate_detection_service import (
    DuplicateMatch,
    DuplicateMatchCategory,
    DuplicateMatchKind,
)
from watch_party_manager.services.rotation_service import RotationService
from watch_party_manager.services.suggestion_display_status import SuggestionDisplayStatus
from watch_party_manager.services.suggestion_service import SuggestionService

GUILD_ID = 100
CHANNEL_ID = 200


class RotationStatusConsistencyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.rotation_service = RotationService(
            self.suggestion_service, repository=JsonRotationRepository(root / "rotations.json")
        )
        self.eligibility_service = CollectionEligibilityService(self.suggestion_service, self.rotation_service)
        self.database = self.suggestion_service.create_database("Movies", GUILD_ID, CHANNEL_ID).database

    def _status_field_value(self, item) -> str:
        embed = build_suggestion_confirmation_embed(
            item, database_name=self.database.name, suggested_by="<@1>", rotation_service=self.rotation_service
        )
        return next(field.value for field in embed.fields if field.name == "Status")

    def _list_bucket_for(self, item):
        eligibility = self.eligibility_service.peek(self.database.database_id, CandidateSelectionMode.ROTATION_POOL)
        for status_filter in (
            SuggestionListStatusFilter.ELIGIBLE,
            SuggestionListStatusFilter.ROTATION_COOLDOWN,
            SuggestionListStatusFilter.VOTE_WINNER,
            SuggestionListStatusFilter.RETIRED,
            SuggestionListStatusFilter.WATCHED,
        ):
            entries = resolve_suggestion_list_entries(eligibility, status_filter)
            for entry_item, display_status in entries:
                if entry_item.id == item.id:
                    return display_status
        return None


class OriginalPostMatchesListTests(RotationStatusConsistencyTestCase):
    def test_eligible_item_agrees_between_post_and_list(self) -> None:
        item = self.suggestion_service.suggest("Alien", database_id=self.database.database_id).watch_item

        self.assertIn("🟢 Available", self._status_field_value(item))
        self.assertEqual(self._list_bucket_for(item), SuggestionDisplayStatus.AVAILABLE)

    def test_rotation_cooldown_item_agrees_between_post_and_list(self) -> None:
        item = self.suggestion_service.suggest("Alien", database_id=self.database.database_id).watch_item
        self.rotation_service.get_or_start_rotation(self.database.database_id)
        self.rotation_service.record_presentation(self.database.database_id, [item.id])
        presented_item = self.suggestion_service.get_suggestion(item.id)

        self.assertIn("🟡 Rotation Cooldown", self._status_field_value(presented_item))
        self.assertEqual(self._list_bucket_for(presented_item), SuggestionDisplayStatus.ROTATION_COOLDOWN)

    def test_vote_winner_item_agrees_between_post_and_list(self) -> None:
        item = self.suggestion_service.suggest("Alien", database_id=self.database.database_id).watch_item
        self.suggestion_service.set_suggestion_status(item.id, WatchItemStatus.VOTE_WINNER, self.rotation_service)
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

    def test_a_rotation_cooldown_item_reactivated_to_suggested_status_disagreement_bug_is_fixed(self) -> None:
        # Section 2/5 bug fix: SuggestionService.set_suggestion_status used
        # to hard-code in_rotation_cooldown=False, so moving an item back
        # to SUGGESTED while it was still genuinely on Rotation Cooldown
        # reported "Available" in the confirmation message even though
        # both the post and /list would show Rotation Cooldown.
        item = self.suggestion_service.suggest("Alien", database_id=self.database.database_id).watch_item
        self.rotation_service.get_or_start_rotation(self.database.database_id)
        self.rotation_service.record_presentation(self.database.database_id, [item.id])

        result = self.suggestion_service.set_suggestion_status(
            item.id, WatchItemStatus.SUGGESTED, self.rotation_service
        )

        self.assertIn("Rotation Cooldown", result.message)
        self.assertNotIn("status set to Available", result.message)


class DuplicateWarningAndRemovalSelectorRotationCooldownTests(RotationStatusConsistencyTestCase):
    def test_duplicate_match_line_reports_the_real_rotation_cooldown_status(self) -> None:
        item = self.suggestion_service.suggest("Alien", database_id=self.database.database_id).watch_item
        self.rotation_service.get_or_start_rotation(self.database.database_id)
        self.rotation_service.record_presentation(self.database.database_id, [item.id])
        presented_item = self.suggestion_service.get_suggestion(item.id)
        match = DuplicateMatch(
            watch_item=presented_item,
            category=DuplicateMatchCategory.ACTIVE,
            kind=DuplicateMatchKind.TITLE_AND_YEAR,
        )

        line = build_duplicate_match_line(match, self.rotation_service)

        self.assertIn("Rotation Cooldown", line)
        self.assertNotIn("status: 🟢 Available", line)

    def test_removal_option_label_reports_the_real_rotation_cooldown_status(self) -> None:
        item = self.suggestion_service.suggest("Alien", database_id=self.database.database_id).watch_item
        self.rotation_service.get_or_start_rotation(self.database.database_id)
        self.rotation_service.record_presentation(self.database.database_id, [item.id])
        presented_item = self.suggestion_service.get_suggestion(item.id)

        label = build_removal_option_label(presented_item, self.suggestion_service, self.rotation_service)

        self.assertIn("Rotation Cooldown", label)


if __name__ == "__main__":
    unittest.main()
