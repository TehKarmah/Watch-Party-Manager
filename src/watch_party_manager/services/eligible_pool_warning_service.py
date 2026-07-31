"""Rotation-removal Phase 2: the Eligible Pool Warning.

Replaces the older Rotation Low-Pool notification. Two behavioral changes
from that predecessor:

  - The threshold is now a flat multiple of the guild's configured
    candidate count (eligible_items <= candidate_count * multiplier,
    default multiplier 5) rather than the old
    max(10% of Active Watch Items, two configured voting rounds) formula.
    Simpler, and no longer needs "Active Watch Items" (a concept that
    itself no longer distinguishes Rotation Cooldown) to compute.
  - Deduplication is threshold-crossing-based, not Rotation-ID-based:
    once the eligible pool drops to or below the threshold, this fires
    once and "arms" -- it stays silent on every subsequent evaluation
    while still at or below threshold, automatically re-arms the moment
    the pool rises back above threshold, and fires again the next time it
    drops below. See persistence/eligible_pool_warning_state_repository.py,
    which persists only that one per-database armed/disarmed flag --
    deliberately independent of RotationService and rotation ids, so nothing
    here needs to change when RotationService is eventually removed.

Kept outside CollectionEligibilityService, mirroring the old service's own
reasoning: composing guild configuration, database configuration, and
eligibility into a single yes/no-plus-message decision is its own
cross-cutting concern.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, Set

from watch_party_manager.domain.guild_configuration import (
    EligiblePoolWarningDestination,
    GuildConfiguration,
)
from watch_party_manager.domain.suggestion_database_configuration import SuggestionDatabaseConfiguration
from watch_party_manager.services.collection_eligibility_service import CollectionEligibilityService

DEFAULT_ELIGIBLE_POOL_WARNING_ENABLED = True
# The finalized automatic default: a flat multiple of the guild's
# configured candidate count (e.g. candidate_count=3 -> threshold=15).
DEFAULT_ELIGIBLE_POOL_WARNING_MULTIPLIER = 5


def resolve_default_eligible_pool_warning_threshold(candidate_count: int) -> int:
    """candidate_count * DEFAULT_ELIGIBLE_POOL_WARNING_MULTIPLIER. Only
    ever used when no explicit database/guild threshold override is saved
    (see resolve_eligible_pool_warning_threshold).
    """
    return candidate_count * DEFAULT_ELIGIBLE_POOL_WARNING_MULTIPLIER


class GuildConfigurationSource(Protocol):
    def get(self, guild_id: int) -> Optional[GuildConfiguration]: ...


class DatabaseConfigurationSource(Protocol):
    def get(self, guild_id: int, database_id: int) -> Optional[SuggestionDatabaseConfiguration]: ...


class DatabaseNameSource(Protocol):
    def get_database(self, database_id: int): ...


class WarningStateSource(Protocol):
    def load(self) -> Set[int]: ...

    def save(self, armed_database_ids: Set[int]) -> None: ...


def resolve_eligible_pool_warning_threshold(
    guild_configuration: Optional[GuildConfiguration],
    database_configuration: Optional[SuggestionDatabaseConfiguration],
    candidate_count: int,
) -> int:
    """The eligible-pool threshold at or below which the warning fires: a
    database override, else a guild override, else the automatic default
    (resolve_default_eligible_pool_warning_threshold). Shared between
    EligiblePoolWarningService's own trigger check and /database health's
    "Low Pool Status" line, so the two can never disagree about what
    "low" means for a given collection.
    """
    if database_configuration is not None and database_configuration.notifications.low_suggestion_pool_threshold is not None:
        return database_configuration.notifications.low_suggestion_pool_threshold
    if (
        guild_configuration is not None
        and guild_configuration.notifications.administrative.low_suggestion_pool_threshold is not None
    ):
        return guild_configuration.notifications.administrative.low_suggestion_pool_threshold
    return resolve_default_eligible_pool_warning_threshold(candidate_count)


@dataclass(frozen=True)
class EligiblePoolWarningDecision:
    """Whether the Eligible Pool Warning should be sent right now."""

    should_send: bool
    message: Optional[str] = None
    destination_channel_id: Optional[int] = None


class EligiblePoolWarningService:
    """Decides when to send the Eligible Pool Warning, and builds its text."""

    def __init__(
        self,
        eligibility_service: CollectionEligibilityService,
        warning_state_repository: WarningStateSource,
        guild_configuration_repository: GuildConfigurationSource,
        suggestion_database_configuration_repository: DatabaseConfigurationSource,
        database_source: Optional[DatabaseNameSource] = None,
    ) -> None:
        self._eligibility_service = eligibility_service
        self._warning_state_repository = warning_state_repository
        self._guild_configuration_repository = guild_configuration_repository
        self._suggestion_database_configuration_repository = suggestion_database_configuration_repository
        self._database_source = database_source
        self._armed_database_ids: Set[int] = set(warning_state_repository.load())

    def evaluate(self, *, guild_id: int, database_id: int) -> EligiblePoolWarningDecision:
        """Decide whether to warn for one database right now. Never
        mutates rotation state (there is none left to mutate here) --
        purely reads eligibility and this service's own armed/disarmed
        flag.
        """
        guild_configuration = self._guild_configuration_repository.get(guild_id)
        database_configuration = self._suggestion_database_configuration_repository.get(guild_id, database_id)

        if not self._resolve_enabled(guild_configuration, database_configuration):
            return EligiblePoolWarningDecision(should_send=False)

        candidate_count = self._resolve_candidate_count(guild_configuration)
        eligibility = self._eligibility_service.get_eligibility(database_id)
        eligible_count = eligibility.eligible_pool_count
        threshold = resolve_eligible_pool_warning_threshold(guild_configuration, database_configuration, candidate_count)

        if eligible_count > threshold:
            self._disarm(database_id)
            return EligiblePoolWarningDecision(should_send=False)

        # eligible_count <= threshold: already warned and still below --
        # suppress the duplicate. Otherwise, this is a fresh crossing
        # into the warning range -- arm and fire.
        if database_id in self._armed_database_ids:
            return EligiblePoolWarningDecision(should_send=False)

        destination_channel_id = self._resolve_destination_channel_id(guild_configuration)
        if destination_channel_id is None:
            return EligiblePoolWarningDecision(should_send=False)

        self._arm(database_id)
        message = self._build_message(database_id, eligible_count, threshold)
        return EligiblePoolWarningDecision(
            should_send=True,
            message=message,
            destination_channel_id=destination_channel_id,
        )

    def _arm(self, database_id: int) -> None:
        if database_id not in self._armed_database_ids:
            self._armed_database_ids.add(database_id)
            self._warning_state_repository.save(set(self._armed_database_ids))

    def _disarm(self, database_id: int) -> None:
        if database_id in self._armed_database_ids:
            self._armed_database_ids.discard(database_id)
            self._warning_state_repository.save(set(self._armed_database_ids))

    def _resolve_enabled(
        self,
        guild_configuration: Optional[GuildConfiguration],
        database_configuration: Optional[SuggestionDatabaseConfiguration],
    ) -> bool:
        if guild_configuration is not None:
            # Both legacy flags are honored (AND'd) so no existing
            # guild's effective enabled/disabled state changes as a
            # result of this migration -- notifications.administrative.
            # low_suggestion_pool is the one to configure going forward.
            guild_enabled = (
                guild_configuration.feature_flags.low_suggestion_pool_alerts
                and guild_configuration.notifications.administrative.low_suggestion_pool
            )
        else:
            guild_enabled = DEFAULT_ELIGIBLE_POOL_WARNING_ENABLED

        if database_configuration is not None and database_configuration.notifications.low_suggestion_pool_alerts is not None:
            return database_configuration.notifications.low_suggestion_pool_alerts
        return guild_enabled

    @staticmethod
    def _resolve_candidate_count(guild_configuration: Optional[GuildConfiguration]) -> int:
        if guild_configuration is None:
            return 3
        return guild_configuration.voting_defaults.candidate_count

    @staticmethod
    def _resolve_destination_channel_id(guild_configuration: Optional[GuildConfiguration]) -> Optional[int]:
        if guild_configuration is None:
            return None
        destination = guild_configuration.notifications.administrative.low_suggestion_pool_destination
        if destination is EligiblePoolWarningDestination.HOME_CHANNEL:
            return guild_configuration.channels.home_channel_id
        return guild_configuration.channels.admin_channel_id

    def _build_message(self, database_id: int, eligible_count: int, threshold: int) -> str:
        database_name = self._resolve_database_name(database_id)
        return (
            f"**Eligible Pool Warning** -- {database_name}\n"
            f"Eligible Items Remaining: {eligible_count}\n"
            f"Warning Threshold: {threshold}\n"
            "Add more suggestions with `/add` followed by a title or IMDb link."
        )

    def _resolve_database_name(self, database_id: int) -> str:
        if self._database_source is None:
            return f"Collection #{database_id}"
        database = self._database_source.get_database(database_id)
        return database.name if database is not None else f"Collection #{database_id}"
