"""Tests for FR-029's /config service (services/config_service.py).

Covers the FR-029 testing checklist: main configuration view (current
values, missing, skipped, invalid), section-based editing (each section:
existing value, valid change saved, invalid change preserves old value,
unrelated configuration remains unchanged), WASH Crew Role change
specifics, Watch Party Role & Join Mode, Suggestion Database, Watched-
Movie Destination, Voting/Reminder/Backup Defaults.
"""

import tempfile
import unittest
from pathlib import Path

from watch_party_manager.domain.guild_configuration import GuildConfiguration, GuildVoteVisibility, JoinMode
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

    def test_skipped_watch_destination_is_reported_as_skipped(self) -> None:
        self._seed_completed_setup()
        self._create_database()
        lines = self.service.build_summary_lines(GUILD_ID, self._full_guild())
        self.assertIn("Watched-Movie Destination: Skipped", lines)

    def test_invalid_role_no_longer_existing_is_reported_as_invalid(self) -> None:
        self._seed_completed_setup(wash_crew_role_id=999999)
        guild = FakeGuild(role_ids=set())
        lines = self.service.build_summary_lines(GUILD_ID, guild)
        self.assertTrue(any(line.startswith("WASH Crew Role: Invalid") for line in lines))

    def test_multiple_active_databases_is_reported_as_invalid(self) -> None:
        self._seed_completed_setup()
        self._create_database(channel_id=400, name="Movies")
        self._create_database(channel_id=401, name="TV Shows")
        lines = self.service.build_summary_lines(GUILD_ID, self._full_guild(extra_channel_ids=[401]))
        self.assertTrue(any(line.startswith("Active Suggestion Database: Invalid") for line in lines))

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


class SuggestionDatabaseSectionTests(ConfigServiceTestCase):
    def test_existing_database_is_selected(self) -> None:
        self._seed_completed_setup()
        database = self._create_database()
        result = self.service.set_active_suggestion_database(GUILD_ID, database.database_id)
        self.assertTrue(result.success)

    def test_missing_database_is_rejected(self) -> None:
        self._seed_completed_setup()
        result = self.service.set_active_suggestion_database(GUILD_ID, 999999)
        self.assertFalse(result.success)

    def test_activating_one_database_does_not_modify_others(self) -> None:
        self._seed_completed_setup()
        first = self._create_database(channel_id=400, name="Movies")
        second = self._create_database(channel_id=401, name="TV Shows")
        self.suggestion_service.deactivate_database(first.database_id, GUILD_ID)
        self.suggestion_service.deactivate_database(second.database_id, GUILD_ID)

        self.service.set_active_suggestion_database(GUILD_ID, first.database_id)

        self.assertTrue(self.suggestion_service.get_database(first.database_id).active)
        self.assertFalse(self.suggestion_service.get_database(second.database_id).active)

    def test_database_from_another_guild_is_rejected(self) -> None:
        self._seed_completed_setup()
        database = self._create_database(guild_id=OTHER_GUILD_ID)
        result = self.service.set_active_suggestion_database(GUILD_ID, database.database_id)
        self.assertFalse(result.success)


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


class WatchDestinationSectionTests(ConfigServiceTestCase):
    def test_channel_is_selected(self) -> None:
        self._seed_completed_setup()
        self._create_database()
        result = self.service.set_watch_destination(GUILD_ID, DESTINATION_CHANNEL_ID, self._full_guild())
        self.assertTrue(result.success)

    def test_thread_is_selected_the_same_way_as_a_channel(self) -> None:
        self._seed_completed_setup()
        self._create_database()
        thread_id = 987654321
        guild = FakeGuild(channel_ids={thread_id})
        result = self.service.set_watch_destination(GUILD_ID, thread_id, guild)
        self.assertTrue(result.success)

    def test_destination_can_be_cleared(self) -> None:
        self._seed_completed_setup()
        self._create_database()
        self.service.set_watch_destination(GUILD_ID, DESTINATION_CHANNEL_ID, self._full_guild())
        result = self.service.skip_watch_destination(GUILD_ID)
        self.assertTrue(result.success)

    def test_missing_resource_is_rejected(self) -> None:
        self._seed_completed_setup()
        self._create_database()
        guild = FakeGuild(channel_ids=set())
        result = self.service.set_watch_destination(GUILD_ID, 555, guild)
        self.assertFalse(result.success)

    def test_insufficient_bot_permissions_is_rejected(self) -> None:
        self._seed_completed_setup()
        self._create_database()
        guild = FakeGuild(
            channel_ids={DESTINATION_CHANNEL_ID},
            channel_permissions={DESTINATION_CHANNEL_ID: FakePermissions(send_messages=False)},
        )
        result = self.service.set_watch_destination(GUILD_ID, DESTINATION_CHANNEL_ID, guild)
        self.assertFalse(result.success)

    def test_requires_an_unambiguous_active_database_first(self) -> None:
        self._seed_completed_setup()
        result = self.service.set_watch_destination(GUILD_ID, DESTINATION_CHANNEL_ID, self._full_guild())
        self.assertFalse(result.success)


