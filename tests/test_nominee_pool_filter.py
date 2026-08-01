"""Tests for the Custom Vote Filter Architecture (services/nominee_pool_filter.py)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from watch_party_manager.domain.watch_item import MediaType, WatchItem
from watch_party_manager.domain.watch_item_journey import WatchItemJourney
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.candidate_selection_strategy import InfinitePoolStrategy
from watch_party_manager.services.nominee_pool_filter import (
    FilteredCandidateSelectionStrategy,
    GenreFilter,
    MemberSuggestionFilter,
    apply_nominee_pool_filters,
    genre_eligibility_counts,
)
from watch_party_manager.services.suggestion_service import SuggestionService

DATABASE_ID = 1


def _item(item_id: int, *, original_suggester=None, genres=()) -> WatchItem:
    return WatchItem(
        title=f"Item {item_id}",
        media_type=MediaType.MOVIE,
        id=item_id,
        genres=genres,
        journey=WatchItemJourney(original_suggester=original_suggester),
    )


class MemberSuggestionFilterTests(unittest.TestCase):
    def test_matches_only_suggestions_from_that_discord_user_id(self) -> None:
        mine = _item(1, original_suggester="111")
        theirs = _item(2, original_suggester="222")
        filter_ = MemberSuggestionFilter(discord_user_id=111, member_display="KC")

        result = filter_.apply([mine, theirs])

        self.assertEqual([item.id for item in result], [1])

    def test_never_matches_a_legacy_suggestion_with_no_recorded_submitter(self) -> None:
        legacy = _item(1, original_suggester=None)
        filter_ = MemberSuggestionFilter(discord_user_id=111, member_display="KC")

        result = filter_.apply([legacy])

        self.assertEqual(result, [])

    def test_matches_by_stable_id_not_display_name_similarity(self) -> None:
        # A suggestion's stored submitter is always a stringified Discord
        # user ID (see SuggestionService.suggest) -- this filter must
        # never be fooled by a coincidentally-similar string.
        item = _item(1, original_suggester="999")
        filter_ = MemberSuggestionFilter(discord_user_id=111, member_display="KC")

        result = filter_.apply([item])

        self.assertEqual(result, [])

    def test_describe_returns_the_member_display(self) -> None:
        filter_ = MemberSuggestionFilter(discord_user_id=111, member_display="KC")
        self.assertEqual(filter_.describe(), "KC")


class GenreFilterTests(unittest.TestCase):
    def test_matches_a_suggestion_with_the_exact_genre(self) -> None:
        item = _item(1, genres=("Horror",))
        filter_ = GenreFilter(genre="Horror")

        self.assertEqual(filter_.apply([item]), [item])

    def test_matches_case_insensitively(self) -> None:
        item = _item(1, genres=("horror",))
        filter_ = GenreFilter(genre="Horror")

        self.assertEqual(filter_.apply([item]), [item])

    def test_matches_when_the_genre_is_one_of_several(self) -> None:
        item = _item(1, genres=("Action", "Comedy", "Drama"))
        filter_ = GenreFilter(genre="Comedy")

        self.assertEqual(filter_.apply([item]), [item])

    def test_excludes_a_suggestion_with_no_genre_metadata(self) -> None:
        item = _item(1, genres=())
        filter_ = GenreFilter(genre="Horror")

        self.assertEqual(filter_.apply([item]), [])

    def test_excludes_a_suggestion_with_a_different_genre(self) -> None:
        item = _item(1, genres=("Drama",))
        filter_ = GenreFilter(genre="Horror")

        self.assertEqual(filter_.apply([item]), [])


class ApplyNomineePoolFiltersTests(unittest.TestCase):
    def test_no_filters_returns_the_pool_unchanged(self) -> None:
        items = [_item(1), _item(2)]
        self.assertEqual(apply_nominee_pool_filters(items, []), items)

    def test_combines_member_and_genre_filters_as_an_intersection(self) -> None:
        kc_comedy = _item(1, original_suggester="111", genres=("Comedy",))
        kc_horror = _item(2, original_suggester="111", genres=("Horror",))
        other_comedy = _item(3, original_suggester="222", genres=("Comedy",))
        filters = [
            MemberSuggestionFilter(discord_user_id=111, member_display="KC"),
            GenreFilter(genre="Comedy"),
        ]

        result = apply_nominee_pool_filters([kc_comedy, kc_horror, other_comedy], filters)

        self.assertEqual([item.id for item in result], [1])

    def test_each_filter_also_works_independently(self) -> None:
        kc_comedy = _item(1, original_suggester="111", genres=("Comedy",))
        other_comedy = _item(2, original_suggester="222", genres=("Comedy",))

        member_only = apply_nominee_pool_filters(
            [kc_comedy, other_comedy], [MemberSuggestionFilter(discord_user_id=111, member_display="KC")]
        )
        genre_only = apply_nominee_pool_filters([kc_comedy, other_comedy], [GenreFilter(genre="Comedy")])

        self.assertEqual([item.id for item in member_only], [1])
        self.assertEqual({item.id for item in genre_only}, {1, 2})


class GenreEligibilityCountsTests(unittest.TestCase):
    def test_counts_one_suggestion_per_genre(self) -> None:
        items = [_item(1, genres=("Horror",)), _item(2, genres=("Horror", "Comedy"))]

        counts = genre_eligibility_counts(items)

        self.assertEqual(counts, {"Horror": 2, "Comedy": 1})

    def test_groups_genres_case_insensitively_using_first_seen_casing(self) -> None:
        items = [_item(1, genres=("Horror",)), _item(2, genres=("horror",))]

        counts = genre_eligibility_counts(items)

        self.assertEqual(counts, {"Horror": 2})

    def test_a_suggestion_with_no_genres_contributes_nothing(self) -> None:
        items = [_item(1, genres=())]

        self.assertEqual(genre_eligibility_counts(items), {})

    def test_duplicate_genres_on_one_item_count_once(self) -> None:
        items = [_item(1, genres=("Horror", "horror"))]

        self.assertEqual(genre_eligibility_counts(items), {"Horror": 1})


class FilteredCandidateSelectionStrategyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _add(self, title: str, **kwargs) -> WatchItem:
        result = self.suggestion_service.suggest(title, database_id=DATABASE_ID, guild_id=100, **kwargs)
        self.assertTrue(result.success)
        return result.watch_item


class FilteredCandidateSelectionStrategyTests(FilteredCandidateSelectionStrategyTestCase):
    def test_narrows_the_inner_strategys_candidate_pool(self) -> None:
        kc_item = self._add("Alien", original_suggester="111")
        other_item = self._add("The Matrix", original_suggester="222")
        inner = InfinitePoolStrategy(suggestion_source=self.suggestion_service)
        strategy = FilteredCandidateSelectionStrategy(
            inner=inner, filters=[MemberSuggestionFilter(discord_user_id=111, member_display="KC")]
        )

        pool_ids = {item.id for item in strategy.candidate_pool(DATABASE_ID)}

        self.assertEqual(pool_ids, {kc_item.id})
        self.assertNotIn(other_item.id, pool_ids)

    def test_no_filters_matches_the_inner_strategy_exactly(self) -> None:
        self._add("Alien")
        self._add("The Matrix")
        inner = InfinitePoolStrategy(suggestion_source=self.suggestion_service)
        strategy = FilteredCandidateSelectionStrategy(inner=inner, filters=[])

        self.assertEqual(
            {item.id for item in strategy.candidate_pool(DATABASE_ID)},
            {item.id for item in inner.candidate_pool(DATABASE_ID)},
        )

    def test_weight_for_delegates_to_the_inner_strategy_unchanged(self) -> None:
        item = self._add("Alien")
        inner = InfinitePoolStrategy(suggestion_source=self.suggestion_service)
        strategy = FilteredCandidateSelectionStrategy(inner=inner, filters=[])

        self.assertEqual(strategy.weight_for(item), inner.weight_for(item))

    def test_on_presented_delegates_to_the_inner_strategy(self) -> None:
        calls = []

        class RecordingStrategy:
            def candidate_pool(self, database_id, requested_count=None):
                return []

            def weight_for(self, watch_item):
                return 1.0

            def on_presented(self, database_id, suggestion_ids):
                calls.append((database_id, list(suggestion_ids)))

        strategy = FilteredCandidateSelectionStrategy(inner=RecordingStrategy(), filters=[])
        strategy.on_presented(DATABASE_ID, [1, 2, 3])

        self.assertEqual(calls, [(DATABASE_ID, [1, 2, 3])])

    def test_combined_member_and_genre_filters_narrow_the_inner_pool(self) -> None:
        match = self._add("Alien", original_suggester="111", genres=("Horror",))
        wrong_genre = self._add("Her", original_suggester="111", genres=("Romance",))
        wrong_member = self._add("The Matrix", original_suggester="222", genres=("Horror",))
        inner = InfinitePoolStrategy(suggestion_source=self.suggestion_service)
        strategy = FilteredCandidateSelectionStrategy(
            inner=inner,
            filters=[
                MemberSuggestionFilter(discord_user_id=111, member_display="KC"),
                GenreFilter(genre="Horror"),
            ],
        )

        pool_ids = {item.id for item in strategy.candidate_pool(DATABASE_ID)}

        self.assertEqual(pool_ids, {match.id})
        self.assertNotIn(wrong_genre.id, pool_ids)
        self.assertNotIn(wrong_member.id, pool_ids)


if __name__ == "__main__":
    unittest.main()
