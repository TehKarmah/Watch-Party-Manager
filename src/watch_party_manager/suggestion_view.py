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

from datetime import date
from typing import Awaitable, Callable, Optional

import discord

from watch_party_manager.domain.watch_item import WatchItem, WatchItemStatus

OnToggleRejectionCallback = Callable[[discord.Interaction, int], Awaitable[None]]
OnUndoRejectionCallback = Callable[[discord.Interaction, int], Awaitable[None]]
OnWatchedClickCallback = Callable[[discord.Interaction, int], Awaitable[None]]
OnWatchedDateSubmit = Callable[[discord.Interaction, str], Awaitable[None]]

REJECTION_CONFIRMATION_TIMEOUT_SECONDS = 300


def build_reject_button_custom_id(suggestion_id: int) -> str:
    """Build the stable custom_id for a suggestion's "I WILL NOT WATCH" button.

    Shared between RejectSuggestionButton's construction and bot.py's
    startup migration (which must detect whether a fetched message
    already carries this exact button before deciding to attach one) so
    the two can never drift apart.
    """
    return f"wpm_suggestion_reject_{suggestion_id}"


def build_reject_button_label(rejection_count: int, threshold: int, *, archived: bool, watched: bool = False) -> str:
    """Build the "I WILL NOT WATCH" button's label.

    The button label itself is how the suggestion message displays its
    current rejection count and threshold (e.g. "I WILL NOT WATCH: 1 / 2")
    -- there is no separate counter element, so refreshing this label
    after every interaction is what keeps the suggestion message current.

    Args:
        rejection_count: How many distinct members have rejected this suggestion.
        threshold: The configured rejection threshold for automatic archiving.
        archived: Whether the suggestion has already been archived.
        watched: Whether the suggestion has been marked Watched (Watched
            Button & Archive Workflow) -- also disables the button, with
            its own, distinct label prefix so it never misleadingly
            claims the item was archived when it wasn't.

    Returns:
        The button's label text.
    """
    counter = f"{rejection_count} / {threshold}"
    if watched:
        return f"Watched — I WILL NOT WATCH: {counter}"
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
        watched: bool = False,
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
            watched: Disables the button once the suggestion has been
                marked Watched -- "I WILL NOT WATCH" no longer applies
                once the group has actually watched it.
            on_toggle: Called with (interaction, suggestion_id) when clicked.
        """
        disabled = archived or watched
        super().__init__(
            label=build_reject_button_label(rejection_count, threshold, archived=archived, watched=watched),
            style=discord.ButtonStyle.secondary if disabled else discord.ButtonStyle.danger,
            custom_id=build_reject_button_custom_id(suggestion_id),
            disabled=disabled,
        )
        self.suggestion_id = suggestion_id
        self._on_toggle = on_toggle

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_toggle(interaction, self.suggestion_id)


def build_watched_button_custom_id(suggestion_id: int) -> str:
    """Build the stable custom_id for a suggestion's "Watched" button.

    Shared between WatchedSuggestionButton's construction and bot.py's
    startup migration, mirroring build_reject_button_custom_id's role
    for the reject button.
    """
    return f"wpm_suggestion_watched_{suggestion_id}"


class WatchedSuggestionButton(discord.ui.Button):
    """The suggestion message's "Watched" button (Watched Button & Archive
    Workflow).

    Visible to everyone -- so members notice an item hasn't been marked
    watched yet -- but only WASH Crew can successfully use it; the
    permission check happens in the on_click callback, never here
    (component visibility is never the security boundary). Delegates
    entirely to on_click for the permission check, confirmation, and
    the actual mutation, matching RejectSuggestionButton's own
    "render and forward only" design.
    """

    def __init__(self, suggestion_id: int, *, watched: bool, on_click: OnWatchedClickCallback) -> None:
        """Initialize the button.

        Args:
            suggestion_id: The suggestion this button belongs to.
            watched: Disables the button once already marked Watched --
                a suggestion is only ever confirmed watched once.
            on_click: Called with (interaction, suggestion_id) when clicked.
        """
        super().__init__(
            label="Watched" if not watched else "✅ Watched",
            style=discord.ButtonStyle.secondary if watched else discord.ButtonStyle.success,
            custom_id=build_watched_button_custom_id(suggestion_id),
            disabled=watched,
        )
        self.suggestion_id = suggestion_id
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction, self.suggestion_id)


class SuggestionView(discord.ui.View):
    """The "I WILL NOT WATCH" and Watched buttons for one suggestion message.

    timeout=None and stable custom_ids make this a persistent Discord
    view. bot.py re-registers the view for every active suggestion's
    stored message on startup (see restore_persistent_suggestion_views).
    """

    def __init__(
        self,
        watch_item: WatchItem,
        threshold: int,
        on_toggle: OnToggleRejectionCallback,
        on_watched: OnWatchedClickCallback,
    ) -> None:
        """Initialize the view for one suggestion.

        Args:
            watch_item: The suggestion this view belongs to. Its current
                rejection count, archived status, and watched status
                drive both buttons' labels and disabled states.
            threshold: The suggestion database's configured rejection threshold.
            on_toggle: Passed through to the reject button.
            on_watched: Passed through to the Watched button.
        """
        if watch_item.id is None or watch_item.id <= 0:
            raise ValueError("SuggestionView requires a suggestion with a positive ID.")

        super().__init__(timeout=None)
        rejection_count = len(watch_item.journey.rejected_by_discord_user_ids)
        archived = watch_item.status == WatchItemStatus.ARCHIVED
        watched = watch_item.status == WatchItemStatus.WATCHED
        self.add_item(
            RejectSuggestionButton(
                watch_item.id,
                rejection_count,
                threshold,
                archived=archived,
                watched=watched,
                on_toggle=on_toggle,
            )
        )
        self.add_item(WatchedSuggestionButton(watch_item.id, watched=watched, on_click=on_watched))


class UndoRejectionButton(discord.ui.Button):
    """The rejection confirmation's one-click "Undo Rejection" control."""

    def __init__(self, suggestion_id: int, on_undo: OnUndoRejectionCallback) -> None:
        super().__init__(label="Undo Rejection", style=discord.ButtonStyle.secondary)
        self.suggestion_id = suggestion_id
        self._on_undo = on_undo

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_undo(interaction, self.suggestion_id)


