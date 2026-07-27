"""Tests for the modernized /database add flow (Command Structure
Cleanup, pre-v1): type selection excluding already-used standard
collection types, then the shared destination choice (Create New
Thread/Use Existing Thread/Use Existing Channel), reused from
/database move.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import discord

from watch_party_manager.bot import handle_database_add
from watch_party_manager.database_admin_view import CollectionTypeSelectionView, DestinationChoiceView
from watch_party_manager.domain.guild_configuration import GuildChannelsConfig, GuildConfiguration
from watch_party_manager.persistence.guild_configuration_repository import GuildConfigurationRepository
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.suggestion_service import SuggestionService

GUILD_ID = 100
HOME_CHANNEL_ID = 500
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
    def __init__(self, *, thread_id: int = 700, fail: bool = False) -> None:
        self._thread_id = thread_id
        self._fail = fail
        self.created_thread: FakeThread | None = None

    async def create_thread(self, *, name, type):
        if self._fail:
            raise RuntimeError("boom")
        self.created_thread = FakeThread(self._thread_id)
        return self.created_thread


class FakeGuild:
    def __init__(self, home_channel=None) -> None:
        self._home_channel = home_channel

    def get_channel(self, channel_id):
        if channel_id == HOME_CHANNEL_ID:
            return self._home_channel
        return None


class FakeCurrentLocation:
    """A minimal stand-in for the channel/thread a command was run in.

    Also doubles as a thread parent for the Create New Thread Improvement
    fallback tests -- when this location is a text channel, Create New
    Thread may fall back to creating the new thread here.
    """

    def __init__(self, location_id: int, channel_type, *, new_thread_id: Optional[int] = None) -> None:
        self.id = location_id
        self.type = channel_type
        self._new_thread_id = new_thread_id
        self.created_thread: "FakeThread | None" = None

    async def create_thread(self, *, name, type):
        self.created_thread = FakeThread(self._new_thread_id if self._new_thread_id is not None else self.id)
        return self.created_thread


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
    def __init__(self, suggestion_service, guild_configuration_repository, *, wash_crew_role_id=WASH_CREW_ROLE_ID) -> None:
        self.suggestion_service = suggestion_service
        self.suggestion_database_configuration_repository = SuggestionDatabaseConfigurationRepository(
            Path(tempfile.mkdtemp()) / "suggestion_database_configurations.json"
        )
        self.guild_configuration_repository = guild_configuration_repository
        self.wash_crew_role_id = wash_crew_role_id


class DatabaseAddCommandTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
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
        self.bot = FakeBot(self.suggestion_service, self.guild_configuration_repository)


class PermissionTests(DatabaseAddCommandTestCase):
    async def test_rejects_a_non_wash_crew_member(self) -> None:
        interaction = FakeInteraction(roles=(1,))

        await handle_database_add(interaction, self.bot)

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIn("WASH Crew", interaction.response.sent_message)

    async def test_fails_closed_when_role_is_unconfigured(self) -> None:
        self.bot.wash_crew_role_id = None
        interaction = FakeInteraction()

        await handle_database_add(interaction, self.bot)

        self.assertIn("not been configured", interaction.response.sent_message)

    async def test_rejects_use_outside_a_guild(self) -> None:
        interaction = FakeInteraction(guild_id=None)

        await handle_database_add(interaction, self.bot)

        self.assertIn("Discord server", interaction.response.sent_message)


class TypeSelectionTests(DatabaseAddCommandTestCase):
    async def test_offers_every_standard_type_when_no_collections_exist(self) -> None:
        interaction = FakeInteraction()

        await handle_database_add(interaction, self.bot)

        self.assertIsInstance(interaction.response.sent_view, CollectionTypeSelectionView)
        custom_ids = [button.custom_id for button in interaction.response.sent_view.children]
        for key in ("movies", "tv_shows", "anime", "holiday", "documentaries", "horror"):
            self.assertIn(f"wpm_database_add_type_{key}", custom_ids)

    async def test_excludes_a_standard_type_that_already_has_a_matching_collection(self) -> None:
        self.suggestion_service.create_database("Movie Suggestions", guild_id=GUILD_ID, channel_id=200)
        interaction = FakeInteraction()

        await handle_database_add(interaction, self.bot)

        custom_ids = [button.custom_id for button in interaction.response.sent_view.children]
        self.assertNotIn("wpm_database_add_type_movies", custom_ids)
        self.assertIn("wpm_database_add_type_tv_shows", custom_ids)

    async def test_special_and_custom_remain_available_when_every_standard_type_exists(self) -> None:
        for index, name in enumerate(
            ["Movie Suggestions", "TV Suggestions", "Anime Suggestions", "Holiday Suggestions",
             "Documentary Suggestions", "Horror Suggestions"]
        ):
            self.suggestion_service.create_database(name, guild_id=GUILD_ID, channel_id=200 + index)
        interaction = FakeInteraction()

        await handle_database_add(interaction, self.bot)

        custom_ids = [button.custom_id for button in interaction.response.sent_view.children]
        self.assertEqual(
            custom_ids, ["wpm_database_add_type_special", "wpm_database_add_type_custom", "wpm_database_add_cancel"]
        )


class DestinationFlowTests(DatabaseAddCommandTestCase):
    async def _reach_destination_choice(self, *, guild=None, channel=None):
        interaction = FakeInteraction(guild=guild, channel=channel)
        await handle_database_add(interaction, self.bot)
        type_view = interaction.response.sent_view
        movies_button = next(b for b in type_view.children if b.custom_id == "wpm_database_add_type_movies")

        type_interaction = FakeInteraction(guild=guild, channel=channel)
        await movies_button.callback(interaction=type_interaction)
        return type_interaction

    async def test_choosing_a_standard_type_shows_the_destination_choice(self) -> None:
        type_interaction = await self._reach_destination_choice()

        self.assertIsInstance(type_interaction.response.edited_view, DestinationChoiceView)
        self.assertIn("Movie Suggestions", type_interaction.response.edited_content)

    async def test_create_new_thread_persists_the_collection_under_the_home_channel(self) -> None:
        home_channel = FakeHomeChannel(thread_id=777)
        guild = FakeGuild(home_channel)
        type_interaction = await self._reach_destination_choice(guild=guild)
        destination_view = type_interaction.response.edited_view
        create_thread_button = next(
            b for b in destination_view.children
            if b.custom_id == "wpm_database_admin_destination_create_thread"
        )

        destination_interaction = FakeInteraction(guild=guild)
        await create_thread_button.callback(interaction=destination_interaction)
        modal = destination_interaction.response.sent_modal
        modal.name_input._value = "Movie Suggestions"

        modal_interaction = FakeInteraction(guild=guild)
        await modal.on_submit(interaction=modal_interaction)

        self.assertEqual(home_channel.created_thread.id, 777)
        databases = self.suggestion_service.list_databases(GUILD_ID)
        self.assertEqual(len(databases), 1)
        self.assertEqual(databases[0].name, "Movie Suggestions")
        self.assertEqual(databases[0].channel_id, 777)
        self.assertIn("created", modal_interaction.response.edited_content)

    async def test_use_existing_thread_persists_the_collection(self) -> None:
        type_interaction = await self._reach_destination_choice()
        destination_view = type_interaction.response.edited_view
        existing_thread_button = next(
            b for b in destination_view.children
            if b.custom_id == "wpm_database_admin_destination_existing_thread"
        )

        destination_interaction = FakeInteraction()
        await existing_thread_button.callback(interaction=destination_interaction)
        select_view = destination_interaction.response.edited_view
        select = select_view.children[0]
        select._values = [type("FakeChannelValue", (), {"id": 888})()]

        select_interaction = FakeInteraction()
        await select.callback(interaction=select_interaction)

        databases = self.suggestion_service.list_databases(GUILD_ID)
        self.assertEqual(len(databases), 1)
        self.assertEqual(databases[0].channel_id, 888)

    async def test_use_existing_channel_persists_the_collection(self) -> None:
        type_interaction = await self._reach_destination_choice()
        destination_view = type_interaction.response.edited_view
        existing_channel_button = next(
            b for b in destination_view.children
            if b.custom_id == "wpm_database_admin_destination_existing_channel"
        )

        destination_interaction = FakeInteraction()
        await existing_channel_button.callback(interaction=destination_interaction)
        select_view = destination_interaction.response.edited_view
        select = select_view.children[0]
        select._values = [type("FakeChannelValue", (), {"id": 999})()]

        select_interaction = FakeInteraction()
        await select.callback(interaction=select_interaction)

        databases = self.suggestion_service.list_databases(GUILD_ID)
        self.assertEqual(len(databases), 1)
        self.assertEqual(databases[0].channel_id, 999)

    async def test_missing_home_channel_shows_an_error_and_offers_existing_options(self) -> None:
        self.guild_configuration_repository.save(
            GuildConfiguration(guild_id=GUILD_ID, guild_name="Test Guild", setup_completed=True)
        )
        type_interaction = await self._reach_destination_choice()
        destination_view = type_interaction.response.edited_view
        create_thread_button = next(
            b for b in destination_view.children
            if b.custom_id == "wpm_database_admin_destination_create_thread"
        )

        destination_interaction = FakeInteraction()
        await create_thread_button.callback(interaction=destination_interaction)

        self.assertIn("home channel", destination_interaction.response.edited_content)
        self.assertIsInstance(destination_interaction.response.edited_view, DestinationChoiceView)
        self.assertEqual(self.suggestion_service.list_databases(GUILD_ID), [])

    async def test_a_failed_creation_rolls_back_the_newly_created_thread(self) -> None:
        self.suggestion_service.create_database("Existing", guild_id=GUILD_ID, channel_id=200)
        home_channel = FakeHomeChannel(thread_id=777)
        guild = FakeGuild(home_channel)
        type_interaction = await self._reach_destination_choice(guild=guild)
        destination_view = type_interaction.response.edited_view
        create_thread_button = next(
            b for b in destination_view.children
            if b.custom_id == "wpm_database_admin_destination_create_thread"
        )
        destination_interaction = FakeInteraction(guild=guild)
        await create_thread_button.callback(interaction=destination_interaction)
        modal = destination_interaction.response.sent_modal
        # Force a duplicate-name failure by reusing the existing database's name.
        modal.name_input._value = "Existing"

        modal_interaction = FakeInteraction(guild=guild)
        await modal.on_submit(interaction=modal_interaction)

        self.assertTrue(home_channel.created_thread.deleted)
        self.assertEqual(len(self.suggestion_service.list_databases(GUILD_ID)), 1)


class UseCurrentThreadOrChannelTests(DatabaseAddCommandTestCase):
    """Command Structure Cleanup Refinement: /database add's destination
    choice offers "Use Current Thread/Channel" -- whatever the command
    was actually run in -- alongside Create New Thread/Use Existing
    Thread/Use Existing Channel.
    """

    async def _reach_destination_choice(self, *, channel=None):
        interaction = FakeInteraction(channel=channel)
        await handle_database_add(interaction, self.bot)
        type_view = interaction.response.sent_view
        movies_button = next(b for b in type_view.children if b.custom_id == "wpm_database_add_type_movies")

        type_interaction = FakeInteraction(channel=channel)
        await movies_button.callback(interaction=type_interaction)
        return type_interaction

    def _use_current_button(self, destination_view):
        return next(
            b for b in destination_view.children
            if b.custom_id == "wpm_database_admin_destination_use_current"
        )

    async def test_enabled_and_persists_when_invoked_from_a_thread(self) -> None:
        current_thread = FakeCurrentLocation(555, discord.ChannelType.public_thread)
        type_interaction = await self._reach_destination_choice(channel=current_thread)
        destination_view = type_interaction.response.edited_view
        use_current_button = self._use_current_button(destination_view)
        self.assertFalse(use_current_button.disabled)

        destination_interaction = FakeInteraction(channel=current_thread)
        await use_current_button.callback(interaction=destination_interaction)

        databases = self.suggestion_service.list_databases(GUILD_ID)
        self.assertEqual(len(databases), 1)
        self.assertEqual(databases[0].channel_id, 555)
        self.assertIn("created", destination_interaction.response.edited_content)

    async def test_enabled_and_persists_when_invoked_from_a_text_channel(self) -> None:
        current_channel = FakeCurrentLocation(556, discord.ChannelType.text)
        type_interaction = await self._reach_destination_choice(channel=current_channel)
        destination_view = type_interaction.response.edited_view
        use_current_button = self._use_current_button(destination_view)
        self.assertFalse(use_current_button.disabled)

        destination_interaction = FakeInteraction(channel=current_channel)
        await use_current_button.callback(interaction=destination_interaction)

        databases = self.suggestion_service.list_databases(GUILD_ID)
        self.assertEqual(len(databases), 1)
        self.assertEqual(databases[0].channel_id, 556)

    async def test_disabled_when_invoked_somewhere_unsuitable(self) -> None:
        unsuitable_location = FakeCurrentLocation(557, discord.ChannelType.voice)
        type_interaction = await self._reach_destination_choice(channel=unsuitable_location)
        destination_view = type_interaction.response.edited_view

        use_current_button = self._use_current_button(destination_view)

        self.assertTrue(use_current_button.disabled)

    async def test_disabled_when_no_current_location_is_known(self) -> None:
        type_interaction = await self._reach_destination_choice(channel=None)
        destination_view = type_interaction.response.edited_view

        use_current_button = self._use_current_button(destination_view)

        self.assertTrue(use_current_button.disabled)

    async def test_a_stale_click_on_a_disabled_option_shows_a_clear_error(self) -> None:
        # Defense in depth: even though the button is disabled, confirm
        # the callback itself refuses to proceed if somehow invoked.
        unsuitable_location = FakeCurrentLocation(557, discord.ChannelType.voice)
        type_interaction = await self._reach_destination_choice(channel=unsuitable_location)
        destination_view = type_interaction.response.edited_view
        use_current_button = self._use_current_button(destination_view)

        destination_interaction = FakeInteraction(channel=unsuitable_location)
        await use_current_button.callback(interaction=destination_interaction)

        self.assertIn("isn't available here", destination_interaction.response.edited_content)
        self.assertEqual(self.suggestion_service.list_databases(GUILD_ID), [])

    async def test_use_current_does_not_roll_back_anything_since_nothing_is_created(self) -> None:
        # Use Current never creates a Discord resource, so a failure here
        # (e.g. a duplicate name) has nothing to roll back -- confirm it
        # simply reports the failure.
        self.suggestion_service.create_database("Existing", guild_id=GUILD_ID, channel_id=200)
        current_channel = FakeCurrentLocation(558, discord.ChannelType.text)
        interaction = FakeInteraction(channel=current_channel)
        await handle_database_add(interaction, self.bot)
        type_view = interaction.response.sent_view
        custom_button = next(b for b in type_view.children if b.custom_id == "wpm_database_add_type_custom")
        type_interaction = FakeInteraction(channel=current_channel)
        await custom_button.callback(interaction=type_interaction)
        modal = type_interaction.response.sent_modal
        modal.name_input._value = "Existing"
        modal_interaction = FakeInteraction(channel=current_channel)
        await modal.on_submit(interaction=modal_interaction)
        destination_view = modal_interaction.response.edited_view
        use_current_button = self._use_current_button(destination_view)

        destination_interaction = FakeInteraction(channel=current_channel)
        await use_current_button.callback(interaction=destination_interaction)

        self.assertIn("already exists", destination_interaction.response.edited_content)
        self.assertEqual(len(self.suggestion_service.list_databases(GUILD_ID)), 1)


class CreateNewThreadFallbackTests(DatabaseAddCommandTestCase):
    """Create New Thread Improvement (polish batch): when no Home Channel
    is configured (or it's no longer available), Create New Thread falls
    back to creating the new thread under the current channel instead of
    presenting an option that can never succeed -- but only when the
    current channel is actually eligible to parent one (Discord doesn't
    allow nesting a thread under another thread).
    """

    def setUp(self) -> None:
        super().setUp()
        # No home_channel_id configured at all -- the unconfigured case.
        self.guild_configuration_repository.save(
            GuildConfiguration(guild_id=GUILD_ID, guild_name="Test Guild", setup_completed=True)
        )

    async def _reach_destination_choice(self, *, guild=None, channel=None):
        interaction = FakeInteraction(guild=guild, channel=channel)
        await handle_database_add(interaction, self.bot)
        type_view = interaction.response.sent_view
        movies_button = next(b for b in type_view.children if b.custom_id == "wpm_database_add_type_movies")

        type_interaction = FakeInteraction(guild=guild, channel=channel)
        await movies_button.callback(interaction=type_interaction)
        return type_interaction

    def _create_thread_button(self, destination_view):
        return next(
            b for b in destination_view.children
            if b.custom_id == "wpm_database_admin_destination_create_thread"
        )

    async def test_falls_back_to_the_current_text_channel(self) -> None:
        current_channel = FakeCurrentLocation(556, discord.ChannelType.text, new_thread_id=888)
        guild = FakeGuild()
        type_interaction = await self._reach_destination_choice(guild=guild, channel=current_channel)
        destination_view = type_interaction.response.edited_view
        create_thread_button = self._create_thread_button(destination_view)
        self.assertFalse(create_thread_button.disabled)
        self.assertIn("here in this channel instead", type_interaction.response.edited_content)

        destination_interaction = FakeInteraction(guild=guild, channel=current_channel)
        await create_thread_button.callback(interaction=destination_interaction)
        modal = destination_interaction.response.sent_modal

        modal_interaction = FakeInteraction(guild=guild, channel=current_channel)
        await modal.on_submit(interaction=modal_interaction)

        self.assertEqual(current_channel.created_thread.id, 888)
        databases = self.suggestion_service.list_databases(GUILD_ID)
        self.assertEqual(len(databases), 1)
        self.assertEqual(databases[0].channel_id, 888)

    async def test_disabled_when_current_location_cannot_parent_a_thread_either(self) -> None:
        current_thread = FakeCurrentLocation(557, discord.ChannelType.public_thread)
        guild = FakeGuild()
        type_interaction = await self._reach_destination_choice(guild=guild, channel=current_thread)
        destination_view = type_interaction.response.edited_view

        create_thread_button = self._create_thread_button(destination_view)

        self.assertTrue(create_thread_button.disabled)
        self.assertNotIn("here in this channel instead", type_interaction.response.edited_content)

    async def test_disabled_when_no_current_location_is_known_either(self) -> None:
        guild = FakeGuild()
        type_interaction = await self._reach_destination_choice(guild=guild, channel=None)
        destination_view = type_interaction.response.edited_view

        create_thread_button = self._create_thread_button(destination_view)

        self.assertTrue(create_thread_button.disabled)

    async def test_a_stale_click_on_the_disabled_button_shows_a_clear_error(self) -> None:
        current_thread = FakeCurrentLocation(557, discord.ChannelType.public_thread)
        guild = FakeGuild()
        type_interaction = await self._reach_destination_choice(guild=guild, channel=current_thread)
        destination_view = type_interaction.response.edited_view
        create_thread_button = self._create_thread_button(destination_view)

        destination_interaction = FakeInteraction(guild=guild, channel=current_thread)
        await create_thread_button.callback(interaction=destination_interaction)

        self.assertIn("nowhere to create a new thread", destination_interaction.response.edited_content)
        self.assertEqual(self.suggestion_service.list_databases(GUILD_ID), [])


class CustomTypeTests(DatabaseAddCommandTestCase):
    async def test_custom_type_collects_a_name_via_modal_then_shows_destination_choice(self) -> None:
        interaction = FakeInteraction()
        await handle_database_add(interaction, self.bot)
        type_view = interaction.response.sent_view
        custom_button = next(b for b in type_view.children if b.custom_id == "wpm_database_add_type_custom")

        type_interaction = FakeInteraction()
        await custom_button.callback(interaction=type_interaction)
        modal = type_interaction.response.sent_modal
        modal.name_input._value = "Book Club Adaptations"

        modal_interaction = FakeInteraction()
        await modal.on_submit(interaction=modal_interaction)

        self.assertIsInstance(modal_interaction.response.edited_view, DestinationChoiceView)
        self.assertIn("Book Club Adaptations", modal_interaction.response.edited_content)


if __name__ == "__main__":
    unittest.main()
