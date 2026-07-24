"""Tests for /about's bot.py wiring (handle_about) -- the single status
and information dashboard that consolidates the former, separate
/diagnostics command's Health/Configuration/Runtime information.

Everyone gets the WASH identity and Documentation fields; WASH Crew also
get Health, Configuration, and Runtime. Covers what test_about_service.py
cannot: permission-based branching, live guild-scoped data gathering, and
the embed actually sent to Discord (never raw content, so no GitHub
link-preview card).
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from watch_party_manager.bot import handle_about, resolve_active_database_display_name
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.persistence.watch_party_repository import JsonWatchPartyRepository
from watch_party_manager.services.imdb_metadata_service import ImdbMetadataService
from watch_party_manager.services.permission_service import PermissionService
from watch_party_manager.services.statistics_service import StatisticsService
from watch_party_manager.services.suggestion_input_service import SuggestionInputService
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.watch_party_service import WatchPartyService

GUILD_ID = 100
WASH_CREW_ROLE_ID = 999
WATCH_PARTY_MEMBER_ROLE_ID = 555


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, role_ids=()) -> None:
        self.roles = [FakeRole(role_id) for role_id in role_ids]


class FakeGuild:
    def __init__(self, name: str = "Test Guild") -> None:
        self.name = name


class FakeResponse:
    def __init__(self) -> None:
        self.sent_content = None
        self.sent_embed = None
        self.sent_ephemeral = None

    async def send_message(self, content=None, embed=None, ephemeral=False) -> None:
        self.sent_content = content
        self.sent_embed = embed
        self.sent_ephemeral = ephemeral


class FakeInteraction:
    def __init__(self, user=None, guild_id=GUILD_ID, guild=None) -> None:
        self.user = user if user is not None else FakeMember([WATCH_PARTY_MEMBER_ROLE_ID])
        self.guild_id = guild_id
        self.guild = guild if guild is not None else (FakeGuild() if guild_id is not None else None)
        self.response = FakeResponse()


class FakeSchedulerHost:
    def __init__(self, is_running: bool = True) -> None:
        self.is_running = is_running


class AboutCommandTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.watch_party_service = WatchPartyService(
            self.suggestion_service, repository=JsonWatchPartyRepository(root / "watch_parties.json")
        )
        self.statistics_service = StatisticsService(
            self.suggestion_service,
            vote_source=JsonVoteRepository(root / "voting.json"),
            watch_party_source=self.watch_party_service,
        )

        class FakeBot:
            pass

        self.bot = FakeBot()
        self.bot.suggestion_service = self.suggestion_service
        self.bot.watch_party_service = self.watch_party_service
        self.bot.statistics_service = self.statistics_service
        self.bot.suggestion_input_service = SuggestionInputService()
        self.bot.permission_service = PermissionService(
            watch_party_member_role_id=WATCH_PARTY_MEMBER_ROLE_ID, wash_crew_role_id=WASH_CREW_ROLE_ID
        )
        self.bot.wash_crew_role_id = WASH_CREW_ROLE_ID
        self.bot.scheduler_host = FakeSchedulerHost(is_running=True)
        self.bot.interactive_voting_restored = True
        self.bot.latency = 0.0426
        self.bot.started_at = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)
        self.bot.is_ready = lambda: True

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _crew_member(self) -> FakeMember:
        return FakeMember([WASH_CREW_ROLE_ID])

    def _fields_by_name(self, embed):
        return {field.name: field.value for field in embed.fields}


class HandleAboutEveryoneTests(AboutCommandTestCase):
    async def test_sends_an_embed_not_plain_content(self) -> None:
        interaction = FakeInteraction()

        await handle_about(interaction, self.bot)

        self.assertIsNone(interaction.response.sent_content)
        self.assertIsNotNone(interaction.response.sent_embed)

    async def test_response_is_ephemeral(self) -> None:
        interaction = FakeInteraction()

        await handle_about(interaction, self.bot)

        self.assertTrue(interaction.response.sent_ephemeral)

    async def test_non_crew_member_only_sees_the_documentation_field(self) -> None:
        interaction = FakeInteraction(user=FakeMember([WATCH_PARTY_MEMBER_ROLE_ID]))

        await handle_about(interaction, self.bot)

        fields = self._fields_by_name(interaction.response.sent_embed)
        self.assertEqual({"Documentation"}, set(fields.keys()))

    async def test_documentation_links_do_not_appear_as_raw_message_content(self) -> None:
        interaction = FakeInteraction()

        await handle_about(interaction, self.bot)

        self.assertIsNone(interaction.response.sent_content)
        fields = self._fields_by_name(interaction.response.sent_embed)
        self.assertIn("github.com", fields["Documentation"])

    async def test_crew_member_outside_a_guild_also_sees_the_reduced_view(self) -> None:
        interaction = FakeInteraction(user=self._crew_member(), guild_id=None)

        await handle_about(interaction, self.bot)

        fields = self._fields_by_name(interaction.response.sent_embed)
        self.assertEqual({"Documentation"}, set(fields.keys()))


class HandleAboutWashCrewTests(AboutCommandTestCase):
    async def test_crew_member_sees_all_expanded_sections(self) -> None:
        interaction = FakeInteraction(user=self._crew_member())

        await handle_about(interaction, self.bot)

        fields = self._fields_by_name(interaction.response.sent_embed)
        self.assertEqual({"Health", "Configuration", "Runtime", "Documentation"}, set(fields.keys()))

    async def test_health_reflects_scheduler_and_voting_restoration_state(self) -> None:
        self.bot.scheduler_host = FakeSchedulerHost(is_running=False)
        self.bot.interactive_voting_restored = False
        interaction = FakeInteraction(user=self._crew_member())

        await handle_about(interaction, self.bot)

        health = self._fields_by_name(interaction.response.sent_embed)["Health"]
        self.assertIn("Scheduler: 🔴 Stopped", health)
        self.assertIn("Interactive voting restored: No", health)

    async def test_health_reflects_omdb_configuration(self) -> None:
        self.bot.suggestion_input_service = SuggestionInputService(
            ImdbMetadataService(api_key="configured")
        )
        interaction = FakeInteraction(user=self._crew_member())

        await handle_about(interaction, self.bot)

        health = self._fields_by_name(interaction.response.sent_embed)["Health"]
        self.assertIn("OMDb integration: 🟢 Configured", health)

    async def test_configuration_reflects_live_guild_data(self) -> None:
        database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=200
        ).database
        self.suggestion_service.suggest("The Matrix", database_id=database.database_id, guild_id=GUILD_ID)
        self.watch_party_service.schedule_watch_party(
            watch_item_id=self.suggestion_service.get_suggestions()[0].id,
            scheduled_at=datetime.now(timezone.utc) + timedelta(days=1),
            guild_id=GUILD_ID,
        )
        interaction = FakeInteraction(user=self._crew_member())

        await handle_about(interaction, self.bot)

        configuration = self._fields_by_name(interaction.response.sent_embed)["Configuration"]
        self.assertIn("Active suggestion database: Movie Night", configuration)
        self.assertIn("Suggestion databases: 1", configuration)
        self.assertIn("Watch items: 1", configuration)
        self.assertIn("Scheduled watch parties: 1", configuration)
        self.assertIn("Active voting round: No", configuration)

    async def test_configuration_reports_no_active_database_when_none_exist(self) -> None:
        interaction = FakeInteraction(user=self._crew_member())

        await handle_about(interaction, self.bot)

        configuration = self._fields_by_name(interaction.response.sent_embed)["Configuration"]
        self.assertIn("Active suggestion database: None configured", configuration)

    async def test_runtime_reflects_guild_name_and_uptime(self) -> None:
        interaction = FakeInteraction(user=self._crew_member(), guild=FakeGuild(name="Movie Club"))

        await handle_about(interaction, self.bot)

        runtime = self._fields_by_name(interaction.response.sent_embed)["Runtime"]
        self.assertIn("Server: Movie Club", runtime)
        self.assertIn("Python:", runtime)
        self.assertIn("discord.py:", runtime)
        self.assertIn("Uptime:", runtime)

    async def test_watch_party_member_below_crew_does_not_see_expanded_sections(self) -> None:
        interaction = FakeInteraction(user=FakeMember([WATCH_PARTY_MEMBER_ROLE_ID]))

        await handle_about(interaction, self.bot)

        fields = self._fields_by_name(interaction.response.sent_embed)
        self.assertEqual({"Documentation"}, set(fields.keys()))


class ResolveActiveDatabaseDisplayNameTests(AboutCommandTestCase):
    def test_returns_the_name_when_exactly_one_database_is_active(self) -> None:
        self.suggestion_service.create_database("Movie Night", guild_id=GUILD_ID, channel_id=200)

        name = resolve_active_database_display_name(self.suggestion_service, GUILD_ID)

        self.assertEqual("Movie Night", name)

    def test_reports_none_configured_when_no_databases_exist(self) -> None:
        name = resolve_active_database_display_name(self.suggestion_service, GUILD_ID)

        self.assertEqual("None configured", name)

    def test_reports_a_count_when_multiple_databases_are_active(self) -> None:
        self.suggestion_service.create_database("Movie Night", guild_id=GUILD_ID, channel_id=200)
        self.suggestion_service.create_database("Anime Night", guild_id=GUILD_ID, channel_id=201)

        name = resolve_active_database_display_name(self.suggestion_service, GUILD_ID)

        self.assertIn("2 active", name)


if __name__ == "__main__":
    unittest.main()
