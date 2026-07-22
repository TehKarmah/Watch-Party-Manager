from __future__ import annotations

import asyncio
import logging
import os
import platform
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, List, Optional, Tuple

import discord
from discord.ext import commands
from dotenv import load_dotenv

from watch_party_manager.domain.vote import (
    DEFAULT_VOTE_CANDIDATE_COUNT,
    DEFAULT_VOTE_DURATION_DAYS,
    MAX_VOTE_CHANGES,
    MAX_VOTE_CANDIDATE_COUNT,
    MAX_VOTE_DURATION_DAYS,
    MIN_CANDIDATES_FOR_A_ROUND,
    MIN_VOTE_CANDIDATE_COUNT,
    MIN_VOTE_DURATION_DAYS,
    VoteRecord,
    VoteRound,
    VoteRoundStatus,
    VoteVisibility,
)
from watch_party_manager.domain.guild_configuration import (
    GuildConfiguration,
    GuildVoteVisibility,
    JoinMode,
)
from watch_party_manager.domain.setup_wizard import (
    SETUP_WIZARD_STEP_ORDER,
    SetupWizardDraft,
    SetupWizardState,
    SetupWizardStep,
)
from watch_party_manager.domain.membership_request import MembershipRequest
from watch_party_manager.domain.suggestion_database import SuggestionDatabase
from watch_party_manager.domain.suggestion_database_configuration import CandidateSelectionMode
from watch_party_manager.domain.watch_item import MetadataProvider, WatchItem, WatchItemStatus
from watch_party_manager.domain.watch_party import WatchParty
from watch_party_manager.logger_config import configure_logging
from watch_party_manager.persistence.guild_configuration_repository import (
    GuildConfigurationRepository,
)
from watch_party_manager.persistence.membership_request_repository import MembershipRequestRepository
from watch_party_manager.persistence.setup_wizard_repository import SetupWizardRepository
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.scheduler import (
    CLOSE_VOTE_JOB_TYPE,
    CloseVoteJobHandler,
    SchedulerHost,
    SchedulerService,
    VOTE_REMINDER_JOB_TYPE,
    VoteReminderJobHandler,
    WATCH_PARTY_REMINDER_JOB_TYPE,
    WatchPartyReminderJobHandler,
    cancel_vote_jobs,
    cancel_watch_party_reminder,
    reschedule_vote_jobs,
    reschedule_watch_party_reminder,
    schedule_vote_jobs,
    schedule_watch_party_reminder,
)
from watch_party_manager.services.about_service import build_about_content
from watch_party_manager.services.backup_service import (
    BackupError,
    BackupKind,
    BackupService,
)
from watch_party_manager.services.config_service import (
    CONFIG_SECTION_ORDER,
    CONFIG_SECTION_TITLES,
    ConfigSection,
    ConfigService,
    ConfigUpdateResult,
)
from watch_party_manager.services.discord_message_link import build_discord_message_link
from watch_party_manager.services.discord_timestamp_formatter import (
    format_datetime_for_display,
)
from watch_party_manager.services.help_service import HelpResponse, build_help_response
from watch_party_manager.services.membership_service import (
    JoinOutcomeKind,
    MemberSearchResult,
    MembershipService,
)
from watch_party_manager.services.nominee_selection_service import NomineeSelectionService
from watch_party_manager.services.permission_service import PermissionService
from watch_party_manager.services.setup_wizard_service import (
    BACKUP_INTERVAL_DAYS_EXTRA_FIELD,
    BACKUP_RETENTION_COUNT_EXTRA_FIELD,
    MAX_BACKUP_INTERVAL_DAYS,
    MAX_BACKUP_RETENTION_COUNT,
    MIN_BACKUP_INTERVAL_DAYS,
    MIN_BACKUP_RETENTION_COUNT,
    SetupWizardService,
)
from watch_party_manager.services.suggestion_input_service import SuggestionInputService
from watch_party_manager.services.suggestion_list_formatter import (
    SuggestionListFormatter,
    SuggestionListView,
)
from watch_party_manager.services.suggestion_service import (
    DEFAULT_REJECTION_THRESHOLD,
    SuggestionService,
)
from watch_party_manager.services.suggestion_repair_service import SuggestionRepairService
from watch_party_manager.services.statistics_service import StatisticsService, StatisticsSnapshot
from watch_party_manager.services.vote_announcement_formatter import (
    build_suggestion_link,
    build_vote_cancellation_notice,
    build_vote_deadline_change_notice,
    build_vote_link,
    format_standings_lines,
)
from watch_party_manager.services.vote_completion_announcer import finalize_vote_completion
from watch_party_manager.services.vote_completion_service import (
    VoteCompletionResult,
    VoteCompletionService,
)
from watch_party_manager.services.vote_service import StandingsEntry, VoteService
from watch_party_manager.services.watch_party_service import WatchPartyService
from watch_party_manager.edit_vote_view import (
    EditVoteConfirmationView,
    EditVoteEndTimeModal,
    EditVoteManagementView,
)
from watch_party_manager.config_view import (
    BackToMenuOnlyView,
    ConfigAdminChannelSectionView,
    ConfigDatabaseSectionView,
    ConfigJoinModeSectionView,
    ConfigMainMenuView,
    ConfigModalRetryView,
    ConfigRoleSectionView,
    ConfigWatchDestinationSectionView,
    OnBackToMenu,
)
from watch_party_manager.membership_view import MembershipApprovalView, PendingRequestSelectView
from watch_party_manager.restore_confirmation_view import RestoreConfirmationView
from watch_party_manager.setup_wizard_view import (
    AdminChannelStepView,
    BackupDefaultsModal,
    CreateDatabaseChannelSelectView,
    CreateDatabaseNameModal,
    ExistingDatabaseSelectView,
    ModalStepIntroView,
    ReminderDefaultsModal,
    ReviewStepView,
    SetupWizardResumeView,
    SuggestionDatabaseChoiceView,
    VotingDefaultsModal,
    WashCrewRoleStepView,
    WatchDestinationStepView,
    WatchPartyRoleStepView,
)
from watch_party_manager.start_vote_view import (
    CustomizeVoteModal,
    StartVoteChoiceView,
)
from watch_party_manager.suggestion_view import SuggestionView, build_reject_button_custom_id
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
        self.statistics_service = StatisticsService(self.suggestion_service)
        self.vote_completion_service = VoteCompletionService(self.vote_service, self.suggestion_service)
        self.watch_party_service = WatchPartyService(self.suggestion_service)
        self.suggestion_database_configuration_repository = SuggestionDatabaseConfigurationRepository()
        self.backup_service = BackupService()
        self.interactive_voting_restored = False
        self.suggestion_views_restored = 0
        self.scheduler_host = SchedulerHost.from_json_file(
            Path("data") / "scheduled_jobs.json"
        )
        self.scheduler_host.scheduler_service.register_handler(
            CLOSE_VOTE_JOB_TYPE,
            CloseVoteJobHandler(self.vote_completion_service, self.vote_service, self.suggestion_service, self),
        )
        self.scheduler_host.scheduler_service.register_handler(
            VOTE_REMINDER_JOB_TYPE, VoteReminderJobHandler(self.vote_service, self.suggestion_service, self)
        )
        self.scheduler_host.scheduler_service.register_handler(
            WATCH_PARTY_REMINDER_JOB_TYPE,
            WatchPartyReminderJobHandler(self.watch_party_service, self.suggestion_service, self),
        )
        self.guild_configuration_repository = GuildConfigurationRepository()
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
        @self.tree.command(name="about")
        async def about(interaction: discord.Interaction) -> None:
            content = build_about_content(
                __version__,
                __build__,
                latency_ms=self.latency * 1000,
                started_at=self.started_at,
                now=datetime.now(timezone.utc),
            )
            message = (
                f"**{content.title}**\n\n"
                f"{content.description}\n\n"
                f"*{content.footer}*"
            )
            await interaction.response.send_message(message, ephemeral=True)

        @self.tree.command(name="join_watch_party")
        async def join_watch_party(interaction: discord.Interaction) -> None:
            await handle_join_watch_party(interaction, self)

        @self.tree.command(name="help")
        async def help_command(interaction: discord.Interaction) -> None:
            show_wash_crew = self.permission_service.is_wash_crew(interaction.user)
            show_watch_party_member = self.permission_service.is_watch_party_member(interaction.user)
            response = build_help_response(
                show_wash_crew=show_wash_crew, show_watch_party_member=show_watch_party_member
            )
            await send_help_response(interaction, response)

        @self.tree.command(name="stats")
        async def stats(interaction: discord.Interaction) -> None:
            permission = self.permission_service.require_wash_crew(interaction.user)
            if not permission.allowed:
                await interaction.response.send_message(permission.message, ephemeral=True)
                return
            message = perform_stats(
                statistics_service=self.statistics_service,
                guild_id=interaction.guild_id,
            )
            await interaction.response.send_message(message)
            logger.info(
                "User %s requested statistics in guild %s",
                interaction.user.id,
                interaction.guild_id,
            )

        @self.tree.command(name="diagnostics")
        async def diagnostics(interaction: discord.Interaction) -> None:
            message, ephemeral = perform_diagnostics(
                statistics_service=self.statistics_service,
                user=interaction.user,
                wash_crew_role_id=self.wash_crew_role_id,
                guild_id=interaction.guild_id,
                version=__version__,
                python_version=platform.python_version(),
                discord_version=getattr(discord, "__version__", "Unknown"),
                latency_ms=self.latency * 1000,
                started_at=self.started_at,
                now=datetime.now(timezone.utc),
                interactive_voting_restored=self.interactive_voting_restored,
            )
            await interaction.response.send_message(message, ephemeral=ephemeral)
            if message.startswith("**WASH Diagnostics**"):
                logger.info(
                    "User %s requested diagnostics in guild %s",
                    interaction.user.id,
                    interaction.guild_id,
                )

        @self.tree.command(name="add")
        async def suggest(
            interaction: discord.Interaction,
            title: str,
            imdb_url: Optional[str] = None,
        ) -> None:
            permission = self.permission_service.require_watch_party_member(interaction.user)
            if not permission.allowed:
                await interaction.response.send_message(permission.message, ephemeral=True)
                return
            message, ephemeral, watch_item = await perform_add_suggestion_from_input(
                suggestion_input_service=self.suggestion_input_service,
                suggestion_service=self.suggestion_service,
                guild_id=interaction.guild_id,
                channel_id=interaction.channel_id,
                title=title,
                imdb_url=imdb_url,
            )
            if watch_item is None:
                await interaction.response.send_message(message, ephemeral=ephemeral)
                return
            resolution = self.suggestion_service.resolve_database_for_channel(
                interaction.guild_id, interaction.channel_id
            )
            database_name = resolution.database.name if resolution.database is not None else "Suggestion Database"
            embed = build_suggestion_confirmation_embed(
                watch_item,
                database_name=database_name,
                suggested_by=getattr(interaction.user, "mention", str(interaction.user)),
            )
            view = build_suggestion_view(
                self.suggestion_service,
                self.suggestion_database_configuration_repository,
                watch_item,
                interaction.guild_id,
                permission_service=self.permission_service,
            )
            await interaction.response.send_message(embed=embed, ephemeral=ephemeral, view=view)
            if watch_item is not None:
                sent_message = await interaction.original_response()
                self.suggestion_service.attach_message_reference(watch_item.id, sent_message.id)
                logger.info(
                    "User %s added watch item %s (%r) to database %s in guild %s",
                    interaction.user.id,
                    watch_item.id,
                    watch_item.title,
                    watch_item.database_id,
                    interaction.guild_id,
                )

        @self.tree.command(name="list")
        @discord.app_commands.describe(
            view="Choose the simple member list or the expanded WASH Crew list.",
            public="Post the list publicly instead of showing it only to you (WASH Crew only).",
        )
        @discord.app_commands.choices(
            view=[
                discord.app_commands.Choice(name="Standard", value="standard"),
                discord.app_commands.Choice(name="WASH Crew", value="crew"),
            ]
        )
        async def suggestions(
            interaction: discord.Interaction,
            view: str = "standard",
            public: bool = False,
        ) -> None:
            permission = self.permission_service.require_wash_crew(interaction.user)
            if not permission.allowed:
                await interaction.response.send_message(permission.message, ephemeral=True)
                return
            message, ephemeral = perform_list_suggestions_response(
                suggestion_service=self.suggestion_service,
                guild_id=interaction.guild_id,
                channel_id=interaction.channel_id,
                user=interaction.user,
                wash_crew_role_id=self.wash_crew_role_id,
                view=view,
                public=public,
            )
            await interaction.response.send_message(message, ephemeral=ephemeral)

        @self.tree.command(name="repair_suggestions")
        async def repair_suggestions(interaction: discord.Interaction) -> None:
            message, ephemeral = await perform_repair_suggestions(
                repair_service=self.suggestion_repair_service,
                user=interaction.user,
                wash_crew_role_id=self.wash_crew_role_id,
            )
            await interaction.response.send_message(message, ephemeral=ephemeral)

        @self.tree.command(name="backup")
        async def backup(interaction: discord.Interaction) -> None:
            message, ephemeral = perform_backup(
                backup_service=self.backup_service,
                user=interaction.user,
                wash_crew_role_id=self.wash_crew_role_id,
            )
            await interaction.response.send_message(message, ephemeral=ephemeral)

        @self.tree.command(name="restore")
        async def restore(interaction: discord.Interaction, backup_filename: str) -> None:
            message, ephemeral, prompt = perform_restore(
                backup_service=self.backup_service,
                user=interaction.user,
                wash_crew_role_id=self.wash_crew_role_id,
                backup_filename=backup_filename,
            )
            if not prompt:
                await interaction.response.send_message(message, ephemeral=ephemeral)
                return

            async def on_confirm(confirm_interaction: discord.Interaction) -> None:
                result_message, result_ephemeral = perform_confirmed_restore(
                    backup_service=self.backup_service,
                    user=confirm_interaction.user,
                    wash_crew_role_id=self.wash_crew_role_id,
                    backup_filename=backup_filename,
                )
                await confirm_interaction.response.send_message(
                    result_message, ephemeral=result_ephemeral
                )

            async def on_cancel(cancel_interaction: discord.Interaction) -> None:
                await cancel_interaction.response.send_message(
                    "Restore cancelled. No data was changed.", ephemeral=True
                )

            view = RestoreConfirmationView(on_confirm, on_cancel)
            await interaction.response.send_message(message, view=view, ephemeral=True)

        @self.tree.command(name="remove")
        async def remove_suggestion(interaction: discord.Interaction, title: str) -> None:
            message, ephemeral, success = perform_remove_suggestion(
                suggestion_service=self.suggestion_service,
                user=interaction.user,
                wash_crew_role_id=self.wash_crew_role_id,
                title=title,
            )
            await interaction.response.send_message(message, ephemeral=ephemeral)
            if success:
                logger.info(
                    "User %s removed watch item %r in guild %s",
                    interaction.user.id,
                    title,
                    interaction.guild_id,
                )

        @self.tree.command(name="start_vote")
        async def start_vote(interaction: discord.Interaction) -> None:
            async def on_use_defaults(choice_interaction: discord.Interaction) -> None:
                await handle_start_vote_use_defaults(
                    choice_interaction,
                    vote_service=self.vote_service,
                    suggestion_service=self.suggestion_service,
                    nominee_selection_service=self.nominee_selection_service,
                    wash_crew_role_id=self.wash_crew_role_id,
                    default_nominee_count=self.default_nominee_count,
                    scheduler_service=self.scheduler_host.scheduler_service,
                    guild_configuration_repository=self.guild_configuration_repository,
                )

            async def on_customize(choice_interaction: discord.Interaction) -> None:
                async def on_modal_submit(
                    modal_interaction: discord.Interaction,
                    nominee_count_text: Optional[str],
                    duration_days_text: Optional[str],
                    visibility_text: Optional[str],
                    reminder_enabled_text: Optional[str],
                    reminder_hours_text: Optional[str],
                ) -> None:
                    await handle_customize_vote_submit(
                        modal_interaction,
                        vote_service=self.vote_service,
                        suggestion_service=self.suggestion_service,
                        nominee_selection_service=self.nominee_selection_service,
                        wash_crew_role_id=self.wash_crew_role_id,
                        default_nominee_count=self.default_nominee_count,
                        nominee_count_text=nominee_count_text,
                        duration_days_text=duration_days_text,
                        visibility_text=visibility_text,
                        reminder_enabled_text=reminder_enabled_text,
                        reminder_hours_text=reminder_hours_text,
                        scheduler_service=self.scheduler_host.scheduler_service,
                        guild_configuration_repository=self.guild_configuration_repository,
                    )

                await choice_interaction.response.send_modal(CustomizeVoteModal(on_modal_submit))

            view = StartVoteChoiceView(on_use_defaults, on_customize)
            await interaction.response.send_message(
                "How would you like to start this voting round?", view=view, ephemeral=True
            )

        @self.tree.command(name="vote_status")
        async def vote_status(interaction: discord.Interaction) -> None:
            permission = self.permission_service.require_wash_crew(interaction.user)
            if not permission.allowed:
                await interaction.response.send_message(permission.message, ephemeral=True)
                return
            message = perform_vote_status(
                vote_service=self.vote_service,
                suggestion_service=self.suggestion_service,
            )
            await interaction.response.send_message(message, ephemeral=True)

        @self.tree.command(name="edit_vote")
        async def edit_vote(interaction: discord.Interaction) -> None:
            message, ephemeral, vote_round = perform_edit_vote_open(
                vote_service=self.vote_service,
                suggestion_service=self.suggestion_service,
                user=interaction.user,
                wash_crew_role_id=self.wash_crew_role_id,
            )
            if vote_round is None:
                await interaction.response.send_message(message, ephemeral=ephemeral)
                return

            round_id = vote_round.id

            async def on_change_end_time(button_interaction: discord.Interaction) -> None:
                async def on_modal_submit(
                    modal_interaction: discord.Interaction, when_text: str
                ) -> None:
                    await handle_change_vote_end_time_completion(
                        modal_interaction,
                        vote_service=self.vote_service,
                        suggestion_service=self.suggestion_service,
                        wash_crew_role_id=self.wash_crew_role_id,
                        round_id=round_id,
                        when=when_text,
                        bot=self,
                        scheduler_service=self.scheduler_host.scheduler_service,
                        guild_configuration_repository=self.guild_configuration_repository,
                    )

                await button_interaction.response.send_modal(
                    EditVoteEndTimeModal(
                        on_modal_submit,
                        current_value=format_datetime_for_display(vote_round.closes_at),
                    )
                )

            async def on_end_now(button_interaction: discord.Interaction) -> None:
                async def on_confirm(confirm_interaction: discord.Interaction) -> None:
                    await handle_end_vote_now_completion(
                        confirm_interaction,
                        vote_completion_service=self.vote_completion_service,
                        vote_service=self.vote_service,
                        suggestion_service=self.suggestion_service,
                        wash_crew_role_id=self.wash_crew_role_id,
                        round_id=round_id,
                        bot=self,
                        scheduler_service=self.scheduler_host.scheduler_service,
                    )

                async def on_abort(abort_interaction: discord.Interaction) -> None:
                    await abort_interaction.response.send_message(
                        "No changes were made.", ephemeral=True
                    )

                confirmation_view = EditVoteConfirmationView(
                    confirm_label="End Now", on_confirm=on_confirm, on_abort=on_abort
                )
                await button_interaction.response.send_message(
                    f"Are you sure you want to end voting round {round_id} now? "
                    "This cannot be undone.",
                    view=confirmation_view,
                    ephemeral=True,
                )

            async def on_cancel_vote(button_interaction: discord.Interaction) -> None:
                async def on_confirm(confirm_interaction: discord.Interaction) -> None:
                    await handle_cancel_vote_now_completion(
                        confirm_interaction,
                        vote_service=self.vote_service,
                        wash_crew_role_id=self.wash_crew_role_id,
                        round_id=round_id,
                        bot=self,
                        scheduler_service=self.scheduler_host.scheduler_service,
                    )

                async def on_abort(abort_interaction: discord.Interaction) -> None:
                    await abort_interaction.response.send_message(
                        "No changes were made.", ephemeral=True
                    )

                confirmation_view = EditVoteConfirmationView(
                    confirm_label="Cancel Vote", on_confirm=on_confirm, on_abort=on_abort
                )
                await button_interaction.response.send_message(
                    f"Are you sure you want to cancel voting round {round_id}? "
                    "This cannot be undone.",
                    view=confirmation_view,
                    ephemeral=True,
                )

            view = EditVoteManagementView(on_change_end_time, on_end_now, on_cancel_vote)
            await interaction.response.send_message(message, view=view, ephemeral=ephemeral)

        @self.tree.command(name="reject")
        async def reject(interaction: discord.Interaction, suggestion_id: int) -> None:
            message, ephemeral = perform_reject_suggestion(
                suggestion_service=self.suggestion_service,
                suggestion_database_configuration_repository=self.suggestion_database_configuration_repository,
                permission_service=self.permission_service,
                user=interaction.user,
                guild_id=interaction.guild_id,
                suggestion_id=suggestion_id,
            )
            await interaction.response.send_message(message, ephemeral=ephemeral)

        @self.tree.command(name="unreject")
        async def unreject(interaction: discord.Interaction, suggestion_id: int) -> None:
            message, ephemeral = perform_remove_rejection(
                suggestion_service=self.suggestion_service,
                permission_service=self.permission_service,
                user=interaction.user,
                suggestion_id=suggestion_id,
            )
            await interaction.response.send_message(message, ephemeral=ephemeral)

        @self.tree.command(name="database_add")
        async def database_add(interaction: discord.Interaction, name: str) -> None:
            message, ephemeral = perform_database_add(
                suggestion_service=self.suggestion_service,
                user=interaction.user,
                wash_crew_role_id=self.wash_crew_role_id,
                guild_id=interaction.guild_id,
                channel_id=interaction.channel_id,
                name=name,
            )
            await interaction.response.send_message(message, ephemeral=ephemeral)

        @self.tree.command(name="database_list")
        async def database_list(interaction: discord.Interaction) -> None:
            message, ephemeral = perform_database_list(
                suggestion_service=self.suggestion_service,
                user=interaction.user,
                wash_crew_role_id=self.wash_crew_role_id,
                guild_id=interaction.guild_id,
            )
            await interaction.response.send_message(message, ephemeral=ephemeral)

        @self.tree.command(name="database_remove")
        async def database_remove(interaction: discord.Interaction, database_id: int) -> None:
            message, ephemeral = perform_database_remove(
                suggestion_service=self.suggestion_service,
                user=interaction.user,
                wash_crew_role_id=self.wash_crew_role_id,
                guild_id=interaction.guild_id,
                database_id=database_id,
            )
            await interaction.response.send_message(message, ephemeral=ephemeral)

        @self.tree.command(name="schedule_watch_party")
        async def schedule_watch_party(
            interaction: discord.Interaction, watch_item_id: int, when: str
        ) -> None:
            await handle_schedule_watch_party_completion(
                interaction,
                watch_party_service=self.watch_party_service,
                suggestion_service=self.suggestion_service,
                wash_crew_role_id=self.wash_crew_role_id,
                watch_item_id=watch_item_id,
                when=when,
                scheduler_service=self.scheduler_host.scheduler_service,
                guild_configuration_repository=self.guild_configuration_repository,
            )

        @self.tree.command(name="reschedule_watch_party")
        async def reschedule_watch_party(
            interaction: discord.Interaction, watch_party_id: int, when: str
        ) -> None:
            await handle_reschedule_watch_party_completion(
                interaction,
                watch_party_service=self.watch_party_service,
                wash_crew_role_id=self.wash_crew_role_id,
                watch_party_id=watch_party_id,
                when=when,
                scheduler_service=self.scheduler_host.scheduler_service,
                guild_configuration_repository=self.guild_configuration_repository,
            )

        @self.tree.command(name="cancel_watch_party")
        async def cancel_watch_party(interaction: discord.Interaction, watch_party_id: int) -> None:
            await handle_cancel_watch_party_completion(
                interaction,
                watch_party_service=self.watch_party_service,
                wash_crew_role_id=self.wash_crew_role_id,
                watch_party_id=watch_party_id,
                scheduler_service=self.scheduler_host.scheduler_service,
            )

        @self.tree.command(name="watch_party_status")
        async def watch_party_status(interaction: discord.Interaction) -> None:
            permission = self.permission_service.require_wash_crew(interaction.user)
            if not permission.allowed:
                await interaction.response.send_message(permission.message, ephemeral=True)
                return
            message = perform_watch_party_status(
                watch_party_service=self.watch_party_service,
                suggestion_service=self.suggestion_service,
            )
            await interaction.response.send_message(message)

        @self.tree.command(name="setup")
        async def setup(interaction: discord.Interaction) -> None:
            message, blocked = perform_setup_permission_check(interaction.user, self.wash_crew_role_id)
            if blocked:
                await interaction.response.send_message(message, ephemeral=True)
                return

            guild_id = interaction.guild_id
            if guild_id is None:
                await interaction.response.send_message("Setup can only be run inside a server.", ephemeral=True)
                return

            redirect_message = perform_setup_redirect_check(self.guild_configuration_repository.get(guild_id))
            if redirect_message is not None:
                await interaction.response.send_message(redirect_message, ephemeral=True)
                return

            state, resumed = self.setup_wizard_service.start_or_resume(guild_id)

            if resumed:
                async def on_continue(resume_interaction: discord.Interaction) -> None:
                    await send_setup_wizard_step(resume_interaction, self, state, edit=True)

                async def on_review(resume_interaction: discord.Interaction) -> None:
                    reviewed = self.setup_wizard_service.go_to_step(state, SetupWizardStep.REVIEW)
                    await send_setup_wizard_step(resume_interaction, self, reviewed, edit=True)

                async def on_restart(resume_interaction: discord.Interaction) -> None:
                    restarted = self.setup_wizard_service.restart(guild_id)
                    await send_setup_wizard_step(resume_interaction, self, restarted, edit=True)

                view = SetupWizardResumeView(on_continue, on_review, on_restart)
                await interaction.response.send_message(
                    "A setup attempt is already in progress. What would you like to do?",
                    view=view,
                    ephemeral=True,
                )
                return

            await send_setup_wizard_step(interaction, self, state, edit=False)

        @self.tree.command(name="config")
        async def config(interaction: discord.Interaction) -> None:
            permission = self.permission_service.require_wash_crew(interaction.user)
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

        self.tree.add_command(WatchPartyAdminGroup(self))

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
        # restore_persistent_voting_view() reads current open-round state,
        # rather than racing scheduler_host.start()'s background task for
        # its first turn on the event loop.
        try:
            await self.scheduler_host.scheduler_service.run_once()
        except Exception:
            logger.exception("Error while checking for due scheduled jobs during startup")

        self.interactive_voting_restored = restore_persistent_voting_view(
            bot=self,
            vote_service=self.vote_service,
            suggestion_service=self.suggestion_service,
            permission_service=self.permission_service,
        )

        self.suggestion_views_restored = await restore_persistent_suggestion_views(
            bot=self,
            suggestion_service=self.suggestion_service,
            suggestion_database_configuration_repository=self.suggestion_database_configuration_repository,
            permission_service=self.permission_service,
        )

        self.membership_views_restored = restore_persistent_membership_approval_views(
            self, self.membership_service
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
            "%s active suggestion(s), open voting round: %s, interactive controls restored: %s, "
            "%s suggestion view(s) restored",
            snapshot.total_databases,
            snapshot.active_databases,
            snapshot.total_watch_items,
            snapshot.active_suggestions,
            "yes" if snapshot.open_vote_rounds else "no",
            "yes" if self.interactive_voting_restored else "no",
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


def parse_vote_visibility(value: str) -> VoteVisibility:
    """Parse a /start_vote visibility option into a VoteVisibility.

    This is the sole place visibility text gets validated. Both the
    default and customized /start_vote paths rely on this helper.

    Args:
        value: The raw text entered for the visibility option, expected to
            be "blind" or "visible" (case-insensitive, whitespace ignored).

    Returns:
        The matching VoteVisibility.

    Raises:
        ValueError: If the value isn't "blind" or "visible".
    """
    normalized = value.strip().lower()
    try:
        return VoteVisibility(normalized)
    except ValueError:
        raise ValueError("Visibility must be 'blind' or 'visible'.")


def parse_vote_duration_days(duration_days: Optional[int]) -> int:
    """Validate and resolve a /start_vote duration_days option.

    Args:
        duration_days: The raw duration option, or None to use the default.

    Returns:
        DEFAULT_VOTE_DURATION_DAYS if duration_days is None, otherwise
        duration_days itself once validated.

    Raises:
        ValueError: If duration_days is outside
            [MIN_VOTE_DURATION_DAYS, MAX_VOTE_DURATION_DAYS].
    """
    if duration_days is None:
        return DEFAULT_VOTE_DURATION_DAYS

    if not (MIN_VOTE_DURATION_DAYS <= duration_days <= MAX_VOTE_DURATION_DAYS):
        raise ValueError(
            f"duration_days must be between {MIN_VOTE_DURATION_DAYS} and "
            f"{MAX_VOTE_DURATION_DAYS}."
        )

    return duration_days


# Bounds for a per-round reminder-hours override, matching
# VoteNotificationsConfig.reminder_hours_before_close's own guild-level
# bounds (domain/guild_configuration.py) so a round can never be
# configured with a value the guild-level setting itself would reject.
MIN_VOTE_REMINDER_HOURS_BEFORE_CLOSE = 1
MAX_VOTE_REMINDER_HOURS_BEFORE_CLOSE = 720


def parse_vote_reminder_hours_before_close(hours: Optional[int]) -> Optional[int]:
    """Validate a /start_vote reminder-hours-before-close override.

    Args:
        hours: The raw reminder_hours option, or None to use the guild's
            configured default (see
            scheduler.vote_scheduling.resolve_vote_reminder_settings).

    Returns:
        None if hours is None (use the guild default), otherwise hours
        itself once validated.

    Raises:
        ValueError: If hours is outside
            [MIN_VOTE_REMINDER_HOURS_BEFORE_CLOSE, MAX_VOTE_REMINDER_HOURS_BEFORE_CLOSE].
    """
    if hours is None:
        return None

    if not (MIN_VOTE_REMINDER_HOURS_BEFORE_CLOSE <= hours <= MAX_VOTE_REMINDER_HOURS_BEFORE_CLOSE):
        raise ValueError(
            f"reminder_hours must be between {MIN_VOTE_REMINDER_HOURS_BEFORE_CLOSE} and "
            f"{MAX_VOTE_REMINDER_HOURS_BEFORE_CLOSE}."
        )

    return hours


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
            f"nominee_count must be between {MIN_VOTE_CANDIDATE_COUNT} and "
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
        "Add a movie with `/add` followed by a movie title or IMDb link."
    )


