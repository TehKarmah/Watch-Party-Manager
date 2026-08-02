"""Refresh already-stored IMDb-derived metadata for existing suggestions
(IMDb Metadata Refresh, reachable from /database manage).

Older suggestions may have been added before WASH started persisting
certain IMDb-derived fields (e.g. cast), or before an OMDb key was ever
configured, or their stored metadata may simply be stale. This module
re-fetches metadata for suggestions that already have a usable persisted
IMDb identifier and merges it onto the existing record -- it never
creates a new suggestion, never moves one between collections, and never
touches any WASH-owned field (status, journey, votes, Discord post
references, Crew Review references, etc.), since every field update goes
through SuggestionService.apply_imdb_metadata_refresh(), which uses
dataclasses.replace() and only ever overrides the specific IMDb-derived
fields this module computes.

This module has no dependency on Discord or bot.py -- it depends only on
SuggestionService and ImdbMetadataService (the exact same OMDb-backed
client /add already uses; no parallel metadata client is introduced).
Discord-specific concerns (posting progress updates, resyncing a
suggestion's public post) are the caller's responsibility, injected here
only as an optional async callback (`post_sync`) so this module stays
fully unit-testable without any Discord objects.

Merge behavior (Section 7): a suggestion's existing field is preserved
whenever the freshly-fetched response does not supply a value for that
field (OMDb returning "N/A", an empty string, or omitting the field
entirely -- see ImdbMetadataService's own None-normalization). A
suggestion's metadata is never blanked out merely because one response
happened to omit a field it previously had a value for.

Request safety (Section 6): fetches are strictly sequential (one IMDb
title at a time, never a concurrent batch), and a single unique IMDb
title is fetched at most once per refresh operation even if many
suggestions -- across one or many collections -- share it; every match
still gets its own independent merge/persist/post-sync outcome. A fetch
that raises or reports failure is retried exactly once after a short
delay (mirroring bot.py's own sync_suggestion_status_embed retry
convention) before being counted as Failed -- never retried indefinitely.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, Dict, List, Optional, Sequence

from watch_party_manager.domain.suggestion_database import SuggestionDatabase
from watch_party_manager.domain.watch_item import MetadataProvider, WatchItem
from watch_party_manager.services.imdb_metadata_service import ImdbMetadataService, ImdbTitleResult
from watch_party_manager.services.suggestion_service import SuggestionService

logger = logging.getLogger(__name__)

# Mirrors bot.py's _STATUS_EMBED_SYNC_MAX_ATTEMPTS/_RETRY_DELAY_SECONDS
# convention: one bounded retry after a short delay, never retried
# indefinitely. A permanent failure (bad ID, no API key configured, title
# not found) simply fails again on the retry at negligible extra cost;
# there is no reliable way to distinguish "temporary" from "permanent"
# from OMDb's response shape alone, so both are treated the same way,
# bounded to a single retry either way.
FETCH_MAX_ATTEMPTS = 2
FETCH_RETRY_DELAY_SECONDS = 0.5


class SuggestionRefreshResult(str, Enum):
    """Per-suggestion outcome of one refresh attempt (Section 7)."""

    REFRESHED = "refreshed"  # metadata changed and persisted successfully.
    UNCHANGED = "unchanged"  # fetched successfully but identical to what was already stored.
    SKIPPED = "skipped"  # no usable persisted IMDb identifier -- never attempted.
    FAILED = "failed"  # lookup, parsing, or persistence failed.


@dataclass
class SuggestionRefreshOutcome:
    """One suggestion's refresh result, plus its post-sync result (Section
    8) when a post-sync callback was supplied and actually attempted.
    """

    watch_item_id: int
    database_id: int
    result: SuggestionRefreshResult
    detail: str = ""
    updated_watch_item: Optional[WatchItem] = None
    # None: post-sync was never attempted (result wasn't REFRESHED, or no
    # post_sync callback was supplied). True: attempted and succeeded.
    # False: attempted and failed -- recorded separately from `result`,
    # since a post-sync failure never rolls back an already-persisted
    # metadata update (Section 8).
    post_synced: Optional[bool] = None


@dataclass
class CollectionRefreshSummary:
    """One collection's totals plus every suggestion's individual outcome,
    for the final per-collection breakdown (Section 10).
    """

    database_id: int
    database_name: str
    processed: int = 0
    refreshed: int = 0
    unchanged: int = 0
    skipped: int = 0
    failed: int = 0
    posts_updated: int = 0
    post_sync_failures: int = 0
    outcomes: List[SuggestionRefreshOutcome] = field(default_factory=list)

    def record(self, outcome: SuggestionRefreshOutcome) -> None:
        self.processed += 1
        self.outcomes.append(outcome)
        if outcome.result is SuggestionRefreshResult.REFRESHED:
            self.refreshed += 1
        elif outcome.result is SuggestionRefreshResult.UNCHANGED:
            self.unchanged += 1
        elif outcome.result is SuggestionRefreshResult.SKIPPED:
            self.skipped += 1
        else:
            self.failed += 1
        if outcome.post_synced is True:
            self.posts_updated += 1
        elif outcome.post_synced is False:
            self.post_sync_failures += 1


@dataclass
class ImdbMetadataRefreshSummary:
    """The overall result of one refresh operation, across every collection
    in scope (one, for Refresh This Collection; many, for Refresh All
    Collections -- always pre-filtered to the current guild by the
    caller, see resolve_refreshable_databases below).
    """

    collections: List[CollectionRefreshSummary] = field(default_factory=list)

    @property
    def collections_processed(self) -> int:
        return len(self.collections)

    @property
    def processed(self) -> int:
        return sum(c.processed for c in self.collections)

    @property
    def refreshed(self) -> int:
        return sum(c.refreshed for c in self.collections)

    @property
    def unchanged(self) -> int:
        return sum(c.unchanged for c in self.collections)

    @property
    def skipped(self) -> int:
        return sum(c.skipped for c in self.collections)

    @property
    def failed(self) -> int:
        return sum(c.failed for c in self.collections)

    @property
    def posts_updated(self) -> int:
        return sum(c.posts_updated for c in self.collections)

    @property
    def post_sync_failures(self) -> int:
        return sum(c.post_sync_failures for c in self.collections)


@dataclass(frozen=True)
class RefreshProgress:
    """A snapshot passed to the caller-supplied on_progress callback after
    every suggestion and at every collection boundary (Section 9) --
    coarsening this into occasional Discord message edits is the caller's
    responsibility (see bot.py), so this module can report as often as is
    actually useful without ever touching Discord's rate limits itself.
    """

    collections_completed: int
    collections_total: int
    current_collection_name: str
    suggestions_processed: int
    suggestions_total: int
    refreshed: int
    unchanged: int
    skipped: int
    failed: int
    posts_updated: int
    post_sync_failures: int
    collection_boundary: bool = False


OnProgress = Callable[[RefreshProgress], Awaitable[None]]
# Returns True if the post was resynced successfully, False if a post
# existed but the sync failed, or None if there was nothing to sync (not
# a failure). See bot.py's SuggestionPostSyncResult for the Discord-level
# three-way result this is expected to be derived from.
OnPostSync = Callable[[WatchItem], Awaitable[Optional[bool]]]


def resolve_refreshable_databases(
    databases: Sequence[SuggestionDatabase], *, guild_id: int
) -> List[SuggestionDatabase]:
    """Filter to active collections belonging to exactly one guild
    (Section 2/13) -- "Refresh All Collections" must never cross a guild
    boundary or include an inactive/removed collection, regardless of
    how many other guilds this WASH process also serves.
    """
    return [database for database in databases if database.guild_id == guild_id and database.active]


class ImdbMetadataRefreshService:
    """Orchestrates one refresh operation across one or more collections."""

    def __init__(
        self,
        suggestion_service: SuggestionService,
        imdb_metadata_service: Optional[ImdbMetadataService] = None,
    ) -> None:
        self._suggestion_service = suggestion_service
        self._imdb_metadata_service = imdb_metadata_service or ImdbMetadataService()

    def eligible_suggestions(self, database_id: int) -> List[WatchItem]:
        """Every suggestion in a collection considered for refresh --
        including archived/retired items, since their stored metadata is
        still worth keeping current (their status is never touched).
        """
        return self._suggestion_service.get_suggestions_for_database(database_id, include_archived=True)

    def has_usable_imdb_identifier(self, watch_item: WatchItem) -> Optional[str]:
        """Return the canonical IMDb title URL this suggestion's stored
        identifier resolves to, or None if it has none usable (Section
        5) -- never a live request, purely local parsing.
        """
        raw = watch_item.metadata_ids.get(MetadataProvider.IMDB)
        if not raw:
            return None
        return self._imdb_metadata_service.normalize_imdb_url(raw)

    def count_refreshable(self, database_id: int) -> tuple[int, int]:
        """Return (usable_count, total_count) for one collection without
        making any network requests -- used to show the confirmation
        screen's "N suggestions eligible for refresh" line.
        """
        items = self.eligible_suggestions(database_id)
        usable = sum(1 for item in items if self.has_usable_imdb_identifier(item) is not None)
        return usable, len(items)

    async def refresh_databases(
        self,
        databases: Sequence[SuggestionDatabase],
        *,
        on_progress: Optional[OnProgress] = None,
        post_sync: Optional[OnPostSync] = None,
    ) -> ImdbMetadataRefreshSummary:
        """Refresh every eligible suggestion in every given database, in
        order, one at a time.

        Args:
            databases: The collections to refresh, already scoped to the
                current guild and filtered to active-only (see
                resolve_refreshable_databases) -- this method trusts its
                caller's scoping rather than re-deriving it, so it can be
                unit-tested against an arbitrary explicit list.
            on_progress: Optional async callback invoked after every
                suggestion and at each collection boundary.
            post_sync: Optional async callback invoked once per
                successfully REFRESHED suggestion (never for Unchanged/
                Skipped/Failed ones, since there is nothing new to show)
                to resync its Discord post. Its bool/None result is
                folded into that suggestion's outcome and this
                operation's posts_updated/post_sync_failures totals.

        Returns:
            The full summary, with one CollectionRefreshSummary per
            input database in the same order.
        """
        summary = ImdbMetadataRefreshSummary()
        per_database_items: Dict[int, List[WatchItem]] = {
            database.database_id: self.eligible_suggestions(database.database_id) for database in databases
        }
        suggestions_total = sum(len(items) for items in per_database_items.values())
        suggestions_processed = 0
        # Spans the entire operation (every collection in scope), so a
        # title shared across collections is fetched at most once total,
        # not once per collection (Section 5).
        fetch_cache: Dict[str, ImdbTitleResult] = {}

        async def report(*, collection_boundary: bool) -> None:
            if on_progress is None:
                return
            await on_progress(
                RefreshProgress(
                    collections_completed=collections_completed,
                    collections_total=len(databases),
                    current_collection_name=database.name,
                    suggestions_processed=suggestions_processed,
                    suggestions_total=suggestions_total,
                    refreshed=summary.refreshed,
                    unchanged=summary.unchanged,
                    skipped=summary.skipped,
                    failed=summary.failed,
                    posts_updated=summary.posts_updated,
                    post_sync_failures=summary.post_sync_failures,
                    collection_boundary=collection_boundary,
                )
            )

        collections_completed = 0
        for database in databases:
            collection_summary = CollectionRefreshSummary(
                database_id=database.database_id, database_name=database.name
            )
            summary.collections.append(collection_summary)

            for watch_item in per_database_items[database.database_id]:
                outcome = await self.refresh_one(watch_item, fetch_cache, post_sync)
                collection_summary.record(outcome)
                suggestions_processed += 1
                await report(collection_boundary=False)

            collections_completed += 1
            await report(collection_boundary=True)

        return summary

    async def refresh_one(
        self,
        watch_item: WatchItem,
        fetch_cache: Dict[str, ImdbTitleResult],
        post_sync: Optional[OnPostSync],
    ) -> SuggestionRefreshOutcome:
        """Refresh exactly one suggestion's IMDb-derived metadata --
        public so IMDb Metadata Recovery (services/imdb_metadata_recovery_
        service.py) can reuse this exact logic immediately after it saves
        a newly-matched IMDb identifier, rather than duplicating any
        fetch/merge/persist/post-sync logic (Section 6: "reuse the
        existing refresh architecture"). `fetch_cache` may be shared
        across an entire recovery run too, so a title matched to more
        than one recovered suggestion is still only fetched once.
        """
        canonical_url = self.has_usable_imdb_identifier(watch_item)
        if canonical_url is None:
            return SuggestionRefreshOutcome(
                watch_item.id,
                watch_item.database_id,
                SuggestionRefreshResult.SKIPPED,
                detail="no usable persisted IMDb identifier",
            )

        if canonical_url in fetch_cache:
            fresh = fetch_cache[canonical_url]
        else:
            fresh = await self._fetch_with_retry(canonical_url)
            fetch_cache[canonical_url] = fresh

        if not fresh.success:
            logger.warning(
                "IMDb metadata refresh lookup failed: guild=%s database=%s suggestion=%s imdb=%s reason=%s",
                watch_item.guild_id,
                watch_item.database_id,
                watch_item.id,
                canonical_url,
                fresh.error_message,
            )
            return SuggestionRefreshOutcome(
                watch_item.id,
                watch_item.database_id,
                SuggestionRefreshResult.FAILED,
                detail=fresh.error_message or "IMDb lookup failed",
            )

        merged = _merge_fields(watch_item, fresh)
        if not _fields_changed(watch_item, merged):
            return SuggestionRefreshOutcome(
                watch_item.id, watch_item.database_id, SuggestionRefreshResult.UNCHANGED, updated_watch_item=watch_item
            )

        result = self._suggestion_service.apply_imdb_metadata_refresh(watch_item.id, **merged)
        if not result.success:
            logger.warning(
                "IMDb metadata refresh could not be persisted: guild=%s database=%s suggestion=%s imdb=%s reason=%s",
                watch_item.guild_id,
                watch_item.database_id,
                watch_item.id,
                canonical_url,
                result.message,
            )
            return SuggestionRefreshOutcome(
                watch_item.id, watch_item.database_id, SuggestionRefreshResult.FAILED, detail=result.message
            )

        post_synced: Optional[bool] = None
        if post_sync is not None:
            try:
                post_synced = await post_sync(result.watch_item)
            except Exception:
                logger.warning(
                    "IMDb metadata refresh post-sync raised unexpectedly: guild=%s database=%s suggestion=%s",
                    watch_item.guild_id,
                    watch_item.database_id,
                    watch_item.id,
                    exc_info=True,
                )
                post_synced = False

        return SuggestionRefreshOutcome(
            watch_item.id,
            watch_item.database_id,
            SuggestionRefreshResult.REFRESHED,
            updated_watch_item=result.watch_item,
            post_synced=post_synced,
        )

    async def _fetch_with_retry(self, canonical_url: str) -> ImdbTitleResult:
        result = await self._imdb_metadata_service.resolve_title(canonical_url)
        for _ in range(FETCH_MAX_ATTEMPTS - 1):
            if result.success:
                return result
            await asyncio.sleep(FETCH_RETRY_DELAY_SECONDS)
            result = await self._imdb_metadata_service.resolve_title(canonical_url)
        return result


def _merge_fields(watch_item: WatchItem, fresh: ImdbTitleResult) -> dict:
    """Compute the FINAL field values to persist: the freshly-fetched
    value when the provider supplied one, otherwise the suggestion's
    existing value, never a blank/None overwrite of a previously valid
    field (Section 7's merge rule).
    """
    return {
        "title": fresh.title or watch_item.title,
        "imdb_url": fresh.imdb_url or watch_item.metadata_ids.get(MetadataProvider.IMDB),
        "runtime_minutes": fresh.runtime_minutes if fresh.runtime_minutes is not None else watch_item.runtime_minutes,
        "genres": fresh.genres if fresh.genres else watch_item.genres,
        "description": fresh.plot if fresh.plot else watch_item.description,
        "content_rating": fresh.content_rating if fresh.content_rating else watch_item.content_rating,
        "director": fresh.director if fresh.director else watch_item.director,
        "imdb_rating": fresh.imdb_rating if fresh.imdb_rating else watch_item.imdb_rating,
        "poster_url": fresh.poster_url if fresh.poster_url else watch_item.poster_url,
        "cast": fresh.cast if fresh.cast else watch_item.cast,
    }


def _fields_changed(watch_item: WatchItem, merged: dict) -> bool:
    """Whether the merged field values differ from what's already stored
    -- used to distinguish Unchanged from Refreshed (Section 7). The
    canonical IMDb URL is deliberately excluded from this comparison:
    it's always re-applied (harmlessly, since it should already match),
    but a purely cosmetic re-normalization of it should never by itself
    count as a change worth persisting or reporting.
    """
    return (
        merged["title"] != watch_item.title
        or merged["runtime_minutes"] != watch_item.runtime_minutes
        or merged["genres"] != watch_item.genres
        or merged["description"] != watch_item.description
        or merged["content_rating"] != watch_item.content_rating
        or merged["director"] != watch_item.director
        or merged["imdb_rating"] != watch_item.imdb_rating
        or merged["poster_url"] != watch_item.poster_url
        or merged["cast"] != watch_item.cast
    )


__all__ = [
    "FETCH_MAX_ATTEMPTS",
    "FETCH_RETRY_DELAY_SECONDS",
    "SuggestionRefreshResult",
    "SuggestionRefreshOutcome",
    "CollectionRefreshSummary",
    "ImdbMetadataRefreshSummary",
    "RefreshProgress",
    "OnProgress",
    "OnPostSync",
    "resolve_refreshable_databases",
    "ImdbMetadataRefreshService",
]
