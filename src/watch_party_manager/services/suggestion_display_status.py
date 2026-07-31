"""Derives a suggestion's admin-facing display status.

The authoritative runtime states are five -- Available, In an Active
Vote, Vote Winner, Watched, Retired.

Only three statuses are persisted on WatchItemStatus (SUGGESTED,
VOTE_WINNER, ARCHIVED). In an Active Vote is computed fresh every time,
from VoteService.get_open_round_for_suggestion() -- never persisted,
since it must automatically revert to Available the moment its round
closes, with no explicit "clear" step required.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from watch_party_manager.domain.watch_item import WatchItem, WatchItemStatus


class SuggestionDisplayStatus(str, Enum):
    """One of the statuses shown on a suggestion's embed/listing."""

    AVAILABLE = "available"
    IN_ACTIVE_VOTE = "in_active_vote"
    VOTE_WINNER = "vote_winner"
    RETIRED = "retired"
    WATCHED = "watched"


# UI Polish (Watch Item Status Presentation): Vote Winner and Retired use
# icons naming what the status actually means -- a trophy for having won
# a vote, an archive box for retired -- rather than generic traffic-light
# colors. Kept as its own dict (rather than only inside
# SUGGESTION_DISPLAY_STATUS_LABELS) so /list's entry lines can prefix a
# bare emoji without repeating the status word per item.
SUGGESTION_DISPLAY_STATUS_EMOJI: dict[SuggestionDisplayStatus, str] = {
    SuggestionDisplayStatus.AVAILABLE: "🟢",
    SuggestionDisplayStatus.IN_ACTIVE_VOTE: "🗳️",
    SuggestionDisplayStatus.VOTE_WINNER: "🏆",
    SuggestionDisplayStatus.RETIRED: "🗄️",
    SuggestionDisplayStatus.WATCHED: "✅",
}

_SUGGESTION_DISPLAY_STATUS_WORDS: dict[SuggestionDisplayStatus, str] = {
    SuggestionDisplayStatus.AVAILABLE: "Available",
    SuggestionDisplayStatus.IN_ACTIVE_VOTE: "In an Active Vote",
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


class VoteRoundLookup:
    """The subset of VoteService this module needs to detect In an Active
    Vote -- deliberately Protocol-based and minimal (see the Protocol
    convention used throughout services/*.py), so this module never
    depends on VoteService's full concrete type.
    """

    def get_open_round_for_suggestion(self, suggestion_id: int): ...


def compute_display_status(watch_item: WatchItem, *, in_active_vote: bool = False) -> SuggestionDisplayStatus:
    """Pure computation: given whether an item is currently nominated in
    an open voting round, resolve its full display status. Split from
    resolve_display_status() so tests never need a real VoteService just
    to exercise this decision table.
    """
    if watch_item.status is WatchItemStatus.WATCHED:
        return SuggestionDisplayStatus.WATCHED
    if watch_item.status is WatchItemStatus.ARCHIVED:
        return SuggestionDisplayStatus.RETIRED
    if watch_item.status is WatchItemStatus.VOTE_WINNER:
        return SuggestionDisplayStatus.VOTE_WINNER
    if in_active_vote:
        return SuggestionDisplayStatus.IN_ACTIVE_VOTE
    return SuggestionDisplayStatus.AVAILABLE


def resolve_display_status(
    watch_item: WatchItem, vote_service: Optional[VoteRoundLookup] = None
) -> SuggestionDisplayStatus:
    """Convenience wrapper: resolve In an Active Vote from a real
    VoteService (or skip the check entirely when not configured --
    matching every other optional-service call site in bot.py) before
    delegating to compute_display_status().

    vote_service defaults to None so existing callers that don't pass one
    keep working unchanged (In an Active Vote simply never applies for
    them).
    """
    in_active_vote = (
        watch_item.id is not None
        and vote_service is not None
        and vote_service.get_open_round_for_suggestion(watch_item.id) is not None
    )
    return compute_display_status(watch_item, in_active_vote=bool(in_active_vote))


def display_status_label(status: SuggestionDisplayStatus) -> str:
    return SUGGESTION_DISPLAY_STATUS_LABELS[status]
