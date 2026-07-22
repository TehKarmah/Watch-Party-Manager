"""JSON-backed persistence for Suggestion Database Configuration.

Mirrors guild_configuration_repository.py's architecture exactly: atomic
writes (temp file + replace), a schema_version + FutureSchemaVersionError
rejection path, a deterministic, sequential migration seam (empty for
now, since schema version 1 is the only version this project has ever
written), a pre-migration backup step, and unknown-field/unknown-nested-
field preservation via _split_known/_merge.

Kept as its own file, separate from guild_configurations.json: Suggestion
Database Configuration is explicitly a separate persistence concept from
Guild Configuration (per the task that introduced this milestone), even
though the two share validation helpers, GuildVoteVisibility, and
TieBehavior from the domain layer.

Identifier semantics: records are keyed by the composite
(guild_id, database_id) pair, where database_id is the SAME identity as
the existing operational SuggestionDatabase.database_id (see
suggestion_database_configuration.py's module docstring for the full
explanation, including why this is intentionally NOT the same identifier
scheme as GuildConfiguration.suggestion_databases' string-based entries).
"""

from __future__ import annotations

import copy
import json
import logging
import shutil
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Union

from watch_party_manager.domain.guild_configuration import GuildVoteVisibility, TieBehavior
from watch_party_manager.domain.suggestion_database_configuration import (
    CandidateSelectionMode,
    SuggestionDatabaseArchiveConfig,
    SuggestionDatabaseChannelsConfig,
    SuggestionDatabaseConfiguration,
    SuggestionDatabaseNotificationOverridesConfig,
    SuggestionDatabasePermissionsConfig,
    SuggestionDatabaseWatchHistoryConfig,
    SuggestionRulesConfig,
    VotingOverridesConfig,
)
from watch_party_manager.persistence.guild_configuration_repository import FutureSchemaVersionError

logger = logging.getLogger(__name__)
DEFAULT_SUGGESTION_DATABASE_CONFIGURATIONS_PATH = Path("data/suggestion_database_configurations.json")

# This module's own schema version, independent of Guild Configuration's
# CURRENT_SCHEMA_VERSION -- the two documents have separate lifecycles.
CURRENT_SCHEMA_VERSION = 1


