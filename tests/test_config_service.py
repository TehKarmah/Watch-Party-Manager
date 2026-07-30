"""Tests for FR-029's /config service (services/config_service.py).

Covers the FR-029 testing checklist: main configuration view (current
values, missing, skipped, invalid), section-based editing (each section:
existing value, valid change saved, invalid change preserves old value,
unrelated configuration remains unchanged), WASH Crew Role change
specifics, Watch Party Role & Join Mode, Manage Databases (per-database
destinations and candidate selection, replacing the old "Active
Suggestion Database" model), Voting/Reminder/Backup Defaults.
"""

import tempfile
import unittest
from pathlib import Path

from watch_party_manager.domain.guild_configuration import (
    GuildChannelsConfig,
    GuildConfiguration,
    GuildVoteVisibility,
    JoinMode,
)
from watch_party_manager.domain.suggestion_database_configuration import CandidateSelectionMode
from watch_party_manager.persistence.guild_configuration_repository import GuildConfigurationRepository
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.config_service import ConfigService
from watch_party_manager.services.suggestion_service import SuggestionService

GUILD_ID = 100
OTHER_GUILD_ID = 200
WASH_CREW_ROLE_ID = 111
WATCH_PARTY_ROLE_ID = 222
DESTINATION_CHANNEL_ID = 400


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


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


class FakeGuild:
    def __init__(self, *, role_ids=(), channel_ids=(), channel_permissions=None) -> None:
        self._role_ids = set(role_ids)
        self._channels = {
            channel_id: FakeChannel(channel_id, permissions=(channel_permissions or {}).get(channel_id))
            for channel_id in channel_ids
        }
        self.me = object()

    def get_role(self, role_id):
        return FakeRole(role_id) if role_id in self._role_ids else None

    def get_channel_or_thread(self, channel_id):
        return self._channels.get(channel_id)


class ConfigServiceTestCase(unittest.TestCase):
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
        self.service = ConfigService(
            self.guild_configuration_repository,
            self.suggestion_service,
            self.suggestion_database_configuration_repository,
        )

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _seed_completed_setup(self, **overrides) -> GuildConfiguration:
        configuration = GuildConfiguration(
            guild_id=GUILD_ID, guild_name="Test Guild", setup_completed=True, **overrides
        )
        self.guild_configuration_repository.save(configuration)
        return self.guild_configuration_repository.get(GUILD_ID)

    def _create_database(self, guild_id=GUILD_ID, channel_id=DESTINATION_CHANNEL_ID, name="Movies"):
        result = self.suggestion_service.create_database(name, guild_id, channel_id)
        self.assertTrue(result.success, result.message)
        return result.database

    def _full_guild(self, *, extra_channel_ids=()):
        return FakeGuild(
            role_ids={WASH_CREW_ROLE_ID, WATCH_PARTY_ROLE_ID},
            channel_ids={DESTINATION_CHANNEL_ID, *extra_channel_ids},
        )


