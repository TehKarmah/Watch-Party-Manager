"""FR-033B Section 7: the Low Pool Reminder.

Evaluates whether a database's suggestion pool has fallen to (or below) a
configured remaining-suggestions threshold, and if so, whether enough
time has passed since the last reminder to send another one -- resolving
enabled/threshold from Guild Configuration with a per-database override,
matching this project's existing "database overrides, else guild
default" convention (see SuggestionDatabaseNotificationOverridesConfig).

Deliberately outside SuggestionService/RotationService: composing guild
configuration, database configuration, and rotation progress into a
single yes/no-plus-message decision is its own cross-cutting concern,
matching how NomineeSelectionService already sits between
SuggestionService and VoteService rather than living inside either.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol

from watch_party_manager.domain.guild_configuration import GuildConfiguration
from watch_party_manager.domain.suggestion_database_configuration import SuggestionDatabaseConfiguration
from watch_party_manager.services.rotation_service import RotationProgress, RotationService

DEFAULT_LOW_POOL_REMINDER_ENABLED = True
DEFAULT_LOW_POOL_REMINDER_THRESHOLD = 10
DEFAULT_LOW_POOL_REMINDER_INTERVAL_HOURS = 24


class GuildConfigurationSource(Protocol):
    def get(self, guild_id: int) -> Optional[GuildConfiguration]: ...


class DatabaseConfigurationSource(Protocol):
    def get(self, guild_id: int, database_id: int) -> Optional[SuggestionDatabaseConfiguration]: ...


@dataclass(frozen=True)
class LowPoolReminderDecision:
    """Whether a Low Pool Reminder should be sent right now, and its text/destination."""

    should_send: bool
    message: Optional[str] = None
    destination_channel_id: Optional[int] = None


class LowPoolReminderService:
    """Decides when to send FR-033B's Low Pool Reminder, and builds its text."""

    def __init__(
        self,
        rotation_service: RotationService,
        guild_configuration_repository: GuildConfigurationSource,
        suggestion_database_configuration_repository: DatabaseConfigurationSource,
    ) -> None:
        self._rotation_service = rotation_service
        self._guild_configuration_repository = guild_configuration_repository
        self._suggestion_database_configuration_repository = suggestion_database_configuration_repository

    def evaluate(
        self,
        *,
        guild_id: int,
        database_id: int,
        remaining_count: int,
        default_suggestion_channel_id: Optional[int] = None,
        now: Optional[datetime] = None,
    ) -> LowPoolReminderDecision:
        """Decide whether to send a reminder for one database right now.

        Args:
            guild_id: The guild the database belongs to.
            database_id: The suggestion database to evaluate.
            remaining_count: The database's current active suggestion
                count (callers resolve this via SuggestionService).
            default_suggestion_channel_id: Fallback destination when no
                explicit reminder destination is configured (see
                SuggestionDatabaseChannelsConfig.suggestion_channel_id).
            now: Injectable clock for deterministic tests. Defaults to
                the current UTC time.
        """
        current_time = now if now is not None else datetime.now(timezone.utc)
        enabled, threshold, interval_hours, destination_channel_id = self._resolve_settings(
            guild_id, database_id, default_suggestion_channel_id
        )

        if not enabled:
            return LowPoolReminderDecision(should_send=False)
        if remaining_count > threshold:
            return LowPoolReminderDecision(should_send=False)
        if destination_channel_id is None:
            return LowPoolReminderDecision(should_send=False)

        last_sent = self._rotation_service.last_low_pool_reminder_sent_at(database_id)
        if last_sent is not None and current_time - last_sent < timedelta(hours=interval_hours):
            return LowPoolReminderDecision(should_send=False)

        progress = self._rotation_service.rotation_progress(database_id)
        message = self._build_message(remaining_count, progress)
        return LowPoolReminderDecision(
            should_send=True, message=message, destination_channel_id=destination_channel_id
        )

    def _resolve_settings(
        self, guild_id: int, database_id: int, default_suggestion_channel_id: Optional[int]
    ) -> tuple[bool, int, int, Optional[int]]:
        guild_configuration = self._guild_configuration_repository.get(guild_id)
        if guild_configuration is not None:
            guild_enabled = (
                guild_configuration.feature_flags.low_suggestion_pool_alerts
                and guild_configuration.notifications.administrative.low_suggestion_pool
            )
            guild_threshold = guild_configuration.notifications.administrative.low_suggestion_pool_threshold
        else:
            guild_enabled = DEFAULT_LOW_POOL_REMINDER_ENABLED
            guild_threshold = DEFAULT_LOW_POOL_REMINDER_THRESHOLD

        database_configuration = self._suggestion_database_configuration_repository.get(guild_id, database_id)
        enabled = guild_enabled
        threshold = guild_threshold
        interval_hours = DEFAULT_LOW_POOL_REMINDER_INTERVAL_HOURS
        destination_channel_id = default_suggestion_channel_id

        if database_configuration is not None:
            overrides = database_configuration.notifications
            if overrides.low_suggestion_pool_alerts is not None:
                enabled = overrides.low_suggestion_pool_alerts
            if overrides.low_suggestion_pool_threshold is not None:
                threshold = overrides.low_suggestion_pool_threshold
            interval_hours = overrides.low_suggestion_pool_minimum_interval_hours
            if overrides.low_suggestion_pool_destination_channel_id is not None:
                destination_channel_id = overrides.low_suggestion_pool_destination_channel_id

        return enabled, threshold, interval_hours, destination_channel_id

    @staticmethod
    def _build_message(remaining_count: int, progress: RotationProgress) -> str:
        suggestion_word = "suggestion" if remaining_count == 1 else "suggestions"
        return (
            f"The suggestion pool is getting low: {remaining_count} eligible {suggestion_word} remaining "
            f"({progress.completion_percentage:.0f}% of the current rotation presented). "
            "Add another with `/add` followed by a movie title or IMDb link."
        )
