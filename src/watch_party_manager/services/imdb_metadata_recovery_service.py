"""Match older suggestions missing a usable IMDb identifier to the
correct IMDb title, save that identifier, and immediately refresh their
IMDb-derived metadata (IMDb Metadata Recovery, reachable from
`/database manage`).

Refresh IMDb Metadata (services/imdb_metadata_refresh_service.py) only
ever touches suggestions that ALREADY have a usable persisted IMDb
identifier -- a suggestion added before IMDb linking existed, or added by
plain title with no link, has no way back into that workflow. This
module is the answer: it finds suggestions with no usable identifier,
searches OMDb by their stored title/year, and -- only once a WASH Crew
member explicitly approves a specific candidate -- saves that identifier
and reuses ImdbMetadataRefreshService.refresh_one() for the exact same
fetch/merge/persist/post-sync logic Refresh IMDb Metadata already uses,
so cast, IMDb rating, MPAA rating, runtime, genres, poster, and plot are
populated immediately rather than requiring a second, separate pass.

This module has no dependency on Discord or bot.py, same as the refresh
service it wraps. Unlike Refresh IMDb Metadata's fully automated batch
loop, recovery is inherently interactive -- every proposed match must be
explicitly approved by a person -- so this module exposes discrete
building blocks (missing_suggestions/count_missing/find_candidates/
accept_match) rather than one long-running "process everything" method;
bot.py's own recovery session (a mutable state dict captured by nested
closures, the same established pattern used by /random watch's and
Custom Vote Filters' own multi-screen sessions) drives one suggestion at
a time, in response to each Discord interaction.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Sequence

from watch_party_manager.domain.suggestion_database import SuggestionDatabase
from watch_party_manager.domain.watch_item import WatchItem
from watch_party_manager.services.imdb_metadata_refresh_service import (
    ImdbMetadataRefreshService,
    OnPostSync,
    SuggestionRefreshResult,
    resolve_refreshable_databases,
)
from watch_party_manager.services.imdb_metadata_service import ImdbMetadataService, ImdbSearchMatch, ImdbTitleResult
from watch_party_manager.services.suggestion_service import SuggestionService

logger = logging.getLogger(__name__)

# Mirrors imdb_metadata_refresh_service's own bounded-retry convention --
# one short retry before a search is counted as a technical failure.
SEARCH_MAX_ATTEMPTS = 2
SEARCH_RETRY_DELAY_SECONDS = 0.5


class RecoveryResult(str, Enum):
    """Per-suggestion outcome of one recovery attempt (Section 9)."""

    MATCHED = "matched"  # an IMDb identifier was found, approved, and saved.
    SKIPPED = "skipped"  # Crew explicitly skipped (no good match, or chose not to link one).
    FAILED = "failed"  # a technical failure (search or persistence) prevented recovery.
    CANCELLED = "cancelled"  # the whole recovery run was cancelled before this suggestion was reached.


@dataclass
class RecoverySuggestionOutcome:
    """One suggestion's recovery result, plus its post-sync result
    (mirrors SuggestionRefreshOutcome's own shape/rationale) when a
    match was actually accepted and refreshed.
    """

    watch_item_id: int
    database_id: int
    result: RecoveryResult
    detail: str = ""
    updated_watch_item: Optional[WatchItem] = None
    post_synced: Optional[bool] = None


@dataclass
class CollectionRecoverySummary:
    """One collection's totals plus every suggestion's individual
    outcome, for the final per-collection breakdown (Section 9).
    """

    database_id: int
    database_name: str
    processed: int = 0
    matched: int = 0
    skipped: int = 0
    failed: int = 0
    cancelled: int = 0
    posts_updated: int = 0
    post_sync_failures: int = 0
    outcomes: List[RecoverySuggestionOutcome] = field(default_factory=list)

    def record(self, outcome: RecoverySuggestionOutcome) -> None:
        self.processed += 1
        self.outcomes.append(outcome)
        if outcome.result is RecoveryResult.MATCHED:
            self.matched += 1
        elif outcome.result is RecoveryResult.SKIPPED:
            self.skipped += 1
        elif outcome.result is RecoveryResult.CANCELLED:
            self.cancelled += 1
        else:
            self.failed += 1
        if outcome.post_synced is True:
            self.posts_updated += 1
        elif outcome.post_synced is False:
            self.post_sync_failures += 1


@dataclass
class ImdbMetadataRecoverySummary:
    """The overall result of one recovery run, across every collection in
    scope (mirrors ImdbMetadataRefreshSummary's own shape).
    """

    collections: List[CollectionRecoverySummary] = field(default_factory=list)

    @property
    def collections_processed(self) -> int:
        return len(self.collections)

    @property
    def processed(self) -> int:
        return sum(collection.processed for collection in self.collections)

    @property
    def matched(self) -> int:
        return sum(collection.matched for collection in self.collections)

    @property
    def skipped(self) -> int:
        return sum(collection.skipped for collection in self.collections)

    @property
    def failed(self) -> int:
        return sum(collection.failed for collection in self.collections)

    @property
    def cancelled(self) -> int:
        return sum(collection.cancelled for collection in self.collections)

    @property
    def posts_updated(self) -> int:
        return sum(collection.posts_updated for collection in self.collections)

    @property
    def post_sync_failures(self) -> int:
        return sum(collection.post_sync_failures for collection in self.collections)


@dataclass(frozen=True)
class CandidateSearch:
    """One search attempt's outcome -- everything bot.py needs to render
    the per-suggestion Crew Review screen (Section 4/5): zero matches,
    one, or several, a technical failure, or a note that the stored year
    didn't match anything so these results are title-only (Section 10).
    """

    matches: tuple[ImdbSearchMatch, ...] = ()
    technical_failure: bool = False
    error_message: Optional[str] = None
    year_mismatch_note: Optional[str] = None
    searched_title: str = ""
    searched_year: Optional[str] = None


def resolve_recoverable_databases(
    databases: Sequence[SuggestionDatabase], *, guild_id: int
) -> List[SuggestionDatabase]:
    """Same guild/active scoping Refresh IMDb Metadata uses (Section 2) --
    reused directly rather than reimplemented, so "all collections" can
    never mean something subtly different between the two workflows.
    """
    return resolve_refreshable_databases(databases, guild_id=guild_id)


class ImdbMetadataRecoveryService:
    """Discovery, OMDb search/matching, and save-then-refresh for
    suggestions missing a usable IMDb identifier.
    """

    def __init__(
        self,
        suggestion_service: SuggestionService,
        imdb_metadata_service: Optional[ImdbMetadataService] = None,
    ) -> None:
        self._suggestion_service = suggestion_service
        self._imdb_metadata_service = imdb_metadata_service or ImdbMetadataService()
        # Composition, not duplication -- reuses eligible_suggestions(),
        # has_usable_imdb_identifier(), and refresh_one() exactly as
        # Refresh IMDb Metadata does (Section 6).
        self._refresh_service = ImdbMetadataRefreshService(suggestion_service, self._imdb_metadata_service)

    def missing_suggestions(self, database_id: int) -> List[WatchItem]:
        """Every suggestion in a collection with no usable, already-
        persisted IMDb identifier (Section 3) -- recomputed fresh on
        every call from current data, never from a stored "already
        scanned" flag, so a suggestion skipped in an earlier recovery run
        still appears here later (Section 11: nothing is permanently
        suppressed).
        """
        return [
            item
            for item in self._refresh_service.eligible_suggestions(database_id)
            if self._refresh_service.has_usable_imdb_identifier(item) is None
        ]

    def count_missing(self, database_id: int) -> tuple[int, int]:
        """(missing_count, total_count) with zero network requests, for
        the confirmation screen (mirrors count_refreshable's shape).
        """
        items = self._refresh_service.eligible_suggestions(database_id)
        missing = sum(1 for item in items if self._refresh_service.has_usable_imdb_identifier(item) is None)
        return missing, len(items)

    async def find_candidates(self, title: str, year: Optional[int] = None) -> CandidateSearch:
        """Search OMDb by title, using the stored/typed year when known
        (Section 10: "use both title and year whenever possible"). If a
        year-scoped search finds nothing, falls back to a title-only
        search rather than silently concluding "no match" -- but flags
        every candidate that comes back from that fallback with a year-
        mismatch note, so Crew is never asked to silently accept a
        different year than what's stored ("do not silently choose one
        ... require Crew selection").
        """
        year_text = str(year) if year is not None else None
        if year_text is None:
            result = await self._search_with_retry(title, year=None)
            if not result.success:
                return CandidateSearch(
                    technical_failure=True, error_message=result.error_message, searched_title=title
                )
            return CandidateSearch(matches=result.matches, searched_title=title)

        primary = await self._search_with_retry(title, year=year_text)
        if not primary.success:
            return CandidateSearch(
                technical_failure=True, error_message=primary.error_message, searched_title=title, searched_year=year_text
            )
        if primary.matches:
            return CandidateSearch(matches=primary.matches, searched_title=title, searched_year=year_text)

        fallback = await self._search_with_retry(title, year=None)
        if not fallback.success:
            return CandidateSearch(
                technical_failure=True, error_message=fallback.error_message, searched_title=title, searched_year=year_text
            )
        note = (
            f"No results matched the stored year ({year_text}) -- these results are for the title only. "
            "Please verify the year before accepting."
            if fallback.matches
            else None
        )
        return CandidateSearch(
            matches=fallback.matches, year_mismatch_note=note, searched_title=title, searched_year=year_text
        )

    async def accept_match(
        self,
        watch_item: WatchItem,
        match: ImdbSearchMatch,
        *,
        fetch_cache: Dict[str, ImdbTitleResult],
        post_sync: Optional[OnPostSync] = None,
    ) -> RecoverySuggestionOutcome:
        """Persist `match`'s IMDb identifier onto `watch_item`, then
        immediately reuse ImdbMetadataRefreshService.refresh_one() to
        populate cast/rating/runtime/genres/poster/plot right away
        (Section 6). `fetch_cache` may be shared across an entire
        recovery run, so more than one recovered suggestion matched to
        the same title is still only fetched once.

        The identifier is saved and reported MATCHED even if the
        immediate enrichment step itself fails -- a future ordinary
        Refresh IMDb Metadata run (or the next Recovery scan, since a
        suggestion with an identifier is no longer "missing") will pick
        it up. Recovery's core contract is attaching the identifier
        (Section 3), not guaranteeing same-session enrichment.
        """
        save_result = self._suggestion_service.set_imdb_identifier(watch_item.id, match.imdb_url)
        if not save_result.success:
            logger.warning(
                "IMDb metadata recovery could not save an identifier: guild=%s database=%s suggestion=%s imdb=%s reason=%s",
                watch_item.guild_id,
                watch_item.database_id,
                watch_item.id,
                match.imdb_url,
                save_result.message,
            )
            return RecoverySuggestionOutcome(
                watch_item.id, watch_item.database_id, RecoveryResult.FAILED, detail=save_result.message
            )

        assert save_result.watch_item is not None
        refresh_outcome = await self._refresh_service.refresh_one(save_result.watch_item, fetch_cache, post_sync)
        detail = f"Matched to {match.title}" + (f" ({match.year})" if match.year else "")
        if refresh_outcome.result is SuggestionRefreshResult.FAILED:
            detail += (
                " -- IMDb identifier saved, but immediate metadata enrichment failed; "
                "a future Refresh IMDb Metadata run will pick it up."
            )
        return RecoverySuggestionOutcome(
            watch_item.id,
            watch_item.database_id,
            RecoveryResult.MATCHED,
            detail=detail,
            updated_watch_item=refresh_outcome.updated_watch_item or save_result.watch_item,
            post_synced=refresh_outcome.post_synced,
        )

    async def _search_with_retry(self, title: str, *, year: Optional[str]):
        result = await self._imdb_metadata_service.search_titles(title, year=year)
        for _ in range(SEARCH_MAX_ATTEMPTS - 1):
            if result.success:
                return result
            await asyncio.sleep(SEARCH_RETRY_DELAY_SECONDS)
            result = await self._imdb_metadata_service.search_titles(title, year=year)
        return result


__all__ = [
    "SEARCH_MAX_ATTEMPTS",
    "SEARCH_RETRY_DELAY_SECONDS",
    "RecoveryResult",
    "RecoverySuggestionOutcome",
    "CollectionRecoverySummary",
    "ImdbMetadataRecoverySummary",
    "CandidateSearch",
    "resolve_recoverable_databases",
    "ImdbMetadataRecoveryService",
]
