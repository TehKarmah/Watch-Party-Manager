"""Formatting helpers for role-aware suggestion list views."""

from __future__ import annotations

from enum import Enum
from typing import Iterable, Optional

from watch_party_manager.domain.suggestion_database import SuggestionDatabase
from watch_party_manager.domain.watch_item import MetadataProvider, WatchItem


class SuggestionListView(str, Enum):
    """Supported presentation modes for the ``/list`` command."""

    STANDARD = "standard"
    CREW = "crew"

    @classmethod
    def parse(cls, value: str | None) -> "SuggestionListView":
        """Parse a user-supplied view name.

        ``None`` and blank strings use the standard member view.
        """
        if value is None or not value.strip():
            return cls.STANDARD
        normalized = value.strip().casefold()
        try:
            return cls(normalized)
        except ValueError as exc:
            valid = ", ".join(view.value for view in cls)
            raise ValueError(f"List view must be one of: {valid}.") from exc


class SuggestionListFormatter:
    """Build consistent standard and WASH Crew suggestion-list output."""

    def format(
        self,
        watch_items: Iterable[WatchItem],
        database: SuggestionDatabase,
        view: SuggestionListView = SuggestionListView.STANDARD,
    ) -> str:
        items = list(watch_items)
        if not items:
            return f'"{database.name}" is currently empty.'

        heading = f"{database.name} Watch Items ({len(items)})"
        lines = [heading, ""]
        if view is SuggestionListView.CREW:
            lines.extend(self._format_crew_item(item) for item in items)
        else:
            lines.extend(self._format_standard_item(item) for item in items)
        return "\n".join(lines)

    @staticmethod
    def _message_url(item: WatchItem) -> Optional[str]:
        if item.guild_id is None or item.channel_id is None or item.message_id is None:
            return None
        return (
            "https://discord.com/channels/"
            f"{item.guild_id}/{item.channel_id}/{item.message_id}"
        )

    def _format_standard_item(self, item: WatchItem) -> str:
        message_url = self._message_url(item)
        if message_url is None:
            return f"- {item.title}"
        return f"- {item.title} | [Original suggestion]({message_url})"

    def _format_crew_item(self, item: WatchItem) -> str:
        reference = f"#{item.id}" if item.id is not None else "Unassigned"
        details = [
            f"**{reference} · {item.title}**",
            f"Status: {item.status.value.replace('_', ' ').title()}",
            f"Media type: {item.media_type.value.replace('_', ' ').title()}",
        ]

        imdb_url = item.metadata_ids.get(MetadataProvider.IMDB)
        if imdb_url:
            details.append(f"IMDb: {imdb_url}")

        message_url = self._message_url(item)
        if message_url:
            details.append(f"[Original suggestion]({message_url})")
        else:
            details.append("Original suggestion: unavailable")

        return "\n".join(details) + "\n"
