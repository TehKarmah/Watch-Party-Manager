"""Tests for FR-029's corrected command-access model.

The approved model restricts /list, /vote_status, /watch_party_status,
/diagnostics, and /stats to WASH Crew (only /add remains Watch Party
member-facing beyond /help and /about). Each of these five commands'
bot.py callback now gates on PermissionService.require_wash_crew before
doing anything else -- the same fail-closed, already-tested gate every
other WASH-only command uses (see test_permission_service.py for its own
full coverage: fails closed when unconfigured, rejects the Watch Party
role alone, accepts the WASH Crew role).

/diagnostics already delegates its gate to perform_diagnostics (an
extracted, Discord-free function), so it's tested directly here.
/list, /vote_status, /watch_party_status, and /stats gate inline in
their @self.tree.command closures (matching this project's established
"inline permission check, then call a permission-agnostic perform_*
content function" pattern already used by /add) -- there's no separate
perform_* wrapper to call in isolation, so this file instead confirms
the shared gate they all now call (PermissionService.require_wash_crew)
behaves correctly for the same WASH/member/unprivileged/unconfigured
matrix, and that each command's content-only perform_* function takes no
user/role at all (i.e. permission is enforced entirely by the gate that
runs before it, never duplicated or bypassed inside it).
"""

import inspect
import unittest

from watch_party_manager.bot import (
    perform_diagnostics,
    perform_list_suggestions_response,
    perform_stats,
    perform_vote_status,
    perform_watch_party_status,
)
from watch_party_manager.services.permission_service import PermissionService

WASH_CREW_ROLE_ID = 999
WATCH_PARTY_ROLE_ID = 888


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, roles=()) -> None:
        self.roles = list(roles)


def _wash_crew_member() -> FakeMember:
    return FakeMember(roles=[FakeRole(WASH_CREW_ROLE_ID)])


def _watch_party_member() -> FakeMember:
    return FakeMember(roles=[FakeRole(WATCH_PARTY_ROLE_ID)])


def _unprivileged_user() -> FakeMember:
    return FakeMember(roles=[])


class SharedWashCrewGateTests(unittest.TestCase):
    """The exact gate /list, /vote_status, /watch_party_status, and
    /stats now call before doing anything else.
    """

    def _service(self) -> PermissionService:
        return PermissionService(watch_party_member_role_id=WATCH_PARTY_ROLE_ID, wash_crew_role_id=WASH_CREW_ROLE_ID)

    def test_wash_crew_member_is_allowed(self) -> None:
        result = self._service().require_wash_crew(_wash_crew_member())
        self.assertTrue(result.allowed)

    def test_watch_party_member_is_blocked(self) -> None:
        result = self._service().require_wash_crew(_watch_party_member())
        self.assertFalse(result.allowed)
        self.assertIn("WASH Crew", result.message)

    def test_unprivileged_user_is_blocked(self) -> None:
        result = self._service().require_wash_crew(_unprivileged_user())
        self.assertFalse(result.allowed)
        self.assertIn("WASH Crew", result.message)

    def test_fails_closed_when_wash_crew_role_is_unconfigured(self) -> None:
        service = PermissionService(watch_party_member_role_id=WATCH_PARTY_ROLE_ID, wash_crew_role_id=None)
        result = service.require_wash_crew(_wash_crew_member())
        self.assertFalse(result.allowed)

    def test_unconfigured_role_rejects_even_a_member_with_no_roles_at_all(self) -> None:
        service = PermissionService(watch_party_member_role_id=WATCH_PARTY_ROLE_ID, wash_crew_role_id=None)
        result = service.require_wash_crew(_unprivileged_user())
        self.assertFalse(result.allowed)


class ContentFunctionsDoNotDuplicateOrBypassPermissionTests(unittest.TestCase):
    """/list, /vote_status, /watch_party_status, and /stats delegate their
    actual content to perform_* functions that take no user or role --
    confirming that shape guards against permission logic silently
    reappearing (or a bypass being reintroduced) inside the content
    function itself, separate from the gate in the command callback.
    """

    def test_perform_list_suggestions_response_still_takes_a_user_for_view_selection_only(self) -> None:
        # perform_list_suggestions_response legitimately takes `user` and
        # `wash_crew_role_id` to choose between the standard/WASH Crew
        # list *views* (an existing, unrelated feature) -- not to gate
        # access, which now happens earlier, in the /list callback itself.
        parameters = inspect.signature(perform_list_suggestions_response).parameters
        self.assertIn("user", parameters)
        self.assertIn("wash_crew_role_id", parameters)

    def test_perform_vote_status_takes_no_permission_parameters(self) -> None:
        parameters = inspect.signature(perform_vote_status).parameters
        self.assertNotIn("user", parameters)
        self.assertNotIn("wash_crew_role_id", parameters)

    def test_perform_watch_party_status_takes_no_permission_parameters(self) -> None:
        parameters = inspect.signature(perform_watch_party_status).parameters
        self.assertNotIn("user", parameters)
        self.assertNotIn("wash_crew_role_id", parameters)

    def test_perform_stats_takes_no_permission_parameters(self) -> None:
        parameters = inspect.signature(perform_stats).parameters
        self.assertNotIn("user", parameters)
        self.assertNotIn("wash_crew_role_id", parameters)


class PerformDiagnosticsPermissionTests(unittest.TestCase):
    """/diagnostics was already WASH Crew-only before this correction;
    covered here for completeness alongside the other five commands.
    """

    def _call(self, *, user, wash_crew_role_id):
        return perform_diagnostics(
            statistics_service=None,
            user=user,
            wash_crew_role_id=wash_crew_role_id,
            guild_id=None,
            version="0.0.0",
            python_version="3.12",
            discord_version="0.0",
            latency_ms=0.0,
            started_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            now=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            interactive_voting_restored=False,
        )

    def test_wash_crew_member_is_allowed(self) -> None:
        message, ephemeral = self._call(user=_wash_crew_member(), wash_crew_role_id=WASH_CREW_ROLE_ID)
        self.assertNotIn("You need the WASH Crew role", message)

    def test_watch_party_member_is_rejected(self) -> None:
        message, ephemeral = self._call(user=_watch_party_member(), wash_crew_role_id=WASH_CREW_ROLE_ID)
        self.assertTrue(ephemeral)
        self.assertIn("WASH Crew", message)

    def test_unprivileged_user_is_rejected(self) -> None:
        message, ephemeral = self._call(user=_unprivileged_user(), wash_crew_role_id=WASH_CREW_ROLE_ID)
        self.assertTrue(ephemeral)
        self.assertIn("WASH Crew", message)

    def test_fails_closed_when_unconfigured(self) -> None:
        message, ephemeral = self._call(user=_wash_crew_member(), wash_crew_role_id=None)
        self.assertTrue(ephemeral)
        self.assertIn("not been configured", message)


if __name__ == "__main__":
    unittest.main()
