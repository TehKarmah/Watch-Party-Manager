"""Discord selectors for FR-033A's /list database picking and /remove match disambiguation.

Both mirror config_view.py's ConfigDatabaseSelect: options built from
(id, label) pairs, capped at 25, labels truncated to 100 characters --
reusing the established selection pattern rather than inventing a new
one.
"""

from __future__ import annotations

from typing import Awaitable, Callable, List, Tuple

import discord

SUGGESTION_SELECTION_VIEW_TIMEOUT_SECONDS = 120

OnDatabaseSelected = Callable[[discord.Interaction, int], Awaitable[None]]
OnRemovalMatchSelected = Callable[[discord.Interaction, int], Awaitable[None]]


class ListDatabaseSelect(discord.ui.Select):
    """Lets a member pick which suggestion database /list should show."""

    def __init__(self, databases: List[Tuple[int, str]], on_select: OnDatabaseSelected) -> None:
        options = [
            discord.SelectOption(label=name[:100], value=str(database_id))
            for database_id, name in databases[:25]
        ]
        super().__init__(
            placeholder="Choose a suggestion database...",
            options=options,
            custom_id="wpm_list_database_select",
        )
        self._on_select = on_select

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_select(interaction, int(self.values[0]))


class ListDatabaseSelectView(discord.ui.View):
    """/list's "which database?" picker.

    Shown only when the invoking channel doesn't identify a database
    and more than one eligible database exists -- never guessed.
    """

    def __init__(self, databases: List[Tuple[int, str]], on_select: OnDatabaseSelected) -> None:
        super().__init__(timeout=SUGGESTION_SELECTION_VIEW_TIMEOUT_SECONDS)
        self.add_item(ListDatabaseSelect(databases, on_select))


class RemovalMatchSelect(discord.ui.Select):
    """Lets WASH Crew pick which of several matching suggestions to act on."""

    def __init__(self, matches: List[Tuple[int, str]], on_select: OnRemovalMatchSelected) -> None:
        options = [
            discord.SelectOption(label=label[:100], value=str(suggestion_id))
            for suggestion_id, label in matches[:25]
        ]
        super().__init__(
            placeholder="Choose which suggestion...",
            options=options,
            custom_id="wpm_remove_match_select",
        )
        self._on_select = on_select

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_select(interaction, int(self.values[0]))


class RemovalMatchSelectView(discord.ui.View):
    """/remove's "which one did you mean?" picker for ambiguous queries."""

    def __init__(self, matches: List[Tuple[int, str]], on_select: OnRemovalMatchSelected) -> None:
        super().__init__(timeout=SUGGESTION_SELECTION_VIEW_TIMEOUT_SECONDS)
        self.add_item(RemovalMatchSelect(matches, on_select))
