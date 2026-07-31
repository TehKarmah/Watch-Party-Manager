"""Rotation & Collection Health: the Rotation Low-Pool notification.

Replaces the older, interval-based Low Pool Reminder (FR-033B Section 7,
formerly low_pool_reminder_service.py): that reminder re-sent itself every
`minimum_interval_hours` (default 24h) and computed "remaining" with its
own flat suggestion count, independent of the database's actual
CandidateSelectionMode -- one more of the eligibility divergences this
milestone's audit found. This service instead:

  - Resolves "eligible remaining" through CollectionEligibilityService.peek()
    -- the same authoritative eligibility every other command now shares,
    and never bootstraps or advances a rotation merely to check.
  - Notifies at most once per rotation, resetting naturally the moment a
    fresh rotation begins (see RotationService.has_sent_low_pool_
    notification's docstring) -- no interval/timestamp involved.
  - Posts to a guild-level destination (Admin Channel by default, or the
    Watch Party Home Channel) rather than the database's own suggestion
    channel/thread -- deliberately never a collection's suggestion
    thread.

Kept outside CollectionEligibilityService/RotationService, mirroring the
old service's own reasoning: composing guild configuration, database
configuration, and eligibility into a single yes/no-plus-message decision
is its own cross-cutting concern.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Protocol

from watch_party_manager.domain.guild_configuration import (
    GuildConfiguration,
    RotationLowPoolNotificationDestination,
)
from watch_party_manager.domain.suggestion_database_configuration import (
    CandidateSelectionMode,
    SuggestionDatabaseConfiguration,
)
from watch_party_manager.services.collection_eligibility_service import CollectionEligibilityService
from watch_party_manager.services.rotation_service import RotationService

DEFAULT_LOW_POOL_NOTIFICATION_ENABLED = True
# The finalized automatic default (UI Polish: Rotation & Collection
# Health): the larger of 10% of the collection's Active Watch Items
# (Eligible for Voting + Rotation Cooldown -- Vote Winners and Retired
# are never counted) or two full configured voting rounds. The
# percentage term scales the threshold with collection size for large
# collections; the rounds term is a sane floor for small ones -- see
# resolve_default_low_pool_threshold.
DEFAULT_LOW_POOL_NOTIFICATION_ROUNDS = 2
DEFAULT_LOW_POOL_NOTIFICATION_PERCENTAGE = 0.10


def resolve_default_low_pool_threshold(active_count: int, candidate_count: int) -> int:
    """max(10% of Active Watch Items, two full configured voting rounds).

    The percentage term is rounded up (never down) -- a threshold that's
    slightly too low would silently under-warn, which is worse than
    warning one suggestion early. Only ever used when no explicit
    database/guild threshold override is saved (see
    resolve_low_pool_threshold).
    """
    percentage_threshold = math.ceil(active_count * DEFAULT_LOW_POOL_NOTIFICATION_PERCENTAGE)
    rounds_threshold = DEFAULT_LOW_POOL_NOTIFICATION_ROUNDS * candidate_count
    return max(percentage_threshold, rounds_threshold)


class GuildConfigurationSource(Protocol):
    def get(self, guild_id: int) -> Optional[GuildConfiguration]: ...


class DatabaseConfigurationSource(Protocol):
    def get(self, guild_id: int, database_id: int) -> Optional[SuggestionDatabaseConfiguration]: ...


class DatabaseNameSource(Protocol):
    def get_database(self, database_id: int): ...


def resolve_low_pool_threshold(
    guild_configuration: Optional[GuildConfiguration],
    database_configuration: Optional[SuggestionDatabaseConfiguration],
    candidate_count: int,
    active_count: int,
) -> int:
    """The eligible-suggestion threshold below which the pool is
    considered low: a database override, else a guild override, else
    the automatic default (resolve_default_low_pool_threshold). Shared
    between RotationLowPoolNotificationService's own trigger check and
    /database health's "Low Pool Status" line, so the two can never
    disagree about what "low" means for a given collection.
    """
    if database_configuration is not None and database_configuration.notifications.low_suggestion_pool_threshold is not None:
        return database_configuration.notifications.low_suggestion_pool_threshold
    if (
        guild_configuration is not None
        and guild_configuration.notifications.administrative.low_suggestion_pool_threshold is not None
    ):
        return guild_configuration.notifications.administrative.low_suggestion_pool_threshold
    return resolve_default_low_pool_threshold(active_count, candidate_count)


@dataclass(frozen=True)
class RotationLowPoolNotificationDecision:
    """Whether the Rotation Low-Pool notification should be sent right now."""

    should_send: bool
    message: Optional[str] = None
    destination_channel_id: Optional[int] = None
    rotation_id: Optional[int] = None


class RotationLowPoolNotificationService:
    """Decides when to send the Rotation Low-Pool notification, and builds its text."""

    def __init__(
        self,
        eligibility_service: CollectionEligibilityService,
        rotation_service: RotationService,
        guild_configuration_repository: GuildConfigurationSource,
        suggestion_database_configuration_repository: DatabaseConfigurationSource,
        database_source: Optional[DatabaseNameSource] = None,
    ) -> None:
        self._eligibility_service = eligibility_service
        self._rotation_service = rotation_service
        self._guild_configuration_repository = guild_configuration_repository
        self._suggestion_database_configuration_repository = suggestion_database_configuration_repository
        self._database_source = database_source

    def evaluate(
        self,
        *,
        guild_id: int,
        database_id: int,
        candidate_selection_mode: CandidateSelectionMode,
    ) -> RotationLowPoolNotificationDecision:
        """Decide whether to notify for one database right now.

        Never bootstraps or advances a rotation merely to calculate
        low-pool status (see CollectionEligibilityService.peek). For
        Infinite Pool, Favor New Additions, and Favor Older Additions
        databases (no rotation concept -- RotationService never creates a
        rotation record for any of them), there is no rotation id to
        dedup against, so this never fires for them; a documented
        limitation, not an oversight (see docs).
        """
        guild_configuration = self._guild_configuration_repository.get(guild_id)
        database_configuration = self._suggestion_database_configuration_repository.get(guild_id, database_id)

        if not self._resolve_enabled(guild_configuration, database_configuration):
            return RotationLowPoolNotificationDecision(should_send=False)

        candidate_count = self._resolve_candidate_count(guild_configuration)

        eligibility = self._eligibility_service.peek(database_id, candidate_selection_mode)
        eligible_count = len(eligibility.eligible)
        active_count = len(eligibility.active)
        threshold = self._resolve_threshold(guild_configuration, database_configuration, candidate_count, active_count)

        # Suppress entirely for very small collections where the
        # threshold isn't meaningful: if the whole Active pool is
        # already at or below the threshold, Eligible (which can never
        # exceed Active) would be below it too on every single rotation
        # forever, with nothing new to report each time -- exactly the
        # repeated, uninformative warning this milestone asks to avoid.
        if active_count <= threshold:
            return RotationLowPoolNotificationDecision(should_send=False)

        # Strictly "fewer than" the threshold -- a pool sitting exactly
        # at the threshold is not yet low.
        if eligible_count >= threshold:
            return RotationLowPoolNotificationDecision(should_send=False)

        rotation = self._rotation_service.get_open_rotation(database_id)
        if rotation is None:
            # Nothing to dedup against yet (no rotation exists at all --
            # either a brand-new Rotation Pool/Soft Rotation database
            # that has never run a vote, or an Infinite Pool database,
            # which never gets one). Never bootstrap one just to send a
            # notification -- skip rather than fabricate rotation state.
            return RotationLowPoolNotificationDecision(should_send=False)

        if self._rotation_service.has_sent_low_pool_notification(database_id, rotation.id):
            return RotationLowPoolNotificationDecision(should_send=False)

        destination_channel_id = self._resolve_destination_channel_id(guild_configuration)
        if destination_channel_id is None:
            return RotationLowPoolNotificationDecision(should_send=False)

        message = self._build_message(database_id, eligible_count, candidate_count)
        return RotationLowPoolNotificationDecision(
            should_send=True,
            message=message,
            destination_channel_id=destination_channel_id,
            rotation_id=rotation.id,
        )

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
            guild_enabled = DEFAULT_LOW_POOL_NOTIFICATION_ENABLED

        if database_configuration is not None and database_configuration.notifications.low_suggestion_pool_alerts is not None:
            return database_configuration.notifications.low_suggestion_pool_alerts
        return guild_enabled

    @staticmethod
    def _resolve_candidate_count(guild_configuration: Optional[GuildConfiguration]) -> int:
        if guild_configuration is None:
            return 3
        return guild_configuration.voting_defaults.candidate_count

    @staticmethod
    def _resolve_threshold(
        guild_configuration: Optional[GuildConfiguration],
        database_configuration: Optional[SuggestionDatabaseConfiguration],
        candidate_count: int,
        active_count: int,
    ) -> int:
        return resolve_low_pool_threshold(guild_configuration, database_configuration, candidate_count, active_count)

    @staticmethod
    def _resolve_destination_channel_id(guild_configuration: Optional[GuildConfiguration]) -> Optional[int]:
        if guild_configuration is None:
            return None
        destination = guild_configuration.notifications.administrative.low_suggestion_pool_destination
        if destination is RotationLowPoolNotificationDestination.HOME_CHANNEL:
            return guild_configuration.channels.home_channel_id
        return guild_configuration.channels.admin_channel_id

    def _build_message(self, database_id: int, eligible_count: int, candidate_count: int) -> str:
        suggestion_word = "suggestion" if eligible_count == 1 else "suggestions"
        database_name = self._resolve_database_name(database_id)
        return (
            f"**Rotation Low-Pool Notification** -- {database_name}\n"
            f"Eligible remaining: {eligible_count} {suggestion_word}\n"
            f"Configured candidate count: {candidate_count}\n"
            "Add more suggestions with `/add` followed by a title or IMDb link."
        )

    def _resolve_database_name(self, database_id: int) -> str:
        if self._database_source is None:
            return f"Collection #{database_id}"
        database = self._database_source.get_database(database_id)
        return database.name if database is not None else f"Collection #{database_id}"
