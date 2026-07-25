import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import restore_persistent_voting_views
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

    def test_returns_zero_when_no_round_is_open(self) -> None:
        bot = FakeBot()

        restored = restore_persistent_voting_views(
            bot, self.vote_service, self.suggestion_service
        )

        self.assertEqual(restored, 0)
        self.assertEqual(bot.calls, [])

    def test_skips_an_open_round_with_no_message_reference(self) -> None:
        self.vote_service.create_round(
            visibility=VoteVisibility.BLIND,
            candidate_suggestion_ids=[self.first.id, self.second.id],
        )
        bot = FakeBot()

        restored = restore_persistent_voting_views(
            bot, self.vote_service, self.suggestion_service
        )

        self.assertEqual(restored, 0)
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

        restored = restore_persistent_voting_views(
            bot, self.vote_service, self.suggestion_service
        )

        self.assertEqual(restored, 1)
        self.assertEqual(len(bot.calls), 1)
        view, message_id = bot.calls[0]
        self.assertIsInstance(view, VotingView)
        self.assertEqual(message_id, 300)
        self.assertEqual(
            [button.suggestion_id for button in view.children],
            [self.first.id, self.second.id],
        )

    def test_skips_a_round_whose_persisted_nominees_cannot_be_resolved(self) -> None:
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

        restored = restore_persistent_voting_views(
            bot, self.vote_service, self.suggestion_service
        )

        self.assertEqual(restored, 0)
        self.assertEqual(bot.calls, [])

    # --- Multiple concurrently-open rounds (one per collection) --------------------

    def test_restores_every_collections_open_round_independently(self) -> None:
        movies = self.suggestion_service.create_database(
            "Movies", guild_id=100, channel_id=201
        ).database
        tv_shows = self.suggestion_service.create_database(
            "TV Shows", guild_id=100, channel_id=202
        ).database
        third = self.suggestion_service.suggest("Breaking Bad", database_id=tv_shows.database_id).watch_item
        fourth = self.suggestion_service.suggest("The Wire", database_id=tv_shows.database_id).watch_item

        movies_round = self.vote_service.create_round(
            visibility=VoteVisibility.VISIBLE,
            candidate_suggestion_ids=[self.first.id, self.second.id],
            database_id=movies.database_id,
        ).vote_round
        tv_round = self.vote_service.create_round(
            visibility=VoteVisibility.VISIBLE,
            candidate_suggestion_ids=[third.id, fourth.id],
            database_id=tv_shows.database_id,
        ).vote_round
        self.vote_service.attach_message_reference(
            movies_round.id, guild_id=100, channel_id=201, message_id=301
        )
        self.vote_service.attach_message_reference(
            tv_round.id, guild_id=100, channel_id=202, message_id=302
        )
        bot = FakeBot()

        restored = restore_persistent_voting_views(
            bot, self.vote_service, self.suggestion_service
        )

        self.assertEqual(restored, 2)
        self.assertEqual(len(bot.calls), 2)
        restored_message_ids = {message_id for _, message_id in bot.calls}
        self.assertEqual(restored_message_ids, {301, 302})

    def test_three_collections_each_open_round_is_restored(self) -> None:
        collections = [
            self.suggestion_service.create_database(name, guild_id=100, channel_id=300 + index).database
            for index, name in enumerate(("Movies", "TV Shows", "Halloween"))
        ]
        bot = FakeBot()
        message_ids = []
        for index, database in enumerate(collections):
            item_a = self.suggestion_service.suggest(f"A{index}", database_id=database.database_id).watch_item
            item_b = self.suggestion_service.suggest(f"B{index}", database_id=database.database_id).watch_item
            vote_round = self.vote_service.create_round(
                visibility=VoteVisibility.VISIBLE,
                candidate_suggestion_ids=[item_a.id, item_b.id],
                database_id=database.database_id,
            ).vote_round
            message_id = 400 + index
            message_ids.append(message_id)
            self.vote_service.attach_message_reference(
                vote_round.id, guild_id=100, channel_id=300 + index, message_id=message_id
            )

        restored = restore_persistent_voting_views(
            bot, self.vote_service, self.suggestion_service
        )

        self.assertEqual(restored, 3)
        self.assertEqual({message_id for _, message_id in bot.calls}, set(message_ids))

    def test_one_unrestorable_round_does_not_block_the_others(self) -> None:
        movies = self.suggestion_service.create_database(
            "Movies", guild_id=100, channel_id=201
        ).database
        tv_shows = self.suggestion_service.create_database(
            "TV Shows", guild_id=100, channel_id=202
        ).database
        third = self.suggestion_service.suggest("Breaking Bad", database_id=tv_shows.database_id).watch_item
        fourth = self.suggestion_service.suggest("The Wire", database_id=tv_shows.database_id).watch_item

        # Movies' round never gets a message reference attached (simulates a
        # failure sending its original voting post).
        self.vote_service.create_round(
            visibility=VoteVisibility.VISIBLE,
            candidate_suggestion_ids=[self.first.id, self.second.id],
            database_id=movies.database_id,
        )
        tv_round = self.vote_service.create_round(
            visibility=VoteVisibility.VISIBLE,
            candidate_suggestion_ids=[third.id, fourth.id],
            database_id=tv_shows.database_id,
        ).vote_round
        self.vote_service.attach_message_reference(
            tv_round.id, guild_id=100, channel_id=202, message_id=302
        )
        bot = FakeBot()

        restored = restore_persistent_voting_views(
            bot, self.vote_service, self.suggestion_service
        )

        self.assertEqual(restored, 1)
        self.assertEqual(len(bot.calls), 1)
        self.assertEqual(bot.calls[0][1], 302)


if __name__ == "__main__":
    unittest.main()