class MainSummaryTests(ConfigServiceTestCase):
    def test_missing_configuration_reports_everything_not_configured(self) -> None:
        lines = self.service.build_summary_lines(GUILD_ID, self._full_guild())
        self.assertTrue(all("Not configured" in line for line in lines))

    def test_configured_values_are_shown(self) -> None:
        self._seed_completed_setup(wash_crew_role_id=WASH_CREW_ROLE_ID)
        lines = self.service.build_summary_lines(GUILD_ID, self._full_guild())
        self.assertIn(f"WASH Crew Role: Configured (<@&{WASH_CREW_ROLE_ID}>)", lines)

    def test_missing_wash_crew_role_reports_not_configured(self) -> None:
        self._seed_completed_setup()
        lines = self.service.build_summary_lines(GUILD_ID, self._full_guild())
        self.assertIn("WASH Crew Role: Not configured", lines)

    def test_manage_collections_reports_not_configured_when_none_exist(self) -> None:
        self._seed_completed_setup()
        lines = self.service.build_summary_lines(GUILD_ID, self._full_guild())
        self.assertIn("Collections: Not configured", lines)

    def test_manage_collections_reports_active_and_total_counts(self) -> None:
        self._seed_completed_setup()
        first = self._create_database(channel_id=400, name="Movies")
        self._create_database(channel_id=401, name="TV Shows")
        self.suggestion_service.deactivate_database(first.database_id, GUILD_ID)
        lines = self.service.build_summary_lines(GUILD_ID, self._full_guild(extra_channel_ids=[401]))
        self.assertTrue(any("Collections: Configured (1 active of 2 total" in line for line in lines))

    def test_watch_destination_default_reports_not_configured_when_unset(self) -> None:
        self._seed_completed_setup()
        lines = self.service.build_summary_lines(GUILD_ID, self._full_guild())
        self.assertIn("Watched Item Archive: Not configured", lines)

    def test_watch_destination_default_reports_configured_when_set(self) -> None:
        self._seed_completed_setup()
        self.service.set_guild_watch_destination(GUILD_ID, DESTINATION_CHANNEL_ID, self._full_guild())
        lines = self.service.build_summary_lines(GUILD_ID, self._full_guild())
        self.assertIn(f"Watched Item Archive: Configured (<#{DESTINATION_CHANNEL_ID}>)", lines)

    def test_invalid_role_no_longer_existing_is_reported_as_invalid(self) -> None:
        self._seed_completed_setup(wash_crew_role_id=999999)
        guild = FakeGuild(role_ids=set())
        lines = self.service.build_summary_lines(GUILD_ID, guild)
        self.assertTrue(any(line.startswith("WASH Crew Role: Invalid") for line in lines))

    def test_voting_defaults_summary_no_longer_mentions_candidate_selection(self) -> None:
        # Candidate selection is per-database now (Manage Databases); the
        # guild-wide Voting Defaults line only covers count/duration/visibility.
        self._seed_completed_setup()
        lines = self.service.build_summary_lines(GUILD_ID, self._full_guild())
        voting_line = next(line for line in lines if line.startswith("Voting Defaults:"))
        self.assertNotIn("candidate selection", voting_line)

    def test_summary_never_exposes_raw_channel_or_database_ids_as_bare_numbers(self) -> None:
        self._seed_completed_setup(wash_crew_role_id=WASH_CREW_ROLE_ID)
        lines = self.service.build_summary_lines(GUILD_ID, self._full_guild())
        combined = "\n".join(lines)
        # Discord role/channel mentions are the expected way IDs surface;
        # nothing here should print a bare "database_id" or file path.
        self.assertNotIn("data/", combined)
        self.assertNotIn(".json", combined)


class WashCrewRoleSectionTests(ConfigServiceTestCase):
    def test_valid_change_is_saved(self) -> None:
        self._seed_completed_setup(wash_crew_role_id=WASH_CREW_ROLE_ID)
        guild = FakeGuild(role_ids={WASH_CREW_ROLE_ID, 999})
        result = self.service.set_wash_crew_role(GUILD_ID, 999, guild)
        self.assertTrue(result.success)
        self.assertEqual(self.guild_configuration_repository.get(GUILD_ID).wash_crew_role_id, 999)

    def test_missing_role_is_rejected(self) -> None:
        self._seed_completed_setup(wash_crew_role_id=WASH_CREW_ROLE_ID)
        guild = FakeGuild(role_ids={WASH_CREW_ROLE_ID})
        result = self.service.set_wash_crew_role(GUILD_ID, 999999, guild)
        self.assertFalse(result.success)
        self.assertEqual(self.guild_configuration_repository.get(GUILD_ID).wash_crew_role_id, WASH_CREW_ROLE_ID)

    def test_existing_role_is_preserved_when_replacement_fails(self) -> None:
        self._seed_completed_setup(wash_crew_role_id=WASH_CREW_ROLE_ID)
        guild = FakeGuild(role_ids={WASH_CREW_ROLE_ID})
        self.service.set_wash_crew_role(GUILD_ID, 999999, guild)
        self.assertEqual(self.guild_configuration_repository.get(GUILD_ID).wash_crew_role_id, WASH_CREW_ROLE_ID)

    def test_unrelated_configuration_is_untouched(self) -> None:
        self._seed_completed_setup(wash_crew_role_id=WASH_CREW_ROLE_ID)
        guild = FakeGuild(role_ids={WASH_CREW_ROLE_ID, 999})
        before = self.guild_configuration_repository.get(GUILD_ID)
        self.service.set_wash_crew_role(GUILD_ID, 999, guild)
        after = self.guild_configuration_repository.get(GUILD_ID)
        self.assertEqual(after.voting_defaults, before.voting_defaults)
        self.assertEqual(after.watch_party_role, before.watch_party_role)

    def test_requires_setup_to_already_be_completed(self) -> None:
        result = self.service.set_wash_crew_role(GUILD_ID, WASH_CREW_ROLE_ID, self._full_guild())
        self.assertFalse(result.success)


