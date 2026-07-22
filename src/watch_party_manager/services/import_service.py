"""FR-032C: import a backup created by another WASH instance (merge or replace).

Reuses RestoreSummaryService directly for validation and the read-only
summary (Section 5's required fields are exactly what
build_restore_summary() already produces) -- this module adds only what
/restore doesn't need: guild-scoped merge/replace of the "portable"
subset of a full backup.

"Portable" data is the suggestion/voting content itself: suggestion
databases, their configuration, their suggestions (including embedded
watch history in each WatchItem's journey), and vote rounds. Everything
else in a full backup is intentionally excluded from import, because it
is either Discord-topology-specific to the *source* server (channel
IDs, scheduled reminders tied to specific messages, scheduled watch
parties) or semantically tied to the source guild's own WASH Crew
approval process (membership requests) -- importing it as-is into a
different guild would silently reference channels, messages, or
approval decisions that don't exist here. GuildConfiguration itself
(roles, channels, guild ID) is never touched by either import mode,
per the explicit requirement to preserve it.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional

from watch_party_manager.domain.suggestion_database import SuggestionDatabase
from watch_party_manager.domain.suggestion_database_configuration import SuggestionDatabaseConfiguration
from watch_party_manager.domain.vote import VoteRound
from watch_party_manager.domain.watch_item import WatchItem
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.services.backup_service import (
    BackupError,
    BackupKind,
    BackupManifest,
    BackupService,
    BackupType,
)
from watch_party_manager.services.restore_summary_service import RestoreSummary, build_restore_summary

SUGGESTION_DATABASES_FILE = "suggestion_databases.json"
SUGGESTIONS_FILE = "suggestions.json"
SUGGESTION_DATABASE_CONFIGURATIONS_FILE = "suggestion_database_configurations.json"
VOTING_FILE = "voting.json"
PORTABLE_FILES = (
    SUGGESTION_DATABASES_FILE,
    SUGGESTIONS_FILE,
    SUGGESTION_DATABASE_CONFIGURATIONS_FILE,
    VOTING_FILE,
)

DATA_EXCLUDED_FROM_IMPORT = (
    "Guild configuration (Discord role/channel IDs)",
    "Membership requests and history",
    "Scheduled reminders and jobs",
    "Scheduled watch parties",
)


class ImportMode(str, Enum):
    """How /import should reconcile a backup's portable data with what's
    already here. Never inferred -- the caller must always choose one.
    """

    MERGE = "merge"
    REPLACE = "replace"


def build_import_summary(backup_service: BackupService, archive_path: Path) -> RestoreSummary:
    """Validate an uploaded backup and build its read-only import summary.

    A thin, intentional wrapper: import only ever accepts a full backup
    (one created by another WASH instance's own /backup), so this reuses
    build_restore_summary() with expected_backup_type=FULL exactly as
    /restore does, rather than re-implementing validation or summarizing.
    """
    return build_restore_summary(backup_service, archive_path, expected_backup_type=BackupType.FULL)


def _extract_portable_data(
    archive_path: Path, manifest: BackupManifest
) -> tuple[
    list[SuggestionDatabase], list[WatchItem], list[SuggestionDatabaseConfiguration], list[VoteRound]
]:
    """Extract the backup's portable files through the same repository
    classes that wrote them, returning plain domain objects.

    Mirrors database_backup_service.py's restore-side extraction
    pattern: stage declared files into a temp directory, then read them
    back through JsonSuggestionDatabaseRepository/JsonSuggestionRepository/
    SuggestionDatabaseConfigurationRepository/JsonVoteRepository so
    deserialization is never duplicated here.
    """
    present_paths = {entry.path for entry in manifest.files}
    with TemporaryDirectory() as temporary_directory:
        temp_root = Path(temporary_directory)
        with zipfile.ZipFile(archive_path, mode="r") as archive:
            for path in PORTABLE_FILES:
                if path in present_paths:
                    (temp_root / path).write_bytes(archive.read(path))

        databases: list[SuggestionDatabase] = []
        if (temp_root / SUGGESTION_DATABASES_FILE).exists():
            databases = JsonSuggestionDatabaseRepository(temp_root / SUGGESTION_DATABASES_FILE).load().databases

        suggestions: list[WatchItem] = []
        if (temp_root / SUGGESTIONS_FILE).exists():
            suggestions = JsonSuggestionRepository(temp_root / SUGGESTIONS_FILE).load().watch_items

        configurations: list[SuggestionDatabaseConfiguration] = []
        if (temp_root / SUGGESTION_DATABASE_CONFIGURATIONS_FILE).exists():
            configurations = SuggestionDatabaseConfigurationRepository(
                temp_root / SUGGESTION_DATABASE_CONFIGURATIONS_FILE
            ).list_all()

        vote_rounds: list[VoteRound] = []
        if (temp_root / VOTING_FILE).exists():
            vote_rounds = JsonVoteRepository(temp_root / VOTING_FILE).load().rounds

        return databases, suggestions, configurations, vote_rounds


@dataclass(frozen=True, slots=True)
class ImportResult:
    """Result of an actual /import execution (either mode)."""

    success: bool
    message: str
    databases_imported: int = 0
    databases_skipped: int = 0
    suggestions_imported: int = 0
    suggestions_skipped: int = 0
    vote_rounds_imported: int = 0
    conflict_titles: tuple[str, ...] = ()
    ids_reassigned: int = 0
    excluded: tuple[str, ...] = DATA_EXCLUDED_FROM_IMPORT
    safety_backup: Optional[Path] = None


async def import_backup(
    backup_service: BackupService,
    database_repository: JsonSuggestionDatabaseRepository,
    suggestion_repository: JsonSuggestionRepository,
    configuration_repository: SuggestionDatabaseConfigurationRepository,
    vote_repository: JsonVoteRepository,
    archive_path: Path,
    guild_id: int,
    mode: ImportMode,
) -> ImportResult:
    """Import an uploaded backup's portable data via merge or replace.

    Re-validates from scratch (mirrors every other confirmed-destructive-
    action entry point in this project) before creating a full safety
    backup and only then mutating anything.
    """
    summary = build_import_summary(backup_service, archive_path)
    if not summary.is_valid:
        detail = "; ".join(summary.errors) or "unknown validation error"
        return ImportResult(False, f"Import validation failed: {detail}")

    validation = backup_service.validate_backup(archive_path)
    manifest = validation.manifest
    assert manifest is not None  # summary.is_valid already guarantees this

    databases, suggestions, configurations, vote_rounds = _extract_portable_data(archive_path, manifest)

    try:
        safety_backup = backup_service.create_backup(BackupKind.MANUAL, enforce_retention=False).archive_path
    except BackupError as exc:
        return ImportResult(
            False, f"Safety backup failed, so the import was aborted. Live data was NOT changed: {exc}"
        )

    if mode is ImportMode.REPLACE:
        return _replace_import(
            database_repository,
            suggestion_repository,
            configuration_repository,
            vote_repository,
            guild_id,
            databases,
            suggestions,
            configurations,
            vote_rounds,
            safety_backup,
        )
    return _merge_import(
        database_repository,
        suggestion_repository,
        configuration_repository,
        vote_repository,
        guild_id,
        databases,
        suggestions,
        configurations,
        vote_rounds,
        safety_backup,
    )


def _merge_import(
    database_repository: JsonSuggestionDatabaseRepository,
    suggestion_repository: JsonSuggestionRepository,
    configuration_repository: SuggestionDatabaseConfigurationRepository,
    vote_repository: JsonVoteRepository,
    guild_id: int,
    incoming_databases: list[SuggestionDatabase],
    incoming_suggestions: list[WatchItem],
    incoming_configurations: list[SuggestionDatabaseConfiguration],
    incoming_vote_rounds: list[VoteRound],
    safety_backup: Path,
) -> ImportResult:
    """Merge: a database whose NAME already exists here (case-insensitive)
    has its compatible suggestions merged into the existing local
    database; every other incoming database is imported as new, with
    identifiers reassigned only when they'd otherwise collide.

    Numeric database/suggestion/round IDs from another WASH instance
    carry no meaning here (each instance assigns them independently),
    so name is the only reliable signal for "this is the same
    database" -- never inferred from ID equality alone.
    """
    database_load = database_repository.load()
    local_databases = list(database_load.databases)
    local_database_ids = {database.database_id for database in local_databases}
    next_database_id = database_load.next_id
    local_by_name = {
        database.name.casefold(): database for database in local_databases if database.guild_id == guild_id
    }

    suggestion_load = suggestion_repository.load()
    local_suggestions = list(suggestion_load.watch_items)
    local_suggestion_ids = {item.id for item in local_suggestions}
    next_suggestion_id = suggestion_load.next_id

    vote_load = vote_repository.load()
    local_rounds = list(vote_load.rounds)
    local_round_ids = {round_.id for round_ in local_rounds}
    next_round_id = vote_load.next_round_id

    configuration_by_source_key = {
        (configuration.guild_id, configuration.database_id): configuration
        for configuration in incoming_configurations
    }

    remap: dict[int, int] = {}
    databases_imported = 0
    databases_skipped = 0
    suggestions_imported = 0
    suggestions_skipped = 0
    conflict_titles: list[str] = []
    ids_reassigned = 0

    for incoming_database in incoming_databases:
        matching_local = local_by_name.get(incoming_database.name.casefold())

        if matching_local is not None:
            remap[incoming_database.database_id] = matching_local.database_id
            databases_skipped += 1
            existing_keys = {
                (item.database_id, item.title.casefold())
                for item in local_suggestions
                if item.database_id == matching_local.database_id
            }
            for item in incoming_suggestions:
                if item.database_id != incoming_database.database_id:
                    continue
                key = (matching_local.database_id, item.title.casefold())
                if key in existing_keys:
                    conflict_titles.append(item.title)
                    suggestions_skipped += 1
                    continue
                new_id = item.id
                if new_id in local_suggestion_ids:
                    new_id = next_suggestion_id
                    next_suggestion_id += 1
                    ids_reassigned += 1
                local_suggestions.append(replace(item, id=new_id, database_id=matching_local.database_id, guild_id=guild_id))
                local_suggestion_ids.add(new_id)
                existing_keys.add(key)
                suggestions_imported += 1
            continue

        new_database_id = incoming_database.database_id
        if new_database_id in local_database_ids:
            new_database_id = next_database_id
            next_database_id += 1
            ids_reassigned += 1
        remap[incoming_database.database_id] = new_database_id
        local_database_ids.add(new_database_id)

        new_database = replace(incoming_database, database_id=new_database_id, guild_id=guild_id)
        local_databases.append(new_database)
        local_by_name[new_database.name.casefold()] = new_database
        databases_imported += 1

        incoming_configuration = configuration_by_source_key.get(
            (incoming_database.guild_id, incoming_database.database_id)
        )
        if incoming_configuration is not None:
            configuration_repository.save(
                replace(incoming_configuration, guild_id=guild_id, database_id=new_database_id)
            )

        for item in incoming_suggestions:
            if item.database_id != incoming_database.database_id:
                continue
            new_id = item.id
            if new_id in local_suggestion_ids:
                new_id = next_suggestion_id
                next_suggestion_id += 1
                ids_reassigned += 1
            local_suggestions.append(replace(item, id=new_id, database_id=new_database_id, guild_id=guild_id))
            local_suggestion_ids.add(new_id)
            suggestions_imported += 1

    vote_rounds_imported = 0
    for round_ in incoming_vote_rounds:
        resolved_database_id = remap.get(round_.database_id) if round_.database_id is not None else None
        if round_.database_id is not None and resolved_database_id is None:
            continue
        new_round_id = round_.id
        if new_round_id in local_round_ids:
            new_round_id = next_round_id
            next_round_id += 1
            ids_reassigned += 1
        local_rounds.append(replace(round_, id=new_round_id, guild_id=guild_id, database_id=resolved_database_id))
        local_round_ids.add(new_round_id)
        vote_rounds_imported += 1

    database_repository.save(local_databases, next_database_id)
    suggestion_repository.save(local_suggestions, next_suggestion_id)
    vote_repository.save(local_rounds, next_round_id)

    message = (
        f"Merge import complete: {databases_imported} database(s) imported, {databases_skipped} matched an "
        f"existing database by name, {suggestions_imported} suggestion(s) imported, "
        f"{suggestions_skipped} skipped as duplicates, {vote_rounds_imported} vote round(s) imported, "
        f"{ids_reassigned} identifier(s) reassigned. "
        f"A safety backup was made first: `{safety_backup.name}`."
    )
    return ImportResult(
        True,
        message,
        databases_imported=databases_imported,
        databases_skipped=databases_skipped,
        suggestions_imported=suggestions_imported,
        suggestions_skipped=suggestions_skipped,
        vote_rounds_imported=vote_rounds_imported,
        conflict_titles=tuple(conflict_titles),
        ids_reassigned=ids_reassigned,
        safety_backup=safety_backup,
    )


def _replace_import(
    database_repository: JsonSuggestionDatabaseRepository,
    suggestion_repository: JsonSuggestionRepository,
    configuration_repository: SuggestionDatabaseConfigurationRepository,
    vote_repository: JsonVoteRepository,
    guild_id: int,
    incoming_databases: list[SuggestionDatabase],
    incoming_suggestions: list[WatchItem],
    incoming_configurations: list[SuggestionDatabaseConfiguration],
    incoming_vote_rounds: list[VoteRound],
    safety_backup: Path,
) -> ImportResult:
    """Replace: every portable record currently belonging to this guild
    is removed first, then the backup's portable data is imported fresh.
    Every other guild's data (in a hypothetical multi-guild deployment)
    and this guild's own GuildConfiguration/role/channel IDs are left
    untouched.
    """
    database_load = database_repository.load()
    remaining_databases = [database for database in database_load.databases if database.guild_id != guild_id]
    local_database_ids = {database.database_id for database in remaining_databases}
    next_database_id = database_load.next_id

    suggestion_load = suggestion_repository.load()
    remaining_suggestions = [item for item in suggestion_load.watch_items if item.guild_id != guild_id]
    local_suggestion_ids = {item.id for item in remaining_suggestions}
    next_suggestion_id = suggestion_load.next_id

    vote_load = vote_repository.load()
    remaining_rounds = [round_ for round_ in vote_load.rounds if round_.guild_id != guild_id]
    local_round_ids = {round_.id for round_ in remaining_rounds}
    next_round_id = vote_load.next_round_id

    configuration_repository.delete_for_guild(guild_id)
    configuration_by_source_key = {
        (configuration.guild_id, configuration.database_id): configuration
        for configuration in incoming_configurations
    }

    remap: dict[int, int] = {}
    databases_imported = 0
    suggestions_imported = 0
    ids_reassigned = 0

    for incoming_database in incoming_databases:
        new_database_id = incoming_database.database_id
        if new_database_id in local_database_ids:
            new_database_id = next_database_id
            next_database_id += 1
            ids_reassigned += 1
        remap[incoming_database.database_id] = new_database_id
        local_database_ids.add(new_database_id)
        remaining_databases.append(replace(incoming_database, database_id=new_database_id, guild_id=guild_id))
        databases_imported += 1

        incoming_configuration = configuration_by_source_key.get(
            (incoming_database.guild_id, incoming_database.database_id)
        )
        if incoming_configuration is not None:
            configuration_repository.save(
                replace(incoming_configuration, guild_id=guild_id, database_id=new_database_id)
            )

        for item in incoming_suggestions:
            if item.database_id != incoming_database.database_id:
                continue
            new_id = item.id
            if new_id in local_suggestion_ids:
                new_id = next_suggestion_id
                next_suggestion_id += 1
                ids_reassigned += 1
            remaining_suggestions.append(replace(item, id=new_id, database_id=new_database_id, guild_id=guild_id))
            local_suggestion_ids.add(new_id)
            suggestions_imported += 1

    vote_rounds_imported = 0
    for round_ in incoming_vote_rounds:
        resolved_database_id = remap.get(round_.database_id) if round_.database_id is not None else None
        if round_.database_id is not None and resolved_database_id is None:
            continue
        new_round_id = round_.id
        if new_round_id in local_round_ids:
            new_round_id = next_round_id
            next_round_id += 1
            ids_reassigned += 1
        remaining_rounds.append(replace(round_, id=new_round_id, guild_id=guild_id, database_id=resolved_database_id))
        local_round_ids.add(new_round_id)
        vote_rounds_imported += 1

    database_repository.save(remaining_databases, next_database_id)
    suggestion_repository.save(remaining_suggestions, next_suggestion_id)
    vote_repository.save(remaining_rounds, next_round_id)

    message = (
        f"Replace import complete: {databases_imported} database(s), {suggestions_imported} suggestion(s), "
        f"and {vote_rounds_imported} vote round(s) imported, replacing this server's previous suggestion "
        f"data. Your Discord role and channel configuration was not changed. "
        f"A safety backup was made first: `{safety_backup.name}`."
    )
    return ImportResult(
        True,
        message,
        databases_imported=databases_imported,
        suggestions_imported=suggestions_imported,
        vote_rounds_imported=vote_rounds_imported,
        ids_reassigned=ids_reassigned,
        safety_backup=safety_backup,
    )
