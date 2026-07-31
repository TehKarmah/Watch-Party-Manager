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

Rotation-removal Phase 1 adds two more, coexisting alongside the three
above rather than replacing them yet (see RotationService's own module
docstring for the removal plan):
  - FavorNewAdditionsStrategy / FavorOlderAdditionsStrategy: like
    InfinitePoolStrategy, neither touches RotationService or excludes
    anything based on presentation history -- every suggestion is always
    immediately selectable. Unlike InfinitePoolStrategy, each applies a
    smooth weighting curve driven by WatchItemJourney.suggestion_date
    (permanent metadata recorded once at suggestion time, entirely
    independent of rotation state) instead of neutral weight for
    everyone. These, together with InfinitePoolStrategy ("Pure Random"),
    are the three modes now offered as Nominee Selection choices; the
    two rotation-based strategies above remain fully functional for any
    collection still configured to use them, but are no longer offered
    as a new choice (see domain/suggestion_database_configuration.py).

Section 9's future weighting architecture (Likes, cooldowns, genre
diversity, etc.) plugs in via WeightingFactor: any new factor is just
another entry in a CompositeWeighting's factor tuple, multiplied in
without changing any strategy's shape -- exactly how the two new
suggestion-date factors below plug in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Protocol, Sequence, runtime_checkable

from watch_party_manager.domain.suggestion_database_configuration import CandidateSelectionMode
from watch_party_manager.domain.watch_item import WatchItem, WatchItemStatus
from watch_party_manager.services.rotation_service import RotationService

# Soft Rotation's weight for a suggestion already presented at least once.
# Not zero -- FR-033B Section 1 requires presented suggestions to "remain
# technically eligible," so they must always retain some chance.
SOFT_ROTATION_PRESENTED_WEIGHT = 0.1
NEUTRAL_WEIGHT = 1.0

# Rotation-removal Phase 1: the floor Favor New/Older Additions' weighting
# curve decays toward -- never zero, mirroring SOFT_ROTATION_PRESENTED_
# WEIGHT's own "never fully exclude" philosophy, just applied to
# suggestion age instead of presentation history.
MIN_SUGGESTION_DATE_WEIGHT = 0.1
# How many days it takes the recency score below to halve. Tunable --
# not exposed as user configuration in this phase.
SUGGESTION_DATE_WEIGHT_HALF_LIFE_DAYS = 30


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

    def candidate_pool(self, database_id: int, requested_count: Optional[int] = None) -> List[WatchItem]: ...

    def weight_for(self, watch_item: WatchItem) -> float: ...

    def on_presented(self, database_id: int, suggestion_ids: Sequence[int]) -> None: ...


class RotationPoolSuggestionSource(Protocol):
    """The subset of SuggestionService rotation-aware strategies need."""

    def get_suggestions_for_database(self, database_id: int) -> List[WatchItem]: ...


@dataclass
class RotationPoolStrategy:
    """FR-033B's default mode: hard exclusion of already-presented suggestions."""

    rotation_service: RotationService

    def candidate_pool(self, database_id: int, requested_count: Optional[int] = None) -> List[WatchItem]:
        # requested_count is known: use the count-aware rollover rule,
        # which rolls the rotation forward whenever it can't currently
        # supply enough pending candidates (release-blocking fix -- see
        # RotationService.resolve_rotation_for_requested_count). When no
        # count is known (a caller checking eligibility with no vote size
        # in mind), fall back to the original exhaustion-only rollover so
        # that behavior is unchanged for those callers.
        if requested_count is not None:
            self.rotation_service.resolve_rotation_for_requested_count(database_id, requested_count)
        else:
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

    def candidate_pool(self, database_id: int, requested_count: Optional[int] = None) -> List[WatchItem]:
        # requested_count is intentionally unused: Soft Rotation never
        # excludes anything (see class docstring), so there is nothing
        # for a requested vote size to roll over -- preserves this
        # mode's existing behavior unchanged. Rotation & Collection
        # Health Audit: a Vote Winner must never be selectable again in
        # any mode, so it's excluded here explicitly -- get_suggestions_
        # for_database's own default only ever excludes Archived. A
        # Watched item is excluded the same way, for the same reason.
        return [
            item
            for item in self.suggestion_source.get_suggestions_for_database(database_id)
            if item.status not in (WatchItemStatus.VOTE_WINNER, WatchItemStatus.WATCHED)
        ]

    def weight_for(self, watch_item: WatchItem) -> float:
        return self.weighting.weight(watch_item)

    def on_presented(self, database_id: int, suggestion_ids: Sequence[int]) -> None:
        self.rotation_service.record_presentation(database_id, suggestion_ids)


