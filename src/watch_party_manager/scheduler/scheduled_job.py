"""Domain model for persistent scheduled work."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobResult(StrEnum):
    EXECUTED = "executed"
    SKIPPED_EXPIRED = "skipped_expired"
    SKIPPED_NOT_APPLICABLE = "skipped_not_applicable"
    CANCELLED = "cancelled"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def require_aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class ScheduledJob:
    guild_id: int
    job_type: str
    logical_key: str
    run_at: datetime
    payload: dict[str, Any] = field(default_factory=dict)
    job_id: str = field(default_factory=lambda: str(uuid4()))
    status: JobStatus = JobStatus.PENDING
    created_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    attempt_count: int = 0
    last_error: str | None = None
    result: JobResult | None = None

    def __post_init__(self) -> None:
        if self.guild_id <= 0:
            raise ValueError("guild_id must be a positive integer")
        if not self.job_type.strip():
            raise ValueError("job_type is required")
        if not self.logical_key.strip():
            raise ValueError("logical_key is required")
        if self.attempt_count < 0:
            raise ValueError("attempt_count cannot be negative")

        object.__setattr__(self, "job_type", self.job_type.strip())
        object.__setattr__(self, "logical_key", self.logical_key.strip())
        object.__setattr__(self, "run_at", require_aware_utc(self.run_at, "run_at"))
        object.__setattr__(
            self, "created_at", require_aware_utc(self.created_at, "created_at")
        )

        if self.started_at is not None:
            object.__setattr__(
                self,
                "started_at",
                require_aware_utc(self.started_at, "started_at"),
            )
        if self.completed_at is not None:
            object.__setattr__(
                self,
                "completed_at",
                require_aware_utc(self.completed_at, "completed_at"),
            )

    @property
    def is_active(self) -> bool:
        return self.status in {JobStatus.PENDING, JobStatus.RUNNING}

    def with_changes(self, **changes: Any) -> "ScheduledJob":
        return replace(self, **changes)
