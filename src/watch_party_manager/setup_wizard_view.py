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
from watch_party_manager.domain.suggestion_database_configuration import (
    CANDIDATE_SELECTION_DISPLAY_LABELS,
    CandidateSelectionMode,
)

SETUP_WIZARD_STEP_TIMEOUT_SECONDS = 900

OnRoleSelected = Callable[[discord.Interaction, int], Awaitable[None]]
OnWatchPartyRoleConfirmed = Callable[[discord.Interaction, Optional[int], JoinMode], Awaitable[None]]
OnWizardCancel = Callable[[discord.Interaction], Awaitable[None]]
OnDatabaseChoiceButton = Callable[[discord.Interaction], Awaitable[None]]
OnExistingDatabaseSelected = Callable[[discord.Interaction, int], Awaitable[None]]
OnDatabaseNameSubmit = Callable[[discord.Interaction, str], Awaitable[None]]
OnThreadNameSubmit = Callable[[discord.Interaction, str], Awaitable[None]]
OnChannelSelected = Callable[[discord.Interaction, int], Awaitable[None]]
OnSkip = Callable[[discord.Interaction], Awaitable[None]]
OnCreateThreadClicked = Callable[[discord.Interaction], Awaitable[None]]
OnVotingDefaultsSubmit = Callable[[discord.Interaction, str, str, str], Awaitable[None]]
OnVotingDefaultsConfigure = Callable[[discord.Interaction, CandidateSelectionMode], Awaitable[None]]
OnReminderDefaultsSubmit = Callable[[discord.Interaction, str, str], Awaitable[None]]
OnBackupDefaultsSubmit = Callable[[discord.Interaction, str, str], Awaitable[None]]
OnSave = Callable[[discord.Interaction], Awaitable[None]]
OnEditSection = Callable[[discord.Interaction, str], Awaitable[None]]
OnResumeChoice = Callable[[discord.Interaction], Awaitable[None]]
OnConfigureClicked = Callable[[discord.Interaction], Awaitable[None]]
OnBack = Callable[[discord.Interaction], Awaitable[None]]
OnSaveForLater = Callable[[discord.Interaction], Awaitable[None]]
OnBeginSetup = Callable[[discord.Interaction], Awaitable[None]]

_DESTINATION_CHANNEL_TYPES = [
    discord.ChannelType.text,
    discord.ChannelType.public_thread,
    discord.ChannelType.private_thread,
]

# Reuses CANDIDATE_SELECTION_DISPLAY_LABELS (the same mapping /about,
# /config, and every other candidate-selection display already draws
# from) so the dropdown's wording can never drift from that single
# source of truth. "(Recommended)" is presentation-only, appended here
# rather than baked into the shared label.
_CANDIDATE_SELECTION_SELECT_LABELS: dict[CandidateSelectionMode, str] = {
    CandidateSelectionMode.ROTATION_POOL: f"{CANDIDATE_SELECTION_DISPLAY_LABELS[CandidateSelectionMode.ROTATION_POOL]} (Recommended)",
    CandidateSelectionMode.SOFT_ROTATION: CANDIDATE_SELECTION_DISPLAY_LABELS[CandidateSelectionMode.SOFT_ROTATION],
    CandidateSelectionMode.INFINITE_POOL: CANDIDATE_SELECTION_DISPLAY_LABELS[CandidateSelectionMode.INFINITE_POOL],
}


class SetupWizardStepView(discord.ui.View):
    """Base class for every /setup step's view.

    Adds one thing over discord.ui.View: an optional requester_id
    interaction_check, matching the project's existing convention (see
    pagination_view.PaginatedListView) for defense-in-depth scoping a
    control surface to whoever is actually driving it -- every /setup
    message is already ephemeral (only the invoking member can even see
    it in Discord's own client), so this is a second layer, not the only
    one. requester_id is captured once per render from whichever
    interaction produced that render (see bot.py's send_setup_wizard_step)
    and threaded through unchanged on every subsequent step, so it always
    reflects the person actually walking through the wizard right now --
    not persisted, and not the same across every guild's entire
    days-long resumable journey (any WASH Crew member may legitimately
    resume a different member's in-progress draft).
    """

    def __init__(self, *, requester_id: Optional[int] = None) -> None:
        super().__init__(timeout=SETUP_WIZARD_STEP_TIMEOUT_SECONDS)
        self._requester_id = requester_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self._requester_id is not None and interaction.user.id != self._requester_id:
            await interaction.response.send_message(
                "Only the person who ran this command can use these controls.", ephemeral=True
            )
            return False
        return True


