"""Core logic for FR-028's resumable /setup wizard.

Kept free of Discord UI objects (no discord.ui.View/Modal/Interaction
here) so every step, validation rule, and the final save can be
unit-tested without a live Discord connection -- mirroring the project's
established perform_*()/handle_*() split (see bot.py). The Discord layer
(setup_wizard_view.py, and the /setup command in bot.py) only collects
raw input and calls into this service.

This service never redesigns GuildConfiguration, SuggestionDatabase, or
SuggestionDatabaseConfiguration -- it only orchestrates the existing
repositories/services to save exactly the same records those would
already accept, once the wizard's draft has been validated.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from watch_party_manager.domain.guild_configuration import (
    GuildConfiguration,
    GuildVoteVisibility,
    JoinMode,
    VotingDefaultsConfig,
    WatchPartyRoleConfig,
)
from watch_party_manager.domain.setup_wizard import (
    SETUP_WIZARD_STEP_ORDER,
    SetupWizardDraft,
    SetupWizardState,
    SetupWizardStatus,
    SetupWizardStep,
)
from watch_party_manager.domain.suggestion_database_configuration import (
    CandidateSelectionMode,
    SuggestionDatabaseConfiguration,
)
from watch_party_manager.persistence.guild_configuration_repository import (
    GuildConfigurationRepository,
)
from watch_party_manager.persistence.setup_wizard_repository import SetupWizardRepository
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.services.configuration_validation import (
    GuildLookup,
    validate_channel_usable,
    validate_role_exists,
)
from watch_party_manager.services.suggestion_service import SuggestionService

# Backup schedule/retention have no dedicated persisted "Application
# Configuration" model yet (see docs/guild_configuration_spec.md: "Application
# Configuration owns backup schedule, retention, storage location, and
# archive format" -- that model doesn't exist in this codebase). Rather than
# inventing new schema for it (explicitly out of scope: "do not redesign
# configuration persistence"), the wizard stores these two values in
# GuildConfiguration.backup.extra_fields, the exact mechanism that section
# already has for exactly this situation -- forward-compatible settings
# preserved through load/save without altering BackupConfig's schema.
BACKUP_INTERVAL_DAYS_EXTRA_FIELD = "automatic_backup_interval_days"
BACKUP_RETENTION_COUNT_EXTRA_FIELD = "backup_retention_count"

MIN_BACKUP_INTERVAL_DAYS = 1
MAX_BACKUP_INTERVAL_DAYS = 30
MIN_BACKUP_RETENTION_COUNT = 1
MAX_BACKUP_RETENTION_COUNT = 100


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One thing wrong with the draft, discovered only at final validation time."""

    step: SetupWizardStep
    message: str


@dataclass(frozen=True, slots=True)
class FinalizeResult:
    """What happened when the wizard tried to save its configuration."""

    success: bool
    message: str
    issues: Tuple[ValidationIssue, ...] = ()
    configuration: Optional[GuildConfiguration] = None


