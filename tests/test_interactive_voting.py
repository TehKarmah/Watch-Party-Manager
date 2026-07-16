import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import (
    build_voting_post_text,
    handle_nominee_vote,
    perform_start_vote,
)
from watch_party_manager.domain.vote import VoteVisibility
from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.services.nominee_selection_service import NomineeSelectionService
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.vote_service import VoteService
from watch_party_manager.voting_view import MAX_NOMINEE_BUTTONS, VotingView

WASH_CREW_ROLE_ID = 999


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, user_id: int, roles=()) -> None:
        self.id = user_id
        self.roles = list(roles)


class FakeResponse:
    def __init__(self) -> None:
        self.sent_message = None
        self.sent_ephemeral = None

    async def send_message(self, content, ephemeral=False) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral


class FakeSentMessage:
    def __init__(self, message_id: int) -> None:
        self.id = message_id


class FakeVotingPostMessage:
    def __init__(self) -> None:
        self.edited_content = None
        self.edit_call_count = 0

    async def edit(self, content=None, **kwargs) -> None:
        self.edited_content = content
        self.edit_call_count += 1


class FakeInteraction:
    def __init__(self, user_id: int, message=None, guild_id=100, channel_id=200) -> None:
        self.user = FakeMember(user_id)
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.response = FakeResponse()
        self.message = message
        self._original_response = FakeSentMessage(message_id=9999)

    async def original_response(self):
        return self._original_response


class BuildVotingPostTextTests(unittest.TestCase):
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
        self.suggestion_service.suggest("The Matrix")
        self.suggestion_service.suggest("Inception")

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_lists_all_nominees(self) -> None:
        created = self.vote_service.create_round(visibility=VoteVisibility.VISIBLE)
        candidates = self.suggestion_service.get_suggestions()

        text = build_voting_post_text(created.vote_round, candidates, standings=None, standings_error=None)

        self.assertIn("[1] The Matrix", text)
        self.assertIn("[2] Inception", text)

    def test_shows_the_voting_deadline(self) -> None:
        created = self.vote_service.create_round(visibility=VoteVisibility.VISIBLE)
        candidates = self.suggestion_service.get_suggestions()

        text = build_voting_post_text(created.vote_round, candidates, standings=None, standings_error=None)

        self.assertIn("Voting ends:", text)

    def test_blind_round_hides_standings(self) -> None:
        created = self.vote_service.create_round(visibility=VoteVisibility.BLIND)
        candidates = self.suggestion_service.get_suggestions()

        text = build_voting_post_text(created.vote_round, candidates, standings=None, standings_error=None)

        self.assertNotIn("Standings", text)

    def test_blind_round_still_shows_total_participation_count(self) -> None:
        created = self.vote_service.create_round(visibility=VoteVisibility.BLIND)
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=1)
        candidates = self.suggestion_service.get_suggestions()

        text = build_voting_post_text(created.vote_round, candidates, standings=None, standings_error=None)

        self.assertIn("Votes cast: 1", text)

    def test_blind_round_does_not_reveal_nominee_totals(self) -> None:
        created = self.vote_service.create_round(visibility=VoteVisibility.BLIND)
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=1)
        self.vote_service.cast_vote(discord_user_id=2, suggestion_id=1)
        standings_result = self.vote_service.calculate_standings(created.vote_round.id)
        candidates = self.suggestion_service.get_suggestions()

        # Even if standings were computed and mistakenly passed in, a blind
        # round must never render them.
        text = build_voting_post_text(
            created.vote_round, candidates, standings=standings_result.standings, standings_error=None
        )

        self.assertNotIn("Standings", text)
        self.assertNotIn("2 votes", text)

    def test_visible_round_shows_standings(self) -> None:
        created = self.vote_service.create_round(visibility=VoteVisibility.VISIBLE)
        self.vote_service.cast_vote(discord_user_id=1, suggestion_id=1)
        standings_result = self.vote_service.calculate_standings(created.vote_round.id)
        candidates = self.suggestion_service.get_suggestions()

        text = build_voting_post_text(
            created.vote_round, candidates, standings=standings_result.standings, standings_error=None
        )

        self.assertIn("Standings:", text)
        self.assertIn("Suggestion #1", text)

    def test_shows_visibility_mode(self) -> None:
        created = self.vote_service.create_round(visibility=VoteVisibility.BLIND)
        candidates = self.suggestion_service.get_suggestions()

        text = build_voting_post_text(created.vote_round, candidates, standings=None, standings_error=None)

        self.assertIn("Blind", text)


