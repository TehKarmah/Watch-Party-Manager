from __future__ import annotations

import asyncio
import logging
import os
import platform
from datetime import datetime, timedelta, timezone
from typing import List, Optional

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
from watch_party_manager.domain.suggestion_database import SuggestionDatabase
from watch_party_manager.domain.watch_item import WatchItem
from watch_party_manager.logger_config import configure_logging
from watch_party_manager.services.about_service import build_about_content
from watch_party_manager.services.nominee_selection_service import NomineeSelectionService
from watch_party_manager.services.suggestion_input_service import SuggestionInputService
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.statistics_service import StatisticsService, StatisticsSnapshot
from watch_party_manager.services.vote_service import StandingsEntry, VoteService
from watch_party_manager.start_vote_view import (
    CustomizeVoteModal,
    StartVoteChoiceView,
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
        default_nominee_count: int = DEFAULT_VOTE_CANDIDATE_COUNT,
    ) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.token = token
        self.guild_id = guild_id
        self.wash_crew_role_id = wash_crew_role_id
        self.default_nominee_count = default_nominee_count
        self.started_at = datetime.now(timezone.utc)
        self.suggestion_service = SuggestionService()
        self.suggestion_input_service = SuggestionInputService()
        self.vote_service = VoteService(self.suggestion_service)
        self.nominee_selection_service = NomineeSelectionService(self.suggestion_service, self.vote_service)
        self.statistics_service = StatisticsService(self.suggestion_service)
        self.interactive_voting_restored = False

    async def setup_hook(self) -> None:
        @self.tree.command(name="ping")
        async def ping(interaction: discord.Interaction) -> None:
            await interaction.response.send_message(
                build_ping_text(
                    latency_ms=self.latency * 1000,
                    started_at=self.started_at,
                    now=datetime.now(timezone.utc),
                )
            )

        @self.tree.command(name="about")
        async def about(interaction: discord.Interaction) -> None:
            content = build_about_content(__version__, __build__)
            embed = discord.Embed(
                title=content.title,
                description=content.description,
            )
            for field in content.fields:
                embed.add_field(
                    name=field.name,
                    value=field.value,
                    inline=field.inline,
                )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        @self.tree.command(name="help")
        async def help_command(interaction: discord.Interaction) -> None:
            show_admin = is_wash_crew_member(interaction.user, self.wash_crew_role_id)
            await interaction.response.send_message(build_help_text(show_admin=show_admin))

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
            message, ephemeral, watch_item = await perform_add_suggestion_from_input(
                suggestion_input_service=self.suggestion_input_service,
                suggestion_service=self.suggestion_service,
                guild_id=interaction.guild_id,
                channel_id=interaction.channel_id,
                title=title,
                imdb_url=imdb_url,
            )
            await interaction.response.send_message(message, ephemeral=ephemeral)
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
        async def suggestions(interaction: discord.Interaction) -> None:
            message = perform_list_suggestions(
                suggestion_service=self.suggestion_service,
                guild_id=interaction.guild_id,
                channel_id=interaction.channel_id,
            )
            await interaction.response.send_message(message)

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
            message = perform_vote_status(
                vote_service=self.vote_service,
                suggestion_service=self.suggestion_service,
            )
            await interaction.response.send_message(message)

        @self.tree.command(name="vote")
        async def vote(interaction: discord.Interaction, suggestion_id: int) -> None:
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

        self.interactive_voting_restored = restore_persistent_voting_view(
            bot=self,
            vote_service=self.vote_service,
            suggestion_service=self.suggestion_service,
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
) -> VotingView:
    """Build a voting view whose buttons use the shared vote handler."""

    async def on_vote_click(
        interaction: discord.Interaction, suggestion_id: int
    ) -> None:
        await handle_nominee_vote(
            interaction, vote_service, suggestion_service, suggestion_id
        )

    return VotingView(candidates, on_vote=on_vote_click)


def restore_persistent_voting_view(
    bot: object,
    vote_service: VoteService,
    suggestion_service: SuggestionService,
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

    view = build_voting_view(vote_service, suggestion_service, candidates)
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
    )


def perform_add_suggestion(
    suggestion_service: SuggestionService,
    guild_id: Optional[int],
    channel_id: Optional[int],
    title: str,
    imdb_url: Optional[str],
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
    )
    if not result.success:
        return result.message, False, None

    return result.message, False, result.watch_item


def perform_list_suggestions(
    suggestion_service: SuggestionService,
    guild_id: Optional[int],
    channel_id: Optional[int],
) -> str:
    """Core logic for /list, kept free of Discord objects except raw IDs.

    Resolves which suggestion database this channel maps to (the same way
    perform_add_suggestion does) and shows only that database's
    suggestions, rather than duplicating the resolution rules here.

    Args:
        suggestion_service: The suggestion service to resolve a database
            through and read suggestions from.
        guild_id: The Discord guild the command was run in.
        channel_id: The Discord channel or thread the command was run in.

    Returns:
        The formatted suggestion list, or a clear explanatory message if
        no single database could be resolved for this channel.
    """
    resolution = suggestion_service.resolve_database_for_channel(guild_id, channel_id)
    if resolution.database is None:
        return resolution.error_message

    return suggestion_service.format_suggestion_list(resolution.database.database_id)


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
    """Build the command guide, optionally including WASH Crew commands."""
    sections = [
        "**WASH Commands**",
        "**General**\n"
        "`/help` - Show this command guide.\n"
        "`/ping` - Check WASH latency and uptime.\n"
        "`/about` - Learn about WASH, its features, roles, version, and project.\n"
        "`/stats` - Show watch-party activity statistics.",
        "**Watch Items**\n"
        "`/add` - Add a watch item by title or IMDb link.\n"
        "`/list` - List watch items in the relevant suggestion database.\n"
        "`/remove` - Remove a watch item.",
        "**Voting**\n"
        "`/start_vote` - Start a new voting round.\n"
        "`/vote_status` - View the current voting round.\n"
        "`/vote` - Cast or update your vote.",
    ]

    if show_admin:
        sections.append(
            "**WASH Crew: Suggestion Databases**\n"
            "`/database_add` - Create a database for the current channel or thread.\n"
            "`/database_list` - List databases configured for this server.\n"
            "`/database_remove` - Deactivate a suggestion database.\n"
            "`/diagnostics` - Show WASH runtime diagnostics."
        )

    return "\n\n".join(sections)


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