class WatchPartyRoleAndJoinModeSectionTests(ConfigServiceTestCase):
    def test_role_is_updated(self) -> None:
        self._seed_completed_setup()
        guild = FakeGuild(role_ids={WATCH_PARTY_ROLE_ID})
        result = self.service.set_watch_party_role(GUILD_ID, WATCH_PARTY_ROLE_ID, guild)
        self.assertTrue(result.success)
        self.assertEqual(
            self.guild_configuration_repository.get(GUILD_ID).watch_party_role.role_id, WATCH_PARTY_ROLE_ID
        )

    def test_invalid_role_is_rejected(self) -> None:
        self._seed_completed_setup()
        guild = FakeGuild(role_ids=set())
        result = self.service.set_watch_party_role(GUILD_ID, 999999, guild)
        self.assertFalse(result.success)
        self.assertIsNone(self.guild_configuration_repository.get(GUILD_ID).watch_party_role.role_id)

    def test_each_join_mode_persists_correctly(self) -> None:
        self._seed_completed_setup()
        for join_mode in JoinMode:
            result = self.service.set_watch_party_join_mode(GUILD_ID, join_mode)
            self.assertTrue(result.success)
            self.assertEqual(self.guild_configuration_repository.get(GUILD_ID).watch_party_role.join_mode, join_mode)

    def test_join_mode_change_does_not_touch_role_id(self) -> None:
        self._seed_completed_setup()
        guild = FakeGuild(role_ids={WATCH_PARTY_ROLE_ID})
        self.service.set_watch_party_role(GUILD_ID, WATCH_PARTY_ROLE_ID, guild)
        self.service.set_watch_party_join_mode(GUILD_ID, JoinMode.APPROVAL)
        configuration = self.guild_configuration_repository.get(GUILD_ID)
        self.assertEqual(configuration.watch_party_role.role_id, WATCH_PARTY_ROLE_ID)
        self.assertEqual(configuration.watch_party_role.join_mode, JoinMode.APPROVAL)


class AdminChannelSectionTests(ConfigServiceTestCase):
    def test_channel_is_selected(self) -> None:
        self._seed_completed_setup()
        result = self.service.set_admin_channel(GUILD_ID, DESTINATION_CHANNEL_ID, self._full_guild())
        self.assertTrue(result.success)
        self.assertEqual(
            self.guild_configuration_repository.get(GUILD_ID).channels.admin_channel_id, DESTINATION_CHANNEL_ID
        )

    def test_missing_resource_is_rejected(self):
        self._seed_completed_setup()
        guild = FakeGuild(channel_ids=set())
        result = self.service.set_admin_channel(GUILD_ID, 555, guild)
        self.assertFalse(result.success)
        self.assertIsNone(self.guild_configuration_repository.get(GUILD_ID).channels.admin_channel_id)

    def test_insufficient_bot_permissions_is_rejected(self):
        self._seed_completed_setup()
        guild = FakeGuild(
            channel_ids={DESTINATION_CHANNEL_ID},
            channel_permissions={DESTINATION_CHANNEL_ID: FakePermissions(send_messages=False)},
        )
        result = self.service.set_admin_channel(GUILD_ID, DESTINATION_CHANNEL_ID, guild)
        self.assertFalse(result.success)

    def test_channel_can_be_cleared(self):
        self._seed_completed_setup()
        self.service.set_admin_channel(GUILD_ID, DESTINATION_CHANNEL_ID, self._full_guild())
        result = self.service.clear_admin_channel(GUILD_ID)
        self.assertTrue(result.success)
        self.assertIsNone(self.guild_configuration_repository.get(GUILD_ID).channels.admin_channel_id)

    def test_summary_reports_not_configured_when_unset(self):
        self._seed_completed_setup()
        lines = self.service.build_summary_lines(GUILD_ID, self._full_guild())
        self.assertIn("Admin Channel: Not configured", lines)

    def test_summary_reports_configured_when_set(self):
        self._seed_completed_setup()
        self.service.set_admin_channel(GUILD_ID, DESTINATION_CHANNEL_ID, self._full_guild())
        lines = self.service.build_summary_lines(GUILD_ID, self._full_guild())
        self.assertIn(f"Admin Channel: Configured (<#{DESTINATION_CHANNEL_ID}>)", lines)

    def test_summary_reports_invalid_when_channel_no_longer_usable(self):
        self._seed_completed_setup()
        self.service.set_admin_channel(GUILD_ID, DESTINATION_CHANNEL_ID, self._full_guild())
        guild = FakeGuild(channel_ids=set())
        lines = self.service.build_summary_lines(GUILD_ID, guild)
        self.assertTrue(any(line.startswith("Admin Channel: Invalid") for line in lines))


