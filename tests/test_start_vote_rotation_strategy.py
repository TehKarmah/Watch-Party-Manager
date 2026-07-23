"""Tests for FR-033B's candidate-selection strategy wiring into /start_vote.

Exercises watch_party_manager.bot.perform_start_vote directly (its own
documented pure-logic entry point) rather than the full Discord command
chain, since rotation_service/suggestion_database_configuration_repository
are threaded straight through to it.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from watch_party_manager.bot import perform_start_vote
from watch_party_manager.domain.suggestion_database_configuration import (
    CandidateSelectionMode,
    SuggestionDatabaseConfiguration,
    SuggestionRulesConfig,
)
from watch_party_manager.persistence.rotation_repository import JsonRotationRepository
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.services.nominee_selection_service import NomineeSelectionService
from watch_party_manager.services.rotation_service import RotationService
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.vote_service import VoteService

GUILD_ID = 100
CHANNEL_ID = 200
WASH_CREW_ROLE_ID = 999


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, role_ids=()) -> None:
        self.roles = [FakeRole(role_id) for role_id in role_ids]


class StartVoteRotationStrategyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.vote_service = VoteService(
            self.suggestion_service, repository=JsonVoteRepository(root / "voting.json")
        )
        self.nominee_selection_service = NomineeSelectionService(self.suggestion_service, self.vote_service)
        self.rotation_service = RotationService(
            self.suggestion_service, repository=JsonRotationRepository(root / "rotations.json")
        )
        self.configuration_repository = SuggestionDatabaseConfigurationRepository(
            root / "suggestion_database_configurations.json"
        )
        self.database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        for title in ("A", "B", "C"):
            self.suggestion_service.suggest(title, database_id=self.database.database_id)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _configure_mode(self, mode: CandidateSelectionMode) -> None:
        self.configuration_repository.save(
            SuggestionDatabaseConfiguration(
                guild_id=GUILD_ID,
                database_id=self.database.database_id,
                display_name="Movie Night",
                suggestion_rules=SuggestionRulesConfig(candidate_selection=mode),
            )
        )

    def _start_vote(self, *, nominee_count=2, rng_seed=1):
        message, ephemeral = perform_start_vote(
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            user=FakeMember([WASH_CREW_ROLE_ID]),
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            visibility_str="visible",
            duration_days=None,
            nominee_count=nominee_count,
            guild_id=GUILD_ID,
            channel_id=CHANNEL_ID,
            rotation_service=self.rotation_service,
            suggestion_database_configuration_repository=self.configuration_repository,
        )
        return message, ephemeral


class RotationPoolModeTests(StartVoteRotationStrategyTestCase):
    def test_starting_a_vote_records_presentation_in_the_rotation(self) -> None:
        self._configure_mode(CandidateSelectionMode.ROTATION_POOL)

        message, ephemeral = self._start_vote(nominee_count=2)

        self.assertFalse(ephemeral)
        vote_round = self.vote_service.get_open_round()
        rotation = self.rotation_service.get_open_rotation(self.database.database_id)
        for candidate_id in vote_round.candidate_suggestion_ids:
            refreshed = self.suggestion_service.get_suggestion(candidate_id)
            self.assertIn(rotation.id, refreshed.journey.rotation_history)

    def test_a_presented_candidate_is_excluded_from_a_second_vote(self) -> None:
        self._configure_mode(CandidateSelectionMode.ROTATION_POOL)
        self._start_vote(nominee_count=2)
        first_round = self.vote_service.get_open_round()
        self.vote_service.close_round(first_round.id)
        presented_ids = set(first_round.candidate_suggestion_ids)

        message, ephemeral = self._start_vote(nominee_count=2)

        if not ephemeral:
            second_round = self.vote_service.get_open_round()
            self.assertFalse(set(second_round.candidate_suggestion_ids) & presented_ids)
        else:
            # Only one item was left unpresented (3 total, 2 presented);
            # requesting 2 nominees but only 1 unpresented candidate
            # remains means MIN_CANDIDATES_FOR_A_ROUND (2) blocks starting
            # a new round -- itself confirming the presented items were
            # excluded from the pool.
            self.assertIn("eligible suggestions are needed", message)


class InfinitePoolModeTests(StartVoteRotationStrategyTestCase):
    def test_starting_a_vote_creates_no_rotation_state(self) -> None:
        self._configure_mode(CandidateSelectionMode.INFINITE_POOL)

        self._start_vote(nominee_count=2)

        self.assertIsNone(self.rotation_service.get_open_rotation(self.database.database_id))

    def test_a_previously_nominated_candidate_remains_eligible(self) -> None:
        self._configure_mode(CandidateSelectionMode.INFINITE_POOL)
        self._start_vote(nominee_count=2)
        first_round = self.vote_service.get_open_round()
        self.vote_service.close_round(first_round.id)

        message, ephemeral = self._start_vote(nominee_count=3)

        self.assertFalse(ephemeral)
        second_round = self.vote_service.get_open_round()
        self.assertEqual(len(second_round.candidate_suggestion_ids), 3)


class NoRotationServiceConfiguredTests(StartVoteRotationStrategyTestCase):
    def test_omitting_rotation_service_preserves_pre_fr_033b_behavior(self) -> None:
        message, ephemeral = perform_start_vote(
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            user=FakeMember([WASH_CREW_ROLE_ID]),
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            visibility_str="visible",
            duration_days=None,
            nominee_count=2,
            guild_id=GUILD_ID,
            channel_id=CHANNEL_ID,
        )

        self.assertFalse(ephemeral)
        self.assertIsNone(self.rotation_service.get_open_rotation(self.database.database_id))


if __name__ == "__main__":
    unittest.main()
