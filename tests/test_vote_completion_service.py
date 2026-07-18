import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.vote import VoteRoundStatus, VoteVisibility
from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.vote_completion_service import (
    VoteCompletionResult,
    VoteCompletionService,
)
from watch_party_manager.services.vote_service import VoteService


class VoteCompletionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        """Real SuggestionService and VoteService, both backed by temp files --
        mirrors the fixture pattern already used in test_persistent_voting.py.
        """
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.vote_service = VoteService(
            self.suggestion_service, repository=JsonVoteRepository(root / "voting.json")
        )
        self.completion_service = VoteCompletionService(self.vote_service, self.suggestion_service)

        self.matrix = self.suggestion_service.suggest("The Matrix").watch_item
        self.inception = self.suggestion_service.suggest("Inception").watch_item
        self.interstellar = self.suggestion_service.suggest("Interstellar").watch_item

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _open_round(self, visibility=VoteVisibility.VISIBLE, closes_at=None):
        return self.vote_service.create_round(
            visibility=visibility,
            closes_at=closes_at,
            candidate_suggestion_ids=[self.matrix.id, self.inception.id, self.interstellar.id],
        ).vote_round

    # --- No-op / safety cases -------------------------------------------------

    def test_returns_none_when_no_round_is_open(self) -> None:
        result = self.completion_service.check_and_complete_expired_round()
        self.assertIsNone(result)

    def test_returns_none_when_the_open_round_has_no_deadline(self) -> None:
        self._open_round(closes_at=None)

        result = self.completion_service.check_and_complete_expired_round()

        self.assertIsNone(result)
        self.assertEqual(self.vote_service.get_open_round().status, VoteRoundStatus.OPEN)

    def test_returns_none_when_the_deadline_has_not_passed_yet(self) -> None:
        future = datetime.now(timezone.utc) + timedelta(days=1)
        self._open_round(closes_at=future)

        result = self.completion_service.check_and_complete_expired_round(now=datetime.now(timezone.utc))

        self.assertIsNone(result)
        self.assertEqual(self.vote_service.get_open_round().status, VoteRoundStatus.OPEN)

    def test_is_safe_to_call_repeatedly_with_nothing_active(self) -> None:
        for _ in range(3):
            self.assertIsNone(self.completion_service.check_and_complete_expired_round())

    # --- Normal winner --------------------------------------------------------

    def test_closes_the_round_when_expired(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        vote_round = self._open_round(closes_at=past)
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)

        self.completion_service.check_and_complete_expired_round(now=datetime.now(timezone.utc))

        self.assertEqual(self.vote_service.get_round(vote_round.id).status, VoteRoundStatus.CLOSED)

    def test_reports_a_single_normal_winner(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        self._open_round(closes_at=past)
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)
        self.vote_service.cast_vote(discord_user_id=2, suggestion_id=self.matrix.id)
        self.vote_service.cast_vote(discord_user_id=3, suggestion_id=self.inception.id)

        result = self.completion_service.check_and_complete_expired_round(now=datetime.now(timezone.utc))

        self.assertIsInstance(result, VoteCompletionResult)
        self.assertEqual(result.winning_suggestion_ids, [self.matrix.id])
        self.assertEqual(result.total_votes_cast, 3)

    def test_prevents_additional_votes_after_completion(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        self._open_round(closes_at=past)
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)

        self.completion_service.check_and_complete_expired_round(now=datetime.now(timezone.utc))

        late_vote = self.vote_service.cast_vote(discord_user_id=2, suggestion_id=self.inception.id)
        self.assertFalse(late_vote.success)

    def test_reuses_get_current_winners_rather_than_recomputing(self) -> None:
        # If VoteService's own winner calculation were bypassed or
        # duplicated, this scenario (a vote changed after being cast)
        # would be exactly where the two could disagree.
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        self._open_round(closes_at=past)
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.inception.id)
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)  # changed vote

        result = self.completion_service.check_and_complete_expired_round(now=datetime.now(timezone.utc))

        self.assertEqual(result.winning_suggestion_ids, [self.matrix.id])

    # --- Tie -------------------------------------------------------------------

    def test_reports_a_tie_with_every_tied_winner(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        self._open_round(closes_at=past)
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)
        self.vote_service.cast_vote(discord_user_id=2, suggestion_id=self.inception.id)

        result = self.completion_service.check_and_complete_expired_round(now=datetime.now(timezone.utc))

        self.assertEqual(sorted(result.winning_suggestion_ids), sorted([self.matrix.id, self.inception.id]))

    def test_a_three_way_tie_reports_all_three(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        self._open_round(closes_at=past)
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)
        self.vote_service.cast_vote(discord_user_id=2, suggestion_id=self.inception.id)
        self.vote_service.cast_vote(discord_user_id=3, suggestion_id=self.interstellar.id)

        result = self.completion_service.check_and_complete_expired_round(now=datetime.now(timezone.utc))

        self.assertEqual(
            sorted(result.winning_suggestion_ids),
            sorted([self.matrix.id, self.inception.id, self.interstellar.id]),
        )

    # --- No votes cast -----------------------------------------------------------

    def test_no_votes_cast_produces_no_winners(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        vote_round = self._open_round(closes_at=past)

        result = self.completion_service.check_and_complete_expired_round(now=datetime.now(timezone.utc))

        self.assertEqual(result.winning_suggestion_ids, [])
        self.assertEqual(result.total_votes_cast, 0)
        self.assertEqual(self.vote_service.get_round(vote_round.id).status, VoteRoundStatus.CLOSED)

    def test_no_votes_cast_does_not_update_any_watch_history(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        self._open_round(closes_at=past)

        self.completion_service.check_and_complete_expired_round(now=datetime.now(timezone.utc))

        self.assertEqual(self.suggestion_service.get_suggestion(self.matrix.id).journey.times_won, 0)

    # --- Watch history updated -------------------------------------------------

    def test_winner_journey_gets_times_won_incremented(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        self._open_round(closes_at=past)
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)

        self.completion_service.check_and_complete_expired_round(now=datetime.now(timezone.utc))

        winner = self.suggestion_service.get_suggestion(self.matrix.id)
        self.assertEqual(winner.journey.times_won, 1)

    def test_winner_journey_gets_last_won_date_set_to_the_rounds_deadline(self) -> None:
        deadline = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        self._open_round(closes_at=deadline)
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)

        self.completion_service.check_and_complete_expired_round(now=deadline + timedelta(hours=3))

        winner = self.suggestion_service.get_suggestion(self.matrix.id)
        self.assertEqual(winner.journey.last_won_date, deadline.date())

    def test_winner_journey_gets_voting_appearances_and_last_nominated_date_updated_too(self) -> None:
        deadline = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        self._open_round(closes_at=deadline)
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)

        self.completion_service.check_and_complete_expired_round(now=deadline)

        winner = self.suggestion_service.get_suggestion(self.matrix.id)
        self.assertEqual(winner.journey.voting_appearances, 1)
        self.assertEqual(winner.journey.last_nominated_date, deadline.date())

    def test_winner_journey_preserves_the_winning_vote_field(self) -> None:
        deadline = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        self._open_round(closes_at=deadline)
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)

        self.completion_service.check_and_complete_expired_round(now=deadline)

        winner = self.suggestion_service.get_suggestion(self.matrix.id)
        self.assertEqual(winner.journey.winning_vote, "The Matrix")

    def test_non_winning_nominees_do_not_get_times_won_incremented(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        self._open_round(closes_at=past)
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)

        self.completion_service.check_and_complete_expired_round(now=datetime.now(timezone.utc))

        loser = self.suggestion_service.get_suggestion(self.inception.id)
        self.assertEqual(loser.journey.times_won, 0)
        self.assertIsNone(loser.journey.last_won_date)

    def test_a_tie_updates_every_winners_journey(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        self._open_round(closes_at=past)
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)
        self.vote_service.cast_vote(discord_user_id=2, suggestion_id=self.inception.id)

        self.completion_service.check_and_complete_expired_round(now=datetime.now(timezone.utc))

        self.assertEqual(self.suggestion_service.get_suggestion(self.matrix.id).journey.times_won, 1)
        self.assertEqual(self.suggestion_service.get_suggestion(self.inception.id).journey.times_won, 1)

    def test_watch_history_updates_are_persisted(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        self._open_round(closes_at=past)
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)

        self.completion_service.check_and_complete_expired_round(now=datetime.now(timezone.utc))

        reloaded_service = SuggestionService(
            repository=JsonSuggestionRepository(Path(self._temp_dir.name) / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(
                Path(self._temp_dir.name) / "suggestion_databases.json"
            ),
        )
        reloaded_winner = reloaded_service.get_suggestion(self.matrix.id)
        self.assertEqual(reloaded_winner.journey.times_won, 1)

    # --- Restart safety / idempotency -------------------------------------------

    def test_repeated_calls_after_completion_are_a_no_op(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        self._open_round(closes_at=past)
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)

        first = self.completion_service.check_and_complete_expired_round(now=datetime.now(timezone.utc))
        second = self.completion_service.check_and_complete_expired_round(now=datetime.now(timezone.utc))

        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_repeated_calls_do_not_double_count_watch_history(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        self._open_round(closes_at=past)
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)

        self.completion_service.check_and_complete_expired_round(now=datetime.now(timezone.utc))
        self.completion_service.check_and_complete_expired_round(now=datetime.now(timezone.utc))

        winner = self.suggestion_service.get_suggestion(self.matrix.id)
        self.assertEqual(winner.journey.times_won, 1)

    def test_simulated_restart_after_expiration_still_completes_the_round(self) -> None:
        # Simulates the bot being offline well past the deadline: a fresh
        # VoteCompletionService (as if freshly constructed after a
        # restart) built on services reloaded from disk still detects
        # and completes the stale round correctly.
        past_deadline = datetime.now(timezone.utc) - timedelta(days=1)
        self._open_round(closes_at=past_deadline)
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)

        root = Path(self._temp_dir.name)
        restarted_suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        restarted_vote_service = VoteService(
            restarted_suggestion_service, repository=JsonVoteRepository(root / "voting.json")
        )
        restarted_completion_service = VoteCompletionService(
            restarted_vote_service, restarted_suggestion_service
        )

        result = restarted_completion_service.check_and_complete_expired_round(now=datetime.now(timezone.utc))

        self.assertIsNotNone(result)
        self.assertEqual(result.winning_suggestion_ids, [self.matrix.id])
        self.assertEqual(
            restarted_vote_service.get_round(result.vote_round.id).status, VoteRoundStatus.CLOSED
        )

    # --- Archive: closed rounds remain accessible -------------------------------

    def test_archived_round_remains_accessible_after_completion(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        vote_round = self._open_round(closes_at=past)
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)

        self.completion_service.check_and_complete_expired_round(now=datetime.now(timezone.utc))

        self.assertIsNotNone(self.vote_service.get_round(vote_round.id))
        self.assertEqual(self.vote_service.get_round(vote_round.id).status, VoteRoundStatus.CLOSED)

    def test_newest_open_round_is_still_returned_after_a_prior_round_completes(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        first_round = self._open_round(closes_at=past)
        self.completion_service.check_and_complete_expired_round(now=datetime.now(timezone.utc))

        second_round = self._open_round(closes_at=None)

        self.assertEqual(self.vote_service.get_open_round().id, second_round.id)
        self.assertNotEqual(second_round.id, first_round.id)
        # The archived round is still directly retrievable.
        self.assertEqual(self.vote_service.get_round(first_round.id).status, VoteRoundStatus.CLOSED)

    def test_get_latest_round_returns_the_completed_round_when_no_new_round_exists(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        vote_round = self._open_round(closes_at=past)

        self.completion_service.check_and_complete_expired_round(now=datetime.now(timezone.utc))

        self.assertEqual(self.vote_service.get_latest_round().id, vote_round.id)
        self.assertEqual(self.vote_service.get_latest_round().status, VoteRoundStatus.CLOSED)

    # --- Standings reuse ---------------------------------------------------------

    def test_standings_are_included_via_the_existing_standings_calculation(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(minutes=1)
        self._open_round(closes_at=past)
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=self.matrix.id)
        self.vote_service.cast_vote(discord_user_id=2, suggestion_id=self.matrix.id)
        self.vote_service.cast_vote(discord_user_id=3, suggestion_id=self.inception.id)

        result = self.completion_service.check_and_complete_expired_round(now=datetime.now(timezone.utc))

        standings_by_id = {entry.suggestion_id: entry.vote_count for entry in result.standings}
        self.assertEqual(standings_by_id[self.matrix.id], 2)
        self.assertEqual(standings_by_id[self.inception.id], 1)


if __name__ == "__main__":
    unittest.main()
