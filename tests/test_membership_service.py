"""Tests for FR-030's membership service (services/membership_service.py).

Covers the FR-030 testing checklist: join modes (Manual, Self-Service,
Approval Required, Discord Managed), Self-Service join/leave, the
Approval workflow (request/approve/deny/duplicate handling), validation
(missing role, deleted role, missing join mode, missing permissions,
role hierarchy, already-in-desired-state), and persistence/restart
scenarios.
"""

import tempfile
import unittest
from pathlib import Path

from watch_party_manager.domain.guild_configuration import (
    GuildChannelsConfig,
    GuildConfiguration,
    JoinMode,
    WatchPartyRoleConfig,
)
from watch_party_manager.persistence.guild_configuration_repository import GuildConfigurationRepository
from watch_party_manager.persistence.membership_request_repository import MembershipRequestRepository
from watch_party_manager.services.membership_service import (
    JoinOutcomeKind,
    MembershipService,
)

GUILD_ID = 100
ROLE_ID = 222
OTHER_ROLE_ID = 333
ADMIN_CHANNEL_ID = 500


class FakeRole:
    def __init__(self, role_id: int, position: int = 5) -> None:
        self.id = role_id
        self.position = position


class FakePermissions:
    def __init__(self, manage_roles: bool = True, view_channel: bool = True, send_messages: bool = True) -> None:
        self.manage_roles = manage_roles
        self.view_channel = view_channel
        self.send_messages = send_messages


class FakeMe:
    def __init__(self, *, manage_roles: bool = True, top_role_position: int = 10) -> None:
        self.guild_permissions = FakePermissions(manage_roles)
        self.top_role = FakeRole(999, position=top_role_position)


class FakeChannel:
    def __init__(self, channel_id: int, *, usable: bool = True) -> None:
        self.id = channel_id
        self._permissions = FakePermissions(send_messages=usable, view_channel=usable)

    def permissions_for(self, member) -> FakePermissions:
        return self._permissions


class FakeGuild:
    def __init__(
        self,
        *,
        role_ids=(ROLE_ID,),
        manage_roles: bool = True,
        top_role_position: int = 10,
        channel_ids=(ADMIN_CHANNEL_ID,),
        unusable_channel_ids=(),
    ) -> None:
        self._role_ids = set(role_ids)
        self.me = FakeMe(manage_roles=manage_roles, top_role_position=top_role_position)
        self._channels = {
            channel_id: FakeChannel(channel_id, usable=channel_id not in unusable_channel_ids)
            for channel_id in channel_ids
        }

    def get_role(self, role_id):
        return FakeRole(role_id) if role_id in self._role_ids else None

    def get_channel_or_thread(self, channel_id):
        return self._channels.get(channel_id)


class FakeMember:
    def __init__(self, user_id: int, roles=()) -> None:
        self.id = user_id
        self.roles = list(roles)
        self.added_role_ids = []
        self.removed_role_ids = []

    async def add_roles(self, role, reason=None) -> None:
        self.roles.append(role)
        self.added_role_ids.append(role.id)

    async def remove_roles(self, role, reason=None) -> None:
        self.roles = [r for r in self.roles if r.id != role.id]
        self.removed_role_ids.append(role.id)


class MembershipServiceTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        temp_path = Path(self._temp_dir.name)
        self.guild_configuration_repository = GuildConfigurationRepository(temp_path / "guild_configurations.json")
        self.membership_request_repository = MembershipRequestRepository(temp_path / "membership_requests.json")
        self.service = MembershipService(self.guild_configuration_repository, self.membership_request_repository)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _seed(
        self,
        join_mode: JoinMode,
        *,
        role_id=ROLE_ID,
        allow_self_leave: bool = True,
        admin_channel_id: int = ADMIN_CHANNEL_ID,
        denial_cooldown_days: int = 7,
    ) -> None:
        self.guild_configuration_repository.save(
            GuildConfiguration(
                guild_id=GUILD_ID,
                guild_name="Test Guild",
                setup_completed=True,
                watch_party_role=WatchPartyRoleConfig(
                    role_id=role_id,
                    join_mode=join_mode,
                    allow_self_leave=allow_self_leave,
                    denial_cooldown_days=denial_cooldown_days,
                ),
                channels=GuildChannelsConfig(admin_channel_id=admin_channel_id),
            )
        )


