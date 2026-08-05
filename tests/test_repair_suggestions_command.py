import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import perform_repair_suggestions, show_repair_confirmation
from watch_party_manager.edit_vote_view import EditVoteConfirmationView
from watch_party_manager.services.suggestion_repair_service import SuggestionRepairReport


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeUser:
    def __init__(self, *role_ids: int) -> None:
        self.roles = [FakeRole(role_id) for role_id in role_ids]


GUILD_ID = 100


class RepairSuggestionsCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_fails_closed_when_role_not_configured(self) -> None:
        service = AsyncMock()
        message, ephemeral = await perform_repair_suggestions(service, FakeUser(7), None, GUILD_ID)
        self.assertTrue(ephemeral)
        self.assertIn("WASH_CREW_ROLE_ID", message)
        service.repair_all.assert_not_awaited()

    async def test_rejects_user_without_wash_crew_role(self) -> None:
        service = AsyncMock()
        message, ephemeral = await perform_repair_suggestions(service, FakeUser(8), 7, GUILD_ID)
        self.assertTrue(ephemeral)
        self.assertIn("WASH Crew", message)
        service.repair_all.assert_not_awaited()

    async def test_wash_crew_member_receives_ephemeral_report(self) -> None:
        service = AsyncMock()
        service.repair_all.return_value = SuggestionRepairReport(scanned=2, repaired=1, removed=1)
        message, ephemeral = await perform_repair_suggestions(service, FakeUser(7), 7, GUILD_ID)
        self.assertTrue(ephemeral)
        self.assertIn("Suggestion Repair Complete", message)
        self.assertIn("Repaired: 1", message)
        service.repair_all.assert_awaited_once()

    async def test_passes_the_invoking_guild_id_to_repair_all(self) -> None:
        service = AsyncMock()
        service.repair_all.return_value = SuggestionRepairReport(scanned=1, repaired=1)
        await perform_repair_suggestions(service, FakeUser(7), 7, GUILD_ID)
        service.repair_all.assert_awaited_once()
        args, _ = service.repair_all.call_args
        self.assertEqual(args[0], GUILD_ID)

    async def test_bot_supplied_wires_a_post_sync_callback(self) -> None:
        # First-Time UX Polish: bot=... makes repair_all() receive a
        # working post_sync callback instead of None.
        service = AsyncMock()
        service.repair_all.return_value = SuggestionRepairReport(scanned=1, repaired=1)
        fake_bot = object()
        await perform_repair_suggestions(service, FakeUser(7), 7, GUILD_ID, bot=fake_bot)
        service.repair_all.assert_awaited_once()
        _, kwargs = service.repair_all.call_args
        self.assertIsNotNone(kwargs.get("post_sync"))


class FakeResponse:
    def __init__(self) -> None:
        self.sent_message = None
        self.sent_ephemeral = None
        self.sent_view = None
        self.edited_content = None
        self.edited_view = "not-edited"

    async def send_message(self, content, ephemeral=False, view=None) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral
        self.sent_view = view

    async def edit_message(self, content=None, view=None) -> None:
        self.edited_content = content
        self.edited_view = view


class FakeInteraction:
    def __init__(self, user=None, guild_id=GUILD_ID) -> None:
        self.user = user if user is not None else FakeUser(7)
        self.guild_id = guild_id
        self.response = FakeResponse()
        self._followup_edits = []

    async def edit_original_response(self, content=None) -> None:
        self._followup_edits.append(content)
        self.edited_content = content


class FakeGuildConfigurationRepository:
    """Multi-Guild Isolation, Phase 3c: resolve_permission_service's own
    minimal GuildConfigurationSource -- returns this fixed role
    configuration for any guild_id, matching this file's existing
    single-guild wash_crew_role_id fixture.
    """

    def __init__(self, wash_crew_role_id=None) -> None:
        self._wash_crew_role_id = wash_crew_role_id

    def get(self, guild_id):
        from types import SimpleNamespace

        return SimpleNamespace(
            wash_crew_role_id=self._wash_crew_role_id, watch_party_role=SimpleNamespace(role_id=None)
        )


