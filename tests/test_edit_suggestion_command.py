"""Tests for FR-033A's Crew-only /edit_suggestion command."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from watch_party_manager.bot import handle_edit_suggestion
from watch_party_manager.domain.watch_item import MetadataProvider
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.suggestion_service import SuggestionService

GUILD_ID = 100
CHANNEL_ID = 200
WASH_CREW_ROLE_ID = 999


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, role_ids=(), *, user_id: int = 1) -> None:
        self.roles = [FakeRole(role_id) for role_id in role_ids]
        self.id = user_id


class FakeResponse:
    def __init__(self) -> None:
        self.sent_message = None
        self.sent_ephemeral = None
        self.sent_view = None

    async def send_message(self, content, ephemeral=False, view=None) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral
        self.sent_view = view


class FakeInteraction:
    def __init__(self, user=None) -> None:
        self.user = user if user is not None else FakeMember([WASH_CREW_ROLE_ID])
        self.response = FakeResponse()


class FakeBot:
    def __init__(self, suggestion_service, wash_crew_role_id=WASH_CREW_ROLE_ID) -> None:
        self.suggestion_service = suggestion_service
        self.wash_crew_role_id = wash_crew_role_id


class EditSuggestionTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.bot = FakeBot(self.suggestion_service)
        self.database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        self.item = self.suggestion_service.suggest(
            "Alien", database_id=self.database.database_id, guild_id=GUILD_ID
        ).watch_item


class EditPermissionTests(EditSuggestionTestCase):
    async def test_non_wash_crew_is_rejected(self) -> None:
        interaction = FakeInteraction(user=FakeMember([]))

        await handle_edit_suggestion(interaction, self.bot, f"#{self.item.id}", "New Title", None, None, None)

        self.assertIn("WASH Crew", interaction.response.sent_message)
        self.assertEqual("Alien", self.suggestion_service.get_suggestion(self.item.id).title)


class EditFieldTests(EditSuggestionTestCase):
    async def test_edits_the_title(self) -> None:
        interaction = FakeInteraction()

        await handle_edit_suggestion(interaction, self.bot, f"#{self.item.id}", "Aliens", None, None, None)

        self.assertEqual("Aliens", self.suggestion_service.get_suggestion(self.item.id).title)

    async def test_edits_the_release_year(self) -> None:
        interaction = FakeInteraction()

        await handle_edit_suggestion(interaction, self.bot, f"#{self.item.id}", None, 1979, None, None)

        self.assertEqual(1979, self.suggestion_service.get_suggestion(self.item.id).release_year)

    async def test_edits_the_imdb_url_and_normalizes_it(self) -> None:
        interaction = FakeInteraction()

        await handle_edit_suggestion(
            interaction, self.bot, f"#{self.item.id}", None, None, "imdb.com/title/tt0078748", None
        )

        stored = self.suggestion_service.get_suggestion(self.item.id)
        self.assertEqual("https://www.imdb.com/title/tt0078748/", stored.metadata_ids[MetadataProvider.IMDB])

    async def test_rejects_an_invalid_imdb_url(self) -> None:
        interaction = FakeInteraction()

        await handle_edit_suggestion(interaction, self.bot, f"#{self.item.id}", None, None, "not-a-link", None)

        self.assertIn("valid IMDb", interaction.response.sent_message)
        self.assertNotIn(MetadataProvider.IMDB, self.suggestion_service.get_suggestion(self.item.id).metadata_ids)

    async def test_moves_to_another_database(self) -> None:
        other_database = self.suggestion_service.create_database(
            "Other DB", guild_id=GUILD_ID, channel_id=555
        ).database
        interaction = FakeInteraction()

        await handle_edit_suggestion(
            interaction, self.bot, f"#{self.item.id}", None, None, None, other_database.database_id
        )

        self.assertEqual(other_database.database_id, self.suggestion_service.get_suggestion(self.item.id).database_id)

    async def test_rejects_moving_to_an_inactive_database(self) -> None:
        other_database = self.suggestion_service.create_database(
            "Other DB", guild_id=GUILD_ID, channel_id=555
        ).database
        self.suggestion_service.deactivate_database(other_database.database_id, guild_id=GUILD_ID)
        interaction = FakeInteraction()

        await handle_edit_suggestion(
            interaction, self.bot, f"#{self.item.id}", None, None, None, other_database.database_id
        )

        self.assertIn("not available", interaction.response.sent_message)
        self.assertEqual(self.database.database_id, self.suggestion_service.get_suggestion(self.item.id).database_id)

    async def test_rejects_an_unknown_destination_database(self) -> None:
        interaction = FakeInteraction()

        await handle_edit_suggestion(interaction, self.bot, f"#{self.item.id}", None, None, None, 999999)

        self.assertIn("not available", interaction.response.sent_message)

    async def test_no_fields_given_reports_no_changes(self) -> None:
        interaction = FakeInteraction()

        await handle_edit_suggestion(interaction, self.bot, f"#{self.item.id}", None, None, None, None)

        self.assertIn("No changes were made", interaction.response.sent_message)

    async def test_preserves_the_stable_id(self) -> None:
        interaction = FakeInteraction()

        await handle_edit_suggestion(interaction, self.bot, f"#{self.item.id}", "Aliens", None, None, None)

        self.assertEqual(self.item.id, self.suggestion_service.get_suggestion(self.item.id).id)

    async def test_preserves_history(self) -> None:
        self.suggestion_service.reject_suggestion(self.item.id, discord_user_id=1, rejection_threshold=99)
        interaction = FakeInteraction()

        await handle_edit_suggestion(interaction, self.bot, f"#{self.item.id}", "Aliens", None, None, None)

        stored = self.suggestion_service.get_suggestion(self.item.id)
        self.assertEqual((1,), stored.journey.rejected_by_discord_user_ids)

    async def test_response_shows_the_diff(self) -> None:
        interaction = FakeInteraction()

        await handle_edit_suggestion(interaction, self.bot, f"#{self.item.id}", "Aliens", None, None, None)

        self.assertIn("Alien", interaction.response.sent_message)
        self.assertIn("Aliens", interaction.response.sent_message)

    async def test_matches_by_exact_title(self) -> None:
        interaction = FakeInteraction()

        await handle_edit_suggestion(interaction, self.bot, "Alien", "Aliens", None, None, None)

        self.assertEqual("Aliens", self.suggestion_service.get_suggestion(self.item.id).title)

    async def test_no_match_returns_a_clear_response(self) -> None:
        interaction = FakeInteraction()

        await handle_edit_suggestion(interaction, self.bot, "Nonexistent", "New Title", None, None, None)

        self.assertIn("No suggestion matches", interaction.response.sent_message)

    async def test_ambiguous_match_requires_a_reference_number(self) -> None:
        other_database = self.suggestion_service.create_database(
            "Other DB", guild_id=GUILD_ID, channel_id=555
        ).database
        self.suggestion_service.suggest("Alien", database_id=other_database.database_id)
        interaction = FakeInteraction()

        await handle_edit_suggestion(interaction, self.bot, "Alien", "Aliens", None, None, None)

        self.assertIn("Multiple suggestions match", interaction.response.sent_message)


class EditDuplicateDetectionTests(EditSuggestionTestCase):
    async def test_edit_blocked_by_a_definite_duplicate(self) -> None:
        self.suggestion_service.suggest("Aliens", database_id=self.database.database_id, release_year=1986)
        interaction = FakeInteraction()

        await handle_edit_suggestion(interaction, self.bot, f"#{self.item.id}", "Aliens", 1986, None, None)

        self.assertIn("would duplicate", interaction.response.sent_message)
        self.assertEqual("Alien", self.suggestion_service.get_suggestion(self.item.id).title)

    async def test_possible_duplicate_requires_crew_confirmation(self) -> None:
        self.suggestion_service.suggest("Aliens", database_id=self.database.database_id, release_year=1986)
        interaction = FakeInteraction()

        await handle_edit_suggestion(interaction, self.bot, f"#{self.item.id}", "Aliens", None, None, None)

        self.assertIsNotNone(interaction.response.sent_view)
        self.assertEqual("Alien", self.suggestion_service.get_suggestion(self.item.id).title)

    async def test_confirming_a_possible_duplicate_with_an_identical_title_still_hits_the_uniqueness_constraint(
        self,
    ) -> None:
        # Known limitation (mirrors /add's equivalent case): a "possible
        # duplicate" match is only ever raised when the normalized title
        # already matches an existing item -- and SuggestionService's
        # storage has always been keyed by (database_id, normalized
        # title), so confirming "Save Anyway" for an identical title
        # still reports the pre-existing uniqueness constraint rather
        # than creating a second same-titled record. Redesigning that
        # storage key is out of this milestone's scope.
        self.suggestion_service.suggest("Aliens", database_id=self.database.database_id, release_year=1986)
        interaction = FakeInteraction()
        await handle_edit_suggestion(interaction, self.bot, f"#{self.item.id}", "Aliens", None, None, None)
        view = interaction.response.sent_view
        self.assertIsNotNone(view)

        confirm_interaction = FakeInteraction()
        await view.children[0].callback(confirm_interaction)

        self.assertIn("already exists", confirm_interaction.response.sent_message)
        self.assertEqual("Alien", self.suggestion_service.get_suggestion(self.item.id).title)

    async def test_editing_does_not_conflict_with_itself(self) -> None:
        # Editing an item's year without changing its title must not
        # treat the item's own unchanged record as a duplicate of itself.
        interaction = FakeInteraction()

        await handle_edit_suggestion(interaction, self.bot, f"#{self.item.id}", None, 1979, None, None)

        self.assertIsNone(interaction.response.sent_view)
        self.assertEqual(1979, self.suggestion_service.get_suggestion(self.item.id).release_year)


if __name__ == "__main__":
    unittest.main()
