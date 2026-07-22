"""Core logic for FR-029's /config command.

Unlike FR-028's /setup wizard (a linear first-time flow with a resumable
draft), /config has no draft state of its own: each section reads the
current GuildConfiguration/SuggestionDatabaseConfiguration, validates one
proposed change, and saves it immediately through the same repositories
setup_wizard_service.py already uses. Every section is independent --
saving one never rewrites another. /config is only usable once initial
setup has completed (see bot.py's /config command, which checks
GuildConfiguration.setup_completed before ever calling into this
service).

Kept free of Discord UI objects, mirroring setup_wizard_service.py's own
perform_*()-style separation, so every section's logic is unit-testable
without a live Discord connection.

Never redesigns GuildConfiguration, SuggestionDatabase, or
SuggestionDatabaseConfiguration -- and never touches
GuildConfiguration.suggestion_databases, the lightweight, deliberately
unreconciled parallel identifier scheme setup_wizard_service.py also
leaves alone (see that module's docstring).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import List, Optional, Tuple

from watch_party_manager.domain.guild_configuration import (
    GuildConfiguration,
    GuildVoteVisibility,
    JoinMode,
    VotingDefaultsConfig,
    WatchPartyRoleConfig,
)
from watch_party_manager.domain.suggestion_database import SuggestionDatabase
from watch_party_manager.domain.suggestion_database_configuration import (
    CandidateSelectionMode,
    SuggestionDatabaseConfiguration,
)
from watch_party_manager.persistence.guild_configuration_repository import (
    GuildConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.services.configuration_validation import (
    GuildLookup,
    validate_channel_usable,
    validate_role_exists,
)
from watch_party_manager.services.setup_wizard_service import (
    BACKUP_INTERVAL_DAYS_EXTRA_FIELD,
    BACKUP_RETENTION_COUNT_EXTRA_FIELD,
)
from watch_party_manager.services.suggestion_service import SuggestionService


class ConfigSection(str, Enum):
    """One editable section of /config, in main-menu display order."""

    WASH_CREW_ROLE = "wash_crew_role"
    WATCH_PARTY_ROLE = "watch_party_role"
    WATCH_PARTY_JOIN_MODE = "watch_party_join_mode"
    SUGGESTION_DATABASE = "suggestion_database"
    WATCH_DESTINATION = "watch_destination"
    VOTING_DEFAULTS = "voting_defaults"
    REMINDER_DEFAULTS = "reminder_defaults"
    BACKUP_DEFAULTS = "backup_defaults"


CONFIG_SECTION_ORDER: Tuple[ConfigSection, ...] = (
    ConfigSection.WASH_CREW_ROLE,
    ConfigSection.WATCH_PARTY_ROLE,
    ConfigSection.WATCH_PARTY_JOIN_MODE,
    ConfigSection.SUGGESTION_DATABASE,
    ConfigSection.WATCH_DESTINATION,
    ConfigSection.VOTING_DEFAULTS,
    ConfigSection.REMINDER_DEFAULTS,
    ConfigSection.BACKUP_DEFAULTS,
)

CONFIG_SECTION_TITLES: dict[ConfigSection, str] = {
    ConfigSection.WASH_CREW_ROLE: "WASH Crew Role",
    ConfigSection.WATCH_PARTY_ROLE: "Watch Party Role",
    ConfigSection.WATCH_PARTY_JOIN_MODE: "Watch Party Join Mode",
    ConfigSection.SUGGESTION_DATABASE: "Active Suggestion Database",
    ConfigSection.WATCH_DESTINATION: "Watched-Movie Destination",
    ConfigSection.VOTING_DEFAULTS: "Voting Defaults",
    ConfigSection.REMINDER_DEFAULTS: "Reminder Defaults",
    ConfigSection.BACKUP_DEFAULTS: "Backup Defaults",
}

NO_DATABASE_CONFIGURED_MESSAGE = (
    "Select an Active Suggestion Database first (exactly one active "
    "database is required before this section can be edited)."
)


@dataclass(frozen=True, slots=True)
class ConfigUpdateResult:
    """What happened when /config tried to save one section's change.

    On failure, nothing was persisted -- the caller's prior saved value
    is untouched, matching /config's "cancelled or invalid updates
    preserve the existing saved value, never partially persist" contract.
    """

    success: bool
    message: str
    configuration: Optional[GuildConfiguration] = None


class ConfigService:
    """Orchestrates FR-029's /config command: read current settings,
    validate one proposed section change, and save it immediately through
    the existing repositories -- exactly as any other caller would.
    """

    def __init__(
        self,
        guild_configuration_repository: GuildConfigurationRepository,
        suggestion_service: SuggestionService,
        suggestion_database_configuration_repository: SuggestionDatabaseConfigurationRepository,
    ) -> None:
        self._guild_configuration_repository = guild_configuration_repository
        self._suggestion_service = suggestion_service
        self._suggestion_database_configuration_repository = suggestion_database_configuration_repository

    # --- Reading current state ---------------------------------------------------

    def get_configuration(self, guild_id: int) -> Optional[GuildConfiguration]:
        """Return the guild's saved configuration, or None if setup was never completed."""
        return self._guild_configuration_repository.get(guild_id)

    def resolve_configured_database(self, guild_id: int) -> Optional[SuggestionDatabase]:
        """Return "the" active suggestion database for this guild, if unambiguous.

        Watched-Movie Destination and Voting Defaults' candidate-selection
        both live on a specific database's SuggestionDatabaseConfiguration
        (see docs/guild_configuration_spec.md's deferred-reconciliation
        note, also honored by setup_wizard_service.py). Multiple databases
        may be simultaneously active (SuggestionDatabase.active is a plain
        per-database flag, not an exclusive selector -- see
        SuggestionService.resolve_database_for_channel's own docstring),
        so this only resolves when exactly one active database exists;
        otherwise the caller should direct WASH Crew to the Active
        Suggestion Database section first.
        """
        active = [database for database in self._suggestion_service.list_databases(guild_id) if database.active]
        if len(active) == 1:
            return active[0]
        return None

    def build_summary_lines(self, guild_id: int, guild: GuildLookup) -> List[str]:
        """Build one status-prefixed summary line per section for the main menu.

        Unlike setup_wizard_service.build_review_lines() (which only ever
        reports Configured/Skipped/Incomplete from the in-memory draft),
        this also validates every configured resource against live
        Discord state, so a role or channel that no longer exists is
        reported as Invalid rather than silently shown as Configured.
        """
        configuration = self.get_configuration(guild_id)
        if configuration is None:
            return [f"{CONFIG_SECTION_TITLES[section]}: Not configured" for section in CONFIG_SECTION_ORDER]

        lines: List[str] = []

        wash_crew_role_id = configuration.wash_crew_role_id
        if wash_crew_role_id is None:
            lines.append("WASH Crew Role: Not configured")
        elif validate_role_exists(wash_crew_role_id, guild, resource_label="WASH Crew role"):
            lines.append(f"WASH Crew Role: Invalid (<@&{wash_crew_role_id}> no longer exists)")
        else:
            lines.append(f"WASH Crew Role: Configured (<@&{wash_crew_role_id}>)")

        watch_party_role_id = configuration.watch_party_role.role_id
        if watch_party_role_id is None:
            lines.append("Watch Party Role: Not configured")
        elif validate_role_exists(watch_party_role_id, guild, resource_label="Watch Party role"):
            lines.append(f"Watch Party Role: Invalid (<@&{watch_party_role_id}> no longer exists)")
        else:
            lines.append(f"Watch Party Role: Configured (<@&{watch_party_role_id}>)")

        lines.append(f"Watch Party Join Mode: Configured ({configuration.watch_party_role.join_mode.value})")

        active_databases = [
            database for database in self._suggestion_service.list_databases(guild_id) if database.active
        ]
        if len(active_databases) == 1:
            lines.append(f'Active Suggestion Database: Configured ("{active_databases[0].name}")')
        elif len(active_databases) == 0:
            lines.append("Active Suggestion Database: Not configured")
        else:
            lines.append("Active Suggestion Database: Invalid (multiple active databases; select one below)")

        destination_channel_id = self._resolve_watch_destination_channel_id(guild_id)
        if destination_channel_id is None:
            if len(active_databases) == 1:
                lines.append("Watched-Movie Destination: Skipped")
            else:
                lines.append("Watched-Movie Destination: Not configured")
        elif validate_channel_usable(destination_channel_id, guild):
            lines.append(f"Watched-Movie Destination: Invalid (<#{destination_channel_id}> no longer usable)")
        else:
            lines.append(f"Watched-Movie Destination: Configured (<#{destination_channel_id}>)")

        voting_defaults = configuration.voting_defaults
        lines.append(
            "Voting Defaults: Configured "
            f"({voting_defaults.candidate_count} nominees, {voting_defaults.duration_days} day(s), "
            f"{voting_defaults.visibility.value})"
        )

        vote_notifications = configuration.notifications.vote
        if vote_notifications.vote_ending_reminder:
            lines.append(
                f"Reminder Defaults: Configured (enabled, {vote_notifications.reminder_hours_before_close}h before close)"
            )
        else:
            lines.append("Reminder Defaults: Configured (disabled)")

        interval = configuration.backup.extra_fields.get(BACKUP_INTERVAL_DAYS_EXTRA_FIELD)
        retention = configuration.backup.extra_fields.get(BACKUP_RETENTION_COUNT_EXTRA_FIELD)
        if interval is None or retention is None:
            lines.append("Backup Defaults: Not configured")
        else:
            lines.append(f"Backup Defaults: Configured (every {interval} day(s), keep {retention})")

        return lines

    def _resolve_watch_destination_channel_id(self, guild_id: int) -> Optional[int]:
        database = self.resolve_configured_database(guild_id)
        if database is None:
            return None
        database_configuration = self._suggestion_database_configuration_repository.get(
            guild_id, database.database_id
        )
        if database_configuration is None:
            return None
        return database_configuration.channels.watch_history_channel_id

    # --- WASH Crew Role ------------------------------------------------------------

    def set_wash_crew_role(self, guild_id: int, role_id: int, guild: GuildLookup) -> ConfigUpdateResult:
        configuration = self.get_configuration(guild_id)
        if configuration is None:
            return ConfigUpdateResult(False, "Run `/setup` before using `/config`.")

        error = validate_role_exists(role_id, guild, resource_label="WASH Crew role")
        if error:
            return ConfigUpdateResult(False, error)

        updated = replace(configuration, wash_crew_role_id=role_id)
        self._guild_configuration_repository.save(updated)
        return ConfigUpdateResult(True, f"WASH Crew role updated to <@&{role_id}>.", updated)

    # --- Watch Party Role ------------------------------------------------------------

    def set_watch_party_role(
        self, guild_id: int, role_id: Optional[int], guild: GuildLookup
    ) -> ConfigUpdateResult:
        configuration = self.get_configuration(guild_id)
        if configuration is None:
            return ConfigUpdateResult(False, "Run `/setup` before using `/config`.")

        error = validate_role_exists(role_id, guild, resource_label="Watch Party role")
        if error:
            return ConfigUpdateResult(False, error)

        updated = replace(
            configuration,
            watch_party_role=replace(configuration.watch_party_role, role_id=role_id),
        )
        self._guild_configuration_repository.save(updated)
        message = (
            f"Watch Party role updated to <@&{role_id}>." if role_id is not None else "Watch Party role cleared."
        )
        return ConfigUpdateResult(True, message, updated)

    # --- Watch Party Join Mode ------------------------------------------------------------

    def set_watch_party_join_mode(self, guild_id: int, join_mode: JoinMode) -> ConfigUpdateResult:
        configuration = self.get_configuration(guild_id)
        if configuration is None:
            return ConfigUpdateResult(False, "Run `/setup` before using `/config`.")

        updated = replace(
            configuration,
            watch_party_role=replace(configuration.watch_party_role, join_mode=join_mode),
        )
        self._guild_configuration_repository.save(updated)
        return ConfigUpdateResult(True, f"Watch Party join mode updated to {join_mode.value}.", updated)

    # --- Active Suggestion Database ------------------------------------------------------------

    def set_active_suggestion_database(self, guild_id: int, database_id: int) -> ConfigUpdateResult:
        configuration = self.get_configuration(guild_id)
        if configuration is None:
            return ConfigUpdateResult(False, "Run `/setup` before using `/config`.")

        database = self._suggestion_service.get_database(database_id)
        if database is None or database.guild_id != guild_id:
            return ConfigUpdateResult(False, "That suggestion database doesn't exist.")

        if not database.active:
            self._suggestion_service.activate_database(database_id, guild_id)

        return ConfigUpdateResult(
            True, f'"{database.name}" is now the active suggestion database.', configuration
        )

    # --- Watched-Movie Destination ------------------------------------------------------------

    def set_watch_destination(self, guild_id: int, channel_id: int, guild: GuildLookup) -> ConfigUpdateResult:
        database = self.resolve_configured_database(guild_id)
        if database is None:
            return ConfigUpdateResult(False, NO_DATABASE_CONFIGURED_MESSAGE)

        error = validate_channel_usable(channel_id, guild)
        if error:
            return ConfigUpdateResult(False, error)

        self._save_database_channel(guild_id, database, channel_id)
        return ConfigUpdateResult(
            True, f"Watched-movie destination updated to <#{channel_id}>.", self.get_configuration(guild_id)
        )

    def skip_watch_destination(self, guild_id: int) -> ConfigUpdateResult:
        database = self.resolve_configured_database(guild_id)
        if database is None:
            return ConfigUpdateResult(False, NO_DATABASE_CONFIGURED_MESSAGE)

        self._save_database_channel(guild_id, database, None)
        return ConfigUpdateResult(
            True, "Watched-movie destination cleared.", self.get_configuration(guild_id)
        )

    def _save_database_channel(
        self, guild_id: int, database: SuggestionDatabase, channel_id: Optional[int]
    ) -> None:
        existing = self._suggestion_database_configuration_repository.get(guild_id, database.database_id)
        base = existing or SuggestionDatabaseConfiguration(
            guild_id=guild_id, database_id=database.database_id, display_name=database.name
        )
        updated = replace(
            base, channels=replace(base.channels, watch_history_channel_id=channel_id)
        )
        self._suggestion_database_configuration_repository.save(updated)

    # --- Voting Defaults ------------------------------------------------------------

    def set_voting_defaults(
        self,
        guild_id: int,
        candidate_count: int,
        duration_days: int,
        visibility: GuildVoteVisibility,
        candidate_selection: CandidateSelectionMode,
    ) -> ConfigUpdateResult:
        configuration = self.get_configuration(guild_id)
        if configuration is None:
            return ConfigUpdateResult(False, "Run `/setup` before using `/config`.")

        updated = replace(
            configuration,
            voting_defaults=VotingDefaultsConfig(
                candidate_count=candidate_count,
                duration_days=duration_days,
                visibility=visibility,
                max_vote_changes=configuration.voting_defaults.max_vote_changes,
                tie_behavior=configuration.voting_defaults.tie_behavior,
            ),
        )
        self._guild_configuration_repository.save(updated)

        database = self.resolve_configured_database(guild_id)
        if database is None:
            return ConfigUpdateResult(
                True,
                "Voting defaults updated. Candidate selection was not saved: "
                "no single active suggestion database is configured.",
                updated,
            )

        existing = self._suggestion_database_configuration_repository.get(guild_id, database.database_id)
        base = existing or SuggestionDatabaseConfiguration(
            guild_id=guild_id, database_id=database.database_id, display_name=database.name
        )
        self._suggestion_database_configuration_repository.save(
            replace(base, suggestion_rules=replace(base.suggestion_rules, candidate_selection=candidate_selection))
        )
        return ConfigUpdateResult(True, "Voting defaults updated.", updated)

    # --- Reminder Defaults ------------------------------------------------------------

    def set_reminder_defaults(
        self, guild_id: int, enabled: bool, hours_before_close: int
    ) -> ConfigUpdateResult:
        configuration = self.get_configuration(guild_id)
        if configuration is None:
            return ConfigUpdateResult(False, "Run `/setup` before using `/config`.")

        updated = replace(
            configuration,
            notifications=replace(
                configuration.notifications,
                vote=replace(
                    configuration.notifications.vote,
                    vote_ending_reminder=enabled,
                    reminder_hours_before_close=hours_before_close,
                ),
            ),
        )
        self._guild_configuration_repository.save(updated)
        message = (
            f"Reminder defaults updated: enabled, {hours_before_close}h before close."
            if enabled
            else "Reminder defaults updated: disabled."
        )
        return ConfigUpdateResult(True, message, updated)

    # --- Backup Defaults ------------------------------------------------------------

    def set_backup_defaults(
        self, guild_id: int, interval_days: int, retention_count: int
    ) -> ConfigUpdateResult:
        configuration = self.get_configuration(guild_id)
        if configuration is None:
            return ConfigUpdateResult(False, "Run `/setup` before using `/config`.")

        backup_extra_fields = dict(configuration.backup.extra_fields)
        backup_extra_fields[BACKUP_INTERVAL_DAYS_EXTRA_FIELD] = interval_days
        backup_extra_fields[BACKUP_RETENTION_COUNT_EXTRA_FIELD] = retention_count
        updated = replace(configuration, backup=replace(configuration.backup, extra_fields=backup_extra_fields))
        self._guild_configuration_repository.save(updated)
        return ConfigUpdateResult(
            True, f"Backup defaults updated: every {interval_days} day(s), keep {retention_count}.", updated
        )
