"""Discord selectors for suggestion-database and suggestion picking.

/list's database picker and /remove's match disambiguation (from
FR-033A) mirror config_view.py's ConfigDatabaseSelect: options built
from (id, label) pairs, capped at 25, labels truncated to 100
characters -- reusing the established selection pattern rather than
inventing a new one.

DatabaseAdminSelect/DatabaseAdminSelectView (Release Polish: Discord-
native UX) extend that same pattern with a description line (Active/
Inactive status and watch-item count) for /database_backup,
/database_reset, and /database_remove -- administrative actions where
seeing exactly what a destructive action targets matters more than for
/list's read-only picker.
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


class DatabaseAdminSelect(discord.ui.Select):
    """Lets WASH Crew pick which suggestion database an admin action
    (backup, reset, remove) should target.

    Each option's description line shows Active/Inactive status and
    watch-item count, so the target of a destructive action is always
    visible rather than guessed from a bare ID.
    """

    def __init__(
        self,
        options: List[Tuple[int, str, str]],
        on_select: OnDatabaseSelected,
        *,
        custom_id: str,
        placeholder: str,
    ) -> None:
        select_options = [
            discord.SelectOption(label=label[:100], description=description[:100], value=str(database_id))
            for database_id, label, description in options[:25]
        ]
        super().__init__(placeholder=placeholder, options=select_options, custom_id=custom_id)
        self._on_select = on_select

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_select(interaction, int(self.values[0]))


class DatabaseAdminSelectView(discord.ui.View):
    """A one-shot "which database?" picker for /database_backup,
    /database_reset, and /database_remove.
    """

    def __init__(
        self,
        options: List[Tuple[int, str, str]],
        on_select: OnDatabaseSelected,
        *,
        custom_id: str,
        placeholder: str,
    ) -> None:
        super().__init__(timeout=SUGGESTION_SELECTION_VIEW_TIMEOUT_SECONDS)
        self.add_item(DatabaseAdminSelect(options, on_select, custom_id=custom_id, placeholder=placeholder))


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
