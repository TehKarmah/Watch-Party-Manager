"""Tests for FR-026: Vote Results Announcement Polish (formatting layer).

Covers vote_announcement_formatter.py's completed-round presentation:
the bug fix ("No votes were cast" must never contradict actual vote
totals), the single results announcement's text and embeds, the closed
original-post text, and the "every nominee, winners first" Final
Standings list. Does not cover the Discord I/O orchestration that sends
these -- see test_vote_completion_announcer.py for that.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.vote import VoteRound, VoteRoundStatus, VoteVisibility
from watch_party_manager.domain.watch_item import MediaType, WatchItem
from watch_party_manager.domain.watch_item_journey import WatchItemJourney
from watch_party_manager.services.vote_announcement_formatter import (
    build_closed_voting_post_text,
    build_final_standings_lines,
    build_suggestion_link,
    build_vote_cancellation_notice,
    build_vote_completion_announcement,
    build_vote_deadline_change_notice,
    build_vote_link,
    build_vote_results_embeds,
    build_winner_detail_embed,
)
from watch_party_manager.services.vote_service import StandingsEntry


def make_watch_item(
    title: str,
    *,
    id: int = 1,
    guild_id=None,
    channel_id=None,
    message_id=None,
    runtime_minutes=None,
    imdb_rating=None,
    genres=(),
    description=None,
    poster_url=None,
    original_suggester=None,
) -> WatchItem:
    return WatchItem(
        title=title,
        media_type=MediaType.MOVIE,
        id=id,
        guild_id=guild_id,
        channel_id=channel_id,
        message_id=message_id,
        runtime_minutes=runtime_minutes,
        imdb_rating=imdb_rating,
        genres=genres,
        description=description,
        poster_url=poster_url,
        journey=WatchItemJourney(original_suggester=original_suggester),
    )


def make_round(round_id=1, visibility=VoteVisibility.VISIBLE) -> VoteRound:
    return VoteRound(id=round_id, status=VoteRoundStatus.CLOSED, visibility=visibility)


class BuildSuggestionLinkTests(unittest.TestCase):
    """FR-024/FR-026: relocated here from bot.py so scheduler-reachable
    code can build suggestion links too.
    """

    def test_builds_a_link_when_all_metadata_is_present(self) -> None:
        watch_item = make_watch_item("Brazil", guild_id=100, channel_id=200, message_id=300)
        self.assertEqual(build_suggestion_link(watch_item), "https://discord.com/channels/100/200/300")

    def test_returns_none_when_metadata_is_missing(self) -> None:
        watch_item = make_watch_item("Brazil")
        self.assertIsNone(build_suggestion_link(watch_item))


class BuildFinalStandingsLinesTests(unittest.TestCase):
    """FR-026: Final Standings -- every nominee, winners first, deterministic ties."""

    def test_matches_the_documented_example_format(self) -> None:
        candidates = [make_watch_item("Brazil (1985)", id=1)]
        standings = [StandingsEntry(suggestion_id=1, vote_count=4)]

        lines = build_final_standings_lines(candidates, standings)

        self.assertEqual(lines, ["Brazil (1985) — 4 votes"])

    def test_uses_singular_vote_wording_for_one_vote(self) -> None:
        candidates = [make_watch_item("Rango (2011)", id=3)]
        standings = [StandingsEntry(suggestion_id=3, vote_count=1)]

        lines = build_final_standings_lines(candidates, standings)

        self.assertEqual(lines, ["Rango (2011) — 1 vote"])

    def test_shows_every_nominee_even_those_with_zero_votes(self) -> None:
        candidates = [
            make_watch_item("Brazil (1985)", id=1),
            make_watch_item("Big (1988)", id=2),
            make_watch_item("Rango (2011)", id=3),
        ]
        standings = [StandingsEntry(suggestion_id=1, vote_count=4)]

        lines = build_final_standings_lines(candidates, standings)

        self.assertEqual(len(lines), 3)
        self.assertIn("Big (1988) — 0 votes", lines[1])
        self.assertIn("Rango (2011) — 0 votes", lines[2])

    def test_winners_are_listed_before_remaining_nominees(self) -> None:
        candidates = [
            make_watch_item("Brazil (1985)", id=1),
            make_watch_item("Big (1988)", id=2),
            make_watch_item("Rango (2011)", id=3),
        ]
        # Suggestion 3 has the most votes -- it must appear first despite
        # candidate order placing it last.
        standings = [
            StandingsEntry(suggestion_id=3, vote_count=5),
            StandingsEntry(suggestion_id=1, vote_count=2),
        ]

        lines = build_final_standings_lines(candidates, standings)

        self.assertTrue(lines[0].startswith("Rango (2011)"))
        self.assertTrue(lines[1].startswith("Brazil (1985)"))
        self.assertTrue(lines[2].startswith("Big (1988)"))

    def test_tied_winners_are_both_listed_first_in_deterministic_order(self) -> None:
        candidates = [
            make_watch_item("Brazil (1985)", id=1),
            make_watch_item("Big (1988)", id=2),
            make_watch_item("Rango (2011)", id=3),
        ]
        # calculate_standings' own deterministic tie-break: same vote
        # count, ascending suggestion ID -- reused unchanged here.
        standings = [
            StandingsEntry(suggestion_id=1, vote_count=3),
            StandingsEntry(suggestion_id=2, vote_count=3),
        ]

        lines = build_final_standings_lines(candidates, standings)

        self.assertTrue(lines[0].startswith("Brazil (1985)"))
        self.assertTrue(lines[1].startswith("Big (1988)"))
        self.assertTrue(lines[2].startswith("Rango (2011)"))

    def test_titles_link_to_their_original_suggestion_when_available(self) -> None:
        candidates = [make_watch_item("Brazil (1985)", id=1, guild_id=100, channel_id=200, message_id=300)]
        standings = [StandingsEntry(suggestion_id=1, vote_count=1)]

        lines = build_final_standings_lines(candidates, standings)

        self.assertIn("[Brazil (1985)](https://discord.com/channels/100/200/300)", lines[0])

    def test_empty_when_there_are_no_candidates(self) -> None:
        self.assertEqual(build_final_standings_lines([], []), [])


class BuildVoteCompletionAnnouncementTests(unittest.TestCase):
    """FR-026: the single results announcement's text."""

    def _candidates(self):
        return [make_watch_item("The Matrix", id=1), make_watch_item("Inception", id=2)]

    # --- Bug fix: "No votes were cast" must match actual totals -------------------

    def test_zero_votes_says_no_votes_were_cast(self) -> None:
        text = build_vote_completion_announcement(make_round(), self._candidates(), [], [], 0)

        self.assertIn("No votes were cast", text)
        self.assertNotIn("Winner:", text)

    def test_never_says_no_votes_were_cast_when_votes_exist_but_winners_are_unresolvable(self) -> None:
        # Regression for FR-026's bug fix: winning_items can be empty even
        # when total_votes_cast > 0, e.g. the winning suggestion was
        # removed after the round closed. The message must never claim
        # no votes were cast in that case.
        text = build_vote_completion_announcement(make_round(), self._candidates(), [], [], 3)

        self.assertNotIn("No votes were cast", text)
        self.assertIn("Total votes cast: 3", text)

    def test_zero_votes_and_unresolvable_winners_are_distinguishable_messages(self) -> None:
        no_votes_text = build_vote_completion_announcement(make_round(), self._candidates(), [], [], 0)
        unresolvable_text = build_vote_completion_announcement(make_round(), self._candidates(), [], [], 3)

        self.assertNotEqual(no_votes_text.splitlines()[2], unresolvable_text.splitlines()[2])

    # --- Single winner / multiple winners ------------------------------------------

    def test_announces_a_single_winner(self) -> None:
        winner = make_watch_item("The Matrix", id=1)
        text = build_vote_completion_announcement(make_round(), self._candidates(), [winner], [], 3)

        self.assertIn("Winner: The Matrix", text)

    def test_announces_a_tie_with_all_winning_titles(self) -> None:
        winners = [make_watch_item("The Matrix", id=1), make_watch_item("Inception", id=2)]
        text = build_vote_completion_announcement(make_round(), self._candidates(), winners, [], 2)

        self.assertIn("tie", text.lower())
        self.assertIn("The Matrix", text)
        self.assertIn("Inception", text)

    def test_shows_total_votes_cast(self) -> None:
        winner = make_watch_item("The Matrix", id=1)
        text = build_vote_completion_announcement(make_round(), self._candidates(), [winner], [], 7)

        self.assertIn("Total votes cast: 7", text)

    def test_mentions_round_id(self) -> None:
        winner = make_watch_item("The Matrix", id=1)
        text = build_vote_completion_announcement(make_round(round_id=42), self._candidates(), [winner], [], 1)

        self.assertIn("42", text)

    # --- Final standings inclusion --------------------------------------------------

    def test_includes_final_standings_for_every_nominee(self) -> None:
        candidates = self._candidates()
        winner = candidates[0]
        standings = [StandingsEntry(suggestion_id=1, vote_count=2)]

        text = build_vote_completion_announcement(make_round(), candidates, [winner], standings, 2)

        self.assertIn("Final Standings:", text)
        self.assertIn("The Matrix", text)
        self.assertIn("Inception", text)
        self.assertIn("0 votes", text)  # Inception got none

    def test_shows_final_standings_even_for_a_round_that_was_blind_while_open(self) -> None:
        # The round is closed by the time this is called, so blind
        # voting's "reveal only after close" rule is satisfied simply by
        # this function always showing standings.
        candidates = self._candidates()
        winner = candidates[0]
        standings = [StandingsEntry(suggestion_id=1, vote_count=2)]

        text = build_vote_completion_announcement(
            make_round(visibility=VoteVisibility.BLIND), candidates, [winner], standings, 2
        )

        self.assertIn("Final Standings:", text)

    # --- Original vote link ----------------------------------------------------------

    def test_includes_the_original_vote_link_when_given(self) -> None:
        winner = make_watch_item("The Matrix", id=1)
        text = build_vote_completion_announcement(
            make_round(), self._candidates(), [winner], [], 1, "https://discord.com/channels/1/2/3"
        )

        self.assertIn("https://discord.com/channels/1/2/3", text)

    def test_omits_the_original_vote_link_when_not_given(self) -> None:
        winner = make_watch_item("The Matrix", id=1)
        text = build_vote_completion_announcement(make_round(), self._candidates(), [winner], [], 1)

        self.assertNotIn("discord.com", text)

    # --- No IMDb links ever -----------------------------------------------------------

    def test_never_links_directly_to_imdb(self) -> None:
        winner = make_watch_item("The Matrix", id=1, guild_id=100, channel_id=200, message_id=300)
        text = build_vote_completion_announcement(make_round(), self._candidates(), [winner], [], 1)

        self.assertNotIn("imdb.com", text)

    def test_links_the_winner_to_its_original_suggestion_when_available(self) -> None:
        winner = make_watch_item("The Matrix", id=1, guild_id=100, channel_id=200, message_id=300)
        text = build_vote_completion_announcement(make_round(), self._candidates(), [winner], [], 1)

        self.assertIn("[The Matrix](https://discord.com/channels/100/200/300)", text)

    # --- About the Winner header ---------------------------------------------------

    def test_includes_the_about_the_winner_header_when_there_is_a_winner(self) -> None:
        winner = make_watch_item("The Matrix", id=1)
        text = build_vote_completion_announcement(make_round(), self._candidates(), [winner], [], 1)

        self.assertIn("About the Winner", text)

    def test_omits_the_about_the_winner_header_when_there_is_no_winner(self) -> None:
        text = build_vote_completion_announcement(make_round(), self._candidates(), [], [], 0)

        self.assertNotIn("About the Winner", text)

    # --- Collection name --------------------------------------------------------------

    def test_includes_the_collection_name_when_given(self) -> None:
        winner = make_watch_item("The Matrix", id=1)
        text = build_vote_completion_announcement(
            make_round(), self._candidates(), [winner], [], 1, collection_name="Movie Suggestions"
        )

        self.assertIn("🎬 Movie Suggestions Voting — Results", text)

    def test_omits_the_collection_line_when_not_given(self) -> None:
        winner = make_watch_item("The Matrix", id=1)
        text = build_vote_completion_announcement(make_round(), self._candidates(), [winner], [], 1)

        self.assertNotIn("Collection:", text)

    def test_collection_line_appears_immediately_after_the_closed_header(self) -> None:
        winner = make_watch_item("The Matrix", id=1)
        text = build_vote_completion_announcement(
            make_round(), self._candidates(), [winner], [], 1, collection_name="Movie Suggestions"
        )

        lines = text.splitlines()
        self.assertEqual(lines[0], "**🎬 Movie Suggestions Voting — Results**")
        self.assertEqual(lines[1], "Round: 1")

    def test_guild_round_number_overrides_the_global_id_when_supplied(self) -> None:
        # First-Time UX Polish: a round with a high global id (e.g. the
        # 47th round ever created across every guild a WASH process
        # hosts) must still show its own guild's local round number.
        winner = make_watch_item("The Matrix", id=1)
        text = build_vote_completion_announcement(
            make_round(round_id=47), self._candidates(), [winner], [], 1, guild_round_number=3
        )

        self.assertIn("Round: 3", text)
        self.assertNotIn("Round: 47", text)

    # --- Custom Vote Summary & Announcement: active filters ------------------------------

    def test_includes_active_filters_when_the_round_tracked_them(self) -> None:
        from watch_party_manager.domain.suggestion_database_configuration import CandidateSelectionMode

        winner = make_watch_item("The Matrix", id=1)
        vote_round = VoteRound(
            id=1,
            status=VoteRoundStatus.CLOSED,
            candidate_selection_mode=CandidateSelectionMode.FAVOR_OLDER_ADDITIONS,
            filter_member_discord_user_id=555,
            filter_genre="Comedy",
        )
        text = build_vote_completion_announcement(vote_round, self._candidates(), [winner], [], 1)

        self.assertIn("Nominee Selection: Favor Older Additions", text)
        self.assertIn("Suggestion Source: <@555>", text)
        self.assertIn("Genre: Comedy", text)

    def test_omits_inactive_filters_and_untracked_mode(self) -> None:
        winner = make_watch_item("The Matrix", id=1)
        text = build_vote_completion_announcement(make_round(), self._candidates(), [winner], [], 1)

        self.assertNotIn("Nominee Selection:", text)
        self.assertNotIn("Suggestion Source:", text)
        self.assertNotIn("Genre:", text)

    # --- Suggested by -------------------------------------------------------------------

    def test_shows_who_suggested_the_winner(self) -> None:
        winner = make_watch_item("The Matrix", id=1, original_suggester="555")
        text = build_vote_completion_announcement(make_round(), self._candidates(), [winner], [], 1)

        self.assertIn("Suggested by: <@555>", text)

    def test_omits_suggested_by_when_there_is_no_winner(self) -> None:
        text = build_vote_completion_announcement(make_round(), self._candidates(), [], [], 0)

        self.assertNotIn("Suggested by", text)

    def test_omits_suggested_by_when_the_winner_has_no_recorded_suggester(self) -> None:
        winner = make_watch_item("The Matrix", id=1)
        text = build_vote_completion_announcement(make_round(), self._candidates(), [winner], [], 1)

        self.assertNotIn("Suggested by", text)

    def test_lists_each_distinct_suggester_for_a_tie(self) -> None:
        winners = [
            make_watch_item("The Matrix", id=1, original_suggester="111"),
            make_watch_item("Inception", id=2, original_suggester="222"),
        ]
        text = build_vote_completion_announcement(make_round(), self._candidates(), winners, [], 2)

        self.assertIn("Suggested by: <@111>, <@222>", text)

    def test_deduplicates_the_same_suggester_across_tied_winners(self) -> None:
        winners = [
            make_watch_item("The Matrix", id=1, original_suggester="111"),
            make_watch_item("Inception", id=2, original_suggester="111"),
        ]
        text = build_vote_completion_announcement(make_round(), self._candidates(), winners, [], 2)

        self.assertIn("Suggested by: <@111>", text)
        self.assertEqual(text.count("<@111>"), 1)

    # --- Section order --------------------------------------------------------------

    def test_sections_appear_in_the_documented_order(self) -> None:
        winner = make_watch_item("The Matrix", id=1, original_suggester="555")
        text = build_vote_completion_announcement(
            make_round(),
            self._candidates(),
            [winner],
            [],
            1,
            "https://discord.com/channels/1/2/3",
            "Movie Suggestions",
        )

        collection_index = text.index("Movie Suggestions")
        winner_index = text.index("Winner:")
        suggested_by_index = text.index("Suggested by:")
        original_post_index = text.index("Original voting post:")
        about_index = text.index("About the Winner")

        self.assertLess(collection_index, winner_index)
        self.assertLess(winner_index, suggested_by_index)
        self.assertLess(suggested_by_index, original_post_index)
        self.assertLess(original_post_index, about_index)