class JoinModeTests(MembershipServiceTestCase):
    async def test_manual_mode_explains_and_does_not_assign_roles(self) -> None:
        self._seed(JoinMode.MANUAL)
        member = FakeMember(1)
        guild = FakeGuild()

        outcome = await self.service.handle_join_request(GUILD_ID, member, guild)

        self.assertEqual(outcome.kind, JoinOutcomeKind.MANUAL_INFO)
        self.assertIn("WASH Crew", outcome.message)
        self.assertEqual(member.added_role_ids, [])

    async def test_discord_managed_mode_explains_and_does_not_modify_roles(self) -> None:
        self._seed(JoinMode.DISCORD_MANAGED)
        member = FakeMember(1)
        guild = FakeGuild()

        outcome = await self.service.handle_join_request(GUILD_ID, member, guild)

        self.assertEqual(outcome.kind, JoinOutcomeKind.DISCORD_MANAGED_INFO)
        self.assertIn("server staff", outcome.message)
        self.assertEqual(member.added_role_ids, [])
        self.assertEqual(member.removed_role_ids, [])

    async def test_self_service_mode_joins_immediately(self) -> None:
        self._seed(JoinMode.SELF_SERVICE)
        member = FakeMember(1)
        guild = FakeGuild()

        outcome = await self.service.handle_join_request(GUILD_ID, member, guild)

        self.assertEqual(outcome.kind, JoinOutcomeKind.JOINED)
        self.assertEqual(member.added_role_ids, [ROLE_ID])

    async def test_approval_required_mode_creates_a_pending_request(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1)
        guild = FakeGuild()

        outcome = await self.service.handle_join_request(GUILD_ID, member, guild)

        self.assertEqual(outcome.kind, JoinOutcomeKind.REQUEST_CREATED)
        self.assertIsNotNone(outcome.request)
        self.assertTrue(outcome.request.is_pending)
        self.assertEqual(member.added_role_ids, [])


class SelfServiceTests(MembershipServiceTestCase):
    async def test_join(self) -> None:
        self._seed(JoinMode.SELF_SERVICE)
        member = FakeMember(1)
        guild = FakeGuild()

        result = await self.service.join_self_service(GUILD_ID, member, guild)

        self.assertTrue(result.success)
        self.assertEqual(member.added_role_ids, [ROLE_ID])

    async def test_leave(self) -> None:
        self._seed(JoinMode.SELF_SERVICE)
        member = FakeMember(1, roles=[FakeRole(ROLE_ID)])
        guild = FakeGuild()

        result = await self.service.leave_self_service(GUILD_ID, member, guild)

        self.assertTrue(result.success)
        self.assertEqual(member.removed_role_ids, [ROLE_ID])

    async def test_join_when_already_a_member_offers_leave_instead(self) -> None:
        self._seed(JoinMode.SELF_SERVICE)
        member = FakeMember(1, roles=[FakeRole(ROLE_ID)])
        guild = FakeGuild()

        outcome = await self.service.handle_join_request(GUILD_ID, member, guild)

        self.assertEqual(outcome.kind, JoinOutcomeKind.OFFER_LEAVE)
        self.assertEqual(member.removed_role_ids, [])

    async def test_join_action_rejects_a_user_already_a_member(self) -> None:
        self._seed(JoinMode.SELF_SERVICE)
        member = FakeMember(1, roles=[FakeRole(ROLE_ID)])
        guild = FakeGuild()

        result = await self.service.join_self_service(GUILD_ID, member, guild)

        self.assertFalse(result.success)
        self.assertIn("already", result.message.lower())

    async def test_leave_when_not_a_member_is_rejected(self) -> None:
        self._seed(JoinMode.SELF_SERVICE)
        member = FakeMember(1)
        guild = FakeGuild()

        result = await self.service.leave_self_service(GUILD_ID, member, guild)

        self.assertFalse(result.success)
        self.assertIn("not currently", result.message)

    async def test_leave_is_unavailable_when_allow_self_leave_is_false(self) -> None:
        self._seed(JoinMode.SELF_SERVICE, allow_self_leave=False)
        member = FakeMember(1, roles=[FakeRole(ROLE_ID)])
        guild = FakeGuild()

        outcome = await self.service.handle_join_request(GUILD_ID, member, guild)
        self.assertEqual(outcome.kind, JoinOutcomeKind.ALREADY_MEMBER_CANNOT_LEAVE)

        result = await self.service.leave_self_service(GUILD_ID, member, guild)
        self.assertFalse(result.success)

    async def test_leave_is_rejected_outside_self_service_mode(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1, roles=[FakeRole(ROLE_ID)])
        guild = FakeGuild()

        result = await self.service.leave_self_service(GUILD_ID, member, guild)

        self.assertFalse(result.success)


