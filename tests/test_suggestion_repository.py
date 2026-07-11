import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.watch_item import MediaType, MetadataProvider, WatchItem
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository


class JsonSuggestionRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        """Point each test at its own temporary file so tests never touch real data."""
        self._temp_dir = tempfile.TemporaryDirectory()
        self.file_path = Path(self._temp_dir.name) / "suggestions.json"
        self.repository = JsonSuggestionRepository(self.file_path)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_load_returns_empty_list_when_file_does_not_exist(self) -> None:
        self.assertFalse(self.file_path.exists())
        self.assertEqual(self.repository.load(), [])

    def test_save_creates_the_file_and_parent_directory(self) -> None:
        nested_path = Path(self._temp_dir.name) / "nested" / "suggestions.json"
        repository = JsonSuggestionRepository(nested_path)

        repository.save([WatchItem(title="The Matrix", media_type=MediaType.MOVIE)])

        self.assertTrue(nested_path.exists())

    def test_save_then_load_round_trips_a_single_suggestion(self) -> None:
        self.repository.save([WatchItem(title="The Matrix", media_type=MediaType.MOVIE)])

        loaded = self.repository.load()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].title, "The Matrix")
        self.assertEqual(loaded[0].media_type, MediaType.MOVIE)

    def test_save_then_load_round_trips_metadata_ids(self) -> None:
        watch_item = WatchItem(
            title="The Matrix",
            media_type=MediaType.MOVIE,
            metadata_ids={MetadataProvider.IMDB: "tt0133093"},
        )
        self.repository.save([watch_item])

        loaded = self.repository.load()
        self.assertEqual(loaded[0].metadata_ids[MetadataProvider.IMDB], "tt0133093")

    def test_save_then_load_preserves_insertion_order(self) -> None:
        watch_items = [
            WatchItem(title="Interstellar", media_type=MediaType.MOVIE),
            WatchItem(title="The Matrix", media_type=MediaType.MOVIE),
            WatchItem(title="Inception", media_type=MediaType.MOVIE),
        ]
        self.repository.save(watch_items)

        loaded_titles = [item.title for item in self.repository.load()]
        self.assertEqual(loaded_titles, ["Interstellar", "The Matrix", "Inception"])

    def test_save_with_empty_list_persists_an_empty_suggestion_list(self) -> None:
        self.repository.save([])

        self.assertTrue(self.file_path.exists())
        self.assertEqual(self.repository.load(), [])

    def test_load_returns_empty_list_and_logs_when_json_is_malformed(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text("{ this is not valid json", encoding="utf-8")

        with self.assertLogs(
            "watch_party_manager.persistence.suggestion_repository", level="ERROR"
        ) as log_context:
            result = self.repository.load()

        self.assertEqual(result, [])
        self.assertTrue(any("suggestions" in message for message in log_context.output))

    def test_load_returns_empty_list_when_expected_key_is_missing(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text('{"not_suggestions": []}', encoding="utf-8")

        self.assertEqual(self.repository.load(), [])

    def test_human_readable_json_contains_expected_fields(self) -> None:
        watch_item = WatchItem(
            title="The Matrix",
            media_type=MediaType.MOVIE,
            metadata_ids={MetadataProvider.IMDB: "tt0133093"},
        )
        self.repository.save([watch_item])

        raw_text = self.file_path.read_text(encoding="utf-8")
        self.assertIn('"title": "The Matrix"', raw_text)
        self.assertIn('"media_type": "movie"', raw_text)
        self.assertIn('"imdb": "tt0133093"', raw_text)


if __name__ == "__main__":
    unittest.main()