def build_start_vote_confirmation(
    vote_round: VoteRound, candidate_count: int, pool_count: Optional[int] = None
) -> str:
    """Build the /start_vote confirmation message.

    Args:
        vote_round: The newly created round.
        candidate_count: How many suggestions were available to vote on.

    Returns:
        A message with the round ID, visibility, candidate count, end
        time, and vote-change allowance. Never includes individual votes.
    """
    return (
        f"Voting round {vote_round.id} is now open.\n"
        f"Visibility: {vote_round.visibility.value.capitalize()}\n"
        f"Candidates: {candidate_count}\n"
        f"Voting ends: {format_datetime_for_display(vote_round.closes_at)}\n"
        f"Vote changes allowed: {format_vote_changes_setting()}"
        f"{build_low_suggestion_pool_warning(pool_count if pool_count is not None else candidate_count)}"
    )


def build_vote_status_text(
    vote_round: VoteRound,
    candidate_count: int,
    standings: Optional[List[StandingsEntry]],
    standings_error: Optional[str],
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

    Returns:
        The formatted status text. Total votes cast is always shown,
        regardless of visibility — only per-suggestion standings are ever
        withheld. Includes a link to the original voting post when the
        round has enough Discord message metadata to build one (see
        build_vote_link); omitted entirely for legacy rounds that don't.
    """
    lines = [
        f"Voting round {vote_round.id}",
        f"Status: {vote_round.status.value.capitalize()}",
        f"Visibility: {vote_round.visibility.value.capitalize()}",
        f"Candidates: {candidate_count}",
        f"Votes cast: {len(vote_round.votes)}",
        f"Voting ends: {format_datetime_for_display(vote_round.closes_at)}",
        f"Vote changes allowed: {format_vote_changes_setting()}",
    ]
    link = build_vote_link(vote_round)
    if link:
        lines.append(f"Original post: {link}")
    lines.extend(format_standings_lines(standings, standings_error))

    return "\n".join(lines)


def perform_start_vote(
    vote_service: VoteService,
    suggestion_service: SuggestionService,
    nominee_selection_service: Optional[NomineeSelectionService],
    user: object,
    wash_crew_role_id: Optional[int],
    visibility_str: str,
    duration_days: Optional[int],
    nominee_count: Optional[int] = None,
    default_nominee_count: int = DEFAULT_VOTE_CANDIDATE_COUNT,
    guild_id: Optional[int] = None,
    channel_id: Optional[int] = None,
    reminder_enabled: Optional[bool] = None,
    reminder_hours_before_close: Optional[int] = None,
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
        visibility_str: The raw visibility option text ("blind"/"visible").
        duration_days: The raw duration option, or None for the default.
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
        reminder_hours_before_close: FR-027: a per-round override of how
            many hours before closing the reminder fires, or None to use
            the guild default.

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

    try:
        visibility = parse_vote_visibility(visibility_str)
    except ValueError as exc:
        return str(exc), True

    try:
        days = parse_vote_duration_days(duration_days)
    except ValueError as exc:
        return str(exc), True

    try:
        count = parse_vote_nominee_count(nominee_count, default=default_nominee_count)
    except ValueError as exc:
        return str(exc), True

    try:
        reminder_hours_before_close = parse_vote_reminder_hours_before_close(reminder_hours_before_close)
    except ValueError as exc:
        return str(exc), True

    resolution = None
    if guild_id is not None and channel_id is not None and nominee_selection_service is not None:
        resolution = suggestion_service.resolve_database_for_channel(guild_id, channel_id)
        if resolution.database is None:
            return resolution.error_message or "No suggestion database is available here.", True
        candidates = nominee_selection_service.select_nominees(resolution.database.database_id, count)
    else:
        # No database context (or no selection service configured): fall
        # back to a simple, non-database-scoped pool, same low-pool rule
        # applied below.
        available = suggestion_service.get_suggestions()
        if len(available) >= count:
            candidates = available[:count]
        else:
            candidates = available

    if len(candidates) < MIN_CANDIDATES_FOR_A_ROUND:
        return (
            f"At least {MIN_CANDIDATES_FOR_A_ROUND} eligible suggestions are needed to start this vote.",
            True,
        )

    closes_at = datetime.now(timezone.utc) + timedelta(days=days)
    result = vote_service.create_round(
        visibility=visibility,
        closes_at=closes_at,
        candidate_suggestion_ids=[candidate.id for candidate in candidates],
        database_id=(resolution.database.database_id if resolution is not None else None),
        reminder_enabled=reminder_enabled,
        reminder_hours_before_close=reminder_hours_before_close,
    )
    if not result.success:
        return result.message, True

    if resolution is not None:
        pool_count = len(suggestion_service.get_suggestions_for_database(resolution.database.database_id))
    else:
        pool_count = suggestion_service.suggestion_count()
    return build_start_vote_confirmation(
        result.vote_round, len(candidates), pool_count=pool_count
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


def perform_setup_permission_check(user: object, wash_crew_role_id: Optional[int]) -> tuple[str, bool]:
    """Gate /setup.

    /setup is the one administrative command that must remain usable
    before a WASH Crew role has been configured -- otherwise nobody could
    ever complete initial setup to configure that role in the first
    place. Once a WASH Crew role IS configured, /setup falls back to the
    same fail-closed rule as every other administrative command, so a
    completed setup can't be silently redone by an unauthorized member.

    Returns:
        (message, blocked) -- blocked is True if the command should stop
        here and show `message` instead of proceeding.
    """
    if wash_crew_role_id is not None and not is_wash_crew_member(user, wash_crew_role_id):
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
        return "Setup has already been completed for this server. Use `/config` to review or change settings."
    return None


SETUP_WIZARD_STEP_TITLES: dict[SetupWizardStep, str] = {
    SetupWizardStep.WASH_CREW_ROLE: "WASH Crew Role",
    SetupWizardStep.WATCH_PARTY_ROLE: "Watch Party Role",
    SetupWizardStep.ADMIN_CHANNEL: "Admin Channel",
    SetupWizardStep.SUGGESTION_DATABASE: "Suggestion Database",
    SetupWizardStep.WATCH_DESTINATION: "Watched Movie Destination",
    SetupWizardStep.VOTING_DEFAULTS: "Voting Defaults",
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
        raise ValueError("Default nominee count must be a whole number.")
    if not (MIN_VOTE_CANDIDATE_COUNT <= count <= MAX_VOTE_CANDIDATE_COUNT):
        raise ValueError(
            f"Default nominee count must be between {MIN_VOTE_CANDIDATE_COUNT} and {MAX_VOTE_CANDIDATE_COUNT}."
        )
    return count


def parse_setup_voting_duration_days(value: str) -> int:
    """Validate a Voting Defaults modal's duration field, reusing /start_vote's bounds."""
    try:
        days = int(value.strip())
    except ValueError:
        raise ValueError("Default vote duration must be a whole number of days.")
    if not (MIN_VOTE_DURATION_DAYS <= days <= MAX_VOTE_DURATION_DAYS):
        raise ValueError(
            f"Default vote duration must be between {MIN_VOTE_DURATION_DAYS} and {MAX_VOTE_DURATION_DAYS} days."
        )
    return days


def parse_setup_voting_visibility(value: str) -> GuildVoteVisibility:
    """Validate a Voting Defaults modal's visibility field."""
    normalized = value.strip().lower()
    try:
        return GuildVoteVisibility(normalized)
    except ValueError:
        raise ValueError("Default visibility must be 'blind' or 'visible'.")


def parse_setup_candidate_selection(value: str) -> CandidateSelectionMode:
    """Validate a Voting Defaults modal's candidate-selection field."""
    normalized = value.strip().lower()
    try:
        return CandidateSelectionMode(normalized)
    except ValueError:
        raise ValueError("Candidate selection must be 'random' or 'balanced_random'.")


def parse_setup_reminder_enabled(value: str) -> bool:
    """Validate a Reminder Defaults modal's enabled field.

    Unlike parse_optional_bool_field's "blank means default" convention,
    this field is required -- the wizard's modal always pre-fills it, so
    a blank submission means the WASH Crew member cleared it deliberately
    and should be asked to re-enter it rather than silently falling back.
    """
    parsed = parse_optional_bool_field(value)
    if parsed is None:
        raise ValueError("Reminder enabled must be 'yes' or 'no'.")
    return parsed


def parse_setup_reminder_hours_before_close(value: str) -> int:
    """Validate a Reminder Defaults modal's hours-before-close field.

    Reuses the same bounds as a /start_vote per-round reminder override
    (MIN/MAX_VOTE_REMINDER_HOURS_BEFORE_CLOSE).
    """
    try:
        hours = int(value.strip())
    except ValueError:
        raise ValueError("Reminder hours before close must be a whole number.")
    if not (MIN_VOTE_REMINDER_HOURS_BEFORE_CLOSE <= hours <= MAX_VOTE_REMINDER_HOURS_BEFORE_CLOSE):
        raise ValueError(
            "Reminder hours before close must be between "
            f"{MIN_VOTE_REMINDER_HOURS_BEFORE_CLOSE} and {MAX_VOTE_REMINDER_HOURS_BEFORE_CLOSE}."
        )
    return hours


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
    lines.append(f"WASH Crew Role: <@&{configuration.wash_crew_role_id}>")
    if configuration.watch_party_role.role_id is not None:
        lines.append(
            f"Watch Party Role: <@&{configuration.watch_party_role.role_id}> "
            f"(join mode: {configuration.watch_party_role.join_mode.value})"
        )
    else:
        lines.append("Watch Party Role: Not set")
    lines.append(f'Suggestion Database: "{draft.suggestion_database_name}" (#{draft.suggestion_database_id})')
    if draft.watch_destination_skipped:
        lines.append("Watched Movie Destination: Skipped (configure later)")
    elif draft.watch_destination_channel_id is not None:
        lines.append(f"Watched Movie Destination: <#{draft.watch_destination_channel_id}>")
    lines.append(
        "Voting Defaults: "
        f"{configuration.voting_defaults.candidate_count} nominees, "
        f"{configuration.voting_defaults.duration_days} day(s), "
        f"{configuration.voting_defaults.visibility.value}"
    )
    if configuration.notifications.vote.vote_ending_reminder:
        lines.append(
            "Reminder Defaults: enabled, "
            f"{configuration.notifications.vote.reminder_hours_before_close}h before close"
        )
    else:
        lines.append("Reminder Defaults: disabled")
    if draft.backup_interval_days is not None:
        lines.append(
            f"Backup Defaults: every {draft.backup_interval_days} day(s), keep {draft.backup_retention_count}"
        )
    return "\n".join(lines)


async def send_setup_wizard_step(
    interaction: discord.Interaction,
    bot: "WatchPartyBot",
    state: SetupWizardState,
    *,
    edit: bool,
    error_message: Optional[str] = None,
) -> None:
    """Render whichever step `state.current_step` points to.

    Every step's callbacks recursively call this again with the next
    state to render the following step in the SAME ephemeral message
    (via edit_message) -- see each `on_*` closure below. This is the one
    place that knows how to turn a SetupWizardState into a Discord
    message; the service layer never touches discord.ui objects.
    """
    setup_wizard_service = bot.setup_wizard_service
    suggestion_service = bot.suggestion_service
    guild_id = state.guild_id

    async def on_cancel(cancel_interaction: discord.Interaction) -> None:
        setup_wizard_service.cancel(guild_id)
        await cancel_interaction.response.edit_message(
            content="Setup has been cancelled. No configuration was changed.", view=None
        )

    header = build_setup_step_header(state)
    body = header if not error_message else f"{header}\n\n⚠ {error_message}"
    step = state.current_step
    view: discord.ui.View

    if step == SetupWizardStep.WASH_CREW_ROLE:

        async def on_select(select_interaction: discord.Interaction, role_id: int) -> None:
            updated = setup_wizard_service.set_wash_crew_role(state, role_id)
            await send_setup_wizard_step(select_interaction, bot, updated, edit=True)

        view = WashCrewRoleStepView(on_select, on_cancel)
        body += "\n\nSelect the Discord role that should control administrative access to WASH."

    elif step == SetupWizardStep.WATCH_PARTY_ROLE:

        async def on_confirm(
            confirm_interaction: discord.Interaction, role_id: Optional[int], join_mode: JoinMode
        ) -> None:
            updated = setup_wizard_service.set_watch_party_role(state, role_id, join_mode)
            await send_setup_wizard_step(confirm_interaction, bot, updated, edit=True)

        view = WatchPartyRoleStepView(on_confirm, on_cancel)
        body += (
            "\n\nSelect the Watch Party role (optional) and its join mode, then press Continue."
        )

    elif step == SetupWizardStep.ADMIN_CHANNEL:

        async def on_select(select_interaction: discord.Interaction, channel_id: int) -> None:
            updated = setup_wizard_service.set_admin_channel(state, channel_id)
            await send_setup_wizard_step(select_interaction, bot, updated, edit=True)

        async def on_skip(skip_interaction: discord.Interaction) -> None:
            updated = setup_wizard_service.skip_admin_channel(state)
            await send_setup_wizard_step(skip_interaction, bot, updated, edit=True)

        view = AdminChannelStepView(on_select, on_skip, on_cancel)
        body += (
            "\n\nSelect the channel where Approval-Required membership requests should be "
            "posted for WASH Crew, or skip for now."
        )

    elif step == SetupWizardStep.SUGGESTION_DATABASE:

        async def on_select_existing(choice_interaction: discord.Interaction) -> None:
            databases = [(d.database_id, d.name) for d in suggestion_service.list_databases(guild_id)]
            if not databases:
                await choice_interaction.response.edit_message(
                    content=body + "\n\nNo suggestion databases exist yet in this server. Choose Create New instead.",
                    view=SuggestionDatabaseChoiceView(on_select_existing, on_create_new, on_cancel),
                )
                return

            async def on_database_selected(select_interaction: discord.Interaction, database_id: int) -> None:
                updated, message = setup_wizard_service.select_existing_database(
                    state, database_id, guild_id=guild_id
                )
                await send_setup_wizard_step(select_interaction, bot, updated, edit=True)

            await choice_interaction.response.edit_message(
                content=body + "\n\nChoose a suggestion database.",
                view=ExistingDatabaseSelectView(databases, on_database_selected, on_cancel),
            )

        async def on_create_new(choice_interaction: discord.Interaction) -> None:
            async def on_name_submit(modal_interaction: discord.Interaction, name: str) -> None:
                async def on_channel_selected(channel_interaction: discord.Interaction, channel_id: int) -> None:
                    updated, message = setup_wizard_service.create_new_database(
                        state, name, channel_id, guild_id=guild_id
                    )
                    await send_setup_wizard_step(channel_interaction, bot, updated, edit=True)

                await modal_interaction.response.edit_message(
                    content=body + f'\n\nWhich channel or thread should "{name}" use?',
                    view=CreateDatabaseChannelSelectView(on_channel_selected, on_cancel),
                )

            await choice_interaction.response.send_modal(CreateDatabaseNameModal(on_name_submit))

        view = SuggestionDatabaseChoiceView(on_select_existing, on_create_new, on_cancel)
        body += "\n\nSelect an existing suggestion database or create a new one."

    elif step == SetupWizardStep.WATCH_DESTINATION:

        async def on_select(select_interaction: discord.Interaction, channel_id: int) -> None:
            updated = setup_wizard_service.set_watch_destination(state, channel_id)
            await send_setup_wizard_step(select_interaction, bot, updated, edit=True)

        async def on_skip(skip_interaction: discord.Interaction) -> None:
            updated = setup_wizard_service.skip_watch_destination(state)
            await send_setup_wizard_step(skip_interaction, bot, updated, edit=True)

        view = WatchDestinationStepView(on_select, on_skip, on_cancel)
        body += "\n\nChoose where watched-movie history should be posted, or skip for now."

    elif step == SetupWizardStep.VOTING_DEFAULTS:

        async def on_configure(configure_interaction: discord.Interaction) -> None:
            async def on_submit(
                modal_interaction: discord.Interaction,
                candidate_count_text: str,
                duration_days_text: str,
                visibility_text: str,
                candidate_selection_text: str,
            ) -> None:
                try:
                    candidate_count = parse_setup_voting_candidate_count(candidate_count_text)
                    duration_days = parse_setup_voting_duration_days(duration_days_text)
                    visibility = parse_setup_voting_visibility(visibility_text)
                    candidate_selection = parse_setup_candidate_selection(candidate_selection_text)
                except ValueError as exc:
                    await modal_interaction.response.edit_message(
                        content=body + f"\n\n⚠ {exc}",
                        view=ModalStepIntroView(
                            on_configure,
                            on_cancel,
                            button_label="Set Voting Defaults",
                            custom_id="wpm_setup_voting_defaults_configure",
                        ),
                    )
                    return

                updated = setup_wizard_service.set_voting_defaults(
                    state, candidate_count, duration_days, visibility, candidate_selection
                )
                await send_setup_wizard_step(modal_interaction, bot, updated, edit=True)

            await configure_interaction.response.send_modal(VotingDefaultsModal(on_submit))

        view = ModalStepIntroView(
            on_configure, on_cancel, button_label="Set Voting Defaults", custom_id="wpm_setup_voting_defaults_configure"
        )
        body += "\n\nConfigure the guild's default nominee count, vote duration, visibility, and candidate selection mode."

    elif step == SetupWizardStep.REMINDER_DEFAULTS:

        async def on_configure(configure_interaction: discord.Interaction) -> None:
            async def on_submit(
                modal_interaction: discord.Interaction, enabled_text: str, hours_text: str
            ) -> None:
                try:
                    enabled = parse_setup_reminder_enabled(enabled_text)
                    hours_before_close = parse_setup_reminder_hours_before_close(hours_text)
                except ValueError as exc:
                    await modal_interaction.response.edit_message(
                        content=body + f"\n\n⚠ {exc}",
                        view=ModalStepIntroView(
                            on_configure,
                            on_cancel,
                            button_label="Set Reminder Defaults",
                            custom_id="wpm_setup_reminder_defaults_configure",
                        ),
                    )
                    return

                updated = setup_wizard_service.set_reminder_defaults(state, enabled, hours_before_close)
                await send_setup_wizard_step(modal_interaction, bot, updated, edit=True)

            await configure_interaction.response.send_modal(ReminderDefaultsModal(on_submit))

        view = ModalStepIntroView(
            on_configure,
            on_cancel,
            button_label="Set Reminder Defaults",
            custom_id="wpm_setup_reminder_defaults_configure",
        )
        body += "\n\nConfigure whether a vote-ending reminder is sent, and how many hours before close."

    elif step == SetupWizardStep.BACKUP_DEFAULTS:

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
                            on_cancel,
                            button_label="Set Backup Defaults",
                            custom_id="wpm_setup_backup_defaults_configure",
                        ),
                    )
                    return

                updated = setup_wizard_service.set_backup_defaults(state, interval_days, retention_count)
                await send_setup_wizard_step(modal_interaction, bot, updated, edit=True)

            await configure_interaction.response.send_modal(BackupDefaultsModal(on_submit))

        view = ModalStepIntroView(
            on_configure, on_cancel, button_label="Set Backup Defaults", custom_id="wpm_setup_backup_defaults_configure"
        )
        body += "\n\nConfigure the automatic backup interval and how many backups to retain."

    else:  # SetupWizardStep.REVIEW

        async def on_save(save_interaction: discord.Interaction) -> None:
            guild = save_interaction.guild
            guild_name = guild.name if guild is not None else ""
            result = setup_wizard_service.finalize(state, guild_id, guild_name, guild)
            if not result.success:
                issue_lines = "\n".join(f"- {issue.step.value}: {issue.message}" for issue in result.issues)
                failed_step = result.issues[0].step if result.issues else SetupWizardStep.REVIEW
                redirected = setup_wizard_service.go_to_step(state, failed_step)
                await send_setup_wizard_step(
                    save_interaction,
                    bot,
                    redirected,
                    edit=True,
                    error_message=f"Setup could not be saved:\n{issue_lines}",
                )
                return

            bot.apply_role_configuration(
                result.configuration.wash_crew_role_id, result.configuration.watch_party_role.role_id
            )
            summary = build_setup_completion_summary(result.configuration, state.draft)
            await save_interaction.response.edit_message(content=summary, view=None)

        async def on_edit_section(select_interaction: discord.Interaction, step_value: str) -> None:
            target_step = SetupWizardStep(step_value)
            updated = setup_wizard_service.go_to_step(state, target_step)
            await send_setup_wizard_step(select_interaction, bot, updated, edit=True)

        review_lines = setup_wizard_service.build_review_lines(state)
        body += "\n\n" + "\n".join(review_lines)
        view = ReviewStepView(
            section_options=[
                (s.value, SETUP_WIZARD_STEP_TITLES[s]) for s in SETUP_WIZARD_STEP_ORDER if s != SetupWizardStep.REVIEW
            ],
            on_save=on_save,
            on_edit_section=on_edit_section,
            on_cancel=on_cancel,
        )

    if edit:
        await interaction.response.edit_message(content=body, view=view)
    else:
        await interaction.response.send_message(body, view=view, ephemeral=True)


