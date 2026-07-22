import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.watch_item import MetadataProvider, WatchItemStatus
from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.suggestion_service import SuggestionService


class SuggestionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        """Create a fresh service backed by isolated, temporary repositories."""
        self._temp_dir = tempfile.TemporaryDirectory()
        self.repository = JsonSuggestionRepository(Path(self._temp_dir.name) / "suggestions.json")
        self.database_repository = JsonSuggestionDatabaseRepository(
            Path(self._temp_dir.name) / "suggestion_databases.json"
        )
        self.service = SuggestionService(
            repository=self.repository, database_repository=self.database_repository
        )

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_suggest_successful_addition(self) -> None:
        result = self.service.suggest("The Matrix")
        self.assertTrue(result.success)
        self.assertIn("Added", result.message)
        self.assertIn("The Matrix", result.message)
        self.assertEqual(self.service.suggestion_count(), 1)

    def test_suggest_rejects_empty_title(self) -> None:
        result = self.service.suggest("")
        self.assertFalse(result.success)
        self.assertIn("I need a title", result.message)
        self.assertEqual(self.service.suggestion_count(), 0)

    def test_suggest_rejects_whitespace_only_title(self) -> None:
        result = self.service.suggest("   ")
        self.assertFalse(result.success)
        self.assertIn("I need a title", result.message)
        self.assertEqual(self.service.suggestion_count(), 0)

    def test_suggest_detects_case_insensitive_duplicates(self) -> None:
        result1 = self.service.suggest("The Matrix")
        self.assertTrue(result1.success)

        result2 = self.service.suggest("the matrix")
        self.assertFalse(result2.success)
        self.assertIn("already on the list", result2.message)
        self.assertEqual(self.service.suggestion_count(), 1)

    def test_suggest_detects_duplicates_with_different_casing(self) -> None:
        result1 = self.service.suggest("The Matrix")
        self.assertTrue(result1.success)

        result2 = self.service.suggest("THE MATRIX")
        self.assertFalse(result2.success)
        self.assertEqual(self.service.suggestion_count(), 1)

    def test_suggest_strips_whitespace_from_title(self) -> None:
        result = self.service.suggest("  Inception  ")
        self.assertTrue(result.success)

        suggestions = self.service.get_suggestions()
        self.assertEqual(suggestions[0].title, "Inception")

    def test_suggest_stores_imdb_url(self) -> None:
        result = self.service.suggest("The Matrix", "tt0133093")
        self.assertTrue(result.success)

        suggestions = self.service.get_suggestions()
        self.assertEqual(len(suggestions), 1)
        self.assertIn(MetadataProvider.IMDB, suggestions[0].metadata_ids)
        self.assertEqual(suggestions[0].metadata_ids[MetadataProvider.IMDB], "tt0133093")

    def test_suggest_handles_imdb_url_with_whitespace(self) -> None:
        result = self.service.suggest("The Matrix", "  tt0133093  ")
        self.assertTrue(result.success)

        suggestions = self.service.get_suggestions()
        self.assertEqual(suggestions[0].metadata_ids[MetadataProvider.IMDB], "tt0133093")

    def test_suggest_ignores_empty_imdb_url(self) -> None:
        result = self.service.suggest("The Matrix", "   ")
        self.assertTrue(result.success)

        suggestions = self.service.get_suggestions()
        self.assertEqual(len(suggestions[0].metadata_ids), 0)

    def test_get_suggestions_returns_all_suggestions(self) -> None:
        self.service.suggest("The Matrix")
        self.service.suggest("Inception")
        self.service.suggest("Interstellar")

        suggestions = self.service.get_suggestions()
        self.assertEqual(len(suggestions), 3)
        titles = {s.title for s in suggestions}
        self.assertEqual(titles, {"The Matrix", "Inception", "Interstellar"})

    def test_suggestion_count_increments(self) -> None:
        self.assertEqual(self.service.suggestion_count(), 0)
        self.service.suggest("The Matrix")
        self.assertEqual(self.service.suggestion_count(), 1)
        self.service.suggest("Inception")
        self.assertEqual(self.service.suggestion_count(), 2)

    def test_clear_suggestions_removes_all(self) -> None:
        self.service.suggest("The Matrix")
        self.service.suggest("Inception")
        self.assertEqual(self.service.suggestion_count(), 2)

        self.service.clear_suggestions()
        self.assertEqual(self.service.suggestion_count(), 0)
        self.assertEqual(len(self.service.get_suggestions()), 0)

    def test_format_suggestion_list_when_empty(self) -> None:
        message = self.service.format_suggestion_list()
        self.assertEqual(message, "The suggestion list is currently empty.")

    def test_format_suggestion_list_with_single_suggestion(self) -> None:
        self.service.suggest("The Matrix")

        message = self.service.format_suggestion_list()
        self.assertEqual(message, "Current watch items:\n- The Matrix")

    def test_format_suggestion_list_links_to_the_original_suggestion_post(self) -> None:
        self.service.suggest(
            "The Matrix",
            database_id=1,
            guild_id=100,
            channel_id=200,
            message_id=300,
        )

        message = self.service.format_suggestion_list()

        self.assertEqual(
            message,
            "Current watch items:\n"
            "- The Matrix | [Original suggestion](https://discord.com/channels/100/200/300)",
        )

    def test_format_suggestion_list_omits_suggestion_ids(self) -> None:
        self.service.suggest("The Matrix")

        message = self.service.format_suggestion_list()

        self.assertNotIn("[1]", message)

    def test_format_suggestion_list_with_multiple_suggestions(self) -> None:
        self.service.suggest("The Matrix")
        self.service.suggest("Inception")
        self.service.suggest("Interstellar")

        message = self.service.format_suggestion_list()
        self.assertEqual(
            message,
            "Current watch items:\n- The Matrix\n- Inception\n- Interstellar",
        )

    def test_format_suggestion_list_preserves_insertion_order(self) -> None:
        self.service.suggest("Interstellar")
        self.service.suggest("The Matrix")
        self.service.suggest("Inception")

        message = self.service.format_suggestion_list()
        self.assertEqual(
            message,
            "Current watch items:\n- Interstellar\n- The Matrix\n- Inception",
        )

    def test_format_suggestion_list_omits_imdb_information(self) -> None:
        self.service.suggest("The Matrix", "tt0133093")

        message = self.service.format_suggestion_list()
        self.assertNotIn("tt0133093", message)

    def test_remove_suggestion_successful_removal(self) -> None:
        self.service.suggest("The Matrix")

        result = self.service.remove_suggestion("The Matrix")
        self.assertTrue(result.success)
        self.assertEqual(result.message, 'Removed "The Matrix" from the suggestion list.')
        self.assertEqual(self.service.suggestion_count(), 0)

    def test_remove_suggestion_matches_case_insensitively(self) -> None:
        self.service.suggest("The Matrix")

        result = self.service.remove_suggestion("the matrix")
        self.assertTrue(result.success)
        self.assertEqual(result.message, 'Removed "The Matrix" from the suggestion list.')
        self.assertEqual(self.service.suggestion_count(), 0)

    def test_remove_suggestion_ignores_surrounding_whitespace(self) -> None:
        self.service.suggest("The Matrix")

        result = self.service.remove_suggestion("  The Matrix  ")
        self.assertTrue(result.success)
        self.assertEqual(result.message, 'Removed "The Matrix" from the suggestion list.')
        self.assertEqual(self.service.suggestion_count(), 0)

    def test_remove_suggestion_rejects_empty_title(self) -> None:
        self.service.suggest("The Matrix")

        result = self.service.remove_suggestion("")
        self.assertFalse(result.success)
        self.assertEqual(result.message, "I need a title before I can remove it.")
        self.assertEqual(self.service.suggestion_count(), 1)

    def test_remove_suggestion_rejects_whitespace_only_title(self) -> None:
        self.service.suggest("The Matrix")

        result = self.service.remove_suggestion("   ")
        self.assertFalse(result.success)
        self.assertEqual(result.message, "I need a title before I can remove it.")
        self.assertEqual(self.service.suggestion_count(), 1)

    def test_remove_suggestion_reports_title_not_found(self) -> None:
        self.service.suggest("The Matrix")

        result = self.service.remove_suggestion("Inception")
        self.assertFalse(result.success)
        self.assertEqual(result.message, "That title is not on the suggestion list.")
        self.assertEqual(self.service.suggestion_count(), 1)

    def test_remove_suggestion_leaves_other_suggestions_intact(self) -> None:
        self.service.suggest("The Matrix")
        self.service.suggest("Inception")
        self.service.suggest("Interstellar")

        result = self.service.remove_suggestion("Inception")
        self.assertTrue(result.success)

        remaining_titles = [item.title for item in self.service.get_suggestions()]
        self.assertEqual(remaining_titles, ["The Matrix", "Interstellar"])

    def test_remove_suggestion_preserves_insertion_order_of_remaining_items(self) -> None:
        self.service.suggest("Interstellar")
        self.service.suggest("The Matrix")
        self.service.suggest("Inception")

        self.service.remove_suggestion("The Matrix")

        message = self.service.format_suggestion_list()
        self.assertEqual(
            message,
            "Current watch items:\n- Interstellar\n- Inception",
        )

    def test_suggest_persists_the_new_suggestion(self) -> None:
        self.service.suggest("The Matrix")

        reloaded_titles = [item.title for item in self.repository.load().watch_items]
        self.assertEqual(reloaded_titles, ["The Matrix"])

    def test_remove_suggestion_persists_the_removal(self) -> None:
        self.service.suggest("The Matrix")
        self.service.suggest("Inception")

        self.service.remove_suggestion("The Matrix")

        reloaded_titles = [item.title for item in self.repository.load().watch_items]
        self.assertEqual(reloaded_titles, ["Inception"])

    def test_failed_suggest_does_not_persist_anything(self) -> None:
        self.service.suggest("")

        self.assertEqual(self.repository.load().watch_items, [])

    def test_new_service_loads_previously_persisted_suggestions(self) -> None:
        self.service.suggest("The Matrix")
        self.service.suggest("Inception")

        reloaded_service = SuggestionService(
            repository=self.repository, database_repository=self.database_repository
        )

        titles = [item.title for item in reloaded_service.get_suggestions()]
        self.assertEqual(titles, ["The Matrix", "Inception"])

    def test_new_service_starts_empty_when_no_suggestions_file_exists(self) -> None:
        empty_repository = JsonSuggestionRepository(Path(self._temp_dir.name) / "does_not_exist.json")

        service = SuggestionService(repository=empty_repository, database_repository=self.database_repository)

        self.assertEqual(service.suggestion_count(), 0)

    def test_suggestion_ids_are_assigned_sequentially(self) -> None:
        self.service.suggest("The Matrix")
        self.service.suggest("Inception")
        self.service.suggest("Interstellar")

        ids = [item.id for item in self.service.get_suggestions()]
        self.assertEqual(ids, [1, 2, 3])

    def test_suggestion_ids_are_never_reused_after_removal(self) -> None:
        self.service.suggest("The Matrix")
        self.service.suggest("Inception")
        self.service.remove_suggestion("The Matrix")
        self.service.suggest("Interstellar")

        ids = [item.id for item in self.service.get_suggestions()]
        # Inception keeps ID 2; the removed Matrix's ID (1) is not reissued.
        self.assertEqual(ids, [2, 3])

    def test_suggestion_ids_persist_across_simulated_restarts(self) -> None:
        self.service.suggest("The Matrix")
        self.service.suggest("Inception")

        restarted_service = SuggestionService(
            repository=self.repository, database_repository=self.database_repository
        )
        restarted_service.suggest("Interstellar")

        ids = [item.id for item in restarted_service.get_suggestions()]
        self.assertEqual(ids, [1, 2, 3])

    def test_existing_suggestions_keep_their_ids_after_reload(self) -> None:
        self.service.suggest("The Matrix")
        self.service.suggest("Inception")

        reloaded_service = SuggestionService(
            repository=self.repository, database_repository=self.database_repository
        )

        original_ids = {item.title: item.id for item in self.service.get_suggestions()}
        reloaded_ids = {item.title: item.id for item in reloaded_service.get_suggestions()}
        self.assertEqual(original_ids, reloaded_ids)

    def test_legacy_suggestions_file_without_ids_is_migrated_on_load(self) -> None:
        legacy_json = """
        {
          "suggestions": [
            {"title": "The Matrix", "media_type": "movie", "metadata_ids": {}},
            {"title": "Inception", "media_type": "movie", "metadata_ids": {}}
          ]
        }
        """
        legacy_path = Path(self._temp_dir.name) / "legacy_suggestions.json"
        legacy_path.write_text(legacy_json, encoding="utf-8")
        legacy_repository = JsonSuggestionRepository(legacy_path)

        service = SuggestionService(repository=legacy_repository, database_repository=self.database_repository)

        ids = [item.id for item in service.get_suggestions()]
        self.assertEqual(ids, [1, 2])

        # A newly suggested title should not collide with the migrated IDs.
        service.suggest("Interstellar")
        new_ids = [item.id for item in service.get_suggestions()]
        self.assertEqual(new_ids, [1, 2, 3])

    def test_migrated_ids_are_written_back_to_disk(self) -> None:
        legacy_json = """
        {
          "suggestions": [
            {"title": "The Matrix", "media_type": "movie", "metadata_ids": {}}
          ]
        }
        """
        legacy_path = Path(self._temp_dir.name) / "legacy_suggestions.json"
        legacy_path.write_text(legacy_json, encoding="utf-8")
        legacy_repository = JsonSuggestionRepository(legacy_path)

        SuggestionService(repository=legacy_repository, database_repository=self.database_repository)

        reloaded = legacy_repository.load()
        self.assertEqual(reloaded.watch_items[0].id, 1)
        self.assertFalse(reloaded.migrated)

    def test_suggestions_command_output_omits_internal_ids(self) -> None:
        self.service.suggest("The Matrix")
        self.service.suggest("Inception")

        message = self.service.format_suggestion_list()
        self.assertEqual(
            message,
            "Current watch items:\n- The Matrix\n- Inception",
        )


class SuggestionServiceDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        """Create a fresh service backed by isolated, temporary repositories."""
        self._temp_dir = tempfile.TemporaryDirectory()
        self.repository = JsonSuggestionRepository(Path(self._temp_dir.name) / "suggestions.json")
        self.database_repository = JsonSuggestionDatabaseRepository(
            Path(self._temp_dir.name) / "suggestion_databases.json"
        )
        self.service = SuggestionService(
            repository=self.repository, database_repository=self.database_repository
        )

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_create_database_succeeds(self) -> None:
        result = self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)

        self.assertTrue(result.success)
        self.assertIsNotNone(result.database)
        self.assertEqual(result.database.name, "Sunday Watch Party")
        self.assertEqual(result.database.database_id, 1)

    def test_create_database_assigns_sequential_ids(self) -> None:
        first = self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)
        second = self.service.create_database("Kung Fu Movies", guild_id=100, channel_id=201)

        self.assertEqual(first.database.database_id, 1)
        self.assertEqual(second.database.database_id, 2)

    def test_create_database_rejects_empty_name(self) -> None:
        result = self.service.create_database("", guild_id=100, channel_id=200)

        self.assertFalse(result.success)
        self.assertIsNone(result.database)

    def test_create_database_rejects_whitespace_only_name(self) -> None:
        result = self.service.create_database("   ", guild_id=100, channel_id=200)
        self.assertFalse(result.success)

    def test_create_database_trims_the_name(self) -> None:
        result = self.service.create_database("  Sunday Watch Party  ", guild_id=100, channel_id=200)
        self.assertEqual(result.database.name, "Sunday Watch Party")

    def test_create_database_rejects_duplicate_name_case_insensitively(self) -> None:
        self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)

        result = self.service.create_database("sunday watch party", guild_id=100, channel_id=201)

        self.assertFalse(result.success)
        self.assertIn("already exists", result.message)

    def test_create_database_allows_the_same_name_in_a_different_guild(self) -> None:
        self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)

        result = self.service.create_database("Sunday Watch Party", guild_id=101, channel_id=201)

        self.assertTrue(result.success)

    def test_create_database_rejects_duplicate_channel_id(self) -> None:
        self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)

        result = self.service.create_database("Kung Fu Movies", guild_id=100, channel_id=200)

        self.assertFalse(result.success)
        self.assertIn("already has a suggestion database", result.message)

    def test_create_database_allows_the_same_channel_id_in_a_different_guild(self) -> None:
        self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)

        result = self.service.create_database("Sunday Watch Party", guild_id=101, channel_id=200)

        self.assertTrue(result.success)

    def test_create_database_defaults_to_active(self) -> None:
        result = self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)
        self.assertTrue(result.database.active)

    def test_create_database_supports_creating_an_inactive_database(self) -> None:
        result = self.service.create_database(
            "Halloween Movies", guild_id=100, channel_id=200, active=False
        )
        self.assertTrue(result.success)
        self.assertFalse(result.database.active)

    def test_get_database_retrieves_by_id(self) -> None:
        created = self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)

        fetched = self.service.get_database(created.database.database_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.name, "Sunday Watch Party")

    def test_get_database_returns_none_for_unknown_id(self) -> None:
        self.assertIsNone(self.service.get_database(999))

    def test_database_exists_true_for_a_created_database(self) -> None:
        created = self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)
        self.assertTrue(self.service.database_exists(created.database.database_id))

    def test_database_exists_false_for_an_unknown_id(self) -> None:
        self.assertFalse(self.service.database_exists(999))

    def test_list_databases_is_empty_initially(self) -> None:
        self.assertEqual(self.service.list_databases(), [])

    def test_list_databases_preserves_creation_order(self) -> None:
        self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)
        self.service.create_database("Kung Fu Movies", guild_id=100, channel_id=201)
        self.service.create_database("Halloween Movies", guild_id=100, channel_id=202)

        names = [database.name for database in self.service.list_databases()]
        self.assertEqual(names, ["Sunday Watch Party", "Kung Fu Movies", "Halloween Movies"])

    def test_list_databases_includes_inactive_databases(self) -> None:
        self.service.create_database("Halloween Movies", guild_id=100, channel_id=200, active=False)

        names = [database.name for database in self.service.list_databases()]
        self.assertEqual(names, ["Halloween Movies"])

    def test_list_databases_filters_by_guild_when_given(self) -> None:
        self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)
        self.service.create_database("Kung Fu Movies", guild_id=101, channel_id=201)

        names = [database.name for database in self.service.list_databases(guild_id=100)]
        self.assertEqual(names, ["Sunday Watch Party"])

    def test_list_databases_without_a_guild_returns_every_database(self) -> None:
        self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)
        self.service.create_database("Kung Fu Movies", guild_id=101, channel_id=201)

        names = [database.name for database in self.service.list_databases()]
        self.assertEqual(names, ["Sunday Watch Party", "Kung Fu Movies"])

    def test_create_database_persists_the_new_database(self) -> None:
        self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)

        reloaded = self.database_repository.load()
        self.assertEqual(len(reloaded.databases), 1)
        self.assertEqual(reloaded.databases[0].name, "Sunday Watch Party")

    def test_failed_create_database_does_not_persist_anything(self) -> None:
        self.service.create_database("", guild_id=100, channel_id=200)

        reloaded = self.database_repository.load()
        self.assertEqual(reloaded.databases, [])

    def test_new_service_loads_previously_persisted_databases(self) -> None:
        self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)
        self.service.create_database("Kung Fu Movies", guild_id=100, channel_id=201)

        reloaded_service = SuggestionService(
            repository=self.repository, database_repository=self.database_repository
        )

        names = [database.name for database in reloaded_service.list_databases()]
        self.assertEqual(names, ["Sunday Watch Party", "Kung Fu Movies"])

    def test_new_service_starts_with_no_databases_when_no_file_exists(self) -> None:
        empty_database_repository = JsonSuggestionDatabaseRepository(
            Path(self._temp_dir.name) / "does_not_exist.json"
        )

        service = SuggestionService(repository=self.repository, database_repository=empty_database_repository)

        self.assertEqual(service.list_databases(), [])

    def test_database_ids_persist_and_are_not_reused_across_restarts(self) -> None:
        self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)

        restarted_service = SuggestionService(
            repository=self.repository, database_repository=self.database_repository
        )
        result = restarted_service.create_database("Kung Fu Movies", guild_id=100, channel_id=201)

        self.assertEqual(result.database.database_id, 2)

    # --- Deactivating a database ---------------------------------------------

    def test_deactivate_database_succeeds(self) -> None:
        created = self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)

        result = self.service.deactivate_database(created.database.database_id, guild_id=100)

        self.assertTrue(result.success)
        self.assertFalse(self.service.get_database(created.database.database_id).active)

    def test_deactivate_database_rejects_an_unknown_id(self) -> None:
        result = self.service.deactivate_database(999, guild_id=100)

        self.assertFalse(result.success)
        self.assertIn("doesn't exist", result.message)

    def test_deactivate_database_rejects_a_database_from_another_guild(self) -> None:
        created = self.service.create_database(
            "Other Guild", guild_id=200, channel_id=300
        )

        result = self.service.deactivate_database(
            created.database.database_id, guild_id=100
        )

        self.assertFalse(result.success)
        self.assertIn("doesn't exist", result.message)
        self.assertTrue(self.service.get_database(created.database.database_id).active)

    def test_deactivate_database_rejects_an_already_inactive_database(self) -> None:
        created = self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)
        self.service.deactivate_database(created.database.database_id, guild_id=100)

        result = self.service.deactivate_database(created.database.database_id, guild_id=100)

        self.assertFalse(result.success)
        self.assertIn("already inactive", result.message)

    def test_deactivate_database_does_not_delete_it(self) -> None:
        created = self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)

        self.service.deactivate_database(created.database.database_id, guild_id=100)

        self.assertTrue(self.service.database_exists(created.database.database_id))
        self.assertIn(created.database.database_id, [db.database_id for db in self.service.list_databases()])

    def test_deactivate_database_preserves_its_suggestions(self) -> None:
        created = self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)
        self.service.suggest("The Matrix", database_id=created.database.database_id)

        self.service.deactivate_database(created.database.database_id, guild_id=100)

        titles = [item.title for item in self.service.get_suggestions()]
        self.assertEqual(titles, ["The Matrix"])
        self.assertEqual(
            self.service.suggestion_count_for_database(created.database.database_id), 1
        )

    def test_deactivate_database_persists_the_change(self) -> None:
        created = self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)
        self.service.deactivate_database(created.database.database_id, guild_id=100)

        reloaded = self.database_repository.load()
        self.assertFalse(reloaded.databases[0].active)

    # --- Suggestion counts per database --------------------------------------

    def test_suggestion_count_for_database_is_zero_when_empty(self) -> None:
        created = self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)
        self.assertEqual(self.service.suggestion_count_for_database(created.database.database_id), 0)

    def test_suggestion_count_for_database_counts_only_that_database(self) -> None:
        first = self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)
        second = self.service.create_database("Kung Fu Movies", guild_id=100, channel_id=201)
        self.service.suggest("The Matrix", database_id=first.database.database_id)
        self.service.suggest("Inception", database_id=first.database.database_id)
        self.service.suggest("Enter the Dragon", database_id=second.database.database_id)

        self.assertEqual(self.service.suggestion_count_for_database(first.database.database_id), 2)
        self.assertEqual(self.service.suggestion_count_for_database(second.database.database_id), 1)

    # --- Inactive databases are excluded from automatic resolution ----------

    def test_resolve_database_for_channel_ignores_an_inactive_database_matching_the_channel(self) -> None:
        created = self.service.create_database(
            "Sunday Watch Party", guild_id=100, channel_id=200, active=False
        )

        resolution = self.service.resolve_database_for_channel(100, created.database.channel_id)

        self.assertIsNone(resolution.database)
        self.assertIn("configure a suggestion database", resolution.error_message)

    def test_resolve_database_for_channel_ignores_an_inactive_database_as_the_sole_database(self) -> None:
        self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200, active=False)

        resolution = self.service.resolve_database_for_channel(100, 999)

        self.assertIsNone(resolution.database)
        self.assertIn("configure a suggestion database", resolution.error_message)

    def test_resolve_database_for_channel_uses_the_only_active_database(self) -> None:
        self.service.create_database("Retired", guild_id=100, channel_id=200, active=False)
        active = self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=201)

        resolution = self.service.resolve_database_for_channel(100, 999)

        self.assertIsNotNone(resolution.database)
        self.assertEqual(resolution.database.database_id, active.database.database_id)


