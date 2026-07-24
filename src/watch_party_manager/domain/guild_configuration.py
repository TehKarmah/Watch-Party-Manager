"""Domain models for per-guild WASH configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class JoinMode(str, Enum):
    MANUAL = "manual"
    SELF_SERVICE = "self_service"
    APPROVAL = "approval"
    DISCORD_MANAGED = "discord_managed"


class GuildVoteVisibility(str, Enum):
    BLIND = "blind"
    VISIBLE = "visible"


class TieBehavior(str, Enum):
    ALL_WINNERS = "all_winners"


def _validate_optional_snowflake(value: Optional[int], field_name: str) -> None:
    if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value <= 0):
        raise ValueError(f"{field_name} must be a positive integer when provided")


def _validate_positive_int(value: int, field_name: str, minimum: int, maximum: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError(f"{field_name} must be between {minimum} and {maximum}")


def _coerce_enum(value: Any, enum_type: type[Enum], field_name: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        supported = ", ".join(item.value for item in enum_type)
        raise ValueError(f"{field_name} must be one of: {supported}") from None


def _validate_extra_fields(value: dict[str, Any]) -> None:
    if not isinstance(value, dict):
        raise ValueError("extra_fields must be a dictionary")


@dataclass(slots=True)
class WatchPartyRoleConfig:
    role_id: Optional[int] = None
    join_mode: JoinMode = JoinMode.SELF_SERVICE
    allow_self_leave: bool = True
    # FR-030 refinement: how long a member must wait after an Approval-
    # Required request is denied before requesting again. Persisted here
    # (rather than invented as new top-level schema) so a future /setup or
    # /config UI can expose it without another migration; only
    # Approval-Required mode consults it.
    denial_cooldown_days: int = 7
    extra_fields: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        _validate_optional_snowflake(self.role_id, "role_id")
        self.join_mode = _coerce_enum(self.join_mode, JoinMode, "join_mode")  # type: ignore[assignment]
        _validate_positive_int(self.denial_cooldown_days, "denial_cooldown_days", 1, 365)
        _validate_extra_fields(self.extra_fields)


@dataclass(slots=True)
class GuildChannelsConfig:
    announcements_channel_id: Optional[int] = None
    log_channel_id: Optional[int] = None
    # FR-030 refinement: dedicated channel for Approval-Required membership
    # requests -- deliberately separate from log_channel_id, which is a
    # general administrative log, not a WASH Crew action queue.
    admin_channel_id: Optional[int] = None
    extra_fields: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        _validate_optional_snowflake(self.announcements_channel_id, "announcements_channel_id")
        _validate_optional_snowflake(self.log_channel_id, "log_channel_id")
        _validate_optional_snowflake(self.admin_channel_id, "admin_channel_id")
        _validate_extra_fields(self.extra_fields)


@dataclass(slots=True)
class GuildSuggestionDatabaseEntry:
    id: str
    display_name: str
    active: bool = True
    extra_fields: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.id = self.id.strip()
        self.display_name = self.display_name.strip()
        if not self.id:
            raise ValueError("id must not be empty")
        if not self.display_name:
            raise ValueError("display_name must not be empty")
        _validate_extra_fields(self.extra_fields)


@dataclass(slots=True)
class VotingDefaultsConfig:
    candidate_count: int = 3
    duration_days: int = 7
    visibility: GuildVoteVisibility = GuildVoteVisibility.VISIBLE
    max_vote_changes: int = 1
    tie_behavior: TieBehavior = TieBehavior.ALL_WINNERS
    extra_fields: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        _validate_positive_int(self.candidate_count, "candidate_count", 2, 10)
        _validate_positive_int(self.duration_days, "duration_days", 1, 30)
        _validate_positive_int(self.max_vote_changes, "max_vote_changes", 0, 10)
        self.visibility = _coerce_enum(self.visibility, GuildVoteVisibility, "visibility")  # type: ignore[assignment]
        self.tie_behavior = _coerce_enum(self.tie_behavior, TieBehavior, "tie_behavior")  # type: ignore[assignment]
        _validate_extra_fields(self.extra_fields)


@dataclass(slots=True)
class VoteNotificationsConfig:
    vote_started: bool = True
    vote_results: bool = True
    vote_ending_reminder: bool = True
    reminder_hours_before_close: int = 24
    extra_fields: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        _validate_positive_int(self.reminder_hours_before_close, "reminder_hours_before_close", 1, 720)
        _validate_extra_fields(self.extra_fields)


@dataclass(slots=True)
class WatchNotificationsConfig:
    enabled: bool = True
    reminder_hours_before_watch: int = 1
    extra_fields: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        _validate_positive_int(self.reminder_hours_before_watch, "reminder_hours_before_watch", 1, 720)
        _validate_extra_fields(self.extra_fields)


@dataclass(slots=True)
class AdministrativeNotificationsConfig:
    low_suggestion_pool: bool = True
    low_suggestion_pool_threshold: int = 10
    backup_completed: bool = True
    backup_failed: bool = True
    restore_completed: bool = True
    restore_failed: bool = True
    extra_fields: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        _validate_positive_int(self.low_suggestion_pool_threshold, "low_suggestion_pool_threshold", 1, 1000)
        _validate_extra_fields(self.extra_fields)


@dataclass(slots=True)
class NotificationsConfig:
    vote: VoteNotificationsConfig = field(default_factory=VoteNotificationsConfig)
    watch: WatchNotificationsConfig = field(default_factory=WatchNotificationsConfig)
    administrative: AdministrativeNotificationsConfig = field(default_factory=AdministrativeNotificationsConfig)
    extra_fields: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        _validate_extra_fields(self.extra_fields)


@dataclass(slots=True)
class FeatureFlagsConfig:
    birthday_picks: bool = False
    self_service_watch_party_role: bool = True
    member_vote_reminders: bool = True
    watch_reminders: bool = True
    low_suggestion_pool_alerts: bool = True
    suggestion_rejection_voting: bool = True
    archived_suggestion_review: bool = True
    extra_fields: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        _validate_extra_fields(self.extra_fields)


@dataclass(slots=True)
class BackupConfig:
    include_in_automatic_backups: bool = True
    notify_on_backup_success: bool = True
    notify_on_backup_failure: bool = True
    allow_restore: bool = True
    extra_fields: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        _validate_extra_fields(self.extra_fields)


@dataclass(slots=True)
class WatchHistoryConfig:
    enabled: bool = True
    allow_retroactive_entries: bool = True
    allow_repeat_watches: bool = True
    extra_fields: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        _validate_extra_fields(self.extra_fields)


@dataclass(slots=True)
class MigrationConfig:
    current_schema_version: int = 1
    automatic_migrations: bool = True
    backup_before_migration: bool = True
    reject_future_schema_versions: bool = True
    extra_fields: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.current_schema_version < 1:
            raise ValueError("current_schema_version must be greater than or equal to 1")
        _validate_extra_fields(self.extra_fields)


@dataclass(slots=True)
class GuildConfiguration:
    guild_id: int
    guild_name: str
    schema_version: int = 1
    setup_completed: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    configuration_version: int = 1
    wash_crew_role_id: Optional[int] = None
    administrator_override: bool = True
    watch_party_role: WatchPartyRoleConfig = field(default_factory=WatchPartyRoleConfig)
    suggestion_databases: tuple[GuildSuggestionDatabaseEntry, ...] = field(default_factory=tuple)
    channels: GuildChannelsConfig = field(default_factory=GuildChannelsConfig)
    voting_defaults: VotingDefaultsConfig = field(default_factory=VotingDefaultsConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    feature_flags: FeatureFlagsConfig = field(default_factory=FeatureFlagsConfig)
    backup: BackupConfig = field(default_factory=BackupConfig)
    watch_history: WatchHistoryConfig = field(default_factory=WatchHistoryConfig)
    migration: MigrationConfig = field(default_factory=MigrationConfig)
    extra_fields: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.guild_name = self.guild_name.strip()
        if not isinstance(self.guild_id, int) or isinstance(self.guild_id, bool) or self.guild_id <= 0:
            raise ValueError("guild_id must be a positive integer")
        if not self.guild_name:
            raise ValueError("guild_name must not be empty")
        if self.schema_version < 1:
            raise ValueError("schema_version must be greater than or equal to 1")
        if self.configuration_version < 1:
            raise ValueError("configuration_version must be greater than or equal to 1")
        _validate_optional_snowflake(self.wash_crew_role_id, "wash_crew_role_id")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("created_at and updated_at must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be earlier than created_at")
        ids = [entry.id for entry in self.suggestion_databases]
        if len(ids) != len(set(ids)):
            raise ValueError("suggestion database IDs must be unique")
        _validate_extra_fields(self.extra_fields)
