"""JSON-backed persistence for FR-030's Approval-Required join requests.

Mirrors JsonSuggestionDatabaseRepository's shape (a whole-collection load/
save plus a persisted next_id counter, since requests are a small,
service-managed collection just like suggestion databases), but writes
atomically via a temp-file-then-replace swap, matching the more recent
convention used by GuildConfigurationRepository and
SetupWizardRepository -- pending requests must reliably survive a bot
restart, so a torn write here is worth avoiding.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional, Union

from watch_party_manager.domain.membership_request import MembershipRequest, MembershipRequestStatus

logger = logging.getLogger(__name__)

DEFAULT_MEMBERSHIP_REQUESTS_PATH = Path("data/membership_requests.json")
FIRST_REQUEST_ID = 1


@dataclass
class MembershipRequestLoadResult:
    """What comes back from loading the membership-requests file.

    next_id is tracked separately from the loaded requests so IDs keep
    increasing even if every request were ever removed (IDs must never
    be reused) -- mirrors SuggestionDatabaseLoadResult exactly.
    """

    requests: list[MembershipRequest]
    next_id: int


class MembershipRequestRepository:
    """Loads and saves Approval-Required join requests as a JSON file on disk.

    MembershipService only ever calls load()/save(), so the storage
    mechanism can be swapped out later without touching it.
    """

    def __init__(self, file_path: Union[Path, str] = DEFAULT_MEMBERSHIP_REQUESTS_PATH) -> None:
        self._file_path = Path(file_path)

    def load(self) -> MembershipRequestLoadResult:
        """Load membership requests from disk.

        A missing file is expected on first run and is not an error. A
        file that exists but can't be parsed is logged and treated as
        empty state rather than crashing the bot.
        """
        if not self._file_path.exists():
            return MembershipRequestLoadResult(requests=[], next_id=FIRST_REQUEST_ID)

        try:
            data = json.loads(self._file_path.read_text(encoding="utf-8"))
            entries = data["requests"]
            requests = [self._deserialize(entry) for entry in entries]
            next_id = data.get("next_id", FIRST_REQUEST_ID)
            return MembershipRequestLoadResult(requests=requests, next_id=next_id)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.error(f"Could not load membership requests from {self._file_path}: {exc}")
            return MembershipRequestLoadResult(requests=[], next_id=FIRST_REQUEST_ID)

    def save(self, requests: Iterable[MembershipRequest], next_id: int) -> None:
        """Save membership requests to disk atomically, overwriting any
        previous contents.

        Args:
            requests: The requests to persist, in creation order.
            next_id: The ID to hand out to the next new request.
        """
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "next_id": next_id,
            "requests": [self._serialize(request) for request in requests],
        }
        temporary_path = self._file_path.with_suffix(self._file_path.suffix + ".tmp")
        temporary_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temporary_path.replace(self._file_path)

    # --- Query helpers ----------------------------------------------------------------
    #
    # These read straight from disk (via load()) rather than an in-memory
    # cache -- MembershipService keeps its own cache for the live
    # /join_watch_party workflow, but these exist independently of that so
    # a future administration feature (not part of this milestone) can
    # query request history without needing a MembershipService instance.

    def get_pending(self, guild_id: Optional[int] = None) -> list[MembershipRequest]:
        """All requests still awaiting a decision."""
        return self._filter(lambda request: request.is_pending, guild_id)

    def get_approved(self, guild_id: Optional[int] = None) -> list[MembershipRequest]:
        """All requests that were approved."""
        return self._filter(lambda request: request.status == MembershipRequestStatus.APPROVED, guild_id)

    def get_denied(self, guild_id: Optional[int] = None) -> list[MembershipRequest]:
        """All requests that were denied."""
        return self._filter(lambda request: request.status == MembershipRequestStatus.DENIED, guild_id)

    def get_by_member(self, guild_id: int, user_id: int) -> list[MembershipRequest]:
        """Every request (any status) a specific member has made in a guild,
        in creation order -- their full membership-request history.
        """
        requests = self._filter(lambda request: request.user_id == user_id, guild_id)
        return sorted(requests, key=lambda request: request.request_id)

    def _filter(self, predicate, guild_id: Optional[int]) -> list[MembershipRequest]:
        requests = self.load().requests
        if guild_id is not None:
            requests = [request for request in requests if request.guild_id == guild_id]
        return [request for request in requests if predicate(request)]

    @staticmethod
    def _serialize(request: MembershipRequest) -> dict:
        return {
            "request_id": request.request_id,
            "guild_id": request.guild_id,
            "user_id": request.user_id,
            "status": request.status.value,
            "created_at": request.created_at.isoformat(),
            "resolved_at": request.resolved_at.isoformat() if request.resolved_at else None,
            "resolved_by_user_id": request.resolved_by_user_id,
            "channel_id": request.channel_id,
            "message_id": request.message_id,
        }

    @staticmethod
    def _deserialize(entry: dict) -> MembershipRequest:
        resolved_at: Optional[datetime] = (
            datetime.fromisoformat(entry["resolved_at"]) if entry.get("resolved_at") else None
        )
        return MembershipRequest(
            request_id=entry["request_id"],
            guild_id=entry["guild_id"],
            user_id=entry["user_id"],
            status=MembershipRequestStatus(entry.get("status", "pending")),
            created_at=datetime.fromisoformat(entry["created_at"]),
            resolved_at=resolved_at,
            resolved_by_user_id=entry.get("resolved_by_user_id"),
            channel_id=entry.get("channel_id"),
            message_id=entry.get("message_id"),
        )
