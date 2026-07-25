import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import (
    build_rejection_confirmation_text,
    build_suggestion_message_link,
    build_suggestion_view,
    handle_suggestion_rejection_toggle,
    handle_undo_rejection,
    perform_add_suggestion,
    perform_add_suggestion_from_input,
    perform_reject_suggestion,
    perform_remove_rejection,
    perform_toggle_suggestion_rejection,
)
from watch_party_manager.domain.watch_item import MetadataProvider, WatchItemStatus
from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.permission_service import PermissionService
from watch_party_manager.services.suggestion_input_service import SuggestionInputService
from watch_party_manager.services.imdb_metadata_service import ImdbMetadataService
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.suggestion_view import RejectionConfirmationView, SuggestionView

WASH_CREW_ROLE_ID = 999
WATCH_PARTY_MEMBER_ROLE_ID = 555

GUILD_ID = 100
CONFIGURED_CHANNEL_ID = 200
OTHER_CONFIGURED_CHANNEL_ID = 201
UNCONFIGURED_CHANNEL_ID = 999


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, role_ids=(), *, user_id: int = 1) -> None:
        self.roles = [FakeRole(role_id) for role_id in role_ids]
        self.id = user_id


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
        self.assertIn("Which collection would you like to use", message)
        self.assertEqual(self.suggestion_service.suggestion_count(), 0)

    def test_add_with_no_databases_configured_explains_wash_crew_must_configure_one(self) -> None:
        message, ephemeral, watch_item = perform_add_suggestion(
            self.suggestion_service, GUILD_ID, UNCONFIGURED_CHANNEL_ID, "The Matrix", None
        )

        self.assertTrue(ephemeral)
        self.assertIsNone(watch_item)
        self.assertIn("create a collection first", message)
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
        self.assertIn("create a collection first", message)


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
        def fetch_metadata(url: str) -> dict[str, str]:
            if "tt0133093" in url:
                return {
                    "Title": "The Matrix",
                    "Year": "1999",
                    "Response": "True",
                }
            return {
                "Title": "Star Wars: Episode IV - A New Hope",
                "Year": "1977",
                "Response": "True",
            }

        metadata_service = ImdbMetadataService(
            api_key="test-key",
            fetch_json=fetch_metadata,
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
        self.assertEqual(watch_item.title, "The Matrix (1999)")
        self.assertEqual(
            watch_item.metadata_ids[MetadataProvider.IMDB],
            "https://www.imdb.com/title/tt0133093/",
        )


class RejectionCommandTests(unittest.TestCase):
    """FR-022: /reject and /unreject."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.permission_service = PermissionService(
            watch_party_member_role_id=WATCH_PARTY_MEMBER_ROLE_ID,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
        )
        self.matrix = self.suggestion_service.suggest("The Matrix").watch_item

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _watch_party_member(self, user_id: int = 1) -> FakeMember:
        return FakeMember([WATCH_PARTY_MEMBER_ROLE_ID], user_id=user_id)

    def _non_member(self, user_id: int = 1) -> FakeMember:
        return FakeMember([], user_id=user_id)

    # --- /reject: permission enforcement -----------------------------------------

    def test_reject_requires_watch_party_member_role(self) -> None:
        message, ephemeral, _ = perform_reject_suggestion(
            self.suggestion_service,
            None,
            self.permission_service,
            self._non_member(),
            GUILD_ID,
            self.matrix.id,
        )

        self.assertTrue(ephemeral)
        self.assertIn("Watch Party member", message)
        journey = self.suggestion_service.get_suggestion(self.matrix.id).journey
        self.assertEqual(journey.rejected_by_discord_user_ids, ())

    def test_reject_allows_a_watch_party_member(self) -> None:
        message, ephemeral, _ = perform_reject_suggestion(
            self.suggestion_service,
            None,
            self.permission_service,
            self._watch_party_member(),
            GUILD_ID,
            self.matrix.id,
        )

        self.assertTrue(ephemeral)
        self.assertIn("recorded", message)

    def test_reject_allows_wash_crew_too(self) -> None:
        # WASH Crew inherits Watch Party member permissions (see PermissionService).
        crew_member = FakeMember([WASH_CREW_ROLE_ID], user_id=1)

        message, ephemeral, _ = perform_reject_suggestion(
            self.suggestion_service, None, self.permission_service, crew_member, GUILD_ID, self.matrix.id
        )

        self.assertTrue(ephemeral)
        self.assertIn("recorded", message)

    # --- /reject: happy path + graceful failures ---------------------------------

    def test_reject_records_the_rejection(self) -> None:
        perform_reject_suggestion(
            self.suggestion_service,
            None,
            self.permission_service,
            self._watch_party_member(1),
            GUILD_ID,
            self.matrix.id,
        )

        journey = self.suggestion_service.get_suggestion(self.matrix.id).journey
        self.assertEqual(journey.rejected_by_discord_user_ids, (1,))

    def test_reject_is_graceful_for_a_nonexistent_suggestion(self) -> None:
        message, ephemeral, _ = perform_reject_suggestion(
            self.suggestion_service,
            None,
            self.permission_service,
            self._watch_party_member(),
            GUILD_ID,
            999,
        )

        self.assertTrue(ephemeral)
        self.assertIn("doesn't exist", message)

    def test_reject_uses_the_configured_threshold_when_available(self) -> None:
        created = self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=GUILD_ID, channel_id=CONFIGURED_CHANNEL_ID
        )
        inception = self.suggestion_service.suggest(
            "Inception", database_id=created.database.database_id
        ).watch_item
        self.assertIsNotNone(inception)

        class FakeConfig:
            class suggestion_rules:
                rejection_threshold = 1

        class FakeConfigRepository:
            def get(self, guild_id, database_id):
                return FakeConfig()

        message, ephemeral, _ = perform_reject_suggestion(
            self.suggestion_service,
            FakeConfigRepository(),
            self.permission_service,
            self._watch_party_member(1),
            GUILD_ID,
            inception.id,
        )

        self.assertIn("archived", message)
        self.assertEqual(
            self.suggestion_service.get_suggestion(inception.id).status.value, "archived"
        )

    def test_reject_falls_back_to_default_threshold_when_unconfigured(self) -> None:
        message, ephemeral, _ = perform_reject_suggestion(
            self.suggestion_service,
            None,
            self.permission_service,
            self._watch_party_member(1),
            GUILD_ID,
            self.matrix.id,
        )

        self.assertNotIn("archived", message)

    # --- /unreject: permission enforcement ---------------------------------------

    def test_unreject_requires_watch_party_member_role(self) -> None:
        perform_reject_suggestion(
            self.suggestion_service,
            None,
            self.permission_service,
            self._watch_party_member(1),
            GUILD_ID,
            self.matrix.id,
        )

        message, ephemeral = perform_remove_rejection(
            self.suggestion_service, self.permission_service, self._non_member(1), self.matrix.id
        )

        self.assertTrue(ephemeral)
        self.assertIn("Watch Party member", message)
        journey = self.suggestion_service.get_suggestion(self.matrix.id).journey
        self.assertEqual(journey.rejected_by_discord_user_ids, (1,))

    # --- /unreject: happy path + graceful failures -------------------------------

    def test_unreject_removes_the_rejection(self) -> None:
        perform_reject_suggestion(
            self.suggestion_service,
            None,
            self.permission_service,
            self._watch_party_member(1),
            GUILD_ID,
            self.matrix.id,
        )

        message, ephemeral = perform_remove_rejection(
            self.suggestion_service, self.permission_service, self._watch_party_member(1), self.matrix.id
        )

        self.assertTrue(ephemeral)
        self.assertIn("removed", message)
        journey = self.suggestion_service.get_suggestion(self.matrix.id).journey
        self.assertEqual(journey.rejected_by_discord_user_ids, ())

    def test_unreject_is_graceful_when_the_member_never_rejected_it(self) -> None:
        message, ephemeral = perform_remove_rejection(
            self.suggestion_service, self.permission_service, self._watch_party_member(1), self.matrix.id
        )

        self.assertTrue(ephemeral)
        self.assertIn("haven't rejected", message)

    def test_unreject_is_graceful_for_a_nonexistent_suggestion(self) -> None:
        message, ephemeral = perform_remove_rejection(
            self.suggestion_service, self.permission_service, self._watch_party_member(), 999
        )

        self.assertTrue(ephemeral)
        self.assertIn("doesn't exist", message)


class FakeResponse:
    def __init__(self) -> None:
        self.sent_message = None
        self.sent_ephemeral = None

    async def send_message(self, content, ephemeral=False, view=None) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral


class FakeSuggestionMessage:
    def __init__(self) -> None:
        self.edited_view = "not-edited"

    async def edit(self, view="not-edited") -> None:
        self.edited_view = view


class FakeSuggestionInteraction:
    def __init__(self, user, guild_id=GUILD_ID) -> None:
        self.user = user
        self.guild_id = guild_id
        self.response = FakeResponse()
        self.message = FakeSuggestionMessage()


class SuggestionRejectionToggleTests(unittest.TestCase):
    """FR-024: perform_toggle_suggestion_rejection -- the "I WILL NOT WATCH" button's core logic."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.permission_service = PermissionService(
            watch_party_member_role_id=WATCH_PARTY_MEMBER_ROLE_ID,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
        )
        self.matrix = self.suggestion_service.suggest("The Matrix").watch_item

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _watch_party_member(self, user_id: int = 1) -> FakeMember:
        return FakeMember([WATCH_PARTY_MEMBER_ROLE_ID], user_id=user_id)

    def _non_member(self, user_id: int = 1) -> FakeMember:
        return FakeMember([], user_id=user_id)

    # --- Permission enforcement ---------------------------------------------------

    def test_requires_watch_party_member_role(self) -> None:
        message, ephemeral, watch_item = perform_toggle_suggestion_rejection(
            self.suggestion_service, None, self.permission_service, self._non_member(), GUILD_ID, self.matrix.id
        )

        self.assertTrue(ephemeral)
        self.assertIn("Watch Party member", message)
        self.assertIsNone(watch_item)
        self.assertEqual(
            self.suggestion_service.get_suggestion(self.matrix.id).journey.rejected_by_discord_user_ids, ()
        )

    # --- Toggle: reject then remove ------------------------------------------------

    def test_first_click_records_a_rejection(self) -> None:
        message, ephemeral, watch_item = perform_toggle_suggestion_rejection(
            self.suggestion_service, None, self.permission_service, self._watch_party_member(1), GUILD_ID, self.matrix.id
        )

        self.assertTrue(ephemeral)
        self.assertIn("recorded", message)
        self.assertEqual(watch_item.journey.rejected_by_discord_user_ids, (1,))

    def test_second_click_by_the_same_member_removes_the_rejection(self) -> None:
        perform_toggle_suggestion_rejection(
            self.suggestion_service, None, self.permission_service, self._watch_party_member(1), GUILD_ID, self.matrix.id
        )

        message, ephemeral, watch_item = perform_toggle_suggestion_rejection(
            self.suggestion_service, None, self.permission_service, self._watch_party_member(1), GUILD_ID, self.matrix.id
        )

        self.assertTrue(ephemeral)
        self.assertIn("removed", message)
        self.assertEqual(watch_item.journey.rejected_by_discord_user_ids, ())

    def test_toggling_twice_never_duplicates_a_members_rejection(self) -> None:
        # The button intelligently toggles rather than allowing a second
        # identical rejection -- this is how "duplicate rejection
        # prevention" is enforced at the button layer.
        perform_toggle_suggestion_rejection(
            self.suggestion_service, None, self.permission_service, self._watch_party_member(1), GUILD_ID, self.matrix.id
        )
        perform_toggle_suggestion_rejection(
            self.suggestion_service, None, self.permission_service, self._watch_party_member(1), GUILD_ID, self.matrix.id
        )
        _, _, watch_item = perform_toggle_suggestion_rejection(
            self.suggestion_service, None, self.permission_service, self._watch_party_member(1), GUILD_ID, self.matrix.id
        )

        # Odd number of toggles (3) => currently rejected, exactly once.
        self.assertEqual(watch_item.journey.rejected_by_discord_user_ids, (1,))

    def test_different_members_toggle_independently(self) -> None:
        perform_toggle_suggestion_rejection(
            self.suggestion_service, None, self.permission_service, self._watch_party_member(1), GUILD_ID, self.matrix.id
        )
        _, _, watch_item = perform_toggle_suggestion_rejection(
            self.suggestion_service, None, self.permission_service, self._watch_party_member(2), GUILD_ID, self.matrix.id
        )

        self.assertEqual(set(watch_item.journey.rejected_by_discord_user_ids), {1, 2})

    # --- Threshold + automatic archive ----------------------------------------------

    def test_threshold_reached_archives_the_suggestion(self) -> None:
        created = self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=GUILD_ID, channel_id=CONFIGURED_CHANNEL_ID
        )
        inception = self.suggestion_service.suggest(
            "Inception", database_id=created.database.database_id
        ).watch_item

        class FakeConfig:
            class suggestion_rules:
                rejection_threshold = 1

        class FakeConfigRepository:
            def get(self, guild_id, database_id):
                return FakeConfig()

        message, ephemeral, watch_item = perform_toggle_suggestion_rejection(
            self.suggestion_service,
            FakeConfigRepository(),
            self.permission_service,
            self._watch_party_member(1),
            GUILD_ID,
            inception.id,
        )

        self.assertIn("archived", message)
        self.assertEqual(watch_item.status, WatchItemStatus.ARCHIVED)

    def test_archived_suggestion_can_no_longer_be_toggled(self) -> None:
        self.matrix.status = WatchItemStatus.ARCHIVED

        message, ephemeral, watch_item = perform_toggle_suggestion_rejection(
            self.suggestion_service, None, self.permission_service, self._watch_party_member(1), GUILD_ID, self.matrix.id
        )

        self.assertTrue(ephemeral)
        self.assertIn("archived", message.lower())
        self.assertEqual(watch_item.journey.rejected_by_discord_user_ids, ())

    def test_is_graceful_for_a_nonexistent_suggestion(self) -> None:
        message, ephemeral, watch_item = perform_toggle_suggestion_rejection(
            self.suggestion_service, None, self.permission_service, self._watch_party_member(1), GUILD_ID, 999
        )

        self.assertTrue(ephemeral)
        self.assertIn("doesn't exist", message)
        self.assertIsNone(watch_item)


