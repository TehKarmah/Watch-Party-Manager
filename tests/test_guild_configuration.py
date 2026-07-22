import unittest
from datetime import datetime, timedelta, timezone

from watch_party_manager.domain.guild_configuration import (
    GuildChannelsConfig,
    GuildConfiguration,
    GuildSuggestionDatabaseEntry,
    GuildVoteVisibility,
    JoinMode,
    TieBehavior,
    VotingDefaultsConfig,
    WatchPartyRoleConfig,
)


class GuildConfigurationTests(unittest.TestCase):
    def test_defaults_match_specification(self):
        config = GuildConfiguration(guild_id=1, guild_name="Guild")
        self.assertEqual(config.configuration_version, 1)
        self.assertFalse(config.setup_completed)
        self.assertTrue(config.administrator_override)
        self.assertEqual(config.watch_party_role.join_mode, JoinMode.SELF_SERVICE)
        self.assertEqual(config.voting_defaults.candidate_count, 3)
        self.assertEqual(config.voting_defaults.duration_days, 7)
        self.assertEqual(config.voting_defaults.visibility, GuildVoteVisibility.BLIND)
        self.assertEqual(config.voting_defaults.max_vote_changes, 1)
        self.assertEqual(config.voting_defaults.tie_behavior, TieBehavior.ALL_WINNERS)
        self.assertFalse(config.feature_flags.birthday_picks)
        self.assertTrue(config.watch_history.allow_repeat_watches)
        self.assertEqual(config.notifications.vote.reminder_hours_before_close, 24)
        self.assertEqual(config.notifications.watch.reminder_hours_before_watch, 1)
        self.assertEqual(config.notifications.administrative.low_suggestion_pool_threshold, 10)

    def test_trims_names(self):
        config = GuildConfiguration(guild_id=1, guild_name="  Guild  ")
        entry = GuildSuggestionDatabaseEntry(id="  movies ", display_name="  Movies ")
        self.assertEqual(config.guild_name, "Guild")
        self.assertEqual(entry.id, "movies")
        self.assertEqual(entry.display_name, "Movies")

    def test_rejects_invalid_guild_id(self):
        with self.assertRaises(ValueError):
            GuildConfiguration(guild_id=0, guild_name="Guild")

    def test_rejects_blank_guild_name(self):
        with self.assertRaises(ValueError):
            GuildConfiguration(guild_id=1, guild_name=" ")

    def test_rejects_naive_timestamps(self):
        with self.assertRaises(ValueError):
            GuildConfiguration(guild_id=1, guild_name="Guild", created_at=datetime.now())

    def test_rejects_updated_at_before_created_at(self):
        created = datetime.now(timezone.utc)
        with self.assertRaises(ValueError):
            GuildConfiguration(
                guild_id=1,
                guild_name="Guild",
                created_at=created,
                updated_at=created - timedelta(seconds=1),
            )

    def test_rejects_duplicate_database_ids(self):
        with self.assertRaises(ValueError):
            GuildConfiguration(
                guild_id=1,
                guild_name="Guild",
                suggestion_databases=(
                    GuildSuggestionDatabaseEntry("movies", "Movies"),
                    GuildSuggestionDatabaseEntry("movies", "Other"),
                ),
            )

    def test_accepts_join_mode_string(self):
        self.assertEqual(WatchPartyRoleConfig(join_mode="approval").join_mode, JoinMode.APPROVAL)

    def test_rejects_invalid_join_mode(self):
        with self.assertRaises(ValueError):
            WatchPartyRoleConfig(join_mode="invalid")

    # --- FR-030 refinement: default Join Mode is Self-Service ---------------------

    def test_default_join_mode_is_self_service_for_a_new_watch_party_role_config(self):
        self.assertEqual(WatchPartyRoleConfig().join_mode, JoinMode.SELF_SERVICE)

    def test_default_join_mode_is_self_service_for_a_new_guild_configuration(self):
        config = GuildConfiguration(guild_id=1, guild_name="Guild")
        self.assertEqual(config.watch_party_role.join_mode, JoinMode.SELF_SERVICE)

    # --- FR-030 refinement: Admin channel and denial cooldown ---------------------

    def test_admin_channel_id_defaults_to_none(self):
        config = GuildConfiguration(guild_id=1, guild_name="Guild")
        self.assertIsNone(config.channels.admin_channel_id)

    def test_admin_channel_id_rejects_non_positive_values(self):
        with self.assertRaises(ValueError):
            GuildChannelsConfig(admin_channel_id=0)

    def test_denial_cooldown_days_defaults_to_seven(self):
        self.assertEqual(WatchPartyRoleConfig().denial_cooldown_days, 7)

    def test_denial_cooldown_days_rejects_out_of_range_values(self):
        with self.assertRaises(ValueError):
            WatchPartyRoleConfig(denial_cooldown_days=0)
        with self.assertRaises(ValueError):
            WatchPartyRoleConfig(denial_cooldown_days=366)

    def test_voting_validation_boundaries(self):
        VotingDefaultsConfig(candidate_count=2, duration_days=1, max_vote_changes=0)
        VotingDefaultsConfig(candidate_count=10, duration_days=30, max_vote_changes=10)

    def test_rejects_invalid_voting_values(self):
        invalid = (
            {"candidate_count": 1}, {"candidate_count": 11},
            {"duration_days": 0}, {"duration_days": 31},
            {"max_vote_changes": -1}, {"max_vote_changes": 11},
            {"visibility": "secret"}, {"tie_behavior": "runoff"},
        )
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                VotingDefaultsConfig(**kwargs)

    def test_preserves_model_extra_fields(self):
        config = GuildConfiguration(guild_id=1, guild_name="Guild", extra_fields={"future": {"x": 1}})
        self.assertEqual(config.extra_fields["future"]["x"], 1)


if __name__ == "__main__":
    unittest.main()
