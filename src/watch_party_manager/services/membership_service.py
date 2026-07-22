"""Core logic for FR-030's Watch Party membership workflow.

Brings the configured Watch Party Join Mode (see
domain/guild_configuration.py's JoinMode) to life behind a single
/join_watch_party command. Kept free of discord.ui objects -- the
Discord layer (membership_view.py, and the command wiring in bot.py)
only renders whatever this service decides and forwards clicks back into
it. Never redesigns GuildConfiguration, WatchPartyRoleConfig, or
PermissionService; it only reads the already-configured role_id/join_mode
and, for Approval-Required mode, persists MembershipRequest records
through the same repository-backed pattern SuggestionService already
uses for its own collections.

Role mutations (adding/removing the Watch Party role) are performed
directly by this service's join/leave/approve methods -- mirroring how
the scheduler's job handlers perform their own Discord I/O (e.g.
VoteReminderJobHandler.execute() calling channel.send() directly) rather
than returning an intent for bot.py to act on. This keeps every
membership rule (including the actual role change) in one place that's
fully unit-testable with fake Members/Guilds, instead of splitting "what
should happen" and "make it happen" across two files.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, List, Optional, Protocol

from watch_party_manager.domain.guild_configuration import JoinMode, WatchPartyRoleConfig
from watch_party_manager.domain.membership_request import MembershipRequest, MembershipRequestStatus
from watch_party_manager.persistence.guild_configuration_repository import GuildConfigurationRepository
from watch_party_manager.persistence.membership_request_repository import MembershipRequestRepository
from watch_party_manager.services.configuration_validation import (
    GuildLookup,
    validate_channel_usable,
    validate_role_mutable,
)
from watch_party_manager.services.discord_timestamp_formatter import format_datetime_for_display

WATCH_PARTY_ROLE_LABEL = "Watch Party role"
ADMIN_CHANNEL_LABEL = "Admin channel"


class MemberLike(Protocol):
    """Duck-typed subset of a discord.Member this service mutates roles on."""

    id: int
    roles: List[Any]

    async def add_roles(self, role: Any, *, reason: Optional[str] = None) -> None: ...

    async def remove_roles(self, role: Any, *, reason: Optional[str] = None) -> None: ...


class JoinOutcomeKind(str, Enum):
    """What /join_watch_party should show, decided by handle_join_request()."""

    NOT_CONFIGURED = "not_configured"
    ROLE_NOT_CONFIGURED = "role_not_configured"
    MANUAL_INFO = "manual_info"
    DISCORD_MANAGED_INFO = "discord_managed_info"
    JOINED = "joined"
    OFFER_LEAVE = "offer_leave"
    ALREADY_MEMBER_CANNOT_LEAVE = "already_member_cannot_leave"
    ALREADY_MEMBER = "already_member"
    REQUEST_PENDING = "request_pending"
    REQUEST_CREATED = "request_created"
    ADMIN_CHANNEL_NOT_CONFIGURED = "admin_channel_not_configured"
    COOLDOWN_ACTIVE = "cooldown_active"
    VALIDATION_ERROR = "validation_error"


@dataclass(frozen=True, slots=True)
class JoinRequestOutcome:
    """The result of one /join_watch_party invocation."""

    kind: JoinOutcomeKind
    message: str
    request: Optional[MembershipRequest] = None


@dataclass(frozen=True, slots=True)
class MembershipActionResult:
    """The result of an explicit join or leave action."""

    success: bool
    message: str


@dataclass(frozen=True, slots=True)
class MembershipApprovalResult:
    """The result of WASH Crew approving or denying a pending request."""

    success: bool
    message: str
    request: Optional[MembershipRequest] = None


class MembershipService:
    """Orchestrates FR-030's membership workflow: join-mode-aware
    join/leave, Approval-Required request tracking, and the shared
    validation both paths need.
    """

    def __init__(
        self,
        guild_configuration_repository: GuildConfigurationRepository,
        membership_request_repository: MembershipRequestRepository,
    ) -> None:
        self._guild_configuration_repository = guild_configuration_repository
        self._membership_request_repository = membership_request_repository
        self._requests: dict[int, MembershipRequest] = {}
        load_result = self._membership_request_repository.load()
        for request in load_result.requests:
            self._requests[request.request_id] = request
        self._next_request_id = load_result.next_id

    # --- Reading configuration -------------------------------------------------------

    def get_role_config(self, guild_id: int) -> Optional[WatchPartyRoleConfig]:
        """Return the guild's Watch Party role configuration, or None if
        setup has never been completed for this guild.
        """
        configuration = self._guild_configuration_repository.get(guild_id)
        if configuration is None:
            return None
        return configuration.watch_party_role

    @staticmethod
    def is_current_member(member: MemberLike, role_id: int) -> bool:
        """Whether `member` currently holds the configured Watch Party role."""
        return any(getattr(role, "id", None) == role_id for role in getattr(member, "roles", []))

    def get_pending_request(self, guild_id: int, user_id: int) -> Optional[MembershipRequest]:
        """Return the member's existing pending request, if any."""
        for request in self._requests.values():
            if request.guild_id == guild_id and request.user_id == user_id and request.is_pending:
                return request
        return None

    def get_request(self, request_id: int) -> Optional[MembershipRequest]:
        return self._requests.get(request_id)

    def list_pending_requests(self, guild_id: Optional[int] = None) -> List[MembershipRequest]:
        """All still-pending requests, in creation order -- used to
        re-register persistent Approve/Deny views after a bot restart.
        """
        requests = [request for request in self._requests.values() if request.is_pending]
        if guild_id is not None:
            requests = [request for request in requests if request.guild_id == guild_id]
        return sorted(requests, key=lambda request: request.request_id)

    # --- Top-level dispatch ------------------------------------------------------------

    async def handle_join_request(
        self, guild_id: int, member: MemberLike, guild: GuildLookup
    ) -> JoinRequestOutcome:
        """Decide what /join_watch_party should do, branching on the
        guild's configured Join Mode.

        Performs the actual role grant immediately for Self-Service
        (when the member isn't already one, delegating to
        join_self_service() so the two never validate differently) and
        creates a persisted request for Approval-Required. Leaving is
        never performed here -- OFFER_LEAVE only signals that bot.py
        should show a confirmation prompt; the removal itself happens via
        leave_self_service() once the member confirms.
        """
        role_config = self.get_role_config(guild_id)
        if role_config is None:
            return JoinRequestOutcome(
                JoinOutcomeKind.NOT_CONFIGURED,
                "Watch Party membership hasn't been configured for this server yet. "
                "Contact a WASH Crew member.",
            )

        if role_config.join_mode is JoinMode.MANUAL:
            return JoinRequestOutcome(JoinOutcomeKind.MANUAL_INFO, self.describe_manual_mode())

        if role_config.join_mode is JoinMode.DISCORD_MANAGED:
            return JoinRequestOutcome(JoinOutcomeKind.DISCORD_MANAGED_INFO, self.describe_discord_managed_mode())

        if role_config.role_id is None:
            return JoinRequestOutcome(
                JoinOutcomeKind.ROLE_NOT_CONFIGURED,
                "The Watch Party role hasn't been configured for this server yet. "
                "Contact a WASH Crew member.",
            )

        if role_config.join_mode is JoinMode.SELF_SERVICE:
            return await self._handle_self_service_request(guild_id, role_config, member, guild)

        return self._handle_approval_request(guild_id, role_config, member, guild)

    async def _handle_self_service_request(
        self, guild_id: int, role_config: WatchPartyRoleConfig, member: MemberLike, guild: GuildLookup
    ) -> JoinRequestOutcome:
        if self.is_current_member(member, role_config.role_id):
            if not role_config.allow_self_leave:
                return JoinRequestOutcome(
                    JoinOutcomeKind.ALREADY_MEMBER_CANNOT_LEAVE,
                    "You're already a Watch Party member. Leaving isn't available for this server.",
                )
            return JoinRequestOutcome(
                JoinOutcomeKind.OFFER_LEAVE,
                "You're already a Watch Party member. Would you like to leave?",
            )

        result = await self.join_self_service(guild_id, member, guild)
        if not result.success:
            return JoinRequestOutcome(JoinOutcomeKind.VALIDATION_ERROR, result.message)
        return JoinRequestOutcome(JoinOutcomeKind.JOINED, result.message)

    def _handle_approval_request(
        self, guild_id: int, role_config: WatchPartyRoleConfig, member: MemberLike, guild: GuildLookup
    ) -> JoinRequestOutcome:
        if self.is_current_member(member, role_config.role_id):
            return JoinRequestOutcome(JoinOutcomeKind.ALREADY_MEMBER, "You're already a Watch Party member.")

        existing = self.get_pending_request(guild_id, member.id)
        if existing is not None:
            return JoinRequestOutcome(
                JoinOutcomeKind.REQUEST_PENDING,
                "Your request to join the Watch Party is already pending WASH Crew review.",
                request=existing,
            )

        cooldown_message = self._describe_active_cooldown(guild_id, member.id, role_config.denial_cooldown_days)
        if cooldown_message is not None:
            return JoinRequestOutcome(JoinOutcomeKind.COOLDOWN_ACTIVE, cooldown_message)

        admin_channel_error = self._validate_admin_channel(guild_id, guild)
        if admin_channel_error is not None:
            return JoinRequestOutcome(JoinOutcomeKind.ADMIN_CHANNEL_NOT_CONFIGURED, admin_channel_error)

        request = self._create_request(guild_id, member.id)
        return JoinRequestOutcome(
            JoinOutcomeKind.REQUEST_CREATED,
            "Your request to join the Watch Party has been sent to WASH Crew for review.",
            request=request,
        )

    def _validate_admin_channel(self, guild_id: int, guild: GuildLookup) -> Optional[str]:
        """Confirm Approval-Required mode has a usable Admin channel configured.

        Never falls back to another channel (e.g. the log channel, or
        wherever /join_watch_party was invoked) -- Approval-Required
        requests are only ever posted to the configured Admin channel, so
        an unconfigured or no-longer-usable one must block the request
        outright rather than silently posting somewhere else.
        """
        guild_configuration = self._guild_configuration_repository.get(guild_id)
        admin_channel_id = guild_configuration.channels.admin_channel_id if guild_configuration else None
        if admin_channel_id is None:
            return (
                "Approval-Required join requests need an Admin channel configured first. "
                "Ask WASH Crew to set one via `/config`."
            )
        return validate_channel_usable(admin_channel_id, guild, resource_label=ADMIN_CHANNEL_LABEL)

    def _describe_active_cooldown(self, guild_id: int, user_id: int, cooldown_days: int) -> Optional[str]:
        """Return a message if this member's most recent denial is still
        within its cooldown period, otherwise None.

        Reuses the request history already tracked in self._requests --
        no separate "last denied at" field is needed since a denied
        MembershipRequest's own resolved_at already records it.
        """
        denied_requests = [
            request
            for request in self._requests.values()
            if request.guild_id == guild_id
            and request.user_id == user_id
            and request.status is MembershipRequestStatus.DENIED
            and request.resolved_at is not None
        ]
        if not denied_requests:
            return None

        last_denied_at = max(request.resolved_at for request in denied_requests)
        cooldown_expires_at = last_denied_at + timedelta(days=cooldown_days)
        if datetime.now(timezone.utc) >= cooldown_expires_at:
            return None

        return (
            "Your previous request to join the Watch Party was denied and is still in its cooldown period. "
            f"You may request again {format_datetime_for_display(cooldown_expires_at)}."
        )

    # --- Informational modes --------------------------------------------------------

    @staticmethod
    def describe_manual_mode() -> str:
        return (
            "Watch Party membership is managed manually for this server. "
            "Contact a WASH Crew member to be added."
        )

    @staticmethod
    def describe_discord_managed_mode() -> str:
        return (
            "Watch Party membership is managed directly by this Discord server. "
            "Contact the server staff for access."
        )

    # --- Self-Service join/leave ----------------------------------------------------

    async def join_self_service(
        self, guild_id: int, member: MemberLike, guild: GuildLookup
    ) -> MembershipActionResult:
        """Grant the configured Watch Party role to `member`.

        Re-validates from scratch (role configured, still exists, WASH
        can assign it, member not already in it) rather than trusting an
        earlier handle_join_request() call, since Discord state can
        change between the two.
        """
        role_config = self.get_role_config(guild_id)
        if role_config is None or role_config.role_id is None:
            return MembershipActionResult(False, "The Watch Party role hasn't been configured for this server.")

        if self.is_current_member(member, role_config.role_id):
            return MembershipActionResult(False, "You're already a Watch Party member.")

        error = validate_role_mutable(role_config.role_id, guild, resource_label=WATCH_PARTY_ROLE_LABEL)
        if error:
            return MembershipActionResult(False, error)

        role = guild.get_role(role_config.role_id)
        await member.add_roles(role, reason="WASH: /join_watch_party (self-service)")
        return MembershipActionResult(True, "You've joined the Watch Party! Welcome aboard.")

    async def leave_self_service(
        self, guild_id: int, member: MemberLike, guild: GuildLookup
    ) -> MembershipActionResult:
        """Remove the configured Watch Party role from `member`."""
        role_config = self.get_role_config(guild_id)
        if role_config is None or role_config.role_id is None:
            return MembershipActionResult(False, "The Watch Party role hasn't been configured for this server.")

        if role_config.join_mode is not JoinMode.SELF_SERVICE:
            return MembershipActionResult(False, "Leaving isn't available under the current join mode.")

        if not role_config.allow_self_leave:
            return MembershipActionResult(False, "Leaving isn't available for this server.")

        if not self.is_current_member(member, role_config.role_id):
            return MembershipActionResult(False, "You're not currently a Watch Party member.")

        error = validate_role_mutable(role_config.role_id, guild, resource_label=WATCH_PARTY_ROLE_LABEL)
        if error:
            return MembershipActionResult(False, error)

        role = guild.get_role(role_config.role_id)
        await member.remove_roles(role, reason="WASH: /join_watch_party (self-service leave)")
        return MembershipActionResult(True, "You've left the Watch Party.")

    # --- Approval-Required workflow --------------------------------------------------

    def _create_request(self, guild_id: int, user_id: int) -> MembershipRequest:
        request = MembershipRequest(request_id=self._next_request_id, guild_id=guild_id, user_id=user_id)
        self._requests[request.request_id] = request
        self._next_request_id += 1
        self._save_requests()
        return request

    def attach_request_message(self, request_id: int, channel_id: int, message_id: int) -> None:
        """Record which Discord message the request's Approve/Deny buttons
        live on, so a persistent view can be re-registered after a
        restart (see bot.py's restore_persistent_membership_approval_views).
        """
        request = self._requests.get(request_id)
        if request is None:
            return
        self._requests[request_id] = replace(request, channel_id=channel_id, message_id=message_id)
        self._save_requests()

    async def approve_request(
        self,
        request_id: int,
        guild_id: int,
        approver_user_id: int,
        member: Optional[MemberLike],
        guild: GuildLookup,
    ) -> MembershipApprovalResult:
        """Approve a pending request: grant the role, then mark it resolved.

        Args:
            member: The requesting member's live Member object, or None
                if they're no longer in the guild (the role can't be
                granted, so approval is refused without changing the
                request's status -- it stays pending in case they rejoin).
        """
        request = self._requests.get(request_id)
        if request is None:
            return MembershipApprovalResult(False, "That request no longer exists.")
        if request.guild_id != guild_id:
            return MembershipApprovalResult(False, "That request no longer exists.")
        if not request.is_pending:
            return MembershipApprovalResult(False, "This request has already been processed.", request=request)

        role_config = self.get_role_config(guild_id)
        if role_config is None or role_config.role_id is None:
            return MembershipApprovalResult(False, "The Watch Party role hasn't been configured for this server.")

        if member is None:
            return MembershipApprovalResult(False, "That member is no longer in this server.")

        error = validate_role_mutable(role_config.role_id, guild, resource_label=WATCH_PARTY_ROLE_LABEL)
        if error:
            return MembershipApprovalResult(False, error)

        role = guild.get_role(role_config.role_id)
        await member.add_roles(role, reason=f"WASH: membership request {request_id} approved")

        resolved = replace(
            request,
            status=MembershipRequestStatus.APPROVED,
            resolved_at=datetime.now(timezone.utc),
            resolved_by_user_id=approver_user_id,
        )
        self._requests[request_id] = resolved
        self._save_requests()
        return MembershipApprovalResult(True, "Request approved.", request=resolved)

    def deny_request(self, request_id: int, guild_id: int, approver_user_id: int) -> MembershipApprovalResult:
        """Deny a pending request. No role change; the requester is simply notified."""
        request = self._requests.get(request_id)
        if request is None:
            return MembershipApprovalResult(False, "That request no longer exists.")
        if request.guild_id != guild_id:
            return MembershipApprovalResult(False, "That request no longer exists.")
        if not request.is_pending:
            return MembershipApprovalResult(False, "This request has already been processed.", request=request)

        resolved = replace(
            request,
            status=MembershipRequestStatus.DENIED,
            resolved_at=datetime.now(timezone.utc),
            resolved_by_user_id=approver_user_id,
        )
        self._requests[request_id] = resolved
        self._save_requests()
        return MembershipApprovalResult(True, "Request denied.", request=resolved)

    def _save_requests(self) -> None:
        self._membership_request_repository.save(self._requests.values(), self._next_request_id)