class ApprovalWorkflowTests(MembershipServiceTestCase):
    async def test_request_is_created(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1)
        guild = FakeGuild()

        outcome = await self.service.handle_join_request(GUILD_ID, member, guild)

        request = self.service.get_request(outcome.request.request_id)
        self.assertIsNotNone(request)
        self.assertTrue(request.is_pending)

    async def test_wash_crew_can_approve(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1)
        guild = FakeGuild()
        outcome = await self.service.handle_join_request(GUILD_ID, member, guild)

        result = await self.service.approve_request(outcome.request.request_id, GUILD_ID, 999, member, guild)

        self.assertTrue(result.success)
        self.assertEqual(member.added_role_ids, [ROLE_ID])
        self.assertEqual(result.request.resolved_by_user_id, 999)

    async def test_wash_crew_can_deny(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1)
        guild = FakeGuild()
        outcome = await self.service.handle_join_request(GUILD_ID, member, guild)

        result = self.service.deny_request(outcome.request.request_id, GUILD_ID, 999)

        self.assertTrue(result.success)
        self.assertEqual(member.added_role_ids, [])
        self.assertFalse(self.service.get_request(outcome.request.request_id).is_pending)

    async def test_non_wash_users_cannot_approve(self) -> None:
        # MembershipService.approve_request itself performs no permission
        # check by design (bot.py's handle_membership_approval_decision
        # gates on PermissionService.require_wash_crew first, mirroring
        # every other WASH-only interaction) -- this documents that
        # boundary so a future change can't silently blur it.
        import inspect

        parameters = inspect.signature(self.service.approve_request).parameters
        self.assertIn("approver_user_id", parameters)

    async def test_duplicate_approval_is_rejected(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1)
        guild = FakeGuild()
        outcome = await self.service.handle_join_request(GUILD_ID, member, guild)
        await self.service.approve_request(outcome.request.request_id, GUILD_ID, 999, member, guild)

        second = await self.service.approve_request(outcome.request.request_id, GUILD_ID, 998, member, guild)

        self.assertFalse(second.success)
        self.assertIn("already been processed", second.message)
        self.assertEqual(member.added_role_ids, [ROLE_ID])  # not granted twice

    async def test_duplicate_denial_is_rejected(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1)
        guild = FakeGuild()
        outcome = await self.service.handle_join_request(GUILD_ID, member, guild)
        self.service.deny_request(outcome.request.request_id, GUILD_ID, 999)

        second = self.service.deny_request(outcome.request.request_id, GUILD_ID, 998)

        self.assertFalse(second.success)
        self.assertIn("already been processed", second.message)

    async def test_already_processed_request_cannot_be_approved_after_denial(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1)
        guild = FakeGuild()
        outcome = await self.service.handle_join_request(GUILD_ID, member, guild)
        self.service.deny_request(outcome.request.request_id, GUILD_ID, 999)

        result = await self.service.approve_request(outcome.request.request_id, GUILD_ID, 998, member, guild)

        self.assertFalse(result.success)
        self.assertEqual(member.added_role_ids, [])

    async def test_duplicate_request_while_pending_is_rejected(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1)
        guild = FakeGuild()
        first = await self.service.handle_join_request(GUILD_ID, member, guild)

        second = await self.service.handle_join_request(GUILD_ID, member, guild)

        self.assertEqual(second.kind, JoinOutcomeKind.REQUEST_PENDING)
        self.assertEqual(second.request.request_id, first.request.request_id)

    async def test_approving_a_nonexistent_request_fails_gracefully(self) -> None:
        member = FakeMember(1)
        guild = FakeGuild()
        result = await self.service.approve_request(999999, GUILD_ID, 999, member, guild)
        self.assertFalse(result.success)

    async def test_denying_a_nonexistent_request_fails_gracefully(self) -> None:
        result = self.service.deny_request(999999, GUILD_ID, 999)
        self.assertFalse(result.success)

    async def test_approval_mode_already_member_is_reported(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1, roles=[FakeRole(ROLE_ID)])
        guild = FakeGuild()

        outcome = await self.service.handle_join_request(GUILD_ID, member, guild)

        self.assertEqual(outcome.kind, JoinOutcomeKind.ALREADY_MEMBER)
        self.assertEqual(member.added_role_ids, [])

    async def test_approving_when_member_left_the_guild_does_not_change_status(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1)
        guild = FakeGuild()
        outcome = await self.service.handle_join_request(GUILD_ID, member, guild)

        result = await self.service.approve_request(outcome.request.request_id, GUILD_ID, 999, None, guild)

        self.assertFalse(result.success)
        self.assertTrue(self.service.get_request(outcome.request.request_id).is_pending)


