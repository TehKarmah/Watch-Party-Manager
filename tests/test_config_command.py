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

import discord

from watch_party_manager.bot import (
    SETUP_WIZARD_STEP_TITLES,
    build_config_section_options,
    build_config_summary_body,
    handle_config_wash_crew_role_selected,
    send_config_backup_defaults_modal,
    send_config_main_menu,
    send_config_manage_databases,
    send_config_reminder_defaults_modal,
    send_config_result,
    send_config_section,
    send_config_voting_defaults_modal,
)
from watch_party_manager.domain.guild_configuration import GuildChannelsConfig, GuildConfiguration, GuildVoteVisibility
from watch_party_manager.domain.setup_wizard import SETUP_WIZARD_STEP_ORDER, SetupWizardStep
from watch_party_manager.domain.suggestion_database_configuration import CandidateSelectionMode
from watch_party_manager.persistence.guild_configuration_repository import GuildConfigurationRepository
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.scheduler.scheduled_job import JobResult, JobStatus, ScheduledJob
from watch_party_manager.scheduler.scheduler_service import SchedulerService
from watch_party_manager.services.config_service import CONFIG_SECTION_ORDER, CONFIG_SECTION_TITLES, ConfigSection, ConfigService
from watch_party_manager.services.permission_service import PermissionService


class MemorySchedulerRepository:
    """In-memory SchedulerRepository fake, matching the project's other tests."""

    def __init__(self) -> None:
        self.jobs: dict[str, ScheduledJob] = {}

    async def add(self, job: ScheduledJob) -> ScheduledJob:
        self.jobs[job.job_id] = job
        return job

    async def get_due(self, now, *, limit: int = 100) -> list[ScheduledJob]:
        return [
            job for job in self.jobs.values() if job.status is JobStatus.PENDING and job.run_at <= now
        ][:limit]

    async def claim(self, job_id: str, started_at) -> ScheduledJob | None:
        job = self.jobs[job_id]
        if job.status is not JobStatus.PENDING:
            return None
        claimed = job.with_changes(status=JobStatus.RUNNING, started_at=started_at, attempt_count=job.attempt_count + 1)
        self.jobs[job_id] = claimed
        return claimed

    async def complete(self, job_id: str, completed_at, result: JobResult) -> ScheduledJob:
        updated = self.jobs[job_id].with_changes(status=JobStatus.COMPLETED, completed_at=completed_at, result=result, last_error=None)
        self.jobs[job_id] = updated
        return updated

    async def retry(self, job_id: str, run_at, error: str) -> ScheduledJob:
        updated = self.jobs[job_id].with_changes(status=JobStatus.PENDING, run_at=run_at, last_error=error)
        self.jobs[job_id] = updated
        return updated

    async def fail(self, job_id: str, completed_at, error: str) -> ScheduledJob:
        updated = self.jobs[job_id].with_changes(status=JobStatus.FAILED, completed_at=completed_at, last_error=error)
        self.jobs[job_id] = updated
        return updated

    async def cancel(self, job_id: str, completed_at) -> ScheduledJob:
        updated = self.jobs[job_id].with_changes(status=JobStatus.CANCELLED, completed_at=completed_at, result=JobResult.CANCELLED)
        self.jobs[job_id] = updated
        return updated

    async def find_active_by_logical_key(self, logical_key: str) -> ScheduledJob | None:
        return next((job for job in self.jobs.values() if job.logical_key == logical_key and job.is_active), None)

    async def find_active_by_guild_and_type(self, guild_id: int, job_type: str) -> ScheduledJob | None:
        return next(
            (job for job in self.jobs.values() if job.guild_id == guild_id and job.job_type == job_type and job.is_active),
            None,
        )


