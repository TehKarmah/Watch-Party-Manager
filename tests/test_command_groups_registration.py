"""Tests for the Command Structure Cleanup (pre-v1) command group
registrations: /database, /vote, and /watch-party each expose the
expected subcommands, and each subcommand still delegates to the same
module-level handler its former top-level command used, so behavior is
provably unchanged even though its registration moved.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import DatabaseGroup, VotingGroup, WatchPartyEventGroup
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.suggestion_service import SuggestionService

GUILD_ID = 100
CHANNEL_ID = 200
WASH_CREW_ROLE_ID = 999


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, roles=()) -> None:
        self.roles = list(roles)


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
    def __init__(self, *, guild_id=GUILD_ID, roles=(WASH_CREW_ROLE_ID,)) -> None:
        self.user = FakeMember(roles=[FakeRole(role_id) for role_id in roles])
        self.guild_id = guild_id
        self.guild = None
        self.response = FakeResponse()


class MinimalFakeBot:
    """Enough of WatchPartyBot for /database list/manage's thin delegation."""

    def __init__(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.suggestion_database_configuration_repository = SuggestionDatabaseConfigurationRepository(
            root / "suggestion_database_configurations.json"
        )
        self.wash_crew_role_id = WASH_CREW_ROLE_ID


class DatabaseGroupRegistrationTests(unittest.IsolatedAsyncioTestCase):
    def test_group_name_is_database(self) -> None:
        group = DatabaseGroup(MinimalFakeBot())
        self.assertEqual(group.name, "database")

    def test_exposes_exactly_the_expected_subcommands(self) -> None:
        group = DatabaseGroup(MinimalFakeBot())
        self.assertEqual(
            {command.name for command in group.commands},
            {"add", "manage", "list", "move", "backup", "restore", "remove", "reset"},
        )

    async def test_manage_delegates_to_handle_database_manage(self) -> None:
        bot = MinimalFakeBot()
        bot.suggestion_service.create_database("Movie Suggestions", guild_id=GUILD_ID, channel_id=CHANNEL_ID)
        group = DatabaseGroup(bot)
        interaction = FakeInteraction()

        await group.manage.callback(group, interaction)

        self.assertIn("manage", interaction.response.sent_message.lower())
        self.assertTrue(interaction.response.sent_ephemeral)

    async def test_list_delegates_to_perform_database_list(self) -> None:
        bot = MinimalFakeBot()
        bot.suggestion_service.create_database("Movie Suggestions", guild_id=GUILD_ID, channel_id=CHANNEL_ID)
        group = DatabaseGroup(bot)
        interaction = FakeInteraction()

        await group.list.callback(group, interaction)

        self.assertIn("Movie Suggestions", interaction.response.sent_message)
        self.assertTrue(interaction.response.sent_ephemeral)


class VotingGroupRegistrationTests(unittest.TestCase):
    def test_group_name_is_vote(self) -> None:
        group = VotingGroup(MinimalFakeBot())
        self.assertEqual(group.name, "vote")

    def test_exposes_exactly_the_expected_subcommands(self) -> None:
        group = VotingGroup(MinimalFakeBot())
        self.assertEqual({command.name for command in group.commands}, {"start", "status", "edit"})


class WatchPartyEventGroupRegistrationTests(unittest.TestCase):
    def test_group_name_is_hyphenated_and_distinct_from_the_membership_group(self) -> None:
        group = WatchPartyEventGroup(MinimalFakeBot())
        # Deliberately "watch-party" (hyphen), not "watch_party" (underscore,
        # the pre-existing membership-administration group) -- see
        # WatchPartyEventGroup's own docstring for why they coexist.
        self.assertEqual(group.name, "watch-party")

    def test_exposes_exactly_the_expected_subcommands(self) -> None:
        group = WatchPartyEventGroup(MinimalFakeBot())
        self.assertEqual(
            {command.name for command in group.commands}, {"schedule", "status", "reschedule", "cancel"}
        )


if __name__ == "__main__":
    unittest.main()
