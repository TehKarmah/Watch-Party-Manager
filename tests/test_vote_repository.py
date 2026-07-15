import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.vote import (
    VoteRecord,
    VoteRound,
    VoteRoundStatus,
    VoteVisibility,
)
from watch_party_manager.persistence.vote_repository import JsonVoteRepository


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class JsonVoteRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        """Point each test at its own temporary file so tests never touch real data."""
        self._temp_dir = tempfile.TemporaryDirectory()
        self.file_path = Path(self._temp_dir.name) / "voting.json"
        self.repository = JsonVoteRepository(self.file_path)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_load_returns_empty_state_when_file_does_not_exist(self) -> None:
        self.assertFalse(self.file_path.exists())

        result = self.repository.load()
        self.assertEqual(result.rounds, [])
        self.assertEqual(result.next_round_id, 1)

    def test_load_returns_empty_state_and_logs_when_json_is_malformed(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text("{ this is not valid json", encoding="utf-8")

        with self.assertLogs(
            "watch_party_manager.persistence.vote_repository", level="ERROR"
        ) as log_context:
            result = self.repository.load()

        self.assertEqual(result.rounds, [])
        self.assertEqual(result.next_round_id, 1)
        self.assertTrue(any("voting" in message for message in log_context.output))

    def test_save_creates_the_file_and_parent_directory(self) -> None:
        nested_path = Path(self._temp_dir.name) / "nested" / "voting.json"
        repository = JsonVoteRepository(nested_path)

        repository.save([VoteRound(id=1)], next_round_id=2)

        self.assertTrue(nested_path.exists())

    def test_save_then_load_round_trips_a_single_round(self) -> None:
        vote_round = VoteRound(id=1)
        self.repository.save([vote_round], next_round_id=2)

        result = self.repository.load()
        self.assertEqual(len(result.rounds), 1)
        self.assertEqual(result.rounds[0].id, 1)
        self.assertEqual(result.next_round_id, 2)

    def test_save_then_load_round_trips_multiple_rounds(self) -> None:
        rounds = [
            VoteRound(id=1, status=VoteRoundStatus.CLOSED),
            VoteRound(id=2, status=VoteRoundStatus.OPEN),
        ]
        self.repository.save(rounds, next_round_id=3)

        result = self.repository.load()
        ids = [r.id for r in result.rounds]
        self.assertEqual(ids, [1, 2])
        self.assertEqual(result.next_round_id, 3)

    def test_save_then_load_round_trips_votes_within_a_round(self) -> None:
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
        self.repository.save([vote_round], next_round_id=2)

        result = self.repository.load()
        loaded_round = result.rounds[0]
        self.assertEqual(len(loaded_round.votes), 2)
        self.assertEqual(loaded_round.votes[111].suggestion_id, 1)
        self.assertEqual(loaded_round.votes[222].suggestion_id, 2)
        # Order of votes should be preserved.
        self.assertEqual(list(loaded_round.votes.keys()), [111, 222])

    def test_save_then_load_round_trips_change_counts(self) -> None:
        now = utc_now()
        vote_round = VoteRound(id=1)
        vote_round.votes[111] = VoteRecord(
            discord_user_id=111,
            suggestion_id=2,
            original_suggestion_id=1,
            first_voted_at=now,
            last_voted_at=now,
            changes_used=1,
        )
        self.repository.save([vote_round], next_round_id=2)

        result = self.repository.load()
        self.assertEqual(result.rounds[0].votes[111].changes_used, 1)

    def test_save_then_load_round_trips_original_suggestion_id(self) -> None:
        now = utc_now()
        vote_round = VoteRound(id=1)
        vote_round.votes[111] = VoteRecord(
            discord_user_id=111,
            suggestion_id=2,
            original_suggestion_id=1,
            first_voted_at=now,
            last_voted_at=now,
            changes_used=1,
        )
        self.repository.save([vote_round], next_round_id=2)

        result = self.repository.load()
        loaded_vote = result.rounds[0].votes[111]
        self.assertEqual(loaded_vote.original_suggestion_id, 1)
        self.assertEqual(loaded_vote.suggestion_id, 2)

    def test_loading_legacy_vote_data_without_original_suggestion_id_falls_back_to_suggestion_id(
        self,
    ) -> None:
        now = utc_now()
        legacy_json = f"""
        {{
          "next_round_id": 2,
          "rounds": [
            {{
              "id": 1,
              "status": "open",
              "visibility": "visible",
              "created_at": "{now.isoformat()}",
              "closes_at": null,
              "winning_suggestion_id": null,
              "votes": [
                {{
                  "discord_user_id": 111,
                  "suggestion_id": 3,
                  "first_voted_at": "{now.isoformat()}",
                  "last_voted_at": "{now.isoformat()}",
                  "changes_used": 0
                }}
              ]
            }}
          ]
        }}
        """
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(legacy_json, encoding="utf-8")

        result = self.repository.load()

        loaded_vote = result.rounds[0].votes[111]
        self.assertEqual(loaded_vote.suggestion_id, 3)
        self.assertEqual(loaded_vote.original_suggestion_id, 3)

    def test_save_then_load_round_trips_visibility(self) -> None:
        self.repository.save([VoteRound(id=1, visibility=VoteVisibility.BLIND)], next_round_id=2)

        result = self.repository.load()
        self.assertEqual(result.rounds[0].visibility, VoteVisibility.BLIND)

    def test_save_then_load_round_trips_status(self) -> None:
        self.repository.save([VoteRound(id=1, status=VoteRoundStatus.CLOSED)], next_round_id=2)

        result = self.repository.load()
        self.assertEqual(result.rounds[0].status, VoteRoundStatus.CLOSED)

    def test_save_then_load_round_trips_timestamps_accurately(self) -> None:
        created_at = datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)
        closes_at = created_at + timedelta(days=7)
        vote_round = VoteRound(id=1, created_at=created_at, closes_at=closes_at)
        self.repository.save([vote_round], next_round_id=2)

        result = self.repository.load()
        self.assertEqual(result.rounds[0].created_at, created_at)
        self.assertEqual(result.rounds[0].closes_at, closes_at)

    def test_save_then_load_round_trips_a_missing_deadline(self) -> None:
        self.repository.save([VoteRound(id=1, closes_at=None)], next_round_id=2)

        result = self.repository.load()
        self.assertIsNone(result.rounds[0].closes_at)

    def test_save_then_load_round_trips_winning_suggestion_id(self) -> None:
        self.repository.save([VoteRound(id=1, winning_suggestion_id=5)], next_round_id=2)

        result = self.repository.load()
        self.assertEqual(result.rounds[0].winning_suggestion_id, 5)

    def test_next_round_id_persists_and_is_not_reused(self) -> None:
        self.repository.save([VoteRound(id=1)], next_round_id=2)
        self.repository.save([], next_round_id=2)  # Round 1 removed, but ID must not be reused.

        result = self.repository.load()
        self.assertEqual(result.rounds, [])
        self.assertEqual(result.next_round_id, 2)

    def test_empty_voting_state_persists(self) -> None:
        self.repository.save([], next_round_id=1)

        result = self.repository.load()
        self.assertEqual(result.rounds, [])
        self.assertEqual(result.next_round_id, 1)

    def test_human_readable_json_contains_expected_fields(self) -> None:
        now = utc_now()
        vote_round = VoteRound(id=1, visibility=VoteVisibility.BLIND)
        vote_round.votes[111] = VoteRecord(
            discord_user_id=111,
            suggestion_id=1,
            original_suggestion_id=1,
            first_voted_at=now,
            last_voted_at=now,
        )
        self.repository.save([vote_round], next_round_id=2)

        raw_text = self.file_path.read_text(encoding="utf-8")
        self.assertIn('"id": 1', raw_text)
        self.assertIn('"visibility": "blind"', raw_text)
        self.assertIn('"discord_user_id": 111', raw_text)
        self.assertIn('"original_suggestion_id": 1', raw_text)
        self.assertIn('"next_round_id": 2', raw_text)

    def test_save_then_load_round_trips_discord_location(self) -> None:
        vote_round = VoteRound(id=1, guild_id=100, channel_id=200, message_id=300)
        self.repository.save([vote_round], next_round_id=2)

        result = self.repository.load()
        loaded = result.rounds[0]
        self.assertEqual(loaded.guild_id, 100)
        self.assertEqual(loaded.channel_id, 200)
        self.assertEqual(loaded.message_id, 300)

    def test_loading_a_file_without_discord_location_fields_defaults_them_to_none(self) -> None:
        now = utc_now()
        legacy_json = f"""
        {{
          "next_round_id": 2,
          "rounds": [
            {{
              "id": 1,
              "status": "open",
              "visibility": "visible",
              "created_at": "{now.isoformat()}",
              "closes_at": null,
              "winning_suggestion_id": null,
              "votes": []
            }}
          ]
        }}
        """
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(legacy_json, encoding="utf-8")

        result = self.repository.load()
        loaded = result.rounds[0]
        self.assertIsNone(loaded.guild_id)
        self.assertIsNone(loaded.channel_id)
        self.assertIsNone(loaded.message_id)


    def test_save_then_load_round_trips_candidate_ids(self) -> None:
        vote_round = VoteRound(id=1, candidate_suggestion_ids=[3, 1, 2])
        self.repository.save([vote_round], next_round_id=2)

        loaded = self.repository.load().rounds[0]
        self.assertEqual(loaded.candidate_suggestion_ids, [3, 1, 2])

    def test_loading_legacy_round_without_candidate_ids_defaults_to_empty(self) -> None:
        now = utc_now()
        legacy_json = f"""
        {{
          "next_round_id": 2,
          "rounds": [{{
            "id": 1,
            "status": "open",
            "visibility": "visible",
            "created_at": "{now.isoformat()}",
            "closes_at": null,
            "winning_suggestion_id": null,
            "votes": []
          }}]
        }}
        """
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(legacy_json, encoding="utf-8")

        loaded = self.repository.load().rounds[0]
        self.assertEqual(loaded.candidate_suggestion_ids, [])


if __name__ == "__main__":
    unittest.main()
