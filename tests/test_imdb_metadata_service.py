import unittest

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

    async def test_resolves_title_from_open_graph_metadata(self) -> None:
        service = ImdbMetadataService(
            fetch_html=lambda _: '<meta property="og:title" content="The Matrix (1999) - IMDb">'
        )

        result = await service.resolve_title("https://www.imdb.com/title/tt0133093/")

        self.assertTrue(result.success)
        self.assertEqual(result.title, "The Matrix")
        self.assertEqual(result.imdb_id, "tt0133093")
        self.assertEqual(result.imdb_url, "https://www.imdb.com/title/tt0133093/")

    async def test_resolves_title_when_content_attribute_comes_first(self) -> None:
        service = ImdbMetadataService(
            fetch_html=lambda _: '<meta content="Alien &amp; Aliens (1979) - IMDb" property="og:title">'
        )

        result = await service.resolve_title("https://www.imdb.com/title/tt0078748/")

        self.assertTrue(result.success)
        self.assertEqual(result.title, "Alien & Aliens")

    async def test_falls_back_to_html_title(self) -> None:
        service = ImdbMetadataService(fetch_html=lambda _: "<title>Arrival (2016) - IMDb</title>")

        result = await service.resolve_title("https://www.imdb.com/title/tt2543164/")

        self.assertTrue(result.success)
        self.assertEqual(result.title, "Arrival")

    async def test_reports_invalid_url_without_fetching(self) -> None:
        fetched = False

        def fetch(_: str) -> str:
            nonlocal fetched
            fetched = True
            return ""

        result = await ImdbMetadataService(fetch_html=fetch).resolve_title("The Matrix")

        self.assertFalse(result.success)
        self.assertFalse(fetched)
        self.assertIn("valid IMDb", result.error_message)

    async def test_reports_fetch_failure(self) -> None:
        def fail(_: str) -> str:
            raise OSError("offline")

        result = await ImdbMetadataService(fetch_html=fail).resolve_title(
            "https://www.imdb.com/title/tt0133093/"
        )

        self.assertFalse(result.success)
        self.assertIn("could not retrieve", result.error_message)

    async def test_reports_missing_title_metadata(self) -> None:
        service = ImdbMetadataService(fetch_html=lambda _: "<html><body>No metadata</body></html>")

        result = await service.resolve_title("https://www.imdb.com/title/tt0133093/")

        self.assertFalse(result.success)
        self.assertIn("could not determine", result.error_message)


if __name__ == "__main__":
    unittest.main()
