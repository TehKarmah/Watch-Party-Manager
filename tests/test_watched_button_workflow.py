"""Tests for the Watched Button & Archive Workflow: handle_watched_button_click
(permission gating, the date-confirmation modal, marking watched, resyncing
the original post) and post_watched_item_archive (posting a one-time
confirmation to the configured Watched Item Archive).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import (
    WatchedDateModal,
    handle_watched_button_click,
    parse_watched_date,
    post_watched_item_archive,
)
from watch_party_manager.domain.watch_item import WatchItemStatus
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.permission_service import PermissionService
from watch_party_manager.services.suggestion_service import SuggestionService

WASH_CREW_ROLE_ID = 999
WATCH_PARTY_MEMBER_ROLE_ID = 555
GUILD_ID = 100


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, role_ids=(), *, user_id: int = 1) -> None:
        self.roles = [FakeRole(role_id) for role_id in role_ids]
        self.id = user_id


class FakeResponse:
    def __init__(self) -> None:
        self.sent_message = None
        self.sent_ephemeral = None
        self.sent_modal = None

    async def send_message(self, content, ephemeral=False, view=None) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral

    async def send_modal(self, modal) -> None:
        self.sent_modal = modal


class FakeInteraction:
    def __init__(self, user) -> None:
        self.user = user
        self.response = FakeResponse()


class FakeMessage:
    def __init__(self, message_id: int) -> None:
        self.id = message_id
        self.edited = None

    async def edit(self, embed=None, view=None) -> None:
        self.edited = (embed, view)


class FakeChannel:
    def __init__(self, channel_id: int, *, fail: bool = False) -> None:
        self.id = channel_id
        self.sent = []
        self._fail = fail
        self._next_message_id = 900

    async def send(self, embed=None, view=None):
        if self._fail:
            raise RuntimeError("cannot send")
        message = FakeMessage(self._next_message_id)
        self._next_message_id += 1
        self.sent.append((embed, view, message))
        return message

    async def fetch_message(self, message_id):
        for _, _, message in self.sent:
            if message.id == message_id:
                return message
        raise RuntimeError("message not found")


class FakeConfigService:
    def __init__(self, destination_channel_id=None) -> None:
        self.destination_channel_id = destination_channel_id

    def resolve_effective_watch_destination(self, guild_id, database_id):
        return self.destination_channel_id


class FakeBot:
    def __init__(
        self,
        suggestion_service,
        permission_service=None,
        *,
        config_service=None,
    ) -> None:
        self.suggestion_service = suggestion_service
        self.suggestion_database_configuration_repository = None
        self.permission_service = permission_service
        self.rotation_service = None
        self.config_service = config_service or FakeConfigService()
        self._channels: dict[int, FakeChannel] = {}

    def register_channel(self, channel: FakeChannel) -> None:
        self._channels[channel.id] = channel

    def get_channel(self, channel_id):
        return self._channels.get(channel_id)

    async def fetch_channel(self, channel_id):
        channel = self._channels.get(channel_id)
        if channel is None:
            raise RuntimeError("channel not found")
        return channel


class WatchedButtonTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.permission_service = PermissionService(
            watch_party_member_role_id=WATCH_PARTY_MEMBER_ROLE_ID, wash_crew_role_id=WASH_CREW_ROLE_ID
        )
        self.database = self.suggestion_service.create_database(
            "Movie Night", guild_id=GUILD_ID, channel_id=200
        ).database
        self.matrix = self.suggestion_service.suggest(
            "The Matrix", database_id=self.database.database_id, guild_id=GUILD_ID
        ).watch_item

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _crew_member(self, user_id: int = 1) -> FakeMember:
        return FakeMember([WASH_CREW_ROLE_ID], user_id=user_id)

    def _watch_party_member(self, user_id: int = 1) -> FakeMember:
        return FakeMember([WATCH_PARTY_MEMBER_ROLE_ID], user_id=user_id)


class PermissionGatingTests(WatchedButtonTestCase):
    async def test_non_wash_crew_gets_a_clear_ephemeral_message_and_no_modal(self) -> None:
        bot = FakeBot(self.suggestion_service, self.permission_service)
        interaction = FakeInteraction(self._watch_party_member())

        await handle_watched_button_click(interaction, bot, self.matrix.id, permission_service=self.permission_service)

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIsNone(interaction.response.sent_modal)
        self.assertEqual(self.suggestion_service.get_suggestion(self.matrix.id).status, WatchItemStatus.SUGGESTED)

    async def test_wash_crew_is_shown_the_confirmation_modal(self) -> None:
        bot = FakeBot(self.suggestion_service, self.permission_service)
        interaction = FakeInteraction(self._crew_member())

        await handle_watched_button_click(interaction, bot, self.matrix.id, permission_service=self.permission_service)

        self.assertIsInstance(interaction.response.sent_modal, WatchedDateModal)
        self.assertEqual(interaction.response.sent_modal.watched_date_input.default, date.today().isoformat())

    async def test_missing_permission_service_is_a_graceful_ephemeral_failure(self) -> None:
        bot = FakeBot(self.suggestion_service, None)
        interaction = FakeInteraction(self._crew_member())

        await handle_watched_button_click(interaction, bot, self.matrix.id, permission_service=None)

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIsNone(interaction.response.sent_modal)

    async def test_missing_bot_is_a_graceful_ephemeral_failure(self) -> None:
        interaction = FakeInteraction(self._crew_member())

        await handle_watched_button_click(interaction, None, self.matrix.id, permission_service=self.permission_service)

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIsNone(interaction.response.sent_modal)

    async def test_nonexistent_suggestion_is_reported_clearly(self) -> None:
        bot = FakeBot(self.suggestion_service, self.permission_service)
        interaction = FakeInteraction(self._crew_member())

        await handle_watched_button_click(interaction, bot, 999999, permission_service=self.permission_service)

        self.assertIn("no longer exists", interaction.response.sent_message)
        self.assertIsNone(interaction.response.sent_modal)

    async def test_an_already_watched_suggestion_is_rejected_before_any_modal(self) -> None:
        self.suggestion_service.mark_suggestion_watched(self.matrix.id, date.today())
        bot = FakeBot(self.suggestion_service, self.permission_service)
        interaction = FakeInteraction(self._crew_member())

        await handle_watched_button_click(interaction, bot, self.matrix.id, permission_service=self.permission_service)

        self.assertIn("already marked watched", interaction.response.sent_message)
        self.assertIsNone(interaction.response.sent_modal)


class WatchedDateSubmissionTests(WatchedButtonTestCase):
    async def _open_modal(self, bot) -> WatchedDateModal:
        interaction = FakeInteraction(self._crew_member())
        await handle_watched_button_click(interaction, bot, self.matrix.id, permission_service=self.permission_service)
        return interaction.response.sent_modal

    async def test_a_valid_past_date_marks_the_item_watched(self) -> None:
        bot = FakeBot(self.suggestion_service, self.permission_service)
        modal = await self._open_modal(bot)
        modal.watched_date_input._value = "2026-07-01"
        submit_interaction = FakeInteraction(self._crew_member())

        await modal.on_submit(interaction=submit_interaction)

        item = self.suggestion_service.get_suggestion(self.matrix.id)
        self.assertEqual(item.status, WatchItemStatus.WATCHED)
        self.assertEqual(item.journey.watch_dates, (date(2026, 7, 1),))
        self.assertIn("marked watched", submit_interaction.response.sent_message)
        self.assertTrue(submit_interaction.response.sent_ephemeral)

    async def test_todays_date_is_accepted(self) -> None:
        bot = FakeBot(self.suggestion_service, self.permission_service)
        modal = await self._open_modal(bot)
        modal.watched_date_input._value = date.today().isoformat()
        submit_interaction = FakeInteraction(self._crew_member())

        await modal.on_submit(interaction=submit_interaction)

        self.assertEqual(self.suggestion_service.get_suggestion(self.matrix.id).status, WatchItemStatus.WATCHED)

    async def test_an_invalid_date_is_rejected_with_a_clear_message_and_no_mutation(self) -> None:
        bot = FakeBot(self.suggestion_service, self.permission_service)
        modal = await self._open_modal(bot)
        modal.watched_date_input._value = "not-a-date"
        submit_interaction = FakeInteraction(self._crew_member())

        await modal.on_submit(interaction=submit_interaction)

        self.assertIn("isn't a valid date", submit_interaction.response.sent_message)
        self.assertEqual(self.suggestion_service.get_suggestion(self.matrix.id).status, WatchItemStatus.SUGGESTED)

    async def test_a_future_date_is_rejected_with_no_mutation(self) -> None:
        bot = FakeBot(self.suggestion_service, self.permission_service)
        modal = await self._open_modal(bot)
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        modal.watched_date_input._value = tomorrow
        submit_interaction = FakeInteraction(self._crew_member())

        await modal.on_submit(interaction=submit_interaction)

        self.assertIn("can't be in the future", submit_interaction.response.sent_message)
        self.assertEqual(self.suggestion_service.get_suggestion(self.matrix.id).status, WatchItemStatus.SUGGESTED)

    async def test_resyncs_the_original_suggestion_posts_embed(self) -> None:
        confirmation_channel = FakeChannel(200)
        # Simulate an existing confirmation post to resync.
        posted_message = await confirmation_channel.send(embed=None, view=None)
        self.suggestion_service.set_confirmation_post_reference(
            self.matrix.id, GUILD_ID, confirmation_channel.id, posted_message.id
        )
        bot = FakeBot(self.suggestion_service, self.permission_service)
        bot.register_channel(confirmation_channel)
        modal = await self._open_modal(bot)
        modal.watched_date_input._value = date.today().isoformat()
        submit_interaction = FakeInteraction(self._crew_member())

        await modal.on_submit(interaction=submit_interaction)

        refreshed_message = await confirmation_channel.fetch_message(posted_message.id)
        self.assertIsNotNone(refreshed_message.edited)
        refreshed_embed, _ = refreshed_message.edited
        status_field = next(field for field in refreshed_embed.fields if field.name == "Status")
        self.assertTrue(status_field.value.startswith("✅ Watched"))

    async def test_no_archive_configured_still_marks_watched_and_notes_it_gracefully(self) -> None:
        bot = FakeBot(self.suggestion_service, self.permission_service, config_service=FakeConfigService(None))
        modal = await self._open_modal(bot)
        modal.watched_date_input._value = date.today().isoformat()
        submit_interaction = FakeInteraction(self._crew_member())

        await modal.on_submit(interaction=submit_interaction)

        self.assertEqual(self.suggestion_service.get_suggestion(self.matrix.id).status, WatchItemStatus.WATCHED)
        self.assertIn("No Watched Item Archive is configured", submit_interaction.response.sent_message)

    async def test_archive_configured_posts_there_and_the_ack_has_no_warning(self) -> None:
        archive_channel = FakeChannel(300)
        bot = FakeBot(self.suggestion_service, self.permission_service, config_service=FakeConfigService(300))
        bot.register_channel(archive_channel)
        modal = await self._open_modal(bot)
        modal.watched_date_input._value = date.today().isoformat()
        submit_interaction = FakeInteraction(self._crew_member())

        await modal.on_submit(interaction=submit_interaction)

        self.assertEqual(len(archive_channel.sent), 1)
        self.assertNotIn("No Watched Item Archive", submit_interaction.response.sent_message)
        self.assertNotIn("could not post", submit_interaction.response.sent_message.lower())


class PostWatchedItemArchiveTests(WatchedButtonTestCase):
    async def test_posts_to_the_configured_destination(self) -> None:
        archive_channel = FakeChannel(300)
        bot = FakeBot(self.suggestion_service, self.permission_service, config_service=FakeConfigService(300))
        bot.register_channel(archive_channel)
        self.suggestion_service.mark_suggestion_watched(self.matrix.id, date.today())
        watch_item = self.suggestion_service.get_suggestion(self.matrix.id)

        posted, note = await post_watched_item_archive(bot, watch_item)

        self.assertTrue(posted)
        self.assertEqual(note, "")
        self.assertEqual(len(archive_channel.sent), 1)

    async def test_gracefully_reports_when_no_destination_is_configured(self) -> None:
        bot = FakeBot(self.suggestion_service, self.permission_service, config_service=FakeConfigService(None))
        self.suggestion_service.mark_suggestion_watched(self.matrix.id, date.today())
        watch_item = self.suggestion_service.get_suggestion(self.matrix.id)

        posted, note = await post_watched_item_archive(bot, watch_item)

        self.assertFalse(posted)
        self.assertIn("No Watched Item Archive is configured", note)
        self.assertIn("/setup", note)

    async def test_gracefully_reports_a_failed_post(self) -> None:
        archive_channel = FakeChannel(300, fail=True)
        bot = FakeBot(self.suggestion_service, self.permission_service, config_service=FakeConfigService(300))
        bot.register_channel(archive_channel)
        self.suggestion_service.mark_suggestion_watched(self.matrix.id, date.today())
        watch_item = self.suggestion_service.get_suggestion(self.matrix.id)

        posted, note = await post_watched_item_archive(bot, watch_item)

        self.assertFalse(posted)
        self.assertIn("could not post", note.lower())


class ParseWatchedDateTests(unittest.TestCase):
    def test_accepts_a_valid_iso_date(self) -> None:
        self.assertEqual(parse_watched_date("2026-07-28"), date(2026, 7, 28))

    def test_strips_surrounding_whitespace(self) -> None:
        self.assertEqual(parse_watched_date("  2026-07-28  "), date(2026, 7, 28))

    def test_rejects_a_blank_value(self) -> None:
        with self.assertRaises(ValueError):
            parse_watched_date("")

    def test_rejects_an_unparseable_value(self) -> None:
        with self.assertRaises(ValueError):
            parse_watched_date("July 28th")

    def test_rejects_a_future_date(self) -> None:
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        with self.assertRaises(ValueError):
            parse_watched_date(tomorrow)

    def test_accepts_today(self) -> None:
        self.assertEqual(parse_watched_date(date.today().isoformat()), date.today())


if __name__ == "__main__":
    unittest.main()
