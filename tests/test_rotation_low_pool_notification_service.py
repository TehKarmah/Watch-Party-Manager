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

    def _seed_pool(self, total: int, presented: int) -> None:
        """Create `total` suggestions and mark the first `presented` of
        them as presented within a freshly started rotation, leaving
        `total - presented` Eligible and `presented` on Rotation Cooldown
        -- Active stays `total` either way (UI Polish: Rotation &
        Collection Health's finalized threshold is evaluated against
        Active Watch Items, so tests need a large-enough Active pool to
        avoid the small-collection suppression rule while still driving
        Eligible below the threshold).
        """
        items = [self._add(f"Title {index}") for index in range(total)]
        self.rotation_service.get_or_start_rotation(DATABASE_ID)
        if presented:
            self.rotation_service.record_presentation(DATABASE_ID, [item.id for item in items[:presented]])

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
        self._seed_pool(total=10, presented=5)
        # 10 active, default candidate_count 3 -> threshold max(1, 6) = 6;
        # 5 eligible < 6, and 10 active is comfortably above the
        # threshold, so this isn't suppressed as "too small to matter".

        decision = self._evaluate()

        self.assertTrue(decision.should_send)


class ThresholdTests(RotationLowPoolNotificationServiceTestCase):
    """UI Polish (Rotation & Collection Health): the finalized automatic
    default is max(10% of Active Watch Items, two configured voting
    rounds) -- see resolve_default_low_pool_threshold. An explicit
    database/guild threshold override still wins over it when set.
    """

    def test_default_threshold_is_two_configured_voting_rounds_when_it_is_the_larger_term(self) -> None:
        self._save_guild_configuration(voting_defaults=VotingDefaultsConfig(candidate_count=4))
        # 20 active, 10% = 2, rounds = 2 * 4 = 8 -- rounds is larger, so
        # the floor (8) is the effective threshold. 8 eligible sits right
        # at that boundary, not below it.
        self._seed_pool(total=20, presented=12)

        decision = self._evaluate()

        self.assertFalse(decision.should_send)

    def test_fires_once_the_pool_drops_below_the_default_threshold(self) -> None:
        self._save_guild_configuration(voting_defaults=VotingDefaultsConfig(candidate_count=3))
        # 10 active, 10% = 1, rounds = 2 * 3 = 6 -- rounds is the
        # effective threshold. 5 eligible is strictly fewer than 6, and
        # 10 active is well above the threshold, so this fires.
        self._seed_pool(total=10, presented=5)

        decision = self._evaluate()

        self.assertTrue(decision.should_send)

    def test_percentage_term_governs_for_a_large_collection(self) -> None:
        self._save_guild_configuration(voting_defaults=VotingDefaultsConfig(candidate_count=2))
        # 100 active, 10% = 10, rounds = 2 * 2 = 4 -- the percentage term
        # (10) is now the larger, effective threshold, well above the
        # flat rounds-based floor. 8 eligible < 10 fires; the collection
        # is large enough that the rounds floor alone would have missed this.
        self._seed_pool(total=100, presented=92)

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
        # the automatic default, but the explicit override wins.

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
        # 10 active (comfortably above the database's own threshold of
        # 5, so not suppressed), 4 eligible < 5.
        self._seed_pool(total=10, presented=6)

        decision = self._evaluate()

        self.assertTrue(decision.should_send)


