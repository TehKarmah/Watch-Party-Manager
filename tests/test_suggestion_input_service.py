import unittest

from watch_party_manager.services.imdb_metadata_service import ImdbMetadataService
from watch_party_manager.services.suggestion_input_service import SuggestionInputService


class SuggestionInputServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        metadata_service = ImdbMetadataService(
            api_key="test-key",
            fetch_json=lambda _: {
                "Title": "The Matrix",
                "Year": "1999",
                "Response": "True",
            },
        )
        self.service = SuggestionInputService(metadata_service)

    async def test_preserves_normal_title_without_imdb_url(self) -> None:
        result = await self.service.resolve(" The Matrix ")

        self.assertTrue(result.success)
        self.assertEqual(result.title, "The Matrix")
        self.assertIsNone(result.imdb_url)

    async def test_preserves_title_and_normalizes_separate_imdb_url(self) -> None:
        result = await self.service.resolve("The Matrix", "imdb.com/title/TT0133093")

        self.assertTrue(result.success)
        self.assertEqual(result.title, "The Matrix (1999)")
        self.assertEqual(result.imdb_url, "https://www.imdb.com/title/tt0133093/")

    async def test_resolves_imdb_link_entered_as_title(self) -> None:
        result = await self.service.resolve("https://www.imdb.com/title/tt0133093/")

        self.assertTrue(result.success)
        self.assertEqual(result.title, "The Matrix (1999)")
        self.assertEqual(result.imdb_url, "https://www.imdb.com/title/tt0133093/")

    async def test_rejects_empty_input(self) -> None:
        result = await self.service.resolve("   ")

        self.assertFalse(result.success)
        self.assertIn("title or IMDb link", result.error_message)

    async def test_rejects_invalid_separate_imdb_url(self) -> None:
        result = await self.service.resolve("The Matrix", "https://example.com/movie")

        self.assertFalse(result.success)
        self.assertIn("valid IMDb", result.error_message)

    async def test_relays_omdb_resolution_failure(self) -> None:
        service = SuggestionInputService(
            ImdbMetadataService(
                api_key="test-key",
                fetch_json=lambda _: {"Response": "False", "Error": "Movie not found!"},
            )
        )

        result = await service.resolve("https://www.imdb.com/title/tt0133093/")

        self.assertFalse(result.success)
        self.assertIn("Movie not found", result.error_message)

    async def test_relays_missing_api_key_error(self) -> None:
        service = SuggestionInputService(ImdbMetadataService(api_key=""))

        result = await service.resolve("https://www.imdb.com/title/tt0133093/")

        self.assertFalse(result.success)
        self.assertIn("OMDB_API_KEY", result.error_message)

    async def test_rejects_two_different_imdb_links(self) -> None:
        result = await self.service.resolve(
            "https://www.imdb.com/title/tt0133093/",
            "https://www.imdb.com/title/tt0078748/",
        )

        self.assertFalse(result.success)
        self.assertIn("two different links", result.error_message)


if __name__ == "__main__":
    unittest.main()
