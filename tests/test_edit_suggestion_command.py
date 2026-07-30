"""Tests for FR-033A/Requirement 8's Crew-only /edit_suggestion command.

Requirement 8 replaced the old field-editing command (title/release_year/
imdb_url/database_id options) with an action picker: Change Status, Move
to Another Collection, Cancel. IMDb-derived metadata is read-only.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import discord

from watch_party_manager.bot import build_edit_suggestion_summary, handle_edit_suggestion
from watch_party_manager.domain.watch_item import MetadataProvider, WatchItemStatus
from watch_party_manager.edit_suggestion_view import (
    ChangeStatusSelectView,
    EditSuggestionActionView,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.suggestion_selection_view import DatabaseAdminSelectView

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


class FakeGuild:
    def __init__(self, guild_id: int) -> None:
        self.id = guild_id

    def get_channel_or_thread(self, channel_id):
        return None


class FakeResponse:
    def __init__(self) -> None:
        self.sent_message = None
        self.sent_ephemeral = None
        self.sent_view = None
        self.edited_content = None
        self.edited_view = "not-edited"

    async def send_message(self, content, ephemeral=False, view=None) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral
        self.sent_view = view

    async def edit_message(self, content=None, *, view=None) -> None:
        self.edited_content = content
        self.edited_view = view


class FakeInteraction:
    def __init__(self, user=None, *, guild_id: int = GUILD_ID) -> None:
        self.user = user if user is not None else FakeMember([WASH_CREW_ROLE_ID])
        self.response = FakeResponse()
        self.guild_id = guild_id
        self.guild = FakeGuild(guild_id) if guild_id is not None else None


class FakeBot:
    def __init__(self, suggestion_service, wash_crew_role_id=WASH_CREW_ROLE_ID) -> None:
        self.suggestion_service = suggestion_service
        self.suggestion_database_configuration_repository = None
        self.rotation_service = None
        self.wash_crew_role_id = wash_crew_role_id


class EditSuggestionTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
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
            "Alien", database_id=self.database.database_id, guild_id=GUILD_ID, release_year=1979
        ).watch_item


class EditPermissionTests(EditSuggestionTestCase):
    async def test_non_wash_crew_is_rejected(self) -> None:
        interaction = FakeInteraction(user=FakeMember([]))

        await handle_edit_suggestion(interaction, self.bot, f"#{self.item.id}")

        self.assertIn("WASH Crew", interaction.response.sent_message)

    async def test_unconfigured_role_fails_closed(self) -> None:
        self.bot.wash_crew_role_id = None
        interaction = FakeInteraction()

        await handle_edit_suggestion(interaction, self.bot, f"#{self.item.id}")

        self.assertIn("not been configured", interaction.response.sent_message)


class ResolveAndPresentTests(EditSuggestionTestCase):
    async def test_no_match_returns_a_clear_response(self) -> None:
        interaction = FakeInteraction()

        await handle_edit_suggestion(interaction, self.bot, "Nonexistent")

        self.assertIn("No suggestion matches", interaction.response.sent_message)

    async def test_ambiguous_match_requires_a_reference_number(self) -> None:
        other_database = self.suggestion_service.create_database(
            "Other DB", guild_id=GUILD_ID, channel_id=555
        ).database
        self.suggestion_service.suggest("Alien", database_id=other_database.database_id)
        interaction = FakeInteraction()

        await handle_edit_suggestion(interaction, self.bot, "Alien")

        self.assertIn("Multiple suggestions match", interaction.response.sent_message)

    async def test_matches_by_exact_title(self) -> None:
        interaction = FakeInteraction()

        await handle_edit_suggestion(interaction, self.bot, "Alien")

        self.assertIsInstance(interaction.response.sent_view, EditSuggestionActionView)

    async def test_shows_the_action_picker_with_a_read_only_summary(self) -> None:
        interaction = FakeInteraction()

        await handle_edit_suggestion(interaction, self.bot, f"#{self.item.id}")

        self.assertIn("Alien", interaction.response.sent_message)
        self.assertIn("(1979)", interaction.response.sent_message)
        self.assertIn("Movie Night", interaction.response.sent_message)
        self.assertIsInstance(interaction.response.sent_view, EditSuggestionActionView)

    async def test_summary_shows_the_current_status(self) -> None:
        summary = build_edit_suggestion_summary(self.item, self.suggestion_service)
        self.assertIn("🟢 Available", summary)

    async def test_summary_shows_imdb_link_when_present(self) -> None:
        # No confirmation post exists for this suggestion (no channel_id/
        # message_id) -- the legacy IMDb-URL fallback applies, since
        # there's no "Original Suggestion" post to link to instead.
        item_with_imdb = self.suggestion_service.suggest(
            "Aliens", database_id=self.database.database_id, imdb_url="https://www.imdb.com/title/tt0090605/"
        ).watch_item
        summary = build_edit_suggestion_summary(item_with_imdb, self.suggestion_service)
        self.assertIn("tt0090605", summary)

    async def test_summary_links_to_the_original_post_instead_of_the_raw_imdb_url_when_one_exists(self) -> None:
        # UX Polish: the original post's own embed already has an
        # IMDb-linked title card, so once a link to that post exists,
        # the top link points there instead of repeating the raw IMDb
        # URL a second time.
        item_with_post = self.suggestion_service.suggest(
            "Aliens",
            database_id=self.database.database_id,
            guild_id=GUILD_ID,
            channel_id=777,
            message_id=888,
            imdb_url="https://www.imdb.com/title/tt0090605/",
        ).watch_item

        summary = build_edit_suggestion_summary(item_with_post, self.suggestion_service)

        expected_link = f"https://discord.com/channels/{GUILD_ID}/777/888"
        self.assertIn(f"[Original Suggestion]({expected_link})", summary)
        self.assertNotIn("tt0090605", summary)

    async def test_summary_falls_back_to_the_raw_imdb_url_for_a_legacy_suggestion_without_a_post(self) -> None:
        # Graceful handling: a legacy suggestion never publicly
        # confirmed has no post to link to -- the raw IMDb URL remains
        # the only way to reach its IMDb page from here.
        item_without_post = self.suggestion_service.suggest(
            "Aliens", database_id=self.database.database_id, imdb_url="https://www.imdb.com/title/tt0090605/"
        ).watch_item

        summary = build_edit_suggestion_summary(item_without_post, self.suggestion_service)

        self.assertNotIn("Original Suggestion", summary)
        self.assertIn("IMDb: https://www.imdb.com/title/tt0090605/", summary)

    async def test_summary_shows_the_won_date_for_a_vote_winner(self) -> None:
        from datetime import date

        winner = self.suggestion_service.suggest("Zombieland", database_id=self.database.database_id).watch_item
        self.suggestion_service.record_vote_win(winner.id, date(2026, 7, 28))
        winner = self.suggestion_service.get_suggestion(winner.id)

        summary = build_edit_suggestion_summary(winner, self.suggestion_service)

        self.assertIn("🏆 Vote Winner", summary)
        self.assertIn("Won: July 28, 2026", summary)

    async def test_summary_omits_the_won_line_for_a_legacy_vote_winner(self) -> None:
        winner = self.suggestion_service.suggest("Zombieland", database_id=self.database.database_id).watch_item
        self.suggestion_service.record_vote_win(winner.id, None)
        winner = self.suggestion_service.get_suggestion(winner.id)

        summary = build_edit_suggestion_summary(winner, self.suggestion_service)

        self.assertIn("🏆 Vote Winner", summary)
        self.assertNotIn("Won", summary)


class ChangeStatusActionTests(EditSuggestionTestCase):
    async def _open_change_status(self):
        interaction = FakeInteraction()
        await handle_edit_suggestion(interaction, self.bot, f"#{self.item.id}")
        action_view = interaction.response.sent_view
        change_status_button = action_view.children[0]
        button_interaction = FakeInteraction()
        await change_status_button.callback(button_interaction)
        return button_interaction

    async def test_change_status_button_shows_a_status_dropdown(self) -> None:
        button_interaction = await self._open_change_status()
        self.assertIsInstance(button_interaction.response.edited_view, ChangeStatusSelectView)

    async def test_selecting_vote_winner_sets_the_status(self) -> None:
        button_interaction = await self._open_change_status()
        select = button_interaction.response.edited_view.children[0]
        select._values = ["vote_winner"]

        select_interaction = FakeInteraction()
        await select.callback(select_interaction)

        self.assertEqual(
            WatchItemStatus.VOTE_WINNER, self.suggestion_service.get_suggestion(self.item.id).status
        )
        # UX Polish: the confirmation uses the established display-status
        # vocabulary ("🏆 Vote Winner"), not a raw internal enum value.
        self.assertIn("🏆 Vote Winner", select_interaction.response.sent_message)

    async def test_selecting_retired_archives_the_suggestion(self) -> None:
        button_interaction = await self._open_change_status()
        select = button_interaction.response.edited_view.children[0]
        select._values = ["archived"]

        select_interaction = FakeInteraction()
        await select.callback(select_interaction)

        self.assertEqual(WatchItemStatus.ARCHIVED, self.suggestion_service.get_suggestion(self.item.id).status)
        self.assertIn("🗄️ Retired", select_interaction.response.sent_message)
        self.assertNotIn("Archived", select_interaction.response.sent_message)

    async def test_selecting_available_restores_suggested(self) -> None:
        self.suggestion_service.archive_suggestion(self.item.id)
        button_interaction = await self._open_change_status()
        select = button_interaction.response.edited_view.children[0]
        select._values = ["suggested"]

        select_interaction = FakeInteraction()
        await select.callback(select_interaction)

        self.assertEqual(
            WatchItemStatus.SUGGESTED, self.suggestion_service.get_suggestion(self.item.id).status
        )
        self.assertIn("🟢 Available", select_interaction.response.sent_message)
        self.assertNotIn("Suggested", select_interaction.response.sent_message)

    async def test_status_dropdown_never_offers_rotation_cooldown(self) -> None:
        button_interaction = await self._open_change_status()
        select = button_interaction.response.edited_view.children[0]
        offered_values = {option.value for option in select.options}
        self.assertEqual({"suggested", "vote_winner", "archived"}, offered_values)


class MoveCollectionActionTests(EditSuggestionTestCase):
    async def _open_move_collection(self):
        interaction = FakeInteraction()
        await handle_edit_suggestion(interaction, self.bot, f"#{self.item.id}")
        action_view = interaction.response.sent_view
        move_button = action_view.children[1]
        button_interaction = FakeInteraction()
        await move_button.callback(button_interaction)
        return button_interaction

    async def test_move_button_shows_a_collection_dropdown(self) -> None:
        button_interaction = await self._open_move_collection()
        self.assertIsInstance(button_interaction.response.edited_view, DatabaseAdminSelectView)

    async def test_moves_to_another_database(self) -> None:
        other_database = self.suggestion_service.create_database(
            "Other DB", guild_id=GUILD_ID, channel_id=555
        ).database
        button_interaction = await self._open_move_collection()
        select = button_interaction.response.edited_view.children[0]
        select._values = [str(other_database.database_id)]

        select_interaction = FakeInteraction()
        await select.callback(select_interaction)

        self.assertEqual(
            other_database.database_id, self.suggestion_service.get_suggestion(self.item.id).database_id
        )

    async def test_move_preserves_the_suggestions_status(self) -> None:
        self.suggestion_service.set_suggestion_status(self.item.id, WatchItemStatus.VOTE_WINNER)
        other_database = self.suggestion_service.create_database(
            "Other DB", guild_id=GUILD_ID, channel_id=555
        ).database
        button_interaction = await self._open_move_collection()
        select = button_interaction.response.edited_view.children[0]
        select._values = [str(other_database.database_id)]

        select_interaction = FakeInteraction()
        await select.callback(select_interaction)

        moved = self.suggestion_service.get_suggestion(self.item.id)
        self.assertEqual(other_database.database_id, moved.database_id)
        self.assertEqual(WatchItemStatus.VOTE_WINNER, moved.status)

    async def test_move_preserves_title_and_release_year(self) -> None:
        other_database = self.suggestion_service.create_database(
            "Other DB", guild_id=GUILD_ID, channel_id=555
        ).database
        button_interaction = await self._open_move_collection()
        select = button_interaction.response.edited_view.children[0]
        select._values = [str(other_database.database_id)]

        select_interaction = FakeInteraction()
        await select.callback(select_interaction)

        moved = self.suggestion_service.get_suggestion(self.item.id)
        self.assertEqual("Alien", moved.title)
        self.assertEqual(1979, moved.release_year)

    async def test_rejects_moving_to_an_inactive_database(self) -> None:
        other_database = self.suggestion_service.create_database(
            "Other DB", guild_id=GUILD_ID, channel_id=555
        ).database
        self.suggestion_service.deactivate_database(other_database.database_id, guild_id=GUILD_ID)
        button_interaction = await self._open_move_collection()
        select = button_interaction.response.edited_view.children[0]
        select._values = [str(other_database.database_id)]

        select_interaction = FakeInteraction()
        await select.callback(select_interaction)

        self.assertIn("not available", select_interaction.response.sent_message)
        self.assertEqual(
            self.database.database_id, self.suggestion_service.get_suggestion(self.item.id).database_id
        )

    async def test_moving_to_the_same_collection_is_a_no_op_with_a_clear_message(self) -> None:
        button_interaction = await self._open_move_collection()
        select = button_interaction.response.edited_view.children[0]
        select._values = [str(self.database.database_id)]

        select_interaction = FakeInteraction()
        await select.callback(select_interaction)

        self.assertIn("already in that collection", select_interaction.response.sent_message)

    async def test_move_blocked_by_a_definite_duplicate(self) -> None:
        other_database = self.suggestion_service.create_database(
            "Other DB", guild_id=GUILD_ID, channel_id=555
        ).database
        self.suggestion_service.suggest("Alien", database_id=other_database.database_id, release_year=1979)
        button_interaction = await self._open_move_collection()
        select = button_interaction.response.edited_view.children[0]
        select._values = [str(other_database.database_id)]

        select_interaction = FakeInteraction()
        await select.callback(select_interaction)

        self.assertIn("would duplicate", select_interaction.response.sent_message)
        self.assertEqual(
            self.database.database_id, self.suggestion_service.get_suggestion(self.item.id).database_id
        )

    async def test_possible_duplicate_requires_crew_confirmation(self) -> None:
        other_database = self.suggestion_service.create_database(
            "Other DB", guild_id=GUILD_ID, channel_id=555
        ).database
        self.suggestion_service.suggest("Alien", database_id=other_database.database_id)
        button_interaction = await self._open_move_collection()
        select = button_interaction.response.edited_view.children[0]
        select._values = [str(other_database.database_id)]

        select_interaction = FakeInteraction()
        await select.callback(select_interaction)

        self.assertIsNotNone(select_interaction.response.sent_view)
        self.assertEqual(
            self.database.database_id, self.suggestion_service.get_suggestion(self.item.id).database_id
        )
        # UX Polish: "Move Anyway" isn't destructive (it just proceeds
        # past a possible-duplicate warning), so it must not use the
        # alarming red/danger style reserved for genuinely destructive
        # confirmations.
        self.assertEqual(select_interaction.response.sent_view.children[0].style, discord.ButtonStyle.primary)

    async def test_confirming_an_identical_title_possible_duplicate_still_hits_the_uniqueness_constraint(
        self,
    ) -> None:
        # Known limitation (mirrors /add's and the old /edit_suggestion's
        # equivalent case): a "possible duplicate" match only ever fires
        # when the normalized title already matches an existing item, and
        # SuggestionService's storage has always been keyed by
        # (database_id, normalized title) -- so confirming "Move Anyway"
        # into a collection that already has that exact title still
        # reports the pre-existing uniqueness constraint instead of
        # creating a second same-titled record there.
        other_database = self.suggestion_service.create_database(
            "Other DB", guild_id=GUILD_ID, channel_id=555
        ).database
        self.suggestion_service.suggest("Alien", database_id=other_database.database_id)
        button_interaction = await self._open_move_collection()
        select = button_interaction.response.edited_view.children[0]
        select._values = [str(other_database.database_id)]
        select_interaction = FakeInteraction()
        await select.callback(select_interaction)
        view = select_interaction.response.sent_view

        confirm_interaction = FakeInteraction()
        await view.children[0].callback(confirm_interaction)

        self.assertIn("already exists", confirm_interaction.response.sent_message)
        self.assertEqual(
            self.database.database_id, self.suggestion_service.get_suggestion(self.item.id).database_id
        )


class CancelActionTests(EditSuggestionTestCase):
    async def test_cancel_makes_no_changes(self) -> None:
        interaction = FakeInteraction()
        await handle_edit_suggestion(interaction, self.bot, f"#{self.item.id}")
        action_view = interaction.response.sent_view
        cancel_button = action_view.children[2]

        cancel_interaction = FakeInteraction()
        await cancel_button.callback(cancel_interaction)

        self.assertIn("No changes were made", cancel_interaction.response.edited_content)
        self.assertEqual(self.database.database_id, self.suggestion_service.get_suggestion(self.item.id).database_id)
        self.assertEqual(WatchItemStatus.SUGGESTED, self.suggestion_service.get_suggestion(self.item.id).status)


if __name__ == "__main__":
    unittest.main()
