"""Service for managing vote rounds and votes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol

from watch_party_manager.domain.vote import (
    MAX_VOTE_CHANGES,
    VoteRecord,
    VoteRound,
    VoteRoundStatus,
    VoteVisibility,
)
from watch_party_manager.persistence.vote_repository import JsonVoteRepository


class SuggestionLookup(Protocol):
    """Anything that can confirm whether a suggestion ID currently exists.

    SuggestionService satisfies this by way of its suggestion_exists()
    method. Keeping this as a small Protocol (rather than importing
    SuggestionService directly) means VoteService only depends on the one
    capability it actually needs, and tests can supply a lightweight fake.
    """

    def suggestion_exists(self, suggestion_id: int) -> bool: ...


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
    ) -> VoteRoundResult:
        """Open a new voting round.

        Args:
            visibility: Whether individual votes are visible to other
                members. Defaults to visible.
            closes_at: Optional deadline for the round. Not enforced by
                this milestone; stored for future use.

        Returns:
            VoteRoundResult. Fails if a round is already open, since only
            one round may be open at a time.
        """
        if self.get_open_round() is not None:
            return VoteRoundResult(success=False, message="A voting round is already open.")

        new_round = VoteRound(
            id=self._next_round_id,
            status=VoteRoundStatus.OPEN,
            visibility=visibility,
            closes_at=closes_at,
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

    def get_round(self, round_id: int) -> Optional[VoteRound]:
        """Get a voting round by ID.

        Args:
            round_id: The round ID to look up.

        Returns:
            The matching VoteRound, or None if no round has that ID.
        """
        return self._rounds.get(round_id)

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
            VoteResult indicating success or failure.
        """
        vote_round = self._rounds.get(round_id)
        if vote_round is None:
            return VoteResult(success=False, message="That voting round doesn't exist.")

        if vote_round.status == VoteRoundStatus.CLOSED:
            return VoteResult(success=False, message="That voting round is already closed.")

        vote_round.status = VoteRoundStatus.CLOSED
        self._save()
        return VoteResult(success=True, message=f"Voting round {round_id} is now closed.")

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

    def _save(self) -> None:
        """Persist the current voting state via the repository."""
        self._repository.save(self._rounds.values(), self._next_round_id)