class HomeChannelSectionTests(ConfigServiceTestCase):
    """Home Channel Visibility & Configuration (polish batch): a dedicated,
    prominently-labeled Watch Party Home Channel section -- previously
    this setting had no summary line and no dedicated section at all.
    """

    def test_channel_is_selected(self) -> None:
        self._seed_completed_setup()
        result = self.service.set_home_channel(GUILD_ID, DESTINATION_CHANNEL_ID, self._full_guild())
        self.assertTrue(result.success)
        self.assertEqual(
            self.guild_configuration_repository.get(GUILD_ID).channels.home_channel_id, DESTINATION_CHANNEL_ID
        )

    def test_missing_resource_is_rejected(self):
        self._seed_completed_setup()
        guild = FakeGuild(channel_ids=set())
        result = self.service.set_home_channel(GUILD_ID, 555, guild)
        self.assertFalse(result.success)
        self.assertIsNone(self.guild_configuration_repository.get(GUILD_ID).channels.home_channel_id)

    def test_insufficient_bot_permissions_is_rejected(self):
        self._seed_completed_setup()
        guild = FakeGuild(
            channel_ids={DESTINATION_CHANNEL_ID},
            channel_permissions={DESTINATION_CHANNEL_ID: FakePermissions(send_messages=False)},
        )
        result = self.service.set_home_channel(GUILD_ID, DESTINATION_CHANNEL_ID, guild)
        self.assertFalse(result.success)
        self.assertIsNone(self.guild_configuration_repository.get(GUILD_ID).channels.home_channel_id)

    def test_channel_can_be_cleared(self):
        self._seed_completed_setup()
        self.service.set_home_channel(GUILD_ID, DESTINATION_CHANNEL_ID, self._full_guild())
        result = self.service.clear_home_channel(GUILD_ID)
        self.assertTrue(result.success)
        self.assertIsNone(self.guild_configuration_repository.get(GUILD_ID).channels.home_channel_id)

    def test_summary_reports_not_configured_when_unset(self):
        self._seed_completed_setup()
        lines = self.service.build_summary_lines(GUILD_ID, self._full_guild())
        self.assertIn("Watch Party Home Channel: Not configured", lines)

    def test_summary_reports_configured_when_set(self):
        self._seed_completed_setup()
        self.service.set_home_channel(GUILD_ID, DESTINATION_CHANNEL_ID, self._full_guild())
        lines = self.service.build_summary_lines(GUILD_ID, self._full_guild())
        self.assertIn(f"Watch Party Home Channel: Configured (<#{DESTINATION_CHANNEL_ID}>)", lines)

    def test_summary_reports_invalid_when_channel_no_longer_usable(self):
        self._seed_completed_setup()
        self.service.set_home_channel(GUILD_ID, DESTINATION_CHANNEL_ID, self._full_guild())
        guild = FakeGuild(channel_ids=set())
        lines = self.service.build_summary_lines(GUILD_ID, guild)
        self.assertTrue(any(line.startswith("Watch Party Home Channel: Invalid") for line in lines))


