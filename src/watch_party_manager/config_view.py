"""Discord UI components for FR-029's /config command.

Like setup_wizard_view.py, this module has no dependency on bot.py: every
view/modal here only knows how to render itself and forward a
selection/click/submission to a caller-supplied callback. All validation,
persistence, and section dispatch live in services/config_service.py and
bot.py's wiring around it.

Reuses setup_wizard_view.py's generic, already-appropriate components
directly (DestinationChannelSelect, the join-mode options, and the three
defaults modals) rather than duplicating them -- only components whose
wording is wizard-specific ("Cancel Setup", multi-step "Continue") are
rebuilt here with /config's own navigation ("Back to Menu"), since /config
edits one section at a time and returns to a menu rather than advancing
through a sequence.

Each screen is a short-lived, ephemeral prompt (timeout, not None).
"""

from __future__ import annotations

from typing import Awaitable, Callable, List, Optional, Tuple

import discord

from watch_party_manager.domain.guild_configuration import JoinMode
from watch_party_manager.domain.suggestion_database_configuration import CandidateSelectionMode
from watch_party_manager.setup_wizard_view import (
    _JOIN_MODE_OPTIONS,
    CandidateSelectionSelectComponent,
    ConfigureStepButton,
    DestinationChannelSelect,
)

CONFIG_VIEW_TIMEOUT_SECONDS = 900

OnConfigRoleSelected = Callable[[discord.Interaction, Optional[int]], Awaitable[None]]
OnConfigJoinModeSelected = Callable[[discord.Interaction, JoinMode], Awaitable[None]]
OnConfigDatabaseSelected = Callable[[discord.Interaction, int], Awaitable[None]]
OnConfigChannelSelected = Callable[[discord.Interaction, int], Awaitable[None]]
OnConfigSkip = Callable[[discord.Interaction], Awaitable[None]]
OnConfigSectionChosen = Callable[[discord.Interaction, str], Awaitable[None]]
OnBackToMenu = Callable[[discord.Interaction], Awaitable[None]]
OnConfigRetry = Callable[[discord.Interaction], Awaitable[None]]
OnConfigVotingDefaultsSubmit = Callable[[discord.Interaction, str, str, str], Awaitable[None]]
OnConfigVotingDefaultsConfigure = Callable[[discord.Interaction, CandidateSelectionMode], Awaitable[None]]
OnConfigReminderDefaultsSubmit = Callable[[discord.Interaction, str, str], Awaitable[None]]
OnConfigBackupDefaultsSubmit = Callable[[discord.Interaction, str, str], Awaitable[None]]


class BackToMenuButton(discord.ui.Button):
    """Returns to the main /config menu without changing anything. Present
    on every section screen.
    """

    def __init__(self, on_click: OnBackToMenu) -> None:
        super().__init__(label="Back to Menu", style=discord.ButtonStyle.secondary, custom_id="wpm_config_back_to_menu")
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


# --- Main menu --------------------------------------------------------------------------


class ConfigSectionSelect(discord.ui.Select):
    """The main menu's "choose a section to edit" dropdown."""

    def __init__(self, section_options: List[Tuple[str, str]], on_select: OnConfigSectionChosen) -> None:
        options = [discord.SelectOption(label=label, value=value) for value, label in section_options]
        super().__init__(placeholder="Choose a section to edit...", options=options, custom_id="wpm_config_section_select")
        self._on_select = on_select

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_select(interaction, self.values[0])


class ConfigMainMenuView(discord.ui.View):
    """The main /config screen: a configuration summary plus a section picker."""

    def __init__(self, section_options: List[Tuple[str, str]], on_select: OnConfigSectionChosen) -> None:
        super().__init__(timeout=CONFIG_VIEW_TIMEOUT_SECONDS)
        self.add_item(ConfigSectionSelect(section_options, on_select))


# --- WASH Crew Role / Watch Party Role (generic role picker) ----------------------------


class ConfigRoleSelect(discord.ui.RoleSelect):
    """A role picker reused for both the WASH Crew Role and Watch Party
    Role sections -- only placeholder/custom_id/min_values differ.
    """

    def __init__(
        self,
        on_select: OnConfigRoleSelected,
        *,
        custom_id: str,
        placeholder: str,
        min_values: int = 1,
    ) -> None:
        super().__init__(placeholder=placeholder, min_values=min_values, max_values=1, custom_id=custom_id)
        self._on_select = on_select

    async def callback(self, interaction: discord.Interaction) -> None:
        role_id = self.values[0].id if self.values else None
        await self._on_select(interaction, role_id)


