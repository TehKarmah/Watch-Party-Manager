"""Multi-Guild Isolation, Phases 3b-3c: end-to-end regression coverage
proving production permission checks are guild-scoped, not the shared
singleton.

Phase 3a's own test_permission_service.py already covers
resolve_permission_service() in isolation (Guild A/B resolve differently,
no shared mutable state, missing config fails closed, etc.). This file
instead drives that resolver through real, migrated production code
paths -- command handlers and persistent-view restoration -- to prove
the migration itself closed the cross-guild bleed, not just that the
resolver works standalone. Phase 3b covered every PermissionService-based
call site; Phase 3c covers the remaining is_wash_crew_member(user,
bot.wash_crew_role_id) call sites Phase 3b's own audit found were still
reading the shared, global singleton attribute directly (see
SimultaneousDifferentWashCrewRolesThroughFormerIsWashCrewMemberPathTests
below).

Reuses this project's established GUILD_A/GUILD_B multi-guild testing
convention (see test_vote_service.py's GuildRoundNumberTests).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from watch_party_manager.bot import (
    MembershipGroup,
    handle_add_suggestion,
    handle_remove_suggestion,
    restore_persistent_suggestion_views,
    restore_persistent_voting_views,
)
from watch_party_manager.domain.guild_configuration import GuildConfiguration, WatchPartyRoleConfig
from watch_party_manager.domain.vote import VoteVisibility
from watch_party_manager.persistence.guild_configuration_repository import GuildConfigurationRepository
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.services.permission_service import resolve_permission_service
from watch_party_manager.services.suggestion_input_service import SuggestionInputService
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.vote_service import VoteService
from watch_party_manager.suggestion_view import SuggestionView
from watch_party_manager.voting_view import VotingView

GUILD_A = 100
GUILD_B = 200
GUILD_A_WASH_CREW_ROLE_ID = 111
GUILD_A_WATCH_PARTY_ROLE_ID = 112
GUILD_B_WASH_CREW_ROLE_ID = 221
GUILD_B_WATCH_PARTY_ROLE_ID = 222


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeGuild:
    def __init__(self, owner_id: int = 0) -> None:
        self.owner_id = owner_id


class FakeMember:
    def __init__(self, user_id: int, role_ids=(), *, guild=None) -> None:
        self.id = user_id
        self.roles = [FakeRole(role_id) for role_id in role_ids]
        self.guild = guild if guild is not None else FakeGuild()


class FakeResponse:
    def __init__(self) -> None:
        self.sent_message = None
        self.sent_ephemeral = None

    async def send_message(self, content=None, ephemeral=False, **kwargs) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral


class FakeMessage:
    async def edit(self, **kwargs) -> None:
        pass


class FakeInteraction:
    def __init__(self, user: FakeMember, guild_id: int) -> None:
        self.user = user
        self.guild_id = guild_id
        self.channel_id = None
        self.response = FakeResponse()
        self.message = FakeMessage()


class MultiGuildTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.guild_configuration_repository = GuildConfigurationRepository(root / "guild_configurations.json")

    def _save_guild(self, guild_id: int, *, wash_crew_role_id=None, watch_party_role_id=None) -> None:
        self.guild_configuration_repository.save(
            GuildConfiguration(
                guild_id=guild_id,
                guild_name=f"Guild {guild_id}",
                wash_crew_role_id=wash_crew_role_id,
                watch_party_role=WatchPartyRoleConfig(role_id=watch_party_role_id),
            )
        )


class SimultaneousDifferentWashCrewRolesTests(MultiGuildTestCase):
    """Guild A and Guild B each configure their own, different WASH Crew
    role, and both must work correctly at the same time -- the exact
    scenario the shared singleton could never support (see
    MembershipGroup.interaction_check, the single point enforcing
    WASH Crew-only access for every /membership subcommand).
    """

    def setUp(self) -> None:
        super().setUp()
        self._save_guild(GUILD_A, wash_crew_role_id=GUILD_A_WASH_CREW_ROLE_ID)
        self._save_guild(GUILD_B, wash_crew_role_id=GUILD_B_WASH_CREW_ROLE_ID)

        class FakeBot:
            pass

        self.bot = FakeBot()
        self.bot.guild_configuration_repository = self.guild_configuration_repository

    async def test_guild_as_crew_role_is_accepted_in_guild_a(self) -> None:
        group = MembershipGroup(self.bot)
        member = FakeMember(1, [GUILD_A_WASH_CREW_ROLE_ID])
        interaction = FakeInteraction(member, GUILD_A)

        allowed = await group.interaction_check(interaction)

        self.assertTrue(allowed)

    async def test_guild_bs_crew_role_is_accepted_in_guild_b(self) -> None:
        group = MembershipGroup(self.bot)
        member = FakeMember(2, [GUILD_B_WASH_CREW_ROLE_ID])
        interaction = FakeInteraction(member, GUILD_B)

        allowed = await group.interaction_check(interaction)

        self.assertTrue(allowed)

    async def test_guild_as_crew_role_does_not_grant_access_in_guild_b(self) -> None:
        # The core cross-guild bleed regression: a role ID that is WASH
        # Crew in Guild A must never be treated as WASH Crew in Guild B,
        # even though both guilds are resolved from the same repository
        # in the same process, back-to-back.
        group = MembershipGroup(self.bot)
        member = FakeMember(3, [GUILD_A_WASH_CREW_ROLE_ID])
        interaction = FakeInteraction(member, GUILD_B)

        allowed = await group.interaction_check(interaction)

        self.assertFalse(allowed)
        self.assertIn("WASH Crew", interaction.response.sent_message)

    async def test_guild_bs_crew_role_does_not_grant_access_in_guild_a(self) -> None:
        group = MembershipGroup(self.bot)
        member = FakeMember(4, [GUILD_B_WASH_CREW_ROLE_ID])
        interaction = FakeInteraction(member, GUILD_A)

        allowed = await group.interaction_check(interaction)

        self.assertFalse(allowed)

    async def test_a_role_change_in_guild_a_never_affects_guild_b(self) -> None:
        # Simulates Guild A's WASH Crew re-running /config to pick a new
        # role -- must never touch Guild B's own, independently
        # configured role.
        self._save_guild(GUILD_A, wash_crew_role_id=999999)
        group = MembershipGroup(self.bot)

        guild_b_member = FakeMember(5, [GUILD_B_WASH_CREW_ROLE_ID])
        allowed_in_b = await group.interaction_check(FakeInteraction(guild_b_member, GUILD_B))
        self.assertTrue(allowed_in_b)

        stale_guild_a_role_member = FakeMember(6, [GUILD_A_WASH_CREW_ROLE_ID])
        allowed_in_a = await group.interaction_check(FakeInteraction(stale_guild_a_role_member, GUILD_A))
        self.assertFalse(allowed_in_a)


class MissingConfigurationFailsClosedThroughProductionCodeTests(MultiGuildTestCase):
    """Missing GuildConfiguration must still fail closed when reached
    through real, migrated production code (not just the resolver
    tested standalone in test_permission_service.py).
    """

    def setUp(self) -> None:
        super().setUp()
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(Path(self._temp_dir.name) / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(
                Path(self._temp_dir.name) / "suggestion_databases.json"
            ),
        )

        class FakeBot:
            pass

        self.bot = FakeBot()
        self.bot.suggestion_service = self.suggestion_service
        self.bot.suggestion_input_service = SuggestionInputService()
        self.bot.suggestion_database_configuration_repository = SuggestionDatabaseConfigurationRepository(
            Path(self._temp_dir.name) / "suggestion_database_configurations.json"
        )
        self.bot.guild_configuration_repository = self.guild_configuration_repository

    async def test_add_suggestion_fails_closed_for_an_unconfigured_guild(self) -> None:
        interaction = FakeInteraction(FakeMember(1, [GUILD_A_WATCH_PARTY_ROLE_ID]), GUILD_A)

        await handle_add_suggestion(interaction, self.bot, "The Matrix", None, None)

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIn("before using this command", interaction.response.sent_message)

    async def test_add_suggestion_succeeds_once_the_guild_is_configured(self) -> None:
        self._save_guild(GUILD_A, watch_party_role_id=GUILD_A_WATCH_PARTY_ROLE_ID)
        interaction = FakeInteraction(FakeMember(1, [GUILD_A_WATCH_PARTY_ROLE_ID]), GUILD_A)

        await handle_add_suggestion(interaction, self.bot, "The Matrix", None, None)

        self.assertNotIn("before using this command", interaction.response.sent_message or "")


class OwnerBypassThroughGuildScopedResolutionTests(unittest.TestCase):
    """The Discord server owner's always-a-Watch-Party-member bypass
    (PermissionService._is_guild_owner) must survive being resolved
    through a real, per-guild GuildConfiguration -- not only when
    PermissionService is constructed directly, as
    ServerOwnerPermissionTests in test_permission_service.py covers.
    """

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.guild_configuration_repository = GuildConfigurationRepository(root / "guild_configurations.json")
        self.guild_configuration_repository.save(
            GuildConfiguration(guild_id=GUILD_A, guild_name="Guild A")
        )

    def test_owner_is_a_watch_party_member_with_no_role_configured(self) -> None:
        service = resolve_permission_service(GUILD_A, self.guild_configuration_repository)
        guild = FakeGuild(owner_id=42)
        owner = FakeMember(42, guild=guild)

        self.assertTrue(service.require_watch_party_member(owner).allowed)


class PersistentVotingViewGuildIsolationTests(unittest.IsolatedAsyncioTestCase):
    """restore_persistent_voting_views restores rounds from every guild
    in one batch call -- each restored vote button must enforce its
    OWN guild's Watch Party role, not whichever guild's round happened
    to be restored most recently.
    """

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.vote_service = VoteService(
            self.suggestion_service, repository=JsonVoteRepository(root / "voting.json")
        )
        self.guild_configuration_repository = GuildConfigurationRepository(root / "guild_configurations.json")
        self.guild_configuration_repository.save(
            GuildConfiguration(
                guild_id=GUILD_A,
                guild_name="Guild A",
                watch_party_role=WatchPartyRoleConfig(role_id=GUILD_A_WATCH_PARTY_ROLE_ID),
            )
        )
        self.guild_configuration_repository.save(
            GuildConfiguration(
                guild_id=GUILD_B,
                guild_name="Guild B",
                watch_party_role=WatchPartyRoleConfig(role_id=GUILD_B_WATCH_PARTY_ROLE_ID),
            )
        )

    async def test_each_restored_rounds_vote_button_enforces_its_own_guilds_role(self) -> None:
        item_a1 = self.suggestion_service.suggest("Guild A Movie 1", guild_id=GUILD_A).watch_item
        item_a2 = self.suggestion_service.suggest("Guild A Movie 2", guild_id=GUILD_A).watch_item
        item_b1 = self.suggestion_service.suggest("Guild B Movie 1", guild_id=GUILD_B).watch_item
        item_b2 = self.suggestion_service.suggest("Guild B Movie 2", guild_id=GUILD_B).watch_item

        round_a = self.vote_service.create_round(
            visibility=VoteVisibility.VISIBLE,
            candidate_suggestion_ids=[item_a1.id, item_a2.id],
            database_id=1,
        ).vote_round
        self.vote_service.attach_message_reference(round_a.id, guild_id=GUILD_A, channel_id=10, message_id=1000)

        round_b = self.vote_service.create_round(
            visibility=VoteVisibility.VISIBLE,
            candidate_suggestion_ids=[item_b1.id, item_b2.id],
            database_id=2,
        ).vote_round
        self.vote_service.attach_message_reference(round_b.id, guild_id=GUILD_B, channel_id=20, message_id=2000)

        added_views = []

        class FakeBot:
            def add_view(self, view, message_id=None) -> None:
                added_views.append((view, message_id))

        restored = restore_persistent_voting_views(
            FakeBot(), self.vote_service, self.suggestion_service, self.guild_configuration_repository
        )
        self.assertEqual(restored, 2)

        views_by_message_id = {message_id: view for view, message_id in added_views}
        view_a: VotingView = views_by_message_id[1000]
        view_b: VotingView = views_by_message_id[2000]

        # A Guild B Watch Party member must never be able to vote on
        # Guild A's restored round via a stale, cross-guild role match.
        guild_b_member = FakeMember(1, [GUILD_B_WATCH_PARTY_ROLE_ID])
        interaction = FakeInteraction(guild_b_member, GUILD_A)
        await view_a.children[0].callback(interaction)
        self.assertIn("Watch Party member", interaction.response.sent_message)

        # The matching Guild A member succeeds on Guild A's own round.
        guild_a_member = FakeMember(2, [GUILD_A_WATCH_PARTY_ROLE_ID])
        interaction = FakeInteraction(guild_a_member, GUILD_A)
        await view_a.children[0].callback(interaction)
        self.assertNotIn("Watch Party member", interaction.response.sent_message)

        # And symmetrically for Guild B's own round.
        interaction = FakeInteraction(guild_a_member, GUILD_B)
        await view_b.children[0].callback(interaction)
        self.assertIn("Watch Party member", interaction.response.sent_message)


class PersistentSuggestionViewGuildIsolationTests(unittest.IsolatedAsyncioTestCase):
    """restore_persistent_suggestion_views restores suggestions from
    every guild in one batch call -- each restored Watched button must
    enforce its OWN guild's WASH Crew role.
    """

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.configuration_repository = SuggestionDatabaseConfigurationRepository(
            root / "suggestion_database_configurations.json"
        )
        self.guild_configuration_repository = GuildConfigurationRepository(root / "guild_configurations.json")
        self.guild_configuration_repository.save(
            GuildConfiguration(
                guild_id=GUILD_A, guild_name="Guild A", wash_crew_role_id=GUILD_A_WASH_CREW_ROLE_ID
            )
        )
        self.guild_configuration_repository.save(
            GuildConfiguration(
                guild_id=GUILD_B, guild_name="Guild B", wash_crew_role_id=GUILD_B_WASH_CREW_ROLE_ID
            )
        )

    async def test_each_restored_suggestions_watched_button_enforces_its_own_guilds_crew_role(self) -> None:
        # channel_id left unset on both items so restore_persistent_suggestion_views
        # takes its simpler "no channel to fetch, just re-register callback
        # routing" branch (bot.add_view only) -- the exact same
        # build_suggestion_view() call the fetch-and-maybe-migrate branch
        # makes, so this still exercises the real restoration function
        # without needing a fake Discord message to fetch/edit.
        item_a = self.suggestion_service.suggest("Guild A Movie", guild_id=GUILD_A).watch_item
        self.suggestion_service.set_confirmation_post_reference(item_a.id, GUILD_A, None, 1000)
        item_b = self.suggestion_service.suggest("Guild B Movie", guild_id=GUILD_B).watch_item
        self.suggestion_service.set_confirmation_post_reference(item_b.id, GUILD_B, None, 2000)

        added_views = []
        suggestion_service = self.suggestion_service

        class FakeBot:
            def __init__(self) -> None:
                self.suggestion_service = suggestion_service

            def add_view(self, view, message_id=None) -> None:
                added_views.append((view, message_id))

        restored = await restore_persistent_suggestion_views(
            FakeBot(), self.suggestion_service, self.configuration_repository, self.guild_configuration_repository
        )
        self.assertEqual(restored, 2)

        views_by_message_id = {message_id: view for view, message_id in added_views}
        view_a: SuggestionView = views_by_message_id[1000]
        view_b: SuggestionView = views_by_message_id[2000]

        watched_button_a = next(
            child for child in view_a.children if getattr(child, "label", "") == "Mark as Watched"
        )
        watched_button_b = next(
            child for child in view_b.children if getattr(child, "label", "") == "Mark as Watched"
        )

        # Guild B's WASH Crew role must never mark Guild A's suggestion watched.
        guild_b_crew = FakeMember(1, [GUILD_B_WASH_CREW_ROLE_ID])
        interaction = FakeInteraction(guild_b_crew, GUILD_A)
        await watched_button_a.callback(interaction)
        self.assertIn("WASH Crew", interaction.response.sent_message)

        # Guild A's own WASH Crew role is accepted on Guild A's suggestion
        # (opens the watched-date modal rather than rejecting).
        guild_a_crew = FakeMember(2, [GUILD_A_WASH_CREW_ROLE_ID])
        interaction = FakeInteraction(guild_a_crew, GUILD_A)

        class ModalCapturingResponse(FakeResponse):
            def __init__(self) -> None:
                super().__init__()
                self.sent_modal = None

            async def send_modal(self, modal) -> None:
                self.sent_modal = modal

        interaction.response = ModalCapturingResponse()
        await watched_button_a.callback(interaction)
        self.assertIsNotNone(interaction.response.sent_modal)
        self.assertIsNone(interaction.response.sent_message)


class SimultaneousDifferentWashCrewRolesThroughFormerIsWashCrewMemberPathTests(unittest.IsolatedAsyncioTestCase):
    """Multi-Guild Isolation, Phase 3c: covers the ~40 production sites
    that previously called is_wash_crew_member(user, bot.wash_crew_role_id)
    directly (the shared, global singleton attribute) rather than going
    through PermissionService/resolve_permission_service at all -- the
    gap Phase 3b's own audit identified as out of its stated scope.
    handle_remove_suggestion is one such site: before this phase it read
    bot.wash_crew_role_id unconditionally; it now resolves through
    resolve_permission_service(interaction.guild_id,
    bot.guild_configuration_repository) exactly like every
    PermissionService-based command already did after Phase 3b.
    """

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.guild_configuration_repository = GuildConfigurationRepository(root / "guild_configurations.json")
        self.guild_configuration_repository.save(
            GuildConfiguration(guild_id=GUILD_A, guild_name="Guild A", wash_crew_role_id=GUILD_A_WASH_CREW_ROLE_ID)
        )
        self.guild_configuration_repository.save(
            GuildConfiguration(guild_id=GUILD_B, guild_name="Guild B", wash_crew_role_id=GUILD_B_WASH_CREW_ROLE_ID)
        )

        class FakeBot:
            pass

        self.bot = FakeBot()
        self.bot.suggestion_service = self.suggestion_service
        self.bot.guild_configuration_repository = self.guild_configuration_repository

    async def test_guild_as_own_crew_role_removes_a_guild_a_suggestion(self) -> None:
        item = self.suggestion_service.suggest("Alien", guild_id=GUILD_A).watch_item
        interaction = FakeInteraction(FakeMember(1, [GUILD_A_WASH_CREW_ROLE_ID]), GUILD_A)

        await handle_remove_suggestion(interaction, self.bot, item.title)

        self.assertIsNotNone(interaction.response.sent_message)
        self.assertNotIn("WASH Crew", interaction.response.sent_message)

    async def test_guild_as_crew_role_cannot_remove_a_guild_bs_suggestion(self) -> None:
        # The exact cross-guild bleed this phase closes: Guild A's WASH
        # Crew role must never be honored while operating in Guild B.
        item = self.suggestion_service.suggest("Alien", guild_id=GUILD_B).watch_item
        interaction = FakeInteraction(FakeMember(1, [GUILD_A_WASH_CREW_ROLE_ID]), GUILD_B)

        await handle_remove_suggestion(interaction, self.bot, item.title)

        self.assertIn("WASH Crew", interaction.response.sent_message)

    async def test_a_role_change_in_guild_a_never_affects_guild_b(self) -> None:
        self.guild_configuration_repository.save(
            GuildConfiguration(guild_id=GUILD_A, guild_name="Guild A", wash_crew_role_id=999999)
        )
        item = self.suggestion_service.suggest("Arrival", guild_id=GUILD_B).watch_item
        guild_b_crew = FakeMember(2, [GUILD_B_WASH_CREW_ROLE_ID])
        interaction = FakeInteraction(guild_b_crew, GUILD_B)

        await handle_remove_suggestion(interaction, self.bot, item.title)

        self.assertNotIn("WASH Crew", interaction.response.sent_message)

    async def test_missing_configuration_fails_closed(self) -> None:
        unconfigured_guild_id = 999
        item = self.suggestion_service.suggest("Dune", guild_id=unconfigured_guild_id).watch_item
        interaction = FakeInteraction(FakeMember(1, [GUILD_A_WASH_CREW_ROLE_ID]), unconfigured_guild_id)

        await handle_remove_suggestion(interaction, self.bot, item.title)

        self.assertIn("not been configured", interaction.response.sent_message)


if __name__ == "__main__":
    unittest.main()
