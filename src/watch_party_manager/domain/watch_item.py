from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Tuple

from watch_party_manager.domain.watch_item_journey import WatchItemJourney

MINIMUM_RELEASE_YEAR = 1870
MAXIMUM_RELEASE_YEAR_LOOKAHEAD = 5


class MediaType(str, Enum):
    """Supported media types for the first implementation pass."""

    MOVIE = "movie"
    TV_SERIES = "tv_series"


class MetadataProvider(str, Enum):
    """Supported metadata providers."""

    IMDB = "imdb"
    TMDB = "tmdb"


class WatchItemStatus(str, Enum):
    """Lifecycle states for a watch item."""

    SUGGESTED = "suggested"
    ELIGIBLE = "eligible"
    CURRENT_ROTATION = "current_rotation"
    SELECTED_FOR_VOTE = "selected_for_vote"
    SCHEDULED = "scheduled"
    WATCHED = "watched"
    REWATCH_ELIGIBLE = "rewatch_eligible"
    ARCHIVED = "archived"


@dataclass(slots=True)
class WatchItem:
    """A single movie or television series managed by the community."""

    title: str
    media_type: MediaType
    runtime_minutes: int | None = None
    genres: Tuple[str, ...] = field(default_factory=tuple)
    metadata_ids: Dict[MetadataProvider, str] = field(default_factory=dict)
    status: WatchItemStatus = WatchItemStatus.SUGGESTED
    description: Optional[str] = None
    content_rating: Optional[str] = None
    director: Optional[str] = None
    imdb_rating: Optional[str] = None
    poster_url: Optional[str] = None
    id: Optional[int] = None
    database_id: Optional[int] = None
    guild_id: Optional[int] = None
    channel_id: Optional[int] = None
    message_id: Optional[int] = None
    journey: WatchItemJourney = field(default_factory=WatchItemJourney)
    release_year: Optional[int] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        self.title = self.title.strip()
        self._validate_title()
        self._validate_runtime()
        self._validate_id()
        self._validate_database_id()
        self._validate_guild_id()
        self._validate_channel_id()
        self._validate_message_id()
        self._validate_release_year()
        self._validate_updated_at()
        self.genres = self._normalize_genres(self.genres)
        self.metadata_ids = self._normalize_metadata_ids(self.metadata_ids)

    @property
    def reference(self) -> str:
        """Return the stable user-facing reference for this watch item.

        References are zero-padded to at least four digits for readability,
        while the underlying persisted ID remains a plain integer.
        """
        if self.id is None:
            return "Unassigned"
        return f"#{self.id:04d}"

    def _validate_title(self) -> None:
        if not self.title:
            raise ValueError("title must not be empty")

    def _validate_runtime(self) -> None:
        if self.runtime_minutes is not None and self.runtime_minutes <= 0:
            raise ValueError("runtime_minutes must be greater than zero when provided")

    def _validate_id(self) -> None:
        if self.id is not None and self.id <= 0:
            raise ValueError("id must be a positive integer when provided")

    def _validate_database_id(self) -> None:
        if self.database_id is not None and self.database_id <= 0:
            raise ValueError("database_id must be a positive integer when provided")

    def _validate_guild_id(self) -> None:
        if self.guild_id is not None and self.guild_id <= 0:
            raise ValueError("guild_id must be a positive integer when provided")

    def _validate_channel_id(self) -> None:
        if self.channel_id is not None and self.channel_id <= 0:
            raise ValueError("channel_id must be a positive integer when provided")

    def _validate_message_id(self) -> None:
        if self.message_id is not None and self.message_id <= 0:
            raise ValueError("message_id must be a positive integer when provided")

    def _validate_release_year(self) -> None:
        if self.release_year is None:
            return
        current_year = datetime.now(timezone.utc).year
        if not (MINIMUM_RELEASE_YEAR <= self.release_year <= current_year + MAXIMUM_RELEASE_YEAR_LOOKAHEAD):
            raise ValueError(
                f"release_year must be between {MINIMUM_RELEASE_YEAR} and "
                f"{current_year + MAXIMUM_RELEASE_YEAR_LOOKAHEAD} when provided"
            )

    def _validate_updated_at(self) -> None:
        if self.updated_at is not None and self.updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware when provided")

    @staticmethod
    def _normalize_genres(genres: Tuple[str, ...] | list[str] | None) -> Tuple[str, ...]:
        if not genres:
            return ()

        normalized: list[str] = []
        for genre in genres:
            if genre is None:
                continue
            cleaned = str(genre).strip()
            if cleaned:
                normalized.append(cleaned)

        return tuple(normalized)

    @staticmethod
    def _normalize_metadata_ids(
        metadata_ids: Dict[MetadataProvider, str] | None,
    ) -> Dict[MetadataProvider, str]:
        if not metadata_ids:
            return {}

        normalized: Dict[MetadataProvider, str] = {}
        for provider, identifier in metadata_ids.items():
            if not isinstance(provider, MetadataProvider):
                raise TypeError("metadata_ids keys must be MetadataProvider instances")
            if not isinstance(identifier, str):
                raise TypeError("metadata_ids values must be strings")
            trimmed_identifier = identifier.strip()
            if not trimmed_identifier:
                raise ValueError("metadata_ids values must be non-empty strings after trimming")
            normalized[provider] = trimmed_identifier

        return normalized
