"""Discord UI components for /browse -- WASH's interactive collection
browser.

Like random_watch_view.py/filter_menu_view.py, this module has no
dependency on bot.py: every component here only knows how to render
itself and forward a click to a caller-supplied callback. Collection
resolution, eligibility, filtering, pagination text, Random Pick, and
Start Vote all reuse existing services and bot.py handlers -- see
services/browse_service.py and bot.py's browse handlers. The actual
filter-editing screens (Genre, IMDb Rating, MPAA Rating, Actor, Member)
are filter_menu_view.py's FilterMenuView, shared unchanged with /random
watch and Custom Vote Filters; pagination reuses pagination_view.py's
paginate_lines()/PaginatedListView exactly as /list already does
(bot.py's send_browse_session extends a PaginatedListView with Browse's
own extra action buttons, the same way send_suggestion_list already
extends one with SwitchCollectionButton).
"""

from __future__ import annotations

from typing import Awaitable, Callable

import discord

from watch_party_manager.pagination_view import PaginatedListView

OnBrowseAction = Callable[[discord.Interaction], Awaitable[None]]


class ChangeFiltersButton(discord.ui.Button):
    def __init__(self, on_click: OnBrowseAction) -> None:
        super().__init__(label="Change Filters", style=discord.ButtonStyle.secondary, custom_id="wpm_browse_change_filters")
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class ChangeCollectionButton(discord.ui.Button):
    def __init__(self, on_click: OnBrowseAction) -> None:
        super().__init__(
            label="Change Collection", style=discord.ButtonStyle.secondary, custom_id="wpm_browse_change_collection"
        )
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class BrowseBackButton(discord.ui.Button):
    """Shown only on the "no matches" screen (Section 7) -- a generic
    "go back" affordance alongside the more specific Change Filters/
    Change Collection, so an empty result never traps the user. Points
    at the same shared filter menu Change Filters does; kept as its own
    button (own label, own custom_id) rather than reusing
    ChangeFiltersButton, since the task calls for three distinct options
    here.
    """

    def __init__(self, on_click: OnBrowseAction) -> None:
        super().__init__(label="Back", style=discord.ButtonStyle.secondary, custom_id="wpm_browse_empty_back")
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class RandomPickButton(discord.ui.Button):
    """Crew only -- omitted entirely for non-Crew (Section 2), never
    merely disabled.
    """

    def __init__(self, on_click: OnBrowseAction) -> None:
        super().__init__(label="🎲 Random Pick", style=discord.ButtonStyle.secondary, custom_id="wpm_browse_random_pick")
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class StartVoteButton(discord.ui.Button):
    """Crew only -- omitted entirely for non-Crew (Section 2)."""

    def __init__(self, on_click: OnBrowseAction) -> None:
        super().__init__(label="🗳️ Start Vote", style=discord.ButtonStyle.secondary, custom_id="wpm_browse_start_vote")
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class PostPubliclyButton(discord.ui.Button):
    """Crew only -- omitted entirely for non-Crew (Section 2)."""

    def __init__(self, on_click: OnBrowseAction) -> None:
        super().__init__(label="📢 Post Publicly", style=discord.ButtonStyle.secondary, custom_id="wpm_browse_post_publicly")
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class BrowsePublicResultsView(PaginatedListView):
    """The dedicated view for a Post Publicly'd browse result (Section
    10/11): Previous/Next only, restricted to the WASH Crew member who
    posted it.

    Deliberately its own class (not the same view instance/session as
    the ephemeral browse screen -- "do not convert the ephemeral
    interaction into a public one") even though it needs no behavior
    beyond what PaginatedListView already provides -- subclassing reuses
    that pagination logic exactly rather than reimplementing Previous/
    Next, page tracking, or the requester-only interaction_check gate a
    second time.
    """

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self._requester_id is not None and interaction.user.id != self._requester_id:
            await interaction.response.send_message(
                "Only the WASH Crew member who posted this can page through it.", ephemeral=True
            )
            return False
        return True


__all__ = [
    "ChangeFiltersButton",
    "ChangeCollectionButton",
    "BrowseBackButton",
    "RandomPickButton",
    "StartVoteButton",
    "PostPubliclyButton",
    "BrowsePublicResultsView",
]
