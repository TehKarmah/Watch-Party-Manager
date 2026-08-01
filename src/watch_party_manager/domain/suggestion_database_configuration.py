"""Domain models for per-database Suggestion Database Configuration.

Mirrors the architecture used by guild_configuration.py: nested,
slots-based dataclasses per settings section, unknown-field preservation
via extra_fields on every level, and reuse of guild_configuration's
shared validation helpers and enums where the same concept applies (blind
vote visibility, tie behavior).

Identifier semantics (see also the repository module's docstring):
    guild_id, database_id together are this record's immutable composite
    identity. database_id matches the numeric database_id used by the
    existing operational SuggestionDatabase model
    (watch_party_manager.domain.suggestion_database) -- this is a
    configuration record FOR an existing operational suggestion database,
    not a new identifier scheme. It is intentionally NOT the same as
    GuildSuggestionDatabaseEntry.id (a separate, string-based identifier
    used only within GuildConfiguration.suggestion_databases). Per
    docs/guild_configuration_spec.md, reconciling those two identifier
    schemes is an explicitly deferred decision and is not addressed here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from watch_party_manager.domain.guild_configuration import (
    GuildVoteVisibility,
    TieBehavior,
    _coerce_enum,
    _validate_extra_fields,
    _validate_optional_snowflake,
    _validate_positive_int,
)


class CandidateSelectionMode(str, Enum):
    """How nominees are chosen for a voting round (FR-033B; user-facing
    name is "Nominee Selection").

    Rotation Removal: this is now a closed set of three rotation-free
    modes -- every eligible suggestion is always immediately selectable,
    with no suggestion ever excluded or held back based on presentation
    history. See services/candidate_selection_strategy.py for the
    algorithms.

    INFINITE_POOL ("Pure Random"): every eligible suggestion, neutral
    weight. FAVOR_NEW_ADDITIONS ("Favor New Additions") and
    FAVOR_OLDER_ADDITIONS ("Favor Older Additions") weight by
    WatchItemJourney.suggestion_date (permanent metadata). FAVOR_NEW_
    ADDITIONS is the default for newly created collections.
    """

    INFINITE_POOL = "infinite_pool"
    FAVOR_NEW_ADDITIONS = "favor_new_additions"
    FAVOR_OLDER_ADDITIONS = "favor_older_additions"


# The single source of truth for how each mode is labeled in Discord UI
# (Setup Wizard, /config, and any summary text) -- kept here, next to the
# enum itself, so every caller (setup_wizard_view.py, bot.py's /config
# and setup summaries) shows the exact same wording rather than each
# inventing its own.
CANDIDATE_SELECTION_DISPLAY_LABELS: dict[CandidateSelectionMode, str] = {
    CandidateSelectionMode.INFINITE_POOL: "Pure Random",
    CandidateSelectionMode.FAVOR_NEW_ADDITIONS: "Favor New Additions",
    CandidateSelectionMode.FAVOR_OLDER_ADDITIONS: "Favor Older Additions",
}

# UI Polish: a one-sentence, plain-language explanation for each mode,
# shown everywhere a WASH Crew member is asked to choose one (the
# Setup Wizard's and /vote start Customize This Vote's dropdown option
# descriptions, /config's Nominee Selection screen, and the Expanded
# Help/Administration docs) so nobody has to already understand the
# underlying algorithm to make an informed choice. Kept here, next to
# CANDIDATE_SELECTION_DISPLAY_LABELS, for the same single-source-of-truth
# reason. Each string is deliberately <=100 characters so it also fits
# verbatim in a Discord SelectOption's description field (Discord's own
# hard limit), rather than needing a separate, longer variant that could
# drift out of sync with this one.
CANDIDATE_SELECTION_HELP_TEXT: dict[CandidateSelectionMode, str] = {
    CandidateSelectionMode.INFINITE_POOL: (
        "Chooses completely at random from eligible suggestions, with no preference or exclusion."
    ),
    CandidateSelectionMode.FAVOR_NEW_ADDITIONS: (
        "Leans toward suggestions added recently; older ones stay eligible, just at a lower chance."
    ),
    CandidateSelectionMode.FAVOR_OLDER_ADDITIONS: (
        "Leans toward suggestions waiting the longest; newer ones stay eligible, just at a lower chance."
    ),
}

# The Nominee Selection choices in display order, FAVOR_NEW_ADDITIONS
# first as the default.
NOMINEE_SELECTION_MODE_ORDER: tuple[CandidateSelectionMode, ...] = (
    CandidateSelectionMode.FAVOR_NEW_ADDITIONS,
    CandidateSelectionMode.FAVOR_OLDER_ADDITIONS,
    CandidateSelectionMode.INFINITE_POOL,
)


@dataclass(slots=True)
class SuggestionDatabaseChannelsConfig:
    """Discord channels specific to one suggestion database.

    All four are optional; an unconfigured channel serializes as null.
    Suggestion and voting may intentionally share the same channel (no
    check against that), but watch history and archive must be different
    channels from each other when both are configured.
    """

    suggestion_channel_id: Optional[int] = None
    voting_channel_id: Optional[int] = None
    watch_history_channel_id: Optional[int] = None
    archive_channel_id: Optional[int] = None
    extra_fields: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        _validate_optional_snowflake(self.suggestion_channel_id, "suggestion_channel_id")
        _validate_optional_snowflake(self.voting_channel_id, "voting_channel_id")
        _validate_optional_snowflake(self.watch_history_channel_id, "watch_history_channel_id")
        _validate_optional_snowflake(self.archive_channel_id, "archive_channel_id")
        if (
            self.watch_history_channel_id is not None
            and self.archive_channel_id is not None
            and self.watch_history_channel_id == self.archive_channel_id
        ):
            raise ValueError("watch_history_channel_id and archive_channel_id must be different channels")
        _validate_extra_fields(self.extra_fields)


@dataclass(slots=True)
class VotingOverridesConfig:
    """Per-database overrides for voting behavior.

    Every field defaults to None, meaning "inherit Guild Configuration's
    voting_defaults" -- resolving that inheritance is a future consumer's
    responsibility; this configuration-only model just stores the
    override (or absence of one). Bounds mirror
    guild_configuration.VotingDefaultsConfig's validation exactly --
    duration is minute-based in both models (Release Candidate Polish:
    Vote Duration made Guild Configuration's own duration_minutes match
    this field's unit and bounds exactly).
    """

    candidate_count: Optional[int] = None
    duration_minutes: Optional[int] = None
    visibility: Optional[GuildVoteVisibility] = None
    max_vote_changes: Optional[int] = None
    tie_behavior: Optional[TieBehavior] = None
    extra_fields: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.candidate_count is not None:
            _validate_positive_int(self.candidate_count, "candidate_count", 2, 10)
        if self.duration_minutes is not None:
            _validate_positive_int(self.duration_minutes, "duration_minutes", 1, 720 * 60)
        if self.visibility is not None:
            self.visibility = _coerce_enum(self.visibility, GuildVoteVisibility, "visibility")  # type: ignore[assignment]
        if self.max_vote_changes is not None:
            _validate_positive_int(self.max_vote_changes, "max_vote_changes", 0, 10)
        if self.tie_behavior is not None:
            self.tie_behavior = _coerce_enum(self.tie_behavior, TieBehavior, "tie_behavior")  # type: ignore[assignment]
        _validate_extra_fields(self.extra_fields)


@dataclass(slots=True)
class SuggestionRulesConfig:
    """Rules governing how suggestions are entered and selected.

    A new suggestion becomes eligible for nomination immediately upon
    creation -- there is no admission delay or mode to configure.
    """

    allow_imdb_links: bool = True
    allow_manual_titles: bool = True
    require_unique_active_titles: bool = True
    rejection_threshold: int = 2
    allow_resuggestion: bool = True
    candidate_selection: CandidateSelectionMode = CandidateSelectionMode.FAVOR_NEW_ADDITIONS
    extra_fields: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self.allow_imdb_links and not self.allow_manual_titles:
            raise ValueError(
                "at least one input method (allow_imdb_links or allow_manual_titles) must be enabled"
            )
        _validate_positive_int(self.rejection_threshold, "rejection_threshold", 1, 10)
        self.candidate_selection = _coerce_enum(  # type: ignore[assignment]
            self.candidate_selection, CandidateSelectionMode, "candidate_selection"
        )
        _validate_extra_fields(self.extra_fields)


@dataclass(slots=True)
class SuggestionDatabaseWatchHistoryConfig:
    """Watch history settings for one database. Configuration only -- no
    operational behavior is implemented here."""

    enabled: bool = True
    allow_retroactive_entries: bool = True
    allow_repeat_watches: bool = True
    include_watch_date: bool = True
    include_vote_result: bool = True
    extra_fields: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        _validate_extra_fields(self.extra_fields)


@dataclass(slots=True)
class SuggestionDatabaseArchiveConfig:
    """Archive settings for one database. Configuration only -- no
    operational behavior is implemented here."""

    enabled: bool = True
    archive_winner_after_watch: bool = True
    archive_rejected_suggestions: bool = True
    allow_resuggestion: bool = True
    extra_fields: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        _validate_extra_fields(self.extra_fields)


@dataclass(slots=True)
class SuggestionDatabaseNotificationOverridesConfig:
    """Per-database notification overrides for the Eligible Pool Warning
    (Rotation & Collection Health; formerly the interval-based Low Pool
    Reminder, FR-033B Section 7).

    low_suggestion_pool_alerts/low_suggestion_pool_threshold default to
    None, meaning "inherit Guild Configuration"
    (AdministrativeNotificationsConfig.low_suggestion_pool /
    low_suggestion_pool_threshold) -- the guild-level Destination
    (Admin Channel or Watch Party Home Channel) has no per-database
    override, since the notification deliberately never posts to a
    collection's own suggestion thread. The old destination_channel_id and
    minimum_interval_hours fields (a raw per-database channel override and
    a resend-interval, respectively) had no equivalent in the current,
    threshold-crossing/guild-destination design and were removed; a
    configuration file saved before this change that still has those keys
    loads without error -- their values are preserved verbatim in
    extra_fields (never interpreted, never re-validated) rather than
    causing a load failure. Member-level notification preferences are
    explicitly out of scope -- this section only ever holds database-wide
    settings.
    """

    low_suggestion_pool_alerts: Optional[bool] = None
    low_suggestion_pool_threshold: Optional[int] = None
    extra_fields: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.low_suggestion_pool_threshold is not None:
            _validate_positive_int(
                self.low_suggestion_pool_threshold, "low_suggestion_pool_threshold", 1, 1000
            )
        _validate_extra_fields(self.extra_fields)


@dataclass(slots=True)
class SuggestionDatabasePermissionsConfig:
    """Database-scoped moderator permissions.

    moderator_role_ids grants authority over this database only, never
    guild-wide authority -- Guild WASH Crew remains the sole guild-wide
    authority regardless of what's configured here. Duplicate role IDs
    are normalized (deduplicated, first-seen order preserved) rather than
    rejected outright, since a duplicate entry is far more likely to be
    accidental input than a meaningful error condition.
    """

    moderator_role_ids: tuple[int, ...] = field(default_factory=tuple)
    use_guild_watch_party_role: bool = True
    extra_fields: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.moderator_role_ids = self._normalize_role_ids(self.moderator_role_ids)
        _validate_extra_fields(self.extra_fields)

    @staticmethod
    def _normalize_role_ids(role_ids: Any) -> tuple[int, ...]:
        normalized: list[int] = []
        for role_id in role_ids:
            if not isinstance(role_id, int) or isinstance(role_id, bool) or role_id <= 0:
                raise ValueError("moderator_role_ids must contain only positive integers")
            if role_id not in normalized:
                normalized.append(role_id)
        return tuple(normalized)


@dataclass(slots=True)
class SuggestionDatabaseConfiguration:
    """The persisted configuration for a single suggestion database.

    (guild_id, database_id) together are this record's immutable
    composite primary key -- see the module docstring for how database_id
    relates to the existing operational SuggestionDatabase model.
    configuration_version and updated_at are managed by
    SuggestionDatabaseConfigurationRepository.save() (auto-incremented /
    refreshed), not by this model itself; created_at is preserved by the
    repository across updates for the same identity.
    """

    guild_id: int
    database_id: int
    display_name: str
    active: bool = True
    schema_version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    configuration_version: int = 1
    channels: SuggestionDatabaseChannelsConfig = field(default_factory=SuggestionDatabaseChannelsConfig)
    voting_overrides: VotingOverridesConfig = field(default_factory=VotingOverridesConfig)
    suggestion_rules: SuggestionRulesConfig = field(default_factory=SuggestionRulesConfig)
    watch_history: SuggestionDatabaseWatchHistoryConfig = field(
        default_factory=SuggestionDatabaseWatchHistoryConfig
    )
    archive: SuggestionDatabaseArchiveConfig = field(default_factory=SuggestionDatabaseArchiveConfig)
    notifications: SuggestionDatabaseNotificationOverridesConfig = field(
        default_factory=SuggestionDatabaseNotificationOverridesConfig
    )
    permissions: SuggestionDatabasePermissionsConfig = field(
        default_factory=SuggestionDatabasePermissionsConfig
    )
    extra_fields: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.display_name = self.display_name.strip()
        if not isinstance(self.guild_id, int) or isinstance(self.guild_id, bool) or self.guild_id <= 0:
            raise ValueError("guild_id must be a positive integer")
        if (
            not isinstance(self.database_id, int)
            or isinstance(self.database_id, bool)
            or self.database_id <= 0
        ):
            raise ValueError("database_id must be a positive integer")
        if not self.display_name:
            raise ValueError("display_name must not be empty")
        if self.schema_version < 1:
            raise ValueError("schema_version must be greater than or equal to 1")
        if self.configuration_version < 1:
            raise ValueError("configuration_version must be greater than or equal to 1")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("created_at and updated_at must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be earlier than created_at")
        _validate_extra_fields(self.extra_fields)