class BuildClosedVotingPostTextTests(unittest.TestCase):
    """FR-026: the original voting post's text once a round has closed."""

    def _candidates(self):
        return [make_watch_item("The Matrix", id=1), make_watch_item("Inception", id=2)]

    def test_indicates_voting_is_closed(self) -> None:
        winner = make_watch_item("The Matrix", id=1)
        text = build_closed_voting_post_text(make_round(), self._candidates(), [winner], [], 1)

        self.assertIn("Voting — Closed", text)

    def test_shows_the_winner(self) -> None:
        winner = make_watch_item("The Matrix", id=1)
        text = build_closed_voting_post_text(make_round(), self._candidates(), [winner], [], 1)

        self.assertIn("Winner: The Matrix", text)

    def test_shows_final_standings(self) -> None:
        candidates = self._candidates()
        winner = candidates[0]
        standings = [StandingsEntry(suggestion_id=1, vote_count=3)]

        text = build_closed_voting_post_text(make_round(), candidates, [winner], standings, 3)

        self.assertIn("Final Standings:", text)
        self.assertIn("3 votes", text)

    def test_never_says_no_votes_were_cast_when_winners_are_unresolvable(self) -> None:
        text = build_closed_voting_post_text(make_round(), self._candidates(), [], [], 2)

        self.assertNotIn("No votes were cast", text)

    def test_guild_round_number_overrides_the_global_id_when_supplied(self) -> None:
        winner = make_watch_item("The Matrix", id=1)
        text = build_closed_voting_post_text(
            make_round(round_id=47), self._candidates(), [winner], [], 1, guild_round_number=3
        )

        self.assertIn("Round: 3", text)
        self.assertNotIn("Round: 47", text)

    def test_omits_the_results_link_when_not_given(self) -> None:
        winner = make_watch_item("The Matrix", id=1)
        text = build_closed_voting_post_text(make_round(), self._candidates(), [winner], [], 1)

        self.assertNotIn("discord.com", text)

    def test_includes_the_results_link_when_given(self) -> None:
        winner = make_watch_item("The Matrix", id=1)
        text = build_closed_voting_post_text(
            make_round(), self._candidates(), [winner], [], 1, "https://discord.com/channels/1/2/999"
        )

        self.assertIn("https://discord.com/channels/1/2/999", text)

    def test_includes_active_filters_when_the_round_tracked_them(self) -> None:
        winner = make_watch_item("The Matrix", id=1)
        vote_round = VoteRound(id=1, status=VoteRoundStatus.CLOSED, filter_genre="Horror")
        text = build_closed_voting_post_text(vote_round, self._candidates(), [winner], [], 1)

        self.assertIn("Genre: Horror", text)


