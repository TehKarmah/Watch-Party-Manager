"""Optional, per-vote nominee-pool filters (Custom Vote Filter Architecture).

A NomineePoolFilter narrows a candidate pool -- it never chooses which of
the remaining suggestions actually become nominees; that decision still
belongs entirely to a CandidateSelectionStrategy (see
candidate_selection_strategy.py). The intended pipeline is:

    Eligible suggestions -> Genre -> IMDb Rating -> MPAA Rating -> Actor
    -> Member -> Nominee Selection strategy -> selected nominees

(Order matches the shared UI's Current Filters summary and filter-editing
flow; each filter is a pure set intersection, so applying them in a
different order never changes the result -- the fixed order is purely
for consistent presentation.)

FilteredCandidateSelectionStrategy is the seam that connects filtering to
selection: it implements the exact same CandidateSelectionStrategy
Protocol as InfinitePoolStrategy/FavorNewAdditionsStrategy/
FavorOlderAdditionsStrategy, wrapping one of them and narrowing its
candidate_pool() output through zero or more NomineePoolFilters before
returning it. weight_for() and on_presented() delegate to the wrapped
strategy unchanged. Because it satisfies the same Protocol, neither
VoteService nor NomineeSelectionService needs to know filters exist at
all -- every caller that already accepts a CandidateSelectionStrategy
(select_nominees, eligible_candidate_count) works with a filtered one
with zero changes. /random_watch uses the same filters directly against
CollectionEligibilityService's own pool, bypassing nominee-selection
weighting entirely (see services/random_watch_service.py).

Every filter here is a per-session/per-vote override only: nothing in
this module is ever persisted onto a collection's own
SuggestionRulesConfig. A future filter is just one more class
implementing NomineePoolFilter -- no existing filter, strategy, or
service needs to change shape to add one.
"""

from __future__ import annotations

import re
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


@dataclass(frozen=True)
class ImdbRatingFilter:
    """Narrows the pool to suggestions whose stored IMDb rating falls
    within an inclusive [minimum, maximum] range (either bound optional).

    Matched against WatchItem.imdb_rating -- already-persisted IMDb
    metadata, parsed as a float at match time and never rounded, so a
    boundary value (e.g. exactly 7.0 against minimum=7.0) always
    matches. A suggestion with no stored rating, or one that can't be
    parsed as a number, never matches while this filter is active --
    matching services/member_filter_validation.py's and GenreFilter's
    own "missing metadata never matches an active filter" convention.
    """

    minimum: Optional[float] = None
    maximum: Optional[float] = None

    def apply(self, pool: Sequence[WatchItem]) -> List[WatchItem]:
        return [item for item in pool if self._matches(item)]

    def _matches(self, item: WatchItem) -> bool:
        if item.imdb_rating is None:
            return False
        try:
            value = float(item.imdb_rating)
        except (TypeError, ValueError):
            return False
        if self.minimum is not None and value < self.minimum:
            return False
        if self.maximum is not None and value > self.maximum:
            return False
        return True

    def describe(self) -> str:
        if self.minimum is not None and self.maximum is not None:
            return f"{self.minimum:.1f}–{self.maximum:.1f}"
        if self.minimum is not None:
            return f"{self.minimum:.1f}+"
        if self.maximum is not None:
            return f"{self.maximum:.1f} or lower"
        return "Any"


@dataclass(frozen=True)
class MpaaRatingFilter:
    """Narrows the pool to suggestions tagged with one MPAA/content
    rating, drawn from WatchItem.content_rating (already-persisted IMDb
    metadata -- OMDb's "Rated" field). Matched case-insensitively with
    surrounding whitespace trimmed, but never treats visibly different
    ratings (e.g. "Not Rated" vs "Unrated") as equivalent -- see
    mpaa_rating_eligibility_counts' own docstring for why those two stay
    distinct. A suggestion with no stored rating never matches while
    this filter is active.
    """

    rating: str

    def apply(self, pool: Sequence[WatchItem]) -> List[WatchItem]:
        target = self.rating.strip().lower()
        return [
            item
            for item in pool
            if item.content_rating is not None and item.content_rating.strip().lower() == target
        ]

    def describe(self) -> str:
        return self.rating


@dataclass(frozen=True)
class ActorFilter:
    """Narrows the pool to suggestions whose stored cast list includes
    one chosen actor, matched case-insensitively with surrounding
    whitespace trimmed against WatchItem.cast (already-persisted IMDb
    metadata -- OMDb's "Actors" field). A suggestion with no stored cast
    metadata never matches while this filter is active. `actor` should
    already be the canonical name as it appears in stored metadata (see
    search_cast_names) -- this filter itself performs no search/matching
    beyond the exact (case-insensitive) comparison.
    """

    actor: str

    def apply(self, pool: Sequence[WatchItem]) -> List[WatchItem]:
        target = self.actor.strip().lower()
        return [item for item in pool if any(name.strip().lower() == target for name in item.cast)]

    def describe(self) -> str:
        return self.actor


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