# --- FR-029: /config -------------------------------------------------------------------


async def send_config_main_menu(
    interaction: discord.Interaction, bot: "WatchPartyBot", guild_id: int, *, edit: bool
) -> None:
    """Render /config's main menu: a live configuration summary plus a
    "choose a section to edit" dropdown.
    """
    config_service = bot.config_service
    lines = config_service.build_summary_lines(guild_id, interaction.guild)
    body = "**WASH Configuration**\n\n" + "\n".join(lines)

    async def on_section_chosen(select_interaction: discord.Interaction, section_value: str) -> None:
        await send_config_section(select_interaction, bot, guild_id, ConfigSection(section_value), edit=True)

    view = ConfigMainMenuView(
        [(section.value, CONFIG_SECTION_TITLES[section]) for section in CONFIG_SECTION_ORDER],
        on_section_chosen,
    )

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
    if section == ConfigSection.REMINDER_DEFAULTS:
        await send_config_reminder_defaults_modal(interaction, bot, guild_id, on_back)
        return
    if section == ConfigSection.BACKUP_DEFAULTS:
        await send_config_backup_defaults_modal(interaction, bot, guild_id, on_back)
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
            placeholder="Select the new WASH Crew role",
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
            placeholder="Select the new Watch Party role (optional)",
            min_values=0,
        )
        body += "\n\nSelect the Watch Party role, or leave blank to clear it."

    elif section == ConfigSection.WATCH_PARTY_JOIN_MODE:

        async def on_select(select_interaction: discord.Interaction, join_mode: JoinMode) -> None:
            result = config_service.set_watch_party_join_mode(guild_id, join_mode)
            await send_config_result(select_interaction, bot, guild_id, result)

        view = ConfigJoinModeSectionView(on_select, on_back)
        body += "\n\nSelect the Watch Party role's join mode."

    elif section == ConfigSection.SUGGESTION_DATABASE:
        databases = [(database.database_id, database.name) for database in bot.suggestion_service.list_databases(guild_id)]
        if not databases:
            view = BackToMenuOnlyView(on_back)
            body += "\n\nNo suggestion databases exist in this server yet."
        else:
            async def on_select(select_interaction: discord.Interaction, database_id: int) -> None:
                result = config_service.set_active_suggestion_database(guild_id, database_id)
                await send_config_result(select_interaction, bot, guild_id, result)

            view = ConfigDatabaseSectionView(databases, on_select, on_back)
            body += "\n\nSelect the suggestion database that should be active."

    elif section == ConfigSection.ADMIN_CHANNEL:

        async def on_select(select_interaction: discord.Interaction, channel_id: int) -> None:
            result = config_service.set_admin_channel(guild_id, channel_id, select_interaction.guild)
            await send_config_result(select_interaction, bot, guild_id, result)

        async def on_skip(skip_interaction: discord.Interaction) -> None:
            result = config_service.clear_admin_channel(guild_id)
            await send_config_result(skip_interaction, bot, guild_id, result)

        view = ConfigAdminChannelSectionView(on_select, on_skip, on_back)
        body += (
            "\n\nSelect the channel where Approval-Required membership requests should be "
            "posted for WASH Crew, or clear it."
        )

    else:  # ConfigSection.WATCH_DESTINATION

        async def on_select(select_interaction: discord.Interaction, channel_id: int) -> None:
            result = config_service.set_watch_destination(guild_id, channel_id, select_interaction.guild)
            await send_config_result(select_interaction, bot, guild_id, result)

        async def on_skip(skip_interaction: discord.Interaction) -> None:
            result = config_service.skip_watch_destination(guild_id)
            await send_config_result(skip_interaction, bot, guild_id, result)

        view = ConfigWatchDestinationSectionView(on_select, on_skip, on_back)
        body += "\n\nChoose where watched-movie history should be posted, or clear it."

    if edit:
        await interaction.response.edit_message(content=body, view=view)
    else:
        await interaction.response.send_message(body, view=view, ephemeral=True)


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


