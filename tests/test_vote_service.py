import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.vote import VoteRoundStatus, VoteVisibility
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.services.vote_service import VoteService


class FakeSuggestionLookup:
    """Minimal stand-in for SuggestionService.suggestion_exists()/suggestion_count()."""

    def __init__(self, existing_ids):
        self._existing_ids = set(existing_ids)

    def suggestion_exists(self, suggestion_id: int) -> bool:
        return suggestion_id in self._existing_ids

    def suggestion_count(self) -> int:
        return len(self._existing_ids)


class VoteServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        """Create a fresh service backed by an isolated, temporary repository."""
        self._temp_dir = tempfile.TemporaryDirectory()
        self.repository = JsonVoteRepository(Path(self._temp_dir.name) / "voting.json")
        self.suggestion_lookup = FakeSuggestionLookup(existing_ids=[1, 2, 3])
        self.service = VoteService(self.suggestion_lookup, repository=self.repository)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    # --- Round creation -----------------------------------------------

    def test_create_round_opens_a_round(self) -> None:
        result = self.service.create_round()

        self.assertTrue(result.success)
        self.assertIsNotNone(result.vote_round)
        self.assertEqual(result.vote_round.status, VoteRoundStatus.OPEN)
        self.assertEqual(result.vote_round.id, 1)

    def test_create_round_defaults_to_visible(self) -> None:
        result = self.service.create_round()
        self.assertEqual(result.vote_round.visibility, VoteVisibility.VISIBLE)

    def test_create_round_supports_blind_visibility(self) -> None:
        result = self.service.create_round(visibility=VoteVisibility.BLIND)
        self.assertEqual(result.vote_round.visibility, VoteVisibility.BLIND)

    def test_cannot_create_a_second_open_round(self) -> None:
        self.service.create_round()

        result = self.service.create_round()
        self.assertFalse(result.success)
        self.assertIn("already open", result.message)

    def test_can_create_a_new_round_after_closing_the_previous_one(self) -> None:
        first = self.service.create_round()
        self.service.close_round(first.vote_round.id)

        second = self.service.create_round()
        self.assertTrue(second.success)
        self.assertEqual(second.vote_round.id, 2)

    def test_cannot_create_a_round_with_fewer_than_two_suggestions(self) -> None:
        repository = JsonVoteRepository(Path(self._temp_dir.name) / "one_suggestion_voting.json")
        service = VoteService(FakeSuggestionLookup(existing_ids=[1]), repository=repository)

        result = service.create_round()
        self.assertFalse(result.success)
        self.assertIn("At least 2 suggestions", result.message)

    def test_cannot_create_a_round_with_no_suggestions(self) -> None:
        repository = JsonVoteRepository(Path(self._temp_dir.name) / "no_suggestions_voting.json")
        service = VoteService(FakeSuggestionLookup(existing_ids=[]), repository=repository)

        result = service.create_round()
        self.assertFalse(result.success)

    def test_can_create_a_round_with_exactly_two_suggestions(self) -> None:
        repository = JsonVoteRepository(Path(self._temp_dir.name) / "two_suggestions_voting.json")
        service = VoteService(FakeSuggestionLookup(existing_ids=[1, 2]), repository=repository)

        result = service.create_round()
        self.assertTrue(result.success)

    # --- Casting votes --------------------------------------------------

    def test_casting_a_first_vote_succeeds(self) -> None:
        self.service.create_round()

        result = self.service.cast_vote(discord_user_id=111, suggestion_id=1)
        self.assertTrue(result.success)

        open_round = self.service.get_open_round()
        self.assertEqual(open_round.votes[111].suggestion_id, 1)
        self.assertEqual(open_round.votes[111].changes_used, 0)

    def test_first_vote_sets_original_and_current_suggestion_id_to_the_same_value(self) -> None:
        self.service.create_round()
        self.service.cast_vote(discord_user_id=111, suggestion_id=1)

        vote = self.service.get_open_round().votes[111]
        self.assertEqual(vote.original_suggestion_id, 1)
        self.assertEqual(vote.suggestion_id, 1)

    def test_casting_a_vote_for_unknown_suggestion_is_rejected(self) -> None:
        self.service.create_round()

        result = self.service.cast_vote(discord_user_id=111, suggestion_id=999)
        self.assertFalse(result.success)
        self.assertIn("doesn't exist", result.message)

    def test_casting_a_vote_with_no_open_round_is_rejected(self) -> None:
        result = self.service.cast_vote(discord_user_id=111, suggestion_id=1)
        self.assertFalse(result.success)
        self.assertIn("no open voting round", result.message)

    def test_casting_a_vote_in_a_closed_round_is_rejected(self) -> None:
        created = self.service.create_round()
        self.service.close_round(created.vote_round.id)

        result = self.service.cast_vote(discord_user_id=111, suggestion_id=1)
        self.assertFalse(result.success)

    def test_changing_a_vote_once_succeeds(self) -> None:
        self.service.create_round()
        self.service.cast_vote(discord_user_id=111, suggestion_id=1)

        result = self.service.cast_vote(discord_user_id=111, suggestion_id=2)
        self.assertTrue(result.success)

        open_round = self.service.get_open_round()
        self.assertEqual(open_round.votes[111].suggestion_id, 2)
        self.assertEqual(open_round.votes[111].changes_used, 1)

    def test_changing_a_vote_preserves_the_original_suggestion_id(self) -> None:
        self.service.create_round()
        self.service.cast_vote(discord_user_id=111, suggestion_id=1)

        self.service.cast_vote(discord_user_id=111, suggestion_id=2)

        vote = self.service.get_open_round().votes[111]
        self.assertEqual(vote.original_suggestion_id, 1)

    def test_changing_a_vote_updates_the_current_suggestion_id(self) -> None:
        self.service.create_round()
        self.service.cast_vote(discord_user_id=111, suggestion_id=1)

        self.service.cast_vote(discord_user_id=111, suggestion_id=2)

        vote = self.service.get_open_round().votes[111]
        self.assertEqual(vote.suggestion_id, 2)

    def test_changing_a_vote_preserves_the_original_first_voted_at(self) -> None:
        self.service.create_round()
        self.service.cast_vote(discord_user_id=111, suggestion_id=1)
        original_first_voted_at = self.service.get_open_round().votes[111].first_voted_at

        self.service.cast_vote(discord_user_id=111, suggestion_id=2)

        updated_first_voted_at = self.service.get_open_round().votes[111].first_voted_at
        self.assertEqual(original_first_voted_at, updated_first_voted_at)

    def test_rejects_a_second_vote_change(self) -> None:
        self.service.create_round()
        self.service.cast_vote(discord_user_id=111, suggestion_id=1)
        self.service.cast_vote(discord_user_id=111, suggestion_id=2)

        result = self.service.cast_vote(discord_user_id=111, suggestion_id=3)
        self.assertFalse(result.success)
        self.assertIn("already used your one vote change", result.message)

        # The rejected change must not have been applied.
        open_round = self.service.get_open_round()
        self.assertEqual(open_round.votes[111].suggestion_id, 2)
        self.assertEqual(open_round.votes[111].changes_used, 1)

    def test_revoting_for_the_same_suggestion_is_rejected_and_does_not_consume_a_change(self) -> None:
        self.service.create_round()
        self.service.cast_vote(discord_user_id=111, suggestion_id=1)

        result = self.service.cast_vote(discord_user_id=111, suggestion_id=1)
        self.assertFalse(result.success)

        open_round = self.service.get_open_round()
        self.assertEqual(open_round.votes[111].changes_used, 0)

    def test_different_members_vote_independently(self) -> None:
        self.service.create_round()
        self.service.cast_vote(discord_user_id=111, suggestion_id=1)
        self.service.cast_vote(discord_user_id=222, suggestion_id=2)

        open_round = self.service.get_open_round()
        self.assertEqual(open_round.votes[111].suggestion_id, 1)
        self.assertEqual(open_round.votes[222].suggestion_id, 2)
        self.assertEqual(len(open_round.votes), 2)

    # --- Closing and retrieving rounds -----------------------------------

    def test_closing_a_round_succeeds(self) -> None:
        created = self.service.create_round()

        result = self.service.close_round(created.vote_round.id)
        self.assertTrue(result.success)
        self.assertEqual(self.service.get_round(created.vote_round.id).status, VoteRoundStatus.CLOSED)

    def test_closing_an_already_closed_round_is_rejected(self) -> None:
        created = self.service.create_round()
        self.service.close_round(created.vote_round.id)

        result = self.service.close_round(created.vote_round.id)
        self.assertFalse(result.success)

    def test_closing_an_unknown_round_is_rejected(self) -> None:
        result = self.service.close_round(999)
        self.assertFalse(result.success)
        self.assertIn("doesn't exist", result.message)

    def test_get_open_round_returns_none_when_nothing_is_open(self) -> None:
        self.assertIsNone(self.service.get_open_round())

    def test_get_round_retrieves_a_round_by_id(self) -> None:
        created = self.service.create_round()

        fetched = self.service.get_round(created.vote_round.id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.id, created.vote_round.id)

    def test_get_round_returns_none_for_unknown_id(self) -> None:
        self.assertIsNone(self.service.get_round(999))

    def test_get_latest_round_returns_none_when_no_rounds_exist(self) -> None:
        self.assertIsNone(self.service.get_latest_round())

    def test_get_latest_round_returns_the_only_round(self) -> None:
        created = self.service.create_round()

        latest = self.service.get_latest_round()
        self.assertEqual(latest.id, created.vote_round.id)

    def test_get_latest_round_returns_the_most_recently_created_round(self) -> None:
        first = self.service.create_round()
        self.service.close_round(first.vote_round.id)
        second = self.service.create_round()

        latest = self.service.get_latest_round()
        self.assertEqual(latest.id, second.vote_round.id)

    def test_get_latest_round_returns_a_closed_round_if_it_is_the_most_recent(self) -> None:
        created = self.service.create_round()
        self.service.close_round(created.vote_round.id)

        latest = self.service.get_latest_round()
        self.assertEqual(latest.status, VoteRoundStatus.CLOSED)

    # --- Discord message reference ------------------------------------------

    def test_attach_message_reference_updates_the_round(self) -> None:
        created = self.service.create_round()

        updated = self.service.attach_message_reference(
            created.vote_round.id, guild_id=100, channel_id=200, message_id=300
        )

        self.assertTrue(updated)
        vote_round = self.service.get_round(created.vote_round.id)
        self.assertEqual(vote_round.guild_id, 100)
        self.assertEqual(vote_round.channel_id, 200)
        self.assertEqual(vote_round.message_id, 300)

    def test_attach_message_reference_persists_the_update(self) -> None:
        created = self.service.create_round()
        self.service.attach_message_reference(created.vote_round.id, guild_id=100, channel_id=200, message_id=300)

        reloaded = self.repository.load()
        self.assertEqual(reloaded.rounds[0].message_id, 300)

    def test_attach_message_reference_returns_false_for_an_unknown_round(self) -> None:
        updated = self.service.attach_message_reference(999, guild_id=100, channel_id=200, message_id=300)
        self.assertFalse(updated)

    # --- Reset behaviors --------------------------------------------------

    def test_remove_member_vote_deletes_the_vote_entirely(self) -> None:
        created = self.service.create_round()
        self.service.cast_vote(discord_user_id=111, suggestion_id=1)

        result = self.service.remove_member_vote(created.vote_round.id, 111)
        self.assertTrue(result.success)
        self.assertNotIn(111, self.service.get_round(created.vote_round.id).votes)

    def test_member_can_vote_again_after_their_vote_is_removed(self) -> None:
        created = self.service.create_round()
        self.service.cast_vote(discord_user_id=111, suggestion_id=1)
        self.service.remove_member_vote(created.vote_round.id, 111)

        result = self.service.cast_vote(discord_user_id=111, suggestion_id=2)
        self.assertTrue(result.success)
        new_vote = self.service.get_open_round().votes[111]
        self.assertEqual(new_vote.changes_used, 0)
        # A fresh vote after removal starts a new "original" pick.
        self.assertEqual(new_vote.original_suggestion_id, 2)
        self.assertEqual(new_vote.suggestion_id, 2)

    def test_remove_member_vote_for_a_member_who_has_not_voted_is_rejected(self) -> None:
        created = self.service.create_round()

        result = self.service.remove_member_vote(created.vote_round.id, 111)
        self.assertFalse(result.success)

    def test_reset_member_vote_changes_keeps_the_vote_but_restores_the_allowance(self) -> None:
        created = self.service.create_round()
        self.service.cast_vote(discord_user_id=111, suggestion_id=1)
        self.service.cast_vote(discord_user_id=111, suggestion_id=2)

        result = self.service.reset_member_vote_changes(created.vote_round.id, 111)
        self.assertTrue(result.success)

        vote = self.service.get_round(created.vote_round.id).votes[111]
        self.assertEqual(vote.suggestion_id, 2)
        self.assertEqual(vote.changes_used, 0)

    def test_reset_member_vote_changes_preserves_both_suggestion_ids(self) -> None:
        created = self.service.create_round()
        self.service.cast_vote(discord_user_id=111, suggestion_id=1)
        self.service.cast_vote(discord_user_id=111, suggestion_id=2)

        self.service.reset_member_vote_changes(created.vote_round.id, 111)

        vote = self.service.get_round(created.vote_round.id).votes[111]
        self.assertEqual(vote.original_suggestion_id, 1)
        self.assertEqual(vote.suggestion_id, 2)

    def test_reset_member_vote_changes_allows_a_further_change(self) -> None:
        created = self.service.create_round()
        self.service.cast_vote(discord_user_id=111, suggestion_id=1)
        self.service.cast_vote(discord_user_id=111, suggestion_id=2)
        self.service.reset_member_vote_changes(created.vote_round.id, 111)

        result = self.service.cast_vote(discord_user_id=111, suggestion_id=3)
        self.assertTrue(result.success)
        self.assertEqual(self.service.get_open_round().votes[111].suggestion_id, 3)

    def test_reset_member_vote_changes_for_a_member_who_has_not_voted_is_rejected(self) -> None:
        created = self.service.create_round()

        result = self.service.reset_member_vote_changes(created.vote_round.id, 111)
        self.assertFalse(result.success)


