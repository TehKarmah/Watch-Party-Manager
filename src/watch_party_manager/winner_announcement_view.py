"""Discord UI components for the Watch Party Lifecycle's winner-announcement
scheduling button.

Like suggestion_view.py/voting_view.py, this module has no dependency on
bot.py: each view/button here only knows how to render itself and forward
a click to a caller-supplied callback. All duplicate-scheduling checks,
the actual scheduling flow, and Discord destination resolution live in
bot.py and the existing WatchPartyService/WatchPartyCompletionService.

WinnerAnnouncementView renders exactly one of three states on a vote's
results announcement:
  - Schedule Watch Party -- a single winner, not yet scheduled.
  - Choose Winner to Schedule -- a tie, not yet scheduled.
  - Watch Party Scheduled (disabled) -- already scheduled (for the single
    winner, or for whichever winner was chosen in a tie), shown
    immediately rather than presenting an option that would just be
    rejected as a duplicate.
timeout=None (never auto-expires): a vote's winner might not be scheduled
for days, and a disabled/greyed-out button would be misleading in the
meantime. Not registered as a restart-persistent view (see bot.py) --
see docs/11-Release-Validation-Checklist.md for the documented limitation
this implies after a bot restart.
"""

from __future__ import annotations

from typing import Awaitable, Callable, List, Tuple

import discord

from watch_party_manager.watch_party_selection_view import WatchPartySelect

WINNER_ANNOUNCEMENT_SCHEDULE_CUSTOM_ID_PREFIX = "wpm_schedule_watch_party"
WINNER_ANNOUNCEMENT_CHOOSE_WINNER_CUSTOM_ID_PREFIX = "wpm_choose_winner_to_schedule"
WINNER_ANNOUNCEMENT_SCHEDULED_CUSTOM_ID_PREFIX = "wpm_watch_party_scheduled"

OnScheduleWatchPartyClicked = Callable[[discord.Interaction, int], Awaitable[None]]
OnChooseWinnerToScheduleClicked = Callable[[discord.Interaction], Awaitable[None]]
OnWinnerChosenForScheduling = Callable[[discord.Interaction, int], Awaitable[None]]


def build_schedule_button_custom_id(vote_round_id: int) -> str:
    """Build the stable custom_id for one vote round's Schedule Watch
    Party button.

    Shared between ScheduleWatchPartyButton's construction and anywhere
    that needs to recognize it later, so the two never drift apart --
    mirrors suggestion_view.py's build_reject_button_custom_id.
    """
    return f"{WINNER_ANNOUNCEMENT_SCHEDULE_CUSTOM_ID_PREFIX}_{vote_round_id}"


def build_choose_winner_button_custom_id(vote_round_id: int) -> str:
    return f"{WINNER_ANNOUNCEMENT_CHOOSE_WINNER_CUSTOM_ID_PREFIX}_{vote_round_id}"


def build_scheduled_button_custom_id(vote_round_id: int) -> str:
    return f"{WINNER_ANNOUNCEMENT_SCHEDULED_CUSTOM_ID_PREFIX}_{vote_round_id}"


class ScheduleWatchPartyButton(discord.ui.Button):
    """A single winner's "Schedule Watch Party" button."""

    def __init__(self, vote_round_id: int, suggestion_id: int, on_click: OnScheduleWatchPartyClicked) -> None:
        super().__init__(
            label="Schedule Watch Party",
            style=discord.ButtonStyle.primary,
            custom_id=build_schedule_button_custom_id(vote_round_id),
        )
        self._suggestion_id = suggestion_id
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction, self._suggestion_id)


class ChooseWinnerToScheduleButton(discord.ui.Button):
    """A tied round's "Choose Winner to Schedule" button."""

    def __init__(self, vote_round_id: int, on_click: OnChooseWinnerToScheduleClicked) -> None:
        super().__init__(
            label="Choose Winner to Schedule",
            style=discord.ButtonStyle.primary,
            custom_id=build_choose_winner_button_custom_id(vote_round_id),
        )
        self._on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_click(interaction)