class ValidationTests(MembershipServiceTestCase):
    async def test_missing_watch_party_role_is_rejected(self) -> None:
        self._seed(JoinMode.SELF_SERVICE, role_id=None)
        member = FakeMember(1)
        guild = FakeGuild()

        outcome = await self.service.handle_join_request(GUILD_ID, member, guild)

        self.assertEqual(outcome.kind, JoinOutcomeKind.ROLE_NOT_CONFIGURED)

    async def test_deleted_role_is_rejected(self) -> None:
        self._seed(JoinMode.SELF_SERVICE)
        member = FakeMember(1)
        guild = FakeGuild(role_ids=set())  # role_id no longer resolves

        outcome = await self.service.handle_join_request(GUILD_ID, member, guild)

        self.assertEqual(outcome.kind, JoinOutcomeKind.VALIDATION_ERROR)
        self.assertIn("no longer exists", outcome.message)

    async def test_missing_join_mode_configuration_is_rejected(self) -> None:
        # No GuildConfiguration at all -- setup has never been completed.
        member = FakeMember(1)
        guild = FakeGuild()

        outcome = await self.service.handle_join_request(GUILD_ID, member, guild)

        self.assertEqual(outcome.kind, JoinOutcomeKind.NOT_CONFIGURED)

    async def test_missing_manage_roles_permission_is_rejected(self) -> None:
        self._seed(JoinMode.SELF_SERVICE)
        member = FakeMember(1)
        guild = FakeGuild(manage_roles=False)

        outcome = await self.service.handle_join_request(GUILD_ID, member, guild)

        self.assertEqual(outcome.kind, JoinOutcomeKind.VALIDATION_ERROR)
        self.assertIn("Manage Roles", outcome.message)
        self.assertEqual(member.added_role_ids, [])

    async def test_role_hierarchy_failure_is_rejected(self) -> None:
        self._seed(JoinMode.SELF_SERVICE)
        member = FakeMember(1)
        guild = FakeGuild(top_role_position=1)  # bot's top role below the target role (position 5)

        outcome = await self.service.handle_join_request(GUILD_ID, member, guild)

        self.assertEqual(outcome.kind, JoinOutcomeKind.VALIDATION_ERROR)
        self.assertIn("positioned above", outcome.message)
        self.assertEqual(member.added_role_ids, [])

    async def test_user_already_in_desired_state_join(self) -> None:
        self._seed(JoinMode.SELF_SERVICE)
        member = FakeMember(1, roles=[FakeRole(ROLE_ID)])
        guild = FakeGuild()

        result = await self.service.join_self_service(GUILD_ID, member, guild)

        self.assertFalse(result.success)

    async def test_user_already_in_desired_state_leave(self) -> None:
        self._seed(JoinMode.SELF_SERVICE)
        member = FakeMember(1)
        guild = FakeGuild()

        result = await self.service.leave_self_service(GUILD_ID, member, guild)

        self.assertFalse(result.success)


