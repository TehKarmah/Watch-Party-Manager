"""Tests for Requirement 5: /edit_vote's redesigned Change End Time menu --
End Now, Shorten Vote, Extend Vote, Set Exact End Time -- driven end-to-end
through handle_edit_vote, mirroring test_vote_context_resolution.py's
FakeBot/FakeInteraction fixture pattern.

Shorten Vote and Extend Vote apply a delta to the round's *current* end
time (never to "now"), using the shared duration parser (10m/1h/1d/1w).
Set Exact End Time is the pre-existing Discord Timestamp workflow, kept
unchanged and only renamed/promoted to this menu's top level.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import handle_edit_vote
from watch_party_manager.domain.vote import VoteVisibility
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.services.permission_service import PermissionService
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.vote_completion_service import VoteCompletionService
from watch_party_manager.services.vote_service import VoteService

GUILD_ID = 100
WASH_CREW_ROLE_ID = 999
WATCH_PARTY_MEMBER_ROLE_ID = 555
CHANNEL_ID = 201


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, role_ids=(), *, user_id: int = 1) -> None:
        self.roles = [FakeRole(role_id) for role_id in role_ids]
        self.id = user_id


class FakeGuild:
    def get_channel_or_thread(self, channel_id):
        return None


class FakeResponse:
    def __init__(self) -> None:
        self.sent_message = None
        self.sent_ephemeral = None
        self.sent_view = None

    async def send_message(self, content, ephemeral=False, view=None) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral
        self.sent_view = view

    async def send_modal(self, modal) -> None:
        self.sent_modal = modal


class FakeInteraction:
    def __init__(self, user=None, guild_id=GUILD_ID, channel_id=CHANNEL_ID) -> None:
        self.user = user if user is not None else FakeMember([WASH_CREW_ROLE_ID], user_id=1)
        self.guild = FakeGuild() if guild_id is not None else None
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.response = FakeResponse()


class FakeSchedulerHost:
    def __init__(self) -> None:
        self.scheduler_service = None


class FakeBot:
    def __init__(self, *, root: Path) -> None:
        self.suggestion_database_repository = JsonSuggestionDatabaseRepository(
            root / "suggestion_databases.json"
        )
        self.suggestion_repository = JsonSuggestionRepository(root / "suggestions.json")
        self.suggestion_database_configuration_repository = SuggestionDatabaseConfigurationRepository(
            root / "suggestion_database_configurations.json"
        )
        self.suggestion_service = SuggestionService(
            repository=self.suggestion_repository, database_repository=self.suggestion_database_repository
        )
        self.vote_service = VoteService(
            self.suggestion_service, repository=JsonVoteRepository(root / "voting.json")
        )
        self.vote_completion_service = VoteCompletionService(self.vote_service, self.suggestion_service)
        self.permission_service = PermissionService(
            watch_party_member_role_id=WATCH_PARTY_MEMBER_ROLE_ID, wash_crew_role_id=WASH_CREW_ROLE_ID
        )
        self.wash_crew_role_id = WASH_CREW_ROLE_ID
        self.guild_configuration_repository = None
        self.scheduler_host = FakeSchedulerHost()

    def get_channel(self, channel_id):
        return None

    async def fetch_channel(self, channel_id):
        raise RuntimeError("no channel available in this test")


class EditVoteShortenExtendTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self._temp_dir.name)
        self.bot = FakeBot(root=self.root)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _crew_member(self) -> FakeMember:
        return FakeMember([WASH_CREW_ROLE_ID], user_id=1)

    async def _open_round(self, *, closes_at=None):
        closes_at = closes_at or (datetime.now(timezone.utc) + timedelta(days=7))
        database = self.bot.suggestion_service.create_database(
            "Movies", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database
        candidates = [
            self.bot.suggestion_service.suggest(title, database_id=database.database_id).watch_item
            for title in ("A", "B")
        ]
        vote_round = self.bot.vote_service.create_round(
            visibility=VoteVisibility.VISIBLE,
            closes_at=closes_at,
            candidate_suggestion_ids=[candidate.id for candidate in candidates],
            database_id=database.database_id,
        ).vote_round
        return vote_round

    async def _open_change_end_time_menu(self):
        vote_round = await self._open_round()
        interaction = FakeInteraction(user=self._crew_member())
        await handle_edit_vote(interaction, self.bot)
        management_view = interaction.response.sent_view

        change_end_time_button = next(
            child for child in management_view.children if child.label == "Change End Time"
        )
        menu_interaction = FakeInteraction(user=self._crew_member())
        await change_end_time_button.callback(interaction=menu_interaction)
        return vote_round, menu_interaction.response.sent_view

    async def test_menu_has_the_four_expected_options(self) -> None:
        _, menu_view = await self._open_change_end_time_menu()

        labels = [child.label for child in menu_view.children]
        self.assertEqual(labels, ["End Now", "Shorten Vote", "Extend Vote", "Set Exact End Time"])

    async def test_shorten_vote_one_hour_subtracts_from_the_current_end_time(self) -> None:
        vote_round, menu_view = await self._open_change_end_time_menu()
        original_closes_at = vote_round.closes_at
        shorten_button = next(child for child in menu_view.children if child.label == "Shorten Vote")

        shorten_interaction = FakeInteraction(user=self._crew_member())
        await shorten_button.callback(interaction=shorten_interaction)
        shorten_menu = shorten_interaction.response.sent_view
        one_hour_button = next(child for child in shorten_menu.children if child.label == "1 Hour")

        pick_interaction = FakeInteraction(user=self._crew_member())
        await one_hour_button.callback(interaction=pick_interaction)

        updated = self.bot.vote_service.get_round(vote_round.id)
        self.assertAlmostEqual(
            updated.closes_at.timestamp(), (original_closes_at - timedelta(hours=1)).timestamp(), delta=2
        )

    async def test_extend_vote_one_day_adds_to_the_current_end_time(self) -> None:
        vote_round, menu_view = await self._open_change_end_time_menu()
        original_closes_at = vote_round.closes_at
        extend_button = next(child for child in menu_view.children if child.label == "Extend Vote")

        extend_interaction = FakeInteraction(user=self._crew_member())
        await extend_button.callback(interaction=extend_interaction)
        extend_menu = extend_interaction.response.sent_view
        one_day_button = next(child for child in extend_menu.children if child.label == "1 Day")

        pick_interaction = FakeInteraction(user=self._crew_member())
        await one_day_button.callback(interaction=pick_interaction)

        updated = self.bot.vote_service.get_round(vote_round.id)
        self.assertAlmostEqual(
            updated.closes_at.timestamp(), (original_closes_at + timedelta(days=1)).timestamp(), delta=2
        )

    async def test_shorten_vote_custom_accepts_the_shared_duration_syntax(self) -> None:
        vote_round, menu_view = await self._open_change_end_time_menu()
        original_closes_at = vote_round.closes_at
        shorten_button = next(child for child in menu_view.children if child.label == "Shorten Vote")

        shorten_interaction = FakeInteraction(user=self._crew_member())
        await shorten_button.callback(interaction=shorten_interaction)
        shorten_menu = shorten_interaction.response.sent_view
        custom_button = next(child for child in shorten_menu.children if child.label == "Custom...")

        custom_interaction = FakeInteraction(user=self._crew_member())
        await custom_button.callback(interaction=custom_interaction)
        modal = custom_interaction.response.sent_modal
        self.assertEqual(modal.title, "Shorten Vote")
        modal.duration_input._value = "30m"

        submit_interaction = FakeInteraction(user=self._crew_member())
        await modal.on_submit(interaction=submit_interaction)

        updated = self.bot.vote_service.get_round(vote_round.id)
        self.assertAlmostEqual(
            updated.closes_at.timestamp(), (original_closes_at - timedelta(minutes=30)).timestamp(), delta=2
        )

    async def test_extend_vote_custom_accepts_the_shared_duration_syntax(self) -> None:
        vote_round, menu_view = await self._open_change_end_time_menu()
        original_closes_at = vote_round.closes_at
        extend_button = next(child for child in menu_view.children if child.label == "Extend Vote")

        extend_interaction = FakeInteraction(user=self._crew_member())
        await extend_button.callback(interaction=extend_interaction)
        extend_menu = extend_interaction.response.sent_view
        custom_button = next(child for child in extend_menu.children if child.label == "Custom...")

        custom_interaction = FakeInteraction(user=self._crew_member())
        await custom_button.callback(interaction=custom_interaction)
        modal = custom_interaction.response.sent_modal
        self.assertEqual(modal.title, "Extend Vote")
        modal.duration_input._value = "1w"

        submit_interaction = FakeInteraction(user=self._crew_member())
        await modal.on_submit(interaction=submit_interaction)

        updated = self.bot.vote_service.get_round(vote_round.id)
        self.assertAlmostEqual(
            updated.closes_at.timestamp(), (original_closes_at + timedelta(weeks=1)).timestamp(), delta=2
        )

    async def test_shorten_vote_custom_rejects_malformed_duration(self) -> None:
        vote_round, menu_view = await self._open_change_end_time_menu()
        original_closes_at = vote_round.closes_at
        shorten_button = next(child for child in menu_view.children if child.label == "Shorten Vote")

        shorten_interaction = FakeInteraction(user=self._crew_member())
        await shorten_button.callback(interaction=shorten_interaction)
        custom_button = next(
            child for child in shorten_interaction.response.sent_view.children if child.label == "Custom..."
        )

        custom_interaction = FakeInteraction(user=self._crew_member())
        await custom_button.callback(interaction=custom_interaction)
        modal = custom_interaction.response.sent_modal
        modal.duration_input._value = "soon"

        submit_interaction = FakeInteraction(user=self._crew_member())
        await modal.on_submit(interaction=submit_interaction)

        self.assertIn("whole number", submit_interaction.response.sent_message)
        updated = self.bot.vote_service.get_round(vote_round.id)
        self.assertEqual(updated.closes_at, original_closes_at)

    async def test_shortening_past_the_current_time_is_rejected(self) -> None:
        # The round closes in 30 minutes; shortening by 1 day would move
        # the deadline into the past, which must be rejected up front
        # rather than silently creating an already-closed-looking round.
        closes_at = datetime.now(timezone.utc) + timedelta(minutes=30)
        vote_round = await self._open_round(closes_at=closes_at)
        interaction = FakeInteraction(user=self._crew_member())
        await handle_edit_vote(interaction, self.bot)
        change_end_time_button = next(
            child for child in interaction.response.sent_view.children if child.label == "Change End Time"
        )
        menu_interaction = FakeInteraction(user=self._crew_member())
        await change_end_time_button.callback(interaction=menu_interaction)
        shorten_button = next(
            child for child in menu_interaction.response.sent_view.children if child.label == "Shorten Vote"
        )

        shorten_interaction = FakeInteraction(user=self._crew_member())
        await shorten_button.callback(interaction=shorten_interaction)
        one_day_button = next(
            child for child in shorten_interaction.response.sent_view.children if child.label == "1 Day"
        )

        pick_interaction = FakeInteraction(user=self._crew_member())
        await one_day_button.callback(interaction=pick_interaction)

        self.assertIn("past", pick_interaction.response.sent_message.lower())
        updated = self.bot.vote_service.get_round(vote_round.id)
        self.assertEqual(updated.closes_at, closes_at)

    async def test_set_exact_end_time_still_uses_the_discord_timestamp_workflow(self) -> None:
        vote_round, menu_view = await self._open_change_end_time_menu()
        set_exact_button = next(child for child in menu_view.children if child.label == "Set Exact End Time")

        exact_interaction = FakeInteraction(user=self._crew_member())
        await set_exact_button.callback(interaction=exact_interaction)
        modal = exact_interaction.response.sent_modal

        future_timestamp = int((datetime.now(timezone.utc) + timedelta(days=3)).timestamp())
        modal.timestamp_input._value = f"<t:{future_timestamp}:F>"

        submit_interaction = FakeInteraction(user=self._crew_member())
        await modal.on_submit(interaction=submit_interaction)

        updated = self.bot.vote_service.get_round(vote_round.id)
        self.assertEqual(int(updated.closes_at.timestamp()), future_timestamp)


if __name__ == "__main__":
    unittest.main()
