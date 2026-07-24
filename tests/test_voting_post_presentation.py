"""Tests for FR-025: Voting Post Visual Polish.

Covers the new per-candidate presentation added to the voting post:
progress bars, vote counts/percentages, hyperlinks to a candidate's
original suggestion message, and blind-voting concealment. Does not
duplicate build_voting_post_text's pre-existing coverage in
test_interactive_voting.py (deadline display, visibility label, total
votes cast) -- only what FR-025 itself changed or added.
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import (
    build_candidate_standings_line,
    build_candidate_standings_lines,
    build_suggestion_link,
    build_vote_progress_bar,
    build_voting_post_embed,
)
from watch_party_manager.domain.vote import VoteRound, VoteVisibility
from watch_party_manager.domain.watch_item import MediaType, WatchItem
from watch_party_manager.services.vote_service import StandingsEntry


def make_watch_item(
    *,
    id: int,
    title: str,
    guild_id=None,
    channel_id=None,
    message_id=None,
) -> WatchItem:
    return WatchItem(
        title=title,
        media_type=MediaType.MOVIE,
        id=id,
        guild_id=guild_id,
        channel_id=channel_id,
        message_id=message_id,
    )


def make_vote_round(*, visibility=VoteVisibility.VISIBLE, vote_count: int = 0) -> VoteRound:
    return VoteRound(
        id=1,
        visibility=visibility,
        closes_at=datetime.now(timezone.utc) + timedelta(days=1),
        candidate_suggestion_ids=[1, 2, 3],
    )


class BuildVoteProgressBarTests(unittest.TestCase):
    def test_full_bar_at_one_hundred_percent(self) -> None:
        self.assertEqual(build_vote_progress_bar(10, 10), "██████████")

    def test_empty_bar_at_zero_percent(self) -> None:
        self.assertEqual(build_vote_progress_bar(0, 10), "░░░░░░░░░░")

    def test_sixty_percent_matches_the_documented_example(self) -> None:
        self.assertEqual(build_vote_progress_bar(6, 10), "██████░░░░")

    def test_bar_is_entirely_empty_when_no_votes_have_been_cast(self) -> None:
        self.assertEqual(build_vote_progress_bar(0, 0), "░░░░░░░░░░")

    def test_bar_length_is_always_ten_by_default(self) -> None:
        for vote_count, total in [(0, 0), (1, 1), (3, 7), (10, 10)]:
            with self.subTest(vote_count=vote_count, total=total):
                self.assertEqual(len(build_vote_progress_bar(vote_count, total)), 10)

    def test_supports_a_custom_length(self) -> None:
        self.assertEqual(build_vote_progress_bar(1, 2, length=4), "██░░")


class BuildCandidateStandingsLineTests(unittest.TestCase):
    def test_uses_singular_vote_wording_for_exactly_one_vote(self) -> None:
        line = build_candidate_standings_line(1, 4)
        self.assertIn("1 vote", line)
        self.assertNotIn("1 votes", line)

    def test_uses_plural_vote_wording_for_zero_votes(self) -> None:
        line = build_candidate_standings_line(0, 4)
        self.assertIn("0 votes", line)

    def test_uses_plural_vote_wording_for_multiple_votes(self) -> None:
        line = build_candidate_standings_line(6, 10)
        self.assertIn("6 votes", line)

    def test_includes_the_percentage(self) -> None:
        self.assertIn("60%", build_candidate_standings_line(6, 10))

    def test_zero_total_votes_shows_zero_percent_without_dividing_by_zero(self) -> None:
        line = build_candidate_standings_line(0, 0)
        self.assertIn("0%", line)

    def test_matches_the_documented_example_format(self) -> None:
        self.assertEqual(build_candidate_standings_line(6, 10), "██████░░░░ 6 votes • 60%")


class BuildSuggestionLinkTests(unittest.TestCase):
    def test_builds_a_link_when_all_metadata_is_present(self) -> None:
        watch_item = make_watch_item(id=1, title="Brazil", guild_id=100, channel_id=200, message_id=300)

        self.assertEqual(build_suggestion_link(watch_item), "https://discord.com/channels/100/200/300")

    def test_returns_none_when_guild_id_is_missing(self) -> None:
        watch_item = make_watch_item(id=1, title="Brazil", channel_id=200, message_id=300)
        self.assertIsNone(build_suggestion_link(watch_item))

    def test_returns_none_when_channel_id_is_missing(self) -> None:
        watch_item = make_watch_item(id=1, title="Brazil", guild_id=100, message_id=300)
        self.assertIsNone(build_suggestion_link(watch_item))

    def test_returns_none_when_message_id_is_missing(self) -> None:
        watch_item = make_watch_item(id=1, title="Brazil", guild_id=100, channel_id=200)
        self.assertIsNone(build_suggestion_link(watch_item))

    def test_returns_none_for_a_fully_legacy_suggestion(self) -> None:
        watch_item = make_watch_item(id=1, title="Brazil")
        self.assertIsNone(build_suggestion_link(watch_item))


class BuildCandidateStandingsLinesTests(unittest.TestCase):
    def _candidates(self, with_links: bool = False):
        if with_links:
            return [
                make_watch_item(id=1, title="Brazil (1985)", guild_id=100, channel_id=200, message_id=301),
                make_watch_item(id=2, title="Big (1988)", guild_id=100, channel_id=200, message_id=302),
                make_watch_item(id=3, title="Rango (2011)", guild_id=100, channel_id=200, message_id=303),
            ]
        return [
            make_watch_item(id=1, title="Brazil (1985)"),
            make_watch_item(id=2, title="Big (1988)"),
            make_watch_item(id=3, title="Rango (2011)"),
        ]

    def test_orders_candidates_in_button_order_not_vote_sorted_order(self) -> None:
        candidates = self._candidates()
        vote_round = make_vote_round(visibility=VoteVisibility.VISIBLE)
        # Suggestion #3 has the most votes but must still appear 3rd
        # (button/candidate order, never re-sorted by vote count).
        standings = [
            StandingsEntry(suggestion_id=3, vote_count=5),
            StandingsEntry(suggestion_id=1, vote_count=2),
        ]

        lines = build_candidate_standings_lines(candidates, vote_round, standings, None)
        text = "\n".join(lines)

        # Release Polish Batch 2, Priority 4: no leading nominee number.
        self.assertIn("Brazil (1985)", text)
        self.assertIn("Big (1988)", text)
        self.assertIn("Rango (2011)", text)
        self.assertNotIn("1. Brazil", text)
        self.assertNotIn("2. Big", text)
        self.assertNotIn("3. Rango", text)
        self.assertLess(text.index("Brazil"), text.index("Big"))
        self.assertLess(text.index("Big"), text.index("Rango"))

    def test_visible_round_shows_a_progress_bar_for_every_candidate(self) -> None:
        candidates = self._candidates()
        vote_round = make_vote_round(visibility=VoteVisibility.VISIBLE)
        vote_round.votes = dict.fromkeys(range(1, 3))
        standings = [StandingsEntry(suggestion_id=1, vote_count=2)]

        lines = build_candidate_standings_lines(candidates, vote_round, standings, None)
        text = "\n".join(lines)

        self.assertIn("█", text)
        self.assertIn("░", text)

    def test_candidate_with_no_votes_still_shows_zero(self) -> None:
        candidates = self._candidates()
        vote_round = make_vote_round(visibility=VoteVisibility.VISIBLE)
        vote_round.votes = dict.fromkeys(range(1, 3))
        standings = [StandingsEntry(suggestion_id=1, vote_count=2)]

        lines = build_candidate_standings_lines(candidates, vote_round, standings, None)
        text = "\n".join(lines)

        self.assertIn("0 votes", text)

    def test_blind_round_hides_bars_counts_and_percentages(self) -> None:
        candidates = self._candidates()
        vote_round = make_vote_round(visibility=VoteVisibility.BLIND)
        standings = [StandingsEntry(suggestion_id=1, vote_count=5)]

        lines = build_candidate_standings_lines(candidates, vote_round, standings, None)
        text = "\n".join(lines)

        self.assertNotIn("█", text)
        self.assertNotIn("░", text)
        self.assertNotIn("%", text)
        self.assertNotIn("vote", text.lower().replace("votes hidden", ""))

    def test_blind_round_shows_the_hidden_notice(self) -> None:
        candidates = self._candidates()
        vote_round = make_vote_round(visibility=VoteVisibility.BLIND)

        lines = build_candidate_standings_lines(candidates, vote_round, None, None)

        self.assertIn("Votes hidden until voting closes.", lines)

    def test_blind_round_never_reveals_standings_even_if_mistakenly_passed(self) -> None:
        # Defensive: even if a caller mistakenly passed real standings for
        # a blind round, they must never be rendered.
        candidates = self._candidates()
        vote_round = make_vote_round(visibility=VoteVisibility.BLIND)
        standings = [StandingsEntry(suggestion_id=1, vote_count=999)]

        lines = build_candidate_standings_lines(candidates, vote_round, standings, None)
        text = "\n".join(lines)

        self.assertNotIn("999", text)

    def test_hyperlinks_present_when_metadata_exists(self) -> None:
        candidates = self._candidates(with_links=True)
        vote_round = make_vote_round(visibility=VoteVisibility.VISIBLE)

        lines = build_candidate_standings_lines(candidates, vote_round, [], None)
        text = "\n".join(lines)

        self.assertIn("[Brazil (1985)](https://discord.com/channels/100/200/301)", text)
        self.assertIn("[Big (1988)](https://discord.com/channels/100/200/302)", text)
        self.assertIn("[Rango (2011)](https://discord.com/channels/100/200/303)", text)

    def test_hyperlinks_omitted_gracefully_for_legacy_suggestions(self) -> None:
        candidates = self._candidates(with_links=False)
        vote_round = make_vote_round(visibility=VoteVisibility.VISIBLE)

        lines = build_candidate_standings_lines(candidates, vote_round, [], None)
        text = "\n".join(lines)

        self.assertNotIn("discord.com", text)
        self.assertIn("Brazil (1985)", text)
        self.assertIn("Big (1988)", text)
        self.assertIn("Rango (2011)", text)

    def test_a_standings_failure_shows_a_message_instead_of_bars(self) -> None:
        candidates = self._candidates()
        vote_round = make_vote_round(visibility=VoteVisibility.VISIBLE)

        lines = build_candidate_standings_lines(candidates, vote_round, None, "Something went wrong.")
        text = "\n".join(lines)

        self.assertIn("Standings unavailable: Something went wrong.", text)
        self.assertNotIn("█", text)


class BuildVotingPostEmbedPresentationTests(unittest.TestCase):
    """FR-025 integration coverage on top of test_interactive_voting.py's existing tests."""

    def _candidates(self):
        return [
            make_watch_item(id=1, title="Brazil (1985)", guild_id=100, channel_id=200, message_id=301),
            make_watch_item(id=2, title="Big (1988)"),
        ]

    def test_candidate_titles_and_years_display_correctly(self) -> None:
        vote_round = make_vote_round(visibility=VoteVisibility.VISIBLE)

        embed = build_voting_post_embed(vote_round, self._candidates(), standings=[], standings_error=None)

        self.assertIn("Brazil (1985)", embed.description)
        self.assertIn("Big (1988)", embed.description)

    def test_progress_bars_and_counts_rendered_for_visible_round(self) -> None:
        vote_round = make_vote_round(visibility=VoteVisibility.VISIBLE)
        # Only the count matters for build_voting_post_embed's percentage
        # math (len(vote_round.votes)), so these are dummy entries giving
        # a 4-vote total.
        vote_round.votes = dict.fromkeys(range(1, 5))
        standings = [StandingsEntry(suggestion_id=1, vote_count=3)]

        embed = build_voting_post_embed(vote_round, self._candidates(), standings=standings, standings_error=None)

        self.assertIn("█", embed.description)
        self.assertIn("3 votes", embed.description)
        self.assertIn("75%", embed.description)  # 3 of the 4-vote total set above

    def test_blind_voting_hides_all_standings_information(self) -> None:
        vote_round = make_vote_round(visibility=VoteVisibility.BLIND)

        embed = build_voting_post_embed(vote_round, self._candidates(), standings=None, standings_error=None)

        self.assertIn("Votes hidden until voting closes.", embed.description)
        self.assertNotIn("%", embed.description)
        self.assertNotIn("█", embed.description)

    def test_hyperlink_present_for_a_candidate_with_message_metadata(self) -> None:
        vote_round = make_vote_round(visibility=VoteVisibility.VISIBLE)

        embed = build_voting_post_embed(vote_round, self._candidates(), standings=[], standings_error=None)

        self.assertIn("[Brazil (1985)](https://discord.com/channels/100/200/301)", embed.description)

    def test_hyperlink_omitted_for_a_legacy_candidate(self) -> None:
        vote_round = make_vote_round(visibility=VoteVisibility.VISIBLE)

        embed = build_voting_post_embed(vote_round, self._candidates(), standings=[], standings_error=None)

        self.assertIn("Big (1988)", embed.description)
        self.assertNotIn("[Big (1988)]", embed.description)


if __name__ == "__main__":
    unittest.main()
