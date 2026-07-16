"""Read-only statistics derived from existing WASH data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from watch_party_manager.domain.suggestion_database import SuggestionDatabase
from watch_party_manager.domain.vote import VoteRound, VoteRoundStatus
from watch_party_manager.domain.watch_item import WatchItem, WatchItemStatus
from watch_party_manager.persistence.vote_repository import JsonVoteRepository, VoteLoadResult


class SuggestionStatisticsSource(Protocol):
    """Suggestion data required by :class:`StatisticsService`."""

    def get_suggestions(self) -> list[WatchItem]: ...

    def list_databases(self, guild_id: int | None = None) -> list[SuggestionDatabase]: ...


class VoteStatisticsSource(Protocol):
    """Voting data required by :class:`StatisticsService`."""

    def load(self) -> VoteLoadResult: ...


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
    ) -> None:
        self._suggestion_source = suggestion_source
        self._vote_source = vote_source if vote_source is not None else JsonVoteRepository()

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

        active_database_ids = {
            database.database_id for database in databases if database.active
        }
        watched_items = sum(
            1 for item in watch_items if item.status == WatchItemStatus.WATCHED
        )
        total_suggestions = sum(
            1 for item in watch_items if item.status != WatchItemStatus.WATCHED
        )
        active_suggestions = sum(
            1
            for item in watch_items
            if item.status != WatchItemStatus.WATCHED
            and item.database_id in active_database_ids
        )

        open_rounds = sum(
            1 for vote_round in vote_rounds if vote_round.status == VoteRoundStatus.OPEN
        )
        closed_rounds = sum(
            1 for vote_round in vote_rounds if vote_round.status == VoteRoundStatus.CLOSED
        )
        total_votes = sum(len(vote_round.votes) for vote_round in vote_rounds)
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