class HandleSuggestionRejectionToggleTests(unittest.IsolatedAsyncioTestCase):
    """FR-024: handle_suggestion_rejection_toggle -- ephemeral confirmation + message refresh."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.permission_service = PermissionService(
            watch_party_member_role_id=WATCH_PARTY_MEMBER_ROLE_ID,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
        )
        self.matrix = self.suggestion_service.suggest("The Matrix").watch_item

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _watch_party_member(self, user_id: int = 1) -> FakeMember:
        return FakeMember([WATCH_PARTY_MEMBER_ROLE_ID], user_id=user_id)

    def _non_member(self, user_id: int = 1) -> FakeMember:
        return FakeMember([], user_id=user_id)

    async def test_sends_an_ephemeral_confirmation(self) -> None:
        interaction = FakeSuggestionInteraction(self._watch_party_member(1))

        await handle_suggestion_rejection_toggle(
            interaction, self.suggestion_service, None, self.matrix.id, permission_service=self.permission_service
        )

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIn("recorded", interaction.response.sent_message)

    async def test_refreshes_the_original_message_button_after_a_rejection(self) -> None:
        interaction = FakeSuggestionInteraction(self._watch_party_member(1))

        await handle_suggestion_rejection_toggle(
            interaction, self.suggestion_service, None, self.matrix.id, permission_service=self.permission_service
        )

        self.assertIsInstance(interaction.message.edited_view, SuggestionView)
        self.assertEqual(interaction.message.edited_view.children[0].label, "I WILL NOT WATCH: 1 / 2")

    async def test_refreshes_the_button_back_to_zero_after_removing_a_rejection(self) -> None:
        first_interaction = FakeSuggestionInteraction(self._watch_party_member(1))
        await handle_suggestion_rejection_toggle(
            first_interaction, self.suggestion_service, None, self.matrix.id, permission_service=self.permission_service
        )

        second_interaction = FakeSuggestionInteraction(self._watch_party_member(1))
        await handle_suggestion_rejection_toggle(
            second_interaction, self.suggestion_service, None, self.matrix.id, permission_service=self.permission_service
        )

        self.assertEqual(second_interaction.message.edited_view.children[0].label, "I WILL NOT WATCH: 0 / 2")

    async def test_button_is_disabled_once_the_threshold_is_reached(self) -> None:
        created = self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=GUILD_ID, channel_id=CONFIGURED_CHANNEL_ID
        )
        inception = self.suggestion_service.suggest(
            "Inception", database_id=created.database.database_id
        ).watch_item

        class FakeConfig:
            class suggestion_rules:
                rejection_threshold = 1

        class FakeConfigRepository:
            def get(self, guild_id, database_id):
                return FakeConfig()

        interaction = FakeSuggestionInteraction(self._watch_party_member(1))

        await handle_suggestion_rejection_toggle(
            interaction, self.suggestion_service, FakeConfigRepository(), inception.id, permission_service=self.permission_service
        )

        view = interaction.message.edited_view
        self.assertIsInstance(view, SuggestionView)
        self.assertTrue(view.children[0].disabled)
        self.assertIn("Archived", view.children[0].label)

    async def test_no_message_refresh_when_permission_is_denied(self) -> None:
        interaction = FakeSuggestionInteraction(self._non_member(1))

        await handle_suggestion_rejection_toggle(
            interaction, self.suggestion_service, None, self.matrix.id, permission_service=self.permission_service
        )

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIn("Watch Party member", interaction.response.sent_message)
        self.assertEqual(interaction.message.edited_view, "not-edited")

    async def test_reports_not_configured_when_no_permission_service_is_given(self) -> None:
        interaction = FakeSuggestionInteraction(self._watch_party_member(1))

        await handle_suggestion_rejection_toggle(
            interaction, self.suggestion_service, None, self.matrix.id, permission_service=None
        )

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIn("not been configured", interaction.response.sent_message)
        self.assertEqual(interaction.message.edited_view, "not-edited")
        self.assertEqual(
            self.suggestion_service.get_suggestion(self.matrix.id).journey.rejected_by_discord_user_ids, ()
        )

    async def test_never_sends_a_second_public_message(self) -> None:
        # handle_suggestion_rejection_toggle must only ever send exactly one
        # ephemeral response plus (optionally) one edit to the existing
        # message -- never an additional public message.
        interaction = FakeSuggestionInteraction(self._watch_party_member(1))

        await handle_suggestion_rejection_toggle(
            interaction, self.suggestion_service, None, self.matrix.id, permission_service=self.permission_service
        )

        self.assertTrue(interaction.response.sent_ephemeral)


class BuildSuggestionViewTests(unittest.TestCase):
    """FR-024: build_suggestion_view resolves the configured threshold for the button label."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.matrix = self.suggestion_service.suggest("The Matrix").watch_item

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_uses_the_default_threshold_when_unconfigured(self) -> None:
        view = build_suggestion_view(self.suggestion_service, None, self.matrix, GUILD_ID)

        self.assertEqual(view.children[0].label, "I WILL NOT WATCH: 0 / 2")

    def test_uses_the_configured_threshold_when_available(self) -> None:
        created = self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=GUILD_ID, channel_id=CONFIGURED_CHANNEL_ID
        )
        inception = self.suggestion_service.suggest(
            "Inception", database_id=created.database.database_id
        ).watch_item

        class FakeConfig:
            class suggestion_rules:
                rejection_threshold = 5

        class FakeConfigRepository:
            def get(self, guild_id, database_id):
                return FakeConfig()

        view = build_suggestion_view(self.suggestion_service, FakeConfigRepository(), inception, GUILD_ID)

        self.assertEqual(view.children[0].label, "I WILL NOT WATCH: 0 / 5")


