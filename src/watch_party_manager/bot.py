from __future__ import annotations

import asyncio
import logging
import os
import platform
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

import discord
from discord.ext import commands, tasks
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
from watch_party_manager.domain.suggestion_database import SuggestionDatabase
from watch_party_manager.domain.watch_item import MetadataProvider, WatchItem
from watch_party_manager.logger_config import configure_logging
from watch_party_manager.services.about_service import build_about_content
from watch_party_manager.services.backup_service import (
    BackupError,
    BackupKind,
    BackupService,
)
from watch_party_manager.services.help_service import HelpResponse, build_help_response
from watch_party_manager.services.nominee_selection_service import NomineeSelectionService
from watch_party_manager.services.permission_service import PermissionService
from watch_party_manager.services.suggestion_input_service import SuggestionInputService
from watch_party_manager.services.suggestion_list_formatter import (
    SuggestionListFormatter,
    SuggestionListView,
)
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.suggestion_repair_service import SuggestionRepairService
from watch_party_manager.services.statistics_service import StatisticsService, StatisticsSnapshot
from watch_party_manager.services.vote_completion_service import VoteCompletionService
from watch_party_manager.services.vote_service import StandingsEntry, VoteService
from watch_party_manager.restore_confirmation_view import RestoreConfirmationView
from watch_party_manager.start_vote_view import (
    CustomizeVoteModal,
    StartVoteChoiceView,
)
from watch_party_manager.version import __build__, __version__
from watch_party_manager.voting_view import VotingView

logger = logging.getLogger(__name__)

