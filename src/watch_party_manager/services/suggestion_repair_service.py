"""Repair malformed or legacy watch-item suggestions."""

from __future__ import annotations

from dataclasses import dataclass

from watch_party_manager.domain.watch_item import MetadataProvider, WatchItem
from watch_party_manager.services.imdb_metadata_service import ImdbMetadataService
from watch_party_manager.services.suggestion_input_service import SuggestionInputService
from watch_party_manager.services.suggestion_service import SuggestionService

_BAD_TITLES = {"javascript is disabled"}


@dataclass(frozen=True, slots=True)
class SuggestionRepairReport:
    scanned: int = 0
    repaired: int = 0
    removed: int = 0
    failed: int = 0
    unchanged: int = 0

    def format_message(self) -> str:
        return (
            "**Suggestion Repair Complete**\n"
            f"Scanned: {self.scanned}\n"
            f"Repaired: {self.repaired}\n"
            f"Removed: {self.removed}\n"
            f"Failed: {self.failed}\n"
            f"Unchanged: {self.unchanged}"
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

    async def repair_all(self) -> SuggestionRepairReport:
        scanned = repaired = removed = failed = unchanged = 0

        for item in list(self._suggestion_service.get_suggestions()):
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
        )

    def _repair_source_url(self, item: WatchItem) -> str | None:
        if ImdbMetadataService.is_imdb_title_url(item.title):
            return item.title
        imdb_url = item.metadata_ids.get(MetadataProvider.IMDB)
        if item.title.strip().casefold() in _BAD_TITLES and imdb_url:
            return imdb_url
        return None
