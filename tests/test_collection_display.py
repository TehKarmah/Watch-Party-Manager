"""Tests for the shared collection display helper (Requirements 2 & 3:
built-in collection emojis, and the one shared display helper every UI
surface must use).
"""

import unittest

from watch_party_manager.services.collection_display import (
    STANDARD_COLLECTION_TYPES,
    collection_emoji,
    format_collection_display,
    used_standard_collection_type_keys,
)


class CollectionEmojiTests(unittest.TestCase):
    def test_movies_matches(self) -> None:
        self.assertEqual(collection_emoji("Movies"), "🎬")
        self.assertEqual(collection_emoji("Movie Suggestions"), "🎬")

    def test_tv_shows_matches(self) -> None:
        self.assertEqual(collection_emoji("TV Shows"), "📺")
        self.assertEqual(collection_emoji("TV Suggestions"), "📺")

    def test_anime_matches(self) -> None:
        self.assertEqual(collection_emoji("Anime"), "🎌")
        self.assertEqual(collection_emoji("Anime Watchlist"), "🎌")

    def test_holiday_matches(self) -> None:
        self.assertEqual(collection_emoji("Holiday"), "🎄")
        self.assertEqual(collection_emoji("Holiday Movies"), "🎬")  # first keyword wins: "movie"

    def test_documentaries_matches(self) -> None:
        self.assertEqual(collection_emoji("Documentaries"), "🎞️")
        self.assertEqual(collection_emoji("Documentary"), "🎞️")

    def test_horror_matches(self) -> None:
        self.assertEqual(collection_emoji("Horror"), "🎃")
        self.assertEqual(collection_emoji("Horror Movies"), "🎬")  # first keyword wins: "movie"

    def test_matching_is_case_insensitive(self) -> None:
        self.assertEqual(collection_emoji("movies"), "🎬")
        self.assertEqual(collection_emoji("MOVIES"), "🎬")

    def test_custom_collection_has_no_emoji(self) -> None:
        self.assertIsNone(collection_emoji("Book Club Adaptations"))
        self.assertIsNone(collection_emoji("Friday Night Picks"))
        self.assertIsNone(collection_emoji("Special Collection"))

    def test_does_not_match_a_substring_inside_another_word(self) -> None:
        # Word-boundary matching: "tv" must not fire on unrelated words
        # that merely contain the letters.
        self.assertIsNone(collection_emoji("Festival Picks"))


class FormatCollectionDisplayTests(unittest.TestCase):
    def test_standard_collection_gets_emoji_prefix(self) -> None:
        self.assertEqual(format_collection_display("Movie Suggestions"), "🎬 Movie Suggestions")
        self.assertEqual(format_collection_display("TV Suggestions"), "📺 TV Suggestions")

    def test_custom_collection_has_no_prefix(self) -> None:
        self.assertEqual(format_collection_display("Book Club Adaptations"), "Book Club Adaptations")


class UsedStandardCollectionTypeKeysTests(unittest.TestCase):
    def test_no_collections_means_nothing_is_used(self) -> None:
        self.assertEqual(used_standard_collection_type_keys([]), set())

    def test_a_matching_collection_marks_its_type_used(self) -> None:
        self.assertEqual(used_standard_collection_type_keys(["Movie Suggestions"]), {"movies"})

    def test_multiple_collections_mark_multiple_types_used(self) -> None:
        used = used_standard_collection_type_keys(["Movie Suggestions", "TV Suggestions", "Book Club"])
        self.assertEqual(used, {"movies", "tv_shows"})

    def test_a_custom_named_collection_marks_nothing_used(self) -> None:
        self.assertEqual(used_standard_collection_type_keys(["Book Club Adaptations"]), set())

    def test_every_standard_type_has_a_stable_unique_key(self) -> None:
        keys = [standard_type.key for standard_type in STANDARD_COLLECTION_TYPES]
        self.assertEqual(len(keys), len(set(keys)))


if __name__ == "__main__":
    unittest.main()
