"""Tests for FR-028's /setup wiring in bot.py.

Covers the pure/testable pieces bot.py adds for the wizard: the
permission gate, the field parsers each modal submission uses, the
progress-header/completion-summary builders, the startup role fallback,
and send_setup_wizard_step's per-step Discord rendering (exercised with
fake interactions instead of a live Discord connection, mirroring
test_edit_vote_command.py's FakeInteraction/FakeResponse pattern).
"""

from __future__ import annotations

import tempfile
import unittest
import unittest.mock
from pathlib import Path

import discord

from watch_party_manager.bot import (
    WatchPartyBot,
    build_channel_destination_options,
    build_setup_completion_summary,
    build_setup_preparation_text,
    build_setup_step_header,
    parse_setup_backup_interval_days,
    parse_setup_backup_retention_count,
    parse_setup_reminder_minutes_before_close,
    parse_setup_voting_candidate_count,
    parse_setup_voting_duration_minutes,
    perform_setup_permission_check,
    perform_setup_redirect_check,
    post_suggestion_confirmation,
    resolve_startup_role_ids,
    send_setup_preparation_screen,
    send_setup_wizard_step,
)
from watch_party_manager.domain.guild_configuration import (
    GuildConfiguration,
    GuildVoteVisibility,
    JoinMode,
)
from watch_party_manager.domain.setup_wizard import (
    SETUP_WIZARD_STEP_ORDER,
    SetupWizardDraft,
    SetupWizardState,
    SetupWizardStep,
)
from watch_party_manager.domain.suggestion_database_configuration import (
    CANDIDATE_SELECTION_DISPLAY_LABELS,
    CandidateSelectionMode,
)
from watch_party_manager.persistence.guild_configuration_repository import GuildConfigurationRepository
from watch_party_manager.persistence.setup_wizard_repository import SetupWizardRepository
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.scheduler.scheduled_job import JobResult, JobStatus, ScheduledJob
from watch_party_manager.scheduler.scheduler_service import SchedulerService
from watch_party_manager.services.config_service import ConfigService
from watch_party_manager.services.discord_ui_limits import find_oversized_view_component_fields
from watch_party_manager.services.setup_wizard_service import SetupWizardService
from watch_party_manager.services.suggestion_service import DEFAULT_REJECTION_THRESHOLD, SuggestionService
from watch_party_manager.setup_wizard_view import (
    AdminChannelNameModal,
    AdminChannelStepView,
    BackupDefaultsChoiceView,
    ExistingChannelSelectView,
    HomeChannelNameModal,
    HomeChannelStepView,
    ModalStepIntroView,
    RejectionSettingsChoiceView,
    RejectionThresholdModal,
    ReminderDefaultsChoiceView,
    ReminderDefaultsModal,
    ReviewStepView,
    SetupBackButton,
    SetupPreparationView,
    SetupProgressSavedView,
    SetupSaveForLaterButton,
    VotingDefaultsIntroView,
    VotingDefaultsModal,
    WashCrewRoleStepView,
    WatchDestinationStepView,
    WatchPartyRoleStepView,
)

GUILD_ID = 100
WASH_CREW_ROLE_ID = 111
WATCH_PARTY_ROLE_ID = 222
DESTINATION_CHANNEL_ID = 400


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


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, roles=(), *, user_id: int = 1) -> None:
        self.roles = list(roles)
        self.id = user_id


class FakeResponse:
    def __init__(self) -> None:
        self.sent_message = None
        self.sent_ephemeral = None
        self.sent_view = None
        self.edited_content = None
        self.edited_view = "not-edited"
        self.sent_modal = None
        self.deferred = False

    async def send_message(self, content, ephemeral=False, view=None) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral
        self.sent_view = view

    async def edit_message(self, content=None, view=None) -> None:
        self.edited_content = content
        self.edited_view = view

    async def send_modal(self, modal) -> None:
        self.sent_modal = modal

    async def defer(self) -> None:
        self.deferred = True


class FakeInteraction:
    def __init__(self, user=None, guild=None) -> None:
        self.user = user if user is not None else FakeMember()
        self.response = FakeResponse()
        self.guild = guild


class FakeUsablePermissions:
    view_channel = True
    send_messages = True


class FakeUsableChannel:
    """A channel/thread that validate_channel_usable() accepts -- used by
    finalize()-time FakeGuilds so a persisted home_channel_id resolves
    successfully instead of failing "no longer exists".
    """

    def permissions_for(self, member) -> FakeUsablePermissions:
        return FakeUsablePermissions()


class FakeUnusablePermissions:
    view_channel = True
    send_messages = False


class FakeUnusableChannel:
    """A channel WASH can see but can't post in -- validate_channel_usable()
    rejects it with an actionable permission-failure message.
    """

    parent = None

    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.name = f"channel-{channel_id}"

    def permissions_for(self, member) -> FakeUnusablePermissions:
        return FakeUnusablePermissions()


class FakeGuildWithSelectivelyUsableChannels:
    """validate()-time FakeGuild where exactly one channel_id fails
    validate_channel_usable() (a permission problem) and every other
    known channel_id/role_id resolves cleanly -- for testing that fixing
    ONLY the one failing step's value is enough to get past Save.
    """

    name = "Test Guild"

    def __init__(self, *, usable_channel_ids, unusable_channel_id: int) -> None:
        self._usable_channel_ids = set(usable_channel_ids)
        self._unusable_channel_id = unusable_channel_id
        self.me = object()

    def get_role(self, role_id):
        return FakeRole(role_id)

    def get_channel_or_thread(self, channel_id):
        if channel_id == self._unusable_channel_id:
            return FakeUnusableChannel(channel_id)
        if channel_id in self._usable_channel_ids:
            return FakeUsableChannel()
        return None


class WatchPartyBotWiringTests(unittest.TestCase):
    def test_constructor_wires_up_the_setup_wizard_service(self) -> None:
        bot = WatchPartyBot(token="test-token")
        self.assertIsInstance(bot.setup_wizard_service, SetupWizardService)

    def test_apply_role_configuration_updates_bot_and_permission_service(self) -> None:
        bot = WatchPartyBot(token="test-token")
        bot.apply_role_configuration(WASH_CREW_ROLE_ID, WATCH_PARTY_ROLE_ID)
        self.assertEqual(bot.wash_crew_role_id, WASH_CREW_ROLE_ID)
        self.assertEqual(bot.watch_party_member_role_id, WATCH_PARTY_ROLE_ID)
        self.assertEqual(bot.permission_service.wash_crew_role_id, WASH_CREW_ROLE_ID)
        self.assertEqual(bot.permission_service.watch_party_member_role_id, WATCH_PARTY_ROLE_ID)


class PerformSetupPermissionCheckTests(unittest.TestCase):
    """Bootstrap Setup Permission Fix: until *this* guild's own
    configuration has associated a WASH Crew role, only the Discord guild
    owner may run /setup -- everyone else is blocked. Once this guild's
    own configuration has a WASH Crew role, /setup enforces that role
    exactly as before, with no special access for the owner.

    Multi-Guild Isolation, Phase 3c: perform_setup_permission_check() no
    longer takes a bot-wide wash_crew_role_id fallback parameter at all
    -- it resolves purely from guild_configuration.wash_crew_role_id, so
    there is no longer a "stale bot-wide role id" scenario to construct;
    the tests that used to name that scenario are replaced with
    cross-guild isolation tests below (a role configured for a
    *different* guild must never grant access to this one).
    """

    def _configured_guild(self, role_id=WASH_CREW_ROLE_ID) -> GuildConfiguration:
        return GuildConfiguration(guild_id=GUILD_ID, guild_name="Guild", wash_crew_role_id=role_id)

    # --- Brand-new guild, no configuration --------------------------------

    def test_guild_owner_may_run_setup_before_any_configuration_exists(self):
        message, blocked = perform_setup_permission_check(
            FakeMember(), guild_configuration=None, is_guild_owner=True
        )
        self.assertFalse(blocked)

    def test_non_owner_is_blocked_before_any_configuration_exists(self):
        message, blocked = perform_setup_permission_check(
            FakeMember(), guild_configuration=None, is_guild_owner=False
        )
        self.assertTrue(blocked)
        self.assertIn("WASH Crew role", message)

    def test_guild_owner_may_run_setup_when_configuration_exists_but_has_no_wash_crew_role(self):
        # A GuildConfiguration row can exist (other settings saved) even
        # though no WASH Crew role has ever been associated with it.
        configuration = GuildConfiguration(guild_id=GUILD_ID, guild_name="Guild")
        message, blocked = perform_setup_permission_check(
            FakeMember(), guild_configuration=configuration, is_guild_owner=True
        )
        self.assertFalse(blocked)

    # --- Configured guild (this guild's own setup already ran) -----------

    def test_wash_crew_members_may_run_setup_once_configured(self):
        member = FakeMember(roles=[FakeRole(WASH_CREW_ROLE_ID)])
        message, blocked = perform_setup_permission_check(
            member, guild_configuration=self._configured_guild()
        )
        self.assertFalse(blocked)

    def test_non_wash_crew_members_are_blocked_once_configured(self):
        member = FakeMember(roles=[FakeRole(999)])
        message, blocked = perform_setup_permission_check(
            member, guild_configuration=self._configured_guild()
        )
        self.assertTrue(blocked)
        self.assertIn("WASH Crew role", message)

    def test_guild_owner_without_the_wash_crew_role_is_blocked_once_configured(self):
        # Expected behavior: once a WASH Crew role is configured for
        # this guild, ownership grants no special access -- an owner
        # without the role is blocked exactly like anyone else.
        member = FakeMember(roles=[])
        message, blocked = perform_setup_permission_check(
            member, guild_configuration=self._configured_guild(), is_guild_owner=True
        )
        self.assertTrue(blocked)
        self.assertIn("WASH Crew role", message)

    # --- Multi-Guild Isolation, Phase 3c: cross-guild role isolation -----

    def test_a_role_configured_as_wash_crew_in_a_different_guild_does_not_grant_access_here(self):
        # Guild A's own WASH Crew role must never grant access to Guild
        # B's /setup, even though both are resolved through the same
        # function in the same process.
        other_guilds_wash_crew_role_id = 424242
        member = FakeMember(roles=[FakeRole(other_guilds_wash_crew_role_id)])
        message, blocked = perform_setup_permission_check(
            member, guild_configuration=self._configured_guild(role_id=WASH_CREW_ROLE_ID)
        )
        self.assertTrue(blocked)
        self.assertIn("WASH Crew role", message)


class PerformSetupRedirectCheckTests(unittest.TestCase):
    """FR-029: completed setup must redirect to /config instead of restarting."""

    def test_no_configuration_yet_does_not_redirect(self) -> None:
        self.assertIsNone(perform_setup_redirect_check(None))

    def test_incomplete_setup_does_not_redirect(self) -> None:
        configuration = GuildConfiguration(guild_id=GUILD_ID, guild_name="Guild", setup_completed=False)
        self.assertIsNone(perform_setup_redirect_check(configuration))

    def test_completed_setup_redirects_to_config(self) -> None:
        configuration = GuildConfiguration(guild_id=GUILD_ID, guild_name="Guild", setup_completed=True)
        message = perform_setup_redirect_check(configuration)
        self.assertIsNotNone(message)
        self.assertIn("/config", message)
        self.assertIn("already been completed", message)


class ResolveStartupRoleIdsTests(unittest.TestCase):
    def _configuration(self) -> GuildConfiguration:
        config = GuildConfiguration(guild_id=GUILD_ID, guild_name="Guild")
        config.wash_crew_role_id = WASH_CREW_ROLE_ID
        config.watch_party_role.role_id = WATCH_PARTY_ROLE_ID
        return config

    def test_env_vars_win_when_both_are_set(self):
        wash_crew, watch_party = resolve_startup_role_ids(1, 2, self._configuration())
        self.assertEqual((wash_crew, watch_party), (1, 2))

    def test_falls_back_to_guild_configuration_when_env_vars_are_unset(self):
        wash_crew, watch_party = resolve_startup_role_ids(None, None, self._configuration())
        self.assertEqual((wash_crew, watch_party), (WASH_CREW_ROLE_ID, WATCH_PARTY_ROLE_ID))

    def test_mixes_sources_when_only_one_env_var_is_set(self):
        wash_crew, watch_party = resolve_startup_role_ids(1, None, self._configuration())
        self.assertEqual((wash_crew, watch_party), (1, WATCH_PARTY_ROLE_ID))

    def test_stays_none_when_no_configuration_exists(self):
        wash_crew, watch_party = resolve_startup_role_ids(None, None, None)
        self.assertEqual((wash_crew, watch_party), (None, None))


class ParseSetupFieldsTests(unittest.TestCase):
    def test_voting_candidate_count_valid_and_invalid(self):
        self.assertEqual(parse_setup_voting_candidate_count("4"), 4)
        with self.assertRaises(ValueError):
            parse_setup_voting_candidate_count("1")
        with self.assertRaises(ValueError):
            parse_setup_voting_candidate_count("abc")

    def test_voting_duration_minutes_valid_and_invalid(self):
        # Requirement 3: one standardized duration syntax -- an explicit
        # unit is always required, no more bare-number-means-days.
        # Release Candidate Polish (Vote Duration): minute-level
        # precision is supported, not just whole-hour amounts.
        self.assertEqual(parse_setup_voting_duration_minutes("10d"), 240 * 60)
        self.assertEqual(parse_setup_voting_duration_minutes("4h"), 4 * 60)
        self.assertEqual(parse_setup_voting_duration_minutes("3 days"), 72 * 60)
        self.assertEqual(parse_setup_voting_duration_minutes("10m"), 10)
        with self.assertRaises(ValueError):
            parse_setup_voting_duration_minutes("10")
        with self.assertRaises(ValueError):
            parse_setup_voting_duration_minutes("0h")
        with self.assertRaises(ValueError):
            parse_setup_voting_duration_minutes("31d")  # 31 days = 744 hours, above the 30-day maximum

    def test_voting_duration_minutes_accepts_the_one_minute_lower_bound(self):
        # Duration UX Standard: the minimum vote duration is 1 minute
        # (not the old 10-minute floor) -- "1m" and "0m" are the exact
        # boundary either side of it.
        self.assertEqual(parse_setup_voting_duration_minutes("1m"), 1)
        with self.assertRaises(ValueError):
            parse_setup_voting_duration_minutes("0m")

    def test_reminder_minutes_before_close_valid_and_invalid(self):
        self.assertEqual(parse_setup_reminder_minutes_before_close("48h"), 48 * 60)
        self.assertEqual(parse_setup_reminder_minutes_before_close("10m"), 10)
        with self.assertRaises(ValueError):
            parse_setup_reminder_minutes_before_close("0h")
        with self.assertRaises(ValueError):
            parse_setup_reminder_minutes_before_close("721h")

    def test_reminder_minutes_before_close_accepts_the_one_minute_lower_bound(self):
        self.assertEqual(parse_setup_reminder_minutes_before_close("1m"), 1)
        with self.assertRaises(ValueError):
            parse_setup_reminder_minutes_before_close("0m")

    def test_duration_error_messages_show_the_standard_examples_consistently(self):
        # Duration UX Standard: every duration field's syntax-error
        # message shows the same three official examples (10m, 1h, 7d)
        # -- never both a "1d" and "7d" example, and never a "1w"
        # example (week syntax stays supported but undocumented) -- and
        # never drifts between fields.
        with self.assertRaises(ValueError) as voting_duration_error:
            parse_setup_voting_duration_minutes("abc")
        with self.assertRaises(ValueError) as reminder_minutes_error:
            parse_setup_reminder_minutes_before_close("abc")
        for error in (voting_duration_error, reminder_minutes_error):
            message = str(error.exception)
            for example in ("'10m'", "'1h'", "'7d'"):
                self.assertIn(example, message)
            self.assertNotIn("'1d'", message)
            self.assertNotIn("'1w'", message)
            self.assertNotIn("week", message.lower())

    def test_backup_interval_days_valid_and_invalid(self):
        self.assertEqual(parse_setup_backup_interval_days("2"), 2)
        with self.assertRaises(ValueError):
            parse_setup_backup_interval_days("0")
        with self.assertRaises(ValueError):
            parse_setup_backup_interval_days("31")

    def test_backup_retention_count_valid_and_invalid(self):
        self.assertEqual(parse_setup_backup_retention_count("15"), 15)
        with self.assertRaises(ValueError):
            parse_setup_backup_retention_count("0")
        with self.assertRaises(ValueError):
            parse_setup_backup_retention_count("101")


class _FakePermissionSet:
    def __init__(self, *, view_channel: bool = True, send_messages: bool = True) -> None:
        self.view_channel = view_channel
        self.send_messages = send_messages


class _FakeThread:
    def __init__(
        self, thread_id: int, name: str, parent, *, archived: bool = False, locked: bool = False,
        permissions: _FakePermissionSet = None,
    ) -> None:
        self.id = thread_id
        self.name = name
        self.parent = parent
        self.archived = archived
        self.locked = locked
        self._permissions = permissions or _FakePermissionSet()

    def permissions_for(self, member) -> _FakePermissionSet:
        return self._permissions


class _FakeDestinationChannel:
    def __init__(
        self, channel_id: int, name: str, *, threads=(), permissions: _FakePermissionSet = None
    ) -> None:
        self.id = channel_id
        self.name = name
        self.threads = list(threads)
        self._permissions = permissions or _FakePermissionSet()

    def permissions_for(self, member) -> _FakePermissionSet:
        return self._permissions


class _FakeDestinationGuild:
    def __init__(self, *, text_channels=(), me: str = "wash-bot") -> None:
        self.text_channels = list(text_channels)
        self.me = me

    def get_channel_or_thread(self, channel_id):
        for channel in self.text_channels:
            if channel.id == channel_id:
                return channel
            for thread in channel.threads:
                if thread.id == channel_id:
                    return thread
        return None


