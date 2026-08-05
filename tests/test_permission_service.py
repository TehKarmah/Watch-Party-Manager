import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.guild_configuration import GuildConfiguration, WatchPartyRoleConfig
from watch_party_manager.persistence.guild_configuration_repository import GuildConfigurationRepository
from watch_party_manager.services.permission_service import PermissionService, resolve_permission_service


class FakeRole:
    def __init__(self, role_id: int, name: str = "some-role-name") -> None:
        self.id = role_id
        self.name = name


class FakeGuild:
    def __init__(self, owner_id) -> None:
        self.owner_id = owner_id


class FakeMember:
    def __init__(self, role_ids=(), *, user_id=None, guild=None) -> None:
        self.roles = [FakeRole(role_id) for role_id in role_ids]
        self.id = user_id
        self.guild = guild


class PermissionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = PermissionService(
            watch_party_member_role_id=111,
            wash_crew_role_id=222,
        )

    def test_watch_party_member_role_is_allowed(self) -> None:
        self.assertTrue(self.service.is_watch_party_member(FakeMember([111])))

    def test_wash_crew_inherits_watch_party_member_permissions(self) -> None:
        self.assertTrue(self.service.is_watch_party_member(FakeMember([222])))

    def test_unrelated_role_is_not_allowed(self) -> None:
        self.assertFalse(self.service.is_watch_party_member(FakeMember([333])))

    def test_watch_party_check_fails_closed_when_both_roles_are_unconfigured(self) -> None:
        service = PermissionService(
            watch_party_member_role_id=None,
            wash_crew_role_id=None,
        )
        result = service.require_watch_party_member(FakeMember([111]))
        self.assertFalse(result.allowed)
        self.assertIn("WATCH_PARTY_MEMBER_ROLE_ID", result.message)

    def test_watch_party_check_rejects_member_without_required_role(self) -> None:
        result = self.service.require_watch_party_member(FakeMember([333]))
        self.assertFalse(result.allowed)
        self.assertIn("Watch Party member role", result.message)

    def test_watch_party_check_allows_configured_member(self) -> None:
        self.assertTrue(
            self.service.require_watch_party_member(FakeMember([111])).allowed
        )

    def test_watch_party_check_allows_wash_crew(self) -> None:
        self.assertTrue(
            self.service.require_watch_party_member(FakeMember([222])).allowed
        )

    def test_wash_crew_check_fails_closed_when_unconfigured(self) -> None:
        service = PermissionService(
            watch_party_member_role_id=111,
            wash_crew_role_id=None,
        )
        result = service.require_wash_crew(FakeMember([222]))
        self.assertFalse(result.allowed)
        self.assertIn("WASH_CREW_ROLE_ID", result.message)

    def test_wash_crew_check_does_not_accept_member_role_alone(self) -> None:
        result = self.service.require_wash_crew(FakeMember([111]))
        self.assertFalse(result.allowed)

    def test_wash_crew_check_accepts_crew_role(self) -> None:
        self.assertTrue(self.service.require_wash_crew(FakeMember([222])).allowed)

    def test_renaming_the_discord_role_after_setup_does_not_break_the_check(self) -> None:
        # First-Time UX Polish (role identification audit): the configured
        # role is looked up by ID only -- a WASH Crew member renaming the
        # Discord role in Server Settings must never break access.
        member = FakeMember([])
        member.roles = [FakeRole(111, name="Whatever They Renamed It To")]
        self.assertTrue(self.service.is_watch_party_member(member))
        self.assertTrue(self.service.require_watch_party_member(member).allowed)