class FakeSchedulerHost:
    def __init__(self, scheduler_service: SchedulerService) -> None:
        self.scheduler_service = scheduler_service
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.config_view import (
    BackToMenuOnlyView,
    ConfigDatabaseCandidateSelectionView,
    ConfigDatabaseSectionView,
    ConfigDatabaseSettingsMenuView,
    ConfigJoinModeSectionView,
    ConfigMainMenuView,
    ConfigRoleSectionView,
    ConfigSuggestionDestinationSectionView,
    ConfigVotingDefaultsIntroView,
    ConfigWatchDestinationSectionView,
    DATABASE_SETTING_CANDIDATE_SELECTION,
    DATABASE_SETTING_SUGGESTION_DESTINATION,
    DATABASE_SETTING_WATCH_DESTINATION,
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
    def __init__(self, channel_id: int, *, permissions: FakePermissions = None) -> None:
        self.id = channel_id
        self._permissions = permissions or FakePermissions()

    def permissions_for(self, member) -> FakePermissions:
        return self._permissions


class _FakeChannelValue:
    """Stands in for the discord.abc.GuildChannel a ChannelSelect hands
    back in `.values` -- only `.id` is read by DestinationChannelSelect.
    """

    def __init__(self, channel_id: int) -> None:
        self.id = channel_id


class FakeCreatedChannel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id


class FakeHTTPResponse:
    status = 403
    reason = "Forbidden"


class FakeGuildForValidation:
    def __init__(self, *, role_ids=(), channel_ids=(), new_channel_id=None, fail_create: bool = False) -> None:
        self._role_ids = set(role_ids)
        self._channels = {channel_id: FakeChannel(channel_id) for channel_id in channel_ids}
        self._new_channel_id = new_channel_id
        self._fail_create = fail_create
        self.created_channel: "FakeCreatedChannel | None" = None
        self.me = object()

    def get_role(self, role_id):
        return FakeRole(role_id) if role_id in self._role_ids else None

    def get_channel_or_thread(self, channel_id):
        return self._channels.get(channel_id)

    async def create_text_channel(self, *, name):
        if self._fail_create:
            raise discord.HTTPException(response=FakeHTTPResponse(), message="boom")
        channel_id = self._new_channel_id if self._new_channel_id is not None else 999999
        self.created_channel = FakeCreatedChannel(channel_id)
        self._channels[channel_id] = FakeChannel(channel_id)
        return self.created_channel


class FakeCurrentChannel:
    """A minimal stand-in for the channel/thread /config was run in."""

    def __init__(self, channel_id: int, channel_type) -> None:
        self.id = channel_id
        self.type = channel_type


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
    def __init__(self, user=None, guild=None, channel=None) -> None:
        self.user = user if user is not None else FakeMember()
        self.response = FakeResponse()
        self.guild = guild if guild is not None else FakeGuildForValidation(
            role_ids={WASH_CREW_ROLE_ID, WATCH_PARTY_ROLE_ID}, channel_ids={DESTINATION_CHANNEL_ID}
        )
        self.channel = channel


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
        self.bot.guild_configuration_repository = self.guild_configuration_repository
        self.scheduler_repository = MemorySchedulerRepository()
        self.bot.scheduler_host = FakeSchedulerHost(SchedulerService(self.scheduler_repository))
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

    async def test_voting_defaults_option_explains_visibility(self) -> None:
        # UI Polish (Visibility Help): since Voting Defaults opens its
        # modal directly with no intro screen, the Visible/Blind
        # explanation lives on this menu option's description instead.
        self._seed_completed_setup(wash_crew_role_id=WASH_CREW_ROLE_ID)
        interaction = FakeInteraction()

        await send_config_main_menu(interaction, self.bot, GUILD_ID, edit=False)

        view: ConfigMainMenuView = interaction.response.sent_view
        select = view.children[0]
        options_by_value = {option.value: option for option in select.options}
        self.assertIsNotNone(options_by_value["voting_defaults"].description)
        self.assertIn("Blind", options_by_value["voting_defaults"].description)
        self.assertIsNone(options_by_value["wash_crew_role"].description)


class ConfigSectionNumberingTests(ConfigCommandTestCase):
    """Consistent Step Numbering: /config's sections are numbered the same
    way the Setup Wizard numbers its own steps -- CONFIG_SECTION_ORDER is
    /config's equivalent of SETUP_WIZARD_STEP_ORDER, so a section's
    position in that tuple (1-based) is its step number everywhere /config
    shows it: the main-menu summary and the section dropdown.
    """

    def test_summary_lines_are_numbered_in_order(self) -> None:
        self._seed_completed_setup(wash_crew_role_id=WASH_CREW_ROLE_ID)

        body = build_config_summary_body(self.bot.config_service, GUILD_ID, FakeInteraction().guild)

        for index, section in enumerate(CONFIG_SECTION_ORDER, start=1):
            self.assertIn(f"{index}. {CONFIG_SECTION_TITLES[section]}:", body)

    def test_summary_numbers_are_sequential_starting_at_one(self) -> None:
        self._seed_completed_setup(wash_crew_role_id=WASH_CREW_ROLE_ID)

        body = build_config_summary_body(self.bot.config_service, GUILD_ID, FakeInteraction().guild)
        summary_section = body.split("\n\n", 1)[1]
        numbered_lines = summary_section.splitlines()

        self.assertEqual(len(numbered_lines), len(CONFIG_SECTION_ORDER))
        for index, line in enumerate(numbered_lines, start=1):
            self.assertTrue(line.startswith(f"{index}. "), f"line {index} was: {line!r}")

    def test_dropdown_options_are_numbered_in_order(self) -> None:
        options = build_config_section_options()

        self.assertEqual(len(options), len(CONFIG_SECTION_ORDER))
        for index, (section, (value, label)) in enumerate(zip(CONFIG_SECTION_ORDER, options), start=1):
            self.assertEqual(value, section.value)
            self.assertEqual(label, f"{index}. {CONFIG_SECTION_TITLES[section]}")

    async def test_main_menu_dropdown_shows_numbered_labels(self) -> None:
        # End-to-end through the real Discord-facing entry point, not just
        # the pure helper -- proves the numbering actually reaches the
        # rendered dropdown a WASH Crew member sees.
        self._seed_completed_setup(wash_crew_role_id=WASH_CREW_ROLE_ID)
        interaction = FakeInteraction()

        await send_config_main_menu(interaction, self.bot, GUILD_ID, edit=False)

        view: ConfigMainMenuView = interaction.response.sent_view
        select = view.children[0]
        labels = [option.label for option in select.options]
        self.assertEqual(
            labels,
            [f"{index}. {CONFIG_SECTION_TITLES[section]}" for index, section in enumerate(CONFIG_SECTION_ORDER, start=1)],
        )

    async def test_main_menu_summary_shows_numbered_lines(self) -> None:
        self._seed_completed_setup(wash_crew_role_id=WASH_CREW_ROLE_ID)
        interaction = FakeInteraction()

        await send_config_main_menu(interaction, self.bot, GUILD_ID, edit=False)

        for index, section in enumerate(CONFIG_SECTION_ORDER, start=1):
            self.assertIn(f"{index}. {CONFIG_SECTION_TITLES[section]}:", interaction.response.sent_message)

    def test_shared_sections_preserve_the_setup_wizards_relative_order(self) -> None:
        """Ordering matches the Setup Wizard: every /config section that
        also exists as a Setup Wizard step must appear in the same
        relative order here as it does in SETUP_WIZARD_STEP_ORDER --
        /config never reorders a step relative to another shared step,
        even though it also carries a couple of its own extra sections
        (Watch Party Join Mode, Rotation Low-Pool Notification) the wizard
        has no equivalent step for.
        """
        config_to_wizard_step = {
            ConfigSection.WASH_CREW_ROLE: SetupWizardStep.WASH_CREW_ROLE,
            ConfigSection.WATCH_PARTY_ROLE: SetupWizardStep.WATCH_PARTY_ROLE,
            ConfigSection.ADMIN_CHANNEL: SetupWizardStep.ADMIN_CHANNEL,
            ConfigSection.HOME_CHANNEL: SetupWizardStep.HOME_CHANNEL,
            ConfigSection.MANAGE_COLLECTIONS: SetupWizardStep.SUGGESTION_DATABASE,
            ConfigSection.WATCH_DESTINATION: SetupWizardStep.WATCH_DESTINATION,
            ConfigSection.VOTING_DEFAULTS: SetupWizardStep.VOTING_DEFAULTS,
            ConfigSection.REMINDER_DEFAULTS: SetupWizardStep.REMINDER_DEFAULTS,
            ConfigSection.BACKUP_DEFAULTS: SetupWizardStep.BACKUP_DEFAULTS,
        }

        wizard_positions = {step: SETUP_WIZARD_STEP_ORDER.index(step) for step in config_to_wizard_step.values()}
        shared_config_sections = [section for section in CONFIG_SECTION_ORDER if section in config_to_wizard_step]
        wizard_order_for_shared_sections = [
            wizard_positions[config_to_wizard_step[section]] for section in shared_config_sections
        ]

        self.assertEqual(wizard_order_for_shared_sections, sorted(wizard_order_for_shared_sections))

    def test_shared_section_titles_match_the_setup_wizard_exactly(self) -> None:
        """Consistency: for every section whose name isn't deliberately
        more descriptive in /config (Watch Party Home Channel says more
        than the wizard's plain Home Channel, by design), the title text
        itself must be identical to the Setup Wizard's -- never a
        different word for the same thing (e.g. Collections, not Manage
        Collections).
        """
        identical_titles = {
            ConfigSection.WASH_CREW_ROLE: SetupWizardStep.WASH_CREW_ROLE,
            ConfigSection.WATCH_PARTY_ROLE: SetupWizardStep.WATCH_PARTY_ROLE,
            ConfigSection.ADMIN_CHANNEL: SetupWizardStep.ADMIN_CHANNEL,
            ConfigSection.MANAGE_COLLECTIONS: SetupWizardStep.SUGGESTION_DATABASE,
            ConfigSection.WATCH_DESTINATION: SetupWizardStep.WATCH_DESTINATION,
            ConfigSection.VOTING_DEFAULTS: SetupWizardStep.VOTING_DEFAULTS,
            ConfigSection.REMINDER_DEFAULTS: SetupWizardStep.REMINDER_DEFAULTS,
            ConfigSection.BACKUP_DEFAULTS: SetupWizardStep.BACKUP_DEFAULTS,
        }
        for config_section, wizard_step in identical_titles.items():
            self.assertEqual(
                CONFIG_SECTION_TITLES[config_section],
                SETUP_WIZARD_STEP_TITLES[wizard_step],
                f"{config_section} and {wizard_step} should use identical wording",
            )


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
        select._values = [str(DESTINATION_CHANNEL_ID)]

        select_interaction = FakeInteraction()
        await select.callback(interaction=select_interaction)

        self.assertIn("Admin channel updated", select_interaction.response.edited_content)
        self.assertEqual(
            self.guild_configuration_repository.get(GUILD_ID).channels.admin_channel_id, DESTINATION_CHANNEL_ID
        )

    async def test_manage_databases_section_shows_the_database_picker(self) -> None:
        self._seed_completed_setup()
        self.suggestion_service.create_database("Movies", GUILD_ID, DESTINATION_CHANNEL_ID)
        interaction = FakeInteraction()
        await send_config_section(interaction, self.bot, GUILD_ID, ConfigSection.MANAGE_COLLECTIONS, edit=False)
        self.assertIsInstance(interaction.response.sent_view, ConfigDatabaseSectionView)

    async def test_manage_databases_section_with_no_databases_shows_back_only(self) -> None:
        self._seed_completed_setup()
        interaction = FakeInteraction()
        await send_config_section(interaction, self.bot, GUILD_ID, ConfigSection.MANAGE_COLLECTIONS, edit=False)
        self.assertIsInstance(interaction.response.sent_view, BackToMenuOnlyView)

    async def test_selecting_a_database_opens_its_settings_menu(self) -> None:
        self._seed_completed_setup()
        database_result = self.suggestion_service.create_database("Movies", GUILD_ID, DESTINATION_CHANNEL_ID)
        interaction = FakeInteraction()
        await send_config_section(interaction, self.bot, GUILD_ID, ConfigSection.MANAGE_COLLECTIONS, edit=False)
        select = interaction.response.sent_view.children[0]
        select._values = [str(database_result.database.database_id)]

        select_interaction = FakeInteraction()
        await select.callback(interaction=select_interaction)

        self.assertIn("Movies", select_interaction.response.edited_content)
        self.assertIsInstance(select_interaction.response.edited_view, ConfigDatabaseSettingsMenuView)

    async def test_database_settings_menu_shows_current_values(self) -> None:
        self._seed_completed_setup()
        database_result = self.suggestion_service.create_database("Movies", GUILD_ID, DESTINATION_CHANNEL_ID)
        database_id = database_result.database.database_id
        self.bot.config_service.set_database_suggestion_destination(
            GUILD_ID, database_id, DESTINATION_CHANNEL_ID, FakeGuildForValidation(channel_ids={DESTINATION_CHANNEL_ID})
        )
        interaction = FakeInteraction()

        async def on_back(back_interaction) -> None:
            pass

        from watch_party_manager.bot import send_config_database_settings_menu

        await send_config_database_settings_menu(interaction, self.bot, GUILD_ID, database_id, on_back)

        self.assertIn(f"<#{DESTINATION_CHANNEL_ID}>", interaction.response.edited_content)
        self.assertIn("Balanced Random", interaction.response.edited_content)

    async def test_choosing_suggestion_destination_shows_the_channel_picker(self) -> None:
        self._seed_completed_setup()
        database_result = self.suggestion_service.create_database("Movies", GUILD_ID, DESTINATION_CHANNEL_ID)
        interaction = FakeInteraction()

        async def on_back(back_interaction) -> None:
            pass

        from watch_party_manager.bot import send_config_database_settings_menu

        await send_config_database_settings_menu(
            interaction, self.bot, GUILD_ID, database_result.database.database_id, on_back
        )
        setting_select = interaction.response.edited_view.children[0]
        setting_select._values = [DATABASE_SETTING_SUGGESTION_DESTINATION]

        setting_interaction = FakeInteraction()
        await setting_select.callback(interaction=setting_interaction)

        self.assertIsInstance(setting_interaction.response.edited_view, ConfigSuggestionDestinationSectionView)

    async def test_choosing_watch_destination_shows_the_channel_picker(self) -> None:
        self._seed_completed_setup()
        database_result = self.suggestion_service.create_database("Movies", GUILD_ID, DESTINATION_CHANNEL_ID)
        interaction = FakeInteraction()

        async def on_back(back_interaction) -> None:
            pass

        from watch_party_manager.bot import send_config_database_settings_menu

        await send_config_database_settings_menu(
            interaction, self.bot, GUILD_ID, database_result.database.database_id, on_back
        )
        setting_select = interaction.response.edited_view.children[0]
        setting_select._values = [DATABASE_SETTING_WATCH_DESTINATION]

        setting_interaction = FakeInteraction()
        await setting_select.callback(interaction=setting_interaction)

        self.assertIsInstance(setting_interaction.response.edited_view, ConfigWatchDestinationSectionView)

    async def test_choosing_candidate_selection_shows_the_dropdown(self) -> None:
        self._seed_completed_setup()
        database_result = self.suggestion_service.create_database("Movies", GUILD_ID, DESTINATION_CHANNEL_ID)
        interaction = FakeInteraction()

        async def on_back(back_interaction) -> None:
            pass

        from watch_party_manager.bot import send_config_database_settings_menu

        await send_config_database_settings_menu(
            interaction, self.bot, GUILD_ID, database_result.database.database_id, on_back
        )
        setting_select = interaction.response.edited_view.children[0]
        setting_select._values = [DATABASE_SETTING_CANDIDATE_SELECTION]

        setting_interaction = FakeInteraction()
        await setting_select.callback(interaction=setting_interaction)

        self.assertIsInstance(setting_interaction.response.edited_view, ConfigDatabaseCandidateSelectionView)
        self.assertEqual(
            setting_interaction.response.edited_view.candidate_selection_select.selected,
            CandidateSelectionMode.ROTATION_POOL,
        )

    async def test_selecting_a_suggestion_destination_saves_immediately(self) -> None:
        self._seed_completed_setup()
        database_result = self.suggestion_service.create_database("Movies", GUILD_ID, DESTINATION_CHANNEL_ID)
        interaction = FakeInteraction()

        async def on_back(back_interaction) -> None:
            pass

        from watch_party_manager.bot import send_config_database_suggestion_destination

        await send_config_database_suggestion_destination(
            interaction, self.bot, GUILD_ID, database_result.database.database_id, on_back
        )
        select = interaction.response.edited_view.children[0]
        select._values = [_FakeChannelValue(DESTINATION_CHANNEL_ID)]

        select_interaction = FakeInteraction()
        await select.callback(interaction=select_interaction)

        self.assertIn("Suggestion post destination updated", select_interaction.response.edited_content)
        database_configuration = self.suggestion_database_configuration_repository.get(
            GUILD_ID, database_result.database.database_id
        )
        self.assertEqual(database_configuration.channels.suggestion_channel_id, DESTINATION_CHANNEL_ID)

    async def test_suggestion_destination_rejects_the_configured_home_channel(self) -> None:
        # Prevent Collections From Using The Home Channel: /config's own
        # Suggestion Destination section must reject it too, not just
        # /database move.
        home_channel_id = 750
        self._seed_completed_setup(channels=GuildChannelsConfig(home_channel_id=home_channel_id))
        database_result = self.suggestion_service.create_database("Movies", GUILD_ID, DESTINATION_CHANNEL_ID)
        guild = FakeGuildForValidation(channel_ids={home_channel_id})
        interaction = FakeInteraction(guild=guild)

        async def on_back(back_interaction) -> None:
            pass

        from watch_party_manager.bot import send_config_database_suggestion_destination

        await send_config_database_suggestion_destination(
            interaction, self.bot, GUILD_ID, database_result.database.database_id, on_back
        )
        select = interaction.response.edited_view.children[0]
        select._values = [_FakeChannelValue(home_channel_id)]

        select_interaction = FakeInteraction(guild=guild)
        await select.callback(interaction=select_interaction)

        self.assertIn("Home Channel", select_interaction.response.edited_content)
        database_configuration = self.suggestion_database_configuration_repository.get(
            GUILD_ID, database_result.database.database_id
        )
        self.assertIsNone(database_configuration)

    async def test_suggestion_destination_screen_has_no_clear_button(self) -> None:
        # Every collection MUST have exactly one dedicated suggestion
        # destination -- this screen can only change it, never clear it.
        self._seed_completed_setup()
        database_result = self.suggestion_service.create_database("Movies", GUILD_ID, DESTINATION_CHANNEL_ID)
        interaction = FakeInteraction()

        async def on_back(back_interaction) -> None:
            pass

        from watch_party_manager.bot import send_config_database_suggestion_destination

        await send_config_database_suggestion_destination(
            interaction, self.bot, GUILD_ID, database_result.database.database_id, on_back
        )

        self.assertEqual(len(interaction.response.edited_view.children), 2)  # channel select + Back to Menu only

    async def test_selecting_a_watch_destination_saves_immediately(self) -> None:
        self._seed_completed_setup()
        database_result = self.suggestion_service.create_database("Movies", GUILD_ID, DESTINATION_CHANNEL_ID)
        interaction = FakeInteraction()

        async def on_back(back_interaction) -> None:
            pass

        from watch_party_manager.bot import send_config_database_watch_destination

        await send_config_database_watch_destination(
            interaction, self.bot, GUILD_ID, database_result.database.database_id, on_back
        )
        select = interaction.response.edited_view.children[0]
        select._values = [str(DESTINATION_CHANNEL_ID)]

        select_interaction = FakeInteraction()
        await select.callback(interaction=select_interaction)

        self.assertIn("Watched Item Archive updated", select_interaction.response.edited_content)
        database_configuration = self.suggestion_database_configuration_repository.get(
            GUILD_ID, database_result.database.database_id
        )
        self.assertEqual(database_configuration.channels.watch_history_channel_id, DESTINATION_CHANNEL_ID)

    async def test_saving_candidate_selection_persists_the_chosen_mode(self) -> None:
        self._seed_completed_setup()
        database_result = self.suggestion_service.create_database("Movies", GUILD_ID, DESTINATION_CHANNEL_ID)
        interaction = FakeInteraction()

        async def on_back(back_interaction) -> None:
            pass

        from watch_party_manager.bot import send_config_database_candidate_selection

        await send_config_database_candidate_selection(
            interaction, self.bot, GUILD_ID, database_result.database.database_id, on_back
        )
        select_view = interaction.response.edited_view
        select_view.candidate_selection_select._values = [CandidateSelectionMode.SOFT_ROTATION.value]

        save_interaction = FakeInteraction()
        await select_view.children[1].callback(interaction=save_interaction)

        self.assertIn("Soft Rotation", save_interaction.response.edited_content)
        database_configuration = self.suggestion_database_configuration_repository.get(
            GUILD_ID, database_result.database.database_id
        )
        self.assertEqual(database_configuration.suggestion_rules.candidate_selection, CandidateSelectionMode.SOFT_ROTATION)

    async def test_two_databases_can_be_managed_independently(self) -> None:
        self._seed_completed_setup()
        first = self.suggestion_service.create_database("Movies", GUILD_ID, 400).database
        second = self.suggestion_service.create_database("TV Shows", GUILD_ID, 401).database
        guild = FakeGuildForValidation(channel_ids={500, 501})

        async def on_back(back_interaction) -> None:
            pass

        from watch_party_manager.bot import send_config_database_suggestion_destination

        first_interaction = FakeInteraction(guild=guild)
        await send_config_database_suggestion_destination(first_interaction, self.bot, GUILD_ID, first.database_id, on_back)
        first_select = first_interaction.response.edited_view.children[0]
        first_select._values = [_FakeChannelValue(500)]
        first_select_interaction = FakeInteraction(guild=guild)
        await first_select.callback(interaction=first_select_interaction)

        second_interaction = FakeInteraction(guild=guild)
        await send_config_database_suggestion_destination(second_interaction, self.bot, GUILD_ID, second.database_id, on_back)
        second_select = second_interaction.response.edited_view.children[0]
        second_select._values = [_FakeChannelValue(501)]
        second_select_interaction = FakeInteraction(guild=guild)
        await second_select.callback(interaction=second_select_interaction)

        self.assertTrue(first_select_interaction.response.edited_content.startswith("Suggestion post destination updated"))
        self.assertTrue(second_select_interaction.response.edited_content.startswith("Suggestion post destination updated"))
        first_configuration = self.suggestion_database_configuration_repository.get(GUILD_ID, first.database_id)
        second_configuration = self.suggestion_database_configuration_repository.get(GUILD_ID, second.database_id)
        self.assertEqual(first_configuration.channels.suggestion_channel_id, 500)
        self.assertEqual(second_configuration.channels.suggestion_channel_id, 501)

    async def test_duplicate_destination_is_rejected_with_a_clear_error(self) -> None:
        self._seed_completed_setup()
        first = self.suggestion_service.create_database("Movies", GUILD_ID, 400).database
        second = self.suggestion_service.create_database("TV Shows", GUILD_ID, 401).database
        guild = FakeGuildForValidation(channel_ids={500})

        async def on_back(back_interaction) -> None:
            pass

        from watch_party_manager.bot import send_config_database_suggestion_destination

        first_interaction = FakeInteraction(guild=guild)
        await send_config_database_suggestion_destination(first_interaction, self.bot, GUILD_ID, first.database_id, on_back)
        first_select = first_interaction.response.edited_view.children[0]
        first_select._values = [_FakeChannelValue(500)]
        await first_select.callback(interaction=FakeInteraction(guild=guild))

        second_interaction = FakeInteraction(guild=guild)
        await send_config_database_suggestion_destination(second_interaction, self.bot, GUILD_ID, second.database_id, on_back)
        second_select = second_interaction.response.edited_view.children[0]
        second_select._values = [_FakeChannelValue(500)]
        second_select_interaction = FakeInteraction(guild=guild)
        await second_select.callback(interaction=second_select_interaction)

        self.assertIn("already routed", second_select_interaction.response.edited_content)
        second_configuration = self.suggestion_database_configuration_repository.get(GUILD_ID, second.database_id)
        self.assertIsNone(second_configuration)


class HomeChannelSectionTests(ConfigCommandTestCase):
    """Home Channel Visibility & Configuration (polish batch): a dedicated
    Home Channel section offering Create New Channel, Use Current
    Channel, Select Existing Channel, and Clear Home Channel.
    """

    async def test_section_shows_all_four_options_plus_back(self) -> None:
        from watch_party_manager.config_view import ConfigHomeChannelSectionView

        self._seed_completed_setup()
        interaction = FakeInteraction()
        await send_config_section(interaction, self.bot, GUILD_ID, ConfigSection.HOME_CHANNEL, edit=False)

        self.assertIsInstance(interaction.response.sent_view, ConfigHomeChannelSectionView)
        self.assertEqual(
            [button.custom_id for button in interaction.response.sent_view.children],
            [
                "wpm_setup_destination_create_channel",
                "wpm_config_home_channel_use_current",
                "wpm_setup_destination_existing_channel",
                "wpm_config_home_channel_clear",
                "wpm_config_back_to_menu",
            ],
        )

    async def test_use_current_channel_is_enabled_from_a_text_channel(self) -> None:
        current_channel = FakeCurrentChannel(700, discord.ChannelType.text)
        guild = FakeGuildForValidation(channel_ids={700})
        self._seed_completed_setup()
        interaction = FakeInteraction(channel=current_channel)
        await send_config_section(interaction, self.bot, GUILD_ID, ConfigSection.HOME_CHANNEL, edit=False)
        use_current_button = next(
            b for b in interaction.response.sent_view.children
            if b.custom_id == "wpm_config_home_channel_use_current"
        )
        self.assertFalse(use_current_button.disabled)

        button_interaction = FakeInteraction(guild=guild, channel=current_channel)
        await use_current_button.callback(interaction=button_interaction)

        self.assertIn("Watch Party Home Channel updated", button_interaction.response.edited_content)
        self.assertEqual(self.guild_configuration_repository.get(GUILD_ID).channels.home_channel_id, 700)

    async def test_use_current_channel_is_disabled_from_a_thread(self) -> None:
        current_thread = FakeCurrentChannel(701, discord.ChannelType.public_thread)
        self._seed_completed_setup()
        interaction = FakeInteraction(channel=current_thread)
        await send_config_section(interaction, self.bot, GUILD_ID, ConfigSection.HOME_CHANNEL, edit=False)
        use_current_button = next(
            b for b in interaction.response.sent_view.children
            if b.custom_id == "wpm_config_home_channel_use_current"
        )
        self.assertTrue(use_current_button.disabled)

    async def test_stale_click_on_disabled_use_current_shows_a_clear_error(self) -> None:
        current_thread = FakeCurrentChannel(701, discord.ChannelType.public_thread)
        self._seed_completed_setup()
        interaction = FakeInteraction(channel=current_thread)
        await send_config_section(interaction, self.bot, GUILD_ID, ConfigSection.HOME_CHANNEL, edit=False)
        use_current_button = next(
            b for b in interaction.response.sent_view.children
            if b.custom_id == "wpm_config_home_channel_use_current"
        )

        button_interaction = FakeInteraction(channel=current_thread)
        await use_current_button.callback(interaction=button_interaction)

        self.assertIn("isn't available here", button_interaction.response.edited_content)
        self.assertIsNone(self.guild_configuration_repository.get(GUILD_ID).channels.home_channel_id)

    async def test_select_existing_channel_saves_immediately(self) -> None:
        self._seed_completed_setup()
        interaction = FakeInteraction()
        await send_config_section(interaction, self.bot, GUILD_ID, ConfigSection.HOME_CHANNEL, edit=False)
        existing_button = next(
            b for b in interaction.response.sent_view.children
            if b.custom_id == "wpm_setup_destination_existing_channel"
        )

        destination_interaction = FakeInteraction()
        await existing_button.callback(interaction=destination_interaction)
        select = destination_interaction.response.edited_view.children[0]
        select._values = [_FakeChannelValue(DESTINATION_CHANNEL_ID)]

        select_interaction = FakeInteraction()
        await select.callback(interaction=select_interaction)

        self.assertIn("Watch Party Home Channel updated", select_interaction.response.edited_content)
        self.assertEqual(
            self.guild_configuration_repository.get(GUILD_ID).channels.home_channel_id, DESTINATION_CHANNEL_ID
        )

    async def test_create_new_channel_creates_and_saves(self) -> None:
        self._seed_completed_setup()
        interaction = FakeInteraction()
        await send_config_section(interaction, self.bot, GUILD_ID, ConfigSection.HOME_CHANNEL, edit=False)
        create_button = next(
            b for b in interaction.response.sent_view.children
            if b.custom_id == "wpm_setup_destination_create_channel"
        )

        create_interaction = FakeInteraction()
        await create_button.callback(interaction=create_interaction)
        modal = create_interaction.response.sent_modal

        guild = FakeGuildForValidation(new_channel_id=800)
        modal_interaction = FakeInteraction(guild=guild)
        await modal.on_submit(interaction=modal_interaction)

        self.assertEqual(guild.created_channel.id, 800)
        self.assertIn("Watch Party Home Channel updated", modal_interaction.response.edited_content)
        self.assertEqual(self.guild_configuration_repository.get(GUILD_ID).channels.home_channel_id, 800)

    async def test_create_new_channel_reports_a_creation_failure(self) -> None:
        self._seed_completed_setup()
        interaction = FakeInteraction()
        await send_config_section(interaction, self.bot, GUILD_ID, ConfigSection.HOME_CHANNEL, edit=False)
        create_button = next(
            b for b in interaction.response.sent_view.children
            if b.custom_id == "wpm_setup_destination_create_channel"
        )

        create_interaction = FakeInteraction()
        await create_button.callback(interaction=create_interaction)
        modal = create_interaction.response.sent_modal

        guild = FakeGuildForValidation(fail_create=True)
        modal_interaction = FakeInteraction(guild=guild)
        await modal.on_submit(interaction=modal_interaction)

        self.assertIn("Could not create the channel", modal_interaction.response.edited_content)
        self.assertIsNone(self.guild_configuration_repository.get(GUILD_ID).channels.home_channel_id)

    async def test_clear_home_channel(self) -> None:
        self._seed_completed_setup(channels=GuildChannelsConfig(home_channel_id=DESTINATION_CHANNEL_ID))
        interaction = FakeInteraction()
        await send_config_section(interaction, self.bot, GUILD_ID, ConfigSection.HOME_CHANNEL, edit=False)
        clear_button = next(
            b for b in interaction.response.sent_view.children
            if b.custom_id == "wpm_config_home_channel_clear"
        )

        clear_interaction = FakeInteraction()
        await clear_button.callback(interaction=clear_interaction)

        self.assertIn("cleared", clear_interaction.response.edited_content)
        self.assertIsNone(self.guild_configuration_repository.get(GUILD_ID).channels.home_channel_id)

    async def test_insufficient_permissions_is_rejected(self) -> None:
        self._seed_completed_setup()
        interaction = FakeInteraction()
        await send_config_section(interaction, self.bot, GUILD_ID, ConfigSection.HOME_CHANNEL, edit=False)
        existing_button = next(
            b for b in interaction.response.sent_view.children
            if b.custom_id == "wpm_setup_destination_existing_channel"
        )

        destination_interaction = FakeInteraction()
        await existing_button.callback(interaction=destination_interaction)
        select = destination_interaction.response.edited_view.children[0]
        restricted_channel_id = 850
        guild = FakeGuildForValidation()
        guild._channels[restricted_channel_id] = FakeChannel(
            restricted_channel_id, permissions=FakePermissions(send_messages=False)
        )
        select._values = [_FakeChannelValue(restricted_channel_id)]

        select_interaction = FakeInteraction(guild=guild)
        await select.callback(interaction=select_interaction)

        self.assertIn("cannot send messages", select_interaction.response.edited_content)
        self.assertIn("Send Messages", select_interaction.response.edited_content)
        self.assertIsNone(self.guild_configuration_repository.get(GUILD_ID).channels.home_channel_id)


class GuildWideWatchDestinationSectionTests(ConfigCommandTestCase):
    """Design refinement: a guild-wide default Watched Item Archive,
    distinct from -- and overridden by -- any per-collection setting
    inside Collections.
    """

    async def test_section_shows_the_channel_picker(self) -> None:
        self._seed_completed_setup()
        interaction = FakeInteraction()
        await send_config_section(interaction, self.bot, GUILD_ID, ConfigSection.WATCH_DESTINATION, edit=False)
        self.assertIsInstance(interaction.response.sent_view, ConfigWatchDestinationSectionView)

    async def test_selecting_a_channel_saves_the_guild_wide_default(self) -> None:
        self._seed_completed_setup()
        interaction = FakeInteraction()
        await send_config_section(interaction, self.bot, GUILD_ID, ConfigSection.WATCH_DESTINATION, edit=False)
        select = interaction.response.sent_view.children[0]
        select._values = [str(DESTINATION_CHANNEL_ID)]

        select_interaction = FakeInteraction()
        await select.callback(interaction=select_interaction)

        self.assertIn("Default Watched Item Archive updated", select_interaction.response.edited_content)
        self.assertEqual(
            self.guild_configuration_repository.get(GUILD_ID).channels.watch_history_channel_id, DESTINATION_CHANNEL_ID
        )

    async def test_clearing_the_guild_wide_default(self) -> None:
        self._seed_completed_setup()
        interaction = FakeInteraction()
        await send_config_section(interaction, self.bot, GUILD_ID, ConfigSection.WATCH_DESTINATION, edit=False)
        clear_button = interaction.response.sent_view.children[1]

        clear_interaction = FakeInteraction()
        await clear_button.callback(interaction=clear_interaction)

        self.assertIn("Default Watched Item Archive cleared", clear_interaction.response.edited_content)

    async def test_a_collections_own_override_wins_over_the_guild_default(self) -> None:
        self._seed_completed_setup()
        database_result = self.suggestion_service.create_database("Movies", GUILD_ID, DESTINATION_CHANNEL_ID)
        other_channel = 401
        guild = FakeGuildForValidation(channel_ids={DESTINATION_CHANNEL_ID, other_channel})
        self.bot.config_service.set_guild_watch_destination(GUILD_ID, DESTINATION_CHANNEL_ID, guild)
        self.bot.config_service.set_database_watch_destination(
            GUILD_ID, database_result.database.database_id, other_channel, guild
        )

        effective = self.bot.config_service.resolve_effective_watch_destination(
            GUILD_ID, database_result.database.database_id
        )

        self.assertEqual(effective, other_channel)


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
    async def _open_voting_defaults_modal(self, on_back=None):
        """Drive /config's Voting Defaults flow through its intro view
        (Visibility select + Set Voting Defaults button) to the modal,
        mirroring the real two-step interaction sequence.
        """
        if on_back is None:
            async def on_back(back_interaction) -> None:
                pass

        interaction = FakeInteraction()
        await send_config_voting_defaults_modal(interaction, self.bot, GUILD_ID, on_back)
        intro_view = interaction.response.edited_view
        configure_button = next(
            child for child in intro_view.children if getattr(child, "label", None) == "Set Voting Defaults"
        )
        configure_interaction = FakeInteraction()
        await configure_button.callback(interaction=configure_interaction)
        return intro_view, configure_interaction.response.sent_modal

    async def test_voting_defaults_shows_a_visibility_select_before_the_modal(self) -> None:
        # Guild-wide default candidate count/duration/visibility --
        # candidate selection stays per-database, under Manage
        # Databases. Visibility is a small fixed set of choices, so it's
        # collected via a dropdown on this intro screen, never inside
        # the modal (Discord's API rejects a Select embedded in a modal
        # at submission time even though discord.py allows constructing
        # one).
        self._seed_completed_setup()
        interaction = FakeInteraction()

        async def on_back(back_interaction) -> None:
            pass

        await send_config_voting_defaults_modal(interaction, self.bot, GUILD_ID, on_back)

        intro_view = interaction.response.edited_view
        self.assertIsInstance(intro_view, ConfigVotingDefaultsIntroView)
        self.assertEqual(intro_view.visibility_select.selected, GuildVoteVisibility.VISIBLE)
        descriptions = {option.value: option.description for option in intro_view.visibility_select.options}
        self.assertIn("shown", descriptions[GuildVoteVisibility.VISIBLE.value].lower())
        self.assertIn("hidden", descriptions[GuildVoteVisibility.BLIND.value].lower())

    async def test_set_voting_defaults_button_opens_a_modal_with_only_text_inputs(self) -> None:
        self._seed_completed_setup()

        _intro_view, modal = await self._open_voting_defaults_modal()

        self.assertIsNotNone(modal)
        self.assertTrue(all(isinstance(child, discord.ui.TextInput) for child in modal.children))
        self.assertEqual(modal.candidate_count_input.default, "3")
        self.assertEqual(modal.duration_input.default, "1d")

    async def test_voting_defaults_submission_saves_the_selected_visibility_and_modal_values(self) -> None:
        self._seed_completed_setup()
        intro_view, modal = await self._open_voting_defaults_modal()
        intro_view.visibility_select._values = [GuildVoteVisibility.BLIND.value]
        # Re-open the modal now that Blind is selected, mirroring the
        # real flow (the modal opens fresh from the intro view's
        # current selection each time).
        configure_button = next(
            child for child in intro_view.children if getattr(child, "label", None) == "Set Voting Defaults"
        )
        configure_interaction = FakeInteraction()
        await configure_button.callback(interaction=configure_interaction)
        modal = configure_interaction.response.sent_modal
        modal.candidate_count_input._value = "5"
        modal.duration_input._value = "14h"

        submit_interaction = FakeInteraction()
        await modal.on_submit(interaction=submit_interaction)

        self.assertIn("Voting defaults updated", submit_interaction.response.edited_content)
        voting_defaults = self.guild_configuration_repository.get(GUILD_ID).voting_defaults
        self.assertEqual(voting_defaults.candidate_count, 5)
        self.assertEqual(voting_defaults.visibility, GuildVoteVisibility.BLIND)

    async def test_voting_defaults_submission_with_invalid_value_shows_retry(self) -> None:
        self._seed_completed_setup()
        _intro_view, modal = await self._open_voting_defaults_modal()
        modal.candidate_count_input._value = "not-a-number"
        modal.duration_input._value = "14"

        submit_interaction = FakeInteraction()
        await modal.on_submit(interaction=submit_interaction)

        self.assertIn("must be a whole number", submit_interaction.response.edited_content)
        # Nothing was saved.
        self.assertEqual(self.guild_configuration_repository.get(GUILD_ID).voting_defaults.candidate_count, 3)

    async def _open_reminder_defaults_modal(self, on_back):
        """Reminder Defaults now shows an Enable/Disable choice first
        (Fixed-Option UX Audit) -- Enable opens the lead-time-only modal.
        """
        interaction = FakeInteraction()
        await send_config_reminder_defaults_modal(interaction, self.bot, GUILD_ID, on_back)
        choice_view = interaction.response.edited_view
        enable_button = next(c for c in choice_view.children if c.custom_id == "wpm_config_reminder_enable")
        configure_interaction = FakeInteraction()
        await enable_button.callback(interaction=configure_interaction)
        return choice_view, configure_interaction.response.sent_modal

    async def test_reminder_defaults_shows_an_enable_disable_choice_first(self) -> None:
        self._seed_completed_setup()

        async def on_back(back_interaction) -> None:
            pass

        interaction = FakeInteraction()
        await send_config_reminder_defaults_modal(interaction, self.bot, GUILD_ID, on_back)

        choice_view = interaction.response.edited_view
        labels = {getattr(child, "label", None) for child in choice_view.children}
        self.assertIn("Enable Vote-Ending Reminder (Recommended)", labels)
        self.assertIn("Disable Vote-Ending Reminder", labels)

    async def test_reminder_defaults_modal_is_prefilled_with_current_values(self) -> None:
        self._seed_completed_setup()

        async def on_back(back_interaction) -> None:
            pass

        _choice_view, modal = await self._open_reminder_defaults_modal(on_back)
        self.assertTrue(all(isinstance(child, discord.ui.TextInput) for child in modal.children))
        self.assertEqual(len(modal.children), 1)
        self.assertEqual(modal.minutes_input.default, "1d")

    async def test_reminder_defaults_enable_submission_saves(self) -> None:
        self._seed_completed_setup()

        async def on_back(back_interaction) -> None:
            pass

        _choice_view, modal = await self._open_reminder_defaults_modal(on_back)
        modal.minutes_input._value = "2h"

        submit_interaction = FakeInteraction()
        await modal.on_submit(interaction=submit_interaction)

        vote_notifications = self.guild_configuration_repository.get(GUILD_ID).notifications.vote
        self.assertTrue(vote_notifications.vote_ending_reminder)
        self.assertEqual(vote_notifications.reminder_minutes_before_close, 120)

    async def test_reminder_defaults_disable_saves_without_opening_a_modal(self) -> None:
        self._seed_completed_setup()

        async def on_back(back_interaction) -> None:
            pass

        interaction = FakeInteraction()
        await send_config_reminder_defaults_modal(interaction, self.bot, GUILD_ID, on_back)
        choice_view = interaction.response.edited_view
        disable_button = next(c for c in choice_view.children if c.custom_id == "wpm_config_reminder_disable")

        disable_interaction = FakeInteraction()
        await disable_button.callback(interaction=disable_interaction)

        self.assertIsNone(disable_interaction.response.sent_modal)
        self.assertFalse(self.guild_configuration_repository.get(GUILD_ID).notifications.vote.vote_ending_reminder)

    async def _open_backup_defaults_modal(self, on_back):
        """Backup Defaults now shows an Enable/Disable choice first
        (Release Polish: Optional Automatic Backups) -- Enable opens the
        same interval/retention modal as before.
        """
        interaction = FakeInteraction()
        await send_config_backup_defaults_modal(interaction, self.bot, GUILD_ID, on_back)
        choice_view = interaction.response.edited_view
        enable_button = next(c for c in choice_view.children if c.custom_id == "wpm_config_backup_enable")
        configure_interaction = FakeInteraction()
        await enable_button.callback(interaction=configure_interaction)
        return configure_interaction.response.sent_modal

    async def test_backup_defaults_modal_is_prefilled_with_current_values(self) -> None:
        self._seed_completed_setup()

        async def on_back(back_interaction) -> None:
            pass

        modal = await self._open_backup_defaults_modal(on_back)
        self.assertEqual(modal.interval_input.default, "1")
        self.assertEqual(modal.retention_input.default, "30")

    async def test_backup_defaults_submission_saves(self) -> None:
        self._seed_completed_setup()

        async def on_back(back_interaction) -> None:
            pass

        modal = await self._open_backup_defaults_modal(on_back)
        modal.interval_input._value = "5"
        modal.retention_input._value = "60"

        submit_interaction = FakeInteraction()
        await modal.on_submit(interaction=submit_interaction)

        backup = self.guild_configuration_repository.get(GUILD_ID).backup
        self.assertTrue(backup.include_in_automatic_backups)
        self.assertEqual(backup.extra_fields["automatic_backup_interval_days"], 5)
        self.assertEqual(backup.extra_fields["backup_retention_count"], 60)

    async def test_backup_defaults_submission_with_invalid_value_shows_retry(self) -> None:
        self._seed_completed_setup()

        async def on_back(back_interaction) -> None:
            pass

        modal = await self._open_backup_defaults_modal(on_back)
        modal.interval_input._value = "0"
        modal.retention_input._value = "60"

        submit_interaction = FakeInteraction()
        await modal.on_submit(interaction=submit_interaction)

        self.assertIn("must be between", submit_interaction.response.edited_content)

    async def test_disabling_automatic_backups_saves_and_reports_disabled(self) -> None:
        self._seed_completed_setup()

        async def on_back(back_interaction) -> None:
            pass

        interaction = FakeInteraction()
        await send_config_backup_defaults_modal(interaction, self.bot, GUILD_ID, on_back)
        choice_view = interaction.response.edited_view
        disable_button = next(c for c in choice_view.children if c.custom_id == "wpm_config_backup_disable")

        disable_interaction = FakeInteraction()
        await disable_button.callback(interaction=disable_interaction)

        self.assertIn("Automatic Backups: Disabled", disable_interaction.response.edited_content)
        backup = self.guild_configuration_repository.get(GUILD_ID).backup
        self.assertFalse(backup.include_in_automatic_backups)

    async def test_enabling_automatic_backups_schedules_the_job_immediately(self) -> None:
        self._seed_completed_setup()

        async def on_back(back_interaction) -> None:
            pass

        modal = await self._open_backup_defaults_modal(on_back)
        modal.interval_input._value = "5"
        modal.retention_input._value = "60"

        submit_interaction = FakeInteraction()
        await modal.on_submit(interaction=submit_interaction)

        active_jobs = [job for job in self.scheduler_repository.jobs.values() if job.is_active]
        self.assertEqual(len(active_jobs), 1)
        self.assertEqual(active_jobs[0].guild_id, GUILD_ID)

    async def test_disabling_automatic_backups_cancels_the_scheduled_job(self) -> None:
        self._seed_completed_setup()

        async def on_back(back_interaction) -> None:
            pass

        # Enable first (scheduling a job), then disable.
        modal = await self._open_backup_defaults_modal(on_back)
        modal.interval_input._value = "1"
        modal.retention_input._value = "30"
        await modal.on_submit(interaction=FakeInteraction())
        self.assertEqual(len([j for j in self.scheduler_repository.jobs.values() if j.is_active]), 1)

        interaction = FakeInteraction()
        await send_config_backup_defaults_modal(interaction, self.bot, GUILD_ID, on_back)
        choice_view = interaction.response.edited_view
        disable_button = next(c for c in choice_view.children if c.custom_id == "wpm_config_backup_disable")
        await disable_button.callback(interaction=FakeInteraction())

        active_jobs = [job for job in self.scheduler_repository.jobs.values() if job.is_active]
        self.assertEqual(active_jobs, [])

    async def test_voting_defaults_fails_safely_when_configuration_is_missing(self) -> None:
        # Defense in depth: /config's own entry point already refuses to
        # proceed unless GuildConfiguration exists and setup_completed is
        # True, so this section function should never normally be
        # reachable with a missing configuration -- but it must fail
        # safely with an actionable message rather than crash if it ever
        # is (e.g. a concurrent /factory_reset), matching the walkthrough
        # requirement that configuration-dependent commands never throw
        # unhandled errors.
        async def on_back(back_interaction) -> None:
            pass

        interaction = FakeInteraction()
        await send_config_voting_defaults_modal(interaction, self.bot, GUILD_ID, on_back)

        self.assertIn("could not be found", interaction.response.edited_content)
        self.assertIn("/setup", interaction.response.edited_content)

    async def test_reminder_defaults_fails_safely_when_configuration_is_missing(self) -> None:
        async def on_back(back_interaction) -> None:
            pass

        interaction = FakeInteraction()
        await send_config_reminder_defaults_modal(interaction, self.bot, GUILD_ID, on_back)
        choice_view = interaction.response.edited_view
        enable_button = next(c for c in choice_view.children if c.custom_id == "wpm_config_reminder_enable")

        configure_interaction = FakeInteraction()
        await enable_button.callback(interaction=configure_interaction)

        self.assertIn("could not be found", configure_interaction.response.edited_content)
        self.assertIn("/setup", configure_interaction.response.edited_content)

    async def test_backup_defaults_fails_safely_when_configuration_is_missing(self) -> None:
        async def on_back(back_interaction) -> None:
            pass

        interaction = FakeInteraction()
        await send_config_backup_defaults_modal(interaction, self.bot, GUILD_ID, on_back)
        choice_view = interaction.response.edited_view
        enable_button = next(c for c in choice_view.children if c.custom_id == "wpm_config_backup_enable")

        configure_interaction = FakeInteraction()
        await enable_button.callback(interaction=configure_interaction)

        self.assertIn("could not be found", configure_interaction.response.edited_content)
        self.assertIn("/setup", configure_interaction.response.edited_content)


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
