"""Application-layer helpers for the WASH slash-command help response."""

from __future__ import annotations

from dataclasses import dataclass

from watch_party_manager.help_registry import build_command_help_text


EXPANDED_HELP_URL = (
    "https://github.com/TehKarmah/Watch-Party-Manager/blob/main/docs/08-Expanded-Help.md"
)


@dataclass(frozen=True, slots=True)
class HelpResponse:
    """The Discord message(s) to send for a /help invocation."""

    messages: tuple[str, ...]
    ephemeral: bool

    def __post_init__(self) -> None:
        if not self.messages:
            raise ValueError("messages must contain at least one message")


def build_expanded_help_link_text() -> str:
    """Return the single link to WASH expanded help documentation."""
    return "\n".join(
        (
            "**Expanded Help Documentation**",
            f"[Open the WASH help guide on GitHub]({EXPANDED_HELP_URL})",
        )
    )


def build_help_response(*, show_wash_crew: bool) -> HelpResponse:
    """Return the role-aware command guide and documentation links.

    /help remains a concise command reference. Definitions, setup details,
    administration procedures, and other infrequently used material stay in
    the GitHub documentation, which is the single source of truth.
    """
    command_text = build_command_help_text(show_wash_crew=show_wash_crew)
    reference_text = build_expanded_help_link_text()

    if show_wash_crew:
        return HelpResponse(
            messages=(command_text, reference_text),
            ephemeral=True,
        )

    return HelpResponse(
        messages=("\n\n".join((command_text, reference_text)),),
        ephemeral=True,
    )