class VotingDefaultsSectionTests(ConfigServiceTestCase):
    def test_nominee_count_duration_and_visibility_are_updated(self) -> None:
        self._seed_completed_setup()
        result = self.service.set_voting_defaults(
            GUILD_ID, 5, 14, GuildVoteVisibility.VISIBLE, CandidateSelectionMode.ROTATION_POOL
        )
        self.assertTrue(result.success)
        voting_defaults = self.guild_configuration_repository.get(GUILD_ID).voting_defaults
        self.assertEqual(voting_defaults.candidate_count, 5)
        self.assertEqual(voting_defaults.duration_days, 14)
        self.assertEqual(voting_defaults.visibility, GuildVoteVisibility.VISIBLE)

    def test_candidate_selection_is_saved_to_the_active_database(self) -> None:
        self._seed_completed_setup()
        self._create_database()
        self.service.set_voting_defaults(
            GUILD_ID, 3, 7, GuildVoteVisibility.BLIND, CandidateSelectionMode.ROTATION_POOL
        )
        database_configuration = self.suggestion_database_configuration_repository.get(GUILD_ID, 1)
        self.assertEqual(database_configuration.suggestion_rules.candidate_selection, CandidateSelectionMode.ROTATION_POOL)

    def test_existing_max_vote_changes_and_tie_behavior_are_preserved(self) -> None:
        configuration = self._seed_completed_setup()
        self.service.set_voting_defaults(
            GUILD_ID, 5, 14, GuildVoteVisibility.VISIBLE, CandidateSelectionMode.ROTATION_POOL
        )
        updated = self.guild_configuration_repository.get(GUILD_ID).voting_defaults
        self.assertEqual(updated.max_vote_changes, configuration.voting_defaults.max_vote_changes)
        self.assertEqual(updated.tie_behavior, configuration.voting_defaults.tie_behavior)


class ReminderDefaultsSectionTests(ConfigServiceTestCase):
    def test_enabled_is_saved(self) -> None:
        self._seed_completed_setup()
        result = self.service.set_reminder_defaults(GUILD_ID, True, 48)
        self.assertTrue(result.success)
        vote_notifications = self.guild_configuration_repository.get(GUILD_ID).notifications.vote
        self.assertTrue(vote_notifications.vote_ending_reminder)
        self.assertEqual(vote_notifications.reminder_hours_before_close, 48)

    def test_disabled_is_saved(self) -> None:
        self._seed_completed_setup()
        result = self.service.set_reminder_defaults(GUILD_ID, False, 24)
        self.assertTrue(result.success)
        self.assertFalse(self.guild_configuration_repository.get(GUILD_ID).notifications.vote.vote_ending_reminder)

    def test_timing_is_updated(self) -> None:
        self._seed_completed_setup()
        self.service.set_reminder_defaults(GUILD_ID, True, 72)
        self.assertEqual(
            self.guild_configuration_repository.get(GUILD_ID).notifications.vote.reminder_hours_before_close, 72
        )


class BackupDefaultsSectionTests(ConfigServiceTestCase):
    def test_interval_and_retention_are_updated(self) -> None:
        self._seed_completed_setup()
        result = self.service.set_backup_defaults(GUILD_ID, 3, 45)
        self.assertTrue(result.success)
        backup = self.guild_configuration_repository.get(GUILD_ID).backup
        self.assertEqual(backup.extra_fields["automatic_backup_interval_days"], 3)
        self.assertEqual(backup.extra_fields["backup_retention_count"], 45)


if __name__ == "__main__":
    unittest.main()
