"""JSON-backed persistence for the Eligible Pool Warning's re-arm state.

Deliberately its own tiny file (data/eligible_pool_warning_state.json): a
simple per-database "already warned, not yet re-armed" flag, threshold-
crossing dedup rather than keyed to any external lifecycle. Nothing here
needs special handling by BackupService: it already sweeps every *.json
file under data/, so this state survives backup/restore/restart for free.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Set, Union

logger = logging.getLogger(__name__)

DEFAULT_ELIGIBLE_POOL_WARNING_STATE_PATH = Path("data/eligible_pool_warning_state.json")


class JsonEligiblePoolWarningStateRepository:
    """Loads and saves which databases currently have an active (armed)
    Eligible Pool Warning -- i.e. have already been notified since last
    rising back above their threshold.
    """

    def __init__(self, file_path: Union[Path, str] = DEFAULT_ELIGIBLE_POOL_WARNING_STATE_PATH) -> None:
        self._file_path = Path(file_path)

    def load(self) -> Set[int]:
        """Load the set of currently-armed database ids from disk.

        A missing file is expected on first run and is not an error. A
        file that exists but can't be parsed is logged and treated as
        empty state rather than crashing the bot.
        """
        if not self._file_path.exists():
            return set()
        try:
            raw_text = self._file_path.read_text(encoding="utf-8")
            data = json.loads(raw_text)
            return {int(database_id) for database_id in data.get("armed_database_ids", [])}
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.error(f"Could not load eligible pool warning state from {self._file_path}: {exc}")
            return set()

    def save(self, armed_database_ids: Set[int]) -> None:
        """Save the set of currently-armed database ids to disk, overwriting any previous contents."""
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"armed_database_ids": sorted(armed_database_ids)}
        self._file_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
