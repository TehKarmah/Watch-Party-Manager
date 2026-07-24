import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import (
    build_database_admin_options,
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
        self.assertIn("Database ID: 1", message)
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

        self.assertIn("Watch items: 2 watch items", message)


    def test_database_list_uses_readable_multiline_format(self) -> None:
        created = self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        )
        self.suggestion_service.suggest("The Matrix", database_id=created.database.database_id)

        message, _ = perform_database_list(
            self.suggestion_service, self._wash_crew_member(), WASH_CREW_ROLE_ID, GUILD_ID
        )

        self.assertTrue(message.startswith("Suggestion Databases\n\n"))
        self.assertIn("Database ID: 1\n", message)
        self.assertIn("Name: Sunday Watch Party\n", message)
        self.assertIn("Status: Active\n", message)
        self.assertIn(f"Channel: <#{CHANNEL_ID}>\n", message)
        self.assertIn("Watch items: 1 watch item", message)

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


    def test_database_list_sorts_active_databases_alphabetically_then_inactive(self) -> None:
        self.suggestion_service.create_database(
            "Zulu", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        )
        inactive = self.suggestion_service.create_database(
            "Alpha Inactive", guild_id=GUILD_ID, channel_id=OTHER_CHANNEL_ID
        ).database
        self.suggestion_service.create_database(
            "Alpha Active", guild_id=GUILD_ID, channel_id=OTHER_CHANNEL_ID + 1
        )
        self.suggestion_service.deactivate_database(inactive.database_id, guild_id=GUILD_ID)

        message, _ = perform_database_list(
            self.suggestion_service, self._wash_crew_member(), WASH_CREW_ROLE_ID, GUILD_ID
        )

        self.assertLess(message.index("Alpha Active"), message.index("Zulu"))
        self.assertLess(message.index("Zulu"), message.index("Alpha Inactive"))

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


    # --- Database selector options (Release Polish: Discord-native UX) --------------

    def test_admin_options_show_the_database_name_as_the_label(self) -> None:
        self.suggestion_service.create_database("Sunday Watch Party", guild_id=GUILD_ID, channel_id=CHANNEL_ID)
        databases = self.suggestion_service.list_databases(GUILD_ID)

        options = build_database_admin_options(self.suggestion_service, databases)

        self.assertEqual(1, len(options))
        database_id, label, description = options[0]
        self.assertEqual("Sunday Watch Party", label)

    def test_admin_options_clearly_indicate_which_database_is_active(self) -> None:
        active = self.suggestion_service.create_database(
            "Active Party", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        inactive = self.suggestion_service.create_database(
            "Inactive Party", guild_id=GUILD_ID, channel_id=OTHER_CHANNEL_ID
        ).database
        self.suggestion_service.deactivate_database(inactive.database_id, guild_id=GUILD_ID)

        options = build_database_admin_options(
            self.suggestion_service, self.suggestion_service.list_databases(GUILD_ID)
        )

        by_id = {database_id: description for database_id, _, description in options}
        self.assertIn("Active", by_id[active.database_id])
        self.assertIn("Inactive", by_id[inactive.database_id])
        self.assertNotIn("Active", by_id[inactive.database_id].replace("Inactive", ""))

    def test_admin_options_include_watch_item_counts(self) -> None:
        database = self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        self.suggestion_service.suggest("The Matrix", database_id=database.database_id)
        self.suggestion_service.suggest("Inception", database_id=database.database_id)

        options = build_database_admin_options(
            self.suggestion_service, self.suggestion_service.list_databases(GUILD_ID)
        )

        self.assertIn("2 watch items", options[0][2])

    def test_admin_options_use_the_same_ordering_as_database_list(self) -> None:
        self.suggestion_service.create_database("Zulu", guild_id=GUILD_ID, channel_id=CHANNEL_ID)
        inactive = self.suggestion_service.create_database(
            "Alpha Inactive", guild_id=GUILD_ID, channel_id=OTHER_CHANNEL_ID
        ).database
        self.suggestion_service.create_database(
            "Alpha Active", guild_id=GUILD_ID, channel_id=OTHER_CHANNEL_ID + 1
        )
        self.suggestion_service.deactivate_database(inactive.database_id, guild_id=GUILD_ID)

        options = build_database_admin_options(
            self.suggestion_service, self.suggestion_service.list_databases(GUILD_ID)
        )

        labels = [label for _, label, _ in options]
        self.assertEqual(["Alpha Active", "Zulu", "Alpha Inactive"], labels)

    def test_admin_options_cap_at_twenty_five_and_include_no_bare_ids_in_the_label(self) -> None:
        for index in range(30):
            self.suggestion_service.create_database(f"Database {index}", guild_id=GUILD_ID, channel_id=1000 + index)

        databases = self.suggestion_service.list_databases(GUILD_ID)
        options = build_database_admin_options(self.suggestion_service, databases)

        self.assertEqual(30, len(databases))
        # build_database_admin_options itself doesn't truncate -- DatabaseAdminSelect
        # does, at Discord's own 25-option ceiling -- but every option must still be
        # a clean (id, label, description) triple with no id embedded in the label.
        self.assertEqual(30, len(options))
        for _, label, _ in options:
            self.assertFalse(label.startswith("["))


if __name__ == "__main__":
    unittest.main()
