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

from watch_party_manager.domain.guild_configuration import GuildVoteVisibility
from watch_party_manager.domain.suggestion_database_configuration import CandidateSelectionMode
from watch_party_manager.setup_wizard_view import CandidateSelectionSelectComponent, VisibilitySelectComponent

OnUseDefaults = Callable[[discord.Interaction], Awaitable[None]]
OnCustomizeChosen = Callable[[discord.Interaction], Awaitable[None]]
OnCustomizeOverridesContinue = Callable[
    [discord.Interaction, CandidateSelectionMode, GuildVoteVisibility], Awaitable[None]
]
OnCustomizeSubmit = Callable[
    [discord.Interaction, Optional[str], Optional[str], Optional[str], Optional[str]],
    Awaitable[None],
]

START_VOTE_CHOICE_TIMEOUT_SECONDS = 180


def _blank_default_placeholder(default_display: str) -> str:
    """"Leave blank to use the configured default", naming the actual value
    when the caller could resolve one, otherwise the plain phrasing.
    """
    if not default_display:
        return "Leave blank to use the configured default"
    return f"Leave blank to use the configured default ({default_display})"


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


class ContinueToVoteSettingsButton(discord.ui.Button):
    """Opens the Customize This Vote modal, carrying forward whichever
    candidate selection mode and visibility are currently selected in the
    two dropdowns above it.
    """

    def __init__(
        self,
        on_click: OnCustomizeOverridesContinue,
        candidate_selection_select: CandidateSelectionSelectComponent,
        visibility_select: VisibilitySelectComponent,
    ) -> None:
        super().__init__(
            label="Continue to Vote Settings",
            style=discord.ButtonStyle.primary,
            custom_id="wpm_start_vote_customize_candidate_selection_continue",
        )
        self._callback = on_click
        self._candidate_selection_select = candidate_selection_select
        self._visibility_select = visibility_select

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._callback(interaction, self._candidate_selection_select.selected, self._visibility_select.selected)


class CustomizeVoteOverridesView(discord.ui.View):
    """Customize This Vote, step one: optionally override this collection's
    configured Candidate Selection Mode and/or the guild's configured Vote
    Visibility, both for this one round only -- nothing chosen here is
    ever saved back to the collection's or guild's own configuration (see
    bot.py's handle_customize_vote_submit). Reuses
    CandidateSelectionSelectComponent and VisibilitySelectComponent
    unchanged, so their option labels, help text, and persisted values can
    never drift from the Setup Wizard's or /config's own dropdowns.

    A separate step from CustomizeVoteModal because Discord modals cannot
    contain Select menus -- the same reason the Setup Wizard's Voting
    Defaults step is a dropdown-then-modal flow rather than one screen.
    """

    def __init__(
        self,
        on_continue: OnCustomizeOverridesContinue,
        *,
        default_candidate_selection: CandidateSelectionMode,
        default_visibility: GuildVoteVisibility,
    ) -> None:
        super().__init__(timeout=START_VOTE_CHOICE_TIMEOUT_SECONDS)
        self.candidate_selection_select = CandidateSelectionSelectComponent(default=default_candidate_selection)
        self.visibility_select = VisibilitySelectComponent(default=default_visibility)
        self.add_item(self.candidate_selection_select)
        self.add_item(self.visibility_select)
        self.add_item(ContinueToVoteSettingsButton(on_continue, self.candidate_selection_select, self.visibility_select))


class CustomizeVoteModal(discord.ui.Modal):
    """Collects nominee count, duration, and reminder overrides.

    All fields are optional text inputs -- a blank field means "use the
    configured default for this setting", matching how nominee_count and
    duration_minutes already behave as optional /start_vote parameters.
    Values are handed to the on_submit callback as raw strings; parsing
    and validation happen in bot.py, reusing the exact same functions
    /start_vote's direct parameters already used, so nothing here
    duplicates that logic.

    Visibility (General Fixed-Option Control Audit) moved out to its own
    dropdown on CustomizeVoteOverridesView, the step before this modal --
    a free-text "blind or visible" field let WASH Crew mistype it into a
    rejected vote, when Candidate Selection Mode already got the same
    fixed-choice-dropdown treatment. This also leaves headroom under
    Discord's 5-component modal limit (FR-027's two reminder fields
    filled the modal to that limit while visibility was still a text
    field).
    """

    def __init__(
        self,
        on_submit: OnCustomizeSubmit,
        *,
        default_nominee_count_display: str = "",
        default_duration_display: str = "",
        default_reminder_enabled_display: str = "",
        default_reminder_minutes_display: str = "",
    ) -> None:
        """Initialize the modal.

        Args:
            on_submit: Called with (interaction, nominee_count_text,
                duration_text, reminder_enabled_text,
                reminder_minutes_text) once submitted.
            default_nominee_count_display: The guild's actual configured
                candidate count (e.g. "5"), shown in the field's
                placeholder so "leave blank" never leaves WASH Crew
                guessing what that means. Each default_*_display param
                below does the same for its own field; all default to ""
                (falls back to the plain "leave blank" wording with no
                value shown) so callers that can't resolve a guild's
                configuration yet -- or existing callers/tests -- keep
                working unchanged.
        """
        super().__init__(title="Customize This Vote")
        self._submit_callback = on_submit

        self.nominee_count_input = discord.ui.TextInput(
            label="Candidate count (2-10)",
            required=False,
            placeholder=_blank_default_placeholder(default_nominee_count_display),
        )
        self.duration_input = discord.ui.TextInput(
            label="Duration (1m-30d)",
            required=False,
            placeholder=(
                "Examples: 10m, 1h, 7d -- blank uses the default"
                + (f" ({default_duration_display})" if default_duration_display else "")
            ),
        )
        self.reminder_enabled_input = discord.ui.TextInput(
            label="Reminder before close? (yes/no)",
            required=False,
            placeholder=_blank_default_placeholder(default_reminder_enabled_display),
        )
        self.reminder_minutes_input = discord.ui.TextInput(
            label="Reminder timing (1m-30d)",
            required=False,
            placeholder=(
                "Examples: 10m, 1h, 7d -- blank uses the default"
                + (f" ({default_reminder_minutes_display})" if default_reminder_minutes_display else "")
            ),
        )
        self.add_item(self.nominee_count_input)
        self.add_item(self.duration_input)
        self.add_item(self.reminder_enabled_input)
        self.add_item(self.reminder_minutes_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Forward the raw optional field values to the configured handler."""
        await self._submit_callback(
            interaction,
            self.nominee_count_input.value or None,
            self.duration_input.value or None,
            self.reminder_enabled_input.value or None,
            self.reminder_minutes_input.value or None,
        )
