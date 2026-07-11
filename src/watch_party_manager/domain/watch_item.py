from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Tuple


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
    id: Optional[int] = None

    def __post_init__(self) -> None:
        self.title = self.title.strip()
        self._validate_title()
        self._validate_runtime()
        self._validate_id()
        self.genres = self._normalize_genres(self.genres)
        self.metadata_ids = self._normalize_metadata_ids(self.metadata_ids)

    def _validate_title(self) -> None:
        if not self.title:
            raise ValueError("title must not be empty")

    def _validate_runtime(self) -> None:
        if self.runtime_minutes is not None and self.runtime_minutes <= 0:
            raise ValueError("runtime_minutes must be greater than zero when provided")

    def _validate_id(self) -> None:
        if self.id is not None and self.id <= 0:
            raise ValueError("id must be a positive integer when provided")

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
