import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.vote import VoteRound, VoteRoundStatus, VoteVisibility
from watch_party_manager.domain.watch_item import MediaType, MetadataProvider, WatchItem
from watch_party_manager.services.vote_announcement_formatter import (
    build_vote_cancellation_notice,
    build_vote_completion_announcement,
    build_vote_deadline_change_notice,
    build_vote_link,
)
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


class BuildVoteLinkTests(unittest.TestCase):
    """FR-023: build_vote_link's jump-link helper."""

    def test_builds_a_link_when_all_metadata_is_present(self) -> None:
        vote_round = VoteRound(id=1, guild_id=100, channel_id=200, message_id=300)

        link = build_vote_link(vote_round)

        self.assertEqual(link, "https://discord.com/channels/100/200/300")

    def test_returns_none_when_guild_id_is_missing(self) -> None:
        vote_round = VoteRound(id=1, channel_id=200, message_id=300)

        self.assertIsNone(build_vote_link(vote_round))

    def test_returns_none_when_channel_id_is_missing(self) -> None:
        vote_round = VoteRound(id=1, guild_id=100, message_id=300)

        self.assertIsNone(build_vote_link(vote_round))

    def test_returns_none_when_message_id_is_missing(self) -> None:
        vote_round = VoteRound(id=1, guild_id=100, channel_id=200)

        self.assertIsNone(build_vote_link(vote_round))


class BuildVoteDeadlineChangeNoticeTests(unittest.TestCase):
    """FR-023: the public notice posted when /edit_vote changes a deadline."""

    def test_mentions_the_round_id(self) -> None:
        vote_round = VoteRound(id=42)

        text = build_vote_deadline_change_notice(vote_round)

        self.assertIn("42", text)
        self.assertIn("deadline has changed", text)

    def test_includes_the_link_when_available(self) -> None:
        vote_round = VoteRound(id=1, guild_id=100, channel_id=200, message_id=300)

        text = build_vote_deadline_change_notice(vote_round)

        self.assertIn("https://discord.com/channels/100/200/300", text)

    def test_omits_the_link_when_unavailable(self) -> None:
        vote_round = VoteRound(id=1)

        text = build_vote_deadline_change_notice(vote_round)

        self.assertNotIn("discord.com", text)


class BuildVoteCancellationNoticeTests(unittest.TestCase):
    """FR-023: the public notice posted when /edit_vote cancels a round."""

    def test_mentions_the_round_id_and_cancellation(self) -> None:
        vote_round = VoteRound(id=42, status=VoteRoundStatus.CANCELLED)

        text = build_vote_cancellation_notice(vote_round)

        self.assertIn("42", text)
        self.assertIn("cancelled", text.lower())

    def test_never_mentions_a_winner(self) -> None:
        vote_round = VoteRound(id=1, status=VoteRoundStatus.CANCELLED)

        text = build_vote_cancellation_notice(vote_round)

        self.assertNotIn("Winner", text)

    def test_includes_the_link_when_available(self) -> None:
        vote_round = VoteRound(
            id=1, status=VoteRoundStatus.CANCELLED, guild_id=100, channel_id=200, message_id=300
        )

        text = build_vote_cancellation_notice(vote_round)

        self.assertIn("https://discord.com/channels/100/200/300", text)

    def test_omits_the_link_when_unavailable(self) -> None:
        vote_round = VoteRound(id=1, status=VoteRoundStatus.CANCELLED)

        text = build_vote_cancellation_notice(vote_round)

        self.assertNotIn("discord.com", text)


if __name__ == "__main__":
    unittest.main()
