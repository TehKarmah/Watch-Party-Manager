"""JSON-backed persistence for per-guild WASH configuration."""

from __future__ import annotations

import copy
import json
import logging
import shutil
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Union

from watch_party_manager.domain.guild_configuration import (
    AdministrativeNotificationsConfig,
    BackupConfig,
    FeatureFlagsConfig,
    GuildChannelsConfig,
    GuildConfiguration,
    GuildSuggestionDatabaseEntry,
    JoinMode,
    MigrationConfig,
    NotificationsConfig,
    TieBehavior,
    VoteNotificationsConfig,
    VotingDefaultsConfig,
    GuildVoteVisibility,
    WatchHistoryConfig,
    WatchNotificationsConfig,
    WatchPartyRoleConfig,
)

logger = logging.getLogger(__name__)
DEFAULT_GUILD_CONFIGURATIONS_PATH = Path("data/guild_configurations.json")
CURRENT_SCHEMA_VERSION = 1


class FutureSchemaVersionError(ValueError):
    """Raised when persisted configuration was written by a newer schema."""


class GuildConfigurationRepository:
    """Loads and saves guild configuration in a single JSON document."""

    _MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}

    def __init__(self, file_path: Union[Path, str] = DEFAULT_GUILD_CONFIGURATIONS_PATH) -> None:
        self._file_path = Path(file_path)

    def get(self, guild_id: int) -> Optional[GuildConfiguration]:
        return self._load_all().get(guild_id)

    def exists(self, guild_id: int) -> bool:
        return guild_id in self._load_all()

    def list_all(self) -> list[GuildConfiguration]:
        return list(self._load_all().values())

    def save(self, configuration: GuildConfiguration) -> None:
        """Create or update a guild configuration atomically.

        Existing records retain created_at, increment configuration_version,
        and receive a fresh timezone-aware updated_at value.
        """
        configurations = self._load_all()
        existing = configurations.get(configuration.guild_id)
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
        configurations[persisted.guild_id] = persisted
        self._save_all(configurations)

    def delete(self, guild_id: int) -> bool:
        """Remove a guild's configuration entirely, e.g. during a factory reset.

        Mirrors SetupWizardRepository.delete()'s exact shape. Once removed,
        every existing "is setup complete" check (get() returning None)
        already treats the guild as never having been set up -- no
        separate flag needs clearing.

        Returns:
            True if a record existed and was removed, False otherwise.
        """
        configurations = self._load_all()
        if guild_id not in configurations:
            return False
        del configurations[guild_id]
        self._save_all(configurations)
        return True

    def _load_all(self) -> dict[int, GuildConfiguration]:
        if not self._file_path.exists():
            return {}
        try:
            data = json.loads(self._file_path.read_text(encoding="utf-8"))
            entries = data["guilds"]
            if not isinstance(entries, dict):
                raise TypeError("guilds must be an object")
            result: dict[int, GuildConfiguration] = {}
            for guild_id_key, raw_entry in entries.items():
                migrated = self._migrate(raw_entry)
                configuration = self._deserialize(migrated)
                if str(configuration.guild_id) != str(guild_id_key):
                    raise ValueError("guild key does not match guild_id")
                result[configuration.guild_id] = configuration
            return result
        except FutureSchemaVersionError:
            raise
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.error("Could not load guild configurations from %s: %s", self._file_path, exc)
            return {}

    def _save_all(self, configurations: dict[int, GuildConfiguration]) -> None:
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"guilds": {str(key): self._serialize(value) for key, value in configurations.items()}}
        temporary_path = self._file_path.with_suffix(self._file_path.suffix + ".tmp")
        temporary_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temporary_path.replace(self._file_path)

    def _migrate(self, raw_entry: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw_entry, dict):
            raise TypeError("guild configuration entry must be an object")
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
        if not self._file_path.exists():
            return
        backup_path = self._file_path.with_suffix(self._file_path.suffix + ".pre_migration.bak")
        shutil.copy2(self._file_path, backup_path)

    @staticmethod
    def _split_known(entry: dict[str, Any], known: set[str]) -> dict[str, Any]:
        return {key: copy.deepcopy(value) for key, value in entry.items() if key not in known}

    @staticmethod
    def _merge(extra: dict[str, Any], known: dict[str, Any]) -> dict[str, Any]:
        merged = copy.deepcopy(extra)
        merged.update(known)
        return merged

    @classmethod
    def _serialize(cls, c: GuildConfiguration) -> dict[str, Any]:
        return cls._merge(c.extra_fields, {
            "schema_version": c.schema_version,
            "guild_id": c.guild_id,
            "guild_name": c.guild_name,
            "setup_completed": c.setup_completed,
            "created_at": c.created_at.isoformat(),
            "updated_at": c.updated_at.isoformat(),
            "configuration_version": c.configuration_version,
            "wash_crew_role_id": c.wash_crew_role_id,
            "administrator_override": c.administrator_override,
            "watch_party_role": cls._merge(c.watch_party_role.extra_fields, {
                "role_id": c.watch_party_role.role_id,
                "join_mode": c.watch_party_role.join_mode.value,
                "allow_self_leave": c.watch_party_role.allow_self_leave,
                "denial_cooldown_days": c.watch_party_role.denial_cooldown_days,
            }),
            "suggestion_databases": {
                item.id: cls._merge(item.extra_fields, {
                    "id": item.id, "display_name": item.display_name, "active": item.active
                }) for item in c.suggestion_databases
            },
            "channels": cls._merge(c.channels.extra_fields, {
                "announcements_channel_id": c.channels.announcements_channel_id,
                "log_channel_id": c.channels.log_channel_id,
                "admin_channel_id": c.channels.admin_channel_id,
            }),
            "voting_defaults": cls._merge(c.voting_defaults.extra_fields, {
                "candidate_count": c.voting_defaults.candidate_count,
                "duration_days": c.voting_defaults.duration_days,
                "visibility": c.voting_defaults.visibility.value,
                "max_vote_changes": c.voting_defaults.max_vote_changes,
                "tie_behavior": c.voting_defaults.tie_behavior.value,
            }),
            "notifications": cls._merge(c.notifications.extra_fields, {
                "vote": cls._merge(c.notifications.vote.extra_fields, {
                    "vote_started": c.notifications.vote.vote_started,
                    "vote_results": c.notifications.vote.vote_results,
                    "vote_ending_reminder": c.notifications.vote.vote_ending_reminder,
                    "reminder_hours_before_close": c.notifications.vote.reminder_hours_before_close,
                }),
                "watch": cls._merge(c.notifications.watch.extra_fields, {
                    "enabled": c.notifications.watch.enabled,
                    "reminder_hours_before_watch": c.notifications.watch.reminder_hours_before_watch,
                }),
                "administrative": cls._merge(c.notifications.administrative.extra_fields, {
                    "low_suggestion_pool": c.notifications.administrative.low_suggestion_pool,
                    "low_suggestion_pool_threshold": c.notifications.administrative.low_suggestion_pool_threshold,
                    "backup_completed": c.notifications.administrative.backup_completed,
                    "backup_failed": c.notifications.administrative.backup_failed,
                    "restore_completed": c.notifications.administrative.restore_completed,
                    "restore_failed": c.notifications.administrative.restore_failed,
                }),
            }),
            "feature_flags": cls._merge(c.feature_flags.extra_fields, {
                "birthday_picks": c.feature_flags.birthday_picks,
                "self_service_watch_party_role": c.feature_flags.self_service_watch_party_role,
                "member_vote_reminders": c.feature_flags.member_vote_reminders,
                "watch_reminders": c.feature_flags.watch_reminders,
                "low_suggestion_pool_alerts": c.feature_flags.low_suggestion_pool_alerts,
                "suggestion_rejection_voting": c.feature_flags.suggestion_rejection_voting,
                "archived_suggestion_review": c.feature_flags.archived_suggestion_review,
            }),
            "backup": cls._merge(c.backup.extra_fields, {
                "include_in_automatic_backups": c.backup.include_in_automatic_backups,
                "notify_on_backup_success": c.backup.notify_on_backup_success,
                "notify_on_backup_failure": c.backup.notify_on_backup_failure,
                "allow_restore": c.backup.allow_restore,
            }),
            "watch_history": cls._merge(c.watch_history.extra_fields, {
                "enabled": c.watch_history.enabled,
                "allow_retroactive_entries": c.watch_history.allow_retroactive_entries,
                "allow_repeat_watches": c.watch_history.allow_repeat_watches,
            }),
            "migration": cls._merge(c.migration.extra_fields, {
                "current_schema_version": c.migration.current_schema_version,
                "automatic_migrations": c.migration.automatic_migrations,
                "backup_before_migration": c.migration.backup_before_migration,
                "reject_future_schema_versions": c.migration.reject_future_schema_versions,
            }),
        })

    @classmethod
    def _deserialize(cls, entry: dict[str, Any]) -> GuildConfiguration:
        role = entry.get("watch_party_role") or {}
        channels = entry.get("channels") or {}
        databases = entry.get("suggestion_databases") or {}
        voting = entry.get("voting_defaults") or {}
        notifications = entry.get("notifications") or {}
        vote_notice = notifications.get("vote") or {}
        watch_notice = notifications.get("watch") or {}
        admin_notice = notifications.get("administrative") or {}
        flags = entry.get("feature_flags") or {}
        backup = entry.get("backup") or {}
        history = entry.get("watch_history") or {}
        migration = entry.get("migration") or {}

        top_known = {
            "schema_version", "guild_id", "guild_name", "setup_completed", "created_at", "updated_at",
            "configuration_version", "wash_crew_role_id", "administrator_override", "watch_party_role",
            "suggestion_databases", "channels", "voting_defaults", "notifications", "feature_flags",
            "backup", "watch_history", "migration",
        }
        return GuildConfiguration(
            guild_id=entry["guild_id"], guild_name=entry["guild_name"],
            schema_version=entry.get("schema_version", 1), setup_completed=entry.get("setup_completed", False),
            created_at=datetime.fromisoformat(entry["created_at"]), updated_at=datetime.fromisoformat(entry["updated_at"]),
            configuration_version=entry.get("configuration_version", 1), wash_crew_role_id=entry.get("wash_crew_role_id"),
            administrator_override=entry.get("administrator_override", True),
            watch_party_role=WatchPartyRoleConfig(
                role_id=role.get("role_id"), join_mode=JoinMode(role.get("join_mode", "self_service")),
                allow_self_leave=role.get("allow_self_leave", True),
                denial_cooldown_days=role.get("denial_cooldown_days", 7),
                extra_fields=cls._split_known(role, {"role_id", "join_mode", "allow_self_leave", "denial_cooldown_days"}),
            ),
            suggestion_databases=tuple(
                GuildSuggestionDatabaseEntry(
                    id=value["id"], display_name=value["display_name"], active=value.get("active", True),
                    extra_fields=cls._split_known(value, {"id", "display_name", "active"}),
                ) for value in databases.values()
            ),
            channels=GuildChannelsConfig(
                announcements_channel_id=channels.get("announcements_channel_id"), log_channel_id=channels.get("log_channel_id"),
                admin_channel_id=channels.get("admin_channel_id"),
                extra_fields=cls._split_known(channels, {"announcements_channel_id", "log_channel_id", "admin_channel_id"}),
            ),
            voting_defaults=VotingDefaultsConfig(
                candidate_count=voting.get("candidate_count", 3), duration_days=voting.get("duration_days", 7),
                visibility=GuildVoteVisibility(voting.get("visibility", "blind")), max_vote_changes=voting.get("max_vote_changes", 1),
                tie_behavior=TieBehavior(voting.get("tie_behavior", "all_winners")),
                extra_fields=cls._split_known(voting, {"candidate_count", "duration_days", "visibility", "max_vote_changes", "tie_behavior"}),
            ),
            notifications=NotificationsConfig(
                vote=VoteNotificationsConfig(
                    vote_started=vote_notice.get("vote_started", True), vote_results=vote_notice.get("vote_results", True),
                    vote_ending_reminder=vote_notice.get("vote_ending_reminder", True),
                    reminder_hours_before_close=vote_notice.get("reminder_hours_before_close", 24),
                    extra_fields=cls._split_known(vote_notice, {"vote_started", "vote_results", "vote_ending_reminder", "reminder_hours_before_close"}),
                ),
                watch=WatchNotificationsConfig(
                    enabled=watch_notice.get("enabled", True), reminder_hours_before_watch=watch_notice.get("reminder_hours_before_watch", 1),
                    extra_fields=cls._split_known(watch_notice, {"enabled", "reminder_hours_before_watch"}),
                ),
                administrative=AdministrativeNotificationsConfig(
                    low_suggestion_pool=admin_notice.get("low_suggestion_pool", True),
                    low_suggestion_pool_threshold=admin_notice.get("low_suggestion_pool_threshold", 10),
                    backup_completed=admin_notice.get("backup_completed", True), backup_failed=admin_notice.get("backup_failed", True),
                    restore_completed=admin_notice.get("restore_completed", True), restore_failed=admin_notice.get("restore_failed", True),
                    extra_fields=cls._split_known(admin_notice, {"low_suggestion_pool", "low_suggestion_pool_threshold", "backup_completed", "backup_failed", "restore_completed", "restore_failed"}),
                ),
                extra_fields=cls._split_known(notifications, {"vote", "watch", "administrative"}),
            ),
            feature_flags=FeatureFlagsConfig(
                birthday_picks=flags.get("birthday_picks", False), self_service_watch_party_role=flags.get("self_service_watch_party_role", True),
                member_vote_reminders=flags.get("member_vote_reminders", True), watch_reminders=flags.get("watch_reminders", True),
                low_suggestion_pool_alerts=flags.get("low_suggestion_pool_alerts", True), suggestion_rejection_voting=flags.get("suggestion_rejection_voting", True),
                archived_suggestion_review=flags.get("archived_suggestion_review", True),
                extra_fields=cls._split_known(flags, {"birthday_picks", "self_service_watch_party_role", "member_vote_reminders", "watch_reminders", "low_suggestion_pool_alerts", "suggestion_rejection_voting", "archived_suggestion_review"}),
            ),
            backup=BackupConfig(
                include_in_automatic_backups=backup.get("include_in_automatic_backups", True), notify_on_backup_success=backup.get("notify_on_backup_success", True),
                notify_on_backup_failure=backup.get("notify_on_backup_failure", True), allow_restore=backup.get("allow_restore", True),
                extra_fields=cls._split_known(backup, {"include_in_automatic_backups", "notify_on_backup_success", "notify_on_backup_failure", "allow_restore"}),
            ),
            watch_history=WatchHistoryConfig(
                enabled=history.get("enabled", True), allow_retroactive_entries=history.get("allow_retroactive_entries", True),
                allow_repeat_watches=history.get("allow_repeat_watches", True),
                extra_fields=cls._split_known(history, {"enabled", "allow_retroactive_entries", "allow_repeat_watches"}),
            ),
            migration=MigrationConfig(
                current_schema_version=migration.get("current_schema_version", CURRENT_SCHEMA_VERSION),
                automatic_migrations=migration.get("automatic_migrations", True), backup_before_migration=migration.get("backup_before_migration", True),
                reject_future_schema_versions=migration.get("reject_future_schema_versions", True),
                extra_fields=cls._split_known(migration, {"current_schema_version", "automatic_migrations", "backup_before_migration", "reject_future_schema_versions"}),
            ),
            extra_fields=cls._split_known(entry, top_known),
        )