class BuildActiveFilterLinesTests(unittest.TestCase):
    """Custom Vote Summary & Announcement: the shared active-filter lines
    /vote_status, the voting-opened post, and the completion announcement
    all reuse (see bot.py's build_vote_status_text/build_voting_post_embed).
    """

    def test_empty_when_nothing_is_tracked(self) -> None:
        from watch_party_manager.services.vote_announcement_formatter import build_active_filter_lines

        self.assertEqual(build_active_filter_lines(make_round()), [])

    def test_shows_only_the_active_fields_omitting_any_member_and_any_genre(self) -> None:
        from watch_party_manager.services.vote_announcement_formatter import build_active_filter_lines

        vote_round = VoteRound(id=1, filter_member_discord_user_id=555)
        self.assertEqual(build_active_filter_lines(vote_round), ["Suggestion Source: <@555>"])

    def test_shows_nominee_selection_mode_when_tracked(self) -> None:
        from watch_party_manager.domain.suggestion_database_configuration import CandidateSelectionMode
        from watch_party_manager.services.vote_announcement_formatter import build_active_filter_lines

        vote_round = VoteRound(id=1, candidate_selection_mode=CandidateSelectionMode.INFINITE_POOL)
        self.assertEqual(build_active_filter_lines(vote_round), ["Nominee Selection: Pure Random"])

    def test_order_is_mode_then_genre_then_member(self) -> None:
        # Shared Filter Order (Section 2): Genre, IMDb Rating, MPAA
        # Rating, Actor, Member -- Member always last among the five
        # filters, with Nominee Selection (not one of the five) first.
        from watch_party_manager.domain.suggestion_database_configuration import CandidateSelectionMode
        from watch_party_manager.services.vote_announcement_formatter import build_active_filter_lines

        vote_round = VoteRound(
            id=1,
            candidate_selection_mode=CandidateSelectionMode.FAVOR_NEW_ADDITIONS,
            filter_member_discord_user_id=555,
            filter_genre="Comedy",
        )
        self.assertEqual(
            build_active_filter_lines(vote_round),
            ["Nominee Selection: Favor New Additions", "Genre: Comedy", "Suggestion Source: <@555>"],
        )

    def test_order_includes_all_five_filters_in_the_shared_order(self) -> None:
        from watch_party_manager.domain.suggestion_database_configuration import CandidateSelectionMode
        from watch_party_manager.services.vote_announcement_formatter import build_active_filter_lines

        vote_round = VoteRound(
            id=1,
            candidate_selection_mode=CandidateSelectionMode.FAVOR_NEW_ADDITIONS,
            filter_genre="Comedy",
            filter_imdb_rating_min=6.0,
            filter_imdb_rating_max=8.0,
            filter_mpaa_rating="PG-13",
            filter_actor="Jim Carrey",
            filter_member_discord_user_id=555,
        )
        self.assertEqual(
            build_active_filter_lines(vote_round),
            [
                "Nominee Selection: Favor New Additions",
                "Genre: Comedy",
                "IMDb Rating: 6.0–8.0",
                "MPAA Rating: PG-13",
                "Actor: Jim Carrey",
                "Suggestion Source: <@555>",
            ],
        )


