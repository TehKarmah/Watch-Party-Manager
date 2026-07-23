"""Tests for FR-029's /config wiring in bot.py.

Covers the pure/testable pieces bot.py adds for /config: the WASH-only
permission gate (via PermissionService, the same shared gate every other
administrative command already uses), the main menu and per-section
rendering (send_config_main_menu / send_config_section), the WASH Crew
Role "you'd lose access" confirmation flow, and the three modal-based
defaults sections -- exercised with fake interactions instead of a live
Discord connection, mirroring test_setup_command.py's FakeInteraction/
FakeResponse pattern.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from watch_party_manager.bot import (
    handle_config_wash_crew_role_selected,
    send_config_backup_defaults_modal,
    send_config_main_menu,
    send_config_reminder_defaults_modal,
    send_config_result,
    send_config_section,
    send_config_voting_defaults_modal,
)
from watch_party_manager.domain.guild_configuration import GuildConfiguration
from watch_party_manager.persistence.guild_configuration_repository import GuildConfigurationRepository
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.config_service import ConfigSection, ConfigService
from watch_party_manager.services.permission_service import PermissionService
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.config_view import (
    ConfigDatabaseSectionView,
    ConfigJoinModeSectionView,
    ConfigMainMenuView,
    ConfigRoleSectionView,
    ConfigWatchDestinationSectionView,
)

GUILD_ID = 100
WASH_CREW_ROLE_ID = 111
WATCH_PARTY_ROLE_ID = 222
DESTINATION_CHANNEL_ID = 400


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, roles=()) -> None:
        self.roles = list(roles)


class FakePermissions:
    def __init__(self, view_channel: bool = True, send_messages: bool = True) -> None:
        self.view_channel = view_channel
        self.send_messages = send_messages


class FakeChannel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id

    def permissions_for(self, member) -> FakePermissions:
        return FakePermissions()


class _FakeChannelValue:
    """Stands in for the discord.abc.GuildChannel a ChannelSelect hands
    back in `.values` -- only `.id` is read by DestinationChannelSelect.
    """

    def __init__(self, channel_id: int) -> None:
        self.id = channel_id


class FakeGuildForValidation:
    def __init__(self, *, role_ids=(), channel_ids=()) -> None:
        self._role_ids = set(role_ids)
        self._channels = {channel_id: FakeChannel(channel_id) for channel_id in channel_ids}
        self.me = object()

    def get_role(self, role_id):
        return FakeRole(role_id) if role_id in self._role_ids else None

    def get_channel_or_thread(self, channel_id):
        return self._channels.get(channel_id)


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


class FakeInteraction:
    def __init__(self, user=None, guild=None) -> None:
        self.user = user if user is not None else FakeMember()
        self.response = FakeResponse()
        self.guild = guild if guild is not None else FakeGuildForValidation(
            role_ids={WASH_CREW_ROLE_ID, WATCH_PARTY_ROLE_ID}, channel_ids={DESTINATION_CHANNEL_ID}
        )


class ConfigCommandTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        temp_path = Path(self._temp_dir.name)

        self.guild_configuration_repository = GuildConfigurationRepository(
            temp_path / "guild_configurations.json"
        )
        self.suggestion_database_configuration_repository = SuggestionDatabaseConfigurationRepository(
            temp_path / "suggestion_database_configurations.json"
        )
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(temp_path / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(temp_path / "suggestion_databases.json"),
        )

        class FakeBot:
            pass

        self.bot = FakeBot()
        self.bot.suggestion_service = self.suggestion_service
        self.bot.suggestion_database_configuration_repository = self.suggestion_database_configuration_repository
        self.bot.config_service = ConfigService(
            self.guild_configuration_repository, self.suggestion_service, self.suggestion_database_configuration_repository
        )
        self.bot.wash_crew_role_id = WASH_CREW_ROLE_ID
        self.bot.watch_party_member_role_id = WATCH_PARTY_ROLE_ID
        self.bot.permission_service = PermissionService(
            watch_party_member_role_id=WATCH_PARTY_ROLE_ID, wash_crew_role_id=WASH_CREW_ROLE_ID
        )
        self.applied_roles = []
        self.bot.apply_role_configuration = lambda wash, watch: self.applied_roles.append((wash, watch))

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _seed_completed_setup(self, **overrides) -> None:
        self.guild_configuration_repository.save(
            GuildConfiguration(guild_id=GUILD_ID, guild_name="Test Guild", setup_completed=True, **overrides)
        )


class ConfigPermissionTests(unittest.TestCase):
    """FR-029's checklist: WASH Crew can use /config, Watch Party member
    cannot, unprivileged cannot, permission fails closed when WASH Crew
    is unconfigured. /config reuses PermissionService.require_wash_crew
    unconditionally (no bootstrapping exception, unlike /setup)."""

    def test_wash_crew_member_is_allowed(self) -> None:
        service = PermissionService(watch_party_member_role_id=WATCH_PARTY_ROLE_ID, wash_crew_role_id=WASH_CREW_ROLE_ID)
        result = service.require_wash_crew(FakeMember(roles=[FakeRole(WASH_CREW_ROLE_ID)]))
        self.assertTrue(result.allowed)

    def test_watch_party_member_is_blocked(self) -> None:
        service = PermissionService(watch_party_member_role_id=WATCH_PARTY_ROLE_ID, wash_crew_role_id=WASH_CREW_ROLE_ID)
        result = service.require_wash_crew(FakeMember(roles=[FakeRole(WATCH_PARTY_ROLE_ID)]))
        self.assertFalse(result.allowed)

    def test_unprivileged_user_is_blocked(self) -> None:
        service = PermissionService(watch_party_member_role_id=WATCH_PARTY_ROLE_ID, wash_crew_role_id=WASH_CREW_ROLE_ID)
        result = service.require_wash_crew(FakeMember(roles=[]))
        self.assertFalse(result.allowed)

    def test_fails_closed_when_wash_crew_role_is_unconfigured(self) -> None:
        service = PermissionService(watch_party_member_role_id=WATCH_PARTY_ROLE_ID, wash_crew_role_id=None)
        result = service.require_wash_crew(FakeMember(roles=[FakeRole(WASH_CREW_ROLE_ID)]))
        self.assertFalse(result.allowed)


class MainMenuTests(ConfigCommandTestCase):
    async def test_sends_summary_and_menu_as_a_new_ephemeral_message(self) -> None:
        self._seed_completed_setup(wash_crew_role_id=WASH_CREW_ROLE_ID)
        interaction = FakeInteraction()

        await send_config_main_menu(interaction, self.bot, GUILD_ID, edit=False)

        self.assertIn("WASH Configuration", interaction.response.sent_message)
        self.assertIn(f"<@&{WASH_CREW_ROLE_ID}>", interaction.response.sent_message)
        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIsInstance(interaction.response.sent_view, ConfigMainMenuView)

    async def test_selecting_a_section_edits_into_that_sections_screen(self) -> None:
        self._seed_completed_setup(wash_crew_role_id=WASH_CREW_ROLE_ID)
        interaction = FakeInteraction()
        await send_config_main_menu(interaction, self.bot, GUILD_ID, edit=False)
        view: ConfigMainMenuView = interaction.response.sent_view
        select = view.children[0]
        select._values = ["wash_crew_role"]

        select_interaction = FakeInteraction()
        await select.callback(interaction=select_interaction)

        self.assertIn("WASH Crew Role", select_interaction.response.edited_content)
        self.assertIsInstance(select_interaction.response.edited_view, ConfigRoleSectionView)


class SectionRenderingTests(ConfigCommandTestCase):
    async def test_watch_party_role_section_shows_the_role_picker(self) -> None:
        self._seed_completed_setup()
        interaction = FakeInteraction()
        await send_config_section(interaction, self.bot, GUILD_ID, ConfigSection.WATCH_PARTY_ROLE, edit=False)
        self.assertIsInstance(interaction.response.sent_view, ConfigRoleSectionView)
        self.assertEqual(interaction.response.sent_view.children[0].min_values, 0)

    async def test_join_mode_section_shows_the_join_mode_picker(self) -> None:
        self._seed_completed_setup()
        interaction = FakeInteraction()
        await send_config_section(interaction, self.bot, GUILD_ID, ConfigSection.WATCH_PARTY_JOIN_MODE, edit=False)
        self.assertIsInstance(interaction.response.sent_view, ConfigJoinModeSectionView)

    async def test_admin_channel_section_shows_the_channel_picker(self) -> None:
        from watch_party_manager.config_view import ConfigAdminChannelSectionView

        self._seed_completed_setup()
        interaction = FakeInteraction()
        await send_config_section(interaction, self.bot, GUILD_ID, ConfigSection.ADMIN_CHANNEL, edit=False)
        self.assertIsInstance(interaction.response.sent_view, ConfigAdminChannelSectionView)

    async def test_selecting_an_admin_channel_saves_immediately(self) -> None:
        self._seed_completed_setup()
        interaction = FakeInteraction()
        await send_config_section(interaction, self.bot, GUILD_ID, ConfigSection.ADMIN_CHANNEL, edit=False)
        select = interaction.response.sent_view.children[0]
        select._values = [_FakeChannelValue(DESTINATION_CHANNEL_ID)]

        select_interaction = FakeInteraction()
        await select.callback(interaction=select_interaction)

        self.assertIn("Admin channel updated", select_interaction.response.edited_content)
        self.assertEqual(
            self.guild_configuration_repository.get(GUILD_ID).channels.admin_channel_id, DESTINATION_CHANNEL_ID
        )

    async def test_database_section_lists_existing_databases(self) -> None:
        self._seed_completed_setup()
        self.suggestion_service.create_database("Movies", GUILD_ID, DESTINATION_CHANNEL_ID)
        interaction = FakeInteraction()
        await send_config_section(interaction, self.bot, GUILD_ID, ConfigSection.SUGGESTION_DATABASE, edit=False)
        self.assertIsInstance(interaction.response.sent_view, ConfigDatabaseSectionView)

    async def test_database_section_with_no_databases_shows_back_only(self) -> None:
        self._seed_completed_setup()
        interaction = FakeInteraction()
        await send_config_section(interaction, self.bot, GUILD_ID, ConfigSection.SUGGESTION_DATABASE, edit=False)
        self.assertEqual(len(interaction.response.sent_view.children), 1)

    async def test_watch_destination_section_shows_the_channel_picker(self) -> None:
        self._seed_completed_setup()
        interaction = FakeInteraction()
        await send_config_section(interaction, self.bot, GUILD_ID, ConfigSection.WATCH_DESTINATION, edit=False)
        self.assertIsInstance(interaction.response.sent_view, ConfigWatchDestinationSectionView)

    async def test_selecting_a_database_saves_immediately_and_shows_result(self) -> None:
        self._seed_completed_setup()
        database_result = self.suggestion_service.create_database("Movies", GUILD_ID, DESTINATION_CHANNEL_ID)
        interaction = FakeInteraction()
        await send_config_section(interaction, self.bot, GUILD_ID, ConfigSection.SUGGESTION_DATABASE, edit=False)
        select = interaction.response.sent_view.children[0]
        select._values = [str(database_result.database.database_id)]

        select_interaction = FakeInteraction()
        await select.callback(interaction=select_interaction)

        self.assertIn("active suggestion database", select_interaction.response.edited_content)


class WashCrewRoleConfirmationTests(ConfigCommandTestCase):
    async def test_saves_immediately_when_the_acting_member_already_has_the_new_role(self) -> None:
        self._seed_completed_setup(wash_crew_role_id=WASH_CREW_ROLE_ID)
        new_role_id = 333
        interaction = FakeInteraction(
            user=FakeMember(roles=[FakeRole(WASH_CREW_ROLE_ID), FakeRole(new_role_id)]),
            guild=FakeGuildForValidation(role_ids={WASH_CREW_ROLE_ID, new_role_id}),
        )

        await handle_config_wash_crew_role_selected(interaction, self.bot, GUILD_ID, new_role_id)

        self.assertIn(f"<@&{new_role_id}>", interaction.response.edited_content)
        self.assertEqual(
            self.guild_configuration_repository.get(GUILD_ID).wash_crew_role_id, new_role_id
        )
        self.assertEqual(self.applied_roles, [(new_role_id, WATCH_PARTY_ROLE_ID)])

    async def test_warns_and_requires_confirmation_when_the_acting_member_lacks_the_new_role(self) -> None:
        self._seed_completed_setup(wash_crew_role_id=WASH_CREW_ROLE_ID)
        new_role_id = 333
        interaction = FakeInteraction(
            user=FakeMember(roles=[FakeRole(WASH_CREW_ROLE_ID)]),
            guild=FakeGuildForValidation(role_ids={WASH_CREW_ROLE_ID, new_role_id}),
        )

        await handle_config_wash_crew_role_selected(interaction, self.bot, GUILD_ID, new_role_id)

        self.assertIn("Continue anyway", interaction.response.edited_content)
        # Nothing was saved yet -- the role must be preserved until confirmed.
        self.assertEqual(self.guild_configuration_repository.get(GUILD_ID).wash_crew_role_id, WASH_CREW_ROLE_ID)
        self.assertEqual(self.applied_roles, [])

    async def test_confirming_the_warning_saves_the_change(self) -> None:
        self._seed_completed_setup(wash_crew_role_id=WASH_CREW_ROLE_ID)
        new_role_id = 333
        guild = FakeGuildForValidation(role_ids={WASH_CREW_ROLE_ID, new_role_id})
        interaction = FakeInteraction(user=FakeMember(roles=[FakeRole(WASH_CREW_ROLE_ID)]), guild=guild)
        await handle_config_wash_crew_role_selected(interaction, self.bot, GUILD_ID, new_role_id)
        confirmation_view = interaction.response.edited_view
        confirm_button = confirmation_view.children[0]

        confirm_interaction = FakeInteraction(guild=guild)
        await confirm_button.callback(interaction=confirm_interaction)

        self.assertEqual(self.guild_configuration_repository.get(GUILD_ID).wash_crew_role_id, new_role_id)
        self.assertEqual(self.applied_roles, [(new_role_id, WATCH_PARTY_ROLE_ID)])

    async def test_aborting_the_warning_preserves_the_existing_role(self) -> None:
        self._seed_completed_setup(wash_crew_role_id=WASH_CREW_ROLE_ID)
        new_role_id = 333
        guild = FakeGuildForValidation(role_ids={WASH_CREW_ROLE_ID, new_role_id})
        interaction = FakeInteraction(user=FakeMember(roles=[FakeRole(WASH_CREW_ROLE_ID)]), guild=guild)
        await handle_config_wash_crew_role_selected(interaction, self.bot, GUILD_ID, new_role_id)
        confirmation_view = interaction.response.edited_view
        abort_button = confirmation_view.children[1]

        abort_interaction = FakeInteraction(guild=guild)
        await abort_button.callback(interaction=abort_interaction)

        self.assertIn("not changed", abort_interaction.response.edited_content)
        self.assertEqual(self.guild_configuration_repository.get(GUILD_ID).wash_crew_role_id, WASH_CREW_ROLE_ID)
        self.assertEqual(self.applied_roles, [])

    async def test_invalid_replacement_role_is_rejected(self) -> None:
        self._seed_completed_setup(wash_crew_role_id=WASH_CREW_ROLE_ID)
        interaction = FakeInteraction(
            user=FakeMember(roles=[FakeRole(WASH_CREW_ROLE_ID), FakeRole(999999)]),
            guild=FakeGuildForValidation(role_ids={WASH_CREW_ROLE_ID}),
        )

        await handle_config_wash_crew_role_selected(interaction, self.bot, GUILD_ID, 999999)

        self.assertIn("no longer exists", interaction.response.edited_content)
        self.assertEqual(self.guild_configuration_repository.get(GUILD_ID).wash_crew_role_id, WASH_CREW_ROLE_ID)


class ModalDefaultsSectionTests(ConfigCommandTestCase):
    async def test_voting_defaults_modal_is_prefilled_with_current_values(self) -> None:
        self._seed_completed_setup()
        interaction = FakeInteraction()

        async def on_back(back_interaction) -> None:
            pass

        await send_config_voting_defaults_modal(interaction, self.bot, GUILD_ID, on_back)

        modal = interaction.response.sent_modal
        self.assertEqual(modal.candidate_count_input.default, "3")
        self.assertEqual(modal.duration_days_input.default, "7")
        self.assertEqual(modal.visibility_input.default, "blind")

    async def test_voting_defaults_submission_saves_and_shows_result(self) -> None:
        self._seed_completed_setup()
        interaction = FakeInteraction()

        async def on_back(back_interaction) -> None:
            pass

        await send_config_voting_defaults_modal(interaction, self.bot, GUILD_ID, on_back)
        modal = interaction.response.sent_modal
        modal.candidate_count_input._value = "5"
        modal.duration_days_input._value = "14"
        modal.visibility_input._value = "visible"
        modal.candidate_selection_input._value = "rotation_pool"

        submit_interaction = FakeInteraction()
        await modal.on_submit(interaction=submit_interaction)

        self.assertIn("Voting defaults updated", submit_interaction.response.edited_content)
        voting_defaults = self.guild_configuration_repository.get(GUILD_ID).voting_defaults
        self.assertEqual(voting_defaults.candidate_count, 5)

    async def test_voting_defaults_submission_with_invalid_value_shows_retry(self) -> None:
        self._seed_completed_setup()
        interaction = FakeInteraction()

        async def on_back(back_interaction) -> None:
            pass

        await send_config_voting_defaults_modal(interaction, self.bot, GUILD_ID, on_back)
        modal = interaction.response.sent_modal
        modal.candidate_count_input._value = "not-a-number"
        modal.duration_days_input._value = "14"
        modal.visibility_input._value = "visible"
        modal.candidate_selection_input._value = "rotation_pool"

        submit_interaction = FakeInteraction()
        await modal.on_submit(interaction=submit_interaction)

        self.assertIn("must be a whole number", submit_interaction.response.edited_content)
        # Nothing was saved.
        self.assertEqual(self.guild_configuration_repository.get(GUILD_ID).voting_defaults.candidate_count, 3)

    async def test_reminder_defaults_modal_is_prefilled_with_current_values(self) -> None:
        self._seed_completed_setup()
        interaction = FakeInteraction()

        async def on_back(back_interaction) -> None:
            pass

        await send_config_reminder_defaults_modal(interaction, self.bot, GUILD_ID, on_back)
        modal = interaction.response.sent_modal
        self.assertEqual(modal.enabled_input.default, "yes")
        self.assertEqual(modal.hours_input.default, "24")

    async def test_reminder_defaults_submission_saves(self) -> None:
        self._seed_completed_setup()
        interaction = FakeInteraction()

        async def on_back(back_interaction) -> None:
            pass

        await send_config_reminder_defaults_modal(interaction, self.bot, GUILD_ID, on_back)
        modal = interaction.response.sent_modal
        modal.enabled_input._value = "no"
        modal.hours_input._value = "24"

        submit_interaction = FakeInteraction()
        await modal.on_submit(interaction=submit_interaction)

        self.assertFalse(self.guild_configuration_repository.get(GUILD_ID).notifications.vote.vote_ending_reminder)

    async def test_backup_defaults_modal_is_prefilled_with_current_values(self) -> None:
        self._seed_completed_setup()
        interaction = FakeInteraction()

        async def on_back(back_interaction) -> None:
            pass

        await send_config_backup_defaults_modal(interaction, self.bot, GUILD_ID, on_back)
        modal = interaction.response.sent_modal
        self.assertEqual(modal.interval_input.default, "1")
        self.assertEqual(modal.retention_input.default, "30")

    async def test_backup_defaults_submission_saves(self) -> None:
        self._seed_completed_setup()
        interaction = FakeInteraction()

        async def on_back(back_interaction) -> None:
            pass

        await send_config_backup_defaults_modal(interaction, self.bot, GUILD_ID, on_back)
        modal = interaction.response.sent_modal
        modal.interval_input._value = "5"
        modal.retention_input._value = "60"

        submit_interaction = FakeInteraction()
        await modal.on_submit(interaction=submit_interaction)

        backup = self.guild_configuration_repository.get(GUILD_ID).backup
        self.assertEqual(backup.extra_fields["automatic_backup_interval_days"], 5)
        self.assertEqual(backup.extra_fields["backup_retention_count"], 60)

    async def test_backup_defaults_submission_with_invalid_value_shows_retry(self) -> None:
        self._seed_completed_setup()
        interaction = FakeInteraction()

        async def on_back(back_interaction) -> None:
            pass

        await send_config_backup_defaults_modal(interaction, self.bot, GUILD_ID, on_back)
        modal = interaction.response.sent_modal
        modal.interval_input._value = "0"
        modal.retention_input._value = "60"

        submit_interaction = FakeInteraction()
        await modal.on_submit(interaction=submit_interaction)

        self.assertIn("must be between", submit_interaction.response.edited_content)


class SendConfigResultTests(ConfigCommandTestCase):
    async def test_success_result_shows_message_and_back_button(self) -> None:
        from watch_party_manager.services.config_service import ConfigUpdateResult

        interaction = FakeInteraction()
        await send_config_result(interaction, self.bot, GUILD_ID, ConfigUpdateResult(True, "All good."))
        self.assertEqual(interaction.response.edited_content, "All good.")
        self.assertEqual(len(interaction.response.edited_view.children), 1)

    async def test_failure_result_shows_warning_prefix(self) -> None:
        from watch_party_manager.services.config_service import ConfigUpdateResult

        interaction = FakeInteraction()
        await send_config_result(interaction, self.bot, GUILD_ID, ConfigUpdateResult(False, "Nope."))
        self.assertIn("Nope.", interaction.response.edited_content)
        self.assertTrue(interaction.response.edited_content.startswith("⚠"))


if __name__ == "__main__":
    unittest.main()