def mpaa_rating_eligibility_counts(pool: Sequence[WatchItem]) -> Dict[str, int]:
    """Count eligible suggestions per MPAA/content rating represented in
    `pool`, in the same shape as genre_eligibility_counts.

    Grouped case-insensitively with whitespace trimmed (matching
    MpaaRatingFilter's own matching rule), each group reported under the
    first-seen original casing/spacing. "Not Rated" and "Unrated" are
    deliberately kept as two distinct groups rather than merged: OMDb
    (and the MPAA/broadcast conventions it reflects) uses them for two
    different things in practice -- "Not Rated" for a release the MPAA
    never rated at all, "Unrated" for an alternate cut (e.g. a director's
    cut) released without submitting it for a new rating -- so treating
    them as equivalent would silently combine two suggestions that may
    not actually be interchangeable for whoever is filtering by rating.
    A suggestion with no stored rating contributes to no count.
    """
    counts: Dict[str, int] = {}
    display_case: Dict[str, str] = {}
    for item in pool:
        if item.content_rating is None:
            continue
        normalized = item.content_rating.strip().lower()
        if not normalized:
            continue
        counts[normalized] = counts.get(normalized, 0) + 1
        display_case.setdefault(normalized, item.content_rating.strip())
    return {display_case[key]: count for key, count in counts.items()}


def search_cast_names(pool: Sequence[WatchItem], query: str) -> List[tuple[str, int]]:
    """Search stored cast names in `pool` for `query` (case-insensitive
    substring match, surrounding whitespace on both the query and each
    stored name ignored), returning (canonical_name, eligible_count)
    pairs sorted deterministically -- most-represented actor first,
    alphabetical to break ties, matching genre_eligibility_counts'/
    build_genre_filter_select_options' own convention.

    "Canonical name" is simply the first-seen exact spelling/casing from
    already-persisted metadata for that matched actor -- there is no
    separate actor identity system, so the stored name itself is the
    stable value (same role stored genres/MPAA ratings play).
    """
    normalized_query = query.strip().lower()
    counts: Dict[str, int] = {}
    display_case: Dict[str, str] = {}
    for item in pool:
        seen_this_item: set[str] = set()
        for name in item.cast:
            normalized_name = name.strip().lower()
            if not normalized_name or normalized_query not in normalized_name:
                continue
            if normalized_name in seen_this_item:
                continue
            seen_this_item.add(normalized_name)
            counts[normalized_name] = counts.get(normalized_name, 0) + 1
            display_case.setdefault(normalized_name, name.strip())
    ordered = sorted(counts.items(), key=lambda pair: (-pair[1], display_case[pair[0]].lower()))
    return [(display_case[key], count) for key, count in ordered]


IMDB_RATING_MINIMUM = 0.0
IMDB_RATING_MAXIMUM = 10.0


def parse_imdb_rating_bounds(minimum_text: Optional[str], maximum_text: Optional[str]) -> tuple[Optional[float], Optional[float]]:
    """Validate and parse an IMDb Rating filter's minimum/maximum text
    fields (e.g. from a modal) into (minimum, maximum) floats.

    Either or both may be blank/None -- both blank means Any IMDb Rating
    (returns (None, None)). Accepts an IMDb-style rating with up to one
    decimal place, from 0.0 through 10.0 inclusive. Raises ValueError
    with a clear, actionable message for anything else: an unparseable
    number, more than one decimal place, a value outside 0.0-10.0, or a
    minimum greater than a maximum that was also supplied.
    """

    def _parse_one(label: str, text: Optional[str]) -> Optional[float]:
        if text is None or not text.strip():
            return None
        cleaned = text.strip()
        if not re.fullmatch(r"\d+(\.\d)?", cleaned):
            raise ValueError(
                f"{label} must be a number from {IMDB_RATING_MINIMUM:.1f} to {IMDB_RATING_MAXIMUM:.1f} "
                "with at most one decimal place (e.g. 7.0)."
            )
        value = float(cleaned)
        if not (IMDB_RATING_MINIMUM <= value <= IMDB_RATING_MAXIMUM):
            raise ValueError(
                f"{label} must be between {IMDB_RATING_MINIMUM:.1f} and {IMDB_RATING_MAXIMUM:.1f}."
            )
        return value

    minimum = _parse_one("Minimum rating", minimum_text)
    maximum = _parse_one("Maximum rating", maximum_text)
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError("Minimum rating cannot be greater than maximum rating.")
    return minimum, maximum
