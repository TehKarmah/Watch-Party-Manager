"""Tests for the slash-command group registrations: /database, /vote,
/random, /join, /membership, /maintenance, /suggestion, and (dormant)
/watch-party each expose the expected subcommands, and each subcommand
still delegates to the same module-level handler its former top-level
(or differently-named) command used, so behavior is provably unchanged
even though its registration/name moved.

/join, /maintenance, and /suggestion are new groups introduced by the
Slash-Command UX Audit, replacing the former bare, underscore-bearing
top-level commands /join_watch_party, /repair_suggestions, /backup,
/restore, /factory_reset, /import, /remove, and /edit_suggestion; the
former /watch_party group (membership administration) was renamed to
/membership for the same reason. No compatibility aliases were kept for
any of these -- see test_at_least_the_known_top_level_commands_are_covered
in test_command_descriptions.py for the full before/after mapping.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import (
    DatabaseGroup,
    JoinGroup,
    JoinWatchGroup,
    MaintenanceGroup,
    MembershipGroup,
    RandomGroup,
    SuggestionGroup,
    VotingGroup,
    WatchPartyBot,
    WatchPartyEventGroup,
)
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
        # Release UX & Command Surface Cleanup: move/backup/reset/remove
        # no longer have their own top-level slash commands -- every one
        # of those actions is reachable only through /database manage
        # now. /database restore is the one necessary exception (a file
        # attachment can't be collected through a button/modal).
        group = DatabaseGroup(MinimalFakeBot())
        self.assertEqual(
            {command.name for command in group.commands},
            {"add", "manage", "list", "health", "restore"},
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


class RandomGroupRegistrationTests(unittest.TestCase):
    def test_group_name_is_random(self) -> None:
        group = RandomGroup(MinimalFakeBot())
        self.assertEqual(group.name, "random")

    def test_exposes_exactly_the_watch_subcommand(self) -> None:
        # Command Rename: /random_watch (underscore) is gone -- the only
        # way to reach it now is the grouped /random watch.
        group = RandomGroup(MinimalFakeBot())
        self.assertEqual({command.name for command in group.commands}, {"watch"})

    async def test_watch_delegates_to_handle_random_watch(self) -> None:
        bot = MinimalFakeBot()
        group = RandomGroup(bot)
        interaction = FakeInteraction()

        with patch("watch_party_manager.bot.handle_random_watch", new=AsyncMock()) as mock_handler:
            await group.watch.callback(group, interaction)

        mock_handler.assert_awaited_once_with(interaction, bot)


class JoinGroupRegistrationTests(unittest.TestCase):
    def test_group_name_is_join(self) -> None:
        group = JoinGroup(MinimalFakeBot())
        self.assertEqual(group.name, "join")

    def test_exposes_exactly_the_nested_watch_subcommand_group(self) -> None:
        # Slash-Command UX Audit: /join_watch_party (underscore) is gone --
        # the only way to reach it now is the grouped /join watch party.
        group = JoinGroup(MinimalFakeBot())
        self.assertEqual({command.name for command in group.commands}, {"watch"})
        nested = next(command for command in group.commands if command.name == "watch")
        self.assertIsInstance(nested, JoinWatchGroup)

    def test_nested_watch_group_exposes_exactly_the_party_subcommand(self) -> None:
        group = JoinWatchGroup(MinimalFakeBot())
        self.assertEqual({command.name for command in group.commands}, {"party"})

    async def test_party_delegates_to_handle_join_watch_party(self) -> None:
        bot = MinimalFakeBot()
        group = JoinWatchGroup(bot)
        interaction = FakeInteraction()

        with patch("watch_party_manager.bot.handle_join_watch_party", new=AsyncMock()) as mock_handler:
            await group.party.callback(group, interaction)

        mock_handler.assert_awaited_once_with(interaction, bot)


class MaintenanceGroupRegistrationTests(unittest.TestCase):
    def test_group_name_is_maintenance(self) -> None:
        group = MaintenanceGroup(MinimalFakeBot())
        self.assertEqual(group.name, "maintenance")

    def test_exposes_exactly_the_expected_subcommands(self) -> None:
        # Slash-Command UX Audit: consolidates the former bare /backup,
        # /restore (whole-instance), /import, /factory_reset (renamed to
        # "reset"), and /repair_suggestions (renamed to "repair") -- all
        # previously separate top-level commands, already documented
        # together under help_registry.py's "WASH Crew: Maintenance"
        # section before this audit gave them a matching Discord group.
        group = MaintenanceGroup(MinimalFakeBot())
        self.assertEqual(
            {command.name for command in group.commands}, {"backup", "restore", "import", "reset", "repair"}
        )

    async def test_restore_delegates_to_handle_restore(self) -> None:
        bot = MinimalFakeBot()
        group = MaintenanceGroup(bot)
        interaction = FakeInteraction()

        with patch("watch_party_manager.bot.handle_restore", new=AsyncMock()) as mock_handler:
            await group.restore.callback(group, interaction, None, None)

        mock_handler.assert_awaited_once_with(interaction, bot, None, None)

    async def test_reset_delegates_to_handle_factory_reset(self) -> None:
        bot = MinimalFakeBot()
        group = MaintenanceGroup(bot)
        interaction = FakeInteraction()

        with patch("watch_party_manager.bot.handle_factory_reset", new=AsyncMock()) as mock_handler:
            await group.reset.callback(group, interaction)

        mock_handler.assert_awaited_once_with(interaction, bot)


class SuggestionGroupRegistrationTests(unittest.TestCase):
    def test_group_name_is_suggestion(self) -> None:
        group = SuggestionGroup(MinimalFakeBot())
        self.assertEqual(group.name, "suggestion")

    def test_exposes_exactly_the_expected_subcommands(self) -> None:
        # Slash-Command UX Audit: consolidates the former bare
        # /edit_suggestion (renamed to "edit") and /remove (renamed to
        # "remove" here, i.e. unchanged in spelling but regrouped) --
        # both WASH-Crew-only, single-suggestion administrative actions.
        group = SuggestionGroup(MinimalFakeBot())
        self.assertEqual({command.name for command in group.commands}, {"edit", "remove"})

    async def test_edit_delegates_to_handle_edit_suggestion(self) -> None:
        bot = MinimalFakeBot()
        group = SuggestionGroup(bot)
        interaction = FakeInteraction()

        with patch("watch_party_manager.bot.handle_edit_suggestion", new=AsyncMock()) as mock_handler:
            await group.edit.callback(group, interaction, "#0007")

        mock_handler.assert_awaited_once_with(interaction, bot, "#0007")

    async def test_remove_delegates_to_handle_remove_suggestion(self) -> None:
        bot = MinimalFakeBot()
        group = SuggestionGroup(bot)
        interaction = FakeInteraction()

        with patch("watch_party_manager.bot.handle_remove_suggestion", new=AsyncMock()) as mock_handler:
            await group.remove.callback(group, interaction, "Alien")

        mock_handler.assert_awaited_once_with(interaction, bot, "Alien")


class WatchPartyEventGroupRegistrationTests(unittest.TestCase):
    def test_group_name_is_hyphenated_and_distinct_from_the_membership_group(self) -> None:
        group = WatchPartyEventGroup(MinimalFakeBot())
        # Deliberately "watch-party" (hyphen) -- this group is currently
        # dormant/unregistered (see WatchPartySchedulingHiddenFromV1Tests
        # below) and out of the Slash-Command UX Audit's scope (Section 1:
        # "audit every *registered* slash command"); see its own
        # docstring for why it originally chose a hyphen over an
        # underscore, and why it could now revert to "watch_party" (freed
        # up by the membership group's rename to /membership) if it's
        # ever re-registered.
        self.assertEqual(group.name, "watch-party")

    def test_exposes_exactly_the_expected_subcommands(self) -> None:
        group = WatchPartyEventGroup(MinimalFakeBot())
        self.assertEqual(
            {command.name for command in group.commands}, {"schedule", "status", "reschedule", "cancel"}
        )


class WatchPartySchedulingHiddenFromV1Tests(unittest.IsolatedAsyncioTestCase):
    """v1 Final Polish, Section 12: the scheduled watch party workflow
    (/watch-party schedule/reschedule/cancel/status) is hidden from v1 by
    simply never registering WatchPartyEventGroup on the real command
    tree -- the class above proves the group itself is still fully
    intact, unchanged, and ready to be re-registered later; this proves
    the actual bot never exposes it as a command in the meantime.
    """

    async def test_watch_party_group_is_not_registered_on_the_real_command_tree(self) -> None:
        bot = WatchPartyBot(token="test-token")
        bot.tree.sync = AsyncMock(return_value=[])

        await bot.setup_hook()

        top_level_names = {command.name for command in bot.tree.get_commands()}
        self.assertNotIn("watch-party", top_level_names)
        # The unrelated, still-active groups/commands must be unaffected.
        # Slash-Command UX Audit: the former /watch_party (membership
        # administration, underscore) is now /membership; the former bare
        # /join_watch_party is now the grouped /join watch party.
        self.assertIn("membership", top_level_names)
        self.assertNotIn("watch_party", top_level_names)
        self.assertIn("database", top_level_names)
        self.assertIn("vote", top_level_names)
        self.assertIn("join", top_level_names)
        self.assertNotIn("join_watch_party", top_level_names)
        self.assertIn("maintenance", top_level_names)
        self.assertIn("suggestion", top_level_names)
        # Command Rename: /random_watch (underscore, standalone) is gone;
        # /random (a group with one "watch" subcommand) replaces it, with
        # no compatibility alias left on the tree.
        self.assertIn("random", top_level_names)
        self.assertNotIn("random_watch", top_level_names)
        random_group = next(command for command in bot.tree.get_commands() if command.name == "random")
        self.assertEqual({subcommand.name for subcommand in random_group.commands}, {"watch"})


if __name__ == "__main__":
    unittest.main()
