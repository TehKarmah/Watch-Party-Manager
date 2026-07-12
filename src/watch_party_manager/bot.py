from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import List, Literal, Optional

import discord
from discord.ext import commands
from dotenv import load_dotenv

from watch_party_manager.domain.vote import (
    DEFAULT_VOTE_DURATION_DAYS,
    MAX_VOTE_CHANGES,
    MAX_VOTE_DURATION_DAYS,
    MIN_VOTE_DURATION_DAYS,
    VoteRound,
    VoteRoundStatus,
    VoteVisibility,
)
from watch_party_manager.logger_config import configure_logging
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.vote_service import StandingsEntry, VoteService
from watch_party_manager.version import __version__

logger = logging.getLogger(__name__)


class WatchPartyBot(commands.Bot):
    """A minimal Discord bot for the initial vertical slice."""

    def __init__(
        self,
        *,
        token: Optional[str] = None,
        guild_id: Optional[int] = None,
        wash_crew_role_id: Optional[int] = None,
    ) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.token = token
        self.guild_id = guild_id
        self.wash_crew_role_id = wash_crew_role_id
        self.suggestion_service = SuggestionService()
        self.vote_service = VoteService(self.suggestion_service)

    async def setup_hook(self) -> None:
        @self.tree.command(name="ping")
        async def ping(interaction: discord.Interaction) -> None:
            await interaction.response.send_message("Pong.")

        @self.tree.command(name="version")
        async def version(interaction: discord.Interaction) -> None:
            await interaction.response.send_message(build_version_text(__version__))

        @self.tree.command(name="help")
        async def help_command(interaction: discord.Interaction) -> None:
            await interaction.response.send_message(build_help_text())

        @self.tree.command(name="suggest")
        async def suggest(
            interaction: discord.Interaction,
            title: str,
            imdb_url: Optional[str] = None,
        ) -> None:
            result = self.suggestion_service.suggest(title, imdb_url)
            await interaction.response.send_message(result.message)

        @self.tree.command(name="suggestions")
        async def suggestions(interaction: discord.Interaction) -> None:
            await interaction.response.send_message(self.suggestion_service.format_suggestion_list())

        @self.tree.command(name="remove_suggestion")
        async def remove_suggestion(interaction: discord.Interaction, title: str) -> None:
            result = self.suggestion_service.remove_suggestion(title)
            await interaction.response.send_message(result.message)

        @self.tree.command(name="start_vote")
        async def start_vote(
            interaction: discord.Interaction,
            visibility: Literal["blind", "visible"],
            duration_days: Optional[int] = None,
        ) -> None:
            message, ephemeral = perform_start_vote(
                vote_service=self.vote_service,
                suggestion_service=self.suggestion_service,
                user=interaction.user,
                wash_crew_role_id=self.wash_crew_role_id,
                visibility_str=visibility,
                duration_days=duration_days,
            )
            await interaction.response.send_message(message, ephemeral=ephemeral)

        @self.tree.command(name="vote_status")
        async def vote_status(interaction: discord.Interaction) -> None:
            message = perform_vote_status(
                vote_service=self.vote_service,
                suggestion_service=self.suggestion_service,
            )
            await interaction.response.send_message(message)

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

    Discord's UI already restricts the option to "blind"/"visible" via
    Literal-based choices (see the /start_vote command), so this mainly
    serves as defensive validation for anything else that calls it.

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


def build_start_vote_confirmation(vote_round: VoteRound, candidate_count: int) -> str:
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

    if standings_error is not None:
        lines.append("")
        lines.append(f"Standings unavailable: {standings_error}")
    elif standings is not None:
        lines.append("")
        if not standings:
            lines.append("Standings: no votes yet.")
        else:
            lines.append("Standings:")
            for position, entry in enumerate(standings, start=1):
                vote_word = "vote" if entry.vote_count == 1 else "votes"
                lines.append(f"{position}. Suggestion #{entry.suggestion_id} — {entry.vote_count} {vote_word}")

    return "\n".join(lines)


def perform_start_vote(
    vote_service: VoteService,
    suggestion_service: SuggestionService,
    user: object,
    wash_crew_role_id: Optional[int],
    visibility_str: str,
    duration_days: Optional[int],
) -> tuple[str, bool]:
    """Core logic for /start_vote, kept free of Discord objects except `user`.

    Args:
        vote_service: The vote service to open a round on.
        suggestion_service: Used to report the current candidate count.
        user: The member invoking the command (checked against the WASH
            Crew role).
        wash_crew_role_id: The configured WASH Crew role ID, or None if
            unconfigured.
        visibility_str: The raw visibility option text ("blind"/"visible").
        duration_days: The raw duration option, or None for the default.

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

    closes_at = datetime.now(timezone.utc) + timedelta(days=days)

    result = vote_service.create_round(visibility=visibility, closes_at=closes_at)
    if not result.success:
        return result.message, True

    candidate_count = suggestion_service.suggestion_count()
    return build_start_vote_confirmation(result.vote_round, candidate_count), False


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

    candidate_count = suggestion_service.suggestion_count()
    return build_vote_status_text(vote_round, candidate_count, standings, standings_error)


def build_help_text() -> str:
    return (
        "Available commands:\n"
        "- /ping\n"
        "- /version\n"
        "- /help\n"
        "- /suggest\n"
        "- /suggestions\n"
        "- /remove_suggestion\n"
        "- /start_vote\n"
        "- /vote_status"
    )


def build_version_text(version: str) -> str:
    return f"Watch Party Manager version {version}"


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

    bot = WatchPartyBot(token=token, guild_id=guild_id, wash_crew_role_id=wash_crew_role_id)

    try:
        asyncio.run(bot.start_bot())
    except RuntimeError as e:
        logger.error(f"Failed to start bot: {e}")
        exit(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    main()