class SetupCancelButton(discord.ui.Button):
    """Cancels the entire wizard, discarding its draft. Present on every step."""

    def __init__(self, on_cancel: OnWizardCancel) -> None:
        super().__init__(label="Cancel Setup", style=discord.ButtonStyle.danger, custom_id="wpm_setup_cancel")
        self._on_cancel = on_cancel

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_cancel(interaction)


class SetupBackButton(discord.ui.Button):
    """Returns to the immediately previous step. Omitted on the first step."""

    def __init__(self, on_back: OnBack) -> None:
        super().__init__(label="Back", style=discord.ButtonStyle.secondary, custom_id="wpm_setup_back")
        self._on_back = on_back

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_back(interaction)


class SetupSaveForLaterButton(discord.ui.Button):
    """Exits the wizard without discarding its draft -- unlike Cancel Setup,
    which deletes it. Present on every step.
    """

    def __init__(self, on_save_for_later: OnSaveForLater) -> None:
        super().__init__(
            label="Save & Finish Later", style=discord.ButtonStyle.secondary, custom_id="wpm_setup_save_for_later"
        )
        self._on_save_for_later = on_save_for_later

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_save_for_later(interaction)


# --- Preparation screen -----------------------------------------------------------------


class BeginSetupButton(discord.ui.Button):
    def __init__(self, on_click: OnBeginSetup) -> None:
        super().__init__(label="Begin Setup", style=discord.ButtonStyle.primary, custom_id="wpm_setup_preparation_begin")
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class SetupPreparationView(SetupWizardStepView):
    """Shown once, before Step 1, for a brand-new (never-resumed) /setup
    run -- helps administrators prepare their Discord server before
    configuration begins. Not itself a SetupWizardStep: it collects
    nothing and is never resumed into, so it adds no wizard state.
    """

    def __init__(
        self,
        on_begin: OnBeginSetup,
        on_cancel: OnWizardCancel,
        *,
        requester_id: Optional[int] = None,
    ) -> None:
        super().__init__(requester_id=requester_id)
        self.add_item(BeginSetupButton(on_begin))
        self.add_item(SetupCancelButton(on_cancel))


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


class SetupWizardResumeView(SetupWizardStepView):
    """Shown when /setup is run again while a previous attempt is still in progress.

    Continue Setup and Review Progress are listed first (and are the
    non-destructive, recommended way back in); Restart Setup remains
    available but is never the default action.
    """

    def __init__(
        self,
        on_continue: OnResumeChoice,
        on_review: OnResumeChoice,
        on_restart: OnResumeChoice,
        *,
        requester_id: Optional[int] = None,
    ) -> None:
        super().__init__(requester_id=requester_id)
        self.add_item(ContinueSetupButton(on_continue))
        self.add_item(ReviewProgressButton(on_review))
        self.add_item(RestartSetupButton(on_restart))


# --- WASH Crew Role ---------------------------------------------------------------------


class WashCrewRoleSelect(discord.ui.RoleSelect):
    def __init__(self, on_select: OnRoleSelected) -> None:
        super().__init__(
            placeholder="Choose the WASH Crew role",
            min_values=1,
            max_values=1,
            custom_id="wpm_setup_wash_crew_role_select",
        )
        self._on_select = on_select

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_select(interaction, self.values[0].id)


class WashCrewRoleStepView(SetupWizardStepView):
    """Step 1: choose the role that controls administrative access to WASH.

    The first step -- never shows a Back button, since there is no
    earlier step to return to.
    """

    def __init__(
        self,
        on_select: OnRoleSelected,
        on_save_for_later: OnSaveForLater,
        on_cancel: OnWizardCancel,
        *,
        requester_id: Optional[int] = None,
    ) -> None:
        super().__init__(requester_id=requester_id)
        self.add_item(WashCrewRoleSelect(on_select))
        self.add_item(SetupSaveForLaterButton(on_save_for_later))
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
            placeholder="Choose the Watch Party role",
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
            placeholder="Choose the join mode (defaults to Self-Service)",
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