class HandleNomineeVoteTests(unittest.IsolatedAsyncioTestCase):
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
        self.suggestion_service.suggest("The Matrix")
        self.suggestion_service.suggest("Inception")
        self.suggestion_service.suggest("Interstellar")

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    async def test_button_click_records_a_first_vote(self) -> None:
        self.vote_service.create_round(visibility=VoteVisibility.VISIBLE)
        interaction = FakeInteraction(user_id=111, message=FakeVotingPostMessage())

        await handle_nominee_vote(interaction, self.vote_service, self.suggestion_service, suggestion_id=1)

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIn("recorded", interaction.response.sent_message)
        self.assertEqual(self.vote_service.get_open_round().votes[111].suggestion_id, 1)

    async def test_button_click_changes_a_vote_when_allowed(self) -> None:
        self.vote_service.create_round(visibility=VoteVisibility.VISIBLE)
        interaction = FakeInteraction(user_id=111, message=FakeVotingPostMessage())
        await handle_nominee_vote(interaction, self.vote_service, self.suggestion_service, suggestion_id=1)

        interaction2 = FakeInteraction(user_id=111, message=FakeVotingPostMessage())
        await handle_nominee_vote(interaction2, self.vote_service, self.suggestion_service, suggestion_id=2)

        self.assertTrue(interaction2.response.sent_ephemeral)
        self.assertIn("updated", interaction2.response.sent_message)
        self.assertEqual(self.vote_service.get_open_round().votes[111].suggestion_id, 2)

    async def test_duplicate_vote_is_rejected(self) -> None:
        self.vote_service.create_round(visibility=VoteVisibility.VISIBLE)
        interaction = FakeInteraction(user_id=111, message=FakeVotingPostMessage())
        await handle_nominee_vote(interaction, self.vote_service, self.suggestion_service, suggestion_id=1)

        interaction2 = FakeInteraction(user_id=111, message=FakeVotingPostMessage())
        await handle_nominee_vote(interaction2, self.vote_service, self.suggestion_service, suggestion_id=1)

        self.assertIn("already voted", interaction2.response.sent_message)

    async def test_exhausted_change_allowance_is_rejected(self) -> None:
        self.vote_service.create_round(visibility=VoteVisibility.VISIBLE)
        interaction = FakeInteraction(user_id=111, message=FakeVotingPostMessage())
        await handle_nominee_vote(interaction, self.vote_service, self.suggestion_service, suggestion_id=1)
        interaction2 = FakeInteraction(user_id=111, message=FakeVotingPostMessage())
        await handle_nominee_vote(interaction2, self.vote_service, self.suggestion_service, suggestion_id=2)

        interaction3 = FakeInteraction(user_id=111, message=FakeVotingPostMessage())
        await handle_nominee_vote(interaction3, self.vote_service, self.suggestion_service, suggestion_id=3)

        self.assertIn("already used your one vote change", interaction3.response.sent_message)
        self.assertEqual(self.vote_service.get_open_round().votes[111].suggestion_id, 2)

    async def test_confirmation_is_always_ephemeral(self) -> None:
        self.vote_service.create_round(visibility=VoteVisibility.VISIBLE)
        interaction = FakeInteraction(user_id=111, message=FakeVotingPostMessage())

        await handle_nominee_vote(interaction, self.vote_service, self.suggestion_service, suggestion_id=1)

        self.assertTrue(interaction.response.sent_ephemeral)

    async def test_visible_round_refreshes_the_post_after_a_vote(self) -> None:
        self.vote_service.create_round(visibility=VoteVisibility.VISIBLE)
        message = FakeVotingPostMessage()
        interaction = FakeInteraction(user_id=111, message=message)

        await handle_nominee_vote(interaction, self.vote_service, self.suggestion_service, suggestion_id=1)

        self.assertEqual(message.edit_call_count, 1)
        self.assertIn("Standings", message.edited_content)
        self.assertIn("Votes cast: 1", message.edited_content)

    async def test_blind_round_does_not_refresh_the_post(self) -> None:
        self.vote_service.create_round(visibility=VoteVisibility.BLIND)
        message = FakeVotingPostMessage()
        interaction = FakeInteraction(user_id=111, message=message)

        await handle_nominee_vote(interaction, self.vote_service, self.suggestion_service, suggestion_id=1)

        self.assertEqual(message.edit_call_count, 0)

    async def test_closed_round_click_is_handled_cleanly(self) -> None:
        created = self.vote_service.create_round(visibility=VoteVisibility.VISIBLE)
        self.vote_service.close_round(created.vote_round.id)
        interaction = FakeInteraction(user_id=111, message=FakeVotingPostMessage())

        await handle_nominee_vote(interaction, self.vote_service, self.suggestion_service, suggestion_id=1)

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIn("no open voting round", interaction.response.sent_message.lower())

    async def test_missing_round_click_is_handled_cleanly(self) -> None:
        interaction = FakeInteraction(user_id=111, message=FakeVotingPostMessage())

        await handle_nominee_vote(interaction, self.vote_service, self.suggestion_service, suggestion_id=1)

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIn("no open voting round", interaction.response.sent_message.lower())

    async def test_different_members_click_independently(self) -> None:
        self.vote_service.create_round(visibility=VoteVisibility.VISIBLE)
        interaction1 = FakeInteraction(user_id=111, message=FakeVotingPostMessage())
        interaction2 = FakeInteraction(user_id=222, message=FakeVotingPostMessage())

        await handle_nominee_vote(interaction1, self.vote_service, self.suggestion_service, suggestion_id=1)
        await handle_nominee_vote(interaction2, self.vote_service, self.suggestion_service, suggestion_id=2)

        vote_round = self.vote_service.get_open_round()
        self.assertEqual(vote_round.votes[111].suggestion_id, 1)
        self.assertEqual(vote_round.votes[222].suggestion_id, 2)


