import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.imdb_metadata_service import ImdbMetadataService
from watch_party_manager.services.suggestion_input_service import SuggestionInputService
from watch_party_manager.services.suggestion_repair_service import SuggestionRepairService
from watch_party_manager.services.suggestion_service import SuggestionService


class SuggestionRepairServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.service = SuggestionService(
            repository=JsonSuggestionRepository(Path(self.temp_dir.name) / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(
                Path(self.temp_dir.name) / "databases.json"
            ),
        )
        self.database = self.service.create_database("Main", 1, 2).database

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _repair_service(self, payload: dict) -> SuggestionRepairService:
        metadata = ImdbMetadataService(api_key="test", fetch_json=lambda _: payload)
        return SuggestionRepairService(self.service, SuggestionInputService(metadata))

    async def test_repairs_title_that_is_an_imdb_url(self) -> None:
        self.service.suggest(
            "https://www.imdb.com/title/tt0076759/",
            database_id=self.database.database_id,
            guild_id=1,
            channel_id=2,
            message_id=3,
        )
        report = await self._repair_service(
            {"Response": "True", "Title": "Star Wars", "Year": "1977"}
        ).repair_all()
        item = self.service.get_suggestions()[0]
        self.assertEqual(item.title, "Star Wars (1977)")
        self.assertEqual(item.message_id, 3)
        self.assertEqual(report.repaired, 1)
        self.assertEqual(report.scanned, 1)

    async def test_repairs_known_bad_title_when_imdb_metadata_exists(self) -> None:
        self.service.suggest(
            "JavaScript is disabled",
            "https://www.imdb.com/title/tt0076759/",
            database_id=self.database.database_id,
        )
        report = await self._repair_service(
            {"Response": "True", "Title": "Star Wars", "Year": "1977"}
        ).repair_all()
        self.assertEqual(self.service.get_suggestions()[0].title, "Star Wars (1977)")
        self.assertEqual(report.repaired, 1)

    async def test_removes_known_bad_title_without_imdb_metadata(self) -> None:
        self.service.suggest(
            "JavaScript is disabled", database_id=self.database.database_id
        )
        report = await self._repair_service({}).repair_all()
        self.assertEqual(self.service.suggestion_count(), 0)
        self.assertEqual(report.removed, 1)

    async def test_leaves_valid_suggestion_unchanged(self) -> None:
        self.service.suggest("Alien (1979)", database_id=self.database.database_id)
        report = await self._repair_service({}).repair_all()
        self.assertEqual(report.unchanged, 1)
        self.assertEqual(self.service.get_suggestions()[0].title, "Alien (1979)")

    async def test_failed_lookup_is_reported_without_deleting_item(self) -> None:
        self.service.suggest(
            "https://www.imdb.com/title/tt0076759/",
            database_id=self.database.database_id,
        )
        report = await self._repair_service(
            {"Response": "False", "Error": "Movie not found!"}
        ).repair_all()
        self.assertEqual(report.failed, 1)
        self.assertEqual(self.service.suggestion_count(), 1)

    async def test_repaired_duplicate_is_removed(self) -> None:
        self.service.suggest("Star Wars (1977)", database_id=self.database.database_id)
        self.service.suggest(
            "https://www.imdb.com/title/tt0076759/",
            database_id=self.database.database_id,
        )
        report = await self._repair_service(
            {"Response": "True", "Title": "Star Wars", "Year": "1977"}
        ).repair_all()
        self.assertEqual(self.service.suggestion_count(), 1)
        self.assertEqual(report.removed, 1)
        self.assertEqual(report.unchanged, 1)

    async def test_report_formats_all_counts(self) -> None:
        self.service.suggest("Alien", database_id=self.database.database_id)
        message = (await self._repair_service({}).repair_all()).format_message()
        self.assertIn("Scanned: 1", message)
        self.assertIn("Repaired: 0", message)
        self.assertIn("Removed: 0", message)
        self.assertIn("Failed: 0", message)
        self.assertIn("Unchanged: 1", message)

    async def test_changes_are_persisted(self) -> None:
        self.service.suggest(
            "https://www.imdb.com/title/tt0076759/",
            database_id=self.database.database_id,
        )
        await self._repair_service(
            {"Response": "True", "Title": "Star Wars", "Year": "1977"}
        ).repair_all()
        reloaded = SuggestionService(
            repository=JsonSuggestionRepository(Path(self.temp_dir.name) / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(
                Path(self.temp_dir.name) / "databases.json"
            ),
        )
        self.assertEqual(reloaded.get_suggestions()[0].title, "Star Wars (1977)")


if __name__ == "__main__":
    unittest.main()