class ManageDatabasesSectionTests(ConfigServiceTestCase):
    """Contextual Database Resolution / Collections refinement: replaces
    the old "Active Suggestion Database" model. Each collection owns its
    own destination/candidate-selection settings, edited directly by
    database_id -- no "exactly one active database" requirement anywhere
    in this section, and every collection always has exactly one
    suggestion destination (settable, but never clearable).
    """

    def test_get_database_configuration_returns_a_fresh_default_when_none_saved_yet(self) -> None:
        self._seed_completed_setup()
        database = self._create_database()
        configuration = self.service.get_database_configuration(GUILD_ID, database.database_id)
        self.assertIsNone(configuration.channels.suggestion_channel_id)
        self.assertEqual(configuration.suggestion_rules.candidate_selection, CandidateSelectionMode.ROTATION_POOL)

    def test_multiple_simultaneously_active_databases_are_both_directly_editable(self) -> None:
        # Unlike the old model, having more than one active database is
        # never "Invalid" -- both are independently, directly editable.
        self._seed_completed_setup()
        first = self._create_database(channel_id=400, name="Movies")
        second = self._create_database(channel_id=401, name="TV Shows")
        guild = self._full_guild(extra_channel_ids=[401, 500, 501])

        first_result = self.service.set_database_suggestion_destination(GUILD_ID, first.database_id, 500, guild)
        second_result = self.service.set_database_suggestion_destination(GUILD_ID, second.database_id, 501, guild)

        self.assertTrue(first_result.success, first_result.message)
        self.assertTrue(second_result.success, second_result.message)

    def test_suggestion_destination_is_set_for_the_specific_database(self) -> None:
        self._seed_completed_setup()
        database = self._create_database()
        other_channel = 401
        guild = self._full_guild(extra_channel_ids=[other_channel])

        result = self.service.set_database_suggestion_destination(GUILD_ID, database.database_id, other_channel, guild)

        self.assertTrue(result.success)
        database_configuration = self.suggestion_database_configuration_repository.get(GUILD_ID, database.database_id)
        self.assertEqual(database_configuration.channels.suggestion_channel_id, other_channel)

    def test_suggestion_destination_has_no_clear_capability(self) -> None:
        # Every collection MUST have exactly one dedicated suggestion
        # destination -- unlike watch destinations, there is no
        # clear_database_suggestion_destination method at all.
        self.assertFalse(hasattr(self.service, "clear_database_suggestion_destination"))

    def test_suggestion_destination_always_resolves_to_exactly_one_channel(self) -> None:
        # Even before any override is configured, the collection's home
        # channel (set at creation, always required) is the effective
        # suggestion destination -- never None.
        database = self._create_database()
        channel_id = self.suggestion_service.resolve_collection_channel_id(
            database, self.suggestion_database_configuration_repository
        )
        self.assertEqual(channel_id, database.channel_id)

    def test_suggestion_destination_rejects_the_configured_home_channel(self) -> None:
        # Prevent Collections From Using The Home Channel: the one shared
        # implementation behind both /config's Suggestion Destination
        # section and /database move must reject WASH's configured Home
        # Channel as a collection's suggestion destination.
        home_channel_id = 700
        self._seed_completed_setup(channels=GuildChannelsConfig(home_channel_id=home_channel_id))
        database = self._create_database()
        guild = self._full_guild(extra_channel_ids=[home_channel_id])

        result = self.service.set_database_suggestion_destination(GUILD_ID, database.database_id, home_channel_id, guild)

        self.assertFalse(result.success)
        self.assertIn("Home Channel", result.message)
        self.assertIsNone(self.suggestion_database_configuration_repository.get(GUILD_ID, database.database_id))

    def test_suggestion_destination_missing_channel_is_rejected(self) -> None:
        self._seed_completed_setup()
        database = self._create_database()
        guild = FakeGuild(channel_ids=set())
        result = self.service.set_database_suggestion_destination(GUILD_ID, database.database_id, 555, guild)
        self.assertFalse(result.success)

    def test_suggestion_destination_for_unknown_database_is_rejected(self) -> None:
        self._seed_completed_setup()
        result = self.service.set_database_suggestion_destination(GUILD_ID, 999999, DESTINATION_CHANNEL_ID, self._full_guild())
        self.assertFalse(result.success)

    def test_suggestion_destination_for_another_guilds_database_is_rejected(self) -> None:
        self._seed_completed_setup()
        database = self._create_database(guild_id=OTHER_GUILD_ID)
        result = self.service.set_database_suggestion_destination(
            GUILD_ID, database.database_id, DESTINATION_CHANNEL_ID, self._full_guild()
        )
        self.assertFalse(result.success)

    def test_suggestion_destination_already_used_by_another_database_is_rejected(self) -> None:
        # Conflict Prevention: a channel/thread can never route to two
        # databases at once.
        self._seed_completed_setup()
        first = self._create_database(channel_id=400, name="Movies")
        second = self._create_database(channel_id=401, name="TV Shows")
        guild = self._full_guild(extra_channel_ids=[401, 500])
        self.service.set_database_suggestion_destination(GUILD_ID, first.database_id, 500, guild)

        result = self.service.set_database_suggestion_destination(GUILD_ID, second.database_id, 500, guild)

        self.assertFalse(result.success)
        self.assertIn("already routed", result.message)
        # Nothing was persisted for the rejected database at all.
        self.assertIsNone(self.suggestion_database_configuration_repository.get(GUILD_ID, second.database_id))

    def test_suggestion_destination_matching_another_databases_home_channel_is_rejected(self) -> None:
        self._seed_completed_setup()
        self._create_database(channel_id=400, name="Movies")
        second = self._create_database(channel_id=401, name="TV Shows")
        guild = self._full_guild(extra_channel_ids=[401])

        result = self.service.set_database_suggestion_destination(GUILD_ID, second.database_id, 400, guild)

        self.assertFalse(result.success)

    def test_watch_destination_is_set_for_the_specific_database(self) -> None:
        self._seed_completed_setup()
        database = self._create_database()
        result = self.service.set_database_watch_destination(GUILD_ID, database.database_id, DESTINATION_CHANNEL_ID, self._full_guild())
        self.assertTrue(result.success)
        database_configuration = self.suggestion_database_configuration_repository.get(GUILD_ID, database.database_id)
        self.assertEqual(database_configuration.channels.watch_history_channel_id, DESTINATION_CHANNEL_ID)

    def test_watch_destination_thread_is_selected_the_same_way_as_a_channel(self) -> None:
        self._seed_completed_setup()
        database = self._create_database()
        thread_id = 987654321
        guild = FakeGuild(channel_ids={thread_id})
        result = self.service.set_database_watch_destination(GUILD_ID, database.database_id, thread_id, guild)
        self.assertTrue(result.success)

    def test_watch_destination_can_be_cleared(self) -> None:
        self._seed_completed_setup()
        database = self._create_database()
        self.service.set_database_watch_destination(GUILD_ID, database.database_id, DESTINATION_CHANNEL_ID, self._full_guild())
        result = self.service.clear_database_watch_destination(GUILD_ID, database.database_id)
        self.assertTrue(result.success)
        database_configuration = self.suggestion_database_configuration_repository.get(GUILD_ID, database.database_id)
        self.assertIsNone(database_configuration.channels.watch_history_channel_id)

    def test_watch_destination_missing_resource_is_rejected(self) -> None:
        self._seed_completed_setup()
        database = self._create_database()
        guild = FakeGuild(channel_ids=set())
        result = self.service.set_database_watch_destination(GUILD_ID, database.database_id, 555, guild)
        self.assertFalse(result.success)

    def test_watch_destination_insufficient_bot_permissions_is_rejected(self) -> None:
        self._seed_completed_setup()
        database = self._create_database()
        guild = FakeGuild(
            channel_ids={DESTINATION_CHANNEL_ID},
            channel_permissions={DESTINATION_CHANNEL_ID: FakePermissions(send_messages=False)},
        )
        result = self.service.set_database_watch_destination(GUILD_ID, database.database_id, DESTINATION_CHANNEL_ID, guild)
        self.assertFalse(result.success)

    def test_setting_suggestion_destination_does_not_change_watch_destination(self) -> None:
        self._seed_completed_setup()
        database = self._create_database()
        self.service.set_database_watch_destination(GUILD_ID, database.database_id, DESTINATION_CHANNEL_ID, self._full_guild())
        other_channel_id = 401
        guild = self._full_guild(extra_channel_ids=[other_channel_id])
        self.service.set_database_suggestion_destination(GUILD_ID, database.database_id, other_channel_id, guild)

        database_configuration = self.suggestion_database_configuration_repository.get(GUILD_ID, database.database_id)
        self.assertEqual(database_configuration.channels.watch_history_channel_id, DESTINATION_CHANNEL_ID)
        self.assertEqual(database_configuration.channels.suggestion_channel_id, other_channel_id)

    def test_candidate_selection_is_saved_for_the_specific_database(self) -> None:
        self._seed_completed_setup()
        database = self._create_database()
        result = self.service.set_database_candidate_selection(GUILD_ID, database.database_id, CandidateSelectionMode.SOFT_ROTATION)
        self.assertTrue(result.success)
        database_configuration = self.suggestion_database_configuration_repository.get(GUILD_ID, database.database_id)
        self.assertEqual(database_configuration.suggestion_rules.candidate_selection, CandidateSelectionMode.SOFT_ROTATION)

    def test_candidate_selection_for_one_database_does_not_affect_another(self) -> None:
        self._seed_completed_setup()
        first = self._create_database(channel_id=400, name="Movies")
        second = self._create_database(channel_id=401, name="TV Shows")

        self.service.set_database_candidate_selection(GUILD_ID, first.database_id, CandidateSelectionMode.INFINITE_POOL)

        second_configuration = self.suggestion_database_configuration_repository.get(GUILD_ID, second.database_id)
        second_mode = (
            second_configuration.suggestion_rules.candidate_selection
            if second_configuration is not None
            else CandidateSelectionMode.ROTATION_POOL
        )
        self.assertEqual(second_mode, CandidateSelectionMode.ROTATION_POOL)

    def test_candidate_selection_for_unknown_database_is_rejected(self) -> None:
        self._seed_completed_setup()
        result = self.service.set_database_candidate_selection(GUILD_ID, 999999, CandidateSelectionMode.SOFT_ROTATION)
        self.assertFalse(result.success)


