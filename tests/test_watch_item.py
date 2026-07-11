import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain import (
    MediaType,
    MetadataProvider,
    WatchItem,
    WatchItemStatus,
)


class WatchItemModelTests(unittest.TestCase):
    def test_watch_item_requires_a_non_empty_title(self) -> None:
        with self.assertRaises(ValueError):
            WatchItem(title="   ", media_type=MediaType.MOVIE, runtime_minutes=120)

    def test_watch_item_allows_a_missing_runtime(self) -> None:
        item = WatchItem(title="Arrival", media_type=MediaType.MOVIE, runtime_minutes=None)

        self.assertIsNone(item.runtime_minutes)

    def test_watch_item_requires_a_positive_runtime_when_provided(self) -> None:
        with self.assertRaises(ValueError):
            WatchItem(title="Arrival", media_type=MediaType.MOVIE, runtime_minutes=0)

    def test_watch_item_normalizes_genres_and_stores_metadata(self) -> None:
        item = WatchItem(
            title="The Matrix",
            media_type=MediaType.MOVIE,
            runtime_minutes=136,
            genres=(" sci-fi ", "Action", "  "),
            metadata_ids={MetadataProvider.IMDB: "tt0133093"},
        )

        self.assertEqual(item.genres, ("sci-fi", "Action"))
        self.assertEqual(item.metadata_ids[MetadataProvider.IMDB], "tt0133093")
        self.assertEqual(item.status, WatchItemStatus.SUGGESTED)

    def test_watch_item_status_can_be_updated(self) -> None:
        item = WatchItem(title="Blade Runner", media_type=MediaType.MOVIE, runtime_minutes=117)

        item.status = WatchItemStatus.CURRENT_ROTATION

        self.assertEqual(item.status, WatchItemStatus.CURRENT_ROTATION)

    def test_metadata_ids_require_provider_keys(self) -> None:
        with self.assertRaises(TypeError):
            WatchItem(
                title="Her",
                media_type=MediaType.MOVIE,
                runtime_minutes=126,
                metadata_ids={"imdb": "tt0114825"},
            )

    def test_metadata_ids_require_non_empty_trimmed_strings(self) -> None:
        with self.assertRaises(ValueError):
            WatchItem(
                title="Her",
                media_type=MediaType.MOVIE,
                runtime_minutes=126,
                metadata_ids={MetadataProvider.IMDB: "   "},
            )

    def test_watch_item_id_defaults_to_none(self) -> None:
        item = WatchItem(title="Arrival", media_type=MediaType.MOVIE)

        self.assertIsNone(item.id)

    def test_watch_item_accepts_a_positive_id(self) -> None:
        item = WatchItem(title="Arrival", media_type=MediaType.MOVIE, id=7)

        self.assertEqual(item.id, 7)

    def test_watch_item_rejects_a_non_positive_id(self) -> None:
        with self.assertRaises(ValueError):
            WatchItem(title="Arrival", media_type=MediaType.MOVIE, id=0)


if __name__ == "__main__":
    unittest.main()
