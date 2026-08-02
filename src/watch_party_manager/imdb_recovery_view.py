"""Discord UI components for /database manage's IMDb Metadata Recovery
workflow: pick a scope, confirm, then -- one suggestion at a time -- Crew
reviews and explicitly approves a proposed IMDb match before it's saved.

This is a deliberately separate workflow from IMDb Metadata Refresh
(imdb_refresh_view.py): Refresh re-fetches metadata for suggestions that
already have a usable IMDb identifier; Recovery finds suggestions that
don't have one yet, matches them, and only then hands off to Refresh's
own logic. The two share no view classes, even where a button would look
identical, so each workflow's custom IDs and labels stay unambiguous.

Like database_admin_view.py and imdb_refresh_view.py, this module has no
dependency on bot.py, and -- since every screen this workflow shows is
already ephemeral (visible only to the WASH Crew member who opened
/database manage) -- there is no separate requester_id gate here, matching
both of those modules' own documented convention.
"""

from __future__ import annotations

from typing import Awaitable, Callable, List, Optional

import discord

IMDB_RECOVERY_VIEW_TIMEOUT_SECONDS = 300

OnScopeChosen = Callable[[discord.Interaction, str], Awaitable[None]]
OnAction = Callable[[discord.Interaction], Awaitable[None]]
OnMatchSelected = Callable[[discord.Interaction, str], Awaitable[None]]
OnSearchAgainSubmitted = Callable[[discord.Interaction, Optional[str]], Awaitable[None]]

RECOVERY_SCOPE_THIS_COLLECTION = "this_collection"
RECOVERY_SCOPE_ALL_COLLECTIONS = "all_collections"


class RecoveryScopeChoiceButton(discord.ui.Button):
    def __init__(self, scope: str, on_click: OnScopeChosen, *, label: str, custom_id: str) -> None:
        super().__init__(label=label, style=discord.ButtonStyle.primary, custom_id=custom_id)
        self._scope = scope
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction, self._scope)


class RecoveryBackButton(discord.ui.Button):
    def __init__(self, on_back: OnAction, *, custom_id: str) -> None:
        super().__init__(label="Back", style=discord.ButtonStyle.secondary, custom_id=custom_id)
        self._on_back = on_back

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_back(interaction)


class RecoveryCancelButton(discord.ui.Button):
    def __init__(self, on_cancel: OnAction, *, custom_id: str, label: str = "Cancel") -> None:
        super().__init__(label=label, style=discord.ButtonStyle.secondary, custom_id=custom_id)
        self._on_cancel = on_cancel

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_cancel(interaction)


class StartRecoveryButton(discord.ui.Button):
    def __init__(self, on_start: OnAction, *, custom_id: str) -> None:
        super().__init__(label="Start Recovery", style=discord.ButtonStyle.primary, custom_id=custom_id)
        self._on_start = on_start

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_start(interaction)


class AcceptMatchButton(discord.ui.Button):
    def __init__(self, on_accept: OnAction, *, custom_id: str) -> None:
        super().__init__(label="Accept", style=discord.ButtonStyle.primary, custom_id=custom_id)
        self._on_accept = on_accept

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_accept(interaction)


class SkipMatchButton(discord.ui.Button):
    def __init__(self, on_skip: OnAction, *, custom_id: str) -> None:
        super().__init__(label="Skip", style=discord.ButtonStyle.secondary, custom_id=custom_id)
        self._on_skip = on_skip

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_skip(interaction)


class SearchAgainButton(discord.ui.Button):
    def __init__(self, on_search_again: OnAction, *, custom_id: str) -> None:
        super().__init__(label="Search Again", style=discord.ButtonStyle.secondary, custom_id=custom_id)
        self._on_search_again = on_search_again

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_search_again(interaction)


class ImdbRecoveryScopeSelectionView(discord.ui.View):
    """Recover This Collection / Recover All Collections / Back.

    Both scope options are always shown, even when the guild only has one
    active collection, matching Refresh IMDb Metadata's own convention.
    """

    def __init__(self, on_scope_chosen: OnScopeChosen, on_back: OnAction) -> None:
        super().__init__(timeout=IMDB_RECOVERY_VIEW_TIMEOUT_SECONDS)
        self.add_item(
            RecoveryScopeChoiceButton(
                RECOVERY_SCOPE_THIS_COLLECTION,
                on_scope_chosen,
                label="Recover This Collection",
                custom_id="wpm_imdb_recovery_scope_this_collection",
            )
        )
        self.add_item(
            RecoveryScopeChoiceButton(
                RECOVERY_SCOPE_ALL_COLLECTIONS,
                on_scope_chosen,
                label="Recover All Collections",
                custom_id="wpm_imdb_recovery_scope_all_collections",
            )
        )
        self.add_item(RecoveryBackButton(on_back, custom_id="wpm_imdb_recovery_scope_back"))


class ImdbRecoveryConfirmationView(discord.ui.View):
    """Start Recovery / Back / Cancel -- no OMDb search happens until
    Start Recovery is actually clicked.
    """

    def __init__(self, on_start: OnAction, on_back: OnAction, on_cancel: OnAction) -> None:
        super().__init__(timeout=IMDB_RECOVERY_VIEW_TIMEOUT_SECONDS)
        self.add_item(StartRecoveryButton(on_start, custom_id="wpm_imdb_recovery_start"))
        self.add_item(RecoveryBackButton(on_back, custom_id="wpm_imdb_recovery_confirm_back"))
        self.add_item(RecoveryCancelButton(on_cancel, custom_id="wpm_imdb_recovery_confirm_cancel"))


