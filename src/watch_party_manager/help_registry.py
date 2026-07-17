"""Structured command and terminology help for WASH.

The registry is intentionally Discord-agnostic so it can support slash-command
responses, documentation generation, and future paginated help views without
coupling help content to the bot startup module.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class HelpAudience(str, Enum):
    """Audience allowed to see a help entry."""

    EVERYONE = "everyone"
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


@dataclass(frozen=True, slots=True)
class GlossaryEntry:
    """A plain-language definition used by the in-app help system."""

    term: str
    definition: str
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.term.strip():
            raise ValueError("glossary term is required")
        if not self.definition.strip():
            raise ValueError("glossary definition is required")


COMMAND_HELP: tuple[CommandHelp, ...] = (
    CommandHelp("/help", "Show the WASH command guide and key definitions.", "General"),
    CommandHelp("/ping", "Check WASH latency and uptime.", "General"),
    CommandHelp(
        "/about", "Learn about WASH, its features, roles, version, and project.", "General"
    ),
    CommandHelp("/stats", "Show watch-party activity statistics.", "General"),
    CommandHelp("/add", "Add a watch item by title or IMDb link.", "Watch Items"),
    CommandHelp(
        "/list", "List watch items in the relevant suggestion database.", "Watch Items"
    ),
    CommandHelp("/remove", "Remove a watch item.", "Watch Items"),
    CommandHelp("/start_vote", "Start a new voting round.", "Voting"),
    CommandHelp("/vote_status", "View the current voting round.", "Voting"),
    CommandHelp("/vote", "Cast or update your vote.", "Voting"),
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
        "/diagnostics",
        "Show WASH runtime diagnostics.",
        "WASH Crew: Diagnostics",
        HelpAudience.WASH_CREW,
    ),
)


GLOSSARY: tuple[GlossaryEntry, ...] = (
    GlossaryEntry(
        "Watch Item",
        "A movie, episode, special, or other title tracked by WASH as a suggestion, nominee, winner, or watched selection.",
        ("item", "title"),
    ),
    GlossaryEntry(
        "Suggestion Database",
        "A named collection of watch items associated with a Discord server, channel, or thread.",
        ("database", "suggestion list"),
    ),
    GlossaryEntry(
        "WASH Crew",
        "The configurable Discord role allowed to manage suggestion databases, start votes, and use administrative tools.",
        ("crew", "admin", "administrator"),
    ),
    GlossaryEntry(
        "Blind Vote",
        "A voting round where current standings and individual selections stay hidden until voting closes.",
        ("blind voting",),
    ),
    GlossaryEntry(
        "Visible Vote",
        "A voting round where current standings may be shown while the round is open.",
        ("visible voting", "open vote"),
    ),
    GlossaryEntry(
        "Journey",
        "The lifetime history of a watch item, including nominations, wins, watched dates, and other accumulated activity.",
        ("watch item journey", "history"),
    ),
    GlossaryEntry(
        "Rotation",
        "A managed group or cycle of watch items used to organize future selections and prevent the same items from dominating repeatedly.",
        ("current rotation",),
    ),
    GlossaryEntry(
        "Voting Round",
        "A time-limited contest between selected watch items in which eligible members cast or update a vote.",
        ("vote", "round"),
    ),
)


def command_sections(*, show_wash_crew: bool) -> tuple[tuple[str, tuple[CommandHelp, ...]], ...]:
    """Return visible command entries grouped in their declared order."""
    grouped: dict[str, list[CommandHelp]] = {}
    for entry in COMMAND_HELP:
        if entry.audience is HelpAudience.WASH_CREW and not show_wash_crew:
            continue
        grouped.setdefault(entry.section, []).append(entry)
    return tuple((section, tuple(entries)) for section, entries in grouped.items())


def build_command_help_text(*, show_wash_crew: bool = True) -> str:
    """Render the command registry as Discord-friendly text."""
    sections = ["**WASH Commands**"]
    for section, entries in command_sections(show_wash_crew=show_wash_crew):
        lines = [f"**{section}**"]
        lines.extend(f"`{entry.name}` - {entry.summary}" for entry in entries)
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def find_glossary_entry(term: str) -> GlossaryEntry | None:
    """Find a glossary entry by term or alias, ignoring case and whitespace."""
    normalized = term.strip().casefold()
    if not normalized:
        return None
    for entry in GLOSSARY:
        names = (entry.term, *entry.aliases)
        if any(normalized == name.casefold() for name in names):
            return entry
    return None


def build_glossary_text() -> str:
    """Render all glossary entries as compact Discord-friendly text."""
    lines = ["**WASH Definitions**"]
    lines.extend(f"**{entry.term}** - {entry.definition}" for entry in GLOSSARY)
    return "\n\n".join(lines)