class RejectionConfirmationLinkTests(unittest.TestCase):
    """Requirement 2: the rejection confirmation links back to the
    original public suggestion post, when one is known.
    """

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_no_link_when_never_publicly_confirmed(self) -> None:
        item = self.suggestion_service.suggest("Alien").watch_item

        self.assertIsNone(build_suggestion_message_link(item))
        self.assertEqual(build_rejection_confirmation_text("Recorded.", item), "Recorded.")

    def test_unchanged_when_no_watch_item_is_known(self) -> None:
        self.assertEqual(build_rejection_confirmation_text("Recorded.", None), "Recorded.")

    def test_includes_a_message_link_when_publicly_confirmed(self) -> None:
        database = self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=GUILD_ID, channel_id=CONFIGURED_CHANNEL_ID
        ).database
        item = self.suggestion_service.suggest(
            "Alien", database_id=database.database_id, guild_id=GUILD_ID, channel_id=CONFIGURED_CHANNEL_ID
        ).watch_item
        self.suggestion_service.set_confirmation_post_reference(item.id, GUILD_ID, CONFIGURED_CHANNEL_ID, 555)
        item = self.suggestion_service.get_suggestion(item.id)

        link = build_suggestion_message_link(item)
        self.assertEqual(link, f"https://discord.com/channels/{GUILD_ID}/{CONFIGURED_CHANNEL_ID}/555")
        text = build_rejection_confirmation_text("Recorded.", item)
        self.assertIn(f"[View Suggestion]({link})", text)