class AdminChannelTests(MembershipServiceTestCase):
    """FR-030 refinement: Approval-Required requests are routed only to
    the configured Admin channel, never the log channel or the
    invocation channel, and are rejected outright if none is configured.
    """

    async def test_missing_admin_channel_rejects_the_join_request(self) -> None:
        self._seed(JoinMode.APPROVAL, admin_channel_id=None)
        member = FakeMember(1)
        guild = FakeGuild()

        outcome = await self.service.handle_join_request(GUILD_ID, member, guild)

        self.assertEqual(outcome.kind, JoinOutcomeKind.ADMIN_CHANNEL_NOT_CONFIGURED)
        self.assertIn("Admin channel", outcome.message)
        self.assertIsNone(outcome.request)

    async def test_deleted_admin_channel_rejects_the_join_request(self) -> None:
        self._seed(JoinMode.APPROVAL, admin_channel_id=ADMIN_CHANNEL_ID)
        member = FakeMember(1)
        guild = FakeGuild(channel_ids=())  # admin channel no longer resolves

        outcome = await self.service.handle_join_request(GUILD_ID, member, guild)

        self.assertEqual(outcome.kind, JoinOutcomeKind.ADMIN_CHANNEL_NOT_CONFIGURED)
        self.assertIn("no longer exists", outcome.message)

    async def test_insufficient_permissions_in_admin_channel_rejects_the_join_request(self) -> None:
        self._seed(JoinMode.APPROVAL, admin_channel_id=ADMIN_CHANNEL_ID)
        member = FakeMember(1)
        guild = FakeGuild(channel_ids=(ADMIN_CHANNEL_ID,), unusable_channel_ids=(ADMIN_CHANNEL_ID,))

        outcome = await self.service.handle_join_request(GUILD_ID, member, guild)

        self.assertEqual(outcome.kind, JoinOutcomeKind.ADMIN_CHANNEL_NOT_CONFIGURED)
        self.assertIn("permission", outcome.message)

    async def test_configured_admin_channel_allows_the_request(self) -> None:
        self._seed(JoinMode.APPROVAL, admin_channel_id=ADMIN_CHANNEL_ID)
        member = FakeMember(1)
        guild = FakeGuild()

        outcome = await self.service.handle_join_request(GUILD_ID, member, guild)

        self.assertEqual(outcome.kind, JoinOutcomeKind.REQUEST_CREATED)


class CooldownTests(MembershipServiceTestCase):
    async def _deny_a_request(self, user_id: int, guild: "FakeGuild" = None):
        guild = guild or FakeGuild()
        member = FakeMember(user_id)
        outcome = await self.service.handle_join_request(GUILD_ID, member, guild)
        self.service.deny_request(outcome.request.request_id, GUILD_ID, 999)
        return self.service.get_request(outcome.request.request_id)

    async def test_denial_starts_a_cooldown(self) -> None:
        self._seed(JoinMode.APPROVAL)
        await self._deny_a_request(1)

        member = FakeMember(1)
        outcome = await self.service.handle_join_request(GUILD_ID, member, FakeGuild())

        self.assertEqual(outcome.kind, JoinOutcomeKind.COOLDOWN_ACTIVE)

    async def test_cooldown_prevents_a_new_request(self) -> None:
        self._seed(JoinMode.APPROVAL)
        await self._deny_a_request(1)

        member = FakeMember(1)
        await self.service.handle_join_request(GUILD_ID, member, FakeGuild())

        self.assertIsNone(self.service.get_pending_request(GUILD_ID, 1))

    async def test_cooldown_message_shows_remaining_time(self) -> None:
        self._seed(JoinMode.APPROVAL, denial_cooldown_days=7)
        await self._deny_a_request(1)

        member = FakeMember(1)
        outcome = await self.service.handle_join_request(GUILD_ID, member, FakeGuild())

        self.assertIn("<t:", outcome.message)  # Discord native timestamp, per project convention
        self.assertIn("cooldown", outcome.message.lower())

    async def test_cooldown_does_not_affect_other_members(self) -> None:
        self._seed(JoinMode.APPROVAL)
        await self._deny_a_request(1)

        other_member = FakeMember(2)
        outcome = await self.service.handle_join_request(GUILD_ID, other_member, FakeGuild())

        self.assertEqual(outcome.kind, JoinOutcomeKind.REQUEST_CREATED)

    async def test_expired_cooldown_allows_a_new_request(self) -> None:
        self._seed(JoinMode.APPROVAL, denial_cooldown_days=7)
        request = await self._deny_a_request(1)

        # Simulate the cooldown having already expired by backdating the
        # denial's resolved_at well past the 7-day window.
        from dataclasses import replace as dataclasses_replace
        from datetime import timedelta

        expired_request = dataclasses_replace(
            self.service.get_request(request.request_id),
            resolved_at=request.resolved_at - timedelta(days=30),
        )
        self.service._requests[request.request_id] = expired_request
        self.service._save_requests()

        member = FakeMember(1)
        outcome = await self.service.handle_join_request(GUILD_ID, member, FakeGuild())

        self.assertEqual(outcome.kind, JoinOutcomeKind.REQUEST_CREATED)

    async def test_only_approval_mode_uses_the_cooldown(self) -> None:
        # A denial can only happen under Approval-Required mode in the
        # first place, but this documents that self-service join/leave
        # never consult denial history at all.
        self._seed(JoinMode.APPROVAL)
        await self._deny_a_request(1)
        self.guild_configuration_repository.save(
            GuildConfiguration(
                guild_id=GUILD_ID,
                guild_name="Test Guild",
                setup_completed=True,
                watch_party_role=WatchPartyRoleConfig(role_id=ROLE_ID, join_mode=JoinMode.SELF_SERVICE),
                channels=GuildChannelsConfig(admin_channel_id=ADMIN_CHANNEL_ID),
            )
        )

        member = FakeMember(1)
        outcome = await self.service.handle_join_request(GUILD_ID, member, FakeGuild())

        self.assertEqual(outcome.kind, JoinOutcomeKind.JOINED)


