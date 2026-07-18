"""Discord-agnostic content for the WASH /about command."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

PROJECT_REPOSITORY_URL = "https://github.com/TehKarmah/Watch-Party-Manager"
TAGLINE = "Organizing great watch parties, one vote at a time."
WASH_ACCENT_COLOR = 0xF5C518
ABOUT_FOOTER = "Watch Party Manager • TehKarmah"


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
    url: str
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


def build_about_content(
    version: str,
    build: str,
    *,
    latency_ms: float | None = None,
    started_at: datetime | None = None,
    now: datetime | None = None,
) -> AboutContent:
    """Build the readable, description-only /about response."""
    clean_version = version.strip()
    clean_build = build.strip()
    if not clean_version:
        raise ValueError("version is required")
    if not clean_build:
        raise ValueError("build is required")

    lines = [
        "**Watch Party Administration & Scheduling Helper**",
        "",
        f"*{TAGLINE}*",
        "",
        "**Version & Build**",
        f"Version: `{clean_version}`",
        f"Build: `{clean_build}`",
    ]
    if latency_ms is not None and started_at is not None and now is not None:
        lines.extend((
            "",
            "**Status**",
            "Online",
            f"Gateway latency: {round(latency_ms)} ms",
            f"Uptime: {_format_uptime(started_at, now)}",
        ))
    lines.extend((
        "",
        "**Features**",
        "• Watch item suggestions",
        "• Intelligent nominee selection",
        "• Interactive voting",
        "• Suggestion databases",
        "• Watch history",
        "• Statistics & diagnostics",
        "",
        "**Roles**",
        "**Watch Party**: Suggest watch items and participate in voting.",
        "**WASH Crew**: Manage databases, voting, configuration, and administration.",
        "",
        "**Project**",
        f"Created by **TehKarmah** • [GitHub repository]({PROJECT_REPOSITORY_URL})",
    ))
    return AboutContent(
        title="WASH",
        description="\n".join(lines),
        fields=(),
        url=PROJECT_REPOSITORY_URL,
        color=WASH_ACCENT_COLOR,
        footer=ABOUT_FOOTER,
    )
