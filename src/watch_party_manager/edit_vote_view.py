"""Discord UI components for /edit_vote's management, modal, and confirmation flows.

Like start_vote_view.py and restore_confirmation_view.py, this module has
no dependency on bot.py: each view/modal here only knows how to render
itself and forward a click or submission to a caller-supplied callback.
All validation and vote-editing logic lives in bot.py's
perform_reschedule_vote_round()/parse_discord_timestamp_vote_end_time()/
perform_end_vote_now()/perform_cancel_vote_now(), reused unchanged
regardless of which button is clicked -- this module adds presentation
only.
"""

from __future__ import annotations

from typing import Awaitable, Callable

import discord

OnChangeEndTime = Callable[[discord.Interaction], Awaitable[None]]
OnCancelVote = Callable[[discord.Interaction], Awaitable[None]]
OnEditVoteConfirmed = Callable[[discord.Interaction], Awaitable[None]]
OnEditVoteAborted = Callable[[discord.Interaction], Awaitable[None]]
OnQuickEndTimePick = Callable[[discord.Interaction, int], Awaitable[None]]
OnEndNowQuickPick = Callable[[discord.Interaction], Awaitable[None]]
OnChooseCustomEndTime = Callable[[discord.Interaction], Awaitable[None]]
OnCustomEndTimeSubmit = Callable[[discord.Interaction, str], Awaitable[None]]

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
    """The /edit_vote management prompt: change end time (which now
    includes ending it immediately as one of its quick options -- see
    VoteEndTimeQuickPickView), or cancel the vote outright.
    """

    def __init__(
        self,
        on_change_end_time: OnChangeEndTime,
        on_cancel_vote: OnCancelVote,
    ) -> None:
        """Initialize the view.

        Args:
            on_change_end_time: Called when "Change End Time" is clicked.
            on_cancel_vote: Called when "Cancel Vote" is clicked.
        """
        super().__init__(timeout=EDIT_VOTE_VIEW_TIMEOUT_SECONDS)
        self.add_item(ChangeEndTimeButton(on_change_end_time))
        self.add_item(CancelVoteButton(on_cancel_vote))


class EndNowQuickPickButton(discord.ui.Button):
    """Ends the vote immediately -- the first, most-final quick option."""

    def __init__(self, on_click: OnEndNowQuickPick) -> None:
        super().__init__(
            label="End Now", style=discord.ButtonStyle.danger, custom_id="wpm_edit_vote_quick_end_now"
        )
        self._callback = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._callback(interaction)


class QuickEndTimeButton(discord.ui.Button):
    """One "In N Minutes/Hour/Day" quick-pick option."""

    def __init__(self, label: str, minutes: int, on_click: OnQuickEndTimePick, *, custom_id: str) -> None:
        super().__init__(label=label, style=discord.ButtonStyle.primary, custom_id=custom_id)
        self._minutes = minutes
        self._callback = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._callback(interaction, self._minutes)


class ChooseCustomEndTimeButton(discord.ui.Button):
    """Opens the Custom Time modal."""

    def __init__(self, on_click: OnChooseCustomEndTime) -> None:
        super().__init__(
            label="Custom Time...",
            style=discord.ButtonStyle.secondary,
            custom_id="wpm_edit_vote_choose_custom_end_time",
        )
        self._callback = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._callback(interaction)


# (label, minutes-from-now) for each timed quick pick, in display order
# (after End Now, before Custom Time...). Kept as minutes (rather than
# pre-built timedeltas) so bot.py can compute "now" itself right at click
# time, matching how every other "must be in the future" check works.
VOTE_END_TIME_QUICK_PICKS: tuple[tuple[str, int], ...] = (
    ("In 5 Minutes", 5),
    ("In 1 Hour", 60),
    ("In 1 Day", 24 * 60),
)


class VoteEndTimeQuickPickView(discord.ui.View):
    """The guided "Change End Time" flow's menu: End Now, timed quick
    options, or a hand-off to the Custom Time modal for anything else.
    """

    def __init__(
        self,
        on_end_now: OnEndNowQuickPick,
        on_quick_pick: OnQuickEndTimePick,
        on_choose_custom: OnChooseCustomEndTime,
    ) -> None:
        """Initialize the view.

        Args:
            on_end_now: Called when "End Now" is clicked.
            on_quick_pick: Called with (interaction, minutes_from_now) when
                one of the timed quick-pick buttons is clicked.
            on_choose_custom: Called when "Custom Time..." is clicked.
        """
        super().__init__(timeout=EDIT_VOTE_VIEW_TIMEOUT_SECONDS)
        self.add_item(EndNowQuickPickButton(on_end_now))
        for index, (label, minutes) in enumerate(VOTE_END_TIME_QUICK_PICKS):
            self.add_item(
                QuickEndTimeButton(
                    label, minutes, on_quick_pick, custom_id=f"wpm_edit_vote_quick_end_time_{index}"
                )
            )
        self.add_item(ChooseCustomEndTimeButton(on_choose_custom))


class CustomVoteEndTimeModal(discord.ui.Modal):
    """Collects a single Discord-native timestamp (e.g. "<t:1785639600:F>")
    rather than typed date/time fields.

    Parsing -- validating the syntax and rejecting a malformed or past
    timestamp -- happens in bot.py's
    parse_discord_timestamp_vote_end_time(), reused unchanged; this modal
    only collects the raw text.
    """

    def __init__(self, on_submit: OnCustomEndTimeSubmit) -> None:
        super().__init__(title="Custom Time")
        self._submit_callback = on_submit

        self.timestamp_input = discord.ui.TextInput(
            label="Discord Timestamp",
            placeholder="<t:1785639600:F>",
            required=True,
        )
        self.add_item(self.timestamp_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Forward the raw timestamp text to the configured handler."""
        await self._submit_callback(interaction, self.timestamp_input.value)


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
