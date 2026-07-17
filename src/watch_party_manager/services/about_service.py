"""Discord-agnostic content for the WASH /about command."""

from __future__ import annotations

from dataclasses import dataclass

PROJECT_REPOSITORY_URL = "https://github.com/TehKarmah/Watch-Party-Manager"
TAGLINE = "Organizing great watch parties, one vote at a time."


@dataclass(frozen=True, slots=True)
class AboutField:
    """One named section in the WASH about response."""

    name: str
    value: str
    inline: bool = False


@dataclass(frozen=True, slots=True)
class AboutContent:
    """Presentation-neutral content used to build the /about embed."""

    title: str
    description: str
    fields: tuple[AboutField, ...]


def build_about_content(version: str, build: str) -> AboutContent:
    """Build the stable content shown by the ephemeral /about command."""
    clean_version = version.strip()
    clean_build = build.strip()
    if not clean_version:
        raise ValueError("version is required")
    if not clean_build:
        raise ValueError("build is required")

    return AboutContent(
        title="WASH",
        description=(
            "**Watch Party Administration & Scheduling Helper**\n\n"
            f"*{TAGLINE}*"
        ),
        fields=(
            AboutField(
                name="📦 Version & Build",
                value=f"**Version:** `{clean_version}`\n**Build:** `{clean_build}`",
            ),
            AboutField(
                name="🎬 Features",
                value=(
                    "• Watch item suggestions\n"
                    "• Intelligent nominee selection\n"
                    "• Interactive voting\n"
                    "• Suggestion databases\n"
                    "• Watch history\n"
                    "• Statistics & diagnostics"
                ),
            ),
            AboutField(
                name="👥 Roles",
                value=(
                    "**🎟️ Watch Party**\n"
                    "Members who suggest watch items and participate in voting.\n\n"
                    "**🛠️ WASH Crew**\n"
                    "Members who manage watch party databases, voting, configuration, "
                    "and administration."
                ),
            ),
            AboutField(
                name="📁 Project",
                value=(
                    "**Watch Party Manager**\n"
                    "Created by **TehKarmah**\n"
                    f"[GitHub repository]({PROJECT_REPOSITORY_URL})"
                ),
            ),
        ),
    )
