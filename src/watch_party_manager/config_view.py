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
from watch_party_manager.setup_wizard_view import _JOIN_MODE_OPTIONS, DestinationChannelSelect

CONFIG_VIEW_TIMEOUT_SECONDS = 900

OnConfigRoleSelected = Callable[[discord.Interaction, Optional[int]], Awaitable[None]]
OnConfigJoinModeSelected = Callable[[discord.Interaction, JoinMode], Awaitable[None]]
OnConfigDatabaseSelected = Callable[[discord.Interaction, int], Awaitable[None]]
OnConfigChannelSelected = Callable[[discord.Interaction, int], Awaitable[None]]
OnConfigSkip = Callable[[discord.Interaction], Awaitable[None]]
OnConfigSectionChosen = Callable[[discord.Interaction, str], Awaitable[None]]
OnBackToMenu = Callable[[discord.Interaction], Awaitable[None]]
OnConfigRetry = Callable[[discord.Interaction], Awaitable[None]]
OnConfigVotingDefaultsSubmit = Callable[[discord.Interaction, str, str, str, str], Awaitable[None]]
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
            placeholder="Select the join mode",
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
                placeholder="Select an existing channel or thread",
            )
        )
        self.add_item(ConfigClearAdminChannelButton(on_clear))
        self.add_item(BackToMenuButton(on_back))


# --- Active Suggestion Database -----------------------------------------------------------


class ConfigDatabaseSelect(discord.ui.Select):
    def __init__(self, databases: List[Tuple[int, str]], on_select: OnConfigDatabaseSelected) -> None:
        options = [
            discord.SelectOption(label=name[:100], value=str(database_id))
            for database_id, name in databases[:25]
        ]
        super().__init__(placeholder="Choose a suggestion database", options=options, custom_id="wpm_config_database_select")
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


# --- Watched-Movie Destination -------------------------------------------------------------


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
                placeholder="Select an existing channel or thread",
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
