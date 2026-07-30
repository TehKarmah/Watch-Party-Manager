"""Derives a suggestion's admin-facing display status.

Replaces the old Watched-based model with four statuses: Available,
Rotation Cooldown, Vote Winner, Retired ("Vote Winner replaces Watched
for v1" -- WASH knows a suggestion won a vote, not that the group
actually watched it).

Only three of those are persisted on WatchItemStatus (SUGGESTED,
VOTE_WINNER, ARCHIVED) -- Rotation Cooldown is deliberately NOT a fourth
enum member. It's computed fresh every time from
RotationService.is_in_rotation_cooldown(), since it must automatically
revert to Available the moment a fresh rotation begins (see that
method's docstring), with no persisted "clear cooldown" step required.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from watch_party_manager.domain.watch_item import WatchItem, WatchItemStatus


class SuggestionDisplayStatus(str, Enum):
    """One of the statuses shown on a suggestion's embed/listing."""

    AVAILABLE = "available"
    ROTATION_COOLDOWN = "rotation_cooldown"
    VOTE_WINNER = "vote_winner"
    RETIRED = "retired"
    WATCHED = "watched"


# UI Polish (Watch Item Status Presentation): Vote Winner and Retired
# moved from generic traffic-light colors (🟣/🔴) to icons naming what
# the status actually means -- a trophy for having won a vote, an
# archive box for retired -- since 🟣/🔴 carried no inherent meaning
# beyond "not green/yellow". Available/Rotation Cooldown keep their
# existing colors unchanged. Kept as its own dict (rather than only
# inside SUGGESTION_DISPLAY_STATUS_LABELS) so /list's entry lines can
# prefix a bare emoji without repeating the status word per item.
SUGGESTION_DISPLAY_STATUS_EMOJI: dict[SuggestionDisplayStatus, str] = {
    SuggestionDisplayStatus.AVAILABLE: "🟢",
    SuggestionDisplayStatus.ROTATION_COOLDOWN: "🟡",
    SuggestionDisplayStatus.VOTE_WINNER: "🏆",
    SuggestionDisplayStatus.RETIRED: "🗄️",
    SuggestionDisplayStatus.WATCHED: "✅",
}

_SUGGESTION_DISPLAY_STATUS_WORDS: dict[SuggestionDisplayStatus, str] = {
    SuggestionDisplayStatus.AVAILABLE: "Available",
    SuggestionDisplayStatus.ROTATION_COOLDOWN: "Rotation Cooldown",
    SuggestionDisplayStatus.VOTE_WINNER: "Vote Winner",
    SuggestionDisplayStatus.RETIRED: "Retired",
    SuggestionDisplayStatus.WATCHED: "Watched",
}

SUGGESTION_DISPLAY_STATUS_LABELS: dict[SuggestionDisplayStatus, str] = {
    status: f"{SUGGESTION_DISPLAY_STATUS_EMOJI[status]} {_SUGGESTION_DISPLAY_STATUS_WORDS[status]}"
    for status in SuggestionDisplayStatus
}


def format_won_date(won_date: date) -> str:
    """Render a vote-win date as "Month D, YYYY" (e.g. "July 28, 2026") --
    date only, deliberately never a time (WASH only ever records the
    date a vote completed, not a time-of-day). Avoids strftime's
    platform-dependent no-leading-zero-day directives (%-d on
    POSIX/%#d on Windows) by building the day number directly.
    """
    return f"{won_date.strftime('%B')} {won_date.day}, {won_date.year}"


def vote_winner_won_date_line(item: WatchItem) -> Optional[str]:
    """"Won: <date>" for a Vote Winner with a recorded win date, or None.

    Legacy Vote Winners recorded before win dates existed have no
    journey.last_won_date -- this is the one, shared place that decides
    to omit the line gracefully rather than showing a blank/placeholder
    date, reused everywhere a Vote Winner's status is shown.
    """
    won_date = item.journey.last_won_date
    if won_date is None:
        return None
    return f"Won: {format_won_date(won_date)}"


def watched_date_line(item: WatchItem) -> Optional[str]:
    """"Watched: <date>" for the most recently confirmed watch date, or
    None if somehow unset. journey.watch_dates is append-only, so its
    last entry is always the most recent confirmation.
    """
    watch_dates = item.journey.watch_dates
    if not watch_dates:
        return None
    return f"Watched: {format_won_date(watch_dates[-1])}"


def format_display_status_with_won_date(item: WatchItem, status: SuggestionDisplayStatus) -> str:
    """The status label, plus a "Won: <date>"/"Watched: <date>" line for
    Vote Winner/Watched when one was recorded -- the one shared building
    block for every "Status" field/line shown anywhere in WASH (the
    public confirmation embed, /edit_suggestion's summary, and /list).
    """
    label = display_status_label(status)
    if status is SuggestionDisplayStatus.VOTE_WINNER:
        won_line = vote_winner_won_date_line(item)
        return label if won_line is None else f"{label}\n{won_line}"
    if status is SuggestionDisplayStatus.WATCHED:
        watched_line = watched_date_line(item)
        return label if watched_line is None else f"{label}\n{watched_line}"
    return label


class RotationCooldownLookup:
    """The subset of RotationService this module needs (see Protocol
    convention used throughout services/*.py -- e.g. RotationSuggestionSource).
    """

    def is_in_rotation_cooldown(self, watch_item: WatchItem) -> bool: ...


def compute_display_status(watch_item: WatchItem, *, in_rotation_cooldown: bool) -> SuggestionDisplayStatus:
    """Pure computation: given whether an item is currently on Rotation
    Cooldown, resolve its full display status. Split from
    resolve_display_status() so tests never need a real RotationService
    just to exercise this decision table.
    """
    if watch_item.status is WatchItemStatus.WATCHED:
        return SuggestionDisplayStatus.WATCHED
    if watch_item.status is WatchItemStatus.ARCHIVED:
        return SuggestionDisplayStatus.RETIRED
    if watch_item.status is WatchItemStatus.VOTE_WINNER:
        return SuggestionDisplayStatus.VOTE_WINNER
    if in_rotation_cooldown:
        return SuggestionDisplayStatus.ROTATION_COOLDOWN
    return SuggestionDisplayStatus.AVAILABLE


def resolve_display_status(
    watch_item: WatchItem, rotation_service: Optional[RotationCooldownLookup]
) -> SuggestionDisplayStatus:
    """Convenience wrapper: resolve Rotation Cooldown from a real
    RotationService (or skip that check entirely when none is
    configured -- matching every other rotation_service-optional call
    site in bot.py) before delegating to compute_display_status().
    """
    in_rotation_cooldown = (
        rotation_service.is_in_rotation_cooldown(watch_item) if rotation_service is not None else False
    )
    return compute_display_status(watch_item, in_rotation_cooldown=in_rotation_cooldown)


def display_status_label(status: SuggestionDisplayStatus) -> str:
    return SUGGESTION_DISPLAY_STATUS_LABELS[status]
