"""Build a human-readable, pre-restore summary from a validated backup archive.

Deliberately kept separate from BackupService: BackupService stays
domain-agnostic (it only knows about generic JSON files, checksums, and
manifests -- see its own module docstring). This module is the one
place that knows what suggestion_databases.json/suggestions.json/
voting.json/membership_requests.json/guild_configurations.json actually
contain, so /restore and /database_restore can show a meaningful,
count-based summary before WASH Crew confirms -- without teaching
BackupService anything about WASH's actual data model.

Every count is best-effort and read-only: it only reads bytes already
inside the (already-validated) archive and never touches live data. A
file that's simply not present in the archive (e.g. a suggestion-
database-scoped backup has no voting.json) reports its count as None
("cannot be reliably determined") rather than 0, since 0 would
incorrectly imply "restoring this backup empties that store."
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from watch_party_manager.services.backup_service import BackupService, BackupType
from watch_party_manager.version import __version__ as APPLICATION_VERSION

SUGGESTION_DATABASES_FILE = "suggestion_databases.json"
SUGGESTIONS_FILE = "suggestions.json"
VOTING_FILE = "voting.json"
MEMBERSHIP_REQUESTS_FILE = "membership_requests.json"
GUILD_CONFIGURATIONS_FILE = "guild_configurations.json"


@dataclass(frozen=True, slots=True)
class RestoreSummary:
    """Everything /restore or /database_restore can reliably tell WASH
    Crew about a candidate backup before they confirm.

    is_valid reflects both BackupService's own archive validation AND
    this module's backup_type check -- a summary with is_valid=False
    must never be offered a Restore button, only Cancel.
    """

    is_valid: bool
    errors: tuple[str, ...] = ()
    backup_type: Optional[BackupType] = None
    project_name: Optional[str] = None
    application_version: Optional[str] = None
    created_at: Optional[str] = None
    guild_id: Optional[int] = None
    database_id: Optional[int] = None
    database_name: Optional[str] = None
    suggestion_database_count: Optional[int] = None
    suggestion_count: Optional[int] = None
    vote_round_count: Optional[int] = None
    membership_request_count: Optional[int] = None
    configuration_present: Optional[bool] = None
    warnings: tuple[str, ...] = ()


def build_restore_summary(
    backup_service: BackupService,
    archive_path: Path,
    *,
    expected_backup_type: Optional[BackupType] = None,
) -> RestoreSummary:
    """Validate archive_path and build a read-only pre-restore summary.

    Args:
        backup_service: Used only for its already-established
            validate_backup() -- this function never mutates data.
        archive_path: The candidate backup to summarize.
        expected_backup_type: When given, a backup whose manifest
            declares a different backup_type makes the summary invalid
            ("Unsupported backup type") -- e.g. /restore (full) must
            reject a suggestion_database-type backup, and
            /database_restore must reject a full backup.
    """
    validation = backup_service.validate_backup(archive_path)
    if not validation.is_valid or validation.manifest is None:
        return RestoreSummary(is_valid=False, errors=validation.errors)

    manifest = validation.manifest
    errors = list(validation.errors)
    warnings: list[str] = []

    if expected_backup_type is not None and manifest.backup_type is not expected_backup_type:
        errors.append(
            f"Unsupported backup type: expected '{expected_backup_type.value}', "
            f"found '{manifest.backup_type.value}'."
        )

    if manifest.application_version is not None and manifest.application_version != APPLICATION_VERSION:
        warnings.append(
            f"This backup was created with application version {manifest.application_version}, "
            f"but WASH is currently running {APPLICATION_VERSION}. Compatibility could not be fully confirmed."
        )

    present_paths = {entry.path for entry in manifest.files}
    payloads: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(archive_path, mode="r") as archive:
            for path in present_paths & {
                SUGGESTION_DATABASES_FILE,
                SUGGESTIONS_FILE,
                VOTING_FILE,
                MEMBERSHIP_REQUESTS_FILE,
                GUILD_CONFIGURATIONS_FILE,
            }:
                payloads[path] = archive.read(path)
    except (OSError, zipfile.BadZipFile, KeyError):
        payloads = {}

    def _count(path: str, key: str) -> Optional[int]:
        payload = payloads.get(path)
        if payload is None:
            return None
        try:
            value = json.loads(payload.decode("utf-8")).get(key)
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
            return None
        return len(value) if isinstance(value, (list, dict)) else None

    configuration_present: Optional[bool] = None
    guild_payload = payloads.get(GUILD_CONFIGURATIONS_FILE)
    if guild_payload is not None:
        try:
            guilds = json.loads(guild_payload.decode("utf-8")).get("guilds", {})
            if manifest.guild_id is not None:
                configuration_present = str(manifest.guild_id) in guilds
            else:
                configuration_present = bool(guilds)
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
            configuration_present = None

    return RestoreSummary(
        is_valid=not errors,
        errors=tuple(errors),
        backup_type=manifest.backup_type,
        project_name=manifest.project_name,
        application_version=manifest.application_version,
        created_at=manifest.created_at,
        guild_id=manifest.guild_id,
        database_id=manifest.database_id,
        database_name=manifest.database_name,
        suggestion_database_count=_count(SUGGESTION_DATABASES_FILE, "databases"),
        suggestion_count=_count(SUGGESTIONS_FILE, "suggestions"),
        vote_round_count=_count(VOTING_FILE, "rounds"),
        membership_request_count=_count(MEMBERSHIP_REQUESTS_FILE, "requests"),
        configuration_present=configuration_present,
        warnings=tuple(warnings),
    )
