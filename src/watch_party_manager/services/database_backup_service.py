"""FR-032B: single suggestion database backup and restore (merge/replace).

Deliberately a new, separate service rather than an extension of
SuggestionService: SuggestionService's constructor loads its
repositories ONCE into memory and caches them for the lifetime of the
bot process (see its own module docstring) -- it has no notion of
"reload from disk" or "import records with a pre-existing identity,"
both of which a restore needs. This service instead operates directly
on the SAME repository classes SuggestionService already uses
(JsonSuggestionDatabaseRepository, JsonSuggestionRepository,
SuggestionDatabaseConfigurationRepository), calling only their existing
load()/save() -- never reaching into private serialization helpers --
so it never diverges from how those repositories already read and
write JSON, and BackupService's own manifest/zip machinery is reused
via create_scoped_backup()/validate_backup() rather than duplicated.

Known limitation: because this operates on the repositories directly
rather than through the bot's already-running SuggestionService
instance, a live bot process will not see a restore's effects until it
restarts -- the same limitation FR-032B's full restore has, and for the
same reason (see docs/05-Administration.md).

Also known: vote rounds are intentionally NOT part of a suggestion
database backup/restore. Section 7's Merge/Replace rules only ever
describe suggestion "records" and the database itself; extending that
to historical vote rounds is out of this milestone's explicit scope.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional

from watch_party_manager.domain.suggestion_database import SuggestionDatabase
from watch_party_manager.domain.watch_item import WatchItem
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.backup_service import (
    BackupCreationResult,
    BackupError,
    BackupKind,
    BackupService,
    BackupType,
)

SUGGESTION_DATABASES_FILE = "suggestion_databases.json"
SUGGESTIONS_FILE = "suggestions.json"
SUGGESTION_DATABASE_CONFIGURATIONS_FILE = "suggestion_database_configurations.json"

DATABASE_BACKUP_DISPLAY_NAME_PREFIX = "Watch_Party_Manager_Database_Backup"


class DatabaseRestoreMode(str, Enum):
    """How /database_restore should reconcile a backup's suggestions
    with whatever the destination database already has. Deliberately
    never inferred -- the caller must always choose one explicitly.
    """

    MERGE = "merge"
    REPLACE = "replace"


def sanitize_database_name_for_filename(name: str) -> str:
    """Turn a database's display name into a safe filename fragment.

    Keeps only alphanumerics, spaces, hyphens, and underscores, then
    collapses whitespace into single underscores -- database names are
    admin-supplied free text (see SuggestionService.create_database)
    and must never be trusted verbatim in a filename.
    """
    safe_characters = [character for character in name if character.isalnum() or character in " -_"]
    collapsed = "".join(safe_characters).strip()
    normalized = "_".join(collapsed.split())
    return normalized or "database"


def _parse_created_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def build_database_backup_display_filename(database_name: str, created_at: datetime) -> str:
    """Build the Discord-facing filename for a single-database backup."""
    safe_name = sanitize_database_name_for_filename(database_name)
    timestamp = created_at.strftime("%Y-%m-%d_%H-%M-%S")
    return f"{DATABASE_BACKUP_DISPLAY_NAME_PREFIX}_{safe_name}_{timestamp}.zip"


@dataclass(frozen=True, slots=True)
class DatabaseBackupResult:
    """Result of creating a single suggestion database's scoped backup."""

    success: bool
    message: str
    creation: Optional[BackupCreationResult] = None
    display_filename: Optional[str] = None


