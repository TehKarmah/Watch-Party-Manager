import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.vote import (
    VoteRecord,
    VoteRound,
    VoteRoundStatus,
    VoteVisibility,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class VoteRecordModelTests(unittest.TestCase):
    def test_valid_vote_record(self) -> None:
        now = utc_now()
        vote = VoteRecord(
            discord_user_id=123456789012345678,
            suggestion_id=1,
            original_suggestion_id=1,
            first_voted_at=now,
            last_voted_at=now,
        )

        self.assertEqual(vote.discord_user_id, 123456789012345678)
        self.assertEqual(vote.suggestion_id, 1)
        self.assertEqual(vote.original_suggestion_id, 1)
        self.assertEqual(vote.changes_used, 0)

    def test_original_and_current_suggestion_id_may_differ(self) -> None:
        now = utc_now()
        vote = VoteRecord(
            discord_user_id=1,
            suggestion_id=2,
            original_suggestion_id=1,
            first_voted_at=now,
            last_voted_at=now,
            changes_used=1,
        )

        self.assertEqual(vote.original_suggestion_id, 1)
        self.assertEqual(vote.suggestion_id, 2)

    def test_rejects_non_positive_discord_user_id(self) -> None:
        now = utc_now()
        with self.assertRaises(ValueError):
            VoteRecord(
                discord_user_id=0,
                suggestion_id=1,
                original_suggestion_id=1,
                first_voted_at=now,
                last_voted_at=now,
            )

    def test_rejects_non_positive_suggestion_id(self) -> None:
        now = utc_now()
        with self.assertRaises(ValueError):
            VoteRecord(
                discord_user_id=1,
                suggestion_id=0,
                original_suggestion_id=1,
                first_voted_at=now,
                last_voted_at=now,
            )

    def test_rejects_non_positive_original_suggestion_id(self) -> None:
        now = utc_now()
        with self.assertRaises(ValueError):
            VoteRecord(
                discord_user_id=1,
                suggestion_id=1,
                original_suggestion_id=0,
                first_voted_at=now,
                last_voted_at=now,
            )

    def test_rejects_negative_change_count(self) -> None:
        now = utc_now()
        with self.assertRaises(ValueError):
            VoteRecord(
                discord_user_id=1,
                suggestion_id=1,
                original_suggestion_id=1,
                first_voted_at=now,
                last_voted_at=now,
                changes_used=-1,
            )

    def test_rejects_more_than_one_change(self) -> None:
        now = utc_now()
        with self.assertRaises(ValueError):
            VoteRecord(
                discord_user_id=1,
                suggestion_id=1,
                original_suggestion_id=1,
                first_voted_at=now,
                last_voted_at=now,
                changes_used=2,
            )

    def test_allows_exactly_one_change(self) -> None:
        now = utc_now()
        vote = VoteRecord(
            discord_user_id=1,
            suggestion_id=1,
            original_suggestion_id=1,
            first_voted_at=now,
            last_voted_at=now,
            changes_used=1,
        )
        self.assertEqual(vote.changes_used, 1)

    def test_rejects_naive_first_voted_at(self) -> None:
        naive = datetime(2026, 1, 1)
        with self.assertRaises(ValueError):
            VoteRecord(
                discord_user_id=1,
                suggestion_id=1,
                original_suggestion_id=1,
                first_voted_at=naive,
                last_voted_at=utc_now(),
            )

    def test_rejects_naive_last_voted_at(self) -> None:
        naive = datetime(2026, 1, 1)
        with self.assertRaises(ValueError):
            VoteRecord(
                discord_user_id=1,
                suggestion_id=1,
                original_suggestion_id=1,
                first_voted_at=utc_now(),
                last_voted_at=naive,
            )


class VoteRoundModelTests(unittest.TestCase):
    def test_valid_vote_round(self) -> None:
        vote_round = VoteRound(id=1)

        self.assertEqual(vote_round.id, 1)
        self.assertEqual(vote_round.status, VoteRoundStatus.OPEN)
        self.assertEqual(vote_round.visibility, VoteVisibility.VISIBLE)
        self.assertEqual(vote_round.votes, {})
        self.assertIsNone(vote_round.winning_suggestion_id)
        self.assertIsNotNone(vote_round.created_at.tzinfo)

    def test_rejects_non_positive_round_id(self) -> None:
        with self.assertRaises(ValueError):
            VoteRound(id=0)

    def test_rejects_non_positive_winning_suggestion_id(self) -> None:
        with self.assertRaises(ValueError):
            VoteRound(id=1, winning_suggestion_id=0)

    def test_allows_a_positive_winning_suggestion_id(self) -> None:
        vote_round = VoteRound(id=1, winning_suggestion_id=5)
        self.assertEqual(vote_round.winning_suggestion_id, 5)

    def test_supports_blind_visibility(self) -> None:
        vote_round = VoteRound(id=1, visibility=VoteVisibility.BLIND)
        self.assertEqual(vote_round.visibility, VoteVisibility.BLIND)

    def test_supports_visible_visibility(self) -> None:
        vote_round = VoteRound(id=1, visibility=VoteVisibility.VISIBLE)
        self.assertEqual(vote_round.visibility, VoteVisibility.VISIBLE)

    def test_supports_open_status(self) -> None:
        vote_round = VoteRound(id=1, status=VoteRoundStatus.OPEN)
        self.assertEqual(vote_round.status, VoteRoundStatus.OPEN)

    def test_supports_closed_status(self) -> None:
        vote_round = VoteRound(id=1, status=VoteRoundStatus.CLOSED)
        self.assertEqual(vote_round.status, VoteRoundStatus.CLOSED)

    def test_rejects_naive_created_at(self) -> None:
        with self.assertRaises(ValueError):
            VoteRound(id=1, created_at=datetime(2026, 1, 1))

    def test_rejects_naive_closes_at(self) -> None:
        with self.assertRaises(ValueError):
            VoteRound(id=1, closes_at=datetime(2026, 1, 8))

    def test_allows_a_timezone_aware_closes_at(self) -> None:
        deadline = utc_now()
        vote_round = VoteRound(id=1, closes_at=deadline)
        self.assertEqual(vote_round.closes_at, deadline)

    def test_votes_preserve_insertion_order(self) -> None:
        now = utc_now()
        vote_round = VoteRound(id=1)
        vote_round.votes[111] = VoteRecord(
            discord_user_id=111,
            suggestion_id=1,
            original_suggestion_id=1,
            first_voted_at=now,
            last_voted_at=now,
        )
        vote_round.votes[222] = VoteRecord(
            discord_user_id=222,
            suggestion_id=2,
            original_suggestion_id=2,
            first_voted_at=now,
            last_voted_at=now,
        )

        self.assertEqual(list(vote_round.votes.keys()), [111, 222])


if __name__ == "__main__":
    unittest.main()
