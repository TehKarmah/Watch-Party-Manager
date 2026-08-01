"""Optional, per-vote nominee-pool filters (Custom Vote Filter Architecture).

A NomineePoolFilter narrows a candidate pool -- it never chooses which of
the remaining suggestions actually become nominees; that decision still
belongs entirely to a CandidateSelectionStrategy (see
candidate_selection_strategy.py). The intended pipeline is:

    Eligible suggestions -> optional member filter -> optional genre
    filter -> Nominee Selection strategy -> selected nominees

FilteredCandidateSelectionStrategy is the seam that connects the two:
it implements the exact same CandidateSelectionStrategy Protocol as
InfinitePoolStrategy/FavorNewAdditionsStrategy/FavorOlderAdditionsStrategy,
wrapping one of them and narrowing its candidate_pool() output through
zero or more NomineePoolFilters before returning it. weight_for() and
on_presented() delegate to the wrapped strategy unchanged. Because it
satisfies the same Protocol, neither VoteService nor
NomineeSelectionService needs to know filters exist at all -- every
caller that already accepts a CandidateSelectionStrategy (select_nominees,
eligible_candidate_count) works with a filtered one with zero changes.

Member and genre filters are per-vote overrides only: nothing here is
ever persisted onto a collection's own SuggestionRulesConfig. A future
filter (runtime, release year, actor, rating, ...) is just a new class
implementing NomineePoolFilter -- no existing filter, strategy, or
service needs to change shape to add one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol, Sequence, runtime_checkable

from watch_party_manager.domain.watch_item import WatchItem
from watch_party_manager.services.candidate_selection_strategy import CandidateSelectionStrategy


@runtime_checkable
class NomineePoolFilter(Protocol):
    """One independent, composable narrowing of a candidate pool.

    apply() must never mutate the pool it's given -- it returns a new,
    narrowed list. describe() is a short, human-readable label (e.g. a
    member's display name, or a genre name) used to build summary and
    announcement text; it never appears in persisted data itself.
    """

    def apply(self, pool: Sequence[WatchItem]) -> List[WatchItem]: ...

    def describe(self) -> str: ...


@dataclass(frozen=True)
class MemberSuggestionFilter:
    """Narrows the pool to suggestions submitted by one Discord member.

    Matches WatchItemJourney.original_suggester -- a stable, stringified
    Discord user ID (see SuggestionService.suggest) -- never a display
    name, so a suggestion can never match based on a stale or similar-
    looking nickname. A suggestion with no recorded submitter (legacy
    suggestions predating original_suggester, where the field is None)
    never matches any member filter.
    """

    discord_user_id: int
    member_display: str

    def apply(self, pool: Sequence[WatchItem]) -> List[WatchItem]:
        target = str(self.discord_user_id)
        return [item for item in pool if item.journey.original_suggester == target]

    def describe(self) -> str:
        return self.member_display


@dataclass(frozen=True)
class GenreFilter:
    """Narrows the pool to suggestions tagged with one genre.

    Matched case-insensitively against WatchItem.genres (already-
    persisted IMDb metadata -- see services/imdb_metadata_service.py) --
    a suggestion with multiple genres matches when any one of them
    equals `genre`. A suggestion with no genre metadata at all never
    matches while a genre filter is active.
    """

    genre: str

    def apply(self, pool: Sequence[WatchItem]) -> List[WatchItem]:
        target = self.genre.strip().lower()
        return [item for item in pool if any(candidate.strip().lower() == target for candidate in item.genres)]

    def describe(self) -> str:
        return self.genre


def apply_nominee_pool_filters(
    pool: Sequence[WatchItem], filters: Sequence[NomineePoolFilter]
) -> List[WatchItem]:
    """Apply every filter in sequence, each narrowing the previous result.

    Combining a member filter and a genre filter this way naturally
    yields "eligible suggestions from this member, in this genre" --
    order between the two never changes the outcome (both are pure set
    intersections), but this is the single place that combination
    happens, so future filters compose the same way automatically.
    """
    result: List[WatchItem] = list(pool)
    for pool_filter in filters:
        result = pool_filter.apply(result)
    return result


@dataclass
class FilteredCandidateSelectionStrategy:
    """Decorator: narrows an inner CandidateSelectionStrategy's candidate
    pool through zero or more NomineePoolFilters.

    Implements CandidateSelectionStrategy itself, so it's a drop-in
    replacement anywhere a plain strategy is accepted (VoteService,
    NomineeSelectionService, bot.py's perform_start_vote) -- neither of
    the first two needs any changes to support per-vote filtering.
    """

    inner: CandidateSelectionStrategy
    filters: Sequence[NomineePoolFilter]

    def candidate_pool(self, database_id: int, requested_count: Optional[int] = None) -> List[WatchItem]:
        pool = self.inner.candidate_pool(database_id, requested_count)
        return apply_nominee_pool_filters(pool, self.filters)

    def weight_for(self, watch_item: WatchItem) -> float:
        return self.inner.weight_for(watch_item)

    def on_presented(self, database_id: int, suggestion_ids: Sequence[int]) -> None:
        self.inner.on_presented(database_id, suggestion_ids)


def genre_eligibility_counts(pool: Sequence[WatchItem]) -> Dict[str, int]:
    """Count eligible suggestions per genre represented in `pool`.

    Genres are grouped case-insensitively (treat capitalization
    consistently), but each group is reported under the first-seen
    original casing so display text still reads naturally (e.g.
    "Horror" rather than a forced canonical case). A suggestion with
    several genres contributes to each of its genres' counts, but only
    once per genre even if it somehow lists a duplicate.
    """
    counts: Dict[str, int] = {}
    display_case: Dict[str, str] = {}
    for item in pool:
        seen_this_item: set[str] = set()
        for genre in item.genres:
            normalized = genre.strip().lower()
            if not normalized or normalized in seen_this_item:
                continue
            seen_this_item.add(normalized)
            counts[normalized] = counts.get(normalized, 0) + 1
            display_case.setdefault(normalized, genre.strip())
    return {display_case[key]: count for key, count in counts.items()}
