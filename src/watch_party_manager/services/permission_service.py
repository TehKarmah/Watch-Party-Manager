"""Role-based permission helpers for WASH commands and interactions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PermissionCheck:
    """Result of a role-based permission check."""

    allowed: bool
    message: str = ""


class PermissionService:
    """Centralize WASH Crew and Watch Party member role checks.

    WASH Crew inherits Watch Party member permissions. All checks fail closed
    when the relevant role is not configured.
    """

    def __init__(
        self,
        *,
        watch_party_member_role_id: Optional[int],
        wash_crew_role_id: Optional[int],
    ) -> None:
        self.watch_party_member_role_id = watch_party_member_role_id
        self.wash_crew_role_id = wash_crew_role_id

    @staticmethod
    def _has_role(user: object, role_id: Optional[int]) -> bool:
        if role_id is None:
            return False
        roles = getattr(user, "roles", [])
        return any(getattr(role, "id", None) == role_id for role in roles)

    def is_wash_crew(self, user: object) -> bool:
        return self._has_role(user, self.wash_crew_role_id)

    def is_watch_party_member(self, user: object) -> bool:
        return self.is_wash_crew(user) or self._has_role(
            user, self.watch_party_member_role_id
        )

    def require_wash_crew(self, user: object) -> PermissionCheck:
        if self.wash_crew_role_id is None:
            return PermissionCheck(
                False,
                "Set WASH_CREW_ROLE_ID before using this command.",
            )
        if not self.is_wash_crew(user):
            return PermissionCheck(
                False,
                "You need the WASH Crew role to use this command.",
            )
        return PermissionCheck(True)

    def require_watch_party_member(self, user: object) -> PermissionCheck:
        if self.watch_party_member_role_id is None and self.wash_crew_role_id is None:
            return PermissionCheck(
                False,
                "Set WATCH_PARTY_MEMBER_ROLE_ID or WASH_CREW_ROLE_ID before using this command.",
            )
        if not self.is_watch_party_member(user):
            return PermissionCheck(
                False,
                "You need the Watch Party member role to use this command.",
            )
        return PermissionCheck(True)
