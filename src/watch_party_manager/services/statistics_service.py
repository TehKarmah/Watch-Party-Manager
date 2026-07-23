"""Read-only statistics derived from existing WASH data.

FR-034: extends the pre-existing server-wide StatisticsSnapshot/snapshot()
(kept unchanged -- /diagnostics depends on its exact shape) with four
additional, independently callable statistic types: server (a richer
voting/watch-party view alongside the original snapshot), suggestion,
member, rotation, and database. Every method here recomputes its result
from the underlying repositories/services on each call -- nothing is
cached or incrementally maintained, per this milestone's explicit "no
running counters" constraint.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from statistics import mean
from typing import Optional, Protocol, Sequence

from watch_party_manager.domain.rotation import Rotation, RotationStatus
from watch_party_manager.domain.suggestion_database import SuggestionDatabase
from watch_party_manager.domain.vote import VoteRound, VoteRoundStatus, VoteVisibility
from watch_party_manager.domain.watch_item import WatchItem, WatchItemStatus
from watch_party_manager.domain.watch_party import WatchParty, WatchPartyStatus
from watch_party_manager.persistence.vote_repository import JsonVoteRepository, VoteLoadResult
from watch_party_manager.services.rotation_service import RotationProgress, RotationService


class SuggestionStatisticsSource(Protocol):
    """Suggestion data required by :class:`StatisticsService`."""

    def get_suggestions(self) -> Sequence[WatchItem]: ...

    def list_databases(
        self, guild_id: int | None = None
    ) -> Sequence[SuggestionDatabase]: ...

    def get_suggestions_for_database(
        self, database_id: int, *, include_archived: bool = False
    ) -> Sequence[WatchItem]: ...

    def get_suggestion(self, suggestion_id: int) -> Optional[WatchItem]: ...

    def get_database(self, database_id: int) -> Optional[SuggestionDatabase]: ...


class VoteStatisticsSource(Protocol):
    """Voting data required by :class:`StatisticsService`."""

    def load(self) -> VoteLoadResult: ...


class WatchPartyStatisticsSource(Protocol):
    """Watch-party data required by :class:`StatisticsService`."""

    def list_watch_parties(self, guild_id: int | None = None) -> Sequence[WatchParty]: ...


@dataclass(frozen=True, slots=True)
class StatisticsSnapshot:
    """A point-in-time summary of WASH activity."""

    total_watch_items: int
    total_suggestions: int
    active_suggestions: int
    watched_items: int
    total_databases: int
    active_databases: int
    total_vote_rounds: int
    open_vote_rounds: int
    closed_vote_rounds: int
    total_votes_cast: int
    average_votes_per_round: float


@dataclass(frozen=True, slots=True)
class ServerStatistics:
    """FR-034 Section 5: a richer, guild-scoped activity summary.

    Deliberately separate from StatisticsSnapshot (which /diagnostics
    depends on unchanged) rather than extending it -- this is the /stats
    "server" type's dedicated shape. total_watch_party_members/
    participation_percentage are None whenever the caller doesn't supply
    a live member count (StatisticsService has no Discord connection of
    its own); average_vote_duration_hours is None when no closed round
    has both created_at and closes_at to measure.
    """

    total_watch_parties: int
    scheduled_watch_parties: int
    cancelled_watch_parties: int
    total_vote_rounds: int
    open_vote_rounds: int
    closed_vote_rounds: int
    cancelled_vote_rounds: int
    total_votes_cast: int
    average_participation_per_round: float
    total_watch_party_members: Optional[int]
    participation_percentage: Optional[float]
    average_candidates_per_round: float
    blind_vote_rounds: int
    visible_vote_rounds: int
    tie_count: int
    average_vote_duration_hours: Optional[float]


@dataclass(frozen=True, slots=True)
class MemberStatistics:
    """FR-034 Section 7: one member's own historical activity.

    Only ever computed for the requesting member themselves (see
    Section 4's privacy requirements) -- nothing here is fit for
    exposing about another member. suggestions_submitted/watched/
    retired/winning_suggestions only ever count suggestions created
    after FR-034 (the first milestone to record a submitter) -- see
    has_submission_history.
    """

    discord_user_id: int
    suggestions_submitted: int
    suggestions_watched: int
    suggestions_retired: int
    winning_suggestions: int
    votes_cast: int
    participation_percentage: float
    has_submission_history: bool


@dataclass(frozen=True, slots=True)
class SuggestionStatistics:
    """FR-034 Section 6: one suggestion's historical activity.

    created_date/submitter are None for a suggestion created before
    FR-034 began recording them (see SuggestionService.suggest). Nomination
    count/first/last nomination are derived directly from historical vote
    rounds' candidate_suggestion_ids rather than from
    journey.voting_appearances/last_nominated_date, since those journey
    fields are only ever updated for a round's eventual winner, not every
    candidate -- scanning vote rounds directly is the only fully accurate
    historical source for "was this suggestion ever nominated."
    """

    suggestion_id: int
    title: str
    status: WatchItemStatus
    created_date: Optional[date]
    submitter: Optional[str]
    nomination_count: int
    first_nomination_at: Optional[datetime]
    last_nomination_at: Optional[datetime]
    watch_count: int
    is_retired: bool
    retired_at: Optional[datetime]
    is_archived: bool
    rotations_participated_in: int
    days_until_first_nomination: Optional[int]
    days_until_watched: Optional[int]


@dataclass(frozen=True, slots=True)
class RotationStatistics:
    """FR-034 Section 8: one database's rotation activity.

    current_progress mirrors RotationService.RotationProgress exactly
    (reused, not duplicated) and is None only when no rotation has ever
    been started for this database (e.g. an Infinite Pool database, or
    one that's never had a vote). average_* fields are None when there's
    no rotation history to average at all.
    """

    database_id: int
    current_rotation_id: Optional[int]
    current_rotation_started_at: Optional[datetime]
    current_progress: Optional[RotationProgress]
    total_rotations: int
    completed_rotations: int
    average_completed_rotation_duration_hours: Optional[float]
    average_rotation_size: Optional[float]


@dataclass(frozen=True, slots=True)
class DatabaseStatistics:
    """FR-034 Section 9: one database's suggestion + rotation summary."""

    database_id: int
    database_name: str
    active_suggestions: int
    archived_suggestions: int
    watched_suggestions: int
    retired_suggestions: int
    rotation: Optional[RotationStatistics]


class StatisticsService:
    """Calculate read-only project statistics from existing services and data.

    The service intentionally owns no persistence and changes no domain state.
    A new snapshot is calculated for every call so callers always receive the
    latest available data.
    """

    def __init__(
        self,
        suggestion_source: SuggestionStatisticsSource,
        vote_source: VoteStatisticsSource | None = None,
        rotation_service: RotationService | None = None,
        watch_party_source: WatchPartyStatisticsSource | None = None,
    ) -> None:
        """Initialize the service.

        rotation_service/watch_party_source are optional, matching this
        project's established "gracefully degrade when a dependency
        isn't configured" pattern (see NomineeSelectionService's
        optional strategy, VoteService's optional reminder settings):
        rotation-dependent statistics report None/empty rather than
        raising when rotation_service is omitted, and watch-party counts
        report zero when watch_party_source is omitted.
        """
        self._suggestion_source = suggestion_source
        self._vote_source = vote_source if vote_source is not None else JsonVoteRepository()
        self._rotation_service = rotation_service
        self._watch_party_source = watch_party_source

    def snapshot(self, guild_id: int | None = None) -> StatisticsSnapshot:
        """Return a complete statistics snapshot.

        Args:
            guild_id: Optional Discord guild scope. When supplied, suggestion
                databases and watch items are limited to that guild, and vote
                rounds are limited to rounds persisted with that guild ID.
                Legacy records without a guild ID are excluded from a scoped
                snapshot.
        """
        watch_items = self._watch_items(guild_id)
        databases = self._databases(guild_id)
        vote_rounds = self._vote_rounds(guild_id)

        active_database_ids = frozenset(
            database.database_id for database in databases if database.active
        )
        watched_items, total_suggestions, active_suggestions = self._count_watch_items(
            watch_items, active_database_ids
        )
        open_rounds, closed_rounds, total_votes = self._count_vote_rounds(vote_rounds)
        average_votes = total_votes / len(vote_rounds) if vote_rounds else 0.0

        return StatisticsSnapshot(
            total_watch_items=len(watch_items),
            total_suggestions=total_suggestions,
            active_suggestions=active_suggestions,
            watched_items=watched_items,
            total_databases=len(databases),
            active_databases=sum(1 for database in databases if database.active),
            total_vote_rounds=len(vote_rounds),
            open_vote_rounds=open_rounds,
            closed_vote_rounds=closed_rounds,
            total_votes_cast=total_votes,
            average_votes_per_round=average_votes,
        )

    def total_watch_items(self, guild_id: int | None = None) -> int:
        return self.snapshot(guild_id).total_watch_items

    def total_suggestions(self, guild_id: int | None = None) -> int:
        return self.snapshot(guild_id).total_suggestions

    def active_suggestions_count(self, guild_id: int | None = None) -> int:
        return self.snapshot(guild_id).active_suggestions

    def watched_count(self, guild_id: int | None = None) -> int:
        return self.snapshot(guild_id).watched_items

    def database_count(self, guild_id: int | None = None) -> int:
        return self.snapshot(guild_id).total_databases

    def active_database_count(self, guild_id: int | None = None) -> int:
        return self.snapshot(guild_id).active_databases

    def total_vote_rounds(self, guild_id: int | None = None) -> int:
        return self.snapshot(guild_id).total_vote_rounds

    def open_vote_rounds(self, guild_id: int | None = None) -> int:
        return self.snapshot(guild_id).open_vote_rounds

    def closed_vote_rounds(self, guild_id: int | None = None) -> int:
        return self.snapshot(guild_id).closed_vote_rounds

    def total_votes_cast(self, guild_id: int | None = None) -> int:
        return self.snapshot(guild_id).total_votes_cast

    def average_votes_per_round(self, guild_id: int | None = None) -> float:
        return self.snapshot(guild_id).average_votes_per_round

    # --- FR-034: Server statistics ---------------------------------------------------

    def server_statistics(
        self, guild_id: int | None = None, *, total_watch_party_members: Optional[int] = None
    ) -> ServerStatistics:
        """Return FR-034 Section 5's richer, guild-scoped activity summary.

        Args:
            guild_id: Optional Discord guild scope, matching snapshot().
            total_watch_party_members: The guild's current Watch Party
                member count, when the caller can supply it (a live
                Discord role lookup -- this service has no Discord
                connection of its own). Drives participation_percentage;
                omit to leave it (and the member count) unavailable
                rather than guessed at.
        """
        vote_rounds = self._vote_rounds(guild_id)
        watch_parties = self._watch_parties(guild_id)

        closed_rounds = [round_ for round_ in vote_rounds if round_.status == VoteRoundStatus.CLOSED]
        total_votes = sum(len(round_.votes) for round_ in vote_rounds)
        unique_voters: set[int] = set()
        for round_ in vote_rounds:
            unique_voters.update(round_.votes.keys())
        average_participation = total_votes / len(vote_rounds) if vote_rounds else 0.0
        average_candidates = (
            sum(len(round_.candidate_suggestion_ids) for round_ in vote_rounds) / len(vote_rounds)
            if vote_rounds
            else 0.0
        )
        participation_percentage = (
            len(unique_voters) / total_watch_party_members * 100
            if total_watch_party_members
            else None
        )

        durations_hours = [
            (round_.closes_at - round_.created_at).total_seconds() / 3600
            for round_ in closed_rounds
            if round_.closes_at is not None
        ]

        return ServerStatistics(
            total_watch_parties=len(watch_parties),
            scheduled_watch_parties=sum(
                1 for watch_party in watch_parties if watch_party.status == WatchPartyStatus.SCHEDULED
            ),
            cancelled_watch_parties=sum(
                1 for watch_party in watch_parties if watch_party.status == WatchPartyStatus.CANCELLED
            ),
            total_vote_rounds=len(vote_rounds),
            open_vote_rounds=sum(1 for round_ in vote_rounds if round_.status == VoteRoundStatus.OPEN),
            closed_vote_rounds=len(closed_rounds),
            cancelled_vote_rounds=sum(
                1 for round_ in vote_rounds if round_.status == VoteRoundStatus.CANCELLED
            ),
            total_votes_cast=total_votes,
            average_participation_per_round=average_participation,
            total_watch_party_members=total_watch_party_members,
            participation_percentage=participation_percentage,
            average_candidates_per_round=average_candidates,
            blind_vote_rounds=sum(1 for round_ in vote_rounds if round_.visibility == VoteVisibility.BLIND),
            visible_vote_rounds=sum(1 for round_ in vote_rounds if round_.visibility == VoteVisibility.VISIBLE),
            tie_count=sum(1 for round_ in closed_rounds if self._is_tie(round_)),
            average_vote_duration_hours=mean(durations_hours) if durations_hours else None,
        )

    @staticmethod
    def _is_tie(vote_round: VoteRound) -> bool:
        """Whether a round's final standings had more than one top suggestion.

        A pure recalculation from the round's persisted votes -- mirrors
        VoteService.get_current_winners' own tally-then-find-max
        approach, but operates on an already-loaded VoteRound rather than
        looking one up by ID, since bulk statistics already have every
        round in hand.
        """
        if not vote_round.votes:
            return False
        tally: dict[int, int] = {}
        for vote in vote_round.votes.values():
            tally[vote.suggestion_id] = tally.get(vote.suggestion_id, 0) + 1
        top_count = max(tally.values())
        return sum(1 for count in tally.values() if count == top_count) > 1

    # --- FR-034: Suggestion statistics -----------------------------------------------

    def suggestion_statistics(self, suggestion_id: int) -> Optional[SuggestionStatistics]:
        """Return FR-034 Section 6's statistics for one suggestion, or None if unknown."""
        watch_item = self._suggestion_source.get_suggestion(suggestion_id)
        if watch_item is None:
            return None

        vote_rounds = self._vote_rounds(watch_item.guild_id)
        nominating_rounds = sorted(
            (round_ for round_ in vote_rounds if suggestion_id in round_.candidate_suggestion_ids),
            key=lambda round_: round_.created_at,
        )
        first_nomination_at = nominating_rounds[0].created_at if nominating_rounds else None
        last_nomination_at = nominating_rounds[-1].created_at if nominating_rounds else None

        journey = watch_item.journey
        suggestion_date = journey.suggestion_date
        watch_dates = sorted(journey.watch_dates)

        days_until_first_nomination = (
            (first_nomination_at.date() - suggestion_date).days
            if suggestion_date is not None and first_nomination_at is not None
            else None
        )
        days_until_watched = (
            (watch_dates[0] - suggestion_date).days
            if suggestion_date is not None and watch_dates
            else None
        )

        return SuggestionStatistics(
            suggestion_id=suggestion_id,
            title=watch_item.title,
            status=watch_item.status,
            created_date=suggestion_date,
            submitter=journey.original_suggester,
            nomination_count=len(nominating_rounds),
            first_nomination_at=first_nomination_at,
            last_nomination_at=last_nomination_at,
            watch_count=len(journey.watch_dates),
            is_retired=journey.retired_at is not None,
            retired_at=journey.retired_at,
            is_archived=watch_item.status == WatchItemStatus.ARCHIVED,
            rotations_participated_in=len(journey.rotation_history),
            days_until_first_nomination=days_until_first_nomination,
            days_until_watched=days_until_watched,
        )

    # --- FR-034: Member statistics ----------------------------------------------------

    def member_statistics(self, guild_id: int | None, discord_user_id: int) -> MemberStatistics:
        """Return FR-034 Section 7's statistics for one member's own activity.

        Only ever meaningful for the requesting member themselves -- see
        the module docstring and MemberStatistics. Suggestions submitted/
        watched/retired/winning only count suggestions whose
        journey.original_suggester matches discord_user_id -- suggestions
        created before FR-034 have no recorded submitter and are
        correctly excluded (see has_submission_history).
        """
        submitter_key = str(discord_user_id)
        watch_items = self._watch_items(guild_id)
        submitted = [item for item in watch_items if item.journey.original_suggester == submitter_key]

        vote_rounds = self._vote_rounds(guild_id)
        rounds_voted_in = sum(1 for round_ in vote_rounds if discord_user_id in round_.votes)
        participation_percentage = (
            rounds_voted_in / len(vote_rounds) * 100 if vote_rounds else 0.0
        )

        return MemberStatistics(
            discord_user_id=discord_user_id,
            suggestions_submitted=len(submitted),
            suggestions_watched=sum(1 for item in submitted if item.status == WatchItemStatus.WATCHED),
            suggestions_retired=sum(1 for item in submitted if item.journey.retired_at is not None),
            winning_suggestions=sum(1 for item in submitted if item.journey.times_won > 0),
            votes_cast=rounds_voted_in,
            participation_percentage=participation_percentage,
            has_submission_history=bool(submitted),
        )

    # --- FR-034: Rotation statistics --------------------------------------------------

    def rotation_statistics(self, database_id: int) -> Optional[RotationStatistics]:
        """Return FR-034 Section 8's statistics for one database's rotations.

        Returns None only when rotation_service wasn't configured (see
        __init__). A database that has never started a rotation (e.g.
        Infinite Pool, or one with no votes yet) is a valid, graceful
        result -- not None -- with every count at zero and every average
        at None; this method never bootstraps rotation state as a side
        effect of being read (see RotationService.get_open_rotation vs.
        get_or_start_rotation).
        """
        if self._rotation_service is None:
            return None

        rotations = self._rotation_service.list_rotations(database_id)
        current_rotation = self._rotation_service.get_open_rotation(database_id)
        current_progress = (
            self._rotation_service.progress_for_rotation(current_rotation)
            if current_rotation is not None
            else None
        )

        completed = [rotation for rotation in rotations if rotation.status == RotationStatus.COMPLETED]
        durations_hours = [
            (rotation.completed_at - rotation.started_at).total_seconds() / 3600
            for rotation in completed
            if rotation.completed_at is not None
        ]
        sizes = [len(rotation.assigned_suggestion_ids) for rotation in rotations]

        return RotationStatistics(
            database_id=database_id,
            current_rotation_id=current_rotation.id if current_rotation is not None else None,
            current_rotation_started_at=current_rotation.started_at if current_rotation is not None else None,
            current_progress=current_progress,
            total_rotations=len(rotations),
            completed_rotations=len(completed),
            average_completed_rotation_duration_hours=mean(durations_hours) if durations_hours else None,
            average_rotation_size=mean(sizes) if sizes else None,
        )

    # --- FR-034: Database statistics --------------------------------------------------

    def database_statistics(self, database_id: int) -> Optional[DatabaseStatistics]:
        """Return FR-034 Section 9's statistics for one suggestion database.

        Returns None if no database with this ID exists.
        """
        database = self._suggestion_source.get_database(database_id)
        if database is None:
            return None

        items = self._suggestion_source.get_suggestions_for_database(database_id, include_archived=True)
        archived = [item for item in items if item.status == WatchItemStatus.ARCHIVED]

        return DatabaseStatistics(
            database_id=database_id,
            database_name=database.name,
            active_suggestions=sum(
                1 for item in items if item.status not in (WatchItemStatus.ARCHIVED, WatchItemStatus.WATCHED)
            ),
            archived_suggestions=len(archived),
            watched_suggestions=sum(1 for item in items if item.status == WatchItemStatus.WATCHED),
            retired_suggestions=sum(1 for item in archived if item.journey.retired_at is not None),
            rotation=self.rotation_statistics(database_id),
        )

    def _watch_parties(self, guild_id: int | None) -> Sequence[WatchParty]:
        if self._watch_party_source is None:
            return ()
        return self._watch_party_source.list_watch_parties(guild_id)

    @staticmethod
    def _count_watch_items(
        watch_items: Sequence[WatchItem], active_database_ids: frozenset[int]
    ) -> tuple[int, int, int]:
        """Return watched, current-suggestion, and active-suggestion counts."""
        watched_items = 0
        total_suggestions = 0
        active_suggestions = 0

        for item in watch_items:
            if item.status == WatchItemStatus.WATCHED:
                watched_items += 1
                continue

            total_suggestions += 1
            if item.database_id in active_database_ids:
                active_suggestions += 1

        return watched_items, total_suggestions, active_suggestions

    @staticmethod
    def _count_vote_rounds(
        vote_rounds: Sequence[VoteRound],
    ) -> tuple[int, int, int]:
        """Return open-round, closed-round, and current-vote counts."""
        open_rounds = 0
        closed_rounds = 0
        total_votes = 0

        for vote_round in vote_rounds:
            if vote_round.status == VoteRoundStatus.OPEN:
                open_rounds += 1
            elif vote_round.status == VoteRoundStatus.CLOSED:
                closed_rounds += 1
            total_votes += len(vote_round.votes)

        return open_rounds, closed_rounds, total_votes

    def _watch_items(self, guild_id: int | None) -> Sequence[WatchItem]:
        items = self._suggestion_source.get_suggestions()
        if guild_id is None:
            return items
        return [item for item in items if item.guild_id == guild_id]

    def _databases(self, guild_id: int | None) -> Sequence[SuggestionDatabase]:
        return self._suggestion_source.list_databases(guild_id)

    def _vote_rounds(self, guild_id: int | None) -> Sequence[VoteRound]:
        rounds = self._vote_source.load().rounds
        if guild_id is None:
            return rounds
        return [vote_round for vote_round in rounds if vote_round.guild_id == guild_id]
