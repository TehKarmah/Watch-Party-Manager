"""Domain model for suggestion databases."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(slots=True)
class SuggestionDatabase:
    """A named collection of suggestions tied to a specific Discord channel or thread.

    Each suggestion database is configured by WASH Crew and represents a
    distinct watch list (e.g. "Sunday Watch Party", "Halloween Movies").
    Suggestions themselves don't belong to a database yet — that
    association, and any context-aware behavior built on it, is future
    work. This model only establishes the database itself.
    """

    database_id: int
    name: str
    guild_id: int
    channel_id: int
    active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        self.name = self.name.strip()
        self._validate_database_id()
        self._validate_name()
        self._validate_guild_id()
        self._validate_channel_id()
        self._validate_created_at()

    def _validate_database_id(self) -> None:
        if self.database_id <= 0:
            raise ValueError("database_id must be a positive integer")

    def _validate_name(self) -> None:
        if not self.name:
            raise ValueError("name must not be empty")

    def _validate_guild_id(self) -> None:
        if self.guild_id <= 0:
            raise ValueError("guild_id must be a positive integer")

    def _validate_channel_id(self) -> None:
        if self.channel_id <= 0:
            raise ValueError("channel_id must be a positive integer")

    def _validate_created_at(self) -> None:
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
