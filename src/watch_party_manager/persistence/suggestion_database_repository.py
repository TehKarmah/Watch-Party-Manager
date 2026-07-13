"""JSON-backed persistence for suggestion databases."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Union

from watch_party_manager.domain.suggestion_database import SuggestionDatabase

logger = logging.getLogger(__name__)

# Kept separate from suggestions.json and voting.json: a suggestion
# database is its own configuration concept, distinct from the
# suggestions and votes it will eventually organize.
DEFAULT_SUGGESTION_DATABASES_PATH = Path("data/suggestion_databases.json")

FIRST_DATABASE_ID = 1


@dataclass
class SuggestionDatabaseLoadResult:
    """What comes back from loading the suggestion-databases file.

    next_id is tracked separately from the loaded databases so that IDs
    keep increasing even if every database were ever removed (IDs must
    never be reused).
    """

    databases: list[SuggestionDatabase]
    next_id: int


class JsonSuggestionDatabaseRepository:
    """Loads and saves suggestion databases as a JSON file on disk.

    Mirrors JsonSuggestionRepository and JsonVoteRepository: this is the
    only place that knows suggestion databases are stored as JSON.
    SuggestionService only ever calls load()/save(), so the storage
    mechanism can be swapped out later without touching it.
    """

    def __init__(self, file_path: Union[Path, str] = DEFAULT_SUGGESTION_DATABASES_PATH) -> None:
        """Initialize the repository.

        Args:
            file_path: Path to the JSON file used for persistence.
        """
        self._file_path = Path(file_path)

    def load(self) -> SuggestionDatabaseLoadResult:
        """Load suggestion databases from disk.

        A missing file is expected on first run (and for every existing
        installation upgrading to this milestone, since this file is new)
        and is not an error. A file that exists but can't be parsed is
        logged and treated as empty state rather than crashing the bot.

        Returns:
            A SuggestionDatabaseLoadResult with the persisted databases
            (in their original creation order) and the next ID to hand out.
        """
        if not self._file_path.exists():
            return SuggestionDatabaseLoadResult(databases=[], next_id=FIRST_DATABASE_ID)

        try:
            raw_text = self._file_path.read_text(encoding="utf-8")
            data = json.loads(raw_text)
            entries = data["databases"]
            databases = [self._deserialize(entry) for entry in entries]
            next_id = data.get("next_id", FIRST_DATABASE_ID)
            return SuggestionDatabaseLoadResult(databases=databases, next_id=next_id)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.error(f"Could not load suggestion databases from {self._file_path}: {exc}")
            return SuggestionDatabaseLoadResult(databases=[], next_id=FIRST_DATABASE_ID)

    def save(self, databases: Iterable[SuggestionDatabase], next_id: int) -> None:
        """Save suggestion databases to disk, overwriting any previous contents.

        Creates the parent directory and the file itself if they don't
        already exist.

        Args:
            databases: The databases to persist, in creation order.
            next_id: The ID to hand out to the next new database.
                Persisted so IDs keep increasing across restarts.
        """
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "next_id": next_id,
            "databases": [self._serialize(database) for database in databases],
        }
        self._file_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @staticmethod
    def _serialize(database: SuggestionDatabase) -> dict:
        return {
            "database_id": database.database_id,
            "name": database.name,
            "guild_id": database.guild_id,
            "channel_id": database.channel_id,
            "active": database.active,
            "created_at": database.created_at.isoformat(),
        }

    @staticmethod
    def _deserialize(entry: dict) -> SuggestionDatabase:
        return SuggestionDatabase(
            database_id=entry["database_id"],
            name=entry["name"],
            guild_id=entry["guild_id"],
            channel_id=entry["channel_id"],
            # Defaults to active for forward compatibility with any future
            # entry that might omit this field.
            active=entry.get("active", True),
            created_at=datetime.fromisoformat(entry["created_at"]),
        )