VOTE_EXPIRATION_CHECK_INTERVAL_SECONDS = 60


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
        self.backup_service = BackupService()
        self.interactive_voting_restored = False

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

        @self.tree.command(name="help")
        async def help_command(interaction: discord.Interaction) -> None:
            show_wash_crew = is_wash_crew_member(
                interaction.user, self.wash_crew_role_id
            )
            response = build_help_response(show_wash_crew=show_wash_crew)
            await send_help_response(interaction, response)

        @self.tree.command(name="stats")
        async def stats(interaction: discord.Interaction) -> None:
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
            await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
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
            permission = self.permission_service.require_watch_party_member(interaction.user)
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
            result = self.suggestion_service.remove_suggestion(title)
            await interaction.response.send_message(result.message)
            if result.success:
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
                )

            async def on_customize(choice_interaction: discord.Interaction) -> None:
                async def on_modal_submit(
                    modal_interaction: discord.Interaction,
                    nominee_count_text: Optional[str],
                    duration_days_text: Optional[str],
                    visibility_text: Optional[str],
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
                    )

                await choice_interaction.response.send_modal(CustomizeVoteModal(on_modal_submit))

            view = StartVoteChoiceView(on_use_defaults, on_customize)
            await interaction.response.send_message(
                "How would you like to start this voting round?", view=view, ephemeral=True
            )

        @self.tree.command(name="vote_status")
        async def vote_status(interaction: discord.Interaction) -> None:
            permission = self.permission_service.require_watch_party_member(interaction.user)
            if not permission.allowed:
                await interaction.response.send_message(permission.message, ephemeral=True)
                return
            message = perform_vote_status(
                vote_service=self.vote_service,
                suggestion_service=self.suggestion_service,
            )
            await interaction.response.send_message(message)

        @self.tree.command(name="vote")
        async def vote(interaction: discord.Interaction, suggestion_id: int) -> None:
            permission = self.permission_service.require_watch_party_member(interaction.user)
            if not permission.allowed:
                await interaction.response.send_message(permission.message, ephemeral=True)
                return
            message, ephemeral = perform_vote(
                vote_service=self.vote_service,
                user_id=interaction.user.id,
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

        # Complete a round that expired while WASH was offline before
        # attempting to restore its interactive voting controls.
        try:
            await check_and_announce_expired_vote(
                self, self.vote_completion_service, self.suggestion_service
            )
        except Exception:
            logger.exception("Error while checking for an expired voting round during startup")

        self.interactive_voting_restored = restore_persistent_voting_view(
            bot=self,
            vote_service=self.vote_service,
            suggestion_service=self.suggestion_service,
            permission_service=self.permission_service,
        )

        if not self.check_expired_votes_task.is_running():
            self.check_expired_votes_task.start()

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

    async def on_ready(self) -> None:
        logger.info(f"Logged in as {self.user}")
        snapshot = self.statistics_service.snapshot()
        logger.info(
            "Startup summary: %s database(s) (%s active), %s watch item(s), "
            "%s active suggestion(s), open voting round: %s, interactive controls restored: %s",
            snapshot.total_databases,
            snapshot.active_databases,
            snapshot.total_watch_items,
            snapshot.active_suggestions,
            "yes" if snapshot.open_vote_rounds else "no",
            "yes" if self.interactive_voting_restored else "no",
        )
        logger.info("Nominee selector initialized")
        logger.info("Ready")

    @tasks.loop(seconds=VOTE_EXPIRATION_CHECK_INTERVAL_SECONDS)
    async def check_expired_votes_task(self) -> None:
        """Periodically close and announce an expired voting round."""
        try:
            await check_and_announce_expired_vote(
                self, self.vote_completion_service, self.suggestion_service
            )
        except Exception:
            logger.exception("Error while checking for expired voting rounds")

    @check_expired_votes_task.before_loop
    async def before_check_expired_votes_task(self) -> None:
        await self.wait_until_ready()

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


def format_datetime_for_display(value: Optional[datetime]) -> str:
    """Format a datetime using Discord's native timestamp syntax.

    Discord renders native timestamps in each member's local timezone. The
    full timestamp gives the exact date and time, while the relative timestamp
    provides quick context such as "in 7 days."

    Args:
        value: A timezone-aware datetime, or None.

    Returns:
        Discord full and relative timestamp codes, or a fallback message when
        no deadline is set.

    Raises:
        ValueError: If value is a naive datetime.
    """
    if value is None:
        return "No deadline set"
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("value must be timezone-aware")

    unix_timestamp = int(value.timestamp())
    return f"<t:{unix_timestamp}:F> (<t:{unix_timestamp}:R>)"


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


def format_standings_lines(
    standings: Optional[List[StandingsEntry]],
    standings_error: Optional[str],
) -> List[str]:
    """Build the display lines for a round's standings, if any are shown.

    Shared by /vote_status and /vote (for visible rounds) so the standings
    format stays identical and isn't duplicated per call site.

    Args:
        standings: Standings entries to display, or None to show nothing.
        standings_error: A message to show instead of standings if
            calculating them failed, or None.

    Returns:
        Lines to append to a message, starting with a blank separator
        line. Empty if there's nothing to show (both args are None).
    """
    if standings_error is not None:
        return ["", f"Standings unavailable: {standings_error}"]

    if standings is not None:
        if not standings:
            return ["", "Standings: no votes yet."]
        lines = ["", "Standings:"]
        for position, entry in enumerate(standings, start=1):
            vote_word = "vote" if entry.vote_count == 1 else "votes"
            lines.append(f"{position}. Suggestion #{entry.suggestion_id} — {entry.vote_count} {vote_word}")
        return lines

    return []


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
        withheld.
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


def parse_start_vote_overrides(
    nominee_count_text: Optional[str],
    duration_days_text: Optional[str],
    visibility_text: Optional[str],
) -> tuple[Optional[int], Optional[int], str]:
    """Parse raw customization-modal values into start-vote arguments.

    Blank numeric fields remain ``None`` so :func:`perform_start_vote` can
    apply configured defaults. Blank visibility uses the established visible
    default. Range and enum validation remain centralized in
    :func:`perform_start_vote`.
    """
    nominee_count = parse_optional_int_field(nominee_count_text)
    duration_days = parse_optional_int_field(duration_days_text)
    visibility = (visibility_text or "").strip() or "visible"
    return nominee_count, duration_days, visibility


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
) -> None:
    """Create a round and publish its interactive voting post."""
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
    )
    if ephemeral:
        await interaction.response.send_message(message, ephemeral=True)
        return

    vote_round = vote_service.get_open_round()
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
) -> None:
    """Start a round using optional one-time modal overrides."""
    try:
        nominee_count, duration_days, visibility_str = parse_start_vote_overrides(
            nominee_count_text, duration_days_text, visibility_text
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


def build_vote_confirmation(vote_record: VoteRecord, is_first_vote: bool, remaining_changes: int) -> str:
    """Build the /vote confirmation message.

    Args:
        vote_record: The member's own vote record after casting.
        is_first_vote: True if this was the member's first vote this round.
        remaining_changes: How many vote changes the member has left.

    Returns:
        A confirmation message. Never mentions any other member's vote.
    """
    if is_first_vote:
        return f"Your vote for suggestion #{vote_record.suggestion_id} has been recorded."

    lines = [f"Your vote has been updated to suggestion #{vote_record.suggestion_id}."]
    if remaining_changes > 0:
        change_word = "change" if remaining_changes == 1 else "changes"
        lines.append(f"You have {remaining_changes} vote {change_word} remaining.")
    else:
        lines.append("You have no vote changes remaining.")
    return "\n".join(lines)


def perform_vote(vote_service: VoteService, user_id: int, suggestion_id: int) -> tuple[str, bool]:
    """Core logic for /vote, kept entirely free of Discord objects.

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
        A (message, ephemeral) tuple. Every /vote response is ephemeral —
        a member's own vote, and any standings shown alongside it, are for
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

    lines = [build_vote_confirmation(vote_record, is_first_vote, remaining_changes)]

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


def build_voting_post_text(
    vote_round: VoteRound,
    candidates: List[WatchItem],
    standings: Optional[List[StandingsEntry]],
    standings_error: Optional[str],
) -> str:
    """Build the public voting post message for a round.

    Used both for the initial post created by /start_vote and to refresh
    it after each vote in a visible round. Reuses format_datetime_for_display
    and format_standings_lines rather than reformatting either here.

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
        blind round that's the only vote information revealed; standings
        are additionally shown for a visible round.
    """
    lines = [
        f"Voting round {vote_round.id} is open!",
        f"Visibility: {vote_round.visibility.value.capitalize()}",
        f"Voting ends: {format_datetime_for_display(vote_round.closes_at)}",
        "",
        "Nominees:",
    ]
    for candidate in candidates:
        lines.append(f"[{candidate.id}] {candidate.title}")
    lines.append("")
    lines.append(f"Votes cast: {len(vote_round.votes)}")

    if vote_round.visibility == VoteVisibility.VISIBLE:
        lines.extend(format_standings_lines(standings, standings_error))

    return "\n".join(lines)


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
    candidates = get_round_candidates(suggestion_service, vote_round)
    standings_result = vote_service.calculate_standings(vote_round.id)
    standings = standings_result.standings if standings_result.success else None
    standings_error = None if standings_result.success else standings_result.message

    text = build_voting_post_text(vote_round, candidates, standings, standings_error)
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
    confirmation -- exactly the same logic /vote uses -- then refreshes
    the public voting post for a visible round. Never duplicates
    VoteService's own validation.

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


def build_vote_completion_announcement(
    vote_round: VoteRound,
    winning_titles: List[str],
    standings: List[StandingsEntry],
    total_votes_cast: int,
) -> str:
    """Build the public announcement for a just-completed voting round.

    By the time this is called the round is already closed, so standings
    are always safe to reveal here -- including for a round that was
    blind while open. That's the entire mechanism behind "reveal standings
    only after voting has closed" for blind rounds: this function is only
    ever invoked post-closure, so there's no separate blind-vs-visible
    branch needed here the way build_voting_post_text has one for the
    still-open case.

    Args:
        vote_round: The round that just completed.
        winning_titles: The winning suggestion(s)' titles, in the same
            order as vote_round's winner calculation. Empty if nobody
            voted.
        standings: The final vote tally, reused from
            VoteService.calculate_standings() via format_standings_lines
            rather than reformatted here.
        total_votes_cast: How many members voted in this round.

    Returns:
        The announcement text.
    """
    lines = [f"Voting round {vote_round.id} has closed!"]

    if not winning_titles:
        lines.append("No votes were cast, so no winner could be determined.")
    elif len(winning_titles) == 1:
        lines.append(f"Winner: {winning_titles[0]}")
    else:
        lines.append("It's a tie! Winners: " + ", ".join(winning_titles))

    lines.append(f"Total votes cast: {total_votes_cast}")
    lines.extend(format_standings_lines(standings, None))

    return "\n".join(lines)


def perform_vote_completion_check(
    vote_completion_service: VoteCompletionService,
    suggestion_service: SuggestionService,
    now: Optional[datetime] = None,
) -> Optional[tuple[VoteRound, str]]:
    """Check for and complete an expired voting round, building its announcement.

    This is the Discord-free core reused by both the periodic background
    check and the one-time startup check (see check_and_announce_expired_vote),
    so neither duplicates the other's logic.

    Args:
        vote_completion_service: Used to detect and complete an expired round.
        suggestion_service: Used to resolve winning suggestion IDs to titles.
        now: Passed through to VoteCompletionService for deterministic testing.

    Returns:
        (vote_round, announcement_text) if a round was just completed, or
        None if there was nothing to do.
    """
    result = vote_completion_service.check_and_complete_expired_round(now=now)
    if result is None:
        return None

    winning_titles = []
    for suggestion_id in result.winning_suggestion_ids:
        watch_item = suggestion_service.get_suggestion(suggestion_id)
        if watch_item is not None:
            winning_titles.append(watch_item.title)

    announcement = build_vote_completion_announcement(
        result.vote_round, winning_titles, result.standings, result.total_votes_cast
    )
    return result.vote_round, announcement


async def check_and_announce_expired_vote(
    bot: object,
    vote_completion_service: VoteCompletionService,
    suggestion_service: SuggestionService,
    now: Optional[datetime] = None,
) -> bool:
    """Complete an expired round, if any, and post its announcement to Discord.

    Used both by the periodic background task and once during startup
    (restart safety), so the two paths share this single implementation
    rather than duplicating the "fetch the channel and send" step.

    Safe to call when nothing is due: perform_vote_completion_check()
    returns None whenever there's no open round, no deadline, the
    deadline hasn't passed, or the round was already completed -- in all
    of those cases this coroutine does nothing and returns False.

    Args:
        bot: Anything with get_channel(channel_id)/fetch_channel(channel_id)
            coroutine-or-sync methods returning an object with a
            send(content) coroutine -- a real discord.Client/Bot satisfies
            this, and tests can supply a lightweight fake.
        vote_completion_service: Used to detect and complete an expired round.
        suggestion_service: Used to resolve winning suggestion IDs to titles.
        now: Passed through for deterministic testing.

    Returns:
        True if a round was completed and its announcement was sent,
        False if there was nothing to do.
    """
    outcome = perform_vote_completion_check(vote_completion_service, suggestion_service, now=now)
    if outcome is None:
        return False

    vote_round, announcement = outcome
    if vote_round.channel_id is None:
        logger.warning(
            "Voting round %s completed but has no channel reference; announcement not sent",
            vote_round.id,
        )
        return True

    channel = bot.get_channel(vote_round.channel_id)
    if channel is None:
        channel = await bot.fetch_channel(vote_round.channel_id)
    await channel.send(announcement)
    logger.info("Announced completion of voting round %s", vote_round.id)
    return True


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

    items = suggestion_service.get_suggestions_for_database(resolution.database.database_id)
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


def build_help_text(show_admin: bool = True) -> str:
    """Build the complete role-aware help response as a single string.

    This compatibility helper delegates to :mod:`help_service`. WASH Crew
    help is sent as two Discord messages, but this helper preserves its
    original single-string contract for existing callers.
    """
    response = build_help_response(show_wash_crew=show_admin)
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