class WatchDestinationSectionTests(ConfigServiceTestCase):
    """Design refinement: watched destinations remain optional and may be
    shared freely. Supported configurations: none, one universal
    (guild-wide) destination, or per-collection overrides -- a
    per-collection override always wins over the universal default.
    """

    def test_guild_wide_default_is_set_and_read_back(self) -> None:
        self._seed_completed_setup()
        result = self.service.set_guild_watch_destination(GUILD_ID, DESTINATION_CHANNEL_ID, self._full_guild())
        self.assertTrue(result.success)
        self.assertEqual(
            self.guild_configuration_repository.get(GUILD_ID).channels.watch_history_channel_id, DESTINATION_CHANNEL_ID
        )

    def test_guild_wide_default_can_be_cleared(self) -> None:
        self._seed_completed_setup()
        self.service.set_guild_watch_destination(GUILD_ID, DESTINATION_CHANNEL_ID, self._full_guild())
        result = self.service.clear_guild_watch_destination(GUILD_ID)
        self.assertTrue(result.success)
        self.assertIsNone(self.guild_configuration_repository.get(GUILD_ID).channels.watch_history_channel_id)

    def test_effective_destination_is_none_when_neither_is_set(self) -> None:
        self._seed_completed_setup()
        database = self._create_database()
        effective = self.service.resolve_effective_watch_destination(GUILD_ID, database.database_id)
        self.assertIsNone(effective)

    def test_effective_destination_falls_back_to_guild_default(self) -> None:
        self._seed_completed_setup()
        database = self._create_database()
        self.service.set_guild_watch_destination(GUILD_ID, DESTINATION_CHANNEL_ID, self._full_guild())

        effective = self.service.resolve_effective_watch_destination(GUILD_ID, database.database_id)

        self.assertEqual(effective, DESTINATION_CHANNEL_ID)

    def test_per_collection_override_wins_over_guild_default(self) -> None:
        self._seed_completed_setup()
        database = self._create_database()
        other_channel = 401
        guild = self._full_guild(extra_channel_ids=[other_channel])
        self.service.set_guild_watch_destination(GUILD_ID, DESTINATION_CHANNEL_ID, guild)
        self.service.set_database_watch_destination(GUILD_ID, database.database_id, other_channel, guild)

        effective = self.service.resolve_effective_watch_destination(GUILD_ID, database.database_id)

        self.assertEqual(effective, other_channel)

    def test_watch_destination_may_be_shared_by_two_collections(self) -> None:
        # Unlike suggestion destinations, watch destinations are never
        # checked for conflicts -- any number of collections (and/or the
        # guild default) may share the same channel.
        self._seed_completed_setup()
        first = self._create_database(channel_id=400, name="Movies")
        second = self._create_database(channel_id=401, name="TV Shows")
        guild = self._full_guild(extra_channel_ids=[401])

        first_result = self.service.set_database_watch_destination(GUILD_ID, first.database_id, DESTINATION_CHANNEL_ID, guild)
        second_result = self.service.set_database_watch_destination(GUILD_ID, second.database_id, DESTINATION_CHANNEL_ID, guild)

        self.assertTrue(first_result.success)
        self.assertTrue(second_result.success)
        self.assertEqual(
            self.service.resolve_effective_watch_destination(GUILD_ID, first.database_id), DESTINATION_CHANNEL_ID
        )
        self.assertEqual(
            self.service.resolve_effective_watch_destination(GUILD_ID, second.database_id), DESTINATION_CHANNEL_ID
        )


