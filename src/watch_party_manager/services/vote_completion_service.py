"""Service that completes a voting round's lifecycle by ID.

This is a dedicated completion service rather than logic living inside
VoteService, SuggestionService, or a scheduler job handler: completing a
round genuinely needs both worlds -- closing the round and computing
winners (VoteService) and updating the winning Watch Item(s)' history
(SuggestionService). Combining them here keeps that cross-cutting concern
out of both individual services and out of the Discord layer entirely,
mirroring the same reasoning NomineeSelectionService already established
in this project for the same kind of two-service concern.

Detecting *when* a round is due is the scheduler's responsibility (see
CloseVoteJobHandler and the close_vote job type) -- as of FR-019, this
service only completes a round it's told to, by ID; it no longer
searches for or due-checks the open round itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import List, Optional, Protocol

from watch_party_manager.domain.vote import VoteRound
from watch_party_manager.services.vote_service import StandingsEntry, VoteService


class JourneyRecorder(Protocol):
    """The subset of SuggestionService needed to record a win.

    Kept minimal and Protocol-based, matching the project's existing
    dependency pattern (see SuggestionLookup in vote_service.py), so this
    service depends only on the one capability it actually uses.
    """

    def record_vote_win(self, suggestion_id: int, won_date: date) -> bool: ...


@dataclass
class VoteCompletionResult:
    """What happened when an expired voting round was completed."""

    vote_round: VoteRound
    winning_suggestion_ids: List[int] = field(default_factory=list)
    standings: List[StandingsEntry] = field(default_factory=list)
    total_votes_cast: int = 0


class VoteCompletionService:
    """Completes a voting round's lifecycle, given its round ID.

    Completing a round means:
      1. Closing it via VoteService.close_round(), which blocks any
         further votes (cast_vote() only ever accepts votes for the
         round returned by get_open_round(), and a closed round is never
         returned there).
      2. Determining the winner(s) by reusing
         VoteService.get_current_winners() -- winner selection is never
         recomputed or duplicated here.
      3. Updating each winning suggestion's WatchItemJourney via
         SuggestionService.record_vote_win().

    This service never sends any Discord messages. CloseVoteJobHandler
    (the sole caller as of FR-019) is responsible for turning a
    VoteCompletionResult into an announcement and delivering it -- see
    build_vote_completion_announcement() in vote_announcement_formatter.py.
    """

    def __init__(self, vote_service: VoteService, journey_recorder: JourneyRecorder) -> None:
        """Initialize the completion service.

        Args:
            vote_service: The vote service to check, close rounds on, and
                compute winners/standings through.
            journey_recorder: Used to record each winner's watch history.
                SuggestionService satisfies this.
        """
        self._vote_service = vote_service
        self._journey_recorder = journey_recorder

    def complete_round(self, round_id: int) -> Optional[VoteCompletionResult]:
        """Close and finalize one specific round by ID, if it's still open.

        The caller is expected to already know round_id is due -- for
        CloseVoteJobHandler, that's enforced by SchedulerService before
        the close_vote job is ever claimed, so this never consults
        closes_at itself. This is the single place "close, determine
        winner(s), update Watch Item Journey, and calculate standings" is
        implemented; callers never duplicate it.

        Safe to call at any time, repeatedly, including after the round
        has already been completed -- whether by an earlier call in this
        same process or a previous run before a restart.
        VoteService.close_round() rejects a round that's already closed,
        and that rejection is what makes this method naturally
        idempotent, without needing any separate "already announced"
        flag: if closing fails, there is nothing left to do, so this
        returns None rather than re-running winner calculation or
        re-updating watch history for a round already fully processed.

        Args:
            round_id: The round to close and finalize.

        Returns:
            A VoteCompletionResult if the round was completed by this
            call, or None if it doesn't exist or was already closed.
        """
        vote_round = self._vote_service.get_round(round_id)
        if vote_round is None:
            return None

        close_result = self._vote_service.close_round(vote_round.id)
        if not close_result.success:
            return None

        winner_result = self._vote_service.get_current_winners(vote_round.id)
        winning_suggestion_ids = winner_result.winning_suggestion_ids if winner_result.success else []

        # The round's own scheduled end date is used (rather than "now")
        # so the recorded history reflects when the vote actually
        # concluded, not whenever the bot happened to notice -- these can
        # differ if the bot was offline past the deadline (see restart
        # safety), and using the deadline keeps the result deterministic
        # and testable. A close_vote job's round always has closes_at set
        # (build_close_vote_job() never schedules one otherwise), but a
        # direct complete_round() call for a round without one is
        # defended with a "now" fallback rather than raising.
        completion_date = (
            vote_round.closes_at.date()
            if vote_round.closes_at is not None
            else datetime.now(timezone.utc).date()
        )
        for suggestion_id in winning_suggestion_ids:
            self._journey_recorder.record_vote_win(suggestion_id, completion_date)

        standings_result = self._vote_service.calculate_standings(vote_round.id)
        standings = standings_result.standings if standings_result.success else []

        return VoteCompletionResult(
            vote_round=vote_round,
            winning_suggestion_ids=winning_suggestion_ids,
            standings=standings,
            total_votes_cast=len(vote_round.votes),
        )