class BuildWinnerDetailEmbedTests(unittest.TestCase):
    """FR-026: the "About the Winner" embed for one winning suggestion."""

    def test_title_is_the_watch_items_title(self) -> None:
        watch_item = make_watch_item("Brazil (1985)", id=1)
        embed = build_winner_detail_embed(watch_item, 4, show_thumbnail=True)

        self.assertEqual(embed.title, "Brazil (1985)")

    def test_description_is_the_summary(self) -> None:
        watch_item = make_watch_item("Brazil (1985)", id=1, description="A dystopian satire.")
        embed = build_winner_detail_embed(watch_item, 4, show_thumbnail=True)

        self.assertEqual(embed.description, "A dystopian satire.")

    def test_url_links_to_the_original_suggestion_not_imdb(self) -> None:
        watch_item = make_watch_item("Brazil (1985)", id=1, guild_id=100, channel_id=200, message_id=300)
        embed = build_winner_detail_embed(watch_item, 4, show_thumbnail=True)

        self.assertEqual(embed.url, "https://discord.com/channels/100/200/300")

    def test_url_is_none_for_a_legacy_suggestion(self) -> None:
        watch_item = make_watch_item("Brazil (1985)", id=1)
        embed = build_winner_detail_embed(watch_item, 4, show_thumbnail=True)

        self.assertIsNone(embed.url)

    def test_shows_runtime(self) -> None:
        watch_item = make_watch_item("Brazil (1985)", id=1, runtime_minutes=132)
        embed = build_winner_detail_embed(watch_item, 4, show_thumbnail=True)

        fields = {field.name: field.value for field in embed.fields}
        self.assertEqual(fields.get("Runtime"), "132 min")

    def test_shows_rating(self) -> None:
        watch_item = make_watch_item("Brazil (1985)", id=1, imdb_rating="8.0")
        embed = build_winner_detail_embed(watch_item, 4, show_thumbnail=True)

        fields = {field.name: field.value for field in embed.fields}
        self.assertEqual(fields.get("IMDb Rating"), "8.0/10")

    def test_shows_genres(self) -> None:
        watch_item = make_watch_item("Brazil (1985)", id=1, genres=("Comedy", "Sci-Fi"))
        embed = build_winner_detail_embed(watch_item, 4, show_thumbnail=True)

        fields = {field.name: field.value for field in embed.fields}
        self.assertEqual(fields.get("Genres"), "Comedy • Sci-Fi")

    def test_shows_the_vote_count(self) -> None:
        watch_item = make_watch_item("Brazil (1985)", id=1)
        embed = build_winner_detail_embed(watch_item, 4, show_thumbnail=True)

        fields = {field.name: field.value for field in embed.fields}
        self.assertEqual(fields.get("Votes"), "4 votes")

    def test_singular_vote_wording_for_one_vote(self) -> None:
        watch_item = make_watch_item("Brazil (1985)", id=1)
        embed = build_winner_detail_embed(watch_item, 1, show_thumbnail=True)

        fields = {field.name: field.value for field in embed.fields}
        self.assertEqual(fields.get("Votes"), "1 vote")

    def test_thumbnail_shown_when_requested_and_a_poster_exists(self) -> None:
        watch_item = make_watch_item("Brazil (1985)", id=1, poster_url="https://example.com/poster.jpg")
        embed = build_winner_detail_embed(watch_item, 4, show_thumbnail=True)

        self.assertEqual(embed.thumbnail.url, "https://example.com/poster.jpg")

    def test_thumbnail_omitted_when_not_requested(self) -> None:
        watch_item = make_watch_item("Brazil (1985)", id=1, poster_url="https://example.com/poster.jpg")
        embed = build_winner_detail_embed(watch_item, 4, show_thumbnail=False)

        self.assertIsNone(embed.thumbnail.url)

    def test_thumbnail_gracefully_omitted_when_no_poster_is_on_file(self) -> None:
        watch_item = make_watch_item("Brazil (1985)", id=1, poster_url=None)
        embed = build_winner_detail_embed(watch_item, 4, show_thumbnail=True)

        self.assertIsNone(embed.thumbnail.url)


