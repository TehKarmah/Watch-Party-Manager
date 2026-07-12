"""JSON-backed persistence for vote rounds and vote records."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Union

from watch_party_manager.domain.vote import (
    VoteRecord,
    VoteRound,
    VoteRoundStatus,
    VoteVisibility,
)

logger = logging.getLogger(__name__)

# Kept separate from suggestions.json: voting is its own concern with its
# own lifecycle, and this file is easy to inspect or reset independently.
DEFAULT_VOTING_PATH = Path("data/voting.json")

FIRST_ROUND_ID = 1


@dataclass
class VoteLoadResult:
    """What comes back from loading the voting file.

    next_round_id is tracked separately from the loaded rounds so that
    round IDs keep increasing even if every round were ever removed
    (round IDs must never be reused).
    """

    rounds: list[VoteRound]
    next_round_id: int


class JsonVoteRepository:
    """Loads and saves vote rounds as a JSON file on disk.

    Mirrors JsonSuggestionRepository: this is the only place that knows
    voting data is stored as JSON. VoteService only ever calls load()/save(),
    so the storage mechanism can be swapped out later without touching it.
    """

    def __init__(self, file_path: Union[Path, str] = DEFAULT_VOTING_PATH) -> None:
        """Initialize the repository.

        Args:
            file_path: Path to the JSON file used for persistence.
        """
        self._file_path = Path(file_path)

    def load(self) -> VoteLoadResult:
        """Load vote rounds from disk.

        A missing file is expected on first run and is not an error. A file
        that exists but can't be parsed is logged and treated as empty
        voting state rather than crashing the bot.

        Returns:
            A VoteLoadResult with the persisted rounds (insertion order
            preserved, each round's votes in their original order) and the
            next round ID to hand out.
        """
        if not self._file_path.exists():
            return VoteLoadResult(rounds=[], next_round_id=FIRST_ROUND_ID)

        try:
            raw_text = self._file_path.read_text(encoding="utf-8")
            data = json.loads(raw_text)
            rounds = [self._deserialize_round(entry) for entry in data["rounds"]]
            next_round_id = data.get("next_round_id", FIRST_ROUND_ID)
            return VoteLoadResult(rounds=rounds, next_round_id=next_round_id)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.error(f"Could not load voting data from {self._file_path}: {exc}")
            return VoteLoadResult(rounds=[], next_round_id=FIRST_ROUND_ID)

    def save(self, vote_rounds: Iterable[VoteRound], next_round_id: int) -> None:
        """Save vote rounds to disk, overwriting any previous contents.

        Creates the parent directory and the file itself if they don't
        already exist.

        Args:
            vote_rounds: The rounds to persist.
            next_round_id: The ID to hand out to the next new round.
                Persisted so round IDs keep increasing across restarts.
        """
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "next_round_id": next_round_id,
            "rounds": [self._serialize_round(vote_round) for vote_round in vote_rounds],
        }
        self._file_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @staticmethod
    def _serialize_round(vote_round: VoteRound) -> dict:
        return {
            "id": vote_round.id,
            "status": vote_round.status.value,
            "visibility": vote_round.visibility.value,
            "created_at": vote_round.created_at.isoformat(),
            "closes_at": vote_round.closes_at.isoformat() if vote_round.closes_at else None,
            "winning_suggestion_id": vote_round.winning_suggestion_id,
            "votes": [
                JsonVoteRepository._serialize_vote(vote_record)
                for vote_record in vote_round.votes.values()
            ],
        }

    @staticmethod
    def _serialize_vote(vote_record: VoteRecord) -> dict:
        return {
            "discord_user_id": vote_record.discord_user_id,
            "suggestion_id": vote_record.suggestion_id,
            "original_suggestion_id": vote_record.original_suggestion_id,
            "first_voted_at": vote_record.first_voted_at.isoformat(),
            "last_voted_at": vote_record.last_voted_at.isoformat(),
            "changes_used": vote_record.changes_used,
        }

    @staticmethod
    def _deserialize_round(entry: dict) -> VoteRound:
        votes: dict[int, VoteRecord] = {}
        for vote_entry in entry.get("votes", []):
            vote_record = JsonVoteRepository._deserialize_vote(vote_entry)
            votes[vote_record.discord_user_id] = vote_record

        closes_at_raw = entry.get("closes_at")
        return VoteRound(
            id=entry["id"],
            status=VoteRoundStatus(entry["status"]),
            visibility=VoteVisibility(entry["visibility"]),
            created_at=datetime.fromisoformat(entry["created_at"]),
            closes_at=datetime.fromisoformat(closes_at_raw) if closes_at_raw else None,
            votes=votes,
            winning_suggestion_id=entry.get("winning_suggestion_id"),
        )

    @staticmethod
    def _deserialize_vote(entry: dict) -> VoteRecord:
        suggestion_id = entry["suggestion_id"]
        # Vote data saved before original_suggestion_id existed won't have
        # it. Falling back to the current suggestion_id is the closest
        # available approximation of the original pick, and lets an older
        # file keep loading instead of being discarded.
        original_suggestion_id = entry.get("original_suggestion_id", suggestion_id)
        return VoteRecord(
            discord_user_id=entry["discord_user_id"],
            suggestion_id=suggestion_id,
            original_suggestion_id=original_suggestion_id,
            first_voted_at=datetime.fromisoformat(entry["first_voted_at"]),
            last_voted_at=datetime.fromisoformat(entry["last_voted_at"]),
            changes_used=entry.get("changes_used", 0),
        )
