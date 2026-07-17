import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import (
    perform_add_suggestion,
    perform_add_suggestion_from_input,
    perform_list_suggestions,
)
from watch_party_manager.domain.watch_item import MetadataProvider
from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.suggestion_input_service import SuggestionInputService
from watch_party_manager.services.imdb_metadata_service import ImdbMetadataService
from watch_party_manager.services.suggestion_service import SuggestionService

GUILD_ID = 100
CONFIGURED_CHANNEL_ID = 200
OTHER_CONFIGURED_CHANNEL_ID = 201
UNCONFIGURED_CHANNEL_ID = 999


class SuggestionCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        """A real SuggestionService backed by isolated, temporary repositories."""
        self._temp_dir = tempfile.TemporaryDirectory()
        self.repository = JsonSuggestionRepository(Path(self._temp_dir.name) / "suggestions.json")
        self.database_repository = JsonSuggestionDatabaseRepository(
            Path(self._temp_dir.name) / "suggestion_databases.json"
        )
        self.suggestion_service = SuggestionService(
            repository=self.repository, database_repository=self.database_repository
        )

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    # --- /add ---------------------------------------------------------------

    def test_add_inside_a_configured_database_uses_that_database(self) -> None:
        created = self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=GUILD_ID, channel_id=CONFIGURED_CHANNEL_ID
        )

        message, ephemeral, watch_item = perform_add_suggestion(
            self.suggestion_service, GUILD_ID, CONFIGURED_CHANNEL_ID, "The Matrix", None
        )

        self.assertFalse(ephemeral)
        self.assertIsNotNone(watch_item)
        self.assertEqual(watch_item.database_id, created.database.database_id)
        self.assertIn("Added", message)

    def test_add_outside_any_database_with_exactly_one_configured_uses_it_automatically(self) -> None:
        created = self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=GUILD_ID, channel_id=CONFIGURED_CHANNEL_ID
        )

        message, ephemeral, watch_item = perform_add_suggestion(
            self.suggestion_service, GUILD_ID, UNCONFIGURED_CHANNEL_ID, "The Matrix", None
        )

        self.assertFalse(ephemeral)
        self.assertIsNotNone(watch_item)
        self.assertEqual(watch_item.database_id, created.database.database_id)

    def test_add_with_multiple_databases_configured_is_rejected_with_a_clear_message(self) -> None:
        self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=GUILD_ID, channel_id=CONFIGURED_CHANNEL_ID
        )
        self.suggestion_service.create_database(
            "Kung Fu Movies", guild_id=GUILD_ID, channel_id=OTHER_CONFIGURED_CHANNEL_ID
        )

        message, ephemeral, watch_item = perform_add_suggestion(
            self.suggestion_service, GUILD_ID, UNCONFIGURED_CHANNEL_ID, "The Matrix", None
        )

        self.assertTrue(ephemeral)
        self.assertIsNone(watch_item)
        self.assertIn("Multiple suggestion databases", message)
        self.assertEqual(self.suggestion_service.suggestion_count(), 0)

    def test_add_with_no_databases_configured_explains_wash_crew_must_configure_one(self) -> None:
        message, ephemeral, watch_item = perform_add_suggestion(
            self.suggestion_service, GUILD_ID, UNCONFIGURED_CHANNEL_ID, "The Matrix", None
        )

        self.assertTrue(ephemeral)
        self.assertIsNone(watch_item)
        self.assertIn("configure a suggestion database", message)
        self.assertEqual(self.suggestion_service.suggestion_count(), 0)

    def test_add_still_accepts_an_imdb_url(self) -> None:
        self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=GUILD_ID, channel_id=CONFIGURED_CHANNEL_ID
        )

        message, ephemeral, watch_item = perform_add_suggestion(
            self.suggestion_service,
            GUILD_ID,
            CONFIGURED_CHANNEL_ID,
            "The Matrix",
            "https://www.imdb.com/title/tt0133093/",
        )

        self.assertFalse(ephemeral)
        self.assertIsNotNone(watch_item)

    def test_add_failure_from_suggest_itself_is_relayed_and_stays_public(self) -> None:
        self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=GUILD_ID, channel_id=CONFIGURED_CHANNEL_ID
        )
        perform_add_suggestion(self.suggestion_service, GUILD_ID, CONFIGURED_CHANNEL_ID, "The Matrix", None)

        message, ephemeral, watch_item = perform_add_suggestion(
            self.suggestion_service, GUILD_ID, CONFIGURED_CHANNEL_ID, "The Matrix", None
        )

        self.assertFalse(ephemeral)
        self.assertIsNone(watch_item)
        self.assertIn("already on the list", message)

    def test_add_result_watch_item_supports_attaching_a_message_reference(self) -> None:
        # This is the exact hand-off bot.py relies on: perform_add_suggestion
        # returns the created suggestion so its Discord message ID can be
        # attached once the confirmation has actually been sent.
        self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=GUILD_ID, channel_id=CONFIGURED_CHANNEL_ID
        )

        _, _, watch_item = perform_add_suggestion(
            self.suggestion_service, GUILD_ID, CONFIGURED_CHANNEL_ID, "The Matrix", None
        )

        updated = self.suggestion_service.attach_message_reference(watch_item.id, message_id=555)
        self.assertTrue(updated)

        matching = next(
            item for item in self.suggestion_service.get_suggestions() if item.id == watch_item.id
        )
        self.assertEqual(matching.message_id, 555)
        self.assertEqual(matching.guild_id, GUILD_ID)
        self.assertEqual(matching.channel_id, CONFIGURED_CHANNEL_ID)

    # --- /list ----------------------------------------------------------------

    def test_list_inside_a_configured_database_shows_only_that_database(self) -> None:
        first = self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=GUILD_ID, channel_id=CONFIGURED_CHANNEL_ID
        )
        second = self.suggestion_service.create_database(
            "Kung Fu Movies", guild_id=GUILD_ID, channel_id=OTHER_CONFIGURED_CHANNEL_ID
        )
        self.suggestion_service.suggest("The Matrix", database_id=first.database.database_id)
        self.suggestion_service.suggest("Enter the Dragon", database_id=second.database.database_id)

        message = perform_list_suggestions(self.suggestion_service, GUILD_ID, CONFIGURED_CHANNEL_ID)

        self.assertIn("The Matrix", message)
        self.assertNotIn("Enter the Dragon", message)

    def test_list_shows_only_the_watch_item_name_when_no_post_link_exists(self) -> None:
        created = self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=GUILD_ID, channel_id=CONFIGURED_CHANNEL_ID
        )
        self.suggestion_service.suggest("The Matrix", database_id=created.database.database_id)

        message = perform_list_suggestions(self.suggestion_service, GUILD_ID, CONFIGURED_CHANNEL_ID)

        self.assertIn("- The Matrix", message)
        self.assertNotIn("[1]", message)

    def test_list_displays_a_separate_discord_post_link(self) -> None:
        created = self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=GUILD_ID, channel_id=CONFIGURED_CHANNEL_ID
        )
        result = self.suggestion_service.suggest(
            "The Matrix",
            database_id=created.database.database_id,
            guild_id=GUILD_ID,
            channel_id=CONFIGURED_CHANNEL_ID,
            message_id=555,
        )

        message = perform_list_suggestions(
            self.suggestion_service, GUILD_ID, CONFIGURED_CHANNEL_ID
        )

        self.assertIsNotNone(result.watch_item)
        self.assertIn(
            f"The Matrix | [Original suggestion](https://discord.com/channels/{GUILD_ID}/"
            f"{CONFIGURED_CHANNEL_ID}/555)",
            message,
        )

    def test_list_never_shows_imdb_information(self) -> None:
        created = self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=GUILD_ID, channel_id=CONFIGURED_CHANNEL_ID
        )
        self.suggestion_service.suggest(
            "The Matrix",
            "https://www.imdb.com/title/tt0133093/",
            database_id=created.database.database_id,
        )

        message = perform_list_suggestions(self.suggestion_service, GUILD_ID, CONFIGURED_CHANNEL_ID)

        self.assertNotIn("imdb.com", message)

    def test_list_outside_any_database_with_exactly_one_configured_lists_it_automatically(self) -> None:
        created = self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=GUILD_ID, channel_id=CONFIGURED_CHANNEL_ID
        )
        self.suggestion_service.suggest("The Matrix", database_id=created.database.database_id)

        message = perform_list_suggestions(self.suggestion_service, GUILD_ID, UNCONFIGURED_CHANNEL_ID)

        self.assertIn("The Matrix", message)

    def test_list_with_multiple_databases_configured_returns_a_temporary_message(self) -> None:
        self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=GUILD_ID, channel_id=CONFIGURED_CHANNEL_ID
        )
        self.suggestion_service.create_database(
            "Kung Fu Movies", guild_id=GUILD_ID, channel_id=OTHER_CONFIGURED_CHANNEL_ID
        )

        message = perform_list_suggestions(self.suggestion_service, GUILD_ID, UNCONFIGURED_CHANNEL_ID)

        self.assertIn("Multiple suggestion databases", message)

    def test_list_with_no_databases_configured_returns_an_appropriate_message(self) -> None:
        message = perform_list_suggestions(self.suggestion_service, GUILD_ID, UNCONFIGURED_CHANNEL_ID)

        self.assertIn("configure a suggestion database", message)

    def test_list_inside_a_configured_database_with_no_suggestions_is_empty(self) -> None:
        self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=GUILD_ID, channel_id=CONFIGURED_CHANNEL_ID
        )

        message = perform_list_suggestions(self.suggestion_service, GUILD_ID, CONFIGURED_CHANNEL_ID)

        self.assertIn("currently empty", message)


