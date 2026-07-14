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
    watch_item: Optional[WatchItem] = None


@dataclass
class SuggestionDatabaseResult:
    """Result of a suggestion-database operation."""

    success: bool
    message: str
    database: Optional[SuggestionDatabase] = None


@dataclass
class DatabaseResolution:
    """Result of figuring out which suggestion database a command should use.

    database is set when resolution succeeded (a channel-matched database,
    the sole configured database, etc). error_message is set instead when
    no usable database could be determined, with user-facing text
    explaining why.
    """

    database: Optional[SuggestionDatabase] = None
    error_message: Optional[str] = None


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
        self._suggestions: dict[tuple[Optional[int], str], WatchItem] = {}
        load_result = self._repository.load()
        for watch_item in load_result.watch_items:
            self._suggestions[(watch_item.database_id, watch_item.title.lower())] = watch_item
        self._next_id = load_result.next_id
        if load_result.migrated:
            # An older file had suggestions with no ID; write the newly
            # assigned IDs back so they're stable from now on.
            self._save()

        self._database_repository = (
            database_repository if database_repository is not None else JsonSuggestionDatabaseRepository()
        )
        # Keyed by database_id, in creation order.
        self._databases: dict[int, SuggestionDatabase] = {}
        database_load_result = self._database_repository.load()
        for database in database_load_result.databases:
            self._databases[database.database_id] = database
        self._next_database_id = database_load_result.next_id

    def suggest(
        self,
        title: str,
        imdb_url: Optional[str] = None,
        database_id: Optional[int] = None,
        guild_id: Optional[int] = None,
        channel_id: Optional[int] = None,
        message_id: Optional[int] = None,
    ) -> SuggestionResult:
        """Add a suggestion to the list.

        Args:
            title: The movie/show title.
            imdb_url: Optional IMDb URL or ID.
            database_id: The suggestion database this belongs to, if one
                has already been resolved (see
                resolve_database_for_channel). Optional so this method
                stays usable without any database context.
            guild_id: The Discord guild the suggestion was made in, if known.
            channel_id: The Discord channel or thread the suggestion was
                made in, if known.
            message_id: The Discord message ID of the suggestion post, if
                already known. Often not available yet at creation time
                (see attach_message_reference).

        Returns:
            SuggestionResult indicating success or failure. On success,
            watch_item is the newly created suggestion.
        """
        # Validate title
        if not title or not title.strip():
            return SuggestionResult(
                success=False,
                message="I need a title before I can add it to the list.",
            )

        title = title.strip()
        title_lower = title.lower()
        suggestion_key = (database_id, title_lower)

        # Check for duplicates within the same database (case-insensitive).
        if suggestion_key in self._suggestions:
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
            database_id=database_id,
            guild_id=guild_id,
            channel_id=channel_id,
            message_id=message_id,
        )
        self._next_id += 1
        self._suggestions[suggestion_key] = watch_item
        self._save()
        return SuggestionResult(
            success=True,
            message=f'Added "{title}" to the suggestion list.',
            watch_item=watch_item,
        )

    def attach_message_reference(self, suggestion_id: int, message_id: int) -> bool:
        """Record the Discord message ID for an already-created suggestion.

        Discord doesn't hand back a new message's ID until after it's been
        sent, so this exists to backfill it onto a suggestion that was
        just created moments earlier in the same command.

        Args:
            suggestion_id: The suggestion to update.
            message_id: The Discord message ID of the suggestion's post.

        Returns:
            True if a matching suggestion was found and updated, False if
            no suggestion has that ID.
        """
        for watch_item in self._suggestions.values():
            if watch_item.id == suggestion_id:
                watch_item.message_id = message_id
                self._save()
                return True
        return False

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

    def format_suggestion_list(self, database_id: Optional[int] = None) -> str:
        """Build the user-facing text for the current suggestion list.

        Args:
            database_id: If given, only suggestions belonging to this
                database are included. If None, every suggestion is shown
                regardless of database -- this preserves the original,
                pre-database behavior for anything that doesn't (yet) have
                a database context.

        Returns:
            A message stating the list is empty, or a numbered list of
            suggestion titles in the order they were added. IMDb and other
            metadata are intentionally omitted from this view.
        """
        if database_id is not None:
            watch_items = [
                watch_item
                for watch_item in self._suggestions.values()
                if watch_item.database_id == database_id
            ]
        else:
            watch_items = list(self._suggestions.values())

        if not watch_items:
            return "The suggestion list is currently empty."

        lines = ["Current suggestions:"]
        for index, watch_item in enumerate(watch_items, start=1):
            lines.append(f"{index}. [{watch_item.id}] {watch_item.title}")
        return "\n".join(lines)

    def remove_suggestion(
        self, title: str, database_id: Optional[int] = None
    ) -> SuggestionResult:
        """Remove a suggestion from the list by title.

        Args:
            title: The movie/show title to remove. Matched case-insensitively,
                with leading/trailing whitespace ignored.
            database_id: Optional database context. When provided, only the
                matching suggestion in that database may be removed. Without
                database context, removal fails if the title exists in more
                than one database.

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
        if database_id is not None:
            key = (database_id, title_lower)
            watch_item = self._suggestions.pop(key, None)
            if watch_item is None:
                return SuggestionResult(
                    success=False,
                    message="That title is not on the suggestion list.",
                )
        else:
            matches = [
                (key, item)
                for key, item in self._suggestions.items()
                if key[1] == title_lower
            ]
            if not matches:
                return SuggestionResult(
                    success=False,
                    message="That title is not on the suggestion list.",
                )
            if len(matches) > 1:
                return SuggestionResult(
                    success=False,
                    message=(
                        "That title appears in more than one suggestion database. "
                        "Choose the database before removing it."
                    ),
                )
            key, watch_item = matches[0]
            del self._suggestions[key]

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

        is_first_database = len(self._databases) == 0

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

        if is_first_database:
            # Suggestions created before any database existed have no
            # database_id yet. Now that there's somewhere real to put
            # them, this is the first unambiguous moment to do so -- if a
            # second database gets created later, any suggestions still
            # orphaned at that point are left alone, since there'd be no
            # way to know which of several databases they belong to.
            self._migrate_orphaned_suggestions_to(database.database_id)

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

    def resolve_database_for_channel(
        self, guild_id: int, channel_id: int
    ) -> DatabaseResolution:
        """Determine which suggestion database applies in a guild/channel.

        Resolution considers only databases belonging to the supplied guild:
          1. A database configured for this exact channel (or thread) ID.
          2. If none matches but exactly one database exists in the guild, use it.
          3. If multiple databases exist in the guild and none match, resolution
             is ambiguous until interactive selection is implemented.
          4. If the guild has no databases, WASH Crew needs to configure one.

        Args:
            guild_id: The Discord guild (server) where the command was run.
            channel_id: The Discord channel or thread ID where it was run.

        Returns:
            DatabaseResolution with either a usable database or a clear
            error message to show the user.
        """
        databases = [
            database
            for database in self._databases.values()
            if database.guild_id == guild_id
        ]
        for database in databases:
            if database.channel_id == channel_id:
                return DatabaseResolution(database=database)

        if len(databases) == 1:
            return DatabaseResolution(database=databases[0])

        if len(databases) > 1:
            return DatabaseResolution(
                error_message=(
                    "Multiple suggestion databases are configured. Database "
                    "selection will be implemented in a future milestone."
                )
            )

        return DatabaseResolution(
            error_message="WASH Crew must configure a suggestion database first."
        )

    def _migrate_orphaned_suggestions_to(self, database_id: int) -> None:
        """Assign every suggestion with no database to the given database.

        Args:
            database_id: The database to assign orphaned suggestions to.
        """
        migrated = False
        for watch_item in self._suggestions.values():
            if watch_item.database_id is None:
                watch_item.database_id = database_id
                migrated = True
        if migrated:
            self._suggestions = {
                (item.database_id, item.title.lower()): item
                for item in self._suggestions.values()
            }
            self._save()

    def _save_databases(self) -> None:
        """Persist the current suggestion databases via the repository."""
        self._database_repository.save(self.list_databases(), self._next_database_id)
