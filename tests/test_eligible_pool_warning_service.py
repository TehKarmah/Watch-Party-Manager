"""Tests for EligiblePoolWarningService (Rotation-removal Phase 2), which
replaced the older Rotation Low-Pool notification.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.guild_configuration import (
    AdministrativeNotificationsConfig,
    EligiblePoolWarningDestination,
    FeatureFlagsConfig,
    GuildChannelsConfig,
    GuildConfiguration,
    NotificationsConfig,
    VotingDefaultsConfig,
)
from watch_party_manager.domain.suggestion_database_configuration import (
    SuggestionDatabaseConfiguration,
    SuggestionDatabaseNotificationOverridesConfig,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.collection_eligibility_service import CollectionEligibilityService
from watch_party_manager.services.eligible_pool_warning_service import (
    DEFAULT_ELIGIBLE_POOL_WARNING_MULTIPLIER,
    EligiblePoolWarningService,
    resolve_default_eligible_pool_warning_threshold,
)
from watch_party_manager.services.suggestion_service import SuggestionService

GUILD_ID = 100
DATABASE_ID = 1
ADMIN_CHANNEL_ID = 200
HOME_CHANNEL_ID = 201


class FakeGuildConfigurationRepository:
    def __init__(self) -> None:
        self._configurations: dict[int, GuildConfiguration] = {}

    def save(self, configuration: GuildConfiguration) -> None:
        self._configurations[configuration.guild_id] = configuration

    def get(self, guild_id: int):
        return self._configurations.get(guild_id)


class FakeDatabaseConfigurationRepository:
    def __init__(self) -> None:
        self._configurations: dict[tuple[int, int], SuggestionDatabaseConfiguration] = {}

    def save(self, configuration: SuggestionDatabaseConfiguration) -> None:
        self._configurations[(configuration.guild_id, configuration.database_id)] = configuration

    def get(self, guild_id: int, database_id: int):
        return self._configurations.get((guild_id, database_id))


class FakeWarningStateRepository:
    """In-memory stand-in for JsonEligiblePoolWarningStateRepository --
    exercises the exact same load()/save(set) contract, without touching
    disk.
    """

    def __init__(self) -> None:
        self.saved: set = set()

    def load(self) -> set:
        return set(self.saved)

    def save(self, armed_database_ids: set) -> None:
        self.saved = set(armed_database_ids)


class EligiblePoolWarningServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.eligibility_service = CollectionEligibilityService(self.suggestion_service, None)
        self.guild_configuration_repository = FakeGuildConfigurationRepository()
        self.database_configuration_repository = FakeDatabaseConfigurationRepository()
        self.warning_state_repository = FakeWarningStateRepository()
        self.service = self._new_service()

    def _new_service(self) -> EligiblePoolWarningService:
        return EligiblePoolWarningService(
            self.eligibility_service,
            self.warning_state_repository,
            self.guild_configuration_repository,
            self.database_configuration_repository,
            self.suggestion_service,
        )

    def _add(self, title: str):
        result = self.suggestion_service.suggest(title, database_id=DATABASE_ID, guild_id=GUILD_ID)
        self.assertTrue(result.success)
        return result.watch_item

    _next_title_index = 0

    def _add_many(self, count: int) -> None:
        for _ in range(count):
            self._add(f"Title {self._next_title_index}")
            self._next_title_index += 1

    def _save_guild_configuration(self, **overrides) -> GuildConfiguration:
        fields = {
            "guild_id": GUILD_ID,
            "guild_name": "Test Guild",
            "channels": GuildChannelsConfig(admin_channel_id=ADMIN_CHANNEL_ID, home_channel_id=HOME_CHANNEL_ID),
        }
        fields.update(overrides)
        configuration = GuildConfiguration(**fields)
        self.guild_configuration_repository.save(configuration)
        return configuration

    def _evaluate(self):
        return self.service.evaluate(guild_id=GUILD_ID, database_id=DATABASE_ID)


class EnabledDisabledTests(EligiblePoolWarningServiceTestCase):
    def test_no_guild_configuration_uses_the_documented_default_and_never_sends(self) -> None:
        # Default enabled is True, but with no guild configuration there's
        # no Admin Channel to resolve either -- confirms this fails
        # closed (no destination), not open.
        self._add("Alien")

        decision = self._evaluate()

        self.assertFalse(decision.should_send)

    def test_disabled_at_the_guild_level_never_sends(self) -> None:
        self._save_guild_configuration(
            notifications=NotificationsConfig(administrative=AdministrativeNotificationsConfig(low_suggestion_pool=False))
        )
        self._add("Alien")

        decision = self._evaluate()

        self.assertFalse(decision.should_send)

    def test_disabled_via_the_legacy_feature_flag_also_suppresses_it(self) -> None:
        # Backward compatibility: both flags are AND'd so a guild that
        # only ever disabled the old feature_flags.low_suggestion_pool_
        # alerts keeps behaving as disabled after this migration.
        self._save_guild_configuration(feature_flags=FeatureFlagsConfig(low_suggestion_pool_alerts=False))
        self._add("Alien")

        decision = self._evaluate()

        self.assertFalse(decision.should_send)

    def test_database_override_can_disable_even_when_the_guild_enables(self) -> None:
        self._save_guild_configuration()
        self.database_configuration_repository.save(
            SuggestionDatabaseConfiguration(
                guild_id=GUILD_ID,
                database_id=DATABASE_ID,
                display_name="Movies",
                notifications=SuggestionDatabaseNotificationOverridesConfig(low_suggestion_pool_alerts=False),
            )
        )
        self._add("Alien")

        decision = self._evaluate()

        self.assertFalse(decision.should_send)

    def test_database_override_can_enable_even_when_the_guild_disables(self) -> None:
        self._save_guild_configuration(
            notifications=NotificationsConfig(administrative=AdministrativeNotificationsConfig(low_suggestion_pool=False)),
            voting_defaults=VotingDefaultsConfig(candidate_count=3),
        )
        self.database_configuration_repository.save(
            SuggestionDatabaseConfiguration(
                guild_id=GUILD_ID,
                database_id=DATABASE_ID,
                display_name="Movies",
                notifications=SuggestionDatabaseNotificationOverridesConfig(low_suggestion_pool_alerts=True),
            )
        )
        self._add_many(10)  # threshold = 3 * 5 = 15; 10 <= 15 fires

        decision = self._evaluate()

        self.assertTrue(decision.should_send)


class ThresholdTests(EligiblePoolWarningServiceTestCase):
    """Eligible Pool Warning threshold: eligible_items <= candidate_count
    * DEFAULT_ELIGIBLE_POOL_WARNING_MULTIPLIER (default multiplier 5),
    matching the task's own example (candidate_count=3 -> threshold=15).
    """

    def test_default_multiplier_is_five(self) -> None:
        self.assertEqual(5, DEFAULT_ELIGIBLE_POOL_WARNING_MULTIPLIER)

    def test_resolve_default_threshold_matches_the_documented_example(self) -> None:
        self.assertEqual(15, resolve_default_eligible_pool_warning_threshold(candidate_count=3))

    def test_never_fires_above_the_threshold(self) -> None:
        self._save_guild_configuration(voting_defaults=VotingDefaultsConfig(candidate_count=3))
        self._add_many(16)  # threshold 15; 16 > 15

        decision = self._evaluate()

        self.assertFalse(decision.should_send)

    def test_fires_exactly_at_the_threshold(self) -> None:
        # The spec's own trigger condition is inclusive: eligible_items
        # <= threshold.
        self._save_guild_configuration(voting_defaults=VotingDefaultsConfig(candidate_count=3))
        self._add_many(15)

        decision = self._evaluate()

        self.assertTrue(decision.should_send)

    def test_fires_below_the_threshold(self) -> None:
        self._save_guild_configuration(voting_defaults=VotingDefaultsConfig(candidate_count=3))
        self._add_many(14)

        decision = self._evaluate()

        self.assertTrue(decision.should_send)

    def test_explicit_guild_threshold_overrides_the_automatic_default(self) -> None:
        self._save_guild_configuration(
            notifications=NotificationsConfig(administrative=AdministrativeNotificationsConfig(low_suggestion_pool_threshold=1))
        )
        self._add("Alien")
        self._add("The Matrix")
        # 2 eligible > explicit threshold of 1 -- would have fired under
        # the automatic default (candidate_count 3 -> threshold 15), but
        # the explicit override wins.

        decision = self._evaluate()

        self.assertFalse(decision.should_send)

    def test_database_threshold_override_wins_over_the_guild_threshold(self) -> None:
        self._save_guild_configuration(
            notifications=NotificationsConfig(administrative=AdministrativeNotificationsConfig(low_suggestion_pool_threshold=1))
        )
        self.database_configuration_repository.save(
            SuggestionDatabaseConfiguration(
                guild_id=GUILD_ID,
                database_id=DATABASE_ID,
                display_name="Movies",
                notifications=SuggestionDatabaseNotificationOverridesConfig(low_suggestion_pool_threshold=5),
            )
        )
        self._add_many(4)  # <= database threshold of 5

        decision = self._evaluate()

        self.assertTrue(decision.should_send)

    def test_threshold_changes_with_the_configured_candidate_count(self) -> None:
        self._save_guild_configuration(voting_defaults=VotingDefaultsConfig(candidate_count=2))
        self._add_many(11)  # threshold = 2 * 5 = 10; 11 > 10, not low

        first = self._evaluate()
        self.assertFalse(first.should_send)

        # Raising the configured candidate count raises the threshold
        # too, without anything about the pool itself changing.
        self._save_guild_configuration(voting_defaults=VotingDefaultsConfig(candidate_count=3))
        # threshold now 3 * 5 = 15; 11 <= 15

        second = self._evaluate()
        self.assertTrue(second.should_send)


class DestinationTests(EligiblePoolWarningServiceTestCase):
    def test_defaults_to_the_admin_channel(self) -> None:
        self._save_guild_configuration(voting_defaults=VotingDefaultsConfig(candidate_count=3))
        self._add_many(10)

        decision = self._evaluate()

        self.assertEqual(decision.destination_channel_id, ADMIN_CHANNEL_ID)

    def test_can_be_configured_to_the_home_channel(self) -> None:
        self._save_guild_configuration(
            voting_defaults=VotingDefaultsConfig(candidate_count=3),
            notifications=NotificationsConfig(
                administrative=AdministrativeNotificationsConfig(
                    low_suggestion_pool_destination=EligiblePoolWarningDestination.HOME_CHANNEL
                )
            ),
        )
        self._add_many(10)

        decision = self._evaluate()

        self.assertEqual(decision.destination_channel_id, HOME_CHANNEL_ID)

    def test_never_sends_when_the_configured_destination_channel_is_unset(self) -> None:
        self._save_guild_configuration(
            voting_defaults=VotingDefaultsConfig(candidate_count=3), channels=GuildChannelsConfig(admin_channel_id=None)
        )
        self._add_many(10)

        decision = self._evaluate()

        self.assertFalse(decision.should_send)


class ThresholdCrossingRearmTests(EligiblePoolWarningServiceTestCase):
    """Rotation-removal Phase 2's own dedup behavior: below threshold
    notifies once, staying below suppresses duplicates, rising above
    re-arms, and dropping below again notifies again -- none of it keyed
    on a Rotation ID.
    """

    def test_fires_the_first_time_it_drops_below_threshold(self) -> None:
        self._save_guild_configuration(voting_defaults=VotingDefaultsConfig(candidate_count=3))
        self._add_many(10)

        decision = self._evaluate()

        self.assertTrue(decision.should_send)

    def test_does_not_resend_while_still_below_threshold(self) -> None:
        self._save_guild_configuration(voting_defaults=VotingDefaultsConfig(candidate_count=3))
        self._add_many(10)
        first = self._evaluate()
        self.assertTrue(first.should_send)

        second = self._evaluate()

        self.assertFalse(second.should_send)

    def test_re_arms_once_the_pool_rises_back_above_threshold(self) -> None:
        self._save_guild_configuration(voting_defaults=VotingDefaultsConfig(candidate_count=3))
        self._add_many(10)
        armed = self._evaluate()
        self.assertTrue(armed.should_send)

        self._add_many(10)  # now 20 eligible, well above threshold 15
        disarmed = self._evaluate()

        self.assertFalse(disarmed.should_send)

    def test_fires_again_after_re_arming_and_dropping_below_again(self) -> None:
        self._save_guild_configuration(voting_defaults=VotingDefaultsConfig(candidate_count=3))
        self._add_many(10)
        self._evaluate()  # fires, arms

        self._add_many(10)  # 20 total, re-arms (disarms the warning)
        self._evaluate()

        # Simulate the pool shrinking back down below threshold (e.g.
        # suggestions retired/archived) by moving some to Vote Winner so
        # they leave the eligible pool.
        items = self.suggestion_service.get_suggestions_for_database(DATABASE_ID)
        for item in items[:10]:
            self.suggestion_service.record_vote_win(item.id, __import__("datetime").date.today())
        # 10 eligible remain <= threshold 15

        third = self._evaluate()

        self.assertTrue(third.should_send)

    def test_never_fires_twice_in_a_row_without_a_no_op_disabled_pass(self) -> None:
        # Regression: evaluate() being called with the feature disabled
        # in between must not itself arm or disarm anything.
        self._save_guild_configuration(voting_defaults=VotingDefaultsConfig(candidate_count=3))
        self._add_many(10)
        first = self._evaluate()
        self.assertTrue(first.should_send)

        self._save_guild_configuration(
            voting_defaults=VotingDefaultsConfig(candidate_count=3),
            notifications=NotificationsConfig(administrative=AdministrativeNotificationsConfig(low_suggestion_pool=False)),
        )
        disabled_pass = self._evaluate()
        self.assertFalse(disabled_pass.should_send)

        self._save_guild_configuration(voting_defaults=VotingDefaultsConfig(candidate_count=3))
        still_armed = self._evaluate()
        self.assertFalse(still_armed.should_send)

    def test_dedup_state_survives_a_simulated_restart(self) -> None:
        self._save_guild_configuration(voting_defaults=VotingDefaultsConfig(candidate_count=3))
        self._add_many(10)
        self._evaluate()

        second_eligibility_service = CollectionEligibilityService(self.suggestion_service, None)
        second_service = EligiblePoolWarningService(
            second_eligibility_service,
            self.warning_state_repository,
            self.guild_configuration_repository,
            self.database_configuration_repository,
            self.suggestion_service,
        )

        decision = second_service.evaluate(guild_id=GUILD_ID, database_id=DATABASE_ID)

        self.assertFalse(decision.should_send)

    def test_does_not_depend_on_any_rotation_state(self) -> None:
        # No RotationService is even constructed anywhere in this test
        # file -- CollectionEligibilityService here is wired with
        # vote_service=None, and EligiblePoolWarningService never
        # references rotation ids at all.
        self._save_guild_configuration(voting_defaults=VotingDefaultsConfig(candidate_count=3))
        self._add_many(10)

        decision = self._evaluate()

        self.assertTrue(decision.should_send)


class MessageContentTests(EligiblePoolWarningServiceTestCase):
    def test_message_includes_eligible_items_remaining_and_threshold(self) -> None:
        self._save_guild_configuration(voting_defaults=VotingDefaultsConfig(candidate_count=3))
        self.suggestion_service.create_database("Movies", guild_id=GUILD_ID, channel_id=555)
        self._add_many(14)

        decision = self._evaluate()

        self.assertTrue(decision.should_send)
        self.assertIn("Eligible Items Remaining: 14", decision.message)
        self.assertIn("Warning Threshold: 15", decision.message)
        self.assertIn("Movies", decision.message)

    def test_message_never_mentions_rotation(self) -> None:
        self._save_guild_configuration(voting_defaults=VotingDefaultsConfig(candidate_count=3))
        self._add_many(10)

        decision = self._evaluate()

        self.assertNotIn("rotation", decision.message.lower())
        self.assertNotIn("round", decision.message.lower())


if __name__ == "__main__":
    unittest.main()
