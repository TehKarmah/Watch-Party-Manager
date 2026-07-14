import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import (
    perform_database_add,
    perform_database_list,
    perform_database_remove,
)
from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.suggestion_service import SuggestionService

GUILD_ID = 100
CHANNEL_ID = 200
OTHER_CHANNEL_ID = 201
WASH_CREW_ROLE_ID = 999


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, roles=()) -> None:
        self.roles = list(roles)


class DatabaseCommandTests(unittest.TestCase):
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

    def _wash_crew_member(self) -> FakeMember:
        return FakeMember(roles=[FakeRole(WASH_CREW_ROLE_ID)])

    def _regular_member(self) -> FakeMember:
        return FakeMember(roles=[FakeRole(1)])

    # --- /database_add --------------------------------------------------------

    def test_database_add_successful_creation(self) -> None:
        message, ephemeral = perform_database_add(
            self.suggestion_service,
            self._wash_crew_member(),
            WASH_CREW_ROLE_ID,
            GUILD_ID,
            CHANNEL_ID,
            "Sunday Watch Party",
        )

        self.assertTrue(ephemeral)
        self.assertIn("Sunday Watch Party", message)
        self.assertIn("Database ID: 1", message)
        self.assertIn(f"<#{CHANNEL_ID}>", message)
        self.assertEqual(len(self.suggestion_service.list_databases()), 1)

    def test_database_add_rejects_a_duplicate_name(self) -> None:
        perform_database_add(
            self.suggestion_service,
            self._wash_crew_member(),
            WASH_CREW_ROLE_ID,
            GUILD_ID,
            CHANNEL_ID,
            "Sunday Watch Party",
        )

        message, ephemeral = perform_database_add(
            self.suggestion_service,
            self._wash_crew_member(),
            WASH_CREW_ROLE_ID,
            GUILD_ID,
            OTHER_CHANNEL_ID,
            "sunday watch party",
        )

        self.assertTrue(ephemeral)
        self.assertIn("already exists", message)
        self.assertEqual(len(self.suggestion_service.list_databases()), 1)

    def test_database_add_rejects_a_duplicate_channel(self) -> None:
        perform_database_add(
            self.suggestion_service,
            self._wash_crew_member(),
            WASH_CREW_ROLE_ID,
            GUILD_ID,
            CHANNEL_ID,
            "Sunday Watch Party",
        )

        message, ephemeral = perform_database_add(
            self.suggestion_service,
            self._wash_crew_member(),
            WASH_CREW_ROLE_ID,
            GUILD_ID,
            CHANNEL_ID,
            "Kung Fu Movies",
        )

        self.assertTrue(ephemeral)
        self.assertIn("already has a suggestion database", message)
        self.assertEqual(len(self.suggestion_service.list_databases()), 1)

    def test_database_add_rejects_a_non_wash_crew_member(self) -> None:
        message, ephemeral = perform_database_add(
            self.suggestion_service,
            self._regular_member(),
            WASH_CREW_ROLE_ID,
            GUILD_ID,
            CHANNEL_ID,
            "Sunday Watch Party",
        )

        self.assertTrue(ephemeral)
        self.assertIn("WASH Crew", message)
        self.assertEqual(self.suggestion_service.list_databases(), [])

    def test_database_add_fails_closed_when_role_is_unconfigured(self) -> None:
        message, ephemeral = perform_database_add(
            self.suggestion_service,
            self._wash_crew_member(),
            None,
            GUILD_ID,
            CHANNEL_ID,
            "Sunday Watch Party",
        )

        self.assertTrue(ephemeral)
        self.assertIn("not been configured", message)
        self.assertEqual(self.suggestion_service.list_databases(), [])

    # --- /database_list --------------------------------------------------------

    def test_database_list_with_no_databases(self) -> None:
        message, ephemeral = perform_database_list(
            self.suggestion_service, self._wash_crew_member(), WASH_CREW_ROLE_ID, GUILD_ID
        )

        self.assertTrue(ephemeral)
        self.assertIn("No suggestion databases", message)

    def test_database_list_with_one_database(self) -> None:
        self.suggestion_service.create_database("Sunday Watch Party", guild_id=GUILD_ID, channel_id=CHANNEL_ID)

        message, ephemeral = perform_database_list(
            self.suggestion_service, self._wash_crew_member(), WASH_CREW_ROLE_ID, GUILD_ID
        )

        self.assertTrue(ephemeral)
        self.assertIn("Sunday Watch Party", message)
        self.assertIn("[1]", message)
        self.assertIn(f"<#{CHANNEL_ID}>", message)

    def test_database_list_with_multiple_databases(self) -> None:
        self.suggestion_service.create_database("Sunday Watch Party", guild_id=GUILD_ID, channel_id=CHANNEL_ID)
        self.suggestion_service.create_database(
            "Kung Fu Movies", guild_id=GUILD_ID, channel_id=OTHER_CHANNEL_ID
        )

        message, ephemeral = perform_database_list(
            self.suggestion_service, self._wash_crew_member(), WASH_CREW_ROLE_ID, GUILD_ID
        )

        self.assertTrue(ephemeral)
        self.assertIn("Sunday Watch Party", message)
        self.assertIn("Kung Fu Movies", message)

    def test_database_list_shows_active_status(self) -> None:
        self.suggestion_service.create_database("Sunday Watch Party", guild_id=GUILD_ID, channel_id=CHANNEL_ID)

        message, _ = perform_database_list(
            self.suggestion_service, self._wash_crew_member(), WASH_CREW_ROLE_ID, GUILD_ID
        )

        self.assertIn("Active", message)

    def test_database_list_shows_inactive_status(self) -> None:
        created = self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        )
        self.suggestion_service.deactivate_database(created.database.database_id, guild_id=GUILD_ID)

        message, _ = perform_database_list(
            self.suggestion_service, self._wash_crew_member(), WASH_CREW_ROLE_ID, GUILD_ID
        )

        self.assertIn("Inactive", message)

    def test_database_list_shows_suggestion_counts(self) -> None:
        created = self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        )
        self.suggestion_service.suggest("The Matrix", database_id=created.database.database_id)
        self.suggestion_service.suggest("Inception", database_id=created.database.database_id)

        message, _ = perform_database_list(
            self.suggestion_service, self._wash_crew_member(), WASH_CREW_ROLE_ID, GUILD_ID
        )

        self.assertIn("2 suggestions", message)

    def test_database_list_only_shows_databases_for_the_current_guild(self) -> None:
        self.suggestion_service.create_database("Sunday Watch Party", guild_id=GUILD_ID, channel_id=CHANNEL_ID)
        self.suggestion_service.create_database(
            "A Different Server's Database", guild_id=555, channel_id=CHANNEL_ID
        )

        message, _ = perform_database_list(
            self.suggestion_service, self._wash_crew_member(), WASH_CREW_ROLE_ID, GUILD_ID
        )

        self.assertIn("Sunday Watch Party", message)
        self.assertNotIn("A Different Server's Database", message)

    def test_database_list_rejects_a_non_wash_crew_member(self) -> None:
        message, ephemeral = perform_database_list(
            self.suggestion_service, self._regular_member(), WASH_CREW_ROLE_ID, GUILD_ID
        )

        self.assertTrue(ephemeral)
        self.assertIn("WASH Crew", message)

    # --- /database_remove --------------------------------------------------------

    def test_database_remove_successful_deactivate(self) -> None:
        created = self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        )

        message, ephemeral = perform_database_remove(
            self.suggestion_service,
            self._wash_crew_member(),
            WASH_CREW_ROLE_ID,
            GUILD_ID,
            created.database.database_id,
        )

        self.assertTrue(ephemeral)
        self.assertIn("deactivated", message)
        self.assertFalse(self.suggestion_service.get_database(created.database.database_id).active)

    def test_database_remove_rejects_an_unknown_database(self) -> None:
        message, ephemeral = perform_database_remove(
            self.suggestion_service, self._wash_crew_member(), WASH_CREW_ROLE_ID, GUILD_ID, 999
        )

        self.assertTrue(ephemeral)
        self.assertIn("doesn't exist", message)

    def test_database_remove_rejects_an_already_inactive_database(self) -> None:
        created = self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        )
        perform_database_remove(
            self.suggestion_service, self._wash_crew_member(), WASH_CREW_ROLE_ID, GUILD_ID, created.database.database_id
        )

        message, ephemeral = perform_database_remove(
            self.suggestion_service, self._wash_crew_member(), WASH_CREW_ROLE_ID, GUILD_ID, created.database.database_id
        )

        self.assertTrue(ephemeral)
        self.assertIn("already inactive", message)

    def test_database_remove_preserves_suggestions(self) -> None:
        created = self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        )
        self.suggestion_service.suggest("The Matrix", database_id=created.database.database_id)

        perform_database_remove(
            self.suggestion_service, self._wash_crew_member(), WASH_CREW_ROLE_ID, GUILD_ID, created.database.database_id
        )

        titles = [item.title for item in self.suggestion_service.get_suggestions()]
        self.assertEqual(titles, ["The Matrix"])

    def test_database_remove_does_not_permanently_delete_the_database(self) -> None:
        created = self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        )

        perform_database_remove(
            self.suggestion_service, self._wash_crew_member(), WASH_CREW_ROLE_ID, GUILD_ID, created.database.database_id
        )

        self.assertTrue(self.suggestion_service.database_exists(created.database.database_id))

    def test_database_remove_rejects_a_non_wash_crew_member(self) -> None:
        created = self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        )

        message, ephemeral = perform_database_remove(
            self.suggestion_service, self._regular_member(), WASH_CREW_ROLE_ID, GUILD_ID, created.database.database_id
        )

        self.assertTrue(ephemeral)
        self.assertIn("WASH Crew", message)
        self.assertTrue(self.suggestion_service.get_database(created.database.database_id).active)

    def test_database_remove_rejects_database_from_another_guild(self) -> None:
        created = self.suggestion_service.create_database(
            "Other Guild", guild_id=GUILD_ID + 1, channel_id=CHANNEL_ID + 1
        )

        message, ephemeral = perform_database_remove(
            self.suggestion_service,
            self._wash_crew_member(),
            WASH_CREW_ROLE_ID,
            GUILD_ID,
            created.database.database_id,
        )

        self.assertTrue(ephemeral)
        self.assertIn("doesn't exist", message)
        self.assertTrue(self.suggestion_service.get_database(created.database.database_id).active)

    def test_database_add_rejects_use_outside_a_guild(self) -> None:
        message, ephemeral = perform_database_add(
            self.suggestion_service,
            self._wash_crew_member(),
            WASH_CREW_ROLE_ID,
            None,
            CHANNEL_ID,
            "Sunday Watch Party",
        )

        self.assertTrue(ephemeral)
        self.assertIn("Discord server", message)
        self.assertEqual(self.suggestion_service.list_databases(), [])

    def test_database_add_rejects_missing_channel_context(self) -> None:
        message, ephemeral = perform_database_add(
            self.suggestion_service,
            self._wash_crew_member(),
            WASH_CREW_ROLE_ID,
            GUILD_ID,
            None,
            "Sunday Watch Party",
        )

        self.assertTrue(ephemeral)
        self.assertIn("channel or thread", message)
        self.assertEqual(self.suggestion_service.list_databases(), [])

    def test_database_list_rejects_use_outside_a_guild(self) -> None:
        message, ephemeral = perform_database_list(
            self.suggestion_service, self._wash_crew_member(), WASH_CREW_ROLE_ID, None
        )

        self.assertTrue(ephemeral)
        self.assertIn("Discord server", message)

    def test_database_remove_rejects_use_outside_a_guild(self) -> None:
        created = self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        )

        message, ephemeral = perform_database_remove(
            self.suggestion_service,
            self._wash_crew_member(),
            WASH_CREW_ROLE_ID,
            None,
            created.database.database_id,
        )

        self.assertTrue(ephemeral)
        self.assertIn("Discord server", message)
        self.assertTrue(self.suggestion_service.get_database(created.database.database_id).active)


if __name__ == "__main__":
    unittest.main()
