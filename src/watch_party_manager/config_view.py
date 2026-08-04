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

from watch_party_manager.domain.guild_configuration import GuildVoteVisibility, JoinMode
from watch_party_manager.domain.suggestion_database_configuration import CandidateSelectionMode
from watch_party_manager.services.discord_ui_limits import build_safe_select_option
from watch_party_manager.setup_wizard_view import (
    _JOIN_MODE_OPTIONS,
    CandidateSelectionSelectComponent,
    ChannelDestinationSelect,
    ConfigureStepButton,
    CreateNewChannelButton,
    DestinationChannelSelect,
    ExistingChannelSelectView,
    HomeChannelNameModal,
    UseExistingChannelButton,
    VisibilitySelectComponent,
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
OnConfigVotingDefaultsSubmit = Callable[[discord.Interaction, str, str], Awaitable[None]]
OnConfigRetryModalSubmit = Callable[[discord.Interaction, str], Awaitable[None]]
OnConfigVotingDefaultsConfigure = Callable[[discord.Interaction, CandidateSelectionMode], Awaitable[None]]
OnConfigVotingDefaultsIntroConfigure = Callable[
    [discord.Interaction, GuildVoteVisibility, Optional[CandidateSelectionMode]], Awaitable[None]
]
OnConfigReminderDefaultsSubmit = Callable[[discord.Interaction, str, str], Awaitable[None]]
OnConfigBackupDefaultsSubmit = Callable[[discord.Interaction, str, str], Awaitable[None]]
OnConfigDone = Callable[[discord.Interaction], Awaitable[None]]


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

    def __init__(
        self,
        section_options: List[Tuple[str, str]],
        on_select: OnConfigSectionChosen,
        *,
        descriptions: Optional[dict[str, str]] = None,
    ) -> None:
        descriptions = descriptions or {}
        options = [
            build_safe_select_option(label, value, description=descriptions.get(value))
            for value, label in section_options
        ]
        super().__init__(placeholder="Choose a section to edit...", options=options, custom_id="wpm_config_section_select")
        self._on_select = on_select

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_select(interaction, self.values[0])


class ConfigDoneButton(discord.ui.Button):
    """Closes the /config session. Labeled "Done" rather than "Save" --
    every section already persists its change immediately on selection,
    so there is never anything left to save here.
    """

    def __init__(self, on_click: OnConfigDone) -> None:
        super().__init__(label="Done", style=discord.ButtonStyle.primary, custom_id="wpm_config_done")
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class ConfigMainMenuView(discord.ui.View):
    """The main /config screen: a configuration summary plus a section picker."""

    def __init__(
        self,
        section_options: List[Tuple[str, str]],
        on_select: OnConfigSectionChosen,
        on_done: OnConfigDone,
        *,
        descriptions: Optional[dict[str, str]] = None,
    ) -> None:
        super().__init__(timeout=CONFIG_VIEW_TIMEOUT_SECONDS)
        self.add_item(ConfigSectionSelect(section_options, on_select, descriptions=descriptions))
        self.add_item(ConfigDoneButton(on_done))


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
    """Reuses setup_wizard_view.py's generic ChannelDestinationSelect,
    exactly like ConfigWatchDestinationSectionView. `options` is built
    fresh by the caller on every render (see bot.py's
    build_channel_destination_options), so a thread created since this
    section was last opened is never missing and the currently saved
    channel is always shown pre-selected.
    """

    def __init__(
        self,
        options: List[discord.SelectOption],
        on_select: OnConfigChannelSelected,
        on_clear: OnConfigSkip,
        on_back: OnBackToMenu,
    ) -> None:
        super().__init__(timeout=CONFIG_VIEW_TIMEOUT_SECONDS)
        self.add_item(
            ChannelDestinationSelect(
                options,
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
    def __init__(
        self,
        databases: List[Tuple[int, str]],
        on_select: OnConfigDatabaseSelected,
        *,
        descriptions: Optional[dict[int, str]] = None,
    ) -> None:
        descriptions = descriptions or {}
        options = [
            build_safe_select_option(name, str(database_id), description=descriptions.get(database_id))
            for database_id, name in databases[:25]
        ]
        super().__init__(placeholder="Choose a collection", options=options, custom_id="wpm_config_database_select")
        self._on_select = on_select

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_select(interaction, int(self.values[0]))


class ConfigDatabaseSectionView(discord.ui.View):
    def __init__(
        self,
        databases: List[Tuple[int, str]],
        on_select: OnConfigDatabaseSelected,
        on_back: OnBackToMenu,
        *,
        descriptions: Optional[dict[int, str]] = None,
    ) -> None:
        super().__init__(timeout=CONFIG_VIEW_TIMEOUT_SECONDS)
        self.add_item(ConfigDatabaseSelect(databases, on_select, descriptions=descriptions))
        self.add_item(BackToMenuButton(on_back))


OnDatabaseSettingChosen = Callable[[discord.Interaction, str], Awaitable[None]]

DATABASE_SETTING_SUGGESTION_DESTINATION = "suggestion_destination"
DATABASE_SETTING_WATCH_DESTINATION = "watch_destination"
DATABASE_SETTING_CANDIDATE_SELECTION = "candidate_selection"


class DatabaseSettingSelect(discord.ui.Select):
    def __init__(self, on_select: OnDatabaseSettingChosen) -> None:
        options = [
            discord.SelectOption(label="Suggestion Post Destination", value=DATABASE_SETTING_SUGGESTION_DESTINATION),
            discord.SelectOption(label="Watched Item Archive", value=DATABASE_SETTING_WATCH_DESTINATION),
            discord.SelectOption(label="Nominee Selection", value=DATABASE_SETTING_CANDIDATE_SELECTION),
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


class ConfigRejectionThresholdModal(discord.ui.Modal):
    """One database's "I Won't Watch" threshold: a single number field,
    mirroring EligiblePoolWarningThresholdModal's shape. Opened only from
    the dedicated "I Won't Watch" Settings section's Enable button (see
    ConfigRejectionSettingsView) -- whether the feature is enabled at all
    is a separate, prior choice, not part of this modal.
    """

    def __init__(self, on_submit: OnConfigRetryModalSubmit, *, default: str) -> None:
        super().__init__(title="I Won't Watch Threshold")
        self._submit_callback = on_submit
        self.threshold_input = discord.ui.TextInput(label="Threshold (1-10)", default=default)
        self.add_item(self.threshold_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._submit_callback(interaction, self.threshold_input.value)


class ConfigEnableRejectionSettingsButton(discord.ui.Button):
    def __init__(self, on_click: OnConfigRetry) -> None:
        super().__init__(label="Enable", style=discord.ButtonStyle.primary, custom_id="wpm_config_rejection_enable")
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class ConfigDisableRejectionSettingsButton(discord.ui.Button):
    def __init__(self, on_click: OnConfigRetry) -> None:
        super().__init__(label="Disable", style=discord.ButtonStyle.secondary, custom_id="wpm_config_rejection_disable")
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class ConfigRejectionSettingsView(discord.ui.View):
    """/config's dedicated "I Won't Watch" Settings section for one
    collection: Enable (opens ConfigRejectionThresholdModal) or Disable,
    mirroring ConfigReminderDefaultsChoiceView/ConfigBackupDefaultsChoiceView's
    identical enable/disable-then-configure shape (see also the Setup
    Wizard's own RejectionSettingsChoiceView, which this mirrors).
    """

    def __init__(self, on_enable: OnConfigRetry, on_disable: OnConfigRetry, on_back: OnBackToMenu) -> None:
        super().__init__(timeout=CONFIG_VIEW_TIMEOUT_SECONDS)
        self.add_item(ConfigEnableRejectionSettingsButton(on_enable))
        self.add_item(ConfigDisableRejectionSettingsButton(on_disable))
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
                label="Save Nominee Selection",
                custom_id="wpm_config_database_candidate_selection_save",
            )
        )
        self.add_item(BackToMenuButton(on_back))
        self._on_save = on_save

    async def _handle_save(self, interaction: discord.Interaction) -> None:
        await self._on_save(interaction, self.candidate_selection_select.selected)


class ConfigVotingDefaultsIntroView(discord.ui.View):
    """/config's Voting Defaults entry screen: mirrors the Setup Wizard's
    own Voting Defaults step (VotingDefaultsIntroView) -- choose Nominee
    Selection (when a collection is in scope) and Visibility from two
    dropdowns, then press Set Voting Defaults to open the modal for the
    remaining, flexible fields (candidate count, duration). Discord
    modals accept TextInput components only, so both fixed-choice values
    are collected here instead of inside the modal (see
    setup_wizard_view.VotingDefaultsModal's own docstring for the full
    explanation).

    Nominee Selection is stored per collection (see bot.py's
    send_config_voting_defaults_screen, which resolves -- or asks the
    administrator to choose -- which collection this screen's Nominee
    Selection dropdown edits before this view is ever built). Passing
    default_candidate_selection=None omits that dropdown entirely --
    used only when the guild has no collections at all to edit Nominee
    Selection for.
    """

    def __init__(
        self,
        on_configure: OnConfigVotingDefaultsIntroConfigure,
        on_back: OnBackToMenu,
        *,
        default_visibility: GuildVoteVisibility,
        default_candidate_selection: Optional[CandidateSelectionMode] = None,
    ) -> None:
        super().__init__(timeout=CONFIG_VIEW_TIMEOUT_SECONDS)
        self.candidate_selection_select: Optional[CandidateSelectionSelectComponent] = None
        if default_candidate_selection is not None:
            self.candidate_selection_select = CandidateSelectionSelectComponent(default=default_candidate_selection)
            self.add_item(self.candidate_selection_select)
        self.visibility_select = VisibilitySelectComponent(default=default_visibility)
        self.add_item(self.visibility_select)
        self.add_item(
            ConfigureStepButton(
                self._handle_configure, label="Set Voting Defaults", custom_id="wpm_config_voting_defaults_configure"
            )
        )
        self.add_item(BackToMenuButton(on_back))
        self._on_configure = on_configure

    async def _handle_configure(self, interaction: discord.Interaction) -> None:
        candidate_selection = (
            self.candidate_selection_select.selected if self.candidate_selection_select is not None else None
        )
        await self._on_configure(interaction, self.visibility_select.selected, candidate_selection)


# --- Suggestion Post Destination ------------------------------------------------------------


_SUGGESTION_DESTINATION_CHANNEL_TYPES = [discord.ChannelType.public_thread, discord.ChannelType.private_thread]


class ConfigSuggestionDestinationSectionView(discord.ui.View):
    """Reuses setup_wizard_view.py's generic DestinationChannelSelect,
    restricted to threads only (Collections Should Live In Threads):
    collections no longer live directly in text channels, so this picker
    only ever offers a thread -- which also makes WASH's Watch Party Home
    Channel (always a plain text channel) structurally unselectable here,
    on top of the explicit rejection in
    ConfigService.set_database_suggestion_destination.

    Deliberately has no Clear/Skip button, unlike ConfigWatchDestinationSectionView:
    every collection MUST have exactly one dedicated suggestion
    destination, so this section can only change it to a different
    thread, never unset it.
    """

    def __init__(self, on_select: OnConfigChannelSelected, on_back: OnBackToMenu) -> None:
        super().__init__(timeout=CONFIG_VIEW_TIMEOUT_SECONDS)
        self.add_item(
            DestinationChannelSelect(
                on_select,
                custom_id="wpm_config_suggestion_destination_channel_select",
                placeholder="Choose an existing thread",
                channel_types=_SUGGESTION_DESTINATION_CHANNEL_TYPES,
            )
        )
        self.add_item(BackToMenuButton(on_back))


# --- Watched Item Archive -------------------------------------------------------------


class ConfigSkipDestinationButton(discord.ui.Button):
    def __init__(self, on_click: OnConfigSkip) -> None:
        super().__init__(label="Clear Archive", style=discord.ButtonStyle.secondary, custom_id="wpm_config_destination_clear")
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class ConfigWatchDestinationSectionView(discord.ui.View):
    """Reuses setup_wizard_view.py's generic ChannelDestinationSelect --
    /config's equivalent of the Setup Wizard's Watch Destination step, so
    it gets the same fresh-every-render, thread-inclusive option list
    (see bot.py's build_channel_destination_options).
    """

    def __init__(
        self,
        options: List[discord.SelectOption],
        on_select: OnConfigChannelSelected,
        on_skip: OnConfigSkip,
        on_back: OnBackToMenu,
    ) -> None:
        super().__init__(timeout=CONFIG_VIEW_TIMEOUT_SECONDS)
        self.add_item(
            ChannelDestinationSelect(
                options,
                on_select,
                custom_id="wpm_config_watch_destination_channel_select",
                placeholder="Choose an existing channel or thread",
            )
        )
        self.add_item(ConfigSkipDestinationButton(on_skip))
        self.add_item(BackToMenuButton(on_back))



# --- Home Channel ----------------------------------------------------------------------------
#
# The channel every collection's suggestion thread (and, by default, the
# Watched Item Archive thread) is created as a sibling under (see
# GuildChannelsConfig.home_channel_id). Reuses setup_wizard_view.py's
# CreateNewChannelButton/UseExistingChannelButton -- the same "create
# vs. use existing" two-step choice /setup's own Home Channel step used
# before Channel Selection Consistency unified it onto one screen
# (HomeChannelStepView); /config keeps this section's own separate
# two-step shape since a single-setting edit has no Back/Save & Finish
# Later flow to stay consistent with. Unlike /setup's HomeChannelStepView
# (which has no Clear -- setup always requires one), /config additionally
# offers Use Current Channel and Clear Home Channel, since this edits an
# already-configured server rather than bootstrapping one.


class ConfigUseCurrentChannelButton(discord.ui.Button):
    """disabled (not omitted) when the current channel isn't a usable
    top-level text channel -- the Home Channel can never be a thread
    (every collection's own thread is created as a sibling under it, and
    Discord doesn't allow nesting a thread under another thread).
    """

    def __init__(self, on_click: OnConfigSkip, *, disabled: bool = False) -> None:
        super().__init__(
            label="Use Current Channel",
            style=discord.ButtonStyle.secondary,
            custom_id="wpm_config_home_channel_use_current",
            disabled=disabled,
        )
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class ConfigClearHomeChannelButton(discord.ui.Button):
    def __init__(self, on_click: OnConfigSkip) -> None:
        super().__init__(
            label="Clear Home Channel", style=discord.ButtonStyle.secondary, custom_id="wpm_config_home_channel_clear"
        )
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class ConfigHomeChannelSectionView(discord.ui.View):
    """/config's Home Channel section: Create New Channel (Recommended),
    Use Current Channel, Use Existing Channel, Clear Home Channel, or
    Back to Menu.
    """

    def __init__(
        self,
        on_create_new: OnConfigSkip,
        on_use_current: OnConfigSkip,
        on_use_existing: OnConfigSkip,
        on_clear: OnConfigSkip,
        on_back: OnBackToMenu,
        *,
        current_channel_available: bool = True,
    ) -> None:
        super().__init__(timeout=CONFIG_VIEW_TIMEOUT_SECONDS)
        self.add_item(CreateNewChannelButton(on_create_new))
        self.add_item(ConfigUseCurrentChannelButton(on_use_current, disabled=not current_channel_available))
        self.add_item(UseExistingChannelButton(on_use_existing))
        self.add_item(ConfigClearHomeChannelButton(on_clear))
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


# --- Backup Defaults enable/disable choice -------------------------------------------------


class ConfigEnableAutomaticBackupsButton(discord.ui.Button):
    def __init__(self, on_click: OnConfigRetry) -> None:
        super().__init__(
            label="Enable Automatic Backups (Recommended)",
            style=discord.ButtonStyle.primary,
            custom_id="wpm_config_backup_enable",
        )
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class ConfigDisableAutomaticBackupsButton(discord.ui.Button):
    def __init__(self, on_click: OnConfigRetry) -> None:
        super().__init__(
            label="Disable Automatic Backups",
            style=discord.ButtonStyle.secondary,
            custom_id="wpm_config_backup_disable",
        )
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class ConfigBackupDefaultsChoiceView(discord.ui.View):
    """/config's Backup Defaults entry screen: choose whether automatic
    backups are enabled at all before -- only if enabled -- configuring
    their interval and retention, mirroring the Setup Wizard's own
    BackupDefaultsChoiceView (Release Polish: Optional Automatic Backups).
    Enable is the recommended, default action.
    """

    def __init__(
        self, on_enable: OnConfigRetry, on_disable: OnConfigRetry, on_back: OnBackToMenu
    ) -> None:
        super().__init__(timeout=CONFIG_VIEW_TIMEOUT_SECONDS)
        self.add_item(ConfigEnableAutomaticBackupsButton(on_enable))
        self.add_item(ConfigDisableAutomaticBackupsButton(on_disable))
        self.add_item(BackToMenuButton(on_back))


# --- Reminder Defaults enable/disable choice -----------------------------------------------


class ConfigEnableVoteEndingReminderButton(discord.ui.Button):
    def __init__(self, on_click: OnConfigRetry) -> None:
        super().__init__(
            label="Enable Vote-Ending Reminder (Recommended)",
            style=discord.ButtonStyle.primary,
            custom_id="wpm_config_reminder_enable",
        )
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class ConfigDisableVoteEndingReminderButton(discord.ui.Button):
    def __init__(self, on_click: OnConfigRetry) -> None:
        super().__init__(
            label="Disable Vote-Ending Reminder",
            style=discord.ButtonStyle.secondary,
            custom_id="wpm_config_reminder_disable",
        )
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class ConfigReminderDefaultsChoiceView(discord.ui.View):
    """/config's Reminder Defaults entry screen: choose whether the
    vote-ending reminder is enabled at all before -- only if enabled --
    configuring its lead time, mirroring ConfigBackupDefaultsChoiceView's
    identical enable/disable-then-configure shape (Fixed-Option UX
    Audit: "enabled?" is a small fixed set of choices, not a free-text
    yes/no field). Enable is the recommended, default action.
    """

    def __init__(
        self, on_enable: OnConfigRetry, on_disable: OnConfigRetry, on_back: OnBackToMenu
    ) -> None:
        super().__init__(timeout=CONFIG_VIEW_TIMEOUT_SECONDS)
        self.add_item(ConfigEnableVoteEndingReminderButton(on_enable))
        self.add_item(ConfigDisableVoteEndingReminderButton(on_disable))
        self.add_item(BackToMenuButton(on_back))


# --- Eligible Pool Warning ------------------------------------


class EligiblePoolWarningThresholdModal(discord.ui.Modal):
    """A single field: the eligible-pool threshold that triggers the
    warning, or blank to restore the automatic default (the guild's
    configured candidate count times the warning multiplier)."""

    def __init__(self, on_submit: OnConfigRetryModalSubmit, *, default: str = "") -> None:
        super().__init__(title="Eligible Pool Warning Threshold")
        self._submit_callback = on_submit
        self.threshold_input = discord.ui.TextInput(
            label="Threshold (blank = automatic)", default=default or None, required=False
        )
        self.add_item(self.threshold_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._submit_callback(interaction, self.threshold_input.value)


class ConfigEnableEligiblePoolWarningButton(discord.ui.Button):
    def __init__(self, on_click: OnConfigRetry) -> None:
        super().__init__(label="Enable", style=discord.ButtonStyle.primary, custom_id="wpm_config_low_pool_enable")
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class ConfigDisableEligiblePoolWarningButton(discord.ui.Button):
    def __init__(self, on_click: OnConfigRetry) -> None:
        super().__init__(label="Disable", style=discord.ButtonStyle.secondary, custom_id="wpm_config_low_pool_disable")
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class ConfigSetEligiblePoolWarningThresholdButton(discord.ui.Button):
    def __init__(self, on_click: OnConfigRetry) -> None:
        super().__init__(
            label="Set Threshold...", style=discord.ButtonStyle.secondary, custom_id="wpm_config_low_pool_threshold"
        )
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class ConfigEligiblePoolWarningUseAdminChannelButton(discord.ui.Button):
    def __init__(self, on_click: OnConfigRetry) -> None:
        super().__init__(
            label="Use Admin Channel (Recommended)",
            style=discord.ButtonStyle.secondary,
            custom_id="wpm_config_low_pool_admin_channel",
        )
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class ConfigEligiblePoolWarningUseHomeChannelButton(discord.ui.Button):
    def __init__(self, on_click: OnConfigRetry) -> None:
        super().__init__(
            label="Use Watch Party Home Channel",
            style=discord.ButtonStyle.secondary,
            custom_id="wpm_config_low_pool_home_channel",
        )
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class ConfigEligiblePoolWarningView(discord.ui.View):
    """/config's Eligible Pool Warning section: Enable/Disable, Set
    Threshold (a modal; blank restores the automatic default), and the
    warning's destination (Admin Channel, the default, or the Watch
    Party Home Channel -- never a collection's suggestion thread).
    """

    def __init__(
        self,
        on_enable: OnConfigRetry,
        on_disable: OnConfigRetry,
        on_set_threshold: OnConfigRetry,
        on_use_admin_channel: OnConfigRetry,
        on_use_home_channel: OnConfigRetry,
        on_back: OnBackToMenu,
    ) -> None:
        super().__init__(timeout=CONFIG_VIEW_TIMEOUT_SECONDS)
        self.add_item(ConfigEnableEligiblePoolWarningButton(on_enable))
        self.add_item(ConfigDisableEligiblePoolWarningButton(on_disable))
        self.add_item(ConfigSetEligiblePoolWarningThresholdButton(on_set_threshold))
        self.add_item(ConfigEligiblePoolWarningUseAdminChannelButton(on_use_admin_channel))
        self.add_item(ConfigEligiblePoolWarningUseHomeChannelButton(on_use_home_channel))
        self.add_item(BackToMenuButton(on_back))
