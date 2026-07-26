"""Tests for /database move (Command Structure Cleanup, pre-v1): move a
collection's suggestion destination to a different channel or thread,
via the same destination-choice flow /database add uses.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import discord

from watch_party_manager.bot import handle_database_move
from watch_party_manager.database_admin_view import DestinationChoiceView
from watch_party_manager.domain.guild_configuration import GuildChannelsConfig, GuildConfiguration
from watch_party_manager.persistence.guild_configuration_repository import GuildConfigurationRepository
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
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
        self.edited_content = None
        self.edited_view = "not-edited"
        self.sent_modal = None

    async def send_message(self, content, ephemeral=False, view=None) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral
        self.sent_view = view

    async def edit_message(self, content=None, view=None) -> None:
        self.edited_content = content
        self.edited_view = view

    async def send_modal(self, modal) -> None:
        self.sent_modal = modal


class FakeThread:
    def __init__(self, thread_id: int) -> None:
        self.id = thread_id
        self.deleted = False

    async def delete(self) -> None:
        self.deleted = True


class FakeHomeChannel:
    def __init__(self, *, thread_id: int = 700) -> None:
        self._thread_id = thread_id
        self.created_thread: FakeThread | None = None

    async def create_thread(self, *, name, type):
        self.created_thread = FakeThread(self._thread_id)
        return self.created_thread


class FakeUsablePermissions:
    view_channel = True
    send_messages = True


class FakeUsableChannel:
    def permissions_for(self, member) -> FakeUsablePermissions:
        return FakeUsablePermissions()


class FakeGuild:
    """Satisfies both ChannelLookup (validate_channel_usable, via
    get_channel_or_thread/me) and the home-channel lookup (get_channel).
    """

    def __init__(self, home_channel=None, *, usable_channel_ids=frozenset()) -> None:
        self._home_channel = home_channel
        self._usable_channel_ids = usable_channel_ids
        self.me = object()

    def get_channel(self, channel_id):
        if channel_id == HOME_CHANNEL_ID:
            return self._home_channel
        return None

    def get_channel_or_thread(self, channel_id):
        if channel_id in self._usable_channel_ids:
            return FakeUsableChannel()
        return None


class FakeCurrentLocation:
    """A minimal stand-in for the channel/thread a command was run in."""

    def __init__(self, location_id: int, channel_type) -> None:
        self.id = location_id
        self.type = channel_type


class FakeInteraction:
    def __init__(
        self, *, guild_id=GUILD_ID, guild=None, roles=(WASH_CREW_ROLE_ID,), channel=None
    ) -> None:
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
        *,
        wash_crew_role_id=WASH_CREW_ROLE_ID,
    ) -> None:
        self.suggestion_service = suggestion_service
        self.suggestion_database_configuration_repository = suggestion_database_configuration_repository
        self.guild_configuration_repository = guild_configuration_repository
        self.config_service = ConfigService(
            guild_configuration_repository, suggestion_service, suggestion_database_configuration_repository
        )
        self.wash_crew_role_id = wash_crew_role_id


class DatabaseMoveCommandTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
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
        self.bot = FakeBot(
            self.suggestion_service,
            self.guild_configuration_repository,
            self.suggestion_database_configuration_repository,
        )
        self.database = self.suggestion_service.create_database(
            "Movie Suggestions", guild_id=GUILD_ID, channel_id=ORIGINAL_CHANNEL_ID
        ).database


class PermissionTests(DatabaseMoveCommandTestCase):
    async def test_rejects_a_non_wash_crew_member(self) -> None:
        interaction = FakeInteraction(roles=(1,))

        await handle_database_move(interaction, self.bot)

        self.assertIn("WASH Crew", interaction.response.sent_message)

    async def test_fails_closed_when_role_is_unconfigured(self) -> None:
        self.bot.wash_crew_role_id = None
        interaction = FakeInteraction()

        await handle_database_move(interaction, self.bot)

        self.assertIn("not been configured", interaction.response.sent_message)

    async def test_rejects_use_outside_a_guild(self) -> None:
        interaction = FakeInteraction(guild_id=None)

        await handle_database_move(interaction, self.bot)

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
        )
        interaction = FakeInteraction()

        await handle_database_move(interaction, empty_bot)

        self.assertIn("No collections", interaction.response.sent_message)


class DatabaseSelectionTests(DatabaseMoveCommandTestCase):
    async def _select_database(self):
        interaction = FakeInteraction()
        await handle_database_move(interaction, self.bot)
        self.assertIsInstance(interaction.response.sent_view, DatabaseAdminSelectView)
        select = interaction.response.sent_view.children[0]

        select_interaction = FakeInteraction()
        select._values = [str(self.database.database_id)]
        await select.callback(interaction=select_interaction)
        return select_interaction

    async def test_shows_the_destination_choice_for_the_selected_collection(self) -> None:
        select_interaction = await self._select_database()

        self.assertIsInstance(select_interaction.response.edited_view, DestinationChoiceView)
        self.assertIn("Movie Suggestions", select_interaction.response.edited_content)


class MoveFlowTests(DatabaseMoveCommandTestCase):
    async def _reach_destination_choice(self, *, guild=None, channel=None):
        interaction = FakeInteraction()
        await handle_database_move(interaction, self.bot)
        select = interaction.response.sent_view.children[0]
        select._values = [str(self.database.database_id)]

        select_interaction = FakeInteraction(guild=guild, channel=channel)
        await select.callback(interaction=select_interaction)
        return select_interaction

    async def test_use_existing_channel_moves_the_suggestion_destination(self) -> None:
        new_channel_id = 400
        guild = FakeGuild(usable_channel_ids={new_channel_id})
        select_interaction = await self._reach_destination_choice(guild=guild)
        destination_view = select_interaction.response.edited_view
        existing_channel_button = next(
            b for b in destination_view.children
            if b.custom_id == "wpm_database_admin_destination_existing_channel"
        )

        destination_interaction = FakeInteraction(guild=guild)
        await existing_channel_button.callback(interaction=destination_interaction)
        select_view = destination_interaction.response.edited_view
        channel_select = select_view.children[0]
        channel_select._values = [type("FakeChannelValue", (), {"id": new_channel_id})()]

        select_interaction2 = FakeInteraction(guild=guild)
        await channel_select.callback(interaction=select_interaction2)

        resolved = self.suggestion_service.resolve_collection_channel_id(
            self.database, self.suggestion_database_configuration_repository
        )
        self.assertEqual(resolved, new_channel_id)
        self.assertIn("updated", select_interaction2.response.edited_content)

    async def test_database_id_and_suggestions_are_preserved_after_a_move(self) -> None:
        self.suggestion_service.suggest("The Matrix", database_id=self.database.database_id)
        new_channel_id = 400
        guild = FakeGuild(usable_channel_ids={new_channel_id})
        select_interaction = await self._reach_destination_choice(guild=guild)
        destination_view = select_interaction.response.edited_view
        existing_channel_button = next(
            b for b in destination_view.children
            if b.custom_id == "wpm_database_admin_destination_existing_channel"
        )
        destination_interaction = FakeInteraction(guild=guild)
        await existing_channel_button.callback(interaction=destination_interaction)
        select_view = destination_interaction.response.edited_view
        channel_select = select_view.children[0]
        channel_select._values = [type("FakeChannelValue", (), {"id": new_channel_id})()]
        select_interaction2 = FakeInteraction(guild=guild)
        await channel_select.callback(interaction=select_interaction2)

        unchanged = self.suggestion_service.get_database(self.database.database_id)
        self.assertEqual(unchanged.database_id, self.database.database_id)
        self.assertEqual(unchanged.channel_id, ORIGINAL_CHANNEL_ID)  # the database's own operational channel is untouched
        suggestions = self.suggestion_service.get_suggestions_for_database(self.database.database_id)
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0].title, "The Matrix")

    async def test_use_existing_thread_moves_the_suggestion_destination(self) -> None:
        new_thread_id = 401
        guild = FakeGuild(usable_channel_ids={new_thread_id})
        select_interaction = await self._reach_destination_choice(guild=guild)
        destination_view = select_interaction.response.edited_view
        existing_thread_button = next(
            b for b in destination_view.children
            if b.custom_id == "wpm_database_admin_destination_existing_thread"
        )

        destination_interaction = FakeInteraction(guild=guild)
        await existing_thread_button.callback(interaction=destination_interaction)
        select_view = destination_interaction.response.edited_view
        thread_select = select_view.children[0]
        thread_select._values = [type("FakeChannelValue", (), {"id": new_thread_id})()]

        select_interaction2 = FakeInteraction(guild=guild)
        await thread_select.callback(interaction=select_interaction2)

        resolved = self.suggestion_service.resolve_collection_channel_id(
            self.database, self.suggestion_database_configuration_repository
        )
        self.assertEqual(resolved, new_thread_id)

    async def test_create_new_thread_moves_the_suggestion_destination(self) -> None:
        home_channel = FakeHomeChannel(thread_id=777)
        guild = FakeGuild(home_channel, usable_channel_ids={777})
        select_interaction = await self._reach_destination_choice(guild=guild)
        destination_view = select_interaction.response.edited_view
        create_thread_button = next(
            b for b in destination_view.children
            if b.custom_id == "wpm_database_admin_destination_create_thread"
        )

        destination_interaction = FakeInteraction(guild=guild)
        await create_thread_button.callback(interaction=destination_interaction)
        modal = destination_interaction.response.sent_modal
        self.assertEqual(modal.name_input.default, "Movie Suggestions")

        modal_interaction = FakeInteraction(guild=guild)
        await modal.on_submit(interaction=modal_interaction)

        self.assertEqual(home_channel.created_thread.id, 777)
        resolved = self.suggestion_service.resolve_collection_channel_id(
            self.database, self.suggestion_database_configuration_repository
        )
        self.assertEqual(resolved, 777)
        # The collection's own name is untouched by a move.
        self.assertEqual(self.suggestion_service.get_database(self.database.database_id).name, "Movie Suggestions")

    async def test_duplicate_destination_is_rejected(self) -> None:
        other = self.suggestion_service.create_database(
            "TV Suggestions", guild_id=GUILD_ID, channel_id=350
        ).database
        new_channel_id = other.channel_id
        guild = FakeGuild(usable_channel_ids={new_channel_id})
        select_interaction = await self._reach_destination_choice(guild=guild)
        destination_view = select_interaction.response.edited_view
        existing_channel_button = next(
            b for b in destination_view.children
            if b.custom_id == "wpm_database_admin_destination_existing_channel"
        )
        destination_interaction = FakeInteraction(guild=guild)
        await existing_channel_button.callback(interaction=destination_interaction)
        select_view = destination_interaction.response.edited_view
        channel_select = select_view.children[0]
        channel_select._values = [type("FakeChannelValue", (), {"id": new_channel_id})()]

        select_interaction2 = FakeInteraction(guild=guild)
        await channel_select.callback(interaction=select_interaction2)

        self.assertIn("already routed", select_interaction2.response.edited_content)
        resolved = self.suggestion_service.resolve_collection_channel_id(
            self.database, self.suggestion_database_configuration_repository
        )
        self.assertEqual(resolved, ORIGINAL_CHANNEL_ID)

    async def test_a_failed_move_rolls_back_the_newly_created_thread(self) -> None:
        other = self.suggestion_service.create_database(
            "TV Suggestions", guild_id=GUILD_ID, channel_id=350
        ).database
        home_channel = FakeHomeChannel(thread_id=other.channel_id)  # collides on purpose
        guild = FakeGuild(home_channel, usable_channel_ids={other.channel_id})
        select_interaction = await self._reach_destination_choice(guild=guild)
        destination_view = select_interaction.response.edited_view
        create_thread_button = next(
            b for b in destination_view.children
            if b.custom_id == "wpm_database_admin_destination_create_thread"
        )
        destination_interaction = FakeInteraction(guild=guild)
        await create_thread_button.callback(interaction=destination_interaction)
        modal = destination_interaction.response.sent_modal

        modal_interaction = FakeInteraction(guild=guild)
        await modal.on_submit(interaction=modal_interaction)

        self.assertTrue(home_channel.created_thread.deleted)


class UseCurrentThreadOrChannelTests(DatabaseMoveCommandTestCase):
    """Command Structure Cleanup Refinement: /database move's destination
    choice offers "Use Current Thread/Channel" too, behaving identically
    to /database add's.
    """

    async def _reach_destination_choice(self, *, guild=None, channel=None):
        interaction = FakeInteraction()
        await handle_database_move(interaction, self.bot)
        select = interaction.response.sent_view.children[0]
        select._values = [str(self.database.database_id)]

        select_interaction = FakeInteraction(guild=guild, channel=channel)
        await select.callback(interaction=select_interaction)
        return select_interaction

    def _use_current_button(self, destination_view):
        return next(
            b for b in destination_view.children
            if b.custom_id == "wpm_database_admin_destination_use_current"
        )

    async def test_enabled_and_moves_when_invoked_from_a_thread(self) -> None:
        new_thread_id = 601
        guild = FakeGuild(usable_channel_ids={new_thread_id})
        current_thread = FakeCurrentLocation(new_thread_id, discord.ChannelType.public_thread)
        select_interaction = await self._reach_destination_choice(guild=guild, channel=current_thread)
        destination_view = select_interaction.response.edited_view
        use_current_button = self._use_current_button(destination_view)
        self.assertFalse(use_current_button.disabled)

        destination_interaction = FakeInteraction(guild=guild, channel=current_thread)
        await use_current_button.callback(interaction=destination_interaction)

        resolved = self.suggestion_service.resolve_collection_channel_id(
            self.database, self.suggestion_database_configuration_repository
        )
        self.assertEqual(resolved, new_thread_id)

    async def test_enabled_and_moves_when_invoked_from_a_text_channel(self) -> None:
        new_channel_id = 602
        guild = FakeGuild(usable_channel_ids={new_channel_id})
        current_channel = FakeCurrentLocation(new_channel_id, discord.ChannelType.text)
        select_interaction = await self._reach_destination_choice(guild=guild, channel=current_channel)
        destination_view = select_interaction.response.edited_view
        use_current_button = self._use_current_button(destination_view)
        self.assertFalse(use_current_button.disabled)

        destination_interaction = FakeInteraction(guild=guild, channel=current_channel)
        await use_current_button.callback(interaction=destination_interaction)

        resolved = self.suggestion_service.resolve_collection_channel_id(
            self.database, self.suggestion_database_configuration_repository
        )
        self.assertEqual(resolved, new_channel_id)

    async def test_disabled_when_invoked_somewhere_unsuitable(self) -> None:
        unsuitable_location = FakeCurrentLocation(603, discord.ChannelType.voice)
        select_interaction = await self._reach_destination_choice(channel=unsuitable_location)
        destination_view = select_interaction.response.edited_view

        use_current_button = self._use_current_button(destination_view)

        self.assertTrue(use_current_button.disabled)

    async def test_disabled_when_no_current_location_is_known(self) -> None:
        select_interaction = await self._reach_destination_choice(channel=None)
        destination_view = select_interaction.response.edited_view

        use_current_button = self._use_current_button(destination_view)

        self.assertTrue(use_current_button.disabled)

    async def test_duplicate_current_location_is_rejected(self) -> None:
        other = self.suggestion_service.create_database(
            "TV Suggestions", guild_id=GUILD_ID, channel_id=350
        ).database
        current_channel = FakeCurrentLocation(other.channel_id, discord.ChannelType.text)
        guild = FakeGuild(usable_channel_ids={other.channel_id})
        select_interaction = await self._reach_destination_choice(guild=guild, channel=current_channel)
        destination_view = select_interaction.response.edited_view
        use_current_button = self._use_current_button(destination_view)

        destination_interaction = FakeInteraction(guild=guild, channel=current_channel)
        await use_current_button.callback(interaction=destination_interaction)

        self.assertIn("already routed", destination_interaction.response.edited_content)
        resolved = self.suggestion_service.resolve_collection_channel_id(
            self.database, self.suggestion_database_configuration_repository
        )
        self.assertEqual(resolved, ORIGINAL_CHANNEL_ID)


if __name__ == "__main__":
    unittest.main()
