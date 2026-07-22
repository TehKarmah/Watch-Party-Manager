"""Tests for FR-033A's /remove rewiring: reference/title matching, the
multi-match selector, and archive-based removal."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from watch_party_manager.bot import handle_remove_suggestion
from watch_party_manager.domain.watch_item import WatchItemStatus
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


class HandleRemoveSuggestionTestCase(unittest.IsolatedAsyncioTestCase):
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

    def tearDown(self) -> None:
        self._temp_dir.cleanup()


class RemovePermissionTests(HandleRemoveSuggestionTestCase):
    async def test_non_wash_crew_is_rejected(self) -> None:
        interaction = FakeInteraction(user=FakeMember([]))

        await handle_remove_suggestion(interaction, self.bot, "Alien")

        self.assertIn("WASH Crew", interaction.response.sent_message)

    async def test_unconfigured_role_fails_closed(self) -> None:
        bot = FakeBot(self.suggestion_service, wash_crew_role_id=None)
        interaction = FakeInteraction()

        await handle_remove_suggestion(interaction, bot, "Alien")

        self.assertIn("not been configured", interaction.response.sent_message)


class RemoveMatchingTests(HandleRemoveSuggestionTestCase):
    async def test_matches_by_reference_number(self) -> None:
        item = self.suggestion_service.suggest("Alien (1979)", database_id=self.database.database_id).watch_item
        interaction = FakeInteraction()

        await handle_remove_suggestion(interaction, self.bot, f"#{item.id}")

        self.assertIn("Remove this suggestion?", interaction.response.sent_message)
        # confirmation view shown, not yet removed
        self.assertIsNotNone(interaction.response.sent_view)

    async def test_matches_by_exact_title(self) -> None:
        self.suggestion_service.suggest("Alien (1979)", database_id=self.database.database_id)
        interaction = FakeInteraction()

        await handle_remove_suggestion(interaction, self.bot, "Alien (1979)")

        self.assertIsNotNone(interaction.response.sent_view)

    async def test_matches_by_title_without_year(self) -> None:
        self.suggestion_service.suggest("Alien (1979)", database_id=self.database.database_id)
        interaction = FakeInteraction()

        await handle_remove_suggestion(interaction, self.bot, "Alien")

        self.assertIsNotNone(interaction.response.sent_view)

    async def test_no_match_returns_a_clear_ephemeral_response(self) -> None:
        interaction = FakeInteraction()

        await handle_remove_suggestion(interaction, self.bot, "Nonexistent")

        self.assertIn("No suggestion matches", interaction.response.sent_message)
        self.assertTrue(interaction.response.sent_ephemeral)

    async def test_multiple_matches_show_a_selector(self) -> None:
        other_database = self.suggestion_service.create_database(
            "Other DB", guild_id=GUILD_ID, channel_id=555
        ).database
        self.suggestion_service.suggest("Alien (1979)", database_id=self.database.database_id)
        self.suggestion_service.suggest("Alien (1979)", database_id=other_database.database_id)
        interaction = FakeInteraction()

        await handle_remove_suggestion(interaction, self.bot, "Alien (1979)")

        self.assertIsNotNone(interaction.response.sent_view)
        self.assertEqual(2, len(interaction.response.sent_view.children[0].options))

    async def test_selecting_from_multiple_matches_shows_confirmation(self) -> None:
        other_database = self.suggestion_service.create_database(
            "Other DB", guild_id=GUILD_ID, channel_id=555
        ).database
        first = self.suggestion_service.suggest("Alien (1979)", database_id=self.database.database_id).watch_item
        self.suggestion_service.suggest("Alien (1979)", database_id=other_database.database_id)
        interaction = FakeInteraction()
        await handle_remove_suggestion(interaction, self.bot, "Alien (1979)")
        select = interaction.response.sent_view.children[0]
        select._values = [str(first.id)]

        select_interaction = FakeInteraction()
        await select.callback(select_interaction)

        self.assertIn("Remove this suggestion?", select_interaction.response.sent_message)


class RemoveArchivalBehaviorTests(HandleRemoveSuggestionTestCase):
    async def test_confirming_archives_rather_than_deletes(self) -> None:
        item = self.suggestion_service.suggest("Alien (1979)", database_id=self.database.database_id).watch_item
        interaction = FakeInteraction()
        await handle_remove_suggestion(interaction, self.bot, "Alien (1979)")
        view = interaction.response.sent_view

        confirm_interaction = FakeInteraction()
        await view.children[0].callback(confirm_interaction)

        stored = self.suggestion_service.get_suggestion(item.id)
        self.assertIsNotNone(stored)  # still exists -- not hard-deleted
        self.assertEqual(WatchItemStatus.ARCHIVED, stored.status)

    async def test_cancel_leaves_the_item_active(self) -> None:
        item = self.suggestion_service.suggest("Alien (1979)", database_id=self.database.database_id).watch_item
        interaction = FakeInteraction()
        await handle_remove_suggestion(interaction, self.bot, "Alien (1979)")
        view = interaction.response.sent_view

        cancel_interaction = FakeInteraction()
        await view.children[1].callback(cancel_interaction)

        stored = self.suggestion_service.get_suggestion(item.id)
        self.assertEqual(WatchItemStatus.SUGGESTED, stored.status)

    async def test_preserves_journey_history(self) -> None:
        item = self.suggestion_service.suggest("Alien (1979)", database_id=self.database.database_id).watch_item
        self.suggestion_service.reject_suggestion(item.id, discord_user_id=1, rejection_threshold=99)
        interaction = FakeInteraction()
        await handle_remove_suggestion(interaction, self.bot, "Alien (1979)")
        view = interaction.response.sent_view

        confirm_interaction = FakeInteraction()
        await view.children[0].callback(confirm_interaction)

        stored = self.suggestion_service.get_suggestion(item.id)
        self.assertEqual((1,), stored.journey.rejected_by_discord_user_ids)


if __name__ == "__main__":
    unittest.main()