class SuggestionServiceDatabaseAssociationTests(unittest.TestCase):
    def setUp(self) -> None:
        """Create a fresh service backed by isolated, temporary repositories."""
        self._temp_dir = tempfile.TemporaryDirectory()
        self.repository = JsonSuggestionRepository(Path(self._temp_dir.name) / "suggestions.json")
        self.database_repository = JsonSuggestionDatabaseRepository(
            Path(self._temp_dir.name) / "suggestion_databases.json"
        )
        self.service = SuggestionService(
            repository=self.repository, database_repository=self.database_repository
        )

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    # --- Suggestions belonging to a database -------------------------------

    def test_suggestion_belongs_to_the_database_it_was_created_in(self) -> None:
        created_database = self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)

        result = self.service.suggest("The Matrix", database_id=created_database.database.database_id)

        self.assertEqual(result.watch_item.database_id, created_database.database.database_id)

    def test_suggestion_without_a_database_id_defaults_to_none(self) -> None:
        result = self.service.suggest("The Matrix")
        self.assertIsNone(result.watch_item.database_id)

    def test_format_suggestion_list_filters_by_database(self) -> None:
        first_database = self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)
        second_database = self.service.create_database("Kung Fu Movies", guild_id=100, channel_id=201)
        self.service.suggest("The Matrix", database_id=first_database.database.database_id)
        self.service.suggest("Enter the Dragon", database_id=second_database.database.database_id)

        message = self.service.format_suggestion_list(first_database.database.database_id)
        self.assertIn("The Matrix", message)
        self.assertNotIn("Enter the Dragon", message)

    # --- Migration of pre-existing suggestions ------------------------------

    def test_migration_assigns_orphaned_suggestions_to_the_first_database_created(self) -> None:
        self.service.suggest("The Matrix")
        self.service.suggest("Inception")

        created = self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)

        database_ids = {item.title: item.database_id for item in self.service.get_suggestions()}
        self.assertEqual(
            database_ids,
            {
                "The Matrix": created.database.database_id,
                "Inception": created.database.database_id,
            },
        )

    def test_migration_persists_the_reassigned_suggestions(self) -> None:
        self.service.suggest("The Matrix")

        created = self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)

        reloaded = self.repository.load()
        self.assertEqual(reloaded.watch_items[0].database_id, created.database.database_id)

    def test_migration_does_not_touch_suggestions_already_in_a_database(self) -> None:
        first_database = self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)
        self.service.suggest("The Matrix", database_id=first_database.database.database_id)
        self.service.suggest("Inception")  # Orphaned, created after the first database exists.

        second_database = self.service.create_database("Kung Fu Movies", guild_id=100, channel_id=201)

        database_ids = {item.title: item.database_id for item in self.service.get_suggestions()}
        # "The Matrix" already belonged to the first database and must stay there;
        # a second database being created does not re-trigger migration for
        # "Inception", which is left orphaned.
        self.assertEqual(database_ids["The Matrix"], first_database.database.database_id)
        self.assertIsNone(database_ids["Inception"])
        self.assertNotEqual(database_ids["The Matrix"], second_database.database.database_id)

    def test_creating_a_database_with_no_orphaned_suggestions_does_not_error(self) -> None:
        result = self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)
        self.assertTrue(result.success)

    # --- Resolving a database for a channel ---------------------------------

    def test_resolve_database_for_channel_matches_the_configured_channel(self) -> None:
        self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)
        self.service.create_database("Kung Fu Movies", guild_id=100, channel_id=201)

        resolution = self.service.resolve_database_for_channel(100, 201)
        self.assertIsNotNone(resolution.database)
        self.assertEqual(resolution.database.name, "Kung Fu Movies")

    def test_resolve_database_for_channel_uses_the_only_database_when_no_channel_matches(self) -> None:
        self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)

        resolution = self.service.resolve_database_for_channel(100, 999)
        self.assertIsNotNone(resolution.database)
        self.assertEqual(resolution.database.name, "Sunday Watch Party")

    def test_resolve_database_for_channel_is_ambiguous_with_multiple_non_matching_databases(self) -> None:
        self.service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)
        self.service.create_database("Kung Fu Movies", guild_id=100, channel_id=201)

        resolution = self.service.resolve_database_for_channel(100, 999)
        self.assertIsNone(resolution.database)
        self.assertIn("Multiple suggestion databases", resolution.error_message)

    def test_resolve_database_for_channel_fails_when_no_databases_exist(self) -> None:
        resolution = self.service.resolve_database_for_channel(100, 999)
        self.assertIsNone(resolution.database)
        self.assertIn("configure a suggestion database", resolution.error_message)

    # --- Discord message reference -------------------------------------------

    def test_attach_message_reference_updates_the_suggestion(self) -> None:
        result = self.service.suggest("The Matrix")

        updated = self.service.attach_message_reference(result.watch_item.id, message_id=999)

        self.assertTrue(updated)
        matching = next(item for item in self.service.get_suggestions() if item.id == result.watch_item.id)
        self.assertEqual(matching.message_id, 999)

    def test_attach_message_reference_persists_the_update(self) -> None:
        result = self.service.suggest("The Matrix")
        self.service.attach_message_reference(result.watch_item.id, message_id=999)

        reloaded = self.repository.load()
        self.assertEqual(reloaded.watch_items[0].message_id, 999)

    def test_attach_message_reference_returns_false_for_an_unknown_suggestion(self) -> None:
        updated = self.service.attach_message_reference(999, message_id=123)
        self.assertFalse(updated)