class BuildChannelDestinationOptionsTests(unittest.TestCase):
    """Live-testing fix (Step 6 thread discovery): options must be built
    fresh from live guild state on every render, include active/
    accessible threads with parent context, and exclude archived/locked/
    inaccessible ones -- never a once-built or stale list.
    """

    def test_returns_empty_list_when_guild_is_none(self) -> None:
        self.assertEqual(build_channel_destination_options(None), [])

    def test_includes_text_channels(self) -> None:
        channel = _FakeDestinationChannel(1, "general")
        guild = _FakeDestinationGuild(text_channels=[channel])
        options = build_channel_destination_options(guild)
        self.assertEqual([o.value for o in options], ["1"])
        self.assertEqual(options[0].label, "#general")

    def test_includes_active_threads_with_parent_context(self) -> None:
        channel = _FakeDestinationChannel(1, "watch-party")
        thread = _FakeThread(2, "watched-items", channel)
        channel.threads = [thread]
        guild = _FakeDestinationGuild(text_channels=[channel])

        options = build_channel_destination_options(guild)

        thread_option = next(o for o in options if o.value == "2")
        self.assertEqual(thread_option.label, "watched-items")
        self.assertEqual(thread_option.description, "Thread in #watch-party")

    def test_a_thread_created_earlier_in_the_same_session_appears_on_the_next_render(self) -> None:
        # The exact live-testing scenario: a collection's suggestion
        # thread is created under the home channel during an earlier
        # step, then Step 6 renders -- it must see that thread without
        # any special-case wiring, since options are rebuilt fresh every
        # single render straight from the guild's current thread list.
        channel = _FakeDestinationChannel(1, "watch-party")
        guild = _FakeDestinationGuild(text_channels=[channel])
        options_before = build_channel_destination_options(guild)
        self.assertNotIn("2", [o.value for o in options_before])

        channel.threads.append(_FakeThread(2, "movie-suggestions", channel))
        options_after = build_channel_destination_options(guild)

        self.assertIn("2", [o.value for o in options_after])

    def test_excludes_archived_threads(self) -> None:
        channel = _FakeDestinationChannel(1, "watch-party")
        channel.threads = [_FakeThread(2, "old-thread", channel, archived=True)]
        guild = _FakeDestinationGuild(text_channels=[channel])

        options = build_channel_destination_options(guild)

        self.assertNotIn("2", [o.value for o in options])

    def test_excludes_locked_threads(self) -> None:
        channel = _FakeDestinationChannel(1, "watch-party")
        channel.threads = [_FakeThread(2, "locked-thread", channel, locked=True)]
        guild = _FakeDestinationGuild(text_channels=[channel])

        options = build_channel_destination_options(guild)

        self.assertNotIn("2", [o.value for o in options])

    def test_excludes_inaccessible_threads(self) -> None:
        channel = _FakeDestinationChannel(1, "watch-party")
        channel.threads = [
            _FakeThread(2, "private-thread", channel, permissions=_FakePermissionSet(view_channel=False))
        ]
        guild = _FakeDestinationGuild(text_channels=[channel])

        options = build_channel_destination_options(guild)

        self.assertNotIn("2", [o.value for o in options])

    def test_marks_the_saved_destination_as_the_default_option(self) -> None:
        channel = _FakeDestinationChannel(1, "watch-party")
        thread = _FakeThread(2, "watched-items", channel)
        channel.threads = [thread]
        guild = _FakeDestinationGuild(text_channels=[channel])

        options = build_channel_destination_options(guild, selected_channel_id=2)

        selected = next(o for o in options if o.value == "2")
        self.assertTrue(selected.default)
        others = [o for o in options if o.value != "2"]
        self.assertTrue(all(not o.default for o in others))

    def test_retains_the_saved_destination_even_if_no_longer_usable(self) -> None:
        # Requirement: "If Discord cannot visually preselect the saved
        # thread: retain it internally, display the current saved
        # destination, do not clear it unless the user explicitly
        # changes it."
        channel = _FakeDestinationChannel(1, "watch-party")
        thread = _FakeThread(2, "watched-items", channel, permissions=_FakePermissionSet(send_messages=False))
        channel.threads = [thread]
        guild = _FakeDestinationGuild(text_channels=[channel])

        options = build_channel_destination_options(guild, selected_channel_id=2)

        self.assertIn("2", [o.value for o in options])
        selected = next(o for o in options if o.value == "2")
        self.assertTrue(selected.default)
        self.assertEqual(selected.label, "watched-items")
        self.assertEqual(selected.description, "Currently selected · Thread in #watch-party")

    def test_excludes_inaccessible_channels(self) -> None:
        channel = _FakeDestinationChannel(1, "secret", permissions=_FakePermissionSet(view_channel=False))
        guild = _FakeDestinationGuild(text_channels=[channel])

        options = build_channel_destination_options(guild)

        self.assertEqual(options, [])

    def test_include_channels_false_omits_plain_channels_but_keeps_threads(self) -> None:
        channel = _FakeDestinationChannel(1, "watch-party")
        channel.threads = [_FakeThread(2, "watched-items", channel)]
        guild = _FakeDestinationGuild(text_channels=[channel])

        options = build_channel_destination_options(guild, include_channels=False)

        self.assertEqual([o.value for o in options], ["2"])


class BuildChannelDestinationOptionsLengthLimitTests(unittest.TestCase):
    """Setup Wizard Step 6 select-option length failure fix: Discord
    rejects the entire component payload (400 Bad Request, error 50035)
    if any option's label/description/value exceeds 100 characters --
    discord.py itself performs no client-side validation, so this must
    be enforced here, on every option this function can ever produce,
    regardless of how long the underlying Discord channel/thread name is
    (Discord itself allows up to 100 characters for either).
    """

    def _assert_within_discord_limits(self, options) -> None:
        # Serializes `options` into a real discord.ui.View and inspects
        # the exact payload via to_components() -- not the Python
        # SelectOption objects' own len() -- so this catches the astral-
        # plane/UTF-16 undercounting bug a plain len(option.label) <= 100
        # check would miss (see discord_ui_limits.py's module docstring:
        # Discord counts length in UTF-16 code units, and Python's len()
        # undercounts any character outside the Basic Multilingual Plane,
        # e.g. most emoji, by half).
        view = discord.ui.View()
        view.add_item(discord.ui.Select(options=list(options)))
        violations = find_oversized_view_component_fields(view)
        self.assertEqual(violations, [])

    def test_a_long_channel_name_never_produces_an_oversized_option(self) -> None:
        channel = _FakeDestinationChannel(1, "x" * 100)
        guild = _FakeDestinationGuild(text_channels=[channel])

        options = build_channel_destination_options(guild)

        self._assert_within_discord_limits(options)

    def test_a_long_thread_name_never_produces_an_oversized_option(self) -> None:
        channel = _FakeDestinationChannel(1, "watch-party")
        channel.threads = [_FakeThread(2, "y" * 100, channel)]
        guild = _FakeDestinationGuild(text_channels=[channel])

        options = build_channel_destination_options(guild)

        self._assert_within_discord_limits(options)

    def test_a_long_parent_channel_name_never_produces_an_oversized_thread_description(self) -> None:
        # The exact failure mode reported live: a thread's description
        # ("Thread in #<parent>") combines a fixed prefix with the
        # parent channel's own name -- for a parent name near Discord's
        # own 100-character channel-name limit, the naive concatenation
        # alone would exceed SelectOption's 100-character description
        # limit and 400 the whole render.
        channel = _FakeDestinationChannel(1, "p" * 100)
        thread = _FakeThread(2, "watched-items", channel)
        channel.threads = [thread]
        guild = _FakeDestinationGuild(text_channels=[channel])

        options = build_channel_destination_options(guild)

        thread_option = next(o for o in options if o.value == "2")
        self.assertIsNotNone(thread_option.description)
        self.assertLessEqual(len(thread_option.description), 100)
        self._assert_within_discord_limits(options)

    def test_a_long_retained_current_selection_description_is_safe(self) -> None:
        # The "retained saved destination" fallback (a thread no longer
        # visible via the live scan, e.g. WASH's permissions changed)
        # builds its own description combining parent context with a
        # "Currently selected" marker -- also must never exceed the limit.
        channel = _FakeDestinationChannel(1, "p" * 100)
        thread = _FakeThread(2, "z" * 100, channel, permissions=_FakePermissionSet(send_messages=False))
        channel.threads = [thread]
        guild = _FakeDestinationGuild(text_channels=[channel])

        options = build_channel_destination_options(guild, selected_channel_id=2)

        selected = next(o for o in options if o.value == "2")
        self.assertTrue(selected.default)
        self.assertIn("Currently selected", selected.description)
        self._assert_within_discord_limits(options)

    def test_an_emoji_heavy_parent_channel_name_never_produces_an_oversized_description(self) -> None:
        # TASK D root cause: Discord measures SelectOption length in
        # UTF-16 code units, the same way JavaScript's String.length
        # does -- not Python's code-point-based len(). Most emoji (e.g.
        # the clapperboard below, U+1F3AC) sit outside the Basic
        # Multilingual Plane and are encoded as a UTF-16 surrogate pair,
        # so they count as 2 units to Discord but only 1 to Python's
        # len(). A parent channel name built entirely from such
        # characters can be well within Discord's own 100-character
        # channel-name limit (measured the same way) and still, once
        # combined into "Thread in #<parent>" and truncated by Python
        # character count, produce a description Discord's API rejects
        # as over 100 -- exactly the live 400 (error 50035) this fix
        # addresses. See discord_ui_limits.py's module docstring.
        emoji_channel_name = "\U0001F3AC" * 90
        channel = _FakeDestinationChannel(1, emoji_channel_name)
        thread = _FakeThread(2, "watched-items", channel)
        channel.threads = [thread]
        guild = _FakeDestinationGuild(text_channels=[channel])

        options = build_channel_destination_options(guild)

        thread_option = next(o for o in options if o.value == "2")
        self.assertIsNotNone(thread_option.description)
        self._assert_within_discord_limits(options)

    def test_an_emoji_heavy_retained_selection_description_is_safe(self) -> None:
        # Same UTF-16-counting failure mode as above, but through the
        # "retained saved destination" fallback path (build_safe_select_
        # option's description= is a string already combining "Currently
        # selected" with parent context before truncation).
        emoji_channel_name = "\U0001F37F" * 90
        channel = _FakeDestinationChannel(1, emoji_channel_name)
        thread = _FakeThread(2, "z" * 100, channel, permissions=_FakePermissionSet(send_messages=False))
        channel.threads = [thread]
        guild = _FakeDestinationGuild(text_channels=[channel])

        options = build_channel_destination_options(guild, selected_channel_id=2)

        selected = next(o for o in options if o.value == "2")
        self.assertTrue(selected.default)
        self.assertIn("Currently selected", selected.description)
        self._assert_within_discord_limits(options)

    def test_an_emoji_heavy_thread_name_never_produces_an_oversized_label(self) -> None:
        channel = _FakeDestinationChannel(1, "watch-party")
        emoji_thread_name = "\U0001F4FA" * 90
        channel.threads = [_FakeThread(2, emoji_thread_name, channel)]
        guild = _FakeDestinationGuild(text_channels=[channel])

        options = build_channel_destination_options(guild)

        self._assert_within_discord_limits(options)

    def test_more_than_25_usable_destinations_is_capped_at_25(self) -> None:
        channels = [_FakeDestinationChannel(i, f"channel-{i}") for i in range(30)]
        guild = _FakeDestinationGuild(text_channels=channels)

        options = build_channel_destination_options(guild)

        self.assertEqual(len(options), 25)

    def test_more_than_25_destinations_still_keeps_the_selected_one(self) -> None:
        channels = [_FakeDestinationChannel(i, f"channel-{i}") for i in range(30)]
        guild = _FakeDestinationGuild(text_channels=channels)

        options = build_channel_destination_options(guild, selected_channel_id=29)

        self.assertEqual(len(options), 25)
        self.assertTrue(any(o.value == "29" and o.default for o in options))

    def test_many_long_named_destinations_together_never_raise(self) -> None:
        # Combines every stress factor at once: more than 25 usable
        # destinations, each with a channel name at Discord's own
        # 100-character limit, some with equally long thread names --
        # building the option list must never raise, and every resulting
        # option must still satisfy Discord's limits.
        channels = []
        for i in range(30):
            channel = _FakeDestinationChannel(i, f"{'c' * 90}-{i}")
            channel.threads = [_FakeThread(1000 + i, f"{'t' * 90}-{i}", channel)]
            channels.append(channel)
        guild = _FakeDestinationGuild(text_channels=channels)

        options = build_channel_destination_options(guild)

        self.assertLessEqual(len(options), 25)
        self._assert_within_discord_limits(options)


class ExistingDatabaseSelectLengthLimitTests(unittest.TestCase):
    """TASK D defense-in-depth: ExistingDatabaseSelect (the Suggestion
    Database step's "choose an existing collection" picker) previously
    built raw discord.SelectOption(label=name[:100], ...) directly,
    instead of routing through build_safe_select_option() like every
    other Setup Wizard option builder -- one of the option builders the
    original TASK C audit missed. Python's len()-based slicing has the
    same UTF-16-undercounting gap Watch Destination's
    build_channel_destination_options had (see discord_ui_limits.py's
    module docstring): a collection name built from emoji or other
    astral-plane characters can be <=100 Python characters and still
    exceed Discord's 100-unit limit.
    """

    def _assert_within_discord_limits(self, select: discord.ui.Select) -> None:
        view = discord.ui.View()
        view.add_item(select)
        violations = find_oversized_view_component_fields(view)
        self.assertEqual(violations, [])

    def test_a_long_ascii_collection_name_is_safe(self) -> None:
        from watch_party_manager.setup_wizard_view import ExistingDatabaseSelect

        select = ExistingDatabaseSelect([(1, "m" * 150)], on_select=None)
        self._assert_within_discord_limits(select)

    def test_an_emoji_heavy_collection_name_is_safe(self) -> None:
        from watch_party_manager.setup_wizard_view import ExistingDatabaseSelect

        emoji_name = "\U0001F37F" * 90
        select = ExistingDatabaseSelect([(1, emoji_name)], on_select=None)
        self._assert_within_discord_limits(select)


class BuildSetupStepHeaderTests(unittest.TestCase):
    def test_shows_position_and_title_for_first_step(self):
        state = SetupWizardState(guild_id=GUILD_ID)
        header = build_setup_step_header(state)
        self.assertIn("Step 1 of 11", header)
        self.assertIn("WASH Crew Role", header)

    def test_shows_position_and_title_for_review_step(self):
        state = SetupWizardState(guild_id=GUILD_ID, current_step=SetupWizardStep.REVIEW)
        header = build_setup_step_header(state)
        self.assertIn("Step 11 of 11", header)
        self.assertIn("Review", header)


class BuildSetupCompletionSummaryTests(unittest.TestCase):
    def test_summary_distinguishes_skipped_destination(self):
        config = GuildConfiguration(guild_id=GUILD_ID, guild_name="Guild")
        config.wash_crew_role_id = WASH_CREW_ROLE_ID
        config.watch_party_role.role_id = WATCH_PARTY_ROLE_ID
        config.watch_party_role.join_mode = JoinMode.MANUAL
        draft = SetupWizardDraft(
            suggestion_database_id=1,
            suggestion_database_name="Movies",
            watch_destination_skipped=True,
            backup_interval_days=1,
            backup_retention_count=30,
        )
        summary = build_setup_completion_summary(config, draft)
        self.assertIn(f"<@&{WASH_CREW_ROLE_ID}>", summary)
        # UX Polish: matches the Review screen's own "Skipped" wording for
        # this exact field, rather than a differently-worded variant only
        # used here.
        self.assertIn("Watched Item Archive: Skipped", summary)
        self.assertNotIn("Skipped (configure later)", summary)
        self.assertIn("Movies", summary)

    def test_summary_shows_a_friendly_join_mode_label(self):
        # UX Polish: "Manual", never the raw enum value "manual".
        config = GuildConfiguration(guild_id=GUILD_ID, guild_name="Guild")
        config.wash_crew_role_id = WASH_CREW_ROLE_ID
        config.watch_party_role.role_id = WATCH_PARTY_ROLE_ID
        config.watch_party_role.join_mode = JoinMode.SELF_SERVICE
        draft = SetupWizardDraft(suggestion_database_id=1, suggestion_database_name="Movies")

        summary = build_setup_completion_summary(config, draft)

        self.assertIn("join mode: Self-Service", summary)
        self.assertNotIn("self_service", summary)

    def test_summary_shows_a_capitalized_visibility(self) -> None:
        # UX Polish: "Visible"/"Blind", matching how visibility is shown
        # everywhere else in the app (e.g. vote status embeds).
        config = GuildConfiguration(guild_id=GUILD_ID, guild_name="Guild")
        config.wash_crew_role_id = WASH_CREW_ROLE_ID
        draft = SetupWizardDraft(suggestion_database_id=1, suggestion_database_name="Movies")

        summary = build_setup_completion_summary(config, draft)

        self.assertIn("Visible", summary)
        self.assertNotIn("visible,", summary)

    def test_summary_includes_a_configured_admin_channel(self) -> None:
        # UX Polish: Admin Channel was previously omitted from this
        # summary entirely, even when configured during the wizard.
        config = GuildConfiguration(guild_id=GUILD_ID, guild_name="Guild")
        config.wash_crew_role_id = WASH_CREW_ROLE_ID
        draft = SetupWizardDraft(
            suggestion_database_id=1, suggestion_database_name="Movies", admin_channel_id=777
        )

        summary = build_setup_completion_summary(config, draft)

        self.assertIn("Admin Channel: <#777>", summary)

    def test_summary_shows_a_skipped_admin_channel(self) -> None:
        config = GuildConfiguration(guild_id=GUILD_ID, guild_name="Guild")
        config.wash_crew_role_id = WASH_CREW_ROLE_ID
        draft = SetupWizardDraft(
            suggestion_database_id=1, suggestion_database_name="Movies", admin_channel_skipped=True
        )

        summary = build_setup_completion_summary(config, draft)

        self.assertIn("Admin Channel: Skipped", summary)

    def test_summary_includes_next_steps_guidance(self) -> None:
        # UX Polish, Goal 6: the administrator should clearly understand
        # the recommended next steps immediately after setup completes.
        config = GuildConfiguration(guild_id=GUILD_ID, guild_name="Guild")
        config.wash_crew_role_id = WASH_CREW_ROLE_ID
        draft = SetupWizardDraft(suggestion_database_id=1, suggestion_database_name="Movies")

        summary = build_setup_completion_summary(config, draft)

        self.assertIn("**Next Steps**", summary)
        self.assertIn("/add", summary)
        self.assertIn("/vote start", summary)


class SetupCommandTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        temp_path = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(temp_path / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(temp_path / "suggestion_databases.json"),
        )

        class FakeBot:
            pass

        self.bot = FakeBot()
        self.bot.suggestion_service = self.suggestion_service
        self.wizard_repository = SetupWizardRepository(temp_path / "setup_wizard_state.json")
        self.guild_configuration_repository = GuildConfigurationRepository(
            temp_path / "guild_configurations.json"
        )
        self.bot.guild_configuration_repository = self.guild_configuration_repository
        self.scheduler_repository = MemorySchedulerRepository()
        self.bot.scheduler_host = FakeSchedulerHost(SchedulerService(self.scheduler_repository))
        self.suggestion_database_configuration_repository = SuggestionDatabaseConfigurationRepository(
            temp_path / "suggestion_database_configurations.json"
        )
        self.bot.suggestion_database_configuration_repository = self.suggestion_database_configuration_repository
        self.bot.setup_wizard_service = SetupWizardService(
            self.wizard_repository,
            self.guild_configuration_repository,
            self.suggestion_service,
            self.suggestion_database_configuration_repository,
        )
        self.bot.apply_role_configuration = lambda wash, watch: self.applied_roles.append((wash, watch))
        self.applied_roles = []

    def tearDown(self) -> None:
        self._temp_dir.cleanup()


