"""Tests for FR-034's /stats command: privacy, statistics types, and errors."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from watch_party_manager.bot import handle_stats
from watch_party_manager.persistence.rotation_repository import JsonRotationRepository
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.services.permission_service import PermissionService
from watch_party_manager.services.rotation_service import RotationService
from watch_party_manager.services.statistics_service import StatisticsService
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.vote_service import VoteService

GUILD_ID = 100
CHANNEL_ID = 200
WASH_CREW_ROLE_ID = 999
WATCH_PARTY_MEMBER_ROLE_ID = 555


class FakeRole:
    def __init__(self, role_id: int, members=()) -> None:
        self.id = role_id
        self.members = list(members)


class FakeMember:
    def __init__(self, role_ids=(), *, user_id: int = 1) -> None:
        self.roles = [FakeRole(role_id) for role_id in role_ids]
        self.id = user_id
        self.mention = f"<@{user_id}>"


class FakeGuild:
    def __init__(self, roles=()) -> None:
        self._roles = {role.id: role for role in roles}

    def get_role(self, role_id):
        return self._roles.get(role_id)


class FakeResponse:
    def __init__(self) -> None:
        self.sent_message = None
        self.sent_ephemeral = None
        self.sent_view = None

    async def send_message(self, content, ephemeral=False, view=None) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral
        self.sent_view = view

    async def edit_message(self, content=None, view=None) -> None:
        self.sent_message = content
        self.sent_view = view


class FakeInteraction:
    def __init__(self, user=None, guild=None, guild_id=GUILD_ID, channel_id=CHANNEL_ID) -> None:
        self.user = user if user is not None else FakeMember([WATCH_PARTY_MEMBER_ROLE_ID], user_id=1)
        self.guild = guild
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.response = FakeResponse()


class FakeBot:
    def __init__(
        self,
        suggestion_service,
        statistics_service,
        rotation_service,
        configuration_repository,
        wash_crew_role_id=WASH_CREW_ROLE_ID,
    ) -> None:
        self.suggestion_service = suggestion_service
        self.statistics_service = statistics_service
        self.rotation_service = rotation_service
        self.suggestion_database_configuration_repository = configuration_repository
        self.permission_service = PermissionService(
            watch_party_member_role_id=WATCH_PARTY_MEMBER_ROLE_ID, wash_crew_role_id=wash_crew_role_id
        )
        self.wash_crew_role_id = wash_crew_role_id
        self.watch_party_member_role_id = WATCH_PARTY_MEMBER_ROLE_ID


class HandleStatsTestCase(unittest.IsolatedAsyncioTestCase):
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
        self.rotation_service = RotationService(
            self.suggestion_service, repository=JsonRotationRepository(root / "rotations.json")
        )
        self.statistics_service = StatisticsService(
            self.suggestion_service, rotation_service=self.rotation_service
        )
        self.configuration_repository = SuggestionDatabaseConfigurationRepository(
            root / "suggestion_database_configurations.json"
        )
        self.bot = FakeBot(
            self.suggestion_service, self.statistics_service, self.rotation_service, self.configuration_repository
        )
        self.database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _crew_member(self, user_id: int = 2) -> FakeMember:
        return FakeMember([WASH_CREW_ROLE_ID], user_id=user_id)

    def _member(self, user_id: int = 1) -> FakeMember:
        return FakeMember([WATCH_PARTY_MEMBER_ROLE_ID], user_id=user_id)


class DefaultBehaviorTests(HandleStatsTestCase):
    async def test_default_type_shows_server_statistics(self) -> None:
        interaction = FakeInteraction()

        await handle_stats(interaction, self.bot, "server", False, None)

        self.assertIn("**Server Statistics**", interaction.response.sent_message)
        self.assertTrue(interaction.response.sent_ephemeral)

    async def test_requires_a_server(self) -> None:
        interaction = FakeInteraction(guild_id=None)

        await handle_stats(interaction, self.bot, "server", False, None)

        self.assertIn("only be used in a server", interaction.response.sent_message)

    async def test_requires_watch_party_membership(self) -> None:
        interaction = FakeInteraction(user=FakeMember([]))

        await handle_stats(interaction, self.bot, "server", False, None)

        self.assertIn("Watch Party member", interaction.response.sent_message)

    async def test_invalid_type_is_rejected(self) -> None:
        interaction = FakeInteraction()

        await handle_stats(interaction, self.bot, "not_a_real_type", False, None)

        self.assertIn("Choose Server, Member, Suggestion, Rotation, or Database.", interaction.response.sent_message)


class PrivacyTests(HandleStatsTestCase):
    async def test_server_statistics_are_ephemeral_by_default(self) -> None:
        interaction = FakeInteraction()

        await handle_stats(interaction, self.bot, "server", False, None)

        self.assertTrue(interaction.response.sent_ephemeral)

    async def test_regular_member_cannot_post_server_statistics_publicly(self) -> None:
        interaction = FakeInteraction(user=self._member())

        await handle_stats(interaction, self.bot, "server", True, None)

        self.assertIn("WASH Crew role to post statistics publicly", interaction.response.sent_message)

    async def test_crew_can_post_server_statistics_publicly(self) -> None:
        interaction = FakeInteraction(user=self._crew_member())

        await handle_stats(interaction, self.bot, "server", True, None)

        self.assertFalse(interaction.response.sent_ephemeral)
        self.assertIn("**Server Statistics**", interaction.response.sent_message)

    async def test_a_regular_member_may_post_their_own_member_statistics_publicly(self) -> None:
        # FR-034 Section 4: member statistics are the one type a regular
        # (non-Crew) member may choose to post publicly -- it's their own
        # data, a self-consenting disclosure distinct from posting an
        # aggregate view.
        interaction = FakeInteraction(user=self._member())

        await handle_stats(interaction, self.bot, "member", True, None)

        self.assertFalse(interaction.response.sent_ephemeral)
        self.assertIn("**Your Statistics**", interaction.response.sent_message)

    async def test_member_statistics_are_ephemeral_by_default(self) -> None:
        interaction = FakeInteraction(user=self._member())

        await handle_stats(interaction, self.bot, "member", False, None)

        self.assertTrue(interaction.response.sent_ephemeral)

    async def test_member_statistics_only_ever_reflect_the_requesting_user(self) -> None:
        # There is no parameter to target another member -- member stats
        # are always computed for interaction.user, never anyone else,
        # so WASH Crew can never retrieve another member's statistics.
        crew_interaction = FakeInteraction(user=self._crew_member(user_id=42))

        await handle_stats(crew_interaction, self.bot, "member", False, None)

        self.assertIn(f"<@{42}>", crew_interaction.response.sent_message)

    async def test_suggestion_statistics_public_posting_requires_crew(self) -> None:
        self.suggestion_service.suggest("Alien", database_id=self.database.database_id)
        interaction = FakeInteraction(user=self._member())

        await handle_stats(interaction, self.bot, "suggestion", True, "Alien")

        self.assertIn("WASH Crew role to post statistics publicly", interaction.response.sent_message)

    async def test_rotation_statistics_public_posting_requires_crew(self) -> None:
        interaction = FakeInteraction(user=self._member())

        await handle_stats(interaction, self.bot, "rotation", True, None)

        self.assertIn("WASH Crew role to post statistics publicly", interaction.response.sent_message)

    async def test_database_statistics_public_posting_requires_crew(self) -> None:
        interaction = FakeInteraction(user=self._member())

        await handle_stats(interaction, self.bot, "database", True, None)

        self.assertIn("WASH Crew role to post statistics publicly", interaction.response.sent_message)


class ServerTypeTests(HandleStatsTestCase):
    async def test_includes_a_live_member_count_when_the_role_is_resolvable(self) -> None:
        role = FakeRole(WATCH_PARTY_MEMBER_ROLE_ID, members=[1, 2, 3])
        guild = FakeGuild(roles=[role])
        interaction = FakeInteraction(guild=guild)

        await handle_stats(interaction, self.bot, "server", False, None)

        self.assertIn("Current Watch Party members: 3", interaction.response.sent_message)

    async def test_omits_member_count_when_no_guild_object_is_available(self) -> None:
        interaction = FakeInteraction(guild=None)

        await handle_stats(interaction, self.bot, "server", False, None)

        self.assertNotIn("Current Watch Party members:", interaction.response.sent_message)
        self.assertIn("Participation percentage: not available", interaction.response.sent_message)


class MemberTypeTests(HandleStatsTestCase):
    async def test_shows_zeroed_statistics_for_a_member_with_no_history(self) -> None:
        interaction = FakeInteraction(user=self._member(user_id=777))

        await handle_stats(interaction, self.bot, "member", False, None)

        self.assertIn("Submitted: 0 suggestions", interaction.response.sent_message)

    async def test_shows_submission_history_note_only_when_relevant(self) -> None:
        interaction = FakeInteraction(user=self._member(user_id=777))

        await handle_stats(interaction, self.bot, "member", False, None)

        self.assertIn("only tracked for suggestions added since this feature shipped", interaction.response.sent_message)


class SuggestionTypeTests(HandleStatsTestCase):
    async def test_requires_a_suggestion_query(self) -> None:
        interaction = FakeInteraction()

        await handle_stats(interaction, self.bot, "suggestion", False, None)

        self.assertIn("Provide a suggestion reference number or title", interaction.response.sent_message)

    async def test_requires_a_non_blank_suggestion_query(self) -> None:
        interaction = FakeInteraction()

        await handle_stats(interaction, self.bot, "suggestion", False, "   ")

        self.assertIn("Provide a suggestion reference number or title", interaction.response.sent_message)

    async def test_reports_no_match_clearly(self) -> None:
        interaction = FakeInteraction()

        await handle_stats(interaction, self.bot, "suggestion", False, "Nonexistent Movie")

        self.assertIn('No suggestion matches "Nonexistent Movie"', interaction.response.sent_message)

    async def test_a_single_match_shows_statistics_directly(self) -> None:
        result = self.suggestion_service.suggest("Alien", database_id=self.database.database_id)
        interaction = FakeInteraction()

        await handle_stats(interaction, self.bot, "suggestion", False, "Alien")

        self.assertIn("Suggestion Statistics -- Alien", interaction.response.sent_message)
        self.assertIn(f"#{result.watch_item.id:04d}", interaction.response.sent_message)

    async def test_a_reference_number_query_matches(self) -> None:
        result = self.suggestion_service.suggest("Alien", database_id=self.database.database_id)
        interaction = FakeInteraction()

        await handle_stats(interaction, self.bot, "suggestion", False, str(result.watch_item.id))

        self.assertIn("Suggestion Statistics -- Alien", interaction.response.sent_message)

    async def test_multiple_matches_show_a_selector(self) -> None:
        self.suggestion_service.suggest("Alien", database_id=self.database.database_id)
        other_database = self.suggestion_service.create_database(
            "Other Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID + 1
        ).database
        self.suggestion_service.suggest("Alien", database_id=other_database.database_id)
        interaction = FakeInteraction()

        await handle_stats(interaction, self.bot, "suggestion", False, "Alien")

        self.assertIsNotNone(interaction.response.sent_view)
        self.assertIn("Multiple suggestions match", interaction.response.sent_message)


class RotationTypeTests(HandleStatsTestCase):
    async def test_reports_no_rotation_started_yet(self) -> None:
        interaction = FakeInteraction()

        await handle_stats(interaction, self.bot, "rotation", False, None)

        self.assertIn("No rotation has been started for this database yet.", interaction.response.sent_message)

    async def test_reports_current_rotation_progress(self) -> None:
        self.suggestion_service.suggest("Alien", database_id=self.database.database_id)
        self.rotation_service.get_or_start_rotation(self.database.database_id)
        interaction = FakeInteraction()

        await handle_stats(interaction, self.bot, "rotation", False, None)

        self.assertIn("Rotation Statistics -- Movie Night", interaction.response.sent_message)
        self.assertIn("Total assigned: 1 suggestion", interaction.response.sent_message)

    async def test_shows_a_database_picker_when_multiple_databases_exist(self) -> None:
        self.suggestion_service.create_database("Second Night", guild_id=GUILD_ID, channel_id=CHANNEL_ID + 1)
        interaction = FakeInteraction(channel_id=999999)

        await handle_stats(interaction, self.bot, "rotation", False, None)

        self.assertIsNotNone(interaction.response.sent_view)
        self.assertIn("Multiple suggestion databases are configured", interaction.response.sent_message)


class DatabaseTypeTests(HandleStatsTestCase):
    async def test_reports_an_empty_database_gracefully(self) -> None:
        interaction = FakeInteraction()

        await handle_stats(interaction, self.bot, "database", False, None)

        self.assertIn("Database Statistics -- Movie Night", interaction.response.sent_message)
        self.assertIn("Active suggestions: 0 suggestions", interaction.response.sent_message)

    async def test_reports_suggestion_counts(self) -> None:
        self.suggestion_service.suggest("Alien", database_id=self.database.database_id)
        interaction = FakeInteraction()

        await handle_stats(interaction, self.bot, "database", False, None)

        self.assertIn("Active suggestions: 1 suggestion", interaction.response.sent_message)

    async def test_no_database_available_reports_clearly(self) -> None:
        service = SuggestionService(
            repository=JsonSuggestionRepository(Path(self._temp_dir.name) / "empty_suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(
                Path(self._temp_dir.name) / "empty_suggestion_databases.json"
            ),
        )
        empty_bot = FakeBot(
            service,
            StatisticsService(service),
            RotationService(service, repository=JsonRotationRepository(Path(self._temp_dir.name) / "empty_rotations.json")),
            SuggestionDatabaseConfigurationRepository(Path(self._temp_dir.name) / "empty_configurations.json"),
        )
        interaction = FakeInteraction()

        await handle_stats(interaction, empty_bot, "database", False, None)

        self.assertIn("must configure a suggestion database", interaction.response.sent_message)


class PaginationTests(HandleStatsTestCase):
    async def test_a_short_statistics_message_is_not_paginated(self) -> None:
        interaction = FakeInteraction()

        await handle_stats(interaction, self.bot, "server", False, None)

        self.assertIsNone(interaction.response.sent_view)

    async def test_an_overlong_statistics_message_paginates(self) -> None:
        from watch_party_manager.bot import paginate_stats_text

        long_text = "**Server Statistics**\n\n" + "\n".join(f"Line {i}: filler content" for i in range(200))

        pages = paginate_stats_text(long_text)

        self.assertGreater(len(pages), 1)
        for page in pages:
            self.assertLessEqual(len(page), 2000)
        self.assertTrue(all(page.startswith("**Server Statistics**") for page in pages))


if __name__ == "__main__":
    unittest.main()