def create_database_backup(
    backup_service: BackupService,
    database_repository: JsonSuggestionDatabaseRepository,
    suggestion_repository: JsonSuggestionRepository,
    configuration_repository: SuggestionDatabaseConfigurationRepository,
    guild_id: int,
    database_id: int,
    *,
    created_at: Optional[datetime] = None,
) -> DatabaseBackupResult:
    """Build a scoped backup ZIP containing only one suggestion database.

    Only the database's own record, its suggestions, and its
    configuration (if any) are included -- enough to faithfully restore
    that one database, nothing from any other database or guild.
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
        return DatabaseBackupResult(False, "No suggestion database with that ID exists in this server.")

    suggestions = [item for item in suggestion_repository.load().watch_items if item.database_id == database_id]
    configuration = configuration_repository.get(guild_id, database_id)

    files: dict[str, bytes] = {}
    with TemporaryDirectory() as temporary_directory:
        temp_root = Path(temporary_directory)

        scoped_database_repository = JsonSuggestionDatabaseRepository(temp_root / SUGGESTION_DATABASES_FILE)
        scoped_database_repository.save([database], next_id=database.database_id + 1)
        files[SUGGESTION_DATABASES_FILE] = (temp_root / SUGGESTION_DATABASES_FILE).read_bytes()

        next_suggestion_id = max((item.id for item in suggestions), default=0) + 1
        scoped_suggestion_repository = JsonSuggestionRepository(temp_root / SUGGESTIONS_FILE)
        scoped_suggestion_repository.save(suggestions, next_id=next_suggestion_id)
        files[SUGGESTIONS_FILE] = (temp_root / SUGGESTIONS_FILE).read_bytes()

        if configuration is not None:
            scoped_configuration_repository = SuggestionDatabaseConfigurationRepository(
                temp_root / SUGGESTION_DATABASE_CONFIGURATIONS_FILE
            )
            scoped_configuration_repository.save(configuration)
            files[SUGGESTION_DATABASE_CONFIGURATIONS_FILE] = (
                temp_root / SUGGESTION_DATABASE_CONFIGURATIONS_FILE
            ).read_bytes()

    try:
        creation = backup_service.create_scoped_backup(
            files,
            kind=BackupKind.MANUAL,
            backup_type=BackupType.SUGGESTION_DATABASE,
            created_at=created_at,
            guild_id=guild_id,
            database_id=database.database_id,
            database_name=database.name,
            filename_tag=f"db{database.database_id}",
        )
    except BackupError as exc:
        return DatabaseBackupResult(False, f"Backup failed: {exc}")

    display_filename = build_database_backup_display_filename(
        database.name, _parse_created_at(creation.manifest.created_at)
    )
    return DatabaseBackupResult(
        True,
        f'Backup created for suggestion database "{database.name}".',
        creation=creation,
        display_filename=display_filename,
    )


@dataclass(frozen=True, slots=True)
class DatabaseRestoreResult:
    """Result of a /database_restore merge or replace attempt."""

    success: bool
    message: str
    imported_count: int = 0
    conflict_titles: tuple[str, ...] = ()
    safety_backup: Optional[Path] = None


def restore_database_backup(
    backup_service: BackupService,
    database_repository: JsonSuggestionDatabaseRepository,
    suggestion_repository: JsonSuggestionRepository,
    configuration_repository: SuggestionDatabaseConfigurationRepository,
    archive_path: Path,
    guild_id: int,
    mode: DatabaseRestoreMode,
) -> DatabaseRestoreResult:
    """Restore a single suggestion database backup via merge or replace.

    Re-validates the archive from scratch rather than trusting a prior
    call (mirrors BackupService.restore_backup()'s own re-validation
    before touching anything). A full safety backup is created via the
    existing backup process before either mode makes any change; if
    that fails, this aborts and live data is left untouched.
    """
    validation = backup_service.validate_backup(archive_path)
    if not validation.is_valid or validation.manifest is None:
        detail = "; ".join(validation.errors) or "unknown validation error"
        return DatabaseRestoreResult(False, f"Backup validation failed: {detail}")

    manifest = validation.manifest
    if manifest.backup_type is not BackupType.SUGGESTION_DATABASE:
        return DatabaseRestoreResult(False, "That backup is not a suggestion database backup.")
    if manifest.guild_id is not None and manifest.guild_id != guild_id:
        return DatabaseRestoreResult(
            False, "That backup was created in a different Discord server and cannot be restored here."
        )
    if manifest.database_id is None:
        return DatabaseRestoreResult(False, "That backup does not record which database it belongs to.")

    try:
        with TemporaryDirectory() as temporary_directory:
            temp_root = Path(temporary_directory)
            with zipfile.ZipFile(archive_path, mode="r") as archive:
                for entry in manifest.files:
                    staged_path = temp_root / Path(entry.path)
                    staged_path.parent.mkdir(parents=True, exist_ok=True)
                    staged_path.write_bytes(archive.read(entry.path))

            backup_database = next(
                (
                    candidate
                    for candidate in JsonSuggestionDatabaseRepository(
                        temp_root / SUGGESTION_DATABASES_FILE
                    )
                    .load()
                    .databases
                    if candidate.database_id == manifest.database_id
                ),
                None,
            )
            if backup_database is None:
                return DatabaseRestoreResult(False, "That backup's database record could not be read.")

            # Defensive filter: an uploaded backup is untrusted input --
            # never import a suggestion whose own database_id doesn't
            # match the backup's declared database, even if present.
            backup_suggestions = [
                item
                for item in JsonSuggestionRepository(temp_root / SUGGESTIONS_FILE).load().watch_items
                if item.database_id == manifest.database_id
            ]

            backup_configuration = None
            configuration_path = temp_root / SUGGESTION_DATABASE_CONFIGURATIONS_FILE
            if configuration_path.exists():
                backup_configuration = SuggestionDatabaseConfigurationRepository(configuration_path).get(
                    guild_id, manifest.database_id
                )
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        return DatabaseRestoreResult(False, f"Could not read backup contents: {exc}")

    try:
        safety_backup = backup_service.create_backup(BackupKind.MANUAL, enforce_retention=False).archive_path
    except BackupError as exc:
        return DatabaseRestoreResult(False, f"Safety backup failed, restore aborted. Live data was not changed: {exc}")

    if mode is DatabaseRestoreMode.REPLACE:
        return _replace_database(
            database_repository,
            suggestion_repository,
            configuration_repository,
            guild_id,
            backup_database,
            backup_suggestions,
            backup_configuration,
            safety_backup,
        )
    return _merge_database(
        database_repository,
        suggestion_repository,
        guild_id,
        backup_database,
        backup_suggestions,
        safety_backup,
    )


def _replace_database(
    database_repository: JsonSuggestionDatabaseRepository,
    suggestion_repository: JsonSuggestionRepository,
    configuration_repository: SuggestionDatabaseConfigurationRepository,
    guild_id: int,
    backup_database: SuggestionDatabase,
    backup_suggestions: list[WatchItem],
    backup_configuration,
    safety_backup: Path,
) -> DatabaseRestoreResult:
    """Replace only the selected database, preserving everything else."""
    try:
        database_load = database_repository.load()
        remaining_databases = [
            existing
            for existing in database_load.databases
            if not (existing.database_id == backup_database.database_id and existing.guild_id == guild_id)
        ]
        restored_database = replace(backup_database, guild_id=guild_id)
        next_database_id = max(database_load.next_id, restored_database.database_id + 1)
        database_repository.save([*remaining_databases, restored_database], next_database_id)

        suggestion_load = suggestion_repository.load()
        remaining_suggestions = [
            item
            for item in suggestion_load.watch_items
            if not (item.database_id == backup_database.database_id and item.guild_id == guild_id)
        ]
        restored_suggestions = [
            replace(item, guild_id=guild_id, database_id=backup_database.database_id)
            for item in backup_suggestions
        ]
        max_suggestion_id = max(
            (item.id for item in [*remaining_suggestions, *restored_suggestions]), default=0
        )
        next_suggestion_id = max(suggestion_load.next_id, max_suggestion_id + 1)
        suggestion_repository.save([*remaining_suggestions, *restored_suggestions], next_suggestion_id)

        if backup_configuration is not None:
            configuration_repository.save(replace(backup_configuration, guild_id=guild_id))
    except (OSError, ValueError) as exc:
        return DatabaseRestoreResult(
            False,
            f"Restore failed: {exc}. Your previous data was preserved in safety backup `{safety_backup.name}`.",
            safety_backup=safety_backup,
        )

    return DatabaseRestoreResult(
        True,
        f'Suggestion database "{restored_database.name}" replaced '
        f"({len(restored_suggestions)} suggestion(s) restored). "
        f"A safety backup was made first: `{safety_backup.name}`.",
        imported_count=len(restored_suggestions),
        safety_backup=safety_backup,
    )


def _merge_database(
    database_repository: JsonSuggestionDatabaseRepository,
    suggestion_repository: JsonSuggestionRepository,
    guild_id: int,
    backup_database: SuggestionDatabase,
    backup_suggestions: list[WatchItem],
    safety_backup: Path,
) -> DatabaseRestoreResult:
    """Import compatible suggestions into an existing database.

    Never touches the destination database's own record or
    configuration, and never overwrites an existing suggestion -- a
    title already present for this database (case-insensitively) is
    reported as a conflict and skipped rather than imported.
    """
    database_load = database_repository.load()
    existing_database = next(
        (
            candidate
            for candidate in database_load.databases
            if candidate.database_id == backup_database.database_id and candidate.guild_id == guild_id
        ),
        None,
    )
    if existing_database is None:
        return DatabaseRestoreResult(
            False,
            "No existing suggestion database with that ID was found to merge into. "
            "Use Replace if you want to recreate it from this backup.",
            safety_backup=safety_backup,
        )

    try:
        suggestion_load = suggestion_repository.load()
        existing_suggestions = suggestion_load.watch_items
        existing_keys = {
            (item.database_id, item.title.casefold())
            for item in existing_suggestions
            if item.database_id == backup_database.database_id
        }
        existing_ids = {item.id for item in existing_suggestions}

        imported: list[WatchItem] = []
        conflicts: list[str] = []
        next_id = suggestion_load.next_id
        for item in backup_suggestions:
            key = (backup_database.database_id, item.title.casefold())
            if key in existing_keys:
                conflicts.append(item.title)
                continue
            new_id = item.id if item.id not in existing_ids else next_id
            next_id = max(next_id, new_id + 1)
            imported.append(replace(item, id=new_id, database_id=backup_database.database_id, guild_id=guild_id))
            existing_ids.add(new_id)
            existing_keys.add(key)

        if imported:
            suggestion_repository.save([*existing_suggestions, *imported], next_id)
    except (OSError, ValueError) as exc:
        return DatabaseRestoreResult(
            False,
            f"Merge failed: {exc}. Your previous data was preserved in safety backup `{safety_backup.name}`.",
            safety_backup=safety_backup,
        )

    message = f'Merged {len(imported)} suggestion(s) into "{existing_database.name}".'
    if conflicts:
        shown = ", ".join(conflicts[:10])
        overflow = f" (+{len(conflicts) - 10} more)" if len(conflicts) > 10 else ""
        message += f" {len(conflicts)} suggestion(s) were skipped as duplicates: {shown}{overflow}."
    message += f" A safety backup was made first: `{safety_backup.name}`."

    return DatabaseRestoreResult(
        True,
        message,
        imported_count=len(imported),
        conflict_titles=tuple(conflicts),
        safety_backup=safety_backup,
    )
