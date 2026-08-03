import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.services.permission_service import PermissionService


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


if __name__ == "__main__":
    unittest.main()
