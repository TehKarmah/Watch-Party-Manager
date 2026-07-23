"""Application-layer helpers for the WASH slash-command help response."""

from __future__ import annotations

from dataclasses import dataclass

from watch_party_manager.help_registry import build_command_help_text


EXPANDED_HELP_URL = (
    "https://github.com/TehKarmah/Watch-Party-Manager/blob/main/docs/08-Expanded-Help.md"
)
COMMANDS_REFERENCE_URL = (
    "https://github.com/TehKarmah/Watch-Party-Manager/blob/main/docs/10-Command-Reference.md"
)


@dataclass(frozen=True, slots=True)
class HelpResponse:
    """The Discord content for a /help invocation.

    command_text is the role-aware command list; reference_title/
    reference_description/reference_url describe the Commands Reference
    link that bot.py's send_help_response renders as a Discord embed
    rather than plain message content -- Discord only auto-generates a
    link-preview card (here, a large GitHub repository card) for links
    found in a message's plain content, never for a link inside an
    embed's own title/description (Release Polish Priority 3).
    """

    command_text: str
    reference_title: str
    reference_description: str
    reference_url: str
    ephemeral: bool

    def __post_init__(self) -> None:
        if not self.command_text.strip():
            raise ValueError("command_text must not be empty")
        if not self.reference_title.strip():
            raise ValueError("reference_title must not be empty")
        if not self.reference_url.strip():
            raise ValueError("reference_url must not be empty")


def build_help_response(*, show_wash_crew: bool, show_watch_party_member: bool = False) -> HelpResponse:
    """Return the role-aware command guide and Commands Reference link.

    /help remains a concise command reference. Definitions, setup details,
    administration procedures, and other infrequently used material stay in
    the GitHub documentation, which is the single source of truth.

    FR-029's three-tier permission model (everyone / Watch Party member /
    WASH Crew) is reflected here via show_watch_party_member in addition
    to show_wash_crew -- show_wash_crew implies show_watch_party_member
    (see help_registry.command_sections), matching
    PermissionService.is_wash_crew's own inheritance.
    """
    command_text = build_command_help_text(
        show_wash_crew=show_wash_crew, show_watch_party_member=show_watch_party_member
    )
    reference_description = (
        "Every WASH command, grouped by permission level, with required roles and key options.\n\n"
        f"For deeper explanations of WASH concepts, see the [Expanded Help Guide]({EXPANDED_HELP_URL})."
    )

    return HelpResponse(
        command_text=command_text,
        reference_title="Commands Reference",
        reference_description=reference_description,
        reference_url=COMMANDS_REFERENCE_URL,
        ephemeral=True,
    )
