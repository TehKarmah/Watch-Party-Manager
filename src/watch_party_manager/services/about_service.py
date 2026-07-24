"""Discord-agnostic content for the WASH /about command.

/about is WASH's single status and information dashboard: "tell me about
this running instance of WASH" (as opposed to /help's "how do I use
WASH?"). Everyone gets the WASH identity and Documentation links; the
Health, Configuration, and Runtime sections -- the information that used
to live behind the separate, WASH Crew-only /diagnostics command -- are
only included for WASH Crew (see build_about_content's
show_expanded_sections).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

PROJECT_REPOSITORY_URL = "https://github.com/TehKarmah/Watch-Party-Manager"
COMMANDS_REFERENCE_URL = (
    "https://github.com/TehKarmah/Watch-Party-Manager/blob/main/docs/10-Command-Reference.md"
)
EXPANDED_HELP_URL = (
    "https://github.com/TehKarmah/Watch-Party-Manager/blob/main/docs/08-Expanded-Help.md"
)
TAGLINE = "Organizing great watch parties, one vote at a time."
WASH_ACCENT_COLOR = 0xF5C518
ABOUT_FOOTER = "WASH"


@dataclass(frozen=True, slots=True)
class AboutHealth:
    """Concise health indicators, formerly /diagnostics-only."""

    discord_connected: bool
    scheduler_running: bool
    interactive_voting_restored: bool
    omdb_configured: bool


@dataclass(frozen=True, slots=True)
class AboutConfiguration:
    """Concise, guild-scoped runtime configuration counts.

    active_database_name is a ready-to-display string: the active
    database's name when exactly one is active, or an explanatory
    placeholder ("None configured" / "N active (ambiguous)") otherwise --
    resolved by the caller (see bot.py's handle_about), which already has
    the suggestion_service list needed to tell those cases apart.
    """

    active_database_name: str
    database_count: int
    watch_item_count: int
    scheduled_watch_party_count: int
    open_vote_round: bool


@dataclass(frozen=True, slots=True)
class AboutRuntime:
    """Runtime facts safe to show a WASH Crew member -- deliberately
    excludes anything only useful for debugging (file paths, internal
    object counts, stack traces, etc.).
    """

    python_version: str
    discord_py_version: str
    guild_name: Optional[str]


@dataclass(frozen=True, slots=True)
class AboutField:
    name: str
    value: str
    inline: bool = False


@dataclass(frozen=True, slots=True)
class AboutContent:
    title: str
    description: str
    fields: tuple[AboutField, ...]
    color: int
    footer: str


def _format_uptime(started_at: datetime, now: datetime) -> str:
    if started_at.tzinfo is None or started_at.utcoffset() is None:
        raise ValueError("started_at must be timezone-aware")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    total = max(0, int((now - started_at).total_seconds()))
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


def _format_latency(latency_ms: float) -> str:
    rounded = round(latency_ms)
    if rounded < 250:
        return f"🟢 Good ({rounded} ms)"
    if rounded < 500:
        return f"🟡 Slow ({rounded} ms)"
    return f"🔴 Poor ({rounded} ms)"


def _build_health_field(health: AboutHealth, latency_ms: Optional[float]) -> AboutField:
    discord_line = "🟢 Connected" if health.discord_connected else "🔴 Disconnected"
    if health.discord_connected and latency_ms is not None:
        discord_line = _format_latency(latency_ms)
    scheduler_line = "🟢 Running" if health.scheduler_running else "🔴 Stopped"
    omdb_line = "🟢 Configured" if health.omdb_configured else "🔴 Not configured"
    lines = [
        f"Discord connection: {discord_line}",
        f"Scheduler: {scheduler_line}",
        f"Interactive voting restored: {'Yes' if health.interactive_voting_restored else 'No'}",
        f"OMDb integration: {omdb_line}",
    ]
    return AboutField(name="Health", value="\n".join(lines))


def _build_configuration_field(configuration: AboutConfiguration) -> AboutField:
    lines = [
        f"Active suggestion database: {configuration.active_database_name}",
        f"Suggestion databases: {configuration.database_count}",
        f"Watch items: {configuration.watch_item_count}",
        f"Scheduled watch parties: {configuration.scheduled_watch_party_count}",
        f"Active voting round: {'Yes' if configuration.open_vote_round else 'No'}",
    ]
    return AboutField(name="Configuration", value="\n".join(lines))


def _build_runtime_field(runtime_info: AboutRuntime, started_at: Optional[datetime], now: Optional[datetime]) -> AboutField:
    lines = [
        f"Python: {runtime_info.python_version}",
        f"discord.py: {runtime_info.discord_py_version}",
    ]
    if started_at is not None and now is not None:
        lines.append(f"Uptime: {_format_uptime(started_at, now)}")
    lines.append(f"Server: {runtime_info.guild_name}" if runtime_info.guild_name else "Server: Unknown")
    return AboutField(name="Runtime", value="\n".join(lines))


def _build_documentation_field() -> AboutField:
    lines = [
        f"[GitHub Repository]({PROJECT_REPOSITORY_URL})",
        f"[Command Reference]({COMMANDS_REFERENCE_URL})",
        f"[Expanded Help]({EXPANDED_HELP_URL})",
    ]
    return AboutField(name="Documentation", value="\n".join(lines))


def build_about_content(
    version: str,
    build: str,
    *,
    show_expanded_sections: bool = False,
    latency_ms: Optional[float] = None,
    started_at: Optional[datetime] = None,
    now: Optional[datetime] = None,
    health: Optional[AboutHealth] = None,
    configuration: Optional[AboutConfiguration] = None,
    runtime_info: Optional[AboutRuntime] = None,
) -> AboutContent:
    """Build /about's content: WASH identity for everyone, plus Health,
    Configuration, and Runtime for WASH Crew (show_expanded_sections).

    Documentation links are always included, and always rendered by the
    caller as a Discord embed (never as raw message content) -- Discord
    only auto-generates a link-preview card for links in a message's
    plain content, never for a link inside an embed field's value.
    """
    clean_version = version.strip()
    clean_build = build.strip()
    if not clean_version:
        raise ValueError("version is required")
    if not clean_build:
        raise ValueError("build is required")

    description = "\n".join(
        (
            f"*{TAGLINE}*",
            "",
            f"Version: `{clean_version}`",
            f"Build: `{clean_build}`",
        )
    )

    fields: list[AboutField] = []
    if show_expanded_sections:
        if health is not None:
            fields.append(_build_health_field(health, latency_ms))
        if configuration is not None:
            fields.append(_build_configuration_field(configuration))
        if runtime_info is not None:
            fields.append(_build_runtime_field(runtime_info, started_at, now))
    fields.append(_build_documentation_field())

    return AboutContent(
        title="WASH",
        description=description,
        fields=tuple(fields),
        color=WASH_ACCENT_COLOR,
        footer=ABOUT_FOOTER,
    )
