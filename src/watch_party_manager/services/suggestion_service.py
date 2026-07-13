"""Service for managing movie suggestions."""

from dataclasses import dataclass
from typing import Optional

from watch_party_manager.domain.suggestion_database import SuggestionDatabase
from watch_party_manager.domain.watch_item import MediaType, MetadataProvider, WatchItem
from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository


@dataclass
class SuggestionResult:
    """Result of a suggestion operation."""

    success: bool
    message: str


@dataclass
class SuggestionDatabaseResult:
    """Result of a suggestion-database operation."""

    success: bool
    message: str
    database: Optional[SuggestionDatabase] = None


class SuggestionService:
    """Manages movie suggestions, persisted through a suggestion repository."""

    def __init__(
        self,
        repository: Optional[JsonSuggestionRepository] = None,
        database_repository: Optional[JsonSuggestionDatabaseRepository] = None,
    ) -> None:
        """Initialize the suggestion service and load any persisted state.

        Args:
            repository: The persistence layer to load suggestions from and
                save them to. Defaults to a JsonSuggestionRepository using
                the default on-disk location.
            database_repository: The persistence layer to load suggestion
                databases from and save them to. Defaults to a
                JsonSuggestionDatabaseRepository using the default on-disk
                location.
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

        self._database_repository = (
            database_repository if database_repository is not None else JsonSuggestionDatabaseRepository()
        )
        # Keyed by database_id, in creation order. Suggestions don't belong
        # to a database yet -- this is groundwork for a future milestone.
        self._databases: dict[int, SuggestionDatabase] = {}
        database_load_result = self._database_repository.load()
        for database in database_load_result.databases:
            self._databases[database.database_id] = database
        self._next_database_id = database_load_result.next_id

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

    def suggestion_exists(self, suggestion_id: int) -> bool:
        """Check whether a suggestion with the given ID is currently on the list.

        Args:
            suggestion_id: The suggestion ID to look up.

        Returns:
            True if a suggestion with this ID currently exists.
        """
        return any(watch_item.id == suggestion_id for watch_item in self._suggestions.values())

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

    def create_database(
        self,
        name: str,
        guild_id: int,
        channel_id: int,
        active: bool = True,
    ) -> SuggestionDatabaseResult:
        """Create a new suggestion database.

        A suggestion database is a WASH Crew-configured collection tied to
        a specific Discord channel or thread (e.g. "Sunday Watch Party").
        Suggestions themselves don't belong to a database yet.

        Args:
            name: Display name for the database. Matched case-insensitively
                against other databases in the same guild to reject
                duplicates.
            guild_id: The Discord guild (server) this database belongs to.
            channel_id: The Discord channel or thread ID this database is
                associated with. A guild may not have two databases on the
                same channel.
            active: Whether the database starts active. Defaults to True.
                Nothing in this service filters on this flag yet -- it's
                groundwork for future archive behavior.

        Returns:
            SuggestionDatabaseResult indicating success or failure.
        """
        if not name or not name.strip():
            return SuggestionDatabaseResult(
                success=False,
                message="I need a name before I can create a suggestion database.",
            )

        trimmed_name = name.strip()
        name_lower = trimmed_name.lower()

        for database in self._databases.values():
            if database.guild_id != guild_id:
                continue
            if database.name.lower() == name_lower:
                return SuggestionDatabaseResult(
                    success=False,
                    message=f'A suggestion database named "{trimmed_name}" already exists in this server.',
                )
            if database.channel_id == channel_id:
                return SuggestionDatabaseResult(
                    success=False,
                    message="This channel already has a suggestion database.",
                )

        database = SuggestionDatabase(
            database_id=self._next_database_id,
            name=trimmed_name,
            guild_id=guild_id,
            channel_id=channel_id,
            active=active,
        )
        self._next_database_id += 1
        self._databases[database.database_id] = database
        self._save_databases()
        return SuggestionDatabaseResult(
            success=True,
            message=f'Created suggestion database "{trimmed_name}".',
            database=database,
        )

    def get_database(self, database_id: int) -> Optional[SuggestionDatabase]:
        """Get a suggestion database by ID.

        Args:
            database_id: The database ID to look up.

        Returns:
            The matching SuggestionDatabase, or None if no database has
            that ID.
        """
        return self._databases.get(database_id)

    def list_databases(self) -> list[SuggestionDatabase]:
        """Get all suggestion databases, in the order they were created.

        Returns:
            List of every SuggestionDatabase, active or not -- this
            service doesn't filter by active status.
        """
        return list(self._databases.values())

    def database_exists(self, database_id: int) -> bool:
        """Check whether a suggestion database with the given ID currently exists.

        Args:
            database_id: The database ID to look up.

        Returns:
            True if a database with this ID currently exists.
        """
        return database_id in self._databases

    def _save_databases(self) -> None:
        """Persist the current suggestion databases via the repository."""
        self._database_repository.save(self.list_databases(), self._next_database_id)