class StartVoteCreatesAVotingPostTests(unittest.IsolatedAsyncioTestCase):
    """Exercises the same sequence of calls the /start_vote command handler
    makes, since the real handler is a nested closure inside setup_hook and
    isn't independently importable/testable.
    """

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
        self.suggestion_service.suggest("The Matrix")
        self.suggestion_service.suggest("Inception")
        self.suggestion_service.suggest("Interstellar")

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _authorized_user(self) -> FakeMember:
        return FakeMember(user_id=1, roles=[FakeRole(WASH_CREW_ROLE_ID)])

    async def test_start_vote_creates_a_voting_post_with_correct_nominee_buttons(self) -> None:
        message, ephemeral = perform_start_vote(
            vote_service=self.vote_service,
            suggestion_service=self.suggestion_service,
            nominee_selection_service=None,
            user=self._authorized_user(),
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            visibility_str="visible",
            duration_days=None,
        )
        self.assertFalse(ephemeral)

        vote_round = self.vote_service.get_open_round()
        self.assertIsNotNone(vote_round)
        candidates = self.suggestion_service.get_suggestions()

        async def on_vote(interaction, suggestion_id) -> None:
            await handle_nominee_vote(interaction, self.vote_service, self.suggestion_service, suggestion_id)

        view = VotingView(candidates, on_vote=on_vote)
        self.assertEqual(len(view.children), 3)
        self.assertEqual({button.suggestion_id for button in view.children}, {1, 2, 3})

        post_text = build_voting_post_text(vote_round, candidates, standings=None, standings_error=None)
        self.assertIn(f"Voting round {vote_round.id} is open!", post_text)
        self.assertIn("[1] The Matrix", post_text)

    async def test_message_ids_are_stored_after_the_post_is_sent(self) -> None:
        perform_start_vote(
            vote_service=self.vote_service,
            suggestion_service=self.suggestion_service,
            nominee_selection_service=None,
            user=self._authorized_user(),
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            visibility_str="visible",
            duration_days=None,
        )
        vote_round = self.vote_service.get_open_round()

        interaction = FakeInteraction(user_id=1, guild_id=100, channel_id=200)
        sent_message = await interaction.original_response()
        updated = self.vote_service.attach_message_reference(
            vote_round.id, interaction.guild_id, interaction.channel_id, sent_message.id
        )

        self.assertTrue(updated)
        stored_round = self.vote_service.get_round(vote_round.id)
        self.assertEqual(stored_round.guild_id, 100)
        self.assertEqual(stored_round.channel_id, 200)
        self.assertEqual(stored_round.message_id, sent_message.id)