class RejectSuggestionReturnsWatchItemTests(unittest.TestCase):
    """perform_reject_suggestion now also returns the watch_item, used to
    link back to its post and offer the Undo Rejection control.
    """

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.permission_service = PermissionService(
            watch_party_member_role_id=WATCH_PARTY_MEMBER_ROLE_ID,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
        )
        self.matrix = self.suggestion_service.suggest("The Matrix").watch_item

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _watch_party_member(self, user_id: int = 1) -> FakeMember:
        return FakeMember([WATCH_PARTY_MEMBER_ROLE_ID], user_id=user_id)

    def test_returns_the_watch_item_on_success(self) -> None:
        message, ephemeral, watch_item = perform_reject_suggestion(
            self.suggestion_service, None, self.permission_service, self._watch_party_member(1), GUILD_ID, self.matrix.id
        )

        self.assertIsNotNone(watch_item)
        self.assertEqual(watch_item.id, self.matrix.id)

    def test_returns_none_when_permission_is_denied(self) -> None:
        _, _, watch_item = perform_reject_suggestion(
            self.suggestion_service, None, self.permission_service, FakeMember([], user_id=1), GUILD_ID, self.matrix.id
        )

        self.assertIsNone(watch_item)

    def test_returns_none_for_a_nonexistent_suggestion(self) -> None:
        _, _, watch_item = perform_reject_suggestion(
            self.suggestion_service, None, self.permission_service, self._watch_party_member(1), GUILD_ID, 999
        )

        self.assertIsNone(watch_item)

    def test_returns_none_when_already_rejected_by_this_member(self) -> None:
        perform_reject_suggestion(
            self.suggestion_service, None, self.permission_service, self._watch_party_member(1), GUILD_ID, self.matrix.id
        )

        _, _, watch_item = perform_reject_suggestion(
            self.suggestion_service, None, self.permission_service, self._watch_party_member(1), GUILD_ID, self.matrix.id
        )

        self.assertIsNone(watch_item)


