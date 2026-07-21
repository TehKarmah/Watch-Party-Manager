"""Discord UI components for /edit_vote's management, modal, and confirmation flows.

Like start_vote_view.py and restore_confirmation_view.py, this module has
no dependency on bot.py: each view/modal here only knows how to render
itself and forward a click or submission to a caller-supplied callback.
All validation and vote-editing logic lives in bot.py's
perform_change_vote_end_time()/perform_end_vote_now()/perform_cancel_vote_now(),
reused unchanged regardless of which button is clicked -- this module
adds presentation only.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Optional

import discord

OnChangeEndTime = Callable[[discord.Interaction], Awaitable[None]]
OnEndNow = Callable[[discord.Interaction], Awaitable[None]]
OnCancelVote = Callable[[discord.Interaction], Awaitable[None]]
OnEndTimeSubmit = Callable[[discord.Interaction, str], Awaitable[None]]
OnEditVoteConfirmed = Callable[[discord.Interaction], Awaitable[None]]
OnEditVoteAborted = Callable[[discord.Interaction], Awaitable[None]]

# A short timeout is appropriate here, matching StartVoteChoiceView -- this
# is a one-time management prompt for whoever ran /edit_vote, not a
# long-lived control surface, so it doesn't need restart persistence.
EDIT_VOTE_VIEW_TIMEOUT_SECONDS = 180

# Matches RestoreConfirmationView's timeout for the same reason: a one-time
# safety prompt, not a persistent view.
EDIT_VOTE_CONFIRMATION_TIMEOUT_SECONDS = 60


class ChangeEndTimeButton(discord.ui.Button):
    """Opens the "change end time" modal."""

    def __init__(self, on_click: OnChangeEndTime) -> None:
        super().__init__(
            label="Change End Time",
            style=discord.ButtonStyle.primary,
            custom_id="wpm_edit_vote_change_end_time",
        )
        self._callback = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._callback(interaction)


class EndVoteNowButton(discord.ui.Button):
    """Starts the "end vote now" confirmation flow."""

    def __init__(self, on_click: OnEndNow) -> None:
        super().__init__(
            label="End Now",
            style=discord.ButtonStyle.danger,
            custom_id="wpm_edit_vote_end_now",
        )
        self._callback = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._callback(interaction)


class CancelVoteButton(discord.ui.Button):
    """Starts the "cancel vote" confirmation flow."""

    def __init__(self, on_click: OnCancelVote) -> None:
        super().__init__(
            label="Cancel Vote",
            style=discord.ButtonStyle.danger,
            custom_id="wpm_edit_vote_cancel_vote",
        )
        self._callback = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._callback(interaction)


class EditVoteManagementView(discord.ui.View):
    """The /edit_vote management prompt: change end time, end now, or cancel."""

    def __init__(
        self,
        on_change_end_time: OnChangeEndTime,
        on_end_now: OnEndNow,
        on_cancel_vote: OnCancelVote,
    ) -> None:
        """Initialize the view.

        Args:
            on_change_end_time: Called when "Change End Time" is clicked.
            on_end_now: Called when "End Now" is clicked.
            on_cancel_vote: Called when "Cancel Vote" is clicked.
        """
        super().__init__(timeout=EDIT_VOTE_VIEW_TIMEOUT_SECONDS)
        self.add_item(ChangeEndTimeButton(on_change_end_time))
        self.add_item(EndVoteNowButton(on_end_now))
        self.add_item(CancelVoteButton(on_cancel_vote))


class EditVoteEndTimeModal(discord.ui.Modal):
    """Collects the voting round's new end date/time.

    Parsing and validation (including "must be in the future") happen in
    bot.py's parse_vote_end_time(), reused unchanged -- this modal only
    collects the raw text.
    """

    def __init__(self, on_submit: OnEndTimeSubmit, *, current_value: Optional[str] = None) -> None:
        """Initialize the modal.

        Args:
            on_submit: Called with (interaction, when_text) once submitted.
            current_value: Pre-fills the field with the round's current
                closing time, if known, so WASH Crew can see what they're
                changing from.
        """
        super().__init__(title="Change Vote End Time")
        self._submit_callback = on_submit

        self.when_input = discord.ui.TextInput(
            label="New end date/time (e.g. 2026-08-01 20:00)",
            required=True,
            default=current_value,
        )
        self.add_item(self.when_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Forward the raw end-time text to the configured handler."""
        await self._submit_callback(interaction, self.when_input.value)


class ConfirmEditVoteActionButton(discord.ui.Button):
    """Proceeds with the destructive action the member confirmed."""

    def __init__(self, label: str, on_click: OnEditVoteConfirmed) -> None:
        super().__init__(
            label=label,
            style=discord.ButtonStyle.danger,
            custom_id="wpm_edit_vote_confirm",
        )
        self._callback = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._callback(interaction)


class AbortEditVoteActionButton(discord.ui.Button):
    """Aborts the confirmation prompt without touching the vote."""

    def __init__(self, on_click: OnEditVoteAborted) -> None:
        super().__init__(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
            custom_id="wpm_edit_vote_abort",
        )
        self._callback = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._callback(interaction)


class EditVoteConfirmationView(discord.ui.View):
    """A generic confirm/abort safeguard for /edit_vote's destructive actions.

    Reused for both "End Now" and "Cancel Vote" -- mirrors
    RestoreConfirmationView's exact confirm/cancel pattern, generalized
    with a caller-supplied confirm_label so this one class covers both
    confirmations instead of two near-identical copies.
    """

    def __init__(
        self,
        *,
        confirm_label: str,
        on_confirm: OnEditVoteConfirmed,
        on_abort: OnEditVoteAborted,
    ) -> None:
        """Initialize the view.

        Args:
            confirm_label: The confirm button's label (e.g. "End Now" or
                "Cancel Vote"), so the prompt clearly names the action
                being confirmed.
            on_confirm: Called when the confirm button is clicked.
            on_abort: Called when "Cancel" is clicked.
        """
        super().__init__(timeout=EDIT_VOTE_CONFIRMATION_TIMEOUT_SECONDS)
        self.add_item(ConfirmEditVoteActionButton(confirm_label, on_confirm))
        self.add_item(AbortEditVoteActionButton(on_abort))
