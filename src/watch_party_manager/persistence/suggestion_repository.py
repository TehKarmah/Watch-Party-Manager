"""JSON-backed persistence for movie suggestions."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Union

from watch_party_manager.domain.watch_item import MediaType, MetadataProvider, WatchItem

logger = logging.getLogger(__name__)

# Default on-disk location for the suggestion list. Sits under the project's
# conventional "data/" directory (already used for other runtime data; see
# .gitignore) so it's easy to find and inspect during development.
DEFAULT_SUGGESTIONS_PATH = Path("data/suggestions.json")

# The ID to hand out first when no suggestions have ever been persisted.
FIRST_SUGGESTION_ID = 1


@dataclass
class LoadResult:
    """What comes back from loading the suggestion file.

    next_id is the ID to assign to the next new suggestion. It is tracked
    separately from the loaded items so that it keeps increasing even after
    every suggestion has been removed (IDs must never be reused).

    migrated is True if one or more suggestions in the file had no ID and
    were assigned one during this load, so the caller knows to write the
    migrated IDs back to disk.
    """

    watch_items: list[WatchItem]
    next_id: int
    migrated: bool = False


class JsonSuggestionRepository:
    """Loads and saves movie suggestions as a JSON file on disk.

    This class is the only place that knows suggestions are stored as JSON.
    SuggestionService talks to it through load()/save() only, so the storage
    mechanism can be swapped for something else later without touching
    SuggestionService.
    """

    def __init__(self, file_path: Union[Path, str] = DEFAULT_SUGGESTIONS_PATH) -> None:
        """Initialize the repository.

        Args:
            file_path: Path to the JSON file used for persistence.
        """
        self._file_path = Path(file_path)

    def load(self) -> LoadResult:
        """Load suggestions from disk.

        A missing file is expected on first run and is not an error. A file
        that exists but can't be parsed is logged and treated as an empty
        list rather than crashing the bot. Suggestions saved before IDs
        existed are migrated in place: they're assigned sequential IDs here,
        in their existing order, without disturbing any IDs already present.

        Returns:
            A LoadResult with the persisted suggestions (in their original
            insertion order, IDs included) and the next ID to hand out.
        """
        if not self._file_path.exists():
            return LoadResult(watch_items=[], next_id=FIRST_SUGGESTION_ID)

        try:
            raw_text = self._file_path.read_text(encoding="utf-8")
            data = json.loads(raw_text)
            entries = data["suggestions"]

            max_existing_id = max(
                (entry["id"] for entry in entries if entry.get("id") is not None),
                default=FIRST_SUGGESTION_ID - 1,
            )
            next_id = max(data.get("next_id", FIRST_SUGGESTION_ID), max_existing_id + 1)

            watch_items: list[WatchItem] = []
            migrated = False
            for entry in entries:
                entry_id = entry.get("id")
                if entry_id is None:
                    # Older file predating suggestion IDs: assign one now.
                    entry_id = next_id
                    next_id += 1
                    migrated = True
                watch_items.append(self._deserialize(entry, entry_id))

            return LoadResult(watch_items=watch_items, next_id=next_id, migrated=migrated)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.error(f"Could not load suggestions from {self._file_path}: {exc}")
            return LoadResult(watch_items=[], next_id=FIRST_SUGGESTION_ID)

    def save(self, watch_items: Iterable[WatchItem], next_id: int) -> None:
        """Save suggestions to disk, overwriting any previous contents.

        Creates the parent directory and the file itself if they don't
        already exist.

        Args:
            watch_items: The suggestions to persist, in insertion order.
            next_id: The ID to hand out to the next new suggestion. Persisted
                alongside the list so IDs keep increasing across restarts.
        """
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "next_id": next_id,
            "suggestions": [self._serialize(item) for item in watch_items],
        }
        self._file_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @staticmethod
    def _serialize(watch_item: WatchItem) -> dict:
        """Convert a WatchItem into a JSON-friendly dict.

        id, title, media_type, metadata_ids, database_id, guild_id,
        channel_id, and message_id are persisted.
        """
        return {
            "id": watch_item.id,
            "title": watch_item.title,
            "media_type": watch_item.media_type.value,
            "metadata_ids": {
                provider.value: identifier
                for provider, identifier in watch_item.metadata_ids.items()
            },
            "database_id": watch_item.database_id,
            "guild_id": watch_item.guild_id,
            "channel_id": watch_item.channel_id,
            "message_id": watch_item.message_id,
        }

    @staticmethod
    def _deserialize(entry: dict, entry_id: int) -> WatchItem:
        """Rebuild a WatchItem from a dict produced by _serialize().

        database_id, guild_id, channel_id, and message_id all default to
        None when absent, so a file saved before this milestone (which has
        none of these keys) still loads without any special handling.

        Args:
            entry: The raw JSON dict for one suggestion.
            entry_id: The ID to use for this suggestion (already resolved by
                load(), whether it came from the file or was migrated).
        """
        metadata_ids = {
            MetadataProvider(provider): identifier
            for provider, identifier in entry.get("metadata_ids", {}).items()
        }
        return WatchItem(
            title=entry["title"],
            media_type=MediaType(entry["media_type"]),
            metadata_ids=metadata_ids,
            id=entry_id,
            database_id=entry.get("database_id"),
            guild_id=entry.get("guild_id"),
            channel_id=entry.get("channel_id"),
            message_id=entry.get("message_id"),
        )
