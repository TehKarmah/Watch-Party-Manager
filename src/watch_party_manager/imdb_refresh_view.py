"""Discord UI components for /database manage's IMDb Metadata Refresh
workflow: pick a scope (this collection, or every active collection in
the current guild), then confirm before any external request is made.

Like database_admin_view.py (whose CollectionManagementMenuView hosts
this workflow's own entry point), this module has no dependency on
bot.py -- every view here only knows how to render itself and forward a
click to a caller-supplied callback. Also like that module, every screen
this workflow shows is already ephemeral (visible only to the WASH Crew
member who opened /database manage), so -- matching that module's own
documented convention -- there is no separate requester_id gate here: a
plain timeout is enough, since nobody else can ever see or click these
buttons in the first place.
"""

from __future__ import annotations

from typing import Awaitable, Callable

import discord

IMDB_REFRESH_VIEW_TIMEOUT_SECONDS = 300

OnScopeChosen = Callable[[discord.Interaction, str], Awaitable[None]]
OnBack = Callable[[discord.Interaction], Awaitable[None]]
OnCancel = Callable[[discord.Interaction], Awaitable[None]]
OnStartRefresh = Callable[[discord.Interaction], Awaitable[None]]

REFRESH_SCOPE_THIS_COLLECTION = "this_collection"
REFRESH_SCOPE_ALL_COLLECTIONS = "all_collections"


class ScopeChoiceButton(discord.ui.Button):
    def __init__(self, scope: str, on_click: OnScopeChosen, *, label: str, custom_id: str) -> None:
        super().__init__(label=label, style=discord.ButtonStyle.primary, custom_id=custom_id)
        self._scope = scope
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction, self._scope)


class BackButton(discord.ui.Button):
    def __init__(self, on_back: OnBack, *, custom_id: str) -> None:
        super().__init__(label="Back", style=discord.ButtonStyle.secondary, custom_id=custom_id)
        self._on_back = on_back

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_back(interaction)


class RefreshCancelButton(discord.ui.Button):
    def __init__(self, on_cancel: OnCancel, *, custom_id: str) -> None:
        super().__init__(label="Cancel", style=discord.ButtonStyle.secondary, custom_id=custom_id)
        self._on_cancel = on_cancel

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_cancel(interaction)


class StartRefreshButton(discord.ui.Button):
    def __init__(self, on_start: OnStartRefresh, *, custom_id: str) -> None:
        super().__init__(label="Start Refresh", style=discord.ButtonStyle.primary, custom_id=custom_id)
        self._on_start = on_start

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_start(interaction)


class ImdbRefreshScopeSelectionView(discord.ui.View):
    """Section 2: Refresh This Collection / Refresh All Collections / Back.

    Both scope options are always shown, even when the guild only has one
    active collection -- Refresh All Collections still works correctly
    in that case (it simply refreshes the one collection), and always
    presenting the same two choices is simpler and more predictable than
    conditionally hiding one of them.
    """

    def __init__(self, on_scope_chosen: OnScopeChosen, on_back: OnBack) -> None:
        super().__init__(timeout=IMDB_REFRESH_VIEW_TIMEOUT_SECONDS)
        self.add_item(
            ScopeChoiceButton(
                REFRESH_SCOPE_THIS_COLLECTION,
                on_scope_chosen,
                label="Refresh This Collection",
                custom_id="wpm_imdb_refresh_scope_this_collection",
            )
        )
        self.add_item(
            ScopeChoiceButton(
                REFRESH_SCOPE_ALL_COLLECTIONS,
                on_scope_chosen,
                label="Refresh All Collections",
                custom_id="wpm_imdb_refresh_scope_all_collections",
            )
        )
        self.add_item(BackButton(on_back, custom_id="wpm_imdb_refresh_scope_back"))


class ImdbRefreshConfirmationView(discord.ui.View):
    """Section 3: Start Refresh / Back / Cancel -- no external request is
    made until Start Refresh is actually clicked.
    """

    def __init__(self, on_start: OnStartRefresh, on_back: OnBack, on_cancel: OnCancel) -> None:
        super().__init__(timeout=IMDB_REFRESH_VIEW_TIMEOUT_SECONDS)
        self.add_item(StartRefreshButton(on_start, custom_id="wpm_imdb_refresh_start"))
        self.add_item(BackButton(on_back, custom_id="wpm_imdb_refresh_confirm_back"))
        self.add_item(RefreshCancelButton(on_cancel, custom_id="wpm_imdb_refresh_confirm_cancel"))


__all__ = [
    "IMDB_REFRESH_VIEW_TIMEOUT_SECONDS",
    "REFRESH_SCOPE_THIS_COLLECTION",
    "REFRESH_SCOPE_ALL_COLLECTIONS",
    "ScopeChoiceButton",
    "BackButton",
    "RefreshCancelButton",
    "StartRefreshButton",
    "ImdbRefreshScopeSelectionView",
    "ImdbRefreshConfirmationView",
]
