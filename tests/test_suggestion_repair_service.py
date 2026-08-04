import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.imdb_metadata_service import ImdbMetadataService
from watch_party_manager.services.suggestion_input_service import SuggestionInputService
from watch_party_manager.services.suggestion_repair_service import (
    SuggestionRepairService,
    resolve_repairable_suggestions,
)
from watch_party_manager.services.suggestion_service import SuggestionService

GUILD_ID = 1


class SuggestionRepairServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.service = SuggestionService(
            repository=JsonSuggestionRepository(Path(self.temp_dir.name) / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(
                Path(self.temp_dir.name) / "databases.json"
            ),
        )
        self.database = self.service.create_database("Main", GUILD_ID, 2).database

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _repair_service(self, payload: dict) -> SuggestionRepairService:
        metadata = ImdbMetadataService(api_key="test", fetch_json=lambda _: payload)
        return SuggestionRepairService(self.service, SuggestionInputService(metadata))

    async def test_repairs_title_that_is_an_imdb_url(self) -> None:
        self.service.suggest(
            "https://www.imdb.com/title/tt0076759/",
            database_id=self.database.database_id,
            guild_id=GUILD_ID,
            channel_id=2,
            message_id=3,
        )
        report = await self._repair_service(
            {"Response": "True", "Title": "Star Wars", "Year": "1977"}
        ).repair_all(GUILD_ID)
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
            guild_id=GUILD_ID,
        )
        report = await self._repair_service(
            {"Response": "True", "Title": "Star Wars", "Year": "1977"}
        ).repair_all(GUILD_ID)
        self.assertEqual(self.service.get_suggestions()[0].title, "Star Wars (1977)")
        self.assertEqual(report.repaired, 1)

    async def test_removes_known_bad_title_without_imdb_metadata(self) -> None:
        self.service.suggest(
            "JavaScript is disabled", database_id=self.database.database_id, guild_id=GUILD_ID
        )
        report = await self._repair_service({}).repair_all(GUILD_ID)
        self.assertEqual(self.service.suggestion_count(), 0)
        self.assertEqual(report.removed, 1)

    async def test_leaves_valid_suggestion_unchanged(self) -> None:
        self.service.suggest("Alien (1979)", database_id=self.database.database_id, guild_id=GUILD_ID)
        report = await self._repair_service({}).repair_all(GUILD_ID)
        self.assertEqual(report.unchanged, 1)
        self.assertEqual(self.service.get_suggestions()[0].title, "Alien (1979)")

    async def test_failed_lookup_is_reported_without_deleting_item(self) -> None:
        self.service.suggest(
            "https://www.imdb.com/title/tt0076759/",
            database_id=self.database.database_id,
            guild_id=GUILD_ID,
        )
        report = await self._repair_service(
            {"Response": "False", "Error": "Movie not found!"}
        ).repair_all(GUILD_ID)
        self.assertEqual(report.failed, 1)
        self.assertEqual(self.service.suggestion_count(), 1)

    async def test_repaired_duplicate_is_removed(self) -> None:
        self.service.suggest(
            "Star Wars (1977)", database_id=self.database.database_id, guild_id=GUILD_ID
        )
        self.service.suggest(
            "https://www.imdb.com/title/tt0076759/",
            database_id=self.database.database_id,
            guild_id=GUILD_ID,
        )
        report = await self._repair_service(
            {"Response": "True", "Title": "Star Wars", "Year": "1977"}
        ).repair_all(GUILD_ID)
        self.assertEqual(self.service.suggestion_count(), 1)
        self.assertEqual(report.removed, 1)
        self.assertEqual(report.unchanged, 1)

    async def test_report_formats_all_counts(self) -> None:
        self.service.suggest("Alien", database_id=self.database.database_id, guild_id=GUILD_ID)
        message = (await self._repair_service({}).repair_all(GUILD_ID)).format_message()
        self.assertIn("Scanned: 1", message)
        self.assertIn("Repaired: 0", message)
        self.assertIn("Removed: 0", message)
        self.assertIn("Failed: 0", message)
        self.assertIn("Unchanged: 1", message)
        self.assertIn("Suggestion posts updated: 0", message)
        self.assertIn("Suggestion post sync failures: 0", message)

    async def test_post_sync_is_never_called_when_not_supplied(self) -> None:
        # Backward compatibility: every pre-existing caller omits post_sync.
        self.service.suggest(
            "https://www.imdb.com/title/tt0076759/",
            database_id=self.database.database_id,
            guild_id=GUILD_ID,
        )
        report = await self._repair_service(
            {"Response": "True", "Title": "Star Wars", "Year": "1977"}
        ).repair_all(GUILD_ID)
        self.assertEqual(report.repaired, 1)
        self.assertEqual(report.posts_updated, 0)
        self.assertEqual(report.post_sync_failures, 0)

    async def test_post_sync_is_called_once_per_repaired_suggestion_and_counted(self) -> None:
        self.service.suggest(
            "https://www.imdb.com/title/tt0076759/",
            database_id=self.database.database_id,
            guild_id=GUILD_ID,
        )
        synced_items = []

        async def post_sync(watch_item):
            synced_items.append(watch_item.id)
            return True

        report = await self._repair_service(
            {"Response": "True", "Title": "Star Wars", "Year": "1977"}
        ).repair_all(GUILD_ID, post_sync=post_sync)
        self.assertEqual(report.repaired, 1)
        self.assertEqual(report.posts_updated, 1)
        self.assertEqual(report.post_sync_failures, 0)
        self.assertEqual(len(synced_items), 1)

    async def test_post_sync_failure_is_counted_separately_from_success(self) -> None:
        self.service.suggest(
            "https://www.imdb.com/title/tt0076759/",
            database_id=self.database.database_id,
            guild_id=GUILD_ID,
        )

        async def post_sync(watch_item):
            return False

        report = await self._repair_service(
            {"Response": "True", "Title": "Star Wars", "Year": "1977"}
        ).repair_all(GUILD_ID, post_sync=post_sync)
        self.assertEqual(report.posts_updated, 0)
        self.assertEqual(report.post_sync_failures, 1)

    async def test_post_sync_is_never_called_for_a_removed_suggestion(self) -> None:
        self.service.suggest(
            "JavaScript is disabled", database_id=self.database.database_id, guild_id=GUILD_ID
        )
        calls = []

        async def post_sync(watch_item):
            calls.append(watch_item.id)
            return True

        report = await self._repair_service({}).repair_all(GUILD_ID, post_sync=post_sync)
        self.assertEqual(report.removed, 1)
        self.assertEqual(calls, [])
        self.assertEqual(report.posts_updated, 0)

    async def test_changes_are_persisted(self) -> None:
        self.service.suggest(
            "https://www.imdb.com/title/tt0076759/",
            database_id=self.database.database_id,
            guild_id=GUILD_ID,
        )
        await self._repair_service(
            {"Response": "True", "Title": "Star Wars", "Year": "1977"}
        ).repair_all(GUILD_ID)
        reloaded = SuggestionService(
            repository=JsonSuggestionRepository(Path(self.temp_dir.name) / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(
                Path(self.temp_dir.name) / "databases.json"
            ),
        )
        self.assertEqual(reloaded.get_suggestions()[0].title, "Star Wars (1977)")

    # --- Guild isolation ------------------------------------------------------

    async def test_repair_all_only_processes_the_requesting_guilds_suggestions(self) -> None:
        self.service.suggest(
            "https://www.imdb.com/title/tt0076759/",
            database_id=self.database.database_id,
            guild_id=GUILD_ID,
        )
        other_database = self.service.create_database("Other Guild", 200, 300).database
        self.service.suggest(
            "JavaScript is disabled", database_id=other_database.database_id, guild_id=200
        )

        report = await self._repair_service(
            {"Response": "True", "Title": "Star Wars", "Year": "1977"}
        ).repair_all(GUILD_ID)

        self.assertEqual(report.scanned, 1)
        self.assertEqual(report.repaired, 1)
        self.assertEqual(report.removed, 0)
        # The foreign guild's bad-title suggestion was never touched.
        foreign_item = next(
            item for item in self.service.get_suggestions() if item.guild_id == 200
        )
        self.assertEqual(foreign_item.title, "JavaScript is disabled")

    async def test_repair_all_never_repairs_another_guilds_malformed_suggestion(self) -> None:
        other_database = self.service.create_database("Other Guild", 200, 300).database
        self.service.suggest(
            "https://www.imdb.com/title/tt0076759/",
            database_id=other_database.database_id,
            guild_id=200,
        )

        report = await self._repair_service(
            {"Response": "True", "Title": "Star Wars", "Year": "1977"}
        ).repair_all(GUILD_ID)

        self.assertEqual(report.scanned, 0)
        self.assertEqual(report.repaired, 0)
        foreign_item = self.service.get_suggestions()[0]
        self.assertEqual(foreign_item.title, "https://www.imdb.com/title/tt0076759/")


class ResolveRepairableSuggestionsTests(unittest.TestCase):
    """resolve_repairable_suggestions() in isolation, mirroring
    resolve_refreshable_databases()'s own dedicated test coverage.
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.service = SuggestionService(
            repository=JsonSuggestionRepository(Path(self.temp_dir.name) / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(
                Path(self.temp_dir.name) / "databases.json"
            ),
        )

    def test_includes_only_the_requested_guilds_suggestions(self) -> None:
        database = self.service.create_database("Main", GUILD_ID, 2).database
        own_item = self.service.suggest(
            "Alien", database_id=database.database_id, guild_id=GUILD_ID
        ).watch_item
        other_database = self.service.create_database("Other Guild", 200, 300).database
        self.service.suggest("Predator", database_id=other_database.database_id, guild_id=200)

        result = resolve_repairable_suggestions(self.service.get_suggestions(), guild_id=GUILD_ID)

        self.assertEqual([own_item], result)

    def test_empty_input_returns_empty_list(self) -> None:
        self.assertEqual([], resolve_repairable_suggestions([], guild_id=GUILD_ID))

    def test_no_matching_guild_returns_empty_list(self) -> None:
        database = self.service.create_database("Main", 200, 2).database
        self.service.suggest("Alien", database_id=database.database_id, guild_id=200)

        result = resolve_repairable_suggestions(self.service.get_suggestions(), guild_id=GUILD_ID)

        self.assertEqual([], result)


if __name__ == "__main__":
    unittest.main()
