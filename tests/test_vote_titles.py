"""Regression tests for Requirement 1 (Release Candidate Polish -- Collection
Identity & Final UX): voting is centered around the collection, not the
round number, across every vote-lifecycle surface. The round number
remains available as secondary information (see build_vote_round_line).

Covers resolve_vote_collection_name, format_vote_title, and
build_vote_round_line directly, plus each public message/embed builder
that consumes them (vote opened, reminder, closed, results/tie, cancelled,
and deadline-changed).
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import build_voting_post_embed
from watch_party_manager.domain.suggestion_database import SuggestionDatabase
from watch_party_manager.domain.vote import VoteRound
from watch_party_manager.domain.watch_item import MediaType, WatchItem
from watch_party_manager.services.vote_announcement_formatter import (
    build_closed_voting_post_text,
    build_vote_cancellation_notice,
    build_vote_completion_announcement,
    build_vote_deadline_change_notice,
    build_vote_round_line,
    format_vote_title,
    resolve_vote_collection_name,
)
from watch_party_manager.scheduler.vote_reminder_job_handler import build_vote_reminder_text


def make_watch_item(title: str, id: int = 1) -> WatchItem:
    return WatchItem(title=title, media_type=MediaType.MOVIE, id=id)


class _FakeDatabaseLookup:
    def __init__(self, databases: dict) -> None:
        self._databases = databases

    def get_database(self, database_id):
        return self._databases.get(database_id)


class ResolveVoteCollectionNameTests(unittest.TestCase):
    def test_returns_none_when_database_id_is_none(self) -> None:
        lookup = _FakeDatabaseLookup({})
        self.assertIsNone(resolve_vote_collection_name(lookup, None))

    def test_returns_none_when_the_collection_no_longer_exists(self) -> None:
        lookup = _FakeDatabaseLookup({})
        self.assertIsNone(resolve_vote_collection_name(lookup, 42))

    def test_returns_the_databases_name_when_found(self) -> None:
        database = SuggestionDatabase(database_id=1, name="Movie Suggestions", guild_id=100, channel_id=200)
        lookup = _FakeDatabaseLookup({1: database})
        self.assertEqual(resolve_vote_collection_name(lookup, 1), "Movie Suggestions")


class FormatVoteTitleTests(unittest.TestCase):
    def test_prefixes_the_suffix_with_the_collections_display_when_given(self) -> None:
        self.assertEqual(
            format_vote_title("Movie Suggestions", "Voting Is Open"),
            "🎬 Movie Suggestions Voting Is Open",
        )

    def test_custom_collection_has_no_emoji_prefix(self) -> None:
        self.assertEqual(
            format_vote_title("Book Club Adaptations", "Voting Is Open"),
            "Book Club Adaptations Voting Is Open",
        )

    def test_falls_back_to_the_bare_suffix_when_no_collection_is_given(self) -> None:
        self.assertEqual(format_vote_title(None, "Voting Is Open"), "Voting Is Open")

    def test_falls_back_to_the_bare_suffix_for_an_empty_collection_name(self) -> None:
        self.assertEqual(format_vote_title("", "Voting Is Open"), "Voting Is Open")


class BuildVoteRoundLineTests(unittest.TestCase):
    def test_shows_the_rounds_id(self) -> None:
        self.assertEqual(build_vote_round_line(VoteRound(id=7)), "Round: 7")


class VoteOpenedTitleTests(unittest.TestCase):
    def test_opened_embed_title_includes_the_collection(self) -> None:
        embed = build_voting_post_embed(VoteRound(id=1), [], standings=None, standings_error=None, collection_name="Movie Suggestions")

        self.assertEqual(embed.title, "🎬 Movie Suggestions Voting Is Open")
        fields = {field.name: field.value for field in embed.fields}
        self.assertEqual(fields.get("Round"), "1")

    def test_opened_embed_title_falls_back_without_a_collection(self) -> None:
        embed = build_voting_post_embed(VoteRound(id=1), [], standings=None, standings_error=None)

        self.assertEqual(embed.title, "Voting Is Open")


class VoteReminderTitleTests(unittest.TestCase):
    def test_reminder_title_includes_the_collection(self) -> None:
        vote_round = VoteRound(id=3)
        text = build_vote_reminder_text(vote_round, [], None, collection_name="TV Suggestions")

        lines = text.splitlines()
        self.assertEqual(lines[0], "**📺 TV Suggestions Voting — Reminder**")
        self.assertEqual(lines[1], "Round: 3")

    def test_reminder_title_falls_back_without_a_collection(self) -> None:
        text = build_vote_reminder_text(VoteRound(id=3), [], None)

        self.assertEqual(text.splitlines()[0], "**Voting — Reminder**")


class VoteClosedTitleTests(unittest.TestCase):
    def test_closed_post_title_includes_the_collection(self) -> None:
        text = build_closed_voting_post_text(VoteRound(id=5), [], [], [], 0, collection_name="Anime Watchlist")

        lines = text.splitlines()
        self.assertEqual(lines[0], "**🎌 Anime Watchlist Voting — Closed**")
        self.assertEqual(lines[1], "Round: 5")

    def test_closed_post_title_falls_back_without_a_collection(self) -> None:
        text = build_closed_voting_post_text(VoteRound(id=5), [], [], [], 0)

        self.assertEqual(text.splitlines()[0], "**Voting — Closed**")


class VoteResultsTitleTests(unittest.TestCase):
    def test_results_title_includes_the_collection(self) -> None:
        text = build_vote_completion_announcement(
            VoteRound(id=9), [], [], [], 0, collection_name="Holiday Movies"
        )

        lines = text.splitlines()
        # "Holiday Movies" contains both the "holiday" and "movie" keywords --
        # the first keyword to match (movies) wins, per collection_display's
        # documented "first keyword wins" behavior.
        self.assertEqual(lines[0], "**🎬 Holiday Movies Voting — Results**")
        self.assertEqual(lines[1], "Round: 9")

    def test_tie_announcement_still_shows_the_collection_centric_title(self) -> None:
        matrix = make_watch_item("The Matrix", id=1)
        inception = make_watch_item("Inception", id=2)
        text = build_vote_completion_announcement(
            VoteRound(id=1), [matrix, inception], [matrix, inception], [], 2, collection_name="Movies"
        )

        self.assertIn("**🎬 Movies Voting — Results**", text)
        self.assertIn("tie", text.lower())

    def test_results_title_falls_back_without_a_collection(self) -> None:
        text = build_vote_completion_announcement(VoteRound(id=9), [], [], [], 0)

        self.assertEqual(text.splitlines()[0], "**Voting — Results**")


class VoteCancellationTitleTests(unittest.TestCase):
    def test_cancellation_notice_includes_the_collection(self) -> None:
        text = build_vote_cancellation_notice(VoteRound(id=2), collection_name="Documentaries")

        lines = text.splitlines()
        self.assertEqual(lines[0], "**🎞️ Documentaries Voting — Cancelled**")
        self.assertEqual(lines[1], "Round: 2")

    def test_cancellation_notice_falls_back_without_a_collection(self) -> None:
        text = build_vote_cancellation_notice(VoteRound(id=2))

        self.assertEqual(text.splitlines()[0], "**Voting — Cancelled**")


class VoteDeadlineChangeTitleTests(unittest.TestCase):
    def test_deadline_change_notice_includes_the_collection(self) -> None:
        text = build_vote_deadline_change_notice(VoteRound(id=4), collection_name="Horror")

        lines = text.splitlines()
        self.assertEqual(lines[0], "**🎃 Horror Voting — Updated**")
        self.assertEqual(lines[1], "Round: 4")

    def test_deadline_change_notice_falls_back_without_a_collection(self) -> None:
        text = build_vote_deadline_change_notice(VoteRound(id=4))

        self.assertEqual(text.splitlines()[0], "**Voting — Updated**")


if __name__ == "__main__":
    unittest.main()
