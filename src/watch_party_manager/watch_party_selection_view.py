"""Discord selector for picking a scheduled watch party.

Mirrors suggestion_selection_view.py's DatabaseAdminSelect: options
built from (id, label, description) triples, capped at 25, each field
truncated to Discord's 100-character SelectOption limits. Used by
/cancel_watch_party and /reschedule_watch_party (Release Polish:
Discord-native UX) so WASH Crew never has to look up or type a
watch_party_id by hand.
"""

from __future__ import annotations

from typing import Awaitable, Callable, List, Tuple

import discord

WATCH_PARTY_SELECTION_VIEW_TIMEOUT_SECONDS = 120

OnWatchPartySelected = Callable[[discord.Interaction, int], Awaitable[None]]


class WatchPartySelect(discord.ui.Select):
    """Lets WASH Crew pick which currently scheduled watch party an
    action (cancel, reschedule) should target.

    Each option's label is the watch item's title; the description
    line gives the scheduled date and time, so the right event is
    identifiable without knowing its internal ID.
    """

    def __init__(
        self,
        options: List[Tuple[int, str, str]],
        on_select: OnWatchPartySelected,
        *,
        custom_id: str,
        placeholder: str,
    ) -> None:
        select_options = [
            discord.SelectOption(label=label[:100], description=description[:100], value=str(watch_party_id))
            for watch_party_id, label, description in options[:25]
        ]
        super().__init__(placeholder=placeholder, options=select_options, custom_id=custom_id)
        self._on_select = on_select

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_select(interaction, int(self.values[0]))


class WatchPartySelectView(discord.ui.View):
    """A one-shot "which watch party?" picker for /cancel_watch_party
    and /reschedule_watch_party.
    """

    def __init__(
        self,
        options: List[Tuple[int, str, str]],
        on_select: OnWatchPartySelected,
        *,
        custom_id: str,
        placeholder: str,
    ) -> None:
        super().__init__(timeout=WATCH_PARTY_SELECTION_VIEW_TIMEOUT_SECONDS)
        self.add_item(WatchPartySelect(options, on_select, custom_id=custom_id, placeholder=placeholder))
