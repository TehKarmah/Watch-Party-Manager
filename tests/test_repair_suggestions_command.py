import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import perform_repair_suggestions
from watch_party_manager.services.suggestion_repair_service import SuggestionRepairReport


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeUser:
    def __init__(self, *role_ids: int) -> None:
        self.roles = [FakeRole(role_id) for role_id in role_ids]


class RepairSuggestionsCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_fails_closed_when_role_not_configured(self) -> None:
        service = AsyncMock()
        message, ephemeral = await perform_repair_suggestions(service, FakeUser(7), None)
        self.assertTrue(ephemeral)
        self.assertIn("WASH_CREW_ROLE_ID", message)
        service.repair_all.assert_not_awaited()

    async def test_rejects_user_without_wash_crew_role(self) -> None:
        service = AsyncMock()
        message, ephemeral = await perform_repair_suggestions(service, FakeUser(8), 7)
        self.assertTrue(ephemeral)
        self.assertIn("WASH Crew", message)
        service.repair_all.assert_not_awaited()

    async def test_wash_crew_member_receives_ephemeral_report(self) -> None:
        service = AsyncMock()
        service.repair_all.return_value = SuggestionRepairReport(scanned=2, repaired=1, removed=1)
        message, ephemeral = await perform_repair_suggestions(service, FakeUser(7), 7)
        self.assertTrue(ephemeral)
        self.assertIn("Suggestion Repair Complete", message)
        self.assertIn("Repaired: 1", message)
        service.repair_all.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
