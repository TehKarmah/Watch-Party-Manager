"""Nominee-selection strategy architecture.

CandidateSelectionStrategy is the pluggable seam that lets
NomineeSelectionService (pre-existing, unchanged) consult an optional
strategy to determine the candidate pool and per-candidate weight
*before* running its own genre/media-type diversity pass -- when no
strategy is supplied, NomineeSelectionService's behavior is exactly what
it was before this architecture existed.

Rotation Removal: this module now has three concrete strategies, all
rotation-free by construction -- no suggestion is ever excluded based on
presentation history, so every eligible suggestion is always immediately
selectable and no rotation state exists anywhere:

  - InfinitePoolStrategy ("Pure Random"): every eligible suggestion,
    neutral weight.
  - FavorNewAdditionsStrategy / FavorOlderAdditionsStrategy: like
    InfinitePoolStrategy, but weighted by each suggestion's permanent
    suggestion_date (WatchItemJourney.suggestion_date) via a smooth
    half-life decay curve, favoring recently- or longest-waiting
    suggestions respectively.

The weighting architecture (WeightingFactor/CompositeWeighting) exists
specifically so a future factor (Likes, genre diversity, franchise
spacing, ...) is just a new class implementing WeightingFactor, composed
alongside existing factors -- no existing strategy needs to change shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Protocol, Sequence, runtime_checkable

from watch_party_manager.domain.suggestion_database_configuration import CandidateSelectionMode
from watch_party_manager.domain.watch_item import WatchItem, WatchItemStatus

NEUTRAL_WEIGHT = 1.0

# The floor Favor New/Older Additions' weighting curve decays toward --
# never zero, so a suggestion is never fully excluded, just heavily
# deprioritized once old.
MIN_SUGGESTION_DATE_WEIGHT = 0.1
# How many days it takes the recency score below to halve. Tunable --
# not exposed as user configuration in this phase.
SUGGESTION_DATE_WEIGHT_HALF_LIFE_DAYS = 30


@runtime_checkable
class WeightingFactor(Protocol):
    """One independent contributor to a candidate's selection weight.

    A future factor (Likes, genre diversity, franchise spacing, ...) is
    just a new class implementing this Protocol, composed alongside
    existing factors via CompositeWeighting -- no existing strategy needs
    to change shape.
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
    are finally chosen, so a strategy can record bookkeeping for suggestions
    that were actually selected. None of the current strategies use
    on_presented for anything (see module docstring) -- it remains part of
    the Protocol for a future strategy that might need it.
    """

    def candidate_pool(self, database_id: int, requested_count: Optional[int] = None) -> List[WatchItem]: ...

    def weight_for(self, watch_item: WatchItem) -> float: ...

    def on_presented(self, database_id: int, suggestion_ids: Sequence[int]) -> None: ...


class CandidateSuggestionSource(Protocol):
    """The subset of SuggestionService every strategy needs."""

    def get_suggestions_for_database(self, database_id: int) -> List[WatchItem]: ...


@dataclass
class InfinitePoolStrategy:
    """"Pure Random" -- every eligible suggestion, always."""

    suggestion_source: CandidateSuggestionSource

    def candidate_pool(self, database_id: int, requested_count: Optional[int] = None) -> List[WatchItem]:
        # requested_count is intentionally unused: there is nothing to
        # roll over. A Vote Winner or Watched item must never be
        # selectable again, so both are excluded explicitly here (the
        # suggestion source's own default only ever excludes Archived).
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
    fully excluded, just heavily deprioritized once old. Uses only
    WatchItemJourney.suggestion_date -- permanent metadata.
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


def _non_terminal_suggestions(suggestion_source: CandidateSuggestionSource, database_id: int) -> List[WatchItem]:
    """Every suggestion still selectable in principle -- excludes Vote
    Winner and Watched (a suggestion must never be nominated again once
    either has happened), shared by every strategy in this module so
    this filter is defined exactly once.
    """
    return [
        item
        for item in suggestion_source.get_suggestions_for_database(database_id)
        if item.status not in (WatchItemStatus.VOTE_WINNER, WatchItemStatus.WATCHED)
    ]


@dataclass
class FavorNewAdditionsStrategy:
    """"Favor New Additions" -- every eligible suggestion is always
    immediately selectable, weighted toward recently-suggested items via
    WatchItemJourney.suggestion_date.
    """

    suggestion_source: CandidateSuggestionSource
    weighting: CompositeWeighting = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.weighting is None:
            self.weighting = CompositeWeighting(factors=(_FavorNewAdditionsWeighting(),))

    def candidate_pool(self, database_id: int, requested_count: Optional[int] = None) -> List[WatchItem]:
        return _non_terminal_suggestions(self.suggestion_source, database_id)

    def weight_for(self, watch_item: WatchItem) -> float:
        return self.weighting.weight(watch_item)

    def on_presented(self, database_id: int, suggestion_ids: Sequence[int]) -> None:
        return None


@dataclass
class FavorOlderAdditionsStrategy:
    """"Favor Older Additions" -- the inverse weighting of
    FavorNewAdditionsStrategy, otherwise identical.
    """

    suggestion_source: CandidateSuggestionSource
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
    suggestion_source: CandidateSuggestionSource,
) -> CandidateSelectionStrategy:
    """Resolve the configured mode to its strategy implementation.

    The single switch point everywhere else (NomineeSelectionService,
    bot.py) works only against the CandidateSelectionStrategy Protocol,
    never against the mode enum directly.
    """
    if mode is CandidateSelectionMode.FAVOR_NEW_ADDITIONS:
        return FavorNewAdditionsStrategy(suggestion_source=suggestion_source)
    if mode is CandidateSelectionMode.FAVOR_OLDER_ADDITIONS:
        return FavorOlderAdditionsStrategy(suggestion_source=suggestion_source)
    return InfinitePoolStrategy(suggestion_source=suggestion_source)
