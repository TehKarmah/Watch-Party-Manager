import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import restore_persistent_suggestion_views
from watch_party_manager.domain.watch_item import WatchItemStatus
from watch_party_manager.persistence.suggestion_database_repository import (
    JsonSuggestionDatabaseRepository,
)
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.suggestion_view import SuggestionView, build_reject_button_custom_id


class SimulatedDiscordError(Exception):
    """Stands in for discord.py's NotFound/Forbidden/HTTPException without depending on them."""


class FakeButtonComponent:
    def __init__(self, custom_id: str) -> None:
        self.custom_id = custom_id
        self.children: list = []


class FakeActionRowComponent:
    """Mirrors real Discord messages: buttons are nested inside a top-level ActionRow."""

    def __init__(self, children) -> None:
        self.custom_id = None
        self.children = list(children)


class FakeSuggestionMessage:
    def __init__(self, *, components=(), edit_error: Exception | None = None) -> None:
        self.components = list(components)
        self.edited_view = "not-edited"
        self._edit_error = edit_error

    async def edit(self, view=None) -> None:
        if self._edit_error is not None:
            raise self._edit_error
        self.edited_view = view


class FakeSuggestionChannel:
    def __init__(self, message: FakeSuggestionMessage, *, fetch_message_error: Exception | None = None) -> None:
        self._message = message
        self._fetch_message_error = fetch_message_error

    async def fetch_message(self, message_id):
        if self._fetch_message_error is not None:
            raise self._fetch_message_error
        return self._message


class FakeBot:
    """Configurable fake covering both the callback-only and fetch/migrate paths.

    channel=None with fetch_channel_error set simulates a missing/deleted
    channel; a real FakeSuggestionChannel simulates a resolvable one
    (optionally itself configured to fail fetch_message, simulating a
    deleted or inaccessible message).
    """

    def __init__(self, channel=None, *, fetch_channel_error: Exception | None = None) -> None:
        self.calls = []
        self._channel = channel
        self._fetch_channel_error = fetch_channel_error

    def add_view(self, view, *, message_id=None) -> None:
        self.calls.append((view, message_id))

    def get_channel(self, channel_id):
        return self._channel

    async def fetch_channel(self, channel_id):
        if self._fetch_channel_error is not None:
            raise self._fetch_channel_error
        return self._channel


class PersistentSuggestionViewsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    async def test_returns_zero_when_there_are_no_suggestions(self) -> None:
        bot = FakeBot()

        restored = await restore_persistent_suggestion_views(bot, self.suggestion_service)

        self.assertEqual(restored, 0)
        self.assertEqual(bot.calls, [])

    async def test_skips_a_suggestion_with_no_message_reference(self) -> None:
        self.suggestion_service.suggest("The Matrix")
        bot = FakeBot()

        restored = await restore_persistent_suggestion_views(bot, self.suggestion_service)

        self.assertEqual(restored, 0)
        self.assertEqual(bot.calls, [])

    async def test_skips_an_archived_suggestion_even_with_a_stored_message(self) -> None:
        watch_item = self.suggestion_service.suggest("The Matrix").watch_item
        self.suggestion_service.attach_message_reference(watch_item.id, message_id=500)
        watch_item.status = WatchItemStatus.ARCHIVED
        bot = FakeBot()

        restored = await restore_persistent_suggestion_views(bot, self.suggestion_service)

        self.assertEqual(restored, 0)
        self.assertEqual(bot.calls, [])

    # --- No channel_id on record: fully legacy metadata, callback-only fallback ---

    async def test_falls_back_to_callback_only_registration_when_channel_id_is_missing(self) -> None:
        # attach_message_reference alone never sets channel_id -- this is
        # the "even more legacy" case where there's no way to fetch the
        # message at all.
        watch_item = self.suggestion_service.suggest("The Matrix").watch_item
        self.suggestion_service.attach_message_reference(watch_item.id, message_id=500)
        bot = FakeBot()

        restored = await restore_persistent_suggestion_views(bot, self.suggestion_service)

        self.assertEqual(restored, 1)
        self.assertEqual(len(bot.calls), 1)
        view, message_id = bot.calls[0]
        self.assertIsInstance(view, SuggestionView)
        self.assertEqual(message_id, 500)

    async def test_restored_button_reflects_current_rejection_state(self) -> None:
        watch_item = self.suggestion_service.suggest("The Matrix").watch_item
        self.suggestion_service.attach_message_reference(watch_item.id, message_id=500)
        self.suggestion_service.reject_suggestion(watch_item.id, discord_user_id=111)
        bot = FakeBot()

        await restore_persistent_suggestion_views(bot, self.suggestion_service)

        view, _ = bot.calls[0]
        self.assertIn("1 /", view.children[0].label)

    # --- Full metadata: fetch, detect, and migrate or register as needed ---------

    def _suggest_with_full_metadata(self, title: str, *, guild_id=100, channel_id=200, message_id=500):
        watch_item = self.suggestion_service.suggest(
            title, guild_id=guild_id, channel_id=channel_id
        ).watch_item
        self.suggestion_service.attach_message_reference(watch_item.id, message_id=message_id)
        return watch_item

    async def test_legacy_message_without_components_is_migrated_by_editing_the_message(self) -> None:
        watch_item = self._suggest_with_full_metadata("The Matrix")
        message = FakeSuggestionMessage(components=[])
        bot = FakeBot(channel=FakeSuggestionChannel(message))

        restored = await restore_persistent_suggestion_views(bot, self.suggestion_service)

        self.assertEqual(restored, 1)
        # The message was edited to attach the button...
        self.assertIsInstance(message.edited_view, SuggestionView)
        self.assertEqual(message.edited_view.children[0].suggestion_id, watch_item.id)
        # ...and editing with a dispatchable view is itself how discord.py
        # registers callback routing, so no separate add_view() call
        # should happen for this suggestion (no duplicate registration).
        self.assertEqual(bot.calls, [])

    async def test_already_updated_message_only_gets_callback_registration_not_a_second_edit(self) -> None:
        watch_item = self._suggest_with_full_metadata("The Matrix")
        custom_id = build_reject_button_custom_id(watch_item.id)
        message = FakeSuggestionMessage(components=[FakeActionRowComponent([FakeButtonComponent(custom_id)])])
        bot = FakeBot(channel=FakeSuggestionChannel(message))

        restored = await restore_persistent_suggestion_views(bot, self.suggestion_service)

        self.assertEqual(restored, 1)
        # Already has the button -- must not be edited again...
        self.assertEqual(message.edited_view, "not-edited")
        # ...only callback routing is (re-)registered, the normal path.
        self.assertEqual(len(bot.calls), 1)
        view, message_id = bot.calls[0]
        self.assertIsInstance(view, SuggestionView)
        self.assertEqual(message_id, watch_item.message_id)

    async def test_a_different_suggestions_button_does_not_count_as_already_present(self) -> None:
        watch_item = self._suggest_with_full_metadata("The Matrix")
        other_custom_id = build_reject_button_custom_id(watch_item.id + 999)
        message = FakeSuggestionMessage(
            components=[FakeActionRowComponent([FakeButtonComponent(other_custom_id)])]
        )
        bot = FakeBot(channel=FakeSuggestionChannel(message))

        await restore_persistent_suggestion_views(bot, self.suggestion_service)

        self.assertIsInstance(message.edited_view, SuggestionView)

    async def test_missing_channel_is_skipped_without_raising(self) -> None:
        watch_item = self._suggest_with_full_metadata("The Matrix")
        bot = FakeBot(channel=None, fetch_channel_error=SimulatedDiscordError("channel gone"))

        restored = await restore_persistent_suggestion_views(bot, self.suggestion_service)

        self.assertEqual(restored, 0)
        self.assertEqual(bot.calls, [])

    async def test_deleted_message_is_skipped_without_raising(self) -> None:
        watch_item = self._suggest_with_full_metadata("The Matrix")
        channel = FakeSuggestionChannel(
            FakeSuggestionMessage(), fetch_message_error=SimulatedDiscordError("message deleted")
        )
        bot = FakeBot(channel=channel)

        restored = await restore_persistent_suggestion_views(bot, self.suggestion_service)

        self.assertEqual(restored, 0)
        self.assertEqual(bot.calls, [])

    async def test_inaccessible_message_is_skipped_without_raising(self) -> None:
        watch_item = self._suggest_with_full_metadata("The Matrix")
        channel = FakeSuggestionChannel(
            FakeSuggestionMessage(), fetch_message_error=SimulatedDiscordError("forbidden")
        )
        bot = FakeBot(channel=channel)

        restored = await restore_persistent_suggestion_views(bot, self.suggestion_service)

        self.assertEqual(restored, 0)

    async def test_edit_failure_on_a_legacy_message_is_skipped_without_raising(self) -> None:
        watch_item = self._suggest_with_full_metadata("The Matrix")
        message = FakeSuggestionMessage(components=[], edit_error=SimulatedDiscordError("cannot edit"))
        bot = FakeBot(channel=FakeSuggestionChannel(message))

        restored = await restore_persistent_suggestion_views(bot, self.suggestion_service)

        self.assertEqual(restored, 0)
        self.assertEqual(bot.calls, [])

    async def test_one_failing_suggestion_does_not_block_another(self) -> None:
        broken = self._suggest_with_full_metadata("Broken", channel_id=201, message_id=501)
        working = self._suggest_with_full_metadata("Working", channel_id=202, message_id=502)

        class MultiChannelBot:
            def __init__(self) -> None:
                self.calls = []

            def add_view(self, view, *, message_id=None) -> None:
                self.calls.append((view, message_id))

            def get_channel(self, channel_id):
                if channel_id == 201:
                    return None
                return FakeSuggestionChannel(FakeSuggestionMessage(components=[]))

            async def fetch_channel(self, channel_id):
                if channel_id == 201:
                    raise SimulatedDiscordError("channel gone")
                return self.get_channel(channel_id)

        bot = MultiChannelBot()

        restored = await restore_persistent_suggestion_views(bot, self.suggestion_service)

        self.assertEqual(restored, 1)

    async def test_uses_the_suggestions_own_guild_id_to_resolve_the_threshold(self) -> None:
        created = self.suggestion_service.create_database("Sunday Watch Party", guild_id=100, channel_id=200)
        watch_item = self.suggestion_service.suggest(
            "The Matrix", database_id=created.database.database_id, guild_id=100, channel_id=200
        ).watch_item
        self.suggestion_service.attach_message_reference(watch_item.id, message_id=500)

        class FakeConfig:
            class suggestion_rules:
                rejection_threshold = 5

        class FakeConfigRepository:
            def get(self, guild_id, database_id):
                return FakeConfig()

        message = FakeSuggestionMessage(components=[])
        bot = FakeBot(channel=FakeSuggestionChannel(message))

        await restore_persistent_suggestion_views(
            bot, self.suggestion_service, suggestion_database_configuration_repository=FakeConfigRepository()
        )

        self.assertIn("/ 5", message.edited_view.children[0].label)


if __name__ == "__main__":
    unittest.main()
