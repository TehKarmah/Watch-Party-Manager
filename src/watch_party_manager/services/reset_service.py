"""FR-032C: suggestion database reset and factory reset.

Both destructive operations follow the same shape: build a read-only
impact summary, require an exact typed confirmation (see
type_to_confirm_view.py), create a full safety backup via the existing
BackupService.create_backup(), and only then mutate data through the
same repositories every other service in this project already uses
(load() the full collection, filter, save() it back) -- no new
persistence mechanism, matching FR-032B's database_backup_service.py's
approach to guild-scoped filtering.

Factory reset guild-scopes every WASH-managed store: it never wipes
another guild's data even though several of these JSON files are not
strictly single-guild documents. Backup archives, environment files,
the bot token, application code, the virtual environment, and logs are
untouched by definition -- this service never touches anything outside
the repository objects it's given, and none of those ever point at
those locations.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from watch_party_manager.persistence.guild_configuration_repository import GuildConfigurationRepository
from watch_party_manager.persistence.membership_request_repository import MembershipRequestRepository
from watch_party_manager.persistence.setup_wizard_repository import SetupWizardRepository
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.persistence.watch_party_repository import JsonWatchPartyRepository
from watch_party_manager.scheduler.json_scheduler_repository import JsonSchedulerRepository
from watch_party_manager.services.backup_service import BackupError, BackupKind, BackupService

# --- Suggestion database reset (Section 1) ------------------------------------------


@dataclass(frozen=True, slots=True)
class DatabaseResetSummary:
    """Read-only preview of what /database_reset would remove."""

    database_id: int
    database_name: str
    suggestion_count: int


def build_database_reset_summary(
    database_repository: JsonSuggestionDatabaseRepository,
    suggestion_repository: JsonSuggestionRepository,
    guild_id: int,
    database_id: int,
) -> Optional[DatabaseResetSummary]:
    """Build the impact summary shown before WASH Crew types RESET.

    Returns None when the database doesn't exist, so the caller can
    reject the request before ever reaching the confirmation step.
    """
    database = next(
        (
            candidate
            for candidate in database_repository.load().databases
            if candidate.database_id == database_id and candidate.guild_id == guild_id
        ),
        None,
    )
    if database is None:
        return None

    suggestion_count = sum(
        1 for item in suggestion_repository.load().watch_items if item.database_id == database_id
    )
    return DatabaseResetSummary(
        database_id=database.database_id, database_name=database.name, suggestion_count=suggestion_count
    )


@dataclass(frozen=True, slots=True)
class DatabaseResetResult:
    """Result of an actual /database_reset execution."""

    success: bool
    message: str
    removed_count: int = 0
    safety_backup: Optional[Path] = None


def reset_suggestion_database(
    backup_service: BackupService,
    database_repository: JsonSuggestionDatabaseRepository,
    suggestion_repository: JsonSuggestionRepository,
    guild_id: int,
    database_id: int,
) -> DatabaseResetResult:
    """Remove every suggestion belonging to one database.

    The database record, its configuration, and every other database
    (including other databases in this same guild) are left completely
    untouched -- only suggestions.json is written.
    """
    database = next(
        (
            candidate
            for candidate in database_repository.load().databases
            if candidate.database_id == database_id and candidate.guild_id == guild_id
        ),
        None,
    )
    if database is None:
        return DatabaseResetResult(False, "No suggestion database with that ID exists in this server.")

    try:
        safety_backup = backup_service.create_backup(BackupKind.MANUAL, enforce_retention=False).archive_path
    except BackupError as exc:
        return DatabaseResetResult(
            False, f"Safety backup failed, so the reset was aborted. Live data was NOT changed: {exc}"
        )

    suggestion_load = suggestion_repository.load()
    remaining = [item for item in suggestion_load.watch_items if item.database_id != database_id]
    removed_count = len(suggestion_load.watch_items) - len(remaining)
    suggestion_repository.save(remaining, suggestion_load.next_id)

    return DatabaseResetResult(
        True,
        f'Suggestion database "{database.name}" has been reset: {removed_count} suggestion(s) removed. '
        "The database, its configuration, and other databases were not affected. "
        f"A safety backup was made first: `{safety_backup.name}`.",
        removed_count=removed_count,
        safety_backup=safety_backup,
    )


# --- Factory reset (Section 2) -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FactoryResetSummary:
    """Read-only preview of what /factory_reset would remove for one guild."""

    configuration_present: bool
    suggestion_database_count: int
    suggestion_count: int
    vote_round_count: int
    membership_request_count: int
    watch_party_count: int
    scheduled_job_count: int


async def build_factory_reset_summary(
    *,
    guild_configuration_repository: GuildConfigurationRepository,
    database_repository: JsonSuggestionDatabaseRepository,
    suggestion_repository: JsonSuggestionRepository,
    vote_repository: JsonVoteRepository,
    membership_request_repository: MembershipRequestRepository,
    watch_party_repository: JsonWatchPartyRepository,
    scheduler_repository: JsonSchedulerRepository,
    guild_id: int,
) -> FactoryResetSummary:
    """Count what a factory reset would remove, without changing anything."""
    scheduled_jobs = await scheduler_repository.list_all()
    return FactoryResetSummary(
        configuration_present=guild_configuration_repository.get(guild_id) is not None,
        suggestion_database_count=sum(
            1 for database in database_repository.load().databases if database.guild_id == guild_id
        ),
        suggestion_count=sum(1 for item in suggestion_repository.load().watch_items if item.guild_id == guild_id),
        vote_round_count=sum(1 for round_ in vote_repository.load().rounds if round_.guild_id == guild_id),
        membership_request_count=(
            len(membership_request_repository.get_pending(guild_id))
            + len(membership_request_repository.get_approved(guild_id))
            + len(membership_request_repository.get_denied(guild_id))
        ),
        watch_party_count=sum(
            1 for watch_party in watch_party_repository.load().watch_parties if watch_party.guild_id == guild_id
        ),
        scheduled_job_count=sum(1 for job in scheduled_jobs if job.guild_id == guild_id),
    )


@dataclass(frozen=True, slots=True)
class FactoryResetResult:
    """Result of an actual /factory_reset execution."""

    success: bool
    message: str
    safety_backup: Optional[Path] = None


async def factory_reset(
    *,
    backup_service: BackupService,
    guild_configuration_repository: GuildConfigurationRepository,
    setup_wizard_repository: SetupWizardRepository,
    database_repository: JsonSuggestionDatabaseRepository,
    suggestion_repository: JsonSuggestionRepository,
    configuration_repository: SuggestionDatabaseConfigurationRepository,
    vote_repository: JsonVoteRepository,
    membership_request_repository: MembershipRequestRepository,
    watch_party_repository: JsonWatchPartyRepository,
    scheduler_repository: JsonSchedulerRepository,
    guild_id: int,
) -> FactoryResetResult:
    """Remove every WASH-managed record belonging to one guild.

    Every other guild's data (in a hypothetical multi-guild deployment)
    is left untouched, since every store is filtered by guild_id rather
    than cleared wholesale. GuildConfiguration is deleted last so every
    other removal happens while the guild is still considered "set up";
    once it's gone, /setup is required again through the existing,
    unmodified perform_setup_redirect_check() logic.
    """
    try:
        safety_backup = backup_service.create_backup(BackupKind.MANUAL, enforce_retention=False).archive_path
    except BackupError as exc:
        return FactoryResetResult(
            False, f"Safety backup failed, so the factory reset was aborted. Live data was NOT changed: {exc}"
        )

    database_load = database_repository.load()
    database_repository.save(
        [database for database in database_load.databases if database.guild_id != guild_id], database_load.next_id
    )

    suggestion_load = suggestion_repository.load()
    suggestion_repository.save(
        [item for item in suggestion_load.watch_items if item.guild_id != guild_id], suggestion_load.next_id
    )

    configuration_repository.delete_for_guild(guild_id)

    vote_load = vote_repository.load()
    vote_repository.save(
        [round_ for round_ in vote_load.rounds if round_.guild_id != guild_id], vote_load.next_round_id
    )

    membership_load = membership_request_repository.load()
    membership_request_repository.save(
        [request for request in membership_load.requests if request.guild_id != guild_id], membership_load.next_id
    )

    watch_party_load = watch_party_repository.load()
    watch_party_repository.save(
        [watch_party for watch_party in watch_party_load.watch_parties if watch_party.guild_id != guild_id],
        watch_party_load.next_id,
    )

    await scheduler_repository.remove_for_guild(guild_id)

    setup_wizard_repository.delete(guild_id)
    guild_configuration_repository.delete(guild_id)

    return FactoryResetResult(
        True,
        "Factory reset complete. All WASH-managed data for this server has been removed; "
        "WASH will require `/setup` again. "
        f"A safety backup was made first: `{safety_backup.name}`.",
        safety_backup=safety_backup,
    )