class ConfigRoleSectionView(discord.ui.View):
    """A section screen offering a single role picker plus Back to Menu."""

    def __init__(
        self,
        on_select: OnConfigRoleSelected,
        on_back: OnBackToMenu,
        *,
        custom_id: str,
        placeholder: str,
        min_values: int = 1,
    ) -> None:
        super().__init__(timeout=CONFIG_VIEW_TIMEOUT_SECONDS)
        self.add_item(ConfigRoleSelect(on_select, custom_id=custom_id, placeholder=placeholder, min_values=min_values))
        self.add_item(BackToMenuButton(on_back))


# --- Watch Party Join Mode ---------------------------------------------------------------


class ConfigJoinModeSelect(discord.ui.Select):
    def __init__(self, on_select: OnConfigJoinModeSelected) -> None:
        super().__init__(
            placeholder="Choose the join mode",
            options=_JOIN_MODE_OPTIONS,
            custom_id="wpm_config_join_mode_select",
        )
        self._on_select = on_select

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_select(interaction, JoinMode(self.values[0]))


class ConfigJoinModeSectionView(discord.ui.View):
    def __init__(self, on_select: OnConfigJoinModeSelected, on_back: OnBackToMenu) -> None:
        super().__init__(timeout=CONFIG_VIEW_TIMEOUT_SECONDS)
        self.add_item(ConfigJoinModeSelect(on_select))
        self.add_item(BackToMenuButton(on_back))


class BackToMenuOnlyView(discord.ui.View):
    """A single Back to Menu button -- shown when a section has nothing to
    pick from yet (e.g. no suggestion databases exist).
    """

    def __init__(self, on_back: OnBackToMenu) -> None:
        super().__init__(timeout=CONFIG_VIEW_TIMEOUT_SECONDS)
        self.add_item(BackToMenuButton(on_back))


# --- Admin Channel -------------------------------------------------------------------------


class ConfigClearAdminChannelButton(discord.ui.Button):
    def __init__(self, on_click: OnConfigSkip) -> None:
        super().__init__(label="Clear Admin Channel", style=discord.ButtonStyle.secondary, custom_id="wpm_config_admin_channel_clear")
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class ConfigAdminChannelSectionView(discord.ui.View):
    """Reuses setup_wizard_view.py's generic DestinationChannelSelect,
    exactly like ConfigWatchDestinationSectionView.
    """

    def __init__(self, on_select: OnConfigChannelSelected, on_clear: OnConfigSkip, on_back: OnBackToMenu) -> None:
        super().__init__(timeout=CONFIG_VIEW_TIMEOUT_SECONDS)
        self.add_item(
            DestinationChannelSelect(
                on_select,
                custom_id="wpm_config_admin_channel_select",
                placeholder="Choose an existing channel or thread",
            )
        )
        self.add_item(ConfigClearAdminChannelButton(on_clear))
        self.add_item(BackToMenuButton(on_back))


# --- Manage Databases ----------------------------------------------------------------------
#
# Replaces the old "Active Suggestion Database" section: WASH Crew picks
# which database to manage (ConfigDatabaseSectionView, below -- reused
# unchanged from that section, just re-purposed to open a per-database
# settings menu instead of activating one), then edits that database's
# own destination/candidate-selection settings directly.


class ConfigDatabaseSelect(discord.ui.Select):
    def __init__(self, databases: List[Tuple[int, str]], on_select: OnConfigDatabaseSelected) -> None:
        options = [
            discord.SelectOption(label=name[:100], value=str(database_id))
            for database_id, name in databases[:25]
        ]
        super().__init__(placeholder="Choose a collection", options=options, custom_id="wpm_config_database_select")
        self._on_select = on_select

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_select(interaction, int(self.values[0]))


class ConfigDatabaseSectionView(discord.ui.View):
    def __init__(
        self, databases: List[Tuple[int, str]], on_select: OnConfigDatabaseSelected, on_back: OnBackToMenu
    ) -> None:
        super().__init__(timeout=CONFIG_VIEW_TIMEOUT_SECONDS)
        self.add_item(ConfigDatabaseSelect(databases, on_select))
        self.add_item(BackToMenuButton(on_back))


OnDatabaseSettingChosen = Callable[[discord.Interaction, str], Awaitable[None]]

DATABASE_SETTING_SUGGESTION_DESTINATION = "suggestion_destination"
DATABASE_SETTING_WATCH_DESTINATION = "watch_destination"
DATABASE_SETTING_CANDIDATE_SELECTION = "candidate_selection"


class DatabaseSettingSelect(discord.ui.Select):
    def __init__(self, on_select: OnDatabaseSettingChosen) -> None:
        options = [
            discord.SelectOption(label="Suggestion Post Destination", value=DATABASE_SETTING_SUGGESTION_DESTINATION),
            discord.SelectOption(label="Watched Movie Destination", value=DATABASE_SETTING_WATCH_DESTINATION),
            discord.SelectOption(label="Candidate Selection", value=DATABASE_SETTING_CANDIDATE_SELECTION),
        ]
        super().__init__(
            placeholder="Choose a setting to edit...", options=options, custom_id="wpm_config_database_setting_select"
        )
        self._on_select = on_select

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_select(interaction, self.values[0])


