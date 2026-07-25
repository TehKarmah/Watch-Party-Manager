"""Tests for the release-blocking fix making voting collection-scoped.

Covers /vote_status and /edit_vote's contextual collection resolution
(handle_vote_status, handle_edit_vote) -- the two commands that used to
call VoteService.get_open_round()/get_latest_round() with no scoping at
all, so they always operated on whichever round happened to be found
first regardless of which collection's channel the command was run in.
Both now reuse resolve_database_then, the same contextual collection
picker model every other collection-scoped command (e.g. /add, /list)
already uses. VoteService's own collection-scoping (create_round,
get_open_round(database_id), get_open_round_for_suggestion,
get_open_rounds) is covered directly in test_vote_service.py; this file
covers the Discord-command layer built on top of it.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import handle_edit_vote, handle_vote_status
from watch_party_manager.domain.vote import VoteVisibility
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.services.permission_service import PermissionService
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.vote_completion_service import VoteCompletionService
from watch_party_manager.services.vote_service import VoteService

GUILD_ID = 100
WASH_CREW_ROLE_ID = 999
WATCH_PARTY_MEMBER_ROLE_ID = 555


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, role_ids=(), *, user_id: int = 1) -> None:
        self.roles = [FakeRole(role_id) for role_id in role_ids]
        self.id = user_id


class FakeGuild:
    def __init__(self, name: str = "Test Guild") -> None:
        self.name = name

    def get_channel_or_thread(self, channel_id):
        # No live Discord channel to resolve in tests -- callers fall
        # back to each collection's stored name (see bot.py's
        # _resolve_collection_name).
        return None


class FakeResponse:
    def __init__(self) -> None:
        self.sent_message = None
        self.sent_ephemeral = None
        self.sent_view = None

    async def send_message(self, content, ephemeral=False, view=None) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral
        self.sent_view = view


class FakeInteraction:
    def __init__(self, user=None, guild=None, guild_id=GUILD_ID, channel_id=None) -> None:
        self.user = user if user is not None else FakeMember([WASH_CREW_ROLE_ID], user_id=1)
        self.guild = guild if guild is not None else (FakeGuild() if guild_id is not None else None)
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.response = FakeResponse()


class FakeSchedulerHost:
    def __init__(self) -> None:
        self.scheduler_service = None


class FakeBot:
    def __init__(self, *, root: Path, wash_crew_role_id=WASH_CREW_ROLE_ID) -> None:
        self.suggestion_database_repository = JsonSuggestionDatabaseRepository(
            root / "suggestion_databases.json"
        )
        self.suggestion_repository = JsonSuggestionRepository(root / "suggestions.json")
        self.suggestion_database_configuration_repository = SuggestionDatabaseConfigurationRepository(
            root / "suggestion_database_configurations.json"
        )
        self.suggestion_service = SuggestionService(
            repository=self.suggestion_repository, database_repository=self.suggestion_database_repository
        )
        self.vote_service = VoteService(
            self.suggestion_service, repository=JsonVoteRepository(root / "voting.json")
        )
        self.vote_completion_service = VoteCompletionService(self.vote_service, self.suggestion_service)
        self.permission_service = PermissionService(
            watch_party_member_role_id=WATCH_PARTY_MEMBER_ROLE_ID, wash_crew_role_id=wash_crew_role_id
        )
        self.wash_crew_role_id = wash_crew_role_id
        self.guild_configuration_repository = None
        self.scheduler_host = FakeSchedulerHost()


class VoteContextResolutionTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self._temp_dir.name)
        self.bot = FakeBot(root=self.root)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _crew_member(self) -> FakeMember:
        return FakeMember([WASH_CREW_ROLE_ID], user_id=1)

    def _make_collection_with_open_round(self, name: str, channel_id: int, *, candidate_titles=("A", "B")):
        database = self.bot.suggestion_service.create_database(
            name, guild_id=GUILD_ID, channel_id=channel_id
        ).database
        candidates = [
            self.bot.suggestion_service.suggest(title, database_id=database.database_id).watch_item
            for title in candidate_titles
        ]
        vote_round = self.bot.vote_service.create_round(
            visibility=VoteVisibility.VISIBLE,
            candidate_suggestion_ids=[candidate.id for candidate in candidates],
            database_id=database.database_id,
        ).vote_round
        return database, vote_round


class HandleVoteStatusContextResolutionTests(VoteContextResolutionTestCase):
    async def test_reports_status_automatically_when_only_one_collection_exists(self) -> None:
        database, vote_round = self._make_collection_with_open_round("Movies", channel_id=201)
        interaction = FakeInteraction(user=self._crew_member(), channel_id=201)

        await handle_vote_status(interaction, self.bot)

        self.assertIn(f"Voting round {vote_round.id}", interaction.response.sent_message)

    async def test_resolves_to_the_collection_matching_the_current_channel(self) -> None:
        movies, movies_round = self._make_collection_with_open_round("Movies", channel_id=201)
        tv_shows, tv_round = self._make_collection_with_open_round(
            "TV Shows", channel_id=202, candidate_titles=("C", "D")
        )
        interaction = FakeInteraction(user=self._crew_member(), channel_id=202)

        await handle_vote_status(interaction, self.bot)

        self.assertIn(f"Voting round {tv_round.id}", interaction.response.sent_message)
        self.assertNotIn(f"Voting round {movies_round.id}", interaction.response.sent_message)

    async def test_shows_a_picker_when_channel_context_is_ambiguous(self) -> None:
        self._make_collection_with_open_round("Movies", channel_id=201)
        self._make_collection_with_open_round("TV Shows", channel_id=202, candidate_titles=("C", "D"))
        interaction = FakeInteraction(user=self._crew_member(), channel_id=999999)

        await handle_vote_status(interaction, self.bot)

        self.assertIsNotNone(interaction.response.sent_view)
        self.assertIn("Which collection would you like to use?", interaction.response.sent_message)

    async def test_selecting_from_the_picker_shows_that_collections_status(self) -> None:
        movies, movies_round = self._make_collection_with_open_round("Movies", channel_id=201)
        tv_shows, tv_round = self._make_collection_with_open_round(
            "TV Shows", channel_id=202, candidate_titles=("C", "D")
        )
        interaction = FakeInteraction(user=self._crew_member(), channel_id=999999)
        await handle_vote_status(interaction, self.bot)
        view = interaction.response.sent_view

        select = view.children[0]
        select._values = [str(tv_shows.database_id)]
        select_interaction = FakeInteraction(user=self._crew_member())
        await select.callback(interaction=select_interaction)

        self.assertIn(f"Voting round {tv_round.id}", select_interaction.response.sent_message)

    async def test_no_collections_configured_shows_a_clear_error(self) -> None:
        interaction = FakeInteraction(user=self._crew_member(), channel_id=201)

        await handle_vote_status(interaction, self.bot)

        self.assertIn("collection", interaction.response.sent_message.lower())

    async def test_unauthorized_user_is_rejected(self) -> None:
        self._make_collection_with_open_round("Movies", channel_id=201)
        interaction = FakeInteraction(user=FakeMember([]), channel_id=201)

        await handle_vote_status(interaction, self.bot)

        self.assertIn("WASH Crew", interaction.response.sent_message)

    async def test_rejected_outside_a_server(self) -> None:
        interaction = FakeInteraction(user=self._crew_member(), guild_id=None)

        await handle_vote_status(interaction, self.bot)

        self.assertIn("server", interaction.response.sent_message.lower())


class HandleEditVoteContextResolutionTests(VoteContextResolutionTestCase):
    async def test_manages_the_round_automatically_when_only_one_collection_exists(self) -> None:
        database, vote_round = self._make_collection_with_open_round("Movies", channel_id=201)
        interaction = FakeInteraction(user=self._crew_member(), channel_id=201)

        await handle_edit_vote(interaction, self.bot)

        self.assertIn(f"Managing voting round {vote_round.id}", interaction.response.sent_message)
        self.assertIsNotNone(interaction.response.sent_view)

    async def test_resolves_to_the_collection_matching_the_current_channel(self) -> None:
        movies, movies_round = self._make_collection_with_open_round("Movies", channel_id=201)
        tv_shows, tv_round = self._make_collection_with_open_round(
            "TV Shows", channel_id=202, candidate_titles=("C", "D")
        )
        interaction = FakeInteraction(user=self._crew_member(), channel_id=202)

        await handle_edit_vote(interaction, self.bot)

        self.assertIn(f"Managing voting round {tv_round.id}", interaction.response.sent_message)

    async def test_managing_one_collections_round_never_reports_anothers(self) -> None:
        movies, movies_round = self._make_collection_with_open_round("Movies", channel_id=201)
        tv_shows, tv_round = self._make_collection_with_open_round(
            "TV Shows", channel_id=202, candidate_titles=("C", "D")
        )
        interaction = FakeInteraction(user=self._crew_member(), channel_id=201)

        await handle_edit_vote(interaction, self.bot)

        self.assertIn(f"Managing voting round {movies_round.id}", interaction.response.sent_message)
        self.assertNotIn(f"Managing voting round {tv_round.id}", interaction.response.sent_message)

    async def test_shows_a_picker_when_channel_context_is_ambiguous(self) -> None:
        self._make_collection_with_open_round("Movies", channel_id=201)
        self._make_collection_with_open_round("TV Shows", channel_id=202, candidate_titles=("C", "D"))
        interaction = FakeInteraction(user=self._crew_member(), channel_id=999999)

        await handle_edit_vote(interaction, self.bot)

        self.assertIsNotNone(interaction.response.sent_view)
        self.assertIn("Which collection would you like to use?", interaction.response.sent_message)

    async def test_selecting_from_the_picker_manages_that_collections_round(self) -> None:
        movies, movies_round = self._make_collection_with_open_round("Movies", channel_id=201)
        tv_shows, tv_round = self._make_collection_with_open_round(
            "TV Shows", channel_id=202, candidate_titles=("C", "D")
        )
        interaction = FakeInteraction(user=self._crew_member(), channel_id=999999)
        await handle_edit_vote(interaction, self.bot)
        view = interaction.response.sent_view

        select = view.children[0]
        select._values = [str(movies.database_id)]
        select_interaction = FakeInteraction(user=self._crew_member())
        await select.callback(interaction=select_interaction)

        self.assertIn(f"Managing voting round {movies_round.id}", select_interaction.response.sent_message)

    async def test_reports_no_active_round_for_a_collection_with_none_open(self) -> None:
        database = self.bot.suggestion_service.create_database(
            "Movies", guild_id=GUILD_ID, channel_id=201
        ).database
        interaction = FakeInteraction(user=self._crew_member(), channel_id=201)

        await handle_edit_vote(interaction, self.bot)

        self.assertIn("no active voting round", interaction.response.sent_message.lower())

    async def test_a_second_collections_round_is_still_manageable_after_the_first_closes(self) -> None:
        movies, movies_round = self._make_collection_with_open_round("Movies", channel_id=201)
        tv_shows, tv_round = self._make_collection_with_open_round(
            "TV Shows", channel_id=202, candidate_titles=("C", "D")
        )
        self.bot.vote_service.close_round(movies_round.id)
        interaction = FakeInteraction(user=self._crew_member(), channel_id=202)

        await handle_edit_vote(interaction, self.bot)

        self.assertIn(f"Managing voting round {tv_round.id}", interaction.response.sent_message)

    async def test_unauthorized_user_is_rejected_after_resolution(self) -> None:
        self._make_collection_with_open_round("Movies", channel_id=201)
        interaction = FakeInteraction(user=FakeMember([]), channel_id=201)

        await handle_edit_vote(interaction, self.bot)

        self.assertIn("WASH Crew", interaction.response.sent_message)

    async def test_rejected_outside_a_server(self) -> None:
        interaction = FakeInteraction(user=self._crew_member(), guild_id=None)

        await handle_edit_vote(interaction, self.bot)

        self.assertIn("server", interaction.response.sent_message.lower())


if __name__ == "__main__":
    unittest.main()
