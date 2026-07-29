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
OnEndNowQuickPick = Callable[[discord.Interaction], Awaitable[None]]
OnShortenVote = Callable[[discord.Interaction], Awaitable[None]]
OnExtendVote = Callable[[discord.Interaction], Awaitable[None]]
OnSetExactEndTime = Callable[[discord.Interaction], Awaitable[None]]
OnDurationDeltaPick = Callable[[discord.Interaction, int], Awaitable[None]]
OnChooseCustomDelta = Callable[[discord.Interaction], Awaitable[None]]
OnCustomDurationSubmit = Callable[[discord.Interaction, str], Awaitable[None]]
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
    includes ending it immediately as one of its options -- see
    VoteEndTimeMenuView), or cancel the vote outright.
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
    """Ends the vote immediately -- the first, most-final option."""

    def __init__(self, on_click: OnEndNowQuickPick) -> None:
        super().__init__(
            label="End Now", style=discord.ButtonStyle.danger, custom_id="wpm_edit_vote_quick_end_now"
        )
        self._callback = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._callback(interaction)


class ShortenVoteButton(discord.ui.Button):
    """Opens the Shorten Vote submenu (1 Hour / 1 Day / Custom...)."""

    def __init__(self, on_click: OnShortenVote) -> None:
        super().__init__(
            label="Shorten Vote", style=discord.ButtonStyle.primary, custom_id="wpm_edit_vote_shorten"
        )
        self._callback = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._callback(interaction)


class ExtendVoteButton(discord.ui.Button):
    """Opens the Extend Vote submenu (1 Hour / 1 Day / Custom...)."""

    def __init__(self, on_click: OnExtendVote) -> None:
        super().__init__(
            label="Extend Vote", style=discord.ButtonStyle.primary, custom_id="wpm_edit_vote_extend"
        )
        self._callback = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._callback(interaction)


class SetExactEndTimeButton(discord.ui.Button):
    """Opens the Discord Timestamp modal (formerly "Custom Time...")."""

    def __init__(self, on_click: OnSetExactEndTime) -> None:
        super().__init__(
            label="Set Exact End Time",
            style=discord.ButtonStyle.secondary,
            custom_id="wpm_edit_vote_set_exact_end_time",
        )
        self._callback = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._callback(interaction)


class VoteEndTimeMenuView(discord.ui.View):
    """The "Change End Time" flow's top-level menu: Shorten Vote, Extend
    Vote, Set Exact End Time, or End Now.

    Button order follows WASH's house convention (primary actions, then
    secondary, then the destructive action last) -- End Now is the
    irreversible one, so it's ordered last despite being conceptually the
    "simplest" option, matching every other menu in the app (e.g.
    EditVoteManagementView's own Change End Time / Cancel Vote ordering).
    """

    def __init__(
        self,
        on_end_now: OnEndNowQuickPick,
        on_shorten: OnShortenVote,
        on_extend: OnExtendVote,
        on_set_exact: OnSetExactEndTime,
    ) -> None:
        """Initialize the view.

        Args:
            on_end_now: Called when "End Now" is clicked.
            on_shorten: Called when "Shorten Vote" is clicked.
            on_extend: Called when "Extend Vote" is clicked.
            on_set_exact: Called when "Set Exact End Time" is clicked.
        """
        super().__init__(timeout=EDIT_VOTE_VIEW_TIMEOUT_SECONDS)
        self.add_item(ShortenVoteButton(on_shorten))
        self.add_item(ExtendVoteButton(on_extend))
        self.add_item(SetExactEndTimeButton(on_set_exact))
        self.add_item(EndNowQuickPickButton(on_end_now))


class DurationDeltaButton(discord.ui.Button):
    """One "1 Hour"/"1 Day" quick-pick option within Shorten/Extend Vote."""

    def __init__(self, label: str, minutes: int, on_click: OnDurationDeltaPick, *, custom_id: str) -> None:
        super().__init__(label=label, style=discord.ButtonStyle.primary, custom_id=custom_id)
        self._minutes = minutes
        self._callback = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._callback(interaction, self._minutes)


class ChooseCustomDeltaButton(discord.ui.Button):
    """Opens the Custom Duration modal within Shorten/Extend Vote."""

    def __init__(self, on_click: OnChooseCustomDelta, *, custom_id: str) -> None:
        super().__init__(
            label="Custom...",
            style=discord.ButtonStyle.secondary,
            custom_id=custom_id,
        )
        self._callback = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._callback(interaction)


class CancelDeltaButton(discord.ui.Button):
    """Dismisses Shorten/Extend Vote's submenu without changing anything --
    every multi-step control surface in WASH offers an explicit way out
    rather than relying on the view silently timing out.
    """

    def __init__(self, on_click: OnEditVoteAborted, *, custom_id: str) -> None:
        super().__init__(label="Cancel", style=discord.ButtonStyle.secondary, custom_id=custom_id)
        self._callback = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._callback(interaction)


