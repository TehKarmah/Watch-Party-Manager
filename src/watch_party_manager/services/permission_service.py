"""Role-based permission helpers for WASH commands and interactions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from watch_party_manager.domain.guild_configuration import GuildConfiguration
from watch_party_manager.persistence.guild_configuration_repository import (
    GuildConfigurationRepository,
)


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


class GuildConfigurationSource(Protocol):
    """The subset of GuildConfigurationRepository resolve_permission_service
    needs -- Protocol-typed (matching CollectionEligibilityService's/
    StatisticsService's own minimal-source convention) so tests can supply
    a duck-typed fake instead of a real, JSON-backed repository.
    """

    def get(self, guild_id: int) -> Optional[GuildConfiguration]: ...


def resolve_permission_service(
    guild_id: Optional[int],
    guild_configuration_repository: Optional[GuildConfigurationSource] = None,
) -> PermissionService:
    """Build a PermissionService scoped to one guild's own configured roles.

    Multi-Guild Isolation, Phase 3a -- Guild-Scoped Permission Resolver.
    This is new infrastructure only: nothing below migrates an existing
    call site to use it (see WatchPartyBot.permission_service in bot.py,
    still the shared singleton every command handler reads today).

    Root cause this exists to eventually replace: WatchPartyBot.__init__
    builds exactly one shared PermissionService for the entire process,
    from WASH_CREW_ROLE_ID / WATCH_PARTY_MEMBER_ROLE_ID -- global
    environment variables parsed once in bot.main() at startup -- and
    stores it as self.permission_service. Every command handler, in
    every guild the bot is installed in, reads that same instance.
    GuildConfiguration already stores role IDs per guild
    (wash_crew_role_id, watch_party_role.role_id -- see
    domain/guild_configuration.py), saved independently by each guild's
    own /setup or /config, but nothing in permission checking reads it:
    WatchPartyBot.apply_role_configuration() (called at startup for
    self.guild_id -- the single DISCORD_GUILD_ID dev guild -- and again
    right after /setup or a /config role change) mutates
    self.permission_service.wash_crew_role_id /
    .watch_party_member_role_id *in place* on that one shared object.
    Because it is the same object every guild's commands check against,
    the most recent guild to trigger that mutation effectively sets the
    role IDs for every other guild too -- a bot actually installed
    across multiple guilds has no way for two guilds to have different
    WASH Crew/Watch Party roles simultaneously. This is the architectural
    cross-guild permission bleed Phase 3b's call-site migration will fix
    by replacing reads of the shared singleton with calls to this
    resolver instead.

    Deliberately stateless and side-effect free: no caching, no module-
    level or instance state kept anywhere, nothing memoized across
    calls -- guild_configuration_repository is consulted fresh on every
    call (matching GuildConfigurationRepository.get()'s own no-cache,
    read-from-disk-each-call behavior), and each call returns a brand
    new PermissionService instance, never a shared or previously
    returned one. Guild A and Guild B, resolved back-to-back, never
    share role IDs or object identity; two calls for the same guild_id
    are behaviorally equivalent but never the same object.

    Args:
        guild_id: The Discord guild to resolve roles for. None (e.g. a
            caller with no guild context) resolves the same way as a
            guild with no saved GuildConfiguration: an unconfigured
            PermissionService (both role IDs None), which fails closed
            for every check exactly like the existing singleton does
            today when its own environment variables are unset.
        guild_configuration_repository: Where the guild's own role
            configuration is read from. Protocol-typed so a test-only
            fake can be supplied without a real JSON-backed repository.
            Defaults to a fresh, default-path GuildConfigurationRepository
            when omitted, matching this project's established
            "optional dependency with a sensible default" convention
            (see StatisticsService's own vote_source default).

    Returns:
        A new PermissionService configured with that guild's own
        wash_crew_role_id and watch_party_role.role_id.
    """
    if guild_configuration_repository is None:
        guild_configuration_repository = GuildConfigurationRepository()

    configuration = guild_configuration_repository.get(guild_id) if guild_id is not None else None
    wash_crew_role_id = configuration.wash_crew_role_id if configuration is not None else None
    watch_party_member_role_id = (
        configuration.watch_party_role.role_id if configuration is not None else None
    )

    return PermissionService(
        watch_party_member_role_id=watch_party_member_role_id,
        wash_crew_role_id=wash_crew_role_id,
    )
