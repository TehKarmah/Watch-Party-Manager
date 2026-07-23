"""Tests for LowPoolReminderService (FR-033B Section 7)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from watch_party_manager.domain.guild_configuration import GuildConfiguration
from watch_party_manager.domain.suggestion_database_configuration import (
    SuggestionDatabaseChannelsConfig,
    SuggestionDatabaseConfiguration,
    SuggestionDatabaseNotificationOverridesConfig,
)
from watch_party_manager.persistence.rotation_repository import JsonRotationRepository
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.low_pool_reminder_service import LowPoolReminderService
from watch_party_manager.services.rotation_service import RotationService
from watch_party_manager.services.suggestion_service import SuggestionService

GUILD_ID = 100
DATABASE_ID = 1


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class FakeGuildConfigurationRepository:
    def __init__(self, configuration=None) -> None:
        self._configuration = configuration

    def get(self, guild_id: int):
        return self._configuration


class FakeDatabaseConfigurationRepository:
    def __init__(self, configuration=None) -> None:
        self._configuration = configuration

    def get(self, guild_id: int, database_id: int):
        return self._configuration


class LowPoolReminderServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.rotation_service = RotationService(
            self.suggestion_service, repository=JsonRotationRepository(root / "rotations.json")
        )

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _build_service(self, guild_configuration=None, database_configuration=None) -> LowPoolReminderService:
        return LowPoolReminderService(
            self.rotation_service,
            FakeGuildConfigurationRepository(guild_configuration),
            FakeDatabaseConfigurationRepository(database_configuration),
        )


class ThresholdTests(LowPoolReminderServiceTestCase):
    def test_sends_when_remaining_count_is_at_the_default_threshold(self) -> None:
        service = self._build_service()

        decision = service.evaluate(
            guild_id=GUILD_ID, database_id=DATABASE_ID, remaining_count=10, default_suggestion_channel_id=777
        )

        self.assertTrue(decision.should_send)

    def test_does_not_send_when_remaining_count_is_above_the_default_threshold(self) -> None:
        service = self._build_service()

        decision = service.evaluate(
            guild_id=GUILD_ID, database_id=DATABASE_ID, remaining_count=11, default_suggestion_channel_id=777
        )

        self.assertFalse(decision.should_send)

    def test_a_database_override_threshold_is_respected(self) -> None:
        database_configuration = SuggestionDatabaseConfiguration(
            guild_id=GUILD_ID,
            database_id=DATABASE_ID,
            display_name="Movies",
            notifications=SuggestionDatabaseNotificationOverridesConfig(low_suggestion_pool_threshold=3),
        )
        service = self._build_service(database_configuration=database_configuration)

        just_above = service.evaluate(
            guild_id=GUILD_ID, database_id=DATABASE_ID, remaining_count=4, default_suggestion_channel_id=777
        )
        at_threshold = service.evaluate(
            guild_id=GUILD_ID, database_id=DATABASE_ID, remaining_count=3, default_suggestion_channel_id=777
        )

        self.assertFalse(just_above.should_send)
        self.assertTrue(at_threshold.should_send)

    def test_message_mentions_the_remaining_count_and_add_command(self) -> None:
        service = self._build_service()

        decision = service.evaluate(
            guild_id=GUILD_ID, database_id=DATABASE_ID, remaining_count=2, default_suggestion_channel_id=777
        )

        self.assertIn("2", decision.message)
        self.assertIn("/add", decision.message)


class DisabledConfigurationTests(LowPoolReminderServiceTestCase):
    def test_a_database_override_can_disable_the_reminder(self) -> None:
        database_configuration = SuggestionDatabaseConfiguration(
            guild_id=GUILD_ID,
            database_id=DATABASE_ID,
            display_name="Movies",
            notifications=SuggestionDatabaseNotificationOverridesConfig(low_suggestion_pool_alerts=False),
        )
        service = self._build_service(database_configuration=database_configuration)

        decision = service.evaluate(
            guild_id=GUILD_ID, database_id=DATABASE_ID, remaining_count=1, default_suggestion_channel_id=777
        )

        self.assertFalse(decision.should_send)

    def test_the_guild_feature_flag_can_disable_the_reminder(self) -> None:
        guild_configuration = GuildConfiguration(guild_id=GUILD_ID, guild_name="Test Guild")
        guild_configuration.feature_flags.low_suggestion_pool_alerts = False
        service = self._build_service(guild_configuration=guild_configuration)

        decision = service.evaluate(
            guild_id=GUILD_ID, database_id=DATABASE_ID, remaining_count=1, default_suggestion_channel_id=777
        )

        self.assertFalse(decision.should_send)


class DestinationHandlingTests(LowPoolReminderServiceTestCase):
    def test_falls_back_to_the_default_suggestion_channel(self) -> None:
        service = self._build_service()

        decision = service.evaluate(
            guild_id=GUILD_ID, database_id=DATABASE_ID, remaining_count=1, default_suggestion_channel_id=777
        )

        self.assertEqual(decision.destination_channel_id, 777)

    def test_a_configured_destination_channel_overrides_the_default(self) -> None:
        database_configuration = SuggestionDatabaseConfiguration(
            guild_id=GUILD_ID,
            database_id=DATABASE_ID,
            display_name="Movies",
            notifications=SuggestionDatabaseNotificationOverridesConfig(
                low_suggestion_pool_destination_channel_id=999
            ),
        )
        service = self._build_service(database_configuration=database_configuration)

        decision = service.evaluate(
            guild_id=GUILD_ID, database_id=DATABASE_ID, remaining_count=1, default_suggestion_channel_id=777
        )

        self.assertEqual(decision.destination_channel_id, 999)

    def test_no_destination_available_means_no_send(self) -> None:
        service = self._build_service()

        decision = service.evaluate(
            guild_id=GUILD_ID, database_id=DATABASE_ID, remaining_count=1, default_suggestion_channel_id=None
        )

        self.assertFalse(decision.should_send)


class ReminderIntervalTests(LowPoolReminderServiceTestCase):
    def test_a_second_reminder_within_the_interval_is_suppressed(self) -> None:
        service = self._build_service()
        now = utc_now()
        self.rotation_service.record_low_pool_reminder_sent(DATABASE_ID, now)

        decision = service.evaluate(
            guild_id=GUILD_ID,
            database_id=DATABASE_ID,
            remaining_count=1,
            default_suggestion_channel_id=777,
            now=now + timedelta(hours=1),
        )

        self.assertFalse(decision.should_send)

    def test_a_reminder_after_the_interval_elapses_is_sent(self) -> None:
        service = self._build_service()
        now = utc_now()
        self.rotation_service.record_low_pool_reminder_sent(DATABASE_ID, now)

        decision = service.evaluate(
            guild_id=GUILD_ID,
            database_id=DATABASE_ID,
            remaining_count=1,
            default_suggestion_channel_id=777,
            now=now + timedelta(hours=25),
        )

        self.assertTrue(decision.should_send)

    def test_a_shorter_configured_interval_is_respected(self) -> None:
        database_configuration = SuggestionDatabaseConfiguration(
            guild_id=GUILD_ID,
            database_id=DATABASE_ID,
            display_name="Movies",
            notifications=SuggestionDatabaseNotificationOverridesConfig(
                low_suggestion_pool_minimum_interval_hours=1
            ),
        )
        service = self._build_service(database_configuration=database_configuration)
        now = utc_now()
        self.rotation_service.record_low_pool_reminder_sent(DATABASE_ID, now)

        decision = service.evaluate(
            guild_id=GUILD_ID,
            database_id=DATABASE_ID,
            remaining_count=1,
            default_suggestion_channel_id=777,
            now=now + timedelta(hours=2),
        )

        self.assertTrue(decision.should_send)


if __name__ == "__main__":
    unittest.main()