class WatchPartyRoleStepView(SetupWizardStepView):
    """Step 2: choose the Watch Party role and its join mode together.

    The Watch Party role gates WASH's participant commands (/add, /list,
    /stats, and more) -- every member who should use them needs it. The
    role select still technically allows zero selections (min_values=0,
    so Continue can be pressed without picking one), but leaving it
    unset is not a neutral choice: until a Watch Party role is
    configured here or later via /config, only WASH Crew can use those
    commands. Join mode always has a value, defaulting to Self-Service
    (WatchPartyRoleConfig's own documented default) if never touched.
    """

    def __init__(
        self,
        on_confirm: OnWatchPartyRoleConfirmed,
        on_back: OnBack,
        on_save_for_later: OnSaveForLater,
        on_cancel: OnWizardCancel,
        *,
        requester_id: Optional[int] = None,
    ) -> None:
        super().__init__(requester_id=requester_id)
        self._on_confirm = on_confirm
        self.role_select = WatchPartyRoleSelectComponent()
        self.join_mode_select = JoinModeSelectComponent()
        self.add_item(self.role_select)
        self.add_item(self.join_mode_select)
        self.add_item(WatchPartyRoleConfirmButton(self._handle_confirm))
        self.add_item(SetupBackButton(on_back))
        self.add_item(SetupSaveForLaterButton(on_save_for_later))
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


class CollectionTypeButton(discord.ui.Button):
    def __init__(
        self,
        on_click: OnDatabaseChoiceButton,
        *,
        label: str,
        custom_id: str,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
    ) -> None:
        super().__init__(label=label, style=style, custom_id=custom_id)
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class CollectionTypeChoiceView(SetupWizardStepView):
    """Step 4, part 2 (reached from Create New): "What type of collection
    would you like to create?"

    Contextual Database Resolution: guides WASH Crew through creating
    their first suggestion database without exposing implementation
    details like "Create a Suggestion Database" as a prompt. Every option
    still produces the exact same SuggestionDatabase object via
    SetupWizardService.create_new_database -- Movies/TV Shows pre-fill
    the name, Special Collection/Custom collect it through a friendlier
    modal, Import Existing points at the standalone /import command
    (Discord has no way to attach a file from within this wizard's own
    interaction flow). Mirrors ExistingDatabaseSelectView's convention
    for transient sub-screens: Cancel only, no Back/Save & Finish Later.
    """

    def __init__(
        self,
        on_movies: OnDatabaseChoiceButton,
        on_tv_shows: OnDatabaseChoiceButton,
        on_special_collection: OnDatabaseChoiceButton,
        on_custom: OnDatabaseChoiceButton,
        on_import_existing: OnDatabaseChoiceButton,
        on_cancel: OnWizardCancel,
        *,
        requester_id: Optional[int] = None,
    ) -> None:
        super().__init__(requester_id=requester_id)
        self.add_item(
            CollectionTypeButton(
                on_movies,
                label="Movies (Recommended)",
                custom_id="wpm_setup_database_type_movies",
                style=discord.ButtonStyle.primary,
            )
        )
        self.add_item(CollectionTypeButton(on_tv_shows, label="TV Shows", custom_id="wpm_setup_database_type_tv_shows"))
        self.add_item(
            CollectionTypeButton(
                on_special_collection, label="Special Collection", custom_id="wpm_setup_database_type_special"
            )
        )
        self.add_item(CollectionTypeButton(on_custom, label="Custom", custom_id="wpm_setup_database_type_custom"))
        self.add_item(
            CollectionTypeButton(
                on_import_existing, label="Import Existing Database", custom_id="wpm_setup_database_type_import"
            )
        )
        self.add_item(SetupCancelButton(on_cancel))


class ImportExistingDatabaseNoticeView(SetupWizardStepView):
    """Shown after choosing Import Existing Database. Discord does not
    allow attaching a file from within an existing interaction flow, so
    this points at running /import directly instead of faking an
    in-wizard upload.
    """

    def __init__(self, on_back: OnBack, on_cancel: OnWizardCancel, *, requester_id: Optional[int] = None) -> None:
        super().__init__(requester_id=requester_id)
        self.add_item(SetupBackButton(on_back))
        self.add_item(SetupCancelButton(on_cancel))


