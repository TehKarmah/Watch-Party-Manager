import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.watch_item_journey import WatchItemJourney
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

    def test_load_returns_empty_result_when_file_does_not_exist(self) -> None:
        self.assertFalse(self.file_path.exists())

        result = self.repository.load()
        self.assertEqual(result.watch_items, [])
        self.assertEqual(result.next_id, 1)
        self.assertFalse(result.migrated)

    def test_save_creates_the_file_and_parent_directory(self) -> None:
        nested_path = Path(self._temp_dir.name) / "nested" / "suggestions.json"
        repository = JsonSuggestionRepository(nested_path)

        repository.save([WatchItem(title="The Matrix", media_type=MediaType.MOVIE, id=1)], next_id=2)

        self.assertTrue(nested_path.exists())

    def test_save_then_load_round_trips_a_single_suggestion(self) -> None:
        self.repository.save([WatchItem(title="The Matrix", media_type=MediaType.MOVIE, id=1)], next_id=2)

        result = self.repository.load()
        self.assertEqual(len(result.watch_items), 1)
        self.assertEqual(result.watch_items[0].title, "The Matrix")
        self.assertEqual(result.watch_items[0].media_type, MediaType.MOVIE)
        self.assertEqual(result.watch_items[0].id, 1)
        self.assertEqual(result.next_id, 2)

    def test_save_then_load_round_trips_metadata_ids(self) -> None:
        watch_item = WatchItem(
            title="The Matrix",
            media_type=MediaType.MOVIE,
            metadata_ids={MetadataProvider.IMDB: "tt0133093"},
            id=1,
        )
        self.repository.save([watch_item], next_id=2)

        result = self.repository.load()
        self.assertEqual(result.watch_items[0].metadata_ids[MetadataProvider.IMDB], "tt0133093")

    def test_save_then_load_preserves_insertion_order(self) -> None:
        watch_items = [
            WatchItem(title="Interstellar", media_type=MediaType.MOVIE, id=1),
            WatchItem(title="The Matrix", media_type=MediaType.MOVIE, id=2),
            WatchItem(title="Inception", media_type=MediaType.MOVIE, id=3),
        ]
        self.repository.save(watch_items, next_id=4)

        result = self.repository.load()
        loaded_titles = [item.title for item in result.watch_items]
        self.assertEqual(loaded_titles, ["Interstellar", "The Matrix", "Inception"])

    def test_save_with_empty_list_persists_an_empty_suggestion_list(self) -> None:
        self.repository.save([], next_id=1)

        self.assertTrue(self.file_path.exists())
        result = self.repository.load()
        self.assertEqual(result.watch_items, [])
        self.assertEqual(result.next_id, 1)

    def test_load_returns_empty_result_and_logs_when_json_is_malformed(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text("{ this is not valid json", encoding="utf-8")

        with self.assertLogs(
            "watch_party_manager.persistence.suggestion_repository", level="ERROR"
        ) as log_context:
            result = self.repository.load()

        self.assertEqual(result.watch_items, [])
        self.assertEqual(result.next_id, 1)
        self.assertTrue(any("suggestions" in message for message in log_context.output))

    def test_load_returns_empty_result_when_expected_key_is_missing(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text('{"not_suggestions": []}', encoding="utf-8")

        result = self.repository.load()
        self.assertEqual(result.watch_items, [])
        self.assertEqual(result.next_id, 1)

    def test_human_readable_json_contains_expected_fields(self) -> None:
        watch_item = WatchItem(
            title="The Matrix",
            media_type=MediaType.MOVIE,
            metadata_ids={MetadataProvider.IMDB: "tt0133093"},
            id=1,
        )
        self.repository.save([watch_item], next_id=2)

        raw_text = self.file_path.read_text(encoding="utf-8")
        self.assertIn('"id": 1', raw_text)
        self.assertIn('"title": "The Matrix"', raw_text)
        self.assertIn('"media_type": "movie"', raw_text)
        self.assertIn('"imdb": "tt0133093"', raw_text)
        self.assertIn('"next_id": 2', raw_text)

    def test_load_assigns_sequential_ids_to_legacy_file_without_ids(self) -> None:
        legacy_json = """
        {
          "suggestions": [
            {"title": "Interstellar", "media_type": "movie", "metadata_ids": {}},
            {"title": "The Matrix", "media_type": "movie", "metadata_ids": {}},
            {"title": "Inception", "media_type": "movie", "metadata_ids": {}}
          ]
        }
        """
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(legacy_json, encoding="utf-8")

        result = self.repository.load()

        self.assertTrue(result.migrated)
        assigned_ids = [item.id for item in result.watch_items]
        self.assertEqual(assigned_ids, [1, 2, 3])
        self.assertEqual(result.next_id, 4)
        # Original order must be preserved through migration.
        titles = [item.title for item in result.watch_items]
        self.assertEqual(titles, ["Interstellar", "The Matrix", "Inception"])

    def test_load_does_not_report_migration_for_a_file_that_already_has_ids(self) -> None:
        self.repository.save(
            [WatchItem(title="The Matrix", media_type=MediaType.MOVIE, id=1)],
            next_id=2,
        )

        result = self.repository.load()
        self.assertFalse(result.migrated)

    def test_ids_persist_across_simulated_restarts(self) -> None:
        first_load = self.repository.load()
        self.assertEqual(first_load.next_id, 1)

        watch_item = WatchItem(title="The Matrix", media_type=MediaType.MOVIE, id=first_load.next_id)
        self.repository.save([watch_item], next_id=first_load.next_id + 1)

        second_load = self.repository.load()
        self.assertEqual(second_load.watch_items[0].id, 1)
        self.assertEqual(second_load.next_id, 2)

        another_watch_item = WatchItem(
            title="Inception", media_type=MediaType.MOVIE, id=second_load.next_id
        )
        self.repository.save(
            second_load.watch_items + [another_watch_item], next_id=second_load.next_id + 1
        )

        third_load = self.repository.load()
        ids = [item.id for item in third_load.watch_items]
        self.assertEqual(ids, [1, 2])
        self.assertEqual(third_load.next_id, 3)

    def test_save_then_load_round_trips_database_and_message_location(self) -> None:
        watch_item = WatchItem(
            title="The Matrix",
            media_type=MediaType.MOVIE,
            id=1,
            database_id=10,
            guild_id=100,
            channel_id=200,
            message_id=300,
        )
        self.repository.save([watch_item], next_id=2)

        result = self.repository.load()
        loaded = result.watch_items[0]
        self.assertEqual(loaded.database_id, 10)
        self.assertEqual(loaded.guild_id, 100)
        self.assertEqual(loaded.channel_id, 200)
        self.assertEqual(loaded.message_id, 300)

    def test_loading_a_file_without_database_fields_defaults_them_to_none(self) -> None:
        legacy_json = """
        {
          "next_id": 2,
          "suggestions": [
            {"id": 1, "title": "The Matrix", "media_type": "movie", "metadata_ids": {}}
          ]
        }
        """
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(legacy_json, encoding="utf-8")

        result = self.repository.load()
        loaded = result.watch_items[0]
        self.assertIsNone(loaded.database_id)
        self.assertIsNone(loaded.guild_id)
        self.assertIsNone(loaded.channel_id)
        self.assertIsNone(loaded.message_id)

    def test_save_then_load_round_trips_the_archived_status(self) -> None:
        from watch_party_manager.domain.watch_item import WatchItemStatus

        watch_item = WatchItem(
            title="The Matrix", media_type=MediaType.MOVIE, id=1, status=WatchItemStatus.ARCHIVED
        )
        self.repository.save([watch_item], next_id=2)

        result = self.repository.load()

        self.assertEqual(result.watch_items[0].status, WatchItemStatus.ARCHIVED)

    def test_a_file_without_a_status_field_defaults_to_suggested(self) -> None:
        from watch_party_manager.domain.watch_item import WatchItemStatus

        legacy_json = """
        {
          "next_id": 2,
          "suggestions": [
            {"id": 1, "title": "The Matrix", "media_type": "movie", "metadata_ids": {}}
          ]
        }
        """
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(legacy_json, encoding="utf-8")

        result = self.repository.load()

        self.assertEqual(result.watch_items[0].status, WatchItemStatus.SUGGESTED)


if __name__ == "__main__":
    unittest.main()


class SuggestionRepositoryJourneyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.file_path = Path(self._temp_dir.name) / "suggestions.json"
        self.repository = JsonSuggestionRepository(self.file_path)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_journey_round_trips_through_save_and_load(self) -> None:
        journey = WatchItemJourney(
            voting_appearances=2,
            winning_vote="The Matrix",
            times_won=1,
            last_nominated_date=date(2026, 7, 1),
            last_won_date=date(2026, 6, 15),
        )
        watch_item = WatchItem(title="The Matrix", media_type=MediaType.MOVIE, id=1, journey=journey)
        self.repository.save([watch_item], next_id=2)

        result = self.repository.load()

        loaded_journey = result.watch_items[0].journey
        self.assertEqual(loaded_journey.voting_appearances, 2)
        self.assertEqual(loaded_journey.winning_vote, "The Matrix")
        self.assertEqual(loaded_journey.times_won, 1)
        self.assertEqual(loaded_journey.last_nominated_date, date(2026, 7, 1))
        self.assertEqual(loaded_journey.last_won_date, date(2026, 6, 15))

    def test_a_journey_with_no_wins_or_nominations_round_trips_cleanly(self) -> None:
        watch_item = WatchItem(title="The Matrix", media_type=MediaType.MOVIE, id=1)
        self.repository.save([watch_item], next_id=2)

        result = self.repository.load()

        loaded_journey = result.watch_items[0].journey
        self.assertEqual(loaded_journey.times_won, 0)
        self.assertIsNone(loaded_journey.last_nominated_date)
        self.assertIsNone(loaded_journey.last_won_date)

    def test_a_suggestion_file_saved_before_journeys_existed_still_loads(self) -> None:
        # Simulates a suggestions.json written before this milestone,
        # which has no "journey" key on any entry at all.
        legacy_json = """
        {
          "next_id": 2,
          "suggestions": [
            {"id": 1, "title": "The Matrix", "media_type": "movie", "metadata_ids": {}}
          ]
        }
        """
        self.file_path.write_text(legacy_json, encoding="utf-8")

        result = self.repository.load()

        loaded_journey = result.watch_items[0].journey
        self.assertEqual(loaded_journey.voting_appearances, 0)
        self.assertEqual(loaded_journey.times_won, 0)
        self.assertIsNone(loaded_journey.last_nominated_date)
        self.assertIsNone(loaded_journey.last_won_date)

    def test_watch_dates_and_rotation_history_also_round_trip(self) -> None:
        journey = WatchItemJourney(
            rotation_history=(1, 2, 3),
            watch_dates=(date(2026, 1, 1), date(2026, 2, 1)),
            rewatch_count=1,
        )
        watch_item = WatchItem(title="The Matrix", media_type=MediaType.MOVIE, id=1, journey=journey)
        self.repository.save([watch_item], next_id=2)

        result = self.repository.load()

        loaded_journey = result.watch_items[0].journey
        self.assertEqual(loaded_journey.rotation_history, (1, 2, 3))
        self.assertEqual(loaded_journey.watch_dates, (date(2026, 1, 1), date(2026, 2, 1)))
        self.assertEqual(loaded_journey.rewatch_count, 1)

    def test_rejection_history_round_trips_through_save_and_load(self) -> None:
        journey = WatchItemJourney(rejected_by_discord_user_ids=(111, 222))
        watch_item = WatchItem(title="The Matrix", media_type=MediaType.MOVIE, id=1, journey=journey)
        self.repository.save([watch_item], next_id=2)

        result = self.repository.load()

        self.assertEqual(result.watch_items[0].journey.rejected_by_discord_user_ids, (111, 222))

    def test_a_journey_saved_before_rejections_existed_still_loads(self) -> None:
        legacy_json = """
        {
          "next_id": 2,
          "suggestions": [
            {
              "id": 1,
              "title": "The Matrix",
              "media_type": "movie",
              "metadata_ids": {},
              "journey": {"original_suggester": "Ada"}
            }
          ]
        }
        """
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(legacy_json, encoding="utf-8")

        result = self.repository.load()

        self.assertEqual(result.watch_items[0].journey.rejected_by_discord_user_ids, ())

    def test_release_year_and_updated_at_round_trip(self) -> None:
        updated_at = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
        watch_item = WatchItem(
            title="Arrival", media_type=MediaType.MOVIE, id=1, release_year=2016, updated_at=updated_at
        )
        self.repository.save([watch_item], next_id=2)

        result = self.repository.load()

        self.assertEqual(2016, result.watch_items[0].release_year)
        self.assertEqual(updated_at, result.watch_items[0].updated_at)

    def test_a_suggestion_saved_before_release_year_existed_still_loads(self) -> None:
        legacy_json = """
        {
          "next_id": 2,
          "suggestions": [
            {
              "id": 1,
              "title": "The Matrix",
              "media_type": "movie",
              "metadata_ids": {}
            }
          ]
        }
        """
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(legacy_json, encoding="utf-8")

        result = self.repository.load()

        self.assertIsNone(result.watch_items[0].release_year)
        self.assertIsNone(result.watch_items[0].updated_at)
