"""Tests for FR-030's /join_watch_party wiring in bot.py.

Covers the pure/testable pieces bot.py adds: the async handlers
handle_join_watch_party / handle_membership_approval_decision, exercised
with fake interactions instead of a live Discord connection -- mirroring
test_setup_command.py/test_config_command.py's FakeInteraction/
FakeResponse pattern.

FR-030 refinement: Approval-Required requests are now routed only to the
guild's configured Admin channel (never the log channel, never a
fallback to wherever /join_watch_party was invoked) -- see
MembershipService._validate_admin_channel, which rejects the join
request outright if no Admin channel is configured or usable.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from watch_party_manager.bot import (
    handle_join_watch_party,
    handle_membership_approval_decision,
    restore_persistent_membership_approval_views,
)
from watch_party_manager.domain.guild_configuration import (
    GuildChannelsConfig,
    GuildConfiguration,
    JoinMode,
    WatchPartyRoleConfig,
)
from watch_party_manager.membership_view import MembershipApprovalView
from watch_party_manager.persistence.guild_configuration_repository import GuildConfigurationRepository
from watch_party_manager.persistence.membership_request_repository import MembershipRequestRepository
from watch_party_manager.services.membership_service import MembershipService
from watch_party_manager.services.permission_service import PermissionService

GUILD_ID = 100
ROLE_ID = 222
WASH_CREW_ROLE_ID = 999
ADMIN_CHANNEL_ID = 500


class FakeRole:
    def __init__(self, role_id: int, position: int = 5) -> None:
        self.id = role_id
        self.position = position


class FakePermissions:
    def __init__(self, manage_roles: bool = True) -> None:
        self.manage_roles = manage_roles


class FakeMe:
    def __init__(self, manage_roles: bool = True, top_role_position: int = 10) -> None:
        self.guild_permissions = FakePermissions(manage_roles)
        self.top_role = FakeRole(998, position=top_role_position)


class FakeMember:
    def __init__(self, user_id: int, roles=()) -> None:
        self.id = user_id
        self.roles = list(roles)
        self.mention = f"<@{user_id}>"
        self.added_role_ids = []
        self.removed_role_ids = []

    async def add_roles(self, role, reason=None) -> None:
        self.roles.append(role)
        self.added_role_ids.append(role.id)

    async def remove_roles(self, role, reason=None) -> None:
        self.roles = [r for r in self.roles if r.id != role.id]
        self.removed_role_ids.append(role.id)


class FakeChannelPermissions:
    def __init__(self, usable: bool = True) -> None:
        self.view_channel = usable
        self.send_messages = usable


class FakeGuildChannel:
    def __init__(self, channel_id: int, *, usable: bool = True) -> None:
        self.id = channel_id
        self._usable = usable

    def permissions_for(self, member) -> FakeChannelPermissions:
        return FakeChannelPermissions(self._usable)


class FakeGuild:
    def __init__(
        self,
        *,
        role_ids=(ROLE_ID,),
        members=None,
        manage_roles: bool = True,
        channel_ids=(ADMIN_CHANNEL_ID,),
        unusable_channel_ids=(),
    ) -> None:
        self._role_ids = set(role_ids)
        self.me = FakeMe(manage_roles=manage_roles)
        self._members = members or {}
        self._channels = {
            channel_id: FakeGuildChannel(channel_id, usable=channel_id not in unusable_channel_ids)
            for channel_id in channel_ids
        }

    def get_role(self, role_id):
        return FakeRole(role_id) if role_id in self._role_ids else None

    def get_channel_or_thread(self, channel_id):
        return self._channels.get(channel_id)

    def get_member(self, user_id):
        return self._members.get(user_id)

    async def fetch_member(self, user_id):
        member = self._members.get(user_id)
        if member is None:
            raise RuntimeError("member not found")
        return member


class FakeMessage:
    def __init__(self, message_id: int = 300) -> None:
        self.id = message_id
        self.edited_content = None
        self.edited_view = "not-edited"

    async def edit(self, content=None, view="not-edited") -> None:
        self.edited_content = content
        self.edited_view = view


class FakeChannel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.sent = []
        self._next_message_id = 300

    async def send(self, content=None, view=None):
        self.sent.append((content, view))
        message = FakeMessage(self._next_message_id)
        self._next_message_id += 1
        return message


class FakeFollowup:
    def __init__(self) -> None:
        self.sent = []

    async def send(self, content, ephemeral=False) -> None:
        self.sent.append((content, ephemeral))


class FakeResponse:
    def __init__(self) -> None:
        self.sent_message = None
        self.sent_ephemeral = None
        self.sent_view = None

    async def send_message(self, content, ephemeral=False, view=None) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral
        self.sent_view = view


class FakeInteraction:
    def __init__(self, user=None, guild=None, guild_id=GUILD_ID, channel_id=200) -> None:
        self.user = user if user is not None else FakeMember(1)
        self.guild = guild if guild is not None else FakeGuild()
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.message = FakeMessage()


class FakeBot:
    def __init__(self, membership_service, guild_configuration_repository, wash_crew_role_id=WASH_CREW_ROLE_ID) -> None:
        self.membership_service = membership_service
        self.guild_configuration_repository = guild_configuration_repository
        self.permission_service = PermissionService(
            watch_party_member_role_id=None, wash_crew_role_id=wash_crew_role_id
        )
        self.wash_crew_role_id = wash_crew_role_id
        self._channels: dict[int, FakeChannel] = {}
        self.added_views = []

    def register_channel(self, channel: FakeChannel) -> None:
        self._channels[channel.id] = channel

    def get_channel(self, channel_id):
        return self._channels.get(channel_id)

    async def fetch_channel(self, channel_id):
        channel = self._channels.get(channel_id)
        if channel is None:
            raise RuntimeError("channel not found")
        return channel

    def add_view(self, view, message_id=None) -> None:
        self.added_views.append((view, message_id))


class MembershipCommandTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        temp_path = Path(self._temp_dir.name)
        self.guild_configuration_repository = GuildConfigurationRepository(temp_path / "guild_configurations.json")
        self.membership_request_repository = MembershipRequestRepository(temp_path / "membership_requests.json")
        self.membership_service = MembershipService(
            self.guild_configuration_repository, self.membership_request_repository
        )
        self.bot = FakeBot(self.membership_service, self.guild_configuration_repository)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _seed(
        self, join_mode: JoinMode, *, role_id=ROLE_ID, allow_self_leave: bool = True, admin_channel_id=ADMIN_CHANNEL_ID
    ) -> None:
        self.guild_configuration_repository.save(
            GuildConfiguration(
                guild_id=GUILD_ID,
                guild_name="Test Guild",
                setup_completed=True,
                watch_party_role=WatchPartyRoleConfig(
                    role_id=role_id, join_mode=join_mode, allow_self_leave=allow_self_leave
                ),
                channels=GuildChannelsConfig(admin_channel_id=admin_channel_id),
            )
        )


class HandleJoinWatchPartyPermissionTests(MembershipCommandTestCase):
    """FR-030: Everyone can execute /join_watch_party -- no role required."""

    async def test_unprivileged_user_can_join_under_self_service(self) -> None:
        self._seed(JoinMode.SELF_SERVICE)
        member = FakeMember(1, roles=[])  # no roles whatsoever
        interaction = FakeInteraction(user=member)

        await handle_join_watch_party(interaction, self.bot)

        self.assertIn("joined", interaction.response.sent_message.lower())
        self.assertEqual(member.added_role_ids, [ROLE_ID])

    async def test_unprivileged_user_gets_manual_mode_info(self) -> None:
        self._seed(JoinMode.MANUAL)
        interaction = FakeInteraction(user=FakeMember(1, roles=[]))

        await handle_join_watch_party(interaction, self.bot)

        self.assertIn("WASH Crew", interaction.response.sent_message)
        self.assertTrue(interaction.response.sent_ephemeral)


class HandleJoinWatchPartyModeTests(MembershipCommandTestCase):
    async def test_requires_a_server(self) -> None:
        interaction = FakeInteraction(guild_id=None)
        interaction.guild = None

        await handle_join_watch_party(interaction, self.bot)

        self.assertIn("server", interaction.response.sent_message.lower())

    async def test_discord_managed_mode(self) -> None:
        self._seed(JoinMode.DISCORD_MANAGED)
        interaction = FakeInteraction()

        await handle_join_watch_party(interaction, self.bot)

        self.assertIn("server staff", interaction.response.sent_message)

    async def test_self_service_offer_leave_then_confirm(self) -> None:
        self._seed(JoinMode.SELF_SERVICE)
        member = FakeMember(1, roles=[FakeRole(ROLE_ID)])
        interaction = FakeInteraction(user=member)

        await handle_join_watch_party(interaction, self.bot)

        self.assertIn("leave", interaction.response.sent_message.lower())
        view = interaction.response.sent_view
        self.assertIsNotNone(view)
        confirm_button = view.children[0]

        confirm_interaction = FakeInteraction(user=member)
        await confirm_button.callback(interaction=confirm_interaction)

        self.assertEqual(member.removed_role_ids, [ROLE_ID])
        self.assertIn("left", confirm_interaction.response.sent_message.lower())

    async def test_self_service_offer_leave_then_abort(self) -> None:
        self._seed(JoinMode.SELF_SERVICE)
        member = FakeMember(1, roles=[FakeRole(ROLE_ID)])
        interaction = FakeInteraction(user=member)
        await handle_join_watch_party(interaction, self.bot)
        view = interaction.response.sent_view
        abort_button = view.children[1]

        abort_interaction = FakeInteraction(user=member)
        await abort_button.callback(interaction=abort_interaction)

        self.assertEqual(member.removed_role_ids, [])
        self.assertIn("no changes", abort_interaction.response.sent_message.lower())

    async def test_approval_mode_notifies_wash_crew_and_persists_message_reference(self) -> None:
        self._seed(JoinMode.APPROVAL)
        channel = FakeChannel(ADMIN_CHANNEL_ID)
        self.bot.register_channel(channel)
        member = FakeMember(1)
        interaction = FakeInteraction(user=member, channel_id=200)

        await handle_join_watch_party(interaction, self.bot)

        self.assertIn("sent to WASH Crew", interaction.response.sent_message)
        self.assertEqual(len(channel.sent), 1)
        content, view = channel.sent[0]
        self.assertIsInstance(view, MembershipApprovalView)
        self.assertIn(member.mention, content)

        pending = self.membership_service.list_pending_requests(GUILD_ID)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].channel_id, ADMIN_CHANNEL_ID)

    async def test_approval_mode_never_uses_the_log_channel_or_invocation_channel(self) -> None:
        # Even with a log channel configured and /join_watch_party
        # invoked from yet another channel, only the Admin channel is used.
        log_channel_id = 555
        invocation_channel_id = 200
        self.guild_configuration_repository.save(
            GuildConfiguration(
                guild_id=GUILD_ID,
                guild_name="G",
                setup_completed=True,
                watch_party_role=WatchPartyRoleConfig(role_id=ROLE_ID, join_mode=JoinMode.APPROVAL),
                channels=GuildChannelsConfig(log_channel_id=log_channel_id, admin_channel_id=ADMIN_CHANNEL_ID),
            )
        )
        admin_channel = FakeChannel(ADMIN_CHANNEL_ID)
        log_channel = FakeChannel(log_channel_id)
        invocation_channel = FakeChannel(invocation_channel_id)
        self.bot.register_channel(admin_channel)
        self.bot.register_channel(log_channel)
        self.bot.register_channel(invocation_channel)
        interaction = FakeInteraction(channel_id=invocation_channel_id)

        await handle_join_watch_party(interaction, self.bot)

        self.assertEqual(len(admin_channel.sent), 1)
        self.assertEqual(len(log_channel.sent), 0)
        self.assertEqual(len(invocation_channel.sent), 0)

    async def test_missing_admin_channel_rejects_the_request_with_no_notification_attempt(self) -> None:
        self._seed(JoinMode.APPROVAL, admin_channel_id=None)
        interaction = FakeInteraction(channel_id=200)

        await handle_join_watch_party(interaction, self.bot)

        self.assertIn("Admin channel", interaction.response.sent_message)
        self.assertEqual(self.membership_service.list_pending_requests(GUILD_ID), [])

    async def test_approval_mode_notification_failure_still_confirms_request_recorded(self) -> None:
        self._seed(JoinMode.APPROVAL)
        # The Admin channel validates fine against interaction.guild (see
        # FakeGuild's default channel_ids), but the bot itself can't
        # fetch it -- simulating a last-second Discord-side hiccup after
        # MembershipService already approved creating the request.
        interaction = FakeInteraction(channel_id=200)

        await handle_join_watch_party(interaction, self.bot)

        self.assertIn("sent to WASH Crew", interaction.response.sent_message)
        self.assertEqual(len(interaction.followup.sent), 1)
        self.assertIn("could not be automatically notified", interaction.followup.sent[0][0])


class HandleMembershipApprovalDecisionTests(MembershipCommandTestCase):
    async def _create_pending_request(self, requester: FakeMember):
        self._seed(JoinMode.APPROVAL)
        channel = FakeChannel(ADMIN_CHANNEL_ID)
        self.bot.register_channel(channel)
        interaction = FakeInteraction(user=requester, channel_id=200)
        await handle_join_watch_party(interaction, self.bot)
        pending = self.membership_service.list_pending_requests(GUILD_ID)
        return pending[0]

    async def test_wash_crew_can_approve(self) -> None:
        requester = FakeMember(1)
        request = await self._create_pending_request(requester)
        approver = FakeMember(WASH_CREW_ROLE_ID, roles=[FakeRole(WASH_CREW_ROLE_ID)])
        guild = FakeGuild(members={requester.id: requester})
        interaction = FakeInteraction(user=approver, guild=guild)

        await handle_membership_approval_decision(interaction, self.bot, request.request_id, approve=True)

        self.assertEqual(requester.added_role_ids, [ROLE_ID])
        self.assertIn("approved", interaction.message.edited_content.lower())
        self.assertIsNone(interaction.message.edited_view)

    async def test_wash_crew_can_deny(self) -> None:
        requester = FakeMember(1)
        request = await self._create_pending_request(requester)
        approver = FakeMember(WASH_CREW_ROLE_ID, roles=[FakeRole(WASH_CREW_ROLE_ID)])
        guild = FakeGuild(members={requester.id: requester})
        interaction = FakeInteraction(user=approver, guild=guild)

        await handle_membership_approval_decision(interaction, self.bot, request.request_id, approve=False)

        self.assertEqual(requester.added_role_ids, [])
        self.assertIn("denied", interaction.message.edited_content.lower())

    async def test_non_wash_user_cannot_approve(self) -> None:
        requester = FakeMember(1)
        request = await self._create_pending_request(requester)
        non_wash_user = FakeMember(2, roles=[])
        interaction = FakeInteraction(user=non_wash_user)

        await handle_membership_approval_decision(interaction, self.bot, request.request_id, approve=True)

        self.assertEqual(requester.added_role_ids, [])
        self.assertIn("WASH Crew", interaction.response.sent_message)
        self.assertIsNone(interaction.message.edited_content)

    async def test_duplicate_approval_fails_gracefully(self) -> None:
        requester = FakeMember(1)
        request = await self._create_pending_request(requester)
        approver = FakeMember(WASH_CREW_ROLE_ID, roles=[FakeRole(WASH_CREW_ROLE_ID)])
        guild = FakeGuild(members={requester.id: requester})

        first_interaction = FakeInteraction(user=approver, guild=guild)
        await handle_membership_approval_decision(first_interaction, self.bot, request.request_id, approve=True)

        second_interaction = FakeInteraction(user=approver, guild=guild)
        await handle_membership_approval_decision(second_interaction, self.bot, request.request_id, approve=True)

        self.assertEqual(requester.added_role_ids, [ROLE_ID])  # not granted twice
        self.assertIn("already been processed", second_interaction.response.sent_message)

    async def test_already_processed_request_rejected(self) -> None:
        requester = FakeMember(1)
        request = await self._create_pending_request(requester)
        approver = FakeMember(WASH_CREW_ROLE_ID, roles=[FakeRole(WASH_CREW_ROLE_ID)])
        guild = FakeGuild(members={requester.id: requester})

        deny_interaction = FakeInteraction(user=approver, guild=guild)
        await handle_membership_approval_decision(deny_interaction, self.bot, request.request_id, approve=False)

        approve_interaction = FakeInteraction(user=approver, guild=guild)
        await handle_membership_approval_decision(approve_interaction, self.bot, request.request_id, approve=True)

        self.assertEqual(requester.added_role_ids, [])
        self.assertIn("already been processed", approve_interaction.response.sent_message)


class RestorePersistentMembershipApprovalViewsTests(MembershipCommandTestCase):
    async def test_restores_a_view_for_every_pending_request_with_a_message(self) -> None:
        self._seed(JoinMode.APPROVAL)
        channel = FakeChannel(ADMIN_CHANNEL_ID)
        self.bot.register_channel(channel)
        interaction = FakeInteraction(channel_id=200)
        await handle_join_watch_party(interaction, self.bot)

        restored_count = restore_persistent_membership_approval_views(self.bot, self.membership_service)

        self.assertEqual(restored_count, 1)
        self.assertEqual(len(self.bot.added_views), 1)
        view, message_id = self.bot.added_views[0]
        self.assertIsInstance(view, MembershipApprovalView)

    async def test_skips_requests_without_a_stored_message(self) -> None:
        self._seed(JoinMode.APPROVAL)
        # No channel registered -- notification fails, so no message_id is ever attached.
        interaction = FakeInteraction(channel_id=200)
        await handle_join_watch_party(interaction, self.bot)

        restored_count = restore_persistent_membership_approval_views(self.bot, self.membership_service)

        self.assertEqual(restored_count, 0)

    async def test_does_not_restore_already_processed_requests(self) -> None:
        requester = FakeMember(1)
        self._seed(JoinMode.APPROVAL)
        channel = FakeChannel(ADMIN_CHANNEL_ID)
        self.bot.register_channel(channel)
        interaction = FakeInteraction(user=requester, channel_id=200)
        await handle_join_watch_party(interaction, self.bot)
        request = self.membership_service.list_pending_requests(GUILD_ID)[0]
        self.membership_service.deny_request(request.request_id, GUILD_ID, WASH_CREW_ROLE_ID)

        restored_count = restore_persistent_membership_approval_views(self.bot, self.membership_service)

        self.assertEqual(restored_count, 0)


if __name__ == "__main__":
    unittest.main()
