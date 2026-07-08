"""Core engine module for Watch Party Manager.

This module intentionally remains lightweight for FR-001 and references the
shared domain model without introducing Discord-specific behavior.
"""

from watch_party_manager.domain import WatchItem


class WatchPartyEngine:
    """Central application engine for Watch Party Manager."""

    def __init__(self) -> None:
        self._watch_items: list[WatchItem] = []

    def register_watch_item(self, watch_item: WatchItem) -> WatchItem:
        self._watch_items.append(watch_item)
        return watch_item
