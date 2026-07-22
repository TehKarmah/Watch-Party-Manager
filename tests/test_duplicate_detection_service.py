"""Tests for FR-033A's duplicate detection service."""

from __future__ import annotations

import unittest

from watch_party_manager.domain.watch_item import MediaType, MetadataProvider, WatchItem, WatchItemStatus
from watch_party_manager.domain.watch_item_journey import WatchItemJourney
from watch_party_manager.services.duplicate_detection_service import (
    DuplicateMatchCategory,
    DuplicateMatchKind,
    categorize_watch_item,
    extract_imdb_id,
    find_duplicates,
    normalize_title_for_comparison,
)


class NormalizeTitleForComparisonTests(unittest.TestCase):
    def test_normalizes_case(self) -> None:
        self.assertEqual(normalize_title_for_comparison("ALIEN"), normalize_title_for_comparison("alien"))

    def test_normalizes_leading_and_trailing_whitespace(self) -> None:
        self.assertEqual(normalize_title_for_comparison("  Alien  "), normalize_title_for_comparison("Alien"))

    def test_normalizes_repeated_internal_whitespace(self) -> None:
        self.assertEqual(normalize_title_for_comparison("Alien   Resurrection"), normalize_title_for_comparison("Alien Resurrection"))

    def test_does_not_strip_punctuation(self) -> None:
        self.assertNotEqual(normalize_title_for_comparison("Alien: Resurrection"), normalize_title_for_comparison("Alien Resurrection"))

    def test_different_titles_remain_different(self) -> None:
        self.assertNotEqual(normalize_title_for_comparison("Alien"), normalize_title_for_comparison("Aliens"))


class ExtractImdbIdTests(unittest.TestCase):
    def test_extracts_id_from_canonical_url(self) -> None:
        self.assertEqual("tt0078748", extract_imdb_id("https://www.imdb.com/title/tt0078748/"))

    def test_returns_none_for_no_url(self) -> None:
        self.assertIsNone(extract_imdb_id(None))
        self.assertIsNone(extract_imdb_id(""))

    def test_returns_none_when_no_id_present(self) -> None:
        self.assertIsNone(extract_imdb_id("https://www.imdb.com/"))

    def test_case_insensitive(self) -> None:
        self.assertEqual("tt0078748", extract_imdb_id("https://www.imdb.com/title/TT0078748/"))


class CategorizeWatchItemTests(unittest.TestCase):
    def _item(self, status: WatchItemStatus, rejected_ids=()) -> WatchItem:
        return WatchItem(
            title="Alien",
            media_type=MediaType.MOVIE,
            status=status,
            journey=WatchItemJourney(rejected_by_discord_user_ids=rejected_ids),
        )

    def test_active_status_categorized_as_active(self) -> None:
        self.assertEqual(DuplicateMatchCategory.ACTIVE, categorize_watch_item(self._item(WatchItemStatus.SUGGESTED)))

    def test_eligible_status_categorized_as_active(self) -> None:
        self.assertEqual(DuplicateMatchCategory.ACTIVE, categorize_watch_item(self._item(WatchItemStatus.ELIGIBLE)))

    def test_watched_status_categorized_as_watched(self) -> None:
        self.assertEqual(DuplicateMatchCategory.WATCHED, categorize_watch_item(self._item(WatchItemStatus.WATCHED)))

    def test_archived_with_rejections_categorized_as_archived_rejected(self) -> None:
        item = self._item(WatchItemStatus.ARCHIVED, rejected_ids=(1, 2))
        self.assertEqual(DuplicateMatchCategory.ARCHIVED_REJECTED, categorize_watch_item(item))

    def test_archived_without_rejections_categorized_as_archived_other(self) -> None:
        item = self._item(WatchItemStatus.ARCHIVED, rejected_ids=())
        self.assertEqual(DuplicateMatchCategory.ARCHIVED_OTHER, categorize_watch_item(item))


