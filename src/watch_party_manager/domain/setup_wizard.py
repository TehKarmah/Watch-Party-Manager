"""Domain models for FR-028's resumable /setup wizard.

This is scratch/draft state for the wizard itself -- entirely separate
from GuildConfiguration (the final, authoritative configuration record).
Nothing here replaces or restructures GuildConfiguration,
SuggestionDatabaseConfiguration, or any other existing persisted model;
the wizard's only job is to collect values across several Discord
interactions and, once validated, hand them off to the existing
repositories (GuildConfigurationRepository,
SuggestionDatabaseConfigurationRepository) exactly as any other caller
would. See services/setup_wizard_service.py for that hand-off.

Mirrors guild_configuration.py's conventions where they apply: slots-based
dataclasses, an extra_fields dict is deliberately NOT included here since
this state is never migrated across schema versions -- it is transient by
design and safe to discard/restart if it ever fails to parse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from watch_party_manager.domain.guild_configuration import GuildVoteVisibility, JoinMode
from watch_party_manager.domain.suggestion_database_configuration import CandidateSelectionMode


class SetupWizardStep(str, Enum):
    """One screen of the /setup wizard, in the suggested walkthrough order."""

    WASH_CREW_ROLE = "wash_crew_role"
    WATCH_PARTY_ROLE = "watch_party_role"
    ADMIN_CHANNEL = "admin_channel"
    SUGGESTION_DATABASE = "suggestion_database"
    WATCH_DESTINATION = "watch_destination"
    VOTING_DEFAULTS = "voting_defaults"
    REMINDER_DEFAULTS = "reminder_defaults"
    BACKUP_DEFAULTS = "backup_defaults"
    REVIEW = "review"


# The wizard's walkthrough order -- also what drives progress display
# ("Step 3 of 9") and the Review screen's "jump to a section" menu.
SETUP_WIZARD_STEP_ORDER: tuple[SetupWizardStep, ...] = (
    SetupWizardStep.WASH_CREW_ROLE,
    SetupWizardStep.WATCH_PARTY_ROLE,
    SetupWizardStep.ADMIN_CHANNEL,
    SetupWizardStep.SUGGESTION_DATABASE,
    SetupWizardStep.WATCH_DESTINATION,
    SetupWizardStep.VOTING_DEFAULTS,
    SetupWizardStep.REMINDER_DEFAULTS,
    SetupWizardStep.BACKUP_DEFAULTS,
    SetupWizardStep.REVIEW,
)


class SetupWizardStatus(str, Enum):
    """Lifecycle of one guild's in-progress setup attempt."""

    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


def _validate_optional_snowflake(value: Optional[int], field_name: str) -> None:
    if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value <= 0):
        raise ValueError(f"{field_name} must be a positive integer when provided")


@dataclass(slots=True)
class SetupWizardDraft:
    """The unsaved values collected so far, one field per wizard step.

    Every field defaults to None/False -- "not yet answered" -- which is
    exactly what makes a step show as "Incomplete" on the Review screen
    (see SetupWizardService.build_review_lines()) rather than
    "Configured" or "Skipped". A step that was explicitly skipped sets
    its own *_skipped flag instead of leaving its value fields blank, so
    "not yet visited" and "visited, deliberately left blank" are never
    confused with each other.
    """

    wash_crew_role_id: Optional[int] = None

    watch_party_role_id: Optional[int] = None
    watch_party_join_mode: Optional[JoinMode] = None

    suggestion_database_id: Optional[int] = None
    suggestion_database_name: Optional[str] = None
    suggestion_database_is_new: bool = False

    admin_channel_id: Optional[int] = None
    admin_channel_skipped: bool = False

    watch_destination_channel_id: Optional[int] = None
    watch_destination_skipped: bool = False

    voting_candidate_count: Optional[int] = None
    voting_duration_days: Optional[int] = None
    voting_visibility: Optional[GuildVoteVisibility] = None
    voting_candidate_selection: Optional[CandidateSelectionMode] = None

    reminder_enabled: Optional[bool] = None
    reminder_hours_before_close: Optional[int] = None

    backup_interval_days: Optional[int] = None
    backup_retention_count: Optional[int] = None

    def __post_init__(self) -> None:
        _validate_optional_snowflake(self.wash_crew_role_id, "wash_crew_role_id")
        _validate_optional_snowflake(self.watch_party_role_id, "watch_party_role_id")
        _validate_optional_snowflake(self.suggestion_database_id, "suggestion_database_id")
        _validate_optional_snowflake(self.admin_channel_id, "admin_channel_id")
        _validate_optional_snowflake(self.watch_destination_channel_id, "watch_destination_channel_id")


@dataclass(slots=True)
class SetupWizardState:
    """One guild's resumable /setup progress.

    guild_id is this record's identity (see SetupWizardRepository, which
    is keyed the same way GuildConfigurationRepository is). current_step
    drives which screen /setup shows if resumed; completed_steps is used
    only for progress display ("Step 3 of 8" / a checklist) -- it is not
    itself re-validated until the Review screen's final validation pass.
    """

    guild_id: int
    status: SetupWizardStatus = SetupWizardStatus.IN_PROGRESS
    current_step: SetupWizardStep = SetupWizardStep.WASH_CREW_ROLE
    completed_steps: tuple[SetupWizardStep, ...] = ()
    draft: SetupWizardDraft = field(default_factory=SetupWizardDraft)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not isinstance(self.guild_id, int) or isinstance(self.guild_id, bool) or self.guild_id <= 0:
            raise ValueError("guild_id must be a positive integer")
        if self.started_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("started_at and updated_at must be timezone-aware")
        if len(self.completed_steps) != len(set(self.completed_steps)):
            raise ValueError("completed_steps must not contain duplicates")

    def with_step_completed(self, step: SetupWizardStep) -> tuple[SetupWizardStep, ...]:
        """Return completed_steps with `step` added, without duplicating it."""
        if step in self.completed_steps:
            return self.completed_steps
        return (*self.completed_steps, step)
