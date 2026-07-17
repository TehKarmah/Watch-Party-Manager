import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.suggestion_database import SuggestionDatabase
from watch_party_manager.domain.watch_item import MediaType, MetadataProvider, WatchItem
from watch_party_manager.services.suggestion_list_formatter import (
    SuggestionListFormatter,
    SuggestionListView,
)


class SuggestionListFormatterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.formatter = SuggestionListFormatter()
        self.database = SuggestionDatabase(
            database_id=1,
            name="Sunday Watch Party",
            guild_id=100,
            channel_id=200,
        )

    def test_parse_defaults_to_standard(self) -> None:
        self.assertIs(SuggestionListView.parse(None), SuggestionListView.STANDARD)
        self.assertIs(SuggestionListView.parse(""), SuggestionListView.STANDARD)

    def test_parse_accepts_case_and_whitespace(self) -> None:
        self.assertIs(SuggestionListView.parse(" Crew "), SuggestionListView.CREW)

    def test_parse_rejects_unknown_view(self) -> None:
        with self.assertRaisesRegex(ValueError, "standard, crew"):
            SuggestionListView.parse("admin")

    def test_empty_list_names_database(self) -> None:
        self.assertEqual(
            self.formatter.format([], self.database),
            '"Sunday Watch Party" is currently empty.',
        )

    def test_standard_view_is_simple_and_links_original_suggestion(self) -> None:
        item = WatchItem(
            id=12,
            title="Alien (1979)",
            media_type=MediaType.MOVIE,
            guild_id=100,
            channel_id=200,
            message_id=300,
            metadata_ids={MetadataProvider.IMDB: "https://www.imdb.com/title/tt0078748/"},
        )

        message = self.formatter.format([item], self.database)

        self.assertIn("Sunday Watch Party Watch Items (1)", message)
        self.assertIn("Alien (1979) | [Original suggestion]", message)
        self.assertNotIn("IMDb:", message)
        self.assertNotIn("#12", message)

    def test_crew_view_includes_available_administrative_details(self) -> None:
        item = WatchItem(
            id=12,
            title="Alien (1979)",
            media_type=MediaType.MOVIE,
            guild_id=100,
            channel_id=200,
            message_id=300,
            metadata_ids={MetadataProvider.IMDB: "https://www.imdb.com/title/tt0078748/"},
        )

        message = self.formatter.format([item], self.database, SuggestionListView.CREW)

        self.assertIn("**#12 · Alien (1979)**", message)
        self.assertIn("Status: Suggested", message)
        self.assertIn("Media type: Movie", message)
        self.assertIn("IMDb: https://www.imdb.com/title/tt0078748/", message)
        self.assertIn("[Original suggestion]", message)

    def test_crew_view_marks_missing_original_suggestion(self) -> None:
        item = WatchItem(id=1, title="Alien", media_type=MediaType.MOVIE)
        message = self.formatter.format([item], self.database, SuggestionListView.CREW)
        self.assertIn("Original suggestion: unavailable", message)


if __name__ == "__main__":
    unittest.main()
