"""Discord UI components for /start_vote's defaults-vs-customize flow.

Like voting_view.py, this module has no dependency on bot.py: the view and
modal here only know how to render themselves and forward a click or
submission to caller-supplied callbacks. All validation and round-creation
logic lives in bot.py's perform_start_vote(), reused unchanged by both
paths -- this module adds presentation only.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Optional

import discord

OnUseDefaults = Callable[[discord.Interaction], Awaitable[None]]
OnCustomizeChosen = Callable[[discord.Interaction], Awaitable[None]]
OnCustomizeSubmit = Callable[
    [discord.Interaction, Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]],
    Awaitable[None],
]

START_VOTE_CHOICE_TIMEOUT_SECONDS = 180


class UseDefaultsButton(discord.ui.Button):
    """Starts a voting round using the configured defaults."""

    def __init__(self, on_click: OnUseDefaults) -> None:
        super().__init__(
            label="Use Defaults",
            style=discord.ButtonStyle.primary,
            custom_id="wpm_start_vote_use_defaults",
        )
        self._callback = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        """Forward the interaction to the configured defaults handler."""
        await self._callback(interaction)


class CustomizeVoteButton(discord.ui.Button):
    """Opens the customization modal for this one voting round."""

    def __init__(self, on_click: OnCustomizeChosen) -> None:
        super().__init__(
            label="Customize This Vote",
            style=discord.ButtonStyle.secondary,
            custom_id="wpm_start_vote_customize",
        )
        self._callback = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        """Forward the interaction to the configured customization handler."""
        await self._callback(interaction)


class StartVoteChoiceView(discord.ui.View):
    """The initial /start_vote prompt: use defaults, or customize this vote.

    A short timeout (rather than None) is appropriate here, unlike
    VotingView -- this is a one-time setup prompt for whoever ran
    /start_vote, not a long-lived public control surface, so it doesn't
    need restart persistence.
    """

    def __init__(self, on_use_defaults: OnUseDefaults, on_customize: OnCustomizeChosen) -> None:
        """Initialize the view.

        Args:
            on_use_defaults: Called when "Use Defaults" is clicked.
            on_customize: Called when "Customize This Vote" is clicked.
        """
        super().__init__(timeout=START_VOTE_CHOICE_TIMEOUT_SECONDS)
        self.add_item(UseDefaultsButton(on_use_defaults))
        self.add_item(CustomizeVoteButton(on_customize))


class CustomizeVoteModal(discord.ui.Modal):
    """Collects nominee count, duration, visibility, and reminder overrides.

    All fields are optional text inputs -- a blank field means "use the
    configured default for this setting", matching how nominee_count and
    duration_days already behave as optional /start_vote parameters.
    Values are handed to the on_submit callback as raw strings; parsing
    and validation happen in bot.py, reusing the exact same functions
    /start_vote's direct parameters already used, so nothing here
    duplicates that logic.

    FR-027 added the two reminder fields -- Discord modals support at
    most 5 components, and this was already at 3, so this is the modal's
    full capacity.
    """

    def __init__(self, on_submit: OnCustomizeSubmit) -> None:
        """Initialize the modal.

        Args:
            on_submit: Called with (interaction, nominee_count_text,
                duration_days_text, visibility_text, reminder_enabled_text,
                reminder_hours_text) once submitted.
        """
        super().__init__(title="Customize This Vote")
        self._submit_callback = on_submit

        self.nominee_count_input = discord.ui.TextInput(
            label="Candidate count (2-10)",
            required=False,
            placeholder="Leave blank to use the configured default",
        )
        self.duration_days_input = discord.ui.TextInput(
            label="Voting duration in days",
            required=False,
            placeholder="Leave blank to use the configured default",
        )
        self.visibility_input = discord.ui.TextInput(
            label="Visibility: blind or visible",
            required=False,
            placeholder="Leave blank to use the configured default",
        )
        self.reminder_enabled_input = discord.ui.TextInput(
            label="Reminder before close? (yes/no)",
            required=False,
            placeholder="Leave blank to use the configured default",
        )
        self.reminder_hours_input = discord.ui.TextInput(
            label="Reminder hours before close (1-720)",
            required=False,
            placeholder="e.g. 1, 4, 12, 24, or 48 -- blank uses the default",
        )
        self.add_item(self.nominee_count_input)
        self.add_item(self.duration_days_input)
        self.add_item(self.visibility_input)
        self.add_item(self.reminder_enabled_input)
        self.add_item(self.reminder_hours_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Forward the raw optional field values to the configured handler."""
        await self._submit_callback(
            interaction,
            self.nominee_count_input.value or None,
            self.duration_days_input.value or None,
            self.visibility_input.value or None,
            self.reminder_enabled_input.value or None,
            self.reminder_hours_input.value or None,
        )
