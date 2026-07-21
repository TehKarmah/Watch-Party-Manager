"""Domain models for the voting system."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Optional

# A member may use at most this many vote changes per round.
MAX_VOTE_CHANGES = 1

# A voting round needs at least this many suggestions to choose between.
MIN_CANDIDATES_FOR_A_ROUND = 2

# How many days a voting round stays open by default when no explicit
# duration is given.
DEFAULT_VOTE_DURATION_DAYS = 7

# Bounds for a custom voting duration, inclusive.
MIN_VOTE_DURATION_DAYS = 1
MAX_VOTE_DURATION_DAYS = 30

# Nominee-count defaults and bounds for interactive voting.
DEFAULT_VOTE_CANDIDATE_COUNT = 3
MIN_VOTE_CANDIDATE_COUNT = 2
MAX_VOTE_CANDIDATE_COUNT = 10


class VoteVisibility(str, Enum):
    """Whether individual votes are visible to other members."""

    BLIND = "blind"
    VISIBLE = "visible"


class VoteRoundStatus(str, Enum):
    """Lifecycle states for a voting round."""

    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class VoteRecord:
    """A single member's vote within a voting round.

    Both suggestion IDs are kept: original_suggestion_id is the member's
    first pick and never changes after that; suggestion_id is their current
    pick and is the one that gets updated on their one allowed change. On a
    first vote these are equal. Both original and most recent vote
    timestamps are kept too, along with how many changes have been used, so
    future statistics (e.g. did this member vote for the eventual winner,
    and did they change their mind to get there) can be reconstructed
    without needing any additional history.
    """

    discord_user_id: int
    suggestion_id: int
    original_suggestion_id: int
    first_voted_at: datetime
    last_voted_at: datetime
    changes_used: int = 0

    def __post_init__(self) -> None:
        self._validate_discord_user_id()
        self._validate_suggestion_id()
        self._validate_changes_used()
        self._validate_timestamps()

    def _validate_discord_user_id(self) -> None:
        if self.discord_user_id <= 0:
            raise ValueError("discord_user_id must be a positive integer")

    def _validate_suggestion_id(self) -> None:
        if self.suggestion_id <= 0:
            raise ValueError("suggestion_id must be a positive integer")
        if self.original_suggestion_id <= 0:
            raise ValueError("original_suggestion_id must be a positive integer")

    def _validate_changes_used(self) -> None:
        if self.changes_used < 0:
            raise ValueError("changes_used must not be negative")
        if self.changes_used > MAX_VOTE_CHANGES:
            raise ValueError(f"changes_used must not exceed {MAX_VOTE_CHANGES}")

    def _validate_timestamps(self) -> None:
        if self.first_voted_at.tzinfo is None:
            raise ValueError("first_voted_at must be timezone-aware")
        if self.last_voted_at.tzinfo is None:
            raise ValueError("last_voted_at must be timezone-aware")


@dataclass(slots=True)
class VoteRound:
    """A single round of voting over the currently suggested Watch Items.

    votes is keyed by discord_user_id, which is both how "one active vote
    per member" is enforced and how member lookups stay O(1). Iteration
    order still reflects insertion order (the order members first voted),
    matching the convention already used by SuggestionService.
    """

    id: int
    status: VoteRoundStatus = VoteRoundStatus.OPEN
    visibility: VoteVisibility = VoteVisibility.VISIBLE
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    closes_at: Optional[datetime] = None
    votes: Dict[int, VoteRecord] = field(default_factory=dict)
    winning_suggestion_id: Optional[int] = None
    guild_id: Optional[int] = None
    channel_id: Optional[int] = None
    message_id: Optional[int] = None
    database_id: Optional[int] = None
    candidate_suggestion_ids: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._validate_id()
        self._validate_winning_suggestion_id()
        self._validate_timestamps()
        self._validate_guild_id()
        self._validate_channel_id()
        self._validate_message_id()
        self._validate_database_id()
        self._validate_candidate_suggestion_ids()

    def _validate_id(self) -> None:
        if self.id <= 0:
            raise ValueError("id must be a positive integer")

    def _validate_winning_suggestion_id(self) -> None:
        if self.winning_suggestion_id is not None and self.winning_suggestion_id <= 0:
            raise ValueError("winning_suggestion_id must be a positive integer when provided")

    def _validate_timestamps(self) -> None:
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        if self.closes_at is not None and self.closes_at.tzinfo is None:
            raise ValueError("closes_at must be timezone-aware")

    def _validate_guild_id(self) -> None:
        if self.guild_id is not None and self.guild_id <= 0:
            raise ValueError("guild_id must be a positive integer when provided")

    def _validate_channel_id(self) -> None:
        if self.channel_id is not None and self.channel_id <= 0:
            raise ValueError("channel_id must be a positive integer when provided")

    def _validate_message_id(self) -> None:
        if self.message_id is not None and self.message_id <= 0:
            raise ValueError("message_id must be a positive integer when provided")

    def _validate_database_id(self) -> None:
        if self.database_id is not None and self.database_id <= 0:
            raise ValueError("database_id must be a positive integer when provided")

    def _validate_candidate_suggestion_ids(self) -> None:
        candidate_ids = list(self.candidate_suggestion_ids)
        if any(candidate_id <= 0 for candidate_id in candidate_ids):
            raise ValueError("candidate_suggestion_ids must contain only positive integers")
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate_suggestion_ids must not contain duplicates")
        self.candidate_suggestion_ids = candidate_ids
