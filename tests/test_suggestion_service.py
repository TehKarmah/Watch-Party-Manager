import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.watch_item import MetadataProvider
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
        self.assertEqual(message, "Current suggestions:\n1. [1] The Matrix")

    def test_format_suggestion_list_with_multiple_suggestions(self) -> None:
        self.service.suggest("The Matrix")
        self.service.suggest("Inception")
        self.service.suggest("Interstellar")

        message = self.service.format_suggestion_list()
        self.assertEqual(
            message,
            "Current suggestions:\n1. [1] The Matrix\n2. [2] Inception\n3. [3] Interstellar",
        )

    def test_format_suggestion_list_preserves_insertion_order(self) -> None:
        self.service.suggest("Interstellar")
        self.service.suggest("The Matrix")
        self.service.suggest("Inception")

        message = self.service.format_suggestion_list()
        self.assertEqual(
            message,
            "Current suggestions:\n1. [1] Interstellar\n2. [2] The Matrix\n3. [3] Inception",
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
            "Current suggestions:\n1. [1] Interstellar\n2. [3] Inception",
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

    def test_suggestions_command_output_includes_ids(self) -> None:
        self.service.suggest("The Matrix")
        self.service.suggest("Inception")

        message = self.service.format_suggestion_list()
        self.assertEqual(
            message,
            "Current suggestions:\n1. [1] The Matrix\n2. [2] Inception",
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


if __name__ == "__main__":
    unittest.main()
