import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import restore_persistent_voting_view
from watch_party_manager.domain.vote import VoteVisibility
from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.vote_service import VoteService
from watch_party_manager.voting_view import VotingView


class FakeBot:
    def __init__(self) -> None:
        self.calls = []

    def add_view(self, view, *, message_id=None) -> None:
        self.calls.append((view, message_id))


class PersistentVotingViewTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(
                root / "suggestion_databases.json"
            ),
        )
        self.vote_service = VoteService(
            self.suggestion_service,
            repository=JsonVoteRepository(root / "voting.json"),
        )
        self.first = self.suggestion_service.suggest("The Matrix").watch_item
        self.second = self.suggestion_service.suggest("Inception").watch_item

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_returns_false_when_no_round_is_open(self) -> None:
        bot = FakeBot()

        restored = restore_persistent_voting_view(
            bot, self.vote_service, self.suggestion_service
        )

        self.assertFalse(restored)
        self.assertEqual(bot.calls, [])

    def test_returns_false_when_open_round_has_no_message_reference(self) -> None:
        self.vote_service.create_round(
            visibility=VoteVisibility.BLIND,
            candidate_suggestion_ids=[self.first.id, self.second.id],
        )
        bot = FakeBot()

        restored = restore_persistent_voting_view(
            bot, self.vote_service, self.suggestion_service
        )

        self.assertFalse(restored)
        self.assertEqual(bot.calls, [])

    def test_registers_view_for_stored_voting_message(self) -> None:
        result = self.vote_service.create_round(
            visibility=VoteVisibility.BLIND,
            candidate_suggestion_ids=[self.first.id, self.second.id],
        )
        self.vote_service.attach_message_reference(
            result.vote_round.id, guild_id=100, channel_id=200, message_id=300
        )
        bot = FakeBot()

        restored = restore_persistent_voting_view(
            bot, self.vote_service, self.suggestion_service
        )

        self.assertTrue(restored)
        self.assertEqual(len(bot.calls), 1)
        view, message_id = bot.calls[0]
        self.assertIsInstance(view, VotingView)
        self.assertEqual(message_id, 300)
        self.assertEqual(
            [button.suggestion_id for button in view.children],
            [self.first.id, self.second.id],
        )

    def test_returns_false_when_persisted_nominees_cannot_be_resolved(self) -> None:
        result = self.vote_service.create_round(
            visibility=VoteVisibility.BLIND,
            candidate_suggestion_ids=[self.first.id, self.second.id],
        )
        self.vote_service.attach_message_reference(
            result.vote_round.id, guild_id=100, channel_id=200, message_id=300
        )
        # Simulate stale persisted references after the source items disappear.
        self.suggestion_service._suggestions.clear()
        bot = FakeBot()

        restored = restore_persistent_voting_view(
            bot, self.vote_service, self.suggestion_service
        )

        self.assertFalse(restored)
        self.assertEqual(bot.calls, [])


if __name__ == "__main__":
    unittest.main()
