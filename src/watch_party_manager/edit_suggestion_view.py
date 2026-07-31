"""Discord UI components for /edit_suggestion's redesigned action flow.

Requirement 8: OMDb-derived fields (title, release year, IMDb link,
genres, director, etc.) are no longer manually editable -- WASH Crew
picks one of three actions instead: Change Status, Move to Another
Collection, or Cancel. Like edit_vote_view.py/start_vote_view.py, this
module has no dependency on bot.py: each view/select here only knows how
to render itself and forward a click/selection to a caller-supplied
callback. All status-changing/database-moving logic lives in bot.py and
services/suggestion_service.py.
"""

from __future__ import annotations

from typing import Awaitable, Callable

import discord

from watch_party_manager.domain.watch_item import WatchItemStatus
from watch_party_manager.services.suggestion_display_status import (
    SuggestionDisplayStatus,
    display_status_label,
)

EDIT_SUGGESTION_VIEW_TIMEOUT_SECONDS = 120

OnChangeStatusClicked = Callable[[discord.Interaction], Awaitable[None]]
OnMoveCollectionClicked = Callable[[discord.Interaction], Awaitable[None]]
OnCancelEditSuggestion = Callable[[discord.Interaction], Awaitable[None]]
OnStatusSelected = Callable[[discord.Interaction, WatchItemStatus], Awaitable[None]]

# The only statuses WASH Crew may set directly. In an Active Vote is
# deliberately absent -- it's a computed display state (see
# services/suggestion_display_status.py), never something to assign.
_ASSIGNABLE_STATUSES: tuple[WatchItemStatus, ...] = (
    WatchItemStatus.SUGGESTED,
    WatchItemStatus.VOTE_WINNER,
    WatchItemStatus.ARCHIVED,
)


class ChangeStatusButton(discord.ui.Button):
    def __init__(self, on_click: OnChangeStatusClicked) -> None:
        super().__init__(
            label="Change Status", style=discord.ButtonStyle.primary, custom_id="wpm_edit_suggestion_change_status"
        )
        self._callback = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._callback(interaction)


class MoveCollectionButton(discord.ui.Button):
    def __init__(self, on_click: OnMoveCollectionClicked) -> None:
        super().__init__(
            label="Move to Another Collection",
            style=discord.ButtonStyle.secondary,
            custom_id="wpm_edit_suggestion_move_collection",
        )
        self._callback = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._callback(interaction)


class CancelEditSuggestionButton(discord.ui.Button):
    def __init__(self, on_click: OnCancelEditSuggestion) -> None:
        super().__init__(
            label="Cancel", style=discord.ButtonStyle.secondary, custom_id="wpm_edit_suggestion_cancel"
        )
        self._callback = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._callback(interaction)


class EditSuggestionActionView(discord.ui.View):
    """/edit_suggestion's entry point: pick an administrator action.

    IMDb metadata (title, release year, director, etc.) is read-only and
    shown alongside this view for reference, never edited here.
    """

    def __init__(
        self,
        on_change_status: OnChangeStatusClicked,
        on_move_collection: OnMoveCollectionClicked,
        on_cancel: OnCancelEditSuggestion,
    ) -> None:
        super().__init__(timeout=EDIT_SUGGESTION_VIEW_TIMEOUT_SECONDS)
        self.add_item(ChangeStatusButton(on_change_status))
        self.add_item(MoveCollectionButton(on_move_collection))
        self.add_item(CancelEditSuggestionButton(on_cancel))


class ChangeStatusSelect(discord.ui.Select):
    """Lets WASH Crew pick one of the three persisted statuses directly."""

    def __init__(self, on_select: OnStatusSelected, *, current_status: WatchItemStatus) -> None:
        options = [
            discord.SelectOption(
                label=display_status_label(_status_to_display(status)),
                value=status.value,
                default=status is current_status,
            )
            for status in _ASSIGNABLE_STATUSES
        ]
        super().__init__(
            placeholder="Choose the new status...",
            options=options,
            custom_id="wpm_edit_suggestion_status_select",
        )
        self._on_select = on_select

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_select(interaction, WatchItemStatus(self.values[0]))


class ChangeStatusSelectView(discord.ui.View):
    def __init__(self, on_select: OnStatusSelected, *, current_status: WatchItemStatus) -> None:
        super().__init__(timeout=EDIT_SUGGESTION_VIEW_TIMEOUT_SECONDS)
        self.add_item(ChangeStatusSelect(on_select, current_status=current_status))


def _status_to_display(status: WatchItemStatus) -> SuggestionDisplayStatus:
    if status is WatchItemStatus.ARCHIVED:
        return SuggestionDisplayStatus.RETIRED
    if status is WatchItemStatus.VOTE_WINNER:
        return SuggestionDisplayStatus.VOTE_WINNER
    return SuggestionDisplayStatus.AVAILABLE
