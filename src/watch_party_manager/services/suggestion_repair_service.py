"""Repair malformed or legacy watch-item suggestions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional, Sequence

from watch_party_manager.domain.watch_item import MetadataProvider, WatchItem
from watch_party_manager.services.imdb_metadata_service import ImdbMetadataService
from watch_party_manager.services.suggestion_input_service import SuggestionInputService
from watch_party_manager.services.suggestion_service import SuggestionService

_BAD_TITLES = {"javascript is disabled"}

# Called once per successfully repaired suggestion (never for a removed
# one -- there's no post left to sync) so bot.py can resync its existing
# public post, the same optional-callback shape
# ImdbMetadataRefreshService.refresh_databases() already uses. Returns
# True/False once actually attempted, or None when the suggestion has no
# existing post to sync at all.
OnPostSync = Callable[[WatchItem], Awaitable[Optional[bool]]]


def resolve_repairable_suggestions(suggestions: Sequence[WatchItem], *, guild_id: int) -> List[WatchItem]:
    """Filter to suggestions belonging to exactly one guild -- mirrors
    imdb_metadata_refresh_service.resolve_refreshable_databases(): repair
    must never scan or modify a suggestion belonging to a different
    guild, regardless of how many other guilds this WASH process also
    serves.
    """
    return [item for item in suggestions if item.guild_id == guild_id]


@dataclass(frozen=True, slots=True)
class SuggestionRepairReport:
    scanned: int = 0
    repaired: int = 0
    removed: int = 0
    failed: int = 0
    unchanged: int = 0
    posts_updated: int = 0
    post_sync_failures: int = 0

    def format_message(self) -> str:
        return (
            "**Suggestion Repair Complete**\n"
            f"Scanned: {self.scanned}\n"
            f"Repaired: {self.repaired}\n"
            f"Removed: {self.removed}\n"
            f"Failed: {self.failed}\n"
            f"Unchanged: {self.unchanged}\n"
            f"Suggestion posts updated: {self.posts_updated}\n"
            f"Suggestion post sync failures: {self.post_sync_failures}"
        )


class SuggestionRepairService:
    """Repair legacy IMDb-link titles and known malformed suggestions."""

    def __init__(
        self,
        suggestion_service: SuggestionService,
        input_service: SuggestionInputService,
    ) -> None:
        self._suggestion_service = suggestion_service
        self._input_service = input_service

    async def repair_all(
        self, guild_id: int, *, post_sync: Optional[OnPostSync] = None
    ) -> SuggestionRepairReport:
        """Repair every malformed/legacy suggestion in one guild's collections.

        Args:
            guild_id: The guild whose suggestions to repair -- required,
                not optional, so a caller can never accidentally repair
                across every guild this WASH process serves (see
                resolve_repairable_suggestions, applied before any
                repair work begins).
            post_sync: First-Time UX Polish (`/maintenance repair`
                confirmation): when supplied, called once per
                successfully repaired suggestion (its title/IMDb link
                already updated in place) so its existing public post --
                if it has one -- can be resynced to show the corrected
                data, the same optional-callback pattern
                ImdbMetadataRefreshService already uses. None (every
                existing caller/test) skips post-sync entirely,
                unchanged from before. Never called for a removed
                suggestion -- there's no post left to sync.
        """
        scanned = repaired = removed = failed = unchanged = 0
        posts_updated = post_sync_failures = 0

        candidates = resolve_repairable_suggestions(self._suggestion_service.get_suggestions(), guild_id=guild_id)
        for item in candidates:
            scanned += 1
            source_url = self._repair_source_url(item)
            if source_url is None:
                if item.title.strip().casefold() in _BAD_TITLES:
                    if self._suggestion_service.remove_suggestion_by_id(item.id):
                        removed += 1
                    else:
                        failed += 1
                else:
                    unchanged += 1
                continue

            resolved = await self._input_service.resolve(source_url)
            if not resolved.success or not resolved.title or not resolved.imdb_url:
                failed += 1
                continue

            update = self._suggestion_service.update_suggestion_identity(
                suggestion_id=item.id,
                title=resolved.title,
                imdb_url=resolved.imdb_url,
            )
            if update == "updated":
                repaired += 1
                if post_sync is not None:
                    synced = await post_sync(item)
                    if synced is True:
                        posts_updated += 1
                    elif synced is False:
                        post_sync_failures += 1
            elif update == "removed_duplicate":
                removed += 1
            else:
                failed += 1

        return SuggestionRepairReport(
            scanned=scanned,
            repaired=repaired,
            removed=removed,
            failed=failed,
            unchanged=unchanged,
            posts_updated=posts_updated,
            post_sync_failures=post_sync_failures,
        )

    def _repair_source_url(self, item: WatchItem) -> str | None:
        if ImdbMetadataService.is_imdb_title_url(item.title):
            return item.title
        imdb_url = item.metadata_ids.get(MetadataProvider.IMDB)
        if item.title.strip().casefold() in _BAD_TITLES and imdb_url:
            return imdb_url
        return None
