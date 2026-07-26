"""Regression tests for a release-blocking bug: /list and /start_vote
disagreed on how many suggestions were eligible for voting.

Root cause: LowPoolReminderService.evaluate() called
RotationService.rotation_progress(), which bootstraps -- and persists --
a rotation snapshotting whatever suggestions exist at that exact instant
(see RotationService.get_or_start_rotation). The Low Pool Reminder is
evaluated after every successful /add, including a brand-new collection's
very first suggestion (the default threshold is 10, so a pool of 1 always
qualifies). That premature snapshot froze the rotation to just the first
suggestion(s) added so far; every suggestion added afterward (admission
mode NEXT_ROTATION, the default) never joined it, since NEXT_ROTATION is
a deliberate no-op until the *next* rotation begins. /start_vote's default
Rotation Pool candidate selection then only ever saw that first, stale
handful of suggestions as eligible, while /list -- which filters purely by
WatchItemStatus and never consults rotation state -- correctly reported
every one of them as available.

The fix (services/low_pool_reminder_service.py): use the same
non-bootstrapping RotationService.get_open_rotation()/progress_for_rotation()
pattern statistics_service.py already used for exactly this reason,
instead of the bootstrapping rotation_progress(). This never creates a
Rotation as a side effect of a routine post-/add check; the first real
bootstrap now happens at actual candidate-selection time (/start_vote),
by which point every suggestion added so far is correctly captured.

These tests reproduce the original bug's exact trigger sequence (adding
suggestions one at a time, with a Low Pool Reminder evaluation after each
-- mirroring bot.py's finish_add_or_reactivate) directly against the real
services, and confirm /list's available count and /start_vote's eligible
count agree in every case the task's regression list calls for.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from watch_party_manager.bot import (
    SuggestionListStatusFilter,
    build_insufficient_candidates_message,
    filter_items_by_status,
    perform_start_vote,
)
from watch_party_manager.domain.suggestion_database_configuration import (
    CandidateSelectionMode,
    SuggestionAdmissionMode,
    SuggestionDatabaseConfiguration,
    SuggestionRulesConfig,
)
from watch_party_manager.domain.watch_item import WatchItemStatus
from watch_party_manager.persistence.rotation_repository import JsonRotationRepository
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.services.low_pool_reminder_service import LowPoolReminderService
from watch_party_manager.services.nominee_selection_service import NomineeSelectionService
from watch_party_manager.services.candidate_selection_strategy import RotationPoolStrategy
from watch_party_manager.services.collection_display import format_collection_display
from watch_party_manager.services.rotation_service import RotationService
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.vote_service import VoteService

GUILD_ID = 100
WASH_CREW_ROLE_ID = 999


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, role_ids=()) -> None:
        self.roles = [FakeRole(role_id) for role_id in role_ids]


class FakeGuildConfigurationRepository:
    def get(self, guild_id: int):
        return None


class EligibilityParityTestCase(unittest.TestCase):
    """A fresh-install-shaped fixture: real SuggestionService, RotationService,
    LowPoolReminderService, and NomineeSelectionService, all backed by temp
    JSON files, matching how these services are actually wired together in
    production (see WatchPartyBot.__init__).
    """

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
        self.low_pool_reminder_service = LowPoolReminderService(
            self.rotation_service, FakeGuildConfigurationRepository(), self.configuration_repository
        )

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _create_database(self, name: str, channel_id: int):
        return self.suggestion_service.create_database(name, guild_id=GUILD_ID, channel_id=channel_id).database

    def _add_suggestion_like_finish_add_or_reactivate(self, database, title: str):
        """Reproduce bot.py's finish_add_or_reactivate sequence exactly:
        suggest, then admit_suggestion_to_rotation (a no-op under the
        default NEXT_ROTATION admission mode), then the Low Pool Reminder
        check -- the exact sequence that triggered the original bug.
        """
        watch_item = self.suggestion_service.suggest(title, database_id=database.database_id).watch_item
        self.rotation_service.admit_suggestion(
            database.database_id, watch_item.id, admission_mode=SuggestionAdmissionMode.NEXT_ROTATION
        )
        remaining_count = len(self.suggestion_service.get_suggestions_for_database(database.database_id))
        self.low_pool_reminder_service.evaluate(
            guild_id=GUILD_ID,
            database_id=database.database_id,
            remaining_count=remaining_count,
            default_suggestion_channel_id=database.channel_id,
        )
        return watch_item

    def _list_available_count(self, database_id: int) -> int:
        """The exact computation /list uses for its default "available" view."""
        items = self.suggestion_service.get_suggestions_for_database(database_id)
        return len(filter_items_by_status(items, SuggestionListStatusFilter.AVAILABLE))

    def _start_vote(self, database, *, nominee_count=2):
        return perform_start_vote(
            self.vote_service,
            self.suggestion_service,
            self.nominee_selection_service,
            user=FakeMember([WASH_CREW_ROLE_ID]),
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            visibility_str="visible",
            duration_minutes=None,
            nominee_count=nominee_count,
            guild_id=GUILD_ID,
            channel_id=database.channel_id,
            rotation_service=self.rotation_service,
            suggestion_database_configuration_repository=self.configuration_repository,
        )


class FreshInstallationTests(EligibilityParityTestCase):
    """The exact bug report: a brand-new collection, suggestions added one
    at a time (each triggering a Low Pool Reminder check, since a pool of
    1-9 is always at or below the default threshold of 10), must end up
    with /list and /start_vote agreeing on every suggestion's eligibility.
    """

    def test_seven_suggestions_added_one_at_a_time_are_all_eligible(self) -> None:
        database = self._create_database("Movie Suggestions", channel_id=200)

        for index in range(1, 8):
            self._add_suggestion_like_finish_add_or_reactivate(database, f"Movie {index}")

        self.assertEqual(self._list_available_count(database.database_id), 7)
        self.assertEqual(
            self.nominee_selection_service.eligible_candidate_count(database.database_id), 7
        )

        message, ephemeral = self._start_vote(database, nominee_count=3)

        self.assertFalse(ephemeral)
        self.assertIsNotNone(self.vote_service.get_open_round())

    def test_empty_collection_is_rejected_with_a_diagnostic_message(self) -> None:
        database = self._create_database("Movie Suggestions", channel_id=200)

        message, ephemeral = self._start_vote(database)

        self.assertTrue(ephemeral)
        self.assertEqual(
            message,
            build_insufficient_candidates_message(format_collection_display("Movie Suggestions"), 0),
        )
        self.assertIn("Movie Suggestions", message)
        self.assertIn("no eligible suggestions", message)
        self.assertIn("at least 2 candidates", message)

    def test_one_suggestion_is_rejected_with_a_diagnostic_message(self) -> None:
        database = self._create_database("Movie Suggestions", channel_id=200)
        self._add_suggestion_like_finish_add_or_reactivate(database, "Alien")

        message, ephemeral = self._start_vote(database)

        self.assertTrue(ephemeral)
        self.assertEqual(
            message,
            build_insufficient_candidates_message(format_collection_display("Movie Suggestions"), 1),
        )
        self.assertIn("Movie Suggestions", message)
        self.assertIn("1 eligible suggestion", message)
        self.assertNotIn("1 eligible suggestions", message)

    def test_two_suggestions_are_enough_to_start(self) -> None:
        database = self._create_database("Movie Suggestions", channel_id=200)
        self._add_suggestion_like_finish_add_or_reactivate(database, "Alien")
        self._add_suggestion_like_finish_add_or_reactivate(database, "The Matrix")

        message, ephemeral = self._start_vote(database, nominee_count=2)

        self.assertFalse(ephemeral)
        self.assertIsNotNone(self.vote_service.get_open_round())


class MultipleCollectionsTests(EligibilityParityTestCase):
    def test_each_collections_eligibility_is_independent(self) -> None:
        movies = self._create_database("Movies", channel_id=200)
        tv_shows = self._create_database("TV Shows", channel_id=210)

        for index in range(1, 8):
            self._add_suggestion_like_finish_add_or_reactivate(movies, f"Movie {index}")
        self._add_suggestion_like_finish_add_or_reactivate(tv_shows, "Breaking Bad")

        self.assertEqual(self._list_available_count(movies.database_id), 7)
        self.assertEqual(self.nominee_selection_service.eligible_candidate_count(movies.database_id), 7)
        self.assertEqual(self._list_available_count(tv_shows.database_id), 1)
        self.assertEqual(self.nominee_selection_service.eligible_candidate_count(tv_shows.database_id), 1)

        movies_message, movies_ephemeral = self._start_vote(movies, nominee_count=3)
        tv_message, tv_ephemeral = self._start_vote(tv_shows, nominee_count=2)

        self.assertFalse(movies_ephemeral)
        self.assertTrue(tv_ephemeral)
        self.assertIn("TV Shows", tv_message)
        self.assertIn("1 eligible suggestion", tv_message)


class RotationCooldownVoteWinnerRetiredTests(EligibilityParityTestCase):
    """Rotation Cooldown, Vote Winner, and Retired suggestions are each
    handled by the one authoritative eligibility source
    (NomineeSelectionService/RotationPoolStrategy) exactly once -- these
    tests confirm /start_vote's own candidate count reflects each state
    correctly and consistently, the same way it did before this fix.
    """

    def _configure_rotation_pool(self, database) -> None:
        self.configuration_repository.save(
            SuggestionDatabaseConfiguration(
                guild_id=GUILD_ID,
                database_id=database.database_id,
                display_name=database.name,
                suggestion_rules=SuggestionRulesConfig(candidate_selection=CandidateSelectionMode.ROTATION_POOL),
            )
        )

    def test_rotation_cooldown_items_are_excluded_from_the_eligible_count_but_still_listed(self) -> None:
        database = self._create_database("Movie Suggestions", channel_id=200)
        self._configure_rotation_pool(database)
        for title in ("Alien", "The Matrix", "Brazil"):
            self._add_suggestion_like_finish_add_or_reactivate(database, title)

        # Presenting 2 of the 3 in a vote round puts them on Rotation
        # Cooldown once the round closes -- /list still shows all 3 as
        # "available" (Rotation Cooldown is a derived display state, not a
        # separate filter bucket -- see SuggestionListStatusFilter.AVAILABLE),
        # but they are correctly excluded from a fresh Rotation Pool vote's
        # eligible count until a new rotation begins.
        first_message, first_ephemeral = self._start_vote(database, nominee_count=2)
        self.assertFalse(first_ephemeral)
        first_round = self.vote_service.get_open_round()
        self.vote_service.close_round(first_round.id)

        strategy = RotationPoolStrategy(rotation_service=self.rotation_service)
        self.assertEqual(self._list_available_count(database.database_id), 3)
        self.assertEqual(
            self.nominee_selection_service.eligible_candidate_count(database.database_id, strategy), 1
        )

        second_message, second_ephemeral = self._start_vote(database, nominee_count=2)
        self.assertTrue(second_ephemeral)
        self.assertIn("1 eligible suggestion", second_message)

    def test_vote_winner_is_excluded_from_both_list_and_eligible_count(self) -> None:
        database = self._create_database("Movie Suggestions", channel_id=200)
        self._configure_rotation_pool(database)
        alien = self._add_suggestion_like_finish_add_or_reactivate(database, "Alien")
        self._add_suggestion_like_finish_add_or_reactivate(database, "The Matrix")
        self._add_suggestion_like_finish_add_or_reactivate(database, "Brazil")

        alien.status = WatchItemStatus.VOTE_WINNER

        strategy = RotationPoolStrategy(rotation_service=self.rotation_service)
        self.assertEqual(self._list_available_count(database.database_id), 2)
        self.assertEqual(
            self.nominee_selection_service.eligible_candidate_count(database.database_id, strategy), 2
        )

    def test_retired_suggestions_are_excluded_from_both_list_and_eligible_count(self) -> None:
        database = self._create_database("Movie Suggestions", channel_id=200)
        self._configure_rotation_pool(database)
        alien = self._add_suggestion_like_finish_add_or_reactivate(database, "Alien")
        self._add_suggestion_like_finish_add_or_reactivate(database, "The Matrix")
        self._add_suggestion_like_finish_add_or_reactivate(database, "Brazil")

        self.suggestion_service.reject_suggestion(alien.id, 1, rejection_threshold=1)

        strategy = RotationPoolStrategy(rotation_service=self.rotation_service)
        self.assertEqual(self._list_available_count(database.database_id), 2)
        self.assertEqual(
            self.nominee_selection_service.eligible_candidate_count(database.database_id, strategy), 2
        )


if __name__ == "__main__":
    unittest.main()