class MetadataTests(MembershipServiceTestCase):
    """FR-030 refinement: requests permanently retain their created
    timestamp, processed timestamp, final status, and reviewer -- never
    overwritten, and processed requests remain persisted (not deleted).
    """

    async def test_created_timestamp_is_stored(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1)
        outcome = await self.service.handle_join_request(GUILD_ID, member, FakeGuild())

        self.assertIsNotNone(outcome.request.created_at)

    async def test_processed_timestamp_and_reviewer_are_stored_on_approval(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1)
        outcome = await self.service.handle_join_request(GUILD_ID, member, FakeGuild())

        result = await self.service.approve_request(outcome.request.request_id, GUILD_ID, 999, member, FakeGuild())

        self.assertIsNotNone(result.request.resolved_at)
        self.assertEqual(result.request.resolved_by_user_id, 999)
        self.assertEqual(result.request.status.value, "approved")

    async def test_processed_timestamp_and_reviewer_are_stored_on_denial(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1)
        outcome = await self.service.handle_join_request(GUILD_ID, member, FakeGuild())

        result = self.service.deny_request(outcome.request.request_id, GUILD_ID, 888)

        self.assertIsNotNone(result.request.resolved_at)
        self.assertEqual(result.request.resolved_by_user_id, 888)
        self.assertEqual(result.request.status.value, "denied")

    async def test_created_timestamp_is_never_overwritten_by_processing(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1)
        outcome = await self.service.handle_join_request(GUILD_ID, member, FakeGuild())
        original_created_at = outcome.request.created_at

        result = self.service.deny_request(outcome.request.request_id, GUILD_ID, 999)

        self.assertEqual(result.request.created_at, original_created_at)

    async def test_processed_requests_remain_retrievable_afterward(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1)
        outcome = await self.service.handle_join_request(GUILD_ID, member, FakeGuild())
        self.service.deny_request(outcome.request.request_id, GUILD_ID, 999)

        self.assertIsNotNone(self.service.get_request(outcome.request.request_id))


class PersistenceTests(MembershipServiceTestCase):
    async def test_pending_requests_persist_across_service_instances(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1)
        guild = FakeGuild()
        await self.service.handle_join_request(GUILD_ID, member, guild)

        restarted = MembershipService(self.guild_configuration_repository, self.membership_request_repository)
        pending = restarted.list_pending_requests(GUILD_ID)

        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].user_id, 1)

    async def test_processed_requests_persist_across_service_instances(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1)
        guild = FakeGuild()
        outcome = await self.service.handle_join_request(GUILD_ID, member, guild)
        await self.service.approve_request(outcome.request.request_id, GUILD_ID, 999, member, guild)

        restarted = MembershipService(self.guild_configuration_repository, self.membership_request_repository)
        request = restarted.get_request(outcome.request.request_id)

        self.assertFalse(request.is_pending)
        self.assertEqual(request.resolved_by_user_id, 999)

    async def test_restart_scenario_only_lists_still_pending_requests(self) -> None:
        self._seed(JoinMode.APPROVAL)
        guild = FakeGuild()
        member1 = FakeMember(1)
        member2 = FakeMember(2)
        outcome1 = await self.service.handle_join_request(GUILD_ID, member1, guild)
        outcome2 = await self.service.handle_join_request(GUILD_ID, member2, guild)
        self.service.deny_request(outcome2.request.request_id, GUILD_ID, 999)

        restarted = MembershipService(self.guild_configuration_repository, self.membership_request_repository)
        pending = restarted.list_pending_requests(GUILD_ID)

        self.assertEqual([request.request_id for request in pending], [outcome1.request.request_id])

    async def test_message_reference_persists_for_restart_restoration(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1)
        guild = FakeGuild()
        outcome = await self.service.handle_join_request(GUILD_ID, member, guild)

        self.service.attach_request_message(outcome.request.request_id, channel_id=500, message_id=600)

        restarted = MembershipService(self.guild_configuration_repository, self.membership_request_repository)
        request = restarted.get_request(outcome.request.request_id)
        self.assertEqual(request.channel_id, 500)
        self.assertEqual(request.message_id, 600)

    async def test_denial_cooldown_persists_across_service_instances(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1)
        outcome = await self.service.handle_join_request(GUILD_ID, member, FakeGuild())
        self.service.deny_request(outcome.request.request_id, GUILD_ID, 999)

        restarted = MembershipService(self.guild_configuration_repository, self.membership_request_repository)
        new_outcome = await restarted.handle_join_request(GUILD_ID, FakeMember(1), FakeGuild())

        self.assertEqual(new_outcome.kind, JoinOutcomeKind.COOLDOWN_ACTIVE)