if __name__ == "__main__":
    unittest.main()


class SuggestionDatabaseScopingTests(unittest.TestCase):
    """Cross-guild resolution and database-scoped duplicate/removal behavior."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "databases.json"),
        )

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_resolution_never_uses_another_guilds_only_database(self) -> None:
        self.service.create_database("Other Guild", guild_id=2, channel_id=200)

        resolution = self.service.resolve_database_for_channel(guild_id=1, channel_id=999)

        self.assertIsNone(resolution.database)
        self.assertIn("configure a suggestion database", resolution.error_message)

    def test_resolution_uses_only_the_sole_database_in_the_requested_guild(self) -> None:
        self.service.create_database("Guild One", guild_id=1, channel_id=100)
        self.service.create_database("Guild Two", guild_id=2, channel_id=200)

        resolution = self.service.resolve_database_for_channel(guild_id=1, channel_id=999)

        self.assertEqual(resolution.database.guild_id, 1)
        self.assertEqual(resolution.database.channel_id, 100)

    def test_duplicate_title_is_rejected_within_the_same_database(self) -> None:
        database = self.service.create_database("One", guild_id=1, channel_id=100).database
        self.service.suggest("The Matrix", database_id=database.database_id)

        result = self.service.suggest("the matrix", database_id=database.database_id)

        self.assertFalse(result.success)

    def test_same_title_is_allowed_in_separate_databases(self) -> None:
        first = self.service.create_database("One", guild_id=1, channel_id=100).database
        second = self.service.create_database("Two", guild_id=1, channel_id=200).database

        one = self.service.suggest("The Matrix", database_id=first.database_id)
        two = self.service.suggest("The Matrix", database_id=second.database_id)

        self.assertTrue(one.success)
        self.assertTrue(two.success)
        self.assertNotEqual(one.watch_item.id, two.watch_item.id)

    def test_legacy_database_none_duplicate_behavior_is_preserved(self) -> None:
        self.service.suggest("The Matrix")

        result = self.service.suggest("the matrix")

        self.assertFalse(result.success)

    def test_ambiguous_title_only_removal_does_not_remove_anything(self) -> None:
        first = self.service.create_database("One", guild_id=1, channel_id=100).database
        second = self.service.create_database("Two", guild_id=1, channel_id=200).database
        self.service.suggest("The Matrix", database_id=first.database_id)
        self.service.suggest("The Matrix", database_id=second.database_id)

        result = self.service.remove_suggestion("The Matrix")

        self.assertFalse(result.success)
        self.assertIn("more than one suggestion database", result.message)
        self.assertEqual(self.service.suggestion_count(), 2)

    def test_removal_with_database_context_removes_only_the_matching_item(self) -> None:
        first = self.service.create_database("One", guild_id=1, channel_id=100).database
        second = self.service.create_database("Two", guild_id=1, channel_id=200).database
        self.service.suggest("The Matrix", database_id=first.database_id)
        self.service.suggest("The Matrix", database_id=second.database_id)

        result = self.service.remove_suggestion("The Matrix", database_id=first.database_id)

        self.assertTrue(result.success)
        remaining = self.service.get_suggestions()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].database_id, second.database_id)


if __name__ == "__main__":
    unittest.main()


class SuggestionServiceJourneyMethodsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.matrix = self.suggestion_service.suggest("The Matrix").watch_item

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    # --- get_suggestion ---------------------------------------------------

    def test_get_suggestion_returns_the_matching_watch_item(self) -> None:
        found = self.suggestion_service.get_suggestion(self.matrix.id)

        self.assertIsNotNone(found)
        self.assertEqual(found.title, "The Matrix")

    def test_get_suggestion_returns_none_for_an_unknown_id(self) -> None:
        self.assertIsNone(self.suggestion_service.get_suggestion(999))

    # --- record_vote_win ----------------------------------------------------

    def test_record_vote_win_returns_true_and_updates_the_journey(self) -> None:
        updated = self.suggestion_service.record_vote_win(self.matrix.id, date(2026, 7, 1))

        self.assertTrue(updated)
        journey = self.suggestion_service.get_suggestion(self.matrix.id).journey
        self.assertEqual(journey.times_won, 1)
        self.assertEqual(journey.last_won_date, date(2026, 7, 1))
        self.assertEqual(journey.voting_appearances, 1)
        self.assertEqual(journey.last_nominated_date, date(2026, 7, 1))
        self.assertEqual(journey.winning_vote, "The Matrix")

    def test_record_vote_win_returns_false_for_an_unknown_id(self) -> None:
        updated = self.suggestion_service.record_vote_win(999, date(2026, 7, 1))
        self.assertFalse(updated)

    def test_record_vote_win_persists_the_update(self) -> None:
        self.suggestion_service.record_vote_win(self.matrix.id, date(2026, 7, 1))

        root = Path(self._temp_dir.name)
        reloaded_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        reloaded_journey = reloaded_service.get_suggestion(self.matrix.id).journey
        self.assertEqual(reloaded_journey.times_won, 1)

    def test_repeated_wins_accumulate(self) -> None:
        self.suggestion_service.record_vote_win(self.matrix.id, date(2026, 6, 1))
        self.suggestion_service.record_vote_win(self.matrix.id, date(2026, 7, 1))

        journey = self.suggestion_service.get_suggestion(self.matrix.id).journey
        self.assertEqual(journey.times_won, 2)
        self.assertEqual(journey.last_won_date, date(2026, 7, 1))
        self.assertEqual(journey.voting_appearances, 2)


class RejectSuggestionTests(unittest.TestCase):
    """FR-022: the "I will not watch" rejection workflow."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.matrix = self.suggestion_service.suggest("The Matrix").watch_item

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    # --- Adding a rejection --------------------------------------------------

    def test_records_a_rejection(self) -> None:
        result = self.suggestion_service.reject_suggestion(self.matrix.id, discord_user_id=1)

        self.assertTrue(result.success)
        journey = self.suggestion_service.get_suggestion(self.matrix.id).journey
        self.assertEqual(journey.rejected_by_discord_user_ids, (1,))

    def test_reject_fails_for_a_nonexistent_suggestion(self) -> None:
        result = self.suggestion_service.reject_suggestion(999, discord_user_id=1)

        self.assertFalse(result.success)
        self.assertIn("doesn't exist", result.message)

    def test_reject_persists_the_rejection(self) -> None:
        self.suggestion_service.reject_suggestion(self.matrix.id, discord_user_id=1)

        root = Path(self._temp_dir.name)
        reloaded = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.assertEqual(
            reloaded.get_suggestion(self.matrix.id).journey.rejected_by_discord_user_ids, (1,)
        )

    # --- Duplicate rejection prevention ---------------------------------------

    def test_duplicate_rejection_from_the_same_member_fails(self) -> None:
        self.suggestion_service.reject_suggestion(self.matrix.id, discord_user_id=1)

        result = self.suggestion_service.reject_suggestion(self.matrix.id, discord_user_id=1)

        self.assertFalse(result.success)
        self.assertIn("already indicated", result.message)

    def test_duplicate_rejection_does_not_count_again_toward_the_threshold(self) -> None:
        self.suggestion_service.reject_suggestion(self.matrix.id, discord_user_id=1, rejection_threshold=2)

        self.suggestion_service.reject_suggestion(self.matrix.id, discord_user_id=1, rejection_threshold=2)

        watch_item = self.suggestion_service.get_suggestion(self.matrix.id)
        self.assertEqual(watch_item.status, WatchItemStatus.SUGGESTED)

    # --- Removing a rejection --------------------------------------------------

    def test_removes_a_rejection(self) -> None:
        self.suggestion_service.reject_suggestion(self.matrix.id, discord_user_id=1)

        result = self.suggestion_service.remove_rejection(self.matrix.id, discord_user_id=1)

        self.assertTrue(result.success)
        journey = self.suggestion_service.get_suggestion(self.matrix.id).journey
        self.assertEqual(journey.rejected_by_discord_user_ids, ())

    def test_remove_rejection_fails_for_a_nonexistent_suggestion(self) -> None:
        result = self.suggestion_service.remove_rejection(999, discord_user_id=1)

        self.assertFalse(result.success)
        self.assertIn("doesn't exist", result.message)

    def test_remove_rejection_fails_when_the_member_never_rejected_it(self) -> None:
        result = self.suggestion_service.remove_rejection(self.matrix.id, discord_user_id=1)

        self.assertFalse(result.success)
        self.assertIn("haven't rejected", result.message)

    def test_remove_rejection_persists_the_change(self) -> None:
        self.suggestion_service.reject_suggestion(self.matrix.id, discord_user_id=1)
        self.suggestion_service.remove_rejection(self.matrix.id, discord_user_id=1)

        root = Path(self._temp_dir.name)
        reloaded = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.assertEqual(
            reloaded.get_suggestion(self.matrix.id).journey.rejected_by_discord_user_ids, ()
        )

    # --- Rejection threshold reached / automatic archive ------------------------

    def test_reaching_the_threshold_archives_the_suggestion(self) -> None:
        self.suggestion_service.reject_suggestion(self.matrix.id, discord_user_id=1, rejection_threshold=2)

        result = self.suggestion_service.reject_suggestion(
            self.matrix.id, discord_user_id=2, rejection_threshold=2
        )

        self.assertTrue(result.success)
        self.assertIn("archived", result.message)
        watch_item = self.suggestion_service.get_suggestion(self.matrix.id)
        self.assertEqual(watch_item.status, WatchItemStatus.ARCHIVED)

    def test_below_the_threshold_does_not_archive(self) -> None:
        self.suggestion_service.reject_suggestion(self.matrix.id, discord_user_id=1, rejection_threshold=2)

        watch_item = self.suggestion_service.get_suggestion(self.matrix.id)
        self.assertEqual(watch_item.status, WatchItemStatus.SUGGESTED)

    def test_default_threshold_is_two(self) -> None:
        self.suggestion_service.reject_suggestion(self.matrix.id, discord_user_id=1)
        self.suggestion_service.reject_suggestion(self.matrix.id, discord_user_id=2)

        watch_item = self.suggestion_service.get_suggestion(self.matrix.id)
        self.assertEqual(watch_item.status, WatchItemStatus.ARCHIVED)

    def test_archiving_preserves_the_rejection_history(self) -> None:
        self.suggestion_service.reject_suggestion(self.matrix.id, discord_user_id=1, rejection_threshold=2)
        self.suggestion_service.reject_suggestion(self.matrix.id, discord_user_id=2, rejection_threshold=2)

        journey = self.suggestion_service.get_suggestion(self.matrix.id).journey
        self.assertEqual(journey.rejected_by_discord_user_ids, (1, 2))

    def test_custom_threshold_is_honored(self) -> None:
        self.suggestion_service.reject_suggestion(self.matrix.id, discord_user_id=1, rejection_threshold=3)
        self.suggestion_service.reject_suggestion(self.matrix.id, discord_user_id=2, rejection_threshold=3)

        watch_item = self.suggestion_service.get_suggestion(self.matrix.id)
        self.assertEqual(watch_item.status, WatchItemStatus.SUGGESTED)

        self.suggestion_service.reject_suggestion(self.matrix.id, discord_user_id=3, rejection_threshold=3)
        self.assertEqual(
            self.suggestion_service.get_suggestion(self.matrix.id).status, WatchItemStatus.ARCHIVED
        )

    # --- Archived suggestions reject additional rejections/removals -------------

    def test_archived_suggestions_reject_additional_rejections(self) -> None:
        self.suggestion_service.reject_suggestion(self.matrix.id, discord_user_id=1, rejection_threshold=2)
        self.suggestion_service.reject_suggestion(self.matrix.id, discord_user_id=2, rejection_threshold=2)

        result = self.suggestion_service.reject_suggestion(
            self.matrix.id, discord_user_id=3, rejection_threshold=2
        )

        self.assertFalse(result.success)
        self.assertIn("already been archived", result.message)

    def test_archived_suggestions_reject_removal_of_a_rejection(self) -> None:
        self.suggestion_service.reject_suggestion(self.matrix.id, discord_user_id=1, rejection_threshold=2)
        self.suggestion_service.reject_suggestion(self.matrix.id, discord_user_id=2, rejection_threshold=2)

        result = self.suggestion_service.remove_rejection(self.matrix.id, discord_user_id=1)

        self.assertFalse(result.success)
        self.assertIn("already been archived", result.message)
        # The rejection history is untouched, exactly as it was when archived.
        journey = self.suggestion_service.get_suggestion(self.matrix.id).journey
        self.assertEqual(journey.rejected_by_discord_user_ids, (1, 2))