class SetupWizardService:
    """Orchestrates FR-028's /setup wizard: draft collection, validation,
    and handing the finished draft off to the existing configuration
    repositories exactly as any other caller would.
    """

    def __init__(
        self,
        wizard_repository: SetupWizardRepository,
        guild_configuration_repository: GuildConfigurationRepository,
        suggestion_service: SuggestionService,
        suggestion_database_configuration_repository: SuggestionDatabaseConfigurationRepository,
    ) -> None:
        self._wizard_repository = wizard_repository
        self._guild_configuration_repository = guild_configuration_repository
        self._suggestion_service = suggestion_service
        self._suggestion_database_configuration_repository = suggestion_database_configuration_repository

    # --- Starting, resuming, restarting, cancelling ---------------------------------

    def start_or_resume(self, guild_id: int) -> Tuple[SetupWizardState, bool]:
        """Return the guild's in-progress wizard state, or start a fresh one.

        Returns:
            (state, resumed) -- resumed is True when an existing
            in-progress state was found (the caller should offer
            Continue/Review/Restart rather than silently proceeding).
        """
        existing = self._wizard_repository.get(guild_id)
        if existing is not None and existing.status is SetupWizardStatus.IN_PROGRESS:
            return existing, True

        state = SetupWizardState(guild_id=guild_id)
        self._wizard_repository.save(state)
        return state, False

    def restart(self, guild_id: int) -> SetupWizardState:
        """Discard any existing draft and start fresh at the first step."""
        state = SetupWizardState(guild_id=guild_id)
        self._wizard_repository.save(state)
        return state

    def cancel(self, guild_id: int) -> bool:
        """Discard in-progress wizard state without saving any configuration.

        Returns:
            True if there was in-progress state to discard, False if
            /setup hadn't been started (or was already finished).
        """
        return self._wizard_repository.delete(guild_id)

    # --- Step updates -----------------------------------------------------------------

    def _advance(
        self, state: SetupWizardState, completed_step: SetupWizardStep, draft: SetupWizardDraft
    ) -> SetupWizardState:
        """Persist a step's answer, mark it completed, and move to the next step."""
        next_step = _next_step(completed_step)
        updated = replace(
            state,
            draft=draft,
            current_step=next_step,
            completed_steps=state.with_step_completed(completed_step),
            updated_at=datetime.now(timezone.utc),
        )
        self._wizard_repository.save(updated)
        return updated

    def go_to_step(self, state: SetupWizardState, step: SetupWizardStep) -> SetupWizardState:
        """Jump directly to a step -- used by the Review screen's "edit a section"."""
        updated = replace(state, current_step=step, updated_at=datetime.now(timezone.utc))
        self._wizard_repository.save(updated)
        return updated

    def set_wash_crew_role(self, state: SetupWizardState, role_id: int) -> SetupWizardState:
        draft = replace(state.draft, wash_crew_role_id=role_id)
        return self._advance(state, SetupWizardStep.WASH_CREW_ROLE, draft)

    def set_watch_party_role(
        self, state: SetupWizardState, role_id: Optional[int], join_mode: JoinMode
    ) -> SetupWizardState:
        draft = replace(state.draft, watch_party_role_id=role_id, watch_party_join_mode=join_mode)
        return self._advance(state, SetupWizardStep.WATCH_PARTY_ROLE, draft)

    def select_existing_database(
        self, state: SetupWizardState, database_id: int, *, guild_id: int
    ) -> Tuple[SetupWizardState, str]:
        """Record an existing suggestion database as this guild's choice.

        Reactivates it first if it had been deactivated (FR-028's "set
        the active database"), via SuggestionService.activate_database()
        -- never duplicated here.
        """
        database = self._suggestion_service.get_database(database_id)
        if database is None or database.guild_id != guild_id:
            return state, "That suggestion database doesn't exist."

        if not database.active:
            self._suggestion_service.activate_database(database_id, guild_id)

        draft = replace(
            state.draft,
            suggestion_database_id=database.database_id,
            suggestion_database_name=database.name,
            suggestion_database_is_new=False,
        )
        return self._advance(state, SetupWizardStep.SUGGESTION_DATABASE, draft), (
            f'Selected suggestion database "{database.name}".'
        )

    def create_new_database(
        self, state: SetupWizardState, name: str, channel_id: int, *, guild_id: int
    ) -> Tuple[SetupWizardState, str]:
        """Create a brand-new suggestion database and record it as this guild's choice.

        Reuses SuggestionService.create_database() unchanged -- the
        wizard never duplicates its validation (duplicate names,
        one-database-per-channel, etc.).
        """
        result = self._suggestion_service.create_database(name, guild_id, channel_id)
        if not result.success:
            return state, result.message

        draft = replace(
            state.draft,
            suggestion_database_id=result.database.database_id,
            suggestion_database_name=result.database.name,
            suggestion_database_is_new=True,
        )
        return self._advance(state, SetupWizardStep.SUGGESTION_DATABASE, draft), result.message

    def set_admin_channel(self, state: SetupWizardState, channel_id: int) -> SetupWizardState:
        draft = replace(state.draft, admin_channel_id=channel_id, admin_channel_skipped=False)
        return self._advance(state, SetupWizardStep.ADMIN_CHANNEL, draft)

    def skip_admin_channel(self, state: SetupWizardState) -> SetupWizardState:
        draft = replace(state.draft, admin_channel_id=None, admin_channel_skipped=True)
        return self._advance(state, SetupWizardStep.ADMIN_CHANNEL, draft)

    def set_watch_destination(self, state: SetupWizardState, channel_id: int) -> SetupWizardState:
        draft = replace(state.draft, watch_destination_channel_id=channel_id, watch_destination_skipped=False)
        return self._advance(state, SetupWizardStep.WATCH_DESTINATION, draft)

    def skip_watch_destination(self, state: SetupWizardState) -> SetupWizardState:
        draft = replace(state.draft, watch_destination_channel_id=None, watch_destination_skipped=True)
        return self._advance(state, SetupWizardStep.WATCH_DESTINATION, draft)

    def set_voting_defaults(
        self,
        state: SetupWizardState,
        candidate_count: int,
        duration_days: int,
        visibility: GuildVoteVisibility,
        candidate_selection: CandidateSelectionMode,
    ) -> SetupWizardState:
        draft = replace(
            state.draft,
            voting_candidate_count=candidate_count,
            voting_duration_days=duration_days,
            voting_visibility=visibility,
            voting_candidate_selection=candidate_selection,
        )
        return self._advance(state, SetupWizardStep.VOTING_DEFAULTS, draft)

    def set_reminder_defaults(
        self, state: SetupWizardState, enabled: bool, hours_before_close: int
    ) -> SetupWizardState:
        draft = replace(
            state.draft, reminder_enabled=enabled, reminder_hours_before_close=hours_before_close
        )
        return self._advance(state, SetupWizardStep.REMINDER_DEFAULTS, draft)

    def set_backup_defaults(
        self, state: SetupWizardState, interval_days: int, retention_count: int
    ) -> SetupWizardState:
        draft = replace(
            state.draft, backup_interval_days=interval_days, backup_retention_count=retention_count
        )
        return self._advance(state, SetupWizardStep.BACKUP_DEFAULTS, draft)

    # --- Review -------------------------------------------------------------------------

    def build_review_lines(self, state: SetupWizardState) -> List[str]:
        """Build one summary line per section for the Review screen.

        Each line is prefixed with its status -- Configured, Skipped, or
        Incomplete -- so WASH Crew can see at a glance what still needs
        attention before saving. "Invalid" is never produced here: that
        status only exists once validate() has actually checked a
        configured resource against live Discord state (see
        build_review_lines_with_issues() in setup_wizard_view.py's
        caller, which merges these lines with validate()'s findings).
        """
        draft = state.draft
        lines: List[str] = []

        if draft.wash_crew_role_id is not None:
            lines.append(f"WASH Crew Role: Configured (<@&{draft.wash_crew_role_id}>)")
        else:
            lines.append("WASH Crew Role: Incomplete")

        if draft.watch_party_role_id is not None:
            join_mode = draft.watch_party_join_mode.value if draft.watch_party_join_mode else "self_service"
            lines.append(f"Watch Party Role: Configured (<@&{draft.watch_party_role_id}>, join mode: {join_mode})")
        else:
            lines.append("Watch Party Role: Incomplete")

        if draft.admin_channel_skipped:
            lines.append("Admin Channel: Skipped")
        elif draft.admin_channel_id is not None:
            lines.append(f"Admin Channel: Configured (<#{draft.admin_channel_id}>)")
        else:
            lines.append("Admin Channel: Incomplete")

        if draft.suggestion_database_id is not None:
            action = "created" if draft.suggestion_database_is_new else "selected"
            lines.append(
                f'Suggestion Database: Configured ({action} "{draft.suggestion_database_name}" '
                f"#{draft.suggestion_database_id})"
            )
        else:
            lines.append("Suggestion Database: Incomplete")

        if draft.watch_destination_skipped:
            lines.append("Watched Movie Destination: Skipped")
        elif draft.watch_destination_channel_id is not None:
            lines.append(f"Watched Movie Destination: Configured (<#{draft.watch_destination_channel_id}>)")
        else:
            lines.append("Watched Movie Destination: Incomplete")

        if draft.voting_candidate_count is not None:
            lines.append(
                "Voting Defaults: Configured "
                f"({draft.voting_candidate_count} nominees, {draft.voting_duration_days} day(s), "
                f"{draft.voting_visibility.value}, {draft.voting_candidate_selection.value})"
            )
        else:
            lines.append("Voting Defaults: Incomplete")

        if draft.reminder_enabled is not None:
            if draft.reminder_enabled:
                lines.append(
                    f"Reminder Defaults: Configured (enabled, {draft.reminder_hours_before_close}h before close)"
                )
            else:
                lines.append("Reminder Defaults: Configured (disabled)")
        else:
            lines.append("Reminder Defaults: Incomplete")

        if draft.backup_interval_days is not None:
            lines.append(
                "Backup Defaults: Configured "
                f"(every {draft.backup_interval_days} day(s), keep {draft.backup_retention_count})"
            )
        else:
            lines.append("Backup Defaults: Incomplete")

        return lines

    # --- Validation -----------------------------------------------------------------------

    def validate(self, state: SetupWizardState, guild: GuildLookup) -> List[ValidationIssue]:
        """Check every selected resource still exists and is usable.

        Only resources the draft actually references are checked --
        leaving an optional section incomplete/skipped is never itself a
        validation failure (the Review screen already surfaces that
        distinctly as "Incomplete"/"Skipped", not "Invalid").

        Args:
            state: The wizard state to validate.
            guild: A live Discord guild (or an equivalent fake in tests)
                used to confirm roles/channels still exist and WASH has
                the permissions it needs.

        Returns:
            Every problem found, empty if the draft is ready to save.
        """
        draft = state.draft
        issues: List[ValidationIssue] = []

        wash_crew_error = validate_role_exists(
            draft.wash_crew_role_id, guild, resource_label="WASH Crew role"
        )
        if wash_crew_error:
            issues.append(ValidationIssue(SetupWizardStep.WASH_CREW_ROLE, wash_crew_error))

        watch_party_role_error = validate_role_exists(
            draft.watch_party_role_id, guild, resource_label="Watch Party role"
        )
        if watch_party_role_error:
            issues.append(ValidationIssue(SetupWizardStep.WATCH_PARTY_ROLE, watch_party_role_error))

        admin_channel_error = validate_channel_usable(draft.admin_channel_id, guild, resource_label="Admin channel")
        if admin_channel_error:
            issues.append(ValidationIssue(SetupWizardStep.ADMIN_CHANNEL, admin_channel_error))

        if draft.suggestion_database_id is None:
            issues.append(ValidationIssue(SetupWizardStep.SUGGESTION_DATABASE, "No suggestion database was selected."))
        elif self._suggestion_service.get_database(draft.suggestion_database_id) is None:
            issues.append(
                ValidationIssue(SetupWizardStep.SUGGESTION_DATABASE, "The selected suggestion database no longer exists.")
            )

        destination_error = validate_channel_usable(draft.watch_destination_channel_id, guild)
        if destination_error:
            issues.append(ValidationIssue(SetupWizardStep.WATCH_DESTINATION, destination_error))

        return issues

    # --- Finalize -------------------------------------------------------------------------

    def finalize(self, state: SetupWizardState, guild_id: int, guild_name: str, guild: GuildLookup) -> FinalizeResult:
        """Validate the draft and, if valid, save it as the guild's configuration.

        Never partially saves: GuildConfigurationRepository.save() (and
        the optional SuggestionDatabaseConfigurationRepository.save())
        are only ever called after validate() reports no issues. Deletes
        the wizard's own draft state once the configuration is saved --
        completion is the one case /setup never remains resumable for.

        Args:
            state: The wizard state to finalize.
            guild_id: The Discord guild being configured.
            guild_name: Used only when creating a brand-new
                GuildConfiguration (an existing one keeps its own stored name).
            guild: Used to validate the draft (see validate()).

        Returns:
            FinalizeResult. On failure, nothing was saved and the wizard
            state is untouched, so /setup remains resumable.
        """
        issues = self.validate(state, guild)
        if issues:
            return FinalizeResult(success=False, message="Setup could not be saved.", issues=tuple(issues))

        configuration = self._build_guild_configuration(state, guild_id, guild_name)
        self._guild_configuration_repository.save(configuration)
        self._save_database_configuration_overrides(state, guild_id)
        self._wizard_repository.delete(guild_id)

        return FinalizeResult(success=True, message="Setup complete.", configuration=configuration)

    def _build_guild_configuration(
        self, state: SetupWizardState, guild_id: int, guild_name: str
    ) -> GuildConfiguration:
        draft = state.draft
        existing = self._guild_configuration_repository.get(guild_id)
        base = existing if existing is not None else GuildConfiguration(guild_id=guild_id, guild_name=guild_name)

        updated = replace(
            base,
            setup_completed=True,
            wash_crew_role_id=draft.wash_crew_role_id,
            watch_party_role=WatchPartyRoleConfig(
                role_id=draft.watch_party_role_id,
                join_mode=draft.watch_party_join_mode or JoinMode.SELF_SERVICE,
                allow_self_leave=base.watch_party_role.allow_self_leave,
            ),
        )

        if draft.admin_channel_id is not None or draft.admin_channel_skipped:
            updated = replace(
                updated, channels=replace(base.channels, admin_channel_id=draft.admin_channel_id)
            )

        if draft.voting_candidate_count is not None:
            updated = replace(
                updated,
                voting_defaults=VotingDefaultsConfig(
                    candidate_count=draft.voting_candidate_count,
                    duration_days=draft.voting_duration_days,
                    visibility=draft.voting_visibility,
                    max_vote_changes=base.voting_defaults.max_vote_changes,
                    tie_behavior=base.voting_defaults.tie_behavior,
                ),
            )

        if draft.reminder_enabled is not None:
            updated_vote_notifications = replace(
                base.notifications.vote,
                vote_ending_reminder=draft.reminder_enabled,
                reminder_hours_before_close=draft.reminder_hours_before_close,
            )
            updated = replace(
                updated,
                notifications=replace(base.notifications, vote=updated_vote_notifications),
            )

        if draft.backup_interval_days is not None:
            backup_extra_fields = dict(base.backup.extra_fields)
            backup_extra_fields[BACKUP_INTERVAL_DAYS_EXTRA_FIELD] = draft.backup_interval_days
            backup_extra_fields[BACKUP_RETENTION_COUNT_EXTRA_FIELD] = draft.backup_retention_count
            updated = replace(updated, backup=replace(base.backup, extra_fields=backup_extra_fields))

        return updated

    def _save_database_configuration_overrides(self, state: SetupWizardState, guild_id: int) -> None:
        draft = state.draft
        if draft.suggestion_database_id is None:
            return
        if draft.voting_candidate_selection is None and not draft.watch_destination_channel_id:
            return

        database = self._suggestion_service.get_database(draft.suggestion_database_id)
        if database is None:
            return

        existing = self._suggestion_database_configuration_repository.get(guild_id, draft.suggestion_database_id)
        base = existing if existing is not None else SuggestionDatabaseConfiguration(
            guild_id=guild_id, database_id=draft.suggestion_database_id, display_name=database.name
        )

        updated = base
        if draft.voting_candidate_selection is not None:
            updated = replace(
                updated,
                suggestion_rules=replace(
                    updated.suggestion_rules, candidate_selection=draft.voting_candidate_selection
                ),
            )
        if draft.watch_destination_channel_id is not None:
            updated = replace(
                updated,
                channels=replace(
                    updated.channels, watch_history_channel_id=draft.watch_destination_channel_id
                ),
            )

        self._suggestion_database_configuration_repository.save(updated)


def _next_step(step: SetupWizardStep) -> SetupWizardStep:
    """Return the step after `step` in the walkthrough order, or REVIEW if
    `step` is already the last configurable step.
    """
    index = SETUP_WIZARD_STEP_ORDER.index(step)
    if index + 1 >= len(SETUP_WIZARD_STEP_ORDER):
        return SETUP_WIZARD_STEP_ORDER[-1]
    return SETUP_WIZARD_STEP_ORDER[index + 1]