class FakeBot:
    def __init__(self, wash_crew_role_id=7) -> None:
        self.wash_crew_role_id = wash_crew_role_id
        self.guild_configuration_repository = FakeGuildConfigurationRepository(wash_crew_role_id)
        self.suggestion_repair_service = AsyncMock()
        self.suggestion_repair_service.repair_all.return_value = SuggestionRepairReport(
            scanned=3, repaired=2, removed=1
        )


class ShowRepairConfirmationTests(unittest.IsolatedAsyncioTestCase):
    async def test_unconfigured_role_rejects_before_showing_confirmation(self) -> None:
        interaction = FakeInteraction()
        bot = FakeBot(wash_crew_role_id=None)

        await show_repair_confirmation(interaction, bot)

        self.assertIn("WASH_CREW_ROLE_ID", interaction.response.sent_message)
        self.assertIsNone(interaction.response.sent_view)
        bot.suggestion_repair_service.repair_all.assert_not_awaited()

    async def test_user_without_role_rejects_before_showing_confirmation(self) -> None:
        interaction = FakeInteraction(user=FakeUser(999))
        bot = FakeBot(wash_crew_role_id=7)

        await show_repair_confirmation(interaction, bot)

        self.assertIn("WASH Crew", interaction.response.sent_message)
        self.assertIsNone(interaction.response.sent_view)
        bot.suggestion_repair_service.repair_all.assert_not_awaited()

    async def test_wash_crew_member_sees_confirmation_with_all_five_warnings(self) -> None:
        interaction = FakeInteraction()
        bot = FakeBot()

        await show_repair_confirmation(interaction, bot)

        message = interaction.response.sent_message
        self.assertIn("may take several minutes on larger collections", message)
        self.assertIn("Discord's own rate limits", message)
        self.assertIn("located and synchronized", message)
        self.assertIn("continues processing every suggestion even if some individually fail", message)
        self.assertIn("Progress is shown when the repair starts", message)
        self.assertIsInstance(interaction.response.sent_view, EditVoteConfirmationView)
        bot.suggestion_repair_service.repair_all.assert_not_awaited()

    async def test_confirming_runs_repair_and_shows_progress_then_final_summary(self) -> None:
        interaction = FakeInteraction()
        bot = FakeBot()
        await show_repair_confirmation(interaction, bot)
        view = interaction.response.sent_view
        confirm_button = next(c for c in view.children if c.custom_id == "wpm_edit_vote_confirm")

        confirm_interaction = FakeInteraction()
        await confirm_button.callback(confirm_interaction)

        self.assertIn("Repairing suggestions", confirm_interaction.response.edited_content)
        self.assertIsNone(confirm_interaction.response.edited_view)
        bot.suggestion_repair_service.repair_all.assert_awaited_once()
        self.assertIn("Suggestion Repair Complete", confirm_interaction.edited_content)

    async def test_passes_the_confirming_interactions_own_guild_id_to_repair(self) -> None:
        # The confirmation button's own interaction is what actually
        # carries the guild context forward, not the original /maintenance
        # repair interaction -- verified here with a distinct guild_id on
        # each so a regression that reads the wrong one is caught.
        interaction = FakeInteraction(guild_id=111)
        bot = FakeBot()
        await show_repair_confirmation(interaction, bot)
        view = interaction.response.sent_view
        confirm_button = next(c for c in view.children if c.custom_id == "wpm_edit_vote_confirm")

        confirm_interaction = FakeInteraction(guild_id=222)
        await confirm_button.callback(confirm_interaction)

        args, _ = bot.suggestion_repair_service.repair_all.call_args
        self.assertEqual(args[0], 222)

    async def test_aborting_cancels_without_running_repair(self) -> None:
        interaction = FakeInteraction()
        bot = FakeBot()
        await show_repair_confirmation(interaction, bot)
        view = interaction.response.sent_view
        abort_button = next(c for c in view.children if c.custom_id == "wpm_edit_vote_abort")

        abort_interaction = FakeInteraction()
        await abort_button.callback(abort_interaction)

        self.assertIn("cancelled", abort_interaction.response.edited_content.lower())
        self.assertIsNone(abort_interaction.response.edited_view)
        bot.suggestion_repair_service.repair_all.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