class FindDuplicatesTests(unittest.TestCase):
    def _item(self, title, *, year=None, imdb=None, status=WatchItemStatus.SUGGESTED, item_id=1, rejected_ids=()):
        metadata_ids = {MetadataProvider.IMDB: imdb} if imdb else {}
        return WatchItem(
            title=title,
            media_type=MediaType.MOVIE,
            metadata_ids=metadata_ids,
            release_year=year,
            status=status,
            id=item_id,
            journey=WatchItemJourney(rejected_by_discord_user_ids=rejected_ids),
        )

    def test_no_matches_when_nothing_similar_exists(self) -> None:
        existing = [self._item("Inception", year=2010)]

        result = find_duplicates(title="Alien", release_year=1979, imdb_url=None, existing_items=existing)

        self.assertFalse(result.has_matches)

    def test_imdb_id_match_is_definite(self) -> None:
        existing = [self._item("Alien", imdb="https://www.imdb.com/title/tt0078748/")]

        result = find_duplicates(
            title="Alien (Director's Cut)",
            release_year=None,
            imdb_url="https://www.imdb.com/title/tt0078748/",
            existing_items=existing,
        )

        self.assertTrue(result.has_definite_match)
        self.assertEqual(DuplicateMatchKind.IMDB, result.matches[0].kind)

    def test_title_and_year_match_is_definite(self) -> None:
        existing = [self._item("Alien", year=1979)]

        result = find_duplicates(title="Alien", release_year=1979, imdb_url=None, existing_items=existing)

        self.assertTrue(result.has_definite_match)
        self.assertEqual(DuplicateMatchKind.TITLE_AND_YEAR, result.matches[0].kind)

    def test_case_normalization_still_matches(self) -> None:
        existing = [self._item("ALIEN", year=1979)]

        result = find_duplicates(title="alien", release_year=1979, imdb_url=None, existing_items=existing)

        self.assertTrue(result.has_definite_match)

    def test_whitespace_normalization_still_matches(self) -> None:
        existing = [self._item("Alien   Resurrection", year=1997)]

        result = find_duplicates(
            title="  Alien Resurrection  ", release_year=1997, imdb_url=None, existing_items=existing
        )

        self.assertTrue(result.has_definite_match)

    def test_same_title_different_known_years_is_not_a_duplicate(self) -> None:
        existing = [self._item("A Star Is Born", year=1976)]

        result = find_duplicates(title="A Star Is Born", release_year=2018, imdb_url=None, existing_items=existing)

        self.assertFalse(result.has_matches)

    def test_possible_duplicate_when_candidate_has_no_year(self) -> None:
        existing = [self._item("Alien", year=1979)]

        result = find_duplicates(title="Alien", release_year=None, imdb_url=None, existing_items=existing)

        self.assertTrue(result.has_possible_only)
        self.assertEqual(DuplicateMatchKind.TITLE_ONLY, result.matches[0].kind)

    def test_possible_duplicate_when_existing_has_no_year(self) -> None:
        existing = [self._item("Alien", year=None)]

        result = find_duplicates(title="Alien", release_year=1979, imdb_url=None, existing_items=existing)

        self.assertTrue(result.has_possible_only)

    def test_shows_all_matching_items(self) -> None:
        existing = [
            self._item("Alien", year=None, item_id=1),
            self._item("Alien", year=None, item_id=2),
        ]

        result = find_duplicates(title="Alien", release_year=None, imdb_url=None, existing_items=existing)

        self.assertEqual(2, len(result.matches))

    def test_checks_active_items(self) -> None:
        existing = [self._item("Alien", year=1979, status=WatchItemStatus.SUGGESTED)]
        result = find_duplicates(title="Alien", release_year=1979, imdb_url=None, existing_items=existing)
        self.assertEqual(DuplicateMatchCategory.ACTIVE, result.matches[0].category)

    def test_checks_archived_items(self) -> None:
        existing = [self._item("Alien", year=1979, status=WatchItemStatus.ARCHIVED, rejected_ids=(1, 2))]
        result = find_duplicates(title="Alien", release_year=1979, imdb_url=None, existing_items=existing)
        self.assertEqual(DuplicateMatchCategory.ARCHIVED_REJECTED, result.matches[0].category)

    def test_checks_watched_items(self) -> None:
        existing = [self._item("Alien", year=1979, status=WatchItemStatus.WATCHED)]
        result = find_duplicates(title="Alien", release_year=1979, imdb_url=None, existing_items=existing)
        self.assertEqual(DuplicateMatchCategory.WATCHED, result.matches[0].category)

    def test_excludes_the_candidates_own_id(self) -> None:
        existing = [self._item("Alien", year=1979, item_id=42)]

        result = find_duplicates(
            title="Alien", release_year=1979, imdb_url=None, existing_items=existing, exclude_id=42
        )

        self.assertFalse(result.has_matches)

    def test_database_isolation_is_the_callers_responsibility(self) -> None:
        # find_duplicates only ever sees what it's given -- passing an
        # empty list (as a caller would for an unrelated database)
        # naturally yields no matches, without find_duplicates itself
        # needing any notion of "database."
        result = find_duplicates(title="Alien", release_year=1979, imdb_url=None, existing_items=[])

        self.assertFalse(result.has_matches)


if __name__ == "__main__":
    unittest.main()
