"""Discord UI components for /database add's modernized type-selection
flow and /database move's destination-choice flow (Command Structure
Cleanup, pre-v1; thread-only destinations per Collections Should Live In
Threads).

Both commands share the exact same "where should this collection's
suggestions post?" destination choice -- Create New Thread (Recommended),
Use Current Thread, or Use Existing Thread -- so it's built once here and
reused by both, rather than duplicated. Collections no longer offer a
plain-channel destination at all: WASH's Watch Party Home Channel is
reserved for discussion, announcements, and navigation, and collection
activity always lives in a thread beneath it. Like setup_wizard_view.py,
start_vote_view.py, and edit_vote_view.py, this module has no dependency
on bot.py: every view only knows how to render itself and forward a
click/selection to a caller-supplied callback. These are standalone admin
commands, not wizard steps, so -- matching suggestion_selection_view.py's
existing DatabaseAdminSelectView convention rather than the wizard's own
SetupWizardStepView -- they use a plain timeout with no separate
requester_id gate (every response here is already ephemeral, visible only
to whoever ran the command).
"""

from __future__ import annotations

from typing import Awaitable, Callable, List, Optional, Set

import discord

from watch_party_manager.services.collection_display import STANDARD_COLLECTION_TYPES
from watch_party_manager.setup_wizard_view import (
    CreateDatabaseNameModal,
    CreateThreadNameModal,
    DestinationChannelSelect,
)

DATABASE_ADMIN_VIEW_TIMEOUT_SECONDS = 120

# key is a STANDARD_COLLECTION_TYPES key, or the sentinel "special"/"custom".
OnCollectionTypeChosen = Callable[[discord.Interaction, str], Awaitable[None]]
OnDestinationChoice = Callable[[discord.Interaction], Awaitable[None]]
OnCancel = Callable[[discord.Interaction], Awaitable[None]]
OnChannelSelected = Callable[[discord.Interaction, int], Awaitable[None]]

_THREAD_CHANNEL_TYPES = [discord.ChannelType.public_thread, discord.ChannelType.private_thread]


class CancelButton(discord.ui.Button):
    def __init__(self, on_cancel: OnCancel, *, custom_id: str) -> None:
        super().__init__(label="Cancel", style=discord.ButtonStyle.danger, custom_id=custom_id)
        self._on_cancel = on_cancel

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_cancel(interaction)


class CollectionTypeButton(discord.ui.Button):
    def __init__(
        self,
        key: str,
        on_click: OnCollectionTypeChosen,
        *,
        label: str,
        custom_id: str,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
    ) -> None:
        super().__init__(label=label, style=style, custom_id=custom_id)
        self._key = key
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction, self._key)


class CollectionTypeSelectionView(discord.ui.View):
    """/database add's "what type of collection?" screen.

    Offers one button per standard collection type that doesn't already
    have a matching collection in this server (see
    collection_display.used_standard_collection_type_keys), plus Special
    Collection and Custom (always available). The first offered standard
    type is styled as the recommended/default choice -- mirroring the
    Setup Wizard's Movies-first convention, generalized to whichever
    standard type happens to be unused first.
    """

    def __init__(
        self,
        used_type_keys: Set[str],
        on_choice: OnCollectionTypeChosen,
        on_cancel: OnCancel,
    ) -> None:
        super().__init__(timeout=DATABASE_ADMIN_VIEW_TIMEOUT_SECONDS)
        is_first = True
        for standard_type in STANDARD_COLLECTION_TYPES:
            if standard_type.key in used_type_keys:
                continue
            label = f"{standard_type.button_label} (Recommended)" if is_first else standard_type.button_label
            style = discord.ButtonStyle.primary if is_first else discord.ButtonStyle.secondary
            self.add_item(
                CollectionTypeButton(
                    standard_type.key,
                    on_choice,
                    label=label,
                    custom_id=f"wpm_database_add_type_{standard_type.key}",
                    style=style,
                )
            )
            is_first = False
        self.add_item(
            CollectionTypeButton(
                "special", on_choice, label="Special Collection", custom_id="wpm_database_add_type_special"
            )
        )
        self.add_item(
            CollectionTypeButton("custom", on_choice, label="Custom", custom_id="wpm_database_add_type_custom")
        )
        self.add_item(CancelButton(on_cancel, custom_id="wpm_database_add_cancel"))


