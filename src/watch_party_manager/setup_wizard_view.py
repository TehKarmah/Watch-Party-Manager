"""Discord UI components for FR-028's /setup wizard.

Like start_vote_view.py and edit_vote_view.py, this module has no
dependency on bot.py: every view/modal here only knows how to render
itself and forward a selection/click/submission to a caller-supplied
callback. All validation, draft updates, and persistence live in
services/setup_wizard_service.py and bot.py's wiring around it, reused
unchanged regardless of which step's UI triggered them.

Each step is a short-lived, ephemeral prompt (timeout, not None) -- the
wizard is a one-time setup flow the invoking WASH Crew member walks
through in one sitting, not a long-lived persistent control surface like
VotingView, so it doesn't need restart persistence.
"""

from __future__ import annotations

from typing import Awaitable, Callable, List, Optional, Tuple

import discord

from watch_party_manager.domain.guild_configuration import JoinMode

SETUP_WIZARD_STEP_TIMEOUT_SECONDS = 900

OnRoleSelected = Callable[[discord.Interaction, int], Awaitable[None]]
OnWatchPartyRoleConfirmed = Callable[[discord.Interaction, Optional[int], JoinMode], Awaitable[None]]
OnWizardCancel = Callable[[discord.Interaction], Awaitable[None]]
OnDatabaseChoiceButton = Callable[[discord.Interaction], Awaitable[None]]
OnExistingDatabaseSelected = Callable[[discord.Interaction, int], Awaitable[None]]
OnDatabaseNameSubmit = Callable[[discord.Interaction, str], Awaitable[None]]
OnChannelSelected = Callable[[discord.Interaction, int], Awaitable[None]]
OnSkip = Callable[[discord.Interaction], Awaitable[None]]
OnVotingDefaultsSubmit = Callable[[discord.Interaction, str, str, str, str], Awaitable[None]]
OnReminderDefaultsSubmit = Callable[[discord.Interaction, str, str], Awaitable[None]]
OnBackupDefaultsSubmit = Callable[[discord.Interaction, str, str], Awaitable[None]]
OnSave = Callable[[discord.Interaction], Awaitable[None]]
OnEditSection = Callable[[discord.Interaction, str], Awaitable[None]]
OnResumeChoice = Callable[[discord.Interaction], Awaitable[None]]
OnConfigureClicked = Callable[[discord.Interaction], Awaitable[None]]

_DESTINATION_CHANNEL_TYPES = [
    discord.ChannelType.text,
    discord.ChannelType.public_thread,
    discord.ChannelType.private_thread,
]


class SetupCancelButton(discord.ui.Button):
    """Cancels the entire wizard, discarding its draft. Present on every step."""

    def __init__(self, on_cancel: OnWizardCancel) -> None:
        super().__init__(label="Cancel Setup", style=discord.ButtonStyle.danger, custom_id="wpm_setup_cancel")
        self._on_cancel = on_cancel

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_cancel(interaction)


# --- Resume prompt --------------------------------------------------------------------


class ContinueSetupButton(discord.ui.Button):
    def __init__(self, on_click: OnResumeChoice) -> None:
        super().__init__(label="Continue Setup", style=discord.ButtonStyle.primary, custom_id="wpm_setup_resume_continue")
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class ReviewProgressButton(discord.ui.Button):
    def __init__(self, on_click: OnResumeChoice) -> None:
        super().__init__(label="Review Progress", style=discord.ButtonStyle.secondary, custom_id="wpm_setup_resume_review")
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class RestartSetupButton(discord.ui.Button):
    def __init__(self, on_click: OnResumeChoice) -> None:
        super().__init__(label="Restart Setup", style=discord.ButtonStyle.danger, custom_id="wpm_setup_resume_restart")
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class SetupWizardResumeView(discord.ui.View):
    """Shown when /setup is run again while a previous attempt is still in progress."""

    def __init__(self, on_continue: OnResumeChoice, on_review: OnResumeChoice, on_restart: OnResumeChoice) -> None:
        super().__init__(timeout=SETUP_WIZARD_STEP_TIMEOUT_SECONDS)
        self.add_item(ContinueSetupButton(on_continue))
        self.add_item(ReviewProgressButton(on_review))
        self.add_item(RestartSetupButton(on_restart))


# --- WASH Crew Role ---------------------------------------------------------------------


class WashCrewRoleSelect(discord.ui.RoleSelect):
    def __init__(self, on_select: OnRoleSelected) -> None:
        super().__init__(
            placeholder="Select the WASH Crew role",
            min_values=1,
            max_values=1,
            custom_id="wpm_setup_wash_crew_role_select",
        )
        self._on_select = on_select

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_select(interaction, self.values[0].id)


