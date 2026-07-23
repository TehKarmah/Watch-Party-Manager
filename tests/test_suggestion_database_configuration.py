"""Tests for the Suggestion Database Configuration domain models."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from watch_party_manager.domain.guild_configuration import GuildVoteVisibility, TieBehavior
from watch_party_manager.domain.suggestion_database_configuration import (
    CandidateSelectionMode,
    SuggestionAdmissionMode,
    SuggestionDatabaseArchiveConfig,
    SuggestionDatabaseChannelsConfig,
    SuggestionDatabaseConfiguration,
    SuggestionDatabaseNotificationOverridesConfig,
    SuggestionDatabasePermissionsConfig,
    SuggestionDatabaseWatchHistoryConfig,
    SuggestionRulesConfig,
    VotingOverridesConfig,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SuggestionDatabaseConfigurationTests(unittest.TestCase):
    def test_valid_minimal_configuration(self) -> None:
        config = SuggestionDatabaseConfiguration(guild_id=100, database_id=1, display_name="Movies")

        self.assertEqual(config.guild_id, 100)
        self.assertEqual(config.database_id, 1)
        self.assertEqual(config.display_name, "Movies")
        self.assertTrue(config.active)
        self.assertEqual(config.schema_version, 1)
        self.assertEqual(config.configuration_version, 1)

    def test_display_name_is_trimmed(self) -> None:
        config = SuggestionDatabaseConfiguration(guild_id=100, database_id=1, display_name="  Movies  ")
        self.assertEqual(config.display_name, "Movies")

    def test_active_can_be_set_to_false(self) -> None:
        config = SuggestionDatabaseConfiguration(
            guild_id=100, database_id=1, display_name="Movies", active=False
        )
        self.assertFalse(config.active)

    def test_default_sections_use_documented_defaults(self) -> None:
        config = SuggestionDatabaseConfiguration(guild_id=100, database_id=1, display_name="Movies")

        self.assertIsInstance(config.channels, SuggestionDatabaseChannelsConfig)
        self.assertIsInstance(config.voting_overrides, VotingOverridesConfig)
        self.assertIsInstance(config.suggestion_rules, SuggestionRulesConfig)
        self.assertIsInstance(config.watch_history, SuggestionDatabaseWatchHistoryConfig)
        self.assertIsInstance(config.archive, SuggestionDatabaseArchiveConfig)
        self.assertIsInstance(config.notifications, SuggestionDatabaseNotificationOverridesConfig)
        self.assertIsInstance(config.permissions, SuggestionDatabasePermissionsConfig)

    # --- guild_id / database_id validation -----------------------------------

    def test_rejects_non_positive_guild_and_database_ids(self) -> None:
        for guild_id, database_id in ((0, 1), (-1, 1), (1, 0), (1, -1)):
            with self.subTest(guild_id=guild_id, database_id=database_id):
                with self.assertRaises(ValueError):
                    SuggestionDatabaseConfiguration(
                        guild_id=guild_id, database_id=database_id, display_name="Movies"
                    )

    def test_rejects_a_boolean_guild_id(self) -> None:
        with self.assertRaises(ValueError):
            SuggestionDatabaseConfiguration(guild_id=True, database_id=1, display_name="Movies")

    def test_rejects_a_boolean_database_id(self) -> None:
        with self.assertRaises(ValueError):
            SuggestionDatabaseConfiguration(guild_id=100, database_id=True, display_name="Movies")

    # --- display_name validation ----------------------------------------------

    def test_rejects_an_empty_display_name(self) -> None:
        with self.assertRaises(ValueError):
            SuggestionDatabaseConfiguration(guild_id=100, database_id=1, display_name="")

    def test_rejects_a_whitespace_only_display_name(self) -> None:
        with self.assertRaises(ValueError):
            SuggestionDatabaseConfiguration(guild_id=100, database_id=1, display_name="   ")

    # --- schema_version / configuration_version validation -----------------------

    def test_rejects_a_schema_version_below_one(self) -> None:
        with self.assertRaises(ValueError):
            SuggestionDatabaseConfiguration(
                guild_id=100, database_id=1, display_name="Movies", schema_version=0
            )

    def test_rejects_a_configuration_version_below_one(self) -> None:
        with self.assertRaises(ValueError):
            SuggestionDatabaseConfiguration(
                guild_id=100, database_id=1, display_name="Movies", configuration_version=0
            )

    # --- timestamp validation -----------------------------------------------------

    def test_rejects_a_naive_created_at(self) -> None:
        with self.assertRaises(ValueError):
            SuggestionDatabaseConfiguration(
                guild_id=100, database_id=1, display_name="Movies", created_at=datetime(2026, 1, 1)
            )

    def test_rejects_a_naive_updated_at(self) -> None:
        with self.assertRaises(ValueError):
            SuggestionDatabaseConfiguration(
                guild_id=100, database_id=1, display_name="Movies", updated_at=datetime(2026, 1, 1)
            )

    def test_rejects_updated_at_before_created_at(self) -> None:
        created = utc_now()
        earlier = datetime(2020, 1, 1, tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            SuggestionDatabaseConfiguration(
                guild_id=100,
                database_id=1,
                display_name="Movies",
                created_at=created,
                updated_at=earlier,
            )

    def test_accepts_explicit_timezone_aware_timestamps(self) -> None:
        created = utc_now()
        config = SuggestionDatabaseConfiguration(
            guild_id=100, database_id=1, display_name="Movies", created_at=created, updated_at=created
        )
        self.assertEqual(config.created_at, created)

    # --- extra_fields (unknown top-level field preservation) ----------------------

    def test_accepts_extra_fields(self) -> None:
        config = SuggestionDatabaseConfiguration(
            guild_id=100, database_id=1, display_name="Movies", extra_fields={"future_field": "value"}
        )
        self.assertEqual(config.extra_fields, {"future_field": "value"})

    def test_rejects_a_non_dict_extra_fields(self) -> None:
        with self.assertRaises(ValueError):
            SuggestionDatabaseConfiguration(
                guild_id=100, database_id=1, display_name="Movies", extra_fields="not a dict"
            )


class SuggestionDatabaseChannelsConfigTests(unittest.TestCase):
    def test_defaults_to_unset(self) -> None:
        channels = SuggestionDatabaseChannelsConfig()

        self.assertIsNone(channels.suggestion_channel_id)
        self.assertIsNone(channels.voting_channel_id)
        self.assertIsNone(channels.watch_history_channel_id)
        self.assertIsNone(channels.archive_channel_id)

    def test_suggestion_and_voting_may_share_a_channel(self) -> None:
        channels = SuggestionDatabaseChannelsConfig(suggestion_channel_id=10, voting_channel_id=10)
        self.assertEqual(channels.suggestion_channel_id, channels.voting_channel_id)

    def test_watch_history_and_archive_must_be_different_channels(self) -> None:
        with self.assertRaises(ValueError):
            SuggestionDatabaseChannelsConfig(watch_history_channel_id=20, archive_channel_id=20)

    def test_watch_history_and_archive_may_differ(self) -> None:
        channels = SuggestionDatabaseChannelsConfig(watch_history_channel_id=20, archive_channel_id=30)
        self.assertNotEqual(channels.watch_history_channel_id, channels.archive_channel_id)

    def test_rejects_a_non_positive_channel_id(self) -> None:
        with self.assertRaises(ValueError):
            SuggestionDatabaseChannelsConfig(suggestion_channel_id=0)


class VotingOverridesConfigTests(unittest.TestCase):
    def test_defaults_all_inherit_guild_configuration(self) -> None:
        overrides = VotingOverridesConfig()

        self.assertIsNone(overrides.candidate_count)
        self.assertIsNone(overrides.duration_hours)
        self.assertIsNone(overrides.visibility)
        self.assertIsNone(overrides.max_vote_changes)
        self.assertIsNone(overrides.tie_behavior)

    def test_accepts_a_full_set_of_overrides(self) -> None:
        overrides = VotingOverridesConfig(
            candidate_count=5,
            duration_hours=48,
            visibility=GuildVoteVisibility.BLIND,
            max_vote_changes=2,
            tie_behavior=TieBehavior.ALL_WINNERS,
        )

        self.assertEqual(overrides.candidate_count, 5)
        self.assertEqual(overrides.duration_hours, 48)
        self.assertEqual(overrides.visibility, GuildVoteVisibility.BLIND)
        self.assertEqual(overrides.max_vote_changes, 2)
        self.assertEqual(overrides.tie_behavior, TieBehavior.ALL_WINNERS)

    def test_coerces_a_raw_string_visibility(self) -> None:
        overrides = VotingOverridesConfig(visibility="visible")
        self.assertEqual(overrides.visibility, GuildVoteVisibility.VISIBLE)

    def test_default_duration_of_twenty_four_hours_is_within_bounds(self) -> None:
        # Confirms the "24 hours" project default fits the field's own
        # accepted range, without hardcoding a default onto the field
        # itself (None still means "inherit Guild Configuration").
        overrides = VotingOverridesConfig(duration_hours=24)
        self.assertEqual(overrides.duration_hours, 24)

    def test_rejects_a_candidate_count_outside_two_to_ten(self) -> None:
        for value in (1, 11):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    VotingOverridesConfig(candidate_count=value)

    def test_rejects_a_duration_hours_outside_one_to_720(self) -> None:
        for value in (0, 721):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    VotingOverridesConfig(duration_hours=value)

    def test_accepts_duration_hours_boundaries(self) -> None:
        self.assertEqual(VotingOverridesConfig(duration_hours=1).duration_hours, 1)
        self.assertEqual(VotingOverridesConfig(duration_hours=720).duration_hours, 720)

    def test_rejects_an_unsupported_visibility_string(self) -> None:
        with self.assertRaises(ValueError):
            VotingOverridesConfig(visibility="sideways")

    def test_max_vote_changes_allows_zero(self) -> None:
        overrides = VotingOverridesConfig(max_vote_changes=0)
        self.assertEqual(overrides.max_vote_changes, 0)

    def test_rejects_a_negative_max_vote_changes(self) -> None:
        with self.assertRaises(ValueError):
            VotingOverridesConfig(max_vote_changes=-1)


class SuggestionRulesConfigTests(unittest.TestCase):
    def test_defaults_match_the_documented_specification(self) -> None:
        rules = SuggestionRulesConfig()

        self.assertTrue(rules.allow_imdb_links)
        self.assertTrue(rules.allow_manual_titles)
        self.assertTrue(rules.require_unique_active_titles)
        self.assertEqual(rules.rejection_threshold, 2)
        self.assertTrue(rules.allow_resuggestion)
        self.assertEqual(rules.candidate_selection, CandidateSelectionMode.ROTATION_POOL)
        self.assertEqual(rules.admission_mode, SuggestionAdmissionMode.NEXT_ROTATION)

    def test_requires_at_least_one_input_method(self) -> None:
        with self.assertRaises(ValueError):
            SuggestionRulesConfig(allow_imdb_links=False, allow_manual_titles=False)

    def test_allows_only_imdb_links(self) -> None:
        rules = SuggestionRulesConfig(allow_imdb_links=True, allow_manual_titles=False)
        self.assertTrue(rules.allow_imdb_links)

    def test_allows_only_manual_titles(self) -> None:
        rules = SuggestionRulesConfig(allow_imdb_links=False, allow_manual_titles=True)
        self.assertTrue(rules.allow_manual_titles)

    def test_rejection_threshold_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            SuggestionRulesConfig(rejection_threshold=0)

    def test_rejection_threshold_rejects_a_negative_value(self) -> None:
        with self.assertRaises(ValueError):
            SuggestionRulesConfig(rejection_threshold=-1)

    def test_accepts_a_custom_rejection_threshold(self) -> None:
        rules = SuggestionRulesConfig(rejection_threshold=5)
        self.assertEqual(rules.rejection_threshold, 5)

    def test_coerces_a_raw_string_candidate_selection(self) -> None:
        rules = SuggestionRulesConfig(candidate_selection="soft_rotation")
        self.assertEqual(rules.candidate_selection, CandidateSelectionMode.SOFT_ROTATION)

    def test_rejects_an_unsupported_candidate_selection(self) -> None:
        with self.assertRaises(ValueError):
            SuggestionRulesConfig(candidate_selection="not_a_real_mode")

    def test_coerces_a_raw_string_admission_mode(self) -> None:
        rules = SuggestionRulesConfig(admission_mode="join_current_rotation")
        self.assertEqual(rules.admission_mode, SuggestionAdmissionMode.JOIN_CURRENT_ROTATION)

    def test_rejects_an_unsupported_admission_mode(self) -> None:
        with self.assertRaises(ValueError):
            SuggestionRulesConfig(admission_mode="not_a_real_mode")


class SuggestionDatabaseWatchHistoryConfigTests(unittest.TestCase):
    def test_defaults_match_the_documented_specification(self) -> None:
        history = SuggestionDatabaseWatchHistoryConfig()

        self.assertTrue(history.enabled)
        self.assertTrue(history.allow_retroactive_entries)
        self.assertTrue(history.allow_repeat_watches)
        self.assertTrue(history.include_watch_date)
        self.assertTrue(history.include_vote_result)

    def test_can_be_disabled(self) -> None:
        history = SuggestionDatabaseWatchHistoryConfig(enabled=False)
        self.assertFalse(history.enabled)


class SuggestionDatabaseArchiveConfigTests(unittest.TestCase):
    def test_defaults_match_the_documented_specification(self) -> None:
        archive = SuggestionDatabaseArchiveConfig()

        self.assertTrue(archive.enabled)
        self.assertTrue(archive.archive_winner_after_watch)
        self.assertTrue(archive.archive_rejected_suggestions)
        self.assertTrue(archive.allow_resuggestion)

    def test_can_be_disabled(self) -> None:
        archive = SuggestionDatabaseArchiveConfig(enabled=False)
        self.assertFalse(archive.enabled)


class SuggestionDatabaseNotificationOverridesConfigTests(unittest.TestCase):
    def test_defaults_inherit_guild_configuration(self) -> None:
        notifications = SuggestionDatabaseNotificationOverridesConfig()

        self.assertIsNone(notifications.low_suggestion_pool_alerts)
        self.assertIsNone(notifications.low_suggestion_pool_threshold)

    def test_accepts_an_explicit_override(self) -> None:
        notifications = SuggestionDatabaseNotificationOverridesConfig(
            low_suggestion_pool_alerts=True, low_suggestion_pool_threshold=5
        )
        self.assertTrue(notifications.low_suggestion_pool_alerts)
        self.assertEqual(notifications.low_suggestion_pool_threshold, 5)

    def test_rejects_a_non_positive_threshold(self) -> None:
        with self.assertRaises(ValueError):
            SuggestionDatabaseNotificationOverridesConfig(low_suggestion_pool_threshold=0)

    def test_low_pool_reminder_defaults(self) -> None:
        # FR-033B Section 7: destination defaults to None (falls back to
        # the database's suggestion channel at send time) and the minimum
        # reminder interval defaults to 24 hours.
        notifications = SuggestionDatabaseNotificationOverridesConfig()

        self.assertIsNone(notifications.low_suggestion_pool_destination_channel_id)
        self.assertEqual(notifications.low_suggestion_pool_minimum_interval_hours, 24)

    def test_low_pool_reminder_accepts_an_explicit_override(self) -> None:
        notifications = SuggestionDatabaseNotificationOverridesConfig(
            low_suggestion_pool_destination_channel_id=555, low_suggestion_pool_minimum_interval_hours=6
        )
        self.assertEqual(notifications.low_suggestion_pool_destination_channel_id, 555)
        self.assertEqual(notifications.low_suggestion_pool_minimum_interval_hours, 6)

    def test_rejects_a_non_positive_destination_channel_id(self) -> None:
        with self.assertRaises(ValueError):
            SuggestionDatabaseNotificationOverridesConfig(low_suggestion_pool_destination_channel_id=0)

    def test_rejects_a_non_positive_minimum_interval_hours(self) -> None:
        with self.assertRaises(ValueError):
            SuggestionDatabaseNotificationOverridesConfig(low_suggestion_pool_minimum_interval_hours=0)


class SuggestionDatabasePermissionsConfigTests(unittest.TestCase):
    def test_defaults_to_no_moderators_and_guild_role_enabled(self) -> None:
        permissions = SuggestionDatabasePermissionsConfig()

        self.assertEqual(permissions.moderator_role_ids, ())
        self.assertTrue(permissions.use_guild_watch_party_role)

    def test_deduplicates_moderator_role_ids_preserving_first_seen_order(self) -> None:
        permissions = SuggestionDatabasePermissionsConfig(moderator_role_ids=(5, 6, 5, 7, 6))
        self.assertEqual(permissions.moderator_role_ids, (5, 6, 7))

    def test_rejects_a_non_positive_role_id(self) -> None:
        with self.assertRaises(ValueError):
            SuggestionDatabasePermissionsConfig(moderator_role_ids=(0,))

    def test_moderator_role_ids_never_grant_guild_wide_authority(self) -> None:
        # This is a documentation-level guarantee (see the class
        # docstring), not something separately enforced by validation --
        # confirming the field simply exists and is scoped as described.
        permissions = SuggestionDatabasePermissionsConfig(
            moderator_role_ids=(5,), use_guild_watch_party_role=False
        )
        self.assertEqual(permissions.moderator_role_ids, (5,))
        self.assertFalse(permissions.use_guild_watch_party_role)


if __name__ == "__main__":
    unittest.main()
