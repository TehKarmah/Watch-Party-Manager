import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import perform_start_vote, perform_vote, perform_vote_status
from watch_party_manager.domain.vote import VoteVisibility
from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.vote_service import StandingsResult, VoteResult, VoteService


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, roles=()) -> None:
        self.roles = list(roles)


WASH_CREW_ROLE_ID = 999


class VoteCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        """A real SuggestionService and VoteService, both backed by temp files,
        pre-loaded with two suggestions (the minimum needed to start a round).
        """
        self._temp_dir = tempfile.TemporaryDirectory()
        suggestions_path = Path(self._temp_dir.name) / "suggestions.json"
        suggestion_databases_path = Path(self._temp_dir.name) / "suggestion_databases.json"
        voting_path = Path(self._temp_dir.name) / "voting.json"

        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(suggestions_path),
            database_repository=JsonSuggestionDatabaseRepository(suggestion_databases_path),
        )
        self.vote_service = VoteService(self.suggestion_service, repository=JsonVoteRepository(voting_path))

        self.suggestion_service.suggest("The Matrix")
        self.suggestion_service.suggest("Inception")

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _authorized_user(self) -> FakeMember:
        return FakeMember(roles=[FakeRole(WASH_CREW_ROLE_ID)])

    def _unauthorized_user(self) -> FakeMember:
        return FakeMember(roles=[FakeRole(1)])

    def _start_vote(
        self, user=None, wash_crew_role_id=WASH_CREW_ROLE_ID, visibility="visible", duration_days=None
    ):
        return perform_start_vote(
            vote_service=self.vote_service,
            suggestion_service=self.suggestion_service,
            user=user if user is not None else self._authorized_user(),
            wash_crew_role_id=wash_crew_role_id,
            visibility_str=visibility,
            duration_days=duration_days,
        )

    # --- /start_vote: permissions ----------------------------------------

    def test_authorized_user_starts_a_round(self) -> None:
        message, ephemeral = self._start_vote()

        self.assertFalse(ephemeral)
        self.assertIn("Voting round 1 is now open.", message)
        self.assertIsNotNone(self.vote_service.get_open_round())

    def test_unauthorized_user_is_rejected(self) -> None:
        message, ephemeral = self._start_vote(user=self._unauthorized_user())

        self.assertTrue(ephemeral)
        self.assertIn("WASH Crew", message)
        self.assertIsNone(self.vote_service.get_open_round())

    def test_unconfigured_role_fails_closed_with_a_clear_message(self) -> None:
        message, ephemeral = self._start_vote(wash_crew_role_id=None)

        self.assertTrue(ephemeral)
        self.assertIn("not been configured", message)
        self.assertIn("WASH_CREW_ROLE_ID", message)
        self.assertIsNone(self.vote_service.get_open_round())

    def test_unconfigured_role_rejects_even_a_member_with_no_roles_at_all(self) -> None:
        # There's nothing special about this user; the point is that an
        # unconfigured role blocks *everyone*, not just people lacking roles.
        message, ephemeral = self._start_vote(
            user=FakeMember(roles=[]), wash_crew_role_id=None
        )

        self.assertTrue(ephemeral)
        self.assertIn("not been configured", message)

    def test_unconfigured_role_message_differs_from_lacks_role_message(self) -> None:
        unconfigured_message, _ = self._start_vote(wash_crew_role_id=None)
        lacks_role_message, _ = self._start_vote(user=self._unauthorized_user())

        self.assertNotEqual(unconfigured_message, lacks_role_message)

    # --- /start_vote: duration ---------------------------------------------

    def test_default_duration_is_seven_days(self) -> None:
        before = datetime.now(timezone.utc)

        self._start_vote(duration_days=None)

        vote_round = self.vote_service.get_open_round()
        expected = before + timedelta(days=7)
        self.assertAlmostEqual(vote_round.closes_at.timestamp(), expected.timestamp(), delta=5)

    def test_custom_duration_is_accepted(self) -> None:
        before = datetime.now(timezone.utc)

        self._start_vote(duration_days=3)

        vote_round = self.vote_service.get_open_round()
        expected = before + timedelta(days=3)
        self.assertAlmostEqual(vote_round.closes_at.timestamp(), expected.timestamp(), delta=5)

    def test_duration_of_one_day_is_accepted(self) -> None:
        message, ephemeral = self._start_vote(duration_days=1)

        self.assertFalse(ephemeral)
        self.assertIsNotNone(self.vote_service.get_open_round())

    def test_duration_of_thirty_days_is_accepted(self) -> None:
        message, ephemeral = self._start_vote(duration_days=30)

        self.assertFalse(ephemeral)
        self.assertIsNotNone(self.vote_service.get_open_round())

    def test_duration_of_zero_is_rejected(self) -> None:
        message, ephemeral = self._start_vote(duration_days=0)

        self.assertTrue(ephemeral)
        self.assertIn("between", message)
        self.assertIsNone(self.vote_service.get_open_round())

    def test_negative_duration_is_rejected(self) -> None:
        message, ephemeral = self._start_vote(duration_days=-1)

        self.assertTrue(ephemeral)
        self.assertIsNone(self.vote_service.get_open_round())

    def test_duration_above_thirty_is_rejected(self) -> None:
        message, ephemeral = self._start_vote(duration_days=31)

        self.assertTrue(ephemeral)
        self.assertIsNone(self.vote_service.get_open_round())

    def test_blind_mode(self) -> None:
        self._start_vote(visibility="blind")

        self.assertEqual(self.vote_service.get_open_round().visibility, VoteVisibility.BLIND)

    def test_visible_mode(self) -> None:
        self._start_vote(visibility="visible")

        self.assertEqual(self.vote_service.get_open_round().visibility, VoteVisibility.VISIBLE)

    def test_fewer_than_two_suggestions_is_rejected(self) -> None:
        self.suggestion_service.remove_suggestion("Inception")

        message, ephemeral = self._start_vote()

        self.assertTrue(ephemeral)
        self.assertIn("At least", message)
        self.assertIsNone(self.vote_service.get_open_round())

    def test_open_round_already_exists_is_rejected(self) -> None:
        self._start_vote()

        message, ephemeral = self._start_vote()

        self.assertTrue(ephemeral)
        self.assertIn("already open", message)

    def test_service_failure_is_displayed_cleanly(self) -> None:
        # An already-open round is the natural service-level failure to
        # exercise here; VoteService's own failure message is relayed
        # verbatim and marked ephemeral, with no traceback or generic error.
        self._start_vote()

        message, ephemeral = self._start_vote()

        self.assertTrue(ephemeral)
        self.assertEqual(message, "A voting round is already open.")

    def test_confirmation_contains_the_expected_details(self) -> None:
        message, ephemeral = self._start_vote(visibility="blind", duration_days=10)

        self.assertFalse(ephemeral)
        self.assertIn("Voting round 1", message)
        self.assertIn("Blind", message)
        self.assertIn("Candidates: 2", message)
        self.assertIn("Voting ends:", message)
        self.assertIn("Vote changes allowed:", message)

    def test_confirmation_never_mentions_individual_votes(self) -> None:
        message, _ = self._start_vote()

        self.assertNotIn("voter", message.lower())

    def test_low_suggestion_pool_warning_is_shown_below_ten(self) -> None:
        message, _ = self._start_vote()

        self.assertIn("suggestion pool is getting low", message.lower())
        self.assertIn("`/add`", message)
        self.assertIn("movie title or IMDb link", message)

    def test_low_suggestion_pool_warning_is_not_shown_at_ten(self) -> None:
        for index in range(3, 11):
            self.suggestion_service.suggest(f"Movie {index}")

        message, _ = self._start_vote()

        self.assertNotIn("suggestion pool is getting low", message.lower())

    def test_low_suggestion_pool_warning_is_shown_at_nine(self) -> None:
        for index in range(3, 10):
            self.suggestion_service.suggest(f"Movie {index}")

        message, _ = self._start_vote()

        self.assertIn("suggestion pool is getting low", message.lower())

    # --- /vote_status ----------------------------------------------------

    def test_vote_status_when_no_round_exists(self) -> None:
        message = perform_vote_status(self.vote_service, self.suggestion_service)

        self.assertIn("no voting round", message.lower())

    def test_vote_status_open_blind_round_hides_standings(self) -> None:
        self.vote_service.create_round(visibility=VoteVisibility.BLIND)
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=1)

        message = perform_vote_status(self.vote_service, self.suggestion_service)

        self.assertNotIn("Standings", message)

    def test_vote_status_open_visible_round_shows_standings(self) -> None:
        self.vote_service.create_round(visibility=VoteVisibility.VISIBLE)
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=1)

        message = perform_vote_status(self.vote_service, self.suggestion_service)

        self.assertIn("Standings:", message)
        self.assertIn("Suggestion #1", message)

    def test_vote_status_shows_closed_round_status(self) -> None:
        created = self.vote_service.create_round(visibility=VoteVisibility.BLIND)
        self.vote_service.close_round(created.vote_round.id)

        message = perform_vote_status(self.vote_service, self.suggestion_service)

        self.assertIn("Status: Closed", message)

    def test_vote_status_with_zero_votes(self) -> None:
        self.vote_service.create_round(visibility=VoteVisibility.VISIBLE)

        message = perform_vote_status(self.vote_service, self.suggestion_service)

        self.assertIn("Votes cast: 0", message)
        self.assertIn("no votes yet", message.lower())

    def test_vote_status_shows_total_vote_count(self) -> None:
        self.vote_service.create_round(visibility=VoteVisibility.VISIBLE)
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=1)
        self.vote_service.cast_vote(discord_user_id=2, suggestion_id=2)

        message = perform_vote_status(self.vote_service, self.suggestion_service)

        self.assertIn("Votes cast: 2", message)

    def test_vote_status_shows_the_vote_change_setting(self) -> None:
        self.vote_service.create_round(visibility=VoteVisibility.VISIBLE)

        message = perform_vote_status(self.vote_service, self.suggestion_service)

        self.assertIn("Vote changes allowed:", message)

    def test_vote_status_handles_a_standings_service_failure(self) -> None:
        self.vote_service.create_round(visibility=VoteVisibility.VISIBLE)
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=1)

        with patch.object(
            self.vote_service,
            "calculate_standings",
            return_value=StandingsResult(success=False, message="Something went wrong."),
        ):
            message = perform_vote_status(self.vote_service, self.suggestion_service)

        self.assertIn("Standings unavailable", message)
        self.assertIn("Something went wrong.", message)

    # --- /vote ------------------------------------------------------------

    def test_successful_first_vote(self) -> None:
        self.vote_service.create_round(visibility=VoteVisibility.VISIBLE)

        message, ephemeral = perform_vote(self.vote_service, user_id=111, suggestion_id=1)

        self.assertTrue(ephemeral)
        self.assertIn("recorded", message)
        self.assertIn("#1", message)
        self.assertEqual(self.vote_service.get_open_round().votes[111].suggestion_id, 1)

    def test_successful_visible_vote_shows_standings(self) -> None:
        self.vote_service.create_round(visibility=VoteVisibility.VISIBLE)

        message, ephemeral = perform_vote(self.vote_service, user_id=111, suggestion_id=1)

        self.assertTrue(ephemeral)
        self.assertIn("Standings:", message)
        self.assertIn("Suggestion #1", message)

    def test_successful_blind_vote_hides_standings(self) -> None:
        self.vote_service.create_round(visibility=VoteVisibility.BLIND)

        message, ephemeral = perform_vote(self.vote_service, user_id=111, suggestion_id=1)

        self.assertTrue(ephemeral)
        self.assertNotIn("Standings", message)

    def test_blind_vote_never_reveals_another_members_vote(self) -> None:
        self.vote_service.create_round(visibility=VoteVisibility.BLIND)
        self.vote_service.cast_vote(discord_user_id=222, suggestion_id=2)

        message, _ = perform_vote(self.vote_service, user_id=111, suggestion_id=1)

        self.assertNotIn("222", message)
        self.assertNotIn("#2", message)

    def test_duplicate_vote_for_the_same_suggestion_is_rejected(self) -> None:
        self.vote_service.create_round(visibility=VoteVisibility.VISIBLE)
        perform_vote(self.vote_service, user_id=111, suggestion_id=1)

        message, ephemeral = perform_vote(self.vote_service, user_id=111, suggestion_id=1)

        self.assertTrue(ephemeral)
        self.assertIn("already voted", message)

    def test_vote_change_is_allowed_once(self) -> None:
        self.vote_service.create_round(visibility=VoteVisibility.VISIBLE)
        perform_vote(self.vote_service, user_id=111, suggestion_id=1)

        message, ephemeral = perform_vote(self.vote_service, user_id=111, suggestion_id=2)

        self.assertTrue(ephemeral)
        self.assertIn("updated", message)
        self.assertIn("#2", message)
        self.assertEqual(self.vote_service.get_open_round().votes[111].suggestion_id, 2)

    def test_vote_change_reports_no_remaining_changes(self) -> None:
        self.vote_service.create_round(visibility=VoteVisibility.VISIBLE)
        perform_vote(self.vote_service, user_id=111, suggestion_id=1)

        message, _ = perform_vote(self.vote_service, user_id=111, suggestion_id=2)

        self.assertIn("no vote changes remaining", message.lower())

    def test_vote_changes_exhausted_is_rejected(self) -> None:
        self.vote_service.create_round(visibility=VoteVisibility.VISIBLE)
        perform_vote(self.vote_service, user_id=111, suggestion_id=1)
        perform_vote(self.vote_service, user_id=111, suggestion_id=2)

        message, ephemeral = perform_vote(self.vote_service, user_id=111, suggestion_id=1)

        self.assertTrue(ephemeral)
        self.assertIn("already used your one vote change", message)
        # The rejected attempt must not have changed the recorded vote.
        self.assertEqual(self.vote_service.get_open_round().votes[111].suggestion_id, 2)

    def test_suggestion_does_not_exist_is_rejected(self) -> None:
        self.vote_service.create_round(visibility=VoteVisibility.VISIBLE)

        message, ephemeral = perform_vote(self.vote_service, user_id=111, suggestion_id=999)

        self.assertTrue(ephemeral)
        self.assertIn("doesn't exist", message)
        self.assertEqual(self.vote_service.get_open_round().votes, {})

    def test_no_active_round_is_rejected(self) -> None:
        message, ephemeral = perform_vote(self.vote_service, user_id=111, suggestion_id=1)

        self.assertTrue(ephemeral)
        self.assertIn("no open voting round", message.lower())

    def test_closed_round_is_rejected(self) -> None:
        created = self.vote_service.create_round(visibility=VoteVisibility.VISIBLE)
        self.vote_service.close_round(created.vote_round.id)

        message, ephemeral = perform_vote(self.vote_service, user_id=111, suggestion_id=1)

        self.assertTrue(ephemeral)
        self.assertIn("no open voting round", message.lower())
        self.assertIsNone(self.vote_service.get_open_round())

    def test_confirmation_formatting_for_a_first_vote(self) -> None:
        self.vote_service.create_round(visibility=VoteVisibility.VISIBLE)

        message, _ = perform_vote(self.vote_service, user_id=111, suggestion_id=1)

        self.assertEqual(
            message,
            "Your vote for suggestion #1 has been recorded.\n\nStandings:\n1. Suggestion #1 — 1 vote",
        )

    def test_confirmation_formatting_for_a_changed_vote(self) -> None:
        self.vote_service.create_round(visibility=VoteVisibility.BLIND)
        perform_vote(self.vote_service, user_id=111, suggestion_id=1)

        message, _ = perform_vote(self.vote_service, user_id=111, suggestion_id=2)

        self.assertEqual(
            message,
            "Your vote has been updated to suggestion #2.\nYou have no vote changes remaining.",
        )

    def test_standings_only_shown_for_visible_rounds(self) -> None:
        self.vote_service.create_round(visibility=VoteVisibility.BLIND)

        message, _ = perform_vote(self.vote_service, user_id=111, suggestion_id=1)

        self.assertNotIn("Standings", message)

    def test_vote_responses_are_always_ephemeral(self) -> None:
        self.vote_service.create_round(visibility=VoteVisibility.VISIBLE)

        success_message, success_ephemeral = perform_vote(self.vote_service, user_id=111, suggestion_id=1)
        failure_message, failure_ephemeral = perform_vote(self.vote_service, user_id=111, suggestion_id=1)

        self.assertTrue(success_ephemeral)
        self.assertTrue(failure_ephemeral)

    def test_vote_service_failure_is_relayed_cleanly(self) -> None:
        self.vote_service.create_round(visibility=VoteVisibility.VISIBLE)

        with patch.object(
            self.vote_service,
            "cast_vote",
            return_value=VoteResult(success=False, message="Something unexpected happened."),
        ):
            message, ephemeral = perform_vote(self.vote_service, user_id=111, suggestion_id=1)

        self.assertTrue(ephemeral)
        self.assertEqual(message, "Something unexpected happened.")

    def test_different_members_can_vote_independently(self) -> None:
        self.vote_service.create_round(visibility=VoteVisibility.VISIBLE)

        perform_vote(self.vote_service, user_id=111, suggestion_id=1)
        perform_vote(self.vote_service, user_id=222, suggestion_id=2)

        vote_round = self.vote_service.get_open_round()
        self.assertEqual(vote_round.votes[111].suggestion_id, 1)
        self.assertEqual(vote_round.votes[222].suggestion_id, 2)


if __name__ == "__main__":
    unittest.main()
