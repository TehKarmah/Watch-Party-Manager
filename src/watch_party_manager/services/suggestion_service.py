"""Service for managing movie suggestions."""

from dataclasses import dataclass
from typing import Optional

from watch_party_manager.domain.watch_item import MediaType, MetadataProvider, WatchItem


@dataclass
class SuggestionResult:
    """Result of a suggestion operation."""

    success: bool
    message: str


class SuggestionService:
    """Manages in-memory movie suggestions."""

    def __init__(self) -> None:
        """Initialize the suggestion service."""
        # Store suggestions as a dict with lowercase title as key for duplicate detection
        # Value is the actual WatchItem with original casing
        self._suggestions: dict[str, WatchItem] = {}

    def suggest(self, title: str, imdb_url: Optional[str] = None) -> SuggestionResult:
        """Add a suggestion to the list.

        Args:
            title: The movie/show title.
            imdb_url: Optional IMDb URL or ID.

        Returns:
            SuggestionResult indicating success or failure.
        """
        # Validate title
        if not title or not title.strip():
            return SuggestionResult(
                success=False,
                message="I need a title before I can add it to the list.",
            )

        title = title.strip()
        title_lower = title.lower()

        # Check for duplicates (case-insensitive)
        if title_lower in self._suggestions:
            return SuggestionResult(
                success=False,
                message="That title is already on the list. Nice try.",
            )

        # Build metadata_ids if IMDb URL is provided
        metadata_ids = {}
        if imdb_url and imdb_url.strip():
            metadata_ids[MetadataProvider.IMDB] = imdb_url.strip()

        # Create and store the WatchItem
        watch_item = WatchItem(
            title=title,
            media_type=MediaType.MOVIE,
            metadata_ids=metadata_ids,
        )
        self._suggestions[title_lower] = watch_item
        return SuggestionResult(
            success=True,
            message=f'Added "{title}" to the suggestion list.',
        )

    def get_suggestions(self) -> list[WatchItem]:
        """Get all current suggestions.

        Returns:
            List of all suggested WatchItems.
        """
        return list(self._suggestions.values())

    def clear_suggestions(self) -> None:
        """Clear all suggestions. Used for testing or bot reset."""
        self._suggestions.clear()

    def suggestion_count(self) -> int:
        """Get the number of current suggestions.

        Returns:
            Number of suggestions in the list.
        """
        return len(self._suggestions)

    def format_suggestion_list(self) -> str:
        """Build the user-facing text for the current suggestion list.

        Returns:
            A message stating the list is empty, or a numbered list of
            suggestion titles in the order they were added. IMDb and other
            metadata are intentionally omitted from this view.
        """
        if not self._suggestions:
            return "The suggestion list is currently empty."

        lines = ["Current suggestions:"]
        for index, watch_item in enumerate(self._suggestions.values(), start=1):
            lines.append(f"{index}. {watch_item.title}")
        return "\n".join(lines)