class GetSuggestionsForDatabaseArchivingTests(unittest.TestCase):
    """FR-022: archived suggestions are excluded from the active pool by default."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.database = self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=1, channel_id=100
        ).database
        self.matrix = self.suggestion_service.suggest(
            "The Matrix", database_id=self.database.database_id
        ).watch_item
        self.inception = self.suggestion_service.suggest(
            "Inception", database_id=self.database.database_id
        ).watch_item

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_archived_suggestions_are_excluded_by_default(self) -> None:
        self.suggestion_service.reject_suggestion(self.matrix.id, discord_user_id=1, rejection_threshold=1)

        items = self.suggestion_service.get_suggestions_for_database(self.database.database_id)

        titles = {item.title for item in items}
        self.assertNotIn("The Matrix", titles)
        self.assertIn("Inception", titles)

    def test_include_archived_true_still_returns_them(self) -> None:
        self.suggestion_service.reject_suggestion(self.matrix.id, discord_user_id=1, rejection_threshold=1)

        items = self.suggestion_service.get_suggestions_for_database(
            self.database.database_id, include_archived=True
        )

        titles = {item.title for item in items}
        self.assertIn("The Matrix", titles)
        self.assertIn("Inception", titles)

    def test_non_archived_suggestions_are_unaffected(self) -> None:
        items = self.suggestion_service.get_suggestions_for_database(self.database.database_id)

        self.assertEqual(len(items), 2)


class ArchiveAndReactivateSuggestionTests(unittest.TestCase):
    """FR-033A: WASH Crew-initiated archive/reactivate, distinct from
    reject_suggestion()'s rejection-threshold auto-archive."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.database = self.service.create_database("Movie Night", guild_id=1, channel_id=100).database
        self.matrix = self.service.suggest("The Matrix", database_id=self.database.database_id).watch_item

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_archive_suggestion_sets_archived_status(self) -> None:
        result = self.service.archive_suggestion(self.matrix.id)

        self.assertTrue(result.success)
        self.assertEqual(WatchItemStatus.ARCHIVED, self.service.get_suggestion(self.matrix.id).status)

    def test_archive_suggestion_does_not_touch_rejection_history(self) -> None:
        self.service.archive_suggestion(self.matrix.id)

        item = self.service.get_suggestion(self.matrix.id)
        self.assertEqual((), item.journey.rejected_by_discord_user_ids)

    def test_archive_suggestion_rejects_an_already_archived_item(self) -> None:
        self.service.archive_suggestion(self.matrix.id)

        result = self.service.archive_suggestion(self.matrix.id)

        self.assertFalse(result.success)

    def test_archive_suggestion_rejects_an_unknown_id(self) -> None:
        result = self.service.archive_suggestion(999999)

        self.assertFalse(result.success)

    def test_reactivate_suggestion_returns_an_archived_item_to_suggested(self) -> None:
        self.service.archive_suggestion(self.matrix.id)

        result = self.service.reactivate_suggestion(self.matrix.id)

        self.assertTrue(result.success)
        self.assertEqual(WatchItemStatus.SUGGESTED, self.service.get_suggestion(self.matrix.id).status)

    def test_reactivate_suggestion_returns_a_watched_item_to_suggested(self) -> None:
        item = self.service.get_suggestion(self.matrix.id)
        item.status = WatchItemStatus.WATCHED

        result = self.service.reactivate_suggestion(self.matrix.id)

        self.assertTrue(result.success)
        self.assertEqual(WatchItemStatus.SUGGESTED, self.service.get_suggestion(self.matrix.id).status)

    def test_reactivate_preserves_the_same_id_and_journey(self) -> None:
        self.service.reject_suggestion(self.matrix.id, discord_user_id=1, rejection_threshold=1)

        self.service.reactivate_suggestion(self.matrix.id)

        item = self.service.get_suggestion(self.matrix.id)
        self.assertEqual(self.matrix.id, item.id)
        self.assertEqual((1,), item.journey.rejected_by_discord_user_ids)

    def test_reactivate_rejects_an_already_active_item(self) -> None:
        result = self.service.reactivate_suggestion(self.matrix.id)

        self.assertFalse(result.success)

    def test_reactivate_rejects_an_unknown_id(self) -> None:
        result = self.service.reactivate_suggestion(999999)

        self.assertFalse(result.success)

    def test_reactivate_does_not_create_a_duplicate_record(self) -> None:
        self.service.archive_suggestion(self.matrix.id)

        self.service.reactivate_suggestion(self.matrix.id)

        items = self.service.get_suggestions_for_database(self.database.database_id, include_archived=True)
        self.assertEqual(1, len(items))


class FindMatchesForRemovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.database = self.service.create_database("Movie Night", guild_id=1, channel_id=100).database
        self.matrix = self.service.suggest(
            "The Matrix (1999)", database_id=self.database.database_id
        ).watch_item

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_matches_by_reference_number_with_hash(self) -> None:
        matches = self.service.find_matches_for_removal(f"#{self.matrix.id}")
        self.assertEqual([self.matrix], matches)

    def test_matches_by_bare_reference_number(self) -> None:
        matches = self.service.find_matches_for_removal(str(self.matrix.id))
        self.assertEqual([self.matrix], matches)

    def test_matches_by_exact_title(self) -> None:
        matches = self.service.find_matches_for_removal("The Matrix (1999)")
        self.assertEqual([self.matrix], matches)

    def test_matches_by_title_without_year(self) -> None:
        matches = self.service.find_matches_for_removal("The Matrix")
        self.assertEqual([self.matrix], matches)

    def test_no_matches_for_unrelated_query(self) -> None:
        matches = self.service.find_matches_for_removal("Inception")
        self.assertEqual([], matches)

    def test_no_matches_for_empty_query(self) -> None:
        matches = self.service.find_matches_for_removal("   ")
        self.assertEqual([], matches)

    def test_multiple_matches_when_title_is_ambiguous(self) -> None:
        other_database = self.service.create_database("Other DB", guild_id=1, channel_id=200).database
        other = self.service.suggest(
            "The Matrix (1999)", database_id=other_database.database_id
        ).watch_item

        matches = self.service.find_matches_for_removal("The Matrix (1999)")

        self.assertEqual({self.matrix.id, other.id}, {item.id for item in matches})

    def test_database_scoping_narrows_matches(self) -> None:
        other_database = self.service.create_database("Other DB", guild_id=1, channel_id=200).database
        self.service.suggest("The Matrix (1999)", database_id=other_database.database_id)

        matches = self.service.find_matches_for_removal(
            "The Matrix (1999)", database_id=self.database.database_id
        )

        self.assertEqual([self.matrix], matches)


class EditSuggestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.database = self.service.create_database("Movie Night", guild_id=1, channel_id=100).database
        self.other_database = self.service.create_database("Other DB", guild_id=1, channel_id=200).database
        self.matrix = self.service.suggest("The Matrix", database_id=self.database.database_id).watch_item

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_edit_updates_title(self) -> None:
        result = self.service.edit_suggestion(
            self.matrix.id,
            title="The Matrix Reloaded",
            release_year=None,
            imdb_url=None,
            database_id=self.database.database_id,
        )

        self.assertTrue(result.success)
        self.assertEqual("The Matrix Reloaded", result.watch_item.title)

    def test_edit_updates_release_year(self) -> None:
        result = self.service.edit_suggestion(
            self.matrix.id,
            title=self.matrix.title,
            release_year=1999,
            imdb_url=None,
            database_id=self.database.database_id,
        )

        self.assertEqual(1999, result.watch_item.release_year)

    def test_edit_updates_imdb_url(self) -> None:
        result = self.service.edit_suggestion(
            self.matrix.id,
            title=self.matrix.title,
            release_year=None,
            imdb_url="https://www.imdb.com/title/tt0133093/",
            database_id=self.database.database_id,
        )

        self.assertEqual(
            "https://www.imdb.com/title/tt0133093/", result.watch_item.metadata_ids[MetadataProvider.IMDB]
        )

    def test_edit_can_clear_the_imdb_url(self) -> None:
        self.service.edit_suggestion(
            self.matrix.id,
            title=self.matrix.title,
            release_year=None,
            imdb_url="https://www.imdb.com/title/tt0133093/",
            database_id=self.database.database_id,
        )

        result = self.service.edit_suggestion(
            self.matrix.id,
            title=self.matrix.title,
            release_year=None,
            imdb_url=None,
            database_id=self.database.database_id,
        )

        self.assertNotIn(MetadataProvider.IMDB, result.watch_item.metadata_ids)

    def test_edit_moves_to_another_database(self) -> None:
        result = self.service.edit_suggestion(
            self.matrix.id,
            title=self.matrix.title,
            release_year=None,
            imdb_url=None,
            database_id=self.other_database.database_id,
        )

        self.assertEqual(self.other_database.database_id, result.watch_item.database_id)
        self.assertEqual(
            0, self.service.suggestion_count_for_database(self.database.database_id)
        )

    def test_edit_preserves_stable_id(self) -> None:
        result = self.service.edit_suggestion(
            self.matrix.id,
            title="Renamed",
            release_year=None,
            imdb_url=None,
            database_id=self.database.database_id,
        )

        self.assertEqual(self.matrix.id, result.watch_item.id)

    def test_edit_preserves_history(self) -> None:
        self.service.reject_suggestion(self.matrix.id, discord_user_id=1, rejection_threshold=99)

        result = self.service.edit_suggestion(
            self.matrix.id,
            title="Renamed",
            release_year=None,
            imdb_url=None,
            database_id=self.database.database_id,
        )

        self.assertEqual((1,), result.watch_item.journey.rejected_by_discord_user_ids)

    def test_edit_sets_updated_at(self) -> None:
        result = self.service.edit_suggestion(
            self.matrix.id,
            title="Renamed",
            release_year=None,
            imdb_url=None,
            database_id=self.database.database_id,
        )

        self.assertIsNotNone(result.watch_item.updated_at)

    def test_edit_rejects_moving_onto_an_existing_title_in_the_destination(self) -> None:
        self.service.suggest("Inception", database_id=self.other_database.database_id)

        result = self.service.edit_suggestion(
            self.matrix.id,
            title="Inception",
            release_year=None,
            imdb_url=None,
            database_id=self.other_database.database_id,
        )

        self.assertFalse(result.success)

    def test_edit_rejects_an_unknown_suggestion(self) -> None:
        result = self.service.edit_suggestion(
            999999, title="Anything", release_year=None, imdb_url=None, database_id=self.database.database_id
        )

        self.assertFalse(result.success)

    def test_edit_rejects_an_empty_title(self) -> None:
        result = self.service.edit_suggestion(
            self.matrix.id, title="   ", release_year=None, imdb_url=None, database_id=self.database.database_id
        )

        self.assertFalse(result.success)


class SetConfirmationPostReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.database = self.service.create_database("Movie Night", guild_id=1, channel_id=100).database
        self.matrix = self.service.suggest("The Matrix", database_id=self.database.database_id).watch_item

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_updates_guild_channel_and_message(self) -> None:
        updated = self.service.set_confirmation_post_reference(self.matrix.id, guild_id=1, channel_id=500, message_id=999)

        self.assertTrue(updated)
        item = self.service.get_suggestion(self.matrix.id)
        self.assertEqual(1, item.guild_id)
        self.assertEqual(500, item.channel_id)
        self.assertEqual(999, item.message_id)

    def test_persists_the_change(self) -> None:
        self.service.set_confirmation_post_reference(self.matrix.id, guild_id=1, channel_id=500, message_id=999)

        reloaded = SuggestionService(
            repository=self.service._repository, database_repository=self.service._database_repository
        )
        self.assertEqual(500, reloaded.get_suggestion(self.matrix.id).channel_id)

    def test_returns_false_for_unknown_id(self) -> None:
        updated = self.service.set_confirmation_post_reference(999999, guild_id=1, channel_id=500, message_id=999)

        self.assertFalse(updated)
