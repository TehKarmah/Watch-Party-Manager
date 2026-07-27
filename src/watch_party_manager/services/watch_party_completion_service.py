"""Service that completes a scheduled watch party's lifecycle by ID.

Mirrors VoteCompletionService's role for voting rounds: a dedicated
completion service rather than logic living inside WatchPartyService or
SuggestionService directly, since completing a watch party genuinely
needs both worlds -- transitioning the watch party itself to COMPLETED
(WatchPartyService) and marking its Watch Item Watched, with a recorded
watch date (SuggestionService). Combining them here keeps that
cross-cutting concern out of both individual services and out of the
Discord layer entirely.

Detecting *when* a watch party is due is the scheduler's responsibility
(see WatchPartyCompletionJobHandler and the watch_party_completion job
type) -- this service only completes a watch party it's told to, by ID.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional, Protocol

from watch_party_manager.domain.watch_item import WatchItem
from watch_party_manager.domain.watch_party import WatchParty
from watch_party_manager.services.watch_party_service import WatchPartyService


class WatchRecorder(Protocol):
    """The subset of SuggestionService needed to record a completed watch.

    Kept minimal and Protocol-based, matching the project's existing
    dependency pattern (see JourneyRecorder in vote_completion_service.py),
    so this service depends only on the one capability it actually uses.
    """

    def record_watch(self, suggestion_id: int, watch_date: date) -> bool: ...

    def get_suggestion(self, suggestion_id: int) -> Optional[WatchItem]: ...


@dataclass
class WatchPartyCompletionResult:
    """What happened when a scheduled watch party was completed."""

    watch_party: WatchParty
    watch_item: Optional[WatchItem]


class WatchPartyCompletionService:
    """Completes a scheduled watch party's lifecycle, given its ID.

    Completing a watch party means:
      1. Transitioning it from SCHEDULED to COMPLETED via
         WatchPartyService.mark_completed() -- also what makes this
         idempotent (see that method's docstring).
      2. Marking its Watch Item Watched and recording the watch date via
         SuggestionService.record_watch() -- the single place that
         actually produces journey.record_watch_date(), completing the
         "future watch-history milestone" earlier project documentation
         anticipated.

    This service never sends any Discord messages, and never asks
    whether the watch party "really" happened -- goal 6 is explicit:
    assume it did, with no confirmation dialog. Correcting a wrong
    automatic completion afterward is a separate, explicit workflow (see
    bot.py's Watch Party Lifecycle correction handling): reverting the
    suggestion's status, removing the recorded watch date, and
    rescheduling all reuse existing mechanisms rather than anything here.
    """

    def __init__(self, watch_party_service: WatchPartyService, watch_recorder: WatchRecorder) -> None:
        """Initialize the completion service.

        Args:
            watch_party_service: The service to check and complete the
                watch party through.
            watch_recorder: Used to mark the associated Watch Item
                Watched and record its watch date. SuggestionService
                satisfies this.
        """
        self._watch_party_service = watch_party_service
        self._watch_recorder = watch_recorder

    def complete_watch_party(self, watch_party_id: int) -> Optional[WatchPartyCompletionResult]:
        """Complete one specific watch party by ID, if it's still scheduled.

        The caller is expected to already know watch_party_id is due --
        for WatchPartyCompletionJobHandler, that's enforced by
        SchedulerService before the watch_party_completion job is ever
        claimed, so this never consults ends_at itself.

        Safe to call at any time, repeatedly, including after the watch
        party has already been completed -- WatchPartyService.
        mark_completed() rejects a watch party that isn't currently
        SCHEDULED, and that rejection is what makes this naturally
        idempotent.

        Args:
            watch_party_id: The watch party to complete.

        Returns:
            A WatchPartyCompletionResult if the watch party was completed
            by this call, or None if it doesn't exist or wasn't
            currently scheduled (already completed, or cancelled).
        """
        watch_party = self._watch_party_service.get_watch_party(watch_party_id)
        if watch_party is None:
            return None

        complete_result = self._watch_party_service.mark_completed(watch_party.id)
        if not complete_result.success:
            return None
        completed_watch_party = complete_result.watch_party

        # The watch party's own expected end time is used (rather than
        # "now") so the recorded history reflects when the watch party
        # was actually supposed to finish, not whenever the bot happened
        # to notice -- these can differ if the bot was offline past the
        # deadline, mirroring VoteCompletionService's identical reasoning
        # for using closes_at over "now". ends_at is always set here in
        # practice (WatchPartyCompletionJobHandler only ever completes a
        # watch party that has a duration, since that's the only way a
        # watch_party_completion job gets scheduled at all), but a "now"
        # fallback defends a direct call for one with no known duration.
        completion_date = (
            completed_watch_party.ends_at.date()
            if completed_watch_party.ends_at is not None
            else datetime.now(timezone.utc).date()
        )
        self._watch_recorder.record_watch(completed_watch_party.watch_item_id, completion_date)
        watch_item = self._watch_recorder.get_suggestion(completed_watch_party.watch_item_id)

        return WatchPartyCompletionResult(watch_party=completed_watch_party, watch_item=watch_item)