class FakeUndoMessage:
    def __init__(self, message_id: int) -> None:
        self.id = message_id
        self.edited_view = "not-edited"

    async def edit(self, *, view=None) -> None:
        self.edited_view = view


class FakeUndoChannel:
    def __init__(self, message: FakeUndoMessage, *, fail_fetch: bool = False) -> None:
        self._message = message
        self._fail_fetch = fail_fetch

    async def fetch_message(self, message_id):
        if self._fail_fetch:
            raise RuntimeError("simulated fetch failure")
        return self._message


class FakeUndoBot:
    def __init__(self, channel: FakeUndoChannel) -> None:
        self._channel = channel

    def get_channel(self, channel_id):
        return self._channel

    async def fetch_channel(self, channel_id):
        return self._channel


class FakeUndoResponse:
    def __init__(self) -> None:
        self.edited_content = None
        self.edited_view = "not-edited"

    async def edit_message(self, content=None, view=None) -> None:
        self.edited_content = content
        self.edited_view = view

    async def send_message(self, content, ephemeral=False) -> None:
        self.edited_content = content


class FakeUndoInteraction:
    def __init__(self, user) -> None:
        self.user = user
        self.response = FakeUndoResponse()


class HandleUndoRejectionTests(unittest.IsolatedAsyncioTestCase):
    """Requirement 2: the rejection confirmation's "Undo Rejection" button."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.permission_service = PermissionService(
            watch_party_member_role_id=WATCH_PARTY_MEMBER_ROLE_ID,
            wash_crew_role_id=WASH_CREW_ROLE_ID,
        )
        self.database = self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=GUILD_ID, channel_id=CONFIGURED_CHANNEL_ID
        ).database
        self.matrix = self.suggestion_service.suggest(
            "The Matrix", database_id=self.database.database_id, guild_id=GUILD_ID, channel_id=CONFIGURED_CHANNEL_ID
        ).watch_item
        self.suggestion_service.set_confirmation_post_reference(self.matrix.id, GUILD_ID, CONFIGURED_CHANNEL_ID, 555)
        self.suggestion_service.reject_suggestion(self.matrix.id, 1, rejection_threshold=2)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _member(self, user_id: int = 1) -> FakeMember:
        return FakeMember([WATCH_PARTY_MEMBER_ROLE_ID], user_id=user_id)

    async def test_removes_the_rejection_and_confirms(self) -> None:
        bot = FakeUndoBot(FakeUndoChannel(FakeUndoMessage(message_id=555)))
        interaction = FakeUndoInteraction(self._member(1))

        await handle_undo_rejection(
            interaction, self.suggestion_service, None, self.permission_service, bot, self.matrix.id
        )

        self.assertIn("removed", interaction.response.edited_content)
        self.assertIsNone(interaction.response.edited_view)
        journey = self.suggestion_service.get_suggestion(self.matrix.id).journey
        self.assertEqual(journey.rejected_by_discord_user_ids, ())

    async def test_refreshes_the_suggestion_posts_rejection_count(self) -> None:
        message = FakeUndoMessage(message_id=555)
        bot = FakeUndoBot(FakeUndoChannel(message))
        interaction = FakeUndoInteraction(self._member(1))

        await handle_undo_rejection(
            interaction, self.suggestion_service, None, self.permission_service, bot, self.matrix.id
        )

        self.assertIsInstance(message.edited_view, SuggestionView)
        self.assertEqual(message.edited_view.children[0].label, "I WILL NOT WATCH: 0 / 2")

    async def test_gracefully_handles_a_deleted_suggestion_post(self) -> None:
        bot = FakeUndoBot(FakeUndoChannel(FakeUndoMessage(message_id=555), fail_fetch=True))
        interaction = FakeUndoInteraction(self._member(1))

        # Must not raise -- the rejection itself is still removed even
        # though the public post could not be refreshed.
        await handle_undo_rejection(
            interaction, self.suggestion_service, None, self.permission_service, bot, self.matrix.id
        )

        self.assertIn("removed", interaction.response.edited_content)
        journey = self.suggestion_service.get_suggestion(self.matrix.id).journey
        self.assertEqual(journey.rejected_by_discord_user_ids, ())

    async def test_gracefully_handles_an_already_reverted_rejection(self) -> None:
        # Simulates the same member having already undone it another way
        # (e.g. toggling the public "I WILL NOT WATCH" button off first).
        self.suggestion_service.remove_rejection(self.matrix.id, 1)
        bot = FakeUndoBot(FakeUndoChannel(FakeUndoMessage(message_id=555)))
        interaction = FakeUndoInteraction(self._member(1))

        await handle_undo_rejection(
            interaction, self.suggestion_service, None, self.permission_service, bot, self.matrix.id
        )

        self.assertIn("haven't rejected", interaction.response.edited_content)

    async def test_reports_not_configured_when_no_permission_service_is_given(self) -> None:
        bot = FakeUndoBot(FakeUndoChannel(FakeUndoMessage(message_id=555)))
        interaction = FakeUndoInteraction(self._member(1))

        await handle_undo_rejection(interaction, self.suggestion_service, None, None, bot, self.matrix.id)

        self.assertIn("not been configured", interaction.response.edited_content)
        journey = self.suggestion_service.get_suggestion(self.matrix.id).journey
        self.assertEqual(journey.rejected_by_discord_user_ids, (1,))


class RejectionConfirmationViewRequesterEnforcementTests(unittest.IsolatedAsyncioTestCase):
    """Only the rejecting member may use their own Undo Rejection button."""

    class FakeCheckResponse:
        def __init__(self) -> None:
            self.sent_message = None

        async def send_message(self, content, ephemeral=False) -> None:
            self.sent_message = content

    class FakeCheckInteraction:
        def __init__(self, user_id: int) -> None:
            self.user = FakeMember([], user_id=user_id)
            self.response = RejectionConfirmationViewRequesterEnforcementTests.FakeCheckResponse()

    async def test_the_rejecting_member_passes(self) -> None:
        async def on_undo(interaction, suggestion_id) -> None:
            pass

        view = RejectionConfirmationView(1, on_undo, requester_id=42)
        allowed = await view.interaction_check(self.FakeCheckInteraction(42))

        self.assertTrue(allowed)

    async def test_another_member_is_blocked(self) -> None:
        async def on_undo(interaction, suggestion_id) -> None:
            pass

        view = RejectionConfirmationView(1, on_undo, requester_id=42)
        interaction = self.FakeCheckInteraction(7)
        allowed = await view.interaction_check(interaction)

        self.assertFalse(allowed)
        self.assertIn("Only the member who rejected", interaction.response.sent_message)


if __name__ == "__main__":
    unittest.main()