def _resolve_config_voting_defaults_modal_defaults(
    bot: "WatchPartyBot", guild_id: int
) -> Tuple[str, str, str, str]:
    """Pre-fill the Voting Defaults modal with the guild's current values."""
    configuration = bot.config_service.get_configuration(guild_id)
    candidate_selection = CandidateSelectionMode.BALANCED_RANDOM
    database = bot.config_service.resolve_configured_database(guild_id)
    if database is not None:
        database_configuration = bot.suggestion_database_configuration_repository.get(guild_id, database.database_id)
        if database_configuration is not None:
            candidate_selection = database_configuration.suggestion_rules.candidate_selection
    return (
        str(configuration.voting_defaults.candidate_count),
        str(configuration.voting_defaults.duration_days),
        configuration.voting_defaults.visibility.value,
        candidate_selection.value,
    )


async def send_config_voting_defaults_modal(
    interaction: discord.Interaction, bot: "WatchPartyBot", guild_id: int, on_back: OnBackToMenu
) -> None:
    config_service = bot.config_service

    async def on_retry(retry_interaction: discord.Interaction) -> None:
        await send_config_voting_defaults_modal(retry_interaction, bot, guild_id, on_back)

    async def on_submit(
        modal_interaction: discord.Interaction,
        candidate_count_text: str,
        duration_days_text: str,
        visibility_text: str,
        candidate_selection_text: str,
    ) -> None:
        try:
            candidate_count = parse_setup_voting_candidate_count(candidate_count_text)
            duration_days = parse_setup_voting_duration_days(duration_days_text)
            visibility = parse_setup_voting_visibility(visibility_text)
            candidate_selection = parse_setup_candidate_selection(candidate_selection_text)
        except ValueError as exc:
            view = ConfigModalRetryView(
                on_retry, on_back, button_label="Try Again", custom_id="wpm_config_voting_defaults_retry"
            )
            await modal_interaction.response.edit_message(
                content=f"**WASH Configuration -- Voting Defaults**\n\n⚠ {exc}", view=view
            )
            return

        result = config_service.set_voting_defaults(
            guild_id, candidate_count, duration_days, visibility, candidate_selection
        )
        await send_config_result(modal_interaction, bot, guild_id, result)

    defaults = _resolve_config_voting_defaults_modal_defaults(bot, guild_id)
    await interaction.response.send_modal(VotingDefaultsModal(on_submit, defaults=defaults))