# (label, minutes) for Shorten/Extend Vote's quick picks, in display order.
DURATION_DELTA_QUICK_PICKS: tuple[tuple[str, int], ...] = (
    ("1 Hour", 60),
    ("1 Day", 24 * 60),
)


class DurationDeltaChoiceView(discord.ui.View):
    """Shorten/Extend Vote's shared submenu: 1 Hour, 1 Day, or a hand-off
    to the Custom Duration modal for anything else. The direction (add
    for Extend, subtract for Shorten) is entirely the caller's concern --
    this view only collects a magnitude in minutes.
    """

    def __init__(
        self,
        on_quick_pick: OnDurationDeltaPick,
        on_choose_custom: OnChooseCustomDelta,
        on_cancel: OnEditVoteAborted,
        *,
        custom_id_prefix: str,
    ) -> None:
        """Initialize the view.

        Args:
            on_quick_pick: Called with (interaction, minutes) when "1 Hour"
                or "1 Day" is clicked.
            on_choose_custom: Called when "Custom..." is clicked.
            on_cancel: Called when "Cancel" is clicked.
            custom_id_prefix: Distinguishes this menu's button custom_ids
                from the other one (Shorten vs. Extend), since both are
                otherwise identical.
        """
        super().__init__(timeout=EDIT_VOTE_VIEW_TIMEOUT_SECONDS)
        for index, (label, minutes) in enumerate(DURATION_DELTA_QUICK_PICKS):
            self.add_item(
                DurationDeltaButton(label, minutes, on_quick_pick, custom_id=f"{custom_id_prefix}_{index}")
            )
        self.add_item(ChooseCustomDeltaButton(on_choose_custom, custom_id=f"{custom_id_prefix}_custom"))
        self.add_item(CancelDeltaButton(on_cancel, custom_id=f"{custom_id_prefix}_cancel"))


class CustomDurationModal(discord.ui.Modal):
    """Collects a single free-text duration (e.g. "10m", "1h", "1d", "1w")
    for Shorten/Extend Vote's Custom... option, using WASH's one shared
    duration syntax (see services.duration_parser).

    Parsing and validation happen in bot.py; this modal only collects the
    raw text. title is caller-supplied ("Shorten Vote" or "Extend Vote")
    so the modal itself names the action being customized.
    """

    def __init__(self, on_submit: OnCustomDurationSubmit, *, title: str) -> None:
        super().__init__(title=title)
        self._submit_callback = on_submit

        self.duration_input = discord.ui.TextInput(
            label="Duration",
            placeholder="e.g. 10m, 1h, 1d, or 1w",
            required=True,
        )
        self.add_item(self.duration_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Forward the raw duration text to the configured handler."""
        await self._submit_callback(interaction, self.duration_input.value)


class CustomVoteEndTimeModal(discord.ui.Modal):
    """Collects a single Discord-native timestamp (e.g. "<t:1785639600:F>")
    rather than typed date/time fields, for "Set Exact End Time".

    Parsing -- validating the syntax and rejecting a malformed or past
    timestamp -- happens in bot.py's
    parse_discord_timestamp_vote_end_time(), reused unchanged; this modal
    only collects the raw text.
    """

    def __init__(self, on_submit: OnCustomEndTimeSubmit) -> None:
        super().__init__(title="Set Exact End Time")
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
    """Proceeds with the action the member confirmed."""

    def __init__(
        self, label: str, on_click: OnEditVoteConfirmed, *, style: discord.ButtonStyle = discord.ButtonStyle.danger
    ) -> None:
        super().__init__(
            label=label,
            style=style,
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
    """A generic confirm/abort safeguard, originally for /edit_vote's
    destructive actions ("End Now", "Cancel Vote") but also reused
    elsewhere in the app for any yes/no confirmation prompt -- mirrors
    RestoreConfirmationView's exact confirm/cancel pattern, generalized
    with a caller-supplied confirm_label so this one class covers many
    near-identical confirmations instead of each inventing its own.
    """

    def __init__(
        self,
        *,
        confirm_label: str,
        on_confirm: OnEditVoteConfirmed,
        on_abort: OnEditVoteAborted,
        confirm_style: discord.ButtonStyle = discord.ButtonStyle.danger,
    ) -> None:
        """Initialize the view.

        Args:
            confirm_label: The confirm button's label (e.g. "End Now" or
                "Cancel Vote"), so the prompt clearly names the action
                being confirmed.
            on_confirm: Called when the confirm button is clicked.
            on_abort: Called when "Cancel" is clicked.
            confirm_style: The confirm button's style. Defaults to
                danger (red), correct for this view's original
                destructive-action use -- callers confirming a
                non-destructive action (e.g. "Add Anyway" past a
                possible-duplicate warning) should pass
                discord.ButtonStyle.primary instead, so a merely
                cautionary confirmation doesn't read as alarming as an
                irreversible one.
        """
        super().__init__(timeout=EDIT_VOTE_CONFIRMATION_TIMEOUT_SECONDS)
        self.add_item(ConfirmEditVoteActionButton(confirm_label, on_confirm, style=confirm_style))
        self.add_item(AbortEditVoteActionButton(on_abort))