class BuildVoteResultsEmbedsTests(unittest.TestCase):
    """FR-026: one embed per winner; thumbnails only for a single winner."""

    def test_empty_when_there_are_no_winning_items(self) -> None:
        self.assertEqual(build_vote_results_embeds([], []), [])

    def test_one_embed_for_a_single_winner(self) -> None:
        winner = make_watch_item("Brazil (1985)", id=1, poster_url="https://example.com/poster.jpg")
        standings = [StandingsEntry(suggestion_id=1, vote_count=4)]

        embeds = build_vote_results_embeds([winner], standings)

        self.assertEqual(len(embeds), 1)
        self.assertEqual(embeds[0].thumbnail.url, "https://example.com/poster.jpg")

    def test_no_thumbnail_for_any_winner_when_there_is_a_tie(self) -> None:
        winners = [
            make_watch_item("Brazil (1985)", id=1, poster_url="https://example.com/a.jpg"),
            make_watch_item("Big (1988)", id=2, poster_url="https://example.com/b.jpg"),
        ]
        standings = [
            StandingsEntry(suggestion_id=1, vote_count=3),
            StandingsEntry(suggestion_id=2, vote_count=3),
        ]

        embeds = build_vote_results_embeds(winners, standings)

        self.assertEqual(len(embeds), 2)
        for embed in embeds:
            self.assertIsNone(embed.thumbnail.url)

    def test_each_tied_winner_still_shows_its_own_vote_count(self) -> None:
        winners = [
            make_watch_item("Brazil (1985)", id=1),
            make_watch_item("Big (1988)", id=2),
        ]
        standings = [
            StandingsEntry(suggestion_id=1, vote_count=3),
            StandingsEntry(suggestion_id=2, vote_count=3),
        ]

        embeds = build_vote_results_embeds(winners, standings)

        for embed in embeds:
            fields = {field.name: field.value for field in embed.fields}
            self.assertEqual(fields.get("Votes"), "3 votes")


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
        self.assertIn("Voting — Updated", text)

    def test_includes_the_link_when_available(self) -> None:
        vote_round = VoteRound(id=1, guild_id=100, channel_id=200, message_id=300)

        text = build_vote_deadline_change_notice(vote_round)

        self.assertIn("https://discord.com/channels/100/200/300", text)

    def test_omits_the_link_when_unavailable(self) -> None:
        vote_round = VoteRound(id=1)

        text = build_vote_deadline_change_notice(vote_round)

        self.assertNotIn("discord.com", text)

    def test_guild_round_number_overrides_the_global_id_when_supplied(self) -> None:
        vote_round = VoteRound(id=47)

        text = build_vote_deadline_change_notice(vote_round, guild_round_number=3)

        self.assertIn("Round: 3", text)
        self.assertNotIn("Round: 47", text)


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

    def test_guild_round_number_overrides_the_global_id_when_supplied(self) -> None:
        vote_round = VoteRound(id=47, status=VoteRoundStatus.CANCELLED)

        text = build_vote_cancellation_notice(vote_round, guild_round_number=3)

        self.assertIn("Round: 3", text)
        self.assertNotIn("Round: 47", text)

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
