"""Tests for read-only WASH statistics."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from watch_party_manager.domain.suggestion_database import SuggestionDatabase
from watch_party_manager.domain.vote import (
    VoteRecord,
    VoteRound,
    VoteRoundStatus,
    VoteVisibility,
)
from watch_party_manager.domain.watch_item import MediaType, WatchItem, WatchItemStatus
from watch_party_manager.domain.watch_item_journey import WatchItemJourney
from watch_party_manager.domain.watch_party import WatchParty, WatchPartyStatus
from watch_party_manager.persistence.rotation_repository import JsonRotationRepository
from watch_party_manager.persistence.vote_repository import VoteLoadResult
from watch_party_manager.services.rotation_service import RotationService
from watch_party_manager.services.statistics_service import (
    DatabaseStatistics,
    MemberStatistics,
    RotationStatistics,
    ServerStatistics,
    StatisticsService,
    StatisticsSnapshot,
    SuggestionStatistics,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


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

    def get_suggestions_for_database(self, database_id, *, include_archived=False):
        items = [item for item in self.items if item.database_id == database_id]
        if include_archived:
            return items
        return [item for item in items if item.status != WatchItemStatus.ARCHIVED]

    def get_suggestion(self, suggestion_id):
        for item in self.items:
            if item.id == suggestion_id:
                return item
        return None

    def get_database(self, database_id):
        for database in self.databases:
            if database.database_id == database_id:
                return database
        return None

    def record_rotation_presentation(self, suggestion_id, rotation_id):
        item = self.get_suggestion(suggestion_id)
        if item is None:
            return False
        item.journey.record_rotation_entry(rotation_id)
        return True


class FakeVoteSource:
    def __init__(self, rounds=None):
        self.rounds = list(rounds or [])
        self.load_calls = 0

    def load(self):
        self.load_calls += 1
        return VoteLoadResult(rounds=list(self.rounds), next_round_id=99)


class FakeWatchPartySource:
    def __init__(self, watch_parties=None):
        self.watch_parties = list(watch_parties or [])

    def list_watch_parties(self, guild_id=None):
        watch_parties = list(self.watch_parties)
        if guild_id is not None:
            watch_parties = [wp for wp in watch_parties if wp.guild_id == guild_id]
        return watch_parties


def make_database(database_id, guild_id=1, active=True):
    return SuggestionDatabase(
        database_id=database_id,
        name=f"Database {database_id}",
        guild_id=guild_id,
        channel_id=1000 + database_id,
        active=active,
    )


def make_item(
    item_id,
    database_id=1,
    guild_id=1,
    status=WatchItemStatus.SUGGESTED,
    title=None,
    journey=None,
):
    return WatchItem(
        id=item_id,
        title=title or f"Item {item_id}",
        media_type=MediaType.MOVIE,
        database_id=database_id,
        guild_id=guild_id,
        status=status,
        journey=journey if journey is not None else WatchItemJourney(),
    )


def make_watch_party(watch_party_id, watch_item_id=1, guild_id=1, status=WatchPartyStatus.SCHEDULED):
    return WatchParty(
        id=watch_party_id,
        watch_item_id=watch_item_id,
        scheduled_at=utc_now() + timedelta(days=1),
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


def make_round(
    round_id,
    *,
    guild_id=1,
    status=VoteRoundStatus.OPEN,
    voter_ids=(),
    visibility=VoteVisibility.VISIBLE,
    candidate_suggestion_ids=(),
    created_at=None,
    closes_at=None,
):
    votes = {user_id: make_vote(user_id) for user_id in voter_ids}
    return VoteRound(
        id=round_id,
        guild_id=guild_id,
        status=status,
        votes=votes,
        visibility=visibility,
        candidate_suggestion_ids=list(candidate_suggestion_ids),
        created_at=created_at if created_at is not None else utc_now(),
        closes_at=closes_at,
    )


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


# --- FR-034: Server statistics --------------------------------------------------------


class ServerStatisticsTests(unittest.TestCase):
    def make_service(self, items=None, databases=None, rounds=None, watch_parties=None):
        suggestion_source = FakeSuggestionSource(items, databases)
        vote_source = FakeVoteSource(rounds)
        watch_party_source = FakeWatchPartySource(watch_parties)
        return StatisticsService(suggestion_source, vote_source, watch_party_source=watch_party_source)

    def test_empty_server_statistics_are_all_zeroed(self):
        service = self.make_service()

        result = service.server_statistics()

        self.assertEqual(result.total_watch_parties, 0)
        self.assertEqual(result.total_vote_rounds, 0)
        self.assertEqual(result.total_votes_cast, 0)
        self.assertEqual(result.average_participation_per_round, 0.0)
        self.assertEqual(result.average_candidates_per_round, 0.0)
        self.assertEqual(result.tie_count, 0)
        self.assertIsNone(result.average_vote_duration_hours)
        self.assertIsNone(result.total_watch_party_members)
        self.assertIsNone(result.participation_percentage)

    def test_counts_watch_parties_by_status(self):
        service = self.make_service(
            watch_parties=[
                make_watch_party(1, status=WatchPartyStatus.SCHEDULED),
                make_watch_party(2, status=WatchPartyStatus.CANCELLED),
                make_watch_party(3, status=WatchPartyStatus.SCHEDULED),
            ]
        )

        result = service.server_statistics()

        self.assertEqual(result.total_watch_parties, 3)
        self.assertEqual(result.scheduled_watch_parties, 2)
        self.assertEqual(result.cancelled_watch_parties, 1)

    def test_counts_vote_rounds_by_status(self):
        service = self.make_service(
            rounds=[
                make_round(1, status=VoteRoundStatus.OPEN),
                make_round(2, status=VoteRoundStatus.CLOSED),
                make_round(3, status=VoteRoundStatus.CANCELLED),
            ]
        )

        result = service.server_statistics()

        self.assertEqual(result.total_vote_rounds, 3)
        self.assertEqual(result.open_vote_rounds, 1)
        self.assertEqual(result.closed_vote_rounds, 1)
        self.assertEqual(result.cancelled_vote_rounds, 1)

    def test_counts_blind_and_visible_rounds(self):
        service = self.make_service(
            rounds=[
                make_round(1, visibility=VoteVisibility.BLIND),
                make_round(2, visibility=VoteVisibility.VISIBLE),
                make_round(3, visibility=VoteVisibility.VISIBLE),
            ]
        )

        result = service.server_statistics()

        self.assertEqual(result.blind_vote_rounds, 1)
        self.assertEqual(result.visible_vote_rounds, 2)

    def test_average_candidates_per_round(self):
        service = self.make_service(
            rounds=[
                make_round(1, candidate_suggestion_ids=(1, 2, 3)),
                make_round(2, candidate_suggestion_ids=(1, 2)),
            ]
        )

        result = service.server_statistics()

        self.assertEqual(result.average_candidates_per_round, 2.5)

    def test_average_participation_per_round(self):
        service = self.make_service(
            rounds=[make_round(1, voter_ids=(1, 2)), make_round(2, voter_ids=(3,))]
        )

        result = service.server_statistics()

        self.assertEqual(result.average_participation_per_round, 1.5)

    def test_tie_count_detects_a_two_way_tie(self):
        votes = {
            1: make_vote(1, suggestion_id=10),
            2: make_vote(2, suggestion_id=20),
        }
        tied_round = VoteRound(id=1, status=VoteRoundStatus.CLOSED, votes=votes)
        service = self.make_service(rounds=[tied_round])

        result = service.server_statistics()

        self.assertEqual(result.tie_count, 1)

    def test_tie_count_excludes_a_clear_winner(self):
        votes = {
            1: make_vote(1, suggestion_id=10),
            2: make_vote(2, suggestion_id=10),
            3: make_vote(3, suggestion_id=20),
        }
        clear_round = VoteRound(id=1, status=VoteRoundStatus.CLOSED, votes=votes)
        service = self.make_service(rounds=[clear_round])

        result = service.server_statistics()

        self.assertEqual(result.tie_count, 0)

    def test_tie_count_ignores_open_rounds(self):
        votes = {1: make_vote(1, suggestion_id=10), 2: make_vote(2, suggestion_id=20)}
        open_tied_round = VoteRound(id=1, status=VoteRoundStatus.OPEN, votes=votes)
        service = self.make_service(rounds=[open_tied_round])

        result = service.server_statistics()

        self.assertEqual(result.tie_count, 0)

    def test_average_vote_duration_uses_closed_rounds_with_a_deadline(self):
        created = utc_now()
        closed_round = make_round(
            1, status=VoteRoundStatus.CLOSED, created_at=created, closes_at=created + timedelta(hours=48)
        )
        service = self.make_service(rounds=[closed_round])

        result = service.server_statistics()

        self.assertEqual(result.average_vote_duration_hours, 48.0)

    def test_average_vote_duration_is_none_without_any_qualifying_round(self):
        service = self.make_service(rounds=[make_round(1, status=VoteRoundStatus.OPEN)])

        result = service.server_statistics()

        self.assertIsNone(result.average_vote_duration_hours)

    def test_participation_percentage_uses_supplied_member_count(self):
        service = self.make_service(
            rounds=[make_round(1, voter_ids=(1, 2)), make_round(2, voter_ids=(2, 3))]
        )

        result = service.server_statistics(total_watch_party_members=6)

        # 3 unique voters (1, 2, 3) out of 6 members.
        self.assertEqual(result.total_watch_party_members, 6)
        self.assertEqual(result.participation_percentage, 50.0)

    def test_participation_percentage_is_none_without_a_member_count(self):
        service = self.make_service(rounds=[make_round(1, voter_ids=(1,))])

        result = service.server_statistics()

        self.assertIsNone(result.participation_percentage)

    def test_server_statistics_respects_guild_scope(self):
        service = self.make_service(
            watch_parties=[make_watch_party(1, guild_id=1), make_watch_party(2, guild_id=2)],
            rounds=[make_round(1, guild_id=1), make_round(2, guild_id=2)],
        )

        result = service.server_statistics(guild_id=1)

        self.assertEqual(result.total_watch_parties, 1)
        self.assertEqual(result.total_vote_rounds, 1)

    def test_server_statistics_without_watch_party_source_reports_zero(self):
        service = StatisticsService(FakeSuggestionSource(), FakeVoteSource())

        result = service.server_statistics()

        self.assertEqual(result.total_watch_parties, 0)


# --- FR-034: Suggestion statistics -----------------------------------------------------


class SuggestionStatisticsTests(unittest.TestCase):
    def make_service(self, items=None, databases=None, rounds=None):
        return StatisticsService(FakeSuggestionSource(items, databases), FakeVoteSource(rounds))

    def test_returns_none_for_an_unknown_suggestion(self):
        service = self.make_service()

        self.assertIsNone(service.suggestion_statistics(999))

    def test_reports_basic_fields(self):
        item = make_item(1, title="Alien", status=WatchItemStatus.SUGGESTED)
        service = self.make_service(items=[item])

        result = service.suggestion_statistics(1)

        self.assertEqual(result.suggestion_id, 1)
        self.assertEqual(result.title, "Alien")
        self.assertEqual(result.status, WatchItemStatus.SUGGESTED)

    def test_created_date_and_submitter_are_none_for_legacy_suggestions(self):
        item = make_item(1, journey=WatchItemJourney())
        service = self.make_service(items=[item])

        result = service.suggestion_statistics(1)

        self.assertIsNone(result.created_date)
        self.assertIsNone(result.submitter)

    def test_created_date_and_submitter_are_reported_when_known(self):
        created = date(2026, 1, 1)
        item = make_item(1, journey=WatchItemJourney(original_suggester="555", suggestion_date=created))
        service = self.make_service(items=[item])

        result = service.suggestion_statistics(1)

        self.assertEqual(result.created_date, created)
        self.assertEqual(result.submitter, "555")

    def test_nomination_count_scans_every_round_candidate_list(self):
        item = make_item(1)
        rounds = [
            make_round(1, candidate_suggestion_ids=(1, 2)),
            make_round(2, candidate_suggestion_ids=(2, 3)),
            make_round(3, candidate_suggestion_ids=(1,)),
        ]
        service = self.make_service(items=[item], rounds=rounds)

        result = service.suggestion_statistics(1)

        self.assertEqual(result.nomination_count, 2)

    def test_first_and_last_nomination_are_chronologically_ordered(self):
        item = make_item(1)
        early = utc_now() - timedelta(days=10)
        late = utc_now()
        rounds = [
            make_round(1, candidate_suggestion_ids=(1,), created_at=late),
            make_round(2, candidate_suggestion_ids=(1,), created_at=early),
        ]
        service = self.make_service(items=[item], rounds=rounds)

        result = service.suggestion_statistics(1)

        self.assertEqual(result.first_nomination_at, early)
        self.assertEqual(result.last_nomination_at, late)

    def test_never_nominated_suggestion_has_no_nomination_dates(self):
        item = make_item(1)
        service = self.make_service(items=[item], rounds=[make_round(1, candidate_suggestion_ids=(2,))])

        result = service.suggestion_statistics(1)

        self.assertEqual(result.nomination_count, 0)
        self.assertIsNone(result.first_nomination_at)
        self.assertIsNone(result.last_nomination_at)

    def test_days_until_first_nomination_requires_both_dates(self):
        created = date(2026, 1, 1)
        nominated_at = datetime(2026, 1, 5, tzinfo=timezone.utc)
        item = make_item(1, journey=WatchItemJourney(suggestion_date=created))
        service = self.make_service(
            items=[item], rounds=[make_round(1, candidate_suggestion_ids=(1,), created_at=nominated_at)]
        )

        result = service.suggestion_statistics(1)

        self.assertEqual(result.days_until_first_nomination, 4)

    def test_days_until_first_nomination_is_none_without_a_created_date(self):
        item = make_item(1, journey=WatchItemJourney())
        service = self.make_service(
            items=[item], rounds=[make_round(1, candidate_suggestion_ids=(1,))]
        )

        result = service.suggestion_statistics(1)

        self.assertIsNone(result.days_until_first_nomination)

    def test_watch_count_reflects_recorded_watch_dates(self):
        journey = WatchItemJourney(watch_dates=(date(2026, 1, 1), date(2026, 6, 1)))
        item = make_item(1, journey=journey)
        service = self.make_service(items=[item])

        result = service.suggestion_statistics(1)

        self.assertEqual(result.watch_count, 2)

    def test_days_until_watched_uses_the_earliest_watch_date(self):
        created = date(2026, 1, 1)
        journey = WatchItemJourney(
            suggestion_date=created, watch_dates=(date(2026, 2, 1), date(2026, 1, 15))
        )
        item = make_item(1, journey=journey)
        service = self.make_service(items=[item])

        result = service.suggestion_statistics(1)

        self.assertEqual(result.days_until_watched, 14)

    def test_days_until_watched_is_none_without_any_watch_dates(self):
        item = make_item(1, journey=WatchItemJourney(suggestion_date=date(2026, 1, 1)))
        service = self.make_service(items=[item])

        result = service.suggestion_statistics(1)

        self.assertIsNone(result.days_until_watched)

    def test_retirement_fields_reflect_the_journey(self):
        retired_at = utc_now()
        journey = WatchItemJourney(retired_at=retired_at, retirement_reason="rejection_threshold_reached")
        item = make_item(1, status=WatchItemStatus.ARCHIVED, journey=journey)
        service = self.make_service(items=[item])

        result = service.suggestion_statistics(1)

        self.assertTrue(result.is_retired)
        self.assertEqual(result.retired_at, retired_at)
        self.assertTrue(result.is_archived)

    def test_a_non_retired_archived_suggestion_is_archived_but_not_retired(self):
        item = make_item(1, status=WatchItemStatus.ARCHIVED, journey=WatchItemJourney())
        service = self.make_service(items=[item])

        result = service.suggestion_statistics(1)

        self.assertFalse(result.is_retired)
        self.assertTrue(result.is_archived)

    def test_rotations_participated_in_counts_rotation_history(self):
        journey = WatchItemJourney(rotation_history=(1, 2, 3))
        item = make_item(1, journey=journey)
        service = self.make_service(items=[item])

        result = service.suggestion_statistics(1)

        self.assertEqual(result.rotations_participated_in, 3)


# --- FR-034: Member statistics ----------------------------------------------------------


class MemberStatisticsTests(unittest.TestCase):
    def make_service(self, items=None, rounds=None):
        return StatisticsService(FakeSuggestionSource(items), FakeVoteSource(rounds))

    def test_a_member_with_no_history_gets_all_zeroed_statistics(self):
        service = self.make_service()

        result = service.member_statistics(guild_id=1, discord_user_id=555)

        self.assertEqual(result.suggestions_submitted, 0)
        self.assertEqual(result.suggestions_watched, 0)
        self.assertEqual(result.suggestions_retired, 0)
        self.assertEqual(result.winning_suggestions, 0)
        self.assertEqual(result.votes_cast, 0)
        self.assertEqual(result.participation_percentage, 0.0)
        self.assertFalse(result.has_submission_history)

    def test_counts_suggestions_submitted_by_this_member_only(self):
        mine = make_item(1, journey=WatchItemJourney(original_suggester="555"))
        someone_elses = make_item(2, journey=WatchItemJourney(original_suggester="777"))
        legacy = make_item(3, journey=WatchItemJourney())
        service = self.make_service(items=[mine, someone_elses, legacy])

        result = service.member_statistics(guild_id=1, discord_user_id=555)

        self.assertEqual(result.suggestions_submitted, 1)
        self.assertTrue(result.has_submission_history)

    def test_counts_watched_and_retired_suggestions_among_submitted(self):
        watched = make_item(
            1, status=WatchItemStatus.WATCHED, journey=WatchItemJourney(original_suggester="555")
        )
        retired = make_item(
            2,
            status=WatchItemStatus.ARCHIVED,
            journey=WatchItemJourney(original_suggester="555", retired_at=utc_now()),
        )
        active = make_item(3, journey=WatchItemJourney(original_suggester="555"))
        service = self.make_service(items=[watched, retired, active])

        result = service.member_statistics(guild_id=1, discord_user_id=555)

        self.assertEqual(result.suggestions_submitted, 3)
        self.assertEqual(result.suggestions_watched, 1)
        self.assertEqual(result.suggestions_retired, 1)

    def test_counts_winning_suggestions(self):
        journey = WatchItemJourney(original_suggester="555")
        journey.times_won = 1
        winner = make_item(1, journey=journey)
        non_winner = make_item(2, journey=WatchItemJourney(original_suggester="555"))
        service = self.make_service(items=[winner, non_winner])

        result = service.member_statistics(guild_id=1, discord_user_id=555)

        self.assertEqual(result.winning_suggestions, 1)

    def test_counts_votes_cast_across_rounds(self):
        rounds = [make_round(1, voter_ids=(555, 2)), make_round(2, voter_ids=(2,)), make_round(3, voter_ids=(555,))]
        service = self.make_service(rounds=rounds)

        result = service.member_statistics(guild_id=1, discord_user_id=555)

        self.assertEqual(result.votes_cast, 2)

    def test_participation_percentage_is_relative_to_all_rounds(self):
        rounds = [make_round(1, voter_ids=(555,)), make_round(2, voter_ids=()), make_round(3, voter_ids=())]
        service = self.make_service(rounds=rounds)

        result = service.member_statistics(guild_id=1, discord_user_id=555)

        self.assertAlmostEqual(result.participation_percentage, 33.33, places=1)

    def test_historical_member_no_longer_in_the_role_is_still_included(self):
        # Nothing here checks live Discord role membership -- a member's
        # statistics are keyed purely by discord_user_id/original_suggester,
        # so someone who has since left the Watch Party role is still
        # fully represented (FR-034 Section 7).
        journey = WatchItemJourney(original_suggester="555")
        item = make_item(1, journey=journey)
        rounds = [make_round(1, voter_ids=(555,))]
        service = self.make_service(items=[item], rounds=rounds)

        result = service.member_statistics(guild_id=1, discord_user_id=555)

        self.assertEqual(result.suggestions_submitted, 1)
        self.assertEqual(result.votes_cast, 1)


# --- FR-034: Rotation statistics --------------------------------------------------------


class RotationStatisticsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_source = FakeSuggestionSource()
        self.rotation_repository = JsonRotationRepository(root / "rotations.json")

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def make_service(self, items=None):
        self.suggestion_source.items = list(items or [])
        rotation_service = RotationService(self.suggestion_source, repository=self.rotation_repository)
        return StatisticsService(self.suggestion_source, rotation_service=rotation_service), rotation_service


class RotationStatisticsTests(RotationStatisticsTestCase):
    def test_returns_none_when_rotation_service_is_not_configured(self):
        service = StatisticsService(FakeSuggestionSource())

        self.assertIsNone(service.rotation_statistics(1))

    def test_a_database_with_no_rotation_yet_reports_gracefully(self):
        service, _ = self.make_service()

        result = service.rotation_statistics(1)

        self.assertIsNone(result.current_rotation_id)
        self.assertIsNone(result.current_progress)
        self.assertEqual(result.total_rotations, 0)
        self.assertEqual(result.completed_rotations, 0)
        self.assertIsNone(result.average_completed_rotation_duration_hours)
        self.assertIsNone(result.average_rotation_size)

    def test_does_not_bootstrap_a_rotation_as_a_side_effect(self):
        # Infinite Pool databases must never gain rotation state just
        # from being asked about (FR-033B's guarantee).
        service, rotation_service = self.make_service()

        service.rotation_statistics(1)

        self.assertIsNone(rotation_service.get_open_rotation(1))

    def test_reports_the_current_open_rotation(self):
        items = [make_item(1, database_id=1), make_item(2, database_id=1)]
        service, rotation_service = self.make_service(items=items)
        rotation = rotation_service.get_or_start_rotation(1)
        rotation_service.record_presentation(1, [1])

        result = service.rotation_statistics(1)

        self.assertEqual(result.current_rotation_id, rotation.id)
        self.assertEqual(result.current_progress.total, 2)
        self.assertEqual(result.current_progress.presented, 1)
        self.assertEqual(result.current_progress.remaining, 1)

    def test_reports_completed_rotation_history(self):
        items = [make_item(1, database_id=1)]
        service, rotation_service = self.make_service(items=items)
        rotation_service.get_or_start_rotation(1)
        rotation_service.record_presentation(1, [1])
        rotation_service.begin_next_rotation(1)

        result = service.rotation_statistics(1)

        self.assertEqual(result.total_rotations, 2)
        self.assertEqual(result.completed_rotations, 1)

    def test_average_rotation_size_across_all_rotations(self):
        items = [make_item(1, database_id=1), make_item(2, database_id=1)]
        service, rotation_service = self.make_service(items=items)
        rotation_service.get_or_start_rotation(1)

        result = service.rotation_statistics(1)

        self.assertEqual(result.average_rotation_size, 2.0)


# --- FR-034: Database statistics --------------------------------------------------------


class DatabaseStatisticsTests(RotationStatisticsTestCase):
    def make_full_service(self, items=None, databases=None):
        self.suggestion_source.items = list(items or [])
        self.suggestion_source.databases = list(databases or [])
        rotation_service = RotationService(self.suggestion_source, repository=self.rotation_repository)
        return StatisticsService(self.suggestion_source, rotation_service=rotation_service)

    def test_returns_none_for_an_unknown_database(self):
        service = self.make_full_service()

        self.assertIsNone(service.database_statistics(999))

    def test_counts_suggestions_by_status(self):
        items = [
            make_item(1, database_id=1, status=WatchItemStatus.SUGGESTED),
            make_item(2, database_id=1, status=WatchItemStatus.WATCHED),
            make_item(
                3,
                database_id=1,
                status=WatchItemStatus.ARCHIVED,
                journey=WatchItemJourney(retired_at=utc_now()),
            ),
            make_item(4, database_id=1, status=WatchItemStatus.ARCHIVED),
        ]
        service = self.make_full_service(items=items, databases=[make_database(1)])

        result = service.database_statistics(1)

        self.assertEqual(result.database_name, "Database 1")
        self.assertEqual(result.active_suggestions, 1)
        self.assertEqual(result.watched_suggestions, 1)
        self.assertEqual(result.archived_suggestions, 2)
        self.assertEqual(result.retired_suggestions, 1)

    def test_includes_rotation_statistics(self):
        items = [make_item(1, database_id=1)]
        service = self.make_full_service(items=items, databases=[make_database(1)])

        result = service.database_statistics(1)

        self.assertIsNotNone(result.rotation)
        self.assertEqual(result.rotation.database_id, 1)

    def test_an_empty_database_reports_gracefully(self):
        service = self.make_full_service(databases=[make_database(1)])

        result = service.database_statistics(1)

        self.assertEqual(result.active_suggestions, 0)
        self.assertEqual(result.archived_suggestions, 0)
        self.assertEqual(result.watched_suggestions, 0)
        self.assertEqual(result.retired_suggestions, 0)


if __name__ == "__main__":
    unittest.main()