class SuggestionDatabaseConfigurationRepository:
    """Loads and saves suggestion database configuration in a single JSON document.

    There is intentionally no separate "create" vs. "update" method:
    save() always performs a full upsert keyed by
    (configuration.guild_id, configuration.database_id). Matching
    guild_configuration_repository.GuildConfigurationRepository's own
    behavior, an existing record's created_at is preserved and
    configuration_version is incremented automatically -- callers never
    need to manage either.
    """

    _MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}

    def __init__(
        self, file_path: Union[Path, str] = DEFAULT_SUGGESTION_DATABASE_CONFIGURATIONS_PATH
    ) -> None:
        self._file_path = Path(file_path)

    def get(self, guild_id: int, database_id: int) -> Optional[SuggestionDatabaseConfiguration]:
        """Load a single database's configuration by its composite key.

        Args:
            guild_id: The Discord guild ID.
            database_id: The suggestion database's ID (matches the
                operational SuggestionDatabase.database_id).

        Returns:
            The matching configuration, or None if none has been saved
            for this (guild_id, database_id) pair yet.
        """
        return self._load_all().get((guild_id, database_id))

    def exists(self, guild_id: int, database_id: int) -> bool:
        """Check whether a configuration has been saved for this database.

        Args:
            guild_id: The Discord guild ID.
            database_id: The suggestion database's ID.

        Returns:
            True if a configuration exists for this (guild_id, database_id) pair.
        """
        return (guild_id, database_id) in self._load_all()

    def list_for_guild(self, guild_id: int) -> list[SuggestionDatabaseConfiguration]:
        """Get every configured database's configuration for one guild.

        Args:
            guild_id: The Discord guild ID to filter by.

        Returns:
            All configurations belonging to this guild. Order is not
            guaranteed.
        """
        return [
            configuration
            for (config_guild_id, _), configuration in self._load_all().items()
            if config_guild_id == guild_id
        ]

    def list_all(self) -> list[SuggestionDatabaseConfiguration]:
        """Get every persisted configuration across every guild.

        Returns:
            All configurations currently on disk. Order is not guaranteed.
        """
        return list(self._load_all().values())

    def save(self, configuration: SuggestionDatabaseConfiguration) -> None:
        """Create or fully replace the configuration for its composite key.

        Creates the storage file (and its parent directory) if it doesn't
        already exist. The write is atomic: a temporary file is written
        and then renamed onto the final path, so a failure mid-write
        never leaves a corrupt or partially-written file.

        For an existing (guild_id, database_id): created_at is preserved
        from the existing record (guild_id, database_id, and created_at
        are this record's immutable identity/history), updated_at is
        refreshed to the current UTC time, and configuration_version is
        incremented automatically -- whatever the incoming object's own
        configuration_version was is not used. For a brand new record,
        the incoming object's own created_at/configuration_version are
        used as given (already validated by the domain model itself).

        Args:
            configuration: The configuration to persist.
        """
        configurations = self._load_all()
        key = (configuration.guild_id, configuration.database_id)
        existing = configurations.get(key)
        now = datetime.now(timezone.utc)
        if existing is None:
            persisted = replace(
                configuration,
                schema_version=CURRENT_SCHEMA_VERSION,
                configuration_version=1,
                updated_at=max(configuration.created_at, now),
            )
        else:
            persisted = replace(
                configuration,
                schema_version=CURRENT_SCHEMA_VERSION,
                created_at=existing.created_at,
                updated_at=now,
                configuration_version=existing.configuration_version + 1,
            )
        configurations[key] = persisted
        self._save_all(configurations)

    def delete(self, guild_id: int, database_id: int) -> bool:
        """Remove a single database's configuration by its composite key.

        Returns:
            True if a record existed and was removed, False otherwise.
        """
        configurations = self._load_all()
        key = (guild_id, database_id)
        if key not in configurations:
            return False
        del configurations[key]
        self._save_all(configurations)
        return True

    def delete_for_guild(self, guild_id: int) -> int:
        """Remove every configuration belonging to one guild, e.g. during a factory reset.

        Returns:
            The number of records removed.
        """
        configurations = self._load_all()
        remaining = {key: value for key, value in configurations.items() if key[0] != guild_id}
        removed_count = len(configurations) - len(remaining)
        if removed_count:
            self._save_all(remaining)
        return removed_count

    def _load_all(self) -> dict[tuple[int, int], SuggestionDatabaseConfiguration]:
        """Load and deserialize every configuration from disk.

        A missing file is expected on first run and is not an error. A
        file that exists but can't be parsed, or contains an entry with
        an unsupported (future) schema_version, is handled per
        docs/guild_configuration_spec.md's migration rules -- see
        _migrate(). Any other malformed content is logged and treated as
        an empty store rather than crashing the bot.

        Returns:
            All persisted configurations, keyed by (guild_id, database_id).
        """
        if not self._file_path.exists():
            return {}

        try:
            data = json.loads(self._file_path.read_text(encoding="utf-8"))
            entries = data["guilds"]
            if not isinstance(entries, dict):
                raise TypeError("guilds must be an object")

            result: dict[tuple[int, int], SuggestionDatabaseConfiguration] = {}
            for guild_id_key, guild_entry in entries.items():
                if not isinstance(guild_entry, dict):
                    raise TypeError("guild entry must be an object")
                databases = guild_entry.get("databases", {})
                if not isinstance(databases, dict):
                    raise TypeError("databases must be an object")
                for database_id_key, raw_entry in databases.items():
                    migrated = self._migrate(raw_entry)
                    configuration = self._deserialize(migrated)
                    if str(configuration.guild_id) != str(guild_id_key):
                        raise ValueError("guild key does not match guild_id")
                    if str(configuration.database_id) != str(database_id_key):
                        raise ValueError("database key does not match database_id")
                    result[(configuration.guild_id, configuration.database_id)] = configuration
            return result
        except FutureSchemaVersionError:
            raise
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.error(
                "Could not load suggestion database configurations from %s: %s", self._file_path, exc
            )
            return {}

    def _save_all(self, configurations: dict[tuple[int, int], SuggestionDatabaseConfiguration]) -> None:
        """Serialize and atomically write every configuration to disk.

        Args:
            configurations: The complete set of configurations to
                persist, keyed by (guild_id, database_id).
        """
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        guilds: dict[str, dict[str, Any]] = {}
        for (guild_id, database_id), configuration in configurations.items():
            guild_key = str(guild_id)
            guilds.setdefault(guild_key, {"databases": {}})
            guilds[guild_key]["databases"][str(database_id)] = self._serialize(configuration)
        data = {"guilds": guilds}

        temporary_path = self._file_path.with_suffix(self._file_path.suffix + ".tmp")
        temporary_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temporary_path.replace(self._file_path)

    def _migrate(self, raw_entry: dict[str, Any]) -> dict[str, Any]:
        """Apply sequential schema migrations to a raw persisted entry.

        No migrations are registered yet -- CURRENT_SCHEMA_VERSION is 1,
        the only version this project has ever written. This is the
        designated seam for future migrations: register a callable in
        _MIGRATIONS keyed by the version it upgrades *from*, and it will
        be applied here, one version at a time, until the entry reaches
        CURRENT_SCHEMA_VERSION. A missing schema_version is treated as
        version 1. A version newer than CURRENT_SCHEMA_VERSION is
        rejected outright (FutureSchemaVersionError) rather than guessed
        at. Migrations run on a deep copy, so a failed or partial
        migration can never affect the raw entry the caller passed in,
        and since this method only ever runs during load (never during
        save), a failed migration can never partially persist anything
        either.

        Args:
            raw_entry: The raw dict as loaded from JSON, before any
                version transformation.

        Returns:
            The entry, migrated to CURRENT_SCHEMA_VERSION.

        Raises:
            FutureSchemaVersionError: If the entry's schema_version is
                newer than this repository supports.
            ValueError: If schema_version is invalid, or if a required
                migration is missing or doesn't advance the version by
                exactly one.
        """
        if not isinstance(raw_entry, dict):
            raise TypeError("suggestion database configuration entry must be an object")

        entry = copy.deepcopy(raw_entry)
        version = entry.get("schema_version", 1)
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValueError("schema_version must be a positive integer")
        if version > CURRENT_SCHEMA_VERSION:
            raise FutureSchemaVersionError(
                f"schema_version {version} is newer than supported version {CURRENT_SCHEMA_VERSION}"
            )
        if version < CURRENT_SCHEMA_VERSION:
            self._backup_before_migration()
        while version < CURRENT_SCHEMA_VERSION:
            migration = self._MIGRATIONS.get(version)
            if migration is None:
                raise ValueError(f"no migration registered for schema version {version}")
            entry = migration(entry)
            next_version = entry.get("schema_version")
            if next_version != version + 1:
                raise ValueError("migration must advance exactly one schema version")
            version = next_version
        return entry

    def _backup_before_migration(self) -> None:
        """Copy the current file aside before an older schema is migrated."""
        if not self._file_path.exists():
            return
        backup_path = self._file_path.with_suffix(self._file_path.suffix + ".pre_migration.bak")
        shutil.copy2(self._file_path, backup_path)

    @staticmethod
    def _split_known(entry: dict[str, Any], known: set[str]) -> dict[str, Any]:
        """Extract everything in entry that isn't one of the known keys.

        Used to capture unknown fields (present versions or future ones)
        into a section's extra_fields, so they survive a load/save cycle
        even though this repository doesn't understand them.
        """
        return {key: copy.deepcopy(value) for key, value in entry.items() if key not in known}

    @staticmethod
    def _merge(extra: dict[str, Any], known: dict[str, Any]) -> dict[str, Any]:
        """Combine a section's extra_fields with its known, modeled fields.

        known always wins on key collision, so a real field's value is
        never shadowed by stale extra data.
        """
        merged = copy.deepcopy(extra)
        merged.update(known)
        return merged

    @classmethod
    def _serialize(cls, configuration: SuggestionDatabaseConfiguration) -> dict[str, Any]:
        c = configuration
        return cls._merge(
            c.extra_fields,
            {
                "schema_version": c.schema_version,
                "guild_id": c.guild_id,
                "database_id": c.database_id,
                "display_name": c.display_name,
                "active": c.active,
                "created_at": c.created_at.isoformat(),
                "updated_at": c.updated_at.isoformat(),
                "configuration_version": c.configuration_version,
                "channels": cls._merge(
                    c.channels.extra_fields,
                    {
                        "suggestion_channel_id": c.channels.suggestion_channel_id,
                        "voting_channel_id": c.channels.voting_channel_id,
                        "watch_history_channel_id": c.channels.watch_history_channel_id,
                        "archive_channel_id": c.channels.archive_channel_id,
                    },
                ),
                "voting_overrides": cls._merge(
                    c.voting_overrides.extra_fields,
                    {
                        "candidate_count": c.voting_overrides.candidate_count,
                        "duration_hours": c.voting_overrides.duration_hours,
                        "visibility": (
                            c.voting_overrides.visibility.value
                            if c.voting_overrides.visibility is not None
                            else None
                        ),
                        "max_vote_changes": c.voting_overrides.max_vote_changes,
                        "tie_behavior": (
                            c.voting_overrides.tie_behavior.value
                            if c.voting_overrides.tie_behavior is not None
                            else None
                        ),
                    },
                ),
                "suggestion_rules": cls._merge(
                    c.suggestion_rules.extra_fields,
                    {
                        "allow_imdb_links": c.suggestion_rules.allow_imdb_links,
                        "allow_manual_titles": c.suggestion_rules.allow_manual_titles,
                        "require_unique_active_titles": c.suggestion_rules.require_unique_active_titles,
                        "rejection_threshold": c.suggestion_rules.rejection_threshold,
                        "allow_resuggestion": c.suggestion_rules.allow_resuggestion,
                        "candidate_selection": c.suggestion_rules.candidate_selection.value,
                    },
                ),
                "watch_history": cls._merge(
                    c.watch_history.extra_fields,
                    {
                        "enabled": c.watch_history.enabled,
                        "allow_retroactive_entries": c.watch_history.allow_retroactive_entries,
                        "allow_repeat_watches": c.watch_history.allow_repeat_watches,
                        "include_watch_date": c.watch_history.include_watch_date,
                        "include_vote_result": c.watch_history.include_vote_result,
                    },
                ),
                "archive": cls._merge(
                    c.archive.extra_fields,
                    {
                        "enabled": c.archive.enabled,
                        "archive_winner_after_watch": c.archive.archive_winner_after_watch,
                        "archive_rejected_suggestions": c.archive.archive_rejected_suggestions,
                        "allow_resuggestion": c.archive.allow_resuggestion,
                    },
                ),
                "notifications": cls._merge(
                    c.notifications.extra_fields,
                    {
                        "low_suggestion_pool_alerts": c.notifications.low_suggestion_pool_alerts,
                        "low_suggestion_pool_threshold": c.notifications.low_suggestion_pool_threshold,
                    },
                ),
                "permissions": cls._merge(
                    c.permissions.extra_fields,
                    {
                        "moderator_role_ids": list(c.permissions.moderator_role_ids),
                        "use_guild_watch_party_role": c.permissions.use_guild_watch_party_role,
                    },
                ),
            },
        )

    @classmethod
    def _deserialize(cls, entry: dict[str, Any]) -> SuggestionDatabaseConfiguration:
        channels = entry.get("channels") or {}
        voting = entry.get("voting_overrides") or {}
        rules = entry.get("suggestion_rules") or {}
        history = entry.get("watch_history") or {}
        archive = entry.get("archive") or {}
        notifications = entry.get("notifications") or {}
        permissions = entry.get("permissions") or {}

        top_known = {
            "schema_version",
            "guild_id",
            "database_id",
            "display_name",
            "active",
            "created_at",
            "updated_at",
            "configuration_version",
            "channels",
            "voting_overrides",
            "suggestion_rules",
            "watch_history",
            "archive",
            "notifications",
            "permissions",
        }

        return SuggestionDatabaseConfiguration(
            guild_id=entry["guild_id"],
            database_id=entry["database_id"],
            display_name=entry["display_name"],
            active=entry.get("active", True),
            schema_version=entry.get("schema_version", 1),
            created_at=datetime.fromisoformat(entry["created_at"]),
            updated_at=datetime.fromisoformat(entry["updated_at"]),
            configuration_version=entry.get("configuration_version", 1),
            channels=SuggestionDatabaseChannelsConfig(
                suggestion_channel_id=channels.get("suggestion_channel_id"),
                voting_channel_id=channels.get("voting_channel_id"),
                watch_history_channel_id=channels.get("watch_history_channel_id"),
                archive_channel_id=channels.get("archive_channel_id"),
                extra_fields=cls._split_known(
                    channels,
                    {
                        "suggestion_channel_id",
                        "voting_channel_id",
                        "watch_history_channel_id",
                        "archive_channel_id",
                    },
                ),
            ),
            voting_overrides=VotingOverridesConfig(
                candidate_count=voting.get("candidate_count"),
                duration_hours=voting.get("duration_hours"),
                visibility=(
                    GuildVoteVisibility(voting["visibility"])
                    if voting.get("visibility") is not None
                    else None
                ),
                max_vote_changes=voting.get("max_vote_changes"),
                tie_behavior=(
                    TieBehavior(voting["tie_behavior"]) if voting.get("tie_behavior") is not None else None
                ),
                extra_fields=cls._split_known(
                    voting,
                    {"candidate_count", "duration_hours", "visibility", "max_vote_changes", "tie_behavior"},
                ),
            ),
            suggestion_rules=SuggestionRulesConfig(
                allow_imdb_links=rules.get("allow_imdb_links", True),
                allow_manual_titles=rules.get("allow_manual_titles", True),
                require_unique_active_titles=rules.get("require_unique_active_titles", True),
                rejection_threshold=rules.get("rejection_threshold", 2),
                allow_resuggestion=rules.get("allow_resuggestion", True),
                candidate_selection=CandidateSelectionMode(
                    rules.get("candidate_selection", CandidateSelectionMode.BALANCED_RANDOM.value)
                ),
                extra_fields=cls._split_known(
                    rules,
                    {
                        "allow_imdb_links",
                        "allow_manual_titles",
                        "require_unique_active_titles",
                        "rejection_threshold",
                        "allow_resuggestion",
                        "candidate_selection",
                    },
                ),
            ),
            watch_history=SuggestionDatabaseWatchHistoryConfig(
                enabled=history.get("enabled", True),
                allow_retroactive_entries=history.get("allow_retroactive_entries", True),
                allow_repeat_watches=history.get("allow_repeat_watches", True),
                include_watch_date=history.get("include_watch_date", True),
                include_vote_result=history.get("include_vote_result", True),
                extra_fields=cls._split_known(
                    history,
                    {
                        "enabled",
                        "allow_retroactive_entries",
                        "allow_repeat_watches",
                        "include_watch_date",
                        "include_vote_result",
                    },
                ),
            ),
            archive=SuggestionDatabaseArchiveConfig(
                enabled=archive.get("enabled", True),
                archive_winner_after_watch=archive.get("archive_winner_after_watch", True),
                archive_rejected_suggestions=archive.get("archive_rejected_suggestions", True),
                allow_resuggestion=archive.get("allow_resuggestion", True),
                extra_fields=cls._split_known(
                    archive,
                    {
                        "enabled",
                        "archive_winner_after_watch",
                        "archive_rejected_suggestions",
                        "allow_resuggestion",
                    },
                ),
            ),
            notifications=SuggestionDatabaseNotificationOverridesConfig(
                low_suggestion_pool_alerts=notifications.get("low_suggestion_pool_alerts"),
                low_suggestion_pool_threshold=notifications.get("low_suggestion_pool_threshold"),
                extra_fields=cls._split_known(
                    notifications, {"low_suggestion_pool_alerts", "low_suggestion_pool_threshold"}
                ),
            ),
            permissions=SuggestionDatabasePermissionsConfig(
                moderator_role_ids=tuple(permissions.get("moderator_role_ids", ())),
                use_guild_watch_party_role=permissions.get("use_guild_watch_party_role", True),
                extra_fields=cls._split_known(
                    permissions, {"moderator_role_ids", "use_guild_watch_party_role"}
                ),
            ),
            extra_fields=cls._split_known(entry, top_known),
        )
