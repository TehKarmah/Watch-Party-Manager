"""Domain model for a scheduled watch party.

Minimal, FR-020-scoped foundation: just enough to hang a reminder off of
(a Watch Item, a scheduled time, and a Discord destination). This is
deliberately not the full "Event Series" / "Scheduled Event" system
docs/04-Data-Model.md describes (recurring schedules, Discord Event IDs,
source types) -- that remains future work (FR-011). Naming this WatchParty
rather than ScheduledEvent keeps that distinction clear.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class WatchPartyStatus(str, Enum):
    """Lifecycle states for a scheduled watch party."""

    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class WatchParty:
    """A single scheduled watch party for one Watch Item.

    Mirrors VoteRound's shape and validation style: a plain, Discord-free
    domain record identified by a stable integer ID, owned by one guild,
    with an optional channel reference (not always known/available, same
    as VoteRound.channel_id) for wherever its reminder should be posted.
    """

    id: int
    watch_item_id: int
    scheduled_at: datetime
    guild_id: int
    channel_id: Optional[int] = None
    status: WatchPartyStatus = WatchPartyStatus.SCHEDULED
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        self._validate_id()
        self._validate_watch_item_id()
        self._validate_guild_id()
        self._validate_channel_id()
        self._validate_timestamps()

    def _validate_id(self) -> None:
        if self.id <= 0:
            raise ValueError("id must be a positive integer")

    def _validate_watch_item_id(self) -> None:
        if self.watch_item_id <= 0:
            raise ValueError("watch_item_id must be a positive integer")

    def _validate_guild_id(self) -> None:
        if self.guild_id <= 0:
            raise ValueError("guild_id must be a positive integer")

    def _validate_channel_id(self) -> None:
        if self.channel_id is not None and self.channel_id <= 0:
            raise ValueError("channel_id must be a positive integer when provided")

    def _validate_timestamps(self) -> None:
        if self.scheduled_at.tzinfo is None or self.scheduled_at.utcoffset() is None:
            raise ValueError("scheduled_at must be timezone-aware")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")

    def with_changes(self, **changes: Any) -> "WatchParty":
        """Return a new, revalidated WatchParty with the given fields replaced.

        Used for updates (e.g. rescheduling) where the new value needs the
        same validation __post_init__ already applies at construction time
        -- mirrors ScheduledJob.with_changes for the same reason.
        """
        return replace(self, **changes)
