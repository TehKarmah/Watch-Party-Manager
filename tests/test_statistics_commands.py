import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import build_statistics_text, perform_stats
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
        self.assertIn("Watch items: 12", text)
        self.assertIn("Active suggestions: 8", text)
        self.assertIn("Watched items: 2", text)
        self.assertIn("Suggestion databases: 3", text)
        self.assertIn("Active databases: 2", text)
        self.assertIn("Voting rounds: 4", text)
        self.assertIn("Open rounds: 1", text)
        self.assertIn("Closed rounds: 3", text)
        self.assertIn("Votes cast: 18", text)
        self.assertIn("Average votes per round: 4.5", text)

    def test_build_statistics_text_formats_zero_average(self) -> None:
        text = build_statistics_text(
            make_snapshot(total_vote_rounds=0, total_votes_cast=0, average_votes_per_round=0.0)
        )
        self.assertIn("Average votes per round: 0.0", text)

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
