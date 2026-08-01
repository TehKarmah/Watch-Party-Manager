"""Tests for the New Review Workflow's Admin Channel notification and
Retire/Keep Active/Reset Rejections decision handling (bot.py):
notify_crew_review, build_crew_review_notification_embed,
build_crew_review_view, and handle_crew_review_decision.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import discord

from watch_party_manager.bot import (
    build_crew_review_notification_embed,
    handle_crew_review_decision,
    handle_suggestion_rejection_toggle,
    notify_crew_review,
    restore_persistent_crew_review_views,
)
from watch_party_manager.crew_review_view import CrewReviewView
from watch_party_manager.domain.guild_configuration import GuildChannelsConfig, GuildConfiguration
from watch_party_manager.domain.watch_item import WatchItemStatus
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.permission_service import PermissionService
from watch_party_manager.services.suggestion_service import SuggestionService

GUILD_ID = 100
CHANNEL_ID = 200
ADMIN_CHANNEL_ID = 900
WASH_CREW_ROLE_ID = 999
WATCH_PARTY_MEMBER_ROLE_ID = 555


class FakeMember:
    def __init__(self, user_id: int, roles) -> None:
        self.id = user_id
        self.roles = [type("R", (), {"id": r})() for r in roles]
        self.mention = f"<@{user_id}>"


class FakeResponse:
    def __init__(self) -> None:
        self.sent_message = None
        self.sent_ephemeral = None

    async def send_message(self, content, ephemeral=False) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral


class FakeMessage:
    def __init__(self, message_id: int = 555) -> None:
        self.id = message_id
        self.edit_calls = 0
        self.edited_content = None
        self.edited_embed = "not-edited"
        self.edited_view = "not-edited"

    async def edit(self, *, content=None, embed=None, view=None) -> None:
        self.edit_calls += 1
        self.edited_content = content
        self.edited_embed = embed
        self.edited_view = view


class FakeInteraction:
    def __init__(self, user, *, message=None, guild_id: int = GUILD_ID) -> None:
        self.user = user
        self.guild_id = guild_id
        self.response = FakeResponse()
        self.message = message or FakeMessage()


class FakeChannel:
    def __init__(self, channel_id: int = ADMIN_CHANNEL_ID) -> None:
        self.id = channel_id
        self.sent = []
        self.sent_message_id = 700

    async def send(self, content=None, *, embed=None, view=None):
        self.sent.append((content, embed, view))
        self.sent_message_id += 1
        return FakeMessage(message_id=self.sent_message_id)


class FakeGuildConfigurationRepository:
    def __init__(self, configuration) -> None:
        self._configuration = configuration

    def get(self, guild_id):
        return self._configuration


class _CrewReviewTestSetupMixin:
    """Shared setUp/helpers for every test class below -- reaches Pending
    Crew Review (a fresh, 2-rejection threshold crossing) so each test
    only has to drive the specific action it's testing.
    """

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.database = self.suggestion_service.create_database("Movies", GUILD_ID, CHANNEL_ID).database
        self.item = self.suggestion_service.suggest(
            "Alien", database_id=self.database.database_id, guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).watch_item
        self.suggestion_service.reject_suggestion(self.item.id, 1, rejection_threshold=2)
        self.suggestion_service.reject_suggestion(self.item.id, 2, rejection_threshold=2)
        self.suggestion_service.set_crew_review_notification_reference(self.item.id, ADMIN_CHANNEL_ID, 12345)
        self.item = self.suggestion_service.get_suggestion(self.item.id)
        self.assertEqual(self.item.status, WatchItemStatus.PENDING_CREW_REVIEW)

    def _bot(self, *, configured: bool = True):
        class FakeBot:
            pass

        bot = FakeBot()
        bot.suggestion_service = self.suggestion_service
        configuration = (
            GuildConfiguration(
                guild_id=GUILD_ID, guild_name="Test", channels=GuildChannelsConfig(admin_channel_id=ADMIN_CHANNEL_ID)
            )
            if configured
            else None
        )
        bot.guild_configuration_repository = FakeGuildConfigurationRepository(configuration)
        bot.wash_crew_role_id = WASH_CREW_ROLE_ID
        self.channel = FakeChannel()

        def get_channel(channel_id):
            return self.channel if channel_id == ADMIN_CHANNEL_ID else None

        bot.get_channel = get_channel

        async def fetch_channel(channel_id):
            return self.channel

        bot.fetch_channel = fetch_channel
        return bot

    def _permission_service(self) -> PermissionService:
        return PermissionService(
            watch_party_member_role_id=WATCH_PARTY_MEMBER_ROLE_ID, wash_crew_role_id=WASH_CREW_ROLE_ID
        )


class NotifyCrewReviewTests(_CrewReviewTestSetupMixin, unittest.IsolatedAsyncioTestCase):
    async def test_sends_to_the_configured_admin_channel(self) -> None:
        bot = self._bot()

        await notify_crew_review(bot, self.item)

        self.assertEqual(len(self.channel.sent), 1)
        content, embed, view = self.channel.sent[0]
        self.assertIn(f"<@&{WASH_CREW_ROLE_ID}>", content)
        self.assertIn(self.item.title, content)
        self.assertIsInstance(embed, discord.Embed)
        self.assertIsInstance(view, CrewReviewView)

    async def test_records_where_the_notification_landed(self) -> None:
        bot = self._bot()

        await notify_crew_review(bot, self.item)

        watch_item = self.suggestion_service.get_suggestion(self.item.id)
        self.assertEqual(watch_item.crew_review_channel_id, self.channel.id)
        self.assertIsNotNone(watch_item.crew_review_message_id)

    async def test_no_op_when_no_admin_channel_configured(self) -> None:
        bot = self._bot(configured=False)

        await notify_crew_review(bot, self.item)  # must not raise

        self.assertEqual(len(self.channel.sent), 0)

    async def test_no_op_when_guild_id_is_missing(self) -> None:
        bot = self._bot()
        item = self.suggestion_service.get_suggestion(self.item.id)
        item.guild_id = None

        await notify_crew_review(bot, item)  # must not raise

        self.assertEqual(len(self.channel.sent), 0)


class BuildCrewReviewNotificationEmbedTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.item = self.suggestion_service.suggest("Alien", guild_id=GUILD_ID, channel_id=CHANNEL_ID).watch_item
        self.item.journey.record_rejection(1)
        self.item.journey.record_rejection(2)

    def test_embed_summarizes_suggestion_collection_count_and_voters(self) -> None:
        embed = build_crew_review_notification_embed(self.item, database_name="Movies")

        # The suggestion's title/reference belongs in the description --
        # never a separate field, which would only repeat it (Live UX:
        # avoid unnecessary verbosity).
        self.assertIn("Alien", embed.description)
        self.assertIn(self.item.reference, embed.description)
        field_values = {field.name: field.value for field in embed.fields}
        self.assertIn("Movies", field_values["Collection"])
        self.assertEqual("2", field_values["Rejections"])
        self.assertIn("<@1>", field_values["Rejected By"])
        self.assertIn("<@2>", field_values["Rejected By"])

    def test_embed_has_no_redundant_suggestion_field(self) -> None:
        embed = build_crew_review_notification_embed(self.item, database_name="Movies")
        field_names = {field.name for field in embed.fields}
        self.assertNotIn("Suggestion", field_names)


class HandleCrewReviewDecisionTests(_CrewReviewTestSetupMixin, unittest.IsolatedAsyncioTestCase):
    async def test_non_wash_crew_is_rejected(self) -> None:
        bot = self._bot()
        bot.permission_service = self._permission_service()
        interaction = FakeInteraction(FakeMember(1, [WATCH_PARTY_MEMBER_ROLE_ID]))

        await handle_crew_review_decision(interaction, bot, self.item.id, decision="retire")

        self.assertIn("WASH Crew", interaction.response.sent_message)
        self.assertEqual(interaction.message.edit_calls, 0)
        self.assertEqual(
            self.suggestion_service.get_suggestion(self.item.id).status, WatchItemStatus.PENDING_CREW_REVIEW
        )

    async def test_retire_archives_and_updates_the_notification(self) -> None:
        bot = self._bot()
        bot.permission_service = self._permission_service()
        interaction = FakeInteraction(FakeMember(1, [WASH_CREW_ROLE_ID]))

        await handle_crew_review_decision(interaction, bot, self.item.id, decision="retire")

        watch_item = self.suggestion_service.get_suggestion(self.item.id)
        self.assertEqual(watch_item.status, WatchItemStatus.ARCHIVED)
        self.assertEqual(interaction.message.edit_calls, 1)
        self.assertIsNone(interaction.message.edited_view)
        # Rejection history (who voted) is preserved -- only the crew
        # review notification reference is cleared, since that specific
        # notification is now resolved.
        self.assertEqual(watch_item.journey.rejected_by_discord_user_ids, (1, 2))
        self.assertIsNone(watch_item.crew_review_channel_id)
        self.assertIsNone(watch_item.crew_review_message_id)

    async def test_keep_active_restores_available(self) -> None:
        bot = self._bot()
        bot.permission_service = self._permission_service()
        interaction = FakeInteraction(FakeMember(1, [WASH_CREW_ROLE_ID]))

        await handle_crew_review_decision(interaction, bot, self.item.id, decision="keep_active")

        watch_item = self.suggestion_service.get_suggestion(self.item.id)
        self.assertEqual(watch_item.status, WatchItemStatus.SUGGESTED)
        self.assertEqual(watch_item.journey.rejected_by_discord_user_ids, ())
        self.assertIsNone(watch_item.crew_review_channel_id)
        self.assertIsNone(watch_item.crew_review_message_id)

    async def test_reset_rejections_restores_available(self) -> None:
        bot = self._bot()
        bot.permission_service = self._permission_service()
        interaction = FakeInteraction(FakeMember(1, [WASH_CREW_ROLE_ID]))

        await handle_crew_review_decision(interaction, bot, self.item.id, decision="reset_rejections")

        watch_item = self.suggestion_service.get_suggestion(self.item.id)
        self.assertEqual(watch_item.status, WatchItemStatus.SUGGESTED)
        self.assertEqual(watch_item.journey.rejected_by_discord_user_ids, ())
        self.assertIsNone(watch_item.crew_review_channel_id)
        self.assertIsNone(watch_item.crew_review_message_id)

    async def test_deciding_on_an_already_resolved_suggestion_fails_gracefully(self) -> None:
        bot = self._bot()
        bot.permission_service = self._permission_service()
        first = FakeInteraction(FakeMember(1, [WASH_CREW_ROLE_ID]))
        await handle_crew_review_decision(first, bot, self.item.id, decision="retire")

        second = FakeInteraction(FakeMember(1, [WASH_CREW_ROLE_ID]))
        await handle_crew_review_decision(second, bot, self.item.id, decision="retire")

        self.assertIn("not pending WASH Crew review", second.response.sent_message)
        self.assertEqual(second.message.edit_calls, 0)


class EndToEndCrewReviewNotificationTests(unittest.IsolatedAsyncioTestCase):
    """The button/command path (handle_suggestion_rejection_toggle) must
    itself trigger the Admin Channel notification once the threshold is
    freshly reached -- not just notify_crew_review() called directly.
    """

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.database = self.suggestion_service.create_database("Movies", GUILD_ID, CHANNEL_ID).database
        self.item = self.suggestion_service.suggest(
            "Alien", database_id=self.database.database_id, guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).watch_item
        self.suggestion_service.set_confirmation_post_reference(self.item.id, GUILD_ID, CHANNEL_ID, 555)
        # Only one rejection recorded here -- the second (threshold-
        # crossing) one is what the test itself drives through the
        # button handler, so the notification trigger is exercised for
        # real rather than by directly calling notify_crew_review().
        self.suggestion_service.reject_suggestion(self.item.id, 1, rejection_threshold=2)

        class FakeBot:
            pass

        self.bot = FakeBot()
        self.bot.suggestion_service = self.suggestion_service
        self.bot.suggestion_database_configuration_repository = None
        self.bot.vote_service = None
        self.bot.guild_configuration_repository = FakeGuildConfigurationRepository(
            GuildConfiguration(
                guild_id=GUILD_ID, guild_name="Test", channels=GuildChannelsConfig(admin_channel_id=ADMIN_CHANNEL_ID)
            )
        )
        self.bot.wash_crew_role_id = WASH_CREW_ROLE_ID
        self.bot.permission_service = PermissionService(
            watch_party_member_role_id=WATCH_PARTY_MEMBER_ROLE_ID, wash_crew_role_id=WASH_CREW_ROLE_ID
        )
        self.admin_channel = FakeChannel()

        self.post_message = FakeMessage()

        class PostChannel:
            def __init__(self, message) -> None:
                self._message = message

            async def fetch_message(self, message_id):
                return self._message

        self.post_channel = PostChannel(self.post_message)

        def get_channel(channel_id):
            if channel_id == ADMIN_CHANNEL_ID:
                return self.admin_channel
            if channel_id == CHANNEL_ID:
                return self.post_channel
            return None

        self.bot.get_channel = get_channel

    async def test_toggle_button_notifies_the_admin_channel_on_a_fresh_transition(self) -> None:
        interaction = FakeInteraction(FakeMember(2, [WATCH_PARTY_MEMBER_ROLE_ID]))

        await handle_suggestion_rejection_toggle(
            interaction,
            self.suggestion_service,
            None,
            self.item.id,
            permission_service=self.bot.permission_service,
            bot=self.bot,
        )

        self.assertEqual(
            self.suggestion_service.get_suggestion(self.item.id).status, WatchItemStatus.PENDING_CREW_REVIEW
        )
        self.assertEqual(len(self.admin_channel.sent), 1)

    async def test_a_second_click_after_pending_review_does_not_renotify(self) -> None:
        first_interaction = FakeInteraction(FakeMember(2, [WATCH_PARTY_MEMBER_ROLE_ID]))
        await handle_suggestion_rejection_toggle(
            first_interaction,
            self.suggestion_service,
            None,
            self.item.id,
            permission_service=self.bot.permission_service,
            bot=self.bot,
        )
        self.assertEqual(len(self.admin_channel.sent), 1)

        second_interaction = FakeInteraction(FakeMember(3, [WATCH_PARTY_MEMBER_ROLE_ID]))
        await handle_suggestion_rejection_toggle(
            second_interaction,
            self.suggestion_service,
            None,
            self.item.id,
            permission_service=self.bot.permission_service,
            bot=self.bot,
        )

        self.assertEqual(len(self.admin_channel.sent), 1)


class FakeRestoreChannel:
    """A channel that can fetch a specific set of known messages, or be
    made to simulate a deleted/inaccessible message on demand -- it
    deliberately has no `send()` at all, so a test where restoration
    wrongly tried to post a fresh notification fails loudly with an
    AttributeError rather than silently succeeding.
    """

    def __init__(self, *, messages=None, fail_fetch: bool = False) -> None:
        self._messages = messages or {}
        self._fail_fetch = fail_fetch

    async def fetch_message(self, message_id):
        if self._fail_fetch or message_id not in self._messages:
            raise RuntimeError("message not found")
        return self._messages[message_id]


class FakeRestoreBot:
    def __init__(self, suggestion_service: SuggestionService) -> None:
        self.suggestion_service = suggestion_service
        self.suggestion_database_configuration_repository = None
        self.guild_configuration_repository = FakeGuildConfigurationRepository(None)
        self.wash_crew_role_id = WASH_CREW_ROLE_ID
        self.added_views = []
        self._channels: dict[int, object] = {}

    def register_channel(self, channel_id: int, channel) -> None:
        self._channels[channel_id] = channel

    def add_view(self, view, *, message_id=None) -> None:
        self.added_views.append((view, message_id))

    def get_channel(self, channel_id):
        return self._channels.get(channel_id)

    async def fetch_channel(self, channel_id):
        channel = self._channels.get(channel_id)
        if channel is None:
            raise RuntimeError("channel not found")
        return channel


class RestorePersistentCrewReviewViewsTests(unittest.IsolatedAsyncioTestCase):
    """The actual release-blocker fix: Crew Review notification buttons
    must survive a bot restart, exactly like every other persistent view
    in WASH.
    """

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.database = self.suggestion_service.create_database("Movies", GUILD_ID, CHANNEL_ID).database

    def _reach_pending_review(self, title: str) -> int:
        item = self.suggestion_service.suggest(
            title, database_id=self.database.database_id, guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).watch_item
        self.suggestion_service.reject_suggestion(item.id, 1, rejection_threshold=2)
        self.suggestion_service.reject_suggestion(item.id, 2, rejection_threshold=2)
        return item.id

    async def test_restores_a_pending_review_with_a_known_notification(self) -> None:
        suggestion_id = self._reach_pending_review("Alien")
        self.suggestion_service.set_crew_review_notification_reference(suggestion_id, ADMIN_CHANNEL_ID, 555)
        bot = FakeRestoreBot(self.suggestion_service)
        bot.register_channel(ADMIN_CHANNEL_ID, FakeRestoreChannel(messages={555: object()}))

        restored = await restore_persistent_crew_review_views(bot, self.suggestion_service)

        self.assertEqual(restored, 1)
        self.assertEqual(len(bot.added_views), 1)
        view, message_id = bot.added_views[0]
        self.assertIsInstance(view, CrewReviewView)
        self.assertEqual(message_id, 555)

    async def test_the_restored_view_reconnects_to_the_correct_suggestion(self) -> None:
        suggestion_id = self._reach_pending_review("Alien")
        self.suggestion_service.set_crew_review_notification_reference(suggestion_id, ADMIN_CHANNEL_ID, 555)
        bot = FakeRestoreBot(self.suggestion_service)
        bot.register_channel(ADMIN_CHANNEL_ID, FakeRestoreChannel(messages={555: object()}))

        await restore_persistent_crew_review_views(bot, self.suggestion_service)

        view, _ = bot.added_views[0]
        retire_button = next(child for child in view.children if child.label == "Retire")
        self.assertIn(str(suggestion_id), retire_button.custom_id)

    async def test_restoring_never_posts_a_new_notification(self) -> None:
        # FakeRestoreChannel has no send() at all -- if restoration ever
        # tried to post a fresh notification, this would raise instead
        # of silently creating a duplicate.
        suggestion_id = self._reach_pending_review("Alien")
        self.suggestion_service.set_crew_review_notification_reference(suggestion_id, ADMIN_CHANNEL_ID, 555)
        bot = FakeRestoreBot(self.suggestion_service)
        bot.register_channel(ADMIN_CHANNEL_ID, FakeRestoreChannel(messages={555: object()}))

        restored = await restore_persistent_crew_review_views(bot, self.suggestion_service)  # must not raise

        self.assertEqual(restored, 1)

    async def test_restores_multiple_independent_reviews(self) -> None:
        first_id = self._reach_pending_review("Alien")
        second_id = self._reach_pending_review("The Matrix")
        self.suggestion_service.set_crew_review_notification_reference(first_id, ADMIN_CHANNEL_ID, 555)
        self.suggestion_service.set_crew_review_notification_reference(second_id, ADMIN_CHANNEL_ID, 556)
        bot = FakeRestoreBot(self.suggestion_service)
        bot.register_channel(ADMIN_CHANNEL_ID, FakeRestoreChannel(messages={555: object(), 556: object()}))

        restored = await restore_persistent_crew_review_views(bot, self.suggestion_service)

        self.assertEqual(restored, 2)
        self.assertEqual(len(bot.added_views), 2)

    async def test_does_not_restore_an_already_resolved_review(self) -> None:
        suggestion_id = self._reach_pending_review("Alien")
        self.suggestion_service.set_crew_review_notification_reference(suggestion_id, ADMIN_CHANNEL_ID, 555)
        # Resolved before the restart -- e.g. WASH Crew clicked Keep
        # Active while the bot was still running.
        self.suggestion_service.keep_suggestion_active(suggestion_id)
        bot = FakeRestoreBot(self.suggestion_service)
        bot.register_channel(ADMIN_CHANNEL_ID, FakeRestoreChannel(messages={555: object()}))

        restored = await restore_persistent_crew_review_views(bot, self.suggestion_service)

        self.assertEqual(restored, 0)
        self.assertEqual(bot.added_views, [])

    async def test_does_not_restore_a_retired_suggestion(self) -> None:
        suggestion_id = self._reach_pending_review("Alien")
        self.suggestion_service.set_crew_review_notification_reference(suggestion_id, ADMIN_CHANNEL_ID, 555)
        self.suggestion_service.retire_pending_review(suggestion_id)
        bot = FakeRestoreBot(self.suggestion_service)
        bot.register_channel(ADMIN_CHANNEL_ID, FakeRestoreChannel(messages={555: object()}))

        restored = await restore_persistent_crew_review_views(bot, self.suggestion_service)

        self.assertEqual(restored, 0)
        self.assertEqual(bot.added_views, [])

    async def test_skips_a_pending_review_with_no_stored_notification_reference(self) -> None:
        # Reached Pending Crew Review before this restoration feature
        # existed (or the notification otherwise never recorded where it
        # landed) -- there's nothing to reconnect to, and this must never
        # post a fresh notification on its behalf.
        self._reach_pending_review("Alien")
        bot = FakeRestoreBot(self.suggestion_service)

        restored = await restore_persistent_crew_review_views(bot, self.suggestion_service)

        self.assertEqual(restored, 0)
        self.assertEqual(bot.added_views, [])

    async def test_gracefully_skips_a_deleted_notification_message(self) -> None:
        suggestion_id = self._reach_pending_review("Alien")
        self.suggestion_service.set_crew_review_notification_reference(suggestion_id, ADMIN_CHANNEL_ID, 555)
        bot = FakeRestoreBot(self.suggestion_service)
        bot.register_channel(ADMIN_CHANNEL_ID, FakeRestoreChannel(fail_fetch=True))

        restored = await restore_persistent_crew_review_views(bot, self.suggestion_service)  # must not raise

        self.assertEqual(restored, 0)
        self.assertEqual(bot.added_views, [])
        # The suggestion itself, and its Pending Crew Review status, are
        # completely unaffected by a lost notification message.
        self.assertEqual(
            self.suggestion_service.get_suggestion(suggestion_id).status, WatchItemStatus.PENDING_CREW_REVIEW
        )

    async def test_gracefully_skips_a_deleted_or_inaccessible_channel(self) -> None:
        suggestion_id = self._reach_pending_review("Alien")
        self.suggestion_service.set_crew_review_notification_reference(suggestion_id, ADMIN_CHANNEL_ID, 555)
        bot = FakeRestoreBot(self.suggestion_service)
        # Never registered -- get_channel returns None and fetch_channel
        # raises, exactly like a deleted channel.

        restored = await restore_persistent_crew_review_views(bot, self.suggestion_service)  # must not raise

        self.assertEqual(restored, 0)
        self.assertEqual(bot.added_views, [])

    async def test_one_bad_reference_never_blocks_restoring_the_others(self) -> None:
        broken_id = self._reach_pending_review("Alien")
        self.suggestion_service.set_crew_review_notification_reference(broken_id, ADMIN_CHANNEL_ID, 555)
        working_id = self._reach_pending_review("The Matrix")
        self.suggestion_service.set_crew_review_notification_reference(working_id, ADMIN_CHANNEL_ID, 556)
        bot = FakeRestoreBot(self.suggestion_service)
        # 555 (broken_id's message) is deliberately absent -- fetch fails.
        bot.register_channel(ADMIN_CHANNEL_ID, FakeRestoreChannel(messages={556: object()}))

        restored = await restore_persistent_crew_review_views(bot, self.suggestion_service)

        self.assertEqual(restored, 1)
        view, message_id = bot.added_views[0]
        self.assertEqual(message_id, 556)


if __name__ == "__main__":
    unittest.main()