class VoteServiceStandingsAndWinnersTests(unittest.TestCase):
    def setUp(self) -> None:
        """A fresh service with a wider pool of valid suggestion IDs for tie scenarios."""
        self._temp_dir = tempfile.TemporaryDirectory()
        self.repository = JsonVoteRepository(Path(self._temp_dir.name) / "voting.json")
        self.suggestion_lookup = FakeSuggestionLookup(existing_ids=range(1, 11))
        self.service = VoteService(self.suggestion_lookup, repository=self.repository)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    # --- Standings --------------------------------------------------------

    def test_standings_for_a_missing_round_is_a_failure(self) -> None:
        result = self.service.calculate_standings(999)

        self.assertFalse(result.success)
        self.assertEqual(result.standings, [])

    def test_standings_are_empty_when_no_votes_have_been_cast(self) -> None:
        created = self.service.create_round()

        result = self.service.calculate_standings(created.vote_round.id)
        self.assertTrue(result.success)
        self.assertEqual(result.standings, [])

    def test_standings_with_a_single_vote(self) -> None:
        created = self.service.create_round()
        self.service.cast_vote(discord_user_id=111, suggestion_id=1)

        result = self.service.calculate_standings(created.vote_round.id)
        self.assertEqual(len(result.standings), 1)
        self.assertEqual(result.standings[0].suggestion_id, 1)
        self.assertEqual(result.standings[0].vote_count, 1)

    def test_standings_tally_multiple_suggestions_with_different_totals(self) -> None:
        created = self.service.create_round()
        self.service.cast_vote(discord_user_id=111, suggestion_id=1)
        self.service.cast_vote(discord_user_id=222, suggestion_id=1)
        self.service.cast_vote(discord_user_id=333, suggestion_id=2)

        result = self.service.calculate_standings(created.vote_round.id)
        counts = {entry.suggestion_id: entry.vote_count for entry in result.standings}
        self.assertEqual(counts, {1: 2, 2: 1})

    def test_standings_sort_by_vote_count_descending(self) -> None:
        created = self.service.create_round()
        self.service.cast_vote(discord_user_id=111, suggestion_id=2)
        self.service.cast_vote(discord_user_id=222, suggestion_id=1)
        self.service.cast_vote(discord_user_id=333, suggestion_id=1)

        result = self.service.calculate_standings(created.vote_round.id)
        ordered_ids = [entry.suggestion_id for entry in result.standings]
        self.assertEqual(ordered_ids, [1, 2])

    def test_standings_break_ties_by_ascending_suggestion_id(self) -> None:
        created = self.service.create_round()
        self.service.cast_vote(discord_user_id=111, suggestion_id=3)
        self.service.cast_vote(discord_user_id=222, suggestion_id=1)
        self.service.cast_vote(discord_user_id=333, suggestion_id=2)

        result = self.service.calculate_standings(created.vote_round.id)
        ordered_ids = [entry.suggestion_id for entry in result.standings]
        # All tied at one vote each: deterministic order is ascending ID.
        self.assertEqual(ordered_ids, [1, 2, 3])

    def test_standings_reflect_a_changed_vote_under_the_new_suggestion(self) -> None:
        created = self.service.create_round()
        self.service.cast_vote(discord_user_id=111, suggestion_id=1)
        self.service.cast_vote(discord_user_id=111, suggestion_id=2)

        result = self.service.calculate_standings(created.vote_round.id)
        counts = {entry.suggestion_id: entry.vote_count for entry in result.standings}
        self.assertEqual(counts, {2: 1})
        self.assertNotIn(1, counts)

    def test_standings_use_current_suggestion_id_not_original(self) -> None:
        created = self.service.create_round()
        self.service.cast_vote(discord_user_id=111, suggestion_id=1)
        self.service.cast_vote(discord_user_id=111, suggestion_id=2)
        self.service.cast_vote(discord_user_id=222, suggestion_id=2)

        vote = self.service.get_round(created.vote_round.id).votes[111]
        self.assertEqual(vote.original_suggestion_id, 1)  # sanity check on the fixture

        result = self.service.calculate_standings(created.vote_round.id)
        counts = {entry.suggestion_id: entry.vote_count for entry in result.standings}
        # Suggestion 1 got zero votes counted even though it was member 111's
        # original pick; only the current suggestion_id (2) is tallied.
        self.assertEqual(counts, {2: 2})

    def test_standings_work_for_a_closed_round(self) -> None:
        created = self.service.create_round()
        self.service.cast_vote(discord_user_id=111, suggestion_id=1)
        self.service.close_round(created.vote_round.id)

        result = self.service.calculate_standings(created.vote_round.id)
        self.assertTrue(result.success)
        self.assertEqual(result.standings[0].suggestion_id, 1)

    # --- Winners ------------------------------------------------------------

    def test_winners_for_a_missing_round_is_a_failure(self) -> None:
        result = self.service.get_current_winners(999)

        self.assertFalse(result.success)
        self.assertEqual(result.winning_suggestion_ids, [])

    def test_no_winners_when_no_votes_have_been_cast(self) -> None:
        created = self.service.create_round()

        result = self.service.get_current_winners(created.vote_round.id)
        self.assertTrue(result.success)
        self.assertEqual(result.winning_suggestion_ids, [])

    def test_a_single_clear_winner(self) -> None:
        created = self.service.create_round()
        self.service.cast_vote(discord_user_id=111, suggestion_id=1)
        self.service.cast_vote(discord_user_id=222, suggestion_id=1)
        self.service.cast_vote(discord_user_id=333, suggestion_id=2)

        result = self.service.get_current_winners(created.vote_round.id)
        self.assertEqual(result.winning_suggestion_ids, [1])

    def test_a_two_way_tie_returns_both_winners(self) -> None:
        created = self.service.create_round()
        self.service.cast_vote(discord_user_id=111, suggestion_id=1)
        self.service.cast_vote(discord_user_id=222, suggestion_id=2)

        result = self.service.get_current_winners(created.vote_round.id)
        self.assertEqual(result.winning_suggestion_ids, [1, 2])

    def test_a_multi_way_tie_returns_every_tied_winner(self) -> None:
        created = self.service.create_round()
        self.service.cast_vote(discord_user_id=111, suggestion_id=3)
        self.service.cast_vote(discord_user_id=222, suggestion_id=1)
        self.service.cast_vote(discord_user_id=333, suggestion_id=2)
        # A fourth suggestion that ends up with zero votes should not
        # appear as a winner alongside the three-way tie.
        self.service.cast_vote(discord_user_id=444, suggestion_id=4)
        self.service.remove_member_vote(created.vote_round.id, 444)

        result = self.service.get_current_winners(created.vote_round.id)
        self.assertEqual(result.winning_suggestion_ids, [1, 2, 3])

    def test_winners_work_for_an_open_round(self) -> None:
        created = self.service.create_round()
        self.service.cast_vote(discord_user_id=111, suggestion_id=1)

        result = self.service.get_current_winners(created.vote_round.id)
        self.assertEqual(result.winning_suggestion_ids, [1])

    def test_winners_work_for_a_closed_round(self) -> None:
        created = self.service.create_round()
        self.service.cast_vote(discord_user_id=111, suggestion_id=1)
        self.service.close_round(created.vote_round.id)

        result = self.service.get_current_winners(created.vote_round.id)
        self.assertEqual(result.winning_suggestion_ids, [1])

    def test_calculating_winners_does_not_close_the_round(self) -> None:
        created = self.service.create_round()
        self.service.cast_vote(discord_user_id=111, suggestion_id=1)

        self.service.get_current_winners(created.vote_round.id)

        self.assertEqual(self.service.get_round(created.vote_round.id).status, VoteRoundStatus.OPEN)

    def test_calculating_winners_does_not_write_winning_suggestion_id(self) -> None:
        created = self.service.create_round()
        self.service.cast_vote(discord_user_id=111, suggestion_id=1)

        self.service.get_current_winners(created.vote_round.id)

        self.assertIsNone(self.service.get_round(created.vote_round.id).winning_suggestion_id)

    def test_calculating_winners_on_a_tie_does_not_write_winning_suggestion_id(self) -> None:
        created = self.service.create_round()
        self.service.cast_vote(discord_user_id=111, suggestion_id=1)
        self.service.cast_vote(discord_user_id=222, suggestion_id=2)

        self.service.get_current_winners(created.vote_round.id)

        self.assertIsNone(self.service.get_round(created.vote_round.id).winning_suggestion_id)

    def test_winners_use_current_suggestion_id_not_original(self) -> None:
        created = self.service.create_round()
        self.service.cast_vote(discord_user_id=111, suggestion_id=1)
        self.service.cast_vote(discord_user_id=111, suggestion_id=2)

        result = self.service.get_current_winners(created.vote_round.id)
        self.assertEqual(result.winning_suggestion_ids, [2])