class ImdbRecoveryConfirmMatchView(discord.ui.View):
    """Crew Review for exactly one proposed candidate: Accept / Skip /
    Search Again / Cancel Recovery (Section 5). Reached either directly
    (a single high-confidence match) or after Crew picks one option from
    a multi-candidate selection list -- either way, the match is never
    saved without landing on this explicit-approval screen first.
    """

    def __init__(self, on_accept: OnAction, on_skip: OnAction, on_search_again: OnAction, on_cancel: OnAction) -> None:
        super().__init__(timeout=IMDB_RECOVERY_VIEW_TIMEOUT_SECONDS)
        self.add_item(AcceptMatchButton(on_accept, custom_id="wpm_imdb_recovery_accept"))
        self.add_item(SkipMatchButton(on_skip, custom_id="wpm_imdb_recovery_skip"))
        self.add_item(SearchAgainButton(on_search_again, custom_id="wpm_imdb_recovery_search_again"))
        self.add_item(RecoveryCancelButton(on_cancel, custom_id="wpm_imdb_recovery_cancel", label="Cancel Recovery"))


class ImdbRecoveryNoMatchView(discord.ui.View):
    """Shown when a search found nothing to propose (or failed
    technically): Search Again / Skip / Cancel Recovery -- there's
    nothing to Accept.
    """

    def __init__(self, on_search_again: OnAction, on_skip: OnAction, on_cancel: OnAction) -> None:
        super().__init__(timeout=IMDB_RECOVERY_VIEW_TIMEOUT_SECONDS)
        self.add_item(SearchAgainButton(on_search_again, custom_id="wpm_imdb_recovery_search_again"))
        self.add_item(SkipMatchButton(on_skip, custom_id="wpm_imdb_recovery_skip"))
        self.add_item(RecoveryCancelButton(on_cancel, custom_id="wpm_imdb_recovery_cancel", label="Cancel Recovery"))


class ImdbRecoveryMatchSelectComponent(discord.ui.Select):
    """Shown only when a search returned more than one plausible
    candidate -- one option per candidate, already built by the caller
    through build_safe_select_option/cap_select_options (see bot.py's
    build_imdb_recovery_match_select_options), so a long title or more
    than 25 candidates can never produce an invalid payload. Selecting an
    option only PROPOSES that candidate -- it still lands on
    ImdbRecoveryConfirmMatchView for explicit Accept, never saving
    directly from this select (Section 5: "every proposed match must be
    explicitly approved").
    """

    def __init__(
        self, on_selected: OnMatchSelected, *, options: List[discord.SelectOption], custom_id: str = "wpm_imdb_recovery_match_select"
    ) -> None:
        super().__init__(placeholder="Choose a match...", min_values=1, max_values=1, options=options, custom_id=custom_id)
        self._on_selected = on_selected

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_selected(interaction, self.values[0])


class ImdbRecoveryMatchSelectionView(discord.ui.View):
    """Multi-candidate selection list: the Select above, plus Skip /
    Search Again / Cancel Recovery (Section 4).
    """

    def __init__(
        self,
        on_selected: OnMatchSelected,
        on_skip: OnAction,
        on_search_again: OnAction,
        on_cancel: OnAction,
        *,
        options: List[discord.SelectOption],
    ) -> None:
        super().__init__(timeout=IMDB_RECOVERY_VIEW_TIMEOUT_SECONDS)
        self.add_item(ImdbRecoveryMatchSelectComponent(on_selected, options=options))
        self.add_item(SkipMatchButton(on_skip, custom_id="wpm_imdb_recovery_skip"))
        self.add_item(SearchAgainButton(on_search_again, custom_id="wpm_imdb_recovery_search_again"))
        self.add_item(RecoveryCancelButton(on_cancel, custom_id="wpm_imdb_recovery_cancel", label="Cancel Recovery"))


class ImdbRecoverySearchAgainModal(discord.ui.Modal):
    """Section 5's "Search Again": Crew types a different title, optionally
    including a year (e.g. "The Thing 1982" or "The Thing (1982)") --
    bot.py's parse_recovery_search_query() extracts a trailing year, if
    any, before re-searching.
    """

    def __init__(self, on_submit: OnSearchAgainSubmitted, *, default_query: str = "") -> None:
        super().__init__(title="Search Again")
        self._submit_callback = on_submit
        self.query_input = discord.ui.TextInput(
            label="Title (optionally with a year)",
            required=True,
            default=default_query,
            placeholder='e.g. "The Thing 1982" or "The Thing (1982)"',
        )
        self.add_item(self.query_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._submit_callback(interaction, self.query_input.value or None)


__all__ = [
    "IMDB_RECOVERY_VIEW_TIMEOUT_SECONDS",
    "RECOVERY_SCOPE_THIS_COLLECTION",
    "RECOVERY_SCOPE_ALL_COLLECTIONS",
    "RecoveryScopeChoiceButton",
    "RecoveryBackButton",
    "RecoveryCancelButton",
    "StartRecoveryButton",
    "AcceptMatchButton",
    "SkipMatchButton",
    "SearchAgainButton",
    "ImdbRecoveryScopeSelectionView",
    "ImdbRecoveryConfirmationView",
    "ImdbRecoveryConfirmMatchView",
    "ImdbRecoveryNoMatchView",
    "ImdbRecoveryMatchSelectComponent",
    "ImdbRecoveryMatchSelectionView",
    "ImdbRecoverySearchAgainModal",
]
