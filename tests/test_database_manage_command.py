"""Tests for /database manage (Command Structure Cleanup Refinement): the
guided workflow -- pick a collection, then choose what to do with it --
alongside the existing direct /database subcommands (move/backup/
restore/reset/remove), which remain available as shortcuts.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import handle_database_manage
from watch_party_manager.database_admin_view import CollectionManagementMenuView, DestinationChoiceView
from watch_party_manager.domain.guild_configuration import GuildChannelsConfig, GuildConfiguration
from watch_party_manager.persistence.guild_configuration_repository import GuildConfigurationRepository
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.backup_service import BackupService
from watch_party_manager.services.config_service import ConfigService
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.suggestion_selection_view import DatabaseAdminSelectView

GUILD_ID = 100
HOME_CHANNEL_ID = 500
ORIGINAL_CHANNEL_ID = 300
WASH_CREW_ROLE_ID = 999


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, roles=()) -> None:
        self.roles = list(roles)


class FakeResponse:
    def __init__(self) -> None:
        self.sent_message = None
        self.sent_ephemeral = None
        self.sent_view = None
        self.sent_file = None
        self.edited_content = None
        self.edited_view = "not-edited"

    async def send_message(self, content, ephemeral=False, view=None, file=None) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral
        self.sent_view = view
        self.sent_file = file

    async def edit_message(self, content=None, view=None) -> None:
        self.edited_content = content
        self.edited_view = view


class FakeInteraction:
    def __init__(self, *, guild_id=GUILD_ID, guild=None, roles=(WASH_CREW_ROLE_ID,), channel=None) -> None:
        self.user = FakeMember(roles=[FakeRole(role_id) for role_id in roles])
        self.guild_id = guild_id
        self.guild = guild
        self.channel = channel
        self.response = FakeResponse()


class FakeBot:
    def __init__(
        self,
        suggestion_service,
        guild_configuration_repository,
        suggestion_database_configuration_repository,
        backup_service,
        *,
        wash_crew_role_id=WASH_CREW_ROLE_ID,
    ) -> None:
        self.suggestion_service = suggestion_service
        self.suggestion_database_configuration_repository = suggestion_database_configuration_repository
        self.guild_configuration_repository = guild_configuration_repository
        self.config_service = ConfigService(
            guild_configuration_repository, suggestion_service, suggestion_database_configuration_repository
        )
        self.backup_service = backup_service
        self.suggestion_database_repository = suggestion_service._database_repository
        self.suggestion_repository = suggestion_service._repository
        self.wash_crew_role_id = wash_crew_role_id


class DatabaseManageCommandTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.suggestion_repository = JsonSuggestionRepository(root / "suggestions.json")
        self.database_repository = JsonSuggestionDatabaseRepository(root / "suggestion_databases.json")
        self.suggestion_service = SuggestionService(
            repository=self.suggestion_repository, database_repository=self.database_repository
        )
        self.suggestion_database_configuration_repository = SuggestionDatabaseConfigurationRepository(
            root / "suggestion_database_configurations.json"
        )
        self.guild_configuration_repository = GuildConfigurationRepository(root / "guild_configurations.json")
        self.guild_configuration_repository.save(
            GuildConfiguration(
                guild_id=GUILD_ID,
                guild_name="Test Guild",
                setup_completed=True,
                channels=GuildChannelsConfig(home_channel_id=HOME_CHANNEL_ID),
            )
        )
        self.backup_service = BackupService(data_directory=root / "data", backup_directory=root / "backups")
        self.bot = FakeBot(
            self.suggestion_service,
            self.guild_configuration_repository,
            self.suggestion_database_configuration_repository,
            self.backup_service,
        )
        self.database = self.suggestion_service.create_database(
            "Movie Suggestions", guild_id=GUILD_ID, channel_id=ORIGINAL_CHANNEL_ID
        ).database

    async def _reach_management_menu(self):
        interaction = FakeInteraction()
        await handle_database_manage(interaction, self.bot)
        self.assertIsInstance(interaction.response.sent_view, DatabaseAdminSelectView)
        select = interaction.response.sent_view.children[0]
        select._values = [str(self.database.database_id)]

        select_interaction = FakeInteraction()
        await select.callback(interaction=select_interaction)
        return select_interaction


class PermissionTests(DatabaseManageCommandTestCase):
    async def test_rejects_a_non_wash_crew_member(self) -> None:
        interaction = FakeInteraction(roles=(1,))

        await handle_database_manage(interaction, self.bot)

        self.assertIn("WASH Crew", interaction.response.sent_message)

    async def test_fails_closed_when_role_is_unconfigured(self) -> None:
        self.bot.wash_crew_role_id = None
        interaction = FakeInteraction()

        await handle_database_manage(interaction, self.bot)

        self.assertIn("not been configured", interaction.response.sent_message)

    async def test_rejects_use_outside_a_guild(self) -> None:
        interaction = FakeInteraction(guild_id=None)

        await handle_database_manage(interaction, self.bot)

        self.assertIn("server", interaction.response.sent_message)

    async def test_reports_when_no_collections_exist(self) -> None:
        empty_bot = FakeBot(
            SuggestionService(
                repository=JsonSuggestionRepository(Path(self._temp_dir.name) / "empty_suggestions.json"),
                database_repository=JsonSuggestionDatabaseRepository(
                    Path(self._temp_dir.name) / "empty_suggestion_databases.json"
                ),
            ),
            self.guild_configuration_repository,
            self.suggestion_database_configuration_repository,
            self.backup_service,
        )
        interaction = FakeInteraction()

        await handle_database_manage(interaction, empty_bot)

        self.assertIn("No collections", interaction.response.sent_message)


class CollectionPickerTests(DatabaseManageCommandTestCase):
    async def test_shows_the_collection_picker(self) -> None:
        interaction = FakeInteraction()

        await handle_database_manage(interaction, self.bot)

        self.assertIsInstance(interaction.response.sent_view, DatabaseAdminSelectView)
        self.assertIn("manage", interaction.response.sent_message.lower())


class ManagementMenuTests(DatabaseManageCommandTestCase):
    async def test_selecting_a_collection_shows_the_management_menu(self) -> None:
        select_interaction = await self._reach_management_menu()

        self.assertIsInstance(select_interaction.response.edited_view, CollectionManagementMenuView)
        self.assertIn("Movie Suggestions", select_interaction.response.edited_content)

    async def test_menu_offers_every_documented_action(self) -> None:
        select_interaction = await self._reach_management_menu()
        menu_view = select_interaction.response.edited_view

        custom_ids = {button.custom_id for button in menu_view.children}

        self.assertEqual(
            custom_ids,
            {
                "wpm_database_manage_move",
                "wpm_database_manage_edit",
                "wpm_database_manage_backup",
                "wpm_database_manage_restore",
                "wpm_database_manage_reset",
                "wpm_database_manage_remove",
                "wpm_database_manage_cancel",
            },
        )

    async def test_a_deactivated_or_unknown_collection_is_reported_gracefully(self) -> None:
        interaction = FakeInteraction()
        await handle_database_manage(interaction, self.bot)
        select = interaction.response.sent_view.children[0]
        select._values = ["999999"]

        select_interaction = FakeInteraction()
        await select.callback(interaction=select_interaction)

        self.assertIn("no longer exists", select_interaction.response.edited_content)


class ManagementActionTests(DatabaseManageCommandTestCase):
    def _button(self, view, custom_id):
        return next(b for b in view.children if b.custom_id == custom_id)

    async def test_move_collection_launches_the_destination_choice(self) -> None:
        select_interaction = await self._reach_management_menu()
        menu_view = select_interaction.response.edited_view
        move_button = self._button(menu_view, "wpm_database_manage_move")

        action_interaction = FakeInteraction()
        await move_button.callback(interaction=action_interaction)

        self.assertIsInstance(action_interaction.response.edited_view, DestinationChoiceView)

    async def test_edit_collection_launches_the_settings_menu(self) -> None:
        select_interaction = await self._reach_management_menu()
        menu_view = select_interaction.response.edited_view
        edit_button = self._button(menu_view, "wpm_database_manage_edit")

        action_interaction = FakeInteraction()
        await edit_button.callback(interaction=action_interaction)

        self.assertIn("Suggestion Destination", action_interaction.response.edited_content)
        self.assertIn("Candidate Selection", action_interaction.response.edited_content)

    async def test_edit_collections_back_button_returns_to_the_management_menu(self) -> None:
        select_interaction = await self._reach_management_menu()
        menu_view = select_interaction.response.edited_view
        edit_button = self._button(menu_view, "wpm_database_manage_edit")
        action_interaction = FakeInteraction()
        await edit_button.callback(interaction=action_interaction)
        settings_view = action_interaction.response.edited_view
        back_button = next(b for b in settings_view.children if getattr(b, "custom_id", None) == "wpm_config_back_to_menu")

        back_interaction = FakeInteraction()
        await back_button.callback(interaction=back_interaction)

        self.assertIsInstance(back_interaction.response.edited_view, CollectionManagementMenuView)
        self.assertIn("Movie Suggestions", back_interaction.response.edited_content)

    async def test_backup_collection_creates_a_backup(self) -> None:
        select_interaction = await self._reach_management_menu()
        menu_view = select_interaction.response.edited_view
        backup_button = self._button(menu_view, "wpm_database_manage_backup")

        action_interaction = FakeInteraction()
        await backup_button.callback(interaction=action_interaction)

        self.assertIsNotNone(action_interaction.response.sent_file)

    async def test_restore_collection_points_at_the_direct_command(self) -> None:
        select_interaction = await self._reach_management_menu()
        menu_view = select_interaction.response.edited_view
        restore_button = self._button(menu_view, "wpm_database_manage_restore")

        action_interaction = FakeInteraction()
        await restore_button.callback(interaction=action_interaction)

        self.assertIn("/database restore", action_interaction.response.edited_content)

    async def test_reset_collection_shows_the_confirmation_flow(self) -> None:
        select_interaction = await self._reach_management_menu()
        menu_view = select_interaction.response.edited_view
        reset_button = self._button(menu_view, "wpm_database_manage_reset")

        action_interaction = FakeInteraction()
        await reset_button.callback(interaction=action_interaction)

        self.assertIn("Reset Collection", action_interaction.response.sent_message)

    async def test_remove_collection_deactivates_it(self) -> None:
        select_interaction = await self._reach_management_menu()
        menu_view = select_interaction.response.edited_view
        remove_button = self._button(menu_view, "wpm_database_manage_remove")

        action_interaction = FakeInteraction()
        await remove_button.callback(interaction=action_interaction)

        self.assertFalse(self.suggestion_service.get_database(self.database.database_id).active)

    async def test_cancel_makes_no_changes(self) -> None:
        select_interaction = await self._reach_management_menu()
        menu_view = select_interaction.response.edited_view
        cancel_button = self._button(menu_view, "wpm_database_manage_cancel")

        action_interaction = FakeInteraction()
        await cancel_button.callback(interaction=action_interaction)

        self.assertIn("No changes", action_interaction.response.edited_content)
        self.assertTrue(self.suggestion_service.get_database(self.database.database_id).active)


if __name__ == "__main__":
    unittest.main()
