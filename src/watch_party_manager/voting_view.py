"""Discord UI components for interactive nominee voting.

This module intentionally has no dependency on bot.py: NomineeButton and
VotingView only know how to render themselves and forward a click to a
caller-supplied on_vote callback. All actual vote-casting logic,
formatting, and service calls live in bot.py, keeping this module a thin
presentation layer with zero business logic of its own.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Awaitable, Callable

import discord

from watch_party_manager.domain.watch_item import WatchItem

# Discord's hard limit on a button's visible label text.
BUTTON_LABEL_MAX_LENGTH = 80

# Discord's hard limit on the number of components a single View may hold
# (5 per row, 5 rows). WASH currently allows at most 10 nominees, so this
# remains a defensive platform limit rather than a normal workflow limit.
MAX_NOMINEE_BUTTONS = 25

OnVoteCallback = Callable[[discord.Interaction, int], Awaitable[None]]


def build_nominee_button_label(title: str) -> str:
    """Build a concise, Discord-safe button label for a nominee.

    Args:
        title: The nominee's full title.

    Returns:
        The title as-is if it fits Discord's label length limit, otherwise
        a truncated version ending in an ellipsis.
    """
    if len(title) <= BUTTON_LABEL_MAX_LENGTH:
        return title
    return title[: BUTTON_LABEL_MAX_LENGTH - 1].rstrip() + "…"


class NomineeButton(discord.ui.Button):
    """A single nominee's voting button.

    Delegates entirely to the on_vote callback for casting the vote and
    responding -- this class only knows how to render itself and forward
    the click, so it carries no business logic and needs no service
    references of its own.
    """

    def __init__(self, suggestion_id: int, title: str, on_vote: OnVoteCallback) -> None:
        """Initialize the button.

        Args:
            suggestion_id: The suggestion this button represents.
            title: The nominee's title, used to build the button label.
            on_vote: Called with (interaction, suggestion_id) when clicked.
        """
        super().__init__(
            label=build_nominee_button_label(title),
            style=discord.ButtonStyle.primary,
            custom_id=f"wpm_vote_suggestion_{suggestion_id}",
        )
        self.suggestion_id = suggestion_id
        self._on_vote = on_vote

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_vote(interaction, self.suggestion_id)


class VotingView(discord.ui.View):
    """One button per nominee for an open voting round.

    Validates the nominee collection against Discord's 25-component limit.
    The configured WASH nominee limit is lower, so exceeding this limit
    indicates a programming or data error rather than a normal workflow.

    timeout=None and stable custom IDs make this a persistent Discord view.
    bot.py re-registers the view for the stored voting message on startup.
    """

    def __init__(self, candidates: Sequence[WatchItem], on_vote: OnVoteCallback) -> None:
        """Initialize the view with one button per candidate.

        Args:
            candidates: The nominees to create buttons for, in order.
            on_vote: Passed through to each NomineeButton.
        """
        candidate_list = list(candidates)
        if len(candidate_list) > MAX_NOMINEE_BUTTONS:
            raise ValueError(
                f"VotingView supports at most {MAX_NOMINEE_BUTTONS} nominees."
            )

        candidate_ids = [candidate.id for candidate in candidate_list]
        if any(candidate_id is None or candidate_id <= 0 for candidate_id in candidate_ids):
            raise ValueError("Every voting nominee must have a positive suggestion ID.")
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("Voting nominees must have unique suggestion IDs.")

        super().__init__(timeout=None)
        for candidate in candidate_list:
            self.add_item(NomineeButton(candidate.id, candidate.title, on_vote))
