"""Service for managing movie suggestions."""

from dataclasses import dataclass
from typing import Optional

from watch_party_manager.domain.watch_item import MediaType, MetadataProvider, WatchItem
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository


@dataclass
class SuggestionResult:
    """Result of a suggestion operation."""

    success: bool
    message: str


class SuggestionService:
    """Manages movie suggestions, persisted through a suggestion repository."""

    def __init__(self, repository: Optional[JsonSuggestionRepository] = None) -> None:
        """Initialize the suggestion service and load any persisted suggestions.

        Args:
            repository: The persistence layer to load from and save to.
                Defaults to a JsonSuggestionRepository using the default
                on-disk location.
        """
        self._repository = repository if repository is not None else JsonSuggestionRepository()
        # Store suggestions as a dict with lowercase title as key for duplicate detection
        # Value is the actual WatchItem with original casing
        self._suggestions: dict[str, WatchItem] = {}
        load_result = self._repository.load()
        for watch_item in load_result.watch_items:
            self._suggestions[watch_item.title.lower()] = watch_item
        self._next_id = load_result.next_id
        if load_result.migrated:
            # An older file had suggestions with no ID; write the newly
            # assigned IDs back so they're stable from now on.
            self._save()

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
            id=self._next_id,
        )
        self._next_id += 1
        self._suggestions[title_lower] = watch_item
        self._save()
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
            lines.append(f"{index}. [{watch_item.id}] {watch_item.title}")
        return "\n".join(lines)

    def remove_suggestion(self, title: str) -> SuggestionResult:
        """Remove a suggestion from the list by title.

        Args:
            title: The movie/show title to remove. Matched case-insensitively,
                with leading/trailing whitespace ignored.

        Returns:
            SuggestionResult indicating success or failure. The stored title's
            original capitalization is used in the success message.
        """
        if not title or not title.strip():
            return SuggestionResult(
                success=False,
                message="I need a title before I can remove it.",
            )

        title_lower = title.strip().lower()

        watch_item = self._suggestions.pop(title_lower, None)
        if watch_item is None:
            return SuggestionResult(
                success=False,
                message="That title is not on the suggestion list.",
            )

        self._save()
        return SuggestionResult(
            success=True,
            message=f'Removed "{watch_item.title}" from the suggestion list.',
        )

    def _save(self) -> None:
        """Persist the current suggestion list via the repository."""
        self._repository.save(self.get_suggestions(), self._next_id)