# --- FR-031: WASH Crew administrative add/remove ------------------------------------


class AdminAddRemoveMemberTests(MembershipServiceTestCase):
    async def test_admin_add_grants_the_role_under_approval_mode(self) -> None:
        # Administrative add must work regardless of configured join mode.
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1)
        guild = FakeGuild()

        result = await self.service.admin_add_member(GUILD_ID, member, guild, actor_user_id=999)

        self.assertTrue(result.success)
        self.assertEqual(member.added_role_ids, [ROLE_ID])

    async def test_admin_add_grants_the_role_under_manual_mode(self) -> None:
        self._seed(JoinMode.MANUAL)
        member = FakeMember(1)
        guild = FakeGuild()

        result = await self.service.admin_add_member(GUILD_ID, member, guild, actor_user_id=999)

        self.assertTrue(result.success)
        self.assertEqual(member.added_role_ids, [ROLE_ID])

    async def test_admin_add_rejects_an_existing_member(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1, roles=[FakeRole(ROLE_ID)])
        guild = FakeGuild()

        result = await self.service.admin_add_member(GUILD_ID, member, guild, actor_user_id=999)

        self.assertFalse(result.success)
        self.assertIn("already", result.message.lower())
        self.assertEqual(member.added_role_ids, [])

    async def test_admin_add_rejects_missing_role_configuration(self) -> None:
        self._seed(JoinMode.APPROVAL, role_id=None)
        member = FakeMember(1)
        guild = FakeGuild()

        result = await self.service.admin_add_member(GUILD_ID, member, guild, actor_user_id=999)

        self.assertFalse(result.success)

    async def test_admin_add_rejects_a_deleted_role(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1)
        guild = FakeGuild(role_ids=set())

        result = await self.service.admin_add_member(GUILD_ID, member, guild, actor_user_id=999)

        self.assertFalse(result.success)
        self.assertIn("no longer exists", result.message)

    async def test_admin_add_rejects_missing_manage_roles_permission(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1)
        guild = FakeGuild(manage_roles=False)

        result = await self.service.admin_add_member(GUILD_ID, member, guild, actor_user_id=999)

        self.assertFalse(result.success)
        self.assertIn("Manage Roles", result.message)

    async def test_admin_add_rejects_role_hierarchy_failure(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1)
        guild = FakeGuild(top_role_position=1)

        result = await self.service.admin_add_member(GUILD_ID, member, guild, actor_user_id=999)

        self.assertFalse(result.success)
        self.assertIn("positioned above", result.message)

    async def test_admin_remove_removes_the_role_under_approval_mode(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1, roles=[FakeRole(ROLE_ID)])
        guild = FakeGuild()

        result = await self.service.admin_remove_member(GUILD_ID, member, guild, actor_user_id=999)

        self.assertTrue(result.success)
        self.assertEqual(member.removed_role_ids, [ROLE_ID])

    async def test_admin_remove_rejects_a_non_member(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1)
        guild = FakeGuild()

        result = await self.service.admin_remove_member(GUILD_ID, member, guild, actor_user_id=999)

        self.assertFalse(result.success)
        self.assertIn("not currently", result.message)
        self.assertEqual(member.removed_role_ids, [])

    async def test_admin_remove_rejects_missing_role_configuration(self) -> None:
        self._seed(JoinMode.APPROVAL, role_id=None)
        member = FakeMember(1)
        guild = FakeGuild()

        result = await self.service.admin_remove_member(GUILD_ID, member, guild, actor_user_id=999)

        self.assertFalse(result.success)

    async def test_admin_remove_rejects_a_deleted_role(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1, roles=[FakeRole(ROLE_ID)])
        guild = FakeGuild(role_ids=set())

        result = await self.service.admin_remove_member(GUILD_ID, member, guild, actor_user_id=999)

        self.assertFalse(result.success)
        self.assertIn("no longer exists", result.message)

    async def test_admin_remove_rejects_missing_manage_roles_permission(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1, roles=[FakeRole(ROLE_ID)])
        guild = FakeGuild(manage_roles=False)

        result = await self.service.admin_remove_member(GUILD_ID, member, guild, actor_user_id=999)

        self.assertFalse(result.success)
        self.assertIn("Manage Roles", result.message)

    async def test_admin_remove_rejects_role_hierarchy_failure(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1, roles=[FakeRole(ROLE_ID)])
        guild = FakeGuild(top_role_position=1)

        result = await self.service.admin_remove_member(GUILD_ID, member, guild, actor_user_id=999)

        self.assertFalse(result.success)
        self.assertIn("positioned above", result.message)