class SuppressionTests(RotationLowPoolNotificationServiceTestCase):
    """UI Polish (Rotation & Collection Health): suppress the warning
    entirely for very small collections where the threshold isn't
    meaningful -- when the whole Active pool is already at or below the
    computed threshold, Eligible (never more than Active) would be below
    it on every rotation forever, so warning provides nothing new.
    """

    def test_never_fires_when_active_is_at_or_below_the_threshold(self) -> None:
        self._save_guild_configuration(voting_defaults=VotingDefaultsConfig(candidate_count=3))
        # 4 active total, default threshold max(1, 6) = 6 -- the entire
        # collection is smaller than its own threshold.
        self._seed_pool(total=4, presented=3)

        decision = self._evaluate()

        self.assertFalse(decision.should_send)

    def test_never_fires_for_a_collection_exactly_at_the_threshold(self) -> None:
        self._save_guild_configuration(voting_defaults=VotingDefaultsConfig(candidate_count=3))
        # 6 active total, exactly equal to the default threshold (6) --
        # suppressed ("at or below"), not just "below".
        self._seed_pool(total=6, presented=5)

        decision = self._evaluate()

        self.assertFalse(decision.should_send)

    def test_fires_once_active_grows_past_the_threshold(self) -> None:
        self._save_guild_configuration(voting_defaults=VotingDefaultsConfig(candidate_count=3))
        # One more active item than the suppressed case above (7 vs 6)
        # is enough for the same relative shortfall to become meaningful
        # and fire.
        self._seed_pool(total=7, presented=6)

        decision = self._evaluate()

        self.assertTrue(decision.should_send)

    def test_suppression_applies_even_with_an_explicit_threshold_override(self) -> None:
        # An administrator's explicit threshold of 5 is still subject to
        # suppression if the collection is smaller than that override --
        # a custom threshold doesn't make a tiny collection's warning any
        # more meaningful.
        self._save_guild_configuration(
            notifications=NotificationsConfig(
                administrative=AdministrativeNotificationsConfig(low_suggestion_pool_threshold=5)
            )
        )
        self._seed_pool(total=4, presented=3)

        decision = self._evaluate()

        self.assertFalse(decision.should_send)


class DestinationTests(RotationLowPoolNotificationServiceTestCase):
    def test_defaults_to_the_admin_channel(self) -> None:
        self._save_guild_configuration()
        self._seed_pool(total=10, presented=5)

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
        self._seed_pool(total=10, presented=5)

        decision = self._evaluate()

        self.assertEqual(decision.destination_channel_id, HOME_CHANNEL_ID)

    def test_never_sends_when_the_configured_destination_channel_is_unset(self) -> None:
        self._save_guild_configuration(channels=GuildChannelsConfig(admin_channel_id=None))
        self._seed_pool(total=10, presented=5)

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
        self._seed_pool(total=10, presented=5)
        first = self._evaluate()
        self.assertTrue(first.should_send)
        self.rotation_service.record_low_pool_notification_sent(DATABASE_ID, first.rotation_id)

        second = self._evaluate()

        self.assertFalse(second.should_send)

    def test_resends_after_a_new_rotation_begins(self) -> None:
        self._save_guild_configuration()
        self._seed_pool(total=10, presented=5)
        first = self._evaluate()
        self.rotation_service.record_low_pool_notification_sent(DATABASE_ID, first.rotation_id)

        self.rotation_service.begin_next_rotation(DATABASE_ID)
        # A fresh rotation reassigns every eligible suggestion, so all 10
        # are eligible again with nothing presented yet -- re-present 5
        # to reproduce the same low-pool condition under the new rotation.
        remaining_items = self.suggestion_service.get_suggestions_for_database(DATABASE_ID)
        self.rotation_service.record_presentation(DATABASE_ID, [item.id for item in remaining_items[:5]])
        second = self._evaluate()

        self.assertTrue(second.should_send)
        self.assertNotEqual(first.rotation_id, second.rotation_id)


class MessageContentTests(RotationLowPoolNotificationServiceTestCase):
    def test_message_includes_eligible_count_and_candidate_count(self) -> None:
        self._save_guild_configuration(voting_defaults=VotingDefaultsConfig(candidate_count=3))
        self.suggestion_service.create_database("Movies", guild_id=GUILD_ID, channel_id=555)
        self._seed_pool(total=10, presented=5)

        decision = self._evaluate()

        self.assertTrue(decision.should_send)
        self.assertIn("Eligible remaining: 5", decision.message)
        self.assertIn("Configured candidate count: 3", decision.message)
        self.assertIn("Movies", decision.message)


if __name__ == "__main__":
    unittest.main()
