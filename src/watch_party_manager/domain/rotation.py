"""Domain model for FR-033B's Rotation Pool candidate-selection tracking.

A Rotation tracks which suggestions in one database were assigned to a
"generation" of candidate selection, and persists across bot restarts,
backups, and restores (see persistence/rotation_repository.py, whose
JSON file lives under data/ alongside every other repository and is
therefore automatically covered by BackupService's generic *.json
sweep -- no special-casing needed).

Which assigned suggestions have been *presented* is intentionally NOT
duplicated on this record: it's derived from whether this rotation's id
appears in a suggestion's own WatchItemJourney.rotation_history (see
watch_item_journey.py), keeping a single source of truth rather than two
copies of the same fact that could drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Tuple


class RotationStatus(str, Enum):
    """Lifecycle state of a Rotation."""

    OPEN = "open"
    COMPLETED = "completed"


@dataclass(slots=True)
class Rotation:
    """One generation of Rotation Pool / Soft Rotation candidate tracking
    for a single suggestion database.
    """

    id: int
    database_id: int
    status: RotationStatus = RotationStatus.OPEN
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    assigned_suggestion_ids: Tuple[int, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        self._validate_id()
        self._validate_database_id()
        self.status = self._coerce_status(self.status)
        self._validate_timestamps()
        self.assigned_suggestion_ids = self._normalize_assigned_ids(self.assigned_suggestion_ids)

    def _validate_id(self) -> None:
        if not isinstance(self.id, int) or isinstance(self.id, bool) or self.id <= 0:
            raise ValueError("id must be a positive integer")

    def _validate_database_id(self) -> None:
        if (
            not isinstance(self.database_id, int)
            or isinstance(self.database_id, bool)
            or self.database_id <= 0
        ):
            raise ValueError("database_id must be a positive integer")

    @staticmethod
    def _coerce_status(status: RotationStatus) -> RotationStatus:
        if isinstance(status, RotationStatus):
            return status
        return RotationStatus(status)

    def _validate_timestamps(self) -> None:
        if self.started_at.tzinfo is None:
            raise ValueError("started_at must be timezone-aware")
        if self.completed_at is not None:
            if self.completed_at.tzinfo is None:
                raise ValueError("completed_at must be timezone-aware when provided")
            if self.completed_at < self.started_at:
                raise ValueError("completed_at must not be earlier than started_at")

    @staticmethod
    def _normalize_assigned_ids(assigned_ids: Tuple[int, ...] | list[int] | None) -> Tuple[int, ...]:
        if not assigned_ids:
            return ()
        normalized: list[int] = []
        for suggestion_id in assigned_ids:
            if not isinstance(suggestion_id, int) or isinstance(suggestion_id, bool) or suggestion_id <= 0:
                raise ValueError("assigned_suggestion_ids must contain only positive integers")
            if suggestion_id not in normalized:
                normalized.append(suggestion_id)
        return tuple(normalized)
