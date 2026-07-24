import os
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from watch_party_manager.services.imdb_metadata_service import ImdbMetadataService


class ImdbMetadataServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_recognizes_supported_imdb_title_urls(self) -> None:
        self.assertTrue(ImdbMetadataService.is_imdb_title_url("https://www.imdb.com/title/tt0133093/"))
        self.assertTrue(ImdbMetadataService.is_imdb_title_url("imdb.com/title/TT0133093"))
        self.assertTrue(ImdbMetadataService.is_imdb_title_url("https://imdb.com/title/tt0133093/?ref_=fn_all_ttl_1"))

    def test_rejects_non_title_and_non_imdb_urls(self) -> None:
        self.assertFalse(ImdbMetadataService.is_imdb_title_url("The Matrix"))
        self.assertFalse(ImdbMetadataService.is_imdb_title_url("https://www.imdb.com/name/nm0000206/"))
        self.assertFalse(ImdbMetadataService.is_imdb_title_url("https://example.com/title/tt0133093/"))

    def test_normalizes_imdb_url(self) -> None:
        self.assertEqual(
            ImdbMetadataService.normalize_imdb_url(" imdb.com/title/TT0133093?ref_=test "),
            "https://www.imdb.com/title/tt0133093/",
        )

    def test_is_configured_true_when_an_api_key_is_given(self) -> None:
        service = ImdbMetadataService(api_key="abc123")
        self.assertTrue(service.is_configured)

    def test_is_configured_false_without_an_api_key(self) -> None:
        service = ImdbMetadataService(api_key="")
        self.assertFalse(service.is_configured)

    def test_is_configured_reads_the_environment_when_no_key_is_given(self) -> None:
        with patch.dict(os.environ, {"OMDB_API_KEY": "env-key"}, clear=False):
            self.assertTrue(ImdbMetadataService().is_configured)
        with patch.dict(os.environ, {"OMDB_API_KEY": ""}, clear=False):
            self.assertFalse(ImdbMetadataService().is_configured)

    async def test_resolves_title_and_year_from_omdb(self) -> None:
        service = ImdbMetadataService(
            api_key="test-key",
            fetch_json=lambda _: {
                "Title": "Star Wars: Episode IV - A New Hope",
                "Year": "1977",
                "Response": "True",
            },
        )

        result = await service.resolve_title("https://www.imdb.com/title/tt0076759/")

        self.assertTrue(result.success)
        self.assertEqual(result.title, "Star Wars: Episode IV - A New Hope (1977)")
        self.assertEqual(result.imdb_id, "tt0076759")
        self.assertEqual(result.imdb_url, "https://www.imdb.com/title/tt0076759/")

    async def test_accepts_json_text_from_injected_fetcher(self) -> None:
        service = ImdbMetadataService(
            api_key="test-key",
            fetch_json=lambda _: '{"Title":"Machete","Year":"2010","Response":"True"}',
        )

        result = await service.resolve_title("https://www.imdb.com/title/tt0985694/")

        self.assertTrue(result.success)
        self.assertEqual(result.title, "Machete (2010)")

    async def test_omits_year_when_omdb_does_not_supply_one(self) -> None:
        service = ImdbMetadataService(
            api_key="test-key",
            fetch_json=lambda _: {"Title": "The Odyssey", "Year": "N/A", "Response": "True"},
        )

        result = await service.resolve_title("https://www.imdb.com/title/tt33764258/")

        self.assertTrue(result.success)
        self.assertEqual(result.title, "The Odyssey")

    async def test_request_uses_imdb_id_and_api_key(self) -> None:
        requested_urls: list[str] = []

        def fetch(url: str):
            requested_urls.append(url)
            return {"Title": "Alien", "Year": "1979", "Response": "True"}

        service = ImdbMetadataService(api_key="secret-key", fetch_json=fetch)
        await service.resolve_title("https://www.imdb.com/title/tt0078748/")

        query = parse_qs(urlparse(requested_urls[0]).query)
        self.assertEqual(query["apikey"], ["secret-key"])
        self.assertEqual(query["i"], ["tt0078748"])
        self.assertEqual(query["r"], ["json"])

    async def test_reads_api_key_from_environment(self) -> None:
        with patch.dict(os.environ, {"OMDB_API_KEY": "environment-key"}):
            service = ImdbMetadataService(
                fetch_json=lambda _: {"Title": "Arrival", "Year": "2016", "Response": "True"}
            )

        result = await service.resolve_title("https://www.imdb.com/title/tt2543164/")

        self.assertTrue(result.success)
        self.assertEqual(result.title, "Arrival (2016)")

    async def test_reports_missing_api_key_without_fetching(self) -> None:
        fetched = False

        def fetch(_: str):
            nonlocal fetched
            fetched = True
            return {}

        service = ImdbMetadataService(api_key="", fetch_json=fetch)
        result = await service.resolve_title("https://www.imdb.com/title/tt0133093/")

        self.assertFalse(result.success)
        self.assertFalse(fetched)
        self.assertIn("OMDB_API_KEY", result.error_message)

    async def test_reports_omdb_title_not_found(self) -> None:
        service = ImdbMetadataService(
            api_key="test-key",
            fetch_json=lambda _: {"Response": "False", "Error": "Incorrect IMDb ID."},
        )

        result = await service.resolve_title("https://www.imdb.com/title/tt0000000/")

        self.assertFalse(result.success)
        self.assertIn("Incorrect IMDb ID", result.error_message)

    async def test_reports_fetch_failure(self) -> None:
        def fail(_: str):
            raise OSError("offline")

        result = await ImdbMetadataService(api_key="test-key", fetch_json=fail).resolve_title(
            "https://www.imdb.com/title/tt0133093/"
        )

        self.assertFalse(result.success)
        self.assertIn("could not retrieve", result.error_message)

    async def test_reports_invalid_json_response(self) -> None:
        service = ImdbMetadataService(api_key="test-key", fetch_json=lambda _: "not-json")

        result = await service.resolve_title("https://www.imdb.com/title/tt0133093/")

        self.assertFalse(result.success)
        self.assertIn("unreadable response", result.error_message)

    async def test_reports_missing_title_in_success_response(self) -> None:
        service = ImdbMetadataService(
            api_key="test-key",
            fetch_json=lambda _: {"Title": "N/A", "Year": "1999", "Response": "True"},
        )

        result = await service.resolve_title("https://www.imdb.com/title/tt0133093/")

        self.assertFalse(result.success)
        self.assertIn("usable name", result.error_message)

    async def test_reports_invalid_url_without_fetching(self) -> None:
        fetched = False

        def fetch(_: str):
            nonlocal fetched
            fetched = True
            return {}

        result = await ImdbMetadataService(api_key="test-key", fetch_json=fetch).resolve_title(
            "The Matrix"
        )

        self.assertFalse(result.success)
        self.assertFalse(fetched)
        self.assertIn("valid IMDb", result.error_message)


if __name__ == "__main__":
    unittest.main()
