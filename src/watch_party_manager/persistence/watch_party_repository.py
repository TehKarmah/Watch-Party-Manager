"""JSON-backed persistence for scheduled watch parties."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Union

from watch_party_manager.domain.watch_party import WatchParty, WatchPartyStatus

logger = logging.getLogger(__name__)

# Kept separate from voting.json/suggestions.json: watch parties are their
# own concern with their own lifecycle, and this file is easy to inspect
# or reset independently.
DEFAULT_WATCH_PARTIES_PATH = Path("data/watch_parties.json")

FIRST_WATCH_PARTY_ID = 1


@dataclass
class WatchPartyLoadResult:
    """What comes back from loading the watch parties file.

    next_id is tracked separately from the loaded watch parties so that
    IDs keep increasing even if every watch party were ever removed
    (IDs must never be reused).
    """

    watch_parties: list[WatchParty]
    next_id: int


class JsonWatchPartyRepository:
    """Loads and saves watch parties as a JSON file on disk.

    Mirrors JsonVoteRepository: this is the only place that knows watch
    party data is stored as JSON. WatchPartyService only ever calls
    load()/save(), so the storage mechanism can be swapped out later
    without touching it.
    """

    def __init__(self, file_path: Union[Path, str] = DEFAULT_WATCH_PARTIES_PATH) -> None:
        """Initialize the repository.

        Args:
            file_path: Path to the JSON file used for persistence.
        """
        self._file_path = Path(file_path)

    def load(self) -> WatchPartyLoadResult:
        """Load watch parties from disk.

        A missing file is expected on first run and is not an error. A file
        that exists but can't be parsed is logged and treated as empty
        state rather than crashing the bot.

        Returns:
            A WatchPartyLoadResult with the persisted watch parties
            (insertion order preserved) and the next ID to hand out.
        """
        if not self._file_path.exists():
            return WatchPartyLoadResult(watch_parties=[], next_id=FIRST_WATCH_PARTY_ID)

        try:
            raw_text = self._file_path.read_text(encoding="utf-8")
            data = json.loads(raw_text)
            watch_parties = [self._deserialize(entry) for entry in data["watch_parties"]]
            next_id = data.get("next_id", FIRST_WATCH_PARTY_ID)
            return WatchPartyLoadResult(watch_parties=watch_parties, next_id=next_id)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.error(f"Could not load watch party data from {self._file_path}: {exc}")
            return WatchPartyLoadResult(watch_parties=[], next_id=FIRST_WATCH_PARTY_ID)

    def save(self, watch_parties: Iterable[WatchParty], next_id: int) -> None:
        """Save watch parties to disk, overwriting any previous contents.

        Creates the parent directory and the file itself if they don't
        already exist.

        Args:
            watch_parties: The watch parties to persist.
            next_id: The ID to hand out to the next new watch party.
                Persisted so IDs keep increasing across restarts.
        """
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "next_id": next_id,
            "watch_parties": [self._serialize(watch_party) for watch_party in watch_parties],
        }
        self._file_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @staticmethod
    def _serialize(watch_party: WatchParty) -> dict:
        return {
            "id": watch_party.id,
            "watch_item_id": watch_party.watch_item_id,
            "scheduled_at": watch_party.scheduled_at.isoformat(),
            "guild_id": watch_party.guild_id,
            "channel_id": watch_party.channel_id,
            "status": watch_party.status.value,
            "created_at": watch_party.created_at.isoformat(),
        }

    @staticmethod
    def _deserialize(entry: dict) -> WatchParty:
        return WatchParty(
            id=entry["id"],
            watch_item_id=entry["watch_item_id"],
            scheduled_at=datetime.fromisoformat(entry["scheduled_at"]),
            guild_id=entry["guild_id"],
            channel_id=entry.get("channel_id"),
            status=WatchPartyStatus(entry.get("status", WatchPartyStatus.SCHEDULED.value)),
            created_at=datetime.fromisoformat(entry["created_at"]),
        )