async def send_config_reminder_defaults_modal(
    interaction: discord.Interaction, bot: "WatchPartyBot", guild_id: int, on_back: OnBackToMenu
) -> None:
    config_service = bot.config_service

    async def on_retry(retry_interaction: discord.Interaction) -> None:
        await send_config_reminder_defaults_modal(retry_interaction, bot, guild_id, on_back)

    async def on_submit(modal_interaction: discord.Interaction, enabled_text: str, hours_text: str) -> None:
        try:
            enabled = parse_setup_reminder_enabled(enabled_text)
            hours_before_close = parse_setup_reminder_hours_before_close(hours_text)
        except ValueError as exc:
            view = ConfigModalRetryView(
                on_retry, on_back, button_label="Try Again", custom_id="wpm_config_reminder_defaults_retry"
            )
            await modal_interaction.response.edit_message(
                content=f"**WASH Configuration -- Reminder Defaults**\n\n⚠ {exc}", view=view
            )
            return

        result = config_service.set_reminder_defaults(guild_id, enabled, hours_before_close)
        await send_config_result(modal_interaction, bot, guild_id, result)

    configuration = bot.config_service.get_configuration(guild_id)
    vote_notifications = configuration.notifications.vote
    defaults = (
        "yes" if vote_notifications.vote_ending_reminder else "no",
        str(vote_notifications.reminder_hours_before_close),
    )
    await interaction.response.send_modal(ReminderDefaultsModal(on_submit, defaults=defaults))


async def send_config_backup_defaults_modal(
    interaction: discord.Interaction, bot: "WatchPartyBot", guild_id: int, on_back: OnBackToMenu
) -> None:
    config_service = bot.config_service

    async def on_retry(retry_interaction: discord.Interaction) -> None:
        await send_config_backup_defaults_modal(retry_interaction, bot, guild_id, on_back)

    async def on_submit(modal_interaction: discord.Interaction, interval_text: str, retention_text: str) -> None:
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

        result = config_service.set_backup_defaults(guild_id, interval_days, retention_count)
        await send_config_result(modal_interaction, bot, guild_id, result)

    configuration = bot.config_service.get_configuration(guild_id)
    interval = configuration.backup.extra_fields.get(BACKUP_INTERVAL_DAYS_EXTRA_FIELD, 1)
    retention = configuration.backup.extra_fields.get(BACKUP_RETENTION_COUNT_EXTRA_FIELD, 30)
    defaults = (str(interval), str(retention))
    await interaction.response.send_modal(BackupDefaultsModal(on_submit, defaults=defaults))


# --- FR-030: /join_watch_party -----------------------------------------------------------


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
    """Handle /join_watch_party: the single entry point for every join mode.

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
        wash_crew_mention = f"<@&{bot.wash_crew_role_id}>" if bot.wash_crew_role_id else "WASH Crew"
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
    permission = bot.permission_service.require_wash_crew(interaction.user)
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


# --- FR-031: /watch_party administration --------------------------------------------------

WATCH_PARTY_LIST_PAGE_SIZE = 10


class WatchPartyAdminGroup(discord.app_commands.Group):
    """WASH Crew-only /watch_party command group.

    Subcommands only collect Discord-native parameters and delegate to
    module-level handle_watch_party_*() functions -- kept as thin as
    every other command in this file -- so the actual behavior stays
    unit-testable without a live Discord connection.
    """

    def __init__(self, bot: "WatchPartyBot") -> None:
        super().__init__(name="watch_party", description="Manage Watch Party membership (WASH Crew only).")
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Gate every /watch_party subcommand on WASH Crew, in one place.

        Reuses PermissionService.require_wash_crew exactly like every
        other WASH-gated command -- fails closed when unconfigured.
        """
        permission = self.bot.permission_service.require_wash_crew(interaction.user)
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


