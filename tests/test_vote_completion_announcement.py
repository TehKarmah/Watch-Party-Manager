import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.vote import VoteRound, VoteRoundStatus, VoteVisibility
from watch_party_manager.domain.watch_item import MediaType, MetadataProvider, WatchItem
from watch_party_manager.services.vote_announcement_formatter import build_vote_completion_announcement
from watch_party_manager.services.vote_service import StandingsEntry


def make_watch_item(title: str, imdb_url: str | None = None) -> WatchItem:
    metadata_ids = {MetadataProvider.IMDB: imdb_url} if imdb_url else {}
    return WatchItem(title=title, media_type=MediaType.MOVIE, metadata_ids=metadata_ids)


class BuildVoteCompletionAnnouncementTests(unittest.TestCase):
    def _round(self, round_id=1, visibility=VoteVisibility.VISIBLE):
        return VoteRound(id=round_id, status=VoteRoundStatus.CLOSED, visibility=visibility)

    def test_announces_a_single_winner(self) -> None:
        text = build_vote_completion_announcement(self._round(), [make_watch_item("The Matrix")], [], 3)

        self.assertIn("Winner: The Matrix", text)

    def test_announces_a_tie_with_all_winning_titles(self) -> None:
        text = build_vote_completion_announcement(
            self._round(), [make_watch_item("The Matrix"), make_watch_item("Inception")], [], 2
        )

        self.assertIn("tie", text.lower())
        self.assertIn("The Matrix", text)
        self.assertIn("Inception", text)

    def test_announces_no_winner_when_no_votes_were_cast(self) -> None:
        text = build_vote_completion_announcement(self._round(), [], [], 0)

        self.assertIn("No votes were cast", text)
        self.assertNotIn("Winner:", text)

    def test_shows_total_votes_cast(self) -> None:
        text = build_vote_completion_announcement(self._round(), [make_watch_item("The Matrix")], [], 7)

        self.assertIn("Total votes cast: 7", text)

    def test_shows_standings_even_for_a_round_that_was_blind_while_open(self) -> None:
        # The round is closed by the time this is called, so blind voting's
        # "reveal only after voting has closed" rule is satisfied simply by
        # this function always showing standings -- there is no separate
        # branch needed for blind vs. visible here.
        standings = [StandingsEntry(suggestion_id=1, vote_count=2)]

        text = build_vote_completion_announcement(
            self._round(visibility=VoteVisibility.BLIND), [make_watch_item("The Matrix")], standings, 2
        )

        self.assertIn("Standings:", text)
        self.assertIn("Suggestion #1", text)

    def test_shows_standings_for_a_round_that_was_visible(self) -> None:
        standings = [StandingsEntry(suggestion_id=1, vote_count=2)]

        text = build_vote_completion_announcement(
            self._round(visibility=VoteVisibility.VISIBLE), [make_watch_item("The Matrix")], standings, 2
        )

        self.assertIn("Standings:", text)

    def test_mentions_round_id(self) -> None:
        text = build_vote_completion_announcement(
            self._round(round_id=42), [make_watch_item("The Matrix")], [], 1
        )

        self.assertIn("42", text)

    # --- IMDb link reuse ---------------------------------------------------------

    def test_single_winner_includes_imdb_link_when_available(self) -> None:
        text = build_vote_completion_announcement(
            self._round(),
            [make_watch_item("The Matrix", imdb_url="https://www.imdb.com/title/tt0133093/")],
            [],
            1,
        )

        self.assertIn("Winner: The Matrix ([View on IMDb](https://www.imdb.com/title/tt0133093/))", text)

    def test_single_winner_without_an_imdb_link_shows_only_the_title(self) -> None:
        text = build_vote_completion_announcement(self._round(), [make_watch_item("The Matrix")], [], 1)

        self.assertIn("Winner: The Matrix", text)
        self.assertNotIn("IMDb", text)

    def test_tie_includes_imdb_links_only_for_winners_that_have_one(self) -> None:
        text = build_vote_completion_announcement(
            self._round(),
            [
                make_watch_item("The Matrix", imdb_url="https://www.imdb.com/title/tt0133093/"),
                make_watch_item("Inception"),
            ],
            [],
            2,
        )

        self.assertIn("The Matrix ([View on IMDb](https://www.imdb.com/title/tt0133093/))", text)
        self.assertIn("Inception", text)
        self.assertNotIn("Inception ([View on IMDb]", text)


if __name__ == "__main__":
    unittest.main()