if __name__ == "__main__":
    unittest.main()


class SuggestionCommandGuildScopingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "databases.json"),
        )

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_add_does_not_fall_back_to_another_guilds_database(self) -> None:
        self.suggestion_service.create_database("Other", guild_id=999, channel_id=777)

        message, ephemeral, watch_item = perform_add_suggestion(
            self.suggestion_service, GUILD_ID, UNCONFIGURED_CHANNEL_ID, "The Matrix", None
        )

        self.assertTrue(ephemeral)
        self.assertIsNone(watch_item)
        self.assertIn("configure a suggestion database", message)

    def test_list_does_not_fall_back_to_another_guilds_database(self) -> None:
        other = self.suggestion_service.create_database("Other", guild_id=999, channel_id=777).database
        self.suggestion_service.suggest("The Matrix", database_id=other.database_id)

        message = perform_list_suggestions(
            self.suggestion_service, GUILD_ID, UNCONFIGURED_CHANNEL_ID
        )

        self.assertIn("configure a suggestion database", message)
        self.assertNotIn("The Matrix", message)


class SuggestionInputCommandIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        repository = JsonSuggestionRepository(Path(self._temp_dir.name) / "suggestions.json")
        database_repository = JsonSuggestionDatabaseRepository(
            Path(self._temp_dir.name) / "suggestion_databases.json"
        )
        self.suggestion_service = SuggestionService(
            repository=repository, database_repository=database_repository
        )
        self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=GUILD_ID, channel_id=CONFIGURED_CHANNEL_ID
        )
        metadata_service = ImdbMetadataService(
            api_key="test-key",
            fetch_json=lambda _: {
                "Title": "Star Wars: Episode IV - A New Hope",
                "Year": "1977",
                "Response": "True",
            },
        )
        self.input_service = SuggestionInputService(metadata_service)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    async def test_add_resolves_imdb_link_entered_as_title_before_persisting(self) -> None:
        message, ephemeral, watch_item = await perform_add_suggestion_from_input(
            self.input_service,
            self.suggestion_service,
            GUILD_ID,
            CONFIGURED_CHANNEL_ID,
            "https://www.imdb.com/title/tt0076759/",
            None,
        )

        self.assertFalse(ephemeral)
        self.assertIsNotNone(watch_item)
        self.assertEqual(watch_item.title, "Star Wars: Episode IV - A New Hope (1977)")
        self.assertEqual(
            watch_item.metadata_ids[MetadataProvider.IMDB],
            "https://www.imdb.com/title/tt0076759/",
        )
        self.assertIn("Star Wars: Episode IV - A New Hope (1977)", message)

    async def test_add_does_not_persist_when_imdb_resolution_fails(self) -> None:
        failing_input_service = SuggestionInputService(
            ImdbMetadataService(
                api_key="test-key",
                fetch_json=lambda _: {"Response": "False", "Error": "Movie not found!"},
            )
        )

        message, ephemeral, watch_item = await perform_add_suggestion_from_input(
            failing_input_service,
            self.suggestion_service,
            GUILD_ID,
            CONFIGURED_CHANNEL_ID,
            "https://www.imdb.com/title/tt0076759/",
            None,
        )

        self.assertTrue(ephemeral)
        self.assertIsNone(watch_item)
        self.assertIn("Movie not found", message)
        self.assertEqual(self.suggestion_service.suggestion_count(), 0)

    async def test_add_preserves_a_normal_title_and_separate_imdb_link(self) -> None:
        _, ephemeral, watch_item = await perform_add_suggestion_from_input(
            self.input_service,
            self.suggestion_service,
            GUILD_ID,
            CONFIGURED_CHANNEL_ID,
            "The Matrix",
            "imdb.com/title/tt0133093",
        )

        self.assertFalse(ephemeral)
        self.assertEqual(watch_item.title, "The Matrix")
        self.assertEqual(
            watch_item.metadata_ids[MetadataProvider.IMDB],
            "https://www.imdb.com/title/tt0133093/",
        )