class ConfigDatabaseSettingsMenuView(discord.ui.View):
    """One database's settings menu -- pick which of its own settings to edit."""

    def __init__(self, on_select: OnDatabaseSettingChosen, on_back: OnBackToMenu) -> None:
        super().__init__(timeout=CONFIG_VIEW_TIMEOUT_SECONDS)
        self.add_item(DatabaseSettingSelect(on_select))
        self.add_item(BackToMenuButton(on_back))


class ConfigDatabaseCandidateSelectionView(discord.ui.View):
    """One database's Candidate Selection setting: choose from the
    dropdown (reusing setup_wizard_view.py's CandidateSelectionSelectComponent
    unchanged) and press Save.
    """

    def __init__(
        self,
        on_save: OnConfigVotingDefaultsConfigure,
        on_back: OnBackToMenu,
        *,
        default_candidate_selection: CandidateSelectionMode,
    ) -> None:
        super().__init__(timeout=CONFIG_VIEW_TIMEOUT_SECONDS)
        self.candidate_selection_select = CandidateSelectionSelectComponent(default=default_candidate_selection)
        self.add_item(self.candidate_selection_select)
        self.add_item(
            ConfigureStepButton(
                self._handle_save,
                label="Save Candidate Selection",
                custom_id="wpm_config_database_candidate_selection_save",
            )
        )
        self.add_item(BackToMenuButton(on_back))
        self._on_save = on_save

    async def _handle_save(self, interaction: discord.Interaction) -> None:
        await self._on_save(interaction, self.candidate_selection_select.selected)


# --- Suggestion Post Destination ------------------------------------------------------------


class ConfigSuggestionDestinationSectionView(discord.ui.View):
    """Reuses setup_wizard_view.py's generic DestinationChannelSelect.

    Deliberately has no Clear/Skip button, unlike ConfigWatchDestinationSectionView:
    every collection MUST have exactly one dedicated suggestion
    destination, so this section can only change it to a different
    channel or thread, never unset it.
    """

    def __init__(self, on_select: OnConfigChannelSelected, on_back: OnBackToMenu) -> None:
        super().__init__(timeout=CONFIG_VIEW_TIMEOUT_SECONDS)
        self.add_item(
            DestinationChannelSelect(
                on_select,
                custom_id="wpm_config_suggestion_destination_channel_select",
                placeholder="Choose an existing channel or thread",
            )
        )
        self.add_item(BackToMenuButton(on_back))


# --- Watched Movie Destination -------------------------------------------------------------


class ConfigSkipDestinationButton(discord.ui.Button):
    def __init__(self, on_click: OnConfigSkip) -> None:
        super().__init__(label="Clear Destination", style=discord.ButtonStyle.secondary, custom_id="wpm_config_destination_clear")
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class ConfigWatchDestinationSectionView(discord.ui.View):
    """Reuses setup_wizard_view.py's generic DestinationChannelSelect."""

    def __init__(self, on_select: OnConfigChannelSelected, on_skip: OnConfigSkip, on_back: OnBackToMenu) -> None:
        super().__init__(timeout=CONFIG_VIEW_TIMEOUT_SECONDS)
        self.add_item(
            DestinationChannelSelect(
                on_select,
                custom_id="wpm_config_watch_destination_channel_select",
                placeholder="Choose an existing channel or thread",
            )
        )
        self.add_item(ConfigSkipDestinationButton(on_skip))
        self.add_item(BackToMenuButton(on_back))



# --- Modal-defaults retry screen (Voting / Reminder / Backup Defaults) ---------------------


class ConfigRetryModalButton(discord.ui.Button):
    def __init__(self, on_click: OnConfigRetry, *, label: str, custom_id: str) -> None:
        super().__init__(label=label, style=discord.ButtonStyle.primary, custom_id=custom_id)
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class ConfigModalRetryView(discord.ui.View):
    """Shown only after a modal submission fails validation -- Discord
    can't reopen a modal directly from a failed modal submission, so this
    offers a button that opens a fresh one, plus Back to Menu.
    """

    def __init__(
        self, on_retry: OnConfigRetry, on_back: OnBackToMenu, *, button_label: str, custom_id: str
    ) -> None:
        super().__init__(timeout=CONFIG_VIEW_TIMEOUT_SECONDS)
        self.add_item(ConfigRetryModalButton(on_retry, label=button_label, custom_id=custom_id))
        self.add_item(BackToMenuButton(on_back))