@dataclass
class InfinitePoolStrategy:
    """Every eligible suggestion, always -- no rotation concept at all."""

    suggestion_source: RotationPoolSuggestionSource

    def candidate_pool(self, database_id: int, requested_count: Optional[int] = None) -> List[WatchItem]:
        # requested_count is intentionally unused: Infinite Pool has no
        # rotation concept at all (see class docstring), so there is
        # nothing to roll over -- preserves this mode's existing
        # behavior unchanged. Rotation & Collection Health Audit: a Vote
        # Winner must never be selectable again in any mode, so it's
        # excluded here explicitly -- see SoftRotationStrategy's
        # identical fix for the full rationale. A Watched item is
        # excluded the same way, for the same reason.
        return [
            item
            for item in self.suggestion_source.get_suggestions_for_database(database_id)
            if item.status not in (WatchItemStatus.VOTE_WINNER, WatchItemStatus.WATCHED)
        ]

    def weight_for(self, watch_item: WatchItem) -> float:
        return NEUTRAL_WEIGHT

    def on_presented(self, database_id: int, suggestion_ids: Sequence[int]) -> None:
        return None


def _suggestion_date_recency_score(watch_item: WatchItem, *, today: Optional[date] = None) -> Optional[float]:
    """How recently `watch_item` was suggested, as a smooth 0-1 score
    that halves every SUGGESTION_DATE_WEIGHT_HALF_LIFE_DAYS (1.0 the day
    it's suggested, 0.5 after one half-life, 0.25 after two, ...,
    asymptotically approaching but never reaching 0).

    Returns None -- callers fall back to NEUTRAL_WEIGHT rather than
    guessing -- when journey.suggestion_date is unset (a legacy/imported
    record predating this field, or a fixture that never set it).
    `today` is only ever overridden by tests; production callers always
    use the real current date.
    """
    suggestion_date = watch_item.journey.suggestion_date
    if suggestion_date is None:
        return None
    reference_date = today if today is not None else date.today()
    age_days = max((reference_date - suggestion_date).days, 0)
    return 2.0 ** (-age_days / SUGGESTION_DATE_WEIGHT_HALF_LIFE_DAYS)


class _FavorNewAdditionsWeighting:
    """WeightingFactor: boosts recently-suggested items, decaying
    smoothly toward MIN_SUGGESTION_DATE_WEIGHT as they age -- never
    fully excluded, just heavily deprioritized once old (mirrors
    _PresentedWeighting's own "never zero" philosophy, applied to
    suggestion age instead of presentation history). Uses only
    WatchItemJourney.suggestion_date -- permanent metadata, no
    RotationService dependency (Rotation-removal Phase 1).
    """

    def weight(self, watch_item: WatchItem) -> float:
        recency = _suggestion_date_recency_score(watch_item)
        if recency is None:
            return NEUTRAL_WEIGHT
        return MIN_SUGGESTION_DATE_WEIGHT + (NEUTRAL_WEIGHT - MIN_SUGGESTION_DATE_WEIGHT) * recency


