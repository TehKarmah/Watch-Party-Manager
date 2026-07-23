import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.vote import VoteVisibility
from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.services.nominee_selection_service import NomineeSelectionService
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.vote_service import VoteService


class NomineeSelectionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        """Real SuggestionService and VoteService, both backed by temp files."""
        self._temp_dir = tempfile.TemporaryDirectory()
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(Path(self._temp_dir.name) / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(
                Path(self._temp_dir.name) / "suggestion_databases.json"
            ),
        )
        self.vote_service = VoteService(
            self.suggestion_service, repository=JsonVoteRepository(Path(self._temp_dir.name) / "voting.json")
        )
        self.selector = NomineeSelectionService(self.suggestion_service, self.vote_service)
        self.database_id = self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=100, channel_id=200
        ).database.database_id

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _suggest(self, title: str, genres=(), media_type=None) -> int:
        from watch_party_manager.domain.watch_item import MediaType

        result = self.suggestion_service.suggest(title, database_id=self.database_id)
        watch_item = result.watch_item
        # suggest() doesn't take genres/media_type directly; set them
        # afterward on the stored instance for test purposes.
        if genres:
            watch_item.genres = tuple(genres)
        if media_type is not None:
            watch_item.media_type = media_type
        return watch_item.id

    # --- Basic selection behavior --------------------------------------------

    def test_selection_returns_requested_count(self) -> None:
        for title in ("A", "B", "C", "D", "E"):
            self._suggest(title)

        selected = self.selector.select_nominees(self.database_id, 3, rng=random.Random(1))

        self.assertEqual(len(selected), 3)
        self.assertEqual(len({item.id for item in selected}), 3)

    def test_rejects_a_non_positive_count(self) -> None:
        with self.assertRaises(ValueError):
            self.selector.select_nominees(self.database_id, 0)

    def test_selection_limited_to_the_current_database(self) -> None:
        other_database_id = self.suggestion_service.create_database(
            "Kung Fu Movies", guild_id=100, channel_id=201
        ).database.database_id
        for title in ("A", "B", "C"):
            self._suggest(title)
        self.suggestion_service.suggest("Enter the Dragon", database_id=other_database_id)
        self.suggestion_service.suggest("Ip Man", database_id=other_database_id)

        selected = self.selector.select_nominees(self.database_id, 2, rng=random.Random(1))

        self.assertTrue(all(item.database_id == self.database_id for item in selected))

    # --- Diversity preference -------------------------------------------------

    def test_prefers_diverse_genres_over_repeated_ones(self) -> None:
        self._suggest("Action 1", genres=["Action"])
        self._suggest("Action 2", genres=["Action"])
        self._suggest("Action 3", genres=["Action"])
        self._suggest("Comedy 1", genres=["Comedy"])

        selected = self.selector.select_nominees(self.database_id, 2, rng=random.Random(3))
        genres_selected = {genre for item in selected for genre in item.genres}

        # With four candidates and three sharing a genre, a diversity-aware
        # selection should include the one genuinely different title.
        self.assertIn("Comedy", genres_selected)

    def test_gracefully_handles_missing_genre_metadata(self) -> None:
        self._suggest("No Genres 1")
        self._suggest("No Genres 2")
        self._suggest("No Genres 3")

        # Should not raise despite every candidate having empty genres.
        selected = self.selector.select_nominees(self.database_id, 2, rng=random.Random(1))
        self.assertEqual(len(selected), 2)

    # --- Rotation: recent winners and nominees -------------------------------

    def _close_round_with_winner(self, candidate_ids, winner_id, voter_id) -> None:
        created = self.vote_service.create_round(
            visibility=VoteVisibility.VISIBLE,
            candidate_suggestion_ids=candidate_ids,
            database_id=self.database_id,
        )
        self.vote_service.cast_vote(discord_user_id=voter_id, suggestion_id=winner_id)
        self.vote_service.close_round(created.vote_round.id)

    def test_recent_winner_is_significantly_deprioritized(self) -> None:
        ids = [self._suggest(title) for title in ("A", "B", "C", "D", "E")]
        winner_id = ids[0]
        self._close_round_with_winner(ids[:3], winner_id, voter_id=1)

        counts = {winner_id: 0}
        for seed in range(200):
            selected = self.selector.select_nominees(self.database_id, 2, rng=random.Random(seed))
            if winner_id in [item.id for item in selected]:
                counts[winner_id] += 1

        # A recent winner should be picked far less than half the time
        # across many trials, though never literally zero.
        self.assertLess(counts[winner_id], 60)

    def test_recent_winner_is_never_permanently_excluded(self) -> None:
        ids = [self._suggest(title) for title in ("A", "B", "C")]
        winner_id = ids[0]
        self._close_round_with_winner(ids, winner_id, voter_id=1)

        picked_at_least_once = False
        for seed in range(500):
            selected = self.selector.select_nominees(self.database_id, 2, rng=random.Random(seed))
            if winner_id in [item.id for item in selected]:
                picked_at_least_once = True
                break

        self.assertTrue(picked_at_least_once)

    def test_recent_nominee_receives_reduced_priority_relative_to_a_fresh_title(self) -> None:
        ids = [self._suggest(title) for title in ("A", "B", "C", "D")]
        nominated_but_not_won = ids[0]
        # A closed round where nobody voted -- A and B were nominated but
        # there's no winner to speak of, just rotation history.
        created = self.vote_service.create_round(
            visibility=VoteVisibility.VISIBLE,
            candidate_suggestion_ids=ids[:2],
            database_id=self.database_id,
        )
        self.vote_service.close_round(created.vote_round.id)

        fresh_title_wins = 0
        recent_nominee_wins = 0
        for seed in range(200):
            selected_ids = {
                item.id for item in self.selector.select_nominees(self.database_id, 1, rng=random.Random(seed))
            }
            if nominated_but_not_won in selected_ids:
                recent_nominee_wins += 1
            if ids[2] in selected_ids or ids[3] in selected_ids:
                fresh_title_wins += 1

        self.assertGreater(fresh_title_wins, recent_nominee_wins)

    def test_only_considers_the_configured_number_of_recent_rounds(self) -> None:
        ids = [self._suggest(title) for title in ("A", "B", "C", "D", "E", "F")]
        old_winner = ids[0]
        self._close_round_with_winner(ids[:2], old_winner, voter_id=1)

        # Push the "old" round outside the recency window with several more.
        selector = NomineeSelectionService(
            self.suggestion_service, self.vote_service, recent_rounds_considered=1
        )
        for _ in range(2):
            other_round = self.vote_service.create_round(
                visibility=VoteVisibility.VISIBLE,
                candidate_suggestion_ids=ids[2:4],
                database_id=self.database_id,
            )
            self.vote_service.close_round(other_round.vote_round.id)

        recent_nominee_ids, recent_winner_ids = selector._recent_rotation_context(self.database_id)
        self.assertNotIn(old_winner, recent_winner_ids)

    def test_other_database_history_does_not_affect_this_database_rotation(self) -> None:
        other_database_id = self.suggestion_service.create_database(
            "Other Watch Party", guild_id=100, channel_id=201
        ).database.database_id
        ids = [self._suggest(title) for title in ("A", "B", "C", "D")]
        other_ids = []
        for title in ("X", "Y"):
            other_ids.append(
                self.suggestion_service.suggest(title, database_id=other_database_id).watch_item.id
            )

        round_a = self.vote_service.create_round(
            visibility=VoteVisibility.VISIBLE,
            candidate_suggestion_ids=ids[:2],
            database_id=self.database_id,
        )
        self.vote_service.close_round(round_a.vote_round.id)
        round_b = self.vote_service.create_round(
            visibility=VoteVisibility.VISIBLE,
            candidate_suggestion_ids=other_ids,
            database_id=other_database_id,
        )
        self.vote_service.close_round(round_b.vote_round.id)

        nominee_ids, _ = self.selector._recent_rotation_context(self.database_id)

        self.assertEqual(nominee_ids, set(ids[:2]))
        self.assertTrue(nominee_ids.isdisjoint(other_ids))

    # --- Low pool behavior -----------------------------------------------------

    def test_uses_every_eligible_suggestion_when_pool_is_below_requested_count(self) -> None:
        self._suggest("A")
        self._suggest("B")

        selected = self.selector.select_nominees(self.database_id, 5, rng=random.Random(1))

        self.assertEqual(len(selected), 2)

    def test_rejects_when_fewer_than_two_eligible_suggestions_exist(self) -> None:
        self._suggest("A")

        selected = self.selector.select_nominees(self.database_id, 3, rng=random.Random(1))

        self.assertEqual(selected, [])

    def test_rejects_when_no_eligible_suggestions_exist(self) -> None:
        selected = self.selector.select_nominees(self.database_id, 3, rng=random.Random(1))
        self.assertEqual(selected, [])

    def test_low_pool_selection_still_respects_database_scope(self) -> None:
        other_database_id = self.suggestion_service.create_database(
            "Kung Fu Movies", guild_id=100, channel_id=201
        ).database.database_id
        self._suggest("A")
        self._suggest("B")
        self.suggestion_service.suggest("Enter the Dragon", database_id=other_database_id)

        selected = self.selector.select_nominees(self.database_id, 5, rng=random.Random(1))

        self.assertEqual(len(selected), 2)
        self.assertTrue(all(item.database_id == self.database_id for item in selected))

    # --- Determinism for testing / production randomness ---------------------

    def test_selection_is_deterministic_with_a_seeded_rng(self) -> None:
        for title in ("A", "B", "C", "D", "E"):
            self._suggest(title)

        first = [item.id for item in self.selector.select_nominees(self.database_id, 3, rng=random.Random(42))]
        second = [item.id for item in self.selector.select_nominees(self.database_id, 3, rng=random.Random(42))]

        self.assertEqual(first, second)

    def test_selection_defaults_to_system_randomness_in_production(self) -> None:
        for title in ("A", "B", "C"):
            self._suggest(title)

        # No rng supplied -- should not raise, and should use SystemRandom
        # internally rather than requiring a seed.
        selected = self.selector.select_nominees(self.database_id, 2)
        self.assertEqual(len(selected), 2)


