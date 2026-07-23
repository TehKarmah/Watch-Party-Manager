"""Service for selecting nominees for a new voting round.

This is a dedicated selection service rather than logic living inside
SuggestionService or bot.py: choosing nominees genuinely needs both
worlds -- suggestion data (titles, genres, media type) from
SuggestionService, and voting history (recent nominees, recent winners)
from VoteService. Combining them here keeps that cross-cutting concern
out of both individual services and out of the Discord layer entirely.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, List, Optional, Protocol, Sequence, Set, Tuple

from watch_party_manager.domain.watch_item import WatchItem

if TYPE_CHECKING:
    from watch_party_manager.services.candidate_selection_strategy import CandidateSelectionStrategy

# How many of the most recent closed rounds to consider when
# deprioritizing recently-rotated titles.
DEFAULT_RECENT_ROUNDS_CONSIDERED = 5

# Selection weights for the initial random pick. Never zero, per "do not
# permanently exclude anything" -- a recent winner or nominee can still be
# picked, just much less often.
RECENT_WINNER_WEIGHT = 0.05
RECENT_NOMINEE_WEIGHT = 0.5
NEUTRAL_WEIGHT = 1.0

# Score penalties applied during the greedy diversity pass. Winners are
# penalized far more heavily than plain recent nominees.
RECENT_WINNER_PENALTY = 100.0
RECENT_NOMINEE_PENALTY = 20.0


class SuggestionSource(Protocol):
    """The subset of SuggestionService the selector needs."""

    def get_suggestions_for_database(self, database_id: int) -> List[WatchItem]: ...


class VoteHistorySource(Protocol):
    """The subset of VoteService the selector needs.

    Kept minimal and Protocol-based, matching the project's existing
    SuggestionLookup pattern, so this service depends only on the two
    capabilities it actually uses.
    """

    def get_recent_closed_rounds(
        self, limit: int, database_id: Optional[int] = None
    ) -> list: ...

    def get_current_winners(self, round_id: int) -> object: ...


class NomineeSelectionService:
    """Selects a diverse, rotation-aware set of nominees for a voting round.

    Selection works in two stages:
      1. An initial pick, weighted away from (but never excluding) titles
         that recently won or were recently nominated.
      2. A greedy pass that fills the remaining slots by preferring
         candidates that add previously unseen genres or media types,
         with the same rotation penalty subtracted from their score.

    Only genre and media_type are used for diversity today, since those
    are the only structured fields WatchItem currently has. Metadata like
    release year, cast, director, or franchise would slot into the same
    scoring approach automatically once such fields exist -- this service
    doesn't need to change shape to support that, only the scoring
    function would grow. Until then, those factors are gracefully treated
    as unavailable rather than guessed at.
    """

    def __init__(
        self,
        suggestion_service: SuggestionSource,
        vote_service: VoteHistorySource,
        recent_rounds_considered: int = DEFAULT_RECENT_ROUNDS_CONSIDERED,
    ) -> None:
        """Initialize the selector.

        Args:
            suggestion_service: Supplies the eligible suggestion pool for
                a database.
            vote_service: Supplies recent round history for rotation
                awareness.
            recent_rounds_considered: How many of the most recent closed
                rounds count as "recent" for deprioritization purposes.
        """
        self._suggestion_service = suggestion_service
        self._vote_service = vote_service
        self._recent_rounds_considered = recent_rounds_considered

    def select_nominees(
        self,
        database_id: int,
        count: int,
        rng: Optional[random.Random] = None,
        strategy: Optional["CandidateSelectionStrategy"] = None,
    ) -> List[WatchItem]:
        """Select nominees for a new voting round from one database's suggestions.

        Never mixes databases -- only suggestions already scoped to
        database_id (via SuggestionService.get_suggestions_for_database, or
        via `strategy`'s own candidate_pool when one is supplied) are ever
        considered.

        Args:
            database_id: The suggestion database to select nominees from.
            count: The desired number of nominees.
            rng: Optional random source for deterministic testing.
                Defaults to random.SystemRandom() in production.
            strategy: FR-033B's optional CandidateSelectionStrategy
                (see services/candidate_selection_strategy.py). When
                given, it determines the candidate pool and contributes a
                per-candidate weight multiplier alongside this service's
                own existing recent-nominee/winner weighting, and is
                notified of the final selection afterward (e.g. so
                Rotation Pool can record presentation). When omitted
                (the default), behavior is identical to before FR-033B.

        Returns:
            - Exactly `count` nominees if the database has at least that
              many eligible suggestions.
            - Every eligible suggestion (fewer than `count`) if the
              database has at least 2 but fewer than `count`.
            - An empty list if the database has fewer than 2 eligible
              suggestions -- the caller should treat this as "not enough
              to start a vote."
        """
        if count <= 0:
            raise ValueError("count must be positive")

        candidates = (
            strategy.candidate_pool(database_id)
            if strategy is not None
            else self._suggestion_service.get_suggestions_for_database(database_id)
        )
        if len(candidates) < 2:
            return []
        if len(candidates) <= count:
            # Low pool: use everything rather than rejecting outright.
            selected_all = list(candidates)
            if strategy is not None:
                strategy.on_presented(database_id, [item.id for item in selected_all])
            return selected_all

        recent_nominee_ids, recent_winner_ids = self._recent_rotation_context(database_id)
        chooser = rng if rng is not None else random.SystemRandom()

        def rotation_penalty(item: WatchItem) -> float:
            if item.id in recent_winner_ids:
                return RECENT_WINNER_PENALTY
            if item.id in recent_nominee_ids:
                return RECENT_NOMINEE_PENALTY
            return 0.0

        def rotation_weight(item: WatchItem) -> float:
            penalty = rotation_penalty(item)
            if penalty >= RECENT_WINNER_PENALTY:
                base_weight = RECENT_WINNER_WEIGHT
            elif penalty >= RECENT_NOMINEE_PENALTY:
                base_weight = RECENT_NOMINEE_WEIGHT
            else:
                base_weight = NEUTRAL_WEIGHT
            if strategy is not None:
                base_weight *= strategy.weight_for(item)
            return base_weight

        remaining = list(candidates)
        weights = [rotation_weight(item) for item in remaining]
        first = chooser.choices(remaining, weights=weights, k=1)[0]
        selected = [first]
        remaining.remove(first)
        seen_genres = {genre.lower() for genre in first.genres}
        seen_media_types = {first.media_type}

        while len(selected) < count:
            scored: list[tuple[float, float, WatchItem]] = []
            for item in remaining:
                new_genres = sum(1 for genre in item.genres if genre.lower() not in seen_genres)
                new_media_type = 1 if item.media_type not in seen_media_types else 0
                diversity_score = new_genres * 3 + new_media_type
                score = diversity_score - rotation_penalty(item)
                scored.append((score, chooser.random(), item))
            _, _, chosen = max(scored, key=lambda entry: (entry[0], entry[1]))
            selected.append(chosen)
            remaining.remove(chosen)
            seen_genres.update(genre.lower() for genre in chosen.genres)
            seen_media_types.add(chosen.media_type)

        if strategy is not None:
            strategy.on_presented(database_id, [item.id for item in selected])
        return selected

    def _recent_rotation_context(self, database_id: int) -> Tuple[Set[int], Set[int]]:
        """Determine which suggestion IDs were recently nominated or won.

        Returns:
            (recent_nominee_ids, recent_winner_ids), both drawn from the
            last `recent_rounds_considered` closed rounds. Winners are a
            subset of nominees by definition, but are tracked separately
            since they carry a much heavier penalty.
        """
        recent_rounds = self._vote_service.get_recent_closed_rounds(
            self._recent_rounds_considered, database_id=database_id
        )
        recent_nominee_ids: Set[int] = set()
        recent_winner_ids: Set[int] = set()
        for vote_round in recent_rounds:
            recent_nominee_ids.update(vote_round.candidate_suggestion_ids)
            winner_result = self._vote_service.get_current_winners(vote_round.id)
            if winner_result.success:
                recent_winner_ids.update(winner_result.winning_suggestion_ids)
        return recent_nominee_ids, recent_winner_ids
