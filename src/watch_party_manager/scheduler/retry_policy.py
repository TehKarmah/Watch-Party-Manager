"""Retry timing for failed scheduled jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    delays: tuple[timedelta, ...] = (
        timedelta(minutes=1),
        timedelta(minutes=5),
        timedelta(minutes=15),
    )

    def __post_init__(self) -> None:
        if not self.delays:
            raise ValueError("at least one retry delay is required")
        if any(delay.total_seconds() <= 0 for delay in self.delays):
            raise ValueError("retry delays must be positive")

    @property
    def maximum_attempts(self) -> int:
        return len(self.delays)

    def delay_after_failure(self, attempt_count: int) -> timedelta | None:
        """Return the delay after a failed attempt.

        attempt_count is the number of attempts already made, including the
        attempt that just failed.
        """
        if attempt_count <= 0:
            raise ValueError("attempt_count must be positive")
        if attempt_count > len(self.delays):
            return None
        return self.delays[attempt_count - 1]
