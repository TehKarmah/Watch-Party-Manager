"""Structured command and terminology help for WASH.

The registry is intentionally Discord-agnostic so it can support slash-command
responses, documentation generation, and future paginated help views without
coupling help content to the bot startup module.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class HelpAudience(str, Enum):
    """Audience allowed to see a help entry.

    FR-029's approved permission model has three tiers: everyone, the
    configured Watch Party role, and WASH Crew (which inherits every
    Watch Party member capability -- see PermissionService.is_wash_crew).
    """

    EVERYONE = "everyone"
    WATCH_PARTY_MEMBER = "watch_party_member"
    WASH_CREW = "wash_crew"


@dataclass(frozen=True, slots=True)
class CommandHelp:
    """Metadata describing one WASH slash command."""

    name: str
    summary: str
    section: str
    audience: HelpAudience = HelpAudience.EVERYONE

    def __post_init__(self) -> None:
        if not self.name.startswith("/"):
            raise ValueError("command name must begin with '/'")
        if not self.summary.strip():
            raise ValueError("command summary is required")
        if not self.section.strip():
            raise ValueError("command section is required")


COMMAND_HELP: tuple[CommandHelp, ...] = (
    CommandHelp("/help", "Show the WASH command guide and documentation links.", "General"),
    CommandHelp(
        "/about", "View WASH information, version, latency, uptime, features, and project.", "General"
    ),
    CommandHelp(
        "/stats", "Show watch-party activity statistics.", "General", HelpAudience.WASH_CREW
    ),
    CommandHelp(
        "/setup",
        "Run the guided first-time server configuration wizard.",
        "WASH Crew: Configuration",
        HelpAudience.WASH_CREW,
    ),
    CommandHelp(
        "/config",
        "View and change WASH's server configuration, one section at a time.",
        "WASH Crew: Configuration",
        HelpAudience.WASH_CREW,
    ),
    CommandHelp(
        "/add", "Add a watch item by title or IMDb link.", "Watch Items", HelpAudience.WATCH_PARTY_MEMBER
    ),
    CommandHelp(
        "/list",
        "List watch items in the relevant suggestion database.",
        "Watch Items",
        HelpAudience.WASH_CREW,
    ),
    CommandHelp(
        "/remove", "Remove a watch item.", "Watch Items", HelpAudience.WASH_CREW
    ),
    CommandHelp(
        "/start_vote", "Start a new voting round.", "WASH Crew: Voting", HelpAudience.WASH_CREW
    ),
    CommandHelp(
        "/vote_status",
        "View the current voting round.",
        "Voting",
        HelpAudience.WASH_CREW,
    ),
    CommandHelp(
        "/edit_vote",
        "Change the active vote's end time, end it now, or cancel it.",
        "WASH Crew: Voting",
        HelpAudience.WASH_CREW,
    ),
    CommandHelp(
        "/database_add",
        "Create a suggestion database for the current channel or thread.",
        "WASH Crew: Suggestion Databases",
        HelpAudience.WASH_CREW,
    ),
    CommandHelp(
        "/database_list",
        "List suggestion databases configured for this server.",
        "WASH Crew: Suggestion Databases",
        HelpAudience.WASH_CREW,
    ),
    CommandHelp(
        "/database_remove",
        "Deactivate a suggestion database.",
        "WASH Crew: Suggestion Databases",
        HelpAudience.WASH_CREW,
    ),
    CommandHelp(
        "/repair_suggestions",
        "Repair legacy IMDb titles and remove malformed suggestions.",
        "WASH Crew: Maintenance",
        HelpAudience.WASH_CREW,
    ),
    CommandHelp(
        "/backup",
        "Create an immediate backup of WASH's data.",
        "WASH Crew: Maintenance",
        HelpAudience.WASH_CREW,
    ),
    CommandHelp(
        "/restore",
        "Restore WASH's data from a selected backup.",
        "WASH Crew: Maintenance",
        HelpAudience.WASH_CREW,
    ),
    CommandHelp(
        "/diagnostics",
        "Show WASH runtime diagnostics.",
        "WASH Crew: Diagnostics",
        HelpAudience.WASH_CREW,
    ),
    CommandHelp(
        "/watch_party_status",
        "View the currently scheduled watch party.",
        "Watch Parties",
        HelpAudience.WASH_CREW,
    ),
    CommandHelp(
        "/schedule_watch_party",
        "Schedule a watch party for a watch item.",
        "WASH Crew: Watch Parties",
        HelpAudience.WASH_CREW,
    ),
    CommandHelp(
        "/reschedule_watch_party",
        "Change when a scheduled watch party starts.",
        "WASH Crew: Watch Parties",
        HelpAudience.WASH_CREW,
    ),
    CommandHelp(
        "/cancel_watch_party",
        "Cancel a scheduled watch party.",
        "WASH Crew: Watch Parties",
        HelpAudience.WASH_CREW,
    ),
)


def command_sections(
    *, show_wash_crew: bool, show_watch_party_member: bool = False
) -> tuple[tuple[str, tuple[CommandHelp, ...]], ...]:
    """Return visible command entries grouped in their declared order.

    show_wash_crew implies show_watch_party_member (WASH Crew inherits
    every Watch Party member capability, matching
    PermissionService.is_wash_crew's own inheritance).
    """
    show_watch_party_member = show_watch_party_member or show_wash_crew
    grouped: dict[str, list[CommandHelp]] = {}
    for entry in COMMAND_HELP:
        if entry.audience is HelpAudience.WASH_CREW and not show_wash_crew:
            continue
        if entry.audience is HelpAudience.WATCH_PARTY_MEMBER and not show_watch_party_member:
            continue
        grouped.setdefault(entry.section, []).append(entry)
    return tuple((section, tuple(entries)) for section, entries in grouped.items())


def build_command_help_text(*, show_wash_crew: bool = True, show_watch_party_member: bool = False) -> str:
    """Render the command registry as Discord-friendly text."""
    sections = ["**WASH Commands**"]
    for section, entries in command_sections(
        show_wash_crew=show_wash_crew, show_watch_party_member=show_watch_party_member
    ):
        lines = [f"**{section}**"]
        lines.extend(f"`{entry.name}` - {entry.summary}" for entry in entries)
        sections.append("\n".join(lines))
    return "\n\n".join(sections)
