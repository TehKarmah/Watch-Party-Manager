"""Tests for RotationLowPoolNotificationService (Rotation & Collection
Health), which replaced the older, interval-based Low Pool Reminder.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.guild_configuration import (
    FeatureFlagsConfig,
    GuildChannelsConfig,
    GuildConfiguration,
    NotificationsConfig,
    RotationLowPoolNotificationDestination,
    VotingDefaultsConfig,
)
from watch_party_manager.domain.guild_configuration import AdministrativeNotificationsConfig
from watch_party_manager.domain.suggestion_database_configuration import (
    CandidateSelectionMode,
    SuggestionDatabaseChannelsConfig,
    SuggestionDatabaseConfiguration,
    SuggestionDatabaseNotificationOverridesConfig,
)
from watch_party_manager.persistence.rotation_repository import JsonRotationRepository
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.collection_eligibility_service import CollectionEligibilityService
from watch_party_manager.services.rotation_low_pool_notification_service import (
    RotationLowPoolNotificationService,
)
from watch_party_manager.services.rotation_service import RotationService
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


class RotationLowPoolNotificationServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.rotation_service = RotationService(
            self.suggestion_service, repository=JsonRotationRepository(root / "rotations.json")
        )
        self.eligibility_service = CollectionEligibilityService(self.suggestion_service, self.rotation_service)
        self.guild_configuration_repository = FakeGuildConfigurationRepository()
        self.database_configuration_repository = FakeDatabaseConfigurationRepository()
        self.service = RotationLowPoolNotificationService(
            self.eligibility_service,
            self.rotation_service,
            self.guild_configuration_repository,
            self.database_configuration_repository,
            self.suggestion_service,
        )

    def _add(self, title: str):
        result = self.suggestion_service.suggest(title, database_id=DATABASE_ID, guild_id=GUILD_ID)
        self.assertTrue(result.success)
        return result.watch_item

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

    def _evaluate(self, mode=CandidateSelectionMode.ROTATION_POOL):
        return self.service.evaluate(guild_id=GUILD_ID, database_id=DATABASE_ID, candidate_selection_mode=mode)


class EnabledDisabledTests(RotationLowPoolNotificationServiceTestCase):
    def test_no_guild_configuration_uses_the_documented_default_and_never_sends(self) -> None:
        # DEFAULT_LOW_POOL_NOTIFICATION_ENABLED is True, but with no
        # guild configuration there's no Admin Channel to resolve either
        # -- confirms this fails closed (no destination), not open.
        self._add("Alien")
        self.rotation_service.get_or_start_rotation(DATABASE_ID)

        decision = self._evaluate()

        self.assertFalse(decision.should_send)

    def test_disabled_at_the_guild_level_never_sends(self) -> None:
        self._save_guild_configuration(
            notifications=NotificationsConfig(administrative=AdministrativeNotificationsConfig(low_suggestion_pool=False))
        )
        self._add("Alien")
        self.rotation_service.get_or_start_rotation(DATABASE_ID)

        decision = self._evaluate()

        self.assertFalse(decision.should_send)

    def test_disabled_via_the_legacy_feature_flag_also_suppresses_it(self) -> None:
        # Backward compatibility: both flags are AND'd so a guild that
        # only ever disabled the old feature_flags.low_suggestion_pool_
        # alerts keeps behaving as disabled after this migration.
        self._save_guild_configuration(feature_flags=FeatureFlagsConfig(low_suggestion_pool_alerts=False))
        self._add("Alien")
        self.rotation_service.get_or_start_rotation(DATABASE_ID)

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
        self.rotation_service.get_or_start_rotation(DATABASE_ID)

        decision = self._evaluate()

        self.assertFalse(decision.should_send)

    def test_database_override_can_enable_even_when_the_guild_disables(self) -> None:
        self._save_guild_configuration(
            notifications=NotificationsConfig(administrative=AdministrativeNotificationsConfig(low_suggestion_pool=False))
        )
        self.database_configuration_repository.save(
            SuggestionDatabaseConfiguration(
                guild_id=GUILD_ID,
                database_id=DATABASE_ID,
                display_name="Movies",
                notifications=SuggestionDatabaseNotificationOverridesConfig(low_suggestion_pool_alerts=True),
            )
        )
        self._add("Alien")
        self.rotation_service.get_or_start_rotation(DATABASE_ID)

        decision = self._evaluate()

        self.assertTrue(decision.should_send)


class ThresholdTests(RotationLowPoolNotificationServiceTestCase):
    def test_default_threshold_is_two_configured_voting_rounds(self) -> None:
        self._save_guild_configuration(voting_defaults=VotingDefaultsConfig(candidate_count=4))
        for title in ("Alien", "The Matrix", "Inception", "Arrival", "Amelie", "Up", "Coco", "Alien 2"):
            self._add(title)
        self.rotation_service.get_or_start_rotation(DATABASE_ID)
        # 8 eligible, threshold = 2 * 4 = 8 -- right at the boundary, not below it.

        decision = self._evaluate()

        self.assertFalse(decision.should_send)

    def test_fires_once_the_pool_drops_below_the_default_threshold(self) -> None:
        self._save_guild_configuration(voting_defaults=VotingDefaultsConfig(candidate_count=3))
        for title in ("Alien", "The Matrix", "Inception", "Arrival", "Amelie"):
            self._add(title)
        self.rotation_service.get_or_start_rotation(DATABASE_ID)
        # 5 eligible, threshold = 2 * 3 = 6 -- strictly fewer than the
        # threshold ("fewer eligible suggestions than two normal voting
        # rounds"), so this fires.

        decision = self._evaluate()

        self.assertTrue(decision.should_send)

    def test_explicit_guild_threshold_overrides_the_automatic_default(self) -> None:
        self._save_guild_configuration(
            notifications=NotificationsConfig(
                administrative=AdministrativeNotificationsConfig(low_suggestion_pool_threshold=1)
            )
        )
        self._add("Alien")
        self._add("The Matrix")
        self.rotation_service.get_or_start_rotation(DATABASE_ID)
        # 2 eligible > explicit threshold of 1 -- would have fired under
        # the automatic default (2 * 3 = 6), but the explicit override wins.

        decision = self._evaluate()

        self.assertFalse(decision.should_send)

    def test_database_threshold_override_wins_over_the_guild_threshold(self) -> None:
        self._save_guild_configuration(
            notifications=NotificationsConfig(
                administrative=AdministrativeNotificationsConfig(low_suggestion_pool_threshold=1)
            )
        )
        self.database_configuration_repository.save(
            SuggestionDatabaseConfiguration(
                guild_id=GUILD_ID,
                database_id=DATABASE_ID,
                display_name="Movies",
                notifications=SuggestionDatabaseNotificationOverridesConfig(low_suggestion_pool_threshold=5),
            )
        )
        self._add("Alien")
        self._add("The Matrix")
        self.rotation_service.get_or_start_rotation(DATABASE_ID)
        # 2 eligible <= the database's own threshold of 5.

        decision = self._evaluate()

        self.assertTrue(decision.should_send)


class DestinationTests(RotationLowPoolNotificationServiceTestCase):
    def test_defaults_to_the_admin_channel(self) -> None:
        self._save_guild_configuration()
        self._add("Alien")
        self.rotation_service.get_or_start_rotation(DATABASE_ID)

        decision = self._evaluate()

        self.assertEqual(decision.destination_channel_id, ADMIN_CHANNEL_ID)

    def test_can_be_configured_to_the_home_channel(self) -> None:
        self._save_guild_configuration(
            notifications=NotificationsConfig(
                administrative=AdministrativeNotificationsConfig(
                    low_suggestion_pool_destination=RotationLowPoolNotificationDestination.HOME_CHANNEL
                )
            )
        )
        self._add("Alien")
        self.rotation_service.get_or_start_rotation(DATABASE_ID)

        decision = self._evaluate()

        self.assertEqual(decision.destination_channel_id, HOME_CHANNEL_ID)

    def test_never_sends_when_the_configured_destination_channel_is_unset(self) -> None:
        self._save_guild_configuration(channels=GuildChannelsConfig(admin_channel_id=None))
        self._add("Alien")
        self.rotation_service.get_or_start_rotation(DATABASE_ID)

        decision = self._evaluate()

        self.assertFalse(decision.should_send)


class BootstrapAndDedupTests(RotationLowPoolNotificationServiceTestCase):
    def test_never_bootstraps_a_rotation(self) -> None:
        self._save_guild_configuration()
        self._add("Alien")

        decision = self._evaluate()

        self.assertFalse(decision.should_send)
        self.assertIsNone(self.rotation_service.get_open_rotation(DATABASE_ID))

    def test_infinite_pool_never_fires_since_it_never_creates_a_rotation(self) -> None:
        self._save_guild_configuration()
        self._add("Alien")

        decision = self._evaluate(mode=CandidateSelectionMode.INFINITE_POOL)

        self.assertFalse(decision.should_send)
        self.assertIsNone(self.rotation_service.get_open_rotation(DATABASE_ID))

    def test_does_not_resend_for_the_same_rotation(self) -> None:
        self._save_guild_configuration()
        self._add("Alien")
        self.rotation_service.get_or_start_rotation(DATABASE_ID)
        first = self._evaluate()
        self.assertTrue(first.should_send)
        self.rotation_service.record_low_pool_notification_sent(DATABASE_ID, first.rotation_id)

        second = self._evaluate()

        self.assertFalse(second.should_send)

    def test_resends_after_a_new_rotation_begins(self) -> None:
        self._save_guild_configuration()
        self._add("Alien")
        self.rotation_service.get_or_start_rotation(DATABASE_ID)
        first = self._evaluate()
        self.rotation_service.record_low_pool_notification_sent(DATABASE_ID, first.rotation_id)

        self.rotation_service.begin_next_rotation(DATABASE_ID)
        second = self._evaluate()

        self.assertTrue(second.should_send)
        self.assertNotEqual(first.rotation_id, second.rotation_id)


class MessageContentTests(RotationLowPoolNotificationServiceTestCase):
    def test_message_includes_eligible_count_and_candidate_count(self) -> None:
        self._save_guild_configuration(voting_defaults=VotingDefaultsConfig(candidate_count=3))
        self.suggestion_service.create_database("Movies", guild_id=GUILD_ID, channel_id=555)
        self._add("Alien")
        self.rotation_service.get_or_start_rotation(DATABASE_ID)

        decision = self._evaluate()

        self.assertTrue(decision.should_send)
        self.assertIn("Eligible remaining: 1", decision.message)
        self.assertIn("Configured candidate count: 3", decision.message)
        self.assertIn("Movies", decision.message)


if __name__ == "__main__":
    unittest.main()
