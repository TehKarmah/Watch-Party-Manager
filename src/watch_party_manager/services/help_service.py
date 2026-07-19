"""Application-layer helpers for the WASH slash-command help response."""

from __future__ import annotations

from dataclasses import dataclass

from watch_party_manager.help_registry import (
    build_command_help_text,
    build_glossary_text,
)


@dataclass(frozen=True, slots=True)
class HelpResponse:
    """The Discord message(s) to send for a /help invocation.

    Regular members receive a single combined message, unchanged from
    before this existed. WASH Crew members receive two separate messages
    (commands, then glossary) to stay within Discord's 2000-character
    message limit as the command list grows -- ``messages`` is always in
    the order they should be sent.
    """

    messages: tuple[str, ...]
    ephemeral: bool

    def __post_init__(self) -> None:
        if not self.messages:
            raise ValueError("messages must contain at least one message")


def build_help_response(*, show_wash_crew: bool) -> HelpResponse:
    """Return the role-aware help message(s) and their ephemeral flag.

    Keeping the message-splitting decision in this service, rather than in
    the Discord command handler, makes the response shape easy to test
    without importing Discord. The handler should send ``messages[0]`` via
    ``interaction.response.send_message`` and any remaining messages via
    ``interaction.followup.send``, both using ``ephemeral``.
    """
    commands_text = build_command_help_text(show_wash_crew=show_wash_crew)
    glossary_text = build_glossary_text()

    if show_wash_crew:
        # Split rather than concatenated: the combined WASH Crew text
        # already sits at Discord's 2000-character limit with the current
        # command set, so any further growth needs headroom on both
        # halves rather than one message carrying everything.
        return HelpResponse(messages=(commands_text, glossary_text), ephemeral=True)

    message = "\n\n".join((commands_text, glossary_text))
    return HelpResponse(messages=(message,), ephemeral=True)
