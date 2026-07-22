"""FR-033A: duplicate detection across active, archived, and watched suggestions.

Pure, Discord-free logic reused by both /add and /edit_suggestion. Never
guesses: a title match where either side's release year is unknown is
reported as a "possible" duplicate requiring explicit WASH Crew
confirmation, never silently treated as unique or as a hard block.

Archive "reason" (rejected via "I WILL NOT WATCH" vs. archived some
other way) is inferred entirely from data WatchItem/WatchItemJourney
already track -- see categorize_watch_item(). No new archive-reason
field is invented, per this milestone's own instruction not to invent
archive states the repository doesn't support.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from watch_party_manager.domain.watch_item import MetadataProvider, WatchItem, WatchItemStatus

_WHITESPACE_PATTERN = re.compile(r"\s+")
_IMDB_ID_PATTERN = re.compile(r"(tt\d+)", re.IGNORECASE)


def normalize_title_for_comparison(title: str) -> str:
    """Case- and whitespace-insensitive title key for duplicate matching.

    Deliberately narrow: only case and whitespace (leading/trailing and
    repeated internal) are normalized. Punctuation and wording are left
    alone -- collapsing those further risks merging genuinely different
    titles.
    """
    collapsed = _WHITESPACE_PATTERN.sub(" ", title.strip())
    return collapsed.casefold()


def extract_imdb_id(imdb_url: Optional[str]) -> Optional[str]:
    """Pull the tt-prefixed IMDb title ID out of a URL, or None."""
    if not imdb_url:
        return None
    match = _IMDB_ID_PATTERN.search(imdb_url)
    return match.group(1).lower() if match else None


class DuplicateMatchCategory(str, Enum):
    """Which bucket the matched existing item currently falls into."""

    ACTIVE = "active"
    ARCHIVED_REJECTED = "archived_rejected"
    ARCHIVED_OTHER = "archived_other"
    WATCHED = "watched"


class DuplicateMatchKind(str, Enum):
    """Why the match was made -- determines definite-block vs. possible-warn."""

    IMDB = "imdb"
    TITLE_AND_YEAR = "title_and_year"
    TITLE_ONLY = "title_only"


@dataclass(frozen=True, slots=True)
class DuplicateMatch:
    """One existing item that matched a candidate suggestion."""

    watch_item: WatchItem
    category: DuplicateMatchCategory
    kind: DuplicateMatchKind

    @property
    def is_definite(self) -> bool:
        return self.kind is not DuplicateMatchKind.TITLE_ONLY


@dataclass(frozen=True, slots=True)
class DuplicateCheckResult:
    """Every match found for one candidate suggestion, in check order."""

    matches: tuple[DuplicateMatch, ...] = ()

    @property
    def has_matches(self) -> bool:
        return bool(self.matches)

    @property
    def definite_matches(self) -> tuple[DuplicateMatch, ...]:
        return tuple(match for match in self.matches if match.is_definite)

    @property
    def has_definite_match(self) -> bool:
        return bool(self.definite_matches)

    @property
    def has_possible_only(self) -> bool:
        """True when every match found is a title-only "possible" match."""
        return self.has_matches and not self.has_definite_match


def categorize_watch_item(watch_item: WatchItem) -> DuplicateMatchCategory:
    """Classify an existing item for duplicate-detection/re-suggestion purposes.

    An ARCHIVED item with at least one recorded "I will not watch"
    rejection is treated as rejection-archived; an ARCHIVED item with
    none was archived some other way (e.g. WASH Crew directly archiving
    it via /remove).
    """
    if watch_item.status is WatchItemStatus.WATCHED:
        return DuplicateMatchCategory.WATCHED
    if watch_item.status is WatchItemStatus.ARCHIVED:
        if watch_item.journey.rejected_by_discord_user_ids:
            return DuplicateMatchCategory.ARCHIVED_REJECTED
        return DuplicateMatchCategory.ARCHIVED_OTHER
    return DuplicateMatchCategory.ACTIVE


def find_duplicates(
    *,
    title: str,
    release_year: Optional[int],
    imdb_url: Optional[str],
    existing_items: List[WatchItem],
    exclude_id: Optional[int] = None,
) -> DuplicateCheckResult:
    """Find duplicate/possible-duplicate matches within one database's items.

    Args:
        title: The candidate suggestion's title (not yet normalized).
        release_year: The candidate's release year, if known.
        imdb_url: The candidate's canonical IMDb URL, if any.
        existing_items: Every item already in the target database --
            active, archived, and watched alike. Callers should pass
            SuggestionService.get_suggestions_for_database(database_id,
            include_archived=True), unfiltered by status, so every
            required category (Section 2) is actually checked.
        exclude_id: The candidate's own ID, when checking an edit against
            its own database (a suggestion never duplicates itself).

    Returns:
        A DuplicateCheckResult listing every match. An IMDb ID match
        always wins over a title-based match for the same existing item
        (only one DuplicateMatch is ever recorded per existing item).
    """
    candidate_key = normalize_title_for_comparison(title)
    candidate_imdb_id = extract_imdb_id(imdb_url)

    matches: list[DuplicateMatch] = []
    for existing in existing_items:
        if exclude_id is not None and existing.id == exclude_id:
            continue

        existing_imdb_id = extract_imdb_id(existing.metadata_ids.get(MetadataProvider.IMDB))
        if (
            candidate_imdb_id is not None
            and existing_imdb_id is not None
            and candidate_imdb_id == existing_imdb_id
        ):
            matches.append(DuplicateMatch(existing, categorize_watch_item(existing), DuplicateMatchKind.IMDB))
            continue

        existing_key = normalize_title_for_comparison(existing.title)
        if existing_key != candidate_key:
            continue

        if release_year is not None and existing.release_year is not None:
            if release_year == existing.release_year:
                matches.append(
                    DuplicateMatch(existing, categorize_watch_item(existing), DuplicateMatchKind.TITLE_AND_YEAR)
                )
            # Different, both-known years: same title but a different
            # release (e.g. a remake). Not a duplicate; no match recorded.
            continue

        # At least one side's year is unknown -- never guess whether
        # these are the same title.
        matches.append(DuplicateMatch(existing, categorize_watch_item(existing), DuplicateMatchKind.TITLE_ONLY))

    return DuplicateCheckResult(tuple(matches))
