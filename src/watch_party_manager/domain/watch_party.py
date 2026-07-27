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
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional


class WatchPartyStatus(str, Enum):
    """Lifecycle states for a scheduled watch party."""

    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"
    # Watch Party Lifecycle: set by WatchPartyCompletionService once a
    # scheduled watch party's end time (scheduled_at + duration_minutes)
    # passes -- see that module for the full Vote Winner -> Watched
    # transition. A watch party with no known duration_minutes has no
    # end time and therefore never automatically completes.
    COMPLETED = "completed"


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
    # Watch Party Lifecycle additions below -- all optional so every
    # existing caller/persisted record (from before this milestone) keeps
    # working unchanged with these simply unset.
    duration_minutes: Optional[int] = None
    # The voting round whose win produced this watch party, when scheduled
    # via the winner announcement's Schedule Watch Party button. None for
    # a watch party scheduled directly via /watch-party schedule (no vote
    # round is known there) -- database association doesn't need its own
    # field since it's always resolvable through watch_item_id.
    vote_round_id: Optional[int] = None
    description_override: Optional[str] = None
    # Discord Scheduled Events integration: the native Discord Scheduled
    # Event created alongside this watch party, when creation succeeded
    # (see winner-announcement scheduling in bot.py). None when Discord
    # Scheduled Events couldn't be used (missing permissions, API error,
    # or a watch party scheduled before this integration existed) --
    # WASH's own internal schedule remains authoritative either way.
    discord_event_id: Optional[int] = None

    def __post_init__(self) -> None:
        self._validate_id()
        self._validate_watch_item_id()
        self._validate_guild_id()
        self._validate_channel_id()
        self._validate_timestamps()
        self._validate_duration_minutes()
        self._validate_vote_round_id()
        self._validate_discord_event_id()
        self.description_override = self._normalize_description(self.description_override)

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

    def _validate_duration_minutes(self) -> None:
        if self.duration_minutes is not None and self.duration_minutes <= 0:
            raise ValueError("duration_minutes must be a positive integer when provided")

    def _validate_vote_round_id(self) -> None:
        if self.vote_round_id is not None and self.vote_round_id <= 0:
            raise ValueError("vote_round_id must be a positive integer when provided")

    def _validate_discord_event_id(self) -> None:
        if self.discord_event_id is not None and self.discord_event_id <= 0:
            raise ValueError("discord_event_id must be a positive integer when provided")

    @staticmethod
    def _normalize_description(description: Optional[str]) -> Optional[str]:
        if description is None:
            return None
        trimmed = description.strip()
        return trimmed or None

    @property
    def ends_at(self) -> Optional[datetime]:
        """When this watch party is expected to end, or None if
        duration_minutes isn't known -- see WatchPartyCompletionService,
        which schedules automatic completion off of this.
        """
        if self.duration_minutes is None:
            return None
        return self.scheduled_at + timedelta(minutes=self.duration_minutes)

    def with_changes(self, **changes: Any) -> "WatchParty":
        """Return a new, revalidated WatchParty with the given fields replaced.

        Used for updates (e.g. rescheduling) where the new value needs the
        same validation __post_init__ already applies at construction time
        -- mirrors ScheduledJob.with_changes for the same reason.
        """
        return replace(self, **changes)