class RejectionConfirmationView(discord.ui.View):
    """Shown alongside /reject's confirmation so a member can immediately
    undo their own rejection without needing to remember /unreject or
    the suggestion's ID.

    requester_id restricts the button to the rejecting member as a second
    layer of defense -- the confirmation is already ephemeral (only they
    can see it in Discord's own client), matching the convention used
    elsewhere for ephemeral controls (see setup_wizard_view.SetupWizardStepView).
    """

    def __init__(
        self, suggestion_id: int, on_undo: OnUndoRejectionCallback, *, requester_id: Optional[int] = None
    ) -> None:
        super().__init__(timeout=REJECTION_CONFIRMATION_TIMEOUT_SECONDS)
        self._requester_id = requester_id
        self.add_item(UndoRejectionButton(suggestion_id, on_undo))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self._requester_id is not None and interaction.user.id != self._requester_id:
            await interaction.response.send_message(
                "Only the member who rejected this suggestion can undo it.", ephemeral=True
            )
            return False
        return True


class WatchedDateModal(discord.ui.Modal):
    """Watched Button & Archive Workflow: confirm (and optionally edit)
    the date a suggestion was actually watched, defaulting to today.

    A single free-text field rather than a Select, since a date has no
    small fixed set of choices -- validation (a real calendar date, not
    in the future) happens in bot.py's on_submit callback, matching
    every other modal in this codebase (the modal only collects raw
    text; parsing/validation is the caller's job).
    """

    def __init__(self, suggestion_id: int, on_submit: OnWatchedDateSubmit, *, default: Optional[str] = None) -> None:
        super().__init__(title="Confirm Watched Date")
        self.suggestion_id = suggestion_id
        self._submit_callback = on_submit
        self.watched_date_input = discord.ui.TextInput(
            label="Watched date (YYYY-MM-DD)",
            default=default or date.today().isoformat(),
        )
        self.add_item(self.watched_date_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._submit_callback(interaction, self.watched_date_input.value)