class _FavorOlderAdditionsWeighting:
    """WeightingFactor: the inverse of _FavorNewAdditionsWeighting --
    boosts suggestions that have sat in the pool the longest, so a
    collection's backlog doesn't languish forever unselected. Same
    half-life curve and None-handling, just applied to (1 - recency)
    instead of recency.
    """

    def weight(self, watch_item: WatchItem) -> float:
        recency = _suggestion_date_recency_score(watch_item)
        if recency is None:
            return NEUTRAL_WEIGHT
        return MIN_SUGGESTION_DATE_WEIGHT + (NEUTRAL_WEIGHT - MIN_SUGGESTION_DATE_WEIGHT) * (1.0 - recency)


def _non_terminal_suggestions(
    suggestion_source: RotationPoolSuggestionSource, database_id: int
) -> List[WatchItem]:
    """Every suggestion still selectable in principle -- excludes Vote
    Winner and Watched (a suggestion must never be nominated again once
    either has happened; see SoftRotationStrategy's identical exclusion
    for the full rationale), shared by every rotation-free strategy
    (Rotation-removal Phase 1) so this filter is defined exactly once.
    """
    return [
        item
        for item in suggestion_source.get_suggestions_for_database(database_id)
        if item.status not in (WatchItemStatus.VOTE_WINNER, WatchItemStatus.WATCHED)
    ]


@dataclass
class FavorNewAdditionsStrategy:
    """Rotation-removal Phase 1: "Favor New Additions" -- every eligible
    suggestion is always immediately selectable (no rotation concept at
    all, exactly like InfinitePoolStrategy), weighted toward recently-
    suggested items via WatchItemJourney.suggestion_date rather than
    rotation-presentation history.
    """

    suggestion_source: RotationPoolSuggestionSource
    weighting: CompositeWeighting = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.weighting is None:
            self.weighting = CompositeWeighting(factors=(_FavorNewAdditionsWeighting(),))

    def candidate_pool(self, database_id: int, requested_count: Optional[int] = None) -> List[WatchItem]:
        # requested_count is intentionally unused -- there is no rotation
        # to roll over, mirroring InfinitePoolStrategy exactly.
        return _non_terminal_suggestions(self.suggestion_source, database_id)

    def weight_for(self, watch_item: WatchItem) -> float:
        return self.weighting.weight(watch_item)

    def on_presented(self, database_id: int, suggestion_ids: Sequence[int]) -> None:
        # No rotation state to record presentation into -- this mode
        # never touches RotationService.
        return None


@dataclass
class FavorOlderAdditionsStrategy:
    """Rotation-removal Phase 1: "Favor Older Additions" -- the inverse
    weighting of FavorNewAdditionsStrategy, otherwise identical (every
    eligible suggestion always immediately selectable, no rotation
    concept).
    """

    suggestion_source: RotationPoolSuggestionSource
    weighting: CompositeWeighting = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.weighting is None:
            self.weighting = CompositeWeighting(factors=(_FavorOlderAdditionsWeighting(),))

    def candidate_pool(self, database_id: int, requested_count: Optional[int] = None) -> List[WatchItem]:
        return _non_terminal_suggestions(self.suggestion_source, database_id)

    def weight_for(self, watch_item: WatchItem) -> float:
        return self.weighting.weight(watch_item)

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

    Rotation-removal Phase 1: rotation_service is still accepted
    unconditionally (never Optional) since ROTATION_POOL/SOFT_ROTATION
    still need it and are not being removed this phase -- the two new
    modes below simply never call it.
    """
    if mode is CandidateSelectionMode.ROTATION_POOL:
        return RotationPoolStrategy(rotation_service=rotation_service)
    if mode is CandidateSelectionMode.SOFT_ROTATION:
        return SoftRotationStrategy(rotation_service=rotation_service, suggestion_source=suggestion_source)
    if mode is CandidateSelectionMode.FAVOR_NEW_ADDITIONS:
        return FavorNewAdditionsStrategy(suggestion_source=suggestion_source)
    if mode is CandidateSelectionMode.FAVOR_OLDER_ADDITIONS:
        return FavorOlderAdditionsStrategy(suggestion_source=suggestion_source)
    return InfinitePoolStrategy(suggestion_source=suggestion_source)
