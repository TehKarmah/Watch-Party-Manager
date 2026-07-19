"""Discord UI components for /restore's confirmation safeguard.

Like start_vote_view.py, this module has no dependency on bot.py: the view
here only knows how to render itself and forward a click to a
caller-supplied callback. All validation and restore logic lives in
bot.py's perform_restore_backup(), reused unchanged regardless of which
button is clicked -- this module adds presentation only.
"""

from __future__ import annotations

from typing import Awaitable, Callable

import discord

OnRestoreConfirmed = Callable[[discord.Interaction], Awaitable[None]]
OnRestoreCancelled = Callable[[discord.Interaction], Awaitable[None]]

RESTORE_CONFIRMATION_TIMEOUT_SECONDS = 60


class ConfirmRestoreButton(discord.ui.Button):
    """Proceeds with the restore the member selected."""

    def __init__(self, on_click: OnRestoreConfirmed) -> None:
        super().__init__(
            label="Confirm Restore",
            style=discord.ButtonStyle.danger,
            custom_id="wpm_restore_confirm",
        )
        self._callback = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        """Forward the interaction to the configured confirm handler."""
        await self._callback(interaction)


class CancelRestoreButton(discord.ui.Button):
    """Cancels the restore without touching any data."""

    def __init__(self, on_click: OnRestoreCancelled) -> None:
        super().__init__(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
            custom_id="wpm_restore_cancel",
        )
        self._callback = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        """Forward the interaction to the configured cancel handler."""
        await self._callback(interaction)


class RestoreConfirmationView(discord.ui.View):
    """The /restore safeguard prompt: confirm, or cancel.

    A short timeout is appropriate here, matching StartVoteChoiceView --
    this is a one-time safety prompt for whoever ran /restore, not a
    long-lived control surface, so it doesn't need restart persistence.
    """

    def __init__(self, on_confirm: OnRestoreConfirmed, on_cancel: OnRestoreCancelled) -> None:
        """Initialize the view.

        Args:
            on_confirm: Called when "Confirm Restore" is clicked.
            on_cancel: Called when "Cancel" is clicked.
        """
        super().__init__(timeout=RESTORE_CONFIRMATION_TIMEOUT_SECONDS)
        self.add_item(ConfirmRestoreButton(on_confirm))
        self.add_item(CancelRestoreButton(on_cancel))
