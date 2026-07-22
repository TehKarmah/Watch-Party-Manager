import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.watch_item_journey import WatchItemJourney
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

    def test_watch_item_database_location_fields_default_to_none(self) -> None:
        item = WatchItem(title="Arrival", media_type=MediaType.MOVIE)

        self.assertIsNone(item.database_id)
        self.assertIsNone(item.guild_id)
        self.assertIsNone(item.channel_id)
        self.assertIsNone(item.message_id)

    def test_watch_item_accepts_database_location_fields(self) -> None:
        item = WatchItem(
            title="Arrival",
            media_type=MediaType.MOVIE,
            database_id=1,
            guild_id=100,
            channel_id=200,
            message_id=300,
        )

        self.assertEqual(item.database_id, 1)
        self.assertEqual(item.guild_id, 100)
        self.assertEqual(item.channel_id, 200)
        self.assertEqual(item.message_id, 300)

    def test_watch_item_rejects_a_non_positive_database_id(self) -> None:
        with self.assertRaises(ValueError):
            WatchItem(title="Arrival", media_type=MediaType.MOVIE, database_id=0)

    def test_watch_item_rejects_a_non_positive_guild_id(self) -> None:
        with self.assertRaises(ValueError):
            WatchItem(title="Arrival", media_type=MediaType.MOVIE, guild_id=0)

    def test_watch_item_rejects_a_non_positive_channel_id(self) -> None:
        with self.assertRaises(ValueError):
            WatchItem(title="Arrival", media_type=MediaType.MOVIE, channel_id=0)

    def test_watch_item_rejects_a_non_positive_message_id(self) -> None:
        with self.assertRaises(ValueError):
            WatchItem(title="Arrival", media_type=MediaType.MOVIE, message_id=0)


if __name__ == "__main__":
    unittest.main()


class WatchItemJourneyFieldTests(unittest.TestCase):
    def test_journey_defaults_to_a_fresh_watch_item_journey(self) -> None:
        item = WatchItem(title="The Matrix", media_type=MediaType.MOVIE)

        self.assertIsInstance(item.journey, WatchItemJourney)
        self.assertEqual(item.journey.voting_appearances, 0)
        self.assertEqual(item.journey.times_won, 0)

    def test_each_watch_item_gets_its_own_independent_journey(self) -> None:
        first = WatchItem(title="The Matrix", media_type=MediaType.MOVIE)
        second = WatchItem(title="Inception", media_type=MediaType.MOVIE)

        first.journey.record_vote_appearance()

        self.assertEqual(first.journey.voting_appearances, 1)
        self.assertEqual(second.journey.voting_appearances, 0)

    def test_a_journey_can_be_supplied_explicitly(self) -> None:
        journey = WatchItemJourney(times_won=3)

        item = WatchItem(title="The Matrix", media_type=MediaType.MOVIE, journey=journey)

        self.assertIs(item.journey, journey)
        self.assertEqual(item.journey.times_won, 3)


class WatchItemReleaseYearAndUpdatedAtTests(unittest.TestCase):
    def test_release_year_defaults_to_none(self) -> None:
        item = WatchItem(title="Arrival", media_type=MediaType.MOVIE)

        self.assertIsNone(item.release_year)

    def test_release_year_can_be_set(self) -> None:
        item = WatchItem(title="Arrival", media_type=MediaType.MOVIE, release_year=2016)

        self.assertEqual(2016, item.release_year)

    def test_release_year_rejects_a_year_before_cinema_existed(self) -> None:
        with self.assertRaises(ValueError):
            WatchItem(title="Arrival", media_type=MediaType.MOVIE, release_year=1800)

    def test_release_year_rejects_a_year_too_far_in_the_future(self) -> None:
        far_future_year = datetime.now(timezone.utc).year + 50
        with self.assertRaises(ValueError):
            WatchItem(title="Arrival", media_type=MediaType.MOVIE, release_year=far_future_year)

    def test_release_year_allows_a_near_future_year(self) -> None:
        near_future_year = datetime.now(timezone.utc).year + 1
        item = WatchItem(title="Arrival", media_type=MediaType.MOVIE, release_year=near_future_year)

        self.assertEqual(near_future_year, item.release_year)

    def test_updated_at_defaults_to_none(self) -> None:
        item = WatchItem(title="Arrival", media_type=MediaType.MOVIE)

        self.assertIsNone(item.updated_at)

    def test_updated_at_can_be_set_when_timezone_aware(self) -> None:
        now = datetime.now(timezone.utc)
        item = WatchItem(title="Arrival", media_type=MediaType.MOVIE, updated_at=now)

        self.assertEqual(now, item.updated_at)

    def test_updated_at_rejects_a_naive_datetime(self) -> None:
        with self.assertRaises(ValueError):
            WatchItem(title="Arrival", media_type=MediaType.MOVIE, updated_at=datetime(2026, 1, 1))
