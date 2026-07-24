"""Tests for /remove's corrected permission model (FR-029 follow-up).

/remove was briefly (and incorrectly) opened to any Watch Party member.
The approved model restricts it to WASH Crew, matching /list,
/vote_status, /watch_party_status, and /stats. This covers
perform_remove_suggestion, the extracted, Discord-free logic the /remove
command callback now delegates to (mirroring perform_database_add's
existing shape: fail-closed permission check first, then the service
call).
"""

import tempfile
import unittest
from pathlib import Path

from watch_party_manager.bot import perform_remove_suggestion
from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.suggestion_service import SuggestionService

WASH_CREW_ROLE_ID = 999
WATCH_PARTY_ROLE_ID = 888


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, roles=()) -> None:
        self.roles = list(roles)


class PerformRemoveSuggestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        temp_path = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(temp_path / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(temp_path / "suggestion_databases.json"),
        )
        self.suggestion_service.suggest("The Matrix")

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _wash_crew_member(self) -> FakeMember:
        return FakeMember(roles=[FakeRole(WASH_CREW_ROLE_ID)])

    def _watch_party_member(self) -> FakeMember:
        return FakeMember(roles=[FakeRole(WATCH_PARTY_ROLE_ID)])

    def _unprivileged_user(self) -> FakeMember:
        return FakeMember(roles=[])

    def test_wash_crew_member_can_remove_a_suggestion(self) -> None:
        message, ephemeral, success = perform_remove_suggestion(
            self.suggestion_service, self._wash_crew_member(), WASH_CREW_ROLE_ID, "The Matrix"
        )

        self.assertTrue(success)
        self.assertFalse(ephemeral)
        self.assertIsNone(self.suggestion_service.get_suggestion(1))

    def test_watch_party_member_is_rejected(self) -> None:
        message, ephemeral, success = perform_remove_suggestion(
            self.suggestion_service, self._watch_party_member(), WASH_CREW_ROLE_ID, "The Matrix"
        )

        self.assertFalse(success)
        self.assertTrue(ephemeral)
        self.assertIn("WASH Crew", message)
        self.assertIsNotNone(self.suggestion_service.get_suggestion(1))

    def test_unprivileged_user_is_rejected(self) -> None:
        message, ephemeral, success = perform_remove_suggestion(
            self.suggestion_service, self._unprivileged_user(), WASH_CREW_ROLE_ID, "The Matrix"
        )

        self.assertFalse(success)
        self.assertTrue(ephemeral)
        self.assertIn("WASH Crew", message)
        self.assertIsNotNone(self.suggestion_service.get_suggestion(1))

    def test_fails_closed_when_wash_crew_role_is_unconfigured(self) -> None:
        message, ephemeral, success = perform_remove_suggestion(
            self.suggestion_service, self._wash_crew_member(), None, "The Matrix"
        )

        self.assertFalse(success)
        self.assertTrue(ephemeral)
        self.assertIn("not been configured", message)
        self.assertIsNotNone(self.suggestion_service.get_suggestion(1))

    def test_unconfigured_role_rejects_even_a_member_with_no_roles_at_all(self) -> None:
        message, ephemeral, success = perform_remove_suggestion(
            self.suggestion_service, self._unprivileged_user(), None, "The Matrix"
        )

        self.assertFalse(success)
        self.assertTrue(ephemeral)
        self.assertIn("not been configured", message)

    def test_removing_a_title_that_does_not_exist_still_requires_wash_crew(self) -> None:
        message, ephemeral, success = perform_remove_suggestion(
            self.suggestion_service, self._watch_party_member(), WASH_CREW_ROLE_ID, "Nonexistent Movie"
        )

        self.assertFalse(success)
        self.assertIn("WASH Crew", message)

    def test_wash_crew_removal_of_a_missing_title_relays_the_service_message(self) -> None:
        message, ephemeral, success = perform_remove_suggestion(
            self.suggestion_service, self._wash_crew_member(), WASH_CREW_ROLE_ID, "Nonexistent Movie"
        )

        self.assertFalse(success)
        self.assertFalse(ephemeral)


if __name__ == "__main__":
    unittest.main()