class CreateNewThreadButton(discord.ui.Button):
    """disabled (not omitted) when there's nowhere to create a new
    thread: no Home Channel is configured (or it's no longer available)
    and the invocation location can't parent one either (e.g. a thread,
    which Discord doesn't allow nesting a thread under) -- presenting an
    option that could never succeed would be confusing.
    """

    def __init__(self, on_click: OnDestinationChoice, *, disabled: bool = False) -> None:
        super().__init__(
            label="Create New Thread (Recommended)",
            style=discord.ButtonStyle.primary,
            custom_id="wpm_database_admin_destination_create_thread",
            disabled=disabled,
        )
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class UseExistingThreadButton(discord.ui.Button):
    def __init__(self, on_click: OnDestinationChoice) -> None:
        super().__init__(
            label="Use Existing Thread",
            style=discord.ButtonStyle.secondary,
            custom_id="wpm_database_admin_destination_existing_thread",
        )
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class UseCurrentThreadButton(discord.ui.Button):
    """Use whichever thread the command was actually run in.

    disabled (not omitted) when the invoking location isn't a thread
    (Collections Should Live In Threads: collections no longer live
    directly in text channels, so a plain text channel -- even WASH's
    configured Home Channel -- no longer qualifies here either) -- the
    option stays visible for consistency across every invocation, just
    unavailable, rather than shifting the other buttons around.
    """

    def __init__(self, on_click: OnDestinationChoice, *, disabled: bool = False) -> None:
        super().__init__(
            label="Use Current Thread",
            style=discord.ButtonStyle.secondary,
            custom_id="wpm_database_admin_destination_use_current",
            disabled=disabled,
        )
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class DestinationChoiceView(discord.ui.View):
    """Shared "where should suggestions post?" screen for /database add
    and /database move: Create New Thread (Recommended), Use Current
    Thread, Use Existing Thread, or Cancel.

    Collections Should Live In Threads: collections no longer offer a
    plain-channel destination at all -- Use Current Channel and Use
    Existing Channel were removed outright (pre-release, no
    compatibility alias needed). WASH's Watch Party Home Channel is
    reserved for discussion, announcements, and navigation; collection
    activity always lives in a thread beneath it.
    """

    def __init__(
        self,
        on_create_new_thread: OnDestinationChoice,
        on_use_current: OnDestinationChoice,
        on_use_existing_thread: OnDestinationChoice,
        on_cancel: OnCancel,
        *,
        current_location_available: bool = True,
        create_new_thread_available: bool = True,
    ) -> None:
        super().__init__(timeout=DATABASE_ADMIN_VIEW_TIMEOUT_SECONDS)
        self.add_item(CreateNewThreadButton(on_create_new_thread, disabled=not create_new_thread_available))
        self.add_item(UseCurrentThreadButton(on_use_current, disabled=not current_location_available))
        self.add_item(UseExistingThreadButton(on_use_existing_thread))
        self.add_item(CancelButton(on_cancel, custom_id="wpm_database_admin_destination_cancel"))


class ExistingThreadSelectView(discord.ui.View):
    """Use Existing Thread: public and private threads only."""

    def __init__(self, on_select: OnChannelSelected, on_cancel: OnCancel) -> None:
        super().__init__(timeout=DATABASE_ADMIN_VIEW_TIMEOUT_SECONDS)
        self.add_item(
            DestinationChannelSelect(
                on_select,
                custom_id="wpm_database_admin_existing_thread_select",
                placeholder="Choose an existing thread",
                channel_types=_THREAD_CHANNEL_TYPES,
            )
        )
        self.add_item(CancelButton(on_cancel, custom_id="wpm_database_admin_existing_thread_cancel"))


# key is one of "move", "edit", "backup", "restore", "reset", "remove".
OnManagementActionChosen = Callable[[discord.Interaction, str], Awaitable[None]]


class ManagementActionButton(discord.ui.Button):
    def __init__(
        self,
        action: str,
        on_click: OnManagementActionChosen,
        *,
        label: str,
        custom_id: str,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary,
    ) -> None:
        super().__init__(label=label, style=style, custom_id=custom_id)
        self._action = action
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction, self._action)


class CollectionManagementMenuView(discord.ui.View):
    """/database manage's guided per-collection action menu.

    Every action reuses the exact logic its equivalent direct /database
    subcommand already uses (see bot.py's start_database_move/backup/
    reset/remove and send_config_database_settings_menu for Edit) -- this
    view only presents the choice.
    """

    def __init__(self, on_action_chosen: OnManagementActionChosen, on_cancel: OnCancel) -> None:
        super().__init__(timeout=DATABASE_ADMIN_VIEW_TIMEOUT_SECONDS)
        self.add_item(
            ManagementActionButton(
                "move", on_action_chosen, label="Move Collection", custom_id="wpm_database_manage_move"
            )
        )
        self.add_item(
            ManagementActionButton(
                "edit", on_action_chosen, label="Edit Collection", custom_id="wpm_database_manage_edit"
            )
        )
        self.add_item(
            ManagementActionButton(
                "backup", on_action_chosen, label="Backup Collection", custom_id="wpm_database_manage_backup"
            )
        )
        self.add_item(
            ManagementActionButton(
                "restore", on_action_chosen, label="Restore Collection", custom_id="wpm_database_manage_restore"
            )
        )
        self.add_item(
            ManagementActionButton(
                "reset", on_action_chosen, label="Reset Collection", custom_id="wpm_database_manage_reset"
            )
        )
        self.add_item(
            ManagementActionButton(
                "remove", on_action_chosen, label="Remove Collection", custom_id="wpm_database_manage_remove"
            )
        )
        self.add_item(CancelButton(on_cancel, custom_id="wpm_database_manage_cancel"))


__all__ = [
    "CollectionManagementMenuView",
    "CollectionTypeSelectionView",
    "CreateDatabaseNameModal",
    "CreateThreadNameModal",
    "DestinationChoiceView",
    "ExistingThreadSelectView",
]
