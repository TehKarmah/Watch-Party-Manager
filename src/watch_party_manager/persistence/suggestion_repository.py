"""JSON-backed persistence for movie suggestions."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable, Union

from watch_party_manager.domain.watch_item import MediaType, MetadataProvider, WatchItem

logger = logging.getLogger(__name__)

# Default on-disk location for the suggestion list. Sits under the project's
# conventional "data/" directory (already used for other runtime data; see
# .gitignore) so it's easy to find and inspect during development.
DEFAULT_SUGGESTIONS_PATH = Path("data/suggestions.json")


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

    def load(self) -> list[WatchItem]:
        """Load suggestions from disk.

        A missing file is expected on first run and is not an error. A file
        that exists but can't be parsed is logged and treated as an empty
        list rather than crashing the bot.

        Returns:
            The persisted suggestions, in their original insertion order.
            Empty if the file is missing or malformed.
        """
        if not self._file_path.exists():
            return []

        try:
            raw_text = self._file_path.read_text(encoding="utf-8")
            data = json.loads(raw_text)
            entries = data["suggestions"]
            return [self._deserialize(entry) for entry in entries]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.error(f"Could not load suggestions from {self._file_path}: {exc}")
            return []

    def save(self, watch_items: Iterable[WatchItem]) -> None:
        """Save suggestions to disk, overwriting any previous contents.

        Creates the parent directory and the file itself if they don't
        already exist.

        Args:
            watch_items: The suggestions to persist, in insertion order.
        """
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"suggestions": [self._serialize(item) for item in watch_items]}
        self._file_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @staticmethod
    def _serialize(watch_item: WatchItem) -> dict:
        """Convert a WatchItem into a JSON-friendly dict.

        Only title, media_type, and metadata_ids are persisted for this
        milestone.
        """
        return {
            "title": watch_item.title,
            "media_type": watch_item.media_type.value,
            "metadata_ids": {
                provider.value: identifier
                for provider, identifier in watch_item.metadata_ids.items()
            },
        }

    @staticmethod
    def _deserialize(entry: dict) -> WatchItem:
        """Rebuild a WatchItem from a dict produced by _serialize()."""
        metadata_ids = {
            MetadataProvider(provider): identifier
            for provider, identifier in entry.get("metadata_ids", {}).items()
        }
        return WatchItem(
            title=entry["title"],
            media_type=MediaType(entry["media_type"]),
            metadata_ids=metadata_ids,
        )
