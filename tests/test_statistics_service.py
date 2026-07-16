"""Tests for read-only WASH statistics."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from watch_party_manager.domain.suggestion_database import SuggestionDatabase
from watch_party_manager.domain.vote import (
    VoteRecord,
    VoteRound,
    VoteRoundStatus,
)
from watch_party_manager.domain.watch_item import MediaType, WatchItem, WatchItemStatus
from watch_party_manager.persistence.vote_repository import VoteLoadResult
from watch_party_manager.services.statistics_service import (
    StatisticsService,
    StatisticsSnapshot,
)


class FakeSuggestionSource:
    def __init__(self, items=None, databases=None):
        self.items = list(items or [])
        self.databases = list(databases or [])

    def get_suggestions(self):
        return list(self.items)

    def list_databases(self, guild_id=None):
        databases = list(self.databases)
        if guild_id is not None:
            databases = [database for database in databases if database.guild_id == guild_id]
        return databases


class FakeVoteSource:
    def __init__(self, rounds=None):
        self.rounds = list(rounds or [])
        self.load_calls = 0

    def load(self):
        self.load_calls += 1
        return VoteLoadResult(rounds=list(self.rounds), next_round_id=99)


def make_database(database_id, guild_id=1, active=True):
    return SuggestionDatabase(
        database_id=database_id,
        name=f"Database {database_id}",
        guild_id=guild_id,
        channel_id=1000 + database_id,
        active=active,
    )


def make_item(item_id, database_id=1, guild_id=1, status=WatchItemStatus.SUGGESTED):
    return WatchItem(
        id=item_id,
        title=f"Item {item_id}",
        media_type=MediaType.MOVIE,
        database_id=database_id,
        guild_id=guild_id,
        status=status,
    )


def make_vote(user_id, suggestion_id=1):
    now = datetime.now(timezone.utc)
    return VoteRecord(
        discord_user_id=user_id,
        suggestion_id=suggestion_id,
        original_suggestion_id=suggestion_id,
        first_voted_at=now,
        last_voted_at=now,
    )


def make_round(round_id, *, guild_id=1, status=VoteRoundStatus.OPEN, voter_ids=()):
    votes = {user_id: make_vote(user_id) for user_id in voter_ids}
    return VoteRound(id=round_id, guild_id=guild_id, status=status, votes=votes)


class StatisticsServiceTests(unittest.TestCase):
    def make_service(self, items=None, databases=None, rounds=None):
        suggestion_source = FakeSuggestionSource(items, databases)
        vote_source = FakeVoteSource(rounds)
        return StatisticsService(suggestion_source, vote_source), vote_source

    def test_empty_snapshot_is_all_zeroes(self):
        service, _ = self.make_service()

        result = service.snapshot()

        self.assertEqual(
            StatisticsSnapshot(
                total_watch_items=0,
                total_suggestions=0,
                active_suggestions=0,
                watched_items=0,
                total_databases=0,
                active_databases=0,
                total_vote_rounds=0,
                open_vote_rounds=0,
                closed_vote_rounds=0,
                total_votes_cast=0,
                average_votes_per_round=0.0,
            ),
            result,
        )

    def test_snapshot_is_frozen(self):
        service, _ = self.make_service()
        result = service.snapshot()

        with self.assertRaises(AttributeError):
            result.total_watch_items = 1

    def test_counts_total_watch_items(self):
        service, _ = self.make_service(items=[make_item(1), make_item(2)])
        self.assertEqual(2, service.total_watch_items())

    def test_watched_items_are_not_counted_as_current_suggestions(self):
        service, _ = self.make_service(
            items=[
                make_item(1, status=WatchItemStatus.SUGGESTED),
                make_item(2, status=WatchItemStatus.WATCHED),
                make_item(3, status=WatchItemStatus.REWATCH_ELIGIBLE),
            ]
        )
        self.assertEqual(2, service.total_suggestions())
        self.assertEqual(1, service.watched_count())

    def test_all_non_watched_states_count_as_suggestions(self):
        statuses = [status for status in WatchItemStatus if status != WatchItemStatus.WATCHED]
        service, _ = self.make_service(
            items=[make_item(index + 1, status=status) for index, status in enumerate(statuses)]
        )
        self.assertEqual(len(statuses), service.total_suggestions())

    def test_active_suggestions_require_an_active_database(self):
        service, _ = self.make_service(
            items=[make_item(1, database_id=1), make_item(2, database_id=2)],
            databases=[make_database(1, active=True), make_database(2, active=False)],
        )
        self.assertEqual(1, service.active_suggestions_count())

    def test_watched_item_in_active_database_is_not_an_active_suggestion(self):
        service, _ = self.make_service(
            items=[make_item(1, status=WatchItemStatus.WATCHED)],
            databases=[make_database(1)],
        )
        self.assertEqual(0, service.active_suggestions_count())

    def test_orphaned_item_is_not_an_active_suggestion(self):
        service, _ = self.make_service(
            items=[make_item(1, database_id=None)],
            databases=[make_database(1)],
        )
        self.assertEqual(0, service.active_suggestions_count())

    def test_counts_all_and_active_databases(self):
        service, _ = self.make_service(
            databases=[make_database(1), make_database(2, active=False)]
        )
        self.assertEqual(2, service.database_count())
        self.assertEqual(1, service.active_database_count())

    def test_counts_open_and_closed_rounds(self):
        service, _ = self.make_service(
            rounds=[
                make_round(1, status=VoteRoundStatus.OPEN),
                make_round(2, status=VoteRoundStatus.CLOSED),
                make_round(3, status=VoteRoundStatus.CLOSED),
            ]
        )
        self.assertEqual(3, service.total_vote_rounds())
        self.assertEqual(1, service.open_vote_rounds())
        self.assertEqual(2, service.closed_vote_rounds())

    def test_total_votes_counts_current_vote_records(self):
        service, _ = self.make_service(
            rounds=[make_round(1, voter_ids=(1, 2)), make_round(2, voter_ids=(3,))]
        )
        self.assertEqual(3, service.total_votes_cast())

    def test_vote_changes_do_not_increase_total_votes(self):
        vote = make_vote(1)
        vote.changes_used = 1
        round_ = VoteRound(id=1, votes={1: vote})
        service, _ = self.make_service(rounds=[round_])
        self.assertEqual(1, service.total_votes_cast())

    def test_average_votes_per_round_includes_open_and_closed_rounds(self):
        service, _ = self.make_service(
            rounds=[
                make_round(1, status=VoteRoundStatus.OPEN, voter_ids=(1, 2)),
                make_round(2, status=VoteRoundStatus.CLOSED, voter_ids=(3,)),
            ]
        )
        self.assertEqual(1.5, service.average_votes_per_round())

    def test_average_votes_per_round_includes_empty_rounds(self):
        service, _ = self.make_service(
            rounds=[make_round(1, voter_ids=(1, 2)), make_round(2)]
        )
        self.assertEqual(1.0, service.average_votes_per_round())

    def test_snapshot_reads_vote_data_once(self):
        service, vote_source = self.make_service(rounds=[make_round(1)])
        service.snapshot()
        self.assertEqual(1, vote_source.load_calls)

    def test_each_public_metric_returns_fresh_data(self):
        service, vote_source = self.make_service(rounds=[])
        self.assertEqual(0, service.total_vote_rounds())
        vote_source.rounds.append(make_round(1))
        self.assertEqual(1, service.total_vote_rounds())

    def test_guild_scope_filters_watch_items_databases_and_rounds(self):
        service, _ = self.make_service(
            items=[make_item(1, guild_id=1), make_item(2, guild_id=2, database_id=2)],
            databases=[make_database(1, guild_id=1), make_database(2, guild_id=2)],
            rounds=[make_round(1, guild_id=1, voter_ids=(1,)), make_round(2, guild_id=2, voter_ids=(2, 3))],
        )
        result = service.snapshot(guild_id=1)
        self.assertEqual(1, result.total_watch_items)
        self.assertEqual(1, result.total_databases)
        self.assertEqual(1, result.total_vote_rounds)
        self.assertEqual(1, result.total_votes_cast)

    def test_guild_scope_excludes_legacy_records_without_guild_id(self):
        legacy_item = make_item(1)
        legacy_item.guild_id = None
        legacy_round = VoteRound(id=1)
        service, _ = self.make_service(
            items=[legacy_item, make_item(2, guild_id=1)],
            databases=[make_database(1, guild_id=1)],
            rounds=[legacy_round, make_round(2, guild_id=1)],
        )
        result = service.snapshot(guild_id=1)
        self.assertEqual(1, result.total_watch_items)
        self.assertEqual(1, result.total_vote_rounds)

    def test_unscoped_snapshot_includes_legacy_records(self):
        legacy_item = make_item(1)
        legacy_item.guild_id = None
        service, _ = self.make_service(items=[legacy_item], rounds=[VoteRound(id=1)])
        result = service.snapshot()
        self.assertEqual(1, result.total_watch_items)
        self.assertEqual(1, result.total_vote_rounds)


    def test_snapshot_accepts_sequence_based_sources(self):
        class TupleSuggestionSource:
            def get_suggestions(self):
                return (make_item(1), make_item(2, status=WatchItemStatus.WATCHED))

            def list_databases(self, guild_id=None):
                return (make_database(1),)

        class TupleVoteSource:
            def load(self):
                return VoteLoadResult(
                    rounds=(make_round(1, voter_ids=(1, 2)),),
                    next_round_id=2,
                )

        result = StatisticsService(TupleSuggestionSource(), TupleVoteSource()).snapshot()

        self.assertEqual(2, result.total_watch_items)
        self.assertEqual(1, result.total_suggestions)
        self.assertEqual(1, result.watched_items)
        self.assertEqual(2, result.total_votes_cast)

    def test_snapshot_does_not_mutate_source_collections(self):
        items = [make_item(1), make_item(2, status=WatchItemStatus.WATCHED)]
        databases = [make_database(1)]
        rounds = [make_round(1, voter_ids=(1,))]
        service, _ = self.make_service(items=items, databases=databases, rounds=rounds)

        service.snapshot()

        self.assertEqual([1, 2], [item.id for item in items])
        self.assertEqual([1], [database.database_id for database in databases])
        self.assertEqual([1], [vote_round.id for vote_round in rounds])

    def test_snapshot_returns_float_average(self):
        service, _ = self.make_service(rounds=[make_round(1, voter_ids=(1,))])
        self.assertIsInstance(service.snapshot().average_votes_per_round, float)


if __name__ == "__main__":
    unittest.main()
