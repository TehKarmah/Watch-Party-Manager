"""FR-033B candidate-selection strategy architecture.

CandidateSelectionStrategy is the pluggable seam FR-033B's instruction #2
asks for ("Do not hard-code behavior into VoteService. Create a reusable
selection strategy architecture."). NomineeSelectionService (pre-existing,
unchanged) consults an optional strategy to determine the candidate pool
and per-candidate weight *before* running its own genre/media-type
diversity pass -- when no strategy is supplied, NomineeSelectionService's
behavior is exactly what it was before this milestone, byte-for-byte.

Three concrete strategies satisfy FR-033B Section 1:
  - RotationPoolStrategy: hard-excludes suggestions already presented in
    the database's current rotation. Uses RotationService.
  - SoftRotationStrategy: never excludes anything, but weights already-
    presented suggestions far lower. Also uses RotationService (so
    "presented" means the same thing in both modes), but never triggers
    rotation-completion side effects a Soft Rotation database doesn't
    need in practice.
  - InfinitePoolStrategy: no rotation state at all -- every eligible
    suggestion, neutral weight. Never touches RotationService, so an
    Infinite Pool database never gets rotation records created for it
    (FR-033B Section 14: "Infinite Pool: ... no rotation state required").

Section 9's future weighting architecture (Likes, cooldowns, genre
diversity, etc.) plugs in via WeightingFactor: any new factor is just
another entry in a CompositeWeighting's factor tuple, multiplied in
without changing any strategy's shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Protocol, Sequence, runtime_checkable

from watch_party_manager.domain.suggestion_database_configuration import CandidateSelectionMode
from watch_party_manager.domain.watch_item import WatchItem
from watch_party_manager.services.rotation_service import RotationService

# Soft Rotation's weight for a suggestion already presented at least once.
# Not zero -- FR-033B Section 1 requires presented suggestions to "remain
# technically eligible," so they must always retain some chance.
SOFT_ROTATION_PRESENTED_WEIGHT = 0.1
NEUTRAL_WEIGHT = 1.0


@runtime_checkable
class WeightingFactor(Protocol):
    """One independent contributor to a candidate's selection weight.

    FR-033B Section 9's extension point: a future factor (Likes,
    cooldowns, genre diversity, franchise spacing, ...) is just a new
    class implementing this Protocol, composed alongside existing factors
    via CompositeWeighting -- no existing strategy needs to change shape.
    """

    def weight(self, watch_item: WatchItem) -> float: ...


@dataclass(frozen=True)
class CompositeWeighting:
    """Combines multiple WeightingFactors into a single multiplicative weight."""

    factors: Sequence[WeightingFactor]

    def weight(self, watch_item: WatchItem) -> float:
        result = NEUTRAL_WEIGHT
        for factor in self.factors:
            result *= factor.weight(watch_item)
        return result


class CandidateSelectionStrategy(Protocol):
    """A pluggable policy for which suggestions are selectable, and how weighted.

    candidate_pool/weight_for are called by NomineeSelectionService before
    its own diversity pass; on_presented is called afterward once nominees
    are finally chosen, so a strategy can record bookkeeping (e.g. rotation
    presentation) only for suggestions that were actually selected.
    """

    def candidate_pool(self, database_id: int) -> List[WatchItem]: ...

    def weight_for(self, watch_item: WatchItem) -> float: ...

    def on_presented(self, database_id: int, suggestion_ids: Sequence[int]) -> None: ...


class RotationPoolSuggestionSource(Protocol):
    """The subset of SuggestionService rotation-aware strategies need."""

    def get_suggestions_for_database(self, database_id: int) -> List[WatchItem]: ...


@dataclass
class RotationPoolStrategy:
    """FR-033B's default mode: hard exclusion of already-presented suggestions."""

    rotation_service: RotationService

    def candidate_pool(self, database_id: int) -> List[WatchItem]:
        # Triggers auto-transition to a fresh rotation first, if the
        # current one is already exhausted (FR-033B Section 1).
        self.rotation_service.current_rotation_for_selection(database_id)
        return self.rotation_service.remaining_suggestions(database_id)

    def weight_for(self, watch_item: WatchItem) -> float:
        return NEUTRAL_WEIGHT

    def on_presented(self, database_id: int, suggestion_ids: Sequence[int]) -> None:
        self.rotation_service.record_presentation(database_id, suggestion_ids)


class _PresentedWeighting:
    """WeightingFactor: weights down a suggestion already presented at least once."""

    def weight(self, watch_item: WatchItem) -> float:
        if watch_item.journey.rotation_history:
            return SOFT_ROTATION_PRESENTED_WEIGHT
        return NEUTRAL_WEIGHT


@dataclass
class SoftRotationStrategy:
    """Presented suggestions remain eligible but are weighted far lower.

    Reuses RotationService purely for its "presented" bookkeeping (see
    module docstring) so weighting is driven by the same signal
    RotationPoolStrategy hard-excludes on -- shared, single source of
    truth, different enforcement.
    """

    rotation_service: RotationService
    suggestion_source: RotationPoolSuggestionSource
    weighting: CompositeWeighting = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.weighting is None:
            self.weighting = CompositeWeighting(factors=(_PresentedWeighting(),))

    def candidate_pool(self, database_id: int) -> List[WatchItem]:
        return list(self.suggestion_source.get_suggestions_for_database(database_id))

    def weight_for(self, watch_item: WatchItem) -> float:
        return self.weighting.weight(watch_item)

    def on_presented(self, database_id: int, suggestion_ids: Sequence[int]) -> None:
        self.rotation_service.record_presentation(database_id, suggestion_ids)


@dataclass
class InfinitePoolStrategy:
    """Every eligible suggestion, always -- no rotation concept at all."""

    suggestion_source: RotationPoolSuggestionSource

    def candidate_pool(self, database_id: int) -> List[WatchItem]:
        return list(self.suggestion_source.get_suggestions_for_database(database_id))

    def weight_for(self, watch_item: WatchItem) -> float:
        return NEUTRAL_WEIGHT

    def on_presented(self, database_id: int, suggestion_ids: Sequence[int]) -> None:
        return None


def build_candidate_selection_strategy(
    mode: CandidateSelectionMode,
    rotation_service: RotationService,
    suggestion_source: RotationPoolSuggestionSource,
) -> CandidateSelectionStrategy:
    """Resolve the configured mode to its strategy implementation.

    The single switch point FR-033B's instruction #2 permits: everywhere
    else (NomineeSelectionService, bot.py) works only against the
    CandidateSelectionStrategy Protocol, never against the mode enum
    directly.
    """
    if mode is CandidateSelectionMode.ROTATION_POOL:
        return RotationPoolStrategy(rotation_service=rotation_service)
    if mode is CandidateSelectionMode.SOFT_ROTATION:
        return SoftRotationStrategy(rotation_service=rotation_service, suggestion_source=suggestion_source)
    return InfinitePoolStrategy(suggestion_source=suggestion_source)
