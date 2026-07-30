"""Rotation & Collection Health: the one authoritative implementation of
"what suggestions are eligible right now?"

Before this, /vote start, /list, the low-pool reminder, and statistics
each answered that question slightly differently (see the audit this
module's introduction was based on -- Soft Rotation and Infinite Pool
candidate pools even included Vote Winner suggestions, a real bug fixed
alongside this consolidation in candidate_selection_strategy.py). Every
one of those call sites now resolves eligibility through
CollectionEligibilityService instead of recomputing it.

Deliberately built as a thin layer on top of the existing, unchanged
CandidateSelectionStrategy architecture (candidate_selection_strategy.py)
rather than a parallel reimplementation: resolve() delegates to
build_candidate_selection_strategy(...).candidate_pool(...), the exact
same call NomineeSelectionService itself makes, so /vote start's actual
nominee pool and this service's "eligible" bucket can never diverge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, Tuple

from watch_party_manager.domain.suggestion_database_configuration import CandidateSelectionMode
from watch_party_manager.domain.watch_item import WatchItem, WatchItemStatus
from watch_party_manager.services.candidate_selection_strategy import build_candidate_selection_strategy
from watch_party_manager.services.rotation_service import RotationService


class EligibilitySuggestionSource(Protocol):
    def get_suggestions_for_database(self, database_id: int, *, include_archived: bool = False) -> list: ...


@dataclass(frozen=True)
class CollectionEligibility:
    """The authoritative breakdown of one collection's suggestions.

    eligible: selectable for the next vote right now (mode-aware --
        excludes Rotation Cooldown only for ROTATION_POOL, since Soft
        Rotation/Infinite Pool have no cooldown concept).
    rotation_cooldown: active but not currently eligible (always empty
        outside ROTATION_POOL mode).
    vote_winners / retired / watched: terminal-state suggestions,
        included for Collection Health's reconciliation, never part of
        "eligible." watched is populated only by the explicit Watched
        action -- never automatically from winning a vote or retiring.
    rollover_occurred: True only when resolve() actually rolled the
        rotation over to produce this snapshot -- always False from
        peek(), which never mutates anything.
    """

    database_id: int
    mode: CandidateSelectionMode
    eligible: Tuple[WatchItem, ...]
    rotation_cooldown: Tuple[WatchItem, ...]
    vote_winners: Tuple[WatchItem, ...]
    retired: Tuple[WatchItem, ...]
    watched: Tuple[WatchItem, ...] = ()
    rollover_occurred: bool = False

    @property
    def active(self) -> Tuple[WatchItem, ...]:
        """Active = Eligible + Rotation Cooldown (Collection Health's own
        reconciliation identity)."""
        return self.eligible + self.rotation_cooldown

    @property
    def total(self) -> int:
        """Total = Active + Vote Winners + Retired + Watched."""
        return len(self.active) + len(self.vote_winners) + len(self.retired) + len(self.watched)


class CollectionEligibilityService:
    """THE authoritative eligibility resolver. See module docstring."""

    def __init__(
        self, suggestion_service: EligibilitySuggestionSource, rotation_service: RotationService
    ) -> None:
        self._suggestion_service = suggestion_service
        self._rotation_service = rotation_service

    def resolve(
        self, database_id: int, mode: CandidateSelectionMode, *, requested_count: Optional[int] = None
    ) -> CollectionEligibility:
        """Resolve eligibility for a command that is allowed to change
        rotation state -- /vote start (via NomineeSelectionService, which
        this mirrors exactly) and /list (Rotation & Collection Health
        goal: /list must never disagree with voting).

        Rollover-before-reporting: when requested_count is given and the
        database uses ROTATION_POOL, a rotation that can't currently
        supply requested_count pending suggestions is automatically
        rolled forward first (see
        RotationService.resolve_rotation_for_requested_count) -- the
        caller never has to separately discover that a rollover was
        needed. Soft Rotation/Infinite Pool have no rotation to roll
        over, so requested_count is simply unused for them, matching
        those strategies' own documented behavior.

        Use peek() instead for a caller that must never mutate rotation
        state (Collection Health, the Rotation Low-Pool notification).
        """
        strategy = build_candidate_selection_strategy(mode, self._rotation_service, self._suggestion_service)
        rotation_before = (
            self._rotation_service.get_open_rotation(database_id)
            if mode is CandidateSelectionMode.ROTATION_POOL
            else None
        )
        eligible_items = strategy.candidate_pool(database_id, requested_count)
        rotation_after = (
            self._rotation_service.get_open_rotation(database_id)
            if mode is CandidateSelectionMode.ROTATION_POOL
            else None
        )
        rollover_occurred = (
            rotation_before is not None
            and rotation_after is not None
            and rotation_before.id != rotation_after.id
        )
        return self._bucket(database_id, mode, eligible_items, rollover_occurred=rollover_occurred)

    def peek(self, database_id: int, mode: CandidateSelectionMode) -> CollectionEligibility:
        """Read-only snapshot of current eligibility. Never bootstraps or
        advances a rotation -- safe for Collection Health (which must
        never modify rotation state) and the Rotation Low-Pool
        notification (which must never bootstrap a rotation merely to
        check pool size). Uses RotationService.is_in_rotation_cooldown,
        the one pre-existing per-item classifier that was already both
        correct and non-bootstrapping.
        """
        active_items = [
            item
            for item in self._suggestion_service.get_suggestions_for_database(database_id)
            if item.status is WatchItemStatus.SUGGESTED
        ]
        if mode is CandidateSelectionMode.ROTATION_POOL:
            eligible_items = [
                item for item in active_items if not self._rotation_service.is_in_rotation_cooldown(item)
            ]
        else:
            eligible_items = active_items
        return self._bucket(database_id, mode, eligible_items, rollover_occurred=False)

    def _bucket(
        self,
        database_id: int,
        mode: CandidateSelectionMode,
        eligible_items,
        *,
        rollover_occurred: bool,
    ) -> CollectionEligibility:
        all_items = self._suggestion_service.get_suggestions_for_database(database_id, include_archived=True)
        retired = tuple(item for item in all_items if item.status is WatchItemStatus.ARCHIVED)
        vote_winners = tuple(item for item in all_items if item.status is WatchItemStatus.VOTE_WINNER)
        watched = tuple(item for item in all_items if item.status is WatchItemStatus.WATCHED)
        active_items = tuple(item for item in all_items if item.status is WatchItemStatus.SUGGESTED)

        eligible = tuple(eligible_items)
        if mode is CandidateSelectionMode.ROTATION_POOL:
            eligible_ids = {item.id for item in eligible}
            rotation_cooldown = tuple(item for item in active_items if item.id not in eligible_ids)
        else:
            rotation_cooldown = ()

        return CollectionEligibility(
            database_id=database_id,
            mode=mode,
            eligible=eligible,
            rotation_cooldown=rotation_cooldown,
            vote_winners=vote_winners,
            retired=retired,
            watched=watched,
            rollover_occurred=rollover_occurred,
        )
