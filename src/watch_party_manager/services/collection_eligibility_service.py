"""The one authoritative implementation of "what suggestions are eligible
right now?", computed entirely from WatchItemStatus and VoteService. The
five authoritative, user-facing states are Available, In an Active Vote,
Vote Winner, Watched, and Retired.

Eligible Pool: "available" is the pool the Eligible Pool Warning and
every other user-facing count is based on (see
services/eligible_pool_warning_service.py) -- every suggestion that is
neither currently nominated, nor a Vote Winner, Watched, nor Retired.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, Tuple

from watch_party_manager.domain.watch_item import WatchItem, WatchItemStatus


class EligibilitySuggestionSource(Protocol):
    def get_suggestions_for_database(self, database_id: int, *, include_archived: bool = False) -> list: ...


class VoteRoundSource(Protocol):
    """The subset of VoteService this module needs to detect which
    suggestions are currently nominated in an open round -- deliberately
    minimal and Protocol-based, matching suggestion_display_status.py's
    own VoteRoundLookup convention.
    """

    def get_open_round(self, database_id: Optional[int] = None): ...


@dataclass(frozen=True)
class CollectionEligibility:
    """The authoritative breakdown of one collection's suggestions.

    available: the Eligible Pool -- selectable for a future vote right
        now. Mode-agnostic; see module docstring.
    in_active_vote: currently nominated in this collection's open voting
        round (empty whenever no round is open).
    vote_winners / retired / watched: terminal-state suggestions,
        included for Collection Health's reconciliation, never part of
        the eligible pool. watched is populated only by the explicit
        Watched action -- never automatically from winning a vote or
        retiring.
    """

    database_id: int
    available: Tuple[WatchItem, ...]
    in_active_vote: Tuple[WatchItem, ...]
    vote_winners: Tuple[WatchItem, ...]
    retired: Tuple[WatchItem, ...]
    watched: Tuple[WatchItem, ...] = ()

    @property
    def active(self) -> Tuple[WatchItem, ...]:
        """Active = Available + In an Active Vote (Collection Health's
        own reconciliation identity)."""
        return self.available + self.in_active_vote

    @property
    def total(self) -> int:
        """Total = Active + Vote Winners + Retired + Watched."""
        return len(self.active) + len(self.vote_winners) + len(self.retired) + len(self.watched)

    @property
    def eligible_pool_count(self) -> int:
        """The Eligible Pool Warning's own "eligible_items" count."""
        return len(self.available)


class CollectionEligibilityService:
    """THE authoritative eligibility resolver. See module docstring."""

    def __init__(self, suggestion_service: EligibilitySuggestionSource, vote_service: Optional[VoteRoundSource]) -> None:
        self._suggestion_service = suggestion_service
        self._vote_service = vote_service

    def get_eligibility(self, database_id: int) -> CollectionEligibility:
        """Read-only snapshot of current eligibility. Never mutates
        anything -- there is nothing here to advance as a side effect of
        merely checking.
        """
        all_items = self._suggestion_service.get_suggestions_for_database(database_id, include_archived=True)
        retired = tuple(item for item in all_items if item.status is WatchItemStatus.ARCHIVED)
        vote_winners = tuple(item for item in all_items if item.status is WatchItemStatus.VOTE_WINNER)
        watched = tuple(item for item in all_items if item.status is WatchItemStatus.WATCHED)
        active_items = [item for item in all_items if item.status is WatchItemStatus.SUGGESTED]

        nominated_ids = self._open_round_candidate_ids(database_id)
        in_active_vote = tuple(item for item in active_items if item.id in nominated_ids)
        available = tuple(item for item in active_items if item.id not in nominated_ids)

        return CollectionEligibility(
            database_id=database_id,
            available=available,
            in_active_vote=in_active_vote,
            vote_winners=vote_winners,
            retired=retired,
            watched=watched,
        )

    def _open_round_candidate_ids(self, database_id: int) -> frozenset:
        if self._vote_service is None:
            return frozenset()
        open_round = self._vote_service.get_open_round(database_id)
        if open_round is None:
            return frozenset()
        return frozenset(open_round.candidate_suggestion_ids)
