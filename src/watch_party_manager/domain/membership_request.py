"""Domain model for FR-030's Approval-Required join-mode workflow.

A MembershipRequest exists only for guilds configured with
JoinMode.APPROVAL (see domain/guild_configuration.py) -- Manual,
Self-Service, and Discord-Managed modes never create one. Nothing here
redesigns GuildConfiguration or WatchPartyRoleConfig; a request only
records that a member asked to join and how WASH Crew resolved it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class MembershipRequestStatus(str, Enum):
    """Lifecycle of one Approval-Required join request."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


def _validate_optional_snowflake(value: Optional[int], field_name: str) -> None:
    if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value <= 0):
        raise ValueError(f"{field_name} must be a positive integer when provided")


def _validate_positive_int(value: int, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")


@dataclass(slots=True)
class MembershipRequest:
    """One member's request to join under Approval-Required mode.

    request_id is this record's identity, assigned by
    MembershipRequestRepository the same way SuggestionDatabase's
    database_id is assigned by JsonSuggestionDatabaseRepository --
    monotonically increasing, never reused.

    channel_id/message_id identify the Discord message WASH Crew's
    Approve/Deny buttons live on, so a persistent view can be
    re-registered for every still-pending request after a bot restart
    (see bot.py's restore_persistent_membership_approval_views, mirroring
    restore_persistent_voting_view's exact approach).
    """

    request_id: int
    guild_id: int
    user_id: int
    status: MembershipRequestStatus = MembershipRequestStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: Optional[datetime] = None
    resolved_by_user_id: Optional[int] = None
    channel_id: Optional[int] = None
    message_id: Optional[int] = None

    def __post_init__(self) -> None:
        _validate_positive_int(self.request_id, "request_id")
        _validate_positive_int(self.guild_id, "guild_id")
        _validate_positive_int(self.user_id, "user_id")
        _validate_optional_snowflake(self.resolved_by_user_id, "resolved_by_user_id")
        _validate_optional_snowflake(self.channel_id, "channel_id")
        _validate_optional_snowflake(self.message_id, "message_id")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        if self.resolved_at is not None and self.resolved_at.tzinfo is None:
            raise ValueError("resolved_at must be timezone-aware")
        if self.status is not MembershipRequestStatus.PENDING:
            if self.resolved_at is None:
                raise ValueError("resolved_at is required once a request has been processed")
            if self.resolved_by_user_id is None:
                raise ValueError("resolved_by_user_id is required once a request has been processed")

    @property
    def is_pending(self) -> bool:
        return self.status is MembershipRequestStatus.PENDING
