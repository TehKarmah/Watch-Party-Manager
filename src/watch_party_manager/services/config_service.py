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
)
from watch_party_manager.domain.suggestion_database import SuggestionDatabase
from watch_party_manager.domain.suggestion_database_configuration import (
    CANDIDATE_SELECTION_DISPLAY_LABELS,
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
from watch_party_manager.services.duration_formatter import format_duration_minutes
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
    ADMIN_CHANNEL = "admin_channel"
    HOME_CHANNEL = "home_channel"
    # Watch Party Lifecycle: where scheduled/reminder/completion/
    # cancellation announcements post -- deliberately separate from Home
    # Channel, a collection's suggestion thread, and the Watched Movie
    # Destination (see set_watch_party_announcement_destination).
    WATCH_PARTY_ANNOUNCEMENT_DESTINATION = "watch_party_announcement_destination"
    MANAGE_COLLECTIONS = "manage_collections"
    WATCH_DESTINATION = "watch_destination"
    VOTING_DEFAULTS = "voting_defaults"
    REMINDER_DEFAULTS = "reminder_defaults"
    BACKUP_DEFAULTS = "backup_defaults"


CONFIG_SECTION_ORDER: Tuple[ConfigSection, ...] = (
    ConfigSection.WASH_CREW_ROLE,
    ConfigSection.WATCH_PARTY_ROLE,
    ConfigSection.WATCH_PARTY_JOIN_MODE,
    ConfigSection.ADMIN_CHANNEL,
    # Polish batch: given its own top-level, prominently-labeled section
    # and summary line (previously it had neither) -- every collection's
    # suggestion/watched-movie thread is created as a sibling under it,
    # so an administrator needs to see its status at a glance, not
    # discover it only indirectly via a failed Create New Thread.
    ConfigSection.HOME_CHANNEL,
    ConfigSection.WATCH_PARTY_ANNOUNCEMENT_DESTINATION,
    ConfigSection.MANAGE_COLLECTIONS,
    ConfigSection.WATCH_DESTINATION,
    ConfigSection.VOTING_DEFAULTS,
    ConfigSection.REMINDER_DEFAULTS,
    ConfigSection.BACKUP_DEFAULTS,
)

CONFIG_SECTION_TITLES: dict[ConfigSection, str] = {
    ConfigSection.WASH_CREW_ROLE: "WASH Crew Role",
    ConfigSection.WATCH_PARTY_ROLE: "Watch Party Role",
    ConfigSection.WATCH_PARTY_JOIN_MODE: "Watch Party Join Mode",
    ConfigSection.ADMIN_CHANNEL: "Admin Channel",
    ConfigSection.HOME_CHANNEL: "Watch Party Home Channel",
    ConfigSection.WATCH_PARTY_ANNOUNCEMENT_DESTINATION: "Watch Party Announcement Destination",
    ConfigSection.MANAGE_COLLECTIONS: "Manage Collections",
    ConfigSection.WATCH_DESTINATION: "Watched Movie Destination (Default)",
    ConfigSection.VOTING_DEFAULTS: "Voting Defaults",
    ConfigSection.REMINDER_DEFAULTS: "Reminder Defaults",
    ConfigSection.BACKUP_DEFAULTS: "Backup Defaults",
}


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

        admin_channel_id = configuration.channels.admin_channel_id
        if admin_channel_id is None:
            lines.append("Admin Channel: Not configured")
        elif validate_channel_usable(admin_channel_id, guild, resource_label="Admin channel"):
            lines.append(f"Admin Channel: Invalid (<#{admin_channel_id}> no longer usable)")
        else:
            lines.append(f"Admin Channel: Configured (<#{admin_channel_id}>)")

        home_channel_id = configuration.channels.home_channel_id
        if home_channel_id is None:
            lines.append("Watch Party Home Channel: Not configured")
        elif validate_channel_usable(home_channel_id, guild, resource_label="Home Channel"):
            lines.append(f"Watch Party Home Channel: Invalid (<#{home_channel_id}> no longer usable)")
        else:
            lines.append(f"Watch Party Home Channel: Configured (<#{home_channel_id}>)")

        announcement_channel_id = configuration.channels.announcements_channel_id
        if announcement_channel_id is None:
            lines.append("Watch Party Announcement Destination: Configured (using Home Channel)")
        elif validate_channel_usable(announcement_channel_id, guild, resource_label="Announcement Destination"):
            lines.append(
                f"Watch Party Announcement Destination: Invalid (<#{announcement_channel_id}> no longer usable)"
            )
        else:
            lines.append(f"Watch Party Announcement Destination: Configured (<#{announcement_channel_id}>)")

        databases = self._suggestion_service.list_databases(guild_id)
        active_databases = [database for database in databases if database.active]
        if not databases:
            lines.append("Manage Collections: Not configured")
        else:
            lines.append(
                f"Manage Collections: Configured ({len(active_databases)} active of {len(databases)} total -- "
                "select below to edit each collection's destination and candidate selection)"
            )

        watch_destination_channel_id = configuration.channels.watch_history_channel_id
        if watch_destination_channel_id is None:
            lines.append("Watched Movie Destination (Default): Not configured")
        elif validate_channel_usable(watch_destination_channel_id, guild):
            lines.append(
                f"Watched Movie Destination (Default): Invalid (<#{watch_destination_channel_id}> no longer usable)"
            )
        else:
            lines.append(f"Watched Movie Destination (Default): Configured (<#{watch_destination_channel_id}>)")

        voting_defaults = configuration.voting_defaults
        lines.append(
            "Voting Defaults: Configured "
            f"({voting_defaults.candidate_count} candidates, {format_duration_minutes(voting_defaults.duration_minutes)}, "
            f"{voting_defaults.visibility.value})"
        )

        vote_notifications = configuration.notifications.vote
        if vote_notifications.vote_ending_reminder:
            lines.append(
                "Reminder Defaults: Configured (enabled, "
                f"{format_duration_minutes(vote_notifications.reminder_minutes_before_close)} before close)"
            )
        else:
            lines.append("Reminder Defaults: Configured (disabled)")

        if not configuration.backup.include_in_automatic_backups:
            lines.append("Backup Defaults: Configured (Automatic Backups: Disabled)")
        else:
            interval = configuration.backup.extra_fields.get(BACKUP_INTERVAL_DAYS_EXTRA_FIELD)
            retention = configuration.backup.extra_fields.get(BACKUP_RETENTION_COUNT_EXTRA_FIELD)
            if interval is None or retention is None:
                lines.append("Backup Defaults: Not configured")
            else:
                day_word = "day" if interval == 1 else "days"
                lines.append(
                    f"Backup Defaults: Configured (Automatic Backups: Every {interval} {day_word}, keep {retention})"
                )

        return lines

    def resolve_effective_watch_destination(self, guild_id: int, database_id: int) -> Optional[int]:
        """The channel where a collection's watched-movie history should
        post: its own override if set, otherwise the guild-wide default
        (Watched Movie Destination (Default) section), otherwise None
        (nothing posted). Unlike a suggestion destination, this may
        legitimately be None, and the same channel may be shared by any
        number of collections and/or the guild default -- never checked
        for conflicts.
        """
        database_configuration = self._suggestion_database_configuration_repository.get(guild_id, database_id)
        if database_configuration is not None and database_configuration.channels.watch_history_channel_id is not None:
            return database_configuration.channels.watch_history_channel_id

        configuration = self.get_configuration(guild_id)
        if configuration is None:
            return None
        return configuration.channels.watch_history_channel_id

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

    # --- Admin Channel ------------------------------------------------------------

    def set_admin_channel(self, guild_id: int, channel_id: int, guild: GuildLookup) -> ConfigUpdateResult:
        configuration = self.get_configuration(guild_id)
        if configuration is None:
            return ConfigUpdateResult(False, "Run `/setup` before using `/config`.")

        error = validate_channel_usable(channel_id, guild, resource_label="Admin channel")
        if error:
            return ConfigUpdateResult(False, error)

        updated = replace(configuration, channels=replace(configuration.channels, admin_channel_id=channel_id))
        self._guild_configuration_repository.save(updated)
        return ConfigUpdateResult(True, f"Admin channel updated to <#{channel_id}>.", updated)

    def clear_admin_channel(self, guild_id: int) -> ConfigUpdateResult:
        configuration = self.get_configuration(guild_id)
        if configuration is None:
            return ConfigUpdateResult(False, "Run `/setup` before using `/config`.")

        updated = replace(configuration, channels=replace(configuration.channels, admin_channel_id=None))
        self._guild_configuration_repository.save(updated)
        return ConfigUpdateResult(True, "Admin channel cleared.", updated)

    # --- Home Channel ---------------------------------------------------------------

    def set_home_channel(self, guild_id: int, channel_id: int, guild: GuildLookup) -> ConfigUpdateResult:
        configuration = self.get_configuration(guild_id)
        if configuration is None:
            return ConfigUpdateResult(False, "Run `/setup` before using `/config`.")

        error = validate_channel_usable(channel_id, guild, resource_label="Home Channel")
        if error:
            return ConfigUpdateResult(False, error)

        updated = replace(configuration, channels=replace(configuration.channels, home_channel_id=channel_id))
        self._guild_configuration_repository.save(updated)
        return ConfigUpdateResult(True, f"Watch Party Home Channel updated to <#{channel_id}>.", updated)

    def clear_home_channel(self, guild_id: int) -> ConfigUpdateResult:
        configuration = self.get_configuration(guild_id)
        if configuration is None:
            return ConfigUpdateResult(False, "Run `/setup` before using `/config`.")

        updated = replace(configuration, channels=replace(configuration.channels, home_channel_id=None))
        self._guild_configuration_repository.save(updated)
        return ConfigUpdateResult(
            True,
            "Watch Party Home Channel cleared. Create New Thread will no longer have a "
            "configured parent channel until a new one is set.",
            updated,
        )

    # --- Watch Party Announcement Destination --------------------------------------------
    #
    # Where scheduled/reminder/completion/cancellation announcements post
    # (Watch Party Lifecycle). Deliberately separate from Home Channel (a
    # collection's own home for organizing threads and hosting general
    # discussion), a collection's suggestion thread, and the Watched
    # Movie Destination (a running record of what's already been
    # watched) -- this is specifically for forward-looking watch party
    # announcements. None means "use the Home Channel" (the documented
    # default), resolved dynamically at each use (see
    # resolve_watch_party_announcement_destination) rather than copying
    # the Home Channel's ID at the moment this is cleared, so it keeps
    # following the Home Channel if that's ever changed later. Threads
    # are never valid here -- announcements always post to a real channel.

    def set_watch_party_announcement_destination(
        self, guild_id: int, channel_id: int, guild: GuildLookup
    ) -> ConfigUpdateResult:
        configuration = self.get_configuration(guild_id)
        if configuration is None:
            return ConfigUpdateResult(False, "Run `/setup` before using `/config`.")

        error = validate_channel_usable(channel_id, guild, resource_label="Announcement Destination")
        if error:
            return ConfigUpdateResult(False, error)

        updated = replace(
            configuration, channels=replace(configuration.channels, announcements_channel_id=channel_id)
        )
        self._guild_configuration_repository.save(updated)
        return ConfigUpdateResult(
            True, f"Watch Party Announcement Destination updated to <#{channel_id}>.", updated
        )

    def clear_watch_party_announcement_destination(self, guild_id: int) -> ConfigUpdateResult:
        configuration = self.get_configuration(guild_id)
        if configuration is None:
            return ConfigUpdateResult(False, "Run `/setup` before using `/config`.")

        updated = replace(configuration, channels=replace(configuration.channels, announcements_channel_id=None))
        self._guild_configuration_repository.save(updated)
        return ConfigUpdateResult(
            True, "Watch Party Announcement Destination will now use the Home Channel.", updated
        )

    def resolve_watch_party_announcement_destination(self, guild_id: int) -> Optional[int]:
        """The channel watch party announcements should post to: the
        explicitly configured destination if set, otherwise WASH's Home
        Channel, otherwise None (nowhere configured to announce to).
        """
        configuration = self.get_configuration(guild_id)
        if configuration is None:
            return None
        if configuration.channels.announcements_channel_id is not None:
            return configuration.channels.announcements_channel_id
        return configuration.channels.home_channel_id

    # --- Watched Movie Destination (guild-wide default) -----------------------------------

    def set_guild_watch_destination(self, guild_id: int, channel_id: int, guild: GuildLookup) -> ConfigUpdateResult:
        configuration = self.get_configuration(guild_id)
        if configuration is None:
            return ConfigUpdateResult(False, "Run `/setup` before using `/config`.")

        error = validate_channel_usable(channel_id, guild)
        if error:
            return ConfigUpdateResult(False, error)

        updated = replace(configuration, channels=replace(configuration.channels, watch_history_channel_id=channel_id))
        self._guild_configuration_repository.save(updated)
        return ConfigUpdateResult(True, f"Default watched movie destination updated to <#{channel_id}>.", updated)

    def clear_guild_watch_destination(self, guild_id: int) -> ConfigUpdateResult:
        configuration = self.get_configuration(guild_id)
        if configuration is None:
            return ConfigUpdateResult(False, "Run `/setup` before using `/config`.")

        updated = replace(configuration, channels=replace(configuration.channels, watch_history_channel_id=None))
        self._guild_configuration_repository.save(updated)
        return ConfigUpdateResult(True, "Default watched movie destination cleared.", updated)

    # --- Manage Collections ------------------------------------------------------------
    #
    # Replaces the old "Active Suggestion Database" section (which required
    # picking exactly one database before any of its settings could be
    # edited) with direct, per-collection management: WASH Crew picks which
    # collection to edit (see bot.py's picker, shown whenever more than one
    # collection exists -- never guessed), then edits that collection's own
    # settings. No collection is ever "the" active one. Every collection
    # always has exactly one suggestion destination -- it can be changed,
    # but never cleared (see set_database_suggestion_destination); a
    # watched-movie destination remains optional and may be shared freely
    # (see resolve_effective_watch_destination above).

    def _get_database_for_guild(self, guild_id: int, database_id: int) -> Optional[SuggestionDatabase]:
        database = self._suggestion_service.get_database(database_id)
        if database is None or database.guild_id != guild_id:
            return None
        return database

    def get_database_configuration(self, guild_id: int, database_id: int) -> SuggestionDatabaseConfiguration:
        """Return this database's saved configuration, or a fresh default
        (never persisted until something is actually changed) if none
        exists yet -- so callers always have a concrete object to read
        current values from.
        """
        database = self._suggestion_service.get_database(database_id)
        display_name = database.name if database is not None else ""
        existing = self._suggestion_database_configuration_repository.get(guild_id, database_id)
        return existing or SuggestionDatabaseConfiguration(
            guild_id=guild_id, database_id=database_id, display_name=display_name
        )

    def set_database_suggestion_destination(
        self, guild_id: int, database_id: int, channel_id: int, guild: GuildLookup
    ) -> ConfigUpdateResult:
        database = self._get_database_for_guild(guild_id, database_id)
        if database is None:
            return ConfigUpdateResult(False, "That collection doesn't exist.")

        # Collections Should Live In Threads: WASH's configured Home
        # Channel organizes collection threads and hosts announcements/
        # discussion -- it must never itself become a collection's
        # suggestion destination. Checked here, the one shared
        # implementation behind both /config's Suggestion Destination
        # section and /database move (and, through it, /database
        # manage's Move Collection), so the rule can't be bypassed
        # through either path.
        configuration = self.get_configuration(guild_id)
        home_channel_id = configuration.channels.home_channel_id if configuration is not None else None
        if home_channel_id is not None and channel_id == home_channel_id:
            return ConfigUpdateResult(
                False,
                "WASH's Watch Party Home Channel can't be used as a collection's suggestion "
                "destination -- it's reserved for discussion, announcements, and navigation. "
                "Choose a thread instead.",
            )

        error = validate_channel_usable(channel_id, guild)
        if error:
            return ConfigUpdateResult(False, error)

        conflict = self._suggestion_service.find_database_using_channel(
            guild_id,
            channel_id,
            self._suggestion_database_configuration_repository,
            exclude_database_id=database_id,
        )
        if conflict is not None:
            return ConfigUpdateResult(
                False, f'<#{channel_id}> is already routed to "{conflict.name}". Choose a different thread.'
            )

        self._save_database_channel(guild_id, database, channel_id, field_name="suggestion_channel_id")
        return ConfigUpdateResult(
            True, f"Suggestion post destination updated to <#{channel_id}>.", self.get_configuration(guild_id)
        )

    def set_database_watch_destination(
        self, guild_id: int, database_id: int, channel_id: int, guild: GuildLookup
    ) -> ConfigUpdateResult:
        database = self._get_database_for_guild(guild_id, database_id)
        if database is None:
            return ConfigUpdateResult(False, "That collection doesn't exist.")

        error = validate_channel_usable(channel_id, guild)
        if error:
            return ConfigUpdateResult(False, error)

        self._save_database_channel(guild_id, database, channel_id, field_name="watch_history_channel_id")
        return ConfigUpdateResult(
            True, f"Watched movie destination updated to <#{channel_id}>.", self.get_configuration(guild_id)
        )

    def clear_database_watch_destination(self, guild_id: int, database_id: int) -> ConfigUpdateResult:
        database = self._get_database_for_guild(guild_id, database_id)
        if database is None:
            return ConfigUpdateResult(False, "That collection doesn't exist.")

        self._save_database_channel(guild_id, database, None, field_name="watch_history_channel_id")
        return ConfigUpdateResult(
            True, "Watched movie destination cleared.", self.get_configuration(guild_id)
        )

    def set_database_candidate_selection(
        self, guild_id: int, database_id: int, candidate_selection: CandidateSelectionMode
    ) -> ConfigUpdateResult:
        database = self._get_database_for_guild(guild_id, database_id)
        if database is None:
            return ConfigUpdateResult(False, "That collection doesn't exist.")

        base = self.get_database_configuration(guild_id, database_id)
        self._suggestion_database_configuration_repository.save(
            replace(base, suggestion_rules=replace(base.suggestion_rules, candidate_selection=candidate_selection))
        )
        label = CANDIDATE_SELECTION_DISPLAY_LABELS[candidate_selection]
        return ConfigUpdateResult(
            True, f'Candidate selection for "{database.name}" updated to {label}.', self.get_configuration(guild_id)
        )

    def _save_database_channel(
        self, guild_id: int, database: SuggestionDatabase, channel_id: Optional[int], *, field_name: str
    ) -> None:
        base = self.get_database_configuration(guild_id, database.database_id)
        updated = replace(base, channels=replace(base.channels, **{field_name: channel_id}))
        self._suggestion_database_configuration_repository.save(updated)

    # --- Voting Defaults ------------------------------------------------------------

    def set_voting_defaults(
        self,
        guild_id: int,
        candidate_count: int,
        duration_minutes: int,
        visibility: GuildVoteVisibility,
    ) -> ConfigUpdateResult:
        configuration = self.get_configuration(guild_id)
        if configuration is None:
            return ConfigUpdateResult(False, "Run `/setup` before using `/config`.")

        updated = replace(
            configuration,
            voting_defaults=VotingDefaultsConfig(
                candidate_count=candidate_count,
                duration_minutes=duration_minutes,
                visibility=visibility,
                max_vote_changes=configuration.voting_defaults.max_vote_changes,
                tie_behavior=configuration.voting_defaults.tie_behavior,
            ),
        )
        self._guild_configuration_repository.save(updated)
        message = (
            f"Voting defaults updated: {candidate_count} candidates, "
            f"{format_duration_minutes(duration_minutes)}, {visibility.value.title()}."
        )
        return ConfigUpdateResult(True, message, updated)

    # --- Reminder Defaults ------------------------------------------------------------

    def set_reminder_defaults(
        self, guild_id: int, enabled: bool, minutes_before_close: int
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
                    reminder_minutes_before_close=minutes_before_close,
                ),
            ),
        )
        self._guild_configuration_repository.save(updated)
        message = (
            f"Reminder defaults updated: enabled, {format_duration_minutes(minutes_before_close)} before close."
            if enabled
            else "Reminder defaults updated: disabled."
        )
        return ConfigUpdateResult(True, message, updated)

    # --- Backup Defaults ------------------------------------------------------------

    def enable_automatic_backups(
        self, guild_id: int, interval_days: int, retention_count: int
    ) -> ConfigUpdateResult:
        configuration = self.get_configuration(guild_id)
        if configuration is None:
            return ConfigUpdateResult(False, "Run `/setup` before using `/config`.")

        backup_extra_fields = dict(configuration.backup.extra_fields)
        backup_extra_fields[BACKUP_INTERVAL_DAYS_EXTRA_FIELD] = interval_days
        backup_extra_fields[BACKUP_RETENTION_COUNT_EXTRA_FIELD] = retention_count
        updated = replace(
            configuration,
            backup=replace(
                configuration.backup, include_in_automatic_backups=True, extra_fields=backup_extra_fields
            ),
        )
        self._guild_configuration_repository.save(updated)
        day_word = "day" if interval_days == 1 else "days"
        return ConfigUpdateResult(
            True,
            f"Backup defaults updated: Automatic Backups: Every {interval_days} {day_word}, keep {retention_count}.",
            updated,
        )

    def disable_automatic_backups(self, guild_id: int) -> ConfigUpdateResult:
        """Disable automatic backups. Manual /backup remains available
        regardless, and existing backups are never deleted by this.
        """
        configuration = self.get_configuration(guild_id)
        if configuration is None:
            return ConfigUpdateResult(False, "Run `/setup` before using `/config`.")

        updated = replace(
            configuration, backup=replace(configuration.backup, include_in_automatic_backups=False)
        )
        self._guild_configuration_repository.save(updated)
        return ConfigUpdateResult(True, "Backup defaults updated: Automatic Backups: Disabled.", updated)
