from __future__ import annotations

import asyncio
import io
import logging
import os
import platform
import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, List, Optional, Tuple

import discord
from discord.ext import commands
from dotenv import load_dotenv

from watch_party_manager.domain.vote import (
    DEFAULT_VOTE_CANDIDATE_COUNT,
    DEFAULT_VOTE_DURATION_MINUTES,
    MAX_VOTE_CHANGES,
    MAX_VOTE_CANDIDATE_COUNT,
    MAX_VOTE_DURATION_MINUTES,
    MIN_VOTE_CANDIDATE_COUNT,
    MIN_VOTE_DURATION_MINUTES,
    VoteRecord,
    VoteRound,
    VoteRoundStatus,
    VoteVisibility,
)
from watch_party_manager.domain.guild_configuration import (
    JOIN_MODE_DISPLAY_LABELS,
    EligiblePoolWarningDestination,
    GuildConfiguration,
    GuildVoteVisibility,
    JoinMode,
    VISIBILITY_HELP_TEXT_SHORT,
)
from watch_party_manager.domain.setup_wizard import (
    SETUP_WIZARD_STEP_ORDER,
    SetupWizardDraft,
    SetupWizardState,
    SetupWizardStep,
)
from watch_party_manager.domain.membership_request import MembershipRequest
from watch_party_manager.domain.suggestion_database import SuggestionDatabase
from watch_party_manager.domain.suggestion_database_configuration import (
    CANDIDATE_SELECTION_DISPLAY_LABELS,
    DEFAULT_CANDIDATE_SELECTION_MODE,
    CandidateSelectionMode,
)
from watch_party_manager.domain.watch_item import MetadataProvider, WatchItem, WatchItemStatus
from watch_party_manager.domain.watch_party import WatchParty, WatchPartyStatus
from watch_party_manager.logger_config import configure_logging
from watch_party_manager.persistence.guild_configuration_repository import (
    GuildConfigurationRepository,
)
from watch_party_manager.persistence.membership_request_repository import MembershipRequestRepository
from watch_party_manager.persistence.setup_wizard_repository import SetupWizardRepository
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.persistence.watch_party_repository import JsonWatchPartyRepository
from watch_party_manager.scheduler import (
    AUTOMATIC_BACKUP_JOB_TYPE,
    AutomaticBackupJobHandler,
    CLOSE_VOTE_JOB_TYPE,
    CloseVoteJobHandler,
    JsonSchedulerRepository,
    SchedulerHost,
    SchedulerService,
    VOTE_REMINDER_JOB_TYPE,
    VoteReminderJobHandler,
    WATCH_PARTY_REMINDER_JOB_TYPE,
    WatchPartyReminderJobHandler,
    cancel_vote_jobs,
    cancel_watch_party_reminder,
    reconcile_automatic_backup_schedule,
    reschedule_vote_jobs,
    reschedule_watch_party_reminder,
    resolve_vote_reminder_settings,
    schedule_vote_jobs,
    schedule_watch_party_reminder,
)
from watch_party_manager.services.about_service import (
    AboutConfiguration,
    AboutHealth,
    AboutRuntime,
    build_about_content,
)
from watch_party_manager.services.backup_service import (
    BACKUP_SCOPE_EXPLANATION,
    BackupError,
    BackupKind,
    BackupService,
    BackupType,
)
from watch_party_manager.services.database_backup_service import (
    DatabaseRestoreMode,
    create_database_backup,
    restore_database_backup,
)
from watch_party_manager.services.import_service import ImportMode, build_import_summary, import_backup
from watch_party_manager.services.reset_service import (
    build_database_reset_summary,
    build_factory_reset_summary,
    factory_reset as perform_factory_reset,
    reset_suggestion_database,
)
from watch_party_manager.services.restore_summary_service import RestoreSummary, build_restore_summary
from watch_party_manager.services.config_service import (
    CONFIG_SECTION_ORDER,
    CONFIG_SECTION_TITLES,
    ConfigSection,
    ConfigService,
    ConfigUpdateResult,
)
from watch_party_manager.services.configuration_validation import GuildLookup
from watch_party_manager.services.collection_display import (
    STANDARD_COLLECTION_TYPES,
    format_collection_display,
    used_standard_collection_type_keys,
)
from watch_party_manager.database_admin_view import (
    CollectionManagementMenuView,
    CollectionTypeSelectionView,
    DestinationChoiceView,
    ExistingThreadSelectView,
)
from watch_party_manager.imdb_refresh_view import (
    REFRESH_SCOPE_ALL_COLLECTIONS,
    REFRESH_SCOPE_THIS_COLLECTION,
    ImdbRefreshConfirmationView,
    ImdbRefreshScopeSelectionView,
)
from watch_party_manager.imdb_recovery_view import (
    RECOVERY_SCOPE_ALL_COLLECTIONS,
    RECOVERY_SCOPE_THIS_COLLECTION,
    ImdbRecoveryConfirmationView,
    ImdbRecoveryConfirmMatchView,
    ImdbRecoveryMatchSelectionView,
    ImdbRecoveryNoMatchView,
    ImdbRecoveryScopeSelectionView,
    ImdbRecoverySearchAgainModal,
)
from watch_party_manager.services.imdb_metadata_refresh_service import (
    ImdbMetadataRefreshService,
    ImdbMetadataRefreshSummary,
    RefreshProgress,
    resolve_refreshable_databases,
)
from watch_party_manager.services.imdb_metadata_recovery_service import (
    CandidateSearch,
    CollectionRecoverySummary,
    ImdbMetadataRecoveryService,
    ImdbMetadataRecoverySummary,
    RecoveryResult,
    RecoverySuggestionOutcome,
    resolve_recoverable_databases,
)
from watch_party_manager.services.imdb_metadata_service import ImdbMetadataService, ImdbSearchMatch, ImdbTitleResult
from watch_party_manager.services.discord_timestamp_formatter import (
    format_datetime_for_display,
)
from watch_party_manager.services.duration_formatter import (
    format_duration_minutes,
    format_duration_minutes_compact,
)
from watch_party_manager.services.duration_parser import (
    DURATION_FORMAT_EXAMPLES,
    DURATION_FORMAT_HELP_TEXT,
    DURATION_SYNTAX_HELP,
    VOTE_DURATION_CLARIFICATION,
    parse_duration_to_minutes,
)
from watch_party_manager.services.embed_factory import EmbedFactory
from watch_party_manager.services.help_service import HelpResponse, build_help_response
from watch_party_manager.services.membership_service import (
    JoinOutcomeKind,
    MemberSearchResult,
    MembershipService,
)
from watch_party_manager.services.nominee_selection_service import NomineeSelectionService
from watch_party_manager.services.candidate_selection_strategy import (
    InfinitePoolStrategy,
    build_candidate_selection_strategy,
)
from watch_party_manager.services.nominee_pool_filter import (
    ActorFilter,
    FilteredCandidateSelectionStrategy,
    GenreFilter,
    ImdbRatingFilter,
    MemberSuggestionFilter,
    MpaaRatingFilter,
    NomineePoolFilter,
    apply_nominee_pool_filters,
    genre_eligibility_counts,
    mpaa_rating_eligibility_counts,
    parse_imdb_rating_bounds,
    search_cast_names,
)
from watch_party_manager.services.random_watch_service import choose_random_watch_item
from watch_party_manager.services.member_filter_validation import validate_member_filter_selection
from watch_party_manager.filter_menu_view import (
    FILTER_CATEGORY_ACTOR,
    FILTER_CATEGORY_GENRE,
    FILTER_CATEGORY_IMDB_RATING,
    FILTER_CATEGORY_MEMBER,
    FILTER_CATEGORY_MPAA_RATING,
    ActorEditView,
    ActorMatchEditView,
    ActorSearchModal,
    FilterMenuView,
    GenreEditView,
    ImdbRatingEditView,
    ImdbRatingModal,
    MemberEditView,
    MpaaRatingEditView,
    format_current_filters_block,
)
from watch_party_manager.services.collection_eligibility_service import (
    CollectionEligibility,
    CollectionEligibilityService,
)
from watch_party_manager.services.eligible_pool_warning_service import (
    EligiblePoolWarningService,
    resolve_eligible_pool_warning_threshold,
)
from watch_party_manager.persistence.eligible_pool_warning_state_repository import (
    JsonEligiblePoolWarningStateRepository,
)
from watch_party_manager.services.permission_service import PermissionService, resolve_permission_service
from watch_party_manager.services.setup_wizard_service import (
    BACKUP_INTERVAL_DAYS_EXTRA_FIELD,
    BACKUP_RETENTION_COUNT_EXTRA_FIELD,
    MAX_BACKUP_INTERVAL_DAYS,
    MAX_BACKUP_RETENTION_COUNT,
    MIN_BACKUP_INTERVAL_DAYS,
    MIN_BACKUP_RETENTION_COUNT,
    SetupWizardService,
)
from watch_party_manager.services.discord_ui_limits import (
    SELECT_MAX_OPTIONS,
    build_safe_select_option,
    cap_select_options,
    find_oversized_view_component_fields,
)
from watch_party_manager.services.duplicate_detection_service import (
    DuplicateCheckResult,
    DuplicateMatch,
    DuplicateMatchCategory,
    find_duplicates,
)
from watch_party_manager.services.suggestion_input_service import SuggestionInputService
from watch_party_manager.services.suggestion_service import (
    DEFAULT_REJECTION_THRESHOLD,
    MAX_REJECTION_THRESHOLD,
    MIN_REJECTION_THRESHOLD,
    DatabaseResolution,
    SuggestionService,
)
from watch_party_manager.services.suggestion_repair_service import SuggestionRepairService
from watch_party_manager.services.suggestion_display_status import (
    SUGGESTION_DISPLAY_STATUS_EMOJI,
    SuggestionDisplayStatus,
    compute_display_status,
    display_status_label,
    format_display_status_with_won_date,
    resolve_display_status,
    vote_winner_won_date_line,
)
from watch_party_manager.services.statistics_service import (
    DatabaseStatistics,
    MemberStatistics,
    ServerStatistics,
    StatisticsService,
    StatisticsSnapshot,
    SuggestionStatistics,
)
from watch_party_manager.services.vote_announcement_formatter import (
    build_active_filter_lines,
    build_suggestion_link,
    build_vote_cancellation_notice,
    build_vote_deadline_change_notice,
    build_vote_link,
    build_vote_round_line,
    format_standings_lines,
    format_vote_title,
    resolve_vote_collection_name,
)
from watch_party_manager.services.title_formatter import format_title_with_year
from watch_party_manager.services.vote_completion_announcer import finalize_vote_completion
from watch_party_manager.services.vote_completion_service import (
    VoteCompletionResult,
    VoteCompletionService,
)
from watch_party_manager.services.vote_service import StandingsEntry, VoteService
from watch_party_manager.services.watch_party_service import WatchPartyService
from watch_party_manager.edit_suggestion_view import (
    ChangeStatusSelectView,
    EditSuggestionActionView,
)
from watch_party_manager.edit_vote_view import (
    CustomDurationModal,
    CustomVoteEndTimeModal,
    DurationDeltaChoiceView,
    EditVoteConfirmationView,
    EditVoteManagementView,
    VoteEndTimeMenuView,
)
from watch_party_manager.config_view import (
    DATABASE_SETTING_SUGGESTION_DESTINATION,
    DATABASE_SETTING_WATCH_DESTINATION,
    BackToMenuOnlyView,
    ConfigAdminChannelSectionView,
    ConfigBackupDefaultsChoiceView,
    ConfigDatabaseCandidateSelectionView,
    ConfigDatabaseSectionView,
    ConfigDatabaseSettingsMenuView,
    ConfigEligiblePoolWarningView,
    ConfigHomeChannelSectionView,
    ConfigJoinModeSectionView,
    ConfigMainMenuView,
    ConfigModalRetryView,
    ConfigReminderDefaultsChoiceView,
    ConfigRejectionSettingsView,
    ConfigRejectionThresholdModal,
    ConfigRoleSectionView,
    ConfigSuggestionDestinationSectionView,
    ConfigVotingDefaultsIntroView,
    ConfigWatchDestinationSectionView,
    EligiblePoolWarningThresholdModal,
    OnBackToMenu,
)
from watch_party_manager.import_view import ImportModeChoiceView
from watch_party_manager.membership_view import MembershipApprovalView, PendingRequestSelectView
from watch_party_manager.pagination_view import PaginatedListView, paginate_lines
from watch_party_manager.restore_confirmation_view import RestoreConfirmationView
from watch_party_manager.suggestion_selection_view import (
    DatabaseAdminSelectView,
    ListDatabaseSelectView,
    RemovalMatchSelectView,
    SwitchCollectionButton,
)
from watch_party_manager.type_to_confirm_view import DestructiveConfirmationView
from watch_party_manager.watch_party_selection_view import WatchPartySelectView
from watch_party_manager.setup_wizard_view import (
    AdminChannelNameModal,
    AdminChannelStepView,
    BackupDefaultsChoiceView,
    BackupDefaultsModal,
    CollectionTypeChoiceView,
    CreateDatabaseNameModal,
    CreateThreadNameModal,
    ExistingChannelSelectView,
    ExistingDatabaseSelectView,
    HomeChannelNameModal,
    HomeChannelStepView,
    ImportExistingDatabaseNoticeView,
    ModalStepIntroView,
    RejectionSettingsChoiceView,
    RejectionThresholdModal,
    ReminderDefaultsChoiceView,
    ReminderDefaultsModal,
    ReviewStepView,
    SetupPreparationView,
    SetupProgressSavedView,
    SetupWizardResumeView,
    SuggestionDatabaseChoiceView,
    VotingDefaultsIntroView,
    VotingDefaultsModal,
    WashCrewRoleStepView,
    WatchDestinationStepView,
    WatchPartyRoleStepView,
)
from watch_party_manager.start_vote_view import (
    CustomizeVoteModal,
    CustomizeVoteOverridesView,
    InsufficientFilteredPoolView,
    StartVoteChoiceView,
)
from watch_party_manager.random_watch_view import (
    RandomWatchInitialView,
    RandomWatchResultView,
)
from watch_party_manager.browse_view import (
    BrowseBackButton,
    BrowsePublicResultsView,
    ChangeCollectionButton as BrowseChangeCollectionButton,
    ChangeFiltersButton as BrowseChangeFiltersButton,
    PostPubliclyButton,
    RandomPickButton,
    StartVoteButton,
)
from watch_party_manager.services.browse_service import build_browse_entry_line, revalidate_browse_filter_state
from watch_party_manager.crew_review_view import CrewReviewView
from watch_party_manager.suggestion_view import (
    RejectionConfirmationView,
    SuggestionView,
    WatchedDateModal,
    build_reject_button_custom_id,
    build_watched_button_custom_id,
)
from watch_party_manager.version import __build__, __version__
from watch_party_manager.voting_view import VotingView

logger = logging.getLogger(__name__)


class WatchPartyBot(commands.Bot):
    """A minimal Discord bot for the initial vertical slice."""

    def __init__(
        self,
        *,
        token: Optional[str] = None,
        guild_id: Optional[int] = None,
        wash_crew_role_id: Optional[int] = None,
        watch_party_member_role_id: Optional[int] = None,
        default_nominee_count: int = DEFAULT_VOTE_CANDIDATE_COUNT,
    ) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.token = token
        self.guild_id = guild_id
        self.wash_crew_role_id = wash_crew_role_id
        self.watch_party_member_role_id = watch_party_member_role_id
        self.permission_service = PermissionService(
            watch_party_member_role_id=watch_party_member_role_id,
            wash_crew_role_id=wash_crew_role_id,
        )
        self.default_nominee_count = default_nominee_count
        self.started_at = datetime.now(timezone.utc)
        self.suggestion_service = SuggestionService()
        self.suggestion_input_service = SuggestionInputService()
        self.suggestion_repair_service = SuggestionRepairService(
            self.suggestion_service, self.suggestion_input_service
        )
        self.vote_service = VoteService(self.suggestion_service)
        self.nominee_selection_service = NomineeSelectionService(self.suggestion_service, self.vote_service)
        self.vote_completion_service = VoteCompletionService(self.vote_service, self.suggestion_service)
        self.watch_party_service = WatchPartyService(self.suggestion_service)
        self.statistics_service = StatisticsService(
            self.suggestion_service,
            watch_party_source=self.watch_party_service,
        )
        self.suggestion_database_configuration_repository = SuggestionDatabaseConfigurationRepository()
        # FR-032B: separate repository instances from suggestion_service's
        # own internal ones, pointed at the same default files. Both
        # repositories always read/write straight through to disk with no
        # caching of their own (see their module docstrings), so two
        # instances sharing a path is safe -- this just avoids reaching
        # into SuggestionService's private state for database backup/restore.
        self.suggestion_database_repository = JsonSuggestionDatabaseRepository()
        self.suggestion_repository = JsonSuggestionRepository()
        # FR-032C: same pattern, extended to every other guild-scoped store
        # factory reset and import need direct access to.
        self.vote_repository = JsonVoteRepository()
        self.watch_party_repository = JsonWatchPartyRepository()
        self.scheduler_repository = JsonSchedulerRepository(Path("data") / "scheduled_jobs.json")
        self.backup_service = BackupService()
        self.interactive_voting_restored = 0
        self.suggestion_views_restored = 0
        self.scheduler_host = SchedulerHost.from_json_file(
            Path("data") / "scheduled_jobs.json"
        )
        self.scheduler_host.scheduler_service.register_handler(
            CLOSE_VOTE_JOB_TYPE,
            CloseVoteJobHandler(
                self.vote_completion_service,
                self.vote_service,
                self.suggestion_service,
                self,
                on_finalized=lambda result: sync_vote_completion_status_embeds(self, result),
            ),
        )
        self.scheduler_host.scheduler_service.register_handler(
            VOTE_REMINDER_JOB_TYPE, VoteReminderJobHandler(self.vote_service, self.suggestion_service, self)
        )
        self.scheduler_host.scheduler_service.register_handler(
            WATCH_PARTY_REMINDER_JOB_TYPE,
            WatchPartyReminderJobHandler(self.watch_party_service, self.suggestion_service, self),
        )
        self.guild_configuration_repository = GuildConfigurationRepository()
        self.scheduler_host.scheduler_service.register_handler(
            AUTOMATIC_BACKUP_JOB_TYPE,
            AutomaticBackupJobHandler(
                self.backup_service,
                self.guild_configuration_repository,
                self.scheduler_host.scheduler_service,
            ),
        )
        self.setup_wizard_repository = SetupWizardRepository()
        self.setup_wizard_service = SetupWizardService(
            self.setup_wizard_repository,
            self.guild_configuration_repository,
            self.suggestion_service,
            self.suggestion_database_configuration_repository,
        )
        self.config_service = ConfigService(
            self.guild_configuration_repository,
            self.suggestion_service,
            self.suggestion_database_configuration_repository,
        )
        self.collection_eligibility_service = CollectionEligibilityService(
            self.suggestion_service, self.vote_service
        )
        self.eligible_pool_warning_state_repository = JsonEligiblePoolWarningStateRepository()
        self.eligible_pool_warning_service = EligiblePoolWarningService(
            self.collection_eligibility_service,
            self.eligible_pool_warning_state_repository,
            self.guild_configuration_repository,
            self.suggestion_database_configuration_repository,
            self.suggestion_service,
        )
        self.membership_request_repository = MembershipRequestRepository()
        self.membership_service = MembershipService(
            self.guild_configuration_repository,
            self.membership_request_repository,
        )
        self.membership_views_restored = 0

    def apply_role_configuration(
        self, wash_crew_role_id: Optional[int], watch_party_member_role_id: Optional[int]
    ) -> None:
        """Apply newly configured WASH Crew / Watch Party role IDs immediately.

        Used both at startup (falling back to a persisted GuildConfiguration
        when the environment variables are unset) and right after /setup
        completes, so administrative commands become available without a
        bot restart -- PermissionService stores these as plain instance
        attributes, so updating it here takes effect on the very next
        command invocation.
        """
        self.wash_crew_role_id = wash_crew_role_id
        self.watch_party_member_role_id = watch_party_member_role_id
        self.permission_service.wash_crew_role_id = wash_crew_role_id
        self.permission_service.watch_party_member_role_id = watch_party_member_role_id

    async def setup_hook(self) -> None:
        @self.tree.command(name="about", description="Show information about WASH.")
        async def about(interaction: discord.Interaction) -> None:
            await handle_about(interaction, self)

        @self.tree.command(name="help", description="Show WASH's command reference.")
        async def help_command(interaction: discord.Interaction) -> None:
            # Multi-Guild Isolation, Phase 3b: guild-scoped resolution, not
            # the shared singleton -- this guild's own configured roles,
            # never another guild's.
            permission_service = resolve_permission_service(
                interaction.guild_id, self.guild_configuration_repository
            )
            show_wash_crew = permission_service.is_wash_crew(interaction.user)
            show_watch_party_member = permission_service.is_watch_party_member(interaction.user)
            response = build_help_response(
                show_wash_crew=show_wash_crew, show_watch_party_member=show_watch_party_member
            )
            await send_help_response(interaction, response)

        @self.tree.command(name="stats", description="Show server, member, suggestion, or collection statistics.")
        @discord.app_commands.describe(
            type="Which statistics to show (defaults to Server).",
            public="Post the statistics publicly instead of showing them only to you (WASH Crew only, except for your own Member statistics).",
            suggestion="Required when type is Suggestion: a reference number or exact title.",
        )
        @discord.app_commands.choices(
            type=[
                discord.app_commands.Choice(name="Server", value="server"),
                discord.app_commands.Choice(name="Member", value="member"),
                discord.app_commands.Choice(name="Suggestion", value="suggestion"),
                discord.app_commands.Choice(name="Collection", value="database"),
            ]
        )
        async def stats(
            interaction: discord.Interaction,
            type: str = "server",
            public: bool = False,
            suggestion: Optional[str] = None,
        ) -> None:
            await handle_stats(interaction, self, type, public, suggestion)
            logger.info(
                "User %s requested %s statistics in guild %s",
                interaction.user.id,
                type,
                interaction.guild_id,
            )

        @self.tree.command(name="add", description="Suggest a watch item by title or IMDb URL.")
        @discord.app_commands.describe(
            title="The watch item's title, or an IMDb link to it.",
            imdb_url="The watch item's IMDb link, if not already given in the title.",
            release_year="The watch item's release year, if known (helps duplicate detection)."
        )
        async def suggest(
            interaction: discord.Interaction,
            title: str,
            imdb_url: Optional[str] = None,
            release_year: Optional[int] = None,
        ) -> None:
            await handle_add_suggestion(interaction, self, title, imdb_url, release_year)

        @self.tree.command(name="list", description="List watch items and their status.")
        @discord.app_commands.describe(
            status="Which watch items to show (defaults to Active Watch Items).",
            public="Post the list publicly instead of showing it only to you (WASH Crew only).",
        )
        @discord.app_commands.choices(
            status=[
                discord.app_commands.Choice(
                    name="Active Watch Items (Eligible + In an Active Vote)", value="active"
                ),
                discord.app_commands.Choice(name="Eligible for Voting", value="eligible"),
                discord.app_commands.Choice(name="In an Active Vote", value="in_active_vote"),
                discord.app_commands.Choice(name="Pending Crew Review", value="pending_crew_review"),
                discord.app_commands.Choice(name="Vote Winners", value="vote_winner"),
                discord.app_commands.Choice(name="Retired", value="retired"),
                discord.app_commands.Choice(name="Watched", value="watched"),
                discord.app_commands.Choice(name="All Watch Items", value="all"),
            ]
        )
        async def suggestions(
            interaction: discord.Interaction,
            status: str = "active",
            public: bool = False,
        ) -> None:
            await handle_list_suggestions(interaction, self, status, public)

        @self.tree.command(name="browse", description="Browse a collection with filters, paginated.")
        async def browse(interaction: discord.Interaction) -> None:
            await handle_browse(interaction, self)

        @self.tree.command(name="reject", description="Indicate you won't watch a suggestion.")
        @discord.app_commands.describe(suggestion_id="The suggestion's numeric ID (shown on its public post).")
        async def reject(interaction: discord.Interaction, suggestion_id: int) -> None:
            await handle_reject_suggestion(interaction, self, suggestion_id)

        @self.tree.command(name="unreject", description="Undo your rejection of a suggestion.")
        @discord.app_commands.describe(suggestion_id="The suggestion's numeric ID (shown on its public post).")
        async def unreject(interaction: discord.Interaction, suggestion_id: int) -> None:
            await handle_unreject_suggestion(interaction, self, suggestion_id)

        @self.tree.command(name="setup", description="Run WASH's first-time setup wizard (WASH Crew only).")
        async def setup(interaction: discord.Interaction) -> None:
            guild_configuration = (
                self.guild_configuration_repository.get(interaction.guild_id)
                if interaction.guild_id is not None
                else None
            )
            is_guild_owner = interaction.guild is not None and interaction.user.id == interaction.guild.owner_id
            message, blocked = perform_setup_permission_check(
                interaction.user,
                guild_configuration=guild_configuration,
                is_guild_owner=is_guild_owner,
            )
            if blocked:
                await interaction.response.send_message(message, ephemeral=True)
                return

            guild_id = interaction.guild_id
            if guild_id is None:
                await interaction.response.send_message("Setup can only be run inside a server.", ephemeral=True)
                return

            redirect_message = perform_setup_redirect_check(guild_configuration)
            if redirect_message is not None:
                await interaction.response.send_message(redirect_message, ephemeral=True)
                return

            requester_id = interaction.user.id
            state, resumed = self.setup_wizard_service.start_or_resume(guild_id)

            if resumed:
                async def on_continue(resume_interaction: discord.Interaction) -> None:
                    await send_setup_wizard_step(resume_interaction, self, state, edit=True, requester_id=requester_id)

                async def on_review(resume_interaction: discord.Interaction) -> None:
                    reviewed = self.setup_wizard_service.go_to_step(state, SetupWizardStep.REVIEW)
                    await send_setup_wizard_step(resume_interaction, self, reviewed, edit=True, requester_id=requester_id)

                async def on_restart(resume_interaction: discord.Interaction) -> None:
                    restarted = self.setup_wizard_service.restart(guild_id)
                    await send_setup_wizard_step(resume_interaction, self, restarted, edit=True, requester_id=requester_id)

                view = SetupWizardResumeView(on_continue, on_review, on_restart, requester_id=requester_id)
                total_steps = len(SETUP_WIZARD_STEP_ORDER)
                completed_count = len(state.completed_steps)
                await interaction.response.send_message(
                    "**Setup already in progress for this server.**\n\n"
                    f"{completed_count} of {total_steps} steps completed so far "
                    f"(currently on: {SETUP_WIZARD_STEP_TITLES[state.current_step]}). What would you like to do?",
                    view=view,
                    ephemeral=True,
                )
                return

            await send_setup_preparation_screen(interaction, self, state, requester_id=requester_id)

        @self.tree.command(name="config", description="Change WASH's configuration (WASH Crew only).")
        async def config(interaction: discord.Interaction) -> None:
            permission = resolve_permission_service(
                interaction.guild_id, self.guild_configuration_repository
            ).require_wash_crew(interaction.user)
            if not permission.allowed:
                await interaction.response.send_message(permission.message, ephemeral=True)
                return

            guild_id = interaction.guild_id
            if guild_id is None:
                await interaction.response.send_message("Config can only be used inside a server.", ephemeral=True)
                return

            configuration = self.guild_configuration_repository.get(guild_id)
            if configuration is None or not configuration.setup_completed:
                await interaction.response.send_message(
                    "Initial setup hasn't been completed yet. Run `/setup` first.", ephemeral=True
                )
                return

            await send_config_main_menu(interaction, self, guild_id, edit=False)

        self.tree.add_command(MembershipGroup(self))
        self.tree.add_command(DatabaseGroup(self))
        self.tree.add_command(VotingGroup(self))
        self.tree.add_command(RandomGroup(self))
        self.tree.add_command(JoinGroup(self))
        self.tree.add_command(MaintenanceGroup(self))
        self.tree.add_command(SuggestionGroup(self))
        # v1 Final Polish: /watch-party schedule/reschedule/cancel/status
        # are intentionally not registered for v1 -- the scheduled watch
        # party workflow is being held back from this release, not
        # removed. WatchPartyEventGroup itself, its underlying services,
        # and its persisted data are left fully intact (see the class
        # definition below) so this can be re-enabled later by restoring
        # this one line; do not delete any of that in the meantime.

        # Environment variables remain the primary way to configure the
        # WASH Crew / Watch Party roles (unchanged, backward compatible),
        # but whichever of the two is left unset there falls back to a
        # persisted GuildConfiguration -- e.g. one saved by /setup -- so a
        # server that has completed setup doesn't also need the env vars.
        if self.guild_id and (self.wash_crew_role_id is None or self.watch_party_member_role_id is None):
            guild_configuration = self.guild_configuration_repository.get(self.guild_id)
            resolved_wash_crew_role_id, resolved_watch_party_member_role_id = resolve_startup_role_ids(
                self.wash_crew_role_id, self.watch_party_member_role_id, guild_configuration
            )
            self.apply_role_configuration(resolved_wash_crew_role_id, resolved_watch_party_member_role_id)

        # Complete any round that expired while WASH was offline before
        # attempting to restore its interactive voting controls. This is
        # the same due-job check scheduler_host.start() below runs every
        # poll_interval_seconds; running it once synchronously here first
        # guarantees it has already closed an overdue round before
        # restore_persistent_voting_views() reads current open-round state,
        # rather than racing scheduler_host.start()'s background task for
        # its first turn on the event loop.
        try:
            await self.scheduler_host.scheduler_service.run_once()
        except Exception:
            logger.exception("Error while checking for due scheduled jobs during startup")

        await reconcile_all_automatic_backup_schedules(self)

        self.interactive_voting_restored = restore_persistent_voting_views(
            bot=self,
            vote_service=self.vote_service,
            suggestion_service=self.suggestion_service,
            guild_configuration_repository=self.guild_configuration_repository,
        )

        self.suggestion_views_restored = await restore_persistent_suggestion_views(
            bot=self,
            suggestion_service=self.suggestion_service,
            suggestion_database_configuration_repository=self.suggestion_database_configuration_repository,
            guild_configuration_repository=self.guild_configuration_repository,
        )

        self.membership_views_restored = restore_persistent_membership_approval_views(
            self, self.membership_service
        )

        self.crew_review_views_restored = await restore_persistent_crew_review_views(
            self, self.suggestion_service
        )

        if self.guild_id:
            logger.info(f"Synchronizing slash commands to development guild {self.guild_id}...")
            guild = discord.Object(id=self.guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info(f"Synchronized {len(synced)} command(s) to guild {self.guild_id}")
        else:
            logger.info("Synchronizing slash commands globally...")
            synced = await self.tree.sync()
            logger.info(f"Synchronized {len(synced)} command(s) globally")

        await self.scheduler_host.start()

    async def close(self) -> None:
        await self.scheduler_host.stop()
        await super().close()

    async def on_ready(self) -> None:
        logger.info(f"Logged in as {self.user}")
        snapshot = self.statistics_service.snapshot()
        logger.info(
            "Startup summary: %s database(s) (%s active), %s watch item(s), "
            "%s active suggestion(s), %s open voting round(s), %s interactive voting view(s) restored, "
            "%s suggestion view(s) restored",
            snapshot.total_databases,
            snapshot.active_databases,
            snapshot.total_watch_items,
            snapshot.active_suggestions,
            snapshot.open_vote_rounds,
            self.interactive_voting_restored,
            self.suggestion_views_restored,
        )
        logger.info("Nominee selector initialized")
        logger.info("Ready")

    async def start_bot(self) -> None:
        if not self.token:
            logger.error("DISCORD_TOKEN environment variable is required. Please set it in .env or your environment.")
            raise RuntimeError("DISCORD_TOKEN environment variable is required")
        try:
            logger.info("Starting WASH...")
            await super().start(self.token)
        except discord.errors.LoginFailure:
            logger.error("Failed to login. Invalid DISCORD_TOKEN or bot token has been revoked.")
            raise


def resolve_startup_role_ids(
    env_wash_crew_role_id: Optional[int],
    env_watch_party_member_role_id: Optional[int],
    guild_configuration: Optional[GuildConfiguration],
) -> tuple[Optional[int], Optional[int]]:
    """Resolve the effective WASH Crew / Watch Party role IDs at startup.

    Environment variables remain the primary configuration mechanism
    (unchanged, backward compatible); a persisted GuildConfiguration --
    e.g. one saved by /setup (FR-028) -- fills in whichever of the two
    roles the environment left unconfigured. Neither role's fail-closed
    behavior changes: if both sources leave a role unset, it stays None.

    Args:
        env_wash_crew_role_id: The WASH_CREW_ROLE_ID env var, already parsed.
        env_watch_party_member_role_id: The WATCH_PARTY_MEMBER_ROLE_ID env var, already parsed.
        guild_configuration: The persisted GuildConfiguration for the
            startup guild, or None if none has been saved yet.

    Returns:
        (wash_crew_role_id, watch_party_member_role_id).
    """
    wash_crew_role_id = env_wash_crew_role_id
    if wash_crew_role_id is None and guild_configuration is not None:
        wash_crew_role_id = guild_configuration.wash_crew_role_id

    watch_party_member_role_id = env_watch_party_member_role_id
    if watch_party_member_role_id is None and guild_configuration is not None:
        watch_party_member_role_id = guild_configuration.watch_party_role.role_id

    return wash_crew_role_id, watch_party_member_role_id


def parse_guild_id(guild_id_str: Optional[str]) -> Optional[int]:
    """Parse and validate a guild ID from an environment variable.
    
    Args:
        guild_id_str: The guild ID as a string from the environment.
    
    Returns:
        The guild ID as an integer, or None if not provided.
    
    Raises:
        ValueError: If the guild ID is provided but not a valid integer.
    """
    if not guild_id_str:
        return None
    
    try:
        guild_id = int(guild_id_str)
        if guild_id <= 0:
            raise ValueError(f"Guild ID must be a positive integer, got {guild_id}")
        return guild_id
    except ValueError as e:
        if "invalid literal" in str(e).lower():
            raise ValueError(f"DISCORD_GUILD_ID must be a valid integer, got '{guild_id_str}'")
        raise


def parse_wash_crew_role_id(role_id_str: Optional[str]) -> Optional[int]:
    """Parse and validate the WASH Crew role ID from an environment variable.

    The WASH Crew role gates commands like /start_vote. It's read from
    configuration rather than hardcoded so it can be set per-server.

    Args:
        role_id_str: The role ID as a string from the environment.

    Returns:
        The role ID as an integer, or None if not configured. When not
        configured, WASH Crew-only commands fail closed: nobody can use
        them until a role is set.

    Raises:
        ValueError: If the role ID is provided but not a valid positive
            integer.
    """
    if not role_id_str:
        return None

    try:
        role_id = int(role_id_str)
        if role_id <= 0:
            raise ValueError(f"Role ID must be a positive integer, got {role_id}")
        return role_id
    except ValueError as e:
        if "invalid literal" in str(e).lower():
            raise ValueError(f"WASH_CREW_ROLE_ID must be a valid integer, got '{role_id_str}'")
        raise


def parse_watch_party_member_role_id(role_id_str: Optional[str]) -> Optional[int]:
    """Parse and validate WATCH_PARTY_MEMBER_ROLE_ID."""
    if not role_id_str:
        return None
    try:
        role_id = int(role_id_str)
        if role_id <= 0:
            raise ValueError(f"Role ID must be a positive integer, got {role_id}")
        return role_id
    except ValueError as exc:
        if "invalid literal" in str(exc).lower():
            raise ValueError(
                "WATCH_PARTY_MEMBER_ROLE_ID must be a valid integer, "
                f"got '{role_id_str}'"
            )
        raise


def is_wash_crew_member(user: object, wash_crew_role_id: Optional[int]) -> bool:
    """Check whether a member has the configured WASH Crew role.

    Fails closed: if no WASH Crew role is configured, this returns False.
    Callers that need to tell "not configured" apart from "configured but
    this user lacks the role" (to give a clearer error message) should
    check `wash_crew_role_id is None` themselves before calling this.

    Args:
        user: The Discord member to check (or anything with a `.roles`
            attribute of objects that have an `.id`, which keeps this
            testable without real Discord objects).
        wash_crew_role_id: The configured WASH Crew role ID, or None if
            none is configured.

    Returns:
        True only if wash_crew_role_id is set and the user has a role
        with that ID.
    """
    if wash_crew_role_id is None:
        return False
    roles = getattr(user, "roles", [])
    return any(getattr(role, "id", None) == wash_crew_role_id for role in roles)


def parse_vote_visibility(
    value: Optional[str], default: GuildVoteVisibility = GuildVoteVisibility.VISIBLE
) -> VoteVisibility:
    """Parse a /start_vote visibility option into a VoteVisibility.

    This is the sole place visibility text gets validated. Both the
    default and customized /start_vote paths rely on this helper.

    Args:
        value: The raw text entered for the visibility option, expected to
            be "blind" or "visible" (case-insensitive, whitespace ignored).
            None or blank means no explicit override was supplied --
            "use default settings", or a blank visibility field while
            customizing -- so the caller's resolved guild default is used
            instead (Release Polish Batch 2, Priority 6: starting a vote
            without an explicit override must use the configured guild
            default, not a hardcoded value).
        default: The guild's configured default visibility (see
            VotingDefaultsConfig.visibility), used only when value is
            None or blank. Defaults to VISIBLE, matching that same
            field's own documented default for callers with no guild
            configuration to resolve.

    Returns:
        The matching VoteVisibility.

    Raises:
        ValueError: If value is non-blank but isn't "blind" or "visible".
    """
    if value is None or not value.strip():
        return VoteVisibility(default.value)
    normalized = value.strip().lower()
    try:
        return VoteVisibility(normalized)
    except ValueError:
        raise ValueError("Visibility must be 'blind' or 'visible'.")


def parse_vote_duration_minutes(duration_minutes: Optional[int], default: int = DEFAULT_VOTE_DURATION_MINUTES) -> int:
    """Validate and resolve a /start_vote duration option, in minutes.

    Args:
        duration_minutes: The already-parsed duration in minutes, or None
            to use the default.
        default: The guild's configured default duration in minutes,
            resolved by the caller from VotingDefaultsConfig.duration_minutes
            when available (see perform_start_vote). Defaults to
            DEFAULT_VOTE_DURATION_MINUTES for callers with no guild
            configuration to resolve.

    Returns:
        default if duration_minutes is None, otherwise duration_minutes
        itself once validated.

    Raises:
        ValueError: If duration_minutes is outside
            [MIN_VOTE_DURATION_MINUTES, MAX_VOTE_DURATION_MINUTES].
    """
    if duration_minutes is None:
        return default

    if not (MIN_VOTE_DURATION_MINUTES <= duration_minutes <= MAX_VOTE_DURATION_MINUTES):
        raise ValueError(
            f"Duration must be between {format_duration_minutes(MIN_VOTE_DURATION_MINUTES)} and "
            f"{format_duration_minutes(MAX_VOTE_DURATION_MINUTES)}."
        )

    return duration_minutes


def build_customize_vote_modal_defaults(
    *,
    default_nominee_count: int,
    guild_id: Optional[int],
    guild_configuration_repository: Optional[GuildConfigurationRepository],
) -> dict[str, str]:
    """Resolve the actual configured defaults CustomizeVoteModal's "leave
    blank" placeholders should name, mirroring perform_start_vote's own
    resolution (default_duration_minutes) and resolve_vote_reminder_settings
    exactly, so what's displayed here can never drift from what starting a
    vote would actually apply. Visibility is resolved separately (see
    resolve_customize_vote_default_visibility) -- General Fixed-Option
    Control Audit moved it out of this modal entirely, onto its own
    dropdown on CustomizeVoteOverridesView.

    Returns a dict of the 4 default_*_display kwargs CustomizeVoteModal
    accepts, ready to unpack straight into its constructor.
    """
    default_duration_minutes = DEFAULT_VOTE_DURATION_MINUTES
    if guild_configuration_repository is not None and guild_id is not None:
        configuration = guild_configuration_repository.get(guild_id)
        if configuration is not None:
            default_duration_minutes = configuration.voting_defaults.duration_minutes

    # guild_id is only ever used as a lookup key inside
    # resolve_vote_reminder_settings; passing 0 when it's unknown is safe
    # (guild_configuration_repository.get(0) simply finds nothing, same
    # as guild_configuration_repository being None) and keeps this
    # resolution identical to what starting the vote will actually use.
    reminder_enabled, reminder_minutes = resolve_vote_reminder_settings(
        guild_configuration_repository, guild_id or 0
    )

    return {
        "default_nominee_count_display": str(default_nominee_count),
        "default_duration_display": format_duration_minutes(default_duration_minutes),
        "default_reminder_enabled_display": "Yes" if reminder_enabled else "No",
        "default_reminder_minutes_display": format_duration_minutes(reminder_minutes),
    }


def resolve_customize_vote_default_visibility(
    guild_id: Optional[int], guild_configuration_repository: Optional[GuildConfigurationRepository]
) -> GuildVoteVisibility:
    """The value Customize This Vote's visibility dropdown
    (CustomizeVoteOverridesView) should preselect: the guild's own
    configured Voting Defaults visibility -- the exact same default
    perform_start_vote itself falls back to when no override is given
    (see parse_vote_visibility). Purely a convenience preselection, never
    a decision acted on here.
    """
    if guild_configuration_repository is not None and guild_id is not None:
        configuration = guild_configuration_repository.get(guild_id)
        if configuration is not None:
            return configuration.voting_defaults.visibility
    return GuildVoteVisibility.VISIBLE


def parse_duration_text_to_minutes(text: str) -> int:
    """Parse free-text voting-duration input into minutes.

    Standardized on WASH's one shared duration syntax (Requirement 3):
    a whole number immediately followed by a unit -- m/minutes, h/hours,
    or d/days (w/week/weeks is also accepted but never advertised --
    Duration UX Standard; see services.duration_parser). A vote's duration
    supports the same minute precision as reminder-before-close and
    /edit_vote's Shorten/Extend Vote (Release Candidate Polish: Vote
    Duration) -- "10m" and "30m" are valid, not just whole-hour amounts.
    Does not perform range validation -- see
    parse_vote_duration_minutes/parse_setup_voting_duration_minutes for
    that, applied by each caller with its own error wording.

    Args:
        text: The raw duration text (already known to be non-blank).

    Returns:
        The equivalent number of minutes.

    Raises:
        ValueError: If text isn't valid duration syntax.
    """
    return parse_duration_to_minutes(text)


# Bounds for a per-round reminder-before-close override, matching
# VoteNotificationsConfig.reminder_minutes_before_close's own guild-level
# bounds (domain/guild_configuration.py) so a round can never be
# configured with a value the guild-level setting itself would reject.
MIN_VOTE_REMINDER_MINUTES_BEFORE_CLOSE = 1
MAX_VOTE_REMINDER_MINUTES_BEFORE_CLOSE = 720 * 60


def parse_vote_reminder_minutes_before_close(minutes: Optional[int]) -> Optional[int]:
    """Validate a /start_vote reminder-before-close override, in minutes.

    Args:
        minutes: The raw reminder minutes value, or None to use the
            guild's configured default (see
            scheduler.vote_scheduling.resolve_vote_reminder_settings).

    Returns:
        None if minutes is None (use the guild default), otherwise
        minutes itself once validated.

    Raises:
        ValueError: If minutes is outside
            [MIN_VOTE_REMINDER_MINUTES_BEFORE_CLOSE, MAX_VOTE_REMINDER_MINUTES_BEFORE_CLOSE].
    """
    if minutes is None:
        return None

    if not (MIN_VOTE_REMINDER_MINUTES_BEFORE_CLOSE <= minutes <= MAX_VOTE_REMINDER_MINUTES_BEFORE_CLOSE):
        raise ValueError(
            f"Reminder before close must be between {format_duration_minutes(MIN_VOTE_REMINDER_MINUTES_BEFORE_CLOSE)} "
            f"and {format_duration_minutes(MAX_VOTE_REMINDER_MINUTES_BEFORE_CLOSE)}."
        )

    return minutes


def parse_optional_reminder_minutes_field(value: Optional[str]) -> Optional[int]:
    """Parse an optional reminder-before-close field from a Discord modal
    into minutes, using WASH's one shared duration syntax (Requirement 3).

    Blank means "use the configured default", matching every other
    optional modal field's convention. Range validation remains in
    parse_vote_reminder_minutes_before_close, applied by the caller.
    """
    if value is None or not value.strip():
        return None
    return parse_duration_to_minutes(value)


def parse_vote_nominee_count(value: Optional[int], default: int = DEFAULT_VOTE_CANDIDATE_COUNT) -> int:
    """Validate and resolve the nominee count for /start_vote.

    Args:
        value: The raw nominee_count option, or None to use the default.
        default: The count to use when value is None. Defaults to
            DEFAULT_VOTE_CANDIDATE_COUNT, but callers pass the
            WASH Crew-configured default (see parse_default_nominee_count)
            when one is set.

    Returns:
        The resolved nominee count.

    Raises:
        ValueError: If value is outside [MIN_VOTE_CANDIDATE_COUNT, MAX_VOTE_CANDIDATE_COUNT].
    """
    if value is None:
        return default
    if not (MIN_VOTE_CANDIDATE_COUNT <= value <= MAX_VOTE_CANDIDATE_COUNT):
        raise ValueError(
            f"Candidate count must be between {MIN_VOTE_CANDIDATE_COUNT} and "
            f"{MAX_VOTE_CANDIDATE_COUNT}."
        )
    return value


def parse_default_nominee_count(value: Optional[str]) -> int:
    """Parse and validate the configured default nominee count from an
    environment variable.

    This is the "default settings" nominee count /start_vote falls back to
    when nominee_count isn't explicitly overridden -- WASH Crew configures
    it once here rather than needing to pass it on every /start_vote call.
    A future setup flow can replace reading this from the environment
    without changing how the rest of the system uses it.

    Args:
        value: The configured default as a string from the environment.

    Returns:
        The parsed default, or DEFAULT_VOTE_CANDIDATE_COUNT if not configured.

    Raises:
        ValueError: If provided but not a valid integer in
            [MIN_VOTE_CANDIDATE_COUNT, MAX_VOTE_CANDIDATE_COUNT].
    """
    if not value:
        return DEFAULT_VOTE_CANDIDATE_COUNT

    try:
        count = int(value)
    except ValueError:
        raise ValueError(f"DEFAULT_VOTE_NOMINEE_COUNT must be a valid integer, got '{value}'")

    if not (MIN_VOTE_CANDIDATE_COUNT <= count <= MAX_VOTE_CANDIDATE_COUNT):
        raise ValueError(
            f"DEFAULT_VOTE_NOMINEE_COUNT must be between {MIN_VOTE_CANDIDATE_COUNT} and "
            f"{MAX_VOTE_CANDIDATE_COUNT}, got {count}"
        )

    return count


def format_vote_changes_setting() -> str:
    """Describe how many vote changes a member is allowed.

    This reflects the fixed, project-wide MAX_VOTE_CHANGES constant.
    VoteRound has no per-round toggle for this today, so /start_vote's
    "allow_vote_changes" is reported, not configured, until the domain
    model supports it.
    """
    if MAX_VOTE_CHANGES <= 0:
        return "No"
    change_word = "change" if MAX_VOTE_CHANGES == 1 else "changes"
    return f"Yes (up to {MAX_VOTE_CHANGES} {change_word})"


LOW_SUGGESTION_POOL_THRESHOLD = 10


def build_low_suggestion_pool_warning(candidate_count: int) -> str:
    """Return a reminder when the suggestion pool is running low."""
    if candidate_count >= LOW_SUGGESTION_POOL_THRESHOLD:
        return ""
    return (
        "\n\nThe suggestion pool is getting low. "
        "Add a watch item with `/add` followed by a title or IMDb link."
    )


def build_start_vote_confirmation(
    vote_round: VoteRound, candidate_count: int, duration_minutes: int, pool_count: Optional[int] = None
) -> str:
    """Build the /start_vote confirmation message.

    Args:
        vote_round: The newly created round.
        candidate_count: How many suggestions were available to vote on.
        duration_minutes: The round's resolved duration, in minutes --
            shown in natural language (see format_duration_minutes)
            alongside the absolute end time, which remains
            Discord-timestamp-formatted and unchanged.

    Returns:
        A message with the round ID, visibility, candidate count,
        duration, end time, and vote-change allowance. Never includes
        individual votes.
    """
    return (
        f"Voting round {vote_round.id} is now open.\n"
        f"Visibility: {vote_round.visibility.value.capitalize()}\n"
        f"Candidates: {candidate_count}\n"
        f"Duration: {format_duration_minutes(duration_minutes)}\n"
        f"Voting ends: {format_datetime_for_display(vote_round.closes_at)}\n"
        f"Vote changes allowed: {format_vote_changes_setting()}"
        f"{build_low_suggestion_pool_warning(pool_count if pool_count is not None else candidate_count)}"
    )


def build_customize_vote_summary_text(
    *,
    collection_name: Optional[str],
    candidate_selection_mode: Optional[CandidateSelectionMode],
    visibility: GuildVoteVisibility,
    candidate_count: int,
    duration_minutes: int,
    filter_member_display: Optional[str] = None,
    filter_genre: Optional[str] = None,
    filter_imdb_rating_min: Optional[float] = None,
    filter_imdb_rating_max: Optional[float] = None,
    filter_mpaa_rating: Optional[str] = None,
    filter_actor: Optional[str] = None,
) -> str:
    """Build Custom Vote Summary & Announcement's pre-creation review
    screen, shown after Customize This Vote's modal is submitted and
    before the round is actually created (Filtered-Pool Validation: no
    round, persistent view, or announcement exists yet at this point).

    Summarizes Collection, Nominee Selection, Vote Visibility, candidate
    count, vote duration, and -- only when active -- each filter in the
    shared Genre/IMDb Rating/MPAA Rating/Actor/Member order (Section 2/12).
    An inactive filter ("Any ...") is never shown here, exactly as it
    will be omitted from the eventual announcement/vote status text (see
    services/vote_announcement_formatter.build_active_filter_lines).
    """
    lines = ["**Review This Vote**", ""]
    if collection_name:
        lines.append(f"Collection: {collection_name}")
    if candidate_selection_mode is not None:
        lines.append(f"Nominee Selection: {CANDIDATE_SELECTION_DISPLAY_LABELS[candidate_selection_mode]}")
    lines.append(f"Vote Visibility: {visibility.value.capitalize()}")
    lines.append(f"Candidate count: {candidate_count}")
    lines.append(f"Vote duration: {format_duration_minutes(duration_minutes)}")
    if filter_genre:
        lines.append(f"Genre: {filter_genre}")
    if filter_imdb_rating_min is not None or filter_imdb_rating_max is not None:
        lines.append(
            f"IMDb Rating: {ImdbRatingFilter(minimum=filter_imdb_rating_min, maximum=filter_imdb_rating_max).describe()}"
        )
    if filter_mpaa_rating:
        lines.append(f"MPAA Rating: {filter_mpaa_rating}")
    if filter_actor:
        lines.append(f"Actor: {filter_actor}")
    if filter_member_display:
        lines.append(f"Suggestion Source: {filter_member_display}")
    lines.append("")
    lines.append("Press **Start Vote** to open this round, or **Cancel** to discard these settings.")
    return "\n".join(lines)


def build_vote_status_text(
    vote_round: VoteRound,
    candidate_count: int,
    standings: Optional[List[StandingsEntry]],
    standings_error: Optional[str],
    candidates: Optional[List[WatchItem]] = None,
    guild_round_number: Optional[int] = None,
) -> str:
    """Build the /vote_status message for a round.

    Args:
        vote_round: The round to report on.
        candidate_count: The current number of suggestions.
        standings: Standings entries to display, or None if standings
            shouldn't be shown for this round right now (a still-open
            blind round).
        standings_error: A message to show instead of standings if
            calculating them failed, or None.
        candidates: The round's nominees, used to resolve standings to
            titles rather than internal suggestion numbers (Release
            Polish Batch 2, Priority 3). Optional so existing callers
            with no candidates in scope keep working.

    Returns:
        The formatted status text. Total votes cast is always shown,
        regardless of visibility — only per-suggestion standings are ever
        withheld. Includes a link to the original voting post when the
        round has enough Discord message metadata to build one (see
        build_vote_link); omitted entirely for legacy rounds that don't.
    """
    lines = [
        f"Voting round {guild_round_number if guild_round_number is not None else vote_round.id}",
        f"Status: {vote_round.status.value.capitalize()}",
        f"Visibility: {vote_round.visibility.value.capitalize()}",
        f"Candidates: {candidate_count}",
        f"Votes cast: {len(vote_round.votes)}",
        f"Voting ends: {format_datetime_for_display(vote_round.closes_at)}",
        f"Vote changes allowed: {format_vote_changes_setting()}",
        *build_active_filter_lines(vote_round),
    ]
    link = build_vote_link(vote_round)
    if link:
        lines.append(f"Original post: {link}")
    lines.extend(format_standings_lines(standings, standings_error, candidates))

    return "\n".join(lines)


def build_insufficient_candidates_message(
    collection_display_name: Optional[str],
    eligible_count: int,
    requested_count: int,
) -> str:
    """Build /vote start's "not enough eligible watch items" message.

    UI Polish (Vote Creation Validation): names the collection, the
    actual eligible count, and the requested candidate count -- a WASH
    Crew member seeing 7 available watch items in /list needs to know
    why /vote start disagrees. requested_count is always the same
    resolved candidate count perform_start_vote actually validated
    against and would have passed to nominee selection (never a
    separate, lower minimum) -- see the eligible_count < requested_count
    check this message is built from. collection_display_name is None
    for the no-database-context fallback pool, which has no single
    collection to name.

    Args:
        collection_display_name: The collection's display name (already
            emoji-formatted via format_collection_display), or None.
        eligible_count: The actual number of eligible watch items found,
            from the same authoritative source /vote start uses to select
            nominees (see NomineeSelectionService.eligible_candidate_count).
        requested_count: The candidate count this vote actually asked
            for -- the same resolved value used for eligibility
            resolution and nominee selection alike.
    """
    collection_clause = f' for "{collection_display_name}"' if collection_display_name else ""
    is_are = "is" if eligible_count == 1 else "are"
    return (
        f"Not enough eligible watch items to start this vote{collection_clause}.\n\n"
        f"This vote requires {requested_count} candidates, but only {eligible_count} {is_are} "
        f"currently available.\n\n"
        "Add more suggestions, reduce the candidate count, or review your Vote Winners and Retired items."
    )


def build_insufficient_filtered_pool_message(
    eligible_count: int,
    requested_count: int,
    *,
    member_display: Optional[str] = None,
    genre: Optional[str] = None,
    imdb_rating_min: Optional[float] = None,
    imdb_rating_max: Optional[float] = None,
    mpaa_rating: Optional[str] = None,
    actor: Optional[str] = None,
) -> str:
    """Build the "not enough eligible suggestions" message for a
    Custom Vote Filters-narrowed pool (Filtered-Pool Validation).

    Identifies how many eligible suggestions remain, how many nominees
    are required, which filter(s) are active, and how to resolve the
    problem -- called instead of build_insufficient_candidates_message
    whenever a member and/or genre filter is active, so the message
    always names the actual active filter(s) rather than the generic
    "not enough eligible watch items" wording. Wording for the
    single-filter cases matches the exact phrasing WASH Crew relies on
    (e.g. "KC has 2 eligible suggestions, but this vote requires 3
    nominees. Reduce the candidate count or choose another member.").

    Args:
        eligible_count: The number of eligible suggestions remaining
            after every active filter has been applied.
        requested_count: The candidate count this vote actually asked for.
        member_display: The filtered member's display text (e.g. "KC"),
            or None if no member filter is active.
        genre: The filtered genre, or None if no genre filter is active.
    """
    nominee_word = "nominee" if requested_count == 1 else "nominees"
    suggestion_word = "suggestion" if eligible_count == 1 else "suggestions"
    has_new_filters = imdb_rating_min is not None or imdb_rating_max is not None or bool(mpaa_rating) or bool(actor)

    if not has_new_filters:
        # Exact legacy wording, unchanged, for the original two filters.
        if member_display and genre:
            return (
                f"{member_display}'s {genre} suggestions leave {eligible_count} eligible {suggestion_word}, "
                f"but this vote requires {requested_count} {nominee_word}. Reduce the candidate count, "
                "choose another member, or choose another genre."
            )
        if member_display:
            return (
                f"{member_display} has {eligible_count} eligible {suggestion_word}, but this vote requires "
                f"{requested_count} {nominee_word}. Reduce the candidate count or choose another member."
            )
        if genre:
            return (
                f"The {genre} filter leaves {eligible_count} eligible {suggestion_word}, but this vote requires "
                f"{requested_count} {nominee_word}. Reduce the candidate count or choose another genre."
            )
        return (
            f"Not enough eligible suggestions to start this vote. This vote requires {requested_count} "
            f"{nominee_word}, but only {eligible_count} {'is' if eligible_count == 1 else 'are'} currently available."
        )

    # Generic path once any of IMDb Rating/MPAA Rating/Actor is also
    # active -- scales to however many of the five filters are on,
    # rather than needing one hand-written branch per combination.
    active_descriptions: List[str] = []
    if genre:
        active_descriptions.append(f"the {genre} genre")
    if imdb_rating_min is not None or imdb_rating_max is not None:
        active_descriptions.append(
            f"an IMDb Rating of {ImdbRatingFilter(minimum=imdb_rating_min, maximum=imdb_rating_max).describe()}"
        )
    if mpaa_rating:
        active_descriptions.append(f"an MPAA Rating of {mpaa_rating}")
    if actor:
        active_descriptions.append(f"{actor} in the cast")
    if member_display:
        active_descriptions.append(f"{member_display}'s suggestions")
    joined = (
        active_descriptions[0]
        if len(active_descriptions) == 1
        else ", ".join(active_descriptions[:-1]) + f", and {active_descriptions[-1]}"
    )
    return (
        f"Filtering to {joined} leaves {eligible_count} eligible {suggestion_word}, but this vote requires "
        f"{requested_count} {nominee_word}. Reduce the candidate count or change a filter."
    )


def build_genre_filter_select_options(pool: List[WatchItem]) -> List[discord.SelectOption]:
    """Build Genre Filter's select options from a collection's eligible
    pool (Genre Filter): one option per genre actually represented,
    each showing its own eligible-suggestion count where Discord's
    limits allow (e.g. "Horror" / "7 eligible suggestions"), built
    through build_safe_select_option so a long or unusual genre name can
    never produce an invalid payload.

    IMDb genre lists normally fit within Discord's 25-option maximum,
    but this still caps and orders deterministically (most-represented
    genre first, alphabetical to break ties) via cap_select_options
    rather than ever sending an invalid, oversized component.
    """
    counts = genre_eligibility_counts(pool)
    ordered_genres = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0].lower()))
    options = [
        build_safe_select_option(
            genre,
            genre,
            description=f"{count} eligible suggestion{'s' if count != 1 else ''}",
        )
        for genre, count in ordered_genres
    ]
    return cap_select_options(options)


def build_mpaa_rating_filter_select_options(pool: List[WatchItem]) -> List[discord.SelectOption]:
    """Build MPAA Rating Filter's select options from a collection's
    eligible pool -- one option per rating actually represented, each
    showing its own eligible-suggestion count, built through
    build_safe_select_option/cap_select_options exactly like Genre's own
    options (see build_genre_filter_select_options). This select is
    min_values=0 (Section 6 UX polish, matching Genre/Member's own
    shape) with resetting handled entirely by MpaaRatingEditView's
    standalone Clear Filter button, so -- unlike the previous design,
    which prepended an inline "Any MPAA Rating" option and had to cap
    one short of Discord's 25-option limit to leave it room -- this can
    now return the full 25.
    """
    counts = mpaa_rating_eligibility_counts(pool)
    ordered_ratings = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0].lower()))
    options = [
        build_safe_select_option(
            rating, rating, description=f"{count} eligible suggestion{'s' if count != 1 else ''}"
        )
        for rating, count in ordered_ratings
    ]
    return cap_select_options(options)


def build_actor_match_select_options(matches: List[tuple[str, int]]) -> List[discord.SelectOption]:
    """Build Actor Filter's disambiguation options from search_cast_names'
    already-sorted (name, count) matches, through the same safe builders
    as every other option list in this project.
    """
    options = [
        build_safe_select_option(name, name, description=f"{count} eligible suggestion{'s' if count != 1 else ''}")
        for name, count in matches
    ]
    return cap_select_options(options)


def describe_imdb_rating_filter_state(filter_state: dict) -> str:
    """The Current Filters summary's IMDb Rating value -- "Any IMDb
    Rating" when both bounds are unset, otherwise ImdbRatingFilter's own
    describe() (e.g. "7.0+", "5.9 or lower", "6.0-8.0").
    """
    minimum = filter_state.get("imdb_rating_min")
    maximum = filter_state.get("imdb_rating_max")
    if minimum is None and maximum is None:
        return "Any IMDb Rating"
    return ImdbRatingFilter(minimum=minimum, maximum=maximum).describe()


def build_current_filters_summary(filter_state: dict) -> str:
    """Render the shared, scalable "Current Filters" block used by both
    Custom Vote Filters and /random watch's Add Filters screen -- a
    dot-leader-aligned line per filter, in the fixed Genre/IMDb Rating/
    MPAA Rating/Actor/Member order (Section 2/12), always shown (an
    inactive filter's "Any ..." value is never omitted), e.g.:

        **Current Filters**

        Genre .......... Sci-Fi
        IMDb Rating .... 7.0+
        MPAA Rating .... Any MPAA Rating
        Actor .......... Any Actor
        Member ......... KC

    The actual layout lives in filter_menu_view.format_current_filters_block
    (reusable as-is by any future consumer of the shared filter menu,
    e.g. /browse) -- this function's only job is supplying the current
    per-category values, via the same build_filter_category_current_values
    both this and FilterCategorySelectComponent's own option labels read
    from, so the two can never drift out of sync.
    """
    return format_current_filters_block(build_filter_category_current_values(filter_state))


def build_filter_category_current_values(filter_state: dict) -> dict:
    """The per-category "current value" strings FilterCategorySelectComponent
    shows in its own option labels (e.g. "Genre: Comedy") -- kept
    consistent with build_current_filters_summary's own wording.
    """
    return {
        FILTER_CATEGORY_GENRE: filter_state.get("genre") or "Any Genre",
        FILTER_CATEGORY_IMDB_RATING: describe_imdb_rating_filter_state(filter_state),
        FILTER_CATEGORY_MPAA_RATING: filter_state.get("mpaa_rating") or "Any MPAA Rating",
        FILTER_CATEGORY_ACTOR: filter_state.get("actor") or "Any Actor",
        FILTER_CATEGORY_MEMBER: filter_state.get("member_display") or "Any Member",
    }


def resolve_active_filters_from_state(filter_state: dict) -> List[NomineePoolFilter]:
    """Build the active NomineePoolFilter list from a filter session's
    state dict, in the shared Genre/IMDb Rating/MPAA Rating/Actor/Member
    order (Section 10's expected conceptual order) -- each filter is a
    pure set intersection, so the order filters are applied in never
    changes the result; this order is purely for consistency with the
    order they're presented and described in.
    """
    filters: List[NomineePoolFilter] = []
    if filter_state.get("genre"):
        filters.append(GenreFilter(genre=filter_state["genre"]))
    if filter_state.get("imdb_rating_min") is not None or filter_state.get("imdb_rating_max") is not None:
        filters.append(
            ImdbRatingFilter(minimum=filter_state.get("imdb_rating_min"), maximum=filter_state.get("imdb_rating_max"))
        )
    if filter_state.get("mpaa_rating"):
        filters.append(MpaaRatingFilter(rating=filter_state["mpaa_rating"]))
    if filter_state.get("actor"):
        filters.append(ActorFilter(actor=filter_state["actor"]))
    if filter_state.get("member_id") is not None:
        filters.append(
            MemberSuggestionFilter(
                discord_user_id=filter_state["member_id"], member_display=filter_state["member_display"]
            )
        )
    return filters


def build_nominee_pool_filters(
    *,
    filter_genre: Optional[str] = None,
    filter_imdb_rating_min: Optional[float] = None,
    filter_imdb_rating_max: Optional[float] = None,
    filter_mpaa_rating: Optional[str] = None,
    filter_actor: Optional[str] = None,
    filter_member_discord_user_id: Optional[int] = None,
    filter_member_display: Optional[str] = None,
) -> List[NomineePoolFilter]:
    """Build the active NomineePoolFilter list from individual scalar
    filter values -- the same shape perform_start_vote/
    handle_customize_vote_submit already receive their filter_* keyword
    arguments in. Mirrors resolve_active_filters_from_state's own filter
    order and construction exactly (that one instead takes a filter
    session's dict, for the filter menu's own use), so the two can never
    drift apart on what counts as "active."
    """
    filters: List[NomineePoolFilter] = []
    if filter_genre is not None:
        filters.append(GenreFilter(genre=filter_genre))
    if filter_imdb_rating_min is not None or filter_imdb_rating_max is not None:
        filters.append(ImdbRatingFilter(minimum=filter_imdb_rating_min, maximum=filter_imdb_rating_max))
    if filter_mpaa_rating is not None:
        filters.append(MpaaRatingFilter(rating=filter_mpaa_rating))
    if filter_actor is not None:
        filters.append(ActorFilter(actor=filter_actor))
    if filter_member_discord_user_id is not None:
        filters.append(
            MemberSuggestionFilter(
                discord_user_id=filter_member_discord_user_id,
                member_display=filter_member_display or f"<@{filter_member_discord_user_id}>",
            )
        )
    return filters


def new_filter_session_state() -> dict:
    return {
        "genre": None,
        "imdb_rating_min": None,
        "imdb_rating_max": None,
        "mpaa_rating": None,
        "actor": None,
        "member_id": None,
        "member_display": None,
        "member_filter_invalid": False,
        "member_filter_status_line": "",
    }


def create_filter_menu_session(
    *,
    bot: "WatchPartyBot",
    eligible_pool: List[WatchItem],
    collection_display_name: str,
    body_header: str,
    on_primary_action: Callable[[discord.Interaction, dict], Awaitable[None]],
    primary_action_label: str,
    primary_action_custom_id: str,
    on_secondary_action: Optional[Callable[[discord.Interaction], Awaitable[None]]] = None,
    secondary_action_label: Optional[str] = None,
):
    """Build one filter-editing session shared by Custom Vote Filters and
    /random watch -- a fresh filter_state dict plus a fully wired
    show_menu(interaction, ...) renderer covering every filter's own
    edit screen (Genre/MPAA Rating: select; Member: UserSelect with the
    existing validate_member_filter_selection warning behavior; IMDb
    Rating/Actor: button-triggered modal, each with its own explicit
    Any-reset button). Returns (filter_state, show_menu) -- the caller
    supplies on_primary_action (Pick Random Item / Continue to Vote
    Settings) and reads filter_state itself once that fires.

    This is the one place the whole filter-editing experience is
    defined, so the two flows can never diverge on order, wording,
    validation, or button behavior (Section 6/10).
    """
    filter_state = new_filter_session_state()
    genre_options = build_genre_filter_select_options(eligible_pool)
    mpaa_options = build_mpaa_rating_filter_select_options(eligible_pool)

    async def show_menu(target: discord.Interaction, *, edit: bool = True, status_line: str = "") -> None:
        body = "\n\n".join(
            part for part in (body_header, status_line, build_current_filters_summary(filter_state)) if part
        )
        view = FilterMenuView(
            on_category_selected,
            on_primary_action_clicked,
            current_values=build_filter_category_current_values(filter_state),
            primary_action_label=primary_action_label,
            primary_action_custom_id=primary_action_custom_id,
            primary_action_disabled=filter_state["member_filter_invalid"],
            on_secondary_action=on_secondary_action,
            secondary_action_label=secondary_action_label,
        )
        if edit:
            await target.response.edit_message(content=body, view=view)
        else:
            await target.response.send_message(content=body, view=view, ephemeral=True)

    async def on_primary_action_clicked(interaction: discord.Interaction) -> None:
        await on_primary_action(interaction, filter_state)

    async def on_category_selected(interaction: discord.Interaction, category: str) -> None:
        if category == FILTER_CATEGORY_GENRE:
            await show_genre_edit(interaction)
        elif category == FILTER_CATEGORY_IMDB_RATING:
            await show_imdb_rating_edit(interaction)
        elif category == FILTER_CATEGORY_MPAA_RATING:
            await show_mpaa_rating_edit(interaction)
        elif category == FILTER_CATEGORY_ACTOR:
            await show_actor_edit(interaction)
        elif category == FILTER_CATEGORY_MEMBER:
            await show_member_edit(interaction)

    async def on_back_to_menu(interaction: discord.Interaction) -> None:
        await show_menu(interaction)

    async def show_genre_edit(interaction: discord.Interaction) -> None:
        view = GenreEditView(on_genre_changed, on_back_to_menu, options=genre_options)
        await interaction.response.edit_message(content=body_header, view=view)

    async def on_genre_changed(interaction: discord.Interaction, genre: Optional[str]) -> None:
        filter_state["genre"] = genre
        await show_menu(interaction)

    async def show_mpaa_rating_edit(interaction: discord.Interaction) -> None:
        view = MpaaRatingEditView(on_mpaa_rating_changed, on_back_to_menu, options=mpaa_options)
        await interaction.response.edit_message(content=body_header, view=view)

    async def on_mpaa_rating_changed(interaction: discord.Interaction, rating: Optional[str]) -> None:
        filter_state["mpaa_rating"] = rating
        await show_menu(interaction)

    async def show_member_edit(interaction: discord.Interaction, *, status_line: str = "") -> None:
        content = f"{body_header}\n\n{status_line}" if status_line else body_header
        view = MemberEditView(on_member_changed, on_back_to_menu)
        await interaction.response.edit_message(content=content, view=view)

    async def on_member_changed(member_interaction: discord.Interaction, member: Optional[discord.Member]) -> None:
        role_config = bot.membership_service.get_role_config(member_interaction.guild_id)
        watch_party_role_id = role_config.role_id if role_config is not None else None
        guild_owner_id = member_interaction.guild.owner_id if member_interaction.guild is not None else None
        result = validate_member_filter_selection(
            member,
            watch_party_role_id=watch_party_role_id,
            wash_crew_role_id=resolve_permission_service(
                member_interaction.guild_id, bot.guild_configuration_repository
            ).wash_crew_role_id,
            guild_owner_id=guild_owner_id,
            eligible_pool=eligible_pool,
            collection_display_name=collection_display_name,
        )
        filter_state["member_id"] = result.discord_user_id
        filter_state["member_display"] = result.member_display
        filter_state["member_filter_invalid"] = not result.valid
        filter_state["member_filter_status_line"] = result.status_line
        await show_member_edit(member_interaction, status_line=result.status_line)

    async def show_imdb_rating_edit(interaction: discord.Interaction) -> None:
        view = ImdbRatingEditView(on_imdb_rating_set_clicked, on_imdb_rating_cleared, on_back_to_menu)
        await interaction.response.edit_message(content=body_header, view=view)

    async def on_imdb_rating_set_clicked(interaction: discord.Interaction) -> None:
        default_minimum = (
            f"{filter_state['imdb_rating_min']:.1f}" if filter_state["imdb_rating_min"] is not None else ""
        )
        default_maximum = (
            f"{filter_state['imdb_rating_max']:.1f}" if filter_state["imdb_rating_max"] is not None else ""
        )
        await interaction.response.send_modal(
            ImdbRatingModal(on_imdb_rating_submitted, default_minimum=default_minimum, default_maximum=default_maximum)
        )

    async def on_imdb_rating_submitted(
        modal_interaction: discord.Interaction, minimum_text: Optional[str], maximum_text: Optional[str]
    ) -> None:
        try:
            minimum, maximum = parse_imdb_rating_bounds(minimum_text, maximum_text)
        except ValueError as exc:
            view = ImdbRatingEditView(on_imdb_rating_set_clicked, on_imdb_rating_cleared, on_back_to_menu)
            await modal_interaction.response.edit_message(content=f"{body_header}\n\n⚠️ {exc}", view=view)
            return
        filter_state["imdb_rating_min"] = minimum
        filter_state["imdb_rating_max"] = maximum
        await show_menu(modal_interaction)

    async def on_imdb_rating_cleared(interaction: discord.Interaction) -> None:
        filter_state["imdb_rating_min"] = None
        filter_state["imdb_rating_max"] = None
        await show_menu(interaction)

    async def show_actor_edit(interaction: discord.Interaction, *, status_line: str = "") -> None:
        content = f"{body_header}\n\n{status_line}" if status_line else body_header
        view = ActorEditView(on_actor_search_clicked, on_actor_cleared, on_back_to_menu)
        await interaction.response.edit_message(content=content, view=view)

    async def on_actor_search_clicked(interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(ActorSearchModal(on_actor_search_submitted))

    async def on_actor_search_submitted(modal_interaction: discord.Interaction, query: Optional[str]) -> None:
        if not query:
            filter_state["actor"] = None
            await show_menu(modal_interaction)
            return
        matches = search_cast_names(eligible_pool, query)
        if not matches:
            await show_actor_edit(
                modal_interaction, status_line=f'⚠️ No actors matching "{query}" were found. Try a different search.'
            )
            return
        if len(matches) == 1:
            filter_state["actor"] = matches[0][0]
            await show_menu(modal_interaction)
            return
        capped = matches[:25]
        note = ""
        if len(matches) > 25:
            note = (
                f"\n\nMore than 25 actors match \"{query}\" -- showing the top 25 by eligible suggestions. "
                "Refine your search for a full list."
            )
        options = build_actor_match_select_options(capped)
        view = ActorMatchEditView(on_actor_match_selected, on_back_to_menu, options=options)
        await modal_interaction.response.edit_message(content=f"{body_header}{note}", view=view)

    async def on_actor_match_selected(interaction: discord.Interaction, actor: str) -> None:
        filter_state["actor"] = actor
        await show_menu(interaction)

    async def on_actor_cleared(interaction: discord.Interaction) -> None:
        filter_state["actor"] = None
        await show_menu(interaction)

    return filter_state, show_menu


def perform_start_vote(
    vote_service: VoteService,
    suggestion_service: SuggestionService,
    nominee_selection_service: Optional[NomineeSelectionService],
    user: object,
    wash_crew_role_id: Optional[int],
    visibility_str: Optional[str],
    duration_minutes: Optional[int],
    nominee_count: Optional[int] = None,
    default_nominee_count: int = DEFAULT_VOTE_CANDIDATE_COUNT,
    guild_id: Optional[int] = None,
    channel_id: Optional[int] = None,
    reminder_enabled: Optional[bool] = None,
    reminder_minutes_before_close: Optional[int] = None,
    suggestion_database_configuration_repository: Optional[SuggestionDatabaseConfigurationRepository] = None,
    guild_configuration_repository: Optional[GuildConfigurationRepository] = None,
    resolved_database_id: Optional[int] = None,
    candidate_selection_override: Optional[CandidateSelectionMode] = None,
    filter_member_discord_user_id: Optional[int] = None,
    filter_member_display: Optional[str] = None,
    filter_genre: Optional[str] = None,
    filter_imdb_rating_min: Optional[float] = None,
    filter_imdb_rating_max: Optional[float] = None,
    filter_mpaa_rating: Optional[str] = None,
    filter_actor: Optional[str] = None,
) -> tuple[str, bool]:
    """Core logic for /start_vote, kept free of Discord objects except `user`.

    Args:
        vote_service: The vote service to open a round on.
        suggestion_service: Used to resolve a database and report pool size.
        nominee_selection_service: Used to choose nominees when a database
            context is available (see resolve_database_for_channel).
            Optional so this stays usable in tests/contexts with no
            selection service configured.
        user: The member invoking the command (checked against the WASH
            Crew role).
        wash_crew_role_id: The configured WASH Crew role ID, or None if
            unconfigured.
        visibility_str: The raw visibility option text ("blind"/"visible"),
            or None when no explicit override was supplied ("use default
            settings", or a blank visibility field while customizing) --
            see parse_vote_visibility, which resolves None against the
            guild's configured default via guild_configuration_repository.
        duration_minutes: The raw duration option in minutes, or None for
            the default -- see parse_vote_duration_minutes, which
            resolves None against the guild's configured default via
            guild_configuration_repository.
        nominee_count: The raw nominee_count option ("customize this
            vote"), or None to use default_nominee_count ("use default
            settings").
        default_nominee_count: The WASH Crew-configured default nominee
            count, used when nominee_count is None.
        guild_id: The Discord guild the command was run in, if known.
        channel_id: The Discord channel or thread the command was run in,
            if known.
        reminder_enabled: FR-027: a per-round override of the guild's
            configured vote-ending reminder setting, or None to use the
            guild default.
        reminder_minutes_before_close: FR-027: a per-round override of how
            many minutes before closing the reminder fires, or None to use
            the guild default.
        suggestion_database_configuration_repository: FR-033B: used to
            resolve the database's configured CandidateSelectionMode into
            a CandidateSelectionStrategy passed to
            nominee_selection_service. Optional -- when None (e.g. no
            database context, or a test caller with no repository
            configured), nominee_selection_service falls back to its own
            default behavior with no strategy applied.
        guild_configuration_repository: Release Polish Batch 2, Priority 6:
            used to resolve the guild's configured default visibility
            when visibility_str is None. Optional -- when unset (e.g. a
            test caller with no guild configuration to resolve),
            visibility_str's own default of VISIBLE applies.
        resolved_database_id: Contextual Database Resolution: when the
            channel context was ambiguous (more than one database, none
            matching), the Discord layer already showed a picker and the
            WASH Crew member already chose -- pass that choice here to
            use it directly instead of re-resolving by channel. None
            (the default) preserves the normal channel-based resolution
            below, unchanged.
        candidate_selection_override: UI Polish (Voting Configuration
            Improvements): a one-time override of this round's candidate
            selection mode, from Customize This Vote's dropdown. None
            (the default, and always the case for "Use Defaults") uses
            the resolved database's own configured mode, unchanged.
            Never persisted -- overriding here never changes the
            collection's own saved Candidate Selection setting.
        filter_member_discord_user_id: Custom Vote Filter Architecture: a
            one-time, per-vote narrowing of the nominee pool to eligible
            suggestions submitted by this one Discord member, applied
            before the resolved Nominee Selection strategy runs (see
            services/nominee_pool_filter.py). None (the default) applies
            no member filter. Never persisted to the collection's own
            configuration.
        filter_member_display: Plain display text for the filtered
            member (e.g. "KC"), used only to build this call's own
            insufficient-pool message -- perform_start_vote otherwise
            stays free of Discord objects, so the caller resolves this
            once up front rather than passing a discord.Member in.
        filter_genre: Custom Vote Filter Architecture: a one-time,
            per-vote narrowing of the nominee pool to eligible
            suggestions tagged with this genre (matched case-
            insensitively against already-persisted IMDb metadata), also
            applied before nominee selection. None (the default) applies
            no genre filter. Never persisted to the collection's own
            configuration. Combines with filter_member_discord_user_id
            when both are set -- the pool is narrowed by both filters
            before selection runs.

    Returns:
        A (message, ephemeral) tuple. Errors and permission failures are
        ephemeral; the success confirmation is not. VoteService.create_round()
        is never called if any validation fails first.
    """
    if wash_crew_role_id is None:
        return (
            "WASH Crew permissions have not been configured. "
            "Set WASH_CREW_ROLE_ID before using this command.",
            True,
        )

    if not is_wash_crew_member(user, wash_crew_role_id):
        return "You need the WASH Crew role to start a voting round.", True

    default_visibility = GuildVoteVisibility.VISIBLE
    default_duration_minutes = DEFAULT_VOTE_DURATION_MINUTES
    if guild_configuration_repository is not None and guild_id is not None:
        configuration = guild_configuration_repository.get(guild_id)
        if configuration is not None:
            default_visibility = configuration.voting_defaults.visibility
            default_duration_minutes = configuration.voting_defaults.duration_minutes

    try:
        visibility = parse_vote_visibility(visibility_str, default=default_visibility)
    except ValueError as exc:
        return str(exc), True

    try:
        minutes = parse_vote_duration_minutes(duration_minutes, default=default_duration_minutes)
    except ValueError as exc:
        return str(exc), True

    try:
        count = parse_vote_nominee_count(nominee_count, default=default_nominee_count)
    except ValueError as exc:
        return str(exc), True

    try:
        reminder_minutes_before_close = parse_vote_reminder_minutes_before_close(reminder_minutes_before_close)
    except ValueError as exc:
        return str(exc), True

    resolution = None
    if resolved_database_id is not None and nominee_selection_service is not None:
        chosen = suggestion_service.get_database(resolved_database_id)
        if chosen is None or (guild_id is not None and chosen.guild_id != guild_id):
            return "That collection no longer exists.", True
        resolution = DatabaseResolution(database=chosen)
    elif guild_id is not None and channel_id is not None and nominee_selection_service is not None:
        resolution = suggestion_service.resolve_database_for_channel(
            guild_id, channel_id, suggestion_database_configuration_repository
        )
        if resolution.database is None:
            return (
                resolution.error_message
                or "Which collection would you like to use? Run this in the channel or thread "
                "configured for the one you mean.",
                True,
            )

    if resolution is not None:
        # This collection's own open-round check must happen before
        # anything below mutates state (select_nominees marks candidates
        # as presented -- a permanent, unrolled-back side effect).
        # vote_service.create_round() below performs this exact same
        # check, but only after that mutation already ran -- checking
        # here first means starting a vote on a collection that already
        # has one open fails cleanly instead of leaving candidates marked
        # presented for a round that was then rejected and never created.
        if vote_service.get_open_round(resolution.database.database_id) is not None:
            return "A voting round is already open for this collection.", True

        strategy = None
        mode = None
        if suggestion_database_configuration_repository is not None:
            database_configuration = suggestion_database_configuration_repository.get(
                guild_id, resolution.database.database_id
            )
            configured_mode = (
                database_configuration.suggestion_rules.candidate_selection
                if database_configuration is not None
                else DEFAULT_CANDIDATE_SELECTION_MODE
            )
            # A Customize This Vote override applies to this round's
            # nominee selection only -- the collection's own configured
            # mode (read just above) is never written back to.
            mode = candidate_selection_override if candidate_selection_override is not None else configured_mode
            strategy = build_candidate_selection_strategy(mode, suggestion_service)

        # Custom Vote Filter Architecture: narrow the pool through
        # zero or more per-vote-only NomineePoolFilters before the
        # resolved Nominee Selection strategy runs, via a
        # FilteredCandidateSelectionStrategy decorator -- neither
        # NomineeSelectionService nor VoteService needs to know filters
        # exist. Wraps a default InfinitePoolStrategy when no
        # repository-resolved strategy exists yet (e.g. a test caller
        # with no repository configured), so filtering still works.
        filters = build_nominee_pool_filters(
            filter_genre=filter_genre,
            filter_imdb_rating_min=filter_imdb_rating_min,
            filter_imdb_rating_max=filter_imdb_rating_max,
            filter_mpaa_rating=filter_mpaa_rating,
            filter_actor=filter_actor,
            filter_member_discord_user_id=filter_member_discord_user_id,
            filter_member_display=filter_member_display,
        )

        effective_strategy = strategy
        if filters:
            base_strategy = strategy if strategy is not None else InfinitePoolStrategy(suggestion_source=suggestion_service)
            effective_strategy = FilteredCandidateSelectionStrategy(inner=base_strategy, filters=filters)

        eligible_count = nominee_selection_service.eligible_candidate_count(
            resolution.database.database_id, effective_strategy, requested_count=count
        )
        # Vote Creation Validation: compare against the actual resolved
        # candidate count for this request (`count` -- the same value
        # just passed to eligible_candidate_count above, and about to be
        # passed to select_nominees below), never a fixed, lower minimum.
        # select_nominees silently returns fewer than `count` nominees
        # when the pool is smaller than requested rather than raising, so
        # this check is the only place that catches "fewer eligible
        # watch items than this vote actually asked for" before a round
        # is created with an unrequested, silently-shrunk candidate list.
        # Filtered-Pool Validation: no round, persistent view, or
        # announcement is ever created past this point when the check
        # fails -- this function returns before any of that exists.
        if eligible_count < count:
            if filters:
                message = build_insufficient_filtered_pool_message(
                    eligible_count,
                    count,
                    member_display=filter_member_display if filter_member_discord_user_id is not None else None,
                    genre=filter_genre,
                    imdb_rating_min=filter_imdb_rating_min,
                    imdb_rating_max=filter_imdb_rating_max,
                    mpaa_rating=filter_mpaa_rating,
                    actor=filter_actor,
                )
            else:
                collection_display_name = format_collection_display(resolution.database.name)
                message = build_insufficient_candidates_message(collection_display_name, eligible_count, count)
            return message, True
        candidates = nominee_selection_service.select_nominees(
            resolution.database.database_id, count, strategy=effective_strategy
        )
    else:
        # No database context (or no selection service configured): fall
        # back to a simple, non-database-scoped pool. Same rule as above
        # -- compare against the actual resolved candidate count, not a
        # fixed minimum, so this fallback path can never silently create
        # a round smaller than what was requested either.
        available = suggestion_service.get_suggestions()
        if len(available) < count:
            return build_insufficient_candidates_message(None, len(available), count), True
        candidates = available[:count]

    closes_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    result = vote_service.create_round(
        visibility=visibility,
        closes_at=closes_at,
        candidate_suggestion_ids=[candidate.id for candidate in candidates],
        database_id=(resolution.database.database_id if resolution is not None else None),
        reminder_enabled=reminder_enabled,
        reminder_minutes_before_close=reminder_minutes_before_close,
        candidate_selection_mode=(mode if resolution is not None else None),
        filter_member_discord_user_id=(filter_member_discord_user_id if resolution is not None else None),
        filter_genre=(filter_genre if resolution is not None else None),
        filter_imdb_rating_min=(filter_imdb_rating_min if resolution is not None else None),
        filter_imdb_rating_max=(filter_imdb_rating_max if resolution is not None else None),
        filter_mpaa_rating=(filter_mpaa_rating if resolution is not None else None),
        filter_actor=(filter_actor if resolution is not None else None),
    )
    if not result.success:
        return result.message, True

    if resolution is not None:
        pool_count = len(suggestion_service.get_suggestions_for_database(resolution.database.database_id))
    else:
        pool_count = suggestion_service.suggestion_count()
    return build_start_vote_confirmation(
        result.vote_round, len(candidates), minutes, pool_count=pool_count
    ), False


def parse_optional_int_field(value: Optional[str]) -> Optional[int]:
    """Parse an optional whole-number field from a Discord modal.

    Blank values use the configured default. Range validation remains in
    the existing vote parsing helpers called by :func:`perform_start_vote`.
    """
    if value is None or not value.strip():
        return None
    try:
        return int(value.strip())
    except ValueError as exc:
        raise ValueError(f"'{value.strip()}' is not a whole number.") from exc


def parse_optional_duration_field(value: Optional[str]) -> Optional[int]:
    """Parse an optional voting-duration field from a Discord modal into minutes.

    Blank values mean "use the configured default", matching
    parse_optional_int_field's own contract. A non-blank value is parsed
    by parse_duration_text_to_minutes (WASH's shared duration syntax --
    "10m", "1h", "7d", etc.) -- range validation against the
    1-minute to 30-day bounds remains in parse_vote_duration_minutes,
    called by :func:`perform_start_vote`.
    """
    if value is None or not value.strip():
        return None
    return parse_duration_text_to_minutes(value)


_TRUTHY_FIELD_VALUES = {"yes", "y", "true", "on", "enable", "enabled"}
_FALSY_FIELD_VALUES = {"no", "n", "false", "off", "disable", "disabled"}


def parse_optional_bool_field(value: Optional[str]) -> Optional[bool]:
    """Parse an optional yes/no field from a Discord modal.

    FR-027: used for the "Customize This Vote" reminder-enabled override.
    Blank means "use the configured default" -- the same convention every
    other optional modal field already follows.

    Args:
        value: The raw field text, or None/blank to use the default.

    Returns:
        None if value is blank, otherwise the parsed boolean.

    Raises:
        ValueError: If value is non-blank but not a recognized yes/no word.
    """
    if value is None or not value.strip():
        return None

    normalized = value.strip().lower()
    if normalized in _TRUTHY_FIELD_VALUES:
        return True
    if normalized in _FALSY_FIELD_VALUES:
        return False
    raise ValueError(f"'{value.strip()}' must be 'yes' or 'no'.")


# --- FR-028: /setup wizard ---------------------------------------------------------------


def perform_setup_permission_check(
    user: object,
    *,
    guild_configuration: Optional[GuildConfiguration] = None,
    is_guild_owner: bool = False,
) -> tuple[str, bool]:
    """Gate /setup.

    Bootstrap Setup Permission Fix: until *this* guild's own configuration
    has associated a WASH Crew role, only the Discord guild owner may run
    /setup -- so a brand-new guild is never locked out. Once this guild's
    configuration has a WASH Crew role, /setup falls back to the exact
    same fail-closed rule as every other administrative command (guild
    ownership grants no special access at that point), so a completed
    setup can't be silently redone by an unauthorized member.

    Multi-Guild Isolation, Phase 3c: previously took a second
    `wash_crew_role_id` parameter -- the shared, bot-wide
    WASH_CREW_ROLE_ID fallback (see WatchPartyBot.__init__) -- and used
    it for the "already configured" branch below, even though
    guild_configuration.wash_crew_role_id (this guild's own, authoritative
    value) was already available right here. That bot-wide parameter is
    removed entirely: both branches now resolve purely from
    guild_configuration, so a role that happens to be WASH Crew in a
    different guild (or a stale, no-longer-relevant environment variable)
    can never grant or deny access to *this* guild's /setup.

    Returns:
        (message, blocked) -- blocked is True if the command should stop
        here and show `message` instead of proceeding.
    """
    guild_wash_crew_role_configured = (
        guild_configuration is not None and guild_configuration.wash_crew_role_id is not None
    )
    if not guild_wash_crew_role_configured:
        if is_guild_owner:
            return "", False
        return "You need the WASH Crew role to run setup.", True

    if not is_wash_crew_member(user, guild_configuration.wash_crew_role_id):
        return "You need the WASH Crew role to run setup.", True
    return "", False


def perform_setup_redirect_check(guild_configuration: Optional[GuildConfiguration]) -> Optional[str]:
    """FR-029: stop /setup from silently restarting a completed setup.

    Once GuildConfiguration.setup_completed is True, /setup must never
    resume or restart the wizard -- changes belong in /config from then
    on. Incomplete setup (guild_configuration is None, or setup_completed
    is still False) is untouched: it keeps FR-028's existing
    resume/restart behavior.

    Returns:
        A message to show instead of starting/resuming the wizard, or
        None if /setup should proceed as usual.
    """
    if guild_configuration is not None and guild_configuration.setup_completed:
        return "**Setup has already been completed for this server.** Use `/config` to review or change settings."
    return None


SETUP_WIZARD_STEP_TITLES: dict[SetupWizardStep, str] = {
    SetupWizardStep.WASH_CREW_ROLE: "🛠️ WASH Crew Role",
    SetupWizardStep.WATCH_PARTY_ROLE: "🍿 Watch Party Role",
    SetupWizardStep.ADMIN_CHANNEL: "Admin Channel",
    SetupWizardStep.HOME_CHANNEL: "Home Channel",
    SetupWizardStep.SUGGESTION_DATABASE: "Collections",
    SetupWizardStep.WATCH_DESTINATION: "Watched Item Archive",
    SetupWizardStep.VOTING_DEFAULTS: "Voting Defaults",
    SetupWizardStep.REJECTION_SETTINGS: "\"I Won't Watch\" Settings",
    SetupWizardStep.REMINDER_DEFAULTS: "Reminder Defaults",
    SetupWizardStep.BACKUP_DEFAULTS: "Backup Defaults",
    SetupWizardStep.REVIEW: "Review",
}


def build_setup_step_header(state: SetupWizardState) -> str:
    """Build the "Step N of M: Title" progress indicator shown atop every step."""
    total = len(SETUP_WIZARD_STEP_ORDER)
    position = SETUP_WIZARD_STEP_ORDER.index(state.current_step) + 1
    title = SETUP_WIZARD_STEP_TITLES[state.current_step]
    return f"**WASH Setup -- Step {position} of {total}: {title}**"


def parse_setup_voting_candidate_count(value: str) -> int:
    """Validate a Voting Defaults modal's nominee-count field.

    Reuses /start_vote's own bounds (MIN/MAX_VOTE_CANDIDATE_COUNT) so the
    guild-wide default the wizard sets can never be a value /start_vote
    would itself reject.
    """
    try:
        count = int(value.strip())
    except ValueError:
        raise ValueError("Default candidate count must be a whole number.")
    if not (MIN_VOTE_CANDIDATE_COUNT <= count <= MAX_VOTE_CANDIDATE_COUNT):
        raise ValueError(
            f"Default candidate count must be between {MIN_VOTE_CANDIDATE_COUNT} and {MAX_VOTE_CANDIDATE_COUNT}."
        )
    return count


def parse_setup_rejection_threshold(value: str) -> int:
    """Validate a Voting Defaults modal's "I Won't Watch" threshold field.

    Reuses SuggestionRulesConfig.rejection_threshold's own bounds
    (MIN/MAX_REJECTION_THRESHOLD) so the value the wizard sets can never
    be one the domain model itself would reject.
    """
    try:
        threshold = int(value.strip())
    except ValueError:
        raise ValueError("I Won't Watch threshold must be a whole number.")
    if not (MIN_REJECTION_THRESHOLD <= threshold <= MAX_REJECTION_THRESHOLD):
        raise ValueError(
            f"I Won't Watch threshold must be between {MIN_REJECTION_THRESHOLD} and {MAX_REJECTION_THRESHOLD}."
        )
    return threshold


def describe_rejection_threshold(threshold: int) -> str:
    """Shared "I Won't Watch" threshold explanation for both the Setup
    Wizard's REJECTION_SETTINGS step and /config's "I Won't Watch"
    Settings section -- kept as one function so the two can never drift
    out of sync with each other.

    Worded around the caller's own current (or about-to-be-set)
    threshold so the example always matches what's actually configured,
    rather than a fixed illustrative number.
    """
    return (
        "Members may press **I WON'T WATCH** on a suggestion's own post -- each member's rejection "
        "counts only once. This threshold controls how many distinct members must do that before the "
        f"suggestion needs WASH Crew review -- for example, threshold {threshold} means the suggestion "
        f"is flagged for review once {threshold} different members choose \"I Won't Watch.\" Reaching it "
        "does not remove the suggestion from the collection: it only removes it from eligibility for "
        "future votes, becoming Pending Crew Review until WASH Crew decides to Retire it or Keep it "
        "Active."
    )


def parse_setup_voting_duration_minutes(value: str) -> int:
    """Validate a Voting Defaults modal's duration field, reusing /start_vote's bounds.

    Accepts the same flexible text as /start_vote's Customize This Vote
    duration field (see parse_duration_text_to_minutes): WASH's shared
    duration syntax -- "10m", "1h", "7d", etc. (w/week/weeks is also
    accepted but never advertised -- Duration UX Standard).
    """
    try:
        minutes = parse_duration_text_to_minutes(value)
    except ValueError:
        raise ValueError(DURATION_SYNTAX_HELP)
    if not (MIN_VOTE_DURATION_MINUTES <= minutes <= MAX_VOTE_DURATION_MINUTES):
        raise ValueError(
            f"Default vote duration must be between {format_duration_minutes(MIN_VOTE_DURATION_MINUTES)} and "
            f"{format_duration_minutes(MAX_VOTE_DURATION_MINUTES)}."
        )
    return minutes


def parse_setup_reminder_minutes_before_close(value: str) -> int:
    """Validate a Reminder Defaults modal's before-close field.

    Standardized on WASH's one shared duration syntax (Requirement 3):
    a whole number immediately followed by a unit -- m/minutes, h/hours,
    or d/days (w/week/weeks is also accepted but never advertised --
    Duration UX Standard). Reuses the same bounds as a /start_vote
    per-round reminder override (MIN/MAX_VOTE_REMINDER_MINUTES_BEFORE_CLOSE).
    """
    try:
        minutes = parse_duration_to_minutes(value)
    except ValueError:
        raise ValueError(DURATION_SYNTAX_HELP)
    if not (MIN_VOTE_REMINDER_MINUTES_BEFORE_CLOSE <= minutes <= MAX_VOTE_REMINDER_MINUTES_BEFORE_CLOSE):
        raise ValueError(
            "Reminder before close must be between "
            f"{format_duration_minutes(MIN_VOTE_REMINDER_MINUTES_BEFORE_CLOSE)} and "
            f"{format_duration_minutes(MAX_VOTE_REMINDER_MINUTES_BEFORE_CLOSE)}."
        )
    return minutes


def parse_setup_backup_interval_days(value: str) -> int:
    """Validate a Backup Defaults modal's interval field."""
    try:
        days = int(value.strip())
    except ValueError:
        raise ValueError("Automatic backup interval must be a whole number of days.")
    if not (MIN_BACKUP_INTERVAL_DAYS <= days <= MAX_BACKUP_INTERVAL_DAYS):
        raise ValueError(
            f"Automatic backup interval must be between {MIN_BACKUP_INTERVAL_DAYS} and {MAX_BACKUP_INTERVAL_DAYS} days."
        )
    return days


def parse_setup_backup_retention_count(value: str) -> int:
    """Validate a Backup Defaults modal's retention field."""
    try:
        count = int(value.strip())
    except ValueError:
        raise ValueError("Backup retention count must be a whole number.")
    if not (MIN_BACKUP_RETENTION_COUNT <= count <= MAX_BACKUP_RETENTION_COUNT):
        raise ValueError(
            f"Backup retention count must be between {MIN_BACKUP_RETENTION_COUNT} and {MAX_BACKUP_RETENTION_COUNT}."
        )
    return count


def build_setup_completion_summary(configuration: GuildConfiguration, draft: SetupWizardDraft) -> str:
    """Build the final "setup complete" message, distinguishing skipped items."""
    lines = ["**WASH Setup Complete**", ""]
    lines.append(f"🛠️ WASH Crew Role: <@&{configuration.wash_crew_role_id}>")
    if configuration.watch_party_role.role_id is not None:
        join_mode_label = JOIN_MODE_DISPLAY_LABELS[configuration.watch_party_role.join_mode]
        lines.append(f"🍿 Watch Party Role: <@&{configuration.watch_party_role.role_id}> (join mode: {join_mode_label})")
    else:
        lines.append("🍿 Watch Party Role: Not set")
    if draft.admin_channel_skipped:
        lines.append("Admin Channel: Skipped")
    elif draft.admin_channel_id is not None:
        lines.append(f"Admin Channel: <#{draft.admin_channel_id}>")
    if draft.home_channel_id is not None:
        lines.append(f"Home Channel: <#{draft.home_channel_id}>")
    lines.append(f'Collections: "{draft.suggestion_database_name}" (#{draft.suggestion_database_id})')
    if draft.watch_destination_skipped:
        lines.append("Watched Item Archive: Skipped")
    elif draft.watch_destination_channel_id is not None:
        lines.append(f"Watched Item Archive: <#{draft.watch_destination_channel_id}>")
    candidate_selection_label = (
        CANDIDATE_SELECTION_DISPLAY_LABELS[draft.voting_candidate_selection]
        if draft.voting_candidate_selection is not None
        else CANDIDATE_SELECTION_DISPLAY_LABELS[DEFAULT_CANDIDATE_SELECTION_MODE]
    )
    lines.append(
        "Voting Defaults: "
        f"{configuration.voting_defaults.candidate_count} candidates, "
        f"{format_duration_minutes(configuration.voting_defaults.duration_minutes)}, "
        f"{configuration.voting_defaults.visibility.value.capitalize()}, "
        f"nominee selection: {candidate_selection_label}"
    )
    if draft.rejection_enabled is False:
        lines.append("I Won't Watch: Disabled")
    else:
        rejection_threshold = (
            draft.rejection_threshold if draft.rejection_threshold is not None else DEFAULT_REJECTION_THRESHOLD
        )
        lines.append(f"I Won't Watch: Enabled, threshold {rejection_threshold}")
    if configuration.notifications.vote.vote_ending_reminder:
        lines.append(
            "Reminder Defaults: enabled, "
            f"{format_duration_minutes(configuration.notifications.vote.reminder_minutes_before_close)} before close"
        )
    else:
        lines.append("Reminder Defaults: disabled")
    if configuration.backup.include_in_automatic_backups:
        interval_days = configuration.backup.extra_fields.get(BACKUP_INTERVAL_DAYS_EXTRA_FIELD)
        retention_count = configuration.backup.extra_fields.get(BACKUP_RETENTION_COUNT_EXTRA_FIELD)
        if interval_days is not None:
            day_word = "day" if interval_days == 1 else "days"
            lines.append(f"Automatic Backups: Every {interval_days} {day_word}, keep {retention_count}")
    else:
        lines.append("Automatic Backups: Disabled")
    lines.append("")
    lines.append("**Next Steps**")
    lines.append(f'- Add your first watch item to "{draft.suggestion_database_name}" with `/add`.')
    lines.append("- Once you have a few suggestions, start your first vote with `/vote start`.")
    lines.append("- Run `/config` any time to change these defaults, or `/help` to see every command.")
    return "\n".join(lines)


def describe_channel_creation_failure(exc: Exception) -> str:
    """Build a user-facing explanation for a failed channel creation.

    Never exposes discord.Forbidden's raw text (a bare "403 Forbidden
    (error code: 50013): Missing Permissions") -- that's a permissions
    problem WASH Crew can actually fix, so it gets a specific, actionable
    message instead. Other failures keep their prior (already reasonably
    informative) exception text.
    """
    if isinstance(exc, discord.Forbidden):
        return (
            "WASH does not have permission to create channels here. Grant WASH the "
            "**Manage Channels** permission, or choose an existing channel from the selector instead."
        )
    return f"Could not create the channel: {exc}"


def build_channel_destination_options(
    guild: Optional[discord.Guild],
    *,
    include_channels: bool = True,
    include_threads: bool = True,
    selected_channel_id: Optional[int] = None,
) -> List[discord.SelectOption]:
    """Freshly enumerate this guild's usable channel/thread destinations.

    Built fresh on every call -- never cached or built once -- so a
    thread created earlier in the same setup/config session (or by
    anyone else, any time before this exact render) always appears
    immediately. This is what a discord.ui.ChannelSelect's own live,
    Discord-client-populated options promise, but discord.py's
    ChannelSelect can't filter out archived/locked threads or show
    parent-channel context, and can't pre-select a saved value -- three
    things this project's setup/config screens need, hence building the
    option list ourselves instead of delegating to it.

    Includes every text channel WASH can post in (when include_channels)
    and every active, unlocked thread WASH can post in (when
    include_threads). A thread's label is its own name, kept short and
    scannable; its parent channel is surfaced separately as the option's
    description ("Thread in #watch-party") rather than crammed into the
    label -- both fields are built through build_safe_select_option(), so
    an arbitrarily long channel/thread name (Discord allows up to 100
    characters each) can never combine into a label or description that
    exceeds Discord's own 100-character limit and gets the whole render
    rejected with a 400 (see discord_ui_limits.py). Archived and locked
    threads are never included. The already-saved destination
    (selected_channel_id), if it still resolves to a real channel/thread,
    is always included and marked as the pre-selected default even if it
    would otherwise be filtered out (e.g. WASH's permissions there
    changed since it was set) -- retaining it rather than silently
    clearing the user's prior choice; Discord's SelectOption (unlike
    ChannelSelect) supports exactly this kind of explicit preselection.

    Capped at 25 options (discord.ui.Select's own hard limit, see
    cap_select_options()) -- the currently selected destination is always
    kept within that cap even if the guild has more usable destinations
    than that.
    """
    if guild is None:
        return []

    def usable(channel: Any) -> bool:
        try:
            permissions = channel.permissions_for(guild.me)
        except Exception:
            return False
        return permissions.view_channel and permissions.send_messages

    options: dict[int, discord.SelectOption] = {}
    for channel in getattr(guild, "text_channels", None) or []:
        if include_channels and usable(channel):
            options[channel.id] = build_safe_select_option(f"#{channel.name}", str(channel.id))
        if include_threads:
            for thread in channel.threads:
                if thread.archived or thread.locked or not usable(thread):
                    continue
                options[thread.id] = build_safe_select_option(
                    thread.name, str(thread.id), description=f"Thread in #{channel.name}"
                )

    if selected_channel_id is not None and selected_channel_id not in options:
        get_channel_or_thread = getattr(guild, "get_channel_or_thread", None)
        current = get_channel_or_thread(selected_channel_id) if get_channel_or_thread is not None else None
        if current is not None:
            parent = getattr(current, "parent", None)
            # "Currently selected" leads (rather than trails) the parent
            # channel's name specifically so it always survives
            # build_safe_select_option's truncation even when the parent
            # name alone is near Discord's own 100-character channel-name
            # limit -- the identifying detail, not the parent context, is
            # what must never silently disappear.
            description = f"Currently selected · Thread in #{parent.name}" if parent is not None else "Currently selected"
            label = current.name if parent is not None else f"#{current.name}"
            options[selected_channel_id] = build_safe_select_option(
                label, str(selected_channel_id), description=description
            )

    ordered = list(options.values())
    selected_value = str(selected_channel_id) if selected_channel_id is not None else None
    for option in ordered:
        option.default = option.value == selected_value
    if selected_value is not None:
        selected_option = next((option for option in ordered if option.value == selected_value), None)
        if selected_option is not None:
            ordered.remove(selected_option)
            ordered.insert(0, selected_option)
    return cap_select_options(ordered)


def build_admin_channel_overwrites(
    guild: discord.Guild, wash_crew_role_id: Optional[int]
) -> dict:
    """Permission overwrites for a newly created Admin Channel.

    Private to WASH Crew: @everyone is denied View Channel, and WASH's
    own bot member plus the configured WASH Crew role (when known) are
    explicitly granted it. Server administrators are never explicitly
    touched here -- Discord's Administrator permission already bypasses
    channel-specific overwrites entirely, so no special-case is needed
    to preserve their access.
    """
    overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
    }
    if wash_crew_role_id is not None:
        wash_crew_role = guild.get_role(wash_crew_role_id)
        if wash_crew_role is not None:
            overwrites[wash_crew_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    return overwrites


def build_setup_preparation_text() -> str:
    """Build the one-time preparation screen shown before Step 1 of a
    brand-new /setup run (never shown again once a draft is resumed).
    """
    return (
        "**WASH Setup**\n\n"
        "Before you begin, we recommend creating the following Discord roles:\n\n"
        "🛠️ **WASH Crew**\n"
        "Administrators who configure and manage WASH.\n\n"
        "🍿 **Watch Party**\n"
        "Members who participate in watch parties and use WASH's member commands "
        "(`/add`, `/list`, `/stats`, and more). The server owner always has these abilities "
        "too, regardless of role.\n\n"
        "You can create these roles now or later, but having them ready will make setup faster -- "
        "see Discord's own "
        "[Roles and Permissions guide](https://support.discord.com/hc/articles/214836687-Discord-Roles-and-Permissions) "
        "if you've never created one.\n\n"
        "Setup can be resumed later using **Save & Finish Later**, and any existing channels or "
        "threads you already have can be reused wherever WASH asks you to choose a destination."
    )


async def send_setup_preparation_screen(
    interaction: discord.Interaction,
    bot: "WatchPartyBot",
    state: SetupWizardState,
    *,
    requester_id: Optional[int] = None,
) -> None:
    """Show the preparation screen for a brand-new (never-resumed) /setup
    run. Begin Setup proceeds into Step 1; Cancel discards the draft
    start_or_resume() already created, exactly like Cancel Setup on any
    other step.
    """
    setup_wizard_service = bot.setup_wizard_service
    guild_id = state.guild_id

    async def on_begin(begin_interaction: discord.Interaction) -> None:
        await send_setup_wizard_step(begin_interaction, bot, state, edit=True, requester_id=requester_id)

    async def on_cancel(cancel_interaction: discord.Interaction) -> None:
        setup_wizard_service.cancel(guild_id)
        await cancel_interaction.response.edit_message(
            content="**Setup has been cancelled.** No configuration was changed.", view=None
        )

    view = SetupPreparationView(on_begin, on_cancel, requester_id=requester_id)
    await interaction.response.send_message(build_setup_preparation_text(), view=view, ephemeral=True)


def _log_setup_wizard_view_if_unsafe(
    view: discord.ui.View, *, state: "SetupWizardState", requester_id: Optional[int]
) -> None:
    """Last checkpoint before a /setup view is sent to Discord: walk the
    exact outgoing payload (view.to_components(), not the Python
    SelectOption objects) and, only if it would actually violate
    Discord's component limits, log full per-option diagnostic detail --
    row/component/option index, label/description/value (repr, Python
    len(), and the UTF-16 length Discord itself enforces -- see
    discord_ui_limits.py), and which option is `default`.

    A no-op in the healthy case (every /setup render, always) so this
    stays permanent, low-noise logging rather than a temporary dump --
    discord.py itself performs zero client-side length validation, so
    without this, an oversized field only ever surfaces as a bare
    HTTPException 400 (error 50035) with no indication of which option,
    field, or generated string caused it, often after an irreversible
    action (e.g. creating a thread) already happened.
    """
    violations = find_oversized_view_component_fields(view)
    if not violations:
        return
    logger.error(
        "Setup Wizard view about to violate Discord's component limits -- step=%s guild_id=%s "
        "watch_destination_channel_id=%s admin_channel_id=%s home_channel_id=%s view=%s "
        "requester_id=%s violations=%s",
        state.current_step,
        state.guild_id,
        state.draft.watch_destination_channel_id,
        state.draft.admin_channel_id,
        state.draft.home_channel_id,
        type(view).__name__,
        requester_id,
        violations,
    )


async def send_setup_wizard_step(
    interaction: discord.Interaction,
    bot: "WatchPartyBot",
    state: SetupWizardState,
    *,
    edit: bool,
    error_message: Optional[str] = None,
    requester_id: Optional[int] = None,
) -> None:
    """Render whichever step `state.current_step` points to.

    Every step's callbacks recursively call this again with the next
    state to render the following step in the SAME ephemeral message
    (via edit_message) -- see each `on_*` closure below. This is the one
    place that knows how to turn a SetupWizardState into a Discord
    message; the service layer never touches discord.ui objects.

    requester_id is threaded through unchanged on every recursive call
    (never re-derived from whichever interaction just fired) so it keeps
    reflecting whoever is actually driving this particular wizard render
    -- see SetupWizardStepView's own docstring for why that's the right
    scope, not "the one person who ever ran /setup for this guild."
    """
    setup_wizard_service = bot.setup_wizard_service
    suggestion_service = bot.suggestion_service
    guild_id = state.guild_id

    async def on_cancel(cancel_interaction: discord.Interaction) -> None:
        setup_wizard_service.cancel(guild_id)
        await cancel_interaction.response.edit_message(
            content="**Setup has been cancelled.** No configuration was changed.", view=None
        )

    async def on_back(back_interaction: discord.Interaction) -> None:
        updated = setup_wizard_service.go_back(state)
        await send_setup_wizard_step(back_interaction, bot, updated, edit=True, requester_id=requester_id)

    async def on_save_for_later(save_later_interaction: discord.Interaction) -> None:
        setup_wizard_service.save_for_later(state)

        async def on_continue(resume_interaction: discord.Interaction) -> None:
            await send_setup_wizard_step(resume_interaction, bot, state, edit=True, requester_id=requester_id)

        await save_later_interaction.response.edit_message(
            content=(
                "**Setup progress saved.**\n\n"
                "Nothing further was changed. Press **Continue Setup** below to resume right away, "
                "or run `/setup` again at any time to resume exactly where you left off."
            ),
            view=SetupProgressSavedView(on_continue, requester_id=requester_id),
        )

    header = build_setup_step_header(state)
    body = header if not error_message else f"{header}\n\n⚠ {error_message}"
    step = state.current_step
    view: discord.ui.View

    if step == SetupWizardStep.WASH_CREW_ROLE:

        async def on_confirm(confirm_interaction: discord.Interaction, role_id: Optional[int]) -> None:
            if role_id is None:
                await send_setup_wizard_step(
                    confirm_interaction,
                    bot,
                    state,
                    edit=True,
                    error_message="Select a WASH Crew role before continuing.",
                    requester_id=requester_id,
                )
                return
            updated = setup_wizard_service.set_wash_crew_role(state, role_id)
            await send_setup_wizard_step(confirm_interaction, bot, updated, edit=True, requester_id=requester_id)

        view = WashCrewRoleStepView(on_confirm, on_save_for_later, on_cancel, requester_id=requester_id)
        body += (
            "\n\nSelect the WASH Crew role, then press **Save & Continue**.\n\n"
            "🛠️ **WASH Crew** can:\n"
            "- Configure WASH\n"
            "- Manage collections\n"
            "- Review suggestions\n"
            "- Start votes\n"
            "- Perform administrative actions\n\n"
            "Create this role in Server Settings → Roles if it doesn't exist yet -- see Discord's own "
            "[Roles and Permissions guide](https://support.discord.com/hc/articles/214836687-Discord-Roles-and-Permissions)."
        )

    elif step == SetupWizardStep.WATCH_PARTY_ROLE:

        async def on_confirm(
            confirm_interaction: discord.Interaction, role_id: Optional[int], join_mode: JoinMode
        ) -> None:
            updated = setup_wizard_service.set_watch_party_role(state, role_id, join_mode)
            await send_setup_wizard_step(confirm_interaction, bot, updated, edit=True, requester_id=requester_id)

        view = WatchPartyRoleStepView(on_confirm, on_back, on_save_for_later, on_cancel, requester_id=requester_id)
        body += (
            "\n\nSelect the Watch Party role, choose a join mode, then press Continue. Until this role "
            "is configured, only WASH Crew can use WASH's member commands.\n\n"
            "🍿 **Watch Party** members can:\n"
            "- Submit suggestions\n"
            "- Browse collections\n"
            "- Vote\n"
            "- Participate in movie nights\n\n"
            "(The server owner always has these abilities too, regardless of role.) Create this role "
            "in Server Settings → Roles if it doesn't exist yet -- see Discord's own "
            "[Roles and Permissions guide](https://support.discord.com/hc/articles/214836687-Discord-Roles-and-Permissions)."
        )

    elif step == SetupWizardStep.ADMIN_CHANNEL:

        admin_channel_options = build_channel_destination_options(
            interaction.guild, selected_channel_id=state.draft.admin_channel_id
        )

        async def on_confirm(confirm_interaction: discord.Interaction, channel_id: Optional[int]) -> None:
            if channel_id is None:
                await send_setup_wizard_step(
                    confirm_interaction,
                    bot,
                    state,
                    edit=True,
                    error_message="Select a channel before continuing, or use Create New Channel or Skip for Now.",
                    requester_id=requester_id,
                )
                return
            updated = setup_wizard_service.set_admin_channel(state, channel_id)
            await send_setup_wizard_step(confirm_interaction, bot, updated, edit=True, requester_id=requester_id)

        async def on_skip(skip_interaction: discord.Interaction) -> None:
            updated = setup_wizard_service.skip_admin_channel(state)
            await send_setup_wizard_step(skip_interaction, bot, updated, edit=True, requester_id=requester_id)

        async def on_create_new(create_interaction: discord.Interaction) -> None:
            async def on_name_submit(modal_interaction: discord.Interaction, channel_name: str) -> None:
                if modal_interaction.guild is None:
                    await modal_interaction.response.edit_message(
                        content=body + "\n\n⚠ That server is no longer available. Choose another option.",
                        view=AdminChannelStepView(
                            admin_channel_options, on_confirm, on_create_new, on_skip, on_back, on_save_for_later,
                            on_cancel, fallback_channel_id=state.draft.admin_channel_id, requester_id=requester_id,
                        ),
                    )
                    return
                try:
                    overwrites = build_admin_channel_overwrites(modal_interaction.guild, state.draft.wash_crew_role_id)
                    channel = await modal_interaction.guild.create_text_channel(
                        name=channel_name,
                        overwrites=overwrites,
                        topic="Private WASH Crew administrative channel -- not visible to other members.",
                    )
                except (discord.Forbidden, discord.HTTPException) as exc:
                    logger.warning("Could not create Admin Channel %r", channel_name, exc_info=True)
                    await modal_interaction.response.edit_message(
                        content=body + f"\n\n⚠ {describe_channel_creation_failure(exc)}",
                        view=AdminChannelStepView(
                            build_channel_destination_options(
                                modal_interaction.guild, selected_channel_id=state.draft.admin_channel_id
                            ),
                            on_confirm, on_create_new, on_skip, on_back, on_save_for_later, on_cancel,
                            fallback_channel_id=state.draft.admin_channel_id, requester_id=requester_id,
                        ),
                    )
                    return
                updated = setup_wizard_service.set_admin_channel(state, channel.id)
                await send_setup_wizard_step(modal_interaction, bot, updated, edit=True, requester_id=requester_id)

            await create_interaction.response.send_modal(AdminChannelNameModal(on_name_submit))

        view = AdminChannelStepView(
            admin_channel_options, on_confirm, on_create_new, on_skip, on_back, on_save_for_later, on_cancel,
            fallback_channel_id=state.draft.admin_channel_id, requester_id=requester_id,
        )
        body += (
            "\n\nSelect the channel where Approval-Required membership requests should be posted for "
            "WASH Crew, then press **Save & Continue** -- or create a new private channel for it, or "
            "skip for now. A newly created channel is visible only to WASH Crew -- it's meant for "
            "private administrative activity, not general discussion.\n\n"
            "🔒 Private channels will appear in the channel selector once WASH has permission to view "
            "them. Grant WASH **View Channel** and **Send Messages** on the channel you want, then "
            "reopen this step (or run `/setup` again) to see it listed. If WASH also assigns server "
            "roles, its own role must be positioned above those roles in the server's role hierarchy."
        )

    elif step == SetupWizardStep.HOME_CHANNEL:

        home_channel_options = build_channel_destination_options(
            interaction.guild, include_threads=False, selected_channel_id=state.draft.home_channel_id
        )

        async def on_confirm(confirm_interaction: discord.Interaction, channel_id: Optional[int]) -> None:
            if channel_id is None:
                await send_setup_wizard_step(
                    confirm_interaction,
                    bot,
                    state,
                    edit=True,
                    error_message="Select a channel before continuing, or use Create New Channel.",
                    requester_id=requester_id,
                )
                return
            updated = setup_wizard_service.set_home_channel(state, channel_id)
            await send_setup_wizard_step(confirm_interaction, bot, updated, edit=True, requester_id=requester_id)

        async def on_create_new(create_interaction: discord.Interaction) -> None:
            async def on_name_submit(modal_interaction: discord.Interaction, channel_name: str) -> None:
                if modal_interaction.guild is None:
                    await modal_interaction.response.edit_message(
                        content=body + "\n\n⚠ That server is no longer available. Choose another option.",
                        view=HomeChannelStepView(
                            home_channel_options, on_confirm, on_create_new, on_back, on_save_for_later, on_cancel,
                            fallback_channel_id=state.draft.home_channel_id, requester_id=requester_id,
                        ),
                    )
                    return
                try:
                    channel = await modal_interaction.guild.create_text_channel(name=channel_name)
                except (discord.Forbidden, discord.HTTPException) as exc:
                    logger.warning("Could not create Home Channel %r", channel_name, exc_info=True)
                    await modal_interaction.response.edit_message(
                        content=body + f"\n\n⚠ {describe_channel_creation_failure(exc)}",
                        view=HomeChannelStepView(
                            build_channel_destination_options(
                                modal_interaction.guild, include_threads=False,
                                selected_channel_id=state.draft.home_channel_id,
                            ),
                            on_confirm, on_create_new, on_back, on_save_for_later, on_cancel,
                            fallback_channel_id=state.draft.home_channel_id, requester_id=requester_id,
                        ),
                    )
                    return
                updated = setup_wizard_service.set_home_channel(state, channel.id)
                await send_setup_wizard_step(modal_interaction, bot, updated, edit=True, requester_id=requester_id)

            await create_interaction.response.send_modal(HomeChannelNameModal(on_name_submit))

        view = HomeChannelStepView(
            home_channel_options, on_confirm, on_create_new, on_back, on_save_for_later, on_cancel,
            fallback_channel_id=state.draft.home_channel_id, requester_id=requester_id,
        )
        body += (
            "\n\nWhere should WASH create its home? Every collection's suggestion thread (and, by "
            "default, the Watched Item Archive thread) is created as a sibling thread under this "
            "one channel. Select an existing text channel and press **Save & Continue**, or create "
            "a new one."
        )

    elif step == SetupWizardStep.SUGGESTION_DATABASE:

        async def show_suggestion_database_choice(choice_interaction: discord.Interaction) -> None:
            """Re-render this step's own top-level choice screen -- used
            as Back from every nested sub-screen (Collection Type,
            Select Existing, Import Existing Notice) so backing out of
            one of those returns here, not further back to Admin Channel.
            """
            await choice_interaction.response.edit_message(
                content=body + "\n\nSelect an existing collection or create a new one.",
                view=SuggestionDatabaseChoiceView(
                    on_select_existing, show_collection_type_choice, on_back, on_save_for_later, on_cancel,
                    requester_id=requester_id,
                ),
            )

        async def on_select_existing(choice_interaction: discord.Interaction) -> None:
            databases = [
                (
                    d.database_id,
                    format_collection_display(
                        _resolve_collection_name(
                            suggestion_service, d, choice_interaction.guild, bot.suggestion_database_configuration_repository
                        )
                    ),
                )
                for d in suggestion_service.list_databases(guild_id)
            ]
            if not databases:
                await choice_interaction.response.edit_message(
                    content=body + "\n\nNo collections exist in this server yet. Choose Create New instead.",
                    view=SuggestionDatabaseChoiceView(
                        on_select_existing, on_create_new, on_back, on_save_for_later, on_cancel,
                        requester_id=requester_id,
                    ),
                )
                return

            async def on_database_selected(select_interaction: discord.Interaction, database_id: int) -> None:
                updated, message = setup_wizard_service.select_existing_database(
                    state, database_id, guild_id=guild_id
                )
                await send_setup_wizard_step(select_interaction, bot, updated, edit=True, requester_id=requester_id)

            await choice_interaction.response.edit_message(
                content=body + "\n\nChoose a collection.",
                view=ExistingDatabaseSelectView(
                    databases, on_database_selected, show_suggestion_database_choice, on_save_for_later, on_cancel,
                    requester_id=requester_id,
                ),
            )

        async def show_collection_type_choice(type_interaction: discord.Interaction) -> None:
            await type_interaction.response.edit_message(
                content=body + "\n\nWhat type of collection would you like to create?",
                view=CollectionTypeChoiceView(
                    on_movies, on_tv_shows, on_special_collection, on_custom, on_import_existing,
                    show_suggestion_database_choice, on_save_for_later, on_cancel,
                    requester_id=requester_id,
                ),
            )

        async def create_collection_thread(interaction_for_edit: discord.Interaction, name: str) -> None:
            """Create the collection's suggestion thread as a sibling
            under WASH's home channel, then create the collection itself
            on it (Requirement 5: "Collections should default to
            threads"). No further destination choice is offered -- the
            home channel already answers "where do things go."
            """
            home_channel = (
                interaction_for_edit.guild.get_channel(state.draft.home_channel_id)
                if interaction_for_edit.guild is not None and state.draft.home_channel_id is not None
                else None
            )
            if home_channel is None:
                await interaction_for_edit.response.edit_message(
                    content=body + "\n\n⚠ WASH's home channel is no longer available. Go back and choose another one.",
                    view=CollectionTypeChoiceView(
                        on_movies, on_tv_shows, on_special_collection, on_custom, on_import_existing,
                        show_suggestion_database_choice, on_save_for_later, on_cancel,
                        requester_id=requester_id,
                    ),
                )
                return
            try:
                thread = await home_channel.create_thread(name=name, type=discord.ChannelType.public_thread)
            except (discord.Forbidden, discord.HTTPException) as exc:
                await interaction_for_edit.response.edit_message(
                    content=body + f"\n\n⚠ Could not create the thread: {exc}",
                    view=CollectionTypeChoiceView(
                        on_movies, on_tv_shows, on_special_collection, on_custom, on_import_existing,
                        show_suggestion_database_choice, on_save_for_later, on_cancel,
                        requester_id=requester_id,
                    ),
                )
                return

            updated, message = setup_wizard_service.create_new_database(
                state, name, thread.id, guild_id=guild_id
            )
            if updated.current_step == SetupWizardStep.SUGGESTION_DATABASE:
                # create_new_database() didn't advance -- creation failed
                # (duplicate name, or Conflict Prevention: this thread
                # somehow already routes to another database). Show why,
                # rather than silently re-rendering the step with no
                # explanation.
                await send_setup_wizard_step(
                    interaction_for_edit, bot, updated, edit=True, error_message=message, requester_id=requester_id
                )
                return
            await send_setup_wizard_step(interaction_for_edit, bot, updated, edit=True, requester_id=requester_id)

        async def on_movies(movies_interaction: discord.Interaction) -> None:
            # Requirement 6 (Setup Wizard Polish): the default thread name
            # is the more descriptive "Movie Suggestions" (matching the
            # recommended tree: Watch Party / Movie Suggestions / TV
            # Suggestions / ...), not the bare collection type "Movies".
            await create_collection_thread(movies_interaction, "Movie Suggestions")

        async def on_tv_shows(tv_shows_interaction: discord.Interaction) -> None:
            await create_collection_thread(tv_shows_interaction, "TV Suggestions")

        async def on_special_collection(special_interaction: discord.Interaction) -> None:
            async def on_name_submit(modal_interaction: discord.Interaction, name: str) -> None:
                await create_collection_thread(modal_interaction, name)

            await special_interaction.response.send_modal(
                CreateDatabaseNameModal(
                    on_name_submit,
                    title="Name Your Special Collection",
                    label="Collection name",
                    placeholder="e.g. Horror Movies, Anime, Documentaries",
                )
            )

        async def on_custom(custom_interaction: discord.Interaction) -> None:
            async def on_name_submit(modal_interaction: discord.Interaction, name: str) -> None:
                await create_collection_thread(modal_interaction, name)

            await custom_interaction.response.send_modal(
                CreateDatabaseNameModal(on_name_submit, title="Name Your Collection", label="Collection name")
            )

        async def on_import_existing(import_interaction: discord.Interaction) -> None:
            await import_interaction.response.edit_message(
                content=(
                    body + "\n\nRun `/maintenance import` in this server to bring in another WASH instance's backup -- "
                    "Discord doesn't allow attaching a file from inside this wizard. Once it's imported, "
                    "come back here and choose **Select Existing** to pick it up."
                ),
                view=ImportExistingDatabaseNoticeView(
                    show_collection_type_choice, on_save_for_later, on_cancel, requester_id=requester_id
                ),
            )

        view = SuggestionDatabaseChoiceView(
            on_select_existing, show_collection_type_choice, on_back, on_save_for_later, on_cancel,
            requester_id=requester_id,
        )
        body += "\n\nSelect an existing collection or create a new one."

    elif step == SetupWizardStep.WATCH_DESTINATION:

        watch_destination_options = build_channel_destination_options(
            interaction.guild, selected_channel_id=state.draft.watch_destination_channel_id
        )

        async def on_confirm(confirm_interaction: discord.Interaction, channel_id: Optional[int]) -> None:
            if channel_id is None:
                await send_setup_wizard_step(
                    confirm_interaction,
                    bot,
                    state,
                    edit=True,
                    error_message="Select a channel before continuing, or use Create New Thread or Skip for Now.",
                    requester_id=requester_id,
                )
                return
            updated = setup_wizard_service.set_watch_destination(state, channel_id)
            await send_setup_wizard_step(confirm_interaction, bot, updated, edit=True, requester_id=requester_id)

        async def on_skip(skip_interaction: discord.Interaction) -> None:
            updated = setup_wizard_service.skip_watch_destination(state)
            await send_setup_wizard_step(skip_interaction, bot, updated, edit=True, requester_id=requester_id)

        async def on_create_thread(create_interaction: discord.Interaction) -> None:
            async def on_name_submit(modal_interaction: discord.Interaction, thread_name: str) -> None:
                # Created as a sibling thread under WASH's home channel --
                # never nested under a collection's own suggestion thread
                # (Requirement 5), matching every collection's own
                # suggestion thread parentage.
                home_channel = (
                    modal_interaction.guild.get_channel(state.draft.home_channel_id)
                    if modal_interaction.guild is not None and state.draft.home_channel_id is not None
                    else None
                )
                if home_channel is None:
                    await modal_interaction.response.edit_message(
                        content=body + "\n\n⚠ WASH's home channel is no longer available. Choose another destination.",
                        view=WatchDestinationStepView(
                            watch_destination_options, on_confirm, on_create_thread, on_skip, on_back,
                            on_save_for_later, on_cancel,
                            fallback_channel_id=state.draft.watch_destination_channel_id, requester_id=requester_id,
                        ),
                    )
                    return
                # Retry safety: if this exact name already exists as an
                # active thread directly under the home channel -- e.g.
                # the previous attempt's thread was created successfully
                # but the response that should have advanced past this
                # step never reached Discord, leaving the user looking at
                # this same modal/button again -- reuse it instead of
                # creating a second, orphaned duplicate.
                existing_thread = next(
                    (
                        candidate
                        for candidate in getattr(home_channel, "threads", None) or []
                        if candidate.name == thread_name and not getattr(candidate, "archived", False)
                    ),
                    None,
                )
                if existing_thread is not None:
                    thread = existing_thread
                else:
                    try:
                        thread = await home_channel.create_thread(
                            name=thread_name, type=discord.ChannelType.public_thread
                        )
                    except (discord.Forbidden, discord.HTTPException) as exc:
                        await modal_interaction.response.edit_message(
                            content=body + f"\n\n⚠ Could not create the thread: {exc}",
                            view=WatchDestinationStepView(
                                build_channel_destination_options(
                                    modal_interaction.guild, selected_channel_id=state.draft.watch_destination_channel_id
                                ),
                                on_confirm, on_create_thread, on_skip, on_back, on_save_for_later, on_cancel,
                                fallback_channel_id=state.draft.watch_destination_channel_id,
                                requester_id=requester_id,
                            ),
                        )
                        return
                updated = setup_wizard_service.set_watch_destination(state, thread.id)
                logger.info(
                    "Setup Wizard: created Watched Item Archive thread (guild_id=%s thread_id=%s "
                    "thread_name=%r parent_channel_id=%s draft_updated=%s next_step=%s)",
                    state.guild_id,
                    thread.id,
                    thread_name,
                    home_channel.id,
                    updated.draft.watch_destination_channel_id == thread.id,
                    updated.current_step,
                )
                try:
                    await send_setup_wizard_step(modal_interaction, bot, updated, edit=True, requester_id=requester_id)
                except Exception:
                    # Narrow wrap around only the render-after-create call
                    # (not thread creation itself, already handled above)
                    # -- the thread was already created irreversibly by
                    # this point, so a render failure here must never be
                    # silently swallowed: re-raised unchanged after
                    # logging, matching every other unhandled-interaction-
                    # error path in this bot.
                    logger.exception(
                        "Setup Wizard: failed to render %s after creating the Watched Item Archive thread "
                        "(guild_id=%s thread_id=%s)",
                        updated.current_step,
                        state.guild_id,
                        thread.id,
                    )
                    raise

            await create_interaction.response.send_modal(
                CreateThreadNameModal(on_name_submit, default="Watched Item Archive")
            )

        view = WatchDestinationStepView(
            watch_destination_options, on_confirm, on_create_thread, on_skip, on_back, on_save_for_later, on_cancel,
            fallback_channel_id=state.draft.watch_destination_channel_id, requester_id=requester_id,
        )
        body += (
            "\n\nChoose where WASH should archive completed watch items. This archive stores Vote "
            "Winners and Retired items together with links back to their suggestion and voting "
            "history. **Create New Thread** (a sibling under WASH's home channel) is recommended, "
            "matching every collection's own suggestion thread -- but you can pick an existing text "
            "channel or thread and press **Save & Continue** instead, or skip for now."
        )

    elif step == SetupWizardStep.VOTING_DEFAULTS:

        voting_defaults_prefill = (
            str(state.draft.voting_candidate_count) if state.draft.voting_candidate_count is not None else "3",
            (
                format_duration_minutes_compact(state.draft.voting_duration_minutes)
                if state.draft.voting_duration_minutes is not None
                else format_duration_minutes_compact(DEFAULT_VOTE_DURATION_MINUTES)
            ),
        )
        default_candidate_selection = (
            state.draft.voting_candidate_selection
            if state.draft.voting_candidate_selection is not None
            else DEFAULT_CANDIDATE_SELECTION_MODE
        )
        default_visibility = (
            state.draft.voting_visibility
            if state.draft.voting_visibility is not None
            else GuildVoteVisibility.VISIBLE
        )

        async def on_configure(
            configure_interaction: discord.Interaction,
            candidate_selection: CandidateSelectionMode,
            visibility: GuildVoteVisibility,
        ) -> None:
            async def on_submit(
                modal_interaction: discord.Interaction,
                candidate_count_text: str,
                duration_text: str,
            ) -> None:
                try:
                    candidate_count = parse_setup_voting_candidate_count(candidate_count_text)
                    duration_minutes = parse_setup_voting_duration_minutes(duration_text)
                except ValueError as exc:
                    await modal_interaction.response.edit_message(
                        content=body + f"\n\n⚠ {exc}",
                        view=VotingDefaultsIntroView(
                            on_configure,
                            on_back,
                            on_save_for_later,
                            on_cancel,
                            default_candidate_selection=candidate_selection,
                            default_visibility=visibility,
                            requester_id=requester_id,
                        ),
                    )
                    return

                updated = setup_wizard_service.set_voting_defaults(
                    state, candidate_count, duration_minutes, visibility, candidate_selection
                )
                await send_setup_wizard_step(modal_interaction, bot, updated, edit=True, requester_id=requester_id)

            await configure_interaction.response.send_modal(
                VotingDefaultsModal(on_submit, defaults=voting_defaults_prefill)
            )

        view = VotingDefaultsIntroView(
            on_configure,
            on_back,
            on_save_for_later,
            on_cancel,
            default_candidate_selection=default_candidate_selection,
            default_visibility=default_visibility,
            requester_id=requester_id,
        )
        body += (
            "\n\nChoose the server's default nominee selection mode and visibility below -- each option "
            "explains its own behavior when opened -- then press **Set Voting Defaults** to configure the "
            f"default candidate count and vote duration. {VOTE_DURATION_CLARIFICATION} {DURATION_FORMAT_HELP_TEXT}"
        )

    elif step == SetupWizardStep.REJECTION_SETTINGS:

        rejection_threshold_prefill = (
            str(state.draft.rejection_threshold)
            if state.draft.rejection_threshold is not None
            else str(DEFAULT_REJECTION_THRESHOLD)
        )

        async def on_enable(configure_interaction: discord.Interaction) -> None:
            async def on_submit(modal_interaction: discord.Interaction, threshold_text: str) -> None:
                try:
                    threshold = parse_setup_rejection_threshold(threshold_text)
                except ValueError as exc:
                    await modal_interaction.response.edit_message(
                        content=body + f"\n\n⚠ {exc}",
                        view=RejectionSettingsChoiceView(
                            on_enable, on_disable, on_back, on_save_for_later, on_cancel, requester_id=requester_id
                        ),
                    )
                    return

                updated = setup_wizard_service.enable_rejection_settings(state, threshold)
                await send_setup_wizard_step(modal_interaction, bot, updated, edit=True, requester_id=requester_id)

            await configure_interaction.response.send_modal(
                RejectionThresholdModal(on_submit, default=rejection_threshold_prefill)
            )

        async def on_disable(disable_interaction: discord.Interaction) -> None:
            updated = setup_wizard_service.disable_rejection_settings(state)
            await send_setup_wizard_step(disable_interaction, bot, updated, edit=True, requester_id=requester_id)

        view = RejectionSettingsChoiceView(
            on_enable, on_disable, on_back, on_save_for_later, on_cancel, requester_id=requester_id
        )
        body += (
            f"\n\n{describe_rejection_threshold(int(rejection_threshold_prefill))} This system can be "
            "disabled entirely for this collection."
        )

    elif step == SetupWizardStep.REMINDER_DEFAULTS:

        reminder_minutes_prefill = (
            format_duration_minutes_compact(state.draft.reminder_minutes_before_close)
            if state.draft.reminder_minutes_before_close is not None
            else "1d"
        )

        async def on_enable(configure_interaction: discord.Interaction) -> None:
            async def on_submit(modal_interaction: discord.Interaction, minutes_text: str) -> None:
                try:
                    minutes_before_close = parse_setup_reminder_minutes_before_close(minutes_text)
                except ValueError as exc:
                    await modal_interaction.response.edit_message(
                        content=body + f"\n\n⚠ {exc}",
                        view=ModalStepIntroView(
                            on_enable,
                            on_back,
                            on_save_for_later,
                            on_cancel,
                            button_label="Set Reminder Defaults",
                            custom_id="wpm_setup_reminder_defaults_configure",
                            requester_id=requester_id,
                        ),
                    )
                    return

                updated = setup_wizard_service.enable_vote_ending_reminder(state, minutes_before_close)
                await send_setup_wizard_step(modal_interaction, bot, updated, edit=True, requester_id=requester_id)

            await configure_interaction.response.send_modal(
                ReminderDefaultsModal(on_submit, defaults=reminder_minutes_prefill)
            )

        async def on_disable(disable_interaction: discord.Interaction) -> None:
            updated = setup_wizard_service.disable_vote_ending_reminder(state)
            await send_setup_wizard_step(disable_interaction, bot, updated, edit=True, requester_id=requester_id)

        view = ReminderDefaultsChoiceView(
            on_enable, on_disable, on_back, on_save_for_later, on_cancel, requester_id=requester_id
        )
        body += (
            "\n\nShould WASH send a reminder shortly before a voting round closes? Choose below, "
            f"then -- if enabled -- set how long before close it's sent. {DURATION_FORMAT_HELP_TEXT}"
        )

    elif step == SetupWizardStep.BACKUP_DEFAULTS:

        backup_defaults_prefill = (
            str(state.draft.backup_interval_days) if state.draft.backup_interval_days is not None else "1",
            str(state.draft.backup_retention_count) if state.draft.backup_retention_count is not None else "30",
        )

        async def on_configure(configure_interaction: discord.Interaction) -> None:
            async def on_submit(
                modal_interaction: discord.Interaction, interval_text: str, retention_text: str
            ) -> None:
                try:
                    interval_days = parse_setup_backup_interval_days(interval_text)
                    retention_count = parse_setup_backup_retention_count(retention_text)
                except ValueError as exc:
                    await modal_interaction.response.edit_message(
                        content=body + f"\n\n⚠ {exc}",
                        view=ModalStepIntroView(
                            on_configure,
                            on_back,
                            on_save_for_later,
                            on_cancel,
                            button_label="Set Backup Defaults",
                            custom_id="wpm_setup_backup_defaults_configure",
                            requester_id=requester_id,
                        ),
                    )
                    return

                updated = setup_wizard_service.enable_automatic_backups(state, interval_days, retention_count)
                await send_setup_wizard_step(modal_interaction, bot, updated, edit=True, requester_id=requester_id)

            await configure_interaction.response.send_modal(
                BackupDefaultsModal(on_submit, defaults=backup_defaults_prefill)
            )

        async def on_disable(disable_interaction: discord.Interaction) -> None:
            updated = setup_wizard_service.disable_automatic_backups(state)
            await send_setup_wizard_step(disable_interaction, bot, updated, edit=True, requester_id=requester_id)

        view = BackupDefaultsChoiceView(
            on_configure, on_disable, on_back, on_save_for_later, on_cancel, requester_id=requester_id
        )
        body += (
            "\n\nAutomatic backups periodically save a snapshot of WASH's data on the schedule you "
            "configure. Manual `/maintenance backup` always remains available either way, and existing backups are "
            f"never deleted by disabling this. {BACKUP_SCOPE_EXPLANATION}"
        )

    else:  # SetupWizardStep.REVIEW

        async def on_save(save_interaction: discord.Interaction) -> None:
            guild = save_interaction.guild
            guild_name = guild.name if guild is not None else ""
            result = setup_wizard_service.finalize(state, guild_id, guild_name, guild)
            if not result.success:
                # result.issues is only populated for validation failures
                # (a bad channel/role, etc.) -- an internal persistence
                # error (result.issues empty) has no single failing step,
                # so it's shown on Review itself with result.message,
                # rather than silently redirecting past a step the user
                # never actually got wrong. Either way, nothing was saved
                # (see SetupWizardService.finalize's own docstring), so
                # the wizard stays open with every entered value intact
                # and the Save button still wired to retry.
                if result.issues:
                    issue_lines = "\n".join(f"- {issue.step.value}: {issue.message}" for issue in result.issues)
                    failed_step = result.issues[0].step
                    error_message = f"Setup could not be saved:\n{issue_lines}"
                else:
                    failed_step = SetupWizardStep.REVIEW
                    error_message = result.message
                # return_to=REVIEW: fixing this one failing step must
                # return straight to Review, not force every later step
                # (already answered once) to be walked through again.
                redirected = setup_wizard_service.go_to_step(
                    state, failed_step, return_to=SetupWizardStep.REVIEW
                )
                await send_setup_wizard_step(
                    save_interaction,
                    bot,
                    redirected,
                    edit=True,
                    error_message=error_message,
                    requester_id=requester_id,
                )
                return

            # Persistence has already succeeded at this point -- show the
            # completion summary before attempting any further, purely
            # best-effort follow-up, so a failure in either one below can
            # never be mistaken for setup itself having failed.
            summary = build_setup_completion_summary(result.configuration, state.draft)
            await save_interaction.response.edit_message(content=summary, view=None)

            try:
                bot.apply_role_configuration(
                    result.configuration.wash_crew_role_id, result.configuration.watch_party_role.role_id
                )
            except Exception:
                logger.exception(
                    "Error applying role configuration for guild %s after setup", guild_id
                )
            try:
                await reconcile_automatic_backup_schedule(
                    bot.scheduler_host.scheduler_service,
                    guild_id,
                    guild_configuration_repository=bot.guild_configuration_repository,
                )
            except Exception:
                logger.exception(
                    "Error reconciling automatic backup schedule for guild %s after setup", guild_id
                )

        async def on_edit_section(select_interaction: discord.Interaction, step_value: str) -> None:
            target_step = SetupWizardStep(step_value)
            # return_to=REVIEW: editing one section from Review must
            # return there afterward, not walk forward through every
            # later step (already answered) again.
            updated = setup_wizard_service.go_to_step(state, target_step, return_to=SetupWizardStep.REVIEW)
            await send_setup_wizard_step(select_interaction, bot, updated, edit=True, requester_id=requester_id)

        review_lines = setup_wizard_service.build_review_lines(state)
        body += "\n\n" + "\n".join(review_lines)
        view = ReviewStepView(
            section_options=[
                (s.value, SETUP_WIZARD_STEP_TITLES[s]) for s in SETUP_WIZARD_STEP_ORDER if s != SetupWizardStep.REVIEW
            ],
            on_save=on_save,
            on_edit_section=on_edit_section,
            on_back=on_back,
            on_cancel=on_cancel,
            requester_id=requester_id,
        )

    _log_setup_wizard_view_if_unsafe(view, state=state, requester_id=requester_id)
    if edit:
        await interaction.response.edit_message(content=body, view=view)
    else:
        await interaction.response.send_message(body, view=view, ephemeral=True)


# --- FR-029: /config -------------------------------------------------------------------


def build_config_summary_body(config_service: ConfigService, guild_id: int, guild: GuildLookup) -> str:
    """Build /config's main-menu summary text: one numbered line per
    section, using the same step numbers as the Setup Wizard's own
    walkthrough (Consistent Step Numbering) -- CONFIG_SECTION_ORDER is
    /config's own equivalent of SETUP_WIZARD_STEP_ORDER, so numbering it
    the same way (position in that tuple, 1-based) can never drift from
    /setup's own "Step N of M" numbering for the sections both share.
    """
    lines = config_service.build_summary_lines(guild_id, guild)
    numbered_lines = [f"{index}. {line}" for index, line in enumerate(lines, start=1)]
    header = "**WASH Configuration**"
    if any(line.startswith("⚠️") for line in lines):
        header += "\n\n⚠️ Some WASH settings are not yet configured. Review the items marked below."
    return header + "\n\n" + "\n".join(numbered_lines)


def build_config_section_options(
    sections: Tuple[ConfigSection, ...] = CONFIG_SECTION_ORDER,
) -> List[Tuple[str, str]]:
    """Build /config's section-picker dropdown options, each numbered with
    its step number in CONFIG_SECTION_ORDER (Consistent Step Numbering) --
    the same numbering build_config_summary_body's lines use, so the
    summary above this dropdown and the dropdown itself never disagree.
    """
    return [
        (section.value, f"{index}. {CONFIG_SECTION_TITLES[section]}")
        for index, section in enumerate(sections, start=1)
    ]


async def send_config_main_menu(
    interaction: discord.Interaction, bot: "WatchPartyBot", guild_id: int, *, edit: bool
) -> None:
    """Render /config's main menu: a live configuration summary plus a
    "choose a section to edit" dropdown.
    """
    config_service = bot.config_service
    body = build_config_summary_body(config_service, guild_id, interaction.guild)

    async def on_section_chosen(select_interaction: discord.Interaction, section_value: str) -> None:
        await send_config_section(select_interaction, bot, guild_id, ConfigSection(section_value), edit=True)

    async def on_done(done_interaction: discord.Interaction) -> None:
        await done_interaction.response.edit_message(
            content="**Configuration closed.**\n\nRun `/config` again at any time to make further changes.",
            view=None,
        )

    # /config Main Menu Cleanup: no descriptions here -- the main menu is
    # a clean numbered list of section names only. Explanatory text
    # (e.g. Visibility's Blind/Visible explanation) belongs on the
    # selected section's own screen instead (see
    # send_config_voting_defaults_screen).
    view = ConfigMainMenuView(build_config_section_options(), on_section_chosen, on_done)

    if edit:
        await interaction.response.edit_message(content=body, view=view)
    else:
        await interaction.response.send_message(body, view=view, ephemeral=True)


async def send_config_result(
    interaction: discord.Interaction, bot: "WatchPartyBot", guild_id: int, result: "ConfigUpdateResult"
) -> None:
    """Show a section's save outcome (success or failure) with a Back to
    Menu button -- /config's "confirm what changed, then return to the
    main menu" contract, for both successful and rejected changes.
    """

    async def on_back(back_interaction: discord.Interaction) -> None:
        await send_config_main_menu(back_interaction, bot, guild_id, edit=True)

    prefix = "" if result.success else "⚠ "
    await interaction.response.edit_message(content=f"{prefix}{result.message}", view=BackToMenuOnlyView(on_back))


async def send_config_section(
    interaction: discord.Interaction,
    bot: "WatchPartyBot",
    guild_id: int,
    section: ConfigSection,
    *,
    edit: bool,
    error_message: Optional[str] = None,
) -> None:
    """Render whichever section of /config `section` points to.

    Voting/Reminder/Backup Defaults are modal-based and must be the
    direct response to a not-yet-answered interaction, so they're
    dispatched to their own send_config_*_modal() helpers instead of
    following this function's generic edit/send tail.
    """
    config_service = bot.config_service

    async def on_back(back_interaction: discord.Interaction) -> None:
        await send_config_main_menu(back_interaction, bot, guild_id, edit=True)

    if section == ConfigSection.VOTING_DEFAULTS:
        await send_config_voting_defaults_modal(interaction, bot, guild_id, on_back)
        return
    if section == ConfigSection.REJECTION_SETTINGS:
        await send_config_rejection_settings(interaction, bot, guild_id, on_back, edit=edit)
        return
    if section == ConfigSection.REMINDER_DEFAULTS:
        await send_config_reminder_defaults_modal(interaction, bot, guild_id, on_back)
        return
    if section == ConfigSection.BACKUP_DEFAULTS:
        await send_config_backup_defaults_modal(interaction, bot, guild_id, on_back)
        return
    if section == ConfigSection.ELIGIBLE_POOL_WARNING:
        await send_config_eligible_pool_warning_section(interaction, bot, guild_id, on_back, edit=edit)
        return

    title = CONFIG_SECTION_TITLES[section]
    summary_lines = config_service.build_summary_lines(guild_id, interaction.guild)
    current_value_line = summary_lines[CONFIG_SECTION_ORDER.index(section)]
    header = f"**WASH Configuration -- {title}**\n\nCurrent value -- {current_value_line}"
    body = header if not error_message else f"{header}\n\n⚠ {error_message}"

    if section == ConfigSection.WASH_CREW_ROLE:

        async def on_select(select_interaction: discord.Interaction, role_id: Optional[int]) -> None:
            await handle_config_wash_crew_role_selected(select_interaction, bot, guild_id, role_id)

        view = ConfigRoleSectionView(
            on_select,
            on_back,
            custom_id="wpm_config_wash_crew_role_select",
            placeholder="Choose the new WASH Crew role",
            min_values=1,
        )
        body += "\n\nSelect the Discord role that should control administrative access to WASH."

    elif section == ConfigSection.WATCH_PARTY_ROLE:

        async def on_select(select_interaction: discord.Interaction, role_id: Optional[int]) -> None:
            result = config_service.set_watch_party_role(guild_id, role_id, select_interaction.guild)
            await send_config_result(select_interaction, bot, guild_id, result)

        view = ConfigRoleSectionView(
            on_select,
            on_back,
            custom_id="wpm_config_watch_party_role_select",
            placeholder="Choose the new Watch Party role (optional)",
            min_values=0,
        )
        body += "\n\nSelect the Watch Party role, or leave blank to clear it."

    elif section == ConfigSection.WATCH_PARTY_JOIN_MODE:

        async def on_select(select_interaction: discord.Interaction, join_mode: JoinMode) -> None:
            result = config_service.set_watch_party_join_mode(guild_id, join_mode)
            await send_config_result(select_interaction, bot, guild_id, result)

        view = ConfigJoinModeSectionView(on_select, on_back)
        body += "\n\nSelect the Watch Party role's join mode."

    elif section == ConfigSection.MANAGE_COLLECTIONS:
        await send_config_manage_databases(interaction, bot, guild_id, on_back, edit=edit)
        return

    elif section == ConfigSection.ADMIN_CHANNEL:

        async def on_select(select_interaction: discord.Interaction, channel_id: int) -> None:
            result = config_service.set_admin_channel(guild_id, channel_id, select_interaction.guild)
            await send_config_result(select_interaction, bot, guild_id, result)

        async def on_skip(skip_interaction: discord.Interaction) -> None:
            result = config_service.clear_admin_channel(guild_id)
            await send_config_result(skip_interaction, bot, guild_id, result)

        current_configuration = config_service.get_configuration(guild_id)
        admin_channel_options = build_channel_destination_options(
            interaction.guild,
            selected_channel_id=current_configuration.channels.admin_channel_id if current_configuration else None,
        )
        view = ConfigAdminChannelSectionView(admin_channel_options, on_select, on_skip, on_back)
        body += (
            "\n\nSelect the channel where Approval-Required membership requests should be "
            "posted for WASH Crew, or clear it."
        )

    elif section == ConfigSection.HOME_CHANNEL:
        current_channel_available = _can_be_new_thread_parent(interaction.channel)

        def home_channel_view() -> ConfigHomeChannelSectionView:
            return ConfigHomeChannelSectionView(
                on_create_new,
                on_use_current,
                on_use_existing,
                on_clear,
                on_back,
                current_channel_available=current_channel_available,
            )

        async def on_use_current(current_interaction: discord.Interaction) -> None:
            if not current_channel_available or interaction.channel is None:
                # Defense in depth -- the button is already disabled for
                # this case, but a stale/cached component could still be
                # clicked.
                await current_interaction.response.edit_message(
                    content=f"{body}\n\n⚠ This option isn't available here. Choose a different option.",
                    view=home_channel_view(),
                )
                return
            result = config_service.set_home_channel(guild_id, interaction.channel.id, current_interaction.guild)
            await send_config_result(current_interaction, bot, guild_id, result)

        async def on_use_existing(existing_interaction: discord.Interaction) -> None:
            async def on_channel_selected(select_interaction: discord.Interaction, channel_id: int) -> None:
                result = config_service.set_home_channel(guild_id, channel_id, select_interaction.guild)
                await send_config_result(select_interaction, bot, guild_id, result)

            await existing_interaction.response.edit_message(
                content=f"{body}\n\nChoose an existing text channel:",
                view=ExistingChannelSelectView(on_channel_selected, on_back),
            )

        async def on_create_new(create_interaction: discord.Interaction) -> None:
            async def on_name_submit(modal_interaction: discord.Interaction, channel_name: str) -> None:
                if modal_interaction.guild is None:
                    await modal_interaction.response.edit_message(
                        content=f"{body}\n\n⚠ That server is no longer available. Choose another option.",
                        view=home_channel_view(),
                    )
                    return
                try:
                    channel = await modal_interaction.guild.create_text_channel(name=channel_name)
                except (discord.Forbidden, discord.HTTPException) as exc:
                    logger.warning("Could not create Home Channel %r via /config", channel_name, exc_info=True)
                    await modal_interaction.response.edit_message(
                        content=f"{body}\n\n⚠ {describe_channel_creation_failure(exc)}",
                        view=home_channel_view(),
                    )
                    return
                result = config_service.set_home_channel(guild_id, channel.id, modal_interaction.guild)
                await send_config_result(modal_interaction, bot, guild_id, result)

            await create_interaction.response.send_modal(HomeChannelNameModal(on_name_submit))

        async def on_clear(clear_interaction: discord.Interaction) -> None:
            result = config_service.clear_home_channel(guild_id)
            await send_config_result(clear_interaction, bot, guild_id, result)

        view = home_channel_view()
        body += (
            "\n\nChoose the channel every collection's suggestion thread (and, by default, the "
            "Watched Item Archive thread) should be created as a sibling under."
        )

    else:  # ConfigSection.WATCH_DESTINATION (guild-wide default)

        async def on_select(select_interaction: discord.Interaction, channel_id: int) -> None:
            result = config_service.set_guild_watch_destination(guild_id, channel_id, select_interaction.guild)
            await send_config_result(select_interaction, bot, guild_id, result)

        async def on_skip(skip_interaction: discord.Interaction) -> None:
            result = config_service.clear_guild_watch_destination(guild_id)
            await send_config_result(skip_interaction, bot, guild_id, result)

        current_configuration = config_service.get_configuration(guild_id)
        watch_destination_options = build_channel_destination_options(
            interaction.guild,
            selected_channel_id=(
                current_configuration.channels.watch_history_channel_id if current_configuration else None
            ),
        )
        view = ConfigWatchDestinationSectionView(watch_destination_options, on_select, on_skip, on_back)
        body += (
            "\n\nChoose the default channel or thread where WASH should archive completed watch "
            "items (Vote Winners and Retired items, together with links back to their suggestion "
            "and voting history) when a collection has no archive of its own configured, or clear "
            "it. Any collection can override this individually via Collections."
        )

    if edit:
        await interaction.response.edit_message(content=body, view=view)
    else:
        await interaction.response.send_message(body, view=view, ephemeral=True)


# --- Manage Databases (replaces "Active Suggestion Database") -----------------------------


async def send_config_manage_databases(
    interaction: discord.Interaction,
    bot: "WatchPartyBot",
    guild_id: int,
    on_back_to_menu: OnBackToMenu,
    *,
    edit: bool = True,
) -> None:
    """Manage Databases section: pick which database to edit, then edit
    that database's own destination/candidate-selection settings
    directly. Replaces the old "Active Suggestion Database" model, which
    required picking exactly one active database before any per-database
    setting could be touched at all.

    edit defaults to True since every production call site reaches this
    by editing /config's existing ephemeral message (matching every other
    section); accepting the flag keeps this consistent with
    send_config_section's own edit/send contract for any caller that
    still needs a fresh message.

    Collections Summary: each collection's own settings menu (one level
    deeper) already shows its current Candidate Selection Mode -- this
    also surfaces it right here, as this picker option's own description,
    so an administrator can compare every collection's mode at a glance
    without opening each one individually. Kept off the main /config
    summary screen itself (avoiding clutter there) per that same
    consistency review.
    """
    databases = bot.suggestion_service.list_databases(guild_id)
    header = f"**WASH Configuration -- {CONFIG_SECTION_TITLES[ConfigSection.MANAGE_COLLECTIONS]}**"

    if not databases:
        body = header + "\n\nNo collections exist in this server yet. Create one with `/database add`."
        view = BackToMenuOnlyView(on_back_to_menu)
        if edit:
            await interaction.response.edit_message(content=body, view=view)
        else:
            await interaction.response.send_message(body, view=view, ephemeral=True)
        return

    async def on_database_selected(select_interaction: discord.Interaction, database_id: int) -> None:
        async def on_back_to_picker(back_interaction: discord.Interaction) -> None:
            await send_config_manage_databases(back_interaction, bot, guild_id, on_back_to_menu)

        await send_config_database_settings_menu(select_interaction, bot, guild_id, database_id, on_back_to_picker)

    options = [
        (
            database.database_id,
            format_collection_display(
                _resolve_collection_name(
                    bot.suggestion_service, database, interaction.guild, bot.suggestion_database_configuration_repository
                )
            ),
        )
        for database in databases
    ]
    candidate_selection_descriptions = {
        database.database_id: (
            "Nominee Selection: "
            + CANDIDATE_SELECTION_DISPLAY_LABELS[
                bot.config_service.get_database_configuration(guild_id, database.database_id).suggestion_rules.candidate_selection
            ]
        )
        for database in databases
    }
    view = ConfigDatabaseSectionView(
        options, on_database_selected, on_back_to_menu, descriptions=candidate_selection_descriptions
    )
    body = header + "\n\nSelect a collection to manage its own destination and nominee selection."
    if edit:
        await interaction.response.edit_message(content=body, view=view)
    else:
        await interaction.response.send_message(body, view=view, ephemeral=True)


async def send_config_database_settings_menu(
    interaction: discord.Interaction,
    bot: "WatchPartyBot",
    guild_id: int,
    database_id: int,
    on_back: OnBackToMenu,
) -> None:
    """One database's settings menu -- pick which of its own settings to edit.

    on_back is called directly (not wrapped further) when Back is
    clicked here or from any of the three setting sub-screens -- the
    caller decides what "back" means. /config's own call site
    (send_config_manage_databases) wraps a callback that re-shows its
    collection picker; /database manage's Edit Collection action (see
    show_database_management_menu) passes one that returns to its own
    per-collection action menu instead -- this menu and its three
    sub-screens are otherwise identical either way, and are never
    duplicated between the two entry points.
    """
    database = bot.suggestion_service.get_database(database_id)
    if database is None or database.guild_id != guild_id:
        await on_back(interaction)
        return

    collection_name = format_collection_display(
        _resolve_collection_name(
            bot.suggestion_service, database, interaction.guild, bot.suggestion_database_configuration_repository
        )
    )
    suggestion_destination = bot.suggestion_service.resolve_collection_channel_id(
        database, bot.suggestion_database_configuration_repository
    )
    watch_destination = bot.config_service.resolve_effective_watch_destination(guild_id, database_id)
    database_configuration = bot.config_service.get_database_configuration(guild_id, database_id)
    candidate_selection_label = CANDIDATE_SELECTION_DISPLAY_LABELS[
        database_configuration.suggestion_rules.candidate_selection
    ]
    rejection_rules = database_configuration.suggestion_rules
    rejection_status = (
        f"Enabled (threshold {rejection_rules.rejection_threshold})"
        if rejection_rules.rejection_enabled
        else "Disabled"
    )

    body = (
        f'**WASH Configuration -- "{collection_name}" Settings**\n\n'
        f"Suggestion Destination: <#{suggestion_destination}>\n"
        f"Watched Item Archive: {f'<#{watch_destination}>' if watch_destination else 'Not configured (no per-collection override or server default set)'}\n"
        f"Nominee Selection: {candidate_selection_label}\n"
        f"I Won't Watch: {rejection_status} "
        f"(edit from its own {CONFIG_SECTION_TITLES[ConfigSection.REJECTION_SETTINGS]} section)"
    )

    async def on_setting_chosen(select_interaction: discord.Interaction, setting: str) -> None:
        if setting == DATABASE_SETTING_SUGGESTION_DESTINATION:
            await send_config_database_suggestion_destination(select_interaction, bot, guild_id, database_id, on_back)
        elif setting == DATABASE_SETTING_WATCH_DESTINATION:
            await send_config_database_watch_destination(select_interaction, bot, guild_id, database_id, on_back)
        else:
            await send_config_database_candidate_selection(select_interaction, bot, guild_id, database_id, on_back)

    view = ConfigDatabaseSettingsMenuView(on_setting_chosen, on_back)
    await interaction.response.edit_message(content=body + "\n\nChoose a setting to edit.", view=view)


async def send_config_database_suggestion_destination(
    interaction: discord.Interaction,
    bot: "WatchPartyBot",
    guild_id: int,
    database_id: int,
    on_back: OnBackToMenu,
) -> None:
    config_service = bot.config_service
    database = bot.suggestion_service.get_database(database_id)
    collection_name = format_collection_display(
        _resolve_collection_name(
            bot.suggestion_service, database, interaction.guild, bot.suggestion_database_configuration_repository
        )
    )
    current = bot.suggestion_service.resolve_collection_channel_id(
        database, bot.suggestion_database_configuration_repository
    )
    body = (
        f'**WASH Configuration -- "{collection_name}" Suggestion Destination**\n\n'
        f"Current value -- <#{current}>\n\n"
        "Every collection must have exactly one dedicated suggestion destination -- choose the "
        "thread new-suggestion confirmation posts should use instead."
    )

    async def on_select(select_interaction: discord.Interaction, channel_id: int) -> None:
        result = config_service.set_database_suggestion_destination(
            guild_id, database_id, channel_id, select_interaction.guild
        )
        await send_config_result(select_interaction, bot, guild_id, result)

    view = ConfigSuggestionDestinationSectionView(on_select, on_back)
    await interaction.response.edit_message(content=body, view=view)


async def send_config_database_watch_destination(
    interaction: discord.Interaction,
    bot: "WatchPartyBot",
    guild_id: int,
    database_id: int,
    on_back: OnBackToMenu,
) -> None:
    config_service = bot.config_service
    database = bot.suggestion_service.get_database(database_id)
    collection_name = format_collection_display(
        _resolve_collection_name(
            bot.suggestion_service, database, interaction.guild, bot.suggestion_database_configuration_repository
        )
    )
    database_configuration = config_service.get_database_configuration(guild_id, database_id)
    current = database_configuration.channels.watch_history_channel_id
    effective = config_service.resolve_effective_watch_destination(guild_id, database_id)
    body = (
        f'**WASH Configuration -- "{collection_name}" Watched Item Archive**\n\n'
        f"This collection's own override -- {f'<#{current}>' if current else 'Not set (using the server default)'}\n"
        f"Currently effective -- {f'<#{effective}>' if effective else 'None'}\n\n"
        "Choose where this collection's completed watch items (Vote Winners and Retired items, "
        "with links back to their suggestion and voting history) should be archived, overriding "
        "the server default, or clear it to go back to using the server default."
    )

    async def on_select(select_interaction: discord.Interaction, channel_id: int) -> None:
        result = config_service.set_database_watch_destination(
            guild_id, database_id, channel_id, select_interaction.guild
        )
        await send_config_result(select_interaction, bot, guild_id, result)

    async def on_clear(clear_interaction: discord.Interaction) -> None:
        result = config_service.clear_database_watch_destination(guild_id, database_id)
        await send_config_result(clear_interaction, bot, guild_id, result)

    database_watch_destination_options = build_channel_destination_options(
        interaction.guild, selected_channel_id=current
    )
    view = ConfigWatchDestinationSectionView(database_watch_destination_options, on_select, on_clear, on_back)
    await interaction.response.edit_message(content=body, view=view)


async def send_config_database_candidate_selection(
    interaction: discord.Interaction,
    bot: "WatchPartyBot",
    guild_id: int,
    database_id: int,
    on_back: OnBackToMenu,
) -> None:
    config_service = bot.config_service
    database = bot.suggestion_service.get_database(database_id)
    collection_name = format_collection_display(
        _resolve_collection_name(
            bot.suggestion_service, database, interaction.guild, bot.suggestion_database_configuration_repository
        )
    )
    database_configuration = config_service.get_database_configuration(guild_id, database_id)
    current = database_configuration.suggestion_rules.candidate_selection
    body = (
        f'**WASH Configuration -- "{collection_name}" Nominee Selection**\n\n'
        f"Current value -- {CANDIDATE_SELECTION_DISPLAY_LABELS[current]}\n\n"
        "Choose the nominee selection mode below, then press Save."
    )

    async def on_save(save_interaction: discord.Interaction, candidate_selection: CandidateSelectionMode) -> None:
        result = config_service.set_database_candidate_selection(guild_id, database_id, candidate_selection)
        await send_config_result(save_interaction, bot, guild_id, result)

    view = ConfigDatabaseCandidateSelectionView(on_save, on_back, default_candidate_selection=current)
    await interaction.response.edit_message(content=body, view=view)


async def handle_config_wash_crew_role_selected(
    interaction: discord.Interaction, bot: "WatchPartyBot", guild_id: int, role_id: Optional[int]
) -> None:
    """Handle a WASH Crew Role selection, warning first if the invoking
    member doesn't have the newly selected role themselves.

    The role change must never leave the server without valid WASH Crew
    access by accident -- if the acting member would lose their own
    access, they must explicitly confirm before it's saved (reusing
    EditVoteConfirmationView, the project's existing confirm/abort
    pattern). Declining leaves the current role untouched.
    """
    config_service = bot.config_service
    member_has_new_role = any(
        getattr(role, "id", None) == role_id for role in getattr(interaction.user, "roles", [])
    )

    if member_has_new_role:
        result = config_service.set_wash_crew_role(guild_id, role_id, interaction.guild)
        if result.success:
            bot.apply_role_configuration(role_id, bot.watch_party_member_role_id)
        await send_config_result(interaction, bot, guild_id, result)
        return

    async def on_confirm(confirm_interaction: discord.Interaction) -> None:
        result = config_service.set_wash_crew_role(guild_id, role_id, confirm_interaction.guild)
        if result.success:
            bot.apply_role_configuration(role_id, bot.watch_party_member_role_id)
        await send_config_result(confirm_interaction, bot, guild_id, result)

    async def on_abort(abort_interaction: discord.Interaction) -> None:
        await send_config_section(
            abort_interaction,
            bot,
            guild_id,
            ConfigSection.WASH_CREW_ROLE,
            edit=True,
            error_message="Change cancelled. The WASH Crew role was not changed.",
        )

    confirmation_view = EditVoteConfirmationView(confirm_label="Change Anyway", on_confirm=on_confirm, on_abort=on_abort)
    await interaction.response.edit_message(
        content=(
            f"**WASH Configuration -- WASH Crew Role**\n\n"
            f"You do not have the selected role (<@&{role_id}>) yourself. If you continue, "
            "you may lose access to WASH Crew commands, including `/config`. Continue anyway?"
        ),
        view=confirmation_view,
    )


async def send_configuration_missing_message(
    interaction: discord.Interaction, on_back: OnBackToMenu, section_title: str
) -> None:
    """Fail safe instead of crashing when a /config section can't find a
    GuildConfiguration to read.

    Should not normally be reachable -- /config's own entry point already
    refuses to proceed unless GuildConfiguration.setup_completed is True
    (see the `config` command) -- but every section that reads
    config_service.get_configuration() a second time, deeper inside its
    own nested callbacks, guards against it having become None or
    incomplete in between (e.g. a concurrent /maintenance reset), rather than
    raising an AttributeError the requester would just see as "this
    command failed" with no explanation.
    """
    await interaction.response.edit_message(
        content=(
            f"**WASH Configuration -- {section_title}**\n\n"
            "⚠ WASH's configuration for this server could not be found or is incomplete. "
            "Run `/setup` to (re)configure WASH, then try again."
        ),
        view=BackToMenuOnlyView(on_back),
    )


def _resolve_config_voting_defaults_modal_defaults(bot: "WatchPartyBot", guild_id: int) -> Tuple[str, str]:
    """Pre-fill the Voting Defaults modal with the guild's current values."""
    configuration = bot.config_service.get_configuration(guild_id)
    if configuration is None:
        return ("3", "1d")
    return (
        str(configuration.voting_defaults.candidate_count),
        format_duration_minutes_compact(configuration.voting_defaults.duration_minutes),
    )


async def send_config_voting_defaults_modal(
    interaction: discord.Interaction, bot: "WatchPartyBot", guild_id: int, on_back: OnBackToMenu, *, edit: bool = True
) -> None:
    """/config's Voting Defaults entry point.

    Mirrors the Setup Wizard's own Voting Defaults step: exposes Nominee
    Selection alongside Vote Visibility, not just visibility/count/
    duration -- previously this screen had no way to change Nominee
    Selection at all, even though Setup presents it as part of the same
    concept (WASH Configuration -- /config Voting Defaults Consistency).

    Nominee Selection remains stored per collection (SuggestionRulesConfig),
    never guild-wide -- that architecture is preserved, not changed. A
    guild with exactly one collection resolves it automatically; a guild
    with more than one requires the administrator to explicitly choose
    which collection's Nominee Selection this screen edits (see
    send_config_voting_defaults_collection_picker) before Nominee
    Selection is shown, so a setting is never silently applied to every
    collection. A guild with no collections at all skips straight to the
    merged screen with no Nominee Selection dropdown to show.
    """
    databases = bot.suggestion_service.list_databases(guild_id)
    if len(databases) > 1:
        await send_config_voting_defaults_collection_picker(interaction, bot, guild_id, on_back, edit=edit)
        return

    database_id = databases[0].database_id if databases else None
    await send_config_voting_defaults_screen(interaction, bot, guild_id, on_back, database_id=database_id, edit=edit)


async def send_config_voting_defaults_collection_picker(
    interaction: discord.Interaction, bot: "WatchPartyBot", guild_id: int, on_back: OnBackToMenu, *, edit: bool
) -> None:
    """Shown only when the guild has more than one collection: choose
    which collection's Nominee Selection the Voting Defaults screen will
    edit. Reuses ConfigDatabaseSectionView unchanged (the same picker
    Manage Collections uses), including its per-option "Nominee
    Selection: <mode>" description, so an administrator can compare
    every collection's current mode before choosing.
    """
    databases = bot.suggestion_service.list_databases(guild_id)
    options = [
        (
            database.database_id,
            format_collection_display(
                _resolve_collection_name(
                    bot.suggestion_service, database, interaction.guild, bot.suggestion_database_configuration_repository
                )
            ),
        )
        for database in databases
    ]
    descriptions = {
        database.database_id: (
            "Nominee Selection: "
            + CANDIDATE_SELECTION_DISPLAY_LABELS[
                bot.config_service.get_database_configuration(
                    guild_id, database.database_id
                ).suggestion_rules.candidate_selection
            ]
        )
        for database in databases
    }

    async def on_database_selected(select_interaction: discord.Interaction, database_id: int) -> None:
        await send_config_voting_defaults_screen(
            select_interaction, bot, guild_id, on_back, database_id=database_id, edit=True
        )

    view = ConfigDatabaseSectionView(options, on_database_selected, on_back, descriptions=descriptions)
    body = (
        "**WASH Configuration -- Voting Defaults**\n\n"
        "Nominee Selection is configured per collection. Choose which collection's Nominee Selection "
        "you'd like to edit here -- Vote Visibility, candidate count, and vote duration remain "
        "guild-wide and apply to every collection regardless of which one you choose."
    )
    if edit:
        await interaction.response.edit_message(content=body, view=view)
    else:
        await interaction.response.send_message(body, view=view, ephemeral=True)


async def send_config_voting_defaults_screen(
    interaction: discord.Interaction,
    bot: "WatchPartyBot",
    guild_id: int,
    on_back: OnBackToMenu,
    *,
    database_id: Optional[int],
    edit: bool,
) -> None:
    """The merged Nominee Selection + Voting Defaults screen: choose
    Nominee Selection (when database_id resolves a collection) and Vote
    Visibility from two dropdowns, then press Set Voting Defaults to open
    the modal for the remaining, flexible fields (candidate count,
    duration) -- Discord modals accept TextInput components only (see
    setup_wizard_view.VotingDefaultsModal's own docstring), so neither
    fixed-choice value can live inside the modal itself.

    Saving persists Nominee Selection to database_id's own collection
    only (config_service.set_database_candidate_selection) and
    Visibility/candidate count/duration to the guild-wide configuration
    (config_service.set_voting_defaults) -- two separate, independently-
    scoped writes from one screen, matching this section's own "clearly
    distinguish collection-specific Nominee Selection from guild-wide
    Voting Defaults" requirement.
    """
    config_service = bot.config_service
    configuration = config_service.get_configuration(guild_id)
    if configuration is None:
        await send_configuration_missing_message(interaction, on_back, "Voting Defaults")
        return
    current_visibility = configuration.voting_defaults.visibility

    current_candidate_selection: Optional[CandidateSelectionMode] = None
    collection_line = ""
    if database_id is not None:
        database_configuration = config_service.get_database_configuration(guild_id, database_id)
        current_candidate_selection = database_configuration.suggestion_rules.candidate_selection
        database = bot.suggestion_service.get_database(database_id)
        collection_name = (
            format_collection_display(
                _resolve_collection_name(
                    bot.suggestion_service, database, interaction.guild, bot.suggestion_database_configuration_repository
                )
            )
            if database is not None
            else None
        )
        if collection_name:
            collection_line = (
                f'Collection-specific setting -- Nominee Selection for "{collection_name}": '
                f"{CANDIDATE_SELECTION_DISPLAY_LABELS[current_candidate_selection]}\n\n"
            )

    async def on_configure(
        configure_interaction: discord.Interaction,
        visibility: GuildVoteVisibility,
        candidate_selection: Optional[CandidateSelectionMode],
    ) -> None:
        async def on_retry(retry_interaction: discord.Interaction) -> None:
            await open_modal(retry_interaction)

        async def on_submit(
            modal_interaction: discord.Interaction,
            candidate_count_text: str,
            duration_text: str,
        ) -> None:
            try:
                candidate_count = parse_setup_voting_candidate_count(candidate_count_text)
                duration_minutes = parse_setup_voting_duration_minutes(duration_text)
            except ValueError as exc:
                view = ConfigModalRetryView(
                    on_retry, on_back, button_label="Try Again", custom_id="wpm_config_voting_defaults_retry"
                )
                await modal_interaction.response.edit_message(
                    content=f"**WASH Configuration -- Voting Defaults**\n\n⚠ {exc}", view=view
                )
                return

            # Guild-wide fields (Visibility, candidate count, duration)
            # save to GuildConfiguration; Nominee Selection -- when a
            # collection is in scope -- saves to that collection only
            # (Do not silently apply a collection-specific setting to
            # every collection).
            result = config_service.set_voting_defaults(guild_id, candidate_count, duration_minutes, visibility)
            if result.success and database_id is not None and candidate_selection is not None:
                candidate_selection_result = config_service.set_database_candidate_selection(
                    guild_id, database_id, candidate_selection
                )
                if not candidate_selection_result.success:
                    result = candidate_selection_result
            await send_config_result(modal_interaction, bot, guild_id, result)

        async def open_modal(modal_trigger_interaction: discord.Interaction) -> None:
            defaults = _resolve_config_voting_defaults_modal_defaults(bot, guild_id)
            await modal_trigger_interaction.response.send_modal(VotingDefaultsModal(on_submit, defaults=defaults))

        await open_modal(configure_interaction)

    view = ConfigVotingDefaultsIntroView(
        on_configure,
        on_back,
        default_visibility=current_visibility,
        default_candidate_selection=current_candidate_selection,
    )
    nominee_selection_line = (
        "Choose this collection's Nominee Selection and the default visibility below, then press "
        if database_id is not None
        else "Choose the default visibility below, then press "
    )
    body = (
        "**WASH Configuration -- Voting Defaults**\n\n"
        + collection_line
        + f"{VISIBILITY_HELP_TEXT_SHORT}\n\n"
        + nominee_selection_line
        + "**Set Voting Defaults** to configure the guild-wide default candidate count and vote duration. "
        + f"{VOTE_DURATION_CLARIFICATION} "
        + DURATION_FORMAT_HELP_TEXT
    )
    if edit:
        await interaction.response.edit_message(content=body, view=view)
    else:
        await interaction.response.send_message(body, view=view, ephemeral=True)


async def send_config_rejection_settings(
    interaction: discord.Interaction, bot: "WatchPartyBot", guild_id: int, on_back: OnBackToMenu, *, edit: bool = True
) -> None:
    """/config's dedicated "I Won't Watch" Settings entry point.

    Its own numbered section, no longer buried inside Voting Defaults --
    mirrors the Setup Wizard's own dedicated step. "I Won't Watch" is
    entirely per-collection (SuggestionRulesConfig), never guild-wide: a
    guild with exactly one collection edits it directly; a guild with
    more than one requires the administrator to explicitly choose which
    collection first (see send_config_rejection_settings_collection_picker),
    so a setting is never silently applied to every collection. A guild
    with no collections at all has nothing to configure yet.
    """
    databases = bot.suggestion_service.list_databases(guild_id)
    if not databases:
        body = (
            f"**WASH Configuration -- {CONFIG_SECTION_TITLES[ConfigSection.REJECTION_SETTINGS]}**\n\n"
            "No collections exist in this server yet. Create one with `/database add`."
        )
        view = BackToMenuOnlyView(on_back)
        if edit:
            await interaction.response.edit_message(content=body, view=view)
        else:
            await interaction.response.send_message(body, view=view, ephemeral=True)
        return

    if len(databases) > 1:
        await send_config_rejection_settings_collection_picker(interaction, bot, guild_id, on_back, edit=edit)
        return

    await send_config_rejection_settings_screen(
        interaction, bot, guild_id, on_back, database_id=databases[0].database_id, edit=edit
    )


async def send_config_rejection_settings_collection_picker(
    interaction: discord.Interaction, bot: "WatchPartyBot", guild_id: int, on_back: OnBackToMenu, *, edit: bool
) -> None:
    """Shown only when the guild has more than one collection: choose
    which collection's "I Won't Watch" Settings to edit. Reuses
    ConfigDatabaseSectionView unchanged, including a per-option status
    description, so an administrator can compare every collection's
    current setting before choosing.
    """
    databases = bot.suggestion_service.list_databases(guild_id)
    options = [
        (
            database.database_id,
            format_collection_display(
                _resolve_collection_name(
                    bot.suggestion_service, database, interaction.guild, bot.suggestion_database_configuration_repository
                )
            ),
        )
        for database in databases
    ]
    descriptions = {}
    for database in databases:
        rules = bot.config_service.get_database_configuration(guild_id, database.database_id).suggestion_rules
        descriptions[database.database_id] = (
            f"I Won't Watch: Enabled (threshold {rules.rejection_threshold})"
            if rules.rejection_enabled
            else "I Won't Watch: Disabled"
        )

    async def on_database_selected(select_interaction: discord.Interaction, database_id: int) -> None:
        await send_config_rejection_settings_screen(
            select_interaction, bot, guild_id, on_back, database_id=database_id, edit=True
        )

    view = ConfigDatabaseSectionView(options, on_database_selected, on_back, descriptions=descriptions)
    body = (
        f"**WASH Configuration -- {CONFIG_SECTION_TITLES[ConfigSection.REJECTION_SETTINGS]}**\n\n"
        "\"I Won't Watch\" is configured per collection. Choose which collection's setting you'd like "
        "to edit."
    )
    if edit:
        await interaction.response.edit_message(content=body, view=view)
    else:
        await interaction.response.send_message(body, view=view, ephemeral=True)


async def send_config_rejection_settings_screen(
    interaction: discord.Interaction,
    bot: "WatchPartyBot",
    guild_id: int,
    on_back: OnBackToMenu,
    *,
    database_id: int,
    edit: bool,
    error_message: Optional[str] = None,
) -> None:
    """One collection's "I Won't Watch" Settings: Enable (opens the
    threshold modal) or Disable, mirroring the Setup Wizard's own
    RejectionSettingsChoiceView shape.
    """
    config_service = bot.config_service
    database = bot.suggestion_service.get_database(database_id)
    collection_name = format_collection_display(
        _resolve_collection_name(
            bot.suggestion_service, database, interaction.guild, bot.suggestion_database_configuration_repository
        )
    )
    rules = config_service.get_database_configuration(guild_id, database_id).suggestion_rules
    status_line = (
        f"Enabled (threshold {rules.rejection_threshold})" if rules.rejection_enabled else "Disabled"
    )

    body = (
        f'**WASH Configuration -- "{collection_name}" I Won\'t Watch Settings**\n\n'
        f"Current value -- {status_line}\n\n"
        f"{describe_rejection_threshold(rules.rejection_threshold)} Choose below whether this is "
        "enabled for this collection."
    )
    if error_message:
        body += f"\n\n⚠ {error_message}"

    async def on_enable(enable_interaction: discord.Interaction) -> None:
        async def on_submit(modal_interaction: discord.Interaction, threshold_text: str) -> None:
            try:
                threshold = parse_setup_rejection_threshold(threshold_text)
            except ValueError as exc:
                await send_config_rejection_settings_screen(
                    modal_interaction, bot, guild_id, on_back, database_id=database_id, edit=True, error_message=str(exc)
                )
                return
            result = config_service.set_database_rejection_settings(
                guild_id, database_id, enabled=True, threshold=threshold
            )
            await send_config_result(modal_interaction, bot, guild_id, result)

        await enable_interaction.response.send_modal(
            ConfigRejectionThresholdModal(on_submit, default=str(rules.rejection_threshold))
        )

    async def on_disable(disable_interaction: discord.Interaction) -> None:
        result = config_service.set_database_rejection_settings(guild_id, database_id, enabled=False)
        await send_config_result(disable_interaction, bot, guild_id, result)

    view = ConfigRejectionSettingsView(on_enable, on_disable, on_back)
    if edit:
        await interaction.response.edit_message(content=body, view=view)
    else:
        await interaction.response.send_message(body, view=view, ephemeral=True)


async def send_config_reminder_defaults_modal(
    interaction: discord.Interaction, bot: "WatchPartyBot", guild_id: int, on_back: OnBackToMenu
) -> None:
    """Enable/disable choice, then -- only if enabled -- the lead-time
    modal, mirroring send_config_backup_defaults_modal's identical shape
    (Fixed-Option UX Audit: "enabled?" is a small fixed set of choices,
    collected via buttons rather than a free-text yes/no modal field).
    """
    config_service = bot.config_service

    async def on_retry(retry_interaction: discord.Interaction) -> None:
        await send_config_reminder_defaults_modal(retry_interaction, bot, guild_id, on_back)

    async def on_enable(configure_interaction: discord.Interaction) -> None:
        async def on_submit(modal_interaction: discord.Interaction, minutes_text: str) -> None:
            try:
                minutes_before_close = parse_setup_reminder_minutes_before_close(minutes_text)
            except ValueError as exc:
                view = ConfigModalRetryView(
                    on_retry, on_back, button_label="Try Again", custom_id="wpm_config_reminder_defaults_retry"
                )
                await modal_interaction.response.edit_message(
                    content=f"**WASH Configuration -- Reminder Defaults**\n\n⚠ {exc}", view=view
                )
                return

            result = config_service.enable_vote_ending_reminder(guild_id, minutes_before_close)
            await send_config_result(modal_interaction, bot, guild_id, result)

        configuration = bot.config_service.get_configuration(guild_id)
        if configuration is None:
            await send_configuration_missing_message(configure_interaction, on_back, "Reminder Defaults")
            return
        minutes_default = format_duration_minutes_compact(
            configuration.notifications.vote.reminder_minutes_before_close
        )
        await configure_interaction.response.send_modal(ReminderDefaultsModal(on_submit, defaults=minutes_default))

    async def on_disable(disable_interaction: discord.Interaction) -> None:
        result = config_service.disable_vote_ending_reminder(guild_id)
        prefix = "" if result.success else "⚠ "
        await disable_interaction.response.edit_message(
            content=f"{prefix}{result.message}", view=BackToMenuOnlyView(on_back)
        )

    await interaction.response.edit_message(
        content="**WASH Configuration -- Reminder Defaults**\n\nShould WASH send a reminder shortly before a "
        "voting round closes? Choose below, then -- if enabled -- set how long before close it's sent. "
        f"{DURATION_FORMAT_HELP_TEXT}",
        view=ConfigReminderDefaultsChoiceView(on_enable, on_disable, on_back),
    )


async def _reconcile_automatic_backup_schedule_after_config_change(
    bot: "WatchPartyBot", guild_id: int
) -> None:
    """Update or remove the guild's scheduled automatic-backup job right
    after /config changes its backup settings, mirroring /setup's own
    finalize()-time reconciliation. Never blocks reporting the config
    change back to WASH Crew even if scheduling itself has a problem.
    """
    try:
        await reconcile_automatic_backup_schedule(
            bot.scheduler_host.scheduler_service,
            guild_id,
            guild_configuration_repository=bot.guild_configuration_repository,
        )
    except Exception:
        logger.exception(
            "Error reconciling automatic backup schedule for guild %s after /config change", guild_id
        )


async def send_config_backup_defaults_modal(
    interaction: discord.Interaction, bot: "WatchPartyBot", guild_id: int, on_back: OnBackToMenu
) -> None:
    config_service = bot.config_service

    async def on_retry(retry_interaction: discord.Interaction) -> None:
        await send_config_backup_defaults_modal(retry_interaction, bot, guild_id, on_back)

    async def on_configure(configure_interaction: discord.Interaction) -> None:
        async def on_submit(
            modal_interaction: discord.Interaction, interval_text: str, retention_text: str
        ) -> None:
            try:
                interval_days = parse_setup_backup_interval_days(interval_text)
                retention_count = parse_setup_backup_retention_count(retention_text)
            except ValueError as exc:
                view = ConfigModalRetryView(
                    on_retry, on_back, button_label="Try Again", custom_id="wpm_config_backup_defaults_retry"
                )
                await modal_interaction.response.edit_message(
                    content=f"**WASH Configuration -- Backup Defaults**\n\n⚠ {exc}", view=view
                )
                return

            result = config_service.enable_automatic_backups(guild_id, interval_days, retention_count)
            if result.success:
                await _reconcile_automatic_backup_schedule_after_config_change(bot, guild_id)
            await send_config_result(modal_interaction, bot, guild_id, result)

        configuration = bot.config_service.get_configuration(guild_id)
        if configuration is None:
            await send_configuration_missing_message(configure_interaction, on_back, "Backup Defaults")
            return
        interval = configuration.backup.extra_fields.get(BACKUP_INTERVAL_DAYS_EXTRA_FIELD, 1)
        retention = configuration.backup.extra_fields.get(BACKUP_RETENTION_COUNT_EXTRA_FIELD, 30)
        defaults = (str(interval), str(retention))
        await configure_interaction.response.send_modal(BackupDefaultsModal(on_submit, defaults=defaults))

    async def on_disable(disable_interaction: discord.Interaction) -> None:
        result = config_service.disable_automatic_backups(guild_id)
        if result.success:
            await _reconcile_automatic_backup_schedule_after_config_change(bot, guild_id)
        prefix = "" if result.success else "⚠ "
        await disable_interaction.response.edit_message(
            content=f"{prefix}{result.message}", view=BackToMenuOnlyView(on_back)
        )

    await interaction.response.edit_message(
        content=(
            "**WASH Configuration -- Backup Defaults**\n\nEnable or disable automatic backups. "
            f"{BACKUP_SCOPE_EXPLANATION}"
        ),
        view=ConfigBackupDefaultsChoiceView(on_configure, on_disable, on_back),
    )


async def send_config_eligible_pool_warning_section(
    interaction: discord.Interaction, bot: "WatchPartyBot", guild_id: int, on_back: OnBackToMenu, *, edit: bool
) -> None:
    """/config's Eligible Pool Warning section -- Enable/Disable, Set
    Threshold (blank restores the automatic "candidate count × 5"
    default), and destination (Admin Channel or the Watch Party Home
    Channel).
    """
    config_service = bot.config_service

    def body() -> str:
        summary_lines = config_service.build_summary_lines(guild_id, interaction.guild)
        current_value_line = summary_lines[CONFIG_SECTION_ORDER.index(ConfigSection.ELIGIBLE_POOL_WARNING)]
        return f"**WASH Configuration -- Eligible Pool Warning**\n\nCurrent value -- {current_value_line}"

    async def on_enable(enable_interaction: discord.Interaction) -> None:
        result = config_service.set_eligible_pool_warning_enabled(guild_id, True)
        await send_config_result(enable_interaction, bot, guild_id, result)

    async def on_disable(disable_interaction: discord.Interaction) -> None:
        result = config_service.set_eligible_pool_warning_enabled(guild_id, False)
        await send_config_result(disable_interaction, bot, guild_id, result)

    async def on_set_threshold(threshold_interaction: discord.Interaction) -> None:
        async def on_submit(modal_interaction: discord.Interaction, threshold_text: str) -> None:
            threshold_text = threshold_text.strip()
            threshold: Optional[int] = None
            if threshold_text:
                try:
                    threshold = int(threshold_text)
                except ValueError:
                    await modal_interaction.response.edit_message(
                        content=f"{body()}\n\n⚠ Threshold must be a whole number, or left blank for automatic.",
                        view=view(),
                    )
                    return
            result = config_service.set_eligible_pool_warning_threshold(guild_id, threshold)
            await send_config_result(modal_interaction, bot, guild_id, result)

        current_configuration = config_service.get_configuration(guild_id)
        current_threshold = (
            current_configuration.notifications.administrative.low_suggestion_pool_threshold
            if current_configuration is not None
            else None
        )
        await threshold_interaction.response.send_modal(
            EligiblePoolWarningThresholdModal(on_submit, default=str(current_threshold) if current_threshold else "")
        )

    async def on_use_admin_channel(admin_interaction: discord.Interaction) -> None:
        result = config_service.set_eligible_pool_warning_destination(
            guild_id, EligiblePoolWarningDestination.ADMIN_CHANNEL
        )
        await send_config_result(admin_interaction, bot, guild_id, result)

    async def on_use_home_channel(home_interaction: discord.Interaction) -> None:
        result = config_service.set_eligible_pool_warning_destination(
            guild_id, EligiblePoolWarningDestination.HOME_CHANNEL
        )
        await send_config_result(home_interaction, bot, guild_id, result)

    def view() -> ConfigEligiblePoolWarningView:
        return ConfigEligiblePoolWarningView(
            on_enable, on_disable, on_set_threshold, on_use_admin_channel, on_use_home_channel, on_back
        )

    if edit:
        await interaction.response.edit_message(content=body(), view=view())
    else:
        await interaction.response.send_message(body(), view=view(), ephemeral=True)


# --- FR-030: /join watch party -----------------------------------------------------------


def _build_membership_decision_callbacks(bot: "WatchPartyBot"):
    """Build the shared Approve/Deny callbacks every MembershipApprovalView uses.

    A single pair suffices for every request: the button itself carries
    its own request_id and passes it as an argument at click time (see
    ApproveMembershipRequestButton/DenyMembershipRequestButton), so no
    per-request closure is needed here.
    """

    async def on_approve(interaction: discord.Interaction, request_id: int) -> None:
        await handle_membership_approval_decision(interaction, bot, request_id, approve=True)

    async def on_deny(interaction: discord.Interaction, request_id: int) -> None:
        await handle_membership_approval_decision(interaction, bot, request_id, approve=False)

    return on_approve, on_deny


async def handle_join_watch_party(interaction: discord.Interaction, bot: "WatchPartyBot") -> None:
    """Handle /join watch party: the single entry point for every join mode.

    Everyone may run this command (see PermissionService's approved
    model -- there is no gate here at all), since it's how a non-member
    becomes one in the first place.
    """
    guild_id = interaction.guild_id
    if guild_id is None or interaction.guild is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    outcome = await bot.membership_service.handle_join_request(guild_id, interaction.user, interaction.guild)

    if outcome.kind is JoinOutcomeKind.OFFER_LEAVE:
        async def on_confirm(confirm_interaction: discord.Interaction) -> None:
            result = await bot.membership_service.leave_self_service(
                guild_id, confirm_interaction.user, confirm_interaction.guild
            )
            await confirm_interaction.response.send_message(result.message, ephemeral=True)

        async def on_abort(abort_interaction: discord.Interaction) -> None:
            await abort_interaction.response.send_message(
                "No changes were made. You're still a Watch Party member.", ephemeral=True
            )

        view = EditVoteConfirmationView(confirm_label="Leave Watch Party", on_confirm=on_confirm, on_abort=on_abort)
        await interaction.response.send_message(outcome.message, view=view, ephemeral=True)
        return

    await interaction.response.send_message(outcome.message, ephemeral=True)

    if outcome.kind is not JoinOutcomeKind.REQUEST_CREATED:
        return

    request = outcome.request
    guild_configuration = bot.guild_configuration_repository.get(guild_id)
    # MembershipService already validated the Admin channel is configured
    # and usable before returning REQUEST_CREATED -- posted only there,
    # never the log channel or the invocation channel, and never a
    # fallback. A None here would mean Discord state changed in the tiny
    # window since that validation ran; the except below handles it the
    # same as any other last-second failure.
    channel_id = guild_configuration.channels.admin_channel_id if guild_configuration is not None else None

    try:
        channel = bot.get_channel(channel_id) if channel_id is not None else None
        if channel is None and channel_id is not None:
            channel = await bot.fetch_channel(channel_id)
        if channel is None:
            raise RuntimeError("no notification channel available")

        on_approve, on_deny = _build_membership_decision_callbacks(bot)
        view = MembershipApprovalView(request.request_id, on_approve, on_deny)
        guild_wash_crew_role_id = guild_configuration.wash_crew_role_id if guild_configuration is not None else None
        wash_crew_mention = f"<@&{guild_wash_crew_role_id}>" if guild_wash_crew_role_id else "WASH Crew"
        message = await channel.send(
            f"{wash_crew_mention} {interaction.user.mention} has requested to join the Watch Party.",
            view=view,
        )
        bot.membership_service.attach_request_message(request.request_id, channel.id, message.id)
    except Exception:
        logger.warning(
            "Could not notify WASH Crew about membership request %s", request.request_id, exc_info=True
        )
        await interaction.followup.send(
            "Your request was recorded, but WASH Crew could not be automatically notified. "
            "Please reach out to them directly.",
            ephemeral=True,
        )


async def handle_membership_approval_decision(
    interaction: discord.Interaction, bot: "WatchPartyBot", request_id: int, *, approve: bool
) -> None:
    """Handle a click on a membership request's Approve or Deny button.

    Only WASH Crew may process approvals -- fails closed exactly like
    every other WASH-gated interaction. Approving/denying an
    already-processed or nonexistent request fails gracefully (the
    service returns success=False with a clear message; nothing raises).
    """
    permission = resolve_permission_service(
        interaction.guild_id, bot.guild_configuration_repository
    ).require_wash_crew(interaction.user)
    if not permission.allowed:
        await interaction.response.send_message(permission.message, ephemeral=True)
        return

    guild_id = interaction.guild_id
    if approve:
        request = bot.membership_service.get_request(request_id)
        member = None
        if request is not None and interaction.guild is not None:
            member = interaction.guild.get_member(request.user_id)
            if member is None:
                try:
                    member = await interaction.guild.fetch_member(request.user_id)
                except Exception:
                    member = None
        result = await bot.membership_service.approve_request(
            request_id, guild_id, interaction.user.id, member, interaction.guild
        )
    else:
        result = bot.membership_service.deny_request(request_id, guild_id, interaction.user.id)

    await interaction.response.send_message(result.message, ephemeral=True)

    if not result.success or result.request is None:
        return

    action_word = "approved" if approve else "denied"
    try:
        await interaction.message.edit(
            content=(
                f"<@{result.request.user_id}> Your request to join the Watch Party was "
                f"{action_word} by {interaction.user.mention}."
            ),
            view=None,
        )
    except Exception:
        logger.warning(
            "Could not update membership request %s's message after it was %s",
            request_id,
            action_word,
            exc_info=True,
        )


# --- FR-031: /membership administration (formerly /watch_party) --------------------------

WATCH_PARTY_LIST_PAGE_SIZE = 10


class MembershipGroup(discord.app_commands.Group):
    """WASH Crew-only /membership command group (Slash-Command UX Audit).

    Renamed from the former /watch_party (underscore, pre-audit) --
    "membership" both drops the underscore and eliminates the previous
    naming collision-in-spirit with WatchPartyEventGroup's own
    "watch-party" (hyphen, currently dormant/unregistered -- see its own
    docstring). Subcommands themselves are unchanged: they only collect
    Discord-native parameters and delegate to module-level
    handle_watch_party_*() functions -- kept as thin as every other
    command in this file, so the actual behavior stays unit-testable
    without a live Discord connection. Those handler function names are
    intentionally left as-is (they describe the *action*, not this
    group's Discord-facing name, and renaming them would only churn
    working code for no behavioral or clarity gain).
    """

    def __init__(self, bot: "WatchPartyBot") -> None:
        super().__init__(name="membership", description="Manage Watch Party membership (WASH Crew only).")
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Gate every /membership subcommand on WASH Crew, in one place.

        Reuses PermissionService.require_wash_crew exactly like every
        other WASH-gated command -- fails closed when unconfigured.
        """
        permission = resolve_permission_service(
            interaction.guild_id, self.bot.guild_configuration_repository
        ).require_wash_crew(interaction.user)
        if not permission.allowed:
            await interaction.response.send_message(permission.message, ephemeral=True)
            return False
        return True

    @discord.app_commands.command(name="members", description="List current Watch Party members.")
    async def members(self, interaction: discord.Interaction) -> None:
        await handle_watch_party_members(interaction, self.bot)

    @discord.app_commands.command(name="pending", description="List pending Approval-Required join requests.")
    async def pending(self, interaction: discord.Interaction) -> None:
        await handle_watch_party_pending(interaction, self.bot)

    @discord.app_commands.command(name="approved", description="List recently approved Watch Party join requests.")
    async def approved(self, interaction: discord.Interaction) -> None:
        await handle_watch_party_approved(interaction, self.bot)

    @discord.app_commands.command(name="denied", description="List recently denied Watch Party join requests.")
    async def denied(self, interaction: discord.Interaction) -> None:
        await handle_watch_party_denied(interaction, self.bot)

    @discord.app_commands.command(name="add", description="Manually add a member to the Watch Party role.")
    async def add(self, interaction: discord.Interaction, member: discord.Member) -> None:
        await handle_watch_party_add(interaction, self.bot, member)

    @discord.app_commands.command(name="remove", description="Manually remove a member from the Watch Party role.")
    async def remove(self, interaction: discord.Interaction, member: discord.Member) -> None:
        await handle_watch_party_remove(interaction, self.bot, member)

    @discord.app_commands.command(name="search", description="Look up a member's Watch Party membership history.")
    async def search(self, interaction: discord.Interaction, member: discord.Member) -> None:
        await handle_watch_party_search(interaction, self.bot, member)


class JoinWatchGroup(discord.app_commands.Group):
    """The "watch" subcommand group nested inside /join, containing
    exactly one subcommand ("party") -- see JoinGroup's own docstring
    for why this three-level shape was chosen over a flatter one.
    """

    def __init__(self, bot: "WatchPartyBot") -> None:
        super().__init__(name="watch", description="Join something related to watching.")
        self.bot = bot

    @discord.app_commands.command(name="party", description="Join or leave the Watch Party.")
    async def party(self, interaction: discord.Interaction) -> None:
        await handle_join_watch_party(interaction, self.bot)


class JoinGroup(discord.app_commands.Group):
    """/join command group (Slash-Command UX Audit) -- replaces the
    former top-level /join_watch_party (underscore) with the grouped
    /join watch party, exactly as directed. "watch party" is a nested
    subcommand group ("watch") containing one subcommand ("party"),
    matching the exact three-token invocation asked for, rather than a
    single hyphenated subcommand name -- this is currently the only
    subcommand under /join, but the shape leaves room for a future
    sibling (e.g. a "/join <something else>") without another rename.
    handle_join_watch_party itself is unchanged -- only its registration
    moved.
    """

    def __init__(self, bot: "WatchPartyBot") -> None:
        super().__init__(name="join", description="Join something in this server.")
        self.bot = bot
        self.add_command(JoinWatchGroup(bot))


class MaintenanceGroup(discord.app_commands.Group):
    """WASH Crew-only /maintenance command group (Slash-Command UX Audit).

    Consolidates five previously-separate top-level commands that
    help_registry.py's own COMMAND_HELP had already been documenting
    together under one "WASH Crew: Maintenance" section, even before
    this audit gave them a matching Discord-level group: the former
    /backup, /restore (whole-instance restore -- distinct from
    /database restore, which restores one collection), /import,
    /factory_reset (renamed here to "reset", dropping both the
    underscore and the redundant "factory" word now that "/maintenance
    reset" already makes the scope clear), and /repair_suggestions
    (renamed to "repair"). Every subcommand still calls the exact same
    module-level handle_*/perform_*() logic its former top-level command
    used -- only the registration moved, so behavior is unchanged.
    """

    def __init__(self, bot: "WatchPartyBot") -> None:
        super().__init__(name="maintenance", description="Back up, restore, import, reset, or repair WASH's data (WASH Crew only).")
        self.bot = bot

    @discord.app_commands.command(name="backup", description="Back up all of WASH's data.")
    async def backup(self, interaction: discord.Interaction) -> None:
        message, ephemeral, archive_path, display_filename = perform_backup(
            backup_service=self.bot.backup_service,
            user=interaction.user,
            wash_crew_role_id=resolve_permission_service(
                interaction.guild_id, self.bot.guild_configuration_repository
            ).wash_crew_role_id,
        )
        if archive_path is None or display_filename is None:
            await interaction.response.send_message(message, ephemeral=ephemeral)
            return

        file = discord.File(archive_path, filename=display_filename)
        await interaction.response.send_message(message, file=file, ephemeral=ephemeral)

    @discord.app_commands.command(name="restore", description="Restore WASH's data from a backup.")
    @discord.app_commands.describe(
        backup_filename="An existing local backup's filename (see /maintenance backup's response).",
        backup_file="Upload a backup .zip to restore from instead of selecting a local one.",
    )
    async def restore(
        self,
        interaction: discord.Interaction,
        backup_filename: Optional[str] = None,
        backup_file: Optional[discord.Attachment] = None,
    ) -> None:
        await handle_restore(interaction, self.bot, backup_filename, backup_file)

    @discord.app_commands.command(name="import", description="Import another WASH instance's backup.")
    @discord.app_commands.describe(
        backup_file="Upload a full backup .zip created by another WASH instance's /maintenance backup."
    )
    async def import_(self, interaction: discord.Interaction, backup_file: discord.Attachment) -> None:
        await handle_import(interaction, self.bot, backup_file)

    @discord.app_commands.command(name="reset", description="Erase all of WASH's data and start over.")
    async def reset(self, interaction: discord.Interaction) -> None:
        await handle_factory_reset(interaction, self.bot)

    @discord.app_commands.command(
        name="repair", description="Repair suggestions with malformed or legacy data."
    )
    async def repair(self, interaction: discord.Interaction) -> None:
        await show_repair_confirmation(interaction, self.bot)


class SuggestionGroup(discord.app_commands.Group):
    """WASH Crew-only /suggestion command group (Slash-Command UX Audit).

    Consolidates the two remaining single-suggestion Crew administrative
    actions that were each their own bare top-level command: the former
    /edit_suggestion (underscore -- renamed here to "edit") and /remove
    (already underscore-free, but pairing it here avoids leaving "edit"
    as a lonely, single-subcommand group and gives both actions a
    consistent, discoverable home). /reject and /unreject deliberately
    stay separate, bare top-level commands -- they're available to every
    Watch Party member, not WASH-Crew-only, so grouping them alongside
    these two Crew-only actions would mix audiences under one command,
    exactly the inconsistency this audit is meant to remove. Both
    subcommands still delegate to the exact same handle_edit_suggestion/
    handle_remove_suggestion functions their former top-level commands
    used -- only the registration moved.
    """

    def __init__(self, bot: "WatchPartyBot") -> None:
        super().__init__(name="suggestion", description="Edit or remove a suggestion (WASH Crew only).")
        self.bot = bot

    @discord.app_commands.command(name="edit", description="Change a suggestion's status or collection.")
    @discord.app_commands.describe(
        reference="The suggestion's reference number (e.g. #0007) or its current exact title.",
    )
    async def edit(self, interaction: discord.Interaction, reference: str) -> None:
        await handle_edit_suggestion(interaction, self.bot, reference)

    @discord.app_commands.command(name="remove", description="Retire a suggestion (reversible, not a permanent deletion).")
    @discord.app_commands.describe(
        query="A reference number (e.g. #0007), exact title, or title without its year."
    )
    async def remove(self, interaction: discord.Interaction, query: str) -> None:
        await handle_remove_suggestion(interaction, self.bot, query)


class DatabaseGroup(discord.app_commands.Group):
    """WASH Crew-only /database command group.

    Release UX & Command Surface Cleanup: the top-level surface is now
    intentionally minimal -- /database add, /database list, /database
    health, and /database manage. /database move, /database backup,
    /database reset, and /database remove are no longer registered as
    standalone slash commands; every one of those actions is fully
    reachable through /database manage's guided picker-then-menu
    workflow instead (see show_database_management_menu), which calls
    the exact same start_database_move/backup/reset/remove() logic those
    former subcommands used -- no behavior was removed, only the extra,
    redundant top-level entry points. Their own former command wrappers
    (handle_database_move/backup/reset/remove) are deliberately left
    intact and still fully tested, even though nothing in this class
    calls them anymore, so the exact permission-check/"no collections"/
    picker behavior they encode remains available and provably correct
    if a future change ever needs it again.

    /database restore is the one exception that stays a top-level
    command: it takes a file-attachment parameter, and Discord's API has
    no way to attach a file in response to a button or modal submission
    -- only a slash command's own options can collect one. /database
    manage's Restore Collection action reflects this honestly: it can't
    perform a restore itself, so it just tells WASH Crew to run
    `/database restore` directly (see show_database_management_menu).

    Each remaining subcommand only collects Discord-native parameters
    and delegates to a module-level handle_*/perform_*() function,
    exactly like every other command in this file, so behavior stays
    unit-testable without a live Discord connection. No group-level
    interaction_check: every subcommand already performs its own WASH
    Crew check internally.
    """

    def __init__(self, bot: "WatchPartyBot") -> None:
        super().__init__(name="database", description="Manage suggestion collections (WASH Crew only).")
        self.bot = bot

    @discord.app_commands.command(name="add", description="Create a new collection.")
    async def add(self, interaction: discord.Interaction) -> None:
        await handle_database_add(interaction, self.bot)

    @discord.app_commands.command(name="list", description="List this server's collections.")
    async def list(self, interaction: discord.Interaction) -> None:
        message, ephemeral = perform_database_list(
            suggestion_service=self.bot.suggestion_service,
            user=interaction.user,
            wash_crew_role_id=resolve_permission_service(
                interaction.guild_id, self.bot.guild_configuration_repository
            ).wash_crew_role_id,
            guild_id=interaction.guild_id,
            suggestion_database_configuration_repository=self.bot.suggestion_database_configuration_repository,
        )
        await interaction.response.send_message(message, ephemeral=ephemeral)

    @discord.app_commands.command(
        name="health", description="Show a collection's eligibility and pool health."
    )
    async def health(self, interaction: discord.Interaction) -> None:
        await handle_database_health(interaction, self.bot)

    @discord.app_commands.command(
        name="manage",
        description="Move, edit, back up, restore, reset, or remove collections.",
    )
    async def manage(self, interaction: discord.Interaction) -> None:
        await handle_database_manage(interaction, self.bot)

    @discord.app_commands.command(name="restore", description="Restore a collection backup.")
    @discord.app_commands.describe(
        mode="Merge adds compatible suggestions without touching existing ones. "
        "Replace overwrites the whole collection.",
        backup_filename="An existing local collection backup's filename.",
        backup_file="Upload a collection backup .zip to restore from instead of selecting a local one.",
    )
    @discord.app_commands.choices(
        mode=[
            discord.app_commands.Choice(name="Merge", value="merge"),
            discord.app_commands.Choice(name="Replace", value="replace"),
        ]
    )
    async def restore(
        self,
        interaction: discord.Interaction,
        mode: str,
        backup_filename: Optional[str] = None,
        backup_file: Optional[discord.Attachment] = None,
    ) -> None:
        # The one subcommand that can't move into /database manage --
        # see this class's own docstring for why (Discord has no way to
        # attach a file in response to a button/modal interaction).
        await handle_database_restore(interaction, self.bot, mode, backup_filename, backup_file)


class RandomGroup(discord.app_commands.Group):
    """/random command group -- currently one subcommand, /random watch.

    Replaces the former top-level /random_watch (underscore) command
    outright, with no compatibility alias -- the visible command name
    now contains no underscore. This was a standalone, narrow rename at
    the time; the project-wide Slash-Command UX Audit later removed
    every other remaining underscore command too (see MembershipGroup,
    JoinGroup, MaintenanceGroup, SuggestionGroup).
    Permissions and behavior are unchanged from the former /random_watch:
    handle_random_watch performs its own Watch Party member check
    internally, so no group-level interaction_check is needed here.
    """

    def __init__(self, bot: "WatchPartyBot") -> None:
        super().__init__(name="random", description="Random-selection discovery tools.")
        self.bot = bot

    @discord.app_commands.command(
        name="watch", description="Pick one random eligible watch item from a collection."
    )
    async def watch(self, interaction: discord.Interaction) -> None:
        await handle_random_watch(interaction, self.bot)


class VotingGroup(discord.app_commands.Group):
    """WASH Crew-only /vote command group (Command Structure Cleanup, pre-v1).

    Replaces the former top-level /start_vote, /vote_status, and
    /edit_vote commands -- removed outright, with no compatibility alias.
    Casting a vote still happens entirely through the interactive buttons
    on a voting post, never a slash command, so there is no standalone
    /vote command and no naming conflict with this group; /vote is WASH
    Crew administration only. Subcommand bodies are unchanged from their
    former top-level commands, including each one's own permission check
    ordering (e.g. /vote edit's WASH Crew check deliberately happens after
    collection resolution, exactly as /edit_vote's did). Renamed from
    /voting to /vote per approved polish batch; no compatibility alias
    kept (pre-release).
    """

    def __init__(self, bot: "WatchPartyBot") -> None:
        super().__init__(name="vote", description="Manage voting rounds (WASH Crew only).")
        self.bot = bot

    @discord.app_commands.command(name="start", description="Start a new voting round.")
    async def start(self, interaction: discord.Interaction) -> None:
        bot = self.bot

        # Pre-Phase 3 UX Polish: permission-gated up front, matching
        # show_repair_confirmation's convention -- an unauthorized member
        # is rejected before ever seeing the "Use Defaults / Customize
        # This Vote" choice UI, not after clicking through it.
        # perform_start_vote's own check further downstream is unchanged
        # and still runs regardless, exactly as
        # perform_repair_suggestions' own check does after
        # show_repair_confirmation's.
        #
        # Multi-Guild Isolation, Phase 3c: resolved once from this guild's
        # own configuration and reused below for the "Use Defaults" path,
        # rather than reading the shared bot.wash_crew_role_id singleton.
        wash_crew_role_id = resolve_permission_service(
            interaction.guild_id, bot.guild_configuration_repository
        ).wash_crew_role_id
        if wash_crew_role_id is None:
            await interaction.response.send_message(
                "WASH Crew permissions have not been configured. Set WASH_CREW_ROLE_ID before using this command.",
                ephemeral=True,
            )
            return
        if not is_wash_crew_member(interaction.user, wash_crew_role_id):
            await interaction.response.send_message(
                "You need the WASH Crew role to start a voting round.", ephemeral=True
            )
            return

        async def on_use_defaults(choice_interaction: discord.Interaction) -> None:
            await handle_start_vote_use_defaults(
                choice_interaction,
                vote_service=bot.vote_service,
                suggestion_service=bot.suggestion_service,
                nominee_selection_service=bot.nominee_selection_service,
                wash_crew_role_id=wash_crew_role_id,
                default_nominee_count=bot.default_nominee_count,
                scheduler_service=bot.scheduler_host.scheduler_service,
                guild_configuration_repository=bot.guild_configuration_repository,
                suggestion_database_configuration_repository=bot.suggestion_database_configuration_repository,
                bot=bot,
            )

        async def on_customize(choice_interaction: discord.Interaction) -> None:
            # Custom Vote Filter Architecture: resolve the collection now
            # (before any filter select is ever built), reusing the same
            # ambiguity-picker pattern handle_start_vote_completion uses
            # for "Use Defaults" -- only the timing differs.
            if choice_interaction.guild_id is not None and bot.nominee_selection_service is not None:
                pre_resolution = bot.suggestion_service.resolve_database_for_channel(
                    choice_interaction.guild_id,
                    choice_interaction.channel_id,
                    bot.suggestion_database_configuration_repository,
                )
                if pre_resolution.ambiguous_candidates:

                    async def on_database_chosen(
                        select_interaction: discord.Interaction, database_id: int
                    ) -> None:
                        await show_customize_vote_overrides(select_interaction, bot, database_id)

                    options = [
                        (
                            database.database_id,
                            format_collection_display(
                                _resolve_collection_name(
                                    bot.suggestion_service,
                                    database,
                                    choice_interaction.guild,
                                    bot.suggestion_database_configuration_repository,
                                )
                            ),
                        )
                        for database in pre_resolution.ambiguous_candidates
                    ]
                    view = ListDatabaseSelectView(options, on_database_chosen)
                    await choice_interaction.response.send_message(
                        "Which collection would you like to use?", view=view, ephemeral=True
                    )
                    return

                target_database_id = (
                    pre_resolution.database.database_id if pre_resolution.database is not None else None
                )
            else:
                target_database_id = None

            await show_customize_vote_overrides(choice_interaction, bot, target_database_id)

        view = StartVoteChoiceView(on_use_defaults, on_customize)
        await interaction.response.send_message(
            "How would you like to start this voting round?", view=view, ephemeral=True
        )

    @discord.app_commands.command(name="status", description="View the current voting round.")
    async def status(self, interaction: discord.Interaction) -> None:
        await handle_vote_status(interaction, self.bot)

    @discord.app_commands.command(name="edit", description="Change, end, or cancel the active vote.")
    async def edit(self, interaction: discord.Interaction) -> None:
        await handle_edit_vote(interaction, self.bot)


class WatchPartyEventGroup(discord.app_commands.Group):
    """WASH Crew-only /watch-party command group (Command Structure Cleanup, pre-v1).

    Replaces the former top-level /schedule_watch_party,
    /reschedule_watch_party, /cancel_watch_party, and /watch_party_status
    commands -- removed outright, with no compatibility alias.

    Named with a hyphen ("watch-party") rather than an underscore
    deliberately: at the time, the pre-existing /watch_party group
    (Watch Party *membership* administration -- members/pending/
    approved/denied/add/remove/search) already owned that exact
    underscore name, and merging the two into one group would have
    conflated two distinct concerns under one command, so this group
    used the hyphenated variant instead of colliding with it. The
    Slash-Command UX Audit later renamed that membership group to
    /membership (see MembershipGroup), which frees up "watch_party" and
    would let this group revert to an underscore-free, un-hyphenated
    name if it's ever re-registered -- left as "watch-party" for now
    since renaming a currently-dormant, unregistered group isn't part of
    that audit's scope (Section 1: "audit every *registered* slash
    command").
    """

    def __init__(self, bot: "WatchPartyBot") -> None:
        super().__init__(name="watch-party", description="Manage the scheduled watch party (WASH Crew only).")
        self.bot = bot

    @discord.app_commands.command(name="schedule", description="Schedule a watch party.")
    async def schedule(self, interaction: discord.Interaction, watch_item_id: int, when: str) -> None:
        bot = self.bot
        await handle_schedule_watch_party_completion(
            interaction,
            watch_party_service=bot.watch_party_service,
            suggestion_service=bot.suggestion_service,
            wash_crew_role_id=resolve_permission_service(
                interaction.guild_id, bot.guild_configuration_repository
            ).wash_crew_role_id,
            watch_item_id=watch_item_id,
            when=when,
            scheduler_service=bot.scheduler_host.scheduler_service,
            guild_configuration_repository=bot.guild_configuration_repository,
        )

    @discord.app_commands.command(name="status", description="View the scheduled watch party.")
    async def status(self, interaction: discord.Interaction) -> None:
        bot = self.bot
        permission = resolve_permission_service(
            interaction.guild_id, bot.guild_configuration_repository
        ).require_wash_crew(interaction.user)
        if not permission.allowed:
            await interaction.response.send_message(permission.message, ephemeral=True)
            return
        message = perform_watch_party_status(
            watch_party_service=bot.watch_party_service, suggestion_service=bot.suggestion_service
        )
        await interaction.response.send_message(message)

    @discord.app_commands.command(name="reschedule", description="Change a watch party's start.")
    async def reschedule(self, interaction: discord.Interaction, when: str) -> None:
        bot = self.bot
        await handle_reschedule_watch_party_completion(
            interaction,
            watch_party_service=bot.watch_party_service,
            wash_crew_role_id=resolve_permission_service(
                interaction.guild_id, bot.guild_configuration_repository
            ).wash_crew_role_id,
            when=when,
            scheduler_service=bot.scheduler_host.scheduler_service,
            guild_configuration_repository=bot.guild_configuration_repository,
            suggestion_service=bot.suggestion_service,
        )

    @discord.app_commands.command(name="cancel", description="Cancel a scheduled watch party.")
    async def cancel(self, interaction: discord.Interaction) -> None:
        bot = self.bot
        await handle_cancel_watch_party_completion(
            interaction,
            watch_party_service=bot.watch_party_service,
            wash_crew_role_id=resolve_permission_service(
                interaction.guild_id, bot.guild_configuration_repository
            ).wash_crew_role_id,
            scheduler_service=bot.scheduler_host.scheduler_service,
            suggestion_service=bot.suggestion_service,
        )


def build_watch_party_members_text(role_name: str, members: List[Any]) -> str:
    """Build /membership members' response text.

    `members` is expected in the order the caller wants displayed (real
    discord.Role.members preserves no particular order, so callers that
    care about recency should sort before calling this).
    """
    if not members:
        return f'"{role_name}" currently has no members.'

    lines = [f"**{role_name} Members ({len(members)})**", ""]
    shown = members[:WATCH_PARTY_LIST_PAGE_SIZE]
    for member in shown:
        display_name = getattr(member, "display_name", None) or str(member)
        username = getattr(member, "name", None)
        joined_at = getattr(member, "joined_at", None)
        joined_text = format_datetime_for_display(joined_at) if joined_at is not None else "Unknown"
        if username and username != display_name:
            lines.append(f"- {display_name} (@{username}) -- joined {joined_text}")
        else:
            lines.append(f"- {display_name} -- joined {joined_text}")

    remaining = len(members) - len(shown)
    if remaining > 0:
        lines.append(f"...and {remaining} more.")
    return "\n".join(lines)


async def handle_watch_party_members(interaction: discord.Interaction, bot: "WatchPartyBot") -> None:
    guild_id = interaction.guild_id
    if guild_id is None or interaction.guild is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    role_config = bot.membership_service.get_role_config(guild_id)
    if role_config is None or role_config.role_id is None:
        await interaction.response.send_message(
            "The Watch Party role hasn't been configured for this server.", ephemeral=True
        )
        return

    role = interaction.guild.get_role(role_config.role_id)
    if role is None:
        await interaction.response.send_message("The configured Watch Party role no longer exists.", ephemeral=True)
        return

    text = build_watch_party_members_text(role.name, list(role.members))
    await interaction.response.send_message(text, ephemeral=True)


async def handle_watch_party_add(interaction: discord.Interaction, bot: "WatchPartyBot", member: discord.Member) -> None:
    guild_id = interaction.guild_id
    if guild_id is None or interaction.guild is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    result = await bot.membership_service.admin_add_member(guild_id, member, interaction.guild, interaction.user.id)
    await interaction.response.send_message(result.message, ephemeral=True)


async def handle_watch_party_remove(
    interaction: discord.Interaction, bot: "WatchPartyBot", member: discord.Member
) -> None:
    guild_id = interaction.guild_id
    if guild_id is None or interaction.guild is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    result = await bot.membership_service.admin_remove_member(guild_id, member, interaction.guild, interaction.user.id)
    await interaction.response.send_message(result.message, ephemeral=True)


def build_watch_party_search_text(member: discord.Member, result: MemberSearchResult) -> str:
    display_name = getattr(member, "display_name", None) or str(member)
    lines = [f"**Watch Party Membership -- {display_name}**", ""]
    lines.append(f"Current status: {'Member' if result.is_current_member else 'Not a member'}")

    if result.pending_request is not None:
        lines.append(f"Pending request: submitted {format_datetime_for_display(result.pending_request.created_at)}")
    else:
        lines.append("Pending request: none")

    if result.last_approved_request is not None:
        lines.append(
            f"Last approval: {format_datetime_for_display(result.last_approved_request.resolved_at)} "
            f"by <@{result.last_approved_request.resolved_by_user_id}>"
        )
    else:
        lines.append("Last approval: none")

    if result.last_denied_request is not None:
        lines.append(
            f"Last denial: {format_datetime_for_display(result.last_denied_request.resolved_at)} "
            f"by <@{result.last_denied_request.resolved_by_user_id}>"
        )
    else:
        lines.append("Last denial: none")

    lines.append(result.cooldown_message if result.cooldown_message is not None else "Cooldown: none active")

    return "\n".join(lines)


async def handle_watch_party_search(
    interaction: discord.Interaction, bot: "WatchPartyBot", member: discord.Member
) -> None:
    guild_id = interaction.guild_id
    if guild_id is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    result = bot.membership_service.search_member(guild_id, member.id, member)
    await interaction.response.send_message(build_watch_party_search_text(member, result), ephemeral=True)


def build_watch_party_pending_text(pending: List[MembershipRequest]) -> str:
    if not pending:
        return "There are no pending Watch Party join requests."

    lines = [f"**Pending Join Requests ({len(pending)})**", ""]
    shown = pending[:WATCH_PARTY_LIST_PAGE_SIZE]
    for request in shown:
        lines.append(
            f"#{request.request_id} -- <@{request.user_id}> -- requested "
            f"{format_datetime_for_display(request.created_at)}"
        )
    remaining = len(pending) - len(shown)
    if remaining > 0:
        lines.append(f"...and {remaining} more.")
    return "\n".join(lines)


async def handle_watch_party_pending(interaction: discord.Interaction, bot: "WatchPartyBot") -> None:
    guild_id = interaction.guild_id
    if guild_id is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    pending = bot.membership_service.list_pending_requests(guild_id)
    text = build_watch_party_pending_text(pending)

    if not pending:
        await interaction.response.send_message(text, ephemeral=True)
        return

    async def on_select(select_interaction: discord.Interaction, request_id: int) -> None:
        request = bot.membership_service.get_request(request_id)
        if request is None or not request.is_pending:
            await select_interaction.response.edit_message(
                content="That request is no longer pending.", view=None
            )
            return

        on_approve, on_deny = _build_membership_decision_callbacks(bot)
        view = MembershipApprovalView(request_id, on_approve, on_deny)
        await select_interaction.response.edit_message(
            content=f"Request #{request_id} from <@{request.user_id}> -- choose an action.",
            view=view,
        )

    options = [
        (request.request_id, f"#{request.request_id} - requested {request.created_at.date()}")
        for request in pending[:WATCH_PARTY_LIST_PAGE_SIZE]
    ]
    view = PendingRequestSelectView(options, on_select)
    await interaction.response.send_message(text, view=view, ephemeral=True)


def build_watch_party_approved_text(approved: List[MembershipRequest]) -> str:
    if not approved:
        return "No approved Watch Party join requests yet."

    lines = [f"**Approved Join Requests ({len(approved)})**", ""]
    shown = approved[:WATCH_PARTY_LIST_PAGE_SIZE]
    for request in shown:
        lines.append(
            f"<@{request.user_id}> -- approved {format_datetime_for_display(request.resolved_at)} "
            f"by <@{request.resolved_by_user_id}>"
        )
    remaining = len(approved) - len(shown)
    if remaining > 0:
        lines.append(f"...and {remaining} more.")
    return "\n".join(lines)


async def handle_watch_party_approved(interaction: discord.Interaction, bot: "WatchPartyBot") -> None:
    guild_id = interaction.guild_id
    if guild_id is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    approved = sorted(
        bot.membership_request_repository.get_approved(guild_id),
        key=lambda request: request.resolved_at,
        reverse=True,
    )
    await interaction.response.send_message(build_watch_party_approved_text(approved), ephemeral=True)


def build_watch_party_denied_text(denied: List[MembershipRequest], cooldown_days: int) -> str:
    if not denied:
        return "No denied Watch Party join requests yet."

    lines = [f"**Denied Join Requests ({len(denied)})**", ""]
    shown = denied[:WATCH_PARTY_LIST_PAGE_SIZE]
    for request in shown:
        cooldown_expires_at = request.resolved_at + timedelta(days=cooldown_days)
        lines.append(
            f"<@{request.user_id}> -- denied {format_datetime_for_display(request.resolved_at)} "
            f"by <@{request.resolved_by_user_id}> -- cooldown until "
            f"{format_datetime_for_display(cooldown_expires_at)}"
        )
    remaining = len(denied) - len(shown)
    if remaining > 0:
        lines.append(f"...and {remaining} more.")
    return "\n".join(lines)


async def handle_watch_party_denied(interaction: discord.Interaction, bot: "WatchPartyBot") -> None:
    guild_id = interaction.guild_id
    if guild_id is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    denied = sorted(
        bot.membership_request_repository.get_denied(guild_id),
        key=lambda request: request.resolved_at,
        reverse=True,
    )
    role_config = bot.membership_service.get_role_config(guild_id)
    cooldown_days = role_config.denial_cooldown_days if role_config is not None else 7
    await interaction.response.send_message(build_watch_party_denied_text(denied, cooldown_days), ephemeral=True)


def parse_start_vote_overrides(
    nominee_count_text: Optional[str],
    duration_text: Optional[str],
    visibility_text: Optional[str],
    reminder_enabled_text: Optional[str] = None,
    reminder_minutes_text: Optional[str] = None,
) -> tuple[Optional[int], Optional[int], Optional[str], Optional[bool], Optional[int]]:
    """Parse raw customization-modal values into start-vote arguments.

    Blank numeric fields remain ``None`` so :func:`perform_start_vote` can
    apply configured defaults. Blank visibility also remains ``None`` so
    :func:`perform_start_vote` (via :func:`parse_vote_visibility`) applies
    the guild's configured default visibility rather than a hardcoded
    value (Release Polish Batch 2, Priority 6). Blank reminder fields
    remain ``None`` so the guild's configured reminder default is used
    (see scheduler.vote_scheduling.resolve_vote_reminder_settings).
    duration_text and reminder_minutes_text both accept WASH's one shared
    duration syntax (Requirement 3; see parse_optional_duration_field/
    parse_duration_text_to_minutes and parse_optional_reminder_minutes_field)
    -- both resolve to minutes. Range and enum validation remain
    centralized in :func:`perform_start_vote`.
    """
    nominee_count = parse_optional_int_field(nominee_count_text)
    duration_minutes = parse_optional_duration_field(duration_text)
    visibility = (visibility_text or "").strip() or None
    reminder_enabled = parse_optional_bool_field(reminder_enabled_text)
    reminder_minutes_before_close = parse_optional_reminder_minutes_field(reminder_minutes_text)
    return nominee_count, duration_minutes, visibility, reminder_enabled, reminder_minutes_before_close


async def handle_start_vote_completion(
    interaction: discord.Interaction,
    vote_service: VoteService,
    suggestion_service: SuggestionService,
    nominee_selection_service: Optional[NomineeSelectionService],
    wash_crew_role_id: Optional[int],
    visibility_str: Optional[str],
    duration_minutes: Optional[int],
    nominee_count: Optional[int],
    default_nominee_count: int,
    scheduler_service: Optional[SchedulerService] = None,
    guild_configuration_repository: Optional[GuildConfigurationRepository] = None,
    reminder_enabled: Optional[bool] = None,
    reminder_minutes_before_close: Optional[int] = None,
    suggestion_database_configuration_repository: Optional[SuggestionDatabaseConfigurationRepository] = None,
    resolved_database_id: Optional[int] = None,
    bot: Optional["WatchPartyBot"] = None,
    candidate_selection_override: Optional[CandidateSelectionMode] = None,
    filter_member_discord_user_id: Optional[int] = None,
    filter_member_display: Optional[str] = None,
    filter_genre: Optional[str] = None,
    filter_imdb_rating_min: Optional[float] = None,
    filter_imdb_rating_max: Optional[float] = None,
    filter_mpaa_rating: Optional[str] = None,
    filter_actor: Optional[str] = None,
) -> None:
    """Create a round and publish its interactive voting post.

    scheduler_service/guild_configuration_repository default to None so
    existing callers that don't pass them keep working unchanged; passing
    None simply skips scheduling (see schedule_vote_jobs).
    reminder_enabled/reminder_minutes_before_close default to None so
    existing callers keep working unchanged too; passing None uses the
    guild's configured reminder default (see FR-027's
    resolve_vote_reminder_settings).
    resolved_database_id defaults to None so existing callers keep
    working unchanged. Contextual Database Resolution: when unset and
    the invoking channel's database context is ambiguous (more than one
    database configured, none matching this channel), a picker is shown
    first and this function recurses with the WASH Crew member's choice
    once made -- see the ambiguity pre-check immediately below.
    bot: optional, used to sync newly-nominated candidates' own
    confirmation posts (see sync_vote_start_status_embeds) and to
    evaluate the Eligible Pool Warning after the round opens. Defaults to
    None so existing callers/tests keep working unchanged; when omitted,
    the round still opens correctly, those two side effects simply don't run.
    """
    target_database_id = resolved_database_id
    if (
        resolved_database_id is None
        and interaction.guild_id is not None
        and nominee_selection_service is not None
    ):
        pre_resolution = suggestion_service.resolve_database_for_channel(
            interaction.guild_id, interaction.channel_id, suggestion_database_configuration_repository
        )
        if pre_resolution.ambiguous_candidates:

            async def on_select(select_interaction: discord.Interaction, database_id: int) -> None:
                await handle_start_vote_completion(
                    select_interaction,
                    vote_service,
                    suggestion_service,
                    nominee_selection_service,
                    wash_crew_role_id,
                    visibility_str,
                    duration_minutes,
                    nominee_count,
                    default_nominee_count,
                    scheduler_service=scheduler_service,
                    guild_configuration_repository=guild_configuration_repository,
                    reminder_enabled=reminder_enabled,
                    reminder_minutes_before_close=reminder_minutes_before_close,
                    suggestion_database_configuration_repository=suggestion_database_configuration_repository,
                    resolved_database_id=database_id,
                    bot=bot,
                    candidate_selection_override=candidate_selection_override,
                    filter_member_discord_user_id=filter_member_discord_user_id,
                    filter_member_display=filter_member_display,
                    filter_genre=filter_genre,
                    filter_imdb_rating_min=filter_imdb_rating_min,
                    filter_imdb_rating_max=filter_imdb_rating_max,
                    filter_mpaa_rating=filter_mpaa_rating,
                    filter_actor=filter_actor,
                )

            options = [
                (
                    database.database_id,
                    format_collection_display(
                        _resolve_collection_name(
                            suggestion_service, database, interaction.guild, suggestion_database_configuration_repository
                        )
                    ),
                )
                for database in pre_resolution.ambiguous_candidates
            ]
            view = ListDatabaseSelectView(options, on_select)
            await interaction.response.send_message(
                "Which collection would you like to use?", view=view, ephemeral=True
            )
            return

        target_database_id = pre_resolution.database.database_id if pre_resolution.database is not None else None

    message, ephemeral = perform_start_vote(
        vote_service=vote_service,
        suggestion_service=suggestion_service,
        nominee_selection_service=nominee_selection_service,
        user=interaction.user,
        wash_crew_role_id=wash_crew_role_id,
        visibility_str=visibility_str,
        duration_minutes=duration_minutes,
        nominee_count=nominee_count,
        default_nominee_count=default_nominee_count,
        guild_id=interaction.guild_id,
        channel_id=interaction.channel_id,
        reminder_enabled=reminder_enabled,
        reminder_minutes_before_close=reminder_minutes_before_close,
        suggestion_database_configuration_repository=suggestion_database_configuration_repository,
        guild_configuration_repository=guild_configuration_repository,
        resolved_database_id=resolved_database_id,
        candidate_selection_override=candidate_selection_override,
        filter_member_discord_user_id=filter_member_discord_user_id,
        filter_member_display=filter_member_display,
        filter_genre=filter_genre,
        filter_imdb_rating_min=filter_imdb_rating_min,
        filter_imdb_rating_max=filter_imdb_rating_max,
        filter_mpaa_rating=filter_mpaa_rating,
        filter_actor=filter_actor,
    )

    # Rotation & Collection Health goal 2/6: a successful vote start is
    # exactly when eligibility was just freshly recomputed (presenting
    # new nominees just shrank the pool further) -- the natural moment to
    # check whether the pool is now low, matching "the user should never
    # have to discover" the pool needs topping up.
    if not ephemeral and target_database_id is not None and bot is not None:
        database = suggestion_service.get_database(target_database_id)
        if database is not None:
            await maybe_send_eligible_pool_warning(bot, database)

    if ephemeral:
        await interaction.response.send_message(message, ephemeral=True)
        return

    # Round IDs are sequential and never reused, and nothing else can run
    # between create_round() succeeding (inside perform_start_vote above)
    # and this line (no `await` in between) -- so the just-created round
    # is always the highest-ID one, regardless of how many other
    # collections also currently have their own round open. Safe and
    # exact, unlike an unscoped "the open round" lookup would now be.
    vote_round = vote_service.get_latest_round()

    # FR-015: schedule this round's future jobs (close_vote, and a
    # vote_reminder if enabled) now that it's confirmed created and
    # persisted -- before any further Discord I/O, so a failure sending
    # the voting post below can never prevent scheduling, and a vote that
    # failed to create (handled above via the `if ephemeral: return`)
    # never reaches this point at all, so no orphaned job is ever created
    # for it.
    if interaction.guild_id is not None:
        await schedule_vote_jobs(
            scheduler_service,
            vote_round,
            interaction.guild_id,
            guild_configuration_repository=guild_configuration_repository,
        )

    candidates = get_round_candidates(suggestion_service, vote_round)
    if bot is not None:
        await sync_vote_start_status_embeds(bot, candidates)
    view = build_voting_view(
        vote_service=vote_service,
        suggestion_service=suggestion_service,
        candidates=candidates,
        # Multi-Guild Isolation, Phase 3b: the known secondary
        # PermissionService constructor -- previously built straight from
        # WATCH_PARTY_MEMBER_ROLE_ID's own environment variable plus the
        # wash_crew_role_id parameter (itself sourced from the same
        # process-wide singleton), bypassing GuildConfiguration entirely.
        # Guild-scoped resolution now matches every other vote/suggestion
        # permission check.
        permission_service=resolve_permission_service(
            interaction.guild_id, guild_configuration_repository
        ),
    )
    collection_name = resolve_vote_collection_name(suggestion_service, vote_round.database_id)
    guild_round_number = (
        vote_service.get_guild_round_number(vote_round.id, interaction.guild_id)
        if interaction.guild_id is not None
        else None
    )
    post_embed = build_voting_post_embed(
        vote_round,
        candidates,
        standings=None,
        standings_error=None,
        collection_name=collection_name,
        guild_round_number=guild_round_number,
    )
    await interaction.response.send_message(embed=post_embed, view=view)
    sent_message = await interaction.original_response()
    vote_service.attach_message_reference(
        vote_round.id, interaction.guild_id, interaction.channel_id, sent_message.id
    )

    logger.info(
        "User %s started voting round %s with %s nominee(s) in database %s "
        "(guild %s, channel %s)",
        interaction.user.id,
        vote_round.id,
        len(candidates),
        vote_round.database_id,
        interaction.guild_id,
        interaction.channel_id,
    )


async def handle_start_vote_use_defaults(
    interaction: discord.Interaction,
    vote_service: VoteService,
    suggestion_service: SuggestionService,
    nominee_selection_service: Optional[NomineeSelectionService],
    wash_crew_role_id: Optional[int],
    default_nominee_count: int,
    scheduler_service: Optional[SchedulerService] = None,
    guild_configuration_repository: Optional[GuildConfigurationRepository] = None,
    suggestion_database_configuration_repository: Optional[SuggestionDatabaseConfigurationRepository] = None,
    bot: Optional["WatchPartyBot"] = None,
) -> None:
    """Start a round using the configured defaults, including the guild's
    configured default voting visibility (visibility_str=None; see
    parse_vote_visibility)."""
    await handle_start_vote_completion(
        interaction,
        vote_service,
        suggestion_service,
        nominee_selection_service,
        wash_crew_role_id,
        visibility_str=None,
        duration_minutes=None,
        nominee_count=None,
        default_nominee_count=default_nominee_count,
        scheduler_service=scheduler_service,
        guild_configuration_repository=guild_configuration_repository,
        suggestion_database_configuration_repository=suggestion_database_configuration_repository,
        bot=bot,
    )


async def handle_customize_vote_submit(
    interaction: discord.Interaction,
    vote_service: VoteService,
    suggestion_service: SuggestionService,
    nominee_selection_service: Optional[NomineeSelectionService],
    wash_crew_role_id: Optional[int],
    default_nominee_count: int,
    nominee_count_text: Optional[str],
    duration_text: Optional[str],
    reminder_enabled_text: Optional[str] = None,
    reminder_minutes_text: Optional[str] = None,
    scheduler_service: Optional[SchedulerService] = None,
    guild_configuration_repository: Optional[GuildConfigurationRepository] = None,
    suggestion_database_configuration_repository: Optional[SuggestionDatabaseConfigurationRepository] = None,
    bot: Optional["WatchPartyBot"] = None,
    candidate_selection_override: Optional[CandidateSelectionMode] = None,
    visibility_override: Optional[GuildVoteVisibility] = None,
    resolved_database_id: Optional[int] = None,
    filter_member_discord_user_id: Optional[int] = None,
    filter_member_display: Optional[str] = None,
    filter_genre: Optional[str] = None,
    filter_imdb_rating_min: Optional[float] = None,
    filter_imdb_rating_max: Optional[float] = None,
    filter_mpaa_rating: Optional[str] = None,
    filter_actor: Optional[str] = None,
    on_change_filters: Optional[Callable[[discord.Interaction], Awaitable[None]]] = None,
    on_change_collection: Optional[Callable[[discord.Interaction], Awaitable[None]]] = None,
    on_back_to_overrides: Optional[Callable[[discord.Interaction], Awaitable[None]]] = None,
) -> None:
    """Show Custom Vote Summary & Announcement's pre-creation review
    screen for optional one-time modal overrides; the round itself is
    only created once that screen's Start Vote button is pressed (see
    on_confirm below) -- Filtered-Pool Validation: nothing is persisted
    or announced before this function returns.

    on_change_filters/on_change_collection/on_back_to_overrides: Custom
    Vote UX Polish: when supplied (only show_customize_vote_overrides
    supplies them -- every other caller leaves these None, keeping their
    existing plain-message behavior unchanged), an eager filtered-pool
    check runs before the Review This Vote screen is ever built. Too few
    eligible suggestions shows InsufficientFilteredPoolView (Change
    Filters/Change Collection/Back/Cancel) instead of a dead-end message,
    with every current setting and filter preserved -- mirroring
    /browse's own empty-results recovery. perform_start_vote's own
    authoritative check (called later, from this screen's Start Vote)
    is unchanged and still runs regardless -- this is purely an earlier,
    friendlier catch of the exact same condition, the same "resolved
    purely for display" idiom the duration/nominee-count checks just
    below already use.

    candidate_selection_override: UI Polish (Voting Configuration
    Improvements): the mode chosen on Customize This Vote's own
    candidate-selection dropdown (a separate step before this modal,
    since Discord modals can't contain Select menus -- see
    CustomizeVoteOverridesView). None uses the resolved database's own
    configured mode, unchanged.

    visibility_override: General Fixed-Option Control Audit: the
    visibility chosen on that same dropdown screen, alongside candidate
    selection. None uses the guild's own configured default visibility,
    unchanged -- see resolve_customize_vote_default_visibility.

    resolved_database_id: Custom Vote Filter Architecture: the
    collection already resolved before CustomizeVoteOverridesView was
    ever shown (see the /vote start command's on_customize closure) --
    passed straight through to handle_start_vote_completion so the
    Customize flow never re-resolves (or re-prompts for) the collection
    a second time.

    filter_member_discord_user_id/filter_member_display/filter_genre:
    Custom Vote Filter Architecture: the optional per-vote filters
    chosen alongside candidate_selection_override/visibility_override on
    CustomizeVoteOverridesView, carried through to both this screen's
    summary text and, once confirmed, to the actual round creation.
    """
    try:
        nominee_count, duration_minutes, _, reminder_enabled, reminder_minutes_before_close = (
            parse_start_vote_overrides(
                nominee_count_text, duration_text, None, reminder_enabled_text, reminder_minutes_text
            )
        )
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    if wash_crew_role_id is None:
        await interaction.response.send_message(
            "WASH Crew permissions have not been configured. Set WASH_CREW_ROLE_ID before using this command.",
            ephemeral=True,
        )
        return
    if not is_wash_crew_member(interaction.user, wash_crew_role_id):
        await interaction.response.send_message(
            "You need the WASH Crew role to start a voting round.", ephemeral=True
        )
        return

    visibility_str = visibility_override.value if visibility_override is not None else None

    # Every value shown below is resolved purely for display --
    # perform_start_vote (only invoked once Start Vote is actually
    # pressed) re-resolves and re-validates everything authoritatively,
    # so nothing here is trusted as a substitute for that.
    collection_name = None
    if resolved_database_id is not None:
        database = suggestion_service.get_database(resolved_database_id)
        if database is not None:
            collection_name = format_collection_display(
                _resolve_collection_name(
                    suggestion_service, database, interaction.guild, suggestion_database_configuration_repository
                )
            )

    default_visibility = GuildVoteVisibility.VISIBLE
    default_duration_minutes = DEFAULT_VOTE_DURATION_MINUTES
    if guild_configuration_repository is not None and interaction.guild_id is not None:
        configuration = guild_configuration_repository.get(interaction.guild_id)
        if configuration is not None:
            default_visibility = configuration.voting_defaults.visibility
            default_duration_minutes = configuration.voting_defaults.duration_minutes
    effective_visibility = visibility_override if visibility_override is not None else default_visibility

    # Vote Creation Validation: resolve nominee count/duration/reminder
    # timing against their configured bounds now (reusing the exact
    # functions perform_start_vote itself calls), so a clearly invalid
    # value (e.g. a candidate count outside 2-10) is rejected immediately
    # rather than only after Start Vote is pressed on a summary screen
    # showing settings that could never have been created. Once
    # validated, nominee_count/duration_minutes/reminder_minutes_before_close
    # hold their concrete, already-in-range values, so perform_start_vote's
    # own re-validation at confirm time is a no-op pass-through.
    try:
        duration_minutes = parse_vote_duration_minutes(duration_minutes, default=default_duration_minutes)
        nominee_count = parse_vote_nominee_count(nominee_count, default=default_nominee_count)
        reminder_minutes_before_close = parse_vote_reminder_minutes_before_close(reminder_minutes_before_close)
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    effective_duration_minutes = duration_minutes
    effective_candidate_count = nominee_count

    active_filters = build_nominee_pool_filters(
        filter_genre=filter_genre,
        filter_imdb_rating_min=filter_imdb_rating_min,
        filter_imdb_rating_max=filter_imdb_rating_max,
        filter_mpaa_rating=filter_mpaa_rating,
        filter_actor=filter_actor,
        filter_member_discord_user_id=filter_member_discord_user_id,
        filter_member_display=filter_member_display,
    )
    if active_filters and resolved_database_id is not None and on_change_filters is not None:
        filtered_pool = FilteredCandidateSelectionStrategy(
            inner=InfinitePoolStrategy(suggestion_source=suggestion_service), filters=active_filters
        ).candidate_pool(resolved_database_id)
        if len(filtered_pool) < effective_candidate_count:
            insufficient_message = build_insufficient_filtered_pool_message(
                len(filtered_pool),
                effective_candidate_count,
                member_display=filter_member_display,
                genre=filter_genre,
                imdb_rating_min=filter_imdb_rating_min,
                imdb_rating_max=filter_imdb_rating_max,
                mpaa_rating=filter_mpaa_rating,
                actor=filter_actor,
            )

            async def on_cancel_insufficient(cancel_interaction: discord.Interaction) -> None:
                await cancel_interaction.response.edit_message(content="Vote creation cancelled.", view=None)

            await interaction.response.send_message(
                insufficient_message,
                view=InsufficientFilteredPoolView(
                    on_change_filters=on_change_filters,
                    on_change_collection=on_change_collection or on_cancel_insufficient,
                    on_back=on_back_to_overrides or on_cancel_insufficient,
                    on_cancel=on_cancel_insufficient,
                ),
                ephemeral=True,
            )
            return

    effective_candidate_selection_mode = candidate_selection_override
    if (
        effective_candidate_selection_mode is None
        and resolved_database_id is not None
        and suggestion_database_configuration_repository is not None
        and interaction.guild_id is not None
    ):
        database_configuration = suggestion_database_configuration_repository.get(
            interaction.guild_id, resolved_database_id
        )
        effective_candidate_selection_mode = (
            database_configuration.suggestion_rules.candidate_selection
            if database_configuration is not None
            else DEFAULT_CANDIDATE_SELECTION_MODE
        )

    summary_text = build_customize_vote_summary_text(
        collection_name=collection_name,
        candidate_selection_mode=effective_candidate_selection_mode,
        visibility=effective_visibility,
        candidate_count=effective_candidate_count,
        duration_minutes=effective_duration_minutes,
        filter_member_display=filter_member_display,
        filter_genre=filter_genre,
        filter_imdb_rating_min=filter_imdb_rating_min,
        filter_imdb_rating_max=filter_imdb_rating_max,
        filter_mpaa_rating=filter_mpaa_rating,
        filter_actor=filter_actor,
    )

    async def on_confirm(confirm_interaction: discord.Interaction) -> None:
        await handle_start_vote_completion(
            confirm_interaction,
            vote_service,
            suggestion_service,
            nominee_selection_service,
            wash_crew_role_id,
            visibility_str=visibility_str,
            duration_minutes=duration_minutes,
            nominee_count=nominee_count,
            default_nominee_count=default_nominee_count,
            scheduler_service=scheduler_service,
            guild_configuration_repository=guild_configuration_repository,
            reminder_enabled=reminder_enabled,
            reminder_minutes_before_close=reminder_minutes_before_close,
            suggestion_database_configuration_repository=suggestion_database_configuration_repository,
            resolved_database_id=resolved_database_id,
            bot=bot,
            candidate_selection_override=candidate_selection_override,
            filter_member_discord_user_id=filter_member_discord_user_id,
            filter_member_display=filter_member_display,
            filter_genre=filter_genre,
            filter_imdb_rating_min=filter_imdb_rating_min,
            filter_imdb_rating_max=filter_imdb_rating_max,
            filter_mpaa_rating=filter_mpaa_rating,
            filter_actor=filter_actor,
        )

    async def on_abort(abort_interaction: discord.Interaction) -> None:
        await abort_interaction.response.edit_message(content="Vote creation cancelled.", view=None)

    confirmation_view = EditVoteConfirmationView(
        confirm_label="Start Vote",
        on_confirm=on_confirm,
        on_abort=on_abort,
        confirm_style=discord.ButtonStyle.primary,
    )
    await interaction.response.send_message(summary_text, view=confirmation_view, ephemeral=True)


async def show_customize_vote_overrides(
    interaction: discord.Interaction,
    bot: "WatchPartyBot",
    database_id: Optional[int],
    *,
    initial_filter_state: Optional[dict] = None,
) -> None:
    """Custom Vote's "Customize This Vote" screen (Nominee Selection/
    Visibility overrides, Edit Filters, then Vote Settings/Review This
    Vote) for an already-resolved collection.

    Extracted from /vote start's own on_customize closure (Custom Vote
    Filter Architecture) so /browse's Start Vote action (Crew only) can
    launch the exact same screen -- reusing this function, not
    reimplementing any of its filter menu wiring or Review This Vote
    hand-off -- with /browse's collection already resolved and, via
    initial_filter_state, its current filters carried over instead of
    starting blank (Section 9: "reuse the existing Custom Vote workflow
    ... do not rebuild filters"). initial_filter_state defaults to None
    so /vote start's own call sites are unaffected -- a blank
    new_filter_session_state() is used exactly as before.
    """
    # Custom Vote Filter Architecture: the collection must be known
    # before the filter menu can be built (its eligible pool and
    # eligible-count previews both depend on it) -- unlike "Use
    # Defaults", whose collection resolution can safely stay late (see
    # handle_start_vote_completion's own ambiguity check).
    eligible_pool: List[WatchItem] = (
        InfinitePoolStrategy(suggestion_source=bot.suggestion_service).candidate_pool(database_id)
        if database_id is not None
        else []
    )
    collection_display_name = "this collection"
    if database_id is not None:
        resolved_database = bot.suggestion_service.get_database(database_id)
        if resolved_database is not None:
            collection_display_name = format_collection_display(
                _resolve_collection_name(
                    bot.suggestion_service,
                    resolved_database,
                    interaction.guild,
                    bot.suggestion_database_configuration_repository,
                )
            )

    async def continue_with_overrides(
        continue_interaction: discord.Interaction,
        candidate_selection_override: CandidateSelectionMode,
        visibility_override: GuildVoteVisibility,
    ) -> None:
        if filter_state["member_filter_invalid"]:
            # Defense in depth: the Continue button is already disabled
            # while a selection is invalid, but this guard is what
            # actually guarantees an invalid member can never reach the
            # modal, Review This Vote, or VoteRound -- not merely
            # Discord's client-side disabled-button behavior.
            await continue_interaction.response.edit_message(
                content=build_overrides_body(filter_state["member_filter_status_line"]),
                view=overrides_view,
            )
            return

        async def on_modal_submit(
            modal_interaction: discord.Interaction,
            nominee_count_text: Optional[str],
            duration_text: Optional[str],
            reminder_enabled_text: Optional[str],
            reminder_minutes_text: Optional[str],
        ) -> None:
            await submit_customize_vote(
                modal_interaction,
                candidate_selection_override,
                visibility_override,
                nominee_count_text,
                duration_text,
                reminder_enabled_text,
                reminder_minutes_text,
            )

        modal_defaults = build_customize_vote_modal_defaults(
            default_nominee_count=bot.default_nominee_count,
            guild_id=continue_interaction.guild_id,
            guild_configuration_repository=bot.guild_configuration_repository,
        )
        await continue_interaction.response.send_modal(CustomizeVoteModal(on_modal_submit, **modal_defaults))

    async def submit_customize_vote(
        submit_interaction: discord.Interaction,
        candidate_selection_override: CandidateSelectionMode,
        visibility_override: GuildVoteVisibility,
        nominee_count_text: Optional[str],
        duration_text: Optional[str],
        reminder_enabled_text: Optional[str],
        reminder_minutes_text: Optional[str],
    ) -> None:
        await handle_customize_vote_submit(
            submit_interaction,
            vote_service=bot.vote_service,
            suggestion_service=bot.suggestion_service,
            nominee_selection_service=bot.nominee_selection_service,
            wash_crew_role_id=resolve_permission_service(
                submit_interaction.guild_id, bot.guild_configuration_repository
            ).wash_crew_role_id,
            default_nominee_count=bot.default_nominee_count,
            nominee_count_text=nominee_count_text,
            duration_text=duration_text,
            reminder_enabled_text=reminder_enabled_text,
            reminder_minutes_text=reminder_minutes_text,
            scheduler_service=bot.scheduler_host.scheduler_service,
            guild_configuration_repository=bot.guild_configuration_repository,
            suggestion_database_configuration_repository=bot.suggestion_database_configuration_repository,
            bot=bot,
            candidate_selection_override=candidate_selection_override,
            visibility_override=visibility_override,
            resolved_database_id=database_id,
            filter_member_discord_user_id=filter_state["member_id"],
            filter_member_display=filter_state["member_display"],
            filter_genre=filter_state["genre"],
            filter_imdb_rating_min=filter_state["imdb_rating_min"],
            filter_imdb_rating_max=filter_state["imdb_rating_max"],
            filter_mpaa_rating=filter_state["mpaa_rating"],
            filter_actor=filter_state["actor"],
            on_change_filters=on_change_filters_from_insufficient,
            on_change_collection=on_change_collection_from_insufficient,
            on_back_to_overrides=on_back_to_overrides,
        )

    async def start_vote_with_current_settings(
        start_interaction: discord.Interaction,
        candidate_selection_override: CandidateSelectionMode,
        visibility_override: GuildVoteVisibility,
    ) -> None:
        # Custom Vote UX Polish: skips Vote Settings' modal entirely --
        # every field blank means "use the configured default", exactly
        # as an untouched modal submission already would.
        if filter_state["member_filter_invalid"]:
            await start_interaction.response.edit_message(
                content=build_overrides_body(filter_state["member_filter_status_line"]),
                view=overrides_view,
            )
            return
        await submit_customize_vote(start_interaction, candidate_selection_override, visibility_override, None, None, None, None)

    async def on_change_filters_from_insufficient(action_interaction: discord.Interaction) -> None:
        await show_filter_menu(action_interaction)

    async def on_change_collection_from_insufficient(action_interaction: discord.Interaction) -> None:
        # Custom Vote UX Polish: mirrors /browse's own Change Collection --
        # every other collection in this guild, current filters carried
        # over (revalidated the same way a fresh session would be, since
        # show_customize_vote_overrides is simply re-entered for the newly
        # chosen collection).
        recovery_guild_id = action_interaction.guild_id
        databases = (
            [database for database in bot.suggestion_service.list_databases(recovery_guild_id) if database.active]
            if recovery_guild_id is not None
            else []
        )
        if not databases:
            await action_interaction.response.edit_message(content="No other collections are available.", view=None)
            return

        options = [
            (
                database.database_id,
                format_collection_display(
                    _resolve_collection_name(
                        bot.suggestion_service, database, action_interaction.guild, bot.suggestion_database_configuration_repository
                    )
                ),
            )
            for database in databases
        ]

        async def on_database_chosen(select_interaction: discord.Interaction, chosen_database_id: int) -> None:
            await show_customize_vote_overrides(
                select_interaction, bot, chosen_database_id, initial_filter_state=dict(filter_state)
            )

        await action_interaction.response.edit_message(
            content="Which collection would you like to use?",
            view=ListDatabaseSelectView(options, on_database_chosen),
        )

    def build_overrides_body(status_line: str = "") -> str:
        paragraphs = [
            "Optionally override this vote's Nominee Selection Mode and/or Vote Visibility "
            "below -- both apply to this round only and never change the collection's or "
            "guild's own configured setting."
        ]
        if database_id is not None:
            paragraphs.append(
                "Use Edit Filters to optionally narrow the eligible pool first (Genre, "
                "IMDb Rating, MPAA Rating, Actor, Member) -- Nominee Selection then chooses "
                "this round's nominees from that narrowed pool."
            )
            paragraphs.append(build_current_filters_summary(filter_state))
        if status_line:
            paragraphs.append(status_line)
        paragraphs.append(
            "Press **Start Vote** to create this round now, using the configured defaults for candidate "
            "count and vote duration, or **Continue to Vote Settings** to override either one first. "
            f"{VOTE_DURATION_CLARIFICATION}"
        )
        return "\n\n".join(paragraphs)

    async def on_filter_menu_continue(menu_interaction: discord.Interaction, _filter_state: dict) -> None:
        # Reachable directly from the filter menu screen too, so WASH
        # Crew never has to go Back just to finish -- reads whichever
        # Nominee Selection/Visibility choices are still selected on the
        # (unmodified, still-alive) overrides_view.
        await continue_with_overrides(
            menu_interaction, overrides_view.candidate_selection_select.selected, overrides_view.visibility_select.selected
        )

    async def on_back_to_overrides(back_interaction: discord.Interaction) -> None:
        # Keep the two screens' Continue buttons in sync: an invalid
        # member filter set from within the filter menu must also
        # disable this screen's own Continue, not just the filter
        # menu's.
        overrides_view.continue_button.disabled = filter_state["member_filter_invalid"]
        await back_interaction.response.edit_message(content=build_overrides_body(), view=overrides_view)

    filter_state, show_filter_menu = create_filter_menu_session(
        bot=bot,
        eligible_pool=eligible_pool,
        collection_display_name=collection_display_name,
        body_header=(
            f'**Customize This Vote -- "{collection_display_name}"**\n\n'
            "Optionally narrow the eligible pool using the filter menu, then continue to this vote's settings."
        ),
        on_primary_action=on_filter_menu_continue,
        primary_action_label="Continue to Vote Settings",
        primary_action_custom_id="wpm_start_vote_customize_filter_menu_continue",
        on_secondary_action=on_back_to_overrides,
        secondary_action_label="Back to Vote Settings",
    )
    if initial_filter_state is not None:
        filter_state.update(initial_filter_state)

    async def on_edit_filters(edit_filters_interaction: discord.Interaction) -> None:
        await show_filter_menu(edit_filters_interaction)

    default_candidate_selection = (
        resolve_candidate_selection_mode(bot, interaction.guild_id, database_id)
        if database_id is not None
        else DEFAULT_CANDIDATE_SELECTION_MODE
    )
    default_visibility = resolve_customize_vote_default_visibility(interaction.guild_id, bot.guild_configuration_repository)
    overrides_view = CustomizeVoteOverridesView(
        continue_with_overrides,
        default_candidate_selection=default_candidate_selection,
        default_visibility=default_visibility,
        on_edit_filters=on_edit_filters if database_id is not None else None,
        on_start_vote=start_vote_with_current_settings,
    )
    await interaction.response.send_message(build_overrides_body(), view=overrides_view, ephemeral=True)


def perform_vote_status(
    vote_service: VoteService,
    suggestion_service: SuggestionService,
    database_id: Optional[int] = None,
) -> str:
    """Core logic for /vote_status, kept free of Discord objects entirely.

    Standings are shown when voting is visible, or once a blind round has
    closed. They're withheld only while a blind round is still open.

    Args:
        vote_service: The vote service to read round/standings from.
        suggestion_service: Used to report the current candidate count.
        database_id: The collection to scope the lookup to -- shows that
            collection's own most recent round rather than whichever
            round happens to have the highest ID across every collection.
            Optional so existing callers/tests without a resolved
            collection keep working unchanged (see
            VoteService.get_latest_round).

    Returns:
        The status message, or a clear "no round exists" message.
    """
    vote_round = vote_service.get_latest_round(database_id)
    if vote_round is None:
        return "There is no voting round yet."

    show_standings = (
        vote_round.visibility == VoteVisibility.VISIBLE or vote_round.status == VoteRoundStatus.CLOSED
    )

    standings: Optional[List[StandingsEntry]] = None
    standings_error: Optional[str] = None
    if show_standings:
        standings_result = vote_service.calculate_standings(vote_round.id)
        if standings_result.success:
            standings = standings_result.standings
        else:
            standings_error = standings_result.message

    candidate_count = (
        len(vote_round.candidate_suggestion_ids)
        if vote_round.candidate_suggestion_ids
        else suggestion_service.suggestion_count()
    )
    candidates = get_round_candidates(suggestion_service, vote_round)
    guild_round_number = (
        vote_service.get_guild_round_number(vote_round.id, vote_round.guild_id)
        if vote_round.guild_id is not None
        else None
    )
    return build_vote_status_text(
        vote_round, candidate_count, standings, standings_error, candidates, guild_round_number
    )


async def handle_vote_status(interaction: discord.Interaction, bot: "WatchPartyBot") -> None:
    """Handle /vote_status: show the invoking collection's own vote status.

    Contextual Collection Resolution: resolves which collection's round
    to report on the same way every other collection-scoped command
    does -- automatically when the channel unambiguously identifies one,
    a "Which collection?" picker when it doesn't, a clear error when no
    collection is configured at all (see resolve_database_then).
    """
    permission = resolve_permission_service(
        interaction.guild_id, bot.guild_configuration_repository
    ).require_wash_crew(interaction.user)
    if not permission.allowed:
        await interaction.response.send_message(permission.message, ephemeral=True)
        return
    guild_id = interaction.guild_id
    if guild_id is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    async def on_resolved(resolved_interaction: discord.Interaction, database: SuggestionDatabase) -> None:
        message = perform_vote_status(
            vote_service=bot.vote_service,
            suggestion_service=bot.suggestion_service,
            database_id=database.database_id,
        )
        await resolved_interaction.response.send_message(message, ephemeral=True)

    await resolve_database_then(interaction, bot, guild_id, interaction.channel_id, on_resolved)


def build_vote_confirmation(
    vote_record: VoteRecord,
    is_first_vote: bool,
    remaining_changes: int,
    vote_round: Optional[VoteRound] = None,
    watch_item: Optional[WatchItem] = None,
) -> str:
    """Build the vote confirmation message shown after casting a vote.

    Args:
        vote_record: The member's own vote record after casting.
        is_first_vote: True if this was the member's first vote this round.
        remaining_changes: How many vote changes the member has left.
        vote_round: The round voted in, used to include a link to the
            original voting post when available. Optional so existing
            callers that don't have it keep working unchanged; None
            simply omits the link.
        watch_item: The voted-for suggestion, used to name it by title
            (Release Polish Batch 2, Priority 4 -- the internal
            suggestion number is no longer the user-facing label).
            Optional so a caller with no resolvable candidate (e.g. it
            was removed between button click and confirmation) still
            gets a graceful message rather than a failure.

    Returns:
        A confirmation message. Never mentions any other member's vote.
    """
    if watch_item is not None:
        # UX Polish: linked to the original suggestion post when
        # available, matching how every candidate title is shown in the
        # standings block that can appear right below this same message
        # (see vote_announcement_formatter.py's _format_candidate_title).
        candidate_label = format_title_with_year(watch_item.title, watch_item.release_year)
        suggestion_link = build_suggestion_link(watch_item)
        if suggestion_link:
            candidate_label = f"[{candidate_label}]({suggestion_link})"
    else:
        candidate_label = f"suggestion #{vote_record.suggestion_id}"
    if is_first_vote:
        lines = [f"Your vote for {candidate_label} has been recorded."]
    else:
        lines = [f"Your vote has been updated to {candidate_label}."]
        if remaining_changes > 0:
            change_word = "change" if remaining_changes == 1 else "changes"
            lines.append(f"You have {remaining_changes} vote {change_word} remaining.")
        else:
            lines.append("You have no vote changes remaining.")

    if vote_round is not None:
        link = build_vote_link(vote_round)
        if link:
            lines.append(f"Original post: {link}")

    return "\n".join(lines)


def perform_vote(
    vote_service: VoteService,
    user_id: int,
    suggestion_id: int,
    suggestion_service: Optional[SuggestionService] = None,
) -> tuple[str, bool]:
    """Core vote-casting logic, kept entirely free of Discord objects.

    Used exclusively by interactive voting's nominee buttons (see
    handle_nominee_vote) -- FR-029 removed the /vote slash command in
    favor of interactive voting only, but this underlying logic is
    unchanged and still fully exercised.

    All eligibility rules — an open round existing, the suggestion ID
    existing, one active vote per member, one allowed change per member —
    are enforced by VoteService.cast_vote(). This function never
    duplicates those checks; it only decides how to present the result
    and whether to attach standings.

    Args:
        vote_service: The vote service to cast the vote through.
        user_id: The voting member's Discord user ID.
        suggestion_id: The suggestion ID they're voting for.
        suggestion_service: Used to resolve the voted candidate's (and
            every other candidate's) title for display, rather than the
            internal suggestion number (Release Polish Batch 2,
            Priorities 3-4). Optional so existing callers with no
            suggestion service in scope keep working -- confirmations
            and standings gracefully fall back to the bare suggestion
            number when unset or a candidate can't be resolved.

    Returns:
        A (message, ephemeral) tuple. Every response is ephemeral — a
        member's own vote, and any standings shown alongside it, are for
        their eyes only.
    """
    # Resolved by suggestion_id, not "the" open round -- a nominee button
    # only ever carries suggestion_id (see NomineeButton.custom_id), and
    # with multiple collections each possibly having their own open
    # round, this is the only way to know which one this click belongs
    # to (see VoteService.get_open_round_for_suggestion's docstring).
    open_round_before = vote_service.get_open_round_for_suggestion(suggestion_id)
    had_existing_vote = open_round_before is not None and user_id in open_round_before.votes

    result = vote_service.cast_vote(discord_user_id=user_id, suggestion_id=suggestion_id)
    if not result.success:
        return result.message, True

    # cast_vote() succeeded, so there is now an open round with this
    # member's vote recorded in it.
    vote_round = vote_service.get_open_round_for_suggestion(suggestion_id)
    vote_record = vote_round.votes[user_id]
    is_first_vote = not had_existing_vote
    remaining_changes = MAX_VOTE_CHANGES - vote_record.changes_used

    watch_item = suggestion_service.get_suggestion(suggestion_id) if suggestion_service is not None else None
    lines = [build_vote_confirmation(vote_record, is_first_vote, remaining_changes, vote_round, watch_item)]

    if vote_round.visibility == VoteVisibility.VISIBLE:
        candidates = get_round_candidates(suggestion_service, vote_round) if suggestion_service is not None else None
        standings_result = vote_service.calculate_standings(vote_round.id)
        if standings_result.success:
            lines.extend(format_standings_lines(standings_result.standings, None, candidates))
        else:
            lines.extend(format_standings_lines(None, standings_result.message, candidates))

    return "\n".join(lines), True


def build_voting_view(
    vote_service: VoteService,
    suggestion_service: SuggestionService,
    candidates: List[WatchItem],
    permission_service: Optional[PermissionService] = None,
) -> VotingView:
    """Build a voting view whose buttons use the shared vote handler."""

    async def on_vote_click(
        interaction: discord.Interaction, suggestion_id: int
    ) -> None:
        await handle_nominee_vote(
            interaction,
            vote_service,
            suggestion_service,
            suggestion_id,
            permission_service=permission_service,
        )

    return VotingView(candidates, on_vote=on_vote_click)


async def reconcile_all_automatic_backup_schedules(bot: "WatchPartyBot") -> None:
    """Reconcile every configured guild's automatic-backup schedule
    against its current settings (Release Polish: Optional Automatic
    Backups).

    Called once at startup (setup_hook): guarantees a job is always
    scheduled while enabled -- even if WASH was offline when it would
    normally have rescheduled itself -- and never left scheduled after
    being disabled while offline, without ever duplicating an
    already-active job (reconcile_automatic_backup_schedule always
    cancels the current one first, see backup_scheduling.py). One
    guild's failure here must never block another's, or the rest of
    startup.
    """
    for guild_configuration in bot.guild_configuration_repository.list_all():
        try:
            await reconcile_automatic_backup_schedule(
                bot.scheduler_host.scheduler_service,
                guild_configuration.guild_id,
                guild_configuration_repository=bot.guild_configuration_repository,
            )
        except Exception:
            logger.exception(
                "Error reconciling automatic backup schedule for guild %s", guild_configuration.guild_id
            )


def restore_persistent_voting_views(
    bot: object,
    vote_service: VoteService,
    suggestion_service: SuggestionService,
    guild_configuration_repository: Optional[GuildConfigurationRepository] = None,
) -> int:
    """Restore button handling for every currently open voting post.

    Discord persistent views must be re-registered each time the bot
    starts. Each collection may have its own independently open round
    (see VoteService.get_open_rounds), so every one of them needs its own
    restored view -- restoring only the first would silently leave every
    other collection's voting buttons dead until its round happens to be
    the one restored after a future restart. Each round's own Discord
    message reference is already persisted, so this reconstructs the
    same stable button custom IDs and binds a view to each original
    message independently; one round failing to restore (e.g. a missing
    message ID or no resolvable nominees) never prevents the others from
    being restored.

    Multi-Guild Isolation, Phase 3b: open rounds restored here may belong
    to any number of different guilds, so each round's own vote_round.guild_id
    -- not a single shared permission_service -- resolves that round's own
    vote button permissions (see resolve_permission_service). A round with
    no guild_id (legacy data predating guild scoping) resolves to an
    unconfigured PermissionService, exactly as an unresolvable guild_id
    already did before this phase.

    Returns:
        The number of rounds whose interactive voting controls were
        successfully restored.
    """
    open_rounds = vote_service.get_open_rounds()
    if not open_rounds:
        logger.debug("No open voting round found; no persistent view to restore")
        return 0

    restored = 0
    for vote_round in open_rounds:
        if vote_round.message_id is None:
            logger.warning(
                "Open voting round %s has no message ID; interactive buttons cannot be restored",
                vote_round.id,
            )
            continue

        candidates = get_round_candidates(suggestion_service, vote_round)
        if not candidates:
            logger.warning(
                "Open voting round %s has no resolvable nominees; interactive buttons cannot be restored",
                vote_round.id,
            )
            continue

        view = build_voting_view(
            vote_service,
            suggestion_service,
            candidates,
            permission_service=resolve_permission_service(
                vote_round.guild_id, guild_configuration_repository
            ),
        )
        bot.add_view(view, message_id=vote_round.message_id)
        logger.info(
            "Restored interactive voting controls for round %s on message %s",
            vote_round.id,
            vote_round.message_id,
        )
        restored += 1

    return restored


def restore_persistent_membership_approval_views(bot: "WatchPartyBot", membership_service: MembershipService) -> int:
    """Restore Approve/Deny button handling for every still-pending membership request.

    Every approval-request message is created fresh by this feature with
    its buttons already attached (unlike suggestion posts, which predate
    their button and needed a migration path) -- so, exactly like
    restore_persistent_voting_views, this only needs to re-register
    callback routing via bot.add_view(), never edit a message.

    Returns:
        The number of pending requests whose buttons were restored.
    """
    on_approve, on_deny = _build_membership_decision_callbacks(bot)
    restored = 0
    for request in membership_service.list_pending_requests():
        if request.message_id is None:
            continue
        view = MembershipApprovalView(request.request_id, on_approve, on_deny)
        bot.add_view(view, message_id=request.message_id)
        restored += 1
    return restored


def build_suggestion_view(
    suggestion_service: SuggestionService,
    suggestion_database_configuration_repository: Optional[SuggestionDatabaseConfigurationRepository],
    watch_item: WatchItem,
    guild_id: Optional[int],
    permission_service: Optional[PermissionService] = None,
    bot: Optional["WatchPartyBot"] = None,
) -> SuggestionView:
    """Build a suggestion's "I Won't Watch" view whose button uses the shared toggle handler.

    Args:
        suggestion_service: Used by the button's toggle callback.
        suggestion_database_configuration_repository: Used to resolve the
            suggestion's configured rejection threshold, both for the
            button's displayed count and for the toggle callback.
        watch_item: The suggestion this view belongs to.
        guild_id: The Discord guild the suggestion belongs to, used to
            resolve its configured rejection threshold. Falls back to the
            documented default threshold if unavailable (see
            resolve_rejection_threshold).
        permission_service: Passed through to the toggle callback.
        bot: Passed through to the toggle callback so a rejection that
            crosses the archive threshold can resync this post's own
            Status field (Suggestion Status Synchronization), not just
            its button. Optional, matching every other caller here --
            omitted only in contexts (e.g. direct unit tests) with no
            bot instance available, where the toggle callback falls back
            to its previous view-only refresh.
    """
    threshold = resolve_rejection_threshold(
        suggestion_database_configuration_repository, guild_id, watch_item.database_id
    )
    rejection_enabled = resolve_rejection_enabled(
        suggestion_database_configuration_repository, guild_id, watch_item.database_id
    )

    async def on_toggle(interaction: discord.Interaction, suggestion_id: int) -> None:
        await handle_suggestion_rejection_toggle(
            interaction,
            suggestion_service,
            suggestion_database_configuration_repository,
            suggestion_id,
            permission_service=permission_service,
            bot=bot,
        )

    async def on_watched(interaction: discord.Interaction, suggestion_id: int) -> None:
        await handle_watched_button_click(interaction, bot, suggestion_id, permission_service=permission_service)

    return SuggestionView(watch_item, threshold, on_toggle, on_watched, rejection_enabled=rejection_enabled)


def _suggestion_message_has_custom_id(message: object, custom_id: str) -> bool:
    """Check whether a fetched suggestion message already carries a
    component with this custom_id.

    A real discord.py Message's components are a list of top-level
    ActionRows, each holding the actual Button/SelectMenu children -- so
    both the top-level components and one level of nested children are
    checked. Used by restore_persistent_suggestion_views() to decide
    whether a legacy message (posted before the reject and/or Watched
    button existed) needs to be edited to attach the current
    SuggestionView, or already has both.
    """
    for component in getattr(message, "components", []):
        if getattr(component, "custom_id", None) == custom_id:
            return True
        for child in getattr(component, "children", []):
            if getattr(child, "custom_id", None) == custom_id:
                return True
    return False


async def restore_persistent_suggestion_views(
    bot: object,
    suggestion_service: SuggestionService,
    suggestion_database_configuration_repository: Optional[SuggestionDatabaseConfigurationRepository] = None,
    guild_configuration_repository: Optional[GuildConfigurationRepository] = None,
) -> int:
    """Restore, and where needed migrate, the "I Won't Watch" button for every active suggestion post.

    Discord persistent views must be re-registered each time the bot
    starts. bot.add_view(view, message_id=...) alone only re-establishes
    callback routing for a button that's *already present* on the
    message -- it does not add a button to a message that has none.
    Suggestion posts created before this feature existed have no button
    at all, so restoring callback routing for them would silently leave
    members unable to reject them.

    For each active (non-archived) suggestion with a known message ID,
    this function fetches the stored message and checks whether a
    matching button is already attached:

    - If it is, only callback routing is (re-)registered via
      bot.add_view(), exactly as before -- the normal persistent-view
      restoration path.
    - If it isn't (a legacy message with no button, or one whose
      components are otherwise missing), the message is edited to
      attach the current SuggestionView. discord.py's Message.edit()
      registers the view for callback routing as a side effect of being
      passed a dispatchable view, so no separate add_view() call is
      needed for that branch -- this never results in two views bound
      to the same message.

    A suggestion missing channel_id (metadata from before that field
    existed) cannot be fetched at all, so it falls back to best-effort
    callback-only registration exactly as this function always did.
    Any other failure to fetch or edit the message (deleted message,
    missing or inaccessible channel, insufficient permissions, or any
    other Discord-side error) is logged and that suggestion is skipped
    -- one bad reference never blocks startup or any other suggestion.

    Archived and Watched suggestions are skipped entirely: both of their
    buttons are already disabled and permanently so (see
    SuggestionService.reject_suggestion/remove_rejection/
    mark_suggestion_watched, none of which allow touching an archived or
    already-watched suggestion), so there's nothing to restore or
    migrate for them.

    Multi-Guild Isolation, Phase 3b: suggestions restored here may belong
    to any number of different guilds, so each suggestion's own
    watch_item.guild_id -- not a single shared permission_service --
    resolves that suggestion's own "I Won't Watch"/Watched button
    permissions (see resolve_permission_service). A suggestion with no
    guild_id (legacy data predating guild scoping) resolves to an
    unconfigured PermissionService, exactly as an unresolvable guild_id
    already did before this phase.

    Returns:
        The number of suggestion views restored or migrated.
    """
    restored = 0
    for watch_item in suggestion_service.get_suggestions():
        if watch_item.status in (WatchItemStatus.ARCHIVED, WatchItemStatus.WATCHED):
            continue
        if watch_item.message_id is None:
            continue

        view = build_suggestion_view(
            suggestion_service,
            suggestion_database_configuration_repository,
            watch_item,
            watch_item.guild_id,
            permission_service=resolve_permission_service(
                watch_item.guild_id, guild_configuration_repository
            ),
            bot=bot,
        )

        if watch_item.channel_id is None:
            bot.add_view(view, message_id=watch_item.message_id)
            restored += 1
            continue

        try:
            channel = bot.get_channel(watch_item.channel_id)
            if channel is None:
                channel = await bot.fetch_channel(watch_item.channel_id)
            message = await channel.fetch_message(watch_item.message_id)
        except Exception:
            logger.warning(
                "Could not fetch suggestion %s's message %s for persistent view "
                "restoration; skipping",
                watch_item.id,
                watch_item.message_id,
                exc_info=True,
            )
            continue

        reject_custom_id = build_reject_button_custom_id(watch_item.id)
        watched_custom_id = build_watched_button_custom_id(watch_item.id)
        has_both_buttons = _suggestion_message_has_custom_id(
            message, reject_custom_id
        ) and _suggestion_message_has_custom_id(message, watched_custom_id)
        if has_both_buttons:
            bot.add_view(view, message_id=watch_item.message_id)
        else:
            try:
                await message.edit(view=view)
            except Exception:
                logger.warning(
                    "Could not attach the current buttons to suggestion %s's "
                    "legacy message %s; skipping",
                    watch_item.id,
                    watch_item.message_id,
                    exc_info=True,
                )
                continue
            logger.info(
                "Attached the current buttons to legacy suggestion %s's message %s",
                watch_item.id,
                watch_item.message_id,
            )

        restored += 1

    logger.info("Restored %s persistent suggestion view(s)", restored)
    return restored


def get_round_candidates(
    suggestion_service: SuggestionService, vote_round: VoteRound
) -> List[WatchItem]:
    """Resolve a round's persisted nominees in their original order."""
    suggestions_by_id = {item.id: item for item in suggestion_service.get_suggestions()}
    if not vote_round.candidate_suggestion_ids:
        return list(suggestions_by_id.values())
    return [
        suggestions_by_id[candidate_id]
        for candidate_id in vote_round.candidate_suggestion_ids
        if candidate_id in suggestions_by_id
    ]


VOTE_PROGRESS_BAR_LENGTH = 10
VOTE_PROGRESS_BAR_FILLED_CHAR = "█"
VOTE_PROGRESS_BAR_EMPTY_CHAR = "░"


def build_vote_progress_bar(vote_count: int, total_votes: int, *, length: int = VOTE_PROGRESS_BAR_LENGTH) -> str:
    """Build a filled/empty block bar representing one candidate's share of the vote.

    Args:
        vote_count: This candidate's current vote count.
        total_votes: Total votes cast across every candidate in the round.
        length: How many block characters make up the bar.

    Returns:
        A string of exactly `length` block characters, e.g. "██████░░░░"
        for 6/10 votes. Entirely empty when total_votes is zero -- there
        is no share of nothing to depict.
    """
    if total_votes <= 0:
        filled = 0
    else:
        filled = max(0, min(length, round((vote_count / total_votes) * length)))
    return (VOTE_PROGRESS_BAR_FILLED_CHAR * filled) + (VOTE_PROGRESS_BAR_EMPTY_CHAR * (length - filled))


def build_candidate_standings_line(vote_count: int, total_votes: int) -> str:
    """Build one candidate's progress-bar line: bar, vote count, and percentage.

    Example: "██████░░░░ 6 votes • 60%". Only ever used for a visible
    round with standings successfully computed -- blind rounds and
    standings failures never call this (see build_candidate_standings_lines).
    """
    bar = build_vote_progress_bar(vote_count, total_votes)
    percentage = round((vote_count / total_votes) * 100) if total_votes > 0 else 0
    vote_word = "vote" if vote_count == 1 else "votes"
    return f"{bar} {vote_count} {vote_word} • {percentage}%"


def build_candidate_standings_lines(
    candidates: List[WatchItem],
    vote_round: VoteRound,
    standings: Optional[List[StandingsEntry]],
    standings_error: Optional[str],
) -> List[str]:
    """Build the voting post's single per-candidate presentation block.

    FR-025: replaces the old duplicate "Nominees:" list plus a
    separately vote-sorted "Standings:" section with one combined list,
    kept in the same order as each candidate's vote button. Each
    candidate is its own paragraph: a linked title (Release Polish
    Batch 2, Priority 4 removed the leading nominee number -- the
    candidate's own vote button below it is the only "which one is
    this" cue needed), followed for a visible round by its progress
    bar, vote count, and share. A blind round never reveals any of that.

    Args:
        candidates: The round's nominees, in button order.
        vote_round: The round, used for its visibility and total votes cast.
        standings: Per-suggestion vote tallies, or None if not available
            (a still-open blind round, or none computed yet).
        standings_error: A message to show instead of a standings line if
            calculating them failed, or None.

    Returns:
        The lines to display: one candidate paragraph after another
        separated by a blank line, followed by a trailing note for a
        blind round or a standings failure.
    """
    is_visible = vote_round.visibility == VoteVisibility.VISIBLE
    show_counts = is_visible and standings_error is None
    total_votes = len(vote_round.votes)
    vote_counts_by_suggestion_id = (
        {entry.suggestion_id: entry.vote_count for entry in standings} if standings is not None else {}
    )

    blocks: List[List[str]] = []
    for candidate in candidates:
        display_title = format_title_with_year(candidate.title, candidate.release_year)
        link = build_suggestion_link(candidate)
        title_display = f"[{display_title}]({link})" if link else display_title
        block = [title_display]
        if show_counts:
            vote_count = vote_counts_by_suggestion_id.get(candidate.id, 0)
            block.append(build_candidate_standings_line(vote_count, total_votes))
        blocks.append(block)

    lines: List[str] = []
    for index, block in enumerate(blocks):
        if index > 0:
            lines.append("")
        lines.extend(block)

    if is_visible and standings_error is not None:
        lines.append("")
        lines.append(f"Standings unavailable: {standings_error}")
    elif not is_visible:
        lines.append("")
        lines.append("Votes hidden until voting closes.")

    return lines


def build_voting_post_embed(
    vote_round: VoteRound,
    candidates: List[WatchItem],
    standings: Optional[List[StandingsEntry]],
    standings_error: Optional[str],
    collection_name: Optional[str] = None,
    guild_round_number: Optional[int] = None,
) -> discord.Embed:
    """Build the public voting post's embed for a round (Release Polish
    Batch 2, Priority 5).

    Used both for the initial post created by /start_vote and to refresh
    it after each vote or a deadline change. Reuses
    format_datetime_for_display and build_candidate_standings_lines
    rather than reformatting either here. Uses WASH's standard
    EmbedFactory styling (the yellow accent color) with no footer -- this
    is an operational voting message, not a branded WASH info card, so it
    carries none of EmbedFactory's default project/attribution footer
    text (Branding Consistency).

    Args:
        vote_round: The round this post is for.
        candidates: The nominees to list, in order. These are whichever
            suggestions existed when /start_vote was run -- this milestone
            doesn't implement nominee selection, so the list is fixed for
            the life of the post.
        standings: Standings entries to display, or None if standings
            shouldn't be shown (a blind round, or none computed yet).
        standings_error: A message to show instead of standings if
            calculating them failed, or None.
        collection_name: The round's collection, if known (Requirement 1:
            voting is centered on the collection, not the round number)
            -- see resolve_vote_collection_name. None falls back to a
            generic "Voting Is Open" title.

    Returns:
        The embed. Total votes cast is always shown -- for a blind round
        that's the only vote information revealed; per-candidate counts,
        percentages, and progress bars are additionally shown for a
        visible round (never for blind, preserving that round's privacy).
    """
    description_lines = [
        build_vote_round_line(vote_round, guild_round_number),
        f"Voting ends: {format_datetime_for_display(vote_round.closes_at)}",
        *build_active_filter_lines(vote_round),
        "",
    ]
    description_lines.extend(build_candidate_standings_lines(candidates, vote_round, standings, standings_error))

    return EmbedFactory.info(
        format_vote_title(collection_name, "Voting Is Open"),
        "\n".join(description_lines),
        footer=None,
        fields=[
            {"name": "Visibility", "value": vote_round.visibility.value.capitalize(), "inline": True},
            {"name": "Votes Cast", "value": str(len(vote_round.votes)), "inline": True},
        ],
    )


def build_current_voting_post_embed(
    vote_service: VoteService,
    suggestion_service: SuggestionService,
    vote_round: VoteRound,
) -> discord.Embed:
    """Recompute and build a round's voting post embed from its current state.

    Shared by refresh_voting_post (called after each vote) and
    handle_reschedule_vote_completion (called after WASH Crew edits the
    deadline via /edit_vote), so recomputing standings/candidates for
    the post is never duplicated between the two.

    Args:
        vote_service: Used to recompute standings.
        suggestion_service: Used to re-list the current nominees.
        vote_round: The round to build the post embed for.

    Returns:
        The embed (see build_voting_post_embed).
    """
    candidates = get_round_candidates(suggestion_service, vote_round)
    standings_result = vote_service.calculate_standings(vote_round.id)
    standings = standings_result.standings if standings_result.success else None
    standings_error = None if standings_result.success else standings_result.message
    collection_name = resolve_vote_collection_name(suggestion_service, vote_round.database_id)
    guild_round_number = (
        vote_service.get_guild_round_number(vote_round.id, vote_round.guild_id)
        if vote_round.guild_id is not None
        else None
    )
    return build_voting_post_embed(
        vote_round, candidates, standings, standings_error, collection_name, guild_round_number
    )


async def refresh_voting_post(
    interaction: discord.Interaction,
    vote_service: VoteService,
    suggestion_service: SuggestionService,
    vote_round: VoteRound,
) -> None:
    """Update the public voting post after a vote, for visible rounds only.

    Args:
        interaction: The button-click interaction whose message is the
            voting post to edit.
        vote_service: Used to recompute standings.
        suggestion_service: Used to re-list the current nominees.
        vote_round: The round being voted in.
    """
    embed = build_current_voting_post_embed(vote_service, suggestion_service, vote_round)
    await interaction.message.edit(embed=embed)


async def handle_nominee_vote(
    interaction: discord.Interaction,
    vote_service: VoteService,
    suggestion_service: SuggestionService,
    suggestion_id: int,
    permission_service: Optional[PermissionService] = None,
) -> None:
    """Core logic for a nominee button click.

    Reuses perform_vote() for the actual vote-casting and ephemeral
    confirmation, then refreshes the public voting post for a visible
    round. Never duplicates VoteService's own validation.

    Args:
        interaction: The button-click interaction.
        vote_service: The vote service to cast the vote through.
        suggestion_service: Used to re-list nominees when refreshing the post.
        suggestion_id: The nominee this button represents.
    """
    if permission_service is not None:
        permission = permission_service.require_watch_party_member(interaction.user)
        if not permission.allowed:
            await interaction.response.send_message(permission.message, ephemeral=True)
            return

    message, ephemeral = perform_vote(vote_service, interaction.user.id, suggestion_id, suggestion_service)
    await interaction.response.send_message(message, ephemeral=ephemeral)

    # Resolved by suggestion_id (see perform_vote above) so this always
    # refreshes the post the click actually happened on, never a
    # different collection's concurrently-open round.
    vote_round = vote_service.get_open_round_for_suggestion(suggestion_id)
    if vote_round is not None and vote_round.visibility == VoteVisibility.VISIBLE:
        await refresh_voting_post(interaction, vote_service, suggestion_service, vote_round)


# --- FR-023: /edit_vote -- WASH Crew administrative vote management -------------


async def update_voting_message(
    bot: object,
    vote_round: VoteRound,
    content: Optional[str] = None,
    *,
    embed: Optional[discord.Embed] = None,
    clear_view: bool = False,
) -> None:
    """Best-effort update of a round's original voting post.

    Used by /edit_vote's change-end-time, end-now, and cancel actions to
    keep the original post's displayed state accurate. Does nothing if
    the round has no channel/message reference (a legacy round, or one
    whose reference was never attached) -- "when supported" in FR-023's
    requirements. Also swallows any Discord-side failure (e.g. the
    message was deleted) rather than raising, matching the project's
    existing "graceful when Discord state is stale" convention (see
    check_and_announce_expired_vote's handling of a missing channel).

    Args:
        bot: Anything with get_channel(channel_id)/fetch_channel(channel_id)
            coroutine-or-sync methods returning an object with a
            fetch_message(message_id) coroutine, itself returning an
            object with an edit(...) coroutine -- a real discord.Client/Bot
            satisfies this, and tests can supply a lightweight fake.
        vote_round: The round whose original post should be updated.
        content: The new plain-text message content, or None to clear
            any existing text (e.g. when switching to embed-only, or the
            reverse when a round closes -- see embed).
        embed: The new embed (Release Polish Batch 2, Priority 5's
            active-vote embed), or None to clear any existing embed. Both
            content and embed are always passed explicitly to Discord
            (never omitted) so switching between an open round's embed
            and a closed round's plain-text summary always replaces the
            other rather than leaving it stale alongside the new one.
        clear_view: When True, removes the message's interactive
            components (the persistent voting buttons) -- used once
            voting is no longer possible (ended or cancelled). Left False
            for a still-open round whose deadline just changed, so its
            voting buttons keep working.
    """
    if vote_round.channel_id is None or vote_round.message_id is None:
        return

    try:
        channel = bot.get_channel(vote_round.channel_id)
        if channel is None:
            channel = await bot.fetch_channel(vote_round.channel_id)
        message = await channel.fetch_message(vote_round.message_id)
        if clear_view:
            await message.edit(content=content, embed=embed, view=None)
        else:
            await message.edit(content=content, embed=embed)
    except Exception:
        logger.exception(
            "Could not update the original voting message for round %s", vote_round.id
        )


def build_edit_vote_management_text(vote_round: VoteRound, candidate_count: int) -> str:
    """Build the ephemeral /edit_vote management response for the active vote.

    Shows enough identifying information for WASH Crew to confirm they're
    about to manage the right round before choosing an action.
    """
    lines = [
        f"Managing voting round {vote_round.id}",
        f"Visibility: {vote_round.visibility.value.capitalize()}",
        f"Candidates: {candidate_count}",
        f"Votes cast: {len(vote_round.votes)}",
        f"Voting ends: {format_datetime_for_display(vote_round.closes_at)}",
    ]
    link = build_vote_link(vote_round)
    if link:
        lines.append(f"Original post: {link}")
    return "\n".join(lines)


def perform_edit_vote_open(
    vote_service: VoteService,
    suggestion_service: SuggestionService,
    user: object,
    wash_crew_role_id: Optional[int],
    database_id: Optional[int] = None,
) -> tuple[str, bool, Optional[VoteRound]]:
    """Core logic for /edit_vote, kept free of Discord objects except `user`.

    Args:
        vote_service: Used to look up the currently open round.
        suggestion_service: Used to report the candidate count when the
            round has no fixed candidate list (a legacy round).
        user: The member invoking the command.
        wash_crew_role_id: The configured WASH Crew role ID, or None if
            unconfigured.
        database_id: The collection whose open round should be managed.
            Optional so existing callers/tests without a resolved
            collection keep working unchanged (see
            VoteService.get_open_round) -- the Discord layer always
            resolves this via contextual collection resolution before
            calling here (see bot.py's /edit_vote command).

    Returns:
        A (message, ephemeral, vote_round) tuple. vote_round is set only
        on success, so the caller can build the management view's button
        callbacks around the specific round being managed. Always ephemeral.
    """
    if wash_crew_role_id is None:
        return (
            "WASH Crew permissions have not been configured. "
            "Set WASH_CREW_ROLE_ID before using this command.",
            True,
            None,
        )

    if not is_wash_crew_member(user, wash_crew_role_id):
        return "You need the WASH Crew role to manage voting rounds.", True, None

    vote_round = vote_service.get_open_round(database_id)
    if vote_round is None:
        return "There's no active voting round to manage.", True, None

    candidate_count = (
        len(vote_round.candidate_suggestion_ids)
        if vote_round.candidate_suggestion_ids
        else suggestion_service.suggestion_count()
    )
    return build_edit_vote_management_text(vote_round, candidate_count), True, vote_round


async def handle_edit_vote(interaction: discord.Interaction, bot: "WatchPartyBot") -> None:
    """Handle /edit_vote: manage the invoking collection's own open round.

    Contextual Collection Resolution: resolves which collection's round
    to manage the same way every other collection-scoped command does --
    automatically when the channel unambiguously identifies one, a
    "Which collection?" picker when it doesn't, a clear error when no
    collection is configured at all (see resolve_database_then). The
    WASH Crew permission check (inside perform_edit_vote_open) still
    happens after resolution, mirroring /start_vote's own established
    ordering.
    """
    guild_id = interaction.guild_id
    if guild_id is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    async def on_resolved(resolved_interaction: discord.Interaction, database: SuggestionDatabase) -> None:
        message, ephemeral, vote_round = perform_edit_vote_open(
            vote_service=bot.vote_service,
            suggestion_service=bot.suggestion_service,
            user=resolved_interaction.user,
            wash_crew_role_id=resolve_permission_service(
                resolved_interaction.guild_id, bot.guild_configuration_repository
            ).wash_crew_role_id,
            database_id=database.database_id,
        )
        if vote_round is None:
            await resolved_interaction.response.send_message(message, ephemeral=ephemeral)
            return

        round_id = vote_round.id

        async def on_change_end_time(button_interaction: discord.Interaction) -> None:
            async def on_end_now_quick_pick(pick_interaction: discord.Interaction) -> None:
                async def on_confirm(confirm_interaction: discord.Interaction) -> None:
                    await handle_end_vote_now_completion(
                        confirm_interaction,
                        vote_completion_service=bot.vote_completion_service,
                        vote_service=bot.vote_service,
                        suggestion_service=bot.suggestion_service,
                        wash_crew_role_id=resolve_permission_service(
                            confirm_interaction.guild_id, bot.guild_configuration_repository
                        ).wash_crew_role_id,
                        round_id=round_id,
                        bot=bot,
                        scheduler_service=bot.scheduler_host.scheduler_service,
                    )

                async def on_abort(abort_interaction: discord.Interaction) -> None:
                    await abort_interaction.response.send_message("No changes were made.", ephemeral=True)

                confirmation_view = EditVoteConfirmationView(
                    confirm_label="End Now", on_confirm=on_confirm, on_abort=on_abort
                )
                await pick_interaction.response.send_message(
                    f"Are you sure you want to end voting round {round_id} now? This cannot be undone.",
                    view=confirmation_view,
                    ephemeral=True,
                )

            async def apply_end_time_delta(delta_interaction: discord.Interaction, delta_minutes: int) -> None:
                """Shared completion for Shorten/Extend Vote: apply a delta
                (negative to shorten, positive to extend) to the round's
                *current* end time -- never to "now" -- rejecting the
                result up front if it would land in the past rather than
                letting VoteService reject it with a less specific message.
                """
                current_round = bot.vote_service.get_round(round_id)
                if current_round is None or current_round.closes_at is None:
                    await delta_interaction.response.send_message(
                        "That voting round doesn't exist or has no end time to adjust.", ephemeral=True
                    )
                    return
                new_closes_at = current_round.closes_at + timedelta(minutes=delta_minutes)
                if new_closes_at <= datetime.now(timezone.utc):
                    await delta_interaction.response.send_message(
                        "That would move the end time into the past. Choose a smaller amount, "
                        "or use End Now instead.",
                        ephemeral=True,
                    )
                    return
                await handle_reschedule_vote_completion(
                    delta_interaction,
                    vote_service=bot.vote_service,
                    suggestion_service=bot.suggestion_service,
                    wash_crew_role_id=resolve_permission_service(
                        delta_interaction.guild_id, bot.guild_configuration_repository
                    ).wash_crew_role_id,
                    round_id=round_id,
                    new_closes_at=new_closes_at,
                    bot=bot,
                    scheduler_service=bot.scheduler_host.scheduler_service,
                    guild_configuration_repository=bot.guild_configuration_repository,
                )

            async def on_shorten(pick_interaction: discord.Interaction) -> None:
                async def on_quick_pick(quick_interaction: discord.Interaction, minutes: int) -> None:
                    await apply_end_time_delta(quick_interaction, -minutes)

                async def on_choose_custom(custom_interaction: discord.Interaction) -> None:
                    async def on_custom_submit(modal_interaction: discord.Interaction, duration_text: str) -> None:
                        try:
                            minutes = parse_duration_to_minutes(duration_text)
                        except ValueError as exc:
                            await modal_interaction.response.send_message(str(exc), ephemeral=True)
                            return
                        await apply_end_time_delta(modal_interaction, -minutes)

                    await custom_interaction.response.send_modal(
                        CustomDurationModal(on_custom_submit, title="Shorten Vote")
                    )

                async def on_cancel_delta(cancel_interaction: discord.Interaction) -> None:
                    await cancel_interaction.response.send_message("No changes were made.", ephemeral=True)

                await pick_interaction.response.send_message(
                    f"Voting round {round_id} currently ends: {format_datetime_for_display(vote_round.closes_at)}\n\n"
                    f"Shorten it by how much? Choose 1 Hour or 1 Day, or Custom... for any other amount of "
                    f"minutes, hours, or days (e.g. {DURATION_FORMAT_EXAMPLES}).",
                    view=DurationDeltaChoiceView(
                        on_quick_pick, on_choose_custom, on_cancel_delta, custom_id_prefix="wpm_edit_vote_shorten_pick"
                    ),
                    ephemeral=True,
                )

            async def on_extend(pick_interaction: discord.Interaction) -> None:
                async def on_quick_pick(quick_interaction: discord.Interaction, minutes: int) -> None:
                    await apply_end_time_delta(quick_interaction, minutes)

                async def on_choose_custom(custom_interaction: discord.Interaction) -> None:
                    async def on_custom_submit(modal_interaction: discord.Interaction, duration_text: str) -> None:
                        try:
                            minutes = parse_duration_to_minutes(duration_text)
                        except ValueError as exc:
                            await modal_interaction.response.send_message(str(exc), ephemeral=True)
                            return
                        await apply_end_time_delta(modal_interaction, minutes)

                    await custom_interaction.response.send_modal(
                        CustomDurationModal(on_custom_submit, title="Extend Vote")
                    )

                async def on_cancel_delta(cancel_interaction: discord.Interaction) -> None:
                    await cancel_interaction.response.send_message("No changes were made.", ephemeral=True)

                await pick_interaction.response.send_message(
                    f"Voting round {round_id} currently ends: {format_datetime_for_display(vote_round.closes_at)}\n\n"
                    f"Extend it by how much? Choose 1 Hour or 1 Day, or Custom... for any other amount of "
                    f"minutes, hours, or days (e.g. {DURATION_FORMAT_EXAMPLES}).",
                    view=DurationDeltaChoiceView(
                        on_quick_pick, on_choose_custom, on_cancel_delta, custom_id_prefix="wpm_edit_vote_extend_pick"
                    ),
                    ephemeral=True,
                )

            async def on_set_exact(custom_interaction: discord.Interaction) -> None:
                async def on_custom_submit(modal_interaction: discord.Interaction, timestamp_text: str) -> None:
                    try:
                        new_closes_at = parse_discord_timestamp_vote_end_time(timestamp_text)
                    except ValueError as exc:
                        await modal_interaction.response.send_message(str(exc), ephemeral=True)
                        return
                    await handle_reschedule_vote_completion(
                        modal_interaction,
                        vote_service=bot.vote_service,
                        suggestion_service=bot.suggestion_service,
                        wash_crew_role_id=resolve_permission_service(
                            modal_interaction.guild_id, bot.guild_configuration_repository
                        ).wash_crew_role_id,
                        round_id=round_id,
                        new_closes_at=new_closes_at,
                        bot=bot,
                        scheduler_service=bot.scheduler_host.scheduler_service,
                        guild_configuration_repository=bot.guild_configuration_repository,
                    )

                await custom_interaction.response.send_modal(CustomVoteEndTimeModal(on_custom_submit))

            await button_interaction.response.send_message(
                f"Voting round {round_id} currently ends: {format_datetime_for_display(vote_round.closes_at)}\n\n"
                "Choose how to change it below.\n\n"
                "**Set Exact End Time:** Create a Discord timestamp by typing `@time` in any normal Discord "
                "message box, selecting the desired date/time, then copying and pasting the generated "
                "timestamp into WASH.",
                view=VoteEndTimeMenuView(on_end_now_quick_pick, on_shorten, on_extend, on_set_exact),
                ephemeral=True,
            )

        async def on_cancel_vote(button_interaction: discord.Interaction) -> None:
            async def on_confirm(confirm_interaction: discord.Interaction) -> None:
                await handle_cancel_vote_now_completion(
                    confirm_interaction,
                    vote_service=bot.vote_service,
                    wash_crew_role_id=resolve_permission_service(
                        confirm_interaction.guild_id, bot.guild_configuration_repository
                    ).wash_crew_role_id,
                    round_id=round_id,
                    bot=bot,
                    scheduler_service=bot.scheduler_host.scheduler_service,
                    suggestion_service=bot.suggestion_service,
                )

            async def on_abort(abort_interaction: discord.Interaction) -> None:
                await abort_interaction.response.send_message("No changes were made.", ephemeral=True)

            confirmation_view = EditVoteConfirmationView(
                confirm_label="Cancel Vote", on_confirm=on_confirm, on_abort=on_abort
            )
            await button_interaction.response.send_message(
                f"Are you sure you want to cancel voting round {round_id}? This cannot be undone.",
                view=confirmation_view,
                ephemeral=True,
            )

        view = EditVoteManagementView(on_change_end_time, on_cancel_vote)
        await resolved_interaction.response.send_message(message, view=view, ephemeral=ephemeral)

    await resolve_database_then(interaction, bot, guild_id, interaction.channel_id, on_resolved)


def perform_reschedule_vote_round(
    vote_service: VoteService,
    user: object,
    wash_crew_role_id: Optional[int],
    round_id: int,
    new_closes_at: datetime,
) -> tuple[str, bool, Optional[VoteRound]]:
    """Core logic shared by every "Change End Time" entry point (quick-pick
    buttons and the Custom Date & Time modal alike) once a new UTC deadline
    has already been determined.

    Args:
        vote_service: Used to reschedule the round.
        user: The member invoking the action.
        wash_crew_role_id: The configured WASH Crew role ID, or None if unconfigured.
        round_id: The round to reschedule.
        new_closes_at: The already-parsed, timezone-aware UTC deadline.

    Returns:
        A (message, ephemeral, vote_round) tuple. vote_round (the
        updated round) is set only on success, so the caller can replace
        its scheduler jobs, refresh its public post, and post the
        deadline-change notice without a redundant lookup. Always
        ephemeral -- this is WASH Crew's own confirmation; the separate
        public notice is what the community sees.
    """
    if wash_crew_role_id is None:
        return (
            "WASH Crew permissions have not been configured. "
            "Set WASH_CREW_ROLE_ID before using this command.",
            True,
            None,
        )

    if not is_wash_crew_member(user, wash_crew_role_id):
        return "You need the WASH Crew role to manage voting rounds.", True, None

    result = vote_service.reschedule_round(round_id, new_closes_at)
    if not result.success:
        return result.message, True, None

    return (
        f"Voting round {round_id} rescheduled. New deadline: "
        f"{format_datetime_for_display(result.vote_round.closes_at)}",
        True,
        result.vote_round,
    )


async def handle_reschedule_vote_completion(
    interaction: discord.Interaction,
    vote_service: VoteService,
    suggestion_service: SuggestionService,
    wash_crew_role_id: Optional[int],
    round_id: int,
    new_closes_at: datetime,
    bot: object,
    scheduler_service: Optional[SchedulerService] = None,
    guild_configuration_repository: Optional[GuildConfigurationRepository] = None,
) -> None:
    """Change a round's deadline from an already-parsed UTC datetime, replace
    its scheduler jobs, and notify the community.

    Used by the Vote End Time guided flow's quick-pick buttons ("In 5
    Minutes", etc.) and its Custom Date & Time modal alike, once each has
    already determined new_closes_at -- both share this one completion
    path so scheduler/notice/persistence behavior never diverges between
    them, only how the new deadline is collected.

    scheduler_service/guild_configuration_repository default to None so
    callers/tests that don't pass them keep working unchanged; passing
    None simply skips scheduling (see reschedule_vote_jobs).
    """
    message, ephemeral, vote_round = perform_reschedule_vote_round(
        vote_service, interaction.user, wash_crew_role_id, round_id, new_closes_at
    )
    await interaction.response.send_message(message, ephemeral=ephemeral)
    if vote_round is None:
        return

    # FR-023: replace this round's close_vote/vote_reminder jobs to
    # reflect the new deadline before any further Discord I/O, mirroring
    # handle_start_vote_completion's existing "schedule before anything
    # that could fail" ordering rationale.
    if vote_round.guild_id is not None:
        await reschedule_vote_jobs(
            scheduler_service,
            vote_round,
            vote_round.guild_id,
            guild_configuration_repository=guild_configuration_repository,
        )

    if vote_round.channel_id is not None:
        collection_name = resolve_vote_collection_name(suggestion_service, vote_round.database_id)
        guild_round_number = (
            vote_service.get_guild_round_number(vote_round.id, vote_round.guild_id)
            if vote_round.guild_id is not None
            else None
        )
        notice = build_vote_deadline_change_notice(vote_round, collection_name, guild_round_number)
        channel = bot.get_channel(vote_round.channel_id)
        if channel is None:
            channel = await bot.fetch_channel(vote_round.channel_id)
        await channel.send(notice)

    embed = build_current_voting_post_embed(vote_service, suggestion_service, vote_round)
    await update_voting_message(bot, vote_round, embed=embed)


def perform_end_vote_now(
    vote_completion_service: VoteCompletionService,
    user: object,
    wash_crew_role_id: Optional[int],
    round_id: int,
) -> tuple[str, bool, Optional[VoteCompletionResult]]:
    """Core logic for /edit_vote's "End Now" action.

    Reuses VoteCompletionService.complete_round() -- the exact same
    authoritative completion logic a scheduled close_vote job uses (see
    CloseVoteJobHandler) -- so ending a vote early never duplicates or
    diverges from normal completion: closing, winner calculation, Watch
    Item Journey updates, and standings all happen exactly as they
    otherwise would.

    Args:
        vote_completion_service: Used to complete the round.
        user: The member invoking the action.
        wash_crew_role_id: The configured WASH Crew role ID, or None if unconfigured.
        round_id: The round to end.

    Returns:
        A (message, ephemeral, result) tuple. result is set only on
        success, so the caller can finalize the completion presentation
        (see vote_completion_announcer.finalize_vote_completion) without
        a redundant lookup. Always ephemeral -- the separate public
        announcement is what the community sees.
    """
    if wash_crew_role_id is None:
        return (
            "WASH Crew permissions have not been configured. "
            "Set WASH_CREW_ROLE_ID before using this command.",
            True,
            None,
        )

    if not is_wash_crew_member(user, wash_crew_role_id):
        return "You need the WASH Crew role to manage voting rounds.", True, None

    result = vote_completion_service.complete_round(round_id)
    if result is None:
        return (
            "That voting round no longer exists or has already been completed or cancelled.",
            True,
            None,
        )

    return f"Voting round {round_id} has been ended.", True, result


async def handle_end_vote_now_completion(
    interaction: discord.Interaction,
    vote_completion_service: VoteCompletionService,
    vote_service: VoteService,
    suggestion_service: SuggestionService,
    wash_crew_role_id: Optional[int],
    round_id: int,
    bot: object,
    scheduler_service: Optional[SchedulerService] = None,
) -> None:
    """End a round immediately, using the normal completion and announcement path.

    scheduler_service defaults to None so callers/tests that don't pass
    one keep working unchanged; passing None simply skips job cancellation.

    FR-026: the original voting post update and results announcement are
    delegated to vote_completion_announcer.finalize_vote_completion() --
    the exact same function CloseVoteJobHandler calls for an automatic
    completion -- so ending a vote early produces an identical
    presentation to letting it expire naturally.
    """
    message, ephemeral, result = perform_end_vote_now(
        vote_completion_service, interaction.user, wash_crew_role_id, round_id
    )
    await interaction.response.send_message(message, ephemeral=ephemeral)
    if result is None:
        return

    # FR-023: remove any pending close_vote/vote_reminder jobs now that
    # the round is already completed -- a no-op if none is active (e.g.
    # the close_vote job is the one that raced us here, or reminders were
    # disabled).
    await cancel_vote_jobs(scheduler_service, round_id)

    await finalize_vote_completion(
        vote_service,
        suggestion_service,
        bot,
        result,
    )
    await sync_vote_completion_status_embeds(bot, result)


def perform_cancel_vote_now(
    vote_service: VoteService,
    user: object,
    wash_crew_role_id: Optional[int],
    round_id: int,
) -> tuple[str, bool, Optional[VoteRound]]:
    """Core logic for /edit_vote's "Cancel Vote" action.

    Args:
        vote_service: Used to cancel the round.
        user: The member invoking the action.
        wash_crew_role_id: The configured WASH Crew role ID, or None if unconfigured.
        round_id: The round to cancel.

    Returns:
        A (message, ephemeral, vote_round) tuple. vote_round (the
        now-cancelled round) is set only on success, so the caller can
        post the public cancellation notice and update the original post
        without a redundant lookup. Always ephemeral -- the separate
        public notice is what the community sees.
    """
    if wash_crew_role_id is None:
        return (
            "WASH Crew permissions have not been configured. "
            "Set WASH_CREW_ROLE_ID before using this command.",
            True,
            None,
        )

    if not is_wash_crew_member(user, wash_crew_role_id):
        return "You need the WASH Crew role to manage voting rounds.", True, None

    result = vote_service.cancel_round(round_id)
    if not result.success:
        return result.message, True, None

    return f"Voting round {round_id} has been cancelled.", True, result.vote_round


async def handle_cancel_vote_now_completion(
    interaction: discord.Interaction,
    vote_service: VoteService,
    wash_crew_role_id: Optional[int],
    round_id: int,
    bot: object,
    scheduler_service: Optional[SchedulerService] = None,
    suggestion_service: Optional[SuggestionService] = None,
) -> None:
    """Cancel a round, notify the community, and disable its original controls.

    scheduler_service defaults to None so callers/tests that don't pass
    one keep working unchanged; passing None simply skips job cancellation.
    suggestion_service similarly defaults to None so existing callers
    keep working; passing None simply falls back to a generic,
    round-centric cancellation notice (see resolve_vote_collection_name).
    """
    message, ephemeral, vote_round = perform_cancel_vote_now(
        vote_service, interaction.user, wash_crew_role_id, round_id
    )
    await interaction.response.send_message(message, ephemeral=ephemeral)
    if vote_round is None:
        return

    # FR-023: remove any pending close_vote/vote_reminder jobs now that
    # the round is cancelled -- a no-op if none is active.
    await cancel_vote_jobs(scheduler_service, round_id)

    if vote_round.channel_id is not None:
        collection_name = (
            resolve_vote_collection_name(suggestion_service, vote_round.database_id)
            if suggestion_service is not None
            else None
        )
        guild_round_number = (
            vote_service.get_guild_round_number(vote_round.id, vote_round.guild_id)
            if vote_round.guild_id is not None
            else None
        )
        notice = build_vote_cancellation_notice(vote_round, collection_name, guild_round_number)
        channel = bot.get_channel(vote_round.channel_id)
        if channel is None:
            channel = await bot.fetch_channel(vote_round.channel_id)
        await channel.send(notice)

    await update_voting_message(
        bot, vote_round, "This voting round was cancelled by WASH Crew.", clear_view=True
    )


async def perform_add_suggestion_from_input(
    suggestion_input_service: SuggestionInputService,
    suggestion_service: SuggestionService,
    guild_id: Optional[int],
    channel_id: Optional[int],
    title: str,
    imdb_url: Optional[str],
    runtime_minutes: Optional[int] = None,
    genres: tuple[str, ...] = (),
    description: Optional[str] = None,
    content_rating: Optional[str] = None,
    director: Optional[str] = None,
    imdb_rating: Optional[str] = None,
    poster_url: Optional[str] = None,
) -> tuple[str, bool, Optional[WatchItem]]:
    """Resolve user input before adding a suggestion.

    IMDb links entered in the title field are converted to the actual watch
    item title while preserving the canonical IMDb URL as metadata. Input
    failures are returned as ephemeral responses and are never persisted.
    """
    resolved = await suggestion_input_service.resolve(title, imdb_url)
    if not resolved.success:
        return resolved.error_message or "I could not resolve that suggestion.", True, None

    return perform_add_suggestion(
        suggestion_service=suggestion_service,
        guild_id=guild_id,
        channel_id=channel_id,
        title=resolved.title or title,
        imdb_url=resolved.imdb_url,
        runtime_minutes=resolved.runtime_minutes,
        genres=resolved.genres,
        description=resolved.plot,
        content_rating=resolved.content_rating,
        director=resolved.director,
        imdb_rating=resolved.imdb_rating,
        poster_url=resolved.poster_url,
        cast=resolved.cast,
    )


# --- FR-033A: /add with duplicate detection, re-suggestion rules, and confirmation posts ---

_TRAILING_YEAR_SUFFIX_PATTERN = re.compile(r"\((\d{4})\)\s*$")


def extract_year_from_title_suffix(title: str) -> Optional[int]:
    """Pull a trailing " (YYYY)" release year out of a title string.

    OMDb-resolved titles already embed the year this way (see
    ImdbMetadataService._format_display_title) -- this only reads
    already-returned text and never contacts IMDb/OMDb itself.
    """
    match = _TRAILING_YEAR_SUFFIX_PATTERN.search(title.strip())
    return int(match.group(1)) if match else None


class AddSuggestionOutcomeKind(str, Enum):
    """What /add should do next, decided by decide_add_suggestion_outcome()."""

    BLOCKED_ACTIVE = "blocked_active"
    BLOCKED_NO_CREW_OVERRIDE = "blocked_no_crew_override"
    NEEDS_CREW_REACTIVATION_CONFIRM = "needs_crew_reactivation_confirm"
    BLOCKED_POSSIBLE_NO_CREW = "blocked_possible_no_crew"
    NEEDS_CREW_POSSIBLE_CONFIRM = "needs_crew_possible_confirm"
    PROCEED = "proceed"


@dataclass(frozen=True, slots=True)
class AddSuggestionDecision:
    kind: AddSuggestionOutcomeKind
    message: str
    matched_item: Optional[WatchItem] = None


def build_original_suggestion_link(item: WatchItem) -> Optional[str]:
    """A labeled `[Original Suggestion](url)` markdown link to the Discord
    message where `item` was first suggested, or None if that message
    reference is missing (a legacy suggestion recorded before message
    linking existed).

    Deliberately never returns a bare URL -- the IMDb preview card
    already shown alongside these responses provides IMDb linking, so
    duplicating a raw link here would be redundant at best and, for a
    plain (non-markdown) message, would render as an unlabeled, easy to
    misclick raw URL.
    """
    if item.guild_id is None or item.channel_id is None or item.message_id is None:
        return None
    message_url = f"https://discord.com/channels/{item.guild_id}/{item.channel_id}/{item.message_id}"
    return f"[Original Suggestion]({message_url})"


def build_duplicate_match_line(
    match: DuplicateMatch,
    vote_service: Optional[VoteService] = None,
) -> str:
    """One matched existing item's line/block within an /add duplicate
    warning.

    UI Polish (Watch Item Status Presentation): a Vote Winner match gets
    its own richer block -- Reference, the 🏆 Vote Winner status (with
    its win date, when recorded), and an Original Suggestion link --
    replacing the old single-line, raw-IMDb-URL format. Every other
    category (active, archived-rejected, archived-other) keeps that
    original single-line format, but with the same labeled link in place
    of the bare IMDb URL it used to show.

    vote_service resolves In an Active Vote -- optional, defaulting to
    None so existing callers/tests keep working unchanged (meaning it's
    never shown).
    """
    item = match.watch_item
    if match.category is DuplicateMatchCategory.VOTE_WINNER:
        return build_vote_winner_duplicate_match_block(item)

    parts = [f"Reference {item.reference}", item.title]
    original_suggestion_link = build_original_suggestion_link(item)
    if original_suggestion_link is not None:
        parts.append(original_suggestion_link)
    display_status = resolve_display_status(item, vote_service)
    parts.append(f"status: {display_status_label(display_status)}")
    return " | ".join(parts)


def build_vote_winner_duplicate_match_block(item: WatchItem) -> str:
    """The Vote Winner-specific block /add's duplicate/reactivation view
    shows: Reference, status (with win date when recorded), then
    Original Suggestion and IMDb as clickable links -- each line omitted
    gracefully when its underlying data isn't available (a legacy Vote
    Winner with no recorded win date, or no original post/IMDb link).
    """
    lines = [f"Reference {item.reference}", display_status_label(SuggestionDisplayStatus.VOTE_WINNER)]

    won_line = vote_winner_won_date_line(item)
    if won_line is not None:
        lines.append(won_line)

    original_suggestion_link = build_original_suggestion_link(item)
    if original_suggestion_link is not None:
        lines.append(original_suggestion_link)

    imdb_url = item.metadata_ids.get(MetadataProvider.IMDB)
    if imdb_url:
        lines.append(f"[IMDb]({imdb_url})")

    return "\n".join(lines)


_ARCHIVE_CATEGORY_LABELS = {
    # Pre-Phase 3 UX Polish: "retired", not "archived" -- this detail line
    # sits directly above build_duplicate_match_line's own "status: 🗄️
    # Retired" rendering (display_status_label), so the two must use the
    # same word for the same WatchItemStatus.ARCHIVED status.
    DuplicateMatchCategory.ARCHIVED_REJECTED: 'been retired after being rejected ("I Won\'t Watch")',
    DuplicateMatchCategory.VOTE_WINNER: "already won a vote",
    DuplicateMatchCategory.ARCHIVED_OTHER: "already been retired",
}


def decide_add_suggestion_outcome(
    duplicate_result: DuplicateCheckResult,
    *,
    is_crew: bool,
    vote_service: Optional[VoteService] = None,
) -> AddSuggestionDecision:
    """Turn a duplicate check into what /add should do next (Sections 2-3).

    Never guesses: an ACTIVE match always blocks outright (no override,
    even for Crew); an archived/watched match only proceeds -- via
    reactivation, never a new record -- after WASH Crew explicitly
    confirms; a possible-only match only proceeds (as a genuinely new
    suggestion) after WASH Crew explicitly confirms. Regular members
    can never bypass either warning.

    vote_service resolves each matched item's In an Active Vote status --
    optional, defaulting to None so existing callers/tests keep working
    unchanged (meaning it's never shown).
    """
    if duplicate_result.has_definite_match:
        active_matches = [
            match for match in duplicate_result.definite_matches if match.category is DuplicateMatchCategory.ACTIVE
        ]
        if active_matches:
            match = active_matches[0]
            return AddSuggestionDecision(
                AddSuggestionOutcomeKind.BLOCKED_ACTIVE,
                "🔴 That title is already in this collection.\n"
                + build_duplicate_match_line(match, vote_service),
                matched_item=match.watch_item,
            )

        match = duplicate_result.definite_matches[0]
        detail = (
            f"This title has {_ARCHIVE_CATEGORY_LABELS[match.category]}:\n"
            + build_duplicate_match_line(match, vote_service)
        )
        if not is_crew:
            return AddSuggestionDecision(
                AddSuggestionOutcomeKind.BLOCKED_NO_CREW_OVERRIDE, detail, matched_item=match.watch_item
            )
        return AddSuggestionDecision(
            AddSuggestionOutcomeKind.NEEDS_CREW_REACTIVATION_CONFIRM, detail, matched_item=match.watch_item
        )

    if duplicate_result.has_possible_only:
        lines = "\n".join(
            build_duplicate_match_line(match, vote_service) for match in duplicate_result.matches
        )
        detail = (
            "This title matches existing item(s) with no confirmed release year, "
            "so it might be a duplicate:\n" + lines
        )
        if not is_crew:
            return AddSuggestionDecision(AddSuggestionOutcomeKind.BLOCKED_POSSIBLE_NO_CREW, detail)
        return AddSuggestionDecision(AddSuggestionOutcomeKind.NEEDS_CREW_POSSIBLE_CONFIRM, detail)

    return AddSuggestionDecision(AddSuggestionOutcomeKind.PROCEED, "")


async def post_suggestion_confirmation(
    bot: "WatchPartyBot",
    watch_item: WatchItem,
    database: SuggestionDatabase,
    interaction: discord.Interaction,
) -> tuple[bool, str]:
    """Post (or refresh) a suggestion's public confirmation post.

    Posts to the database's configured suggestion channel -- never the
    channel /add happened to be run from. Reuses (edits) the existing
    confirmation post when the suggestion already points at one in that
    same channel (e.g. reactivation), otherwise sends a fresh one.

    Returns:
        A (posted, note) tuple. note is a short sentence for the
        ephemeral acknowledgment: empty on a clean post, or an
        explanation when no destination is configured or posting failed.
        The suggestion itself is never rolled back either way.
    """
    configuration = bot.suggestion_database_configuration_repository.get(database.guild_id, database.database_id)
    channel_id = configuration.channels.suggestion_channel_id if configuration is not None else None
    if channel_id is None:
        return (
            False,
            "No public confirmation post was created because no suggestion channel is configured for this collection.",
        )

    embed = build_suggestion_confirmation_embed(
        watch_item,
        database_name=database.name,
        suggested_by=getattr(interaction.user, "mention", str(interaction.user)),
        vote_service=getattr(bot, "vote_service", None),
    )
    view = build_suggestion_view(
        bot.suggestion_service,
        bot.suggestion_database_configuration_repository,
        watch_item,
        database.guild_id,
        permission_service=resolve_permission_service(
            database.guild_id, bot.guild_configuration_repository
        ),
        bot=bot,
    )

    try:
        channel = bot.get_channel(channel_id)
        if channel is None:
            channel = await bot.fetch_channel(channel_id)

        if watch_item.channel_id == channel_id and watch_item.message_id is not None:
            try:
                message = await channel.fetch_message(watch_item.message_id)
                await message.edit(embed=embed, view=view)
                return True, ""
            except discord.HTTPException:
                pass  # The old post is gone or unreachable -- fall through and post fresh.

        message = await channel.send(embed=embed, view=view)
        bot.suggestion_service.set_confirmation_post_reference(
            watch_item.id, database.guild_id, channel_id, message.id
        )
        return True, ""
    except Exception:
        logger.warning("Could not post suggestion confirmation for %s", watch_item.id, exc_info=True)
        return (
            False,
            "The suggestion was saved, but WASH could not post the public confirmation. "
            "Check the configured suggestion channel's permissions.",
        )


async def post_watched_item_archive(bot: "WatchPartyBot", watch_item: WatchItem) -> tuple[bool, str]:
    """Post a freshly-Watched suggestion's own confirmation embed to the
    configured Watched Item Archive (Watched Button & Archive Workflow).

    A one-time post, never edited afterward -- the Watched button is
    disabled the moment a suggestion is marked watched, so there is only
    ever one confirmation to post here, unlike post_suggestion_confirmation
    (which must handle repeated re-confirmation/reactivation).

    Returns:
        A (posted, note) tuple, mirroring post_suggestion_confirmation's
        own contract: note is empty on a clean post, or an actionable
        explanation when no archive is configured or posting failed.
        Marking the suggestion watched is never rolled back either way.
    """
    if watch_item.guild_id is None or watch_item.database_id is None:
        return (
            False,
            "No Watched Item Archive is configured, so this wasn't posted there.",
        )

    destination_channel_id = bot.config_service.resolve_effective_watch_destination(
        watch_item.guild_id, watch_item.database_id
    )
    if destination_channel_id is None:
        return (
            False,
            "No Watched Item Archive is configured, so this wasn't posted there. "
            "Configure one via `/setup` or `/config` to archive future Watched items.",
        )

    database = bot.suggestion_service.get_database(watch_item.database_id)
    suggested_by = (
        f"<@{watch_item.journey.original_suggester}>" if watch_item.journey.original_suggester else "Unknown"
    )
    embed = build_suggestion_confirmation_embed(
        watch_item,
        database_name=database.name if database is not None else "Unknown collection",
        suggested_by=suggested_by,
        vote_service=getattr(bot, "vote_service", None),
    )

    try:
        channel = bot.get_channel(destination_channel_id)
        if channel is None:
            channel = await bot.fetch_channel(destination_channel_id)
        await channel.send(embed=embed)
        return True, ""
    except Exception:
        logger.warning("Could not post Watched Item Archive entry for suggestion %s", watch_item.id, exc_info=True)
        return (
            False,
            "The suggestion was marked watched, but WASH could not post to the Watched Item Archive. "
            "Check that channel's permissions.",
        )


# Rotation-state consistency audit (Section 3): sync_suggestion_status_embed
# retries a transient failure once, after a short delay, rather than leaving
# a post stale until some unrelated later event happens to touch it again.
# Deliberately small and inline (no background job/queue) -- a genuinely
# unrecoverable failure (deleted message, revoked permissions) is detected
# via discord.NotFound/discord.Forbidden and never retried.
_STATUS_EMBED_SYNC_MAX_ATTEMPTS = 2
_STATUS_EMBED_SYNC_RETRY_DELAY_SECONDS = 0.5


class SuggestionPostSyncResult(str, Enum):
    """sync_suggestion_status_embed's outcome, for callers (e.g. IMDb
    Metadata Refresh) that need to know whether the post was actually
    updated rather than only fire-and-forgetting it. Every existing
    caller predating this still just awaits the call and ignores the
    return value, so adding this is fully backward compatible.
    """

    NO_POST = "no_post"  # nothing to sync (no known message/channel, or no resolvable database) -- not a failure.
    SYNCED = "synced"  # the existing post was found and edited successfully.
    FAILED = "failed"  # a post exists but could not be edited (deleted, inaccessible, or a persistent error).


async def sync_suggestion_status_embed(bot: "WatchPartyBot", watch_item: WatchItem) -> SuggestionPostSyncResult:
    """Edit a suggestion's existing public post in place after its status
    changes (Requirement 7), so its Status field never goes stale.

    Deliberately never creates a new post -- "edit the original
    suggestion embed... Do not recreate the message." A suggestion with
    no existing post (message_id/channel_id unset, e.g. one never
    publicly confirmed) is a no-op: there's nothing to sync. An
    unreachable post (deleted, permissions revoked -- discord.NotFound/
    discord.Forbidden) is logged and skipped without retrying, since
    that's a permanent condition. Any other failure (a transient network
    blip, a Discord-side 5xx) is retried once after a short delay before
    being logged and skipped -- see _STATUS_EMBED_SYNC_MAX_ATTEMPTS.
    Either way, the status itself is already correctly persisted
    regardless of whether the embed could be refreshed. Called after
    every status change this milestone introduces admin control over: archive,
    reactivate, and /suggestion edit's Change Status action. Vote
    completion is synchronized separately, once per candidate, by
    sync_vote_completion_status_embeds() -- called after both completion
    paths (a scheduled close_vote job and /edit_vote's "End Now").
    """
    if watch_item.channel_id is None or watch_item.message_id is None:
        return SuggestionPostSyncResult.NO_POST

    database = (
        bot.suggestion_service.get_database(watch_item.database_id) if watch_item.database_id is not None else None
    )
    if database is None:
        return SuggestionPostSyncResult.NO_POST

    suggested_by = (
        f"<@{watch_item.journey.original_suggester}>"
        if watch_item.journey.original_suggester
        else "Unknown"
    )
    embed = build_suggestion_confirmation_embed(
        watch_item,
        database_name=database.name,
        suggested_by=suggested_by,
        vote_service=getattr(bot, "vote_service", None),
    )
    view = build_suggestion_view(
        bot.suggestion_service,
        bot.suggestion_database_configuration_repository,
        watch_item,
        database.guild_id,
        permission_service=resolve_permission_service(
            database.guild_id, bot.guild_configuration_repository
        ),
        bot=bot,
    )

    def log_final_failure(reason: str) -> None:
        # Logs enough to find the exact post by hand: which suggestion,
        # and which Discord message/channel its post lives at.
        logger.warning(
            "Could not sync suggestion %s's status embed (channel %s, message %s) after %s attempt(s); "
            "leaving the existing post as-is (%s)",
            watch_item.id,
            watch_item.channel_id,
            watch_item.message_id,
            attempt + 1,
            reason,
            exc_info=True,
        )

    for attempt in range(_STATUS_EMBED_SYNC_MAX_ATTEMPTS):
        try:
            channel = bot.get_channel(watch_item.channel_id)
            if channel is None:
                channel = await bot.fetch_channel(watch_item.channel_id)
            message = await channel.fetch_message(watch_item.message_id)
            await message.edit(embed=embed, view=view)
            return SuggestionPostSyncResult.SYNCED
        except (discord.NotFound, discord.Forbidden):
            # The message/channel is gone or no longer accessible -- a
            # permanent condition retrying can't fix (message deleted,
            # permissions revoked) -- so this is logged and skipped
            # without spending the remaining retry attempts.
            log_final_failure("permanent: message/channel unreachable")
            return SuggestionPostSyncResult.FAILED
        except Exception:
            if attempt + 1 >= _STATUS_EMBED_SYNC_MAX_ATTEMPTS:
                log_final_failure("transient failure persisted past the retry budget")
                return SuggestionPostSyncResult.FAILED
            # Reliability fix (rotation-state consistency audit): a
            # transient failure (network blip, Discord-side 5xx) used to
            # leave a post permanently stale with no retry. One short,
            # bounded retry -- not a background job or queue -- resolves
            # the common transient case with minimal added complexity.
            logger.info(
                "Retrying suggestion %s's status embed sync (channel %s, message %s) after a transient failure",
                watch_item.id,
                watch_item.channel_id,
                watch_item.message_id,
                exc_info=True,
            )
            await asyncio.sleep(_STATUS_EMBED_SYNC_RETRY_DELAY_SECONDS)


async def sync_vote_start_status_embeds(bot: "WatchPartyBot", candidates: "List[WatchItem]") -> None:
    """Sync every newly-presented candidate's own confirmation-post Status
    field the moment a vote round opens (Suggestion Status Synchronization).

    A nominee is now In an Active Vote the moment perform_start_vote
    returns. Without this, a candidate's public post keeps showing
    whatever status it had when last synced (typically Available) from
    the moment a round opens until the round later closes and
    sync_vote_completion_status_embeds finally corrects it -- the same
    kind of staleness sync_vote_completion_status_embeds fixes for the
    other direction of this same lifecycle.
    """
    for watch_item in candidates:
        await sync_suggestion_status_embed(bot, watch_item)


async def sync_vote_completion_status_embeds(bot: "WatchPartyBot", result: VoteCompletionResult) -> None:
    """Sync every candidate's own confirmation-post Status field once a
    round completes, regardless of whether completion happened via a
    scheduled close_vote job or /edit_vote's "End Now" -- both call this
    after finalize_vote_completion().

    Every candidate, not just the winner(s), must be refreshed here: a
    losing nominee stops being In an Active Vote the moment the round
    completes, but its public post keeps showing whatever status it had
    when last synced (typically In an Active Vote) until something
    actually edits it. Syncing only the winner (the original bug) left
    every losing nominee's post stuck on a stale status indefinitely.
    """
    for suggestion_id in result.vote_round.candidate_suggestion_ids:
        watch_item = bot.suggestion_service.get_suggestion(suggestion_id)
        if watch_item is not None:
            await sync_suggestion_status_embed(bot, watch_item)


async def maybe_send_eligible_pool_warning(bot: "WatchPartyBot", database: SuggestionDatabase) -> None:
    """Send the Eligible Pool Warning when due, and only when due.

    Evaluated after every successful add/reactivation (the pool size may
    have just crossed the configured threshold) and after a vote
    completes (the moment a collection's eligible pool shrinks by
    however many nominees didn't win). EligiblePoolWarningService itself
    enforces the enabled flag, threshold, and threshold-crossing dedup --
    arming/disarming its own state as a side effect of evaluate() -- so
    there is nothing left for this function to record. A Discord send
    failure is logged and swallowed; the warning stays armed regardless
    (matching every other "don't retry a transient send failure" call
    site in this file), so it won't re-fire on the very next unrelated
    change to this collection.
    """
    decision = bot.eligible_pool_warning_service.evaluate(guild_id=database.guild_id, database_id=database.database_id)
    if not decision.should_send or decision.destination_channel_id is None:
        return
    try:
        channel = bot.get_channel(decision.destination_channel_id)
        if channel is None:
            channel = await bot.fetch_channel(decision.destination_channel_id)
        await channel.send(decision.message)
    except Exception:
        logger.warning("Could not send the Eligible Pool Warning for database %s", database.database_id, exc_info=True)


async def finish_add_or_reactivate(
    interaction: discord.Interaction,
    bot: "WatchPartyBot",
    watch_item: WatchItem,
    database: SuggestionDatabase,
    *,
    is_new: bool,
) -> None:
    """Send the ephemeral acknowledgment and attempt the public confirmation post.

    The acknowledgment is always ephemeral (Section 8) regardless of
    whether the public post succeeds, is skipped (no destination
    configured), or fails.
    """
    action_word = "added" if is_new else "reactivated"
    posted, note = await post_suggestion_confirmation(bot, watch_item, database, interaction)
    ack = f'"{watch_item.title}" has been {action_word}. Reference: {watch_item.reference}.'
    if posted and watch_item.channel_id != interaction.channel_id:
        # Only worth a link when /add was invoked somewhere other than
        # the collection's own destination -- otherwise the member is
        # already looking at the post that was just created/refreshed.
        link = build_suggestion_message_link(watch_item)
        if link is not None:
            ack += f"\n[View Suggestion]({link})"
    if note:
        ack += f"\n{note}"
    await interaction.response.send_message(ack, ephemeral=True)

    await maybe_send_eligible_pool_warning(bot, database)


async def handle_add_suggestion(
    interaction: discord.Interaction,
    bot: "WatchPartyBot",
    title: str,
    imdb_url: Optional[str],
    release_year: Optional[int],
) -> None:
    permission = resolve_permission_service(
        interaction.guild_id, bot.guild_configuration_repository
    ).require_watch_party_member(interaction.user)
    if not permission.allowed:
        await interaction.response.send_message(permission.message, ephemeral=True)
        return

    guild_id = interaction.guild_id
    channel_id = interaction.channel_id
    if guild_id is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    resolved = await bot.suggestion_input_service.resolve(title, imdb_url)
    if not resolved.success:
        await interaction.response.send_message(
            resolved.error_message or "I could not resolve that suggestion.", ephemeral=True
        )
        return

    async def on_resolved(target_interaction: discord.Interaction, database: SuggestionDatabase) -> None:
        final_title = resolved.title or title
        final_release_year = release_year if release_year is not None else extract_year_from_title_suffix(final_title)
        is_crew = resolve_permission_service(
            target_interaction.guild_id, bot.guild_configuration_repository
        ).is_wash_crew(target_interaction.user)

        existing_items = bot.suggestion_service.get_suggestions_for_database(
            database.database_id, include_archived=True
        )
        duplicate_result = find_duplicates(
            title=final_title, release_year=final_release_year, imdb_url=resolved.imdb_url, existing_items=existing_items
        )
        decision = decide_add_suggestion_outcome(
            duplicate_result,
            is_crew=is_crew,
            vote_service=getattr(bot, "vote_service", None),
        )

        async def create_new_suggestion(create_interaction: discord.Interaction) -> None:
            result = bot.suggestion_service.suggest(
                final_title,
                resolved.imdb_url,
                database_id=database.database_id,
                guild_id=guild_id,
                channel_id=channel_id,
                release_year=final_release_year,
                runtime_minutes=resolved.runtime_minutes,
                genres=resolved.genres,
                description=resolved.plot,
                content_rating=resolved.content_rating,
                director=resolved.director,
                imdb_rating=resolved.imdb_rating,
                poster_url=resolved.poster_url,
                cast=resolved.cast,
                original_suggester=str(create_interaction.user.id),
            )
            if not result.success or result.watch_item is None:
                await create_interaction.response.send_message(result.message, ephemeral=True)
                return
            await finish_add_or_reactivate(create_interaction, bot, result.watch_item, database, is_new=True)

        async def reactivate_existing(reactivate_interaction: discord.Interaction, matched_item: WatchItem) -> None:
            result = bot.suggestion_service.reactivate_suggestion(matched_item.id)
            if not result.success or result.watch_item is None:
                await reactivate_interaction.response.send_message(result.message, ephemeral=True)
                return
            await finish_add_or_reactivate(reactivate_interaction, bot, result.watch_item, database, is_new=False)

        if decision.kind in (
            AddSuggestionOutcomeKind.BLOCKED_ACTIVE,
            AddSuggestionOutcomeKind.BLOCKED_NO_CREW_OVERRIDE,
            AddSuggestionOutcomeKind.BLOCKED_POSSIBLE_NO_CREW,
        ):
            await target_interaction.response.send_message(decision.message, ephemeral=True)
            return

        if decision.kind is AddSuggestionOutcomeKind.PROCEED:
            await create_new_suggestion(target_interaction)
            return

        if decision.kind is AddSuggestionOutcomeKind.NEEDS_CREW_REACTIVATION_CONFIRM:
            matched_item = decision.matched_item

            async def on_confirm(confirm_interaction: discord.Interaction) -> None:
                await reactivate_existing(confirm_interaction, matched_item)

            async def on_abort(abort_interaction: discord.Interaction) -> None:
                await abort_interaction.response.send_message("No changes were made.", ephemeral=True)

            view = EditVoteConfirmationView(
                confirm_label="Reactivate",
                on_confirm=on_confirm,
                on_abort=on_abort,
                confirm_style=discord.ButtonStyle.primary,
            )
            await target_interaction.response.send_message(decision.message, view=view, ephemeral=True)
            return

        # NEEDS_CREW_POSSIBLE_CONFIRM
        async def on_confirm(confirm_interaction: discord.Interaction) -> None:
            await create_new_suggestion(confirm_interaction)

        async def on_abort(abort_interaction: discord.Interaction) -> None:
            await abort_interaction.response.send_message("No changes were made.", ephemeral=True)

        view = EditVoteConfirmationView(
            confirm_label="Add Anyway",
            on_confirm=on_confirm,
            on_abort=on_abort,
            confirm_style=discord.ButtonStyle.primary,
        )
        await target_interaction.response.send_message(decision.message, view=view, ephemeral=True)

    await resolve_database_then(interaction, bot, guild_id, channel_id, on_resolved)


def find_backup_by_filename(backup_service: BackupService, backup_filename: str) -> Optional[Path]:
    """Find a known backup archive by exact filename, across all backup kinds.

    Args:
        backup_service: The backup service to list known archives from.
        backup_filename: The archive's filename (not a full path), as
            reported by /backup or a prior /restore attempt.

    Returns:
        The archive's full path if a backup with that filename exists,
        otherwise None.
    """
    for archive_path in backup_service.list_backups():
        if archive_path.name == backup_filename:
            return archive_path
    return None


def build_backup_not_found_message(backup_service: BackupService, backup_filename: str) -> str:
    """Build a clear error message listing valid backup filenames.

    Args:
        backup_service: The backup service to list known archives from.
        backup_filename: The filename that couldn't be found.

    Returns:
        An error message. Lists every currently known backup filename so
        the member can retry with a valid one, or explains that none
        exist yet.
    """
    available = [archive_path.name for archive_path in backup_service.list_backups()]
    if not available:
        return f"No backups are available to restore. (Requested: `{backup_filename}`)"
    listed = "\n".join(f"- `{name}`" for name in available)
    return f"No backup named `{backup_filename}` was found. Available backups:\n{listed}"


BACKUP_DISPLAY_NAME_PREFIX = "Watch_Party_Manager_Backup"


def build_backup_display_filename(created_at: datetime) -> str:
    """Build the user-facing filename for a backup's Discord attachment.

    Uses the project's own name (Watch Party Manager), not the bot's
    Discord-facing name (WASH), so a downloaded backup is self-explanatory
    outside of Discord. The archive stored under data/backups/ keeps its
    existing wash-<kind>-<timestamp>.zip name unchanged -- retention and
    /restore's filename lookup both depend on that internal name, so only
    the attachment/display name changes here.
    """
    return f"{BACKUP_DISPLAY_NAME_PREFIX}_{created_at.strftime('%Y-%m-%d_%H-%M-%S')}.zip"


def _parse_manifest_created_at(created_at: str) -> datetime:
    return datetime.fromisoformat(created_at.replace("Z", "+00:00"))


def perform_backup(
    backup_service: BackupService,
    user: object,
    wash_crew_role_id: Optional[int],
) -> tuple[str, bool, Optional[Path], Optional[str]]:
    """Create an immediate manual backup for the WASH Crew-only /backup command.

    Args:
        backup_service: The backup service to create the archive through.
        user: The member invoking the command.
        wash_crew_role_id: The configured WASH Crew role ID, or None if
            unconfigured.

    Returns:
        A (message, ephemeral, archive_path, display_filename) tuple.
        Every /maintenance backup response is ephemeral -- this is an admin
        maintenance command. archive_path/display_filename are None
        whenever no backup was created (permission failure or
        BackupError), telling the caller there's nothing to attach.
    """
    if wash_crew_role_id is None:
        return (
            "WASH Crew permissions have not been configured. "
            "Set WASH_CREW_ROLE_ID before using this command.",
            True,
            None,
            None,
        )
    if not is_wash_crew_member(user, wash_crew_role_id):
        return "You need the WASH Crew role to create a backup.", True, None, None

    try:
        result = backup_service.create_backup(BackupKind.MANUAL)
    except BackupError as exc:
        return f"Backup failed: {exc}", True, None, None

    created_at = _parse_manifest_created_at(result.manifest.created_at)
    display_filename = build_backup_display_filename(created_at)
    message = (
        "Backup created successfully.\n"
        f"**Filename:** `{display_filename}`\n"
        f"**Created:** {format_datetime_for_display(created_at)}\n"
        f"**Type:** {result.manifest.kind.value.capitalize()}"
    )
    return message, True, result.archive_path, display_filename


def build_restore_summary_text(summary: RestoreSummary) -> str:
    """Render a RestoreSummary as the text shown between validation and confirmation.

    Only ever shows fields the summary itself could determine -- None
    values are simply omitted rather than displayed as 0 or "unknown",
    per FR-032B's "do not report values that cannot be reliably
    determined."
    """
    if not summary.is_valid:
        detail = "; ".join(summary.errors) or "unknown validation error"
        return f"This backup failed validation and cannot be restored: {detail}"

    lines = ["**Restore Summary**", ""]
    if summary.project_name:
        lines.append(f"Project: {summary.project_name}")
    if summary.application_version:
        lines.append(f"Application version: {summary.application_version}")
    if summary.created_at:
        try:
            created_at = datetime.fromisoformat(summary.created_at.replace("Z", "+00:00"))
            lines.append(f"Created: {format_datetime_for_display(created_at)}")
        except ValueError:
            pass
    if summary.backup_type is not None:
        lines.append(f"Backup type: {summary.backup_type.value.replace('_', ' ').title()}")
    if summary.database_name:
        lines.append(f"Collection: {summary.database_name} (ID {summary.database_id})")
    if summary.guild_id is not None:
        lines.append(f"Server ID: {summary.guild_id}")
    if summary.suggestion_database_count is not None:
        lines.append(f"Collections: {summary.suggestion_database_count}")
    if summary.suggestion_count is not None:
        lines.append(f"Suggestions: {summary.suggestion_count}")
    if summary.vote_round_count is not None:
        lines.append(f"Vote rounds: {summary.vote_round_count}")
    if summary.membership_request_count is not None:
        lines.append(f"Membership requests: {summary.membership_request_count}")
    if summary.configuration_present is not None:
        lines.append(f"Server configuration present: {'Yes' if summary.configuration_present else 'No'}")

    if summary.warnings:
        lines.append("")
        lines.append("**Warnings**")
        lines.extend(f"- {warning}" for warning in summary.warnings)

    return "\n".join(lines)


def perform_restore_from_path(
    backup_service: BackupService,
    user: object,
    wash_crew_role_id: Optional[int],
    archive_path: Path,
) -> tuple[str, bool, bool]:
    """Validate a full-restore candidate and build its summary + confirmation gate.

    Never restores anything -- bot.py resolves a `backup_filename`
    argument to a path (via find_backup_by_filename) or downloads an
    uploaded `backup_file` attachment to a temporary path before calling
    this, so this function only ever deals in already-resolved paths.

    Returns:
        A (message, ephemeral, needs_confirmation) tuple. needs_confirmation
        is False for a permission failure or a backup that fails
        validation, and True only when the summary is valid and the
        member should be shown the confirm/cancel prompt.
    """
    if wash_crew_role_id is None:
        return (
            "WASH Crew permissions have not been configured. "
            "Set WASH_CREW_ROLE_ID before using this command.",
            True,
            False,
        )
    if not is_wash_crew_member(user, wash_crew_role_id):
        return "You need the WASH Crew role to restore a backup.", True, False

    summary = build_restore_summary(backup_service, archive_path, expected_backup_type=BackupType.FULL)
    text = build_restore_summary_text(summary)
    if summary.is_valid:
        text += (
            "\n\nRestoring will overwrite WASH's current data with this backup's contents. "
            "A safety backup of the current data will be made automatically first. "
            "A bot restart is recommended afterward so all in-memory state reflects the restored data."
        )
    return text, True, summary.is_valid


def perform_confirmed_restore_from_path(
    backup_service: BackupService,
    user: object,
    wash_crew_role_id: Optional[int],
    archive_path: Path,
) -> tuple[str, bool]:
    """Perform the actual full restore after WASH Crew has confirmed.

    Re-checks the WASH Crew permission and re-validates archive_path
    from scratch rather than trusting the initial /restore call -- the
    confirm click is a separate interaction, and the file (especially an
    uploaded one sitting in a temporary directory) could have changed or
    vanished since.
    """
    if wash_crew_role_id is None:
        return (
            "WASH Crew permissions have not been configured. "
            "Set WASH_CREW_ROLE_ID before using this command.",
            True,
        )
    if not is_wash_crew_member(user, wash_crew_role_id):
        return "You need the WASH Crew role to restore a backup.", True

    summary = build_restore_summary(backup_service, archive_path, expected_backup_type=BackupType.FULL)
    if not summary.is_valid:
        detail = "; ".join(summary.errors) or "unknown validation error"
        return f"That backup failed validation and cannot be restored: {detail}", True

    try:
        result = backup_service.restore_backup(archive_path)
    except BackupError as exc:
        detail = str(exc)
        if detail.startswith("Could not create backup"):
            return (
                f"Safety backup failed, so the restore was aborted. Your live data was NOT changed. Details: {detail}",
                True,
            )
        return (
            f"Restore failed after the safety backup succeeded. Your previous data is preserved in that "
            f"safety backup. Details: {detail}",
            True,
        )

    message = f"Restored {len(result.restored_files)} file(s) from this backup."
    if result.safety_backup is not None:
        message += f" A safety backup of your previous data was made first: `{result.safety_backup.name}`."
    message += " A bot restart is recommended so all in-memory state reflects the restored data."
    return message, True


async def handle_restore(
    interaction: discord.Interaction,
    bot: "WatchPartyBot",
    backup_filename: Optional[str],
    backup_file: Optional[discord.Attachment],
) -> None:
    """Handle /maintenance restore: select-or-upload -> validate -> summary -> confirm/cancel.

    Downloading an uploaded attachment is a network call that can
    outlast Discord's 3-second initial-response window, so this always
    defers first and replies via followup from then on -- unlike every
    other command in this file, which responds directly.
    """
    wash_crew_role_id = resolve_permission_service(
        interaction.guild_id, bot.guild_configuration_repository
    ).wash_crew_role_id
    if wash_crew_role_id is None:
        await interaction.response.send_message(
            "WASH Crew permissions have not been configured. Set WASH_CREW_ROLE_ID before using this command.",
            ephemeral=True,
        )
        return
    if not is_wash_crew_member(interaction.user, wash_crew_role_id):
        await interaction.response.send_message("You need the WASH Crew role to restore a backup.", ephemeral=True)
        return

    if (backup_filename is None) == (backup_file is None):
        await interaction.response.send_message(
            "Provide exactly one of `backup_filename` (an existing local backup) or "
            "`backup_file` (a .zip to upload).",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    temporary_directory: Optional[tempfile.TemporaryDirectory] = None
    if backup_file is not None:
        if not backup_file.filename.casefold().endswith(".zip"):
            await interaction.followup.send("The uploaded file must be a `.zip` backup archive.", ephemeral=True)
            return
        temporary_directory = tempfile.TemporaryDirectory()
        archive_path = Path(temporary_directory.name) / "uploaded-backup.zip"
        try:
            await backup_file.save(archive_path)
        except discord.HTTPException as exc:
            temporary_directory.cleanup()
            await interaction.followup.send(f"Could not download the uploaded backup: {exc}", ephemeral=True)
            return
    else:
        found = find_backup_by_filename(bot.backup_service, backup_filename)
        if found is None:
            await interaction.followup.send(
                build_backup_not_found_message(bot.backup_service, backup_filename), ephemeral=True
            )
            return
        archive_path = found

    message, _, needs_confirmation = perform_restore_from_path(
        bot.backup_service, interaction.user, wash_crew_role_id, archive_path
    )
    if not needs_confirmation:
        if temporary_directory is not None:
            temporary_directory.cleanup()
        await interaction.followup.send(message, ephemeral=True)
        return

    async def on_confirm(confirm_interaction: discord.Interaction) -> None:
        try:
            result_message, result_ephemeral = perform_confirmed_restore_from_path(
                bot.backup_service,
                confirm_interaction.user,
                resolve_permission_service(
                    confirm_interaction.guild_id, bot.guild_configuration_repository
                ).wash_crew_role_id,
                archive_path,
            )
        finally:
            if temporary_directory is not None:
                temporary_directory.cleanup()
        await confirm_interaction.response.send_message(result_message, ephemeral=result_ephemeral)

    async def on_cancel(cancel_interaction: discord.Interaction) -> None:
        if temporary_directory is not None:
            temporary_directory.cleanup()
        await cancel_interaction.response.send_message("Restore cancelled. No data was changed.", ephemeral=True)

    view = RestoreConfirmationView(on_confirm, on_cancel)
    await interaction.followup.send(message, view=view, ephemeral=True)


async def start_database_backup(
    interaction: discord.Interaction, bot: "WatchPartyBot", guild_id: int, database_id: int
) -> None:
    """Back up one already-known database. Shared by /database backup
    (after its own picker) and /database manage's Backup Collection
    action -- neither duplicates this logic.
    """
    result = create_database_backup(
        bot.backup_service,
        bot.suggestion_database_repository,
        bot.suggestion_repository,
        bot.suggestion_database_configuration_repository,
        guild_id,
        database_id,
    )
    if not result.success or result.creation is None or result.display_filename is None:
        await interaction.response.send_message(result.message, ephemeral=True)
        return

    file = discord.File(result.creation.archive_path, filename=result.display_filename)
    await interaction.response.send_message(result.message, file=file, ephemeral=True)


async def handle_database_backup(interaction: discord.Interaction, bot: "WatchPartyBot") -> None:
    """Show a "which database?" picker, then back up the chosen one.

    Release Polish (Discord-native UX): database_id is no longer a
    command parameter -- WASH Crew picks the target from a selector
    showing each database's name, Active/Inactive status, and watch-item
    count instead of typing an internal ID.
    """
    guild_permission_service = resolve_permission_service(interaction.guild_id, bot.guild_configuration_repository)
    if guild_permission_service.wash_crew_role_id is None:
        await interaction.response.send_message(
            "WASH Crew permissions have not been configured. Set WASH_CREW_ROLE_ID before using this command.",
            ephemeral=True,
        )
        return
    if not guild_permission_service.is_wash_crew(interaction.user):
        await interaction.response.send_message(
            "You need the WASH Crew role to back up a collection.", ephemeral=True
        )
        return

    guild_id = interaction.guild_id
    if guild_id is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    databases = bot.suggestion_service.list_databases(guild_id)
    if not databases:
        await interaction.response.send_message("No collections exist in this server yet. Create one with `/database add`.", ephemeral=True)
        return

    async def on_select(select_interaction: discord.Interaction, database_id: int) -> None:
        await start_database_backup(select_interaction, bot, guild_id, database_id)

    options = build_database_admin_options(bot.suggestion_service, databases, interaction.guild, bot.suggestion_database_configuration_repository)
    view = DatabaseAdminSelectView(
        options, on_select, custom_id="wpm_database_backup_select", placeholder="Choose a collection to back up..."
    )
    await interaction.response.send_message("Choose which collection to back up:", view=view, ephemeral=True)


async def handle_database_restore(
    interaction: discord.Interaction,
    bot: "WatchPartyBot",
    mode: str,
    backup_filename: Optional[str],
    backup_file: Optional[discord.Attachment],
) -> None:
    guild_permission_service = resolve_permission_service(interaction.guild_id, bot.guild_configuration_repository)
    if guild_permission_service.wash_crew_role_id is None:
        await interaction.response.send_message(
            "WASH Crew permissions have not been configured. Set WASH_CREW_ROLE_ID before using this command.",
            ephemeral=True,
        )
        return
    if not guild_permission_service.is_wash_crew(interaction.user):
        await interaction.response.send_message(
            "You need the WASH Crew role to restore a collection.", ephemeral=True
        )
        return

    guild_id = interaction.guild_id
    if guild_id is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    try:
        restore_mode = DatabaseRestoreMode(mode)
    except ValueError:
        await interaction.response.send_message("Choose either Merge or Replace.", ephemeral=True)
        return

    if (backup_filename is None) == (backup_file is None):
        await interaction.response.send_message(
            "Provide exactly one of `backup_filename` (an existing local backup) or "
            "`backup_file` (a .zip to upload).",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    temporary_directory: Optional[tempfile.TemporaryDirectory] = None
    if backup_file is not None:
        if not backup_file.filename.casefold().endswith(".zip"):
            await interaction.followup.send("The uploaded file must be a `.zip` backup archive.", ephemeral=True)
            return
        temporary_directory = tempfile.TemporaryDirectory()
        archive_path = Path(temporary_directory.name) / "uploaded-database-backup.zip"
        try:
            await backup_file.save(archive_path)
        except discord.HTTPException as exc:
            temporary_directory.cleanup()
            await interaction.followup.send(f"Could not download the uploaded backup: {exc}", ephemeral=True)
            return
    else:
        found = find_backup_by_filename(bot.backup_service, backup_filename)
        if found is None:
            await interaction.followup.send(
                build_backup_not_found_message(bot.backup_service, backup_filename), ephemeral=True
            )
            return
        archive_path = found

    summary = build_restore_summary(bot.backup_service, archive_path, expected_backup_type=BackupType.SUGGESTION_DATABASE)
    text = build_restore_summary_text(summary)
    if not summary.is_valid:
        if temporary_directory is not None:
            temporary_directory.cleanup()
        await interaction.followup.send(text, ephemeral=True)
        return

    text += (
        f"\n\n{restore_mode.value.title()} this collection? "
        "A safety backup of your current data will be made automatically first."
    )

    async def on_confirm(confirm_interaction: discord.Interaction) -> None:
        try:
            result = restore_database_backup(
                bot.backup_service,
                bot.suggestion_database_repository,
                bot.suggestion_repository,
                bot.suggestion_database_configuration_repository,
                archive_path,
                guild_id,
                restore_mode,
            )
        finally:
            if temporary_directory is not None:
                temporary_directory.cleanup()
        message = result.message
        if result.success:
            message += " A bot restart is recommended so all in-memory state reflects the restored data."
        await confirm_interaction.response.send_message(message, ephemeral=True)

    async def on_cancel(cancel_interaction: discord.Interaction) -> None:
        if temporary_directory is not None:
            temporary_directory.cleanup()
        await cancel_interaction.response.send_message("Restore cancelled. No data was changed.", ephemeral=True)

    view = RestoreConfirmationView(on_confirm, on_cancel)
    await interaction.followup.send(text, view=view, ephemeral=True)


# --- FR-032C: suggestion database reset ----------------------------------------------


def build_database_reset_summary_text(summary) -> str:
    return (
        f'**Reset Collection "{summary.database_name}"**\n\n'
        f"This will permanently remove {format_count(summary.suggestion_count, 'suggestion')} from this collection.\n"
        "The collection itself, its configuration, and every other collection will NOT be affected."
    )


async def start_database_reset(
    interaction: discord.Interaction, bot: "WatchPartyBot", guild_id: int, database_id: int
) -> None:
    """Show the existing RESET confirmation flow for one already-known
    database. Shared by /database reset (after its own picker) and
    /database manage's Reset Collection action -- neither duplicates
    this logic.
    """
    summary = build_database_reset_summary(
        bot.suggestion_database_repository, bot.suggestion_repository, guild_id, database_id
    )
    if summary is None:
        await interaction.response.send_message(
            "No collection with that ID exists in this server.", ephemeral=True
        )
        return

    async def on_confirm(confirm_interaction: discord.Interaction) -> None:
        if not resolve_permission_service(guild_id, bot.guild_configuration_repository).is_wash_crew(
            confirm_interaction.user
        ):
            await confirm_interaction.response.send_message(
                "You need the WASH Crew role to reset a collection.", ephemeral=True
            )
            return
        result = reset_suggestion_database(
            bot.backup_service,
            bot.suggestion_database_repository,
            bot.suggestion_repository,
            bot.suggestion_service,
            guild_id,
            database_id,
        )
        await confirm_interaction.response.send_message(result.message, ephemeral=True)

    async def on_cancel(cancel_interaction: discord.Interaction) -> None:
        await cancel_interaction.response.send_message("Reset cancelled. No data was changed.", ephemeral=True)

    confirmation_view = DestructiveConfirmationView(
        button_label="Reset",
        required_text="RESET",
        modal_title="Reset Collection",
        custom_id_prefix="database_reset",
        on_confirm=on_confirm,
        on_cancel=on_cancel,
    )
    await interaction.response.send_message(
        build_database_reset_summary_text(summary), view=confirmation_view, ephemeral=True
    )


async def handle_database_reset(interaction: discord.Interaction, bot: "WatchPartyBot") -> None:
    """Show a "which database?" picker, then the existing RESET
    confirmation flow for the chosen one.

    Release Polish (Discord-native UX): database_id is no longer a
    command parameter -- WASH Crew picks the target from a selector
    showing each database's name, Active/Inactive status, and watch-item
    count instead of typing an internal ID.
    """
    guild_permission_service = resolve_permission_service(interaction.guild_id, bot.guild_configuration_repository)
    if guild_permission_service.wash_crew_role_id is None:
        await interaction.response.send_message(
            "WASH Crew permissions have not been configured. Set WASH_CREW_ROLE_ID before using this command.",
            ephemeral=True,
        )
        return
    if not guild_permission_service.is_wash_crew(interaction.user):
        await interaction.response.send_message(
            "You need the WASH Crew role to reset a collection.", ephemeral=True
        )
        return

    guild_id = interaction.guild_id
    if guild_id is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    databases = bot.suggestion_service.list_databases(guild_id)
    if not databases:
        await interaction.response.send_message("No collections exist in this server yet. Create one with `/database add`.", ephemeral=True)
        return

    async def on_select(select_interaction: discord.Interaction, database_id: int) -> None:
        await start_database_reset(select_interaction, bot, guild_id, database_id)

    options = build_database_admin_options(bot.suggestion_service, databases, interaction.guild, bot.suggestion_database_configuration_repository)
    view = DatabaseAdminSelectView(
        options, on_select, custom_id="wpm_database_reset_select", placeholder="Choose a collection to reset..."
    )
    await interaction.response.send_message("Choose which collection to reset:", view=view, ephemeral=True)


# --- FR-032C: factory reset -----------------------------------------------------------


def build_factory_reset_summary_text(summary) -> str:
    lines = [
        "**Factory Reset**",
        "",
        "This will permanently remove ALL WASH-managed data for this server, including:",
        f"- Server configuration: {'present' if summary.configuration_present else 'not configured'}",
        f"- Collections: {summary.suggestion_database_count}",
        f"- Suggestions: {summary.suggestion_count}",
        f"- Vote rounds: {summary.vote_round_count}",
        f"- Membership requests: {summary.membership_request_count}",
        f"- Scheduled watch parties: {summary.watch_party_count}",
        f"- Scheduled jobs: {summary.scheduled_job_count}",
        "",
        "Backup archives, environment files, and application code are NOT affected. "
        "WASH will require `/setup` again afterward.",
    ]
    return "\n".join(lines)


async def handle_factory_reset(interaction: discord.Interaction, bot: "WatchPartyBot") -> None:
    guild_permission_service = resolve_permission_service(interaction.guild_id, bot.guild_configuration_repository)
    if guild_permission_service.wash_crew_role_id is None:
        await interaction.response.send_message(
            "WASH Crew permissions have not been configured. Set WASH_CREW_ROLE_ID before using this command.",
            ephemeral=True,
        )
        return
    if not guild_permission_service.is_wash_crew(interaction.user):
        await interaction.response.send_message("You need the WASH Crew role to factory reset WASH.", ephemeral=True)
        return

    guild_id = interaction.guild_id
    if guild_id is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    summary = await build_factory_reset_summary(
        guild_configuration_repository=bot.guild_configuration_repository,
        database_repository=bot.suggestion_database_repository,
        suggestion_repository=bot.suggestion_repository,
        vote_repository=bot.vote_repository,
        membership_request_repository=bot.membership_request_repository,
        watch_party_repository=bot.watch_party_repository,
        scheduler_repository=bot.scheduler_repository,
        guild_id=guild_id,
    )

    async def on_confirm(confirm_interaction: discord.Interaction) -> None:
        if not resolve_permission_service(guild_id, bot.guild_configuration_repository).is_wash_crew(
            confirm_interaction.user
        ):
            await confirm_interaction.response.send_message(
                "You need the WASH Crew role to factory reset WASH.", ephemeral=True
            )
            return
        result = await perform_factory_reset(
            backup_service=bot.backup_service,
            guild_configuration_repository=bot.guild_configuration_repository,
            setup_wizard_repository=bot.setup_wizard_repository,
            database_repository=bot.suggestion_database_repository,
            suggestion_repository=bot.suggestion_repository,
            suggestion_service=bot.suggestion_service,
            configuration_repository=bot.suggestion_database_configuration_repository,
            vote_repository=bot.vote_repository,
            membership_request_repository=bot.membership_request_repository,
            watch_party_repository=bot.watch_party_repository,
            scheduler_repository=bot.scheduler_repository,
            guild_id=guild_id,
        )
        await confirm_interaction.response.send_message(result.message, ephemeral=True)

    async def on_cancel(cancel_interaction: discord.Interaction) -> None:
        await cancel_interaction.response.send_message(
            "Factory reset cancelled. No data was changed.", ephemeral=True
        )

    view = DestructiveConfirmationView(
        button_label="Factory Reset",
        required_text="RESET",
        modal_title="Factory Reset",
        custom_id_prefix="factory_reset",
        on_confirm=on_confirm,
        on_cancel=on_cancel,
    )
    await interaction.response.send_message(build_factory_reset_summary_text(summary), view=view, ephemeral=True)


# --- FR-032C: import from another WASH instance ---------------------------------------


def build_import_result_text(result) -> str:
    lines = [result.message]
    if result.excluded:
        lines.append("")
        lines.append("Not imported by design: " + "; ".join(result.excluded) + ".")
    if result.success:
        lines.append("A bot restart is recommended so all in-memory state reflects the imported data.")
    return "\n".join(lines)


async def handle_import(
    interaction: discord.Interaction, bot: "WatchPartyBot", backup_file: discord.Attachment
) -> None:
    guild_permission_service = resolve_permission_service(interaction.guild_id, bot.guild_configuration_repository)
    if guild_permission_service.wash_crew_role_id is None:
        await interaction.response.send_message(
            "WASH Crew permissions have not been configured. Set WASH_CREW_ROLE_ID before using this command.",
            ephemeral=True,
        )
        return
    if not guild_permission_service.is_wash_crew(interaction.user):
        await interaction.response.send_message("You need the WASH Crew role to import a backup.", ephemeral=True)
        return

    guild_id = interaction.guild_id
    if guild_id is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    if not backup_file.filename.casefold().endswith(".zip"):
        await interaction.response.send_message("The uploaded file must be a `.zip` backup archive.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    temporary_directory = tempfile.TemporaryDirectory()
    archive_path = Path(temporary_directory.name) / "uploaded-import.zip"
    try:
        await backup_file.save(archive_path)
    except discord.HTTPException as exc:
        temporary_directory.cleanup()
        await interaction.followup.send(f"Could not download the uploaded backup: {exc}", ephemeral=True)
        return

    summary = build_import_summary(bot.backup_service, archive_path)
    text = build_restore_summary_text(summary)
    if not summary.is_valid:
        temporary_directory.cleanup()
        await interaction.followup.send(text, ephemeral=True)
        return

    text += (
        "\n\nChoose how to import this backup's collections, suggestions, and vote rounds. "
        "Your Discord role/channel configuration and server ID will never be changed by an import."
    )

    async def _run_import(run_interaction: discord.Interaction, mode: ImportMode) -> None:
        if not resolve_permission_service(guild_id, bot.guild_configuration_repository).is_wash_crew(
            run_interaction.user
        ):
            await run_interaction.response.send_message(
                "You need the WASH Crew role to import a backup.", ephemeral=True
            )
            return
        try:
            result = await import_backup(
                bot.backup_service,
                bot.suggestion_database_repository,
                bot.suggestion_repository,
                bot.suggestion_database_configuration_repository,
                bot.vote_repository,
                archive_path,
                guild_id,
                mode,
            )
        finally:
            temporary_directory.cleanup()
        await run_interaction.response.send_message(build_import_result_text(result), ephemeral=True)

    async def on_merge(merge_interaction: discord.Interaction) -> None:
        await _run_import(merge_interaction, ImportMode.MERGE)

    async def on_replace(replace_interaction: discord.Interaction) -> None:
        await _run_import(replace_interaction, ImportMode.REPLACE)

    async def on_cancel(cancel_interaction: discord.Interaction) -> None:
        temporary_directory.cleanup()
        await cancel_interaction.response.send_message("Import cancelled. No data was changed.", ephemeral=True)

    view = ImportModeChoiceView(on_merge=on_merge, on_replace=on_replace, on_cancel=on_cancel)
    await interaction.followup.send(text, view=view, ephemeral=True)


def build_suggestion_confirmation_embed(
    watch_item: WatchItem,
    *,
    database_name: str,
    suggested_by: str,
    vote_service: Optional[VoteService] = None,
):
    """Build the public /add confirmation as a compact record-style embed.

    vote_service is optional (defaults to None, meaning In an Active Vote
    is never shown) so existing callers/tests that construct this without
    one keep working unchanged -- see
    suggestion_display_status.resolve_display_status.

    Deliberately built without EmbedFactory's default footer/timestamp
    (unlike most other WASH embeds): this post is edited in place
    repeatedly as the suggestion's status changes (see
    sync_suggestion_status_embed), and a timestamp would read as "just
    added" every time it's resynced, misrepresenting when the suggestion
    actually entered the collection.
    """
    imdb_url = watch_item.metadata_ids.get(MetadataProvider.IMDB)
    description_parts: list[str] = []
    if watch_item.description:
        description_parts.append(watch_item.description)
    if imdb_url:
        description_parts.append(f"[View on IMDb]({imdb_url})")

    embed = discord.Embed(
        title=watch_item.title,
        description="\n\n".join(description_parts) or None,
        url=imdb_url,
        color=0xF5C518,
    )
    details: list[str] = []
    if watch_item.genres:
        details.append(" • ".join(watch_item.genres))
    if watch_item.runtime_minutes:
        details.append(f"{watch_item.runtime_minutes} min")
    if watch_item.content_rating:
        details.append(f"Rated {watch_item.content_rating}")
    if details:
        embed.add_field(name="Details", value=" • ".join(details), inline=False)
    if watch_item.director:
        embed.add_field(name="Director", value=watch_item.director, inline=True)
    if watch_item.imdb_rating:
        embed.add_field(name="IMDb Rating", value=f"{watch_item.imdb_rating}/10", inline=True)
    embed.add_field(name="Suggested By", value=suggested_by, inline=True)
    embed.add_field(name="Collection", value=format_collection_display(database_name), inline=True)
    embed.add_field(name="Reference", value=watch_item.reference, inline=True)
    display_status = resolve_display_status(watch_item, vote_service)
    embed.add_field(
        name="Status", value=format_display_status_with_won_date(watch_item, display_status), inline=True
    )
    if watch_item.poster_url:
        embed.set_thumbnail(url=watch_item.poster_url)
    return embed


def perform_add_suggestion(
    suggestion_service: SuggestionService,
    guild_id: Optional[int],
    channel_id: Optional[int],
    title: str,
    imdb_url: Optional[str],
    runtime_minutes: Optional[int] = None,
    genres: tuple[str, ...] = (),
    description: Optional[str] = None,
    content_rating: Optional[str] = None,
    director: Optional[str] = None,
    imdb_rating: Optional[str] = None,
    poster_url: Optional[str] = None,
    cast: tuple[str, ...] = (),
) -> tuple[str, bool, Optional[WatchItem]]:
    """Core logic for /add, kept free of Discord objects except raw IDs.

    Resolves which suggestion database this channel maps to (via
    SuggestionService.resolve_database_for_channel) before delegating the
    actual suggestion creation to SuggestionService.suggest(). This
    function never duplicates either of those services' own validation.

    Args:
        suggestion_service: The suggestion service to resolve a database
            through and add the suggestion to.
        guild_id: The Discord guild the command was run in.
        channel_id: The Discord channel or thread the command was run in.
        title: The movie/show title.
        imdb_url: Optional IMDb URL or ID.

    Returns:
        A (message, ephemeral, watch_item) tuple. watch_item is the newly
        created suggestion on success, so its Discord message ID can be
        attached once the confirmation has actually been sent -- it's
        None on any failure (no usable database, or suggest() itself
        rejected the title).
    """
    resolution = suggestion_service.resolve_database_for_channel(guild_id, channel_id)
    if resolution.database is None:
        return (
            resolution.error_message
            or "Which collection would you like to use? Run this in the channel or thread "
            "configured for the one you mean.",
            True,
            None,
        )

    result = suggestion_service.suggest(
        title,
        imdb_url,
        database_id=resolution.database.database_id,
        guild_id=guild_id,
        channel_id=channel_id,
        runtime_minutes=runtime_minutes,
        genres=genres,
        description=description,
        content_rating=content_rating,
        director=director,
        imdb_rating=imdb_rating,
        poster_url=poster_url,
        cast=cast,
    )
    if not result.success:
        return result.message, False, None

    return result.message, False, result.watch_item


async def show_repair_confirmation(interaction: discord.Interaction, bot: "WatchPartyBot") -> None:
    """`/maintenance repair`'s confirmation screen (First-Time UX Polish),
    shown before anything is scanned or changed.

    Permission-gated up front, matching handle_factory_reset's and every
    other Crew-only confirmation screen's own convention -- an
    unauthorized member is rejected before ever seeing the confirmation
    text, not after clicking Start Repair.
    """
    guild_permission_service = resolve_permission_service(interaction.guild_id, bot.guild_configuration_repository)
    if guild_permission_service.wash_crew_role_id is None:
        await interaction.response.send_message(
            "WASH Crew permissions have not been configured. Set WASH_CREW_ROLE_ID before using this command.",
            ephemeral=True,
        )
        return
    if not guild_permission_service.is_wash_crew(interaction.user):
        await interaction.response.send_message("You need the WASH Crew role to repair suggestions.", ephemeral=True)
        return

    async def on_confirm(confirm_interaction: discord.Interaction) -> None:
        await confirm_interaction.response.edit_message(
            content="**Repairing suggestions...** This may take a few minutes -- WASH will post a full summary when it's done.",
            view=None,
        )
        message, _ = await perform_repair_suggestions(
            repair_service=bot.suggestion_repair_service,
            user=confirm_interaction.user,
            wash_crew_role_id=resolve_permission_service(
                confirm_interaction.guild_id, bot.guild_configuration_repository
            ).wash_crew_role_id,
            guild_id=confirm_interaction.guild_id,
            bot=bot,
        )
        await confirm_interaction.edit_original_response(content=message)

    async def on_abort(abort_interaction: discord.Interaction) -> None:
        await abort_interaction.response.edit_message(content="Suggestion repair cancelled. No changes were made.", view=None)

    view = EditVoteConfirmationView(
        confirm_label="Start Repair", on_confirm=on_confirm, on_abort=on_abort, confirm_style=discord.ButtonStyle.primary
    )
    text = (
        "**Suggestion Repair -- Confirm**\n\n"
        "Scans every suggestion for malformed or legacy IMDb-link titles and known bad titles, "
        "repairing what it can and removing what it can't.\n\n"
        "- This may take several minutes on larger collections, since each malformed suggestion is "
        "re-resolved against IMDb one at a time.\n"
        "- IMDb lookups and Discord's own rate limits may both slow progress if many suggestions need repair.\n"
        "- Existing suggestion posts for repaired suggestions will be located and synchronized to show "
        "the corrected title.\n"
        "- WASH continues processing every suggestion even if some individually fail.\n"
        "- Progress is shown when the repair starts, and a full summary is posted once it finishes."
    )
    await interaction.response.send_message(text, view=view, ephemeral=True)


async def perform_repair_suggestions(
    repair_service: SuggestionRepairService,
    user: object,
    wash_crew_role_id: Optional[int],
    guild_id: Optional[int],
    bot: Optional["WatchPartyBot"] = None,
) -> tuple[str, bool]:
    """Run the WASH Crew-only suggestion repair workflow.

    guild_id: The Discord guild the command was run in. Required by
        repair_all() (not optional there) so a repair triggered in one
        guild can never scan or modify a different guild's suggestions,
        regardless of how many other guilds this WASH process also
        serves. /maintenance repair is only ever reachable from inside a
        guild, so this is always a real ID by the time it reaches here.
    bot: when supplied (the real `/maintenance repair` command always
    passes it), each successfully repaired suggestion's existing public
    post is located and resynced via sync_suggestion_status_embed, so a
    corrected title/IMDb link never leaves a stale post behind -- the
    same established mechanism Refresh IMDb Metadata already uses. None
    (every existing test double) skips post-sync entirely, preserving
    the exact previous behavior.
    """
    if wash_crew_role_id is None:
        return (
            "WASH Crew permissions have not been configured. "
            "Set WASH_CREW_ROLE_ID before using this command.",
            True,
        )
    if not is_wash_crew_member(user, wash_crew_role_id):
        return "You need the WASH Crew role to repair suggestions.", True

    post_sync = None
    if bot is not None:

        async def post_sync(watch_item: WatchItem) -> Optional[bool]:
            result = await sync_suggestion_status_embed(bot, watch_item)
            if result is SuggestionPostSyncResult.NO_POST:
                return None
            return result is SuggestionPostSyncResult.SYNCED

    report = await repair_service.repair_all(guild_id, post_sync=post_sync)
    return report.format_message(), True


# --- FR-033A: /list with status filters, richer entries, and pagination -------------------


class SuggestionListStatusFilter(str, Enum):
    """Which suggestions /list should include.

    All filters are resolved through CollectionEligibilityService --
    the same authoritative calculation every other command uses -- so
    none of them can ever disagree with what starting a vote would
    actually see. Every bucket below
    (available, in_active_vote, pending_crew_review, vote_winners,
    retired, watched) is already computed by CollectionEligibility
    itself; this filter only chooses which of those (already-identical)
    buckets to show, never recomputes eligibility.

    ACTIVE (the new default) is Available + In an Active Vote mixed
    together -- CollectionEligibility.active, unchanged. ELIGIBLE is the
    Eligible Pool (what actually feeds the next vote). IN_ACTIVE_VOTE,
    PENDING_CREW_REVIEW, VOTE_WINNER, RETIRED, and WATCHED are unchanged,
    plain-bucket filters. ALL is every watch item in the collection,
    active or not.
    """

    ACTIVE = "active"
    ELIGIBLE = "eligible"
    IN_ACTIVE_VOTE = "in_active_vote"
    PENDING_CREW_REVIEW = "pending_crew_review"
    VOTE_WINNER = "vote_winner"
    RETIRED = "retired"
    WATCHED = "watched"
    ALL = "all"


SUGGESTION_LIST_STATUS_FILTER_LABELS: dict[SuggestionListStatusFilter, str] = {
    SuggestionListStatusFilter.ACTIVE: "Active Watch Items",
    SuggestionListStatusFilter.ELIGIBLE: "Eligible for Voting",
    SuggestionListStatusFilter.IN_ACTIVE_VOTE: "In an Active Vote",
    SuggestionListStatusFilter.PENDING_CREW_REVIEW: "Pending Crew Review",
    SuggestionListStatusFilter.VOTE_WINNER: "Vote Winners",
    SuggestionListStatusFilter.RETIRED: "Retired",
    SuggestionListStatusFilter.WATCHED: "Watched",
    SuggestionListStatusFilter.ALL: "All Watch Items",
}


def resolve_suggestion_list_entries(
    eligibility: "CollectionEligibility",
    status_filter: SuggestionListStatusFilter,
) -> List[Tuple[WatchItem, SuggestionDisplayStatus]]:
    """Pick which of CollectionEligibility's already-computed buckets a
    /list filter shows, paired with each item's display status -- known
    directly from which bucket it came from, since CollectionEligibility
    itself already separates Available from In an Active Vote (no
    separate per-item VoteService lookup needed here).
    """
    if status_filter is SuggestionListStatusFilter.ACTIVE:
        return [(item, SuggestionDisplayStatus.AVAILABLE) for item in eligibility.available] + [
            (item, SuggestionDisplayStatus.IN_ACTIVE_VOTE) for item in eligibility.in_active_vote
        ]
    if status_filter is SuggestionListStatusFilter.ELIGIBLE:
        return [(item, SuggestionDisplayStatus.AVAILABLE) for item in eligibility.available]
    if status_filter is SuggestionListStatusFilter.IN_ACTIVE_VOTE:
        return [(item, SuggestionDisplayStatus.IN_ACTIVE_VOTE) for item in eligibility.in_active_vote]
    if status_filter is SuggestionListStatusFilter.PENDING_CREW_REVIEW:
        return [
            (item, SuggestionDisplayStatus.PENDING_CREW_REVIEW) for item in eligibility.pending_crew_review
        ]
    if status_filter is SuggestionListStatusFilter.VOTE_WINNER:
        return [(item, SuggestionDisplayStatus.VOTE_WINNER) for item in eligibility.vote_winners]
    if status_filter is SuggestionListStatusFilter.RETIRED:
        return [(item, SuggestionDisplayStatus.RETIRED) for item in eligibility.retired]
    if status_filter is SuggestionListStatusFilter.WATCHED:
        return [(item, SuggestionDisplayStatus.WATCHED) for item in eligibility.watched]
    # ALL
    return (
        [(item, SuggestionDisplayStatus.AVAILABLE) for item in eligibility.available]
        + [(item, SuggestionDisplayStatus.IN_ACTIVE_VOTE) for item in eligibility.in_active_vote]
        + [
            (item, SuggestionDisplayStatus.PENDING_CREW_REVIEW)
            for item in eligibility.pending_crew_review
        ]
        + [(item, SuggestionDisplayStatus.VOTE_WINNER) for item in eligibility.vote_winners]
        + [(item, SuggestionDisplayStatus.RETIRED) for item in eligibility.retired]
        + [(item, SuggestionDisplayStatus.WATCHED) for item in eligibility.watched]
    )


def build_suggestion_entry_line(item: WatchItem, display_status: SuggestionDisplayStatus) -> str:
    """Render one /list entry: status emoji, title, year, and (when
    available) a clean link back to the original public suggestion post.

    UI Polish (Watch Item Status Presentation): every entry now leads
    with its status emoji (🟢/🗳️/🏆/🗄️/✅) so Active Watch Items' mixed
    Eligible/In an Active Vote list -- and every other filter -- reads
    clearly at a glance without needing a separate status column.
    Deliberately still excludes the internal reference number and IMDb
    link the old /list rendering also omitted (Release Polish Priority 2)
    -- only a Vote Winner's win date is added, never a reference or IMDb
    link, matching this milestone's own scope.
    """
    heading = format_title_with_year(item.title, item.release_year)
    emoji = SUGGESTION_DISPLAY_STATUS_EMOJI[display_status]
    line = f"{emoji} {heading}"

    if item.guild_id is not None and item.channel_id is not None and item.message_id is not None:
        message_url = f"https://discord.com/channels/{item.guild_id}/{item.channel_id}/{item.message_id}"
        line += f" | [Original Suggestion]({message_url})"

    if display_status is SuggestionDisplayStatus.VOTE_WINNER:
        won_line = vote_winner_won_date_line(item)
        if won_line is not None:
            line += f"\n{won_line}"

    return line


def resolve_configured_candidate_count(bot: "WatchPartyBot", guild_id: Optional[int]) -> int:
    """The guild's configured default voting candidate count, the same
    value /vote start's "Use Defaults" path resolves -- shared so /list
    and /database health's rollover-before-reporting always target the
    same "how many do we actually need" number /vote start does.
    """
    if guild_id is not None:
        configuration = bot.guild_configuration_repository.get(guild_id)
        if configuration is not None:
            return configuration.voting_defaults.candidate_count
    return DEFAULT_VOTE_CANDIDATE_COUNT


def resolve_candidate_selection_mode(
    bot: "WatchPartyBot", guild_id: int, database_id: int
) -> CandidateSelectionMode:
    configuration = bot.suggestion_database_configuration_repository.get(guild_id, database_id)
    if configuration is None:
        return DEFAULT_CANDIDATE_SELECTION_MODE
    return configuration.suggestion_rules.candidate_selection


def resolve_customize_vote_default_candidate_selection(
    bot: "WatchPartyBot", guild_id: Optional[int], channel_id: Optional[int]
) -> CandidateSelectionMode:
    """The value Customize This Vote's candidate-selection dropdown should
    preselect, resolved the same read-only, non-mutating way used
    elsewhere (see resolve_database_for_channel) -- purely to show a
    sensible starting point before the collection itself is finally
    resolved (and, if ambiguous, chosen) later in
    handle_start_vote_completion. Falls back to the recommended default
    (Favor Older Additions) whenever the channel context is unknown,
    ambiguous, or matches no collection -- the dropdown's preselected
    value is only a convenience, never a decision that's actually acted
    on here.
    """
    if guild_id is None or channel_id is None:
        return DEFAULT_CANDIDATE_SELECTION_MODE
    resolution = bot.suggestion_service.resolve_database_for_channel(
        guild_id, channel_id, bot.suggestion_database_configuration_repository
    )
    if resolution.database is None:
        return DEFAULT_CANDIDATE_SELECTION_MODE
    return resolve_candidate_selection_mode(bot, guild_id, resolution.database.database_id)


async def send_suggestion_list(
    interaction: discord.Interaction,
    bot: "WatchPartyBot",
    database: SuggestionDatabase,
    status_filter: SuggestionListStatusFilter,
    public: bool,
    *,
    edit: bool = False,
) -> None:
    """Every filter is resolved through CollectionEligibilityService --
    the same authoritative calculation every other command uses --
    computed entirely from WatchItemStatus and VoteService.
    """
    eligibility = bot.collection_eligibility_service.get_eligibility(database.database_id)

    entries = resolve_suggestion_list_entries(eligibility, status_filter)
    # Deterministic ordering: by stable suggestion ID (assignment order),
    # never re-sorted by anything that could change between pages.
    entries = sorted(entries, key=lambda entry: entry[0].id or 0)

    display_name = format_collection_display(
        _resolve_collection_name(
            bot.suggestion_service, database, interaction.guild, bot.suggestion_database_configuration_repository
        )
    )
    filter_label = SUGGESTION_LIST_STATUS_FILTER_LABELS[status_filter]

    async def on_switch_collection(switch_interaction: discord.Interaction) -> None:
        await show_list_switch_collection_picker(switch_interaction, bot, status_filter, public)

    switch_view_addition = await build_switch_collection_options(bot, database.guild_id) is not None

    if not entries:
        content = f'"{display_name}" has no watch items matching {filter_label}.'
        view = discord.ui.View(timeout=180)
        if switch_view_addition:
            view.add_item(SwitchCollectionButton(on_switch_collection))
        send_view = view if switch_view_addition else None
        if edit:
            await interaction.response.edit_message(content=content, view=send_view)
        else:
            await interaction.response.send_message(
                content, view=send_view, ephemeral=not public, suppress_embeds=True
            )
        return

    header = f"**{display_name} -- {filter_label} ({len(entries)})**"
    if status_filter is SuggestionListStatusFilter.ACTIVE:
        # A short summary before the mixed Eligible/In an Active Vote
        # list, e.g. "🟢 Eligible for Voting: 2\n🗳️ In an Active Vote: 6".
        header += (
            f"\n{SUGGESTION_DISPLAY_STATUS_EMOJI[SuggestionDisplayStatus.AVAILABLE]} "
            f"Eligible for Voting: {len(eligibility.available)}\n"
            f"{SUGGESTION_DISPLAY_STATUS_EMOJI[SuggestionDisplayStatus.IN_ACTIVE_VOTE]} "
            f"In an Active Vote: {len(eligibility.in_active_vote)}"
        )
    lines = [build_suggestion_entry_line(item, display_status) for item, display_status in entries]
    pages = paginate_lines(header, lines)

    requester_id = getattr(interaction.user, "id", None)
    if len(pages) == 1:
        view = discord.ui.View(timeout=180)
        if switch_view_addition:
            view.add_item(SwitchCollectionButton(on_switch_collection))
        send_view = view if switch_view_addition else None
    else:
        view = PaginatedListView(pages, requester_id=requester_id, suppress_embeds=True)
        if switch_view_addition:
            view.add_item(SwitchCollectionButton(on_switch_collection))
        send_view = view

    if edit:
        await interaction.response.edit_message(content=pages[0], view=send_view)
    else:
        await interaction.response.send_message(
            pages[0], view=send_view, ephemeral=not public, suppress_embeds=True
        )


async def build_switch_collection_options(
    bot: "WatchPartyBot", guild_id: int
) -> Optional[List[Tuple[int, str]]]:
    """(database_id, display_name) pairs for every other active
    collection in the guild, or None when fewer than two exist (nothing
    to switch to).
    """
    databases = [database for database in bot.suggestion_service.list_databases(guild_id) if database.active]
    if len(databases) < 2:
        return None
    return [
        (
            database.database_id,
            format_collection_display(
                _resolve_collection_name(
                    bot.suggestion_service, database, None, bot.suggestion_database_configuration_repository
                )
            ),
        )
        for database in databases
    ]


async def show_list_switch_collection_picker(
    interaction: discord.Interaction, bot: "WatchPartyBot", status_filter: SuggestionListStatusFilter, public: bool
) -> None:
    """Show the Switch Collection picker.

    Wrapped end-to-end so an unexpected exception always still leaves the
    interaction acknowledged -- otherwise Discord's client falls back to a
    generic "didn't respond in time" with nothing in the logs pointing at
    why, since discord.ui.View's default on_error only logs to discord.py's
    own logger and never responds to the interaction itself.
    """
    try:
        options = await build_switch_collection_options(bot, interaction.guild_id)
        if not options:
            await interaction.response.send_message("There's only one collection in this server.", ephemeral=True)
            return

        async def on_select(select_interaction: discord.Interaction, database_id: int) -> None:
            database = bot.suggestion_service.get_database(database_id)
            if database is None:
                await select_interaction.response.send_message("That collection no longer exists.", ephemeral=True)
                return
            await send_suggestion_list(select_interaction, bot, database, status_filter, public, edit=True)

        await interaction.response.edit_message(
            content="Choose a collection:", view=ListDatabaseSelectView(options, on_select)
        )
    except Exception:
        logger.warning("Switch Collection picker failed for guild %s", interaction.guild_id, exc_info=True)
        error_message = "Something went wrong showing the collection picker. Please try again."
        if interaction.response.is_done():
            await interaction.followup.send(error_message, ephemeral=True)
        else:
            await interaction.response.send_message(error_message, ephemeral=True)


# --- Collection Health: /database health -----------------------------------------
#
# Goal 5: uses CollectionEligibilityService.peek() exclusively -- never
# resolve() -- so this command can never modify eligibility state,
# matching its own hard requirement. Collection selection reuses the
# exact same thread-context-default-plus-Switch-Collection pattern
# /list uses (see resolve_database_then/build_switch_collection_options
# above), rather than a second, parallel implementation of "which
# collection?".


class NextVoteStatus(str, Enum):
    """Whether this collection currently has enough active suggestions to
    start a vote.
    """

    READY = "Ready"
    INSUFFICIENT = "Insufficient Suggestions"


class LowPoolStatus(str, Enum):
    HEALTHY = "Healthy"
    ALMOST_COMPLETE = "Almost Complete"
    INSUFFICIENT = "Insufficient"


# /database health Visual Consistency: reuses the same traffic-light
# idiom SUGGESTION_DISPLAY_STATUS_EMOJI already established for /list
# (green for Available) rather than inventing a new visual system -- red
# extends that same idiom for the one case /list never had: a status
# that's an actual blocking problem.
NEXT_VOTE_STATUS_EMOJI: dict[NextVoteStatus, str] = {
    NextVoteStatus.READY: "🟢",
    NextVoteStatus.INSUFFICIENT: "🔴",
}

LOW_POOL_STATUS_EMOJI: dict[LowPoolStatus, str] = {
    LowPoolStatus.HEALTHY: "🟢",
    LowPoolStatus.ALMOST_COMPLETE: "🟡",
    LowPoolStatus.INSUFFICIENT: "🔴",
}


def build_collection_health_report(bot: "WatchPartyBot", database: SuggestionDatabase, guild: Optional[discord.Guild]) -> str:
    """Build /database health's report text for one collection.

    The numbers are guaranteed to reconcile by construction, not by
    coincidence: Active is literally len(available) + len(in_active_vote)
    (CollectionEligibility.active), and Total is literally Active +
    Vote Winners + Retired + Watched + Pending Crew Review
    (CollectionEligibility.total) -- both computed once, inside
    CollectionEligibilityService itself, never recomputed separately here.
    """
    eligibility = bot.collection_eligibility_service.get_eligibility(database.database_id)

    guild_configuration = bot.guild_configuration_repository.get(database.guild_id)
    database_configuration = bot.suggestion_database_configuration_repository.get(
        database.guild_id, database.database_id
    )
    candidate_count = resolve_configured_candidate_count(bot, database.guild_id)
    eligible_count = eligibility.eligible_pool_count
    active_count = len(eligibility.active)
    threshold = resolve_eligible_pool_warning_threshold(guild_configuration, database_configuration, candidate_count)

    next_vote = NextVoteStatus.READY if active_count >= candidate_count else NextVoteStatus.INSUFFICIENT

    if eligible_count < candidate_count:
        low_pool = LowPoolStatus.INSUFFICIENT
    elif eligible_count <= threshold:
        low_pool = LowPoolStatus.ALMOST_COMPLETE
    else:
        low_pool = LowPoolStatus.HEALTHY

    display_name = format_collection_display(
        _resolve_collection_name(
            bot.suggestion_service, database, guild, bot.suggestion_database_configuration_repository
        )
    )

    # Formatting Polish: Eligible for Voting and In an Active Vote are
    # indented under Active Watch Items so the reconciliation identity
    # (Active = Eligible for Voting + In an Active Vote) reads directly
    # from the layout, not just the parenthetical -- purely presentational,
    # the underlying numbers are unchanged.
    lines = [
        f"**Collection Health -- {display_name}**",
        "",
        f"Total Watch Items: {eligibility.total}",
        "",
        f"Active Watch Items: {active_count} (Eligible for Voting + In an Active Vote)",
        f"    {SUGGESTION_DISPLAY_STATUS_EMOJI[SuggestionDisplayStatus.AVAILABLE]} Eligible for Voting: {eligible_count}",
        f"    {SUGGESTION_DISPLAY_STATUS_EMOJI[SuggestionDisplayStatus.IN_ACTIVE_VOTE]} In an Active Vote: {len(eligibility.in_active_vote)}",
        f"{SUGGESTION_DISPLAY_STATUS_EMOJI[SuggestionDisplayStatus.PENDING_CREW_REVIEW]} Pending Crew Review: {len(eligibility.pending_crew_review)}",
        f"{SUGGESTION_DISPLAY_STATUS_EMOJI[SuggestionDisplayStatus.VOTE_WINNER]} Vote Winners: {len(eligibility.vote_winners)}",
        f"{SUGGESTION_DISPLAY_STATUS_EMOJI[SuggestionDisplayStatus.RETIRED]} Retired: {len(eligibility.retired)}",
        f"{SUGGESTION_DISPLAY_STATUS_EMOJI[SuggestionDisplayStatus.WATCHED]} Watched: {len(eligibility.watched)}",
        "",
    ]
    lines.append(f"Configured Candidate Count: {candidate_count}")
    lines.append("")
    lines.append(f"Next Vote: {NEXT_VOTE_STATUS_EMOJI[next_vote]} {next_vote.value}")
    lines.append(f"Low Pool Status: {LOW_POOL_STATUS_EMOJI[low_pool]} {low_pool.value}")
    return "\n".join(lines)


async def send_collection_health(interaction: discord.Interaction, bot: "WatchPartyBot", database: SuggestionDatabase) -> None:
    report = build_collection_health_report(bot, database, interaction.guild)

    async def on_switch_collection(switch_interaction: discord.Interaction) -> None:
        await show_health_switch_collection_picker(switch_interaction, bot)

    options = await build_switch_collection_options(bot, database.guild_id)
    view: Optional[discord.ui.View] = None
    if options:
        view = discord.ui.View(timeout=180)
        view.add_item(SwitchCollectionButton(on_switch_collection))

    await interaction.response.send_message(report, view=view, ephemeral=True)


async def show_health_switch_collection_picker(interaction: discord.Interaction, bot: "WatchPartyBot") -> None:
    options = await build_switch_collection_options(bot, interaction.guild_id)
    if not options:
        await interaction.response.send_message("There's only one collection in this server.", ephemeral=True)
        return

    async def on_select(select_interaction: discord.Interaction, database_id: int) -> None:
        database = bot.suggestion_service.get_database(database_id)
        if database is None:
            await select_interaction.response.send_message("That collection no longer exists.", ephemeral=True)
            return
        report = build_collection_health_report(bot, database, select_interaction.guild)

        async def on_switch_again(switch_interaction: discord.Interaction) -> None:
            await show_health_switch_collection_picker(switch_interaction, bot)

        other_options = await build_switch_collection_options(bot, database.guild_id)
        view: Optional[discord.ui.View] = None
        if other_options:
            view = discord.ui.View(timeout=180)
            view.add_item(SwitchCollectionButton(on_switch_again))
        await select_interaction.response.edit_message(content=report, view=view)

    await interaction.response.edit_message(
        content="Choose a collection:", view=ListDatabaseSelectView(options, on_select)
    )


async def handle_database_health(interaction: discord.Interaction, bot: "WatchPartyBot") -> None:
    """/database health: WASH Crew-only, matching the rest of /database."""
    permission = resolve_permission_service(
        interaction.guild_id, bot.guild_configuration_repository
    ).require_wash_crew(interaction.user)
    if not permission.allowed:
        await interaction.response.send_message(permission.message, ephemeral=True)
        return

    guild_id = interaction.guild_id
    if guild_id is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    async def on_resolved(target_interaction: discord.Interaction, database: SuggestionDatabase) -> None:
        await send_collection_health(target_interaction, bot, database)

    await resolve_database_then(interaction, bot, guild_id, interaction.channel_id, on_resolved)


# --- /random_watch -----------------------------------------------------------------------------
#
# A discovery command only: it never creates a voting round, changes a
# suggestion's status, marks anything watched, or touches a collection's
# own configuration. Everything here orchestrates existing services --
# resolve_database_then/ListDatabaseSelectView for collection selection
# (the same pattern /list and /database health use),
# CollectionEligibilityService for eligibility (the same authoritative
# source voting and /list use), services/nominee_pool_filter.py's
# MemberSuggestionFilter/GenreFilter for optional filtering (the same
# architecture Custom Vote Filters uses), and
# services/random_watch_service.choose_random_watch_item for the one
# genuinely new piece: an unweighted, uniform draw from the resolved
# pool. No existing eligibility or filtering logic is duplicated.


def build_random_watch_filters_intro(collection_display_name: str) -> str:
    """The fixed header/instructions shown above the filter menu and
    every one of its filter-editing sub-screens -- never includes the
    Current Filters block itself (show_menu appends that separately, but
    a filter's own edit screen intentionally omits it to save space).
    """
    return (
        f'**Random Watch -- "{collection_display_name}"**\n\n'
        "Optionally narrow the random pool using the filter menu, then press **Pick Random Item**. "
        "These filters apply only to this session -- they never change the collection's or guild's own "
        "configuration, and never affect a future `/random watch` session."
    )


def build_random_watch_result_header(collection_display_name: str, filter_state: dict) -> str:
    """The plain-text line shown above the result embed -- names the
    collection and any active filter(s), in the shared Genre/IMDb
    Rating/MPAA Rating/Actor/Member order, and makes clear this was a
    random pick. An inactive filter (Any ...) is never shown.
    """
    lines = [f'🎲 Random pick from "{collection_display_name}"']
    if filter_state.get("genre"):
        lines.append(f"Genre: {filter_state['genre']}")
    if filter_state.get("imdb_rating_min") is not None or filter_state.get("imdb_rating_max") is not None:
        lines.append(f"IMDb Rating: {describe_imdb_rating_filter_state(filter_state)}")
    if filter_state.get("mpaa_rating"):
        lines.append(f"MPAA Rating: {filter_state['mpaa_rating']}")
    if filter_state.get("actor"):
        lines.append(f"Actor: {filter_state['actor']}")
    if filter_state.get("member_display"):
        lines.append(f"Suggestion Source: {filter_state['member_display']}")
    return "\n".join(lines)


def build_random_watch_empty_pool_message(collection_display_name: str, filter_state: dict) -> str:
    """The "nothing to pick from" message for an empty (possibly
    filtered) pool -- always names the collection and, when any filters
    are active, which ones are responsible, and always offers a way
    forward (Filtered-Pool Feedback). Built from however many of the
    five filters happen to be active, rather than a fixed set of
    combinations, so this scales the same way build_current_filters_summary
    does as filters are added.
    """
    active_descriptions: List[str] = []
    if filter_state.get("genre"):
        active_descriptions.append(f"the {filter_state['genre']} genre")
    if filter_state.get("imdb_rating_min") is not None or filter_state.get("imdb_rating_max") is not None:
        active_descriptions.append(f"an IMDb Rating of {describe_imdb_rating_filter_state(filter_state)}")
    if filter_state.get("mpaa_rating"):
        active_descriptions.append(f"an MPAA Rating of {filter_state['mpaa_rating']}")
    if filter_state.get("actor"):
        active_descriptions.append(f"{filter_state['actor']} in the cast")
    if filter_state.get("member_display"):
        active_descriptions.append(f"{filter_state['member_display']}'s suggestions")

    if not active_descriptions:
        return (
            f'"{collection_display_name}" has no eligible watch items to pick from right now. '
            "Add more suggestions, or choose another collection."
        )
    if len(active_descriptions) == 1:
        joined = active_descriptions[0]
    else:
        joined = ", ".join(active_descriptions[:-1]) + f", and {active_descriptions[-1]}"
    return (
        f'No eligible watch items in "{collection_display_name}" match {joined}. '
        "Change or clear a filter, or choose another collection."
    )


def build_random_watch_public_handoff_message(item_title: str) -> str:
    """Close out the private setup/filter screen once a public result is
    on its way -- shown in place of the filter controls so nothing stale
    or reusable is left behind on the ephemeral message (Section 4: the
    setup UI stays private, only the result itself is posted publicly).
    """
    return f'🎲 Picked "{item_title}" -- see the public post below.'


def build_random_watch_result_embed(watch_item: WatchItem, *, database_name: str, suggested_by: str) -> discord.Embed:
    """Build /random_watch's result embed.

    Reuses the same visual language as build_suggestion_confirmation_embed
    (color, Details field, thumbnail) but is its own function, not a call
    to it: it additionally shows Release Year (not part of the
    suggestion-confirmation embed) and deliberately does NOT duplicate
    the IMDb link as a separate description line -- the title itself is
    already the link (via the embed's own url=), matching "do not
    duplicate raw IMDb URLs when the title is already linked".
    """
    imdb_url = watch_item.metadata_ids.get(MetadataProvider.IMDB)
    embed = discord.Embed(
        title=watch_item.title,
        description=watch_item.description or None,
        url=imdb_url,
        color=0xF5C518,
    )
    if watch_item.release_year:
        embed.add_field(name="Year", value=str(watch_item.release_year), inline=True)
    details: list[str] = []
    if watch_item.genres:
        details.append(" • ".join(watch_item.genres))
    if watch_item.runtime_minutes:
        details.append(f"{watch_item.runtime_minutes} min")
    if watch_item.content_rating:
        details.append(f"Rated {watch_item.content_rating}")
    if details:
        embed.add_field(name="Details", value=" • ".join(details), inline=False)
    if watch_item.director:
        embed.add_field(name="Director", value=watch_item.director, inline=True)
    if watch_item.imdb_rating:
        embed.add_field(name="IMDb Rating", value=f"{watch_item.imdb_rating}/10", inline=True)
    embed.add_field(name="Suggested By", value=suggested_by, inline=True)
    embed.add_field(name="Collection", value=format_collection_display(database_name), inline=True)
    embed.add_field(name="Reference", value=watch_item.reference, inline=True)
    if watch_item.poster_url:
        embed.set_thumbnail(url=watch_item.poster_url)
    embed.set_footer(text="🎲 Picked at random with /random watch")
    return embed


async def handle_random_watch(interaction: discord.Interaction, bot: "WatchPartyBot") -> None:
    """/random_watch: a discovery tool only -- never creates a voting
    round, changes a suggestion's status, marks anything watched, or
    changes any collection/nominee-selection setting.

    Access (Section 1): any Watch Party member, the same rule /list and
    /add already use -- not WASH Crew-only, since nothing here is an
    administrative action.
    """
    permission = resolve_permission_service(
        interaction.guild_id, bot.guild_configuration_repository
    ).require_watch_party_member(interaction.user)
    if not permission.allowed:
        await interaction.response.send_message(permission.message, ephemeral=True)
        return

    guild_id = interaction.guild_id
    if guild_id is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    async def on_resolved(target_interaction: discord.Interaction, database: SuggestionDatabase) -> None:
        await send_random_watch_session(target_interaction, bot, database, edit=False)

    await resolve_database_then(interaction, bot, guild_id, interaction.channel_id, on_resolved)


async def show_random_watch_collection_picker(
    interaction: discord.Interaction, bot: "WatchPartyBot", guild_id: int, *, edit: bool
) -> None:
    """/random_watch's "choose a collection" screen: shown for the
    initial ambiguous-collection case (via resolve_database_then) and
    reused unchanged as the Change Collection target from within an
    active session. Unlike Switch Collection elsewhere, this always
    lists every active collection (never excludes the current one) --
    Change Collection is an explicit request to choose again, including
    re-choosing the same collection.
    """
    databases = [database for database in bot.suggestion_service.list_databases(guild_id) if database.active]
    if not databases:
        content = "WASH Crew must create a collection first -- run `/database add` or `/setup`."
        if edit:
            await interaction.response.edit_message(content=content, view=None)
        else:
            await interaction.response.send_message(content, ephemeral=True)
        return

    if len(databases) == 1:
        await send_random_watch_session(interaction, bot, databases[0], edit=edit)
        return

    options = [
        (
            database.database_id,
            format_collection_display(
                _resolve_collection_name(
                    bot.suggestion_service, database, interaction.guild, bot.suggestion_database_configuration_repository
                )
            ),
        )
        for database in databases
    ]

    async def on_select(select_interaction: discord.Interaction, database_id: int) -> None:
        database = bot.suggestion_service.get_database(database_id)
        if database is None:
            await select_interaction.response.send_message("That collection no longer exists.", ephemeral=True)
            return
        await send_random_watch_session(select_interaction, bot, database, edit=True)

    view = ListDatabaseSelectView(options, on_select)
    content = "Choose a collection:"
    if edit:
        await interaction.response.edit_message(content=content, view=view)
    else:
        await interaction.response.send_message(content, view=view, ephemeral=True)


async def send_random_watch_session(
    interaction: discord.Interaction, bot: "WatchPartyBot", database: SuggestionDatabase, *, edit: bool
) -> None:
    """Show /random watch's initial Pick Random Item / Add Filters screen
    for one resolved collection, starting a fresh filter session (no
    filter carried over from a previous collection -- see
    on_change_collection below). Also the render target Change Collection
    calls back into once a (possibly different) collection is chosen.

    Add Filters opens the shared FilterMenuView session (see
    create_filter_menu_session) -- Genre, IMDb Rating, MPAA Rating,
    Actor, and Member all narrow the same eligible_pool before a plain
    Pick Random Item click draws from whatever's left, entirely bypassing
    NomineeSelectionService's weighted CandidateSelectionStrategy pipeline
    (see services/random_watch_service.choose_random_watch_item).
    """
    guild_id = database.guild_id
    collection_display_name = format_collection_display(
        _resolve_collection_name(
            bot.suggestion_service, database, interaction.guild, bot.suggestion_database_configuration_repository
        )
    )
    eligibility = bot.collection_eligibility_service.get_eligibility(database.database_id)
    eligible_pool: List[WatchItem] = list(eligibility.available)

    async def perform_pick(pick_interaction: discord.Interaction, *, from_public: bool) -> None:
        """Draw a random item and show the result.

        from_public=False (Pick Random Item, from the private setup/
        filter screens): a found item is announced as a brand-new PUBLIC
        message -- /random watch's setup stays private, but the actual
        result is a public event for the whole channel (Section 4). The
        private message that triggered this is closed out with a short
        hand-off note rather than left showing stale controls.

        from_public=True (Pick Again, from the already-public result):
        a found item rerolls in place on that same public message --
        it's already public, so there is no new message to create. An
        empty pool always answers privately (ephemeral) to the clicking
        member only, since there is nothing new to announce publicly.
        """
        if not from_public and filter_state["member_filter_invalid"]:
            # Defense in depth: the filtered Pick Random Item button is
            # already disabled while a selection is invalid, but this
            # guard is what actually guarantees an invalid member can
            # never produce a result -- not merely Discord's client-side
            # disabled-button behavior.
            await show_menu(pick_interaction, status_line=filter_state["member_filter_status_line"])
            return

        filtered_pool = apply_nominee_pool_filters(eligible_pool, resolve_active_filters_from_state(filter_state))
        item = choose_random_watch_item(filtered_pool)
        if item is None:
            message = build_random_watch_empty_pool_message(collection_display_name, filter_state)
            if from_public:
                await pick_interaction.response.send_message(content=message, ephemeral=True)
            else:
                await show_menu(pick_interaction, status_line=message)
            return

        suggested_by = f"<@{item.journey.original_suggester}>" if item.journey.original_suggester else "Unknown"
        embed = build_random_watch_result_embed(item, database_name=database.name, suggested_by=suggested_by)
        original_url = build_suggestion_message_link(item)
        content = build_random_watch_result_header(collection_display_name, filter_state)
        view = RandomWatchResultView(
            do_pick_again,
            on_change_filters_from_public,
            on_change_collection_from_public,
            original_suggestion_url=original_url,
            requester_id=pick_interaction.user.id,
        )
        if from_public:
            await pick_interaction.response.edit_message(content=content, embed=embed, view=view)
        else:
            await pick_interaction.response.edit_message(
                content=build_random_watch_public_handoff_message(item.title), embed=None, view=None
            )
            await pick_interaction.followup.send(content=content, embed=embed, view=view)

    async def do_pick_unfiltered(pick_interaction: discord.Interaction) -> None:
        await perform_pick(pick_interaction, from_public=False)

    async def do_pick_again(pick_interaction: discord.Interaction) -> None:
        await perform_pick(pick_interaction, from_public=True)

    async def on_menu_primary_action(pick_interaction: discord.Interaction, _filter_state: dict) -> None:
        await perform_pick(pick_interaction, from_public=False)

    async def on_change_collection(change_interaction: discord.Interaction) -> None:
        await show_random_watch_collection_picker(change_interaction, bot, guild_id, edit=True)

    async def on_change_collection_from_public(change_interaction: discord.Interaction) -> None:
        await show_random_watch_collection_picker(change_interaction, bot, guild_id, edit=False)

    filter_state, show_menu = create_filter_menu_session(
        bot=bot,
        eligible_pool=eligible_pool,
        collection_display_name=collection_display_name,
        body_header=build_random_watch_filters_intro(collection_display_name),
        on_primary_action=on_menu_primary_action,
        primary_action_label="Pick Random Item",
        primary_action_custom_id="wpm_random_watch_pick_filtered",
        on_secondary_action=on_change_collection,
        secondary_action_label="Change Collection",
    )

    async def on_change_filters_from_public(change_filters_interaction: discord.Interaction) -> None:
        # Reached from the public result's Change Filters -- the filter
        # UI must stay private, and a public message can never be edited
        # into an ephemeral one, so this opens a brand-new ephemeral
        # message instead of touching the public result.
        await show_menu(change_filters_interaction, edit=False)

    async def on_add_filters(filters_interaction: discord.Interaction) -> None:
        await show_menu(filters_interaction)

    body = f'**Random Watch -- "{collection_display_name}"**\n\nPick a random eligible watch item, or add filters first.'
    view = RandomWatchInitialView(do_pick_unfiltered, on_add_filters)
    if edit:
        await interaction.response.edit_message(content=body, view=view)
    else:
        await interaction.response.send_message(body, view=view, ephemeral=True)


# --- /browse: WASH's interactive collection browser -------------------------------
#
# Another consumer of the shared filter engine (filter_menu_view.py,
# services/nominee_pool_filter.py) -- alongside /random watch and Custom
# Vote, never a second filtering implementation. Random Pick reuses
# choose_random_watch_item()/build_random_watch_result_embed()/
# RandomWatchResultView (the exact /random watch presentation); Start
# Vote reuses show_customize_vote_overrides() (Custom Vote's own
# "Customize This Vote" screen, extracted for this exact purpose -- see
# its docstring). Only Browse's own results formatting
# (services/browse_service.build_browse_entry_line) and collection-
# change filter revalidation (revalidate_browse_filter_state) are new.


async def handle_browse(interaction: discord.Interaction, bot: "WatchPartyBot") -> None:
    """/browse: explore a collection's eligible pool -- never starts a
    vote automatically, changes a suggestion's status, modifies a
    collection, or edits metadata (Section 1). Available to every Watch
    Party member (Section 2); WASH Crew additionally see Random Pick,
    Start Vote, and Post Publicly on the results screen.
    """
    permission = resolve_permission_service(
        interaction.guild_id, bot.guild_configuration_repository
    ).require_watch_party_member(interaction.user)
    if not permission.allowed:
        await interaction.response.send_message(permission.message, ephemeral=True)
        return

    guild_id = interaction.guild_id
    if guild_id is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    async def on_resolved(target_interaction: discord.Interaction, database: SuggestionDatabase) -> None:
        await send_browse_session(target_interaction, bot, database, edit=False)

    await resolve_database_then(interaction, bot, guild_id, interaction.channel_id, on_resolved)


async def show_browse_collection_picker(
    interaction: discord.Interaction,
    bot: "WatchPartyBot",
    guild_id: int,
    *,
    edit: bool,
    initial_filter_state: Optional[dict] = None,
) -> None:
    """/browse's "choose a collection" screen (Section 4) -- mirrors
    /random watch's own show_random_watch_collection_picker exactly:
    always lists every active collection (never excludes the current
    one), since Change Collection is an explicit request to choose
    again, including re-choosing the same collection.

    initial_filter_state (Section 6) carries the previous collection's
    filters through to whichever collection is ultimately chosen --
    send_browse_session revalidates them against that collection's own
    eligible pool, clearing only whichever no longer apply.
    """
    databases = [database for database in bot.suggestion_service.list_databases(guild_id) if database.active]
    if not databases:
        content = "WASH Crew must create a collection first -- run `/database add` or `/setup`."
        if edit:
            await interaction.response.edit_message(content=content, view=None)
        else:
            await interaction.response.send_message(content, ephemeral=True)
        return

    if len(databases) == 1:
        await send_browse_session(interaction, bot, databases[0], edit=edit, initial_filter_state=initial_filter_state)
        return

    options = [
        (
            database.database_id,
            format_collection_display(
                _resolve_collection_name(
                    bot.suggestion_service, database, interaction.guild, bot.suggestion_database_configuration_repository
                )
            ),
        )
        for database in databases
    ]

    async def on_select(select_interaction: discord.Interaction, database_id: int) -> None:
        database = bot.suggestion_service.get_database(database_id)
        if database is None:
            await select_interaction.response.send_message("That collection no longer exists.", ephemeral=True)
            return
        await send_browse_session(
            select_interaction, bot, database, edit=True, initial_filter_state=initial_filter_state
        )

    view = ListDatabaseSelectView(options, on_select)
    content = "Choose a collection:"
    if edit:
        await interaction.response.edit_message(content=content, view=view)
    else:
        await interaction.response.send_message(content, view=view, ephemeral=True)


def build_browse_header(*, collection_display_name: str, filter_state: dict, match_count: int) -> str:
    """Section 5/7/10's shared header: collection, match count, and the
    exact shared Current Filters summary (never a second presentation of
    it) -- reused verbatim for both the ephemeral results screen and a
    Post Publicly'd page, so the two can never show conflicting text for
    the same underlying state.
    """
    return "\n\n".join(
        [
            f'**Browse -- "{collection_display_name}"**',
            f"Matches: {match_count}",
            build_current_filters_summary(filter_state),
        ]
    )


async def perform_browse_random_pick(
    interaction: discord.Interaction,
    bot: "WatchPartyBot",
    database: SuggestionDatabase,
    collection_display_name: str,
    filtered_pool: List[WatchItem],
    filter_state: dict,
) -> None:
    """Section 8: Crew-only Random Pick -- reuses choose_random_watch_item
    and /random watch's own result presentation exactly (same embed
    builder, same result view), drawing from Browse's CURRENT filtered
    results (`filtered_pool`, computed once by send_browse_session's own
    render_results) -- never the whole collection, never an
    independently rebuilt pool, and never disturbing the ephemeral
    browse screen underneath it (deferred, not edited -- Section 10's
    "do not convert the ephemeral interaction" spirit applies here too,
    since the browse session must remain fully usable afterward).
    """
    item = choose_random_watch_item(filtered_pool)
    await interaction.response.defer()
    if item is None:
        await interaction.followup.send("No suggestions match the current filters.", ephemeral=True)
        return

    suggested_by = f"<@{item.journey.original_suggester}>" if item.journey.original_suggester else "Unknown"
    embed = build_random_watch_result_embed(item, database_name=database.name, suggested_by=suggested_by)
    original_url = build_suggestion_message_link(item)
    content = f'🎲 **Random Pick -- "{collection_display_name}"**\n\n{build_current_filters_summary(filter_state)}'

    async def on_pick_again(pick_interaction: discord.Interaction) -> None:
        await perform_browse_random_pick(
            pick_interaction, bot, database, collection_display_name, filtered_pool, filter_state
        )

    async def on_change_filters(change_interaction: discord.Interaction) -> None:
        await send_browse_session(change_interaction, bot, database, edit=False)

    async def on_change_collection(change_interaction: discord.Interaction) -> None:
        await show_browse_collection_picker(change_interaction, bot, database.guild_id, edit=False)

    view = RandomWatchResultView(
        on_pick_again,
        on_change_filters,
        on_change_collection,
        original_suggestion_url=original_url,
        requester_id=interaction.user.id,
    )
    await interaction.followup.send(content=content, embed=embed, view=view)


async def perform_browse_post_publicly(
    interaction: discord.Interaction, pages: List[str], requester_id: int, current_index: int
) -> None:
    """Section 10/11: post the CURRENT browse page publicly, starting on
    whichever page the Crew member had paginated to, using a dedicated
    public view (BrowsePublicResultsView) -- never the ephemeral
    session's own view/message. `pages` already carries Collection,
    Active Filters, and Match Count in its shared header (see
    build_browse_header), and each page's own suggestions -- nothing
    here reformats any of that a second time. Deferred rather than
    edited, so the ephemeral browse screen underneath is completely
    untouched -- "do not convert the ephemeral interaction into a public
    one."
    """
    await interaction.response.defer()
    if len(pages) == 1:
        await interaction.followup.send(content=pages[0], ephemeral=False)
        return
    # Starts on the same page the Crew member was already viewing
    # ephemeral-side (start_index), not always page 1.
    view = BrowsePublicResultsView(pages, requester_id=requester_id, suppress_embeds=True, start_index=current_index)
    await interaction.followup.send(content=view.current_page, view=view, ephemeral=False)


async def send_browse_session(
    interaction: discord.Interaction,
    bot: "WatchPartyBot",
    database: SuggestionDatabase,
    *,
    edit: bool,
    initial_filter_state: Optional[dict] = None,
) -> None:
    """/browse's main results screen for one resolved collection
    (Sections 5/6/7). Also the render target Change Collection calls
    back into once a (possibly different) collection is chosen --
    initial_filter_state carries over the previous collection's filters,
    revalidated against the new collection's own eligible pool (Section
    4/12: "clears only filters that become invalid").

    The eligible pool is CollectionEligibilityService's own `available`
    bucket -- the exact same pool /random watch and Custom Vote's
    Customize flow already browse/filter/vote from, so Random Pick and
    Start Vote launched from here are guaranteed to operate on
    suggestions that could actually be nominated or watched next, not a
    separately-defined "browsable" set.
    """
    guild_id = database.guild_id
    is_crew = resolve_permission_service(guild_id, bot.guild_configuration_repository).is_wash_crew(interaction.user)
    collection_display_name = format_collection_display(
        _resolve_collection_name(
            bot.suggestion_service, database, interaction.guild, bot.suggestion_database_configuration_repository
        )
    )
    eligibility = bot.collection_eligibility_service.get_eligibility(database.database_id)
    eligible_pool: List[WatchItem] = list(eligibility.available)

    async def on_change_collection(change_interaction: discord.Interaction) -> None:
        # Section 6: carries the current filters through so the newly
        # chosen collection can revalidate and preserve whichever still
        # apply, rather than always starting blank.
        await show_browse_collection_picker(
            change_interaction, bot, guild_id, edit=True, initial_filter_state=dict(filter_state)
        )

    async def render_results(target_interaction: discord.Interaction, *, edit_message: bool) -> None:
        # Section 14: filtered once per state change (a filter edit or a
        # collection change) -- Previous/Next never re-enters this
        # function at all (PaginatedListView pages entirely on its own),
        # so paginating never re-filters or re-fetches anything.
        filtered_pool = apply_nominee_pool_filters(eligible_pool, resolve_active_filters_from_state(filter_state))
        header = build_browse_header(
            collection_display_name=collection_display_name, filter_state=filter_state, match_count=len(filtered_pool)
        )

        async def on_change_filters(filters_interaction: discord.Interaction) -> None:
            await show_filter_menu(filters_interaction)

        async def on_random_pick(pick_interaction: discord.Interaction) -> None:
            await perform_browse_random_pick(
                pick_interaction, bot, database, collection_display_name, filtered_pool, filter_state
            )

        async def on_start_vote(vote_interaction: discord.Interaction) -> None:
            await show_customize_vote_overrides(
                vote_interaction, bot, database.database_id, initial_filter_state=dict(filter_state)
            )

        async def on_post_publicly(post_interaction: discord.Interaction) -> None:
            requester_id = getattr(post_interaction.user, "id", None)
            # Reads the live view's current page at click time (not a
            # value frozen when this screen was first rendered), so
            # Post Publicly always starts on whichever page the Crew
            # member has actually paginated to via Previous/Next.
            current_index = getattr(view, "current_index", 0)
            await perform_browse_post_publicly(post_interaction, pages, requester_id, current_index)

        if not filtered_pool:
            # Section 7: View Results never opens the paginated results
            # screen for zero matches -- a plain, recoverable message
            # instead, offering Change Filters, Change Collection, and
            # Back (Back and Change Filters both return to the same
            # filter menu; Back is the generic "undo this" affordance,
            # Change Filters the more specific call to action -- neither
            # traps the Crew/member here).
            content = f"{header}\n\nNo suggestions match the current filters."
            view = discord.ui.View(timeout=180)
            view.add_item(BrowseChangeFiltersButton(on_change_filters))
            view.add_item(BrowseChangeCollectionButton(on_change_collection))
            view.add_item(BrowseBackButton(on_change_filters))
            if edit_message:
                await target_interaction.response.edit_message(content=content, view=view, suppress_embeds=True)
            else:
                await target_interaction.response.send_message(
                    content, view=view, ephemeral=True, suppress_embeds=True
                )
            return

        lines = [build_browse_entry_line(item, SuggestionDisplayStatus.AVAILABLE) for item in filtered_pool]
        pages = paginate_lines(header, lines)
        requester_id_for_view = getattr(target_interaction.user, "id", None)
        if len(pages) == 1:
            view = discord.ui.View(timeout=180)
        else:
            view = PaginatedListView(pages, requester_id=requester_id_for_view, suppress_embeds=True)
        view.add_item(BrowseChangeFiltersButton(on_change_filters))
        view.add_item(BrowseChangeCollectionButton(on_change_collection))
        if is_crew:
            view.add_item(RandomPickButton(on_random_pick))
            view.add_item(StartVoteButton(on_start_vote))
            view.add_item(PostPubliclyButton(on_post_publicly))

        if edit_message:
            await target_interaction.response.edit_message(content=pages[0], view=view, suppress_embeds=True)
        else:
            await target_interaction.response.send_message(
                pages[0], view=view, ephemeral=True, suppress_embeds=True
            )

    async def on_view_results(menu_interaction: discord.Interaction, _filter_state: dict) -> None:
        # Section 3/6: View Results is the only thing that ever builds
        # the filtered pool/pages -- always lands on page 1, since
        # render_results() rebuilds pages/view from scratch every time
        # it's called.
        await render_results(menu_interaction, edit_message=True)

    filter_state, show_filter_menu = create_filter_menu_session(
        bot=bot,
        eligible_pool=eligible_pool,
        collection_display_name=collection_display_name,
        body_header=(
            f'**Browse -- "{collection_display_name}"**\n\nChoose filters, then View Results.'
        ),
        on_primary_action=on_view_results,
        primary_action_label="View Results",
        primary_action_custom_id="wpm_browse_filter_menu_view_results",
    )
    if initial_filter_state is not None:
        filter_state.update(revalidate_browse_filter_state(initial_filter_state, eligible_pool))

    # Section 1/9: Browse now always starts at the shared filter menu --
    # results (and therefore the filtered pool/pages) are never built
    # until View Results is explicitly clicked. This also means a single-
    # collection guild lands directly on the filter menu (Section 1),
    # and Change Collection's own target (this same function) never
    # jumps straight back to results either (Section 6).
    await show_filter_menu(interaction, edit=edit)


async def handle_list_suggestions(
    interaction: discord.Interaction, bot: "WatchPartyBot", status: str, public: bool
) -> None:
    """Handle /list: resolve a database (automatically, or via picker),
    then show it filtered by status with pagination.

    Preserves existing permission behavior for the pieces that already
    had it (public posting requires WASH Crew) while extending general
    access to every Watch Party member per FR-033A Section 9.
    """
    permission = resolve_permission_service(
        interaction.guild_id, bot.guild_configuration_repository
    ).require_watch_party_member(interaction.user)
    if not permission.allowed:
        await interaction.response.send_message(permission.message, ephemeral=True)
        return

    guild_id = interaction.guild_id
    channel_id = interaction.channel_id
    if guild_id is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    is_crew = resolve_permission_service(guild_id, bot.guild_configuration_repository).is_wash_crew(interaction.user)
    if public and not is_crew:
        await interaction.response.send_message(
            "You need the WASH Crew role to post the suggestion list publicly.", ephemeral=True
        )
        return

    try:
        status_filter = SuggestionListStatusFilter(status)
    except ValueError:
        await interaction.response.send_message(
            "Choose Active Watch Items, Eligible for Voting, In an Active Vote, Vote Winners, Retired, "
            "Watched, or All Watch Items.",
            ephemeral=True,
        )
        return

    async def on_resolved(target_interaction: discord.Interaction, database: SuggestionDatabase) -> None:
        await send_suggestion_list(target_interaction, bot, database, status_filter, public)

    await resolve_database_then(interaction, bot, guild_id, channel_id, on_resolved)


def resolve_rejection_threshold(
    suggestion_database_configuration_repository: Optional[SuggestionDatabaseConfigurationRepository],
    guild_id: Optional[int],
    database_id: Optional[int],
) -> int:
    """Look up the configured rejection threshold for a suggestion database.

    Mirrors the resolve_*_settings pattern already established for guild
    configuration (see scheduler/vote_scheduling.py's
    resolve_vote_reminder_settings): falls back to
    SuggestionRulesConfig's own documented default (2) when no
    repository, guild_id, or database_id is available, or no
    configuration has been saved for this database yet -- there is
    currently no way for WASH Crew to configure this (no /setup or
    /config command exists yet), so an unconfigured database is the
    common case today, not an error condition.

    Args:
        suggestion_database_configuration_repository: Where to look up
            the database's configuration, or None to always use the default.
        guild_id: The Discord guild the suggestion belongs to.
        database_id: The suggestion database the suggestion belongs to.

    Returns:
        The configured rejection threshold, or the documented default.
    """
    if (
        suggestion_database_configuration_repository is None
        or guild_id is None
        or database_id is None
    ):
        return DEFAULT_REJECTION_THRESHOLD

    configuration = suggestion_database_configuration_repository.get(guild_id, database_id)
    if configuration is None:
        return DEFAULT_REJECTION_THRESHOLD

    return configuration.suggestion_rules.rejection_threshold


def resolve_rejection_enabled(
    suggestion_database_configuration_repository: Optional[SuggestionDatabaseConfigurationRepository],
    guild_id: Optional[int],
    database_id: Optional[int],
) -> bool:
    """Look up whether "I Won't Watch" is enabled for a suggestion database.

    Mirrors resolve_rejection_threshold's own fallback behavior exactly:
    an unconfigured database (or no repository/guild_id/database_id
    available) defaults to enabled, matching
    SuggestionRulesConfig.rejection_enabled's own documented default.

    Returns:
        True if new rejections should be accepted for this database.
    """
    if (
        suggestion_database_configuration_repository is None
        or guild_id is None
        or database_id is None
    ):
        return True

    configuration = suggestion_database_configuration_repository.get(guild_id, database_id)
    if configuration is None:
        return True

    return configuration.suggestion_rules.rejection_enabled


def perform_reject_suggestion(
    suggestion_service: SuggestionService,
    suggestion_database_configuration_repository: Optional[SuggestionDatabaseConfigurationRepository],
    permission_service: PermissionService,
    user: object,
    guild_id: Optional[int],
    suggestion_id: int,
) -> tuple[str, bool, Optional[WatchItem]]:
    """Core logic for /reject, kept free of Discord objects except `user`.

    Args:
        suggestion_service: The suggestion service to record the rejection through.
        suggestion_database_configuration_repository: Used to resolve the
            configured rejection threshold for the suggestion's database.
        permission_service: Used to require Watch Party member permission.
        user: The member invoking the command.
        guild_id: The Discord guild the command was run in.
        suggestion_id: The suggestion being rejected.

    Returns:
        A (message, ephemeral, watch_item) tuple. Always ephemeral -- a
        member's own rejection is for their eyes only. watch_item is the
        suggestion's current state on success (used to link back to its
        public post and to offer an Undo Rejection control), or None on
        any failure (permission denied, nonexistent suggestion, already
        archived, or already rejected by this member).
    """
    permission = permission_service.require_watch_party_member(user)
    if not permission.allowed:
        return permission.message, True, None

    watch_item = suggestion_service.get_suggestion(suggestion_id)
    if watch_item is None:
        return "That suggestion doesn't exist.", True, None

    if not resolve_rejection_enabled(
        suggestion_database_configuration_repository, guild_id, watch_item.database_id
    ):
        return '"I Won\'t Watch" is disabled for this collection.', True, None

    threshold = resolve_rejection_threshold(
        suggestion_database_configuration_repository, guild_id, watch_item.database_id
    )
    result = suggestion_service.reject_suggestion(
        suggestion_id, user.id, rejection_threshold=threshold, guild_id=guild_id
    )
    return result.message, True, result.watch_item


async def handle_reject_suggestion(
    interaction: discord.Interaction, bot: "WatchPartyBot", suggestion_id: int
) -> None:
    """Handle /reject: record the rejection via perform_reject_suggestion,
    then keep the suggestion's own public post in sync.

    Suggestion Status Synchronization: a rejection that crosses the
    configured threshold moves the suggestion to Pending Crew Review (see
    SuggestionService.reject_suggestion) -- its own public post must
    reflect that immediately via sync_suggestion_status_embed, the same
    mechanism every other status change goes through, not just the next
    time something unrelated happens to sync it. New Review Workflow: a
    fresh Pending Crew Review transition also notifies the Admin Channel.
    """
    existing = bot.suggestion_service.get_suggestion(suggestion_id)
    was_pending_review_before = existing is not None and existing.status is WatchItemStatus.PENDING_CREW_REVIEW

    message, ephemeral, watch_item = perform_reject_suggestion(
        suggestion_service=bot.suggestion_service,
        suggestion_database_configuration_repository=bot.suggestion_database_configuration_repository,
        permission_service=resolve_permission_service(
            interaction.guild_id, bot.guild_configuration_repository
        ),
        user=interaction.user,
        guild_id=interaction.guild_id,
        suggestion_id=suggestion_id,
    )
    content = build_rejection_confirmation_text(message, watch_item)
    view = None
    if watch_item is not None:
        await sync_suggestion_status_embed(bot, watch_item)
        # Notify only on a fresh transition into Pending Crew Review --
        # was_pending_review_before guards against re-notifying on a
        # repeated /reject attempt against an already-pending suggestion
        # (rejected by reject_suggestion(), but perform_reject_suggestion
        # still returns the current watch_item for display purposes, so
        # checking watch_item.status alone can't tell "just reached" from
        # "already was" apart).
        if not was_pending_review_before and watch_item.status is WatchItemStatus.PENDING_CREW_REVIEW:
            await notify_crew_review(bot, watch_item)

        async def on_undo(undo_interaction: discord.Interaction, undo_suggestion_id: int) -> None:
            # Resolved fresh from the Undo click's own guild context --
            # the nearest available guild_id -- rather than reusing the
            # original /reject interaction's resolution, which may be
            # stale by the time this button is actually clicked.
            await handle_undo_rejection(
                undo_interaction,
                bot.suggestion_service,
                bot.suggestion_database_configuration_repository,
                resolve_permission_service(
                    undo_interaction.guild_id, bot.guild_configuration_repository
                ),
                bot,
                undo_suggestion_id,
            )

        view = RejectionConfirmationView(watch_item.id, on_undo, requester_id=interaction.user.id)
    await interaction.response.send_message(content, view=view, ephemeral=ephemeral)


def perform_remove_rejection(
    suggestion_service: SuggestionService,
    permission_service: PermissionService,
    user: object,
    suggestion_id: int,
    guild_id: Optional[int] = None,
) -> tuple[str, bool]:
    """Core logic for /unreject, kept free of Discord objects except `user`.

    Args:
        suggestion_service: The suggestion service to remove the rejection through.
        permission_service: Used to require Watch Party member permission.
        user: The member invoking the command.
        suggestion_id: The suggestion to remove the member's rejection from.
        guild_id: The Discord guild the command was run in. Threaded
            through to remove_rejection() so a bare, globally-unique
            suggestion_id typed into /unreject can never reach (or
            reveal the existence of) a suggestion belonging to a
            different guild. Defaults to None so callers with no guild
            context, such as the "Undo Rejection" button (already
            guild-bound by which message it's attached to), keep
            working unchanged.

    Returns:
        A (message, ephemeral) tuple. Always ephemeral, matching /reject.
    """
    permission = permission_service.require_watch_party_member(user)
    if not permission.allowed:
        return permission.message, True

    result = suggestion_service.remove_rejection(suggestion_id, user.id, guild_id=guild_id)
    return result.message, True


async def handle_unreject_suggestion(
    interaction: discord.Interaction, bot: "WatchPartyBot", suggestion_id: int
) -> None:
    """Handle /unreject: remove the rejection via perform_remove_rejection,
    then keep the suggestion's own public post in sync.

    Pre-Phase 3 UX Polish: previously the bare /unreject command was the
    one rejection-removal path that never refreshed the suggestion's own
    post afterward -- /reject (handle_reject_suggestion), the "I Won't
    Watch" toggle (handle_suggestion_rejection_toggle), and the "Undo
    Rejection" button (handle_undo_rejection) all already did. Reuses
    sync_suggestion_status_embed, the same shared mechanism
    handle_reject_suggestion calls, rather than Undo Rejection's older,
    narrower bespoke view-only refresh.
    """
    message, ephemeral = perform_remove_rejection(
        suggestion_service=bot.suggestion_service,
        permission_service=resolve_permission_service(
            interaction.guild_id, bot.guild_configuration_repository
        ),
        user=interaction.user,
        suggestion_id=suggestion_id,
        guild_id=interaction.guild_id,
    )
    await interaction.response.send_message(message, ephemeral=ephemeral)

    watch_item = bot.suggestion_service.get_suggestion(suggestion_id)
    if watch_item is not None:
        await sync_suggestion_status_embed(bot, watch_item)


def build_suggestion_message_link(watch_item: WatchItem) -> Optional[str]:
    """Build a link back to a suggestion's original public post, or None
    if it was never publicly confirmed (or that reference is incomplete).
    """
    if watch_item.guild_id is None or watch_item.channel_id is None or watch_item.message_id is None:
        return None
    return f"https://discord.com/channels/{watch_item.guild_id}/{watch_item.channel_id}/{watch_item.message_id}"


def build_rejection_confirmation_text(message: str, watch_item: Optional[WatchItem]) -> str:
    """Append a link back to the original suggestion post to a rejection
    confirmation, when one is known (Requirement 2).
    """
    link = build_suggestion_message_link(watch_item) if watch_item is not None else None
    if link is None:
        return message
    return f"{message}\n[View Suggestion]({link})"


# --- New Review Workflow: Crew Review notification -------------------------------------


def build_crew_review_notification_embed(watch_item: WatchItem, *, database_name: str) -> discord.Embed:
    """Summarize a suggestion pending Crew Review for the Admin Channel
    notification: the suggestion (title + reference, in the description --
    never a separate field, which would only repeat it), its collection,
    the current rejection count, and who voted "I Won't Watch". Kept to
    exactly these four facts -- nothing else needed to make a Retire/Keep
    Active/Reset Rejections decision.
    """
    voters = (
        ", ".join(f"<@{discord_user_id}>" for discord_user_id in watch_item.journey.rejected_by_discord_user_ids)
        or "None"
    )
    embed = discord.Embed(
        title="⚠️ Pending Crew Review",
        description=f"**{watch_item.title}** ({watch_item.reference}) needs a Retire / Keep Active / Reset Rejections decision.",
        color=0xE67E22,
    )
    embed.add_field(name="Collection", value=format_collection_display(database_name), inline=True)
    embed.add_field(
        name="Rejections", value=str(len(watch_item.journey.rejected_by_discord_user_ids)), inline=True
    )
    embed.add_field(name="Rejected By", value=voters, inline=False)
    return embed


def build_crew_review_view(bot: "WatchPartyBot", watch_item: WatchItem) -> CrewReviewView:
    async def on_retire(interaction: discord.Interaction, suggestion_id: int) -> None:
        await handle_crew_review_decision(interaction, bot, suggestion_id, decision="retire")

    async def on_keep_active(interaction: discord.Interaction, suggestion_id: int) -> None:
        await handle_crew_review_decision(interaction, bot, suggestion_id, decision="keep_active")

    async def on_reset_rejections(interaction: discord.Interaction, suggestion_id: int) -> None:
        await handle_crew_review_decision(interaction, bot, suggestion_id, decision="reset_rejections")

    return CrewReviewView(
        watch_item.id,
        on_retire,
        on_keep_active,
        on_reset_rejections,
        suggestion_url=build_suggestion_message_link(watch_item),
    )


async def notify_crew_review(bot: "WatchPartyBot", watch_item: WatchItem) -> None:
    """Notify the guild's Admin Channel that a suggestion has reached its
    "I Won't Watch" rejection threshold and now needs a WASH Crew
    Retire/Keep Active/Reset Rejections decision (New Review Workflow).

    Records where the notification landed (see
    SuggestionService.set_crew_review_notification_reference) so
    restore_persistent_crew_review_views can reconnect its buttons after
    a bot restart without posting a second, duplicate notification.

    Best-effort, mirroring handle_join_watch_party's own Admin Channel
    notification: logs a warning and never raises if the Admin Channel
    isn't configured or reachable -- the suggestion's status change is
    already persisted by SuggestionService.reject_suggestion regardless
    of whether WASH Crew could be notified about it.
    """
    if watch_item.guild_id is None or watch_item.database_id is None or watch_item.id is None:
        return

    guild_configuration = bot.guild_configuration_repository.get(watch_item.guild_id)
    channel_id = guild_configuration.channels.admin_channel_id if guild_configuration is not None else None
    if channel_id is None:
        logger.warning(
            "Could not notify WASH Crew about suggestion %s's Crew Review: no Admin Channel configured",
            watch_item.id,
        )
        return

    database = bot.suggestion_service.get_database(watch_item.database_id)
    database_name = database.name if database is not None else "Unknown Collection"

    try:
        channel = bot.get_channel(channel_id)
        if channel is None:
            channel = await bot.fetch_channel(channel_id)
        guild_wash_crew_role_id = guild_configuration.wash_crew_role_id if guild_configuration is not None else None
        wash_crew_mention = f"<@&{guild_wash_crew_role_id}>" if guild_wash_crew_role_id else "WASH Crew"
        message = await channel.send(
            f'{wash_crew_mention} "{watch_item.title}" needs review.',
            embed=build_crew_review_notification_embed(watch_item, database_name=database_name),
            view=build_crew_review_view(bot, watch_item),
        )
        bot.suggestion_service.set_crew_review_notification_reference(watch_item.id, channel.id, message.id)
    except Exception:
        logger.warning(
            "Could not notify WASH Crew about suggestion %s's Crew Review", watch_item.id, exc_info=True
        )


async def restore_persistent_crew_review_views(bot: "WatchPartyBot", suggestion_service: SuggestionService) -> int:
    """Restore Retire/Keep Active/Reset Rejections button handling for
    every suggestion still pending Crew Review.

    Every Crew Review notification is created fresh by this feature with
    its buttons already attached (see notify_crew_review) -- there is no
    legacy "message with no buttons" case to migrate, unlike
    restore_persistent_suggestion_views. This still fetches each stored
    message (rather than blindly calling bot.add_view(), the simpler
    pattern restore_persistent_membership_approval_views uses) so a
    notification whose message or channel has since been deleted is
    detected and skipped here, at startup -- instead of only surfacing
    when a WASH Crew member eventually clicks a dead button. View
    Suggestion needs no restoration at all: Discord stores a link
    button's URL directly on the message itself and opens it entirely
    client-side, so it keeps working across a restart regardless of
    whether this function ever runs.

    A suggestion no longer PENDING_CREW_REVIEW (already resolved, by
    whichever action) is never restored -- there's nothing to restore,
    and re-registering a resolved review's buttons would let a stale
    Retire/Keep Active/Reset Rejections click resolve it a second time. A
    PENDING_CREW_REVIEW suggestion with no stored notification reference
    (reached this status before this restoration feature existed) is
    skipped too: there is no known message to reconnect to, and nothing
    here ever posts a fresh notification on its behalf -- that would risk
    a duplicate if the original notification (unknown to WASH) still
    exists.

    Returns:
        The number of Crew Review notifications whose buttons were restored.
    """
    restored = 0
    for watch_item in suggestion_service.get_suggestions():
        if watch_item.status is not WatchItemStatus.PENDING_CREW_REVIEW:
            continue
        if watch_item.crew_review_channel_id is None or watch_item.crew_review_message_id is None:
            continue

        try:
            channel = bot.get_channel(watch_item.crew_review_channel_id)
            if channel is None:
                channel = await bot.fetch_channel(watch_item.crew_review_channel_id)
            await channel.fetch_message(watch_item.crew_review_message_id)
        except Exception:
            logger.warning(
                "Could not fetch suggestion %s's Crew Review notification message %s for "
                "persistent view restoration; skipping",
                watch_item.id,
                watch_item.crew_review_message_id,
                exc_info=True,
            )
            continue

        view = build_crew_review_view(bot, watch_item)
        bot.add_view(view, message_id=watch_item.crew_review_message_id)
        restored += 1

    logger.info("Restored %s persistent Crew Review view(s)", restored)
    return restored


async def handle_crew_review_decision(
    interaction: discord.Interaction, bot: "WatchPartyBot", suggestion_id: int, *, decision: str
) -> None:
    """Handle a click on a Crew Review notification's Retire, Keep
    Active, or Reset Rejections button.

    Only WASH Crew may resolve a Crew Review -- fails closed exactly
    like every other WASH-gated interaction. Deciding on an
    already-resolved or nonexistent suggestion fails gracefully (the
    service returns success=False with a clear message; nothing raises).
    """
    permission = resolve_permission_service(
        interaction.guild_id, bot.guild_configuration_repository
    ).require_wash_crew(interaction.user)
    if not permission.allowed:
        await interaction.response.send_message(permission.message, ephemeral=True)
        return

    if decision == "retire":
        result = bot.suggestion_service.retire_pending_review(suggestion_id)
    elif decision == "keep_active":
        result = bot.suggestion_service.keep_suggestion_active(suggestion_id)
    else:
        result = bot.suggestion_service.reset_suggestion_rejections(suggestion_id)

    await interaction.response.send_message(result.message, ephemeral=True)

    if not result.success or result.watch_item is None:
        return

    try:
        await interaction.message.edit(
            content=f"{result.message}\n(Decided by {interaction.user.mention})", embed=None, view=None
        )
    except Exception:
        logger.warning(
            "Could not update the Crew Review notification for suggestion %s after it was resolved",
            suggestion_id,
            exc_info=True,
        )

    await sync_suggestion_status_embed(bot, result.watch_item)


async def handle_undo_rejection(
    interaction: discord.Interaction,
    suggestion_service: SuggestionService,
    suggestion_database_configuration_repository: Optional[SuggestionDatabaseConfigurationRepository],
    permission_service: Optional[PermissionService],
    bot: "WatchPartyBot",
    suggestion_id: int,
) -> None:
    """Handle a click on a rejection confirmation's "Undo Rejection" button.

    Reuses perform_remove_rejection() for the actual removal -- the exact
    logic /unreject already uses -- then edits the confirmation message in
    place to say what happened and refreshes the suggestion's own public
    post so its rejection count stays accurate. Gracefully handles a
    suggestion post that's since been deleted (or otherwise unreachable),
    and a rejection that's already been undone some other way (e.g. the
    same member separately toggled it off via the public "I WILL NOT
    WATCH" button first) -- both are reported in the edited confirmation
    text rather than raising.
    """
    if permission_service is None:
        await interaction.response.edit_message(
            content="Watch Party member permissions have not been configured.", view=None
        )
        return

    message, _ = perform_remove_rejection(
        suggestion_service=suggestion_service,
        permission_service=permission_service,
        user=interaction.user,
        suggestion_id=suggestion_id,
    )
    await interaction.response.edit_message(content=message, view=None)

    watch_item = suggestion_service.get_suggestion(suggestion_id)
    if watch_item is None or watch_item.channel_id is None or watch_item.message_id is None:
        return

    view = build_suggestion_view(
        suggestion_service,
        suggestion_database_configuration_repository,
        watch_item,
        watch_item.guild_id,
        permission_service=permission_service,
        bot=bot,
    )
    try:
        channel = bot.get_channel(watch_item.channel_id)
        if channel is None:
            channel = await bot.fetch_channel(watch_item.channel_id)
        post = await channel.fetch_message(watch_item.message_id)
        await post.edit(view=view)
    except Exception:
        logger.warning(
            "Could not refresh suggestion %s's post after an undone rejection; leaving it as-is",
            suggestion_id,
            exc_info=True,
        )


def perform_toggle_suggestion_rejection(
    suggestion_service: SuggestionService,
    suggestion_database_configuration_repository: Optional[SuggestionDatabaseConfigurationRepository],
    permission_service: PermissionService,
    user: object,
    guild_id: Optional[int],
    suggestion_id: int,
) -> tuple[str, bool, Optional[WatchItem]]:
    """Core logic for the suggestion message's "I Won't Watch" button.

    Toggles between SuggestionService.reject_suggestion() and
    remove_rejection() depending on whether `user` has already rejected
    this suggestion, reusing both service methods and
    resolve_rejection_threshold() unchanged rather than introducing a
    second rejection code path -- /reject and /unreject
    (perform_reject_suggestion/perform_remove_rejection) remain available
    as fallback commands and share this exact same underlying logic.

    Args:
        suggestion_service: The suggestion service to toggle the rejection through.
        suggestion_database_configuration_repository: Used to resolve the
            configured rejection threshold for the suggestion's database.
        permission_service: Used to require Watch Party member permission.
        user: The member who clicked the button.
        guild_id: The Discord guild the interaction happened in.
        suggestion_id: The suggestion the button belongs to.

    Returns:
        A (message, ephemeral, watch_item) tuple. Always ephemeral, like
        /reject and /unreject. watch_item is the suggestion's current
        state when the original message should be refreshed to reflect
        it (a successful toggle, or a conflict against another member's
        concurrent click), or None when nothing changed and no refresh is
        needed (permission denied, or the suggestion no longer exists).
    """
    permission = permission_service.require_watch_party_member(user)
    if not permission.allowed:
        return permission.message, True, None

    watch_item = suggestion_service.get_suggestion(suggestion_id)
    if watch_item is None:
        return "That suggestion doesn't exist.", True, None

    already_rejected = user.id in watch_item.journey.rejected_by_discord_user_ids
    if already_rejected:
        result = suggestion_service.remove_rejection(suggestion_id, user.id)
    else:
        if not resolve_rejection_enabled(
            suggestion_database_configuration_repository, guild_id, watch_item.database_id
        ):
            return '"I Won\'t Watch" is disabled for this collection.', True, None
        threshold = resolve_rejection_threshold(
            suggestion_database_configuration_repository, guild_id, watch_item.database_id
        )
        result = suggestion_service.reject_suggestion(
            suggestion_id, user.id, rejection_threshold=threshold
        )

    refreshed_watch_item = result.watch_item if result.watch_item is not None else watch_item
    return result.message, True, refreshed_watch_item


async def handle_suggestion_rejection_toggle(
    interaction: discord.Interaction,
    suggestion_service: SuggestionService,
    suggestion_database_configuration_repository: Optional[SuggestionDatabaseConfigurationRepository],
    suggestion_id: int,
    permission_service: Optional[PermissionService] = None,
    bot: Optional["WatchPartyBot"] = None,
) -> None:
    """Handle a click on a suggestion's "I Won't Watch" button.

    Reuses perform_toggle_suggestion_rejection() for all rejection logic,
    then refreshes the original suggestion message so its displayed
    button (count/threshold/archived state) and Status field both stay
    accurate -- mirroring handle_nominee_vote's "respond ephemerally,
    then refresh the original post" pattern. Never posts an additional
    public message.

    Args:
        interaction: The button-click interaction.
        suggestion_service: The suggestion service to toggle the rejection through.
        suggestion_database_configuration_repository: Used to resolve the
            configured rejection threshold.
        suggestion_id: The suggestion this button belongs to.
        permission_service: Used to require Watch Party member permission.
            Optional so this stays usable in a context with none
            configured; if omitted, the button reports a clear
            "not configured" message rather than allowing the click
            through, matching PermissionService's own fail-closed convention.
        bot: Used to resync this post's embed (Status field) through the
            same sync_suggestion_status_embed every other status change
            goes through (Suggestion Status Synchronization) -- a
            rejection that crosses the archive threshold changes this
            suggestion's status, and that post is the one the button
            lives on. Optional: when omitted (e.g. a caller with no bot
            instance available), falls back to refreshing only the
            view, matching this function's previous behavior.
    """
    if permission_service is None:
        await interaction.response.send_message(
            "Watch Party member permissions have not been configured.", ephemeral=True
        )
        return

    existing = suggestion_service.get_suggestion(suggestion_id)
    was_pending_review_before = existing is not None and existing.status is WatchItemStatus.PENDING_CREW_REVIEW

    message, ephemeral, watch_item = perform_toggle_suggestion_rejection(
        suggestion_service,
        suggestion_database_configuration_repository,
        permission_service,
        interaction.user,
        interaction.guild_id,
        suggestion_id,
    )
    await interaction.response.send_message(message, ephemeral=ephemeral)

    if watch_item is None:
        return

    if bot is not None:
        await sync_suggestion_status_embed(bot, watch_item)
        # See handle_reject_suggestion's identical guard for why
        # was_pending_review_before is needed: without it, a repeated
        # click on an already-pending suggestion would re-notify the
        # Admin Channel every time.
        if not was_pending_review_before and watch_item.status is WatchItemStatus.PENDING_CREW_REVIEW:
            await notify_crew_review(bot, watch_item)
        return

    view = build_suggestion_view(
        suggestion_service,
        suggestion_database_configuration_repository,
        watch_item,
        interaction.guild_id,
        permission_service=permission_service,
    )
    await interaction.message.edit(view=view)


async def handle_watched_button_click(
    interaction: discord.Interaction,
    bot: Optional["WatchPartyBot"],
    suggestion_id: int,
    *,
    permission_service: Optional[PermissionService] = None,
) -> None:
    """Handle a click on a suggestion's Watched button.

    Visible to everyone, but only WASH Crew may successfully use it --
    permission-gated here, in the callback, never by hiding or removing
    the component itself (component visibility is never the security
    boundary). Opens a modal to confirm (and optionally edit) the
    watched date; nothing is recorded until that modal is submitted and
    the date validates.
    """
    if bot is None or permission_service is None:
        await interaction.response.send_message(
            "This isn't available right now. Please try again later.", ephemeral=True
        )
        return

    permission = permission_service.require_wash_crew(interaction.user)
    if not permission.allowed:
        await interaction.response.send_message(permission.message, ephemeral=True)
        return

    watch_item = bot.suggestion_service.get_suggestion(suggestion_id)
    if watch_item is None:
        await interaction.response.send_message("That suggestion no longer exists.", ephemeral=True)
        return
    if watch_item.status is WatchItemStatus.WATCHED:
        await interaction.response.send_message("That suggestion is already marked watched.", ephemeral=True)
        return

    async def on_date_submit(modal_interaction: discord.Interaction, date_text: str) -> None:
        try:
            watched_date = parse_watched_date(date_text)
        except ValueError as exc:
            await modal_interaction.response.send_message(
                f"⚠ {exc} Click Watched again to retry.", ephemeral=True
            )
            return

        result = bot.suggestion_service.mark_suggestion_watched(suggestion_id, watched_date)
        if not result.success:
            await modal_interaction.response.send_message(result.message, ephemeral=True)
            return

        await sync_suggestion_status_embed(bot, result.watch_item)
        archive_posted, archive_note = await post_watched_item_archive(bot, result.watch_item)

        ack = f'"{result.watch_item.title}" has been marked watched ({watched_date.isoformat()}).'
        if not archive_posted:
            ack += f"\n{archive_note}"
        await modal_interaction.response.send_message(ack, ephemeral=True)

    await interaction.response.send_modal(WatchedDateModal(suggestion_id, on_date_submit))


def build_database_add_confirmation(database: SuggestionDatabase) -> str:
    """Build the /database_add confirmation message.

    Args:
        database: The newly created suggestion database.

    Returns:
        A confirmation naming the database, its ID, and its channel.
    """
    return (
        f'Collection "{database.name}" created.\n'
        f"Database ID: {database.database_id}\n"
        f"Destination: <#{database.channel_id}>"
    )


def _resolve_collection_name(
    suggestion_service: SuggestionService,
    database: SuggestionDatabase,
    guild: Optional[discord.Guild],
    suggestion_database_configuration_repository: Optional[SuggestionDatabaseConfigurationRepository],
) -> str:
    """A collection's display name is its suggestion destination channel
    or thread's current Discord name, not a separately-maintained
    string -- renaming the channel in Discord immediately changes what
    WASH shows everywhere it's next displayed. Falls back to the
    database's stored name (set once at creation, never auto-updated)
    when no guild is available to resolve against, or the channel can no
    longer be resolved (e.g. it was deleted).
    """
    if guild is not None:
        channel_id = suggestion_service.resolve_collection_channel_id(
            database, suggestion_database_configuration_repository
        )
        channel = guild.get_channel_or_thread(channel_id)
        if channel is not None and getattr(channel, "name", None):
            return channel.name
    return database.name


def build_database_list_text(
    suggestion_service: SuggestionService,
    databases: List[SuggestionDatabase],
    guild: Optional[discord.Guild] = None,
    suggestion_database_configuration_repository: Optional[SuggestionDatabaseConfigurationRepository] = None,
) -> str:
    """Build the /database_list message for a set of databases.

    Args:
        suggestion_service: Used to look up each database's watch-item
            count and (with guild) resolve its live collection name.
        databases: The databases to display, in the order given.
        guild: Optional; when supplied, each collection's Name line shows
            its suggestion destination channel/thread's current Discord
            name instead of the stored, never-auto-updated name. Omitted
            by callers/tests with no live Discord connection, in which
            case the stored name is shown (unchanged prior behavior).
        suggestion_database_configuration_repository: Optional; used
            together with guild to resolve a collection's configured
            suggestion-destination override, if any, before its home
            channel.

    Returns:
        A readable multi-line block per database with its ID, name, status,
        Discord channel mention, and current watch-item count. The ID is
        labeled explicitly ("Database ID: 1") rather than shown as a bare
        "[1]" prefix, which read as ambiguous (Release Polish Priority 2).
    """
    sections = ["Collections"]
    ordered_databases = sorted(
        databases,
        key=lambda database: (not database.active, database.name.casefold(), database.database_id),
    )
    for database in ordered_databases:
        status = "Active" if database.active else "Inactive"
        suggestion_count = suggestion_service.suggestion_count_for_database(database.database_id)
        display_name = format_collection_display(
            _resolve_collection_name(
                suggestion_service, database, guild, suggestion_database_configuration_repository
            )
        )
        current_channel_id = suggestion_service.resolve_collection_channel_id(
            database, suggestion_database_configuration_repository
        )
        sections.append(
            f"Database ID: {database.database_id}\n"
            f"Name: {display_name}\n"
            f"Status: {status}\n"
            f"Destination: <#{current_channel_id}>\n"
            f"Watch items: {suggestion_count}"
        )
    return "\n\n".join(sections)


def build_database_admin_options(
    suggestion_service: SuggestionService,
    databases: List[SuggestionDatabase],
    guild: Optional[discord.Guild] = None,
    suggestion_database_configuration_repository: Optional[SuggestionDatabaseConfigurationRepository] = None,
) -> List[Tuple[int, str, str]]:
    """Build (id, label, description) options for DatabaseAdminSelectView.

    Shared by /database_backup, /database_reset, and /database_remove
    (Release Polish: Discord-native UX) so a destructive/administrative
    action's target is always shown by name, Active/Inactive status, and
    watch-item count -- never picked by a bare, easy-to-mistype ID.

    Args:
        suggestion_service: Used to look up each database's watch-item count.
        databases: The databases to build options for.
        guild: Optional; see build_database_list_text.
        suggestion_database_configuration_repository: Optional; see
            build_database_list_text.

    Returns:
        Options ordered the same way as build_database_list_text (Active
        first, then alphabetically by name).
    """
    ordered_databases = sorted(
        databases,
        key=lambda database: (not database.active, database.name.casefold(), database.database_id),
    )
    options: List[Tuple[int, str, str]] = []
    for database in ordered_databases:
        status = "Active" if database.active else "Inactive"
        suggestion_count = suggestion_service.suggestion_count_for_database(database.database_id)
        item_word = "watch item" if suggestion_count == 1 else "watch items"
        display_name = format_collection_display(
            _resolve_collection_name(
                suggestion_service, database, guild, suggestion_database_configuration_repository
            )
        )
        options.append((database.database_id, display_name, f"{status} - {suggestion_count} {item_word}"))
    return options


def perform_remove_suggestion(
    suggestion_service: SuggestionService,
    user: object,
    wash_crew_role_id: Optional[int],
    title: str,
) -> tuple[str, bool, bool]:
    """Core logic for /suggestion remove, kept free of Discord objects except `user`.

    FR-029's approved permission model restricts /suggestion remove to WASH Crew
    (an earlier revision of this milestone incorrectly allowed any Watch
    Party member; this is the corrected, fail-closed WASH Crew check).

    Args:
        suggestion_service: The suggestion service to remove the item through.
        user: The member invoking the command.
        wash_crew_role_id: The configured WASH Crew role ID, or None if unconfigured.
        title: The watch item's title to remove.

    Returns:
        A (message, ephemeral, success) tuple. Permission failures are
        ephemeral; the service's own result message is shown publicly on
        success or failure, matching /suggestion remove's existing confirmation style.
    """
    if wash_crew_role_id is None:
        return (
            "WASH Crew permissions have not been configured. "
            "Set WASH_CREW_ROLE_ID before using this command.",
            True,
            False,
        )

    if not is_wash_crew_member(user, wash_crew_role_id):
        return "You need the WASH Crew role to remove a watch item.", True, False

    result = suggestion_service.remove_suggestion(title)
    return result.message, False, result.success


# --- FR-033A: /suggestion remove with reference/title matching, a selector, and archival --


def build_removal_option_label(
    item: WatchItem,
    suggestion_service: SuggestionService,
    vote_service: Optional[VoteService] = None,
) -> str:
    """Build one /suggestion remove selector option's label: reference, title, year,
    database, and status (Section 6's "Show a selector including...").

    vote_service resolves In an Active Vote -- optional, defaulting to
    None so existing callers/tests keep working unchanged (meaning it's
    never shown).
    """
    database = suggestion_service.get_database(item.database_id) if item.database_id is not None else None
    database_name = database.name if database is not None else "Unknown collection"
    year_part = f" ({item.release_year})" if item.release_year else ""
    display_status = resolve_display_status(item, vote_service)
    status_part = display_status_label(display_status)
    return f"{item.reference} {item.title}{year_part} -- {database_name} -- {status_part}"


async def send_removal_confirmation(interaction: discord.Interaction, bot: "WatchPartyBot", item: WatchItem) -> None:
    """Show the "remove this one?" confirmation, then archive on confirm.

    Prefers archival over permanent deletion (Section 6): the existing
    SuggestionService.remove_suggestion()/remove_suggestion_by_id()
    hard-delete methods are untouched (still used by /maintenance repair
    for genuinely broken records) -- this reuses archive_suggestion()
    instead, which preserves identity, journey, and history.
    """
    summary = build_removal_option_label(item, bot.suggestion_service, getattr(bot, "vote_service", None))

    async def on_confirm(confirm_interaction: discord.Interaction) -> None:
        result = bot.suggestion_service.archive_suggestion(item.id, guild_id=item.guild_id)
        await confirm_interaction.response.send_message(result.message, ephemeral=True)
        if result.success:
            await sync_suggestion_status_embed(bot, result.watch_item)

    async def on_abort(abort_interaction: discord.Interaction) -> None:
        await abort_interaction.response.send_message("No changes were made.", ephemeral=True)

    view = EditVoteConfirmationView(confirm_label="Remove", on_confirm=on_confirm, on_abort=on_abort)
    await interaction.response.send_message(f"Remove this suggestion?\n{summary}", view=view, ephemeral=True)


async def handle_remove_suggestion(interaction: discord.Interaction, bot: "WatchPartyBot", query: str) -> None:
    guild_permission_service = resolve_permission_service(interaction.guild_id, bot.guild_configuration_repository)
    if guild_permission_service.wash_crew_role_id is None:
        await interaction.response.send_message(
            "WASH Crew permissions have not been configured. Set WASH_CREW_ROLE_ID before using this command.",
            ephemeral=True,
        )
        return
    if not guild_permission_service.is_wash_crew(interaction.user):
        await interaction.response.send_message("You need the WASH Crew role to remove a watch item.", ephemeral=True)
        return

    matches = bot.suggestion_service.find_matches_for_removal(query, guild_id=interaction.guild_id)
    if not matches:
        await interaction.response.send_message(f'No suggestion matches "{query}".', ephemeral=True)
        return

    if len(matches) == 1:
        await send_removal_confirmation(interaction, bot, matches[0])
        return

    async def on_select(select_interaction: discord.Interaction, suggestion_id: int) -> None:
        item = bot.suggestion_service.get_suggestion(suggestion_id)
        if item is None:
            await select_interaction.response.send_message("That suggestion no longer exists.", ephemeral=True)
            return
        await send_removal_confirmation(select_interaction, bot, item)

    options = [
        (
            item.id,
            build_removal_option_label(item, bot.suggestion_service, getattr(bot, "vote_service", None)),
        )
        for item in matches
    ]
    view = RemovalMatchSelectView(options, on_select)
    await interaction.response.send_message(
        f'Multiple suggestions match "{query}". Choose one:', view=view, ephemeral=True
    )


# --- FR-033A: Crew-only suggestion editing --------------------------------------------


def build_edit_suggestion_summary(
    item: WatchItem,
    suggestion_service: SuggestionService,
    vote_service: Optional[VoteService] = None,
) -> str:
    """Build the read-only IMDb-metadata summary shown alongside
    /suggestion edit's action picker (Requirement 8: OMDb-derived fields
    are for reference only, never manually edited here).

    Links to the suggestion's own Discord post rather than repeating its
    raw IMDb URL -- that post's own embed already has an IMDb-linked
    title card, so this is the more useful (and clickable) top link.
    Falls back to the raw IMDb URL only for a legacy suggestion with no
    confirmation post to link to, since that's otherwise the only way to
    reach its IMDb page from here at all. vote_service resolves In an
    Active Vote -- optional, defaulting to None so existing callers/tests
    keep working unchanged (meaning it's never shown).
    """
    database = suggestion_service.get_database(item.database_id) if item.database_id is not None else None
    database_name = database.name if database is not None else "Unknown collection"
    year_part = f" ({item.release_year})" if item.release_year else ""
    imdb_url = item.metadata_ids.get(MetadataProvider.IMDB)
    display_status = resolve_display_status(item, vote_service)
    lines = [
        f"{item.reference} {item.title}{year_part}",
        f"Collection: {database_name}",
        f"Status: {format_display_status_with_won_date(item, display_status)}",
    ]
    post_link = build_suggestion_message_link(item)
    if post_link is not None:
        lines.append(f"[Original Suggestion]({post_link})")
    elif imdb_url:
        lines.append(f"IMDb: {imdb_url}")
    return "\n".join(lines)


async def handle_edit_suggestion(interaction: discord.Interaction, bot: "WatchPartyBot", reference: str) -> None:
    """Show /suggestion edit's action picker: Change Status, Move to
    Another Collection, or Cancel (Requirement 8). IMDb metadata (title,
    release year, director, etc.) is read-only here -- shown for
    reference in the summary, never manually editable.
    """
    guild_permission_service = resolve_permission_service(interaction.guild_id, bot.guild_configuration_repository)
    if guild_permission_service.wash_crew_role_id is None:
        await interaction.response.send_message(
            "WASH Crew permissions have not been configured. Set WASH_CREW_ROLE_ID before using this command.",
            ephemeral=True,
        )
        return
    if not guild_permission_service.is_wash_crew(interaction.user):
        await interaction.response.send_message("You need the WASH Crew role to edit a suggestion.", ephemeral=True)
        return

    matches = bot.suggestion_service.find_matches_for_removal(reference, guild_id=interaction.guild_id)
    if not matches:
        await interaction.response.send_message(f'No suggestion matches "{reference}".', ephemeral=True)
        return
    if len(matches) > 1:
        await interaction.response.send_message(
            f'Multiple suggestions match "{reference}". Use its reference number (e.g. #0007) to be specific.',
            ephemeral=True,
        )
        return
    item = matches[0]

    async def on_change_status(button_interaction: discord.Interaction) -> None:
        current_item = bot.suggestion_service.get_suggestion(item.id)
        if current_item is None:
            await button_interaction.response.send_message("That suggestion no longer exists.", ephemeral=True)
            return

        async def on_status_selected(select_interaction: discord.Interaction, new_status: WatchItemStatus) -> None:
            result = bot.suggestion_service.set_suggestion_status(
                item.id, new_status, getattr(bot, "vote_service", None), guild_id=current_item.guild_id
            )
            await select_interaction.response.send_message(result.message, ephemeral=True)
            if result.success:
                await sync_suggestion_status_embed(bot, result.watch_item)

        view = ChangeStatusSelectView(on_status_selected, current_status=current_item.status)
        await button_interaction.response.edit_message(
            content=build_edit_suggestion_summary(
                current_item,
                bot.suggestion_service,
                getattr(bot, "vote_service", None),
            )
            + "\n\nChoose the new status:",
            view=view,
        )

    async def on_move_collection(button_interaction: discord.Interaction) -> None:
        guild_id = button_interaction.guild_id or item.guild_id
        databases = bot.suggestion_service.list_databases(guild_id) if guild_id is not None else []
        if not databases:
            await button_interaction.response.send_message("No collections exist in this server yet. Create one with `/database add`.", ephemeral=True)
            return

        async def on_database_selected(select_interaction: discord.Interaction, new_database_id: int) -> None:
            current_item = bot.suggestion_service.get_suggestion(item.id)
            if current_item is None:
                await select_interaction.response.send_message(
                    "That suggestion no longer exists.", ephemeral=True
                )
                return

            destination = bot.suggestion_service.get_database(new_database_id)
            if destination is None or not destination.active or destination.guild_id != current_item.guild_id:
                await select_interaction.response.send_message(
                    "That destination collection is not available -- it may not exist, be inactive, "
                    "or belong to a different server.",
                    ephemeral=True,
                )
                return

            if new_database_id == current_item.database_id:
                await select_interaction.response.send_message(
                    "That suggestion is already in that collection.", ephemeral=True
                )
                return

            current_imdb_url = current_item.metadata_ids.get(MetadataProvider.IMDB)

            async def apply_move(target_interaction: discord.Interaction) -> None:
                result = bot.suggestion_service.edit_suggestion(
                    current_item.id,
                    title=current_item.title,
                    release_year=current_item.release_year,
                    imdb_url=current_imdb_url,
                    database_id=new_database_id,
                )
                await target_interaction.response.send_message(result.message, ephemeral=True)
                if result.success:
                    await sync_suggestion_status_embed(bot, result.watch_item)

            existing_items = bot.suggestion_service.get_suggestions_for_database(
                new_database_id, include_archived=True
            )
            duplicate_result = find_duplicates(
                title=current_item.title,
                release_year=current_item.release_year,
                imdb_url=current_imdb_url,
                existing_items=existing_items,
                exclude_id=current_item.id,
            )

            if duplicate_result.has_definite_match:
                match = duplicate_result.definite_matches[0]
                await select_interaction.response.send_message(
                    "This move would duplicate an existing suggestion:\n"
                    + build_duplicate_match_line(match, getattr(bot, "vote_service", None)),
                    ephemeral=True,
                )
                return

            if duplicate_result.has_possible_only:
                lines = "\n".join(
                    build_duplicate_match_line(match, getattr(bot, "vote_service", None))
                    for match in duplicate_result.matches
                )
                message = f"This move might duplicate existing item(s):\n{lines}"

                async def on_confirm(confirm_interaction: discord.Interaction) -> None:
                    await apply_move(confirm_interaction)

                async def on_abort(abort_interaction: discord.Interaction) -> None:
                    await abort_interaction.response.send_message("No changes were made.", ephemeral=True)

                view = EditVoteConfirmationView(
                    confirm_label="Move Anyway",
                    on_confirm=on_confirm,
                    on_abort=on_abort,
                    confirm_style=discord.ButtonStyle.primary,
                )
                await select_interaction.response.send_message(message, view=view, ephemeral=True)
                return

            await apply_move(select_interaction)

        options = build_database_admin_options(
            bot.suggestion_service, databases, button_interaction.guild, bot.suggestion_database_configuration_repository
        )
        view = DatabaseAdminSelectView(
            options,
            on_database_selected,
            custom_id="wpm_edit_suggestion_move_collection_select",
            placeholder="Choose a destination collection...",
        )
        await button_interaction.response.edit_message(
            content=build_edit_suggestion_summary(
                item, bot.suggestion_service, getattr(bot, "vote_service", None)
            )
            + "\n\nMove to which collection?",
            view=view,
        )

    async def on_cancel(button_interaction: discord.Interaction) -> None:
        await button_interaction.response.edit_message(content="No changes were made.", view=None)

    view = EditSuggestionActionView(on_change_status, on_move_collection, on_cancel)
    await interaction.response.send_message(
        build_edit_suggestion_summary(item, bot.suggestion_service, getattr(bot, "vote_service", None)),
        view=view,
        ephemeral=True,
    )


_USABLE_CURRENT_THREAD_TYPES = (
    discord.ChannelType.public_thread,
    discord.ChannelType.private_thread,
)


def _is_usable_current_thread(channel: Optional[Any]) -> bool:
    """Whether "Use Current Thread" is offerable for `channel`.

    True only when the command was actually run inside a thread --
    Collections Should Live In Threads: a plain text channel (even
    WASH's configured Home Channel) no longer qualifies as a collection
    destination at all. Used to disable (never omit) the button so it
    appears consistently across every invocation.
    """
    return channel is not None and getattr(channel, "type", None) in _USABLE_CURRENT_THREAD_TYPES


def _can_be_new_thread_parent(channel: Optional[Any]) -> bool:
    """Whether `channel` could parent a brand-new thread.

    Only a top-level text channel qualifies -- Discord does not support
    nesting a thread under another thread. Used by Create New Thread's
    fallback: when no Home Channel is configured (or it's no longer
    available), the current channel becomes the new thread's parent
    instead, but only if it's actually eligible; a thread invocation
    location never is, so the button is disabled in that case rather
    than offered and left to fail.
    """
    return channel is not None and getattr(channel, "type", None) == discord.ChannelType.text


def _register_freshly_created_thread(guild: Optional[Any], thread: discord.Thread) -> None:
    """Bug fix: discord.py's TextChannel.create_thread() returns the new
    Thread but never adds it to the guild's own cache -- that only
    happens once the gateway later dispatches THREAD_CREATE (handled by
    ConnectionState.parse_thread_create), which is not guaranteed to have
    arrived yet by the time code immediately following create_thread()
    runs. Create New Thread's on_destination_resolved callback for
    /database move (ConfigService.set_database_suggestion_destination)
    re-validates the destination via validate_channel_usable(), which
    calls guild.get_channel_or_thread() -- against the stale cache this
    reports the brand-new thread as "no longer exists", so the move
    fails and finalize()'s rollback deletes the very thread that was
    just created. Registering it immediately (mirroring what
    ConnectionState.parse_thread_create itself does) closes that race.
    A no-op for any guild-like object that doesn't expose this
    discord.py-internal hook (e.g. a test double that doesn't model it).
    """
    register = getattr(guild, "_add_thread", None)
    if callable(register):
        register(thread)


async def send_destination_choice(
    interaction: discord.Interaction,
    bot: "WatchPartyBot",
    *,
    guild_id: int,
    current_channel: Optional[Any],
    prompt: str,
    thread_name_default: str,
    on_destination_resolved: Callable[[discord.Interaction, int, Optional[str]], Awaitable[bool]],
    on_cancel: Callable[[discord.Interaction], Awaitable[None]],
) -> None:
    """The one shared "where should this collection's suggestions post?"
    flow for /database add and /database move (Collections Should Live In
    Threads): Create New Thread (Recommended), Use Current Thread, or Use
    Existing Thread. Collections no longer offer a plain-channel
    destination at all -- WASH's Watch Party Home Channel is reserved for
    discussion, announcements, and navigation, never a collection
    destination itself; collection activity always lives in a thread
    beneath it.

    Neither destination validation, thread creation, thread selection,
    nor rollback is duplicated between /database add and /database move
    (or /database manage's Move Collection action) -- all three call this
    one function.

    Args:
        interaction: The interaction to render the destination choice on
            (always edited, never sent fresh -- callers only reach this
            after their own preceding step, e.g. a type choice or a
            collection picker, already produced a response to edit).
        bot: Used to resolve the guild's configured home channel for
            Create New Thread.
        guild_id: The guild the destination is being chosen in.
        current_channel: The channel or thread the originating command
            was actually run in (typically `interaction.channel` at the
            top of the caller), or None. Determines whether Use Current
            Thread is enabled (only when this is itself a thread), and
            which channel_id it resolves to.
        prompt: The message content to show alongside the choice.
        thread_name_default: The suggested (editable) name for Create New
            Thread's rename modal.
        on_destination_resolved: Called once a destination channel_id is
            determined, as `(interaction, channel_id, created_thread_name)`.
            created_thread_name is the (possibly renamed) name typed into
            Create New Thread's modal, or None for every other path --
            /database add uses it as the new collection's name (it has no
            established name yet); /database move ignores it (a
            collection's name is never changed by a move). Must send its
            own response (edit_message) reporting success or failure, and
            return whether it succeeded, so a just-created thread can be
            rolled back on failure.
        on_cancel: Called if Cancel is clicked at any point in the flow.
    """
    current_location_available = _is_usable_current_thread(current_channel)

    guild_configuration = bot.guild_configuration_repository.get(guild_id)
    home_channel_id = guild_configuration.channels.home_channel_id if guild_configuration is not None else None
    home_channel = (
        interaction.guild.get_channel(home_channel_id)
        if interaction.guild is not None and home_channel_id is not None
        else None
    )
    # Create New Thread Improvement: prefer the configured Home Channel
    # as the new thread's parent, same as before. When there isn't one
    # (unconfigured, or no longer available), fall back to the current
    # channel *only* if it's a usable parent for a brand-new thread --
    # Discord doesn't allow nesting a thread under another thread, so
    # this fallback never applies when the command was run inside a
    # thread. If neither is available, the button is disabled instead of
    # presenting an option that can never succeed.
    thread_parent = home_channel if home_channel is not None else (
        current_channel if _can_be_new_thread_parent(current_channel) else None
    )
    create_new_thread_available = thread_parent is not None
    using_current_channel_as_parent = home_channel is None and thread_parent is not None

    async def finalize(
        final_interaction: discord.Interaction,
        channel_id: int,
        *,
        created_thread: Optional[discord.Thread] = None,
        created_thread_name: Optional[str] = None,
    ) -> None:
        success = await on_destination_resolved(final_interaction, channel_id, created_thread_name)
        if not success and created_thread is not None:
            try:
                await created_thread.delete()
            except (discord.Forbidden, discord.HTTPException):
                logger.warning(
                    "Could not roll back newly created thread %s after a failed destination change",
                    created_thread.id,
                    exc_info=True,
                )

    def destination_view() -> DestinationChoiceView:
        return DestinationChoiceView(
            on_create_new_thread,
            on_use_current,
            on_use_existing_thread,
            on_cancel,
            current_location_available=current_location_available,
            create_new_thread_available=create_new_thread_available,
        )

    async def on_create_new_thread(destination_interaction: discord.Interaction) -> None:
        if thread_parent is None:
            # Defense in depth -- the button is already disabled for this
            # case, but a stale/cached component could still be clicked.
            await destination_interaction.response.edit_message(
                content=(
                    "⚠ There's nowhere to create a new thread here. WASH's home channel isn't "
                    "configured (or is no longer available), and this command wasn't run in a "
                    "text channel that could parent one. Choose a different destination option, "
                    "or configure a Home Channel in `/config`."
                ),
                view=destination_view(),
            )
            return

        async def on_thread_name_submit(modal_interaction: discord.Interaction, thread_name: str) -> None:
            try:
                thread = await thread_parent.create_thread(name=thread_name, type=discord.ChannelType.public_thread)
            except (discord.Forbidden, discord.HTTPException) as exc:
                await modal_interaction.response.edit_message(
                    content=f"⚠ Could not create the thread: {exc}", view=destination_view()
                )
                return
            _register_freshly_created_thread(modal_interaction.guild, thread)
            await finalize(modal_interaction, thread.id, created_thread=thread, created_thread_name=thread_name)

        await destination_interaction.response.send_modal(
            CreateThreadNameModal(on_thread_name_submit, default=thread_name_default)
        )

    async def on_use_current(destination_interaction: discord.Interaction) -> None:
        if not current_location_available:
            # Defense in depth -- the button is already disabled for this
            # case, but a stale/cached component could still be clicked.
            await destination_interaction.response.edit_message(
                content="⚠ This option isn't available here. Choose a different destination.",
                view=destination_view(),
            )
            return
        await finalize(destination_interaction, current_channel.id)

    async def on_use_existing_thread(destination_interaction: discord.Interaction) -> None:
        async def on_thread_selected(select_interaction: discord.Interaction, channel_id: int) -> None:
            await finalize(select_interaction, channel_id)

        await destination_interaction.response.edit_message(
            content="Choose an existing thread:",
            view=ExistingThreadSelectView(on_thread_selected, on_cancel),
        )

    if using_current_channel_as_parent:
        prompt = (
            f"{prompt}\n\n"
            "*(WASH's Home Channel isn't configured, so **Create New Thread** will create the "
            "new thread here in this channel instead. Set a Home Channel in `/config` to change this.)*"
        )
    await interaction.response.edit_message(content=prompt, view=destination_view())


async def handle_database_add(interaction: discord.Interaction, bot: "WatchPartyBot") -> None:
    """Modernized /database add (Command Structure Cleanup, pre-v1).

    Mirrors the Setup Wizard's own collection-creation flow: choose a
    type -- every standard collection type (Movies, TV Shows, Anime,
    Holiday, Documentaries, Horror) this server doesn't already have a
    matching collection for, plus Special Collection and Custom (always
    available) -- then choose where its suggestions should post, the
    same Create New Thread (Recommended)/Use Current Thread/Use
    Existing Thread choice /database move offers. name is no longer a
    typed command parameter for the standard types; Special Collection
    and Custom still collect it via modal, exactly as the wizard does.

    Every /database add response is ephemeral -- this is an admin
    configuration command. All creation rules (duplicate name, duplicate
    channel, duplicate configured suggestion destination) are still
    enforced by SuggestionService.create_database() -- this only adds
    presentation and Discord-side thread creation around it.
    """
    guild_permission_service = resolve_permission_service(interaction.guild_id, bot.guild_configuration_repository)
    if guild_permission_service.wash_crew_role_id is None:
        await interaction.response.send_message(
            "WASH Crew permissions have not been configured. Set WASH_CREW_ROLE_ID before using this command.",
            ephemeral=True,
        )
        return
    if not guild_permission_service.is_wash_crew(interaction.user):
        await interaction.response.send_message(
            "You need the WASH Crew role to create a collection.", ephemeral=True
        )
        return

    guild_id = interaction.guild_id
    if guild_id is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    used_type_keys = used_standard_collection_type_keys(
        database.name for database in bot.suggestion_service.list_databases(guild_id)
    )
    current_channel = interaction.channel

    async def on_cancel(cancel_interaction: discord.Interaction) -> None:
        await cancel_interaction.response.edit_message(
            content="Collection creation cancelled. No changes were made.", view=None
        )

    async def show_destination_choice(source_interaction: discord.Interaction, name: str) -> None:
        async def create_collection(
            response_interaction: discord.Interaction, channel_id: int, created_thread_name: Optional[str]
        ) -> bool:
            # The collection doesn't have an established name yet (unlike
            # /database move) -- whatever was finally typed into Create
            # New Thread's rename modal becomes the collection's name
            # too, so the thread title and collection name never
            # diverge; every other destination path keeps the name
            # already chosen at the type-selection step.
            final_name = created_thread_name if created_thread_name is not None else name
            result = bot.suggestion_service.create_database(
                final_name,
                guild_id=guild_id,
                channel_id=channel_id,
                suggestion_database_configuration_repository=bot.suggestion_database_configuration_repository,
            )
            if not result.success:
                await response_interaction.response.edit_message(content=result.message, view=None)
                return False
            await response_interaction.response.edit_message(
                content=build_database_add_confirmation(result.database), view=None
            )
            return True

        await send_destination_choice(
            source_interaction,
            bot,
            guild_id=guild_id,
            current_channel=current_channel,
            prompt=f'Where should "{name}" suggestions post?',
            thread_name_default=name,
            on_destination_resolved=create_collection,
            on_cancel=on_cancel,
        )

    async def on_type_chosen(type_interaction: discord.Interaction, key: str) -> None:
        if key == "special":

            async def on_name_submit(modal_interaction: discord.Interaction, name: str) -> None:
                await show_destination_choice(modal_interaction, name)

            await type_interaction.response.send_modal(
                CreateDatabaseNameModal(
                    on_name_submit,
                    title="Name Your Special Collection",
                    label="Collection name",
                    placeholder="e.g. Horror Movies, Anime, Documentaries",
                )
            )
            return
        if key == "custom":

            async def on_name_submit(modal_interaction: discord.Interaction, name: str) -> None:
                await show_destination_choice(modal_interaction, name)

            await type_interaction.response.send_modal(
                CreateDatabaseNameModal(on_name_submit, title="Name Your Collection", label="Collection name")
            )
            return

        standard_type = next(candidate for candidate in STANDARD_COLLECTION_TYPES if candidate.key == key)
        await show_destination_choice(type_interaction, standard_type.default_thread_name)

    view = CollectionTypeSelectionView(used_type_keys, on_type_chosen, on_cancel)
    await interaction.response.send_message(
        "What type of collection would you like to create?", view=view, ephemeral=True
    )


def perform_database_list(
    suggestion_service: SuggestionService,
    user: object,
    wash_crew_role_id: Optional[int],
    guild_id: Optional[int],
    suggestion_database_configuration_repository: Optional[SuggestionDatabaseConfigurationRepository] = None,
) -> tuple[str, bool]:
    """Core logic for /database_list, kept free of Discord objects except `user`.

    Args:
        suggestion_service: The suggestion service to read databases from.
        user: The member invoking the command.
        wash_crew_role_id: The configured WASH Crew role ID, or None if
            unconfigured.
        guild_id: The Discord guild the command was run in.
        suggestion_database_configuration_repository: Optional; when
            supplied, each collection's Channel line reflects its current
            resolved destination (Context Resolution Audit) rather than
            always its original home channel. Omitted by callers/tests
            with no repository in scope, in which case the home channel
            is shown (unchanged prior behavior for a never-moved
            collection).

    Returns:
        A (message, ephemeral) tuple. Every /database_list response is
        ephemeral -- this is an admin configuration command.
    """
    if wash_crew_role_id is None:
        return (
            "WASH Crew permissions have not been configured. "
            "Set WASH_CREW_ROLE_ID before using this command.",
            True,
        )

    if not is_wash_crew_member(user, wash_crew_role_id):
        return "You need the WASH Crew role to view collections.", True

    if guild_id is None:
        return "This command can only be used in a server.", True

    databases = suggestion_service.list_databases(guild_id)
    if not databases:
        return "No collections exist in this server yet. Create one with `/database add`.", True

    return (
        build_database_list_text(
            suggestion_service,
            databases,
            suggestion_database_configuration_repository=suggestion_database_configuration_repository,
        ),
        True,
    )


def perform_database_remove(
    suggestion_service: SuggestionService,
    user: object,
    wash_crew_role_id: Optional[int],
    guild_id: Optional[int],
    database_id: int,
) -> tuple[str, bool]:
    """Core logic for /database_remove, kept free of Discord objects except `user`.

    This deactivates a database rather than deleting it -- all the actual
    rules (unknown ID, already inactive) are enforced by
    SuggestionService.deactivate_database().

    Args:
        suggestion_service: The suggestion service to deactivate the
            database in.
        user: The member invoking the command.
        wash_crew_role_id: The configured WASH Crew role ID, or None if
            unconfigured.
        guild_id: The Discord guild the command was run in, or None outside
            a guild.
        database_id: The database to deactivate.

    Returns:
        A (message, ephemeral) tuple. Every /database_remove response is
        ephemeral -- this is an admin configuration command.
    """
    if wash_crew_role_id is None:
        return (
            "WASH Crew permissions have not been configured. "
            "Set WASH_CREW_ROLE_ID before using this command.",
            True,
        )

    if not is_wash_crew_member(user, wash_crew_role_id):
        return "You need the WASH Crew role to remove a collection.", True

    if guild_id is None:
        return "This command can only be used in a server.", True

    result = suggestion_service.deactivate_database(database_id, guild_id)
    return result.message, True


async def start_database_remove(
    interaction: discord.Interaction, bot: "WatchPartyBot", guild_id: int, database_id: int
) -> None:
    """Deactivate one already-known database. Shared by /database remove
    (after its own picker) and /database manage's Remove Collection
    action -- neither duplicates this logic. perform_database_remove
    itself is unchanged (still the single source of truth for the
    permission checks and deactivation rule).
    """
    message, ephemeral = perform_database_remove(
        suggestion_service=bot.suggestion_service,
        user=interaction.user,
        wash_crew_role_id=resolve_permission_service(
            guild_id, bot.guild_configuration_repository
        ).wash_crew_role_id,
        guild_id=guild_id,
        database_id=database_id,
    )
    await interaction.response.send_message(message, ephemeral=ephemeral)


async def handle_database_remove(interaction: discord.Interaction, bot: "WatchPartyBot") -> None:
    """Show a "which database?" picker, then deactivate the chosen one.

    Release Polish (Discord-native UX): database_id is no longer a
    command parameter -- WASH Crew picks the target from a selector
    showing each database's name, Active/Inactive status, and watch-item
    count instead of typing an internal ID.
    """
    guild_permission_service = resolve_permission_service(interaction.guild_id, bot.guild_configuration_repository)
    if guild_permission_service.wash_crew_role_id is None:
        await interaction.response.send_message(
            "WASH Crew permissions have not been configured. Set WASH_CREW_ROLE_ID before using this command.",
            ephemeral=True,
        )
        return
    if not guild_permission_service.is_wash_crew(interaction.user):
        await interaction.response.send_message(
            "You need the WASH Crew role to remove a collection.", ephemeral=True
        )
        return

    guild_id = interaction.guild_id
    if guild_id is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    databases = bot.suggestion_service.list_databases(guild_id)
    if not databases:
        await interaction.response.send_message("No collections exist in this server yet. Create one with `/database add`.", ephemeral=True)
        return

    async def on_select(select_interaction: discord.Interaction, database_id: int) -> None:
        await start_database_remove(select_interaction, bot, guild_id, database_id)

    options = build_database_admin_options(bot.suggestion_service, databases, interaction.guild, bot.suggestion_database_configuration_repository)
    view = DatabaseAdminSelectView(
        options, on_select, custom_id="wpm_database_remove_select", placeholder="Choose a collection to remove..."
    )
    await interaction.response.send_message("Choose which collection to remove:", view=view, ephemeral=True)


async def start_database_move(
    interaction: discord.Interaction, bot: "WatchPartyBot", guild_id: int, database_id: int
) -> None:
    """Show the destination choice and move one already-known database's
    suggestion destination. Shared by /database move (after its own
    picker) and /database manage's Move Collection action -- neither
    duplicates this logic.

    Moves ONLY the suggestion destination -- reuses
    ConfigService.set_database_suggestion_destination() unchanged, the
    exact same operation /config's Collections -> Suggestion
    Destination already performs, so this can never disagree with it.
    That method already enforces destination validation (still exists,
    still usable) and duplicate-destination prevention (no other
    collection may already route there); it changes only the collection's
    configured suggestion_channel_id, never its database ID, suggestions,
    statuses, vote history, or statistics. Existing
    Discord suggestion posts are untouched -- they keep whatever
    channel/message reference they already had -- only future
    suggestions post to the new destination.
    """
    database = bot.suggestion_service.get_database(database_id)
    if database is None or database.guild_id != guild_id:
        await interaction.response.edit_message(content="That collection no longer exists.", view=None)
        return

    async def on_cancel(cancel_interaction: discord.Interaction) -> None:
        await cancel_interaction.response.edit_message(content="Move cancelled. No changes were made.", view=None)

    async def apply_move(
        response_interaction: discord.Interaction, channel_id: int, created_thread_name: Optional[str]
    ) -> bool:
        # created_thread_name (a Create New Thread rename) never affects
        # a collection's own name -- that's an /database add-only rule,
        # since a move never touches the collection's established name.
        result = bot.config_service.set_database_suggestion_destination(
            guild_id, database_id, channel_id, response_interaction.guild
        )
        await response_interaction.response.edit_message(content=result.message, view=None)
        return result.success

    await send_destination_choice(
        interaction,
        bot,
        guild_id=guild_id,
        current_channel=interaction.channel,
        prompt=f'Where should "{database.name}" suggestions post now?',
        thread_name_default=database.name,
        on_destination_resolved=apply_move,
        on_cancel=on_cancel,
    )


async def handle_database_move(interaction: discord.Interaction, bot: "WatchPartyBot") -> None:
    """/database move (Collections Should Live In Threads): pick a
    collection, then move its suggestion destination to a different
    thread. See start_database_move for the actual move.
    """
    guild_permission_service = resolve_permission_service(interaction.guild_id, bot.guild_configuration_repository)
    if guild_permission_service.wash_crew_role_id is None:
        await interaction.response.send_message(
            "WASH Crew permissions have not been configured. Set WASH_CREW_ROLE_ID before using this command.",
            ephemeral=True,
        )
        return
    if not guild_permission_service.is_wash_crew(interaction.user):
        await interaction.response.send_message(
            "You need the WASH Crew role to move a collection.", ephemeral=True
        )
        return

    guild_id = interaction.guild_id
    if guild_id is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    databases = bot.suggestion_service.list_databases(guild_id)
    if not databases:
        await interaction.response.send_message("No collections exist in this server yet. Create one with `/database add`.", ephemeral=True)
        return

    async def on_database_selected(select_interaction: discord.Interaction, database_id: int) -> None:
        await start_database_move(select_interaction, bot, guild_id, database_id)

    options = build_database_admin_options(
        bot.suggestion_service, databases, interaction.guild, bot.suggestion_database_configuration_repository
    )
    view = DatabaseAdminSelectView(
        options, on_database_selected, custom_id="wpm_database_move_select", placeholder="Choose a collection to move..."
    )
    await interaction.response.send_message("Choose which collection to move:", view=view, ephemeral=True)


async def show_database_management_menu(
    interaction: discord.Interaction, bot: "WatchPartyBot", guild_id: int, database_id: int
) -> None:
    """/database manage's per-collection action menu: Move Collection,
    Edit Collection, Backup Collection, Restore Collection, Refresh IMDb
    Metadata, Recover Missing IMDb Links, Reset Collection, Remove
    Collection, or Cancel.

    Every action reuses the exact same logic its direct /database
    subcommand uses -- Move/Backup/Reset/Remove call the same
    start_database_*() functions their subcommands call after their own
    picker (see handle_database_move/backup/reset/remove), and Edit
    reuses /config's own send_config_database_settings_menu unchanged,
    with its Back button wired to return here instead of to /config's
    collection picker. Restore Collection can't be driven from a button
    click at all -- Discord doesn't allow attaching a file upload in
    response to a component interaction -- so it points at running
    `/database restore` directly, the same way the Setup Wizard's
    "Import Existing Backup" option points at `/maintenance import` for the exact
    same platform reason.
    """
    database = bot.suggestion_service.get_database(database_id)
    if database is None or database.guild_id != guild_id:
        await interaction.response.edit_message(content="That collection no longer exists.", view=None)
        return

    collection_name = format_collection_display(
        _resolve_collection_name(
            bot.suggestion_service, database, interaction.guild, bot.suggestion_database_configuration_repository
        )
    )

    async def on_back_to_menu(back_interaction: discord.Interaction) -> None:
        await show_database_management_menu(back_interaction, bot, guild_id, database_id)

    async def on_action_chosen(action_interaction: discord.Interaction, action: str) -> None:
        if action == "move":
            await start_database_move(action_interaction, bot, guild_id, database_id)
        elif action == "edit":
            await send_config_database_settings_menu(action_interaction, bot, guild_id, database_id, on_back_to_menu)
        elif action == "backup":
            await start_database_backup(action_interaction, bot, guild_id, database_id)
        elif action == "restore":
            await action_interaction.response.edit_message(
                content=(
                    f'To restore "{collection_name}", run `/database restore` directly and choose Merge or '
                    "Replace, then select an existing local backup or upload one -- Discord doesn't allow "
                    "attaching a file from inside this menu."
                ),
                view=None,
            )
        elif action == "refresh_imdb":
            await show_imdb_refresh_scope_selection(action_interaction, bot, guild_id, database_id)
        elif action == "recover_imdb":
            await show_imdb_recovery_scope_selection(action_interaction, bot, guild_id, database_id)
        elif action == "reset":
            await start_database_reset(action_interaction, bot, guild_id, database_id)
        else:
            await start_database_remove(action_interaction, bot, guild_id, database_id)

    async def on_cancel(cancel_interaction: discord.Interaction) -> None:
        # UX Polish: matches every sibling Cancel confirmation's "X
        # cancelled. No changes were made." pattern (e.g. /database add's
        # "Collection creation cancelled...", /database move's "Move
        # cancelled...") -- this was previously the one that dropped the
        # leading clause naming what was cancelled.
        await cancel_interaction.response.edit_message(
            content="Collection management cancelled. No changes were made.", view=None
        )

    view = CollectionManagementMenuView(on_action_chosen, on_cancel)
    await interaction.response.edit_message(
        content=f'**Manage "{collection_name}"**\n\nChoose an action.', view=view
    )


async def handle_database_manage(interaction: discord.Interaction, bot: "WatchPartyBot") -> None:
    """/database manage: the guided workflow -- pick a collection, then
    choose what to do with it. Release UX & Command Surface Cleanup:
    this is now the only way to move, back up, reset, or remove a
    collection -- those four no longer have their own top-level slash
    commands (see DatabaseGroup's own docstring for why, and for
    /database restore's one necessary exception).
    """
    guild_permission_service = resolve_permission_service(interaction.guild_id, bot.guild_configuration_repository)
    if guild_permission_service.wash_crew_role_id is None:
        await interaction.response.send_message(
            "WASH Crew permissions have not been configured. Set WASH_CREW_ROLE_ID before using this command.",
            ephemeral=True,
        )
        return
    if not guild_permission_service.is_wash_crew(interaction.user):
        await interaction.response.send_message(
            "You need the WASH Crew role to manage a collection.", ephemeral=True
        )
        return

    guild_id = interaction.guild_id
    if guild_id is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    databases = bot.suggestion_service.list_databases(guild_id)
    if not databases:
        await interaction.response.send_message("No collections exist in this server yet. Create one with `/database add`.", ephemeral=True)
        return

    async def on_database_selected(select_interaction: discord.Interaction, database_id: int) -> None:
        await show_database_management_menu(select_interaction, bot, guild_id, database_id)

    options = build_database_admin_options(
        bot.suggestion_service, databases, interaction.guild, bot.suggestion_database_configuration_repository
    )
    view = DatabaseAdminSelectView(
        options, on_database_selected, custom_id="wpm_database_manage_select", placeholder="Choose a collection to manage..."
    )
    await interaction.response.send_message("Choose which collection to manage:", view=view, ephemeral=True)


# IMDb Metadata Refresh: how often a running refresh's progress message is
# actually re-edited on Discord -- every suggestion reports progress to
# perform_imdb_metadata_refresh's on_progress callback, but that callback
# only issues a real Discord edit at a collection boundary or after this
# many seconds have passed since the last edit, whichever comes first
# (Section 9: "do not edit... for every single suggestion").
IMDB_REFRESH_PROGRESS_EDIT_MIN_INTERVAL_SECONDS = 2.0

# A plain message's content is capped at 2000 characters by Discord; kept
# a little under that so the final summary always has headroom.
IMDB_REFRESH_SUMMARY_MAX_CONTENT_LENGTH = 1900

# Rate-limit friendliness: minimum gap between two suggestion-post edits
# (sync_suggestion_status_embed's message.edit call) in the SAME channel
# during a bulk IMDb Metadata Refresh. Live testing surfaced repeated
# Discord 429s on PATCH /channels/{id}/messages/{id} when many refreshed
# suggestions in one channel were resynced back-to-back -- discord.py's
# own retry-after handling already recovers from those, but pacing our
# side of it means we hit that path far less often. 1.25s sits in the
# middle of the 1.0-1.5s range Discord's own edit rate limit tolerates
# comfortably for a single channel.
IMDB_REFRESH_POST_EDIT_MIN_INTERVAL_SECONDS = 1.25


class _ChannelEditPacer:
    """Centralized pacing for the suggestion-post edits IMDb Metadata
    Refresh issues in bulk. Refresh processing is already strictly
    sequential (Section 6), so this only needs to insert a wait before an
    edit that would otherwise land too soon after the previous edit to
    the SAME channel -- Discord's message-edit rate limit is bucketed per
    channel, so edits to a different channel never need to wait on this.
    Deliberately scoped to this one workflow (not sync_suggestion_status_
    embed itself), since every other caller of that function edits at
    most one post per user action, never in bulk.
    """

    def __init__(
        self,
        *,
        min_interval_seconds: float,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self._min_interval_seconds = min_interval_seconds
        self._sleep = sleep
        self._clock = clock or (lambda: asyncio.get_event_loop().time())
        self._last_edit_at_by_channel: dict[int, float] = {}

    async def wait_for_turn(self, channel_id: int) -> None:
        last_edit_at = self._last_edit_at_by_channel.get(channel_id)
        if last_edit_at is not None:
            remaining = self._min_interval_seconds - (self._clock() - last_edit_at)
            if remaining > 0:
                await self._sleep(remaining)
        self._last_edit_at_by_channel[channel_id] = self._clock()


def _imdb_metadata_service_for(bot: "WatchPartyBot") -> ImdbMetadataService:
    """The one, already-configured ImdbMetadataService instance /add's
    own suggestion-input pipeline uses -- reused as-is (Section 5: no
    parallel metadata client), so the refresh workflow shares the exact
    same API key resolution and (in tests) the exact same injected
    fetch_json.
    """
    return bot.suggestion_input_service.imdb_metadata_service


async def show_imdb_refresh_scope_selection(
    interaction: discord.Interaction, bot: "WatchPartyBot", guild_id: int, database_id: int
) -> None:
    """IMDb Metadata Refresh, Section 2: choose Refresh This Collection,
    Refresh All Collections (active collections in this guild only), or
    go back to this collection's management menu.
    """
    database = bot.suggestion_service.get_database(database_id)
    if database is None or database.guild_id != guild_id:
        await interaction.response.edit_message(content="That collection no longer exists.", view=None)
        return

    collection_name = format_collection_display(
        _resolve_collection_name(
            bot.suggestion_service, database, interaction.guild, bot.suggestion_database_configuration_repository
        )
    )

    async def on_back(back_interaction: discord.Interaction) -> None:
        await show_database_management_menu(back_interaction, bot, guild_id, database_id)

    async def on_scope_chosen(scope_interaction: discord.Interaction, scope: str) -> None:
        if scope == REFRESH_SCOPE_THIS_COLLECTION:
            scope_databases = [database]
        else:
            scope_databases = resolve_refreshable_databases(
                bot.suggestion_service.list_databases(guild_id), guild_id=guild_id
            )
        await show_imdb_refresh_confirmation(
            scope_interaction, bot, guild_id, database_id, scope_databases, scope=scope
        )

    view = ImdbRefreshScopeSelectionView(on_scope_chosen, on_back)
    await interaction.response.edit_message(
        content=(
            f'**Refresh IMDb Metadata -- "{collection_name}"**\n\n'
            "Re-fetch IMDb-derived details (title, year, plot, poster, runtime, genres, IMDb rating, "
            "MPAA/content rating, director, and cast) for suggestions that already have a usable IMDb link. "
            "Existing WASH history and statuses are never changed. Choose a scope:"
        ),
        view=view,
    )


def build_imdb_refresh_confirmation_text(
    *, scope: str, database_names: List[str], eligible_count: int, suggestion_count: int
) -> str:
    """Section 3: the confirmation screen's body -- scope, collection
    count, and how many suggestions are actually eligible (have a usable
    IMDb identifier) out of the total considered, plus the required
    disclosures before any external request is made.
    """
    if scope == REFRESH_SCOPE_THIS_COLLECTION:
        collection_name = database_names[0] if database_names else "this collection"
        scope_line = f'Scope: Refresh This Collection -- "{collection_name}"'
    else:
        scope_line = (
            "Scope: Refresh All Collections -- every active collection in this server "
            f"({len(database_names)} collection{'s' if len(database_names) != 1 else ''})"
        )
    return "\n\n".join(
        [
            "**Refresh IMDb Metadata -- Confirm**",
            scope_line,
            f"Collections: {len(database_names)}",
            f"Suggestions eligible for refresh: {eligible_count} (of {suggestion_count} total considered)",
            (
                "This will make IMDb/OMDb requests for each eligible suggestion's already-linked title. "
                "Existing WASH history, statuses, votes, and Discord post references are never changed -- "
                "only already-persisted IMDb-derived details (title, year, plot, poster, runtime, genres, "
                "IMDb rating, MPAA/content rating, director, cast) are refreshed."
            ),
            "This may take some time depending on how many suggestions are eligible.",
        ]
    )


async def show_imdb_refresh_confirmation(
    interaction: discord.Interaction,
    bot: "WatchPartyBot",
    guild_id: int,
    origin_database_id: int,
    databases: List[SuggestionDatabase],
    *,
    scope: str,
) -> None:
    """IMDb Metadata Refresh, Section 3: require confirmation, showing
    counts computed with zero external requests (count_refreshable only
    ever reads already-persisted metadata), before Start Refresh can
    make a single IMDb/OMDb request.
    """
    refresh_service = ImdbMetadataRefreshService(bot.suggestion_service, _imdb_metadata_service_for(bot))
    eligible_total = 0
    suggestion_total = 0
    database_names: List[str] = []
    for database in databases:
        usable, total = refresh_service.count_refreshable(database.database_id)
        eligible_total += usable
        suggestion_total += total
        database_names.append(
            format_collection_display(
                _resolve_collection_name(
                    bot.suggestion_service, database, interaction.guild, bot.suggestion_database_configuration_repository
                )
            )
        )

    async def on_back(back_interaction: discord.Interaction) -> None:
        await show_imdb_refresh_scope_selection(back_interaction, bot, guild_id, origin_database_id)

    async def on_cancel(cancel_interaction: discord.Interaction) -> None:
        await cancel_interaction.response.edit_message(
            content="IMDb metadata refresh cancelled. No changes were made.", view=None
        )

    async def on_start(start_interaction: discord.Interaction) -> None:
        await perform_imdb_metadata_refresh(start_interaction, bot, databases)

    view = ImdbRefreshConfirmationView(on_start, on_back, on_cancel)
    text = build_imdb_refresh_confirmation_text(
        scope=scope, database_names=database_names, eligible_count=eligible_total, suggestion_count=suggestion_total
    )
    await interaction.response.edit_message(content=text, view=view)


def build_imdb_refresh_progress_text(progress: RefreshProgress) -> str:
    """Section 9's suggested progress fields, rendered as a compact block."""
    collection_line = f"Collections: {progress.collections_completed}/{progress.collections_total}"
    if progress.collections_completed < progress.collections_total:
        collection_line += f" (currently refreshing: {progress.current_collection_name})"
    return "\n".join(
        [
            "**Refreshing IMDb Metadata...**",
            "",
            collection_line,
            f"Suggestions: {progress.suggestions_processed}/{progress.suggestions_total}",
            f"Refreshed: {progress.refreshed}",
            f"Unchanged: {progress.unchanged}",
            f"Skipped: {progress.skipped}",
            f"Failed: {progress.failed}",
            f"Suggestion posts updated: {progress.posts_updated}",
            f"Suggestion post sync failures: {progress.post_sync_failures}",
        ]
    )


def _build_imdb_refresh_totals_lines(summary: ImdbMetadataRefreshSummary) -> List[str]:
    return [
        "**IMDb Metadata Refresh Complete**",
        "",
        f"Collections processed: {summary.collections_processed}",
        f"Suggestions processed: {summary.processed}",
        f"Refreshed: {summary.refreshed}",
        f"Unchanged: {summary.unchanged}",
        f"Skipped: {summary.skipped}",
        f"Failed: {summary.failed}",
        f"Suggestion posts updated: {summary.posts_updated}",
        f"Suggestion post sync failures: {summary.post_sync_failures}",
    ]


def _build_imdb_refresh_breakdown_lines(summary: ImdbMetadataRefreshSummary) -> List[str]:
    lines = ["", "**Per-Collection Breakdown**"]
    for collection in summary.collections:
        lines.append(
            f"• {collection.database_name}: {collection.processed} processed, {collection.refreshed} refreshed, "
            f"{collection.unchanged} unchanged, {collection.skipped} skipped, {collection.failed} failed, "
            f"{collection.posts_updated} posts updated, {collection.post_sync_failures} post sync failures"
        )
    return lines


def build_imdb_refresh_summary_message(
    summary: ImdbMetadataRefreshSummary, *, multi_collection: bool
) -> tuple[str, Optional[str]]:
    """Section 10: the final ephemeral completion summary. For Refresh
    All Collections, also includes a concise per-collection breakdown --
    unless that would push the plain message content over Discord's
    2000-character limit, in which case the totals stay inline and the
    full per-collection breakdown is returned separately as
    attachment_text for the caller to send as a text file instead
    (never a truncated/invalid payload).

    Returns:
        (content, attachment_text) -- attachment_text is None unless the
        full breakdown had to be moved out of the message body.
    """
    totals_lines = _build_imdb_refresh_totals_lines(summary)
    if not multi_collection or not summary.collections:
        return "\n".join(totals_lines), None

    breakdown_lines = _build_imdb_refresh_breakdown_lines(summary)
    full_text = "\n".join(totals_lines + breakdown_lines)
    if len(full_text) <= IMDB_REFRESH_SUMMARY_MAX_CONTENT_LENGTH:
        return full_text, None

    attachment_text = "\n".join(breakdown_lines[2:])  # drop the leading blank line + header; the file stands alone.
    content = "\n".join(
        totals_lines
        + [
            "",
            f"Per-collection breakdown attached ({len(summary.collections)} collections) -- too long to show inline.",
        ]
    )
    return content, attachment_text


async def perform_imdb_metadata_refresh(
    interaction: discord.Interaction,
    bot: "WatchPartyBot",
    databases: List[SuggestionDatabase],
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """IMDb Metadata Refresh, Sections 6/8/9/10: run the refresh, posting
    bounded progress updates and a final ephemeral summary. Stays
    ephemeral throughout -- Start Refresh's own click is the interaction
    this edits in place, so nothing here is ever visible to anyone but
    the WASH Crew member who started it.

    `sleep` is injectable purely for tests (see _ChannelEditPacer) --
    production callers always use the real asyncio.sleep default.
    """
    await interaction.response.edit_message(
        content="**Refreshing IMDb Metadata...**\n\nStarting -- progress will update periodically.", view=None
    )

    refresh_service = ImdbMetadataRefreshService(bot.suggestion_service, _imdb_metadata_service_for(bot))
    # Anchored to "now" (not 0.0) so the very first per-suggestion
    # progress event is correctly throttled too -- asyncio's loop clock
    # is monotonic from an arbitrary starting point (often process
    # start), not from zero, so a 0.0 starting point would make that
    # first check always look like "well over the interval" and issue
    # an extra, unwanted edit immediately.
    last_edit_at = asyncio.get_event_loop().time()
    post_edit_pacer = _ChannelEditPacer(min_interval_seconds=IMDB_REFRESH_POST_EDIT_MIN_INTERVAL_SECONDS, sleep=sleep)

    async def on_progress(progress: RefreshProgress) -> None:
        nonlocal last_edit_at
        loop = asyncio.get_event_loop()
        now = loop.time()
        if not progress.collection_boundary and (now - last_edit_at) < IMDB_REFRESH_PROGRESS_EDIT_MIN_INTERVAL_SECONDS:
            return
        last_edit_at = now
        try:
            await interaction.edit_original_response(content=build_imdb_refresh_progress_text(progress))
        except discord.HTTPException:
            logger.warning("Could not update IMDb metadata refresh progress message", exc_info=True)

    async def post_sync(watch_item: WatchItem) -> Optional[bool]:
        # Only pace when an edit will actually be attempted -- mirrors
        # sync_suggestion_status_embed's own NO_POST guard, so suggestions
        # with no existing post never incur a needless wait.
        if watch_item.channel_id is not None and watch_item.message_id is not None:
            await post_edit_pacer.wait_for_turn(watch_item.channel_id)
        result = await sync_suggestion_status_embed(bot, watch_item)
        if result is SuggestionPostSyncResult.NO_POST:
            return None
        return result is SuggestionPostSyncResult.SYNCED

    summary = await refresh_service.refresh_databases(databases, on_progress=on_progress, post_sync=post_sync)

    content, attachment_text = build_imdb_refresh_summary_message(summary, multi_collection=len(databases) > 1)
    try:
        if attachment_text is not None:
            report_file = discord.File(io.BytesIO(attachment_text.encode("utf-8")), filename="imdb_refresh_report.txt")
            await interaction.edit_original_response(content=content, attachments=[report_file])
        else:
            await interaction.edit_original_response(content=content)
    except discord.HTTPException:
        logger.warning("Could not post the final IMDb metadata refresh summary", exc_info=True)


# IMDb Metadata Recovery: matches "The Thing 1982" or "The Thing (1982)"
# in Search Again's manual query -- a trailing 4-digit year (optionally
# parenthesized), split from the title text before it. No year is
# assumed if the text doesn't end that way.
_RECOVERY_QUERY_YEAR_PATTERN = re.compile(r"^(.*?)\s*\(?\s*((?:19|20)\d{2})\)?\s*$")


def parse_recovery_search_query(text: str) -> tuple[str, Optional[int]]:
    """Split a Search Again query into (title, year) -- Section 5's
    example: "The Thing 1982" and "The Thing (1982)" both split into
    ("The Thing", 1982). No trailing year at all leaves it unset.
    """
    cleaned = (text or "").strip()
    match = _RECOVERY_QUERY_YEAR_PATTERN.match(cleaned)
    if match and match.group(1).strip():
        return match.group(1).strip(), int(match.group(2))
    return cleaned, None


def build_imdb_recovery_match_select_options(matches: list[ImdbSearchMatch]) -> List[discord.SelectOption]:
    """Build the multi-candidate selection list's options (Section 4) --
    through the same safe builders as every other option list in this
    project, so a long title or more than 25 candidates can never
    produce an invalid payload.
    """
    options = [
        build_safe_select_option(
            f"{match.title} ({match.year})" if match.year else match.title,
            match.imdb_id,
            description=match.media_type.title() if match.media_type else None,
        )
        for match in matches
    ]
    return cap_select_options(options)


def build_imdb_recovery_progress_header(*, processed: int, total: int) -> str:
    """Section 9's "Recovering 3 / 12" line -- shown atop every per-
    suggestion screen. Recovery is inherently interactive (every match
    needs an explicit decision), so there is no separate throttled
    progress-edit mechanism the way Refresh IMDb Metadata needs one --
    each screen IS the progress update, and screens only ever appear one
    at a time, paced naturally by how quickly a person can review them.
    """
    if total == 0:
        return "**Recover Missing IMDb Links**"
    current = min(processed + 1, total)
    return f"**Recover Missing IMDb Links**\n\nRecovering {current} / {total}"


def build_imdb_recovery_candidate_screen_text(
    *, header: str, watch_item: WatchItem, collection_name: str, search: CandidateSearch
) -> str:
    """Section 4/5's Crew Review screen body -- branches on whether the
    search technically failed, found nothing, found one high-confidence
    match, or found several plausible ones.
    """
    year_text = str(watch_item.release_year) if watch_item.release_year else "year unknown"
    suggestion_line = f'Suggestion: "{watch_item.title}" ({year_text}) -- {collection_name}'

    if search.technical_failure:
        return "\n\n".join(
            [
                header,
                suggestion_line,
                f"⚠️ Search failed: {search.error_message or 'an unexpected error occurred.'}",
                "Use Search Again to retry, Skip to leave this suggestion unmatched, or Cancel Recovery to stop.",
            ]
        )

    if not search.matches:
        return "\n\n".join(
            [
                header,
                suggestion_line,
                "No IMDb matches were found for this title.",
                (
                    "Use Search Again to try a different title/year, Skip to leave this suggestion unmatched "
                    "(it will still appear in a future recovery scan), or Cancel Recovery to stop."
                ),
            ]
        )

    note = f"⚠️ {search.year_mismatch_note}" if search.year_mismatch_note else None

    if len(search.matches) == 1:
        match = search.matches[0]
        match_line = f"**{match.title}**" + (f" ({match.year})" if match.year else "")
        if match.media_type:
            match_line += f" -- {match.media_type.title()}"
        parts = [header, suggestion_line, "Proposed match:", match_line]
        if note:
            parts.append(note)
        parts.append(
            "Accept to save this IMDb link and refresh this suggestion's metadata immediately, Skip to leave "
            "it unmatched, Search Again to try a different title/year, or Cancel Recovery to stop."
        )
        return "\n\n".join(parts)

    shown = min(len(search.matches), SELECT_MAX_OPTIONS)
    if shown == len(search.matches):
        match_count_line = f"{shown} possible matches were found."
    else:
        match_count_line = f"{len(search.matches)} possible matches were found -- showing the top {shown}."
    parts = [header, suggestion_line, match_count_line]
    if note:
        parts.append(note)
    parts.append("Choose one below, or use Skip, Search Again, or Cancel Recovery.")
    return "\n\n".join(parts)


def _build_imdb_recovery_totals_lines(summary: ImdbMetadataRecoverySummary) -> List[str]:
    return [
        "**Recover Missing IMDb Links Complete**",
        "",
        f"Collections processed: {summary.collections_processed}",
        f"Suggestions processed: {summary.processed}",
        f"Matched: {summary.matched}",
        f"Skipped: {summary.skipped}",
        f"Failed: {summary.failed}",
        f"Cancelled: {summary.cancelled}",
        f"Suggestion posts updated: {summary.posts_updated}",
        f"Suggestion post sync failures: {summary.post_sync_failures}",
    ]


def _build_imdb_recovery_breakdown_lines(summary: ImdbMetadataRecoverySummary) -> List[str]:
    lines = ["", "**Per-Collection Breakdown**"]
    for collection in summary.collections:
        lines.append(
            f"• {collection.database_name}: {collection.processed} processed, {collection.matched} matched, "
            f"{collection.skipped} skipped, {collection.failed} failed, {collection.cancelled} cancelled, "
            f"{collection.posts_updated} posts updated, {collection.post_sync_failures} post sync failures"
        )
    return lines


def build_imdb_recovery_summary_message(
    summary: ImdbMetadataRecoverySummary, *, multi_collection: bool
) -> tuple[str, Optional[str]]:
    """Section 9's final ephemeral completion summary -- mirrors IMDb
    Metadata Refresh's own build_imdb_refresh_summary_message exactly
    (totals inline; for Recover All Collections, a per-collection
    breakdown too, moved to an attached text file instead of shown
    inline if it would push the message over Discord's length limit).

    Returns:
        (content, attachment_text) -- attachment_text is None unless the
        full breakdown had to be moved out of the message body.
    """
    totals_lines = _build_imdb_recovery_totals_lines(summary)
    if not multi_collection or not summary.collections:
        return "\n".join(totals_lines), None

    breakdown_lines = _build_imdb_recovery_breakdown_lines(summary)
    full_text = "\n".join(totals_lines + breakdown_lines)
    if len(full_text) <= IMDB_REFRESH_SUMMARY_MAX_CONTENT_LENGTH:
        return full_text, None

    attachment_text = "\n".join(breakdown_lines[2:])  # drop the leading blank line + header; the file stands alone.
    content = "\n".join(
        totals_lines
        + [
            "",
            f"Per-collection breakdown attached ({len(summary.collections)} collections) -- too long to show inline.",
        ]
    )
    return content, attachment_text


async def show_imdb_recovery_scope_selection(
    interaction: discord.Interaction, bot: "WatchPartyBot", guild_id: int, database_id: int
) -> None:
    """IMDb Metadata Recovery, Section 2: choose Recover This Collection,
    Recover All Collections (active collections in this guild only), or
    go back to this collection's management menu.
    """
    database = bot.suggestion_service.get_database(database_id)
    if database is None or database.guild_id != guild_id:
        await interaction.response.edit_message(content="That collection no longer exists.", view=None)
        return

    collection_name = format_collection_display(
        _resolve_collection_name(
            bot.suggestion_service, database, interaction.guild, bot.suggestion_database_configuration_repository
        )
    )

    async def on_back(back_interaction: discord.Interaction) -> None:
        await show_database_management_menu(back_interaction, bot, guild_id, database_id)

    async def on_scope_chosen(scope_interaction: discord.Interaction, scope: str) -> None:
        if scope == RECOVERY_SCOPE_THIS_COLLECTION:
            scope_databases = [database]
        else:
            scope_databases = resolve_recoverable_databases(
                bot.suggestion_service.list_databases(guild_id), guild_id=guild_id
            )
        await show_imdb_recovery_confirmation(
            scope_interaction, bot, guild_id, database_id, scope_databases, scope=scope
        )

    view = ImdbRecoveryScopeSelectionView(on_scope_chosen, on_back)
    await interaction.response.edit_message(
        content=(
            f'**Recover Missing IMDb Links -- "{collection_name}"**\n\n'
            "Find suggestions that don't have a usable IMDb link yet, search OMDb for the correct match, and -- "
            "only once you approve a match -- save it and immediately refresh that suggestion's metadata. "
            "Existing WASH history and statuses are never changed. Choose a scope:"
        ),
        view=view,
    )


def build_imdb_recovery_confirmation_text(
    *, scope: str, database_names: List[str], missing_count: int, suggestion_count: int
) -> str:
    """Section 3-equivalent confirmation screen's body for Recovery --
    scope, collection count, and how many suggestions are actually
    missing a usable IMDb link out of how many were considered, plus the
    required disclosures before any external request is made.
    """
    if scope == RECOVERY_SCOPE_THIS_COLLECTION:
        collection_name = database_names[0] if database_names else "this collection"
        scope_line = f'Scope: Recover This Collection -- "{collection_name}"'
    else:
        scope_line = (
            "Scope: Recover All Collections -- every active collection in this server "
            f"({len(database_names)} collection{'s' if len(database_names) != 1 else ''})"
        )
    return "\n\n".join(
        [
            "**Recover Missing IMDb Links -- Confirm**",
            scope_line,
            f"Collections: {len(database_names)}",
            f"Suggestions missing a usable IMDb link: {missing_count} (of {suggestion_count} total considered)",
            (
                "This will search OMDb for each of those suggestions. Every proposed match must be explicitly "
                "approved before anything is saved -- nothing is linked automatically, and an existing IMDb "
                "identifier is never overwritten. Existing WASH history, statuses, votes, and Discord post "
                "references are never changed."
            ),
            "This requires your attention throughout -- each proposed match needs a decision.",
        ]
    )


async def show_imdb_recovery_confirmation(
    interaction: discord.Interaction,
    bot: "WatchPartyBot",
    guild_id: int,
    origin_database_id: int,
    databases: List[SuggestionDatabase],
    *,
    scope: str,
) -> None:
    """IMDb Metadata Recovery's confirmation screen: require confirmation,
    showing counts computed with zero external requests (count_missing
    only ever reads already-persisted metadata), before Start Recovery
    can make a single OMDb search.
    """
    recovery_service = ImdbMetadataRecoveryService(bot.suggestion_service, _imdb_metadata_service_for(bot))
    missing_total = 0
    suggestion_total = 0
    database_names: List[str] = []
    for database in databases:
        missing, total = recovery_service.count_missing(database.database_id)
        missing_total += missing
        suggestion_total += total
        database_names.append(
            format_collection_display(
                _resolve_collection_name(
                    bot.suggestion_service, database, interaction.guild, bot.suggestion_database_configuration_repository
                )
            )
        )

    async def on_back(back_interaction: discord.Interaction) -> None:
        await show_imdb_recovery_scope_selection(back_interaction, bot, guild_id, origin_database_id)

    async def on_cancel(cancel_interaction: discord.Interaction) -> None:
        await cancel_interaction.response.edit_message(
            content="IMDb link recovery cancelled. No changes were made.", view=None
        )

    async def on_start(start_interaction: discord.Interaction) -> None:
        await start_imdb_recovery_session(start_interaction, bot, guild_id, databases)

    view = ImdbRecoveryConfirmationView(on_start, on_back, on_cancel)
    text = build_imdb_recovery_confirmation_text(
        scope=scope, database_names=database_names, missing_count=missing_total, suggestion_count=suggestion_total
    )
    await interaction.response.edit_message(content=text, view=view)


async def start_imdb_recovery_session(
    interaction: discord.Interaction, bot: "WatchPartyBot", guild_id: int, databases: List[SuggestionDatabase]
) -> None:
    """IMDb Metadata Recovery, Sections 4-9: walk through every
    suggestion missing a usable IMDb identifier, one at a time, only ever
    saving a match once WASH Crew explicitly approves it.

    Unlike perform_imdb_metadata_refresh's fully automated batch loop,
    every step here is its own separate Discord interaction (a button
    click, a select, or a modal submit) -- so there is no long-running
    loop or progress-edit throttling to manage here; each screen IS the
    progress update, naturally paced by how quickly a person can review
    it. Only this function's own first transition (Start Recovery's
    click acknowledging immediately, then rendering the first real
    screen within that same interaction) needs edit_original_response
    instead of response.edit_message, exactly like
    perform_imdb_metadata_refresh's own initial "Starting..." ack --
    every other screen here is a fresh interaction and uses
    response.edit_message directly, only once, as normal.
    """
    await interaction.response.edit_message(
        content="**Recovering Missing IMDb Links...**\n\nLooking for suggestions to recover.", view=None
    )

    recovery_service = ImdbMetadataRecoveryService(bot.suggestion_service, _imdb_metadata_service_for(bot))
    collections: List[CollectionRecoverySummary] = []
    collections_by_id: dict[int, CollectionRecoverySummary] = {}
    queue: List[WatchItem] = []
    for database in databases:
        collection_summary = CollectionRecoverySummary(database_id=database.database_id, database_name=database.name)
        collections.append(collection_summary)
        collections_by_id[database.database_id] = collection_summary
        queue.extend(recovery_service.missing_suggestions(database.database_id))

    summary = ImdbMetadataRecoverySummary(collections=collections)
    suggestions_total = len(queue)
    fetch_cache: dict[str, ImdbTitleResult] = {}
    state: dict[str, object] = {"queue": queue, "processed": 0, "current_search": None}

    async def post_sync(watch_item: WatchItem) -> Optional[bool]:
        result = await sync_suggestion_status_embed(bot, watch_item)
        if result is SuggestionPostSyncResult.NO_POST:
            return None
        return result is SuggestionPostSyncResult.SYNCED

    async def render_screen(
        target_interaction: discord.Interaction,
        *,
        content: str,
        view: Optional[discord.ui.View],
        first_render: bool,
        attachments: Optional[List[discord.File]] = None,
    ) -> None:
        try:
            if first_render:
                if attachments is not None:
                    await target_interaction.edit_original_response(content=content, view=view, attachments=attachments)
                else:
                    await target_interaction.edit_original_response(content=content, view=view)
            elif attachments is not None:
                await target_interaction.response.edit_message(content=content, view=view, attachments=attachments)
            else:
                await target_interaction.response.edit_message(content=content, view=view)
        except discord.HTTPException:
            logger.warning("Could not update IMDb metadata recovery screen", exc_info=True)

    async def show_final_summary(target_interaction: discord.Interaction, *, first_render: bool) -> None:
        content, attachment_text = build_imdb_recovery_summary_message(summary, multi_collection=len(databases) > 1)
        attachments = None
        if attachment_text is not None:
            attachments = [discord.File(io.BytesIO(attachment_text.encode("utf-8")), filename="imdb_recovery_report.txt")]
        await render_screen(target_interaction, content=content, view=None, first_render=first_render, attachments=attachments)

    async def render_candidate_screen(
        target_interaction: discord.Interaction, watch_item: WatchItem, search: CandidateSearch, *, first_render: bool
    ) -> None:
        state["current_search"] = search
        header = build_imdb_recovery_progress_header(processed=state["processed"], total=suggestions_total)
        collection_name = collections_by_id[watch_item.database_id].database_name
        text = build_imdb_recovery_candidate_screen_text(
            header=header, watch_item=watch_item, collection_name=collection_name, search=search
        )

        if search.technical_failure or not search.matches:
            view = ImdbRecoveryNoMatchView(on_search_again_clicked, on_skip, on_cancel_recovery)
        elif len(search.matches) == 1:
            view = ImdbRecoveryConfirmMatchView(on_accept, on_skip, on_search_again_clicked, on_cancel_recovery)
        else:
            options = build_imdb_recovery_match_select_options(list(search.matches))
            view = ImdbRecoveryMatchSelectionView(
                on_match_selected, on_skip, on_search_again_clicked, on_cancel_recovery, options=options
            )

        await render_screen(target_interaction, content=text, view=view, first_render=first_render)

    async def show_next_or_finish(target_interaction: discord.Interaction, *, first_render: bool) -> None:
        if not state["queue"]:
            await show_final_summary(target_interaction, first_render=first_render)
            return
        watch_item = state["queue"][0]
        search = await recovery_service.find_candidates(watch_item.title, watch_item.release_year)
        await render_candidate_screen(target_interaction, watch_item, search, first_render=first_render)

    async def on_accept(target_interaction: discord.Interaction) -> None:
        watch_item = state["queue"][0]
        search: CandidateSearch = state["current_search"]
        match = search.matches[0]
        outcome = await recovery_service.accept_match(watch_item, match, fetch_cache=fetch_cache, post_sync=post_sync)
        collections_by_id[watch_item.database_id].record(outcome)
        state["queue"].pop(0)
        state["processed"] += 1
        await show_next_or_finish(target_interaction, first_render=False)

    async def on_skip(target_interaction: discord.Interaction) -> None:
        watch_item = state["queue"][0]
        search: Optional[CandidateSearch] = state["current_search"]
        if search is not None and search.technical_failure:
            outcome = RecoverySuggestionOutcome(
                watch_item.id,
                watch_item.database_id,
                RecoveryResult.FAILED,
                detail=search.error_message or "The search failed.",
            )
        else:
            outcome = RecoverySuggestionOutcome(
                watch_item.id, watch_item.database_id, RecoveryResult.SKIPPED, detail="Skipped by WASH Crew."
            )
        collections_by_id[watch_item.database_id].record(outcome)
        state["queue"].pop(0)
        state["processed"] += 1
        await show_next_or_finish(target_interaction, first_render=False)

    async def on_search_again_clicked(target_interaction: discord.Interaction) -> None:
        watch_item = state["queue"][0]
        default_query = (
            watch_item.title
            if watch_item.release_year is None
            else f"{watch_item.title} ({watch_item.release_year})"
        )
        await target_interaction.response.send_modal(
            ImdbRecoverySearchAgainModal(on_search_again_submitted, default_query=default_query)
        )

    async def on_search_again_submitted(target_interaction: discord.Interaction, raw_query: Optional[str]) -> None:
        watch_item = state["queue"][0]
        query_title, query_year = parse_recovery_search_query(raw_query or watch_item.title)
        search = await recovery_service.find_candidates(query_title, query_year)
        await render_candidate_screen(target_interaction, watch_item, search, first_render=False)

    async def on_match_selected(target_interaction: discord.Interaction, imdb_id: str) -> None:
        watch_item = state["queue"][0]
        search: CandidateSearch = state["current_search"]
        chosen = next((match for match in search.matches if match.imdb_id == imdb_id), None)
        if chosen is None:
            await render_candidate_screen(target_interaction, watch_item, search, first_render=False)
            return
        narrowed = CandidateSearch(
            matches=(chosen,),
            year_mismatch_note=search.year_mismatch_note,
            searched_title=search.searched_title,
            searched_year=search.searched_year,
        )
        await render_candidate_screen(target_interaction, watch_item, narrowed, first_render=False)

    async def on_cancel_recovery(target_interaction: discord.Interaction) -> None:
        for watch_item in state["queue"]:
            outcome = RecoverySuggestionOutcome(
                watch_item.id, watch_item.database_id, RecoveryResult.CANCELLED, detail="Recovery was cancelled."
            )
            collections_by_id[watch_item.database_id].record(outcome)
        state["queue"] = []
        await show_final_summary(target_interaction, first_render=False)

    await show_next_or_finish(interaction, first_render=True)


def parse_watch_party_schedule_time(value: str) -> datetime:
    """Parse a watch party's scheduled date/time into a UTC-aware datetime.

    Accepts ISO 8601-style date/time text (e.g. "2026-08-01 20:00" or
    "2026-08-01T20:00:00"). A value with no UTC offset is interpreted as
    UTC, matching how every other scheduled time in WASH (e.g.
    VoteRound.closes_at) is stored and compared internally -- there is no
    per-guild scheduling timezone configured yet.

    Args:
        value: The raw "when" command option text.

    Returns:
        A timezone-aware datetime in UTC.

    Raises:
        ValueError: If value is blank or not a parseable date/time.
    """
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValueError("A scheduled date and time is required, e.g. '2026-08-01 20:00'.")

    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError as exc:
        raise ValueError(
            f"'{cleaned}' isn't a valid date/time. Use a format like '2026-08-01 20:00'."
        ) from exc

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_watched_date(value: str) -> date:
    """Parse the Watched button's confirmation modal date into a plain
    calendar date (Watched Button & Archive Workflow).

    Deliberately a bare date, never a time -- WASH only ever records the
    date a suggestion was watched, matching journey.watch_dates'
    existing date-only field, and mirroring format_won_date's own
    date-only convention for Vote Winners.

    Args:
        value: The raw modal field text.

    Returns:
        The parsed date.

    Raises:
        ValueError: If value is blank, not a parseable date, or in the future.
    """
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValueError(f"A watched date is required, e.g. '{date.today().isoformat()}'.")

    try:
        parsed = date.fromisoformat(cleaned)
    except ValueError as exc:
        raise ValueError(
            f"'{cleaned}' isn't a valid date. Use the format YYYY-MM-DD, e.g. '{date.today().isoformat()}'."
        ) from exc

    if parsed > date.today():
        raise ValueError("The watched date can't be in the future.")
    return parsed


_DISCORD_TIMESTAMP_PATTERN = re.compile(r"^<t:(-?\d+)(?::[tTdDfFR])?>$")


def parse_discord_timestamp_vote_end_time(value: str, *, now: Optional[datetime] = None) -> datetime:
    """Parse the Custom Time modal's Discord-native timestamp into a UTC-aware datetime.

    Accepts Discord's own `<t:unix>` and `<t:unix:STYLE>` markup (STYLE
    one of t/T/d/D/f/F/R) -- the exact text produced when a member types
    "@time" in a normal Discord message box, picks a date/time, and
    copies the generated timestamp. The unix epoch it carries is already
    an absolute, timezone-independent instant, so no separate timezone
    configuration is needed to interpret it; it converts directly to a
    UTC-aware datetime for storage/scheduling exactly like every other
    path into VoteRound.closes_at.

    Raises:
        ValueError: If the text isn't a well-formed Discord timestamp, or
            the moment it encodes isn't in the future.
    """
    cleaned = (value or "").strip()
    match = _DISCORD_TIMESTAMP_PATTERN.fullmatch(cleaned)
    if not match:
        raise ValueError(
            "That doesn't look like a Discord timestamp. Type @time in any normal Discord message box, "
            "select a date/time, then copy the generated timestamp here (e.g. <t:1785639600:F>)."
        )

    try:
        parsed = datetime.fromtimestamp(int(match.group(1)), tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError("That Discord timestamp isn't a valid date/time.") from exc

    current_time = now if now is not None else datetime.now(timezone.utc)
    if parsed <= current_time:
        raise ValueError("The new closing time must be in the future.")
    return parsed


def build_schedule_watch_party_confirmation(
    watch_party: WatchParty, watch_item: Optional[WatchItem]
) -> str:
    """Build the public confirmation for a newly scheduled watch party."""
    title = watch_item.title if watch_item is not None else f"watch item #{watch_party.watch_item_id}"
    return (
        f'Watch party #{watch_party.id} scheduled for "{title}".\n'
        f"Starts: {format_datetime_for_display(watch_party.scheduled_at)}"
    )


def build_watch_party_select_options(
    watch_parties: List[WatchParty], suggestion_service: Optional[SuggestionService]
) -> List[Tuple[int, str, str]]:
    """Build (id, label, description) options for WatchPartySelectView.

    label is the watch item's title (falling back to a "Watch item #N"
    placeholder if it can no longer be resolved, or if no
    suggestion_service was given at all); description is the scheduled
    date/time as plain text -- SelectOption fields are rendered as
    literal text by Discord, so this deliberately does not use
    format_datetime_for_display's `<t:...>` markup, which would show up
    unparsed in the picker.

    Args:
        watch_parties: The watch parties to build options for.
        suggestion_service: Used to resolve each watch item's title.

    Returns:
        Options ordered soonest-scheduled first.
    """
    ordered = sorted(watch_parties, key=lambda watch_party: watch_party.scheduled_at)
    options: List[Tuple[int, str, str]] = []
    for watch_party in ordered:
        watch_item = (
            suggestion_service.get_suggestion(watch_party.watch_item_id)
            if suggestion_service is not None
            else None
        )
        title = watch_item.title if watch_item is not None else f"Watch item #{watch_party.watch_item_id}"
        scheduled_text = watch_party.scheduled_at.strftime("%Y-%m-%d %H:%M UTC")
        options.append((watch_party.id, title, f"Scheduled {scheduled_text}"))
    return options


def build_reschedule_watch_party_confirmation(watch_party: WatchParty) -> str:
    """Build the public confirmation for a rescheduled watch party."""
    return (
        f"Watch party #{watch_party.id} rescheduled.\n"
        f"Starts: {format_datetime_for_display(watch_party.scheduled_at)}"
    )


def build_watch_party_status_text(watch_party: WatchParty, watch_item: Optional[WatchItem]) -> str:
    """Build the /watch_party_status response for one watch party.

    Args:
        watch_party: The watch party to report on.
        watch_item: The Watch Item being watched, if it could still be
            resolved. None if it was removed after being scheduled -- the
            watch party is still identified by its own ID rather than
            failing to report status at all.

    Returns:
        Watch item title, current status, Discord-formatted scheduled
        time, and an IMDb link when one is on file.
    """
    title = watch_item.title if watch_item is not None else f"Watch item #{watch_party.watch_item_id}"
    lines = [
        # UX Polish: "Watch party #N", matching every other confirmation
        # that names a specific scheduled watch party by ID (schedule/
        # reschedule/cancel) -- "Watch Party" (Title Case) is reserved
        # for the community/role/membership concept elsewhere.
        f"Watch party #{watch_party.id}",
        f"Watch Item: {title}",
        f"Status: {watch_party.status.value.capitalize()}",
        f"Scheduled for: {format_datetime_for_display(watch_party.scheduled_at)}",
    ]

    if watch_item is not None:
        imdb_url = watch_item.metadata_ids.get(MetadataProvider.IMDB)
        if imdb_url:
            lines.append(f"IMDb: {imdb_url}")

    return "\n".join(lines)


def perform_schedule_watch_party(
    watch_party_service: WatchPartyService,
    suggestion_service: SuggestionService,
    user: object,
    wash_crew_role_id: Optional[int],
    guild_id: Optional[int],
    channel_id: Optional[int],
    watch_item_id: int,
    when: str,
) -> tuple[str, bool, Optional[WatchParty]]:
    """Core logic for /schedule_watch_party, kept free of Discord objects except `user`.

    Args:
        watch_party_service: The service to schedule the watch party through.
        suggestion_service: Used to resolve the watch item for the confirmation text.
        user: The member invoking the command.
        wash_crew_role_id: The configured WASH Crew role ID, or None if unconfigured.
        guild_id: The Discord guild the command was run in, or None outside a guild.
        channel_id: The Discord channel or thread the command was run in --
            used as the watch party's reminder channel.
        watch_item_id: The watch item to schedule a watch party for.
        when: The raw "when" option text; parsed via parse_watch_party_schedule_time.

    Returns:
        A (message, ephemeral, watch_party) tuple. watch_party is set only
        on success, so the caller can schedule its reminder job without a
        redundant lookup. The confirmation is public (not ephemeral) --
        scheduling a watch party is community-relevant, matching
        /start_vote's equivalent announcement.
    """
    if wash_crew_role_id is None:
        return (
            "WASH Crew permissions have not been configured. "
            "Set WASH_CREW_ROLE_ID before using this command.",
            True,
            None,
        )

    if not is_wash_crew_member(user, wash_crew_role_id):
        return "You need the WASH Crew role to schedule a watch party.", True, None

    if guild_id is None:
        return "This command can only be used in a server.", True, None

    try:
        scheduled_at = parse_watch_party_schedule_time(when)
    except ValueError as exc:
        return str(exc), True, None

    result = watch_party_service.schedule_watch_party(
        watch_item_id=watch_item_id,
        scheduled_at=scheduled_at,
        guild_id=guild_id,
        channel_id=channel_id,
    )
    if not result.success:
        return result.message, True, None

    watch_item = suggestion_service.get_suggestion(watch_item_id)
    return (
        build_schedule_watch_party_confirmation(result.watch_party, watch_item),
        False,
        result.watch_party,
    )


async def handle_schedule_watch_party_completion(
    interaction: discord.Interaction,
    watch_party_service: WatchPartyService,
    suggestion_service: SuggestionService,
    wash_crew_role_id: Optional[int],
    watch_item_id: int,
    when: str,
    scheduler_service: Optional[SchedulerService] = None,
    guild_configuration_repository: Optional[GuildConfigurationRepository] = None,
) -> None:
    """Schedule a watch party and its reminder job.

    scheduler_service/guild_configuration_repository default to None so
    callers/tests that don't pass them keep working unchanged; passing
    None simply skips scheduling (see schedule_watch_party_reminder).
    """
    message, ephemeral, watch_party = perform_schedule_watch_party(
        watch_party_service=watch_party_service,
        suggestion_service=suggestion_service,
        user=interaction.user,
        wash_crew_role_id=wash_crew_role_id,
        guild_id=interaction.guild_id,
        channel_id=interaction.channel_id,
        watch_item_id=watch_item_id,
        when=when,
    )
    await interaction.response.send_message(message, ephemeral=ephemeral)
    if ephemeral or watch_party is None:
        return

    # FR-021: schedule this watch party's reminder job now that it's
    # confirmed created and persisted, mirroring
    # handle_start_vote_completion's equivalent step for voting rounds.
    await schedule_watch_party_reminder(
        scheduler_service,
        watch_party,
        watch_party.guild_id,
        guild_configuration_repository=guild_configuration_repository,
    )


def perform_reschedule_watch_party(
    watch_party_service: WatchPartyService,
    user: object,
    wash_crew_role_id: Optional[int],
    watch_party_id: int,
    when: str,
) -> tuple[str, bool, Optional[WatchParty]]:
    """Core logic for /reschedule_watch_party, kept free of Discord objects except `user`.

    Args:
        watch_party_service: The service to reschedule the watch party through.
        user: The member invoking the command.
        wash_crew_role_id: The configured WASH Crew role ID, or None if unconfigured.
        watch_party_id: The watch party to reschedule.
        when: The raw new "when" option text; parsed via parse_watch_party_schedule_time.

    Returns:
        A (message, ephemeral, watch_party) tuple. watch_party is set only
        on success, so the caller can replace its reminder job without a
        redundant lookup.
    """
    if wash_crew_role_id is None:
        return (
            "WASH Crew permissions have not been configured. "
            "Set WASH_CREW_ROLE_ID before using this command.",
            True,
            None,
        )

    if not is_wash_crew_member(user, wash_crew_role_id):
        return "You need the WASH Crew role to reschedule a watch party.", True, None

    try:
        new_scheduled_at = parse_watch_party_schedule_time(when)
    except ValueError as exc:
        return str(exc), True, None

    result = watch_party_service.reschedule_watch_party(watch_party_id, new_scheduled_at)
    if not result.success:
        return result.message, True, None

    return build_reschedule_watch_party_confirmation(result.watch_party), False, result.watch_party


async def handle_reschedule_watch_party_completion(
    interaction: discord.Interaction,
    watch_party_service: WatchPartyService,
    wash_crew_role_id: Optional[int],
    when: str,
    scheduler_service: Optional[SchedulerService] = None,
    guild_configuration_repository: Optional[GuildConfigurationRepository] = None,
    suggestion_service: Optional[SuggestionService] = None,
) -> None:
    """Show a "which watch party?" picker, then reschedule the chosen one
    and replace its reminder job.

    Release Polish (Discord-native UX): watch_party_id is no longer a
    command parameter -- WASH Crew picks the target from a selector of
    currently scheduled watch parties instead of typing an internal ID.
    `when` still applies to whichever watch party is selected.

    scheduler_service/guild_configuration_repository/suggestion_service
    default to None so callers/tests that don't pass them keep working
    unchanged (suggestion_service absent just falls back to a
    "Watch item #N" label -- see build_watch_party_select_options).
    """
    if wash_crew_role_id is None:
        await interaction.response.send_message(
            "WASH Crew permissions have not been configured. Set WASH_CREW_ROLE_ID before using this command.",
            ephemeral=True,
        )
        return
    if not is_wash_crew_member(interaction.user, wash_crew_role_id):
        await interaction.response.send_message(
            "You need the WASH Crew role to reschedule a watch party.", ephemeral=True
        )
        return

    guild_id = interaction.guild_id
    if guild_id is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    scheduled = [
        watch_party
        for watch_party in watch_party_service.list_watch_parties(guild_id)
        if watch_party.status == WatchPartyStatus.SCHEDULED
    ]
    if not scheduled:
        await interaction.response.send_message("No watch parties are currently scheduled.", ephemeral=True)
        return

    async def on_select(select_interaction: discord.Interaction, watch_party_id: int) -> None:
        message, ephemeral, watch_party = perform_reschedule_watch_party(
            watch_party_service=watch_party_service,
            user=select_interaction.user,
            wash_crew_role_id=wash_crew_role_id,
            watch_party_id=watch_party_id,
            when=when,
        )
        await select_interaction.response.send_message(message, ephemeral=ephemeral)
        if ephemeral or watch_party is None:
            return

        # FR-021: replace the reminder job to reflect the new scheduled_at --
        # reschedule_watch_party_reminder cancels whatever job is currently
        # active for this watch party and schedules a fresh one, mirroring the
        # scheduler's documented rescheduling policy (see
        # docs/architecture/scheduler.md, "Cancellation & Rescheduling").
        await reschedule_watch_party_reminder(
            scheduler_service,
            watch_party,
            watch_party.guild_id,
            guild_configuration_repository=guild_configuration_repository,
        )

    options = build_watch_party_select_options(scheduled, suggestion_service)
    view = WatchPartySelectView(
        options,
        on_select,
        custom_id="wpm_reschedule_watch_party_select",
        placeholder="Choose a watch party to reschedule...",
    )
    await interaction.response.send_message("Choose which watch party to reschedule:", view=view, ephemeral=True)


def perform_cancel_watch_party(
    watch_party_service: WatchPartyService,
    user: object,
    wash_crew_role_id: Optional[int],
    watch_party_id: int,
) -> tuple[str, bool]:
    """Core logic for /cancel_watch_party, kept free of Discord objects except `user`.

    Args:
        watch_party_service: The service to cancel the watch party through.
        user: The member invoking the command.
        wash_crew_role_id: The configured WASH Crew role ID, or None if unconfigured.
        watch_party_id: The watch party to cancel.

    Returns:
        A (message, ephemeral) tuple. The confirmation is public (not
        ephemeral) on success -- a cancellation is community-relevant.
    """
    if wash_crew_role_id is None:
        return (
            "WASH Crew permissions have not been configured. "
            "Set WASH_CREW_ROLE_ID before using this command.",
            True,
        )

    if not is_wash_crew_member(user, wash_crew_role_id):
        return "You need the WASH Crew role to cancel a watch party.", True

    result = watch_party_service.cancel_watch_party(watch_party_id)
    return result.message, not result.success


async def handle_cancel_watch_party_completion(
    interaction: discord.Interaction,
    watch_party_service: WatchPartyService,
    wash_crew_role_id: Optional[int],
    scheduler_service: Optional[SchedulerService] = None,
    suggestion_service: Optional[SuggestionService] = None,
) -> None:
    """Show a "which watch party?" picker, then cancel the chosen one and
    remove its pending reminder job.

    Release Polish (Discord-native UX): watch_party_id is no longer a
    command parameter -- WASH Crew picks the target from a selector of
    currently scheduled watch parties instead of typing an internal ID.

    scheduler_service/suggestion_service default to None so callers/
    tests that don't pass them keep working unchanged; a missing
    scheduler_service simply skips reminder-job cancellation (see
    cancel_watch_party_reminder), and a missing suggestion_service just
    falls back to a "Watch item #N" label (see
    build_watch_party_select_options).
    """
    if wash_crew_role_id is None:
        await interaction.response.send_message(
            "WASH Crew permissions have not been configured. Set WASH_CREW_ROLE_ID before using this command.",
            ephemeral=True,
        )
        return
    if not is_wash_crew_member(interaction.user, wash_crew_role_id):
        await interaction.response.send_message(
            "You need the WASH Crew role to cancel a watch party.", ephemeral=True
        )
        return

    guild_id = interaction.guild_id
    if guild_id is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    scheduled = [
        watch_party
        for watch_party in watch_party_service.list_watch_parties(guild_id)
        if watch_party.status == WatchPartyStatus.SCHEDULED
    ]
    if not scheduled:
        await interaction.response.send_message("No watch parties are currently scheduled.", ephemeral=True)
        return

    async def on_select(select_interaction: discord.Interaction, watch_party_id: int) -> None:
        message, ephemeral = perform_cancel_watch_party(
            watch_party_service=watch_party_service,
            user=select_interaction.user,
            wash_crew_role_id=wash_crew_role_id,
            watch_party_id=watch_party_id,
        )
        await select_interaction.response.send_message(message, ephemeral=ephemeral)
        if ephemeral:
            return

        # FR-021: remove any pending reminder job now that the watch party is
        # cancelled -- a no-op if none is active (e.g. reminders were
        # disabled, or it already fired).
        await cancel_watch_party_reminder(scheduler_service, watch_party_id)

    options = build_watch_party_select_options(scheduled, suggestion_service)
    view = WatchPartySelectView(
        options,
        on_select,
        custom_id="wpm_cancel_watch_party_select",
        placeholder="Choose a watch party to cancel...",
    )
    await interaction.response.send_message("Choose which watch party to cancel:", view=view, ephemeral=True)


def perform_watch_party_status(
    watch_party_service: WatchPartyService, suggestion_service: SuggestionService
) -> str:
    """Core logic for /watch_party_status, kept free of Discord objects entirely.

    Args:
        watch_party_service: Used to look up the currently scheduled watch party.
        suggestion_service: Used to resolve the watch item's title and IMDb link.

    Returns:
        The status text for the soonest-scheduled watch party, or a clear
        "nothing scheduled" message.
    """
    watch_party = watch_party_service.get_current_watch_party()
    if watch_party is None:
        return "No watch party is currently scheduled."

    watch_item = suggestion_service.get_suggestion(watch_party.watch_item_id)
    return build_watch_party_status_text(watch_party, watch_item)


def format_count(count: int, singular: str, plural: Optional[str] = None) -> str:
    """Return a count with correct singular or plural wording."""
    word = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {word}"


def build_statistics_text(snapshot: StatisticsSnapshot) -> str:
    """Format a guild-scoped statistics snapshot for Discord."""
    return "\n".join(
        [
            "**Watch Party Statistics**",
            "",
            "**Watch Items**",
            f"Total: {format_count(snapshot.total_watch_items, 'watch item')}",
            f"Active suggestions: {format_count(snapshot.active_suggestions, 'suggestion')}",
            f"Watched: {format_count(snapshot.watched_items, 'watch item')}",
            "",
            "**Collections**",
            f"Total: {format_count(snapshot.total_databases, 'collection')}",
            f"Active: {format_count(snapshot.active_databases, 'collection')}",
            "",
            "**Voting**",
            f"Rounds: {format_count(snapshot.total_vote_rounds, 'round')}",
            f"Open: {format_count(snapshot.open_vote_rounds, 'round')}",
            f"Closed: {format_count(snapshot.closed_vote_rounds, 'round')}",
            f"Votes cast: {format_count(snapshot.total_votes_cast, 'vote')}",
            f"Average votes per round: {snapshot.average_votes_per_round:.1f}",
        ]
    )


def perform_stats(
    statistics_service: StatisticsService,
    guild_id: Optional[int],
) -> str:
    """Return the /stats response for the current Discord server."""
    if guild_id is None:
        return "This command can only be used in a server."
    return build_statistics_text(statistics_service.snapshot(guild_id))


# --- FR-034: Statistics & Reporting -------------------------------------------------
#
# /stats replaces the WASH-Crew-only, always-public command above with a
# Watch-Party-Member-accessible one that defaults to ephemeral output and
# lets WASH Crew (or, for a member's own statistics, anyone) opt into
# posting publicly -- mirroring /list's exact public/private pattern
# (FR-033A Section 9). perform_stats/build_statistics_text above are
# reused unchanged as the "server" type's underlying data/formatting.


class StatsType(str, Enum):
    """Which /stats view to show."""

    SERVER = "server"
    MEMBER = "member"
    SUGGESTION = "suggestion"
    DATABASE = "database"


def format_optional_percentage(value: Optional[float]) -> str:
    return "not available" if value is None else f"{value:.1f}%"


def format_optional_hours(value: Optional[float]) -> str:
    return "not available" if value is None else f"{value:.1f}h"


def format_optional_date(value: Optional[date]) -> str:
    return value.isoformat() if value is not None else "unknown (created before statistics tracking began)"


def format_optional_timestamp(value: Optional[datetime]) -> str:
    if value is None:
        return "never"
    return f"<t:{int(value.timestamp())}:f>"


def format_optional_days(value: Optional[int]) -> str:
    if value is None:
        return "not available"
    return format_count(value, "day")


def build_server_statistics_text(stats: ServerStatistics) -> str:
    """Format FR-034 Section 5's server statistics for Discord."""
    lines = [
        "**Server Statistics**",
        "",
        "**Watch Parties**",
        f"Total: {format_count(stats.total_watch_parties, 'watch party', 'watch parties')}",
        f"Scheduled: {format_count(stats.scheduled_watch_parties, 'watch party', 'watch parties')}",
        f"Cancelled: {format_count(stats.cancelled_watch_parties, 'watch party', 'watch parties')}",
        "",
        "**Voting Rounds**",
        f"Total: {format_count(stats.total_vote_rounds, 'round')}",
        f"Open: {format_count(stats.open_vote_rounds, 'round')}",
        f"Closed: {format_count(stats.closed_vote_rounds, 'round')}",
        f"Cancelled: {format_count(stats.cancelled_vote_rounds, 'round')}",
        f"Blind: {format_count(stats.blind_vote_rounds, 'round')}",
        f"Visible: {format_count(stats.visible_vote_rounds, 'round')}",
        f"Ties: {format_count(stats.tie_count, 'round')}",
        f"Average vote duration: {format_optional_hours(stats.average_vote_duration_hours)}",
        f"Average candidates per round: {stats.average_candidates_per_round:.1f}",
        "",
        "**Participation**",
        f"Votes cast: {format_count(stats.total_votes_cast, 'vote')}",
        f"Average participation per round: {stats.average_participation_per_round:.1f}",
    ]
    if stats.total_watch_party_members is not None:
        lines.append(f"Current Watch Party members: {stats.total_watch_party_members}")
    lines.append(f"Participation percentage: {format_optional_percentage(stats.participation_percentage)}")
    return "\n".join(lines)


def build_member_statistics_text(stats: MemberStatistics, member_mention: str) -> str:
    """Format FR-034 Section 7's member statistics for Discord."""
    lines = [
        f"**Your Statistics** ({member_mention})",
        "",
        "**Suggestions**",
        f"Submitted: {format_count(stats.suggestions_submitted, 'suggestion')}",
        f"Watched: {format_count(stats.suggestions_watched, 'suggestion')}",
        f"Retired: {format_count(stats.suggestions_retired, 'suggestion')}",
        f"Won a vote: {format_count(stats.winning_suggestions, 'suggestion')}",
        "",
        "**Voting**",
        f"Votes cast: {format_count(stats.votes_cast, 'vote')}",
        f"Participation percentage: {stats.participation_percentage:.1f}% of all rounds",
    ]
    if not stats.has_submission_history:
        lines.append(
            "\nNo suggestions with a recorded submitter were found for you -- "
            "suggestion statistics are only tracked for suggestions added since this feature shipped."
        )
    return "\n".join(lines)


def build_suggestion_statistics_text(stats: SuggestionStatistics) -> str:
    """Format FR-034 Section 6's suggestion statistics for Discord.

    Status is rendered through display_status_label(), the same shared
    resolver a suggestion's own post, /list, and /suggestion edit all use
    -- /stats must never show a different status vocabulary for the same
    suggestion. "Currently archived" is dropped entirely: it was always
    true exactly when the Status line above already reads "🗄️ Retired,"
    so it added nothing once Status uses the correct label. "Retired" is
    renamed to "Retired via Crew Review" since it tracks a narrower fact
    (journey.retired_at, only ever set by the Retire decision) than the
    Retired *status* above it, which also covers a suggestion archived
    directly via /suggestion remove.
    """
    status_text = display_status_label(stats.display_status)
    lines = [
        f"**Suggestion Statistics -- {stats.title}**",
        "",
        f"Reference: #{stats.suggestion_id:04d}",
        f"Status: {status_text}",
        f"Created: {format_optional_date(stats.created_date)}",
        f"Submitted by: {f'<@{stats.submitter}>' if stats.submitter else 'unknown (created before statistics tracking began)'}",
        "",
        "**Voting**",
        f"Nominations: {format_count(stats.nomination_count, 'time')}",
        f"First nominated: {format_optional_timestamp(stats.first_nomination_at)}",
        f"Last nominated: {format_optional_timestamp(stats.last_nomination_at)}",
        f"Days until first nomination: {format_optional_days(stats.days_until_first_nomination)}",
        "",
        "**Lifecycle**",
        f"Watched: {format_count(stats.watch_count, 'time')}",
        f"Days until watched: {format_optional_days(stats.days_until_watched)}",
        f"Retired via Crew Review: {'Yes' if stats.is_retired else 'No'}"
        + (f" ({format_optional_timestamp(stats.retired_at)})" if stats.is_retired else ""),
    ]
    return "\n".join(lines)


def build_database_statistics_text(stats: DatabaseStatistics) -> str:
    """Format FR-034 Section 9's database statistics for Discord.

    "Retired suggestions" (not "Archived") matches the shared display
    label every ARCHIVED-status suggestion renders as everywhere else
    (its own post, /list, /suggestion edit). "Retired via Crew Review"
    is the narrower subset that went through Crew Review's own Retire
    decision specifically, not every archived suggestion -- disambiguated
    from the line above it rather than reusing the word "Retired" for
    two different counts. Watched and Vote Winner are reported as two
    separate, disjoint counts (previously conflated into one).
    """
    lines = [
        f"**Collection Statistics -- {stats.database_name}**",
        "",
        f"Active suggestions: {format_count(stats.active_suggestions, 'suggestion')}",
        f"Watched suggestions: {format_count(stats.watched_suggestions, 'suggestion')}",
        f"Vote Winner suggestions: {format_count(stats.vote_winner_suggestions, 'suggestion')}",
        f"Retired suggestions: {format_count(stats.archived_suggestions, 'suggestion')}",
        f"Retired via Crew Review: {format_count(stats.retired_suggestions, 'suggestion')}",
    ]
    return "\n".join(lines)


OnStatsDatabaseResolved = Callable[[discord.Interaction, SuggestionDatabase], Awaitable[None]]


async def resolve_database_then(
    interaction: discord.Interaction,
    bot: "WatchPartyBot",
    guild_id: int,
    channel_id: Optional[int],
    on_resolved: OnStatsDatabaseResolved,
) -> None:
    """Resolve which suggestion database a command should use for this
    channel/thread, then continue with on_resolved.

    The one shared contextual-resolution entry point every database-
    scoped command goes through: automatic, silent resolution when the
    channel unambiguously identifies a database (its home channel or its
    configured suggestion post destination -- see
    SuggestionService.resolve_database_for_channel), an interactive
    picker when more than one database exists and none matches, and a
    clear error when no database is configured in the guild at all.
    Never guesses.
    """
    resolution = bot.suggestion_service.resolve_database_for_channel(
        guild_id, channel_id, bot.suggestion_database_configuration_repository
    )
    if resolution.database is not None:
        await on_resolved(interaction, resolution.database)
        return

    if resolution.ambiguous_candidates:

        async def on_select(select_interaction: discord.Interaction, database_id: int) -> None:
            database = bot.suggestion_service.get_database(database_id)
            if database is None:
                await select_interaction.response.send_message(
                    "That collection no longer exists.", ephemeral=True
                )
                return
            await on_resolved(select_interaction, database)

        options = [
            (
                database.database_id,
                format_collection_display(
                    _resolve_collection_name(
                        bot.suggestion_service, database, interaction.guild, bot.suggestion_database_configuration_repository
                    )
                ),
            )
            for database in resolution.ambiguous_candidates
        ]
        view = ListDatabaseSelectView(options, on_select)
        await interaction.response.send_message(
            "Which collection would you like to use?", view=view, ephemeral=True
        )
        return

    await interaction.response.send_message(
        resolution.error_message or "No collection is available here.", ephemeral=True
    )


def paginate_stats_text(text: str) -> List[str]:
    """Split a rendered statistics message into Discord-safe pages.

    Every build_*_statistics_text function renders "**Title**", "",
    then content lines -- reused as paginate_lines' (header, lines) shape
    so any statistics view that grows too long for one message
    (Section 10: "Large result sets should paginate") degrades to
    Previous/Next pages exactly like /list, instead of failing outright.
    In practice none of FR-034's fixed-shape summaries are likely to
    exceed one page, but this keeps that guarantee true regardless.
    """
    parts = text.split("\n")
    header = parts[0]
    body_lines = parts[2:] if len(parts) > 1 and parts[1] == "" else parts[1:]
    return paginate_lines(header, body_lines)


async def send_paginated_stats(interaction: discord.Interaction, text: str, public: bool) -> None:
    pages = paginate_stats_text(text)
    if len(pages) == 1:
        await interaction.response.send_message(pages[0], ephemeral=not public)
        return
    requester_id = getattr(interaction.user, "id", None)
    view = PaginatedListView(pages, requester_id=requester_id)
    await interaction.response.send_message(pages[0], view=view, ephemeral=not public)


async def send_server_statistics(
    interaction: discord.Interaction, bot: "WatchPartyBot", guild_id: int, public: bool
) -> None:
    total_watch_party_members = None
    guild = getattr(interaction, "guild", None)
    role_id = bot.watch_party_member_role_id
    if guild is not None and role_id is not None:
        role = guild.get_role(role_id)
        if role is not None:
            total_watch_party_members = len(role.members)

    stats = bot.statistics_service.server_statistics(guild_id, total_watch_party_members=total_watch_party_members)
    await send_paginated_stats(interaction, build_server_statistics_text(stats), public)


async def send_member_statistics(interaction: discord.Interaction, bot: "WatchPartyBot", guild_id: int, public: bool) -> None:
    user = interaction.user
    stats = bot.statistics_service.member_statistics(guild_id, user.id)
    mention = getattr(user, "mention", str(user))
    await send_paginated_stats(interaction, build_member_statistics_text(stats, mention), public)


async def send_suggestion_statistics(
    interaction: discord.Interaction, bot: "WatchPartyBot", query: str, public: bool
) -> None:
    matches = bot.suggestion_service.find_matches_for_removal(query, guild_id=interaction.guild_id)
    if not matches:
        await interaction.response.send_message(f'No suggestion matches "{query}".', ephemeral=True)
        return

    if len(matches) > 1:
        options = [
            (
                item.id,
                build_removal_option_label(item, bot.suggestion_service, getattr(bot, "vote_service", None)),
            )
            for item in matches
        ]

        async def on_select(select_interaction: discord.Interaction, suggestion_id: int) -> None:
            stats = bot.statistics_service.suggestion_statistics(suggestion_id)
            if stats is None:
                await select_interaction.response.send_message("That suggestion no longer exists.", ephemeral=True)
                return
            await send_paginated_stats(select_interaction, build_suggestion_statistics_text(stats), public)

        view = RemovalMatchSelectView(options, on_select)
        await interaction.response.send_message(
            f'Multiple suggestions match "{query}". Choose one:', view=view, ephemeral=True
        )
        return

    stats = bot.statistics_service.suggestion_statistics(matches[0].id)
    await send_paginated_stats(interaction, build_suggestion_statistics_text(stats), public)


async def send_database_statistics(
    interaction: discord.Interaction, bot: "WatchPartyBot", guild_id: int, channel_id: Optional[int], public: bool
) -> None:
    async def show(target_interaction: discord.Interaction, database: SuggestionDatabase) -> None:
        stats = bot.statistics_service.database_statistics(database.database_id)
        if stats is None:
            await target_interaction.response.send_message(
                "That collection no longer exists.", ephemeral=True
            )
            return
        await send_paginated_stats(target_interaction, build_database_statistics_text(stats), public)

    await resolve_database_then(interaction, bot, guild_id, channel_id, show)


async def handle_stats(
    interaction: discord.Interaction,
    bot: "WatchPartyBot",
    stats_type: str,
    public: bool,
    suggestion_query: Optional[str],
) -> None:
    """Handle /stats: resolve type, enforce FR-034 Section 4's privacy rules, dispatch.

    Every type requires at least Watch Party membership. Public posting
    requires WASH Crew for every type except "member" -- a member
    choosing to reveal their own statistics is a different, self-
    consenting action than posting an aggregate view, so it needs no
    elevated permission (Section 4: "users may optionally post their own
    statistics publicly").
    """
    permission = resolve_permission_service(
        interaction.guild_id, bot.guild_configuration_repository
    ).require_watch_party_member(interaction.user)
    if not permission.allowed:
        await interaction.response.send_message(permission.message, ephemeral=True)
        return

    guild_id = interaction.guild_id
    channel_id = interaction.channel_id
    if guild_id is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    try:
        resolved_type = StatsType(stats_type)
    except ValueError:
        await interaction.response.send_message(
            "Choose Server, Member, Suggestion, or Collection.", ephemeral=True
        )
        return

    is_crew = resolve_permission_service(guild_id, bot.guild_configuration_repository).is_wash_crew(interaction.user)
    if public and resolved_type is not StatsType.MEMBER and not is_crew:
        await interaction.response.send_message(
            "You need the WASH Crew role to post statistics publicly.", ephemeral=True
        )
        return

    if resolved_type is StatsType.SERVER:
        await send_server_statistics(interaction, bot, guild_id, public)
    elif resolved_type is StatsType.MEMBER:
        await send_member_statistics(interaction, bot, guild_id, public)
    elif resolved_type is StatsType.SUGGESTION:
        if not suggestion_query or not suggestion_query.strip():
            await interaction.response.send_message(
                "Provide a suggestion reference number or title with the `suggestion` option.", ephemeral=True
            )
            return
        await send_suggestion_statistics(interaction, bot, suggestion_query.strip(), public)
    else:
        await send_database_statistics(interaction, bot, guild_id, channel_id, public)


def resolve_active_database_display_name(suggestion_service: SuggestionService, guild_id: int) -> str:
    """Build a display-ready "Active collection" string for /about.

    Mirrors the same active-database filtering build_database_list_text
    and ConfigService.build_summary_lines already use -- zero active
    databases and multiple active databases (SuggestionDatabase.active is
    not an exclusive selector, see SuggestionService.resolve_database_for_
    channel's own docstring) are both explained rather than guessed at.
    """
    active = [database for database in suggestion_service.list_databases(guild_id) if database.active]
    if len(active) == 1:
        return active[0].name
    if not active:
        return "None configured"
    return f"{len(active)} active (see /database list)"


async def handle_about(interaction: discord.Interaction, bot: "WatchPartyBot") -> None:
    """Send /about: WASH identity and Documentation links for everyone,
    plus Health/Configuration/Runtime (formerly the separate, WASH Crew-
    only /diagnostics command) for WASH Crew.

    Never fails or rejects -- non-crew members and DM/no-guild
    invocations simply see the reduced, everyone-visible view.
    """
    guild_id = interaction.guild_id
    is_crew = resolve_permission_service(guild_id, bot.guild_configuration_repository).is_wash_crew(
        interaction.user
    )
    show_expanded = is_crew and guild_id is not None

    health: Optional[AboutHealth] = None
    configuration: Optional[AboutConfiguration] = None
    runtime_info: Optional[AboutRuntime] = None
    latency_ms: Optional[float] = None
    started_at: Optional[datetime] = None
    now: Optional[datetime] = None

    if show_expanded:
        latency_ms = bot.latency * 1000
        started_at = bot.started_at
        now = datetime.now(timezone.utc)

        health = AboutHealth(
            discord_connected=bot.is_ready(),
            scheduler_running=bot.scheduler_host.is_running,
            interactive_voting_restored=bool(bot.interactive_voting_restored),
            omdb_configured=bot.suggestion_input_service.is_omdb_configured,
        )

        snapshot = bot.statistics_service.snapshot(guild_id)
        server_stats = bot.statistics_service.server_statistics(guild_id)
        configuration = AboutConfiguration(
            active_database_name=resolve_active_database_display_name(bot.suggestion_service, guild_id),
            database_count=snapshot.total_databases,
            watch_item_count=snapshot.total_watch_items,
            scheduled_watch_party_count=server_stats.scheduled_watch_parties,
            open_vote_round=snapshot.open_vote_rounds > 0,
        )

        runtime_info = AboutRuntime(
            python_version=platform.python_version(),
            discord_py_version=getattr(discord, "__version__", "Unknown"),
            guild_name=getattr(interaction.guild, "name", None),
        )

    content = build_about_content(
        __version__,
        __build__,
        show_expanded_sections=show_expanded,
        latency_ms=latency_ms,
        started_at=started_at,
        now=now,
        health=health,
        configuration=configuration,
        runtime_info=runtime_info,
    )
    embed = EmbedFactory.info(
        content.title,
        content.description,
        footer=None,
        include_timestamp=False,
        fields=[{"name": field.name, "value": field.value, "inline": field.inline} for field in content.fields],
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


def build_help_text(show_admin: bool = True, show_member: bool = False) -> str:
    """Build the complete role-aware help response as a single string.

    This compatibility helper delegates to :mod:`help_service`, folding
    the Commands Reference link (an embed when actually sent to Discord,
    see send_help_response) into a plain-text footer so it preserves its
    original single-string contract for existing callers. show_member
    reflects FR-029's three-tier permission model (everyone / Watch Party
    member / WASH Crew); show_admin implies show_member, matching
    build_help_response's own inheritance.
    """
    response = build_help_response(show_wash_crew=show_admin, show_watch_party_member=show_member)
    reference_text = "\n".join(
        (f"**{response.reference_title}**", response.reference_description, response.reference_url)
    )
    return "\n\n".join((response.command_text, reference_text))


async def send_help_response(interaction: discord.Interaction, response: HelpResponse) -> None:
    """Send /help's command list and Commands Reference link as one message.

    The reference link is presented as an embed rather than plain message
    content -- Discord only auto-generates a link-preview card (here, a
    large GitHub repository card) for links in a message's plain content,
    never for a link inside an embed's own title/description.

    A trailing blank line (a zero-width space so Discord doesn't collapse
    it as pure trailing whitespace) separates the last command section
    from the embed card below it -- otherwise the two sit flush against
    each other with no breathing room.
    """
    embed = EmbedFactory.info(response.reference_title, response.reference_description, url=response.reference_url)
    await interaction.response.send_message(
        f"{response.command_text}\n\u200b", embed=embed, ephemeral=response.ephemeral
    )


def main() -> None:
    configure_logging(level=logging.INFO)
    
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    
    guild_id_str = os.getenv("DISCORD_GUILD_ID")
    try:
        guild_id = parse_guild_id(guild_id_str)
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        exit(1)

    wash_crew_role_id_str = os.getenv("WASH_CREW_ROLE_ID")
    try:
        wash_crew_role_id = parse_wash_crew_role_id(wash_crew_role_id_str)
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        exit(1)

    watch_party_member_role_id_str = os.getenv("WATCH_PARTY_MEMBER_ROLE_ID")
    try:
        watch_party_member_role_id = parse_watch_party_member_role_id(
            watch_party_member_role_id_str
        )
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        exit(1)

    default_nominee_count_str = os.getenv("DEFAULT_VOTE_NOMINEE_COUNT")
    try:
        default_nominee_count = parse_default_nominee_count(default_nominee_count_str)
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        exit(1)

    bot = WatchPartyBot(
        token=token,
        guild_id=guild_id,
        wash_crew_role_id=wash_crew_role_id,
        watch_party_member_role_id=watch_party_member_role_id,
        default_nominee_count=default_nominee_count,
    )

    try:
        asyncio.run(bot.start_bot())
    except RuntimeError as e:
        logger.error(f"Failed to start bot: {e}")
        exit(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    main()
