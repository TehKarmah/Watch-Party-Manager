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
    IMDB_RATING_MAXIMUM,
    IMDB_RATING_MINIMUM,
    ActorFilter,
    FilteredCandidateSelectionStrategy,
    GenreFilter,
    ImdbRatingFilter,
    MemberSuggestionFilter,
    MpaaRatingFilter,
    apply_nominee_pool_filters,
    genre_eligibility_counts,
    mpaa_rating_eligibility_counts,
    parse_imdb_rating_bounds,
    search_cast_names,
)
from watch_party_manager.services.suggestion_service import SuggestionService

DATABASE_ID = 1


def _item(
    item_id: int,
    *,
    original_suggester=None,
    genres=(),
    imdb_rating=None,
    content_rating=None,
    cast=(),
) -> WatchItem:
    return WatchItem(
        title=f"Item {item_id}",
        media_type=MediaType.MOVIE,
        id=item_id,
        genres=genres,
        imdb_rating=imdb_rating,
        content_rating=content_rating,
        cast=cast,
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


class ImdbRatingFilterTests(unittest.TestCase):
    def test_minimum_only_is_inclusive(self) -> None:
        at_boundary = _item(1, imdb_rating="7.0")
        below = _item(2, imdb_rating="6.9")
        above = _item(3, imdb_rating="9.5")
        filter_ = ImdbRatingFilter(minimum=7.0)

        result = filter_.apply([at_boundary, below, above])

        self.assertEqual({item.id for item in result}, {1, 3})

    def test_maximum_only_is_inclusive(self) -> None:
        at_boundary = _item(1, imdb_rating="5.9")
        below = _item(2, imdb_rating="3.0")
        above = _item(3, imdb_rating="6.0")
        filter_ = ImdbRatingFilter(maximum=5.9)

        result = filter_.apply([at_boundary, below, above])

        self.assertEqual({item.id for item in result}, {1, 2})

    def test_minimum_and_maximum_together(self) -> None:
        in_range = _item(1, imdb_rating="7.0")
        too_low = _item(2, imdb_rating="5.9")
        too_high = _item(3, imdb_rating="8.1")
        filter_ = ImdbRatingFilter(minimum=6.0, maximum=8.0)

        result = filter_.apply([in_range, too_low, too_high])

        self.assertEqual([item.id for item in result], [1])

    def test_no_bounds_matches_every_rated_item(self) -> None:
        rated = _item(1, imdb_rating="4.2")
        filter_ = ImdbRatingFilter()

        self.assertEqual(filter_.apply([rated]), [rated])

    def test_excludes_a_suggestion_with_no_stored_rating(self) -> None:
        unrated = _item(1, imdb_rating=None)
        filter_ = ImdbRatingFilter(minimum=0.0)

        self.assertEqual(filter_.apply([unrated]), [])

    def test_excludes_a_suggestion_with_an_unparseable_rating(self) -> None:
        malformed = _item(1, imdb_rating="N/A")
        filter_ = ImdbRatingFilter(minimum=0.0)

        self.assertEqual(filter_.apply([malformed]), [])

    def test_does_not_round_the_stored_value(self) -> None:
        # 6.95 stored -- must not be rounded to 7.0 before comparing.
        item = _item(1, imdb_rating="6.95")
        filter_ = ImdbRatingFilter(minimum=7.0)

        self.assertEqual(filter_.apply([item]), [])

    def test_describe_variants(self) -> None:
        self.assertEqual(ImdbRatingFilter().describe(), "Any")
        self.assertEqual(ImdbRatingFilter(minimum=7.0).describe(), "7.0+")
        self.assertEqual(ImdbRatingFilter(maximum=5.9).describe(), "5.9 or lower")
        self.assertEqual(ImdbRatingFilter(minimum=6.0, maximum=8.0).describe(), "6.0–8.0")


class MpaaRatingFilterTests(unittest.TestCase):
    def test_matches_the_exact_rating(self) -> None:
        item = _item(1, content_rating="PG-13")
        filter_ = MpaaRatingFilter(rating="PG-13")

        self.assertEqual(filter_.apply([item]), [item])

    def test_matches_case_insensitively_and_trims_whitespace(self) -> None:
        item = _item(1, content_rating=" pg-13 ")
        filter_ = MpaaRatingFilter(rating="PG-13")

        self.assertEqual(filter_.apply([item]), [item])

    def test_does_not_treat_different_ratings_as_equivalent(self) -> None:
        not_rated = _item(1, content_rating="Not Rated")
        unrated = _item(2, content_rating="Unrated")
        filter_ = MpaaRatingFilter(rating="Not Rated")

        result = filter_.apply([not_rated, unrated])

        self.assertEqual([item.id for item in result], [1])

    def test_excludes_a_suggestion_with_no_stored_rating(self) -> None:
        item = _item(1, content_rating=None)
        filter_ = MpaaRatingFilter(rating="PG-13")

        self.assertEqual(filter_.apply([item]), [])

    def test_describe_returns_the_rating(self) -> None:
        self.assertEqual(MpaaRatingFilter(rating="R").describe(), "R")


class ActorFilterTests(unittest.TestCase):
    def test_matches_a_suggestion_whose_cast_includes_the_actor(self) -> None:
        item = _item(1, cast=("Jim Carrey", "Cameron Diaz"))
        filter_ = ActorFilter(actor="Jim Carrey")

        self.assertEqual(filter_.apply([item]), [item])

    def test_matches_case_insensitively_and_trims_whitespace(self) -> None:
        item = _item(1, cast=("jim carrey",))
        filter_ = ActorFilter(actor=" Jim Carrey ")

        self.assertEqual(filter_.apply([item]), [item])

    def test_excludes_a_suggestion_with_no_cast_metadata(self) -> None:
        item = _item(1, cast=())
        filter_ = ActorFilter(actor="Jim Carrey")

        self.assertEqual(filter_.apply([item]), [])

    def test_excludes_a_suggestion_missing_that_actor(self) -> None:
        item = _item(1, cast=("Cameron Diaz",))
        filter_ = ActorFilter(actor="Jim Carrey")

        self.assertEqual(filter_.apply([item]), [])

    def test_describe_returns_the_actor(self) -> None:
        self.assertEqual(ActorFilter(actor="Jim Carrey").describe(), "Jim Carrey")


class MpaaRatingEligibilityCountsTests(unittest.TestCase):
    def test_counts_one_suggestion_per_rating(self) -> None:
        items = [_item(1, content_rating="PG-13"), _item(2, content_rating="PG-13"), _item(3, content_rating="R")]

        counts = mpaa_rating_eligibility_counts(items)

        self.assertEqual(counts, {"PG-13": 2, "R": 1})

    def test_groups_case_insensitively_using_first_seen_casing(self) -> None:
        items = [_item(1, content_rating="PG-13"), _item(2, content_rating="pg-13")]

        self.assertEqual(mpaa_rating_eligibility_counts(items), {"PG-13": 2})

    def test_not_rated_and_unrated_stay_distinct(self) -> None:
        items = [_item(1, content_rating="Not Rated"), _item(2, content_rating="Unrated")]

        counts = mpaa_rating_eligibility_counts(items)

        self.assertEqual(counts, {"Not Rated": 1, "Unrated": 1})

    def test_a_suggestion_with_no_rating_contributes_nothing(self) -> None:
        self.assertEqual(mpaa_rating_eligibility_counts([_item(1, content_rating=None)]), {})


class SearchCastNamesTests(unittest.TestCase):
    def test_exact_match(self) -> None:
        items = [_item(1, cast=("Jim Carrey",))]

        results = search_cast_names(items, "Jim Carrey")

        self.assertEqual(results, [("Jim Carrey", 1)])

    def test_partial_case_insensitive_search(self) -> None:
        items = [_item(1, cast=("Jim Carrey",))]

        results = search_cast_names(items, "carrey")

        self.assertEqual(results, [("Jim Carrey", 1)])

    def test_no_match_returns_empty(self) -> None:
        items = [_item(1, cast=("Jim Carrey",))]

        self.assertEqual(search_cast_names(items, "Tom Hanks"), [])

    def test_multiple_matches_sorted_by_count_then_name(self) -> None:
        items = [
            _item(1, cast=("Jim Carrey",)),
            _item(2, cast=("Jim Carrey",)),
            _item(3, cast=("Jimmy Fallon",)),
        ]

        results = search_cast_names(items, "jim")

        self.assertEqual(results, [("Jim Carrey", 2), ("Jimmy Fallon", 1)])

    def test_missing_cast_metadata_never_matches(self) -> None:
        items = [_item(1, cast=())]

        self.assertEqual(search_cast_names(items, "jim"), [])

    def test_multi_actor_suggestion_matches_on_any_cast_member(self) -> None:
        items = [_item(1, cast=("Cameron Diaz", "Jim Carrey"))]

        self.assertEqual(search_cast_names(items, "Jim Carrey"), [("Jim Carrey", 1)])

    def test_canonical_name_uses_first_seen_stored_spelling(self) -> None:
        items = [_item(1, cast=("Jim Carrey",)), _item(2, cast=("JIM CARREY",))]

        self.assertEqual(search_cast_names(items, "jim"), [("Jim Carrey", 2)])

    def test_more_than_twenty_five_matches_are_all_returned_for_the_caller_to_cap(self) -> None:
        items = [_item(i, cast=(f"Actor {i:02d}",)) for i in range(1, 31)]

        results = search_cast_names(items, "Actor")

        self.assertEqual(len(results), 30)


class ParseImdbRatingBoundsTests(unittest.TestCase):
    def test_both_blank_is_any_rating(self) -> None:
        self.assertEqual(parse_imdb_rating_bounds(None, None), (None, None))
        self.assertEqual(parse_imdb_rating_bounds("", "  "), (None, None))

    def test_minimum_only(self) -> None:
        self.assertEqual(parse_imdb_rating_bounds("7.0", None), (7.0, None))

    def test_maximum_only(self) -> None:
        self.assertEqual(parse_imdb_rating_bounds(None, "5.9"), (None, 5.9))

    def test_minimum_and_maximum(self) -> None:
        self.assertEqual(parse_imdb_rating_bounds("6.0", "8.0"), (6.0, 8.0))

    def test_accepts_the_full_valid_range_boundaries(self) -> None:
        self.assertEqual(
            parse_imdb_rating_bounds(str(IMDB_RATING_MINIMUM), str(IMDB_RATING_MAXIMUM)),
            (IMDB_RATING_MINIMUM, IMDB_RATING_MAXIMUM),
        )

    def test_rejects_a_non_numeric_value(self) -> None:
        with self.assertRaises(ValueError):
            parse_imdb_rating_bounds("great", None)

    def test_rejects_more_than_one_decimal_place(self) -> None:
        with self.assertRaises(ValueError):
            parse_imdb_rating_bounds("7.55", None)

    def test_rejects_a_value_above_the_maximum(self) -> None:
        with self.assertRaises(ValueError):
            parse_imdb_rating_bounds("10.1", None)

    def test_rejects_a_negative_value(self) -> None:
        with self.assertRaises(ValueError):
            parse_imdb_rating_bounds("-1.0", None)

    def test_rejects_minimum_greater_than_maximum(self) -> None:
        with self.assertRaises(ValueError):
            parse_imdb_rating_bounds("8.0", "6.0")

    def test_error_messages_are_actionable(self) -> None:
        with self.assertRaisesRegex(ValueError, "Minimum rating"):
            parse_imdb_rating_bounds("bad", None)
        with self.assertRaisesRegex(ValueError, "Maximum rating"):
            parse_imdb_rating_bounds(None, "bad")
        with self.assertRaisesRegex(ValueError, "cannot be greater than"):
            parse_imdb_rating_bounds("8.0", "6.0")


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

    def test_all_five_filters_combine_as_a_single_intersection(self) -> None:
        perfect_match = _item(
            1,
            original_suggester="111",
            genres=("Comedy",),
            imdb_rating="7.5",
            content_rating="PG-13",
            cast=("Jim Carrey",),
        )
        wrong_actor = _item(
            2,
            original_suggester="111",
            genres=("Comedy",),
            imdb_rating="7.5",
            content_rating="PG-13",
            cast=("Cameron Diaz",),
        )
        filters = [
            GenreFilter(genre="Comedy"),
            ImdbRatingFilter(minimum=7.0, maximum=8.0),
            MpaaRatingFilter(rating="PG-13"),
            ActorFilter(actor="Jim Carrey"),
            MemberSuggestionFilter(discord_user_id=111, member_display="KC"),
        ]

        result = apply_nominee_pool_filters([perfect_match, wrong_actor], filters)

        self.assertEqual([item.id for item in result], [1])


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