def build_watch_party_members_text(role_name: str, members: List[Any]) -> str:
    """Build /watch_party members' response text.

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
    duration_days_text: Optional[str],
    visibility_text: Optional[str],
    reminder_enabled_text: Optional[str] = None,
    reminder_hours_text: Optional[str] = None,
) -> tuple[Optional[int], Optional[int], str, Optional[bool], Optional[int]]:
    """Parse raw customization-modal values into start-vote arguments.

    Blank numeric fields remain ``None`` so :func:`perform_start_vote` can
    apply configured defaults. Blank visibility uses the established visible
    default. Blank reminder fields remain ``None`` so the guild's
    configured reminder default is used (see
    scheduler.vote_scheduling.resolve_vote_reminder_settings). Range and
    enum validation remain centralized in :func:`perform_start_vote`.
    """
    nominee_count = parse_optional_int_field(nominee_count_text)
    duration_days = parse_optional_int_field(duration_days_text)
    visibility = (visibility_text or "").strip() or "visible"
    reminder_enabled = parse_optional_bool_field(reminder_enabled_text)
    reminder_hours_before_close = parse_optional_int_field(reminder_hours_text)
    return nominee_count, duration_days, visibility, reminder_enabled, reminder_hours_before_close


async def handle_start_vote_completion(
    interaction: discord.Interaction,
    vote_service: VoteService,
    suggestion_service: SuggestionService,
    nominee_selection_service: Optional[NomineeSelectionService],
    wash_crew_role_id: Optional[int],
    visibility_str: str,
    duration_days: Optional[int],
    nominee_count: Optional[int],
    default_nominee_count: int,
    scheduler_service: Optional[SchedulerService] = None,
    guild_configuration_repository: Optional[GuildConfigurationRepository] = None,
    reminder_enabled: Optional[bool] = None,
    reminder_hours_before_close: Optional[int] = None,
) -> None:
    """Create a round and publish its interactive voting post.

    scheduler_service/guild_configuration_repository default to None so
    existing callers that don't pass them keep working unchanged; passing
    None simply skips scheduling (see schedule_vote_jobs).
    reminder_enabled/reminder_hours_before_close default to None so
    existing callers keep working unchanged too; passing None uses the
    guild's configured reminder default (see FR-027's
    resolve_vote_reminder_settings).
    """
    message, ephemeral = perform_start_vote(
        vote_service=vote_service,
        suggestion_service=suggestion_service,
        nominee_selection_service=nominee_selection_service,
        user=interaction.user,
        wash_crew_role_id=wash_crew_role_id,
        visibility_str=visibility_str,
        duration_days=duration_days,
        nominee_count=nominee_count,
        default_nominee_count=default_nominee_count,
        guild_id=interaction.guild_id,
        channel_id=interaction.channel_id,
        reminder_enabled=reminder_enabled,
        reminder_hours_before_close=reminder_hours_before_close,
    )
    if ephemeral:
        await interaction.response.send_message(message, ephemeral=True)
        return

    vote_round = vote_service.get_open_round()

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
    view = build_voting_view(
        vote_service=vote_service,
        suggestion_service=suggestion_service,
        candidates=candidates,
        permission_service=PermissionService(
            watch_party_member_role_id=parse_watch_party_member_role_id(
                os.getenv("WATCH_PARTY_MEMBER_ROLE_ID")
            ),
            wash_crew_role_id=wash_crew_role_id,
        ),
    )
    post_text = build_voting_post_text(
        vote_round, candidates, standings=None, standings_error=None
    )
    await interaction.response.send_message(post_text, view=view)
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
) -> None:
    """Start a visible round using the configured defaults."""
    await handle_start_vote_completion(
        interaction,
        vote_service,
        suggestion_service,
        nominee_selection_service,
        wash_crew_role_id,
        visibility_str="visible",
        duration_days=None,
        nominee_count=None,
        default_nominee_count=default_nominee_count,
        scheduler_service=scheduler_service,
        guild_configuration_repository=guild_configuration_repository,
    )


async def handle_customize_vote_submit(
    interaction: discord.Interaction,
    vote_service: VoteService,
    suggestion_service: SuggestionService,
    nominee_selection_service: Optional[NomineeSelectionService],
    wash_crew_role_id: Optional[int],
    default_nominee_count: int,
    nominee_count_text: Optional[str],
    duration_days_text: Optional[str],
    visibility_text: Optional[str],
    reminder_enabled_text: Optional[str] = None,
    reminder_hours_text: Optional[str] = None,
    scheduler_service: Optional[SchedulerService] = None,
    guild_configuration_repository: Optional[GuildConfigurationRepository] = None,
) -> None:
    """Start a round using optional one-time modal overrides."""
    try:
        nominee_count, duration_days, visibility_str, reminder_enabled, reminder_hours_before_close = (
            parse_start_vote_overrides(
                nominee_count_text, duration_days_text, visibility_text, reminder_enabled_text, reminder_hours_text
            )
        )
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    await handle_start_vote_completion(
        interaction,
        vote_service,
        suggestion_service,
        nominee_selection_service,
        wash_crew_role_id,
        visibility_str=visibility_str,
        duration_days=duration_days,
        nominee_count=nominee_count,
        default_nominee_count=default_nominee_count,
        scheduler_service=scheduler_service,
        guild_configuration_repository=guild_configuration_repository,
        reminder_enabled=reminder_enabled,
        reminder_hours_before_close=reminder_hours_before_close,
    )


def perform_vote_status(vote_service: VoteService, suggestion_service: SuggestionService) -> str:
    """Core logic for /vote_status, kept free of Discord objects entirely.

    Standings are shown when voting is visible, or once a blind round has
    closed. They're withheld only while a blind round is still open.

    Args:
        vote_service: The vote service to read round/standings from.
        suggestion_service: Used to report the current candidate count.

    Returns:
        The status message, or a clear "no round exists" message.
    """
    vote_round = vote_service.get_latest_round()
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
    return build_vote_status_text(vote_round, candidate_count, standings, standings_error)


def build_vote_confirmation(
    vote_record: VoteRecord, is_first_vote: bool, remaining_changes: int, vote_round: Optional[VoteRound] = None
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

    Returns:
        A confirmation message. Never mentions any other member's vote.
    """
    if is_first_vote:
        lines = [f"Your vote for suggestion #{vote_record.suggestion_id} has been recorded."]
    else:
        lines = [f"Your vote has been updated to suggestion #{vote_record.suggestion_id}."]
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


def perform_vote(vote_service: VoteService, user_id: int, suggestion_id: int) -> tuple[str, bool]:
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

    Returns:
        A (message, ephemeral) tuple. Every response is ephemeral — a
        member's own vote, and any standings shown alongside it, are for
        their eyes only.
    """
    open_round_before = vote_service.get_open_round()
    had_existing_vote = open_round_before is not None and user_id in open_round_before.votes

    result = vote_service.cast_vote(discord_user_id=user_id, suggestion_id=suggestion_id)
    if not result.success:
        return result.message, True

    # cast_vote() succeeded, so there is now an open round with this
    # member's vote recorded in it.
    vote_round = vote_service.get_open_round()
    vote_record = vote_round.votes[user_id]
    is_first_vote = not had_existing_vote
    remaining_changes = MAX_VOTE_CHANGES - vote_record.changes_used

    lines = [build_vote_confirmation(vote_record, is_first_vote, remaining_changes, vote_round)]

    if vote_round.visibility == VoteVisibility.VISIBLE:
        standings_result = vote_service.calculate_standings(vote_round.id)
        if standings_result.success:
            lines.extend(format_standings_lines(standings_result.standings, None))
        else:
            lines.extend(format_standings_lines(None, standings_result.message))

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


def restore_persistent_voting_view(
    bot: object,
    vote_service: VoteService,
    suggestion_service: SuggestionService,
    permission_service: Optional[PermissionService] = None,
) -> bool:
    """Restore button handling for the currently open voting post.

    Discord persistent views must be re-registered each time the bot starts.
    The round and its Discord message reference are already persisted, so
    this function reconstructs the same stable button custom IDs and binds
    the view to the original message.

    Returns:
        True when a view was registered, otherwise False.
    """
    vote_round = vote_service.get_open_round()
    if vote_round is None:
        logger.debug("No open voting round found; no persistent view to restore")
        return False
    if vote_round.message_id is None:
        logger.warning(
            "Open voting round %s has no message ID; interactive buttons cannot be restored",
            vote_round.id,
        )
        return False

    candidates = get_round_candidates(suggestion_service, vote_round)
    if not candidates:
        logger.warning(
            "Open voting round %s has no resolvable nominees; interactive buttons cannot be restored",
            vote_round.id,
        )
        return False

    view = build_voting_view(
        vote_service,
        suggestion_service,
        candidates,
        permission_service=permission_service,
    )
    bot.add_view(view, message_id=vote_round.message_id)
    logger.info(
        "Restored interactive voting controls for round %s on message %s",
        vote_round.id,
        vote_round.message_id,
    )
    return True


def restore_persistent_membership_approval_views(bot: "WatchPartyBot", membership_service: MembershipService) -> int:
    """Restore Approve/Deny button handling for every still-pending membership request.

    Every approval-request message is created fresh by this feature with
    its buttons already attached (unlike suggestion posts, which predate
    their button and needed a migration path) -- so, exactly like
    restore_persistent_voting_view, this only needs to re-register
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
) -> SuggestionView:
    """Build a suggestion's "I WILL NOT WATCH" view whose button uses the shared toggle handler.

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
    """
    threshold = resolve_rejection_threshold(
        suggestion_database_configuration_repository, guild_id, watch_item.database_id
    )

    async def on_toggle(interaction: discord.Interaction, suggestion_id: int) -> None:
        await handle_suggestion_rejection_toggle(
            interaction,
            suggestion_service,
            suggestion_database_configuration_repository,
            suggestion_id,
            permission_service=permission_service,
        )

    return SuggestionView(watch_item, threshold, on_toggle)


def _suggestion_message_has_reject_button(message: object, custom_id: str) -> bool:
    """Check whether a fetched suggestion message already carries this button.

    A real discord.py Message's components are a list of top-level
    ActionRows, each holding the actual Button/SelectMenu children -- so
    both the top-level components and one level of nested children are
    checked. Used by restore_persistent_suggestion_views() to decide
    whether a legacy message (posted before this feature existed) needs
    to be edited to attach the button, or already has it.
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
    permission_service: Optional[PermissionService] = None,
) -> int:
    """Restore, and where needed migrate, the "I WILL NOT WATCH" button for every active suggestion post.

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

    Archived suggestions are skipped entirely: their button is already
    disabled and permanently so (see SuggestionService.reject_suggestion/
    remove_rejection, which both refuse to touch an archived suggestion),
    so there's nothing to restore or migrate for them.

    Returns:
        The number of suggestion views restored or migrated.
    """
    restored = 0
    for watch_item in suggestion_service.get_suggestions():
        if watch_item.status == WatchItemStatus.ARCHIVED:
            continue
        if watch_item.message_id is None:
            continue

        view = build_suggestion_view(
            suggestion_service,
            suggestion_database_configuration_repository,
            watch_item,
            watch_item.guild_id,
            permission_service=permission_service,
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

        custom_id = build_reject_button_custom_id(watch_item.id)
        if _suggestion_message_has_reject_button(message, custom_id):
            bot.add_view(view, message_id=watch_item.message_id)
        else:
            try:
                await message.edit(view=view)
            except Exception:
                logger.warning(
                    "Could not attach the rejection button to suggestion %s's "
                    "legacy message %s; skipping",
                    watch_item.id,
                    watch_item.message_id,
                    exc_info=True,
                )
                continue
            logger.info(
                "Attached a new rejection button to legacy suggestion %s's message %s",
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
    kept in the same order as each candidate's vote button (not sorted
    by vote count) so the displayed numbering always matches the
    buttons below. Each candidate is its own paragraph: a numbered,
    linked title, followed for a visible round by its progress bar,
    vote count, and share. A blind round never reveals any of that.

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
    for position, candidate in enumerate(candidates, start=1):
        link = build_suggestion_link(candidate)
        title_display = f"[{candidate.title}]({link})" if link else candidate.title
        block = [f"{position}. {title_display}"]
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


def build_voting_post_text(
    vote_round: VoteRound,
    candidates: List[WatchItem],
    standings: Optional[List[StandingsEntry]],
    standings_error: Optional[str],
) -> str:
    """Build the public voting post message for a round.

    Used both for the initial post created by /start_vote and to refresh
    it after each vote. Reuses format_datetime_for_display and
    build_candidate_standings_lines rather than reformatting either here.

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

    Returns:
        The formatted post text. Total votes cast is always shown -- for a
        blind round that's the only vote information revealed; per-candidate
        counts, percentages, and progress bars are additionally shown for
        a visible round.
    """
    lines = [
        f"Voting round {vote_round.id} is open!",
        f"Visibility: {vote_round.visibility.value.capitalize()}",
        f"Voting ends: {format_datetime_for_display(vote_round.closes_at)}",
        "",
    ]
    lines.extend(build_candidate_standings_lines(candidates, vote_round, standings, standings_error))
    lines.append("")
    lines.append(f"Votes cast: {len(vote_round.votes)}")

    return "\n".join(lines)


def build_current_voting_post_text(
    vote_service: VoteService, suggestion_service: SuggestionService, vote_round: VoteRound
) -> str:
    """Recompute and build a round's voting post text from its current state.

    Shared by refresh_voting_post (called after each vote) and
    handle_change_vote_end_time_completion (called after WASH Crew edits
    the deadline via /edit_vote), so recomputing standings/candidates for
    the post is never duplicated between the two.

    Args:
        vote_service: Used to recompute standings.
        suggestion_service: Used to re-list the current nominees.
        vote_round: The round to build the post text for.

    Returns:
        The formatted post text (see build_voting_post_text).
    """
    candidates = get_round_candidates(suggestion_service, vote_round)
    standings_result = vote_service.calculate_standings(vote_round.id)
    standings = standings_result.standings if standings_result.success else None
    standings_error = None if standings_result.success else standings_result.message
    return build_voting_post_text(vote_round, candidates, standings, standings_error)


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
    text = build_current_voting_post_text(vote_service, suggestion_service, vote_round)
    await interaction.message.edit(content=text)


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

    message, ephemeral = perform_vote(vote_service, interaction.user.id, suggestion_id)
    await interaction.response.send_message(message, ephemeral=ephemeral)

    vote_round = vote_service.get_open_round()
    if vote_round is not None and vote_round.visibility == VoteVisibility.VISIBLE:
        await refresh_voting_post(interaction, vote_service, suggestion_service, vote_round)


# --- FR-023: /edit_vote -- WASH Crew administrative vote management -------------


async def update_voting_message(
    bot: object,
    vote_round: VoteRound,
    content: str,
    *,
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
        content: The new message content.
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
            await message.edit(content=content, view=None)
        else:
            await message.edit(content=content)
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
) -> tuple[str, bool, Optional[VoteRound]]:
    """Core logic for /edit_vote, kept free of Discord objects except `user`.

    Args:
        vote_service: Used to look up the currently open round.
        suggestion_service: Used to report the candidate count when the
            round has no fixed candidate list (a legacy round).
        user: The member invoking the command.
        wash_crew_role_id: The configured WASH Crew role ID, or None if
            unconfigured.

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

    vote_round = vote_service.get_open_round()
    if vote_round is None:
        return "There's no active voting round to manage.", True, None

    candidate_count = (
        len(vote_round.candidate_suggestion_ids)
        if vote_round.candidate_suggestion_ids
        else suggestion_service.suggestion_count()
    )
    return build_edit_vote_management_text(vote_round, candidate_count), True, vote_round