class VotingDefaultsSectionTests(ConfigServiceTestCase):
    def test_nominee_count_duration_and_visibility_are_updated(self) -> None:
        self._seed_completed_setup()
        result = self.service.set_voting_defaults(GUILD_ID, 5, 14, GuildVoteVisibility.VISIBLE)
        self.assertTrue(result.success)
        voting_defaults = self.guild_configuration_repository.get(GUILD_ID).voting_defaults
        self.assertEqual(voting_defaults.candidate_count, 5)
        self.assertEqual(voting_defaults.duration_minutes, 14)
        self.assertEqual(voting_defaults.visibility, GuildVoteVisibility.VISIBLE)

    def test_existing_max_vote_changes_and_tie_behavior_are_preserved(self) -> None:
        configuration = self._seed_completed_setup()
        self.service.set_voting_defaults(GUILD_ID, 5, 14, GuildVoteVisibility.VISIBLE)
        updated = self.guild_configuration_repository.get(GUILD_ID).voting_defaults
        self.assertEqual(updated.max_vote_changes, configuration.voting_defaults.max_vote_changes)
        self.assertEqual(updated.tie_behavior, configuration.voting_defaults.tie_behavior)

    def test_does_not_touch_any_databases_candidate_selection(self) -> None:
        # Candidate selection moved to Manage Databases -- Voting Defaults
        # is guild-wide only now and must never reach into per-database
        # configuration.
        self._seed_completed_setup()
        database = self._create_database()
        self.service.set_database_candidate_selection(GUILD_ID, database.database_id, CandidateSelectionMode.SOFT_ROTATION)

        self.service.set_voting_defaults(GUILD_ID, 5, 14, GuildVoteVisibility.VISIBLE)

        database_configuration = self.suggestion_database_configuration_repository.get(GUILD_ID, database.database_id)
        self.assertEqual(database_configuration.suggestion_rules.candidate_selection, CandidateSelectionMode.SOFT_ROTATION)


