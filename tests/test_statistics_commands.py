import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import build_statistics_text, format_count, perform_stats
from watch_party_manager.services.statistics_service import StatisticsSnapshot


class FakeStatisticsService:
    def __init__(self, snapshot: StatisticsSnapshot) -> None:
        self._snapshot = snapshot
        self.guild_ids = []

    def snapshot(self, guild_id=None) -> StatisticsSnapshot:
        self.guild_ids.append(guild_id)
        return self._snapshot


def make_snapshot(**overrides) -> StatisticsSnapshot:
    values = {
        "total_watch_items": 12,
        "total_suggestions": 10,
        "active_suggestions": 8,
        "watched_items": 2,
        "total_databases": 3,
        "active_databases": 2,
        "total_vote_rounds": 4,
        "open_vote_rounds": 1,
        "closed_vote_rounds": 3,
        "total_votes_cast": 18,
        "average_votes_per_round": 4.5,
    }
    values.update(overrides)
    return StatisticsSnapshot(**values)


class StatisticsCommandTests(unittest.TestCase):
    def test_build_statistics_text_displays_snapshot_values(self) -> None:
        text = build_statistics_text(make_snapshot())

        self.assertIn("**Watch Party Statistics**", text)
        self.assertIn("**Watch Items**", text)
        self.assertIn("Total: 12 watch items", text)
        self.assertIn("Active suggestions: 8 suggestions", text)
        self.assertIn("Watched: 2 watch items", text)
        self.assertIn("**Suggestion Databases**", text)
        self.assertIn("Total: 3 databases", text)
        self.assertIn("Active: 2 databases", text)
        self.assertIn("**Voting**", text)
        self.assertIn("Rounds: 4 rounds", text)
        self.assertIn("Open: 1 round", text)
        self.assertIn("Closed: 3 rounds", text)
        self.assertIn("Votes cast: 18 votes", text)
        self.assertIn("Average votes per round: 4.5", text)

    def test_build_statistics_text_formats_zero_average(self) -> None:
        text = build_statistics_text(
            make_snapshot(total_vote_rounds=0, total_votes_cast=0, average_votes_per_round=0.0)
        )
        self.assertIn("Average votes per round: 0.0", text)

    def test_format_count_handles_singular_and_plural(self) -> None:
        self.assertEqual(format_count(1, "vote"), "1 vote")
        self.assertEqual(format_count(2, "vote"), "2 votes")
        self.assertEqual(format_count(0, "category", "categories"), "0 categories")

    def test_statistics_text_uses_singular_wording(self) -> None:
        text = build_statistics_text(
            make_snapshot(
                total_watch_items=1,
                active_suggestions=1,
                watched_items=1,
                total_databases=1,
                active_databases=1,
                total_vote_rounds=1,
                open_vote_rounds=1,
                closed_vote_rounds=0,
                total_votes_cast=1,
                average_votes_per_round=1.0,
            )
        )
        self.assertIn("1 watch item", text)
        self.assertIn("1 suggestion", text)
        self.assertIn("1 database", text)
        self.assertIn("1 round", text)
        self.assertIn("1 vote", text)

    def test_perform_stats_scopes_snapshot_to_current_guild(self) -> None:
        service = FakeStatisticsService(make_snapshot())

        message = perform_stats(service, guild_id=123)

        self.assertIn("Watch Party Statistics", message)
        self.assertEqual(service.guild_ids, [123])

    def test_perform_stats_rejects_direct_messages(self) -> None:
        service = FakeStatisticsService(make_snapshot())

        message = perform_stats(service, guild_id=None)

        self.assertEqual(message, "This command can only be used in a Discord server.")
        self.assertEqual(service.guild_ids, [])


if __name__ == "__main__":
    unittest.main()
