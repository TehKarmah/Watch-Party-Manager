import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.watch_item import MetadataProvider
from watch_party_manager.services.suggestion_service import SuggestionService


class SuggestionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        """Create a fresh service for each test."""
        self.service = SuggestionService()

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
        self.assertEqual(message, "Current suggestions:\n1. The Matrix")

    def test_format_suggestion_list_with_multiple_suggestions(self) -> None:
        self.service.suggest("The Matrix")
        self.service.suggest("Inception")
        self.service.suggest("Interstellar")

        message = self.service.format_suggestion_list()
        self.assertEqual(
            message,
            "Current suggestions:\n1. The Matrix\n2. Inception\n3. Interstellar",
        )

    def test_format_suggestion_list_preserves_insertion_order(self) -> None:
        self.service.suggest("Interstellar")
        self.service.suggest("The Matrix")
        self.service.suggest("Inception")

        message = self.service.format_suggestion_list()
        self.assertEqual(
            message,
            "Current suggestions:\n1. Interstellar\n2. The Matrix\n3. Inception",
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
            "Current suggestions:\n1. Interstellar\n2. Inception",
        )


if __name__ == "__main__":
    unittest.main()
