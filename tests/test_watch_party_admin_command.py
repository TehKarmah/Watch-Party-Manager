"""Tests for FR-031's /watch_party administration wiring in bot.py.

Mirrors test_membership_command.py's FakeInteraction/FakeResponse/FakeBot
pattern -- these handlers are exercised directly (handle_watch_party_*)
without a live Discord connection. WatchPartyAdminGroup itself is
exercised too, since its interaction_check is the single point enforcing
WASH Crew-only access for every /watch_party subcommand.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from watch_party_manager.bot import (
    WatchPartyAdminGroup,
    handle_watch_party_add,
    handle_watch_party_approved,
    handle_watch_party_denied,
    handle_watch_party_members,
    handle_watch_party_pending,
    handle_watch_party_remove,
    handle_watch_party_search,
)
from watch_party_manager.domain.guild_configuration import (
    GuildChannelsConfig,
    GuildConfiguration,
    JoinMode,
    WatchPartyRoleConfig,
)
from watch_party_manager.membership_view import MembershipApprovalView, PendingRequestSelectView
from watch_party_manager.persistence.guild_configuration_repository import GuildConfigurationRepository
from watch_party_manager.persistence.membership_request_repository import MembershipRequestRepository
from watch_party_manager.services.membership_service import MembershipService
from watch_party_manager.services.permission_service import PermissionService

GUILD_ID = 100
ROLE_ID = 222
WASH_CREW_ROLE_ID = 999
ADMIN_CHANNEL_ID = 500


class FakePermissions:
    def __init__(self, manage_roles: bool = True) -> None:
        self.manage_roles = manage_roles


class FakeMe:
    def __init__(self, manage_roles: bool = True, top_role_position: int = 10) -> None:
        self.guild_permissions = FakePermissions(manage_roles)
        self.top_role = FakeRole(998, position=top_role_position)


class FakeMember:
    def __init__(self, user_id: int, roles=(), display_name=None, name=None, joined_at=None) -> None:
        self.id = user_id
        self.roles = list(roles)
        self.mention = f"<@{user_id}>"
        self.display_name = display_name or f"Member{user_id}"
        self.name = name or f"member{user_id}"
        self.joined_at = joined_at
        self.added_role_ids = []
        self.removed_role_ids = []

    async def add_roles(self, role, reason=None) -> None:
        self.roles.append(role)
        self.added_role_ids.append(role.id)

    async def remove_roles(self, role, reason=None) -> None:
        self.roles = [r for r in self.roles if r.id != role.id]
        self.removed_role_ids.append(role.id)

    def __str__(self) -> str:
        return self.name


class FakeRole:
    def __init__(self, role_id: int, position: int = 5, name: str = "Watch Party", members=None) -> None:
        self.id = role_id
        self.position = position
        self.name = name
        self.members = members or []


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
        role: "FakeRole | None" = None,
        manage_roles: bool = True,
        top_role_position: int = 10,
        members=None,
        channel_ids=(ADMIN_CHANNEL_ID,),
    ) -> None:
        self._role = role if role is not None else FakeRole(ROLE_ID)
        self.me = FakeMe(manage_roles=manage_roles, top_role_position=top_role_position)
        self._members = members or {}
        self._channels = {channel_id: FakeGuildChannel(channel_id) for channel_id in channel_ids}

    def get_role(self, role_id):
        return self._role if role_id == self._role.id else None

    def get_member(self, user_id):
        return self._members.get(user_id)

    def get_channel_or_thread(self, channel_id):
        return self._channels.get(channel_id)


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
        self.edited_content = None
        self.edited_view = "not-edited"

    async def send_message(self, content, ephemeral=False, view=None) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral
        self.sent_view = view

    async def edit_message(self, content=None, view="not-edited") -> None:
        self.edited_content = content
        self.edited_view = view


class FakeMessage:
    def __init__(self) -> None:
        self.edited_content = None
        self.edited_view = "not-edited"

    async def edit(self, content=None, view="not-edited") -> None:
        self.edited_content = content
        self.edited_view = view


class FakeInteraction:
    def __init__(self, user=None, guild=None, guild_id=GUILD_ID) -> None:
        self.user = user if user is not None else FakeMember(1)
        self.guild = guild if guild is not None else FakeGuild()
        self.guild_id = guild_id
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.message = FakeMessage()


class FakeBot:
    def __init__(self, membership_service, membership_request_repository, wash_crew_role_id=WASH_CREW_ROLE_ID) -> None:
        self.membership_service = membership_service
        self.membership_request_repository = membership_request_repository
        self.permission_service = PermissionService(
            watch_party_member_role_id=None, wash_crew_role_id=wash_crew_role_id
        )
        self.wash_crew_role_id = wash_crew_role_id


class WatchPartyAdminCommandTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        temp_path = Path(self._temp_dir.name)
        self.guild_configuration_repository = GuildConfigurationRepository(temp_path / "guild_configurations.json")
        self.membership_request_repository = MembershipRequestRepository(temp_path / "membership_requests.json")
        self.membership_service = MembershipService(
            self.guild_configuration_repository, self.membership_request_repository
        )
        self.bot = FakeBot(self.membership_service, self.membership_request_repository)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _seed(
        self, join_mode: JoinMode = JoinMode.APPROVAL, *, role_id=ROLE_ID, denial_cooldown_days: int = 7
    ) -> None:
        self.guild_configuration_repository.save(
            GuildConfiguration(
                guild_id=GUILD_ID,
                guild_name="Test Guild",
                setup_completed=True,
                watch_party_role=WatchPartyRoleConfig(
                    role_id=role_id, join_mode=join_mode, denial_cooldown_days=denial_cooldown_days
                ),
                channels=GuildChannelsConfig(admin_channel_id=ADMIN_CHANNEL_ID),
            )
        )

    def _wash_crew_member(self, user_id: int = 42) -> FakeMember:
        return FakeMember(user_id, roles=[FakeRole(WASH_CREW_ROLE_ID)])


# --- Permission enforcement -----------------------------------------------------------


class WatchPartyGroupPermissionTests(WatchPartyAdminCommandTestCase):
    async def test_wash_crew_passes_the_interaction_check(self) -> None:
        group = WatchPartyAdminGroup(self.bot)
        interaction = FakeInteraction(user=self._wash_crew_member())

        allowed = await group.interaction_check(interaction)

        self.assertTrue(allowed)
        self.assertIsNone(interaction.response.sent_message)

    async def test_non_wash_crew_fails_the_interaction_check(self) -> None:
        group = WatchPartyAdminGroup(self.bot)
        interaction = FakeInteraction(user=FakeMember(1, roles=[]))

        allowed = await group.interaction_check(interaction)

        self.assertFalse(allowed)
        self.assertIn("WASH Crew", interaction.response.sent_message)
        self.assertTrue(interaction.response.sent_ephemeral)

    async def test_unconfigured_wash_crew_role_fails_closed(self) -> None:
        bot = FakeBot(self.membership_service, self.membership_request_repository, wash_crew_role_id=None)
        group = WatchPartyAdminGroup(bot)
        interaction = FakeInteraction(user=FakeMember(1, roles=[]))

        allowed = await group.interaction_check(interaction)

        self.assertFalse(allowed)


# --- /watch_party members --------------------------------------------------------------


class WatchPartyMembersTests(WatchPartyAdminCommandTestCase):
    async def test_lists_current_members(self) -> None:
        self._seed()
        members = [FakeMember(1, display_name="Alice"), FakeMember(2, display_name="Bob")]
        role = FakeRole(ROLE_ID, members=members)
        interaction = FakeInteraction(guild=FakeGuild(role=role))

        await handle_watch_party_members(interaction, self.bot)

        self.assertIn("Alice", interaction.response.sent_message)
        self.assertIn("Bob", interaction.response.sent_message)
        self.assertIn("(2)", interaction.response.sent_message)

    async def test_empty_membership_list(self) -> None:
        self._seed()
        interaction = FakeInteraction(guild=FakeGuild(role=FakeRole(ROLE_ID, members=[])))

        await handle_watch_party_members(interaction, self.bot)

        self.assertIn("no members", interaction.response.sent_message.lower())

    async def test_missing_role_configuration(self) -> None:
        self._seed(role_id=None)
        interaction = FakeInteraction()

        await handle_watch_party_members(interaction, self.bot)

        self.assertIn("hasn't been configured", interaction.response.sent_message)

    async def test_deleted_role(self) -> None:
        self._seed()
        interaction = FakeInteraction(guild=FakeGuild(role=None))
        interaction.guild._role = FakeRole(role_id=ROLE_ID + 1)  # guild's only role no longer matches

        await handle_watch_party_members(interaction, self.bot)

        self.assertIn("no longer exists", interaction.response.sent_message)

    async def test_requires_a_server(self) -> None:
        interaction = FakeInteraction(guild_id=None)
        interaction.guild = None

        await handle_watch_party_members(interaction, self.bot)

        self.assertIn("server", interaction.response.sent_message.lower())


# --- /watch_party add / remove ----------------------------------------------------------


class WatchPartyAddRemoveTests(WatchPartyAdminCommandTestCase):
    async def test_add_grants_the_role(self) -> None:
        self._seed()
        member = FakeMember(1)
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_watch_party_add(interaction, self.bot, member)

        self.assertEqual(member.added_role_ids, [ROLE_ID])
        self.assertIn("added", interaction.response.sent_message.lower())

    async def test_add_rejects_an_existing_member(self) -> None:
        self._seed()
        member = FakeMember(1, roles=[FakeRole(ROLE_ID)])
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_watch_party_add(interaction, self.bot, member)

        self.assertEqual(member.added_role_ids, [])
        self.assertIn("already", interaction.response.sent_message.lower())

    async def test_remove_removes_the_role(self) -> None:
        self._seed()
        member = FakeMember(1, roles=[FakeRole(ROLE_ID)])
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_watch_party_remove(interaction, self.bot, member)

        self.assertEqual(member.removed_role_ids, [ROLE_ID])
        self.assertIn("removed", interaction.response.sent_message.lower())

    async def test_remove_rejects_a_non_member(self) -> None:
        self._seed()
        member = FakeMember(1)
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_watch_party_remove(interaction, self.bot, member)

        self.assertEqual(member.removed_role_ids, [])
        self.assertIn("not currently", interaction.response.sent_message.lower())

    async def test_add_rejects_missing_manage_roles_permission(self) -> None:
        self._seed()
        member = FakeMember(1)
        interaction = FakeInteraction(user=self._wash_crew_member(), guild=FakeGuild(manage_roles=False))

        await handle_watch_party_add(interaction, self.bot, member)

        self.assertIn("Manage Roles", interaction.response.sent_message)

    async def test_add_rejects_role_hierarchy_failure(self) -> None:
        self._seed()
        member = FakeMember(1)
        interaction = FakeInteraction(user=self._wash_crew_member(), guild=FakeGuild(top_role_position=1))

        await handle_watch_party_add(interaction, self.bot, member)

        self.assertIn("positioned above", interaction.response.sent_message)

    async def test_add_rejects_a_deleted_role(self) -> None:
        self._seed()
        member = FakeMember(1)
        interaction = FakeInteraction(user=self._wash_crew_member(), guild=FakeGuild(role=FakeRole(ROLE_ID + 1)))

        await handle_watch_party_add(interaction, self.bot, member)

        self.assertIn("no longer exists", interaction.response.sent_message)


# --- /watch_party search ----------------------------------------------------------------


class WatchPartySearchTests(WatchPartyAdminCommandTestCase):
    async def test_search_a_current_member(self) -> None:
        self._seed()
        member = FakeMember(1, roles=[FakeRole(ROLE_ID)], display_name="Alice")
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_watch_party_search(interaction, self.bot, member)

        self.assertIn("Alice", interaction.response.sent_message)
        self.assertIn("Current status: Member", interaction.response.sent_message)

    async def test_search_an_unknown_member(self) -> None:
        self._seed()
        member = FakeMember(999999, display_name="Ghost")
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_watch_party_search(interaction, self.bot, member)

        self.assertIn("Current status: Not a member", interaction.response.sent_message)
        self.assertIn("Pending request: none", interaction.response.sent_message)
        self.assertIn("Last approval: none", interaction.response.sent_message)
        self.assertIn("Last denial: none", interaction.response.sent_message)

    async def test_search_a_pending_requester(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1)
        outcome = await self.membership_service.handle_join_request(GUILD_ID, member, FakeGuild())
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_watch_party_search(interaction, self.bot, member)

        self.assertIn("Pending request: submitted", interaction.response.sent_message)
        self.assertTrue(outcome.request.is_pending)

    async def test_search_an_approved_requester(self) -> None:
        self._seed(JoinMode.APPROVAL)
        member = FakeMember(1)
        guild = FakeGuild()
        outcome = await self.membership_service.handle_join_request(GUILD_ID, member, guild)
        await self.membership_service.approve_request(outcome.request.request_id, GUILD_ID, WASH_CREW_ROLE_ID, member, guild)
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_watch_party_search(interaction, self.bot, member)

        self.assertIn("Last approval:", interaction.response.sent_message)
        self.assertNotIn("Last approval: none", interaction.response.sent_message)

    async def test_search_a_denied_requester_shows_cooldown(self) -> None:
        self._seed(JoinMode.APPROVAL, denial_cooldown_days=7)
        member = FakeMember(1)
        outcome = await self.membership_service.handle_join_request(GUILD_ID, member, FakeGuild())
        self.membership_service.deny_request(outcome.request.request_id, GUILD_ID, WASH_CREW_ROLE_ID)
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_watch_party_search(interaction, self.bot, member)

        self.assertIn("Last denial:", interaction.response.sent_message)
        self.assertIn("cooldown", interaction.response.sent_message.lower())


# --- /watch_party pending ---------------------------------------------------------------


class WatchPartyPendingTests(WatchPartyAdminCommandTestCase):
    async def test_empty_pending_list(self) -> None:
        self._seed(JoinMode.APPROVAL)
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_watch_party_pending(interaction, self.bot)

        self.assertIn("no pending", interaction.response.sent_message.lower())
        self.assertIsNone(interaction.response.sent_view)

    async def test_pending_list_shows_requesters_and_a_picker(self) -> None:
        self._seed(JoinMode.APPROVAL)
        requester = FakeMember(1)
        await self.membership_service.handle_join_request(GUILD_ID, requester, FakeGuild())
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_watch_party_pending(interaction, self.bot)

        self.assertIn(f"<@{requester.id}>", interaction.response.sent_message)
        self.assertIsInstance(interaction.response.sent_view, PendingRequestSelectView)

    async def test_approve_from_the_pending_picker_reuses_the_approval_workflow(self) -> None:
        self._seed(JoinMode.APPROVAL)
        requester = FakeMember(1)
        outcome = await self.membership_service.handle_join_request(GUILD_ID, requester, FakeGuild())
        interaction = FakeInteraction(user=self._wash_crew_member())
        await handle_watch_party_pending(interaction, self.bot)
        select = interaction.response.sent_view.children[0]
        select._values = [str(outcome.request.request_id)]

        select_interaction = FakeInteraction(user=self._wash_crew_member())
        await select.callback(select_interaction)

        self.assertIsInstance(select_interaction.response.edited_view, MembershipApprovalView)
        approval_view = select_interaction.response.edited_view
        approver_interaction = FakeInteraction(
            user=self._wash_crew_member(), guild=FakeGuild(members={requester.id: requester})
        )
        await approval_view.children[0].callback(interaction=approver_interaction)

        self.assertEqual(requester.added_role_ids, [ROLE_ID])
        self.assertIn("approved", approver_interaction.response.sent_message.lower())

    async def test_deny_from_the_pending_picker_reuses_the_approval_workflow(self) -> None:
        self._seed(JoinMode.APPROVAL)
        requester = FakeMember(1)
        outcome = await self.membership_service.handle_join_request(GUILD_ID, requester, FakeGuild())
        interaction = FakeInteraction(user=self._wash_crew_member())
        await handle_watch_party_pending(interaction, self.bot)
        select = interaction.response.sent_view.children[0]
        select._values = [str(outcome.request.request_id)]

        select_interaction = FakeInteraction(user=self._wash_crew_member())
        await select.callback(select_interaction)

        approval_view = select_interaction.response.edited_view
        approver_interaction = FakeInteraction(
            user=self._wash_crew_member(), guild=FakeGuild(members={requester.id: requester})
        )
        await approval_view.children[1].callback(interaction=approver_interaction)

        self.assertEqual(requester.added_role_ids, [])
        self.assertIn("denied", approver_interaction.response.sent_message.lower())

    async def test_selecting_an_already_processed_request_fails_gracefully(self) -> None:
        self._seed(JoinMode.APPROVAL)
        requester = FakeMember(1)
        outcome = await self.membership_service.handle_join_request(GUILD_ID, requester, FakeGuild())
        interaction = FakeInteraction(user=self._wash_crew_member())
        await handle_watch_party_pending(interaction, self.bot)
        select = interaction.response.sent_view.children[0]
        select._values = [str(outcome.request.request_id)]
        self.membership_service.deny_request(outcome.request.request_id, GUILD_ID, WASH_CREW_ROLE_ID)

        select_interaction = FakeInteraction(user=self._wash_crew_member())
        await select.callback(select_interaction)

        self.assertIn("no longer pending", select_interaction.response.edited_content.lower())


# --- /watch_party approved / denied ------------------------------------------------------


class WatchPartyApprovedDeniedTests(WatchPartyAdminCommandTestCase):
    async def test_empty_approved_list(self) -> None:
        self._seed(JoinMode.APPROVAL)
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_watch_party_approved(interaction, self.bot)

        self.assertIn("no approved", interaction.response.sent_message.lower())

    async def test_approved_list_shows_approver(self) -> None:
        self._seed(JoinMode.APPROVAL)
        requester = FakeMember(1)
        guild = FakeGuild()
        outcome = await self.membership_service.handle_join_request(GUILD_ID, requester, guild)
        await self.membership_service.approve_request(
            outcome.request.request_id, GUILD_ID, WASH_CREW_ROLE_ID, requester, guild
        )
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_watch_party_approved(interaction, self.bot)

        self.assertIn(f"<@{requester.id}>", interaction.response.sent_message)
        self.assertIn(f"<@{WASH_CREW_ROLE_ID}>", interaction.response.sent_message)

    async def test_empty_denied_list(self) -> None:
        self._seed(JoinMode.APPROVAL)
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_watch_party_denied(interaction, self.bot)

        self.assertIn("no denied", interaction.response.sent_message.lower())

    async def test_denied_list_shows_denier_and_cooldown_expiration(self) -> None:
        self._seed(JoinMode.APPROVAL, denial_cooldown_days=7)
        requester = FakeMember(1)
        outcome = await self.membership_service.handle_join_request(GUILD_ID, requester, FakeGuild())
        self.membership_service.deny_request(outcome.request.request_id, GUILD_ID, WASH_CREW_ROLE_ID)
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_watch_party_denied(interaction, self.bot)

        self.assertIn(f"<@{requester.id}>", interaction.response.sent_message)
        self.assertIn("cooldown until", interaction.response.sent_message)

    async def test_approved_and_denied_lists_are_paginated(self) -> None:
        self._seed(JoinMode.APPROVAL)
        guild = FakeGuild()
        for user_id in range(1, 13):
            requester = FakeMember(user_id)
            outcome = await self.membership_service.handle_join_request(GUILD_ID, requester, guild)
            await self.membership_service.approve_request(
                outcome.request.request_id, GUILD_ID, WASH_CREW_ROLE_ID, requester, guild
            )
        interaction = FakeInteraction(user=self._wash_crew_member())

        await handle_watch_party_approved(interaction, self.bot)

        self.assertIn("...and 2 more.", interaction.response.sent_message)


if __name__ == "__main__":
    unittest.main()
