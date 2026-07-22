"""Create, validate, retain, and restore WASH JSON data backups."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Iterable, Optional

from watch_party_manager.version import __version__ as APPLICATION_VERSION

BACKUP_FORMAT_VERSION = 1
PROJECT_NAME = "Watch Party Manager"
MANIFEST_NAME = "manifest.json"
DEFAULT_DATA_DIRECTORY = Path("data")
DEFAULT_BACKUP_DIRECTORY = DEFAULT_DATA_DIRECTORY / "backups"
DEFAULT_BACKUP_INTERVAL_DAYS = 1
DEFAULT_RETENTION_LIMIT = 30


class BackupKind(str, Enum):
    """Why/when a backup was created -- drives its retention directory
    and retention limit. Orthogonal to BackupType (what it contains).
    """

    SCHEDULED = "scheduled"
    MANUAL = "manual"


class BackupType(str, Enum):
    """What a backup archive's contents represent.

    Orthogonal to BackupKind: a suggestion-database-scoped backup is
    still created "manually" (BackupKind.MANUAL) for retention purposes,
    but its BackupType tells /restore it must never be used for a full
    restore, and vice versa.
    """

    FULL = "full"
    SUGGESTION_DATABASE = "suggestion_database"



@dataclass(frozen=True, slots=True)
class BackupScheduleSettings:
    """Configurable automatic-backup schedule and retention settings."""

    enabled: bool = True
    interval_days: int = DEFAULT_BACKUP_INTERVAL_DAYS
    scheduled_retention_limit: int = DEFAULT_RETENTION_LIMIT
    manual_retention_limit: int = DEFAULT_RETENTION_LIMIT

    def __post_init__(self) -> None:
        if self.interval_days < 1:
            raise ValueError("interval_days must be at least 1")
        if self.scheduled_retention_limit < 1:
            raise ValueError("scheduled_retention_limit must be at least 1")
        if self.manual_retention_limit < 1:
            raise ValueError("manual_retention_limit must be at least 1")


@dataclass(frozen=True, slots=True)
class BackupFile:
    """Metadata for one file stored in a backup archive."""

    path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class BackupManifest:
    """Metadata describing a complete WASH backup.

    project_name/application_version/guild_id/database_id/database_name
    are all optional: backups created before FR-032B never recorded
    them, so validate_backup() must keep accepting archives that omit
    them rather than treating their absence as corruption (see "Do not
    report values that cannot be reliably determined").
    """

    format_version: int
    created_at: str
    kind: BackupKind
    files: tuple[BackupFile, ...]
    backup_type: BackupType = BackupType.FULL
    project_name: Optional[str] = None
    application_version: Optional[str] = None
    guild_id: Optional[int] = None
    database_id: Optional[int] = None
    database_name: Optional[str] = None


@dataclass(frozen=True, slots=True)
class BackupValidationResult:
    """Result of validating a backup archive."""

    is_valid: bool
    errors: tuple[str, ...] = ()
    manifest: BackupManifest | None = None


@dataclass(frozen=True, slots=True)
class BackupCreationResult:
    """Result of creating a backup archive."""

    archive_path: Path
    manifest: BackupManifest
    removed_archives: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class BackupRestoreResult:
    """Result of restoring files from a validated archive."""

    restored_files: tuple[Path, ...]
    safety_backup: Path | None = None


class BackupError(RuntimeError):
    """Raised when backup creation or restoration cannot complete safely."""


class BackupService:
    """Manage versioned ZIP snapshots of WASH JSON persistence files.

    Backup archives are grouped by kind under ``data/backups``. Each archive
    contains a manifest with checksums and relative paths. Restores validate
    the entire archive before touching live data and create a safety backup by
    default when existing JSON files are present.
    """

    def __init__(
        self,
        data_directory: Path | str = DEFAULT_DATA_DIRECTORY,
        backup_directory: Path | str = DEFAULT_BACKUP_DIRECTORY,
        settings: BackupScheduleSettings | None = None,
    ) -> None:
        self._data_directory = Path(data_directory)
        self._backup_directory = Path(backup_directory)
        self._settings = settings or BackupScheduleSettings()

    def create_backup(
        self,
        kind: BackupKind = BackupKind.MANUAL,
        *,
        created_at: datetime | None = None,
        enforce_retention: bool = True,
        guild_id: int | None = None,
    ) -> BackupCreationResult:
        """Create a checksummed ZIP snapshot of all JSON data files.

        guild_id is purely informational (recorded in the manifest for
        display/troubleshooting) -- the archive itself still contains
        every JSON file under data_directory, unscoped by guild, exactly
        as before. This project's data model is not strictly multi-
        tenant across files, so a full backup has never been (and still
        isn't) filtered by guild.
        """
        timestamp = self._normalize_datetime(created_at)
        source_files = tuple(self._iter_data_files())
        file_contents = {
            relative_path.as_posix(): source_path.read_bytes() for source_path, relative_path in source_files
        }
        return self.create_scoped_backup(
            file_contents,
            kind=kind,
            backup_type=BackupType.FULL,
            created_at=timestamp,
            enforce_retention=enforce_retention,
            guild_id=guild_id,
        )

    def create_scoped_backup(
        self,
        files: dict[str, bytes],
        *,
        kind: BackupKind,
        backup_type: BackupType,
        created_at: datetime | None = None,
        enforce_retention: bool = True,
        guild_id: int | None = None,
        database_id: int | None = None,
        database_name: str | None = None,
        filename_tag: str | None = None,
    ) -> BackupCreationResult:
        """Create a checksummed ZIP from explicit in-memory file contents.

        create_backup() is a thin wrapper around this for the common
        case (every *.json file under data_directory). This lower-level
        entry point exists for FR-032B's single suggestion database
        backups, which need a filtered file set (just one database's
        records) instead of everything on disk.

        Args:
            files: Relative archive path (posix-style, e.g.
                "suggestions.json") mapped to its exact file content.
            kind: Retention category (drives which directory/limit the
                archive counts against -- unchanged from before).
            backup_type: What the archive's contents represent (full
                snapshot vs. a single suggestion database).
            filename_tag: Optional short tag appended to the generated
                filename (e.g. "db3") so a scoped backup's filename is
                self-descriptive without needing to open its manifest.
        """
        timestamp = self._normalize_datetime(created_at)
        manifest = self._build_manifest(
            files,
            kind,
            timestamp,
            backup_type=backup_type,
            guild_id=guild_id,
            database_id=database_id,
            database_name=database_name,
        )

        destination_directory = self._backup_directory / kind.value
        destination_directory.mkdir(parents=True, exist_ok=True)
        archive_path = self._unique_archive_path(destination_directory, kind, timestamp, tag=filename_tag)

        try:
            with tempfile.NamedTemporaryFile(
                dir=destination_directory,
                prefix=f".{archive_path.stem}-",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)

            with zipfile.ZipFile(
                temporary_path,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
            ) as archive:
                archive.writestr(
                    MANIFEST_NAME,
                    json.dumps(self._manifest_to_dict(manifest), indent=2),
                )
                for relative_path, payload in files.items():
                    archive.writestr(relative_path, payload)

            temporary_path.replace(archive_path)
        except (OSError, zipfile.BadZipFile) as exc:
            if "temporary_path" in locals():
                temporary_path.unlink(missing_ok=True)
            raise BackupError(f"Could not create backup: {exc}") from exc

        removed = self.prune_backups(kind) if enforce_retention else ()
        return BackupCreationResult(archive_path, manifest, removed)

    def list_backups(self, kind: BackupKind | None = None) -> tuple[Path, ...]:
        """Return known backup archives from newest to oldest."""
        directories = (
            (self._backup_directory / kind.value,)
            if kind is not None
            else tuple(self._backup_directory / item.value for item in BackupKind)
        )
        archives = [
            path
            for directory in directories
            if directory.exists()
            for path in directory.glob("wash-*.zip")
            if path.is_file()
        ]
        return tuple(sorted(archives, key=lambda path: path.stat().st_mtime_ns, reverse=True))

    def prune_backups(self, kind: BackupKind) -> tuple[Path, ...]:
        """Delete older archives beyond the configured retention limit."""
        archives = self.list_backups(kind)
        retention_limit = self.retention_limit_for(kind)
        removed: list[Path] = []
        for archive_path in archives[retention_limit:]:
            archive_path.unlink(missing_ok=True)
            removed.append(archive_path)
        return tuple(removed)

    @property
    def settings(self) -> BackupScheduleSettings:
        """Return the configured schedule and retention settings."""
        return self._settings

    def retention_limit_for(self, kind: BackupKind) -> int:
        """Return the configured retention limit for a backup category."""
        if kind is BackupKind.SCHEDULED:
            return self._settings.scheduled_retention_limit
        return self._settings.manual_retention_limit

    def is_scheduled_backup_due(
        self,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Return whether an automatic backup should be created now.

        The scheduler can call this method periodically. A scheduled backup is
        due when automatic backups are enabled and no prior scheduled archive
        exists within the configured interval.
        """
        if not self._settings.enabled:
            return False

        current_time = self._normalize_datetime(now)
        scheduled_backups = self.list_backups(BackupKind.SCHEDULED)
        if not scheduled_backups:
            return True

        latest_created_at = self._read_manifest_created_at(scheduled_backups[0])
        return current_time >= latest_created_at + timedelta(
            days=self._settings.interval_days
        )

    def validate_backup(self, archive_path: Path | str) -> BackupValidationResult:
        """Validate archive structure, paths, JSON payloads, sizes, and hashes."""
        path = Path(archive_path)
        errors: list[str] = []
        manifest: BackupManifest | None = None

        if not path.is_file():
            return BackupValidationResult(False, (f"Backup does not exist: {path}",))

        try:
            with zipfile.ZipFile(path, mode="r") as archive:
                names = archive.namelist()
                if MANIFEST_NAME not in names:
                    return BackupValidationResult(False, ("Backup manifest is missing.",))

                manifest = self._parse_manifest(archive.read(MANIFEST_NAME))
                declared_paths = [entry.path for entry in manifest.files]
                if len(declared_paths) != len(set(declared_paths)):
                    errors.append("Backup manifest contains duplicate file paths.")

                payload_names = [name for name in names if name != MANIFEST_NAME]
                undeclared = sorted(set(payload_names) - set(declared_paths))
                missing = sorted(set(declared_paths) - set(payload_names))
                if undeclared:
                    errors.append(f"Backup contains undeclared files: {', '.join(undeclared)}")
                if missing:
                    errors.append(f"Backup is missing declared files: {', '.join(missing)}")

                for entry in manifest.files:
                    if not self._is_safe_relative_json_path(entry.path):
                        errors.append(f"Unsafe backup path: {entry.path}")
                        continue
                    if entry.path not in names:
                        continue
                    payload = archive.read(entry.path)
                    if len(payload) != entry.size:
                        errors.append(f"Size mismatch for {entry.path}.")
                    if self._sha256(payload) != entry.sha256:
                        errors.append(f"Checksum mismatch for {entry.path}.")
                    try:
                        json.loads(payload.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        errors.append(f"Invalid JSON payload: {entry.path}")
        except (OSError, zipfile.BadZipFile, KeyError, TypeError, ValueError) as exc:
            errors.append(f"Could not validate backup: {exc}")

        return BackupValidationResult(not errors, tuple(errors), manifest)

    def restore_backup(
        self,
        archive_path: Path | str,
        *,
        create_safety_backup: bool = True,
    ) -> BackupRestoreResult:
        """Restore declared JSON files after validating the complete archive.

        Existing JSON files not present in the archive are left untouched.
        This avoids deleting newer repositories that were introduced after an
        older backup was created.
        """
        validation = self.validate_backup(archive_path)
        if not validation.is_valid or validation.manifest is None:
            detail = "; ".join(validation.errors) or "unknown validation error"
            raise BackupError(f"Backup validation failed: {detail}")

        safety_backup: Path | None = None
        if create_safety_backup and any(self._iter_data_files()):
            safety_backup = self.create_backup(
                BackupKind.MANUAL,
                enforce_retention=False,
            ).archive_path

        restored: list[Path] = []
        try:
            with zipfile.ZipFile(Path(archive_path), mode="r") as archive:
                with tempfile.TemporaryDirectory() as temporary_directory:
                    stage_root = Path(temporary_directory)
                    for entry in validation.manifest.files:
                        staged_path = stage_root / Path(entry.path)
                        staged_path.parent.mkdir(parents=True, exist_ok=True)
                        staged_path.write_bytes(archive.read(entry.path))

                    for entry in validation.manifest.files:
                        destination = self._data_directory / Path(entry.path)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        # Atomic per-file swap (temp file + replace), matching
                        # every repository's own save() convention -- a crash
                        # mid-restore can never leave a partially written
                        # destination file, only a stray .tmp sibling.
                        temporary_destination = destination.with_name(destination.name + ".tmp")
                        shutil.copy2(stage_root / Path(entry.path), temporary_destination)
                        temporary_destination.replace(destination)
                        restored.append(destination)
        except (OSError, zipfile.BadZipFile, KeyError) as exc:
            raise BackupError(f"Could not restore backup: {exc}") from exc

        return BackupRestoreResult(tuple(restored), safety_backup)

    def _read_manifest_created_at(self, archive_path: Path) -> datetime:
        validation = self.validate_backup(archive_path)
        if not validation.is_valid or validation.manifest is None:
            detail = "; ".join(validation.errors) or "unknown validation error"
            raise BackupError(f"Could not read scheduled backup timestamp: {detail}")
        value = validation.manifest.created_at.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise BackupError("Backup manifest contains an invalid creation time.") from exc
        if parsed.tzinfo is None:
            raise BackupError("Backup manifest creation time must include a timezone.")
        return parsed.astimezone(timezone.utc)

    def _iter_data_files(self) -> Iterable[tuple[Path, PurePosixPath]]:
        if not self._data_directory.exists():
            return ()

        backup_root = self._backup_directory.resolve()
        files: list[tuple[Path, PurePosixPath]] = []
        for path in self._data_directory.rglob("*.json"):
            if not path.is_file():
                continue
            try:
                path.resolve().relative_to(backup_root)
            except ValueError:
                relative = PurePosixPath(path.relative_to(self._data_directory).as_posix())
                files.append((path, relative))
        return tuple(sorted(files, key=lambda item: item[1].as_posix()))

    def _build_manifest(
        self,
        files: dict[str, bytes],
        kind: BackupKind,
        created_at: datetime,
        *,
        backup_type: BackupType = BackupType.FULL,
        guild_id: int | None = None,
        database_id: int | None = None,
        database_name: str | None = None,
    ) -> BackupManifest:
        entries = [
            BackupFile(path=relative_path, size=len(payload), sha256=self._sha256(payload))
            for relative_path, payload in sorted(files.items())
        ]
        return BackupManifest(
            format_version=BACKUP_FORMAT_VERSION,
            created_at=created_at.isoformat().replace("+00:00", "Z"),
            kind=kind,
            files=tuple(entries),
            backup_type=backup_type,
            project_name=PROJECT_NAME,
            application_version=APPLICATION_VERSION,
            guild_id=guild_id,
            database_id=database_id,
            database_name=database_name,
        )

    @staticmethod
    def _normalize_datetime(value: datetime | None) -> datetime:
        if value is None:
            return datetime.now(timezone.utc)
        if value.tzinfo is None:
            raise ValueError("created_at must include timezone information")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _unique_archive_path(
        directory: Path, kind: BackupKind, created_at: datetime, *, tag: str | None = None
    ) -> Path:
        timestamp = created_at.strftime("%Y%m%dT%H%M%S%fZ")
        suffix = f"-{tag}" if tag else ""
        return directory / f"wash-{kind.value}{suffix}-{timestamp}.zip"

    @staticmethod
    def _sha256(payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _is_safe_relative_json_path(value: str) -> bool:
        path = PurePosixPath(value)
        return (
            bool(value)
            and not path.is_absolute()
            and ".." not in path.parts
            and path.suffix.casefold() == ".json"
            and "\\" not in value
        )

    @staticmethod
    def _manifest_to_dict(manifest: BackupManifest) -> dict:
        return {
            "format_version": manifest.format_version,
            # Mirrors format_version under the name FR-032B's manifest
            # schema specifies. format_version remains the field every
            # existing reader (this class's own _parse_manifest, and
            # already-shipped backups) actually depends on; this is an
            # additive alias, not a replacement.
            "backup_format_version": manifest.format_version,
            "created_at": manifest.created_at,
            "kind": manifest.kind.value,
            "backup_type": manifest.backup_type.value,
            "project_name": manifest.project_name,
            "application_version": manifest.application_version,
            "guild_id": manifest.guild_id,
            "database_id": manifest.database_id,
            "database_name": manifest.database_name,
            "files": [
                {"path": entry.path, "size": entry.size, "sha256": entry.sha256}
                for entry in manifest.files
            ],
        }

    @staticmethod
    def _parse_manifest(payload: bytes) -> BackupManifest:
        raw = json.loads(payload.decode("utf-8"))
        if raw["format_version"] != BACKUP_FORMAT_VERSION:
            raise ValueError(
                f"Unsupported backup format version: {raw['format_version']}"
            )
        files = tuple(
            BackupFile(
                path=str(entry["path"]),
                size=int(entry["size"]),
                sha256=str(entry["sha256"]),
            )
            for entry in raw["files"]
        )
        # backup_type/project_name/application_version/guild_id/database_id/
        # database_name are all absent from backups created before
        # FR-032B -- default rather than require, so older archives
        # still validate and restore.
        return BackupManifest(
            format_version=int(raw["format_version"]),
            created_at=str(raw["created_at"]),
            kind=BackupKind(raw["kind"]),
            files=files,
            backup_type=BackupType(raw.get("backup_type", BackupType.FULL.value)),
            project_name=raw.get("project_name"),
            application_version=raw.get("application_version"),
            guild_id=raw.get("guild_id"),
            database_id=raw.get("database_id"),
            database_name=raw.get("database_name"),
        )
