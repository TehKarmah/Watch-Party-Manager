"""Tests for FR-032C's suggestion database reset and factory reset service."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from watch_party_manager.domain.guild_configuration import GuildConfiguration
from watch_party_manager.domain.membership_request import MembershipRequest
from watch_party_manager.domain.suggestion_database import SuggestionDatabase
from watch_party_manager.domain.suggestion_database_configuration import SuggestionDatabaseConfiguration
from watch_party_manager.domain.vote import VoteRound, VoteRoundStatus, VoteVisibility
from watch_party_manager.domain.watch_item import MediaType, WatchItem
from watch_party_manager.domain.watch_party import WatchParty, WatchPartyStatus
from watch_party_manager.persistence.guild_configuration_repository import GuildConfigurationRepository
from watch_party_manager.persistence.membership_request_repository import MembershipRequestRepository
from watch_party_manager.persistence.setup_wizard_repository import SetupWizardRepository
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.persistence.watch_party_repository import JsonWatchPartyRepository
from watch_party_manager.scheduler.json_scheduler_repository import JsonSchedulerRepository
from watch_party_manager.scheduler.scheduled_job import ScheduledJob
from watch_party_manager.services.backup_service import BackupError, BackupScheduleSettings, BackupService
from watch_party_manager.services.reset_service import (
    build_database_reset_summary,
    build_factory_reset_summary,
    factory_reset,
    reset_suggestion_database,
)
from watch_party_manager.services.suggestion_service import SuggestionService

GUILD_ID = 100
OTHER_GUILD_ID = 200
CREATED_AT = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)


class ResetServiceTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self._temp_dir.name)
        self.data_directory = self.root / "data"
        self.backup_directory = self.data_directory / "backups"
        self.backup_service = BackupService(
            self.data_directory, self.backup_directory, settings=BackupScheduleSettings()
        )
        self.database_repository = JsonSuggestionDatabaseRepository(self.data_directory / "suggestion_databases.json")
        self.suggestion_repository = JsonSuggestionRepository(self.data_directory / "suggestions.json")
        self.configuration_repository = SuggestionDatabaseConfigurationRepository(
            self.data_directory / "suggestion_database_configurations.json"
        )
        self.guild_configuration_repository = GuildConfigurationRepository(
            self.data_directory / "guild_configurations.json"
        )
        self.setup_wizard_repository = SetupWizardRepository(self.data_directory / "setup_wizard_state.json")
        self.vote_repository = JsonVoteRepository(self.data_directory / "voting.json")
        self.membership_request_repository = MembershipRequestRepository(
            self.data_directory / "membership_requests.json"
        )
        self.watch_party_repository = JsonWatchPartyRepository(self.data_directory / "watch_parties.json")
        self.scheduler_repository = JsonSchedulerRepository(self.data_directory / "scheduled_jobs.json")

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _make_suggestion_service(self) -> SuggestionService:
        """Build the live, in-process SuggestionService every other
        command actually reads through -- wired to the exact same
        repository objects reset writes to directly, mirroring bot.py's
        real wiring (see WatchPartyBot.__init__'s "separate repository
        instances... both point at the same files" comment). Called
        after seeding test data via the raw repositories, so its initial
        in-memory state matches what a real bot would have loaded at
        startup; called again (fresh) wherever a test needs to confirm
        what a brand-new instance sees after reset, simulating a restart.
        """
        return SuggestionService(
            repository=self.suggestion_repository, database_repository=self.database_repository
        )

    def _seed_database(self, database_id=1, guild_id=GUILD_ID, name="Movie Night", channel_id=555) -> SuggestionDatabase:
        database = SuggestionDatabase(
            database_id=database_id, name=name, guild_id=guild_id, channel_id=channel_id, created_at=CREATED_AT
        )
        existing = [d for d in self.database_repository.load().databases if d.database_id != database_id]
        self.database_repository.save([*existing, database], next_id=database_id + 1)
        return database


class BuildDatabaseResetSummaryTests(ResetServiceTestCase):
    async def test_summary_reports_the_database_name_and_suggestion_count(self) -> None:
        self._seed_database(name="Movie Night")
        self.suggestion_repository.save(
            [
                WatchItem(title="Alien", media_type=MediaType.MOVIE, database_id=1, guild_id=GUILD_ID),
                WatchItem(title="Aliens", media_type=MediaType.MOVIE, database_id=1, guild_id=GUILD_ID),
            ],
            next_id=3,
        )

        summary = build_database_reset_summary(self.database_repository, self.suggestion_repository, GUILD_ID, 1)

        self.assertIsNotNone(summary)
        self.assertEqual("Movie Night", summary.database_name)
        self.assertEqual(2, summary.suggestion_count)

    async def test_summary_is_none_for_an_unknown_database(self) -> None:
        summary = build_database_reset_summary(self.database_repository, self.suggestion_repository, GUILD_ID, 999)

        self.assertIsNone(summary)


class ResetSuggestionDatabaseTests(ResetServiceTestCase):
    async def test_removes_only_the_targeted_databases_suggestions(self) -> None:
        self._seed_database(database_id=1, name="Movie Night")
        self._seed_database(database_id=2, name="Other DB", channel_id=777)
        self.suggestion_repository.save(
            [
                WatchItem(title="Alien", media_type=MediaType.MOVIE, database_id=1, guild_id=GUILD_ID),
                WatchItem(title="Untouched", media_type=MediaType.MOVIE, database_id=2, guild_id=GUILD_ID),
            ],
            next_id=3,
        )

        suggestion_service = self._make_suggestion_service()
        result = reset_suggestion_database(
            self.backup_service, self.database_repository, self.suggestion_repository, suggestion_service, GUILD_ID, 1
        )

        self.assertTrue(result.success)
        self.assertEqual(1, result.removed_count)
        titles = {item.title for item in self.suggestion_repository.load().watch_items}
        self.assertEqual({"Untouched"}, titles)

    async def test_preserves_the_database_record(self) -> None:
        database = self._seed_database(name="Movie Night")
        self.suggestion_repository.save(
            [WatchItem(title="Alien", media_type=MediaType.MOVIE, database_id=1, guild_id=GUILD_ID)], next_id=2
        )

        suggestion_service = self._make_suggestion_service()
        reset_suggestion_database(
            self.backup_service, self.database_repository, self.suggestion_repository, suggestion_service, GUILD_ID, 1
        )

        remaining_databases = self.database_repository.load().databases
        self.assertEqual([database], remaining_databases)

    async def test_preserves_the_databases_configuration(self) -> None:
        self._seed_database(name="Movie Night")
        self.suggestion_repository.save(
            [WatchItem(title="Alien", media_type=MediaType.MOVIE, database_id=1, guild_id=GUILD_ID)], next_id=2
        )
        self.configuration_repository.save(
            SuggestionDatabaseConfiguration(guild_id=GUILD_ID, database_id=1, display_name="Movie Night")
        )

        suggestion_service = self._make_suggestion_service()
        reset_suggestion_database(
            self.backup_service, self.database_repository, self.suggestion_repository, suggestion_service, GUILD_ID, 1
        )

        preserved = self.configuration_repository.get(GUILD_ID, 1)
        self.assertIsNotNone(preserved)
        self.assertEqual("Movie Night", preserved.display_name)

    async def test_creates_a_safety_backup(self) -> None:
        self._seed_database()
        self.suggestion_repository.save(
            [WatchItem(title="Alien", media_type=MediaType.MOVIE, database_id=1, guild_id=GUILD_ID)], next_id=2
        )

        suggestion_service = self._make_suggestion_service()
        result = reset_suggestion_database(
            self.backup_service, self.database_repository, self.suggestion_repository, suggestion_service, GUILD_ID, 1
        )

        self.assertIsNotNone(result.safety_backup)
        self.assertTrue(result.safety_backup.is_file())

    async def test_rejects_an_unknown_database(self) -> None:
        suggestion_service = self._make_suggestion_service()
        result = reset_suggestion_database(
            self.backup_service, self.database_repository, self.suggestion_repository, suggestion_service, GUILD_ID, 999
        )

        self.assertFalse(result.success)
        self.assertIn("No collection", result.message)

    async def test_aborts_and_leaves_data_unchanged_when_safety_backup_fails(self) -> None:
        self._seed_database()
        self.suggestion_repository.save(
            [WatchItem(title="Alien", media_type=MediaType.MOVIE, database_id=1, guild_id=GUILD_ID)], next_id=2
        )
        original_titles = {item.title for item in self.suggestion_repository.load().watch_items}
        suggestion_service = self._make_suggestion_service()

        with patch.object(self.backup_service, "create_backup", side_effect=BackupError("disk full")):
            result = reset_suggestion_database(
                self.backup_service,
                self.database_repository,
                self.suggestion_repository,
                suggestion_service,
                GUILD_ID,
                1,
            )

        self.assertFalse(result.success)
        self.assertIn("NOT changed", result.message)
        remaining_titles = {item.title for item in self.suggestion_repository.load().watch_items}
        self.assertEqual(original_titles, remaining_titles)


class _NoOpSaveSuggestionRepository:
    """Wraps a real JsonSuggestionRepository but silently drops save() --
    simulates a persistence failure that doesn't raise (e.g. a disk or
    permission problem swallowed somewhere below this layer), so
    reset_suggestion_database's post-write verification is the only thing
    that can catch it.
    """

    def __init__(self, real_repository: JsonSuggestionRepository) -> None:
        self._real = real_repository

    def load(self):
        return self._real.load()

    def save(self, watch_items, next_id) -> None:  # intentionally a no-op
        pass


class ResetSuggestionDatabaseLiveStateTests(ResetServiceTestCase):
    """Release-blocking bug fix: a reset that writes suggestions.json
    correctly used to still be invisible to /list and /add's duplicate
    detection, because bot.py's live SuggestionService instance caches
    every suggestion in memory and was never told to reload after a
    reset wrote through a *different* repository object. These tests
    exercise the fix directly against SuggestionService, the same class
    every Discord command reads through.
    """

    async def test_reset_clears_duplicate_detection_immediately(self) -> None:
        self._seed_database()
        suggestion_service = self._make_suggestion_service()
        suggestion_service.suggest("Dogma", database_id=1, guild_id=GUILD_ID)

        result = reset_suggestion_database(
            self.backup_service, self.database_repository, self.suggestion_repository, suggestion_service, GUILD_ID, 1
        )

        self.assertTrue(result.success)
        # Re-suggesting the exact same title must succeed as a brand-new
        # suggestion, not be rejected as a duplicate of the "removed" one.
        second_attempt = suggestion_service.suggest("Dogma", database_id=1, guild_id=GUILD_ID)
        self.assertTrue(second_attempt.success)

    async def test_reset_updates_the_live_services_in_memory_count(self) -> None:
        self._seed_database()
        suggestion_service = self._make_suggestion_service()
        suggestion_service.suggest("Dogma", database_id=1, guild_id=GUILD_ID)
        self.assertEqual(1, suggestion_service.suggestion_count_for_database(1))

        reset_suggestion_database(
            self.backup_service, self.database_repository, self.suggestion_repository, suggestion_service, GUILD_ID, 1
        )

        self.assertEqual(0, suggestion_service.suggestion_count_for_database(1))

    async def test_reset_persists_the_empty_state_for_a_freshly_constructed_service(self) -> None:
        # Simulates a bot restart: a brand-new SuggestionService, loading
        # only from disk, must also see the collection as empty.
        self._seed_database()
        suggestion_service = self._make_suggestion_service()
        suggestion_service.suggest("Dogma", database_id=1, guild_id=GUILD_ID)

        reset_suggestion_database(
            self.backup_service, self.database_repository, self.suggestion_repository, suggestion_service, GUILD_ID, 1
        )

        restarted_service = self._make_suggestion_service()
        self.assertEqual(0, restarted_service.suggestion_count_for_database(1))

    async def test_reset_does_not_affect_other_collections_live_state(self) -> None:
        self._seed_database(database_id=1, name="Movies")
        self._seed_database(database_id=2, name="TV Shows", channel_id=777)
        suggestion_service = self._make_suggestion_service()
        suggestion_service.suggest("Dogma", database_id=1, guild_id=GUILD_ID)
        suggestion_service.suggest("Breaking Bad", database_id=2, guild_id=GUILD_ID)

        reset_suggestion_database(
            self.backup_service, self.database_repository, self.suggestion_repository, suggestion_service, GUILD_ID, 1
        )

        self.assertEqual(0, suggestion_service.suggestion_count_for_database(1))
        self.assertEqual(1, suggestion_service.suggestion_count_for_database(2))
        remaining_titles = {item.title for item in suggestion_service.get_suggestions()}
        self.assertEqual({"Breaking Bad"}, remaining_titles)

    async def test_verification_catches_a_silent_persistence_failure(self) -> None:
        self._seed_database()
        self.suggestion_repository.save(
            [WatchItem(title="Alien", media_type=MediaType.MOVIE, database_id=1, guild_id=GUILD_ID)], next_id=2
        )
        suggestion_service = self._make_suggestion_service()
        broken_repository = _NoOpSaveSuggestionRepository(self.suggestion_repository)

        with self.assertLogs("watch_party_manager.services.reset_service", level="ERROR") as logs:
            result = reset_suggestion_database(
                self.backup_service, self.database_repository, broken_repository, suggestion_service, GUILD_ID, 1
            )

        self.assertFalse(result.success)
        self.assertIn("could not be verified", result.message.lower())
        # No stack trace or exception text should be surfaced to Discord.
        self.assertNotIn("Traceback", result.message)
        # A diagnosable log entry naming the database/guild must exist.
        self.assertTrue(any("1" in entry and str(GUILD_ID) in entry for entry in logs.output))
        # The safety backup must still be preserved even though
        # verification failed -- it's the recovery path for exactly this case.
        self.assertIsNotNone(result.safety_backup)
        self.assertTrue(result.safety_backup.is_file())
        # The live service must still show the item -- no false "removed" claim.
        self.assertEqual(1, suggestion_service.suggestion_count_for_database(1))


class BuildFactoryResetSummaryTests(ResetServiceTestCase):
    async def test_counts_only_the_targeted_guilds_data(self) -> None:
        self.guild_configuration_repository.save(GuildConfiguration(guild_id=GUILD_ID, guild_name="Guild"))
        self._seed_database(guild_id=GUILD_ID)
        self._seed_database(database_id=2, guild_id=OTHER_GUILD_ID, channel_id=999)
        self.suggestion_repository.save(
            [
                WatchItem(title="Mine", media_type=MediaType.MOVIE, database_id=1, guild_id=GUILD_ID),
                WatchItem(title="Not mine", media_type=MediaType.MOVIE, database_id=2, guild_id=OTHER_GUILD_ID),
            ],
            next_id=3,
        )

        summary = await build_factory_reset_summary(
            guild_configuration_repository=self.guild_configuration_repository,
            database_repository=self.database_repository,
            suggestion_repository=self.suggestion_repository,
            vote_repository=self.vote_repository,
            membership_request_repository=self.membership_request_repository,
            watch_party_repository=self.watch_party_repository,
            scheduler_repository=self.scheduler_repository,
            guild_id=GUILD_ID,
        )

        self.assertTrue(summary.configuration_present)
        self.assertEqual(1, summary.suggestion_database_count)
        self.assertEqual(1, summary.suggestion_count)

    async def test_configuration_present_is_false_when_unconfigured(self) -> None:
        summary = await build_factory_reset_summary(
            guild_configuration_repository=self.guild_configuration_repository,
            database_repository=self.database_repository,
            suggestion_repository=self.suggestion_repository,
            vote_repository=self.vote_repository,
            membership_request_repository=self.membership_request_repository,
            watch_party_repository=self.watch_party_repository,
            scheduler_repository=self.scheduler_repository,
            guild_id=GUILD_ID,
        )

        self.assertFalse(summary.configuration_present)


class FactoryResetTests(ResetServiceTestCase):
    async def _factory_reset(self, guild_id=GUILD_ID, suggestion_service=None):
        return await factory_reset(
            backup_service=self.backup_service,
            guild_configuration_repository=self.guild_configuration_repository,
            setup_wizard_repository=self.setup_wizard_repository,
            database_repository=self.database_repository,
            suggestion_repository=self.suggestion_repository,
            suggestion_service=suggestion_service or self._make_suggestion_service(),
            configuration_repository=self.configuration_repository,
            vote_repository=self.vote_repository,
            membership_request_repository=self.membership_request_repository,
            watch_party_repository=self.watch_party_repository,
            scheduler_repository=self.scheduler_repository,
            guild_id=guild_id,
        )

    async def test_removes_managed_data_for_the_guild(self) -> None:
        self.guild_configuration_repository.save(GuildConfiguration(guild_id=GUILD_ID, guild_name="Guild"))
        self._seed_database(guild_id=GUILD_ID)
        self.suggestion_repository.save(
            [WatchItem(title="Alien", media_type=MediaType.MOVIE, database_id=1, guild_id=GUILD_ID)], next_id=2
        )

        result = await self._factory_reset()

        self.assertTrue(result.success)
        self.assertIsNone(self.guild_configuration_repository.get(GUILD_ID))
        self.assertEqual([], self.database_repository.load().databases)
        self.assertEqual([], self.suggestion_repository.load().watch_items)

    async def test_preserves_other_guilds_data(self) -> None:
        self.guild_configuration_repository.save(GuildConfiguration(guild_id=GUILD_ID, guild_name="Guild"))
        self.guild_configuration_repository.save(GuildConfiguration(guild_id=OTHER_GUILD_ID, guild_name="Other"))
        self._seed_database(database_id=1, guild_id=GUILD_ID)
        self._seed_database(database_id=2, guild_id=OTHER_GUILD_ID, channel_id=999)

        await self._factory_reset()

        self.assertIsNotNone(self.guild_configuration_repository.get(OTHER_GUILD_ID))
        remaining = self.database_repository.load().databases
        self.assertEqual([2], [d.database_id for d in remaining])

    async def test_removes_membership_requests_vote_rounds_and_watch_parties(self) -> None:
        self.vote_repository.save(
            [
                VoteRound(
                    id=1,
                    guild_id=GUILD_ID,
                    created_at=CREATED_AT,
                    visibility=VoteVisibility.VISIBLE,
                    status=VoteRoundStatus.OPEN,
                )
            ],
            next_round_id=2,
        )
        self.membership_request_repository.save(
            [MembershipRequest(request_id=1, guild_id=GUILD_ID, user_id=1)], next_id=2
        )
        self.watch_party_repository.save(
            [
                WatchParty(
                    id=1,
                    watch_item_id=1,
                    scheduled_at=CREATED_AT,
                    guild_id=GUILD_ID,
                    channel_id=555,
                    status=WatchPartyStatus.SCHEDULED,
                    created_at=CREATED_AT,
                )
            ],
            next_id=2,
        )

        await self._factory_reset()

        self.assertEqual([], self.vote_repository.load().rounds)
        self.assertEqual([], self.membership_request_repository.load().requests)
        self.assertEqual([], self.watch_party_repository.load().watch_parties)

    async def test_removes_scheduled_jobs(self) -> None:
        job = ScheduledJob(
            guild_id=GUILD_ID, job_type="close_vote", logical_key="close_vote:1", run_at=CREATED_AT, created_at=CREATED_AT
        )
        await self.scheduler_repository.add(job)

        await self._factory_reset()

        self.assertEqual([], await self.scheduler_repository.list_all())

    async def test_creates_a_safety_backup(self) -> None:
        self.guild_configuration_repository.save(GuildConfiguration(guild_id=GUILD_ID, guild_name="Guild"))

        result = await self._factory_reset()

        self.assertIsNotNone(result.safety_backup)
        self.assertTrue(result.safety_backup.is_file())

    async def test_backup_archives_are_preserved(self) -> None:
        self.guild_configuration_repository.save(GuildConfiguration(guild_id=GUILD_ID, guild_name="Guild"))
        pre_existing = self.backup_service.create_backup().archive_path

        await self._factory_reset()

        self.assertTrue(pre_existing.is_file())

    async def test_setup_is_required_again_afterward(self) -> None:
        self.guild_configuration_repository.save(
            GuildConfiguration(guild_id=GUILD_ID, guild_name="Guild", setup_completed=True)
        )

        await self._factory_reset()

        self.assertIsNone(self.guild_configuration_repository.get(GUILD_ID))

    async def test_aborts_and_leaves_data_unchanged_when_safety_backup_fails(self) -> None:
        self.guild_configuration_repository.save(GuildConfiguration(guild_id=GUILD_ID, guild_name="Guild"))
        self._seed_database(guild_id=GUILD_ID)

        with patch.object(self.backup_service, "create_backup", side_effect=BackupError("disk full")):
            result = await self._factory_reset()

        self.assertFalse(result.success)
        self.assertIn("NOT changed", result.message)
        self.assertIsNotNone(self.guild_configuration_repository.get(GUILD_ID))
        self.assertEqual(1, len(self.database_repository.load().databases))


class FactoryResetLiveStateTests(ResetServiceTestCase):
    """Same live-state-staleness bug as ResetSuggestionDatabaseLiveStateTests,
    verified against factory_reset -- it writes suggestions.json and
    suggestion_databases.json through the same bypass pattern.
    """

    async def test_factory_reset_clears_duplicate_detection_immediately(self) -> None:
        self._seed_database(guild_id=GUILD_ID)
        suggestion_service = self._make_suggestion_service()
        suggestion_service.suggest("Dogma", database_id=1, guild_id=GUILD_ID)

        result = await factory_reset(
            backup_service=self.backup_service,
            guild_configuration_repository=self.guild_configuration_repository,
            setup_wizard_repository=self.setup_wizard_repository,
            database_repository=self.database_repository,
            suggestion_repository=self.suggestion_repository,
            suggestion_service=suggestion_service,
            configuration_repository=self.configuration_repository,
            vote_repository=self.vote_repository,
            membership_request_repository=self.membership_request_repository,
            watch_party_repository=self.watch_party_repository,
            scheduler_repository=self.scheduler_repository,
            guild_id=GUILD_ID,
        )

        self.assertTrue(result.success)
        self.assertEqual([], suggestion_service.list_databases(GUILD_ID))
        self.assertEqual(0, len(suggestion_service.get_suggestions()))
        # Re-creating a same-named, same-channel collection and re-suggesting
        # the same title must both work -- nothing stale remains cached.
        recreated = suggestion_service.create_database("Movie Night", guild_id=GUILD_ID, channel_id=555)
        self.assertTrue(recreated.success)
        resuggested = suggestion_service.suggest("Dogma", database_id=recreated.database.database_id, guild_id=GUILD_ID)
        self.assertTrue(resuggested.success)

    async def test_factory_reset_persists_the_empty_state_for_a_freshly_constructed_service(self) -> None:
        self._seed_database(guild_id=GUILD_ID)
        suggestion_service = self._make_suggestion_service()
        suggestion_service.suggest("Dogma", database_id=1, guild_id=GUILD_ID)

        await factory_reset(
            backup_service=self.backup_service,
            guild_configuration_repository=self.guild_configuration_repository,
            setup_wizard_repository=self.setup_wizard_repository,
            database_repository=self.database_repository,
            suggestion_repository=self.suggestion_repository,
            suggestion_service=suggestion_service,
            configuration_repository=self.configuration_repository,
            vote_repository=self.vote_repository,
            membership_request_repository=self.membership_request_repository,
            watch_party_repository=self.watch_party_repository,
            scheduler_repository=self.scheduler_repository,
            guild_id=GUILD_ID,
        )

        restarted_service = self._make_suggestion_service()
        self.assertEqual([], restarted_service.list_databases(GUILD_ID))
        self.assertEqual(0, len(restarted_service.get_suggestions()))

    async def test_verification_catches_a_silent_suggestion_persistence_failure(self) -> None:
        self.guild_configuration_repository.save(GuildConfiguration(guild_id=GUILD_ID, guild_name="Guild"))
        self._seed_database(guild_id=GUILD_ID)
        self.suggestion_repository.save(
            [WatchItem(title="Alien", media_type=MediaType.MOVIE, database_id=1, guild_id=GUILD_ID)], next_id=2
        )
        suggestion_service = self._make_suggestion_service()
        broken_repository = _NoOpSaveSuggestionRepository(self.suggestion_repository)

        with self.assertLogs("watch_party_manager.services.reset_service", level="ERROR"):
            result = await factory_reset(
                backup_service=self.backup_service,
                guild_configuration_repository=self.guild_configuration_repository,
                setup_wizard_repository=self.setup_wizard_repository,
                database_repository=self.database_repository,
                suggestion_repository=broken_repository,
                suggestion_service=suggestion_service,
                configuration_repository=self.configuration_repository,
                vote_repository=self.vote_repository,
                membership_request_repository=self.membership_request_repository,
                watch_party_repository=self.watch_party_repository,
                scheduler_repository=self.scheduler_repository,
                guild_id=GUILD_ID,
            )

        self.assertFalse(result.success)
        self.assertIn("could not be verified", result.message.lower())
        self.assertNotIn("Traceback", result.message)
        self.assertIsNotNone(result.safety_backup)
        self.assertTrue(result.safety_backup.is_file())
        # Guild configuration must NOT have been deleted -- verification
        # runs before the point of no return.
        self.assertIsNotNone(self.guild_configuration_repository.get(GUILD_ID))


if __name__ == "__main__":
    unittest.main()
