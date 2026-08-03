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
    when the relevant role is not configured -- except the Discord server
    owner, who always has Watch Party member-level access (First-Time UX
    Polish: the owner should never need to assign themselves the configured
    role just to add suggestions, browse, vote, or participate). This is
    member-level only -- the owner still needs the configured WASH Crew
    role for administrative commands, exactly like everyone else.
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

    @staticmethod
    def _is_guild_owner(user: object) -> bool:
        """True when `user` is the owner of its own guild.

        Reads guild.owner_id off the member object itself (discord.Member
        already exposes .guild), so no extra parameter is needed at any
        existing call site.
        """
        guild = getattr(user, "guild", None)
        owner_id = getattr(guild, "owner_id", None)
        return owner_id is not None and owner_id == getattr(user, "id", None)

    def is_wash_crew(self, user: object) -> bool:
        return self._has_role(user, self.wash_crew_role_id)

    def is_watch_party_member(self, user: object) -> bool:
        return (
            self._is_guild_owner(user)
            or self.is_wash_crew(user)
            or self._has_role(user, self.watch_party_member_role_id)
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
        if self._is_guild_owner(user):
            return PermissionCheck(True)
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
