"""Service for managing vote rounds and votes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Protocol

from watch_party_manager.domain.vote import (
    MAX_VOTE_CHANGES,
    MIN_CANDIDATES_FOR_A_ROUND,
    VoteRecord,
    VoteRound,
    VoteRoundStatus,
    VoteVisibility,
)
from watch_party_manager.persistence.vote_repository import JsonVoteRepository


class SuggestionLookup(Protocol):
    """Anything that can confirm whether a suggestion ID currently exists,
    and how many suggestions currently exist.

    SuggestionService satisfies this by way of its suggestion_exists() and
    suggestion_count() methods. Keeping this as a small Protocol (rather
    than importing SuggestionService directly) means VoteService only
    depends on the capabilities it actually needs, and tests can supply a
    lightweight fake.
    """

    def suggestion_exists(self, suggestion_id: int) -> bool: ...

    def suggestion_count(self) -> int: ...


@dataclass
class VoteResult:
    """Result of a voting operation that doesn't need to return a round."""

    success: bool
    message: str


@dataclass
class VoteRoundResult:
    """Result of an operation that creates or otherwise concerns a round."""

    success: bool
    message: str
    vote_round: Optional[VoteRound] = None


@dataclass
class StandingsEntry:
    """A single suggestion's vote count within a round's standings."""

    suggestion_id: int
    vote_count: int


@dataclass
class StandingsResult:
    """Result of calculating standings for a voting round."""

    success: bool
    message: str
    standings: List[StandingsEntry] = field(default_factory=list)


@dataclass
class WinnerResult:
    """Result of calculating the winner(s) of a voting round.

    winning_suggestion_ids holds every suggestion tied for the highest vote
    count. It's empty (with success=True) when the round has no votes yet;
    that's a valid outcome, not an error.
    """

    success: bool
    message: str
    winning_suggestion_ids: List[int] = field(default_factory=list)


