import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import build_diagnostics_text, perform_diagnostics
from watch_party_manager.services.statistics_service import StatisticsSnapshot

WASH_CREW_ROLE_ID = 999


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, roles=()) -> None:
        self.roles = list(roles)


class FakeStatisticsService:
    def __init__(self, snapshot: StatisticsSnapshot) -> None:
        self._snapshot = snapshot
        self.guild_ids = []

    def snapshot(self, guild_id=None) -> StatisticsSnapshot:
        self.guild_ids.append(guild_id)
        return self._snapshot


def make_snapshot(open_vote_rounds: int = 1) -> StatisticsSnapshot:
    return StatisticsSnapshot(
        total_watch_items=12,
        total_suggestions=10,
        active_suggestions=8,
        watched_items=2,
        total_databases=3,
        active_databases=2,
        total_vote_rounds=4,
        open_vote_rounds=open_vote_rounds,
        closed_vote_rounds=3,
        total_votes_cast=18,
        average_votes_per_round=4.5,
    )


class DiagnosticsCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.started_at = datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc)
        self.now = self.started_at + timedelta(hours=2, minutes=3, seconds=4)

    def _crew_member(self) -> FakeMember:
        return FakeMember([FakeRole(WASH_CREW_ROLE_ID)])

    def _call(self, service, **overrides):
        values = {
            "statistics_service": service,
            "user": self._crew_member(),
            "wash_crew_role_id": WASH_CREW_ROLE_ID,
            "guild_id": 123,
            "version": "0.1.0",
            "python_version": "3.12.10",
            "discord_version": "2.6.0",
            "latency_ms": 42.6,
            "started_at": self.started_at,
            "now": self.now,
            "interactive_voting_restored": True,
        }
        values.update(overrides)
        return perform_diagnostics(**values)

    def test_build_diagnostics_text_displays_runtime_information(self) -> None:
        text = build_diagnostics_text(
            version="0.1.0",
            python_version="3.12.10",
            discord_version="2.6.0",
            latency_ms=42.6,
            started_at=self.started_at,
            now=self.now,
            snapshot=make_snapshot(),
            interactive_voting_restored=True,
        )

        self.assertIn("**WASH Diagnostics**", text)
        self.assertIn("WASH version: 0.1.0", text)
        self.assertIn("Python: 3.12.10", text)
        self.assertIn("discord.py: 2.6.0", text)
        self.assertIn("Gateway latency: 43 ms", text)
        self.assertIn("Uptime: 2h 3m 4s", text)
        self.assertIn("**Runtime**", text)
        self.assertIn("**Loaded Data**", text)
        self.assertIn("**Voting**", text)
        self.assertIn("Suggestion databases: 3 databases", text)
        self.assertIn("Watch items: 12 watch items", text)
        self.assertIn("Active suggestions: 8 suggestions", text)
        self.assertIn("Open voting round: Yes", text)
        self.assertIn("Interactive controls restored: Yes", text)

    def test_build_diagnostics_text_reports_no_open_round_or_restoration(self) -> None:
        text = build_diagnostics_text(
            version="0.1.0",
            python_version="3.12.10",
            discord_version="2.6.0",
            latency_ms=0,
            started_at=self.started_at,
            now=self.started_at,
            snapshot=make_snapshot(open_vote_rounds=0),
            interactive_voting_restored=False,
        )
        self.assertIn("Open voting round: No", text)
        self.assertIn("Interactive controls restored: No", text)

    def test_diagnostics_is_ephemeral_and_guild_scoped(self) -> None:
        service = FakeStatisticsService(make_snapshot())

        message, ephemeral = self._call(service)

        self.assertTrue(ephemeral)
        self.assertIn("WASH Diagnostics", message)
        self.assertEqual(service.guild_ids, [123])

    def test_diagnostics_fails_closed_when_role_is_unconfigured(self) -> None:
        service = FakeStatisticsService(make_snapshot())

        message, ephemeral = self._call(service, wash_crew_role_id=None)

        self.assertTrue(ephemeral)
        self.assertIn("have not been configured", message)
        self.assertEqual(service.guild_ids, [])

    def test_diagnostics_rejects_regular_members(self) -> None:
        service = FakeStatisticsService(make_snapshot())

        message, ephemeral = self._call(service, user=FakeMember([FakeRole(1)]))

        self.assertTrue(ephemeral)
        self.assertIn("WASH Crew role", message)
        self.assertEqual(service.guild_ids, [])

    def test_diagnostics_rejects_direct_messages(self) -> None:
        service = FakeStatisticsService(make_snapshot())

        message, ephemeral = self._call(service, guild_id=None)

        self.assertTrue(ephemeral)
        self.assertEqual(message, "This command can only be used in a Discord server.")
        self.assertEqual(service.guild_ids, [])


if __name__ == "__main__":
    unittest.main()
