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


# UI Polish: the one shared, human-readable label for each join mode --
# mirrors CANDIDATE_SELECTION_DISPLAY_LABELS's rationale (see
# suggestion_database_configuration.py) so every surface that names a
# join mode (the Setup Wizard's own select options, its Review/
# Completion summaries, and /config's summary and save confirmation)
# shows the same wording instead of the raw snake_case enum value.
JOIN_MODE_DISPLAY_LABELS: dict[JoinMode, str] = {
    JoinMode.MANUAL: "Manual",
    JoinMode.SELF_SERVICE: "Self-Service",
    JoinMode.APPROVAL: "Approval",
    JoinMode.DISCORD_MANAGED: "Discord-Managed",
}


class GuildVoteVisibility(str, Enum):
    BLIND = "blind"
    VISIBLE = "visible"


# UI Polish: a single, plain-language explanation of the two visibility
# modes, shown everywhere a WASH Crew member is asked to choose or
# confirm one -- the Setup Wizard's and /config's Voting Defaults
# screens, /vote start's Customize This Vote, and the Expanded
# Help/Administration docs -- so the choice never has to be inferred
# from the bare word "Blind"/"Visible" alone. Kept here, next to the
# enum itself, as the one source of truth for this wording. Trimmed to
# fit Discord's 100-character SelectOption description limit -- used
# where a live VisibilitySelectComponent dropdown's own per-option
# descriptions aren't yet on screen (e.g. /config's main menu option for
# Voting Defaults, which has no other room for explanatory body text
# before its modal opens). Everywhere the dropdown itself is already
# shown, its per-option descriptions are the explanation -- no separate
# help text is needed alongside it (see VisibilitySelectComponent).
VISIBILITY_HELP_TEXT_SHORT = "Visible: totals shown live. Blind: hidden until voting closes."


class TieBehavior(str, Enum):
    ALL_WINNERS = "all_winners"


class RotationLowPoolNotificationDestination(str, Enum):
    """Where the Rotation Low-Pool notification (Rotation & Collection
    Health) posts -- never a collection's suggestion thread (that option
    is deliberately not offered; see the notification service's module
    docstring)."""

    ADMIN_CHANNEL = "admin_channel"
    HOME_CHANNEL = "home_channel"


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
    # Contextual Collections refinement: the guild-wide default watched-
    # movie destination. A collection's own
    # SuggestionDatabaseConfiguration.channels.watch_history_channel_id
    # overrides this when set -- see
    # services/config_service.py's resolve_effective_watch_destination().
    # Unlike a suggestion destination, this may be None (no watch
    # history posted) and may be shared across every collection.
    watch_history_channel_id: Optional[int] = None
    # Command Structure Cleanup: the channel /setup's Home Channel step
    # created or selected, persisted here so it remains resolvable after
    # setup completes (the wizard's own draft is deleted on finalize) --
    # /database add and /database move's "Create New Thread" both need
    # it to create a collection's suggestion thread as a sibling under
    # it, the same way the wizard itself already does.
    home_channel_id: Optional[int] = None
    extra_fields: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        _validate_optional_snowflake(self.announcements_channel_id, "announcements_channel_id")
        _validate_optional_snowflake(self.log_channel_id, "log_channel_id")
        _validate_optional_snowflake(self.admin_channel_id, "admin_channel_id")
        _validate_optional_snowflake(self.watch_history_channel_id, "watch_history_channel_id")
        _validate_optional_snowflake(self.home_channel_id, "home_channel_id")
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
    # Minutes is the single internal unit for voting duration (Release
    # Candidate Polish: Vote Duration), matching
    # VoteNotificationsConfig.reminder_minutes_before_close's own
    # minute-precision convention -- 1440 minutes preserves the previous
    # one-day default exactly. See
    # GuildConfigurationRepository._resolve_duration_minutes for
    # backward-compatible loading of older hour-based persisted values.
    duration_minutes: int = 24 * 60
    visibility: GuildVoteVisibility = GuildVoteVisibility.VISIBLE
    max_vote_changes: int = 1
    tie_behavior: TieBehavior = TieBehavior.ALL_WINNERS
    extra_fields: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        _validate_positive_int(self.candidate_count, "candidate_count", 2, 10)
        _validate_positive_int(self.duration_minutes, "duration_minutes", 1, 720 * 60)
        _validate_positive_int(self.max_vote_changes, "max_vote_changes", 0, 10)
        self.visibility = _coerce_enum(self.visibility, GuildVoteVisibility, "visibility")  # type: ignore[assignment]
        self.tie_behavior = _coerce_enum(self.tie_behavior, TieBehavior, "tie_behavior")  # type: ignore[assignment]
        _validate_extra_fields(self.extra_fields)


@dataclass(slots=True)
class VoteNotificationsConfig:
    vote_started: bool = True
    vote_results: bool = True
    vote_ending_reminder: bool = True
    reminder_minutes_before_close: int = 24 * 60
    extra_fields: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        _validate_positive_int(self.reminder_minutes_before_close, "reminder_minutes_before_close", 1, 720 * 60)
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
    """low_suggestion_pool/low_suggestion_pool_threshold/
    low_suggestion_pool_destination together configure the Rotation
    Low-Pool notification (Rotation & Collection Health), which replaced
    the older, interval-based Low Pool Reminder.

    low_suggestion_pool_threshold defaults to None ("auto": fewer
    eligible suggestions than two configured voting rounds, computed
    dynamically against the guild's current candidate_count rather than
    a fixed number, since candidate_count is itself configurable). A
    guild configuration saved before this milestone already has a
    concrete threshold (the old flat default was 10) -- that explicit
    value is preserved and used as-is; only a guild that has never set
    this at all gets the new dynamic default.
    """

    low_suggestion_pool: bool = True
    low_suggestion_pool_threshold: Optional[int] = None
    low_suggestion_pool_destination: RotationLowPoolNotificationDestination = (
        RotationLowPoolNotificationDestination.ADMIN_CHANNEL
    )
    backup_completed: bool = True
    backup_failed: bool = True
    restore_completed: bool = True
    restore_failed: bool = True
    extra_fields: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.low_suggestion_pool_threshold is not None:
            _validate_positive_int(self.low_suggestion_pool_threshold, "low_suggestion_pool_threshold", 1, 1000)
        self.low_suggestion_pool_destination = _coerce_enum(  # type: ignore[assignment]
            self.low_suggestion_pool_destination, RotationLowPoolNotificationDestination, "low_suggestion_pool_destination"
        )
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