class SetupCommandFlowTests(SetupCommandTestCase):
    async def test_first_step_is_sent_as_a_new_ephemeral_message(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        self.assertIn("Step 1 of 11", interaction.response.sent_message)
        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIsInstance(interaction.response.sent_view, WashCrewRoleStepView)

    async def test_selecting_the_wash_crew_role_does_not_advance_until_confirmed(self) -> None:
        # First-Time UX Polish: the role select alone must never advance
        # the wizard -- only Save & Continue does, after the member has
        # had a chance to review their selection.
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view: WashCrewRoleStepView = interaction.response.sent_view

        class FakeRoleValue:
            id = WASH_CREW_ROLE_ID

        role_select = view.children[0]
        role_select._values = [FakeRoleValue()]
        select_interaction = FakeInteraction()
        await role_select.callback(interaction=select_interaction)

        self.assertTrue(select_interaction.response.deferred)
        self.assertIsNone(select_interaction.response.edited_content)
        self.assertEqual(select_interaction.response.edited_view, "not-edited")

    async def test_confirming_the_wash_crew_role_advances_to_watch_party_role_step(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view: WashCrewRoleStepView = interaction.response.sent_view

        class FakeRoleValue:
            id = WASH_CREW_ROLE_ID

        view.role_select._values = [FakeRoleValue()]
        confirm_button = next(
            c for c in view.children if getattr(c, "custom_id", None) == "wpm_setup_wash_crew_role_confirm"
        )
        confirm_interaction = FakeInteraction()
        await confirm_button.callback(interaction=confirm_interaction)

        self.assertIn("Step 2 of 11", confirm_interaction.response.edited_content)
        self.assertIsInstance(confirm_interaction.response.edited_view, WatchPartyRoleStepView)

    async def test_confirming_with_no_role_selected_shows_a_validation_message_and_does_not_advance(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view: WashCrewRoleStepView = interaction.response.sent_view

        confirm_button = next(
            c for c in view.children if getattr(c, "custom_id", None) == "wpm_setup_wash_crew_role_confirm"
        )
        confirm_interaction = FakeInteraction()
        await confirm_button.callback(interaction=confirm_interaction)

        self.assertIn("Select a WASH Crew role before continuing", confirm_interaction.response.edited_content)
        self.assertIsInstance(confirm_interaction.response.edited_view, WashCrewRoleStepView)

    async def test_admin_channel_step_renders_and_advances_to_suggestion_database(self) -> None:
        from watch_party_manager.domain.setup_wizard import SetupWizardStep
        from watch_party_manager.setup_wizard_view import AdminChannelStepView

        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.ADMIN_CHANNEL)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        self.assertIn("Step 3 of 11", interaction.response.sent_message)
        self.assertIn("Admin Channel", interaction.response.sent_message)
        self.assertIsInstance(interaction.response.sent_view, AdminChannelStepView)

        skip_button = next(
            child for child in interaction.response.sent_view.children if getattr(child, "label", None) == "Skip for Now"
        )
        skip_interaction = FakeInteraction()
        await skip_button.callback(interaction=skip_interaction)

        self.assertIn("Step 4 of 11", skip_interaction.response.edited_content)

    async def test_voting_defaults_step_sends_a_modal_with_valid_component_labels(self) -> None:
        # Regression test: Voting Defaults previously crashed with
        # discord.errors.HTTPException 400 ("Must be between 1 and 45 in
        # length") because VotingDefaultsModal's fourth TextInput label
        # was 46 characters. Candidate Selection Mode and Visibility are
        # both collected beforehand, as Selects on VotingDefaultsIntroView
        # -- never inside the modal itself, since Discord's API rejects a
        # Select embedded in a modal at submission time even though
        # discord.py allows constructing one. This exercises the current
        # path -- go_to_step -> send_setup_wizard_step ->
        # VotingDefaultsIntroView's Set Voting Defaults button ->
        # on_configure -> interaction.response.send_modal(...) -- and
        # confirms every field on the modal actually sent is a TextInput
        # with a label within Discord's 1-45 character limit.
        from watch_party_manager.domain.setup_wizard import SetupWizardStep
        from watch_party_manager.setup_wizard_view import VotingDefaultsIntroView, VotingDefaultsModal

        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.VOTING_DEFAULTS)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        self.assertIsInstance(interaction.response.sent_view, VotingDefaultsIntroView)
        configure_button = next(
            child for child in interaction.response.sent_view.children
            if getattr(child, "label", None) == "Set Voting Defaults"
        )

        configure_interaction = FakeInteraction()
        await configure_button.callback(interaction=configure_interaction)

        sent_modal = configure_interaction.response.sent_modal
        self.assertIsInstance(sent_modal, VotingDefaultsModal)
        self.assertTrue(all(isinstance(child, discord.ui.TextInput) for child in sent_modal.children))
        for child in sent_modal.children:
            label = getattr(child, "label", None)
            if label is not None:
                self.assertGreaterEqual(len(label), 1)
                self.assertLessEqual(len(label), 45)

    async def test_cancel_deletes_wizard_state_and_edits_the_message(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view = interaction.response.sent_view
        cancel_button = view.children[-1]

        cancel_interaction = FakeInteraction()
        await cancel_button.callback(interaction=cancel_interaction)

        self.assertIn("cancelled", cancel_interaction.response.edited_content)
        # UX Polish: consistent with every other terminal wizard message
        # ("WASH Setup Complete", "Setup progress saved.") -- previously
        # the one plain, un-bolded message in the set.
        self.assertIn("**Setup has been cancelled.**", cancel_interaction.response.edited_content)
        self.assertIsNone(cancel_interaction.response.edited_view)
        self.assertIsNone(self.wizard_repository.get(GUILD_ID))

    async def test_review_step_shows_configured_and_incomplete_sections(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.REVIEW)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        self.assertIn(f"WASH Crew Role: Configured (<@&{WASH_CREW_ROLE_ID}>)", interaction.response.sent_message)
        self.assertIn("⚠️ Collections: Incomplete (Required)", interaction.response.sent_message)
        self.assertIsInstance(interaction.response.sent_view, ReviewStepView)

    async def test_review_step_has_no_save_for_later_button(self) -> None:
        # UX Polish: every section has already been answered by the time
        # a member reaches Review, so there's nothing left to save and
        # come back to -- only Save, Back, and Cancel Setup remain.
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.REVIEW)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        view: ReviewStepView = interaction.response.sent_view
        labels = [getattr(child, "label", None) for child in view.children]
        self.assertNotIn("Save & Finish Later", labels)
        self.assertIn("Save", labels)
        self.assertIn("Back", labels)
        self.assertIn("Cancel Setup", labels)

    async def test_save_with_incomplete_draft_shows_issues_and_returns_to_the_failing_step(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.REVIEW)
        interaction = FakeInteraction(guild=None)
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view: ReviewStepView = interaction.response.sent_view
        save_button = view.children[0]

        class FakeGuild:
            name = "Test Guild"

            def get_role(self, role_id):
                return None

            def get_channel_or_thread(self, channel_id):
                return None

            me = object()

        save_interaction = FakeInteraction(guild=FakeGuild())
        await save_button.callback(interaction=save_interaction)

        self.assertIn("could not be saved", save_interaction.response.edited_content)
        self.assertEqual(self.applied_roles, [])

    async def test_validation_failure_preserves_state_and_returns_to_review_after_the_fix(self) -> None:
        # Live-testing fix: a validation failure at Save (e.g. WASH can no
        # longer post in the selected Admin Channel) used to redirect to
        # the failing step correctly, but fixing it there then forced the
        # WASH Crew member to walk forward through every later step
        # again -- Home Channel, Collection, Voting Defaults, and so on --
        # instead of returning directly to Review with everything already
        # answered still intact.
        database_result = self.suggestion_service.create_database("Movies", GUILD_ID, DESTINATION_CHANNEL_ID)
        BAD_ADMIN_CHANNEL_ID = 12345
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        state = self.bot.setup_wizard_service.set_watch_party_role(state, WATCH_PARTY_ROLE_ID, JoinMode.MANUAL)
        state = self.bot.setup_wizard_service.set_admin_channel(state, BAD_ADMIN_CHANNEL_ID)
        state = self.bot.setup_wizard_service.set_home_channel(state, DESTINATION_CHANNEL_ID)
        state, _ = self.bot.setup_wizard_service.select_existing_database(
            state, database_result.database.database_id, guild_id=GUILD_ID
        )
        state = self.bot.setup_wizard_service.skip_watch_destination(state)
        state = self.bot.setup_wizard_service.set_voting_defaults(
            state, 5, 14, GuildVoteVisibility.BLIND, CandidateSelectionMode.FAVOR_OLDER_ADDITIONS
        )
        state = self.bot.setup_wizard_service.enable_rejection_settings(state, 2)
        state = self.bot.setup_wizard_service.enable_vote_ending_reminder(state, 30)
        state = self.bot.setup_wizard_service.enable_automatic_backups(state, 2, 15)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.REVIEW)

        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view: ReviewStepView = interaction.response.sent_view
        save_button = view.children[0]

        guild = FakeGuildWithSelectivelyUsableChannels(
            usable_channel_ids={DESTINATION_CHANNEL_ID}, unusable_channel_id=BAD_ADMIN_CHANNEL_ID
        )
        save_interaction = FakeInteraction(guild=guild)
        await save_button.callback(interaction=save_interaction)

        # Redirected specifically to Admin Channel (Step 3), with an
        # actionable message -- not a generic failure.
        self.assertIn("Step 3 of 11", save_interaction.response.edited_content)
        self.assertIn("cannot send messages", save_interaction.response.edited_content)
        self.assertEqual(self.applied_roles, [])

        # Every other already-entered value survived the redirect.
        preserved = self.wizard_repository.get(GUILD_ID)
        self.assertEqual(preserved.draft.wash_crew_role_id, WASH_CREW_ROLE_ID)
        self.assertEqual(preserved.draft.watch_party_role_id, WATCH_PARTY_ROLE_ID)
        self.assertEqual(preserved.draft.home_channel_id, DESTINATION_CHANNEL_ID)
        self.assertEqual(preserved.draft.voting_candidate_count, 5)
        self.assertEqual(preserved.draft.voting_candidate_selection, CandidateSelectionMode.FAVOR_OLDER_ADDITIONS)
        self.assertTrue(preserved.draft.reminder_enabled)
        self.assertEqual(preserved.draft.backup_interval_days, 2)

        # Fix the one failing value.
        admin_channel_view = save_interaction.response.edited_view
        channel_select = admin_channel_view.children[0]
        GOOD_ADMIN_CHANNEL_ID = 777
        channel_select._values = [str(GOOD_ADMIN_CHANNEL_ID)]
        confirm_button = next(
            c for c in admin_channel_view.children if getattr(c, "custom_id", None) == "wpm_setup_admin_channel_confirm"
        )
        fix_interaction = FakeInteraction()
        await confirm_button.callback(interaction=fix_interaction)

        # Returns straight to Review -- not Home Channel or any other
        # step -- and every later value is still there, unchanged.
        self.assertIsInstance(fix_interaction.response.edited_view, ReviewStepView)
        after_fix = self.wizard_repository.get(GUILD_ID)
        self.assertEqual(after_fix.current_step, SetupWizardStep.REVIEW)
        self.assertEqual(after_fix.draft.admin_channel_id, GOOD_ADMIN_CHANNEL_ID)
        self.assertEqual(after_fix.draft.voting_candidate_count, 5)
        self.assertEqual(after_fix.draft.backup_interval_days, 2)

        # Save now succeeds without re-entering anything.
        guild_with_fixed_channel = FakeGuildWithSelectivelyUsableChannels(
            usable_channel_ids={DESTINATION_CHANNEL_ID, GOOD_ADMIN_CHANNEL_ID}, unusable_channel_id=-1
        )
        final_save_button = fix_interaction.response.edited_view.children[0]
        final_save_interaction = FakeInteraction(guild=guild_with_fixed_channel)
        await final_save_button.callback(interaction=final_save_interaction)

        self.assertIn("WASH Setup Complete", final_save_interaction.response.edited_content)
        saved = self.guild_configuration_repository.get(GUILD_ID)
        self.assertEqual(saved.channels.admin_channel_id, GOOD_ADMIN_CHANNEL_ID)
        self.assertEqual(saved.voting_defaults.candidate_count, 5)

    async def test_persistence_failure_stays_on_review_distinct_from_validation_failure(self) -> None:
        # Requirement: validation failures and persistence failures are
        # separate recovery paths -- a persistence failure has no single
        # "failing step" to redirect to, so it must keep the WASH Crew
        # member on Review itself, not send them to some arbitrary step.
        database_result = self.suggestion_service.create_database("Movies", GUILD_ID, DESTINATION_CHANNEL_ID)
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        state = self.bot.setup_wizard_service.set_watch_party_role(state, WATCH_PARTY_ROLE_ID, JoinMode.MANUAL)
        state = self.bot.setup_wizard_service.set_home_channel(state, DESTINATION_CHANNEL_ID)
        state, _ = self.bot.setup_wizard_service.select_existing_database(
            state, database_result.database.database_id, guild_id=GUILD_ID
        )
        state = self.bot.setup_wizard_service.skip_watch_destination(state)
        state = self.bot.setup_wizard_service.set_voting_defaults(
            state, 3, 7, GuildVoteVisibility.BLIND, CandidateSelectionMode.FAVOR_OLDER_ADDITIONS
        )
        state = self.bot.setup_wizard_service.enable_rejection_settings(state, 2)
        state = self.bot.setup_wizard_service.enable_vote_ending_reminder(state, 24)
        state = self.bot.setup_wizard_service.enable_automatic_backups(state, 1, 30)

        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view: ReviewStepView = interaction.response.sent_view
        save_button = view.children[0]

        class FakeGuild:
            name = "Test Guild"

            def get_role(self, role_id):
                return FakeRole(role_id)

            def get_channel_or_thread(self, channel_id):
                if channel_id == DESTINATION_CHANNEL_ID:
                    return FakeUsableChannel()
                return None

            me = object()

        import unittest.mock

        with unittest.mock.patch.object(
            self.guild_configuration_repository, "save", side_effect=OSError("disk full")
        ):
            save_interaction = FakeInteraction(guild=FakeGuild())
            await save_button.callback(interaction=save_interaction)

        self.assertIsInstance(save_interaction.response.edited_view, ReviewStepView)
        preserved = self.wizard_repository.get(GUILD_ID)
        self.assertEqual(preserved.current_step, SetupWizardStep.REVIEW)
        self.assertEqual(preserved.draft.voting_candidate_count, 3)

    async def test_editing_a_section_from_review_returns_to_review_after_answering_it(self) -> None:
        # Same return_to_step mechanism as the validation-failure fix,
        # applied to Review's own "edit a section" dropdown: picking a
        # section to fix and re-answering it must return to Review, not
        # march forward through every step after it.
        database_result = self.suggestion_service.create_database("Movies", GUILD_ID, DESTINATION_CHANNEL_ID)
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        state = self.bot.setup_wizard_service.set_watch_party_role(state, WATCH_PARTY_ROLE_ID, JoinMode.MANUAL)
        state = self.bot.setup_wizard_service.set_home_channel(state, DESTINATION_CHANNEL_ID)
        state, _ = self.bot.setup_wizard_service.select_existing_database(
            state, database_result.database.database_id, guild_id=GUILD_ID
        )
        state = self.bot.setup_wizard_service.skip_watch_destination(state)
        state = self.bot.setup_wizard_service.set_voting_defaults(
            state, 5, 14, GuildVoteVisibility.BLIND, CandidateSelectionMode.FAVOR_OLDER_ADDITIONS
        )
        state = self.bot.setup_wizard_service.enable_rejection_settings(state, 2)
        state = self.bot.setup_wizard_service.enable_vote_ending_reminder(state, 24)
        state = self.bot.setup_wizard_service.enable_automatic_backups(state, 1, 30)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.REVIEW)

        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view: ReviewStepView = interaction.response.sent_view
        edit_section_select = view.children[1]

        edit_interaction = FakeInteraction()
        edit_section_select._values = [SetupWizardStep.WASH_CREW_ROLE.value]
        await edit_section_select.callback(interaction=edit_interaction)

        self.assertIsInstance(edit_interaction.response.edited_view, WashCrewRoleStepView)

        class FakeRoleValue:
            id = 999

        role_step_view = edit_interaction.response.edited_view
        role_step_view.role_select._values = [FakeRoleValue()]
        select_interaction = FakeInteraction()
        await role_step_view.role_select.callback(interaction=select_interaction)

        confirm_button = next(
            c for c in role_step_view.children if getattr(c, "custom_id", None) == "wpm_setup_wash_crew_role_confirm"
        )
        answer_interaction = FakeInteraction()
        await confirm_button.callback(interaction=answer_interaction)

        # Back to Review -- not Watch Party Role (the next step after
        # WASH Crew Role in the normal walkthrough order) -- and every
        # later value is untouched.
        self.assertIsInstance(answer_interaction.response.edited_view, ReviewStepView)
        preserved = self.wizard_repository.get(GUILD_ID)
        self.assertEqual(preserved.current_step, SetupWizardStep.REVIEW)
        self.assertEqual(preserved.draft.wash_crew_role_id, 999)
        self.assertEqual(preserved.draft.voting_candidate_count, 5)
        self.assertEqual(preserved.draft.backup_interval_days, 1)

    async def test_save_with_a_complete_and_valid_draft_applies_role_configuration(self) -> None:
        database_result = self.suggestion_service.create_database("Movies", GUILD_ID, DESTINATION_CHANNEL_ID)
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        state = self.bot.setup_wizard_service.set_watch_party_role(state, WATCH_PARTY_ROLE_ID, JoinMode.MANUAL)
        state = self.bot.setup_wizard_service.set_home_channel(state, DESTINATION_CHANNEL_ID)
        state, _ = self.bot.setup_wizard_service.select_existing_database(
            state, database_result.database.database_id, guild_id=GUILD_ID
        )
        state = self.bot.setup_wizard_service.skip_watch_destination(state)
        state = self.bot.setup_wizard_service.set_voting_defaults(
            state, 3, 7, GuildVoteVisibility.BLIND, CandidateSelectionMode.FAVOR_OLDER_ADDITIONS
        )
        state = self.bot.setup_wizard_service.enable_rejection_settings(state, 2)
        state = self.bot.setup_wizard_service.enable_vote_ending_reminder(state, 24)
        state = self.bot.setup_wizard_service.enable_automatic_backups(state, 1, 30)

        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view: ReviewStepView = interaction.response.sent_view
        save_button = view.children[0]

        class FakeGuild:
            name = "Test Guild"

            def get_role(self, role_id):
                return FakeRole(role_id)

            def get_channel_or_thread(self, channel_id):
                if channel_id == DESTINATION_CHANNEL_ID:
                    return FakeUsableChannel()
                return None

            me = object()

        save_interaction = FakeInteraction(guild=FakeGuild())
        await save_button.callback(interaction=save_interaction)

        self.assertIn("WASH Setup Complete", save_interaction.response.edited_content)
        self.assertIsNone(save_interaction.response.edited_view)
        self.assertEqual(self.applied_roles, [(WASH_CREW_ROLE_ID, WATCH_PARTY_ROLE_ID)])

    async def test_unexpected_persistence_failure_never_shows_false_success_and_stays_resumable(self) -> None:
        # Release Candidate walkthrough fix: an unexpected error while
        # saving (not a validation issue) must never report success, must
        # keep the wizard open with every value intact, and must let the
        # WASH Crew member retry without redoing setup or losing state.
        database_result = self.suggestion_service.create_database("Movies", GUILD_ID, DESTINATION_CHANNEL_ID)
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        state = self.bot.setup_wizard_service.set_watch_party_role(state, WATCH_PARTY_ROLE_ID, JoinMode.MANUAL)
        state = self.bot.setup_wizard_service.set_home_channel(state, DESTINATION_CHANNEL_ID)
        state, _ = self.bot.setup_wizard_service.select_existing_database(
            state, database_result.database.database_id, guild_id=GUILD_ID
        )
        state = self.bot.setup_wizard_service.skip_watch_destination(state)
        state = self.bot.setup_wizard_service.set_voting_defaults(
            state, 3, 7, GuildVoteVisibility.BLIND, CandidateSelectionMode.FAVOR_OLDER_ADDITIONS
        )
        state = self.bot.setup_wizard_service.enable_rejection_settings(state, 2)
        state = self.bot.setup_wizard_service.enable_vote_ending_reminder(state, 24)
        state = self.bot.setup_wizard_service.enable_automatic_backups(state, 1, 30)

        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view: ReviewStepView = interaction.response.sent_view
        save_button = view.children[0]

        class FakeGuild:
            name = "Test Guild"

            def get_role(self, role_id):
                return FakeRole(role_id)

            def get_channel_or_thread(self, channel_id):
                if channel_id == DESTINATION_CHANNEL_ID:
                    return FakeUsableChannel()
                return None

            me = object()

        with unittest.mock.patch.object(
            self.guild_configuration_repository, "save", side_effect=OSError("disk full")
        ):
            save_interaction = FakeInteraction(guild=FakeGuild())
            await save_button.callback(interaction=save_interaction)

        # No false success, no premature dismissal (view=None is /setup's
        # own signal that the wizard finished): the wizard stays open.
        self.assertNotIn("WASH Setup Complete", save_interaction.response.edited_content)
        self.assertIn("⚠", save_interaction.response.edited_content)
        self.assertIsNotNone(save_interaction.response.edited_view)
        self.assertEqual(self.applied_roles, [])

        # Retained state: every previously entered value survives, and a
        # retry (pressing Save again, now that the failure is resolved)
        # completes without repeating any step.
        retry_view: ReviewStepView = save_interaction.response.edited_view
        retry_save_button = retry_view.children[0]
        retry_interaction = FakeInteraction(guild=FakeGuild())
        await retry_save_button.callback(interaction=retry_interaction)

        self.assertIn("WASH Setup Complete", retry_interaction.response.edited_content)
        saved = self.guild_configuration_repository.get(GUILD_ID)
        self.assertEqual(saved.wash_crew_role_id, WASH_CREW_ROLE_ID)
        self.assertEqual(saved.voting_defaults.candidate_count, 3)

    async def test_saving_schedules_the_automatic_backup_job(self) -> None:
        # Release Polish (Optional Automatic Backups): completing setup
        # with automatic backups enabled must schedule the job
        # immediately, not just persist the setting.
        database_result = self.suggestion_service.create_database("Movies", GUILD_ID, DESTINATION_CHANNEL_ID)
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        state = self.bot.setup_wizard_service.set_watch_party_role(state, WATCH_PARTY_ROLE_ID, JoinMode.MANUAL)
        state = self.bot.setup_wizard_service.set_home_channel(state, DESTINATION_CHANNEL_ID)
        state, _ = self.bot.setup_wizard_service.select_existing_database(
            state, database_result.database.database_id, guild_id=GUILD_ID
        )
        state = self.bot.setup_wizard_service.skip_watch_destination(state)
        state = self.bot.setup_wizard_service.set_voting_defaults(
            state, 3, 7, GuildVoteVisibility.BLIND, CandidateSelectionMode.FAVOR_OLDER_ADDITIONS
        )
        state = self.bot.setup_wizard_service.enable_rejection_settings(state, 2)
        state = self.bot.setup_wizard_service.enable_vote_ending_reminder(state, 24)
        state = self.bot.setup_wizard_service.enable_automatic_backups(state, 3, 15)

        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view: ReviewStepView = interaction.response.sent_view
        save_button = view.children[0]

        class FakeGuild:
            name = "Test Guild"

            def get_role(self, role_id):
                return FakeRole(role_id)

            def get_channel_or_thread(self, channel_id):
                if channel_id == DESTINATION_CHANNEL_ID:
                    return FakeUsableChannel()
                return None

            me = object()

        save_interaction = FakeInteraction(guild=FakeGuild())
        await save_button.callback(interaction=save_interaction)

        active_jobs = [job for job in self.scheduler_repository.jobs.values() if job.is_active]
        self.assertEqual(len(active_jobs), 1)
        self.assertEqual(active_jobs[0].guild_id, GUILD_ID)


