"""Discord UI components for the "I WILL NOT WATCH" suggestion button.

Mirrors voting_view.py's design: this module has no dependency on bot.py.
RejectSuggestionButton and SuggestionView only know how to render
themselves (including the rejection count/threshold shown on the button
itself) and forward a click to a caller-supplied on_toggle callback. All
actual rejection logic -- toggling between reject and remove-rejection,
permission checks, threshold resolution, and archiving -- lives in bot.py
and the existing SuggestionService, reused unchanged.
"""

from __future__ import annotations

from typing import Awaitable, Callable

import discord

from watch_party_manager.domain.watch_item import WatchItem, WatchItemStatus

OnToggleRejectionCallback = Callable[[discord.Interaction, int], Awaitable[None]]


def build_reject_button_custom_id(suggestion_id: int) -> str:
    """Build the stable custom_id for a suggestion's "I WILL NOT WATCH" button.

    Shared between RejectSuggestionButton's construction and bot.py's
    startup migration (which must detect whether a fetched message
    already carries this exact button before deciding to attach one) so
    the two can never drift apart.
    """
    return f"wpm_suggestion_reject_{suggestion_id}"


def build_reject_button_label(rejection_count: int, threshold: int, *, archived: bool) -> str:
    """Build the "I WILL NOT WATCH" button's label.

    The button label itself is how the suggestion message displays its
    current rejection count and threshold (e.g. "I WILL NOT WATCH: 1 / 2")
    -- there is no separate counter element, so refreshing this label
    after every interaction is what keeps the suggestion message current.

    Args:
        rejection_count: How many distinct members have rejected this suggestion.
        threshold: The configured rejection threshold for automatic archiving.
        archived: Whether the suggestion has already been archived.

    Returns:
        The button's label text.
    """
    counter = f"{rejection_count} / {threshold}"
    if archived:
        return f"Archived — I WILL NOT WATCH: {counter}"
    return f"I WILL NOT WATCH: {counter}"


class RejectSuggestionButton(discord.ui.Button):
    """The suggestion message's single interaction button.

    Delegates entirely to the on_toggle callback for deciding whether a
    click means "reject" or "remove rejection" and for recording it --
    this class only knows how to render itself and forward the click, so
    it carries no business logic and needs no service references of its
    own.
    """

    def __init__(
        self,
        suggestion_id: int,
        rejection_count: int,
        threshold: int,
        *,
        archived: bool,
        on_toggle: OnToggleRejectionCallback,
    ) -> None:
        """Initialize the button.

        Args:
            suggestion_id: The suggestion this button belongs to.
            rejection_count: Shown in the button's label.
            threshold: Shown in the button's label.
            archived: Disables the button and switches its style/label
                once the suggestion has been archived -- an archived
                suggestion's rejection history is final.
            on_toggle: Called with (interaction, suggestion_id) when clicked.
        """
        super().__init__(
            label=build_reject_button_label(rejection_count, threshold, archived=archived),
            style=discord.ButtonStyle.secondary if archived else discord.ButtonStyle.danger,
            custom_id=build_reject_button_custom_id(suggestion_id),
            disabled=archived,
        )
        self.suggestion_id = suggestion_id
        self._on_toggle = on_toggle

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_toggle(interaction, self.suggestion_id)


class SuggestionView(discord.ui.View):
    """The single "I WILL NOT WATCH" button for one suggestion message.

    timeout=None and a stable custom_id make this a persistent Discord
    view. bot.py re-registers the view for every active suggestion's
    stored message on startup (see restore_persistent_suggestion_views).
    """

    def __init__(self, watch_item: WatchItem, threshold: int, on_toggle: OnToggleRejectionCallback) -> None:
        """Initialize the view for one suggestion.

        Args:
            watch_item: The suggestion this view belongs to. Its current
                rejection count and archived status drive the button's
                label and disabled state.
            threshold: The suggestion database's configured rejection threshold.
            on_toggle: Passed through to the button.
        """
        if watch_item.id is None or watch_item.id <= 0:
            raise ValueError("SuggestionView requires a suggestion with a positive ID.")

        super().__init__(timeout=None)
        rejection_count = len(watch_item.journey.rejected_by_discord_user_ids)
        archived = watch_item.status == WatchItemStatus.ARCHIVED
        self.add_item(
            RejectSuggestionButton(
                watch_item.id,
                rejection_count,
                threshold,
                archived=archived,
                on_toggle=on_toggle,
            )
        )
