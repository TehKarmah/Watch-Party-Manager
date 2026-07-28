"""JSON-backed persistence for FR-033B rotation state.

Mirrors JsonVoteRepository's shape exactly (a single next-id counter plus
a flat list of records). Kept as its own file (data/rotations.json),
separate from suggestions.json and voting.json, matching this project's
convention of one concern per persistence file. Nothing here needs
special handling by BackupService: it already sweeps every *.json file
under data/, so rotation state survives backup/restore/restart for free.

Also persists, alongside rotation records, a small per-database map
tracking which rotation the Rotation Low-Pool notification (Rotation &
Collection Health) has already been sent for -- kept in the same file
since it's a similarly small, database-scoped piece of runtime state
with no other natural home, and this avoids introducing a fourth small
JSON file for one dict.

Rotation & Collection Health Audit: this replaces the old, interval-based
Low Pool Reminder's `low_pool_reminder_last_sent_at` timestamp map
(FR-033B Section 7) with a per-rotation dedup map instead -- "notify once
per rotation, reset after rollover" falls out naturally from keying on
rotation_id rather than a timestamp, with no explicit reset step needed
(the same pattern RotationService.is_in_rotation_cooldown already uses
for "clearing" cooldown on a fresh rotation). A rotations.json file saved
before this change simply has no `low_pool_notified_rotation_ids` key yet
-- it loads as an empty map (equivalent to "never notified"), and its old
`low_pool_reminder_last_sent_at` key, if present, is silently ignored
rather than migrated: the worst case is one extra notification being sent
shortly after upgrading, never a missed or duplicated one thereafter.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Union

from watch_party_manager.domain.rotation import Rotation, RotationStatus

logger = logging.getLogger(__name__)

DEFAULT_ROTATIONS_PATH = Path("data/rotations.json")

FIRST_ROTATION_ID = 1


@dataclass
class RotationLoadResult:
    """What comes back from loading the rotations file.

    next_rotation_id is tracked separately from the loaded rotations so
    IDs keep increasing even if every rotation record were ever removed.
    low_pool_notified_rotation_ids maps database_id to the id of the
    rotation the Rotation Low-Pool notification has already been sent
    for (absent, or a stale/no-longer-open id, means "not yet notified
    for the current rotation").
    """

    rotations: list[Rotation]
    next_rotation_id: int
    low_pool_notified_rotation_ids: Dict[int, int] = field(default_factory=dict)


class JsonRotationRepository:
    """Loads and saves FR-033B rotation state as a JSON file on disk.

    Mirrors JsonVoteRepository: this is the only place that knows
    rotation data is stored as JSON. RotationService only ever calls
    load()/save(), so the storage mechanism can be swapped out later
    without touching it.
    """

    def __init__(self, file_path: Union[Path, str] = DEFAULT_ROTATIONS_PATH) -> None:
        self._file_path = Path(file_path)

    def load(self) -> RotationLoadResult:
        """Load rotation state from disk.

        A missing file is expected on first run and is not an error. A
        file that exists but can't be parsed is logged and treated as
        empty rotation state rather than crashing the bot.
        """
        if not self._file_path.exists():
            return RotationLoadResult(rotations=[], next_rotation_id=FIRST_ROTATION_ID)

        try:
            raw_text = self._file_path.read_text(encoding="utf-8")
            data = json.loads(raw_text)
            rotations = [self._deserialize_rotation(entry) for entry in data.get("rotations", [])]
            next_rotation_id = data.get("next_rotation_id", FIRST_ROTATION_ID)
            notified_raw = data.get("low_pool_notified_rotation_ids", {})
            notified = {int(database_id): int(value) for database_id, value in notified_raw.items()}
            return RotationLoadResult(
                rotations=rotations,
                next_rotation_id=next_rotation_id,
                low_pool_notified_rotation_ids=notified,
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.error(f"Could not load rotation data from {self._file_path}: {exc}")
            return RotationLoadResult(rotations=[], next_rotation_id=FIRST_ROTATION_ID)

    def save(
        self,
        rotations: Iterable[Rotation],
        next_rotation_id: int,
        low_pool_notified_rotation_ids: Dict[int, int],
    ) -> None:
        """Save rotation state to disk, overwriting any previous contents."""
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "next_rotation_id": next_rotation_id,
            "rotations": [self._serialize_rotation(rotation) for rotation in rotations],
            "low_pool_notified_rotation_ids": {
                str(database_id): rotation_id
                for database_id, rotation_id in low_pool_notified_rotation_ids.items()
            },
        }
        self._file_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @staticmethod
    def _serialize_rotation(rotation: Rotation) -> dict:
        return {
            "id": rotation.id,
            "database_id": rotation.database_id,
            "status": rotation.status.value,
            "started_at": rotation.started_at.isoformat(),
            "completed_at": rotation.completed_at.isoformat() if rotation.completed_at else None,
            "assigned_suggestion_ids": list(rotation.assigned_suggestion_ids),
        }

    @staticmethod
    def _deserialize_rotation(entry: dict) -> Rotation:
        completed_at_raw = entry.get("completed_at")
        return Rotation(
            id=entry["id"],
            database_id=entry["database_id"],
            status=RotationStatus(entry.get("status", RotationStatus.OPEN.value)),
            started_at=datetime.fromisoformat(entry["started_at"]),
            completed_at=datetime.fromisoformat(completed_at_raw) if completed_at_raw else None,
            assigned_suggestion_ids=tuple(entry.get("assigned_suggestion_ids", ())),
        )
