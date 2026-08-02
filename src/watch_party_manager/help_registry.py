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
    CommandHelp("/help", "Show the WASH command guide.", "General"),
    CommandHelp("/about", "View WASH's status, health, and configuration info.", "General"),
    CommandHelp(
        "/join_watch_party",
        "Join or leave the Watch Party.",
        "General",
    ),
    CommandHelp(
        "/stats",
        "Show server, member, suggestion, or collection statistics.",
        "General",
        HelpAudience.WATCH_PARTY_MEMBER,
    ),
    CommandHelp(
        "/watch_party",
        "Manage Watch Party membership.",
        "WASH Crew: Membership",
        HelpAudience.WASH_CREW,
    ),
    CommandHelp(
        "/setup",
        "Run the guided server setup wizard.",
        "WASH Crew: Configuration",
        HelpAudience.WASH_CREW,
    ),
    CommandHelp(
        "/config",
        "View or change WASH's server configuration.",
        "WASH Crew: Configuration",
        HelpAudience.WASH_CREW,
    ),
    CommandHelp(
        "/add", "Add a watch item by title or IMDb link.", "Watch Items", HelpAudience.WATCH_PARTY_MEMBER
    ),
    CommandHelp(
        "/list",
        "List watch items by status.",
        "Watch Items",
        HelpAudience.WATCH_PARTY_MEMBER,
    ),
    CommandHelp(
        "/random watch",
        "Pick one random eligible watch item from a collection.",
        "Watch Items",
        HelpAudience.WATCH_PARTY_MEMBER,
    ),
    CommandHelp(
        "/browse",
        "Browse a collection with filters, paginated.",
        "Watch Items",
        HelpAudience.WATCH_PARTY_MEMBER,
    ),
    CommandHelp(
        "/remove", "Remove a watch item.", "Watch Items", HelpAudience.WASH_CREW
    ),
    CommandHelp(
        "/edit_suggestion",
        "Edit a suggestion's details or collection.",
        "Watch Items",
        HelpAudience.WASH_CREW,
    ),
    CommandHelp(
        "/vote start", "Start a new voting round.", "WASH Crew: Voting", HelpAudience.WASH_CREW
    ),
    CommandHelp(
        "/vote status",
        "View the current voting round.",
        "WASH Crew: Voting",
        HelpAudience.WASH_CREW,
    ),
    CommandHelp(
        "/vote edit",
        "Change, end, or cancel the active vote.",
        "WASH Crew: Voting",
        HelpAudience.WASH_CREW,
    ),
    CommandHelp(
        "/database add",
        "Create a collection.",
        "WASH Crew: Collections",
        HelpAudience.WASH_CREW,
    ),
    CommandHelp(
        "/database list",
        "View collections.",
        "WASH Crew: Collections",
        HelpAudience.WASH_CREW,
    ),
    CommandHelp(
        "/database health",
        "Show a collection's eligibility and pool health.",
        "WASH Crew: Collections",
        HelpAudience.WASH_CREW,
    ),
    CommandHelp(
        "/database manage",
        "Move, edit, back up, restore, refresh IMDb metadata, recover missing IMDb links, reset, or remove collections.",
        "WASH Crew: Collections",
        HelpAudience.WASH_CREW,
    ),
    CommandHelp(
        "/repair_suggestions",
        "Repair bad suggestion data.",
        "WASH Crew: Maintenance",
        HelpAudience.WASH_CREW,
    ),
    CommandHelp(
        "/backup",
        "Create and download a WASH backup.",
        "WASH Crew: Maintenance",
        HelpAudience.WASH_CREW,
    ),
    CommandHelp(
        "/restore",
        "Restore WASH's data from a backup.",
        "WASH Crew: Maintenance",
        HelpAudience.WASH_CREW,
    ),
    CommandHelp(
        "/factory_reset",
        "Erase all WASH data for this server.",
        "WASH Crew: Maintenance",
        HelpAudience.WASH_CREW,
    ),
    CommandHelp(
        "/import",
        "Import a backup from another WASH instance.",
        "WASH Crew: Maintenance",
        HelpAudience.WASH_CREW,
    ),
    # v1 Final Polish: /watch-party schedule/reschedule/cancel/status are
    # intentionally not listed here for v1 -- the command group itself is
    # unregistered (see WatchPartyBot's command-tree setup in bot.py),
    # so there is nothing here to document yet. The scheduled watch
    # party workflow may return in a later release; do not re-add these
    # entries without also re-registering the command group.
)


# Database Manage Cleanup: a plain-text note appended after a section's
# command lines in build_command_help_text -- for pointing users at
# additional shortcuts without inventing a fake "/command" entry for
# what isn't actually a command. Keyed by the exact section name used in
# COMMAND_HELP.
SECTION_NOTES: dict[str, str] = {
    "WASH Crew: Collections": (
        "Additional collection shortcuts are available under `/database`. "
        "The full command list remains documented in the Command Reference."
    ),
}


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
        note = SECTION_NOTES.get(section)
        if note:
            lines.append(note)
        sections.append("\n".join(lines))
    return "\n\n".join(sections)