def perform_change_vote_end_time(
    vote_service: VoteService,
    user: object,
    wash_crew_role_id: Optional[int],
    round_id: int,
    when: str,
    *,
    now: Optional[datetime] = None,
) -> tuple[str, bool, Optional[VoteRound]]:
    """Core logic for /edit_vote's "Change End Time" action.

    Args:
        vote_service: Used to reschedule the round.
        user: The member invoking the action.
        wash_crew_role_id: The configured WASH Crew role ID, or None if unconfigured.
        round_id: The round to reschedule.
        when: The raw new end-time text from the modal.
        now: Passed through to parse_vote_end_time for deterministic testing.

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

    try:
        new_closes_at = parse_vote_end_time(when, now=now)
    except ValueError as exc:
        return str(exc), True, None

    result = vote_service.reschedule_round(round_id, new_closes_at)
    if not result.success:
        return result.message, True, None

    return (
        f"Voting round {round_id} rescheduled. New deadline: "
        f"{format_datetime_for_display(result.vote_round.closes_at)}",
        True,
        result.vote_round,
    )


async def handle_change_vote_end_time_completion(
    interaction: discord.Interaction,
    vote_service: VoteService,
    suggestion_service: SuggestionService,
    wash_crew_role_id: Optional[int],
    round_id: int,
    when: str,
    bot: object,
    scheduler_service: Optional[SchedulerService] = None,
    guild_configuration_repository: Optional[GuildConfigurationRepository] = None,
) -> None:
    """Change a round's deadline, replace its scheduler jobs, and notify the community.

    scheduler_service/guild_configuration_repository default to None so
    callers/tests that don't pass them keep working unchanged; passing
    None simply skips scheduling (see reschedule_vote_jobs).
    """
    message, ephemeral, vote_round = perform_change_vote_end_time(
        vote_service, interaction.user, wash_crew_role_id, round_id, when
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
        notice = build_vote_deadline_change_notice(vote_round)
        channel = bot.get_channel(vote_round.channel_id)
        if channel is None:
            channel = await bot.fetch_channel(vote_round.channel_id)
        await channel.send(notice)

    text = build_current_voting_post_text(vote_service, suggestion_service, vote_round)
    await update_voting_message(bot, vote_round, text)


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

    await finalize_vote_completion(vote_service, suggestion_service, bot, result)


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
) -> None:
    """Cancel a round, notify the community, and disable its original controls.

    scheduler_service defaults to None so callers/tests that don't pass
    one keep working unchanged; passing None simply skips job cancellation.
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
        notice = build_vote_cancellation_notice(vote_round)
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
    )


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


def perform_backup(
    backup_service: BackupService,
    user: object,
    wash_crew_role_id: Optional[int],
) -> tuple[str, bool]:
    """Create an immediate manual backup for the WASH Crew-only /backup command.

    Args:
        backup_service: The backup service to create the archive through.
        user: The member invoking the command.
        wash_crew_role_id: The configured WASH Crew role ID, or None if
            unconfigured.

    Returns:
        A (message, ephemeral) tuple. Every /backup response is ephemeral
        -- this is an admin maintenance command. On success, the message
        includes the created archive's filename.
    """
    if wash_crew_role_id is None:
        return (
            "WASH Crew permissions have not been configured. "
            "Set WASH_CREW_ROLE_ID before using this command.",
            True,
        )
    if not is_wash_crew_member(user, wash_crew_role_id):
        return "You need the WASH Crew role to create a backup.", True

    try:
        result = backup_service.create_backup(BackupKind.MANUAL)
    except BackupError as exc:
        return f"Backup failed: {exc}", True

    return f"Backup created: `{result.archive_path.name}`", True


def perform_restore(
    backup_service: BackupService,
    user: object,
    wash_crew_role_id: Optional[int],
    backup_filename: str,
) -> tuple[str, bool, bool]:
    """Validate a requested /restore target and build its confirmation prompt.

    This function never restores anything -- it only checks permissions
    and validates the requested backup, then hands back a message for
    bot.py to show alongside a confirmation view. The actual restore only
    happens if that confirmation is accepted (see perform_confirmed_restore).

    Args:
        backup_service: The backup service to look up and validate the
            requested archive through.
        user: The member invoking the command.
        wash_crew_role_id: The configured WASH Crew role ID, or None if
            unconfigured.
        backup_filename: The archive's filename, as reported by /backup or
            a previous /restore attempt's error message.

    Returns:
        A (message, ephemeral, needs_confirmation) tuple. needs_confirmation
        is False whenever there's nothing left to confirm -- a permission
        failure, an unknown filename, or a backup that fails validation --
        and True only when a valid backup was found and the member should
        be shown the confirm/cancel prompt.
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

    archive_path = find_backup_by_filename(backup_service, backup_filename)
    if archive_path is None:
        return build_backup_not_found_message(backup_service, backup_filename), True, False

    validation = backup_service.validate_backup(archive_path)
    if not validation.is_valid:
        detail = "; ".join(validation.errors) or "unknown validation error"
        return f"That backup failed validation and cannot be restored: {detail}", True, False

    return (
        f"Restoring from `{archive_path.name}` will overwrite WASH's current data with "
        "this backup's contents. A safety backup of the current data will be made "
        "first, but this action cannot be undone from within Discord. Proceed?",
        True,
        True,
    )


def perform_confirmed_restore(
    backup_service: BackupService,
    user: object,
    wash_crew_role_id: Optional[int],
    backup_filename: str,
) -> tuple[str, bool]:
    """Perform the actual restore after the member has confirmed.

    Re-checks the WASH Crew permission and re-resolves the requested
    backup rather than trusting anything carried over from the initial
    /restore call -- the confirmation button click is a separate
    interaction, and re-validating here keeps this function's own
    behavior correct regardless of how it's invoked.

    Args:
        backup_service: The backup service to restore through.
        user: The member who clicked "Confirm Restore".
        wash_crew_role_id: The configured WASH Crew role ID, or None if
            unconfigured.
        backup_filename: The archive's filename to restore from.

    Returns:
        A (message, ephemeral) tuple reporting success or any error.
    """
    if wash_crew_role_id is None:
        return (
            "WASH Crew permissions have not been configured. "
            "Set WASH_CREW_ROLE_ID before using this command.",
            True,
        )
    if not is_wash_crew_member(user, wash_crew_role_id):
        return "You need the WASH Crew role to restore a backup.", True

    archive_path = find_backup_by_filename(backup_service, backup_filename)
    if archive_path is None:
        return build_backup_not_found_message(backup_service, backup_filename), True

    try:
        result = backup_service.restore_backup(archive_path)
    except BackupError as exc:
        return f"Restore failed: {exc}", True

    return f"Restored {len(result.restored_files)} file(s) from `{archive_path.name}`.", True


def build_suggestion_confirmation_embed(
    watch_item: WatchItem,
    *,
    database_name: str,
    suggested_by: str,
):
    """Build the public /add confirmation as a compact record-style embed."""
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
    embed.add_field(name="Database", value=database_name, inline=True)
    embed.add_field(name="Reference", value=watch_item.reference, inline=True)
    if watch_item.poster_url:
        embed.set_thumbnail(url=watch_item.poster_url)
    embed.set_footer(text="Watch Party Manager • TehKarmah")
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
        return resolution.error_message, True, None

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
    )
    if not result.success:
        return result.message, False, None

    return result.message, False, result.watch_item


async def perform_repair_suggestions(
    repair_service: SuggestionRepairService,
    user: object,
    wash_crew_role_id: Optional[int],
) -> tuple[str, bool]:
    """Run the WASH Crew-only suggestion repair workflow."""
    if wash_crew_role_id is None:
        return (
            "Set WASH_CREW_ROLE_ID before using this command.",
            True,
        )
    if not is_wash_crew_member(user, wash_crew_role_id):
        return "You need the WASH Crew role to repair suggestions.", True
    report = await repair_service.repair_all()
    return report.format_message(), True


def perform_list_suggestions_response(
    suggestion_service: SuggestionService,
    guild_id: Optional[int],
    channel_id: Optional[int],
    user: object | None = None,
    wash_crew_role_id: Optional[int] = None,
    view: str | None = None,
    public: bool = False,
) -> tuple[str, bool]:
    """Build the role-aware ``/list`` response.

    The standard view is available to everyone and is ephemeral by default.
    The expanded Crew view and public posting are restricted to WASH Crew.
    """
    try:
        parsed_view = SuggestionListView.parse(view)
    except ValueError as exc:
        return str(exc), True

    is_crew = is_wash_crew_member(user, wash_crew_role_id) if user is not None else False
    if parsed_view is SuggestionListView.CREW and not is_crew:
        return "You need the WASH Crew role to use the Crew list view.", True
    if public and not is_crew:
        return "You need the WASH Crew role to post the suggestion list publicly.", True

    resolution = suggestion_service.resolve_database_for_channel(guild_id, channel_id)
    if resolution.database is None:
        return resolution.error_message or "No suggestion database is available here.", True

    # The Crew view is WASH's administrative view (already gated above),
    # so it continues to show archived suggestions -- e.g. those rejected
    # via /reject -- while the standard view excludes them.
    items = suggestion_service.get_suggestions_for_database(
        resolution.database.database_id, include_archived=parsed_view is SuggestionListView.CREW
    )
    message = SuggestionListFormatter().format(items, resolution.database, parsed_view)
    return message, not public


def perform_list_suggestions(
    suggestion_service: SuggestionService,
    guild_id: Optional[int],
    channel_id: Optional[int],
) -> str:
    """Backward-compatible standard list formatter used by existing callers."""
    message, _ = perform_list_suggestions_response(
        suggestion_service=suggestion_service,
        guild_id=guild_id,
        channel_id=channel_id,
    )
    return message


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


def perform_reject_suggestion(
    suggestion_service: SuggestionService,
    suggestion_database_configuration_repository: Optional[SuggestionDatabaseConfigurationRepository],
    permission_service: PermissionService,
    user: object,
    guild_id: Optional[int],
    suggestion_id: int,
) -> tuple[str, bool]:
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
        A (message, ephemeral) tuple. Always ephemeral -- a member's own
        rejection is for their eyes only.
    """
    permission = permission_service.require_watch_party_member(user)
    if not permission.allowed:
        return permission.message, True

    watch_item = suggestion_service.get_suggestion(suggestion_id)
    if watch_item is None:
        return "That suggestion doesn't exist.", True

    threshold = resolve_rejection_threshold(
        suggestion_database_configuration_repository, guild_id, watch_item.database_id
    )
    result = suggestion_service.reject_suggestion(
        suggestion_id, user.id, rejection_threshold=threshold
    )
    return result.message, True


def perform_remove_rejection(
    suggestion_service: SuggestionService,
    permission_service: PermissionService,
    user: object,
    suggestion_id: int,
) -> tuple[str, bool]:
    """Core logic for /unreject, kept free of Discord objects except `user`.

    Args:
        suggestion_service: The suggestion service to remove the rejection through.
        permission_service: Used to require Watch Party member permission.
        user: The member invoking the command.
        suggestion_id: The suggestion to remove the member's rejection from.

    Returns:
        A (message, ephemeral) tuple. Always ephemeral, matching /reject.
    """
    permission = permission_service.require_watch_party_member(user)
    if not permission.allowed:
        return permission.message, True

    result = suggestion_service.remove_rejection(suggestion_id, user.id)
    return result.message, True


