"""Service for scheduling, rescheduling, and cancelling watch parties.

Deliberately scheduler-agnostic, mirroring VoteService: creating,
rescheduling, and cancelling a watch party here never touches
SchedulerService directly. Keeping the reminder job in sync with a watch
party's current state is watch_party_scheduling.py's job (see
schedule_watch_party_reminder/reschedule_watch_party_reminder/
cancel_watch_party_reminder there), the same separation vote_scheduling.py
already established for voting reminders.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol

from watch_party_manager.domain.watch_party import WatchParty, WatchPartyStatus
from watch_party_manager.persistence.watch_party_repository import JsonWatchPartyRepository


class WatchItemLookup(Protocol):
    """The subset of SuggestionService needed to validate a watch_item_id.

    Kept minimal and Protocol-based, matching the project's existing
    dependency pattern (see SuggestionLookup in vote_service.py), so this
    service depends only on the one capability it actually uses.
    """

    def suggestion_exists(self, suggestion_id: int) -> bool: ...


@dataclass
class WatchPartyResult:
    """Result of a watch-party operation."""

    success: bool
    message: str
    watch_party: Optional[WatchParty] = None


class WatchPartyService:
    """Manages scheduled watch parties, persisted through a watch party repository.

    Business rules enforced here:
      - A watch party must reference a Watch Item that currently exists.
      - A cancelled watch party cannot be rescheduled or cancelled again.
    """

    def __init__(
        self,
        watch_item_lookup: WatchItemLookup,
        repository: Optional[JsonWatchPartyRepository] = None,
    ) -> None:
        """Initialize the service and load any persisted watch parties.

        Args:
            watch_item_lookup: Used to validate that a watch_item_id
                exists before a watch party is scheduled for it.
            repository: The persistence layer to load from and save to.
                Defaults to a JsonWatchPartyRepository using the default
                on-disk location.
        """
        self._watch_item_lookup = watch_item_lookup
        self._repository = repository if repository is not None else JsonWatchPartyRepository()
        load_result = self._repository.load()
        # Keyed by watch party ID; insertion order follows load order,
        # which is the order watch parties were originally created.
        self._watch_parties: dict[int, WatchParty] = {
            watch_party.id: watch_party for watch_party in load_result.watch_parties
        }
        self._next_id = load_result.next_id

    def schedule_watch_party(
        self,
        watch_item_id: int,
        scheduled_at: datetime,
        guild_id: int,
        channel_id: Optional[int] = None,
    ) -> WatchPartyResult:
        """Schedule a new watch party.

        Args:
            watch_item_id: The Watch Item this party is for. Must
                currently exist.
            scheduled_at: When the watch party starts. Must be
                timezone-aware (enforced by WatchParty itself).
            guild_id: The Discord guild this watch party belongs to.
            channel_id: The Discord channel or thread to post the
                reminder to, if already known.

        Returns:
            WatchPartyResult indicating success or failure. On success,
            watch_party is the newly created watch party.
        """
        if not self._watch_item_lookup.suggestion_exists(watch_item_id):
            return WatchPartyResult(
                success=False,
                message="That watch item doesn't exist.",
            )

        watch_party = WatchParty(
            id=self._next_id,
            watch_item_id=watch_item_id,
            scheduled_at=scheduled_at,
            guild_id=guild_id,
            channel_id=channel_id,
        )
        self._next_id += 1
        self._watch_parties[watch_party.id] = watch_party
        self._save()
        return WatchPartyResult(
            success=True,
            message=f"Watch party #{watch_party.id} scheduled.",
            watch_party=watch_party,
        )

    def get_watch_party(self, watch_party_id: int) -> Optional[WatchParty]:
        """Get a watch party by ID.

        Args:
            watch_party_id: The watch party ID to look up.

        Returns:
            The matching WatchParty, or None if no watch party has that ID.
        """
        return self._watch_parties.get(watch_party_id)

    def get_current_watch_party(self) -> Optional[WatchParty]:
        """Get the soonest-upcoming scheduled watch party, if any.

        WatchPartyService does not enforce only one scheduled watch party
        at a time (unlike VoteService's single-open-round rule), so this
        is a display convenience for "/watch_party_status" rather than an
        invariant: it's a deterministic pick (the closest scheduled_at
        among non-cancelled watch parties), not proof that only one exists.

        Returns:
            The scheduled watch party with the earliest scheduled_at, or
            None if none are currently scheduled (none exist, or all have
            been cancelled).
        """
        scheduled = [
            watch_party
            for watch_party in self._watch_parties.values()
            if watch_party.status == WatchPartyStatus.SCHEDULED
        ]
        if not scheduled:
            return None
        return min(scheduled, key=lambda watch_party: watch_party.scheduled_at)

    def reschedule_watch_party(
        self, watch_party_id: int, new_scheduled_at: datetime
    ) -> WatchPartyResult:
        """Change when an existing watch party starts.

        Args:
            watch_party_id: The watch party to reschedule.
            new_scheduled_at: The new start time. Must be timezone-aware.

        Returns:
            WatchPartyResult indicating success or failure. On success,
            watch_party is the updated watch party.
        """
        watch_party = self._watch_parties.get(watch_party_id)
        if watch_party is None:
            return WatchPartyResult(success=False, message="That watch party doesn't exist.")

        if watch_party.status == WatchPartyStatus.CANCELLED:
            return WatchPartyResult(
                success=False,
                message="That watch party has been cancelled and cannot be rescheduled.",
            )

        updated = watch_party.with_changes(scheduled_at=new_scheduled_at)
        self._watch_parties[watch_party_id] = updated
        self._save()
        return WatchPartyResult(
            success=True,
            message=f"Watch party #{watch_party_id} rescheduled.",
            watch_party=updated,
        )

    def cancel_watch_party(self, watch_party_id: int) -> WatchPartyResult:
        """Cancel a scheduled watch party.

        Args:
            watch_party_id: The watch party to cancel.

        Returns:
            WatchPartyResult indicating success or failure.
        """
        watch_party = self._watch_parties.get(watch_party_id)
        if watch_party is None:
            return WatchPartyResult(success=False, message="That watch party doesn't exist.")

        if watch_party.status == WatchPartyStatus.CANCELLED:
            return WatchPartyResult(success=False, message="That watch party is already cancelled.")

        watch_party.status = WatchPartyStatus.CANCELLED
        self._save()
        return WatchPartyResult(
            success=True,
            message=f"Watch party #{watch_party_id} cancelled.",
            watch_party=watch_party,
        )

    def _save(self) -> None:
        """Persist the current watch parties via the repository."""
        self._repository.save(self._watch_parties.values(), self._next_id)
