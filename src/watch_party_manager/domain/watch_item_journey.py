from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Tuple


@dataclass(slots=True)
class WatchItemJourney:
    """Historical record of a Watch Item's lifecycle and viewings."""

    original_suggester: Optional[str] = None
    suggestion_date: Optional[date] = None
    rotation_history: Tuple[int, ...] = field(default_factory=tuple)
    voting_appearances: int = 0
    winning_vote: Optional[str] = None
    watch_dates: Tuple[date, ...] = field(default_factory=tuple)
    rewatch_count: int = 0
    times_won: int = 0
    last_nominated_date: Optional[date] = None
    last_won_date: Optional[date] = None

    def __post_init__(self) -> None:
        self.original_suggester = self._normalize_optional_text(self.original_suggester)
        self.winning_vote = self._normalize_optional_text(self.winning_vote)
        self._validate_non_negative_counts()
        self.rotation_history = self._normalize_rotations(self.rotation_history)
        self.watch_dates = self._normalize_watch_dates(self.watch_dates)
        if self.last_nominated_date is not None:
            self._validate_watch_date(self.last_nominated_date)
        if self.last_won_date is not None:
            self._validate_watch_date(self.last_won_date)

    def _validate_non_negative_counts(self) -> None:
        if self.voting_appearances < 0:
            raise ValueError("voting_appearances must be greater than or equal to zero")
        if self.rewatch_count < 0:
            raise ValueError("rewatch_count must be greater than or equal to zero")
        if self.times_won < 0:
            raise ValueError("times_won must be greater than or equal to zero")

    @staticmethod
    def _normalize_optional_text(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None

    @staticmethod
    def _validate_rotation_number(rotation_number: int) -> None:
        if isinstance(rotation_number, bool) or not isinstance(rotation_number, int):
            raise TypeError("rotation_number must be an integer")
        if rotation_number <= 0:
            raise ValueError("rotation_number must be greater than zero")

    @staticmethod
    def _validate_watch_date(watch_date: date) -> None:
        if isinstance(watch_date, bool) or not isinstance(watch_date, date):
            raise TypeError("watch_date must be a datetime.date instance")

    @staticmethod
    def _normalize_rotations(rotations: Tuple[int, ...] | list[int] | None) -> Tuple[int, ...]:
        if not rotations:
            return ()

        normalized: list[int] = []
        for rotation in rotations:
            if rotation is None:
                continue
            WatchItemJourney._validate_rotation_number(rotation)
            normalized.append(int(rotation))

        return tuple(normalized)

    @staticmethod
    def _normalize_watch_dates(watch_dates: Tuple[date, ...] | list[date] | None) -> Tuple[date, ...]:
        if not watch_dates:
            return ()

        normalized: list[date] = []
        for watch_date in watch_dates:
            if watch_date is None:
                continue
            WatchItemJourney._validate_watch_date(watch_date)
            normalized.append(watch_date)

        return tuple(normalized)

    def record_rotation_entry(self, rotation_number: int) -> None:
        self._validate_rotation_number(rotation_number)
        self.rotation_history = (*self.rotation_history, int(rotation_number))

    def record_vote_appearance(self, nominated_date: Optional[date] = None) -> None:
        """Record that this item was nominated in a voting round.

        Args:
            nominated_date: The date this nomination happened. Optional so
                existing callers that only care about the count (not the
                date) keep working unchanged; last_nominated_date simply
                stays at its previous value when omitted.
        """
        self.voting_appearances += 1
        if nominated_date is not None:
            self._validate_watch_date(nominated_date)
            self.last_nominated_date = nominated_date

    def record_winning_vote(self, winning_vote: str, won_date: Optional[date] = None) -> None:
        """Record that this item won a voting round.

        Args:
            winning_vote: Preserved for backward compatibility -- see the
                field's existing docstring/behavior. Unchanged by this
                milestone.
            won_date: The date this win happened. Optional for the same
                backward-compatibility reason as record_vote_appearance:
                existing callers that only set winning_vote keep working
                unchanged, and times_won/last_won_date simply aren't
                touched when omitted.
        """
        self.winning_vote = self._normalize_optional_text(winning_vote)
        if won_date is not None:
            self._validate_watch_date(won_date)
            self.times_won += 1
            self.last_won_date = won_date

    def record_watch_date(self, watch_date: date) -> None:
        self._validate_watch_date(watch_date)
        self.watch_dates = (*self.watch_dates, watch_date)

    def record_rewatch(self) -> None:
        self.rewatch_count += 1