def perform_toggle_suggestion_rejection(
    suggestion_service: SuggestionService,
    suggestion_database_configuration_repository: Optional[SuggestionDatabaseConfigurationRepository],
    permission_service: PermissionService,
    user: object,
    guild_id: Optional[int],
    suggestion_id: int,
) -> tuple[str, bool, Optional[WatchItem]]:
    """Core logic for the suggestion message's "I WILL NOT WATCH" button.

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
) -> None:
    """Handle a click on a suggestion's "I WILL NOT WATCH" button.

    Reuses perform_toggle_suggestion_rejection() for all rejection logic,
    then refreshes the original suggestion message's button so its
    displayed count/threshold and archived state stay accurate --
    mirroring handle_nominee_vote's "respond ephemerally, then refresh
    the original post" pattern. Never posts an additional public message.

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
    """
    if permission_service is None:
        await interaction.response.send_message(
            "Watch Party member permissions have not been configured.", ephemeral=True
        )
        return

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

    view = build_suggestion_view(
        suggestion_service,
        suggestion_database_configuration_repository,
        watch_item,
        interaction.guild_id,
        permission_service=permission_service,
    )
    await interaction.message.edit(view=view)


def build_database_add_confirmation(database: SuggestionDatabase) -> str:
    """Build the /database_add confirmation message.

    Args:
        database: The newly created suggestion database.

    Returns:
        A confirmation naming the database, its ID, and its channel.
    """
    return (
        f'Suggestion database "{database.name}" created.\n'
        f"Database ID: {database.database_id}\n"
        f"Channel: <#{database.channel_id}>"
    )


def build_database_list_text(
    suggestion_service: SuggestionService, databases: List[SuggestionDatabase]
) -> str:
    """Build the /database_list message for a set of databases.

    Args:
        suggestion_service: Used to look up each database's watch-item count.
        databases: The databases to display, in the order given.

    Returns:
        A readable multi-line block per database with its ID, name, status,
        Discord channel mention, and current watch-item count.
    """
    sections = ["Suggestion Databases"]
    ordered_databases = sorted(
        databases,
        key=lambda database: (not database.active, database.name.casefold(), database.database_id),
    )
    for database in ordered_databases:
        status = "Active" if database.active else "Inactive"
        suggestion_count = suggestion_service.suggestion_count_for_database(database.database_id)
        item_word = "watch item" if suggestion_count == 1 else "watch items"
        sections.append(
            f"[{database.database_id}] {database.name}\n"
            f"Status: {status}\n"
            f"Channel: <#{database.channel_id}>\n"
            f"Watch items: {suggestion_count} {item_word}"
        )
    return "\n\n".join(sections)


def perform_remove_suggestion(
    suggestion_service: SuggestionService,
    user: object,
    wash_crew_role_id: Optional[int],
    title: str,
) -> tuple[str, bool, bool]:
    """Core logic for /remove, kept free of Discord objects except `user`.

    FR-029's approved permission model restricts /remove to WASH Crew
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
        success or failure, matching /remove's existing confirmation style.
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


def perform_database_add(
    suggestion_service: SuggestionService,
    user: object,
    wash_crew_role_id: Optional[int],
    guild_id: Optional[int],
    channel_id: Optional[int],
    name: str,
) -> tuple[str, bool]:
    """Core logic for /database_add, kept free of Discord objects except `user`.

    All the actual creation rules (duplicate name, duplicate channel) are
    enforced by SuggestionService.create_database(); this function only
    handles the WASH Crew permission check and presentation.

    Args:
        suggestion_service: The suggestion service to create the database in.
        user: The member invoking the command.
        wash_crew_role_id: The configured WASH Crew role ID, or None if
            unconfigured.
        guild_id: The Discord guild the command was run in.
        channel_id: The Discord channel or thread the command was run in.
        name: The desired database name.

    Returns:
        A (message, ephemeral) tuple. Every /database_add response is
        ephemeral -- this is an admin configuration command.
    """
    if wash_crew_role_id is None:
        return (
            "WASH Crew permissions have not been configured. "
            "Set WASH_CREW_ROLE_ID before using this command.",
            True,
        )

    if not is_wash_crew_member(user, wash_crew_role_id):
        return "You need the WASH Crew role to configure a suggestion database.", True

    if guild_id is None:
        return "This command can only be used in a Discord server.", True

    if channel_id is None:
        return "This command must be used in a server channel or thread.", True

    result = suggestion_service.create_database(name, guild_id=guild_id, channel_id=channel_id)
    if not result.success:
        return result.message, True

    return build_database_add_confirmation(result.database), True


def perform_database_list(
    suggestion_service: SuggestionService,
    user: object,
    wash_crew_role_id: Optional[int],
    guild_id: Optional[int],
) -> tuple[str, bool]:
    """Core logic for /database_list, kept free of Discord objects except `user`.

    Args:
        suggestion_service: The suggestion service to read databases from.
        user: The member invoking the command.
        wash_crew_role_id: The configured WASH Crew role ID, or None if
            unconfigured.
        guild_id: The Discord guild the command was run in.

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
        return "You need the WASH Crew role to view suggestion databases.", True

    if guild_id is None:
        return "This command can only be used in a Discord server.", True

    databases = suggestion_service.list_databases(guild_id)
    if not databases:
        return "No suggestion databases are configured yet.", True

    return build_database_list_text(suggestion_service, databases), True


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
        return "You need the WASH Crew role to remove a suggestion database.", True

    if guild_id is None:
        return "This command can only be used in a Discord server.", True

    result = suggestion_service.deactivate_database(database_id, guild_id)
    return result.message, True


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


def parse_vote_end_time(value: str, *, now: Optional[datetime] = None) -> datetime:
    """Parse and validate a new closing date/time for /edit_vote.

    Reuses parse_watch_party_schedule_time's exact ISO 8601 parsing and
    UTC-assumption convention rather than duplicating it, then layers on
    the one extra constraint specific to editing an *active* vote's
    deadline: it must not already be in the past.

    Args:
        value: The raw end-time text from the modal.
        now: The current time to validate against. Defaults to the real
            current UTC time; tests supply a fixed value for determinism.

    Returns:
        A timezone-aware datetime in UTC, strictly after `now`.

    Raises:
        ValueError: If value is blank, not a parseable date/time, or not
            in the future.
    """
    parsed = parse_watch_party_schedule_time(value)
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
        Movie title, current status, Discord-formatted scheduled time,
        and an IMDb link when one is on file.
    """
    title = watch_item.title if watch_item is not None else f"Watch item #{watch_party.watch_item_id}"
    lines = [
        f"Watch Party #{watch_party.id}",
        f"Movie: {title}",
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
        return "This command can only be used in a Discord server.", True, None

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
    watch_party_id: int,
    when: str,
    scheduler_service: Optional[SchedulerService] = None,
    guild_configuration_repository: Optional[GuildConfigurationRepository] = None,
) -> None:
    """Reschedule a watch party and replace its reminder job.

    scheduler_service/guild_configuration_repository default to None so
    callers/tests that don't pass them keep working unchanged.
    """
    message, ephemeral, watch_party = perform_reschedule_watch_party(
        watch_party_service=watch_party_service,
        user=interaction.user,
        wash_crew_role_id=wash_crew_role_id,
        watch_party_id=watch_party_id,
        when=when,
    )
    await interaction.response.send_message(message, ephemeral=ephemeral)
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
    watch_party_id: int,
    scheduler_service: Optional[SchedulerService] = None,
) -> None:
    """Cancel a watch party and remove its pending reminder job.

    scheduler_service defaults to None so callers/tests that don't pass
    one keep working unchanged; passing None simply skips cancellation
    (see cancel_watch_party_reminder).
    """
    message, ephemeral = perform_cancel_watch_party(
        watch_party_service=watch_party_service,
        user=interaction.user,
        wash_crew_role_id=wash_crew_role_id,
        watch_party_id=watch_party_id,
    )
    await interaction.response.send_message(message, ephemeral=ephemeral)
    if ephemeral:
        return

    # FR-021: remove any pending reminder job now that the watch party is
    # cancelled -- a no-op if none is active (e.g. reminders were
    # disabled, or it already fired).
    await cancel_watch_party_reminder(scheduler_service, watch_party_id)


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
            "**Suggestion Databases**",
            f"Total: {format_count(snapshot.total_databases, 'database')}",
            f"Active: {format_count(snapshot.active_databases, 'database')}",
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
        return "This command can only be used in a Discord server."
    return build_statistics_text(statistics_service.snapshot(guild_id))


def build_diagnostics_text(
    *,
    version: str,
    python_version: str,
    discord_version: str,
    latency_ms: float,
    started_at: datetime,
    now: datetime,
    snapshot: StatisticsSnapshot,
    interactive_voting_restored: bool,
) -> str:
    """Format WASH Crew runtime diagnostics."""
    ping_lines = build_ping_text(latency_ms, started_at, now).splitlines()
    latency_line = ping_lines[1].removeprefix("Gateway latency: ")
    uptime_line = ping_lines[2].removeprefix("Uptime: ")
    return "\n".join(
        [
            "**WASH Diagnostics**",
            "",
            "**Runtime**",
            f"WASH version: {version}",
            f"Python: {python_version}",
            f"discord.py: {discord_version}",
            f"Gateway latency: {latency_line}",
            f"Uptime: {uptime_line}",
            "",
            "**Loaded Data**",
            f"Suggestion databases: {format_count(snapshot.total_databases, 'database')}",
            f"Watch items: {format_count(snapshot.total_watch_items, 'watch item')}",
            f"Active suggestions: {format_count(snapshot.active_suggestions, 'suggestion')}",
            "",
            "**Voting**",
            f"Open voting round: {'Yes' if snapshot.open_vote_rounds else 'No'}",
            f"Interactive controls restored: {'Yes' if interactive_voting_restored else 'No'}",
        ]
    )


def perform_diagnostics(
    *,
    statistics_service: StatisticsService,
    user: object,
    wash_crew_role_id: Optional[int],
    guild_id: Optional[int],
    version: str,
    python_version: str,
    discord_version: str,
    latency_ms: float,
    started_at: datetime,
    now: datetime,
    interactive_voting_restored: bool,
) -> tuple[str, bool]:
    """Return the WASH Crew-only /diagnostics response."""
    if wash_crew_role_id is None:
        return (
            "WASH Crew permissions have not been configured. "
            "Set WASH_CREW_ROLE_ID before using this command.",
            True,
        )
    if not is_wash_crew_member(user, wash_crew_role_id):
        return "You need the WASH Crew role to view diagnostics.", True
    if guild_id is None:
        return "This command can only be used in a Discord server.", True

    return (
        build_diagnostics_text(
            version=version,
            python_version=python_version,
            discord_version=discord_version,
            latency_ms=latency_ms,
            started_at=started_at,
            now=now,
            snapshot=statistics_service.snapshot(guild_id),
            interactive_voting_restored=interactive_voting_restored,
        ),
        True,
    )


def build_help_text(show_admin: bool = True, show_member: bool = False) -> str:
    """Build the complete role-aware help response as a single string.

    This compatibility helper delegates to :mod:`help_service`. WASH Crew
    help is sent as two Discord messages, but this helper preserves its
    original single-string contract for existing callers. show_member
    reflects FR-029's three-tier permission model (everyone / Watch Party
    member / WASH Crew); show_admin implies show_member, matching
    build_help_response's own inheritance.
    """
    response = build_help_response(show_wash_crew=show_admin, show_watch_party_member=show_member)
    return "\n\n".join(response.messages)


async def send_help_response(interaction: discord.Interaction, response: HelpResponse) -> None:
    """Send the initial help message followed by any additional messages."""
    await interaction.response.send_message(
        response.messages[0], ephemeral=response.ephemeral
    )
    for message in response.messages[1:]:
        await interaction.followup.send(message, ephemeral=response.ephemeral)


def build_ping_text(latency_ms: float, started_at: datetime, now: datetime) -> str:
    """Build a compact /ping response with gateway latency and uptime."""
    if started_at.tzinfo is None or started_at.utcoffset() is None:
        raise ValueError("started_at must be timezone-aware")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    uptime_seconds = max(0, int((now - started_at).total_seconds()))
    days, remainder = divmod(uptime_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    uptime_parts = []
    if days:
        uptime_parts.append(f"{days}d")
    if hours or days:
        uptime_parts.append(f"{hours}h")
    if minutes or hours or days:
        uptime_parts.append(f"{minutes}m")
    uptime_parts.append(f"{seconds}s")

    return f"Pong.\nGateway latency: {round(latency_ms)} ms\nUptime: {' '.join(uptime_parts)}"



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