class WatchPartyScheduledButton(discord.ui.Button):
    """Shown instead of the above once a watch party already exists for
    this round's winner (or is otherwise already scheduled) -- disabled,
    never omitted, so the state is visible at a glance.
    """

    def __init__(self, vote_round_id: int) -> None:
        super().__init__(
            label="Watch Party Scheduled",
            style=discord.ButtonStyle.secondary,
            custom_id=build_scheduled_button_custom_id(vote_round_id),
            disabled=True,
        )


class WinnerAnnouncementView(discord.ui.View):
    """The results announcement's single scheduling-related button.

    Exactly one of on_schedule/on_choose_winner is ever meaningful for a
    given round (single winner vs. tie) -- already_scheduled overrides
    either, showing the disabled Watch Party Scheduled state instead.
    """

    def __init__(
        self,
        vote_round_id: int,
        winning_suggestion_ids: List[int],
        on_schedule: OnScheduleWatchPartyClicked,
        on_choose_winner: OnChooseWinnerToScheduleClicked,
        *,
        already_scheduled: bool = False,
    ) -> None:
        super().__init__(timeout=None)
        if already_scheduled:
            self.add_item(WatchPartyScheduledButton(vote_round_id))
        elif len(winning_suggestion_ids) == 1:
            self.add_item(ScheduleWatchPartyButton(vote_round_id, winning_suggestion_ids[0], on_schedule))
        else:
            self.add_item(ChooseWinnerToScheduleButton(vote_round_id, on_choose_winner))


OnScheduleWatchPartyModalSubmit = Callable[[discord.Interaction, str, str, str], Awaitable[None]]


class ScheduleWatchPartyModal(discord.ui.Modal):
    """Collects only what the guided scheduling flow doesn't already
    know: watch date & time, event duration, and an optional description
    override -- never the movie itself (Remove Friction: title, IMDb
    data, and runtime are already known from the winning suggestion).

    Watch date and time share a single field, reusing WASH's existing
    parse_watch_party_schedule_time() free-text format exactly as
    /watch-party schedule already does, rather than inventing a separate
    two-field date/time input. Event duration reuses WASH's one shared
    duration syntax (parse_duration_text_to_minutes) and is pre-filled
    with the winning suggestion's own runtime when known (still fully
    editable, e.g. to add socializing time) -- see
    default_duration_text.
    """

    def __init__(
        self,
        on_submit: OnScheduleWatchPartyModalSubmit,
        *,
        default_duration_text: str = "",
    ) -> None:
        super().__init__(title="Schedule Watch Party")
        self._submit_callback = on_submit
        self.when_input = discord.ui.TextInput(
            label="Watch date & time",
            placeholder="e.g. 2026-08-01 20:00",
            required=True,
        )
        self.duration_input = discord.ui.TextInput(
            label="Event duration",
            placeholder="e.g. 2h 30m or 150m",
            default=default_duration_text or None,
            required=True,
        )
        self.description_input = discord.ui.TextInput(
            label="Description override (optional)",
            style=discord.TextStyle.paragraph,
            required=False,
        )
        self.add_item(self.when_input)
        self.add_item(self.duration_input)
        self.add_item(self.description_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._submit_callback(
            interaction, self.when_input.value, self.duration_input.value, self.description_input.value
        )


class ChooseWinnerSelectView(discord.ui.View):
    """Tie-breaking picker: which winning title should be scheduled?

    Reuses watch_party_selection_view.py's generic WatchPartySelect
    (id/label/description triples) rather than a duplicate select
    component -- the same "which record should this action target"
    picker /reschedule_watch_party and /cancel_watch_party already use.
    """

    def __init__(self, options: List[Tuple[int, str, str]], on_select: OnWinnerChosenForScheduling) -> None:
        super().__init__(timeout=120)
        self.add_item(
            WatchPartySelect(
                options, on_select, custom_id="wpm_choose_winner_to_schedule_select", placeholder="Choose a title..."
            )
        )