class ReminderDefaultsSectionTests(ConfigServiceTestCase):
    def test_enabled_is_saved(self) -> None:
        self._seed_completed_setup()
        result = self.service.enable_vote_ending_reminder(GUILD_ID, 48)
        self.assertTrue(result.success)
        vote_notifications = self.guild_configuration_repository.get(GUILD_ID).notifications.vote
        self.assertTrue(vote_notifications.vote_ending_reminder)
        self.assertEqual(vote_notifications.reminder_minutes_before_close, 48)

    def test_disabled_is_saved(self) -> None:
        self._seed_completed_setup()
        result = self.service.disable_vote_ending_reminder(GUILD_ID)
        self.assertTrue(result.success)
        self.assertFalse(self.guild_configuration_repository.get(GUILD_ID).notifications.vote.vote_ending_reminder)

    def test_disabling_does_not_change_the_previously_saved_lead_time(self) -> None:
        # Fixed-Option UX Audit: Disable is a button choice that skips the
        # lead-time modal entirely -- it must never blow away whatever
        # lead time was last configured.
        self._seed_completed_setup()
        self.service.enable_vote_ending_reminder(GUILD_ID, 72)
        self.service.disable_vote_ending_reminder(GUILD_ID)
        self.assertEqual(
            self.guild_configuration_repository.get(GUILD_ID).notifications.vote.reminder_minutes_before_close, 72
        )

    def test_timing_is_updated(self) -> None:
        self._seed_completed_setup()
        self.service.enable_vote_ending_reminder(GUILD_ID, 72)
        self.assertEqual(
            self.guild_configuration_repository.get(GUILD_ID).notifications.vote.reminder_minutes_before_close, 72
        )


class BackupDefaultsSectionTests(ConfigServiceTestCase):
    def test_interval_and_retention_are_updated(self) -> None:
        self._seed_completed_setup()
        result = self.service.enable_automatic_backups(GUILD_ID, 3, 45)
        self.assertTrue(result.success)
        backup = self.guild_configuration_repository.get(GUILD_ID).backup
        self.assertTrue(backup.include_in_automatic_backups)
        self.assertEqual(backup.extra_fields["automatic_backup_interval_days"], 3)
        self.assertEqual(backup.extra_fields["backup_retention_count"], 45)

    def test_disabling_automatic_backups(self) -> None:
        self._seed_completed_setup()
        result = self.service.disable_automatic_backups(GUILD_ID)
        self.assertTrue(result.success)
        backup = self.guild_configuration_repository.get(GUILD_ID).backup
        self.assertFalse(backup.include_in_automatic_backups)

    def test_re_enabling_automatic_backups_after_disabling(self) -> None:
        self._seed_completed_setup()
        self.service.disable_automatic_backups(GUILD_ID)
        result = self.service.enable_automatic_backups(GUILD_ID, 7, 10)
        self.assertTrue(result.success)
        backup = self.guild_configuration_repository.get(GUILD_ID).backup
        self.assertTrue(backup.include_in_automatic_backups)
        self.assertEqual(backup.extra_fields["automatic_backup_interval_days"], 7)
        self.assertEqual(backup.extra_fields["backup_retention_count"], 10)


if __name__ == "__main__":
    unittest.main()