if __name__ == "__main__":
    unittest.main()

class VoteServiceCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        repository = JsonVoteRepository(Path(self._temp_dir.name) / "candidate_voting.json")
        self.service = VoteService(FakeSuggestionLookup(existing_ids=[1, 2, 3, 4]), repository=repository)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_create_round_persists_exact_candidate_ids(self) -> None:
        result = self.service.create_round(candidate_suggestion_ids=[3, 1, 2])
        self.assertTrue(result.success)
        self.assertEqual(result.vote_round.candidate_suggestion_ids, [3, 1, 2])

    def test_create_round_rejects_duplicate_candidate_ids(self) -> None:
        result = self.service.create_round(candidate_suggestion_ids=[1, 1, 2])
        self.assertFalse(result.success)

    def test_create_round_rejects_missing_candidate(self) -> None:
        result = self.service.create_round(candidate_suggestion_ids=[1, 2, 99])
        self.assertFalse(result.success)

    def test_vote_for_non_nominee_is_rejected(self) -> None:
        self.service.create_round(candidate_suggestion_ids=[1, 2, 3])
        result = self.service.cast_vote(discord_user_id=123, suggestion_id=4)
        self.assertFalse(result.success)
        self.assertIn("not a nominee", result.message)

class VoteServiceDatabaseHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.service = VoteService(
            FakeSuggestionLookup(existing_ids=[1, 2, 3, 4]),
            repository=JsonVoteRepository(Path(self._temp_dir.name) / "voting.json"),
        )

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _closed_round(self, database_id: int, candidates: list[int]):
        created = self.service.create_round(
            candidate_suggestion_ids=candidates,
            database_id=database_id,
        )
        self.service.close_round(created.vote_round.id)
        return created.vote_round

    def test_create_round_stores_database_id(self) -> None:
        created = self.service.create_round(
            candidate_suggestion_ids=[1, 2], database_id=5
        )
        self.assertEqual(created.vote_round.database_id, 5)

    def test_recent_closed_rounds_can_be_filtered_by_database(self) -> None:
        first_a = self._closed_round(10, [1, 2])
        self._closed_round(20, [3, 4])
        second_a = self._closed_round(10, [2, 3])

        rounds = self.service.get_recent_closed_rounds(5, database_id=10)

        self.assertEqual([round_.id for round_ in rounds], [second_a.id, first_a.id])

    def test_limit_is_applied_after_database_filtering(self) -> None:
        old_a = self._closed_round(10, [1, 2])
        for _ in range(3):
            self._closed_round(20, [3, 4])
        new_a = self._closed_round(10, [2, 3])

        rounds = self.service.get_recent_closed_rounds(2, database_id=10)

        self.assertEqual([round_.id for round_ in rounds], [new_a.id, old_a.id])

    def test_legacy_round_does_not_affect_database_specific_history(self) -> None:
        legacy = self.service.create_round(candidate_suggestion_ids=[1, 2])
        self.service.close_round(legacy.vote_round.id)
        scoped = self._closed_round(10, [2, 3])

        rounds = self.service.get_recent_closed_rounds(5, database_id=10)

        self.assertEqual([round_.id for round_ in rounds], [scoped.id])
