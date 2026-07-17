import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.services.permission_service import PermissionService


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, role_ids=()) -> None:
        self.roles = [FakeRole(role_id) for role_id in role_ids]


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


if __name__ == "__main__":
    unittest.main()