class VoteService:
    """Manages vote rounds and votes, persisted through a vote repository.

    Business rules enforced here:
      - Only one round may be open at a time.
      - A member has at most one active vote per round.
      - A member may change that vote at most once (MAX_VOTE_CHANGES).
      - Voting for the suggestion a member already voted for is a no-op:
        it doesn't count as a change and isn't treated as an error either,
        it's simply reported back as already being their vote.
    """

    def __init__(
        self,
        suggestion_lookup: SuggestionLookup,
        repository: Optional[JsonVoteRepository] = None,
    ) -> None:
        """Initialize the vote service and load any persisted rounds.

        Args:
            suggestion_lookup: Used to validate that a suggestion ID exists
                before a vote for it is accepted.
            repository: The persistence layer to load from and save to.
                Defaults to a JsonVoteRepository using the default on-disk
                location.
        """
        self._suggestion_lookup = suggestion_lookup
        self._repository = repository if repository is not None else JsonVoteRepository()
        load_result = self._repository.load()
        # Keyed by round ID; insertion order follows the order rounds were
        # loaded in, which is the order they were originally created.
        self._rounds: dict[int, VoteRound] = {
            vote_round.id: vote_round for vote_round in load_result.rounds
        }
        self._next_round_id = load_result.next_round_id

    def create_round(
        self,
        visibility: VoteVisibility = VoteVisibility.VISIBLE,
        closes_at: Optional[datetime] = None,
        candidate_suggestion_ids: Optional[List[int]] = None,
        database_id: Optional[int] = None,
    ) -> VoteRoundResult:
        """Open a new voting round.

        Args:
            visibility: Whether individual votes are visible to other
                members. Defaults to visible.
            closes_at: Optional deadline for the round.
            candidate_suggestion_ids: The exact nominees eligible in this round.
                When omitted, legacy service callers retain the previous behavior.
            database_id: The suggestion database this round belongs to, when known.

        Returns:
            VoteRoundResult. Fails if a round is already open (only one
            round may be open at a time), or if there currently aren't
            enough suggestions to choose between.
        """
        if self.get_open_round() is not None:
            return VoteRoundResult(success=False, message="A voting round is already open.")

        if candidate_suggestion_ids is not None:
            candidate_ids = list(candidate_suggestion_ids)
            if any(not isinstance(candidate_id, int) or isinstance(candidate_id, bool) or candidate_id <= 0 for candidate_id in candidate_ids):
                return VoteRoundResult(
                    success=False,
                    message="Nominee IDs must be positive integers.",
                )
            if len(candidate_ids) < MIN_CANDIDATES_FOR_A_ROUND:
                return VoteRoundResult(
                    success=False,
                    message=(
                        f"At least {MIN_CANDIDATES_FOR_A_ROUND} nominees are needed "
                        "to start a voting round."
                    ),
                )
            if len(candidate_ids) != len(set(candidate_ids)):
                return VoteRoundResult(success=False, message="Nominee IDs must be unique.")
            missing = [candidate_id for candidate_id in candidate_ids if not self._suggestion_lookup.suggestion_exists(candidate_id)]
            if missing:
                return VoteRoundResult(success=False, message="One or more selected nominees no longer exist.")
        else:
            candidate_ids = []
            if self._suggestion_lookup.suggestion_count() < MIN_CANDIDATES_FOR_A_ROUND:
                return VoteRoundResult(
                    success=False,
                    message=(
                        f"At least {MIN_CANDIDATES_FOR_A_ROUND} suggestions are needed "
                        "to start a voting round."
                    ),
                )

        new_round = VoteRound(
            id=self._next_round_id,
            status=VoteRoundStatus.OPEN,
            visibility=visibility,
            closes_at=closes_at,
            candidate_suggestion_ids=candidate_ids,
            database_id=database_id,
        )
        self._next_round_id += 1
        self._rounds[new_round.id] = new_round
        self._save()
        return VoteRoundResult(
            success=True,
            message=f"Voting round {new_round.id} is now open.",
            vote_round=new_round,
        )

    def get_open_round(self) -> Optional[VoteRound]:
        """Get the currently open voting round, if any.

        Returns:
            The open VoteRound, or None if no round is open.
        """
        for vote_round in self._rounds.values():
            if vote_round.status == VoteRoundStatus.OPEN:
                return vote_round
        return None

    def get_latest_round(self) -> Optional[VoteRound]:
        """Get the most recently created voting round, open or closed.

        Round IDs are assigned sequentially and never reused, so the round
        with the highest ID is always the most recently created one. This
        is what /vote_status shows when no round ID is specified.

        Returns:
            The most recently created VoteRound, or None if no round has
            ever been created.
        """
        if not self._rounds:
            return None
        return max(self._rounds.values(), key=lambda vote_round: vote_round.id)

    def get_recent_closed_rounds(
        self, limit: int, database_id: Optional[int] = None
    ) -> List[VoteRound]:
        """Get the most recently closed voting rounds, most recent first.

        Used by nominee selection to see which suggestions were recently
        nominated or won, so they can be deprioritized in favor of
        rotation. Round IDs are sequential and never reused, so ordering
        by ID descending is equivalent to ordering by recency.

        Args:
            limit: Maximum number of rounds to return.
            database_id: Optional database ID to scope history to. Legacy rounds
                without a database ID are excluded from database-specific results.

        Returns:
            Up to `limit` closed rounds, most recently created first. Open
            rounds are never included, since they have no final nominee
            list or determined outcome that's safe to compare against.
        """
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        if database_id is not None and (not isinstance(database_id, int) or isinstance(database_id, bool) or database_id <= 0):
            raise ValueError("database_id must be a positive integer when provided")

        closed_rounds = [
            vote_round
            for vote_round in self._rounds.values()
            if vote_round.status == VoteRoundStatus.CLOSED
            and (database_id is None or vote_round.database_id == database_id)
        ]
        closed_rounds.sort(key=lambda vote_round: vote_round.id, reverse=True)
        return closed_rounds[:limit]

    def get_round(self, round_id: int) -> Optional[VoteRound]:
        """Get a voting round by ID.

        Args:
            round_id: The round ID to look up.

        Returns:
            The matching VoteRound, or None if no round has that ID.
        """
        return self._rounds.get(round_id)

    def attach_message_reference(
        self, round_id: int, guild_id: int, channel_id: int, message_id: int
    ) -> bool:
        """Record where a round's public voting post lives.

        Discord doesn't hand back a new message's ID until after it's been
        sent, so this exists to backfill it onto a round that was just
        created moments earlier in the same command.

        Args:
            round_id: The round to update.
            guild_id: The Discord guild the voting post was sent in.
            channel_id: The Discord channel or thread the voting post was
                sent in.
            message_id: The Discord message ID of the voting post.

        Returns:
            True if a matching round was found and updated, False if no
            round has that ID.
        """
        vote_round = self._rounds.get(round_id)
        if vote_round is None:
            return False

        vote_round.guild_id = guild_id
        vote_round.channel_id = channel_id
        vote_round.message_id = message_id
        self._save()
        return True

    def cast_vote(self, discord_user_id: int, suggestion_id: int) -> VoteResult:
        """Cast or change a member's vote in the currently open round.

        Handles both a member's first vote and their one allowed change:
        if they haven't voted yet, this records their vote; if they have,
        and haven't used their one change yet, this updates it.

        Args:
            discord_user_id: The voting member's Discord user ID.
            suggestion_id: The suggestion ID they're voting for.

        Returns:
            VoteResult indicating success or failure.
        """
        vote_round = self.get_open_round()
        if vote_round is None:
            return VoteResult(success=False, message="There's no open voting round right now.")

        if not self._suggestion_lookup.suggestion_exists(suggestion_id):
            return VoteResult(success=False, message="That suggestion ID doesn't exist.")

        if vote_round.candidate_suggestion_ids and suggestion_id not in vote_round.candidate_suggestion_ids:
            return VoteResult(success=False, message="That suggestion is not a nominee in this voting round.")

        now = datetime.now(timezone.utc)
        existing_vote = vote_round.votes.get(discord_user_id)

        if existing_vote is None:
            vote_round.votes[discord_user_id] = VoteRecord(
                discord_user_id=discord_user_id,
                suggestion_id=suggestion_id,
                original_suggestion_id=suggestion_id,
                first_voted_at=now,
                last_voted_at=now,
            )
            self._save()
            return VoteResult(success=True, message="Your vote has been recorded.")

        if existing_vote.suggestion_id == suggestion_id:
            # Re-voting for the same suggestion doesn't consume a change
            # and doesn't touch the record at all.
            return VoteResult(success=False, message="You already voted for that suggestion.")

        if existing_vote.changes_used >= MAX_VOTE_CHANGES:
            return VoteResult(
                success=False,
                message="You've already used your one vote change for this round.",
            )

        vote_round.votes[discord_user_id] = VoteRecord(
            discord_user_id=discord_user_id,
            suggestion_id=suggestion_id,
            original_suggestion_id=existing_vote.original_suggestion_id,
            first_voted_at=existing_vote.first_voted_at,
            last_voted_at=now,
            changes_used=existing_vote.changes_used + 1,
        )
        self._save()
        return VoteResult(success=True, message="Your vote has been changed.")

    def close_round(self, round_id: int) -> VoteResult:
        """Close a voting round.

        Args:
            round_id: The round to close.

        Returns:
            VoteResult indicating success or failure. Fails for a round
            that is already CLOSED or has been CANCELLED -- only an OPEN
            round can be closed, so a cancelled round (see cancel_round)
            can never be resurrected into a normal completion with a
            winner by a late-firing close_vote job or a repeated "End
            Now" click.
        """
        vote_round = self._rounds.get(round_id)
        if vote_round is None:
            return VoteResult(success=False, message="That voting round doesn't exist.")

        if vote_round.status != VoteRoundStatus.OPEN:
            return VoteResult(
                success=False, message=f"That voting round is already {vote_round.status.value}."
            )

        vote_round.status = VoteRoundStatus.CLOSED
        self._save()
        return VoteResult(success=True, message=f"Voting round {round_id} is now closed.")

    def reschedule_round(self, round_id: int, new_closes_at: datetime) -> VoteRoundResult:
        """Change when an open voting round closes.

        Preserves the round's identity and every submitted vote -- only
        closes_at is updated. Existing scheduler jobs are not this
        service's concern (see scheduler.vote_scheduling.reschedule_vote_jobs,
        called by the caller after this succeeds).

        Args:
            round_id: The round to reschedule.
            new_closes_at: The new closing time. Must be timezone-aware
                (enforced by VoteRound itself); "must be in the future"
                is a command-layer concern (see bot.py's
                parse_vote_end_time), not re-validated here.

        Returns:
            VoteRoundResult indicating success or failure. Fails if the
            round doesn't exist or is not currently open.
        """
        vote_round = self._rounds.get(round_id)
        if vote_round is None:
            return VoteRoundResult(success=False, message="That voting round doesn't exist.")

        if vote_round.status != VoteRoundStatus.OPEN:
            return VoteRoundResult(
                success=False,
                message=f"That voting round is already {vote_round.status.value} and cannot be rescheduled.",
            )

        vote_round.closes_at = new_closes_at
        self._save()
        return VoteRoundResult(
            success=True,
            message=f"Voting round {round_id} has been rescheduled.",
            vote_round=vote_round,
        )

    def cancel_round(self, round_id: int) -> VoteRoundResult:
        """Cancel an open voting round without determining a winner.

        Preserves the round and every submitted ballot -- only status
        changes to CANCELLED. A cancelled round is excluded from
        get_open_round() (same mechanism CLOSED already relies on), so
        cast_vote() naturally rejects further votes with no extra check
        needed here.

        Args:
            round_id: The round to cancel.

        Returns:
            VoteRoundResult indicating success or failure. Fails if the
            round doesn't exist or is not currently open -- repeated
            cancellation of the same round is therefore a safe, idempotent
            no-op from the caller's perspective (see bot.py's
            handle_cancel_vote_now_completion).
        """
        vote_round = self._rounds.get(round_id)
        if vote_round is None:
            return VoteRoundResult(success=False, message="That voting round doesn't exist.")

        if vote_round.status != VoteRoundStatus.OPEN:
            return VoteRoundResult(
                success=False,
                message=f"That voting round is already {vote_round.status.value}.",
            )

        vote_round.status = VoteRoundStatus.CANCELLED
        self._save()
        return VoteRoundResult(
            success=True,
            message=f"Voting round {round_id} has been cancelled.",
            vote_round=vote_round,
        )

    def remove_member_vote(self, round_id: int, discord_user_id: int) -> VoteResult:
        """Remove a member's vote entirely, letting them cast a fresh vote.

        This is one of two admin-facing reset behaviors (see also
        reset_member_vote_changes). Use this when the member's existing
        vote should be undone completely rather than just re-allowing a
        change to it.

        Args:
            round_id: The round the vote belongs to.
            discord_user_id: The member whose vote should be removed.

        Returns:
            VoteResult indicating success or failure.
        """
        vote_round = self._rounds.get(round_id)
        if vote_round is None:
            return VoteResult(success=False, message="That voting round doesn't exist.")

        if discord_user_id not in vote_round.votes:
            return VoteResult(success=False, message="That member hasn't voted in this round.")

        del vote_round.votes[discord_user_id]
        self._save()
        return VoteResult(success=True, message="The member's vote has been removed.")

    def reset_member_vote_changes(self, round_id: int, discord_user_id: int) -> VoteResult:
        """Give a member back their one allowed vote change.

        This is the other admin-facing reset behavior (see also
        remove_member_vote). Use this when the member's current vote should
        stay in place, but they should be allowed to change it again.

        Args:
            round_id: The round the vote belongs to.
            discord_user_id: The member whose change allowance should reset.

        Returns:
            VoteResult indicating success or failure.
        """
        vote_round = self._rounds.get(round_id)
        if vote_round is None:
            return VoteResult(success=False, message="That voting round doesn't exist.")

        existing_vote = vote_round.votes.get(discord_user_id)
        if existing_vote is None:
            return VoteResult(success=False, message="That member hasn't voted in this round.")

        existing_vote.changes_used = 0
        self._save()
        return VoteResult(
            success=True,
            message="The member's vote change allowance has been reset.",
        )

    def calculate_standings(self, round_id: int) -> StandingsResult:
        """Tally votes by current suggestion_id for a round.

        Works for both open and closed rounds — standings are just a tally
        of the votes as they currently stand, regardless of whether the
        round is still accepting them. A member's original_suggestion_id is
        never counted; only where their vote currently stands matters.

        Only suggestions that received at least one vote appear here.
        VoteService only has a way to check whether a specific suggestion
        ID exists (SuggestionLookup.suggestion_exists), not to list every
        current suggestion, so it can't cleanly fill in zero-vote entries
        for suggestions nobody voted for.

        Args:
            round_id: The round to tally.

        Returns:
            StandingsResult with entries sorted by vote count descending,
            then by suggestion ID ascending to break ties deterministically.
            A failure result if the round doesn't exist.
        """
        vote_round = self._rounds.get(round_id)
        if vote_round is None:
            return StandingsResult(success=False, message="That voting round doesn't exist.")

        vote_counts: dict[int, int] = {}
        for vote in vote_round.votes.values():
            vote_counts[vote.suggestion_id] = vote_counts.get(vote.suggestion_id, 0) + 1

        standings = [
            StandingsEntry(suggestion_id=suggestion_id, vote_count=vote_count)
            for suggestion_id, vote_count in vote_counts.items()
        ]
        standings.sort(key=lambda entry: (-entry.vote_count, entry.suggestion_id))

        return StandingsResult(success=True, message="Standings calculated.", standings=standings)

    def get_current_winners(self, round_id: int) -> WinnerResult:
        """Determine which suggestion(s) currently have the most votes.

        This is a pure calculation: it does not close the round, and it
        does not write to VoteRound.winning_suggestion_id. That field can
        only hold one ID, so it can't accurately represent a tie. Winners
        are only ever reported back here, never persisted, until that
        field's design is revisited.

        Args:
            round_id: The round to evaluate.

        Returns:
            WinnerResult with every suggestion tied for the highest vote
            count, in ascending suggestion ID order. Empty (but still
            success=True) if no votes have been cast. A failure result if
            the round doesn't exist.
        """
        standings_result = self.calculate_standings(round_id)
        if not standings_result.success:
            return WinnerResult(success=False, message=standings_result.message)

        if not standings_result.standings:
            return WinnerResult(success=True, message="No votes have been cast yet.")

        top_vote_count = standings_result.standings[0].vote_count
        winning_suggestion_ids = sorted(
            entry.suggestion_id
            for entry in standings_result.standings
            if entry.vote_count == top_vote_count
        )
        return WinnerResult(
            success=True,
            message="Winner(s) calculated.",
            winning_suggestion_ids=winning_suggestion_ids,
        )

    def _save(self) -> None:
        """Persist the current voting state via the repository."""
        self._repository.save(self._rounds.values(), self._next_round_id)