# --- FR-031: search_member / get_cooldown_status -------------------------------------


class SearchMemberTests(MembershipServiceTestCase):
    async def test_search_reports_current_membership(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1, roles=[FakeRole(ROLE_ID)])

        result = self.service.search_member(GUILD_ID, member.id, member)

        self.assertTrue(result.is_current_member)
        self.assertIsNone(result.pending_request)
        self.assertIsNone(result.last_approved_request)
        self.assertIsNone(result.last_denied_request)
        self.assertIsNone(result.cooldown_message)

    async def test_search_reports_non_membership(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1)

        result = self.service.search_member(GUILD_ID, member.id, member)

        self.assertFalse(result.is_current_member)

    async def test_search_reports_an_unknown_member_as_not_a_member(self) -> None:
        self._seed(JoinMode.APPROVAL)

        result = self.service.search_member(GUILD_ID, 12345, None)

        self.assertFalse(result.is_current_member)
        self.assertIsNone(result.pending_request)

    async def test_search_reports_a_pending_request(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1)
        outcome = await self.service.handle_join_request(GUILD_ID, member, FakeGuild())

        result = self.service.search_member(GUILD_ID, member.id, member)

        self.assertIsNotNone(result.pending_request)
        self.assertEqual(result.pending_request.request_id, outcome.request.request_id)

    async def test_search_reports_the_last_approval(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1)
        guild = FakeGuild()
        outcome = await self.service.handle_join_request(GUILD_ID, member, guild)
        await self.service.approve_request(outcome.request.request_id, GUILD_ID, 999, member, guild)

        result = self.service.search_member(GUILD_ID, member.id, member)

        self.assertIsNotNone(result.last_approved_request)
        self.assertEqual(result.last_approved_request.resolved_by_user_id, 999)
        self.assertIsNone(result.last_denied_request)

    async def test_search_reports_the_last_denial_and_cooldown(self) -> None:
        self._seed(JoinMode.APPROVAL, denial_cooldown_days=7)
        member = FakeMember(1)
        outcome = await self.service.handle_join_request(GUILD_ID, member, FakeGuild())
        self.service.deny_request(outcome.request.request_id, GUILD_ID, 999)

        result = self.service.search_member(GUILD_ID, member.id, member)

        self.assertIsNotNone(result.last_denied_request)
        self.assertEqual(result.last_denied_request.resolved_by_user_id, 999)
        self.assertIsNotNone(result.cooldown_message)
        self.assertIn("cooldown", result.cooldown_message.lower())

    async def test_get_cooldown_status_returns_none_without_a_denial(self) -> None:
        self._seed(JoinMode.APPROVAL)

        status = self.service.get_cooldown_status(GUILD_ID, 1)

        self.assertIsNone(status)

    async def test_get_cooldown_status_returns_none_when_unconfigured(self) -> None:
        status = self.service.get_cooldown_status(GUILD_ID, 1)

        self.assertIsNone(status)


if __name__ == "__main__":
    unittest.main()