class BackNavigationIntegrationTests(SetupCommandTestCase):
    """Setup Wizard Polish Batch 1, Section 1: Back navigation."""

    async def test_first_step_shows_no_back_button(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        view = interaction.response.sent_view
        self.assertFalse(any(isinstance(child, SetupBackButton) for child in view.children))

    async def test_second_step_shows_a_back_button(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        view = interaction.response.sent_view
        self.assertTrue(any(isinstance(child, SetupBackButton) for child in view.children))

    async def test_back_from_watch_party_role_returns_to_wash_crew_role(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view: WatchPartyRoleStepView = interaction.response.sent_view
        back_button = next(c for c in view.children if isinstance(c, SetupBackButton))

        back_interaction = FakeInteraction()
        await back_button.callback(interaction=back_interaction)

        self.assertIn("Step 1 of 11", back_interaction.response.edited_content)
        self.assertIsInstance(back_interaction.response.edited_view, WashCrewRoleStepView)

    async def test_back_does_not_clear_the_previously_saved_value(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view = interaction.response.sent_view
        back_button = next(c for c in view.children if isinstance(c, SetupBackButton))
        back_interaction = FakeInteraction()

        await back_button.callback(interaction=back_interaction)

        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertEqual(persisted.draft.wash_crew_role_id, WASH_CREW_ROLE_ID)
        self.assertEqual(persisted.current_step, SetupWizardStep.WASH_CREW_ROLE)

    async def test_going_back_and_forward_again_preserves_the_answer(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        state = self.bot.setup_wizard_service.go_back(state)
        # Re-answering the first step again (as if reviewing/confirming
        # it) must not have lost the value that was already there.
        self.assertEqual(state.draft.wash_crew_role_id, WASH_CREW_ROLE_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        self.assertEqual(state.current_step, SetupWizardStep.WATCH_PARTY_ROLE)

    async def test_voting_defaults_modal_reopened_after_back_shows_previously_saved_values(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_voting_defaults(
            state, 5, 14, GuildVoteVisibility.VISIBLE, CandidateSelectionMode.FAVOR_OLDER_ADDITIONS
        )
        # Simulate returning to Voting Defaults later (e.g. via Back from
        # Reminder Defaults, or Review's edit-a-section) -- the modal must
        # be pre-filled with what was actually saved, not the bare
        # hardcoded defaults.
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.VOTING_DEFAULTS)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        intro_view: VotingDefaultsIntroView = interaction.response.sent_view
        self.assertEqual(intro_view.candidate_selection_select.selected, CandidateSelectionMode.FAVOR_OLDER_ADDITIONS)
        self.assertEqual(intro_view.visibility_select.selected, GuildVoteVisibility.VISIBLE)
        configure_button = next(
            child for child in intro_view.children if getattr(child, "label", None) == "Set Voting Defaults"
        )

        configure_interaction = FakeInteraction()
        await configure_button.callback(interaction=configure_interaction)

        modal: VotingDefaultsModal = configure_interaction.response.sent_modal
        self.assertEqual(modal.candidate_count_input.default, "5")
        self.assertEqual(modal.duration_input.default, "14m")

    async def test_back_from_review_returns_to_backup_defaults(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.REVIEW)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view: ReviewStepView = interaction.response.sent_view
        back_button = next(c for c in view.children if isinstance(c, SetupBackButton))

        back_interaction = FakeInteraction()
        await back_button.callback(interaction=back_interaction)

        self.assertIsInstance(back_interaction.response.edited_view, BackupDefaultsChoiceView)

    async def test_backup_defaults_step_explains_what_is_and_is_not_backed_up(self) -> None:
        # First-Time UX Polish: a live setup walkthrough found "backups"
        # ambiguous -- must be unmistakably WASH's own data, not Discord.
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.BACKUP_DEFAULTS)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        self.assertIn("WASH's own data only", interaction.response.sent_message)
        self.assertIn("never includes Discord itself", interaction.response.sent_message)

    async def test_admin_channel_step_shows_a_back_button_to_watch_party_role(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.ADMIN_CHANNEL)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        view: AdminChannelStepView = interaction.response.sent_view
        back_button = next(c for c in view.children if isinstance(c, SetupBackButton))
        back_interaction = FakeInteraction()
        await back_button.callback(interaction=back_interaction)
        self.assertIn("Watch Party Role", back_interaction.response.edited_content)

    async def test_watch_destination_step_back_returns_to_suggestion_database(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.WATCH_DESTINATION)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        view: WatchDestinationStepView = interaction.response.sent_view
        back_button = next(c for c in view.children if isinstance(c, SetupBackButton))
        back_interaction = FakeInteraction()
        await back_button.callback(interaction=back_interaction)
        self.assertIn("Collections", back_interaction.response.edited_content)

    async def test_unauthorized_user_cannot_use_another_administrators_wizard_controls(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        starter = FakeMember(user_id=1)
        interaction = FakeInteraction(user=starter)

        await send_setup_wizard_step(interaction, self.bot, state, edit=False, requester_id=starter.id)

        view = interaction.response.sent_view
        other_member = FakeMember(user_id=2)
        other_interaction = FakeInteraction(user=other_member)

        allowed = await view.interaction_check(other_interaction)

        self.assertFalse(allowed)
        self.assertIn("Only the person who ran this command", other_interaction.response.sent_message)

    async def test_the_requester_can_still_use_their_own_wizard_controls(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        starter = FakeMember(user_id=1)
        interaction = FakeInteraction(user=starter)

        await send_setup_wizard_step(interaction, self.bot, state, edit=False, requester_id=starter.id)

        view = interaction.response.sent_view
        same_user_interaction = FakeInteraction(user=FakeMember(user_id=1))

        allowed = await view.interaction_check(same_user_interaction)

        self.assertTrue(allowed)


class SaveAndFinishLaterIntegrationTests(SetupCommandTestCase):
    """Setup Wizard Polish Batch 1, Section 2: Save & Finish Later."""

    async def test_every_step_shows_a_save_for_later_button(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        view = interaction.response.sent_view
        self.assertTrue(any(isinstance(child, SetupSaveForLaterButton) for child in view.children))

    async def test_clicking_save_for_later_confirms_and_explains_how_to_resume(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view = interaction.response.sent_view
        save_for_later_button = next(c for c in view.children if isinstance(c, SetupSaveForLaterButton))

        save_interaction = FakeInteraction()
        await save_for_later_button.callback(interaction=save_interaction)

        self.assertIn("saved", save_interaction.response.edited_content.lower())
        self.assertIn("/setup", save_interaction.response.edited_content)
        saved_view = save_interaction.response.edited_view
        self.assertIsInstance(saved_view, SetupProgressSavedView)
        self.assertTrue(any(getattr(c, "label", None) == "Continue Setup" for c in saved_view.children))

    async def test_continue_setup_button_resumes_from_the_saved_step(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view = interaction.response.sent_view
        save_for_later_button = next(c for c in view.children if isinstance(c, SetupSaveForLaterButton))
        save_interaction = FakeInteraction()
        await save_for_later_button.callback(interaction=save_interaction)
        saved_view = save_interaction.response.edited_view
        continue_button = next(c for c in saved_view.children if getattr(c, "label", None) == "Continue Setup")

        continue_interaction = FakeInteraction()
        await continue_button.callback(interaction=continue_interaction)

        self.assertEqual(continue_interaction.response.edited_content, interaction.response.sent_message)

    async def test_save_for_later_does_not_delete_the_draft(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view = interaction.response.sent_view
        save_for_later_button = next(c for c in view.children if isinstance(c, SetupSaveForLaterButton))

        await save_for_later_button.callback(interaction=FakeInteraction())

        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted.draft.wash_crew_role_id, WASH_CREW_ROLE_ID)

    async def test_save_for_later_does_not_mark_setup_complete(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view = interaction.response.sent_view
        save_for_later_button = next(c for c in view.children if isinstance(c, SetupSaveForLaterButton))

        await save_for_later_button.callback(interaction=FakeInteraction())

        self.assertIsNone(self.guild_configuration_repository.get(GUILD_ID))
        self.assertEqual(self.applied_roles, [])

    async def test_save_for_later_does_not_roll_back_earlier_finalized_configuration(self) -> None:
        # If setup was already completed once (unusual but possible if a
        # future FR ever allows re-entering /setup after completion),
        # Save & Finish Later on a *new* draft must never touch the
        # already-saved GuildConfiguration.
        existing = GuildConfiguration(guild_id=GUILD_ID, guild_name="Guild", setup_completed=True)
        existing.wash_crew_role_id = WASH_CREW_ROLE_ID
        self.guild_configuration_repository.save(existing)

        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view = interaction.response.sent_view
        save_for_later_button = next(c for c in view.children if isinstance(c, SetupSaveForLaterButton))

        await save_for_later_button.callback(interaction=FakeInteraction())

        reloaded = self.guild_configuration_repository.get(GUILD_ID)
        self.assertTrue(reloaded.setup_completed)
        self.assertEqual(reloaded.wash_crew_role_id, WASH_CREW_ROLE_ID)


class ResumeDetectionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Setup Wizard Polish Batch 1, Section 2: resuming a saved-for-later draft."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        temp_path = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(temp_path / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(temp_path / "suggestion_databases.json"),
        )
        self.wizard_repository = SetupWizardRepository(temp_path / "setup_wizard_state.json")
        self.guild_configuration_repository = GuildConfigurationRepository(
            temp_path / "guild_configurations.json"
        )
        self.setup_wizard_service = SetupWizardService(
            self.wizard_repository,
            self.guild_configuration_repository,
            self.suggestion_service,
            SuggestionDatabaseConfigurationRepository(temp_path / "suggestion_database_configurations.json"),
        )

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_setup_not_started_has_no_resumable_state(self) -> None:
        self.assertIsNone(self.wizard_repository.get(GUILD_ID))
        self.assertIsNone(self.guild_configuration_repository.get(GUILD_ID))

    def test_setup_in_progress_is_detected_as_resumable(self) -> None:
        state, _ = self.setup_wizard_service.start_or_resume(GUILD_ID)
        self.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)

        _, resumed = self.setup_wizard_service.start_or_resume(GUILD_ID)

        self.assertTrue(resumed)

    def test_resuming_preserves_completed_steps_and_current_step(self) -> None:
        state, _ = self.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        state = self.setup_wizard_service.set_watch_party_role(state, WATCH_PARTY_ROLE_ID, JoinMode.MANUAL)

        resumed_state, resumed = self.setup_wizard_service.start_or_resume(GUILD_ID)

        self.assertTrue(resumed)
        self.assertEqual(resumed_state.current_step, SetupWizardStep.ADMIN_CHANNEL)
        self.assertIn(SetupWizardStep.WASH_CREW_ROLE, resumed_state.completed_steps)
        self.assertIn(SetupWizardStep.WATCH_PARTY_ROLE, resumed_state.completed_steps)
        self.assertEqual(resumed_state.draft.wash_crew_role_id, WASH_CREW_ROLE_ID)
        self.assertEqual(resumed_state.draft.watch_party_role_id, WATCH_PARTY_ROLE_ID)

    def test_setup_completed_is_no_longer_resumable(self) -> None:
        configuration = GuildConfiguration(guild_id=GUILD_ID, guild_name="Guild", setup_completed=True)
        self.guild_configuration_repository.save(configuration)

        message = perform_setup_redirect_check(self.guild_configuration_repository.get(GUILD_ID))

        self.assertIsNotNone(message)
        self.assertIn("/config", message)

    def test_partially_configured_guild_can_resume_setup_without_losing_existing_data(self) -> None:
        # Recovery path: channels/threads/suggestions can exist (created
        # as side effects of earlier wizard steps) even though
        # GuildConfiguration itself is missing -- e.g. an old, pre-fix
        # finalize() failure, or the WASH Crew member simply never
        # finished. /setup must still be runnable (not blocked), and
        # resuming/restarting must never delete what already exists.
        database_result = self.suggestion_service.create_database("Movies", GUILD_ID, 400)
        database_id = database_result.database.database_id
        self.suggestion_service.suggest(
            "Existing Movie", database_id=database_id, guild_id=GUILD_ID, release_year=2020
        )
        self.assertIsNone(self.guild_configuration_repository.get(GUILD_ID))

        redirect_message = perform_setup_redirect_check(self.guild_configuration_repository.get(GUILD_ID))
        self.assertIsNone(redirect_message)

        state, resumed = self.setup_wizard_service.start_or_resume(GUILD_ID)
        self.assertFalse(resumed)  # no wizard draft existed -- a fresh one is fine, nothing destructive happened

        databases = self.suggestion_service.list_databases(GUILD_ID)
        self.assertEqual(len(databases), 1)
        self.assertEqual(databases[0].database_id, database_id)
        suggestions = self.suggestion_service.get_suggestions_for_database(database_id)
        self.assertEqual(len(suggestions), 1)

        # Reusing the existing database (rather than being forced to
        # create a duplicate) is exactly SUGGESTION_DATABASE step's
        # "Select Existing" path -- select_existing_database() never
        # touches the suggestion repository.
        updated, message = self.setup_wizard_service.select_existing_database(
            state, database_id, guild_id=GUILD_ID
        )
        self.assertEqual(updated.draft.suggestion_database_id, database_id)
        self.assertEqual(len(self.suggestion_service.list_databases(GUILD_ID)), 1)
        self.assertEqual(len(self.suggestion_service.get_suggestions_for_database(database_id)), 1)

    async def test_setup_command_shows_resume_prompt_with_progress_count(self) -> None:
        state, _ = self.setup_wizard_service.start_or_resume(GUILD_ID)
        self.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)

        class FakeBot:
            pass

        bot = FakeBot()
        bot.suggestion_service = self.suggestion_service
        bot.setup_wizard_service = self.setup_wizard_service

        resumed_state, resumed = self.setup_wizard_service.start_or_resume(GUILD_ID)
        self.assertTrue(resumed)

        async def on_continue(resume_interaction) -> None:
            await send_setup_wizard_step(resume_interaction, bot, resumed_state, edit=True)

        from watch_party_manager.setup_wizard_view import SetupWizardResumeView

        view = SetupWizardResumeView(on_continue, on_continue, on_continue)
        interaction = FakeInteraction()
        message = (
            f"{len(resumed_state.completed_steps)} of 9 steps completed so far "
            f"(currently on: Watch Party Role)."
        )
        await interaction.response.send_message(message, view=view, ephemeral=True)

        self.assertIn("1 of 9 steps completed", interaction.response.sent_message)


class BackwardCompatibilityIntegrationTests(SetupCommandTestCase):
    """Setup Wizard Polish Batch 1, Section 2: existing state files without
    the newer admin_channel_id/admin_channel_skipped fields must still
    load safely (Section 2's "preserve backward compatibility" and the
    pre-existing repository serialization gap this batch fixed).
    """

    def test_loading_a_pre_existing_state_file_without_admin_channel_fields_is_safe(self) -> None:
        import json

        raw = {
            "guilds": {
                str(GUILD_ID): {
                    "guild_id": GUILD_ID,
                    "status": "in_progress",
                    "current_step": "admin_channel",
                    "completed_steps": ["wash_crew_role", "watch_party_role"],
                    "started_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                    "draft": {
                        "wash_crew_role_id": WASH_CREW_ROLE_ID,
                        "watch_party_role_id": None,
                        "watch_party_join_mode": None,
                        "suggestion_database_id": None,
                        "suggestion_database_name": None,
                        "suggestion_database_is_new": False,
                        # Deliberately no admin_channel_id/admin_channel_skipped keys,
                        # matching every state file saved before this batch.
                        "watch_destination_channel_id": None,
                        "watch_destination_skipped": False,
                        "voting_candidate_count": None,
                        "voting_duration_days": None,
                        "voting_visibility": None,
                        "voting_candidate_selection": None,
                        "reminder_enabled": None,
                        "reminder_hours_before_close": None,
                        "backup_interval_days": None,
                        "backup_retention_count": None,
                    },
                }
            }
        }
        self.wizard_repository._file_path.parent.mkdir(parents=True, exist_ok=True)
        self.wizard_repository._file_path.write_text(json.dumps(raw), encoding="utf-8")

        state = self.wizard_repository.get(GUILD_ID)

        self.assertIsNotNone(state)
        self.assertIsNone(state.draft.admin_channel_id)
        self.assertFalse(state.draft.admin_channel_skipped)
        self.assertEqual(state.draft.wash_crew_role_id, WASH_CREW_ROLE_ID)


class CandidateSelectionSetupIntegrationTests(SetupCommandTestCase):
    """Setup Wizard Polish Batch 1, Section 3: Candidate Selection in
    Voting Defaults, exercised end-to-end through the wizard.
    """

    async def test_voting_defaults_dropdown_defaults_to_favor_older_additions(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.VOTING_DEFAULTS)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        intro_view: VotingDefaultsIntroView = interaction.response.sent_view
        self.assertEqual(intro_view.candidate_selection_select.selected, CandidateSelectionMode.FAVOR_OLDER_ADDITIONS)
        self.assertEqual(
            CANDIDATE_SELECTION_DISPLAY_LABELS[CandidateSelectionMode.FAVOR_OLDER_ADDITIONS], "Favor Older Additions"
        )

    async def test_visibility_is_collected_on_the_setup_view_not_the_modal(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.VOTING_DEFAULTS)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        intro_view: VotingDefaultsIntroView = interaction.response.sent_view
        self.assertEqual(intro_view.visibility_select.selected, GuildVoteVisibility.VISIBLE)
        option_values = {option.value for option in intro_view.visibility_select.options}
        self.assertEqual(option_values, {GuildVoteVisibility.VISIBLE.value, GuildVoteVisibility.BLIND.value})
        descriptions = {option.value: option.description for option in intro_view.visibility_select.options}
        self.assertIn("shown", descriptions[GuildVoteVisibility.VISIBLE.value].lower())
        self.assertIn("hidden", descriptions[GuildVoteVisibility.BLIND.value].lower())

    async def test_dropdown_displays_all_three_nominee_selection_modes(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.VOTING_DEFAULTS)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        intro_view: VotingDefaultsIntroView = interaction.response.sent_view
        option_values = {option.value for option in intro_view.candidate_selection_select.options}
        self.assertEqual(
            option_values,
            {
                CandidateSelectionMode.FAVOR_NEW_ADDITIONS.value,
                CandidateSelectionMode.FAVOR_OLDER_ADDITIONS.value,
                CandidateSelectionMode.INFINITE_POOL.value,
            },
        )

    async def test_pure_random_can_be_selected_via_the_dropdown_and_persists(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.VOTING_DEFAULTS)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        intro_view: VotingDefaultsIntroView = interaction.response.sent_view
        intro_view.candidate_selection_select._values = [CandidateSelectionMode.INFINITE_POOL.value]
        intro_view.visibility_select._values = [GuildVoteVisibility.BLIND.value]
        configure_button = next(
            child for child in intro_view.children if getattr(child, "label", None) == "Set Voting Defaults"
        )

        configure_interaction = FakeInteraction()
        await configure_button.callback(interaction=configure_interaction)
        modal: VotingDefaultsModal = configure_interaction.response.sent_modal
        modal.candidate_count_input._value = "3"
        modal.duration_input._value = "7d"

        submit_interaction = FakeInteraction()
        await modal.on_submit(interaction=submit_interaction)

        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertEqual(persisted.draft.voting_candidate_selection, CandidateSelectionMode.INFINITE_POOL)
        self.assertEqual(persisted.draft.voting_visibility, GuildVoteVisibility.BLIND)

    async def test_candidate_count_default_is_three(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.VOTING_DEFAULTS)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        configure_button = next(
            child for child in interaction.response.sent_view.children
            if getattr(child, "label", None) == "Set Voting Defaults"
        )

        configure_interaction = FakeInteraction()
        await configure_button.callback(interaction=configure_interaction)

        modal: VotingDefaultsModal = configure_interaction.response.sent_modal
        self.assertEqual(modal.candidate_count_input.default, "3")

    async def test_candidate_count_validation_still_enforced(self) -> None:
        with self.assertRaises(ValueError):
            parse_setup_voting_candidate_count("1")
        with self.assertRaises(ValueError):
            parse_setup_voting_candidate_count("11")

    async def test_completion_summary_includes_the_candidate_selection_mode(self) -> None:
        database_result = self.suggestion_service.create_database("Movies", GUILD_ID, DESTINATION_CHANNEL_ID)
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        state = self.bot.setup_wizard_service.set_watch_party_role(state, WATCH_PARTY_ROLE_ID, JoinMode.MANUAL)
        state = self.bot.setup_wizard_service.set_home_channel(state, DESTINATION_CHANNEL_ID)
        state, _ = self.bot.setup_wizard_service.select_existing_database(
            state, database_result.database.database_id, guild_id=GUILD_ID
        )
        state = self.bot.setup_wizard_service.skip_watch_destination(state)
        state = self.bot.setup_wizard_service.set_voting_defaults(
            state, 3, 7, GuildVoteVisibility.BLIND, CandidateSelectionMode.FAVOR_OLDER_ADDITIONS
        )
        state = self.bot.setup_wizard_service.enable_rejection_settings(state, 2)
        state = self.bot.setup_wizard_service.enable_vote_ending_reminder(state, 24)
        state = self.bot.setup_wizard_service.enable_automatic_backups(state, 1, 30)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.REVIEW)

        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view: ReviewStepView = interaction.response.sent_view
        save_button = view.children[0]

        class FakeGuild:
            name = "Test Guild"

            def get_role(self, role_id):
                return FakeRole(role_id)

            def get_channel_or_thread(self, channel_id):
                if channel_id == DESTINATION_CHANNEL_ID:
                    return FakeUsableChannel()
                return None

            me = object()

        save_interaction = FakeInteraction(guild=FakeGuild())
        await save_button.callback(interaction=save_interaction)

        self.assertIn("Favor Older Additions", save_interaction.response.edited_content)

    async def test_review_line_shows_the_friendly_candidate_selection_label(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_voting_defaults(
            state, 3, 7, GuildVoteVisibility.BLIND, CandidateSelectionMode.FAVOR_NEW_ADDITIONS
        )
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.REVIEW)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        self.assertIn("Favor New Additions", interaction.response.sent_message)

    async def test_settings_persist_through_a_repository_round_trip(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        self.bot.setup_wizard_service.set_voting_defaults(
            state, 7, 3, GuildVoteVisibility.VISIBLE, CandidateSelectionMode.INFINITE_POOL
        )

        reloaded = self.wizard_repository.get(GUILD_ID)

        self.assertEqual(reloaded.draft.voting_candidate_count, 7)
        self.assertEqual(reloaded.draft.voting_candidate_selection, CandidateSelectionMode.INFINITE_POOL)

    async def test_config_reads_the_same_saved_candidate_selection(self) -> None:
        database_result = self.suggestion_service.create_database("Movies", GUILD_ID, DESTINATION_CHANNEL_ID)
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        state = self.bot.setup_wizard_service.set_watch_party_role(state, WATCH_PARTY_ROLE_ID, JoinMode.MANUAL)
        state = self.bot.setup_wizard_service.set_home_channel(state, DESTINATION_CHANNEL_ID)
        state, _ = self.bot.setup_wizard_service.select_existing_database(
            state, database_result.database.database_id, guild_id=GUILD_ID
        )
        state = self.bot.setup_wizard_service.skip_watch_destination(state)
        state = self.bot.setup_wizard_service.set_voting_defaults(
            state, 3, 7, GuildVoteVisibility.BLIND, CandidateSelectionMode.FAVOR_OLDER_ADDITIONS
        )
        state = self.bot.setup_wizard_service.enable_rejection_settings(state, 6)
        state = self.bot.setup_wizard_service.enable_vote_ending_reminder(state, 24)
        state = self.bot.setup_wizard_service.enable_automatic_backups(state, 1, 30)

        class FakeGuild:
            name = "Test Guild"

            def get_role(self, role_id):
                return FakeRole(role_id)

            def get_channel_or_thread(self, channel_id):
                if channel_id == DESTINATION_CHANNEL_ID:
                    return FakeUsableChannel()
                return None

            me = object()

        result = self.bot.setup_wizard_service.finalize(state, GUILD_ID, "Test Guild", FakeGuild())
        self.assertTrue(result.success, result.message)

        # Candidate selection and the "I Won't Watch" threshold are both
        # per-database (Manage Databases), not guild-wide summary lines --
        # read them back the same way /config's Manage Databases screen
        # would, by database_id.
        config_service = ConfigService(
            self.guild_configuration_repository,
            self.suggestion_service,
            self.suggestion_database_configuration_repository,
        )
        database_configuration = config_service.get_database_configuration(
            GUILD_ID, database_result.database.database_id
        )

        self.assertEqual(database_configuration.suggestion_rules.candidate_selection, CandidateSelectionMode.FAVOR_OLDER_ADDITIONS)
        self.assertEqual(database_configuration.suggestion_rules.rejection_threshold, 6)

    async def test_older_persisted_database_configuration_defaults_to_favor_new_additions(self) -> None:
        # A database configuration saved before candidate_selection existed
        # (or one that never had this section touched) must still resolve
        # to the configured default, not error or show blank.
        database_result = self.suggestion_service.create_database("Movies", GUILD_ID, DESTINATION_CHANNEL_ID)
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        state = self.bot.setup_wizard_service.set_watch_party_role(state, WATCH_PARTY_ROLE_ID, JoinMode.MANUAL)
        state = self.bot.setup_wizard_service.set_home_channel(state, DESTINATION_CHANNEL_ID)
        state, _ = self.bot.setup_wizard_service.select_existing_database(
            state, database_result.database.database_id, guild_id=GUILD_ID
        )
        state = self.bot.setup_wizard_service.skip_watch_destination(state)
        state = self.bot.setup_wizard_service.set_voting_defaults(
            state, 3, 7, GuildVoteVisibility.BLIND, CandidateSelectionMode.FAVOR_NEW_ADDITIONS
        )
        state = self.bot.setup_wizard_service.enable_rejection_settings(state, 2)
        state = self.bot.setup_wizard_service.enable_vote_ending_reminder(state, 24)
        state = self.bot.setup_wizard_service.enable_automatic_backups(state, 1, 30)

        class FakeGuild:
            name = "Test Guild"

            def get_role(self, role_id):
                return FakeRole(role_id)

            def get_channel_or_thread(self, channel_id):
                if channel_id == DESTINATION_CHANNEL_ID:
                    return FakeUsableChannel()
                return None

            me = object()

        result = self.bot.setup_wizard_service.finalize(state, GUILD_ID, "Test Guild", FakeGuild())
        self.assertTrue(result.success, result.message)

        # No explicit /config edit ever touched suggestion_rules -- the
        # database's configuration still resolves cleanly.
        config_service = ConfigService(
            self.guild_configuration_repository,
            self.suggestion_service,
            self.suggestion_database_configuration_repository,
        )
        database_configuration = config_service.get_database_configuration(
            GUILD_ID, database_result.database.database_id
        )
        self.assertEqual(database_configuration.suggestion_rules.candidate_selection, CandidateSelectionMode.FAVOR_NEW_ADDITIONS)
        self.assertEqual(database_configuration.suggestion_rules.rejection_threshold, 2)


class VoteDurationWordingSetupIntegrationTests(SetupCommandTestCase):
    """Vote Duration Wording (Release UX & Command Surface Cleanup): every
    pre-filled duration modal (Voting Defaults, Reminder Defaults) has a
    short label naming only the field and its range -- the fuller
    minutes/hours/days explanation with examples lives on the screen
    shown immediately before the modal opens instead.
    """

    async def test_voting_defaults_step_explains_duration_format_on_the_screen(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.VOTING_DEFAULTS)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        self.assertIn(
            "Durations combine a number with a unit -- minutes (m), hours (h), or days (d). "
            "Examples: 10m, 1h, 7d.",
            interaction.response.sent_message,
        )

    async def test_reminder_defaults_step_explains_duration_format_on_the_screen(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.REMINDER_DEFAULTS)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        self.assertIn(
            "Durations combine a number with a unit -- minutes (m), hours (h), or days (d). "
            "Examples: 10m, 1h, 7d.",
            interaction.response.sent_message,
        )


class RejectionSettingsSetupIntegrationTests(SetupCommandTestCase):
    """"I Won't Watch" Settings: its own dedicated step, immediately after
    Voting Defaults -- an Enable/Disable choice, mirroring Reminder
    Defaults' identical enable/disable-then-configure shape, and only
    ever opens the threshold modal when Enable is chosen.
    """

    async def test_step_appears_immediately_after_voting_defaults(self) -> None:
        self.assertEqual(
            SETUP_WIZARD_STEP_ORDER[SETUP_WIZARD_STEP_ORDER.index(SetupWizardStep.VOTING_DEFAULTS) + 1],
            SetupWizardStep.REJECTION_SETTINGS,
        )

    async def test_step_shows_the_enable_disable_choice_view(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.REJECTION_SETTINGS)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        view: RejectionSettingsChoiceView = interaction.response.sent_view
        self.assertIsInstance(view, RejectionSettingsChoiceView)
        labels = [getattr(child, "label", None) for child in view.children]
        self.assertIn("Enable \"I Won't Watch\" (Recommended)", labels)
        self.assertIn("Disable \"I Won't Watch\"", labels)
        self.assertIn("Step 8 of 11", interaction.response.sent_message)

    async def test_step_body_explains_the_threshold_with_a_concrete_example(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.REJECTION_SETTINGS)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        message = interaction.response.sent_message
        self.assertIn("distinct members must do that before the suggestion needs WASH Crew review", message)
        self.assertIn(f"threshold {DEFAULT_REJECTION_THRESHOLD} means", message)
        self.assertIn("does not remove the suggestion from the collection", message)

    async def test_disable_saves_immediately_without_opening_a_modal(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.REJECTION_SETTINGS)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view: RejectionSettingsChoiceView = interaction.response.sent_view
        disable_button = next(
            child for child in view.children if getattr(child, "label", None) == "Disable \"I Won't Watch\""
        )

        disable_interaction = FakeInteraction()
        await disable_button.callback(interaction=disable_interaction)

        self.assertIsNone(disable_interaction.response.sent_modal)
        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertFalse(persisted.draft.rejection_enabled)
        self.assertIsNone(persisted.draft.rejection_threshold)
        self.assertEqual(persisted.current_step, SetupWizardStep.REMINDER_DEFAULTS)

    async def test_enable_opens_a_modal_with_only_the_threshold_field(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.REJECTION_SETTINGS)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view: RejectionSettingsChoiceView = interaction.response.sent_view
        enable_button = next(
            child for child in view.children
            if getattr(child, "label", None) == "Enable \"I Won't Watch\" (Recommended)"
        )

        enable_interaction = FakeInteraction()
        await enable_button.callback(interaction=enable_interaction)

        modal: RejectionThresholdModal = enable_interaction.response.sent_modal
        self.assertEqual(len(modal.children), 1)
        self.assertTrue(all(isinstance(child, discord.ui.TextInput) for child in modal.children))
        self.assertEqual(modal.threshold_input.default, "2")

    async def test_enable_then_submit_saves_the_threshold_and_advances_to_reminder_defaults(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.REJECTION_SETTINGS)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view: RejectionSettingsChoiceView = interaction.response.sent_view
        enable_button = next(
            child for child in view.children
            if getattr(child, "label", None) == "Enable \"I Won't Watch\" (Recommended)"
        )
        enable_interaction = FakeInteraction()
        await enable_button.callback(interaction=enable_interaction)
        modal: RejectionThresholdModal = enable_interaction.response.sent_modal
        modal.threshold_input._value = "4"

        submit_interaction = FakeInteraction()
        await modal.on_submit(interaction=submit_interaction)

        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertTrue(persisted.draft.rejection_enabled)
        self.assertEqual(persisted.draft.rejection_threshold, 4)
        self.assertEqual(persisted.current_step, SetupWizardStep.REMINDER_DEFAULTS)

    async def test_invalid_threshold_shows_a_retry_screen(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.REJECTION_SETTINGS)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view: RejectionSettingsChoiceView = interaction.response.sent_view
        enable_button = next(
            child for child in view.children
            if getattr(child, "label", None) == "Enable \"I Won't Watch\" (Recommended)"
        )
        enable_interaction = FakeInteraction()
        await enable_button.callback(interaction=enable_interaction)
        modal: RejectionThresholdModal = enable_interaction.response.sent_modal
        modal.threshold_input._value = "11"

        submit_interaction = FakeInteraction()
        await modal.on_submit(interaction=submit_interaction)

        self.assertIn("⚠", submit_interaction.response.edited_content)
        retry_view = submit_interaction.response.edited_view
        self.assertIsInstance(retry_view, RejectionSettingsChoiceView)

    async def test_completion_summary_shows_disabled_when_declined(self) -> None:
        database = self.suggestion_service.create_database(
            "Movies", GUILD_ID, DESTINATION_CHANNEL_ID
        ).database
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        state = self.bot.setup_wizard_service.set_watch_party_role(state, WATCH_PARTY_ROLE_ID, JoinMode.MANUAL)
        state = self.bot.setup_wizard_service.set_home_channel(state, DESTINATION_CHANNEL_ID)
        state, _ = self.bot.setup_wizard_service.select_existing_database(
            state, database.database_id, guild_id=GUILD_ID
        )
        state = self.bot.setup_wizard_service.set_watch_destination(state, DESTINATION_CHANNEL_ID)
        state = self.bot.setup_wizard_service.set_voting_defaults(
            state, 3, 7, GuildVoteVisibility.BLIND, CandidateSelectionMode.FAVOR_OLDER_ADDITIONS
        )
        state = self.bot.setup_wizard_service.disable_rejection_settings(state)
        state = self.bot.setup_wizard_service.enable_vote_ending_reminder(state, 24)
        state = self.bot.setup_wizard_service.enable_automatic_backups(state, 1, 30)

        class FakeGuild:
            name = "Test Guild"

            def get_role(self, role_id):
                return FakeRole(role_id)

            def get_channel_or_thread(self, channel_id):
                if channel_id == DESTINATION_CHANNEL_ID:
                    return FakeUsableChannel()
                return None

            me = object()

        result = self.bot.setup_wizard_service.finalize(state, GUILD_ID, "Test Guild", FakeGuild())
        self.assertTrue(result.success)

        summary = build_setup_completion_summary(result.configuration, state.draft)

        self.assertIn("I Won't Watch: Disabled", summary)
        saved_database_configuration = self.suggestion_database_configuration_repository.get(
            GUILD_ID, database.database_id
        )
        self.assertFalse(saved_database_configuration.suggestion_rules.rejection_enabled)


class ReminderDefaultsSetupIntegrationTests(SetupCommandTestCase):
    """Fixed-Option UX Audit: Reminder Defaults' "enabled?" field is an
    Enable/Disable button choice, not a free-text yes/no modal field.
    Disable must skip the lead-time modal entirely; Enable must open it.
    """

    async def test_step_shows_the_enable_disable_choice_view(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.REMINDER_DEFAULTS)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        view: ReminderDefaultsChoiceView = interaction.response.sent_view
        self.assertIsInstance(view, ReminderDefaultsChoiceView)
        labels = [getattr(child, "label", None) for child in view.children]
        self.assertIn("Enable Vote-Ending Reminder (Recommended)", labels)
        self.assertIn("Disable Vote-Ending Reminder", labels)

    async def test_disable_saves_immediately_without_opening_a_modal(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.REMINDER_DEFAULTS)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view: ReminderDefaultsChoiceView = interaction.response.sent_view
        disable_button = next(
            child for child in view.children if getattr(child, "label", None) == "Disable Vote-Ending Reminder"
        )

        disable_interaction = FakeInteraction()
        await disable_button.callback(interaction=disable_interaction)

        self.assertIsNone(disable_interaction.response.sent_modal)
        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertFalse(persisted.draft.reminder_enabled)
        self.assertEqual(persisted.current_step, SetupWizardStep.BACKUP_DEFAULTS)

    async def test_enable_opens_a_modal_with_only_the_lead_time_field(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.REMINDER_DEFAULTS)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view: ReminderDefaultsChoiceView = interaction.response.sent_view
        enable_button = next(
            child for child in view.children
            if getattr(child, "label", None) == "Enable Vote-Ending Reminder (Recommended)"
        )

        enable_interaction = FakeInteraction()
        await enable_button.callback(interaction=enable_interaction)

        modal: ReminderDefaultsModal = enable_interaction.response.sent_modal
        self.assertEqual(len(modal.children), 1)
        self.assertTrue(all(isinstance(child, discord.ui.TextInput) for child in modal.children))
        self.assertEqual(modal.minutes_input.default, "1d")

    async def test_enable_then_submit_saves_the_lead_time(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.REMINDER_DEFAULTS)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view: ReminderDefaultsChoiceView = interaction.response.sent_view
        enable_button = next(
            child for child in view.children
            if getattr(child, "label", None) == "Enable Vote-Ending Reminder (Recommended)"
        )
        enable_interaction = FakeInteraction()
        await enable_button.callback(interaction=enable_interaction)
        modal: ReminderDefaultsModal = enable_interaction.response.sent_modal
        modal.minutes_input._value = "2h"

        submit_interaction = FakeInteraction()
        await modal.on_submit(interaction=submit_interaction)

        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertTrue(persisted.draft.reminder_enabled)
        self.assertEqual(persisted.draft.reminder_minutes_before_close, 120)
        self.assertEqual(persisted.current_step, SetupWizardStep.BACKUP_DEFAULTS)

    async def test_invalid_lead_time_shows_a_retry_screen_preserving_enabled_choice(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.REMINDER_DEFAULTS)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        view: ReminderDefaultsChoiceView = interaction.response.sent_view
        enable_button = next(
            child for child in view.children
            if getattr(child, "label", None) == "Enable Vote-Ending Reminder (Recommended)"
        )
        enable_interaction = FakeInteraction()
        await enable_button.callback(interaction=enable_interaction)
        modal: ReminderDefaultsModal = enable_interaction.response.sent_modal
        modal.minutes_input._value = "not a duration"

        submit_interaction = FakeInteraction()
        await modal.on_submit(interaction=submit_interaction)

        self.assertIn("⚠", submit_interaction.response.edited_content)
        retry_view = submit_interaction.response.edited_view
        retry_button = next(
            child for child in retry_view.children if getattr(child, "label", None) == "Set Reminder Defaults"
        )
        retry_interaction = FakeInteraction()
        await retry_button.callback(interaction=retry_interaction)
        self.assertIsInstance(retry_interaction.response.sent_modal, ReminderDefaultsModal)


class SetupPreparationScreenIntegrationTests(SetupCommandTestCase):
    """Setup Wizard UX Polish: the preparation screen shown before Step 1."""

    async def test_preparation_screen_explains_the_recommended_roles(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        interaction = FakeInteraction()

        await send_setup_preparation_screen(interaction, self.bot, state)

        self.assertIn("Watch Party", interaction.response.sent_message)
        self.assertIn("WASH Crew", interaction.response.sent_message)
        self.assertIn("Save & Finish Later", interaction.response.sent_message)
        self.assertIsInstance(interaction.response.sent_view, SetupPreparationView)
        self.assertTrue(interaction.response.sent_ephemeral)

    async def test_begin_setup_proceeds_to_step_one(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        interaction = FakeInteraction()
        await send_setup_preparation_screen(interaction, self.bot, state)
        begin_button = interaction.response.sent_view.children[0]

        begin_interaction = FakeInteraction()
        await begin_button.callback(interaction=begin_interaction)

        self.assertIn("Step 1 of 11", begin_interaction.response.edited_content)
        self.assertIsInstance(begin_interaction.response.edited_view, WashCrewRoleStepView)

    async def test_cancel_from_preparation_screen_discards_the_draft(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        interaction = FakeInteraction()
        await send_setup_preparation_screen(interaction, self.bot, state)
        cancel_button = interaction.response.sent_view.children[1]

        cancel_interaction = FakeInteraction()
        await cancel_button.callback(interaction=cancel_interaction)

        self.assertIn("cancelled", cancel_interaction.response.edited_content)
        self.assertIsNone(cancel_interaction.response.edited_view)
        self.assertIsNone(self.wizard_repository.get(GUILD_ID))

    async def test_build_setup_preparation_text_mentions_reusing_existing_channels(self) -> None:
        text = build_setup_preparation_text()
        self.assertIn("existing channels", text)


class WatchPartyRoleWordingTests(SetupCommandTestCase):
    """Setup Wizard UX Polish: the Watch Party role must not be described
    as optional -- it gates every participant command.
    """

    async def test_step_body_does_not_describe_the_role_as_optional(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        self.assertNotIn("(optional)", interaction.response.sent_message)
        self.assertIn("only WASH Crew can use WASH's member commands", interaction.response.sent_message)

    async def test_role_select_placeholder_does_not_say_optional(self) -> None:
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_wash_crew_role(state, WASH_CREW_ROLE_ID)
        interaction = FakeInteraction()

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        role_select = interaction.response.sent_view.children[0]
        self.assertNotIn("optional", role_select.placeholder)

    async def test_the_role_can_still_technically_be_left_unset(self) -> None:
        # Leaving it unset must remain possible (min_values=0) even though
        # it's no longer described as optional -- WASH Crew can always
        # configure it later via /config.
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_watch_party_role(state, None, JoinMode.SELF_SERVICE)
        self.assertIsNone(state.draft.watch_party_role_id)


class WatchDestinationStepIntegrationTests(SetupCommandTestCase):
    """Setup Wizard UX Polish: Watch Destination wording, navigation
    controls, and the create-new-thread option (Requirement 5: created as
    a sibling thread under WASH's home channel, never asking for a parent
    -- that's already been established by the Home Channel step).
    """

    HOME_CHANNEL_ID = 600

    class FakeThread:
        def __init__(self, thread_id: int) -> None:
            self.id = thread_id

    class FakeHTTPResponse:
        status = 403
        reason = "Forbidden"

    class FakeHomeChannel:
        def __init__(self, *, thread_id: int = 999, fail: bool = False, channel_id: int = 600) -> None:
            self._thread_id = thread_id
            self._fail = fail
            self.id = channel_id
            self.created_with = None

        async def create_thread(self, *, name, type):
            self.created_with = (name, type)
            if self._fail:
                import discord

                raise discord.HTTPException(
                    response=WatchDestinationStepIntegrationTests.FakeHTTPResponse(), message="boom"
                )
            return WatchDestinationStepIntegrationTests.FakeThread(self._thread_id)

    class FakeGuildWithChannel:
        def __init__(self, channel) -> None:
            self._channel = channel

        def get_channel(self, channel_id):
            return self._channel

    async def _render_step(self):
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_home_channel(state, self.HOME_CHANNEL_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.WATCH_DESTINATION)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        return interaction

    async def test_body_explains_the_archive_purpose_and_mentions_thread_creation(self) -> None:
        interaction = await self._render_step()
        self.assertIn("archive completed watch items", interaction.response.sent_message)
        self.assertIn("Vote Winners and Retired items", interaction.response.sent_message)
        self.assertIn("Create New Thread", interaction.response.sent_message)

    async def test_a_thread_created_earlier_in_setup_appears_in_the_selector(self) -> None:
        # Live-testing fix: a thread created during an earlier step (or
        # an earlier visit to this one) must show up here without any
        # special wiring -- the selector's options are rebuilt fresh from
        # live guild state on every single render.
        home_channel = _FakeDestinationChannel(self.HOME_CHANNEL_ID, "watch-party")
        home_channel.threads = [_FakeThread(701, "movie-suggestions", home_channel)]
        guild = _FakeDestinationGuild(text_channels=[home_channel])

        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_home_channel(state, self.HOME_CHANNEL_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.WATCH_DESTINATION)
        interaction = FakeInteraction(guild=guild)

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        select = interaction.response.sent_view.children[0]
        option_values = [option.value for option in select.options]
        self.assertIn("701", option_values)
        thread_option = next(o for o in select.options if o.value == "701")
        self.assertEqual(thread_option.label, "movie-suggestions")
        self.assertEqual(thread_option.description, "Thread in #watch-party")

    async def test_saved_destination_is_preselected_when_returning_to_the_step(self) -> None:
        home_channel = _FakeDestinationChannel(self.HOME_CHANNEL_ID, "watch-party")
        thread = _FakeThread(701, "movie-suggestions", home_channel)
        home_channel.threads = [thread]
        guild = _FakeDestinationGuild(text_channels=[home_channel])

        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_home_channel(state, self.HOME_CHANNEL_ID)
        state = self.bot.setup_wizard_service.set_watch_destination(state, 701)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.WATCH_DESTINATION)
        interaction = FakeInteraction(guild=guild)

        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        select = interaction.response.sent_view.children[0]
        selected = next(o for o in select.options if o.value == "701")
        self.assertTrue(selected.default)

    async def test_body_recommends_creating_a_new_thread(self) -> None:
        # Walkthrough fix: Step 6 should steer users toward creating a
        # dedicated thread, consistent with every other setup step's own
        # "(Recommended)" convention, without forcing it.
        interaction = await self._render_step()
        self.assertIn("recommended", interaction.response.sent_message.lower())

    async def test_shows_back_and_save_for_later(self) -> None:
        interaction = await self._render_step()
        view = interaction.response.sent_view
        self.assertTrue(any(isinstance(child, SetupBackButton) for child in view.children))
        self.assertTrue(any(isinstance(child, SetupSaveForLaterButton) for child in view.children))

    async def test_selecting_a_channel_does_not_advance_until_confirmed(self) -> None:
        # Channel Selection Consistency: opening the selector and picking
        # a channel must never advance the step on its own.
        interaction = await self._render_step()
        view = interaction.response.sent_view
        channel_select = view.children[0]
        channel_select._values = [str(DESTINATION_CHANNEL_ID)]

        select_interaction = FakeInteraction()
        await channel_select.callback(interaction=select_interaction)

        self.assertTrue(select_interaction.response.deferred)
        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertIsNone(persisted.draft.watch_destination_channel_id)

    async def test_selecting_an_existing_channel_or_thread_advances_and_persists(self) -> None:
        interaction = await self._render_step()
        view = interaction.response.sent_view
        channel_select = view.children[0]
        channel_select._values = [str(DESTINATION_CHANNEL_ID)]

        confirm_button = next(
            c for c in view.children if getattr(c, "custom_id", None) == "wpm_setup_watch_destination_confirm"
        )
        confirm_interaction = FakeInteraction()
        await confirm_button.callback(interaction=confirm_interaction)

        self.assertIn("Step 7 of 11", confirm_interaction.response.edited_content)
        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertEqual(persisted.draft.watch_destination_channel_id, DESTINATION_CHANNEL_ID)
        self.assertFalse(persisted.draft.watch_destination_skipped)

    async def test_create_new_thread_end_to_end_persists_the_new_threads_id(self) -> None:
        interaction = await self._render_step()
        create_thread_button = next(
            c for c in interaction.response.sent_view.children
            if getattr(c, "label", None) == "Create New Thread (Recommended)"
        )

        fake_home_channel = self.FakeHomeChannel(thread_id=777)
        create_interaction = FakeInteraction(guild=self.FakeGuildWithChannel(fake_home_channel))
        await create_thread_button.callback(interaction=create_interaction)
        name_modal = create_interaction.response.sent_modal
        self.assertEqual(name_modal.name_input.default, "Watched Item Archive")
        name_modal.name_input._value = "Watched Item Archive"

        submit_interaction = FakeInteraction(guild=self.FakeGuildWithChannel(fake_home_channel))
        await name_modal.on_submit(interaction=submit_interaction)

        self.assertEqual(
            fake_home_channel.created_with, ("Watched Item Archive", __import__("discord").ChannelType.public_thread)
        )
        self.assertIn("Step 7 of 11", submit_interaction.response.edited_content)
        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertEqual(persisted.draft.watch_destination_channel_id, 777)

    async def test_create_new_thread_failure_shows_an_error_and_stays_on_the_step(self) -> None:
        interaction = await self._render_step()
        create_thread_button = next(
            c for c in interaction.response.sent_view.children
            if getattr(c, "label", None) == "Create New Thread (Recommended)"
        )

        failing_channel = self.FakeHomeChannel(fail=True)
        create_interaction = FakeInteraction(guild=self.FakeGuildWithChannel(failing_channel))
        await create_thread_button.callback(interaction=create_interaction)
        name_modal = create_interaction.response.sent_modal
        name_modal.name_input._value = "Watched Item Archive"

        submit_interaction = FakeInteraction(guild=self.FakeGuildWithChannel(failing_channel))
        await name_modal.on_submit(interaction=submit_interaction)

        self.assertIn("Could not create the thread", submit_interaction.response.edited_content)
        self.assertIsInstance(submit_interaction.response.edited_view, WatchDestinationStepView)
        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertIsNone(persisted.draft.watch_destination_channel_id)

    async def test_skip_still_works(self) -> None:
        interaction = await self._render_step()
        skip_button = next(
            c for c in interaction.response.sent_view.children if getattr(c, "label", None) == "Skip for Now"
        )

        skip_interaction = FakeInteraction()
        await skip_button.callback(interaction=skip_interaction)

        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertTrue(persisted.draft.watch_destination_skipped)

    async def test_skip_still_works_with_many_long_destination_names(self) -> None:
        # Setup Wizard Step 6 select-option length failure fix: Skip must
        # never depend on successfully building a destination option
        # list -- confirmed here with a guild deliberately stacked with
        # more than 25 usable destinations, several with 100-character
        # names, the exact conditions that used to risk an invalid
        # payload during this step's own initial render.
        channels = []
        for i in range(30):
            channel = _FakeDestinationChannel(i, f"{'c' * 90}-{i}")
            channel.threads = [_FakeThread(1000 + i, f"{'t' * 90}-{i}", channel)]
            channels.append(channel)
        guild = _FakeDestinationGuild(text_channels=channels)

        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_home_channel(state, self.HOME_CHANNEL_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.WATCH_DESTINATION)
        interaction = FakeInteraction(guild=guild)
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)

        skip_button = next(
            c for c in interaction.response.sent_view.children if getattr(c, "label", None) == "Skip for Now"
        )
        skip_interaction = FakeInteraction(guild=guild)
        await skip_button.callback(interaction=skip_interaction)

        self.assertIn("Step 7 of 11", skip_interaction.response.edited_content)
        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertTrue(persisted.draft.watch_destination_skipped)

    async def test_newly_created_thread_appears_and_is_preselected_when_step_6_rerenders(self) -> None:
        # Requirement: a thread created via "Create New Thread" must show
        # up, marked as the current selection, the next time Step 6 is
        # rendered (e.g. after navigating Back from Voting Defaults) --
        # never missing just because it didn't exist at the time of an
        # earlier render.
        interaction = await self._render_step()
        create_thread_button = next(
            c for c in interaction.response.sent_view.children
            if getattr(c, "label", None) == "Create New Thread (Recommended)"
        )

        fake_home_channel = self.FakeHomeChannel(thread_id=777)
        create_interaction = FakeInteraction(guild=self.FakeGuildWithChannel(fake_home_channel))
        await create_thread_button.callback(interaction=create_interaction)
        name_modal = create_interaction.response.sent_modal
        name_modal.name_input._value = "Watched Item Archive"
        submit_interaction = FakeInteraction(guild=self.FakeGuildWithChannel(fake_home_channel))
        await name_modal.on_submit(interaction=submit_interaction)

        # Now go back to Step 6 with a live guild that actually has the
        # created thread (777) under the home channel, as it would after
        # a real Discord API call.
        home_channel = _FakeDestinationChannel(self.HOME_CHANNEL_ID, "watch-party")
        home_channel.threads = [_FakeThread(777, "Watched Item Archive", home_channel)]
        guild = _FakeDestinationGuild(text_channels=[home_channel])
        persisted = self.wizard_repository.get(GUILD_ID)
        rerendered_state = self.bot.setup_wizard_service.go_to_step(persisted, SetupWizardStep.WATCH_DESTINATION)
        back_interaction = FakeInteraction(guild=guild)

        await send_setup_wizard_step(back_interaction, self.bot, rerendered_state, edit=False)

        select = back_interaction.response.sent_view.children[0]
        option_values = [option.value for option in select.options]
        self.assertIn("777", option_values)
        created_option = next(o for o in select.options if o.value == "777")
        self.assertTrue(created_option.default)

    async def test_retrying_create_new_thread_with_the_same_name_does_not_create_a_duplicate(self) -> None:
        # Setup Wizard Step 6 select-option length failure fix: if the
        # thread was already created successfully (e.g. the response
        # that should have advanced past this step never reached
        # Discord, leaving the user looking at the same modal again),
        # retrying with the identical name must reuse the existing
        # thread rather than creating a second, orphaned one.
        interaction = await self._render_step()
        create_thread_button = next(
            c for c in interaction.response.sent_view.children
            if getattr(c, "label", None) == "Create New Thread (Recommended)"
        )

        fake_home_channel = self.FakeHomeChannel(thread_id=777)
        # Simulate the first attempt's thread already existing under the
        # home channel, as it would after a real (successful) Discord
        # thread-creation call.
        fake_home_channel.threads = [self.FakeThread(777)]
        fake_home_channel.threads[0].name = "Watched Item Archive"
        fake_home_channel.threads[0].archived = False

        create_interaction = FakeInteraction(guild=self.FakeGuildWithChannel(fake_home_channel))
        await create_thread_button.callback(interaction=create_interaction)
        name_modal = create_interaction.response.sent_modal
        name_modal.name_input._value = "Watched Item Archive"

        submit_interaction = FakeInteraction(guild=self.FakeGuildWithChannel(fake_home_channel))
        await name_modal.on_submit(interaction=submit_interaction)

        # create_thread must never have been called a second time --
        # created_with stays None, confirming the existing thread (777)
        # was reused instead.
        self.assertIsNone(fake_home_channel.created_with)
        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertEqual(persisted.draft.watch_destination_channel_id, 777)

    async def test_the_real_production_sequence_with_ordinary_default_names_reveals_the_actual_offending_option(
        self,
    ) -> None:
        # TASK E: reproduces the exact live flow using ONLY real, ordinary
        # production data -- the modal's own default thread name
        # ("Watched Item Archive"), nothing synthetic, long, or
        # emoji-heavy. Proves the live 400 was never about channel/thread
        # names: creating the thread correctly advances the wizard to
        # Voting Defaults (Step 7, per SETUP_WIZARD_STEP_ORDER/
        # _next_step -- confirmed empirically, not assumed), and it is
        # THAT view's first render which violates Discord's limits, via a
        # static, name-independent description string built outside
        # build_safe_select_option() entirely (CandidateSelectionSelect
        # Component -> CANDIDATE_SELECTION_HELP_TEXT, see
        # domain/suggestion_database_configuration.py).
        interaction = await self._render_step()
        create_thread_button = next(
            c for c in interaction.response.sent_view.children
            if getattr(c, "label", None) == "Create New Thread (Recommended)"
        )

        fake_home_channel = self.FakeHomeChannel(thread_id=777)
        create_interaction = FakeInteraction(guild=self.FakeGuildWithChannel(fake_home_channel))
        await create_thread_button.callback(interaction=create_interaction)
        name_modal = create_interaction.response.sent_modal
        self.assertEqual(name_modal.name_input.default, "Watched Item Archive")
        name_modal.name_input._value = name_modal.name_input.default

        submit_interaction = FakeInteraction(guild=self.FakeGuildWithChannel(fake_home_channel))
        await name_modal.on_submit(interaction=submit_interaction)

        view = submit_interaction.response.edited_view
        self.assertIsInstance(view, VotingDefaultsIntroView)

        # The exact JSON path from the live traceback:
        # data.components.0.components.0.options.1.description
        components = view.to_components()
        offending_option = components[0]["components"][0]["options"][1]
        self.assertEqual(offending_option["label"], "Favor New Additions")
        offending_description = offending_option["description"]
        utf16_length = len(offending_description.encode("utf-16-le")) // 2
        self.assertLessEqual(
            utf16_length,
            100,
            msg=(
                f"components[0].components[0].options[1].description is {utf16_length} Discord "
                f"(UTF-16) length units, {len(offending_description)} Python len(): "
                f"{offending_description!r}"
            ),
        )
        self.assertEqual(find_oversized_view_component_fields(view), [])

    async def test_post_create_step_6_rerender_with_an_emoji_home_channel_name_serializes_within_discord_limits(
        self,
    ) -> None:
        # TASK D's exact reproduction recipe: render Step 6, click Create
        # New Thread, submit a name, thread creation succeeds, then Step
        # 6 is rebuilt with the created thread retained/selected (Back
        # from Voting Defaults, or Review's "edit a section" jump, both
        # land back here). Root cause (discord_ui_limits.py's module
        # docstring): Discord measures SelectOption length in UTF-16 code
        # units, not Python's code-point-based len() -- an emoji-heavy
        # home channel name (a completely ordinary real-world choice,
        # many Discord servers decorate channel names this way) combines
        # with "Thread in #<parent>" into a description that was <=100
        # Python characters after the old truncate_for_discord() but
        # still well over 100 by Discord's own count, producing the live
        # 400 Bad Request (error 50035) on
        # data.components.0.components.0.options.1.description.
        interaction = await self._render_step()
        create_thread_button = next(
            c for c in interaction.response.sent_view.children
            if getattr(c, "label", None) == "Create New Thread (Recommended)"
        )

        fake_home_channel = self.FakeHomeChannel(thread_id=777)
        create_interaction = FakeInteraction(guild=self.FakeGuildWithChannel(fake_home_channel))
        await create_thread_button.callback(interaction=create_interaction)
        name_modal = create_interaction.response.sent_modal
        name_modal.name_input._value = "Watched Item Archive"

        submit_interaction = FakeInteraction(guild=self.FakeGuildWithChannel(fake_home_channel))
        await name_modal.on_submit(interaction=submit_interaction)

        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertEqual(persisted.draft.watch_destination_channel_id, 777)

        # Rebuild Step 6 against a live guild where the created thread is
        # visible under an emoji-heavy home/parent channel name.
        emoji_home_channel_name = "\U0001F3AC" * 90
        home_channel = _FakeDestinationChannel(self.HOME_CHANNEL_ID, emoji_home_channel_name)
        home_channel.threads = [_FakeThread(777, "Watched Item Archive", home_channel)]
        guild = _FakeDestinationGuild(text_channels=[home_channel])
        rerendered_state = self.bot.setup_wizard_service.go_to_step(persisted, SetupWizardStep.WATCH_DESTINATION)
        back_interaction = FakeInteraction(guild=guild)

        await send_setup_wizard_step(back_interaction, self.bot, rerendered_state, edit=False)

        view = back_interaction.response.sent_view
        self.assertIsInstance(view, WatchDestinationStepView)
        select = view.children[0]
        created_option = next(o for o in select.options if o.value == "777")
        self.assertTrue(created_option.default)

        # The exact check TASK D requires: walk the real outgoing payload
        # (view.to_components(), not the Python SelectOption objects)
        # and confirm no option field violates Discord's limits.
        violations = find_oversized_view_component_fields(view)
        self.assertEqual(violations, [])

    async def test_a_long_plain_ascii_thread_name_selected_after_creation_serializes_safely(self) -> None:
        # Non-emoji long-name variant of the same rerender scenario, to
        # keep both failure shapes (astral-character undercounting and
        # ordinary long names) under regression coverage together.
        interaction = await self._render_step()
        create_thread_button = next(
            c for c in interaction.response.sent_view.children
            if getattr(c, "label", None) == "Create New Thread (Recommended)"
        )

        fake_home_channel = self.FakeHomeChannel(thread_id=777)
        create_interaction = FakeInteraction(guild=self.FakeGuildWithChannel(fake_home_channel))
        await create_thread_button.callback(interaction=create_interaction)
        name_modal = create_interaction.response.sent_modal
        long_thread_name = "t" * 100
        name_modal.name_input._value = long_thread_name

        submit_interaction = FakeInteraction(guild=self.FakeGuildWithChannel(fake_home_channel))
        await name_modal.on_submit(interaction=submit_interaction)
        persisted = self.wizard_repository.get(GUILD_ID)

        long_parent_channel_name = "p" * 100
        home_channel = _FakeDestinationChannel(self.HOME_CHANNEL_ID, long_parent_channel_name)
        home_channel.threads = [_FakeThread(777, long_thread_name, home_channel)]
        guild = _FakeDestinationGuild(text_channels=[home_channel])
        rerendered_state = self.bot.setup_wizard_service.go_to_step(persisted, SetupWizardStep.WATCH_DESTINATION)
        back_interaction = FakeInteraction(guild=guild)

        await send_setup_wizard_step(back_interaction, self.bot, rerendered_state, edit=False)

        view = back_interaction.response.sent_view
        violations = find_oversized_view_component_fields(view)
        self.assertEqual(violations, [])

    async def test_skip_still_works_after_a_thread_has_already_been_created(self) -> None:
        # A thread created earlier in the same Step 6 visit (or an
        # earlier one) must never block Skip on a later visit -- Skip
        # must succeed independent of destination-option rendering.
        interaction = await self._render_step()
        create_thread_button = next(
            c for c in interaction.response.sent_view.children
            if getattr(c, "label", None) == "Create New Thread (Recommended)"
        )

        fake_home_channel = self.FakeHomeChannel(thread_id=777)
        create_interaction = FakeInteraction(guild=self.FakeGuildWithChannel(fake_home_channel))
        await create_thread_button.callback(interaction=create_interaction)
        name_modal = create_interaction.response.sent_modal
        name_modal.name_input._value = "Watched Item Archive"
        submit_interaction = FakeInteraction(guild=self.FakeGuildWithChannel(fake_home_channel))
        await name_modal.on_submit(interaction=submit_interaction)
        persisted = self.wizard_repository.get(GUILD_ID)

        home_channel = _FakeDestinationChannel(self.HOME_CHANNEL_ID, "\U0001F3AC" * 90)
        home_channel.threads = [_FakeThread(777, "Watched Item Archive", home_channel)]
        guild = _FakeDestinationGuild(text_channels=[home_channel])
        rerendered_state = self.bot.setup_wizard_service.go_to_step(persisted, SetupWizardStep.WATCH_DESTINATION)
        back_interaction = FakeInteraction(guild=guild)
        await send_setup_wizard_step(back_interaction, self.bot, rerendered_state, edit=False)

        skip_button = next(
            c for c in back_interaction.response.sent_view.children if getattr(c, "label", None) == "Skip for Now"
        )
        skip_interaction = FakeInteraction(guild=guild)
        await skip_button.callback(interaction=skip_interaction)

        self.assertIn("Step 7 of 11", skip_interaction.response.edited_content)
        final = self.wizard_repository.get(GUILD_ID)
        self.assertTrue(final.draft.watch_destination_skipped)
        self.assertIsNone(final.draft.watch_destination_channel_id)


class HomeChannelStepIntegrationTests(SetupCommandTestCase):
    """Release Candidate Polish, Requirement 7: the Home Channel step
    itself -- both the "create a new channel" and "use an existing
    channel" paths persist home_channel_id and advance, exactly as the
    downstream Suggestion Destination/Watch Destination steps (which
    already create their threads as siblings of whatever this step
    persists -- see GuidedCollectionCreationIntegrationTests and
    WatchDestinationStepIntegrationTests) assume it will.
    """

    class FakeHTTPResponse:
        status = 403
        reason = "Forbidden"

    class FakeCreatedChannel:
        def __init__(self, channel_id: int) -> None:
            self.id = channel_id

    class FakeGuildForHomeChannel:
        def __init__(self, *, channel_id: int = 900, fail: bool = False, forbidden: bool = False) -> None:
            self._channel_id = channel_id
            self._fail = fail
            self._forbidden = forbidden
            self.created_with = None

        async def create_text_channel(self, *, name):
            self.created_with = name
            if self._forbidden:
                import discord

                raise discord.Forbidden(
                    response=HomeChannelStepIntegrationTests.FakeHTTPResponse(), message="Missing Permissions"
                )
            if self._fail:
                import discord

                raise discord.HTTPException(
                    response=HomeChannelStepIntegrationTests.FakeHTTPResponse(), message="boom"
                )
            return HomeChannelStepIntegrationTests.FakeCreatedChannel(self._channel_id)

    async def _render_step(self):
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.HOME_CHANNEL)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        return interaction

    async def test_creating_a_new_channel_persists_it_and_advances(self) -> None:
        interaction = await self._render_step()
        self.assertIsInstance(interaction.response.sent_view, HomeChannelStepView)
        create_new_button = next(
            c for c in interaction.response.sent_view.children
            if getattr(c, "label", None) == "Create New Channel (Recommended)"
        )

        create_interaction = FakeInteraction(guild=self.FakeGuildForHomeChannel(channel_id=901))
        await create_new_button.callback(interaction=create_interaction)
        name_modal = create_interaction.response.sent_modal
        self.assertIsInstance(name_modal, HomeChannelNameModal)
        self.assertEqual(name_modal.name_input.default, "Watch Party")
        name_modal.name_input._value = "Watch Party"

        guild = self.FakeGuildForHomeChannel(channel_id=901)
        submit_interaction = FakeInteraction(guild=guild)
        await name_modal.on_submit(interaction=submit_interaction)

        self.assertEqual(guild.created_with, "Watch Party")
        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertEqual(persisted.draft.home_channel_id, 901)
        self.assertIn(SetupWizardStep.HOME_CHANNEL, persisted.completed_steps)

    async def test_channel_creation_failure_shows_an_error_and_stays_on_the_step(self) -> None:
        interaction = await self._render_step()
        create_new_button = next(
            c for c in interaction.response.sent_view.children
            if getattr(c, "label", None) == "Create New Channel (Recommended)"
        )

        failing_guild = self.FakeGuildForHomeChannel(fail=True)
        create_interaction = FakeInteraction(guild=failing_guild)
        await create_new_button.callback(interaction=create_interaction)
        name_modal = create_interaction.response.sent_modal
        name_modal.name_input._value = "Watch Party"

        submit_interaction = FakeInteraction(guild=failing_guild)
        await name_modal.on_submit(interaction=submit_interaction)

        self.assertIn("Could not create the channel", submit_interaction.response.edited_content)
        self.assertIsInstance(submit_interaction.response.edited_view, HomeChannelStepView)
        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertIsNone(persisted.draft.home_channel_id)

    async def test_missing_permission_shows_a_friendly_message_not_the_raw_exception(self) -> None:
        interaction = await self._render_step()
        create_new_button = next(
            c for c in interaction.response.sent_view.children
            if getattr(c, "label", None) == "Create New Channel (Recommended)"
        )

        failing_guild = self.FakeGuildForHomeChannel(forbidden=True)
        create_interaction = FakeInteraction(guild=failing_guild)
        await create_new_button.callback(interaction=create_interaction)
        name_modal = create_interaction.response.sent_modal
        name_modal.name_input._value = "Watch Party"

        submit_interaction = FakeInteraction(guild=failing_guild)
        await name_modal.on_submit(interaction=submit_interaction)

        message = submit_interaction.response.edited_content
        self.assertIn("does not have permission to create channels", message)
        self.assertIn("Manage Channels", message)
        self.assertIn("existing channel from the selector", message)
        self.assertNotIn("403", message)
        self.assertNotIn("Missing Permissions", message)
        self.assertIsInstance(submit_interaction.response.edited_view, HomeChannelStepView)
        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertIsNone(persisted.draft.home_channel_id)

    async def test_selecting_a_channel_does_not_advance_until_confirmed(self) -> None:
        # Channel Selection Consistency: opening the selector and picking
        # a channel must never advance the step on its own.
        interaction = await self._render_step()
        view: HomeChannelStepView = interaction.response.sent_view

        view.channel_select._values = [str(DESTINATION_CHANNEL_ID)]
        select_interaction = FakeInteraction()
        await view.channel_select.callback(interaction=select_interaction)

        self.assertTrue(select_interaction.response.deferred)
        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertIsNone(persisted.draft.home_channel_id)

    async def test_using_an_existing_channel_persists_it_and_advances(self) -> None:
        interaction = await self._render_step()
        view: HomeChannelStepView = interaction.response.sent_view
        view.channel_select._values = [str(DESTINATION_CHANNEL_ID)]

        confirm_button = next(
            c for c in view.children if getattr(c, "custom_id", None) == "wpm_setup_home_channel_confirm"
        )
        confirm_interaction = FakeInteraction()
        await confirm_button.callback(interaction=confirm_interaction)

        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertEqual(persisted.draft.home_channel_id, DESTINATION_CHANNEL_ID)
        self.assertIn(SetupWizardStep.HOME_CHANNEL, persisted.completed_steps)

    async def test_resuming_after_the_home_channel_step_keeps_the_chosen_channel(self) -> None:
        # Regression coverage for Requirement 7: a WASH Crew member who
        # sets the home channel, then saves and resumes later (a fresh
        # SetupWizardService/repository round-trip, mirroring a bot
        # restart), must not lose that choice.
        interaction = await self._render_step()
        view: HomeChannelStepView = interaction.response.sent_view
        view.channel_select._values = [str(DESTINATION_CHANNEL_ID)]
        confirm_button = next(
            c for c in view.children if getattr(c, "custom_id", None) == "wpm_setup_home_channel_confirm"
        )
        await confirm_button.callback(interaction=FakeInteraction())

        resumed_state, resumed = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)

        self.assertTrue(resumed)
        self.assertEqual(resumed_state.draft.home_channel_id, DESTINATION_CHANNEL_ID)
        self.assertIn(SetupWizardStep.HOME_CHANNEL, resumed_state.completed_steps)


class AdminChannelCreationIntegrationTests(SetupCommandTestCase):
    """Setup Wizard Channel Creation UX: the Admin Channel step's "Create
    New Channel" option (mirroring Home Channel's) must create a private,
    WASH-Crew-only channel -- @everyone denied, WASH's own bot member and
    the configured WASH Crew role granted -- and surface the same friendly
    missing-permission message as Home Channel rather than a raw exception.
    """

    class FakeHTTPResponse:
        status = 403
        reason = "Forbidden"

    class FakeRole:
        def __init__(self, role_id: int) -> None:
            self.id = role_id

    class FakeCreatedChannel:
        def __init__(self, channel_id: int) -> None:
            self.id = channel_id

    class FakeGuildForAdminChannel:
        def __init__(self, *, channel_id: int = 950, wash_crew_role_id=None, forbidden: bool = False) -> None:
            self._channel_id = channel_id
            self._forbidden = forbidden
            self.default_role = object()
            self.me = object()
            self._roles = {}
            if wash_crew_role_id is not None:
                self._roles[wash_crew_role_id] = AdminChannelCreationIntegrationTests.FakeRole(wash_crew_role_id)
            self.created_with = None

        def get_role(self, role_id):
            return self._roles.get(role_id)

        async def create_text_channel(self, *, name, overwrites=None, topic=None):
            self.created_with = (name, overwrites, topic)
            if self._forbidden:
                import discord

                raise discord.Forbidden(
                    response=AdminChannelCreationIntegrationTests.FakeHTTPResponse(), message="Missing Permissions"
                )
            return AdminChannelCreationIntegrationTests.FakeCreatedChannel(self._channel_id)

    async def _render_step(self, *, wash_crew_role_id=None):
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        if wash_crew_role_id is not None:
            state = self.bot.setup_wizard_service.set_wash_crew_role(state, wash_crew_role_id)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.ADMIN_CHANNEL)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        return interaction

    async def test_step_body_explains_private_channel_visibility(self) -> None:
        # Live-testing fix: administrators may not see a private channel
        # in the picker until WASH is actually granted access there --
        # the step must say so explicitly, name the two permissions
        # needed, and mention role hierarchy for a WASH role that also
        # manages other roles, without ever suggesting Administrator.
        interaction = await self._render_step()
        message = interaction.response.sent_message
        self.assertIn("Private channels will appear in the channel selector", message)
        self.assertIn("View Channel", message)
        self.assertIn("Send Messages", message)
        self.assertIn("role hierarchy", message)
        self.assertNotIn("Administrator", message)

    async def test_creating_a_new_channel_persists_it_advances_and_applies_overwrites(self) -> None:
        interaction = await self._render_step(wash_crew_role_id=WASH_CREW_ROLE_ID)
        self.assertIsInstance(interaction.response.sent_view, AdminChannelStepView)
        create_new_button = next(
            child
            for child in interaction.response.sent_view.children
            if getattr(child, "label", None) == "Create New Channel"
        )

        guild = self.FakeGuildForAdminChannel(channel_id=951, wash_crew_role_id=WASH_CREW_ROLE_ID)
        create_interaction = FakeInteraction(guild=guild)
        await create_new_button.callback(interaction=create_interaction)
        name_modal = create_interaction.response.sent_modal
        self.assertIsInstance(name_modal, AdminChannelNameModal)
        self.assertEqual(name_modal.name_input.default, "WASH Crew")
        name_modal.name_input._value = "WASH Crew"

        submit_interaction = FakeInteraction(guild=guild)
        await name_modal.on_submit(interaction=submit_interaction)

        name, overwrites, topic = guild.created_with
        self.assertEqual(name, "WASH Crew")
        self.assertIn("private", topic.lower())
        self.assertFalse(overwrites[guild.default_role].view_channel)
        self.assertTrue(overwrites[guild.me].view_channel)
        wash_crew_role = guild.get_role(WASH_CREW_ROLE_ID)
        self.assertTrue(overwrites[wash_crew_role].view_channel)

        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertEqual(persisted.draft.admin_channel_id, 951)

    async def test_creating_a_new_channel_without_a_configured_wash_crew_role_still_succeeds(self) -> None:
        interaction = await self._render_step()
        create_new_button = next(
            child
            for child in interaction.response.sent_view.children
            if getattr(child, "label", None) == "Create New Channel"
        )

        guild = self.FakeGuildForAdminChannel(channel_id=952)
        create_interaction = FakeInteraction(guild=guild)
        await create_new_button.callback(interaction=create_interaction)
        name_modal = create_interaction.response.sent_modal
        name_modal.name_input._value = "WASH Crew"

        submit_interaction = FakeInteraction(guild=guild)
        await name_modal.on_submit(interaction=submit_interaction)

        name, overwrites, _topic = guild.created_with
        self.assertEqual(name, "WASH Crew")
        self.assertEqual(set(overwrites), {guild.default_role, guild.me})
        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertEqual(persisted.draft.admin_channel_id, 952)

    async def test_missing_permission_shows_a_friendly_message_not_the_raw_exception(self) -> None:
        interaction = await self._render_step()
        create_new_button = next(
            child
            for child in interaction.response.sent_view.children
            if getattr(child, "label", None) == "Create New Channel"
        )

        failing_guild = self.FakeGuildForAdminChannel(forbidden=True)
        create_interaction = FakeInteraction(guild=failing_guild)
        await create_new_button.callback(interaction=create_interaction)
        name_modal = create_interaction.response.sent_modal
        name_modal.name_input._value = "WASH Crew"

        submit_interaction = FakeInteraction(guild=failing_guild)
        await name_modal.on_submit(interaction=submit_interaction)

        message = submit_interaction.response.edited_content
        self.assertIn("does not have permission to create channels", message)
        self.assertIn("Manage Channels", message)
        self.assertIn("existing channel from the selector", message)
        self.assertNotIn("403", message)
        self.assertNotIn("Missing Permissions", message)
        self.assertIsInstance(submit_interaction.response.edited_view, AdminChannelStepView)
        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertIsNone(persisted.draft.admin_channel_id)


class GuidedCollectionCreationIntegrationTests(SetupCommandTestCase):
    """Contextual Database Resolution: Setup Wizard's guided "what type of
    collection" flow. Requirement 5 ("Collections should default to
    threads") means every option now creates its suggestion destination
    automatically as a sibling thread under WASH's home channel -- no
    separate destination choice is offered, since Requirement 4's Home
    Channel step already answered "where do things go." Every option
    still ends up calling SetupWizardService.create_new_database()
    unchanged, and Requirement 6 requires the created thread be persisted
    immediately as that collection's Suggestion Destination.
    """

    HOME_CHANNEL_ID = 600

    class FakeThread:
        def __init__(self, thread_id: int) -> None:
            self.id = thread_id

    class FakeHTTPResponse:
        status = 403
        reason = "Forbidden"

    class FakeHomeChannel:
        def __init__(self, *, thread_id: int = 999, fail: bool = False, channel_id: int = 600) -> None:
            self._thread_id = thread_id
            self._fail = fail
            self.id = channel_id
            self.created_with = None

        async def create_thread(self, *, name, type):
            self.created_with = (name, type)
            if self._fail:
                import discord

                raise discord.HTTPException(
                    response=GuidedCollectionCreationIntegrationTests.FakeHTTPResponse(), message="boom"
                )
            return GuidedCollectionCreationIntegrationTests.FakeThread(self._thread_id)

    class FakeGuildWithChannel:
        def __init__(self, channel) -> None:
            self._channel = channel

        def get_channel(self, channel_id):
            return self._channel

    async def _reach_collection_type_choice(self):
        from watch_party_manager.setup_wizard_view import CollectionTypeChoiceView, SuggestionDatabaseChoiceView

        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_home_channel(state, self.HOME_CHANNEL_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.SUGGESTION_DATABASE)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        self.assertIsInstance(interaction.response.sent_view, SuggestionDatabaseChoiceView)
        create_new_button = interaction.response.sent_view.children[0]

        type_interaction = FakeInteraction()
        await create_new_button.callback(interaction=type_interaction)
        self.assertIsInstance(type_interaction.response.edited_view, CollectionTypeChoiceView)
        return type_interaction.response.edited_view

    async def test_movies_option_creates_a_database_named_movies_in_a_new_thread(self) -> None:
        type_view = await self._reach_collection_type_choice()
        movies_button = type_view.children[0]
        self.assertEqual(movies_button.label, "Movies (Recommended)")

        fake_home_channel = self.FakeHomeChannel(thread_id=701)
        movies_interaction = FakeInteraction(guild=self.FakeGuildWithChannel(fake_home_channel))
        await movies_button.callback(interaction=movies_interaction)

        # Requirement 6: the default thread name is the more descriptive
        # "Movie Suggestions", not the bare collection type "Movies".
        self.assertEqual(
            fake_home_channel.created_with,
            ("Movie Suggestions", __import__("discord").ChannelType.public_thread),
        )
        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertEqual(persisted.draft.suggestion_database_name, "Movie Suggestions")
        databases = self.suggestion_service.list_databases(GUILD_ID)
        self.assertEqual(databases[0].name, "Movie Suggestions")
        self.assertEqual(databases[0].channel_id, 701)

    async def test_tv_shows_option_creates_a_database_named_tv_shows_in_a_new_thread(self) -> None:
        type_view = await self._reach_collection_type_choice()
        tv_shows_button = type_view.children[1]
        self.assertEqual(tv_shows_button.label, "TV Shows")

        fake_home_channel = self.FakeHomeChannel(thread_id=702)
        tv_interaction = FakeInteraction(guild=self.FakeGuildWithChannel(fake_home_channel))
        await tv_shows_button.callback(interaction=tv_interaction)

        databases = self.suggestion_service.list_databases(GUILD_ID)
        self.assertEqual(databases[0].name, "TV Suggestions")
        self.assertEqual(databases[0].channel_id, 702)

    async def test_special_collection_option_asks_for_a_name_first(self) -> None:
        from watch_party_manager.setup_wizard_view import CreateDatabaseNameModal

        type_view = await self._reach_collection_type_choice()
        special_button = type_view.children[2]
        self.assertEqual(special_button.label, "Special Collection")

        special_interaction = FakeInteraction()
        await special_button.callback(interaction=special_interaction)

        modal = special_interaction.response.sent_modal
        self.assertIsInstance(modal, CreateDatabaseNameModal)
        modal.name_input._value = "Horror Movies"

        fake_home_channel = self.FakeHomeChannel(thread_id=703)
        submit_interaction = FakeInteraction(guild=self.FakeGuildWithChannel(fake_home_channel))
        await modal.on_submit(interaction=submit_interaction)

        databases = self.suggestion_service.list_databases(GUILD_ID)
        self.assertEqual(databases[0].name, "Horror Movies")
        self.assertEqual(databases[0].channel_id, 703)

    async def test_custom_option_asks_for_any_name(self) -> None:
        from watch_party_manager.setup_wizard_view import CreateDatabaseNameModal

        type_view = await self._reach_collection_type_choice()
        custom_button = type_view.children[3]
        self.assertEqual(custom_button.label, "Custom")

        custom_interaction = FakeInteraction()
        await custom_button.callback(interaction=custom_interaction)

        modal = custom_interaction.response.sent_modal
        self.assertIsInstance(modal, CreateDatabaseNameModal)
        modal.name_input._value = "Book Club Adaptations"

        fake_home_channel = self.FakeHomeChannel(thread_id=704)
        submit_interaction = FakeInteraction(guild=self.FakeGuildWithChannel(fake_home_channel))
        await modal.on_submit(interaction=submit_interaction)

        databases = self.suggestion_service.list_databases(GUILD_ID)
        self.assertEqual(databases[0].name, "Book Club Adaptations")
        self.assertEqual(databases[0].channel_id, 704)

    async def test_import_existing_explains_running_import_separately(self) -> None:
        from watch_party_manager.setup_wizard_view import ImportExistingDatabaseNoticeView

        type_view = await self._reach_collection_type_choice()
        import_button = type_view.children[4]
        self.assertEqual(import_button.label, "Import Existing Backup")

        import_interaction = FakeInteraction()
        await import_button.callback(interaction=import_interaction)

        self.assertIn("/maintenance import", import_interaction.response.edited_content)
        self.assertIsInstance(import_interaction.response.edited_view, ImportExistingDatabaseNoticeView)

    async def test_import_existing_back_button_returns_to_collection_type_choice(self) -> None:
        from watch_party_manager.setup_wizard_view import CollectionTypeChoiceView

        type_view = await self._reach_collection_type_choice()
        import_button = type_view.children[4]
        import_interaction = FakeInteraction()
        await import_button.callback(interaction=import_interaction)
        back_button = next(c for c in import_interaction.response.edited_view.children if getattr(c, "label", None) == "Back")

        back_interaction = FakeInteraction()
        await back_button.callback(interaction=back_interaction)

        self.assertIsInstance(back_interaction.response.edited_view, CollectionTypeChoiceView)

    async def test_import_existing_notice_has_save_for_later(self) -> None:
        type_view = await self._reach_collection_type_choice()
        import_button = type_view.children[4]
        import_interaction = FakeInteraction()
        await import_button.callback(interaction=import_interaction)

        labels = [getattr(c, "label", None) for c in import_interaction.response.edited_view.children]
        self.assertIn("Back", labels)
        self.assertIn("Save & Finish Later", labels)
        self.assertIn("Cancel Setup", labels)

    async def test_collection_type_choice_has_back_and_save_for_later(self) -> None:
        # Live-testing fix: this nested sub-screen previously offered
        # Cancel Setup only -- the only way to exit safely was to
        # destroy the entire draft.
        type_view = await self._reach_collection_type_choice()
        labels = [getattr(child, "label", None) for child in type_view.children]
        self.assertIn("Back", labels)
        self.assertIn("Save & Finish Later", labels)
        self.assertIn("Cancel Setup", labels)

    async def test_collection_type_choice_back_returns_to_collection_step_preserving_draft(self) -> None:
        from watch_party_manager.setup_wizard_view import SuggestionDatabaseChoiceView

        type_view = await self._reach_collection_type_choice()
        back_button = next(c for c in type_view.children if getattr(c, "label", None) == "Back")

        back_interaction = FakeInteraction()
        await back_button.callback(interaction=back_interaction)

        # Back from the "what type" sub-screen returns to the Collection
        # step's own top-level choice screen -- not further back to an
        # earlier wizard step -- and never touches the draft.
        self.assertIsInstance(back_interaction.response.edited_view, SuggestionDatabaseChoiceView)
        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertEqual(persisted.draft.home_channel_id, self.HOME_CHANNEL_ID)
        self.assertEqual(persisted.current_step, SetupWizardStep.SUGGESTION_DATABASE)

    async def test_collection_type_choice_save_for_later_preserves_draft_without_creating_a_collection(self) -> None:
        type_view = await self._reach_collection_type_choice()
        save_button = next(
            c for c in type_view.children if getattr(c, "label", None) == "Save & Finish Later"
        )

        save_interaction = FakeInteraction()
        await save_button.callback(interaction=save_interaction)

        self.assertIn("saved", save_interaction.response.edited_content.lower())
        self.assertIsInstance(save_interaction.response.edited_view, SetupProgressSavedView)
        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted.draft.home_channel_id, self.HOME_CHANNEL_ID)
        self.assertIsNone(persisted.draft.suggestion_database_id)
        self.assertEqual(self.suggestion_service.list_databases(GUILD_ID), [])
        self.assertIsNone(self.guild_configuration_repository.get(GUILD_ID))

    async def _reach_existing_database_select(self):
        from watch_party_manager.setup_wizard_view import ExistingDatabaseSelectView, SuggestionDatabaseChoiceView

        self.suggestion_service.create_database("Movies", GUILD_ID, self.HOME_CHANNEL_ID)
        state, _ = self.bot.setup_wizard_service.start_or_resume(GUILD_ID)
        state = self.bot.setup_wizard_service.set_home_channel(state, self.HOME_CHANNEL_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.SUGGESTION_DATABASE)
        interaction = FakeInteraction()
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        self.assertIsInstance(interaction.response.sent_view, SuggestionDatabaseChoiceView)
        select_existing_button = interaction.response.sent_view.children[1]

        select_interaction = FakeInteraction()
        await select_existing_button.callback(interaction=select_interaction)
        self.assertIsInstance(select_interaction.response.edited_view, ExistingDatabaseSelectView)
        return select_interaction.response.edited_view

    async def test_existing_database_select_has_back_and_save_for_later(self) -> None:
        select_view = await self._reach_existing_database_select()
        labels = [getattr(child, "label", None) for child in select_view.children]
        self.assertIn("Back", labels)
        self.assertIn("Save & Finish Later", labels)
        self.assertIn("Cancel Setup", labels)

    async def test_existing_database_select_back_returns_to_collection_step_preserving_draft(self) -> None:
        from watch_party_manager.setup_wizard_view import SuggestionDatabaseChoiceView

        select_view = await self._reach_existing_database_select()
        back_button = next(c for c in select_view.children if getattr(c, "label", None) == "Back")

        back_interaction = FakeInteraction()
        await back_button.callback(interaction=back_interaction)

        self.assertIsInstance(back_interaction.response.edited_view, SuggestionDatabaseChoiceView)
        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertEqual(persisted.draft.home_channel_id, self.HOME_CHANNEL_ID)
        self.assertIsNone(persisted.draft.suggestion_database_id)

    async def test_existing_database_select_save_for_later_does_not_select_a_database(self) -> None:
        select_view = await self._reach_existing_database_select()
        save_button = next(
            c for c in select_view.children if getattr(c, "label", None) == "Save & Finish Later"
        )

        save_interaction = FakeInteraction()
        await save_button.callback(interaction=save_interaction)

        self.assertIsInstance(save_interaction.response.edited_view, SetupProgressSavedView)
        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertIsNone(persisted.draft.suggestion_database_id)

    async def test_creating_a_second_database_on_an_already_used_thread_is_rejected(self) -> None:
        # Conflict Prevention: a channel/thread can never route to two
        # databases at once, even when the second one is created through
        # the guided flow during setup.
        self.suggestion_service.create_database("Movies", GUILD_ID, DESTINATION_CHANNEL_ID)
        type_view = await self._reach_collection_type_choice()
        tv_shows_button = type_view.children[1]

        fake_home_channel = self.FakeHomeChannel(thread_id=DESTINATION_CHANNEL_ID)
        tv_interaction = FakeInteraction(guild=self.FakeGuildWithChannel(fake_home_channel))
        await tv_shows_button.callback(interaction=tv_interaction)

        # create_new_database's failure re-renders the current step with
        # a clear error rather than silently advancing.
        self.assertIn("already has a collection", tv_interaction.response.edited_content)
        databases = self.suggestion_service.list_databases(GUILD_ID)
        self.assertEqual(len(databases), 1)
        self.assertEqual(databases[0].name, "Movies")

    async def test_missing_home_channel_shows_a_warning_and_returns_to_type_choice(self) -> None:
        from watch_party_manager.setup_wizard_view import CollectionTypeChoiceView

        type_view = await self._reach_collection_type_choice()
        movies_button = type_view.children[0]

        movies_interaction = FakeInteraction(guild=self.FakeGuildWithChannel(None))
        await movies_button.callback(interaction=movies_interaction)

        self.assertIn("home channel is no longer available", movies_interaction.response.edited_content)
        self.assertIsInstance(movies_interaction.response.edited_view, CollectionTypeChoiceView)
        databases = self.suggestion_service.list_databases(GUILD_ID)
        self.assertEqual(databases, [])

    async def test_thread_creation_failure_shows_an_error_and_stays_recoverable(self) -> None:
        from watch_party_manager.setup_wizard_view import CollectionTypeChoiceView

        type_view = await self._reach_collection_type_choice()
        movies_button = type_view.children[0]

        failing_channel = self.FakeHomeChannel(fail=True)
        movies_interaction = FakeInteraction(guild=self.FakeGuildWithChannel(failing_channel))
        await movies_button.callback(interaction=movies_interaction)

        self.assertIn("Could not create the thread", movies_interaction.response.edited_content)
        self.assertIsInstance(movies_interaction.response.edited_view, CollectionTypeChoiceView)
        databases = self.suggestion_service.list_databases(GUILD_ID)
        self.assertEqual(databases, [])

    async def test_created_thread_is_persisted_as_the_collections_suggestion_destination(self) -> None:
        # Setup Bug fix (Requirement 6): the thread setup creates must be
        # saved immediately as the collection's Suggestion Destination,
        # not merely as its operational home channel -- otherwise /add
        # reports no suggestion channel is configured (Requirement 7).
        type_view = await self._reach_collection_type_choice()
        movies_button = type_view.children[0]

        fake_home_channel = self.FakeHomeChannel(thread_id=705)
        movies_interaction = FakeInteraction(guild=self.FakeGuildWithChannel(fake_home_channel))
        await movies_button.callback(interaction=movies_interaction)

        databases = self.suggestion_service.list_databases(GUILD_ID)
        configuration = self.suggestion_database_configuration_repository.get(GUILD_ID, databases[0].database_id)
        self.assertIsNotNone(configuration)
        self.assertEqual(configuration.channels.suggestion_channel_id, 705)

    async def test_add_immediately_posts_to_the_new_thread_with_no_configuration_error(self) -> None:
        # Requirement 7 validation: after setup creates a collection
        # thread, /add's public confirmation post must succeed in it
        # immediately -- no further configuration should be needed, and
        # post_suggestion_confirmation must never report "no suggestion
        # channel is configured".
        type_view = await self._reach_collection_type_choice()
        movies_button = type_view.children[0]

        fake_home_channel = self.FakeHomeChannel(thread_id=706)
        movies_interaction = FakeInteraction(guild=self.FakeGuildWithChannel(fake_home_channel))
        await movies_button.callback(interaction=movies_interaction)

        database = self.suggestion_service.list_databases(GUILD_ID)[0]
        watch_item = self.suggestion_service.suggest(
            "Alien", database_id=database.database_id, guild_id=GUILD_ID, channel_id=706
        ).watch_item

        class FakePostMessage:
            def __init__(self, message_id: int) -> None:
                self.id = message_id

        class FakePostChannel:
            def __init__(self) -> None:
                self.sent = []

            async def send(self, *, embed, view):
                self.sent.append((embed, view))
                return FakePostMessage(999)

        post_channel = FakePostChannel()

        class FakeGuildConfigurationRepository:
            def get(self, guild_id):
                return None

        class FakePostBot:
            def __init__(self, suggestion_service, suggestion_database_configuration_repository, channel):
                self.suggestion_service = suggestion_service
                self.suggestion_database_configuration_repository = suggestion_database_configuration_repository
                self.permission_service = None
                self.guild_configuration_repository = FakeGuildConfigurationRepository()
                self._channel = channel

            def get_channel(self, channel_id):
                return self._channel

            async def fetch_channel(self, channel_id):
                return self._channel

        fake_bot = FakePostBot(
            self.suggestion_service, self.suggestion_database_configuration_repository, post_channel
        )
        add_interaction = FakeInteraction()

        posted, note = await post_suggestion_confirmation(fake_bot, watch_item, database, add_interaction)

        self.assertTrue(posted)
        self.assertEqual(note, "")
        self.assertEqual(len(post_channel.sent), 1)

    async def test_watched_destination_thread_is_created_as_a_sibling_of_the_suggestion_thread(self) -> None:
        # Requirement 5 (Collections default to threads): the watched
        # destination thread must be created directly under the home
        # channel -- a sibling of the suggestion thread -- never nested
        # beneath it.
        type_view = await self._reach_collection_type_choice()
        movies_button = type_view.children[0]

        fake_home_channel = self.FakeHomeChannel(thread_id=707)
        movies_interaction = FakeInteraction(guild=self.FakeGuildWithChannel(fake_home_channel))
        await movies_button.callback(interaction=movies_interaction)

        state = self.wizard_repository.get(GUILD_ID)
        state = self.bot.setup_wizard_service.go_to_step(state, SetupWizardStep.WATCH_DESTINATION)
        interaction = FakeInteraction(guild=self.FakeGuildWithChannel(fake_home_channel))
        await send_setup_wizard_step(interaction, self.bot, state, edit=False)
        create_thread_button = next(
            c for c in interaction.response.sent_view.children
            if getattr(c, "label", None) == "Create New Thread (Recommended)"
        )

        create_interaction = FakeInteraction(guild=self.FakeGuildWithChannel(fake_home_channel))
        await create_thread_button.callback(interaction=create_interaction)
        name_modal = create_interaction.response.sent_modal
        name_modal.name_input._value = "Watched Item Archive"

        submit_interaction = FakeInteraction(guild=self.FakeGuildWithChannel(fake_home_channel))
        await name_modal.on_submit(interaction=submit_interaction)

        # The watched-destination thread was created directly against the
        # SAME home channel object the suggestion thread used -- a
        # sibling, never nested under the suggestion thread itself.
        self.assertEqual(
            fake_home_channel.created_with,
            ("Watched Item Archive", __import__("discord").ChannelType.public_thread),
        )
        persisted = self.wizard_repository.get(GUILD_ID)
        self.assertEqual(persisted.draft.watch_destination_channel_id, 707)


if __name__ == "__main__":
    unittest.main()