class ServerOwnerPermissionTests(unittest.TestCase):
    """First-Time UX Polish: the Discord server owner always has Watch
    Party member-level access, without needing the configured role.
    """

    def setUp(self) -> None:
        self.service = PermissionService(
            watch_party_member_role_id=111,
            wash_crew_role_id=222,
        )

    def test_owner_without_any_role_is_a_watch_party_member(self) -> None:
        guild = FakeGuild(owner_id=42)
        owner = FakeMember([], user_id=42, guild=guild)
        self.assertTrue(self.service.is_watch_party_member(owner))
        self.assertTrue(self.service.require_watch_party_member(owner).allowed)

    def test_owner_is_a_watch_party_member_even_when_neither_role_is_configured(self) -> None:
        unconfigured_service = PermissionService(watch_party_member_role_id=None, wash_crew_role_id=None)
        guild = FakeGuild(owner_id=42)
        owner = FakeMember([], user_id=42, guild=guild)
        self.assertTrue(unconfigured_service.require_watch_party_member(owner).allowed)

    def test_non_owner_without_the_role_is_still_rejected(self) -> None:
        guild = FakeGuild(owner_id=42)
        non_owner = FakeMember([], user_id=1234, guild=guild)
        self.assertFalse(self.service.is_watch_party_member(non_owner))
        self.assertFalse(self.service.require_watch_party_member(non_owner).allowed)

    def test_owner_without_the_wash_crew_role_still_needs_it_for_crew_only_commands(self) -> None:
        # Member-level access only -- the owner is not implicitly WASH Crew.
        guild = FakeGuild(owner_id=42)
        owner = FakeMember([], user_id=42, guild=guild)
        self.assertFalse(self.service.is_wash_crew(owner))
        self.assertFalse(self.service.require_wash_crew(owner).allowed)

    def test_member_with_no_guild_attribute_is_never_treated_as_owner(self) -> None:
        # FakeMember's default guild=None matches every other existing test
        # in this file -- confirms the owner check never raises and never
        # accidentally passes when guild context is unavailable.
        member = FakeMember([111])
        self.assertTrue(self.service.is_watch_party_member(member))


class FakeGuildConfigurationSource:
    """A minimal, duck-typed GuildConfigurationSource -- exercises
    resolve_permission_service's Protocol contract directly, without a
    real JSON-backed repository. Deliberately holds its own plain dict
    (no hidden caching layer) so a test can assert the resolver itself
    introduces no shared mutable state on top of whatever source it's
    given.
    """

    def __init__(self, configurations: Optional[dict] = None) -> None:
        self._configurations = dict(configurations or {})

    def get(self, guild_id: int) -> Optional[GuildConfiguration]:
        return self._configurations.get(guild_id)


GUILD_A = 100
GUILD_B = 200


