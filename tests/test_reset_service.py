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

        result = reset_suggestion_database(
            self.backup_service, self.database_repository, self.suggestion_repository, GUILD_ID, 1
        )

        self.assertTrue(result.success)
        self.assertEqual(1, result.removed_count)
        titles = {item.title for item in self.suggestion_repository.load().watch_items}
        self.assertEqual({"Untouched"}, titles)

    async def test_preserves_the_database_record_and_configuration(self) -> None:
        database = self._seed_database(name="Movie Night")
        self.suggestion_repository.save(
            [WatchItem(title="Alien", media_type=MediaType.MOVIE, database_id=1, guild_id=GUILD_ID)], next_id=2
        )

        reset_suggestion_database(
            self.backup_service, self.database_repository, self.suggestion_repository, GUILD_ID, 1
        )

        remaining_databases = self.database_repository.load().databases
        self.assertEqual([database], remaining_databases)

    async def test_creates_a_safety_backup(self) -> None:
        self._seed_database()
        self.suggestion_repository.save(
            [WatchItem(title="Alien", media_type=MediaType.MOVIE, database_id=1, guild_id=GUILD_ID)], next_id=2
        )

        result = reset_suggestion_database(
            self.backup_service, self.database_repository, self.suggestion_repository, GUILD_ID, 1
        )

        self.assertIsNotNone(result.safety_backup)
        self.assertTrue(result.safety_backup.is_file())

    async def test_rejects_an_unknown_database(self) -> None:
        result = reset_suggestion_database(
            self.backup_service, self.database_repository, self.suggestion_repository, GUILD_ID, 999
        )

        self.assertFalse(result.success)
        self.assertIn("No suggestion database", result.message)

    async def test_aborts_and_leaves_data_unchanged_when_safety_backup_fails(self) -> None:
        self._seed_database()
        self.suggestion_repository.save(
            [WatchItem(title="Alien", media_type=MediaType.MOVIE, database_id=1, guild_id=GUILD_ID)], next_id=2
        )
        original_titles = {item.title for item in self.suggestion_repository.load().watch_items}

        with patch.object(self.backup_service, "create_backup", side_effect=BackupError("disk full")):
            result = reset_suggestion_database(
                self.backup_service, self.database_repository, self.suggestion_repository, GUILD_ID, 1
            )

        self.assertFalse(result.success)
        self.assertIn("NOT changed", result.message)
        remaining_titles = {item.title for item in self.suggestion_repository.load().watch_items}
        self.assertEqual(original_titles, remaining_titles)


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
    async def _factory_reset(self, guild_id=GUILD_ID):
        return await factory_reset(
            backup_service=self.backup_service,
            guild_configuration_repository=self.guild_configuration_repository,
            setup_wizard_repository=self.setup_wizard_repository,
            database_repository=self.database_repository,
            suggestion_repository=self.suggestion_repository,
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


if __name__ == "__main__":
    unittest.main()