class FakeCandidateSelectionStrategy:
    """A minimal CandidateSelectionStrategy for testing NomineeSelectionService's integration."""

    def __init__(self, pool, weights=None) -> None:
        self._pool = pool
        self._weights = weights or {}
        self.presented_calls: list[tuple[int, list[int]]] = []

    def candidate_pool(self, database_id: int):
        return list(self._pool)

    def weight_for(self, watch_item) -> float:
        return self._weights.get(watch_item.id, 1.0)

    def on_presented(self, database_id: int, suggestion_ids) -> None:
        self.presented_calls.append((database_id, list(suggestion_ids)))


class NomineeSelectionServiceStrategyIntegrationTests(unittest.TestCase):
    """FR-033B: NomineeSelectionService consults an optional strategy."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(Path(self._temp_dir.name) / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(
                Path(self._temp_dir.name) / "suggestion_databases.json"
            ),
        )
        self.vote_service = VoteService(
            self.suggestion_service, repository=JsonVoteRepository(Path(self._temp_dir.name) / "voting.json")
        )
        self.selector = NomineeSelectionService(self.suggestion_service, self.vote_service)
        self.database_id = self.suggestion_service.create_database(
            "Sunday Watch Party", guild_id=100, channel_id=200
        ).database.database_id

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _suggest(self, title: str) -> int:
        result = self.suggestion_service.suggest(title, database_id=self.database_id)
        return result.watch_item.id

    def test_strategy_candidate_pool_replaces_the_default_lookup(self) -> None:
        self._suggest("A")
        self._suggest("B")
        only_item_id = self._suggest("C")
        only_item = self.suggestion_service.get_suggestion(only_item_id)
        second_item = self.suggestion_service.get_suggestion(self._suggest("D"))
        strategy = FakeCandidateSelectionStrategy(pool=[only_item, second_item])

        selected = self.selector.select_nominees(self.database_id, 2, rng=random.Random(1), strategy=strategy)

        selected_ids = {item.id for item in selected}
        self.assertEqual(selected_ids, {only_item.id, second_item.id})

    def test_a_strategy_with_fewer_than_two_candidates_returns_empty(self) -> None:
        item = self.suggestion_service.get_suggestion(self._suggest("A"))
        strategy = FakeCandidateSelectionStrategy(pool=[item])

        selected = self.selector.select_nominees(self.database_id, 2, rng=random.Random(1), strategy=strategy)

        self.assertEqual(selected, [])

    def test_on_presented_is_called_with_the_final_selection_low_pool_path(self) -> None:
        item_a = self.suggestion_service.get_suggestion(self._suggest("A"))
        item_b = self.suggestion_service.get_suggestion(self._suggest("B"))
        strategy = FakeCandidateSelectionStrategy(pool=[item_a, item_b])

        selected = self.selector.select_nominees(self.database_id, 5, rng=random.Random(1), strategy=strategy)

        self.assertEqual(len(strategy.presented_calls), 1)
        called_database_id, called_ids = strategy.presented_calls[0]
        self.assertEqual(called_database_id, self.database_id)
        self.assertEqual(set(called_ids), {item.id for item in selected})

    def test_on_presented_is_called_with_the_final_selection_full_pool_path(self) -> None:
        for title in ("A", "B", "C", "D", "E"):
            self._suggest(title)
        pool = [self.suggestion_service.get_suggestion(item_id) for item_id in range(1, 6)]
        strategy = FakeCandidateSelectionStrategy(pool=pool)

        selected = self.selector.select_nominees(self.database_id, 3, rng=random.Random(1), strategy=strategy)

        self.assertEqual(len(strategy.presented_calls), 1)
        _, called_ids = strategy.presented_calls[0]
        self.assertEqual(set(called_ids), {item.id for item in selected})

    def test_a_low_weighted_candidate_is_picked_less_often(self) -> None:
        for title in ("Favored", "Disfavored"):
            self._suggest(title)
        favored = self.suggestion_service.get_suggestion(1)
        disfavored = self.suggestion_service.get_suggestion(2)
        strategy = FakeCandidateSelectionStrategy(pool=[favored, disfavored], weights={disfavored.id: 0.0001})

        first_picks = [
            self.selector.select_nominees(self.database_id, 1, rng=random.Random(seed), strategy=strategy)
            for seed in range(30)
        ]
        # select_nominees only returns the full pool when it's <= count;
        # here count=1 < pool size 2, so the weighted branch runs and
        # near-zero-weighted candidates should almost never be first.
        favored_first_count = sum(1 for picks in first_picks if picks and picks[0].id == favored.id)
        self.assertGreater(favored_first_count, 20)

    def test_no_strategy_preserves_pre_existing_behavior(self) -> None:
        for title in ("A", "B", "C"):
            self._suggest(title)

        with_strategy = self.selector.select_nominees(self.database_id, 2, rng=random.Random(7), strategy=None)
        without_strategy_param = self.selector.select_nominees(self.database_id, 2, rng=random.Random(7))

        self.assertEqual([item.id for item in with_strategy], [item.id for item in without_strategy_param])


if __name__ == "__main__":
    unittest.main()