class SuggestionDatabaseChoiceView(SetupWizardStepView):
    """Step 4, part 1: select an existing suggestion database, or create one."""

    def __init__(
        self,
        on_select_existing: OnDatabaseChoiceButton,
        on_create_new: OnDatabaseChoiceButton,
        on_back: OnBack,
        on_save_for_later: OnSaveForLater,
        on_cancel: OnWizardCancel,
        *,
        requester_id: Optional[int] = None,
    ) -> None:
        super().__init__(requester_id=requester_id)
        self.add_item(SelectExistingDatabaseButton(on_select_existing))
        self.add_item(CreateNewDatabaseButton(on_create_new))
        self.add_item(SetupBackButton(on_back))
        self.add_item(SetupSaveForLaterButton(on_save_for_later))
        self.add_item(SetupCancelButton(on_cancel))


class ExistingDatabaseSelect(discord.ui.Select):
    def __init__(self, databases: List[Tuple[int, str]], on_select: OnExistingDatabaseSelected) -> None:
        options = [
            discord.SelectOption(label=name[:100], value=str(database_id))
            for database_id, name in databases[:25]
        ]
        super().__init__(placeholder="Choose a collection", options=options, custom_id="wpm_setup_database_select")
        self._on_select = on_select

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_select(interaction, int(self.values[0]))


class ExistingDatabaseSelectView(SetupWizardStepView):
    """Step 4, part 2a: pick which existing suggestion database to use.

    A transient sub-screen of the Suggestion Database step, not a
    top-level wizard step in its own right -- Cancel Setup returns here
    (matching the choice screen it was reached from); use the Suggestion
    Database step's own Back button to return to Admin Channel.
    """

    def __init__(
        self,
        databases: List[Tuple[int, str]],
        on_select: OnExistingDatabaseSelected,
        on_cancel: OnWizardCancel,
        *,
        requester_id: Optional[int] = None,
    ) -> None:
        super().__init__(requester_id=requester_id)
        self.add_item(ExistingDatabaseSelect(databases, on_select))
        self.add_item(SetupCancelButton(on_cancel))


class CreateDatabaseNameModal(discord.ui.Modal):
    """Step 4, part 2b (1 of 2): collect the new collection's name.

    title/label/placeholder default to the original generic wording so
    every existing caller keeps working unchanged; CollectionTypeChoiceView's
    Special Collection and Custom options pass friendlier wording instead
    (see their callbacks in bot.py).
    """

    def __init__(
        self,
        on_submit: OnDatabaseNameSubmit,
        *,
        title: str = "New Collection",
        label: str = "Collection name",
        placeholder: Optional[str] = None,
    ) -> None:
        super().__init__(title=title)
        self._submit_callback = on_submit
        self.name_input = discord.ui.TextInput(label=label, required=True, placeholder=placeholder)
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._submit_callback(interaction, self.name_input.value)


class DestinationChannelSelect(discord.ui.ChannelSelect):
    def __init__(
        self,
        on_select: OnChannelSelected,
        *,
        custom_id: str,
        placeholder: str,
        channel_types: Optional[List[discord.ChannelType]] = None,
    ) -> None:
        super().__init__(
            placeholder=placeholder,
            channel_types=channel_types or _DESTINATION_CHANNEL_TYPES,
            min_values=1,
            max_values=1,
            custom_id=custom_id,
        )
        self._on_select = on_select

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_select(interaction, self.values[0].id)


# --- Home Channel Creation ------------------------------------------------------------------
#
# CreateNewChannelButton/UseExistingChannelButton/ExistingChannelSelectView
# back the Home Channel step (create vs. use existing). Collections
# themselves no longer choose a channel at all -- each one's suggestion
# thread is created automatically under the home channel (see Requirement
# 5: "Collections should default to threads").

_EXISTING_CHANNEL_TYPES = [discord.ChannelType.text]


