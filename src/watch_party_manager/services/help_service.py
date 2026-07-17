"""Application-layer helpers for the WASH slash-command help response."""

from __future__ import annotations

from watch_party_manager.help_registry import (
    build_command_help_text,
    build_glossary_text,
)


def build_help_response(*, show_wash_crew: bool) -> tuple[str, bool]:
    """Return the role-aware help text and its Discord ephemeral flag.

    Keeping the visibility decision in this service makes the behavior easy to
    test without importing Discord. The command handler should pass the second
    return value to ``interaction.response.send_message(..., ephemeral=...)``.
    """
    message = "\n\n".join(
        (
            build_command_help_text(show_wash_crew=show_wash_crew),
            build_glossary_text(),
        )
    )
    return message, True