class WashCrewRoleStepView(discord.ui.View):
    """Step 1: choose the role that controls administrative access to WASH."""

    def __init__(self, on_select: OnRoleSelected, on_cancel: OnWizardCancel) -> None:
        super().__init__(timeout=SETUP_WIZARD_STEP_TIMEOUT_SECONDS)
        self.add_item(WashCrewRoleSelect(on_select))
        self.add_item(SetupCancelButton(on_cancel))


# --- Watch Party Role + join mode ---------------------------------------------------------


_JOIN_MODE_OPTIONS = [
    discord.SelectOption(label="Manual", value=JoinMode.MANUAL.value, description="WASH Crew assigns the role manually."),
    discord.SelectOption(
        label="Self-Service", value=JoinMode.SELF_SERVICE.value, description="Members can join themselves."
    ),
    discord.SelectOption(label="Approval", value=JoinMode.APPROVAL.value, description="Joining requires approval."),
    discord.SelectOption(
        label="Discord-Managed", value=JoinMode.DISCORD_MANAGED.value, description="Discord's own role management applies."
    ),
]


class WatchPartyRoleSelectComponent(discord.ui.RoleSelect):
    """Records a role choice without advancing the step -- see WatchPartyRoleConfirmButton."""

    def __init__(self) -> None:
        super().__init__(
            placeholder="Select the Watch Party role (optional)",
            min_values=0,
            max_values=1,
            custom_id="wpm_setup_watch_party_role_select",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()


class JoinModeSelectComponent(discord.ui.Select):
    """Records a join-mode choice without advancing the step -- see WatchPartyRoleConfirmButton."""

    def __init__(self) -> None:
        super().__init__(
            placeholder="Select the join mode (defaults to Self-Service)",
            custom_id="wpm_setup_watch_party_join_mode_select",
            options=_JOIN_MODE_OPTIONS,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()


class WatchPartyRoleConfirmButton(discord.ui.Button):
    def __init__(self, on_click: Callable[[discord.Interaction], Awaitable[None]]) -> None:
        super().__init__(label="Continue", style=discord.ButtonStyle.primary, custom_id="wpm_setup_watch_party_role_confirm")
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class WatchPartyRoleStepView(discord.ui.View):
    """Step 2: choose the Watch Party role and its join mode together.

    The role is optional (a guild may not want a distinct Watch Party
    role at all); join mode always has a value, defaulting to
    Self-Service (WatchPartyRoleConfig's own documented default) if never
    touched.
    """

    def __init__(self, on_confirm: OnWatchPartyRoleConfirmed, on_cancel: OnWizardCancel) -> None:
        super().__init__(timeout=SETUP_WIZARD_STEP_TIMEOUT_SECONDS)
        self._on_confirm = on_confirm
        self.role_select = WatchPartyRoleSelectComponent()
        self.join_mode_select = JoinModeSelectComponent()
        self.add_item(self.role_select)
        self.add_item(self.join_mode_select)
        self.add_item(WatchPartyRoleConfirmButton(self._handle_confirm))
        self.add_item(SetupCancelButton(on_cancel))

    async def _handle_confirm(self, interaction: discord.Interaction) -> None:
        role_id = self.role_select.values[0].id if self.role_select.values else None
        join_mode = JoinMode(self.join_mode_select.values[0]) if self.join_mode_select.values else JoinMode.SELF_SERVICE
        await self._on_confirm(interaction, role_id, join_mode)


# --- Suggestion Database -----------------------------------------------------------------


class SelectExistingDatabaseButton(discord.ui.Button):
    def __init__(self, on_click: OnDatabaseChoiceButton) -> None:
        super().__init__(label="Select Existing", style=discord.ButtonStyle.primary, custom_id="wpm_setup_database_select_existing")
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class CreateNewDatabaseButton(discord.ui.Button):
    def __init__(self, on_click: OnDatabaseChoiceButton) -> None:
        super().__init__(label="Create New", style=discord.ButtonStyle.secondary, custom_id="wpm_setup_database_create_new")
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class SuggestionDatabaseChoiceView(discord.ui.View):
    """Step 3, part 1: select an existing suggestion database, or create one."""

    def __init__(
        self, on_select_existing: OnDatabaseChoiceButton, on_create_new: OnDatabaseChoiceButton, on_cancel: OnWizardCancel
    ) -> None:
        super().__init__(timeout=SETUP_WIZARD_STEP_TIMEOUT_SECONDS)
        self.add_item(SelectExistingDatabaseButton(on_select_existing))
        self.add_item(CreateNewDatabaseButton(on_create_new))
        self.add_item(SetupCancelButton(on_cancel))


class ExistingDatabaseSelect(discord.ui.Select):
    def __init__(self, databases: List[Tuple[int, str]], on_select: OnExistingDatabaseSelected) -> None:
        options = [
            discord.SelectOption(label=name[:100], value=str(database_id))
            for database_id, name in databases[:25]
        ]
        super().__init__(placeholder="Choose a suggestion database", options=options, custom_id="wpm_setup_database_select")
        self._on_select = on_select

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_select(interaction, int(self.values[0]))


class ExistingDatabaseSelectView(discord.ui.View):
    """Step 3, part 2a: pick which existing suggestion database to use."""

    def __init__(
        self, databases: List[Tuple[int, str]], on_select: OnExistingDatabaseSelected, on_cancel: OnWizardCancel
    ) -> None:
        super().__init__(timeout=SETUP_WIZARD_STEP_TIMEOUT_SECONDS)
        self.add_item(ExistingDatabaseSelect(databases, on_select))
        self.add_item(SetupCancelButton(on_cancel))


class CreateDatabaseNameModal(discord.ui.Modal):
    """Step 3, part 2b (1 of 2): collect the new database's name."""

    def __init__(self, on_submit: OnDatabaseNameSubmit) -> None:
        super().__init__(title="New Suggestion Database")
        self._submit_callback = on_submit
        self.name_input = discord.ui.TextInput(label="Database name", required=True)
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._submit_callback(interaction, self.name_input.value)


class DestinationChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, on_select: OnChannelSelected, *, custom_id: str, placeholder: str) -> None:
        super().__init__(
            placeholder=placeholder,
            channel_types=_DESTINATION_CHANNEL_TYPES,
            min_values=1,
            max_values=1,
            custom_id=custom_id,
        )
        self._on_select = on_select

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_select(interaction, self.values[0].id)


class CreateDatabaseChannelSelectView(discord.ui.View):
    """Step 3, part 2b (2 of 2): pick the new database's channel or thread."""

    def __init__(self, on_select: OnChannelSelected, on_cancel: OnWizardCancel) -> None:
        super().__init__(timeout=SETUP_WIZARD_STEP_TIMEOUT_SECONDS)
        self.add_item(
            DestinationChannelSelect(
                on_select,
                custom_id="wpm_setup_database_channel_select",
                placeholder="Select the channel or thread for this database",
            )
        )
        self.add_item(SetupCancelButton(on_cancel))


# --- Admin Channel ---------------------------------------------------------------------------


class SkipAdminChannelButton(discord.ui.Button):
    def __init__(self, on_click: OnSkip) -> None:
        super().__init__(label="Skip for Now", style=discord.ButtonStyle.secondary, custom_id="wpm_setup_admin_channel_skip")
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class AdminChannelStepView(discord.ui.View):
    """Admin Channel step: choose where Approval-Required membership
    requests are posted for WASH Crew, or skip for now.

    Reuses DestinationChannelSelect (generic, already used for the Watch
    Destination step) rather than a duplicate channel-select component.
    """

    def __init__(self, on_select: OnChannelSelected, on_skip: OnSkip, on_cancel: OnWizardCancel) -> None:
        super().__init__(timeout=SETUP_WIZARD_STEP_TIMEOUT_SECONDS)
        self.add_item(
            DestinationChannelSelect(
                on_select,
                custom_id="wpm_setup_admin_channel_select",
                placeholder="Select an existing channel or thread",
            )
        )
        self.add_item(SkipAdminChannelButton(on_skip))
        self.add_item(SetupCancelButton(on_cancel))


# --- Watched Movie Destination -------------------------------------------------------------


class SkipWatchDestinationButton(discord.ui.Button):
    def __init__(self, on_click: OnSkip) -> None:
        super().__init__(label="Skip for Now", style=discord.ButtonStyle.secondary, custom_id="wpm_setup_destination_skip")
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class WatchDestinationStepView(discord.ui.View):
    """Step 4: choose where watched-movie history posts, or skip for now."""

    def __init__(self, on_select: OnChannelSelected, on_skip: OnSkip, on_cancel: OnWizardCancel) -> None:
        super().__init__(timeout=SETUP_WIZARD_STEP_TIMEOUT_SECONDS)
        self.add_item(
            DestinationChannelSelect(
                on_select,
                custom_id="wpm_setup_watch_destination_channel_select",
                placeholder="Select an existing channel or thread",
            )
        )
        self.add_item(SkipWatchDestinationButton(on_skip))
        self.add_item(SetupCancelButton(on_cancel))


# --- Modal-based steps: Voting/Reminder/Backup Defaults ------------------------------------


class ConfigureStepButton(discord.ui.Button):
    def __init__(self, on_click: OnConfigureClicked, *, label: str, custom_id: str) -> None:
        super().__init__(label=label, style=discord.ButtonStyle.primary, custom_id=custom_id)
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class ModalStepIntroView(discord.ui.View):
    """A short intro screen whose single button opens the step's modal.

    Reused for Voting Defaults, Reminder Defaults, and Backup Defaults --
    a modal cannot be sent directly in response to /setup's own
    interaction chain without an intervening component click, so each of
    these three steps shows this one-button prompt first.
    """

    def __init__(self, on_configure: OnConfigureClicked, on_cancel: OnWizardCancel, *, button_label: str, custom_id: str) -> None:
        super().__init__(timeout=SETUP_WIZARD_STEP_TIMEOUT_SECONDS)
        self.add_item(ConfigureStepButton(on_configure, label=button_label, custom_id=custom_id))
        self.add_item(SetupCancelButton(on_cancel))


class VotingDefaultsModal(discord.ui.Modal):
    """Step 5: default nominee count, duration, visibility, and candidate selection."""

    def __init__(self, on_submit: OnVotingDefaultsSubmit, *, defaults: Optional[Tuple[str, str, str, str]] = None) -> None:
        super().__init__(title="Voting Defaults")
        self._submit_callback = on_submit
        candidate_count_default, duration_days_default, visibility_default, candidate_selection_default = (
            defaults or ("3", "7", "blind", "balanced_random")
        )
        self.candidate_count_input = discord.ui.TextInput(
            label="Default nominee count (2-10)", default=candidate_count_default
        )
        self.duration_days_input = discord.ui.TextInput(
            label="Default vote duration in days (1-30)", default=duration_days_default
        )
        self.visibility_input = discord.ui.TextInput(
            label="Default visibility: blind or visible", default=visibility_default
        )
        self.candidate_selection_input = discord.ui.TextInput(
            label="Candidate selection: random or balanced_random", default=candidate_selection_default
        )
        self.add_item(self.candidate_count_input)
        self.add_item(self.duration_days_input)
        self.add_item(self.visibility_input)
        self.add_item(self.candidate_selection_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._submit_callback(
            interaction,
            self.candidate_count_input.value,
            self.duration_days_input.value,
            self.visibility_input.value,
            self.candidate_selection_input.value,
        )


class ReminderDefaultsModal(discord.ui.Modal):
    """Step 6: whether a vote-ending reminder is sent, and how many hours before close."""

    def __init__(self, on_submit: OnReminderDefaultsSubmit, *, defaults: Optional[Tuple[str, str]] = None) -> None:
        super().__init__(title="Reminder Defaults")
        self._submit_callback = on_submit
        enabled_default, hours_default = defaults or ("yes", "24")
        self.enabled_input = discord.ui.TextInput(label="Reminder enabled? (yes/no)", default=enabled_default)
        self.hours_input = discord.ui.TextInput(label="Reminder hours before close (1-720)", default=hours_default)
        self.add_item(self.enabled_input)
        self.add_item(self.hours_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._submit_callback(interaction, self.enabled_input.value, self.hours_input.value)


class BackupDefaultsModal(discord.ui.Modal):
    """Step 7: automatic backup interval and retention count."""

    def __init__(self, on_submit: OnBackupDefaultsSubmit, *, defaults: Optional[Tuple[str, str]] = None) -> None:
        super().__init__(title="Backup Defaults")
        self._submit_callback = on_submit
        interval_default, retention_default = defaults or ("1", "30")
        self.interval_input = discord.ui.TextInput(label="Automatic backup interval, in days", default=interval_default)
        self.retention_input = discord.ui.TextInput(label="Backup retention count", default=retention_default)
        self.add_item(self.interval_input)
        self.add_item(self.retention_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._submit_callback(interaction, self.interval_input.value, self.retention_input.value)


# --- Review ----------------------------------------------------------------------------------


class SaveSetupButton(discord.ui.Button):
    def __init__(self, on_click: OnSave) -> None:
        super().__init__(label="Save", style=discord.ButtonStyle.success, custom_id="wpm_setup_review_save")
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class EditSectionSelect(discord.ui.Select):
    def __init__(self, section_options: List[Tuple[str, str]], on_select: OnEditSection) -> None:
        options = [discord.SelectOption(label=label, value=value) for value, label in section_options]
        super().__init__(placeholder="Go back and edit a section...", options=options, custom_id="wpm_setup_review_edit_section")
        self._on_select = on_select

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_select(interaction, self.values[0])


class ReviewStepView(discord.ui.View):
    """Step 8: summarize every section, then Save, edit one, or cancel."""

    def __init__(
        self,
        section_options: List[Tuple[str, str]],
        on_save: OnSave,
        on_edit_section: OnEditSection,
        on_cancel: OnWizardCancel,
    ) -> None:
        super().__init__(timeout=SETUP_WIZARD_STEP_TIMEOUT_SECONDS)
        self.add_item(SaveSetupButton(on_save))
        self.add_item(EditSectionSelect(section_options, on_edit_section))
        self.add_item(SetupCancelButton(on_cancel))