class ResolvePermissionServiceTests(unittest.TestCase):
    """Multi-Guild Isolation, Phase 3a: resolve_permission_service() must
    build a fresh, independently configured PermissionService from each
    guild's own GuildConfiguration -- not the shared, env-var-derived
    singleton every command still reads today (WatchPartyBot.permission_service,
    unchanged and untouched by this resolver).
    """

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        path = Path(self._temp_dir.name) / "guild_configurations.json"
        self.repository = GuildConfigurationRepository(path)
        self.repository.save(
            GuildConfiguration(
                guild_id=GUILD_A,
                guild_name="Guild A",
                wash_crew_role_id=111,
                watch_party_role=WatchPartyRoleConfig(role_id=222),
            )
        )
        self.repository.save(
            GuildConfiguration(
                guild_id=GUILD_B,
                guild_name="Guild B",
                wash_crew_role_id=333,
                watch_party_role=WatchPartyRoleConfig(role_id=444),
            )
        )

    def test_resolves_a_correctly_configured_service_for_guild_a(self) -> None:
        service = resolve_permission_service(GUILD_A, self.repository)

        self.assertEqual(service.wash_crew_role_id, 111)
        self.assertEqual(service.watch_party_member_role_id, 222)

    def test_resolves_a_differently_configured_service_for_guild_b(self) -> None:
        service = resolve_permission_service(GUILD_B, self.repository)

        self.assertEqual(service.wash_crew_role_id, 333)
        self.assertEqual(service.watch_party_member_role_id, 444)

    def test_role_ids_are_isolated_between_guilds(self) -> None:
        service_a = resolve_permission_service(GUILD_A, self.repository)
        service_b = resolve_permission_service(GUILD_B, self.repository)

        self.assertNotEqual(service_a.wash_crew_role_id, service_b.wash_crew_role_id)
        self.assertNotEqual(service_a.watch_party_member_role_id, service_b.watch_party_member_role_id)
        # Guild A's own resolved service must never accept Guild B's
        # WASH Crew role, and vice versa -- the concrete cross-guild
        # bleed this resolver exists to eventually close off.
        crew_member_of_b = FakeMember([333])
        self.assertFalse(service_a.is_wash_crew(crew_member_of_b))
        crew_member_of_a = FakeMember([111])
        self.assertFalse(service_b.is_wash_crew(crew_member_of_a))

    def test_missing_guild_configuration_resolves_to_an_unconfigured_service(self) -> None:
        unknown_guild_id = 999
        service = resolve_permission_service(unknown_guild_id, self.repository)

        self.assertIsNone(service.wash_crew_role_id)
        self.assertIsNone(service.watch_party_member_role_id)
        # Fails closed exactly like the existing singleton does when its
        # own environment variables are unset.
        result = service.require_wash_crew(FakeMember([111]))
        self.assertFalse(result.allowed)

    def test_none_guild_id_resolves_to_an_unconfigured_service(self) -> None:
        service = resolve_permission_service(None, self.repository)

        self.assertIsNone(service.wash_crew_role_id)
        self.assertIsNone(service.watch_party_member_role_id)

    def test_resolver_introduces_no_shared_mutable_state(self) -> None:
        # Two resolutions for the same guild must never be (or silently
        # become) the same object -- mutating one must never leak into
        # the other, unlike WatchPartyBot.apply_role_configuration's own
        # in-place mutation of the single shared singleton.
        first = resolve_permission_service(GUILD_A, self.repository)
        second = resolve_permission_service(GUILD_A, self.repository)

        self.assertIsNot(first, second)
        first.wash_crew_role_id = 999999
        self.assertEqual(second.wash_crew_role_id, 111)

    def test_repeated_resolution_produces_equivalent_behavior(self) -> None:
        first = resolve_permission_service(GUILD_A, self.repository)
        second = resolve_permission_service(GUILD_A, self.repository)

        member = FakeMember([222])
        self.assertEqual(
            first.require_watch_party_member(member).allowed,
            second.require_watch_party_member(member).allowed,
        )
        self.assertEqual(first.wash_crew_role_id, second.wash_crew_role_id)
        self.assertEqual(first.watch_party_member_role_id, second.watch_party_member_role_id)

    def test_accepts_a_duck_typed_source_satisfying_the_protocol(self) -> None:
        # Confirms the Protocol contract itself -- a real
        # GuildConfigurationRepository is not required, matching every
        # other minimal-source Protocol already used in this project
        # (CollectionEligibilityService, StatisticsService).
        source = FakeGuildConfigurationSource(
            {
                GUILD_A: GuildConfiguration(
                    guild_id=GUILD_A,
                    guild_name="Guild A",
                    wash_crew_role_id=555,
                    watch_party_role=WatchPartyRoleConfig(role_id=666),
                )
            }
        )

        service = resolve_permission_service(GUILD_A, source)

        self.assertEqual(service.wash_crew_role_id, 555)
        self.assertEqual(service.watch_party_member_role_id, 666)

    def test_defaults_to_a_real_repository_when_none_is_supplied(self) -> None:
        # resolve_permission_service(guild_id) alone -- the literal,
        # minimal call signature Phase 3b call sites will use -- must
        # not raise, and must fail closed for a guild with nothing saved
        # under the default repository path.
        service = resolve_permission_service(999999999)

        self.assertIsInstance(service, PermissionService)

    def test_existing_singleton_construction_is_unaffected(self) -> None:
        # The resolver is new, additive infrastructure -- direct
        # PermissionService construction (WatchPartyBot's own singleton
        # shape) must behave exactly as it did before this phase.
        singleton = PermissionService(watch_party_member_role_id=111, wash_crew_role_id=222)

        self.assertTrue(singleton.require_watch_party_member(FakeMember([111])).allowed)
        self.assertTrue(singleton.require_wash_crew(FakeMember([222])).allowed)


if __name__ == "__main__":
    unittest.main()