class CreateNewChannelButton(discord.ui.Button):
    def __init__(self, on_click: OnDatabaseChoiceButton) -> None:
        super().__init__(
            label="Create New Channel (Recommended)",
            style=discord.ButtonStyle.primary,
            custom_id="wpm_setup_destination_create_channel",
        )
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class UseExistingChannelButton(discord.ui.Button):
    def __init__(self, on_click: OnDatabaseChoiceButton) -> None:
        super().__init__(
            label="Use Existing Channel", style=discord.ButtonStyle.secondary, custom_id="wpm_setup_destination_existing_channel"
        )
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class ExistingChannelSelectView(SetupWizardStepView):
    """Use Existing Channel: text channels only. Reused by both the Home
    Channel step and (via HomeChannelChoiceView's shared buttons) any
    other "pick an existing channel" screen.
    """

    def __init__(
        self, on_select: OnChannelSelected, on_cancel: OnWizardCancel, *, requester_id: Optional[int] = None
    ) -> None:
        super().__init__(requester_id=requester_id)
        self.add_item(
            DestinationChannelSelect(
                on_select,
                custom_id="wpm_setup_destination_existing_channel_select",
                placeholder="Choose an existing text channel",
                channel_types=_EXISTING_CHANNEL_TYPES,
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


class AdminChannelStepView(SetupWizardStepView):
    """Admin Channel step: choose where Approval-Required membership
    requests are posted for WASH Crew, or skip for now.

    Reuses DestinationChannelSelect (generic, already used for the Watch
    Destination step) rather than a duplicate channel-select component.
    """

    def __init__(
        self,
        on_select: OnChannelSelected,
        on_skip: OnSkip,
        on_back: OnBack,
        on_save_for_later: OnSaveForLater,
        on_cancel: OnWizardCancel,
        *,
        requester_id: Optional[int] = None,
    ) -> None:
        super().__init__(requester_id=requester_id)
        self.add_item(
            DestinationChannelSelect(
                on_select,
                custom_id="wpm_setup_admin_channel_select",
                placeholder="Choose an existing channel or thread",
            )
        )
        self.add_item(SkipAdminChannelButton(on_skip))
        self.add_item(SetupBackButton(on_back))
        self.add_item(SetupSaveForLaterButton(on_save_for_later))
        self.add_item(SetupCancelButton(on_cancel))


# --- Home Channel -----------------------------------------------------------------------------
#
# WASH's "home": the parent channel every collection's suggestion thread
# (and, by default, the watched-movie destination thread) is created
# under, so collections read as siblings under one recognizable channel
# rather than being scattered across the server as top-level channels.
# No skip option -- every collection needs somewhere to create its
# thread, so a home channel is always required.


class HomeChannelChoiceView(SetupWizardStepView):
    """Home Channel step: create a new channel (recommended), or use an
    existing one. Reuses CreateNewChannelButton/UseExistingChannelButton
    -- the same "create vs. use existing" choice the old suggestion-
    destination sub-flow offered, now surfaced once, up front.
    """

    def __init__(
        self,
        on_create_new: OnDatabaseChoiceButton,
        on_use_existing: OnDatabaseChoiceButton,
        on_back: OnBack,
        on_save_for_later: OnSaveForLater,
        on_cancel: OnWizardCancel,
        *,
        requester_id: Optional[int] = None,
    ) -> None:
        super().__init__(requester_id=requester_id)
        self.add_item(CreateNewChannelButton(on_create_new))
        self.add_item(UseExistingChannelButton(on_use_existing))
        self.add_item(SetupBackButton(on_back))
        self.add_item(SetupSaveForLaterButton(on_save_for_later))
        self.add_item(SetupCancelButton(on_cancel))


class HomeChannelNameModal(discord.ui.Modal):
    """Collects the new home channel's name, pre-filled with the
    documented default ("Watch Party") so accepting it requires no typing.
    """

    def __init__(self, on_submit: OnDatabaseNameSubmit, *, default: str = "Watch Party") -> None:
        super().__init__(title="Name the Home Channel")
        self._submit_callback = on_submit
        self.name_input = discord.ui.TextInput(label="Channel name", required=True, default=default)
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._submit_callback(interaction, self.name_input.value)


# --- Watched Movie Destination -------------------------------------------------------------


class SkipWatchDestinationButton(discord.ui.Button):
    def __init__(self, on_click: OnSkip) -> None:
        super().__init__(label="Skip for Now", style=discord.ButtonStyle.secondary, custom_id="wpm_setup_destination_skip")
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class CreateNewThreadButton(discord.ui.Button):
    def __init__(self, on_click: OnCreateThreadClicked) -> None:
        super().__init__(
            label="Create New Thread", style=discord.ButtonStyle.secondary, custom_id="wpm_setup_destination_create_thread"
        )
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class CreateThreadNameModal(discord.ui.Modal):
    """Watched Movie Destination step: name the new thread, created as a
    sibling under WASH's home channel (never nested under another
    thread -- Discord doesn't support that regardless).
    """

    def __init__(self, on_submit: OnThreadNameSubmit, *, default: Optional[str] = None) -> None:
        super().__init__(title="New Thread")
        self._submit_callback = on_submit
        self.name_input = discord.ui.TextInput(
            label="Thread name", required=True, max_length=100, default=default
        )
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._submit_callback(interaction, self.name_input.value)


class WatchDestinationStepView(SetupWizardStepView):
    """Watched Movie Destination step: choose an existing channel or
    thread, create a new thread, or skip for now.
    """

    def __init__(
        self,
        on_select: OnChannelSelected,
        on_create_thread: OnCreateThreadClicked,
        on_skip: OnSkip,
        on_back: OnBack,
        on_save_for_later: OnSaveForLater,
        on_cancel: OnWizardCancel,
        *,
        requester_id: Optional[int] = None,
    ) -> None:
        super().__init__(requester_id=requester_id)
        self.add_item(
            DestinationChannelSelect(
                on_select,
                custom_id="wpm_setup_watch_destination_channel_select",
                placeholder="Choose an existing channel or thread",
            )
        )
        self.add_item(CreateNewThreadButton(on_create_thread))
        self.add_item(SkipWatchDestinationButton(on_skip))
        self.add_item(SetupBackButton(on_back))
        self.add_item(SetupSaveForLaterButton(on_save_for_later))
        self.add_item(SetupCancelButton(on_cancel))


# --- Modal-based steps: Voting/Reminder/Backup Defaults ------------------------------------


class ConfigureStepButton(discord.ui.Button):
    def __init__(self, on_click: OnConfigureClicked, *, label: str, custom_id: str) -> None:
        super().__init__(label=label, style=discord.ButtonStyle.primary, custom_id=custom_id)
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class ModalStepIntroView(SetupWizardStepView):
    """A short intro screen whose single button opens the step's modal.

    Reused for Voting Defaults, Reminder Defaults, and Backup Defaults --
    a modal cannot be sent directly in response to /setup's own
    interaction chain without an intervening component click, so each of
    these three steps shows this one-button prompt first. Also reused,
    unchanged, as the retry screen shown after a failed modal submission.
    """

    def __init__(
        self,
        on_configure: OnConfigureClicked,
        on_back: OnBack,
        on_save_for_later: OnSaveForLater,
        on_cancel: OnWizardCancel,
        *,
        button_label: str,
        custom_id: str,
        requester_id: Optional[int] = None,
    ) -> None:
        super().__init__(requester_id=requester_id)
        self.add_item(ConfigureStepButton(on_configure, label=button_label, custom_id=custom_id))
        self.add_item(SetupBackButton(on_back))
        self.add_item(SetupSaveForLaterButton(on_save_for_later))
        self.add_item(SetupCancelButton(on_cancel))


class VotingDefaultsModal(discord.ui.Modal):
    """Default nominee count, duration, and visibility.

    Candidate selection is per-database (Contextual Database Resolution)
    and is chosen separately, via CandidateSelectionSelectComponent on
    VotingDefaultsIntroView (/setup, where exactly one database exists
    so far) or /config's Manage Databases -> a specific database ->
    Candidate Selection screen -- Discord modals cannot contain Select
    menus, so it can no longer live here as a free-text field once it
    became a dropdown.
    """

    def __init__(self, on_submit: OnVotingDefaultsSubmit, *, defaults: Optional[Tuple[str, str, str]] = None) -> None:
        super().__init__(title="Voting Defaults")
        self._submit_callback = on_submit
        candidate_count_default, duration_default, visibility_default = defaults or ("3", "1 day", "visible")
        self.candidate_count_input = discord.ui.TextInput(
            label="Default candidate count (2-10)", default=candidate_count_default
        )
        self.duration_input = discord.ui.TextInput(
            label="Default vote duration (1 hour - 30 days)",
            default=duration_default,
            placeholder="e.g. 4h, 12h, 1 day, or 7 days",
        )
        self.visibility_input = discord.ui.TextInput(
            label="Default visibility: blind or visible", default=visibility_default
        )
        self.add_item(self.candidate_count_input)
        self.add_item(self.duration_input)
        self.add_item(self.visibility_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._submit_callback(
            interaction,
            self.candidate_count_input.value,
            self.duration_input.value,
            self.visibility_input.value,
        )


class CandidateSelectionSelectComponent(discord.ui.Select):
    """Discord Select replacing free-text candidate-selection entry.

    Persists exactly the same CandidateSelectionMode values as before
    (option value == CandidateSelectionMode.value); only the input
    mechanism changed. Reused, unchanged, by /setup's
    VotingDefaultsIntroView and /config's
    ConfigDatabaseCandidateSelectionView (config_view.py).
    """

    def __init__(self, *, default: CandidateSelectionMode) -> None:
        options = [
            discord.SelectOption(
                label=_CANDIDATE_SELECTION_SELECT_LABELS[mode],
                value=mode.value,
                default=mode is default,
            )
            for mode in (
                CandidateSelectionMode.ROTATION_POOL,
                CandidateSelectionMode.SOFT_ROTATION,
                CandidateSelectionMode.INFINITE_POOL,
            )
        ]
        super().__init__(
            placeholder="Choose the candidate selection mode",
            options=options,
            custom_id="wpm_setup_voting_candidate_selection_select",
        )
        self._default = default

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

    @property
    def selected(self) -> CandidateSelectionMode:
        """The chosen mode, or the pre-filled default if never touched."""
        return CandidateSelectionMode(self.values[0]) if self.values else self._default


class VotingDefaultsIntroView(SetupWizardStepView):
    """Voting Defaults step: choose the candidate selection mode from a
    dropdown, then press Set Voting Defaults to open the modal for the
    remaining fields (candidate count, duration, visibility).
    """

    def __init__(
        self,
        on_configure: OnVotingDefaultsConfigure,
        on_back: OnBack,
        on_save_for_later: OnSaveForLater,
        on_cancel: OnWizardCancel,
        *,
        default_candidate_selection: CandidateSelectionMode,
        requester_id: Optional[int] = None,
    ) -> None:
        super().__init__(requester_id=requester_id)
        self.candidate_selection_select = CandidateSelectionSelectComponent(default=default_candidate_selection)
        self.add_item(self.candidate_selection_select)
        self.add_item(
            ConfigureStepButton(
                self._handle_configure, label="Set Voting Defaults", custom_id="wpm_setup_voting_defaults_configure"
            )
        )
        self.add_item(SetupBackButton(on_back))
        self.add_item(SetupSaveForLaterButton(on_save_for_later))
        self.add_item(SetupCancelButton(on_cancel))
        self._on_configure = on_configure

    async def _handle_configure(self, interaction: discord.Interaction) -> None:
        await self._on_configure(interaction, self.candidate_selection_select.selected)


class ReminderDefaultsModal(discord.ui.Modal):
    """Step 7: whether a vote-ending reminder is sent, and how long before close."""

    def __init__(self, on_submit: OnReminderDefaultsSubmit, *, defaults: Optional[Tuple[str, str]] = None) -> None:
        super().__init__(title="Reminder Defaults")
        self._submit_callback = on_submit
        enabled_default, minutes_default = defaults or ("yes", "1 day")
        self.enabled_input = discord.ui.TextInput(label="Reminder enabled? (yes/no)", default=enabled_default)
        self.minutes_input = discord.ui.TextInput(
            label="Reminder before close (e.g. 10m, 1h, 1d)", default=minutes_default
        )
        self.add_item(self.enabled_input)
        self.add_item(self.minutes_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._submit_callback(interaction, self.enabled_input.value, self.minutes_input.value)


class BackupDefaultsModal(discord.ui.Modal):
    """Step 8: automatic backup interval and retention count."""

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
        super().__init__(placeholder="Choose a section to edit...", options=options, custom_id="wpm_setup_review_edit_section")
        self._on_select = on_select

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_select(interaction, self.values[0])


class ReviewStepView(SetupWizardStepView):
    """Step 9 (final): summarize every section, then Save, edit one via the
    dropdown, step Back to Backup Defaults, Save & Finish Later, or Cancel.
    """

    def __init__(
        self,
        section_options: List[Tuple[str, str]],
        on_save: OnSave,
        on_edit_section: OnEditSection,
        on_back: OnBack,
        on_save_for_later: OnSaveForLater,
        on_cancel: OnWizardCancel,
        *,
        requester_id: Optional[int] = None,
    ) -> None:
        super().__init__(requester_id=requester_id)
        self.add_item(SaveSetupButton(on_save))
        self.add_item(EditSectionSelect(section_options, on_edit_section))
        self.add_item(SetupBackButton(on_back))
        self.add_item(SetupSaveForLaterButton(on_save_for_later))
        self.add_item(SetupCancelButton(on_cancel))
