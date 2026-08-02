"""Discord UI components for /random watch's discovery flow.

Like start_vote_view.py and edit_vote_view.py, this module has no
dependency on bot.py: every view/component here only knows how to render
itself and forward a click/selection to a caller-supplied callback. All
collection resolution, eligibility, filtering, and random selection logic
lives in bot.py's handlers and the existing services they orchestrate
(CollectionEligibilityService, services/nominee_pool_filter.py,
services/random_watch_service.py) -- this module adds presentation only.

The actual filter-editing screens (Genre, IMDb Rating, MPAA Rating,
Actor, Member) live in filter_menu_view.py, shared with /vote start's
Custom Vote Filters -- this module only covers the two screens unique to
/random watch: the initial Pick Random Item/Add Filters choice, and the
result.

Every screen is a short-lived, ephemeral discovery session, not an
unresolved administrative workflow -- so, like start_vote_view.py's
StartVoteChoiceView, every view here uses a plain timeout (not None) and
needs no persistent restart restoration.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Optional

import discord

RANDOM_WATCH_VIEW_TIMEOUT_SECONDS = 180

OnRandomWatchAction = Callable[[discord.Interaction], Awaitable[None]]


class PickRandomItemButton(discord.ui.Button):
    def __init__(self, on_click: OnRandomWatchAction, *, custom_id: str) -> None:
        super().__init__(label="Pick Random Item", style=discord.ButtonStyle.primary, custom_id=custom_id)
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class AddFiltersButton(discord.ui.Button):
    def __init__(self, on_click: OnRandomWatchAction) -> None:
        super().__init__(label="Add Filters", style=discord.ButtonStyle.secondary, custom_id="wpm_random_watch_add_filters")
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class RandomWatchInitialView(discord.ui.View):
    """/random watch's first screen for the resolved collection: Pick
    Random Item (true-random, no filters) or Add Filters (opens the
    shared filter_menu_view.FilterMenuView session).
    """

    def __init__(self, on_pick_random: OnRandomWatchAction, on_add_filters: OnRandomWatchAction) -> None:
        super().__init__(timeout=RANDOM_WATCH_VIEW_TIMEOUT_SECONDS)
        self.add_item(PickRandomItemButton(on_pick_random, custom_id="wpm_random_watch_pick_initial"))
        self.add_item(AddFiltersButton(on_add_filters))


class ChangeCollectionButton(discord.ui.Button):
    def __init__(self, on_click: OnRandomWatchAction, *, custom_id: str = "wpm_random_watch_change_collection") -> None:
        super().__init__(label="Change Collection", style=discord.ButtonStyle.secondary, custom_id=custom_id)
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class PickAgainButton(discord.ui.Button):
    def __init__(self, on_click: OnRandomWatchAction) -> None:
        super().__init__(label="Pick Again", style=discord.ButtonStyle.primary, custom_id="wpm_random_watch_pick_again")
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class ChangeFiltersButton(discord.ui.Button):
    def __init__(self, on_click: OnRandomWatchAction) -> None:
        super().__init__(label="Change Filters", style=discord.ButtonStyle.secondary, custom_id="wpm_random_watch_change_filters")
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class ViewOriginalSuggestionButton(discord.ui.Button):
    """A link-style button -- Discord requires link buttons to carry a
    URL and no custom_id/callback of their own; clicking it is handled
    entirely client-side (it just opens the link).
    """

    def __init__(self, url: str) -> None:
        super().__init__(label="View Original Suggestion", style=discord.ButtonStyle.link, url=url)


class RandomWatchResultView(discord.ui.View):
    """/random watch's result screen: Pick Again (new draw, same
    collection/filters), Change Filters (same collection), Change
    Collection (back to the picker), and -- only when the chosen item
    has a linkable original post -- View Original Suggestion.

    Unlike the rest of /random watch's screens, this one is posted
    publicly in the channel (see bot.py's send_random_watch_session) --
    so, matching the project's established requester-scoping convention
    (pagination_view.PaginatedListView, setup_wizard_view.SetupWizardStepView),
    requester_id restricts Pick Again/Change Filters/Change Collection to
    whoever ran /random watch. This is the only enforcement for those
    buttons -- there is no ephemeral visibility to fall back on once the
    message is public.
    """

    def __init__(
        self,
        on_pick_again: OnRandomWatchAction,
        on_change_filters: OnRandomWatchAction,
        on_change_collection: OnRandomWatchAction,
        *,
        original_suggestion_url: Optional[str] = None,
        requester_id: Optional[int] = None,
    ) -> None:
        super().__init__(timeout=RANDOM_WATCH_VIEW_TIMEOUT_SECONDS)
        self._requester_id = requester_id
        self.add_item(PickAgainButton(on_pick_again))
        self.add_item(ChangeFiltersButton(on_change_filters))
        self.add_item(ChangeCollectionButton(on_change_collection, custom_id="wpm_random_watch_result_change_collection"))
        if original_suggestion_url:
            self.add_item(ViewOriginalSuggestionButton(original_suggestion_url))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self._requester_id is not None and interaction.user.id != self._requester_id:
            await interaction.response.send_message(
                "Only the person who ran this command can use these controls.", ephemeral=True
            )
            return False
        return True


__all__ = [
    "RANDOM_WATCH_VIEW_TIMEOUT_SECONDS",
    "RandomWatchInitialView",
    "RandomWatchResultView",
]