class StartVoteWithSelectionServiceTests(unittest.IsolatedAsyncioTestCase):
    """Exercises /start_vote's database-aware path, where nominees come from
    NomineeSelectionService rather than the simple in-memory slice used when
    there's no guild/channel context.
    """

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

    def _authorized_user(self) -> FakeMember:
        return FakeMember(user_id=1, roles=[FakeRole(WASH_CREW_ROLE_ID)])

    def test_selection_is_limited_to_the_resolved_database(self) -> None:
        other_database_id = self.suggestion_service.create_database(
            "Kung Fu Movies", guild_id=100, channel_id=201
        ).database.database_id
        self.suggestion_service.suggest("The Matrix", database_id=self.database_id)
        self.suggestion_service.suggest("Inception", database_id=self.database_id)
        self.suggestion_service.suggest("Enter the Dragon", database_id=other_database_id)

        message, ephemeral = perform_start_vote(
            vote_service=self.vote_service,
            suggestion_service=self.suggestion_service,
            nominee_selection_service=self.selector,
            user=self._authorized_user(),
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            visibility_str="visible",
            duration_days=None,
            guild_id=100,
            channel_id=200,
        )

        self.assertFalse(ephemeral)
        vote_round = self.vote_service.get_open_round()
        self.assertEqual(len(vote_round.candidate_suggestion_ids), 2)

        titles_by_id = {item.id: item.title for item in self.suggestion_service.get_suggestions()}
        nominee_titles = {titles_by_id[nid] for nid in vote_round.candidate_suggestion_ids}
        self.assertNotIn("Enter the Dragon", nominee_titles)

    def test_low_pool_uses_every_eligible_suggestion_instead_of_rejecting(self) -> None:
        self.suggestion_service.suggest("The Matrix", database_id=self.database_id)
        self.suggestion_service.suggest("Inception", database_id=self.database_id)

        message, ephemeral = perform_start_vote(
            vote_service=self.vote_service,
            suggestion_service=self.suggestion_service,
            nominee_selection_service=self.selector,
            user=self._authorized_user(),
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            visibility_str="visible",
            duration_days=None,
            nominee_count=5,
            guild_id=100,
            channel_id=200,
        )

        self.assertFalse(ephemeral)
        vote_round = self.vote_service.get_open_round()
        self.assertEqual(len(vote_round.candidate_suggestion_ids), 2)

    def test_insufficient_suggestions_is_rejected(self) -> None:
        self.suggestion_service.suggest("The Matrix", database_id=self.database_id)

        message, ephemeral = perform_start_vote(
            vote_service=self.vote_service,
            suggestion_service=self.suggestion_service,
            nominee_selection_service=self.selector,
            user=self._authorized_user(),
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            visibility_str="visible",
            duration_days=None,
            guild_id=100,
            channel_id=200,
        )

        self.assertTrue(ephemeral)
        self.assertIn("At least 2", message)
        self.assertIsNone(self.vote_service.get_open_round())

    def test_selected_nominees_persist_correctly(self) -> None:
        self.suggestion_service.suggest("The Matrix", database_id=self.database_id)
        self.suggestion_service.suggest("Inception", database_id=self.database_id)
        self.suggestion_service.suggest("Interstellar", database_id=self.database_id)

        perform_start_vote(
            vote_service=self.vote_service,
            suggestion_service=self.suggestion_service,
            nominee_selection_service=self.selector,
            user=self._authorized_user(),
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            visibility_str="visible",
            duration_days=None,
            nominee_count=2,
            guild_id=100,
            channel_id=200,
        )
        vote_round = self.vote_service.get_open_round()
        self.assertEqual(vote_round.database_id, self.database_id)

        reloaded = JsonVoteRepository(Path(self._temp_dir.name) / "voting.json").load()
        self.assertEqual(reloaded.rounds[0].candidate_suggestion_ids, vote_round.candidate_suggestion_ids)
        self.assertEqual(len(reloaded.rounds[0].candidate_suggestion_ids), 2)

    async def test_interactive_voting_still_functions_with_selected_nominees(self) -> None:
        self.suggestion_service.suggest("The Matrix", database_id=self.database_id)
        self.suggestion_service.suggest("Inception", database_id=self.database_id)
        self.suggestion_service.suggest("Interstellar", database_id=self.database_id)

        perform_start_vote(
            vote_service=self.vote_service,
            suggestion_service=self.suggestion_service,
            nominee_selection_service=self.selector,
            user=self._authorized_user(),
            wash_crew_role_id=WASH_CREW_ROLE_ID,
            visibility_str="visible",
            duration_days=None,
            nominee_count=2,
            guild_id=100,
            channel_id=200,
        )
        vote_round = self.vote_service.get_open_round()
        nominee_id = vote_round.candidate_suggestion_ids[0]

        interaction = FakeInteraction(user_id=111, message=FakeVotingPostMessage())
        await handle_nominee_vote(interaction, self.vote_service, self.suggestion_service, suggestion_id=nominee_id)

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIn("recorded", interaction.response.sent_message)
        self.assertEqual(self.vote_service.get_open_round().votes[111].suggestion_id, nominee_id)


if __name__ == "__main__":
    unittest.main()
